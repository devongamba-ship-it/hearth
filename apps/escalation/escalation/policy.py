"""Incident registry: per-type escalation policies (BAT-31).

Every incident *type* is defined independently — its own trigger (owned by the
emitter, not here), priority, escalation path, claim semantics, auto-resolve, and
quiet-hours behavior — as a row in a per-home ``escalation_policies.yaml``. The
:class:`~escalation.policy_engine.PolicyEngine` executes whatever the registry
says; adding an incident type is a YAML edit, never a code change (canonical:
platform design v0.1 §4.5).

This module owns the **schema** and the **v1 registry**. The fall cascade that
BAT-23 hand-coded is here re-expressed as the ``fall.confirmed`` policy instance
per the BAT-31 registry table: T+0 push circle + ops queue, T+1:00 SMS + voice
ladder, T+2:00 operator. An unconfirmed claim resumes AT the operator step
(platform design v0.1 §4 step 3), expressed as ``resume_at_action``.

Loading order (``load_registry``):

* ``escalation_policies.yaml`` at ``$HEARTH_POLICIES_FILE`` if set, else
* the embedded :data:`DEFAULT_REGISTRY` (the v1 table below), so the service
  works with zero config. A file may override/extend the embedded defaults.

The embedded default is the source of truth for behavior; the shipped
``escalation_policies.example.yaml`` is the same registry in YAML for humans to
copy and tune.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# -- claim modes ------------------------------------------------------------
CLAIM_PAUSE = "pause"   # pause the ladder; resume if unconfirmed in confirm_window
CLAIM_CLOSE = "close"   # claiming acknowledges and stops the ladder; no resume
CLAIM_NONE = "none"     # type is not claimable (digest-only / informational)
CLAIM_MODES = (CLAIM_PAUSE, CLAIM_CLOSE, CLAIM_NONE)

# -- quiet-hours behavior ---------------------------------------------------
QUIET_PAGE = "page"     # ignore quiet hours, always page (P1)
QUIET_DEFER = "defer"   # defer delayed paging steps to the end of the quiet window
QUIET_MODES = (QUIET_PAGE, QUIET_DEFER)

# Channels that constitute a "page" (wake someone). digest/ops never page.
PAGING_CHANNELS = frozenset({"sms", "push", "voice"})

# Default 15-minute confirm window for pause-type claims.
DEFAULT_CONFIRM_WINDOW = 15 * 60


class PolicyError(Exception):
    """Raised on a malformed registry or an unknown incident type/action."""


@dataclass(frozen=True)
class Step:
    """One scheduled action in a policy's escalation path.

    ``at_secs`` is the offset from incident creation the action fires at (0 =
    immediately). ``action`` names a pluggable sender on the engine
    (broadcast/sms/push/voice/operator/ops_queue/digest). ``priority`` defaults
    to the policy priority and drives the quiet-hours page/defer decision.
    """

    at_secs: float
    action: str
    priority: str | None = None


@dataclass(frozen=True)
class Policy:
    """The full response definition for one incident type."""

    type: str
    priority: str
    steps: tuple[Step, ...]
    claim_mode: str = CLAIM_NONE
    confirm_window_secs: float = DEFAULT_CONFIRM_WINDOW
    snoozeable: bool = False
    auto_resolve: bool = False
    quiet_hours: str = QUIET_PAGE
    # dedupe: collapse repeat raises sharing a dedupe key.
    #   one_open=True  -> dedupe while any non-terminal instance is open.
    #   dedupe_window_secs>0 -> dedupe repeats within that many seconds.
    one_open: bool = False
    dedupe_window_secs: float = 0.0
    # Where an expired (unconfirmed) pause-claim resumes. None = the step that
    # was pending when claimed; an action name = the first step with that
    # action (fall.confirmed resumes at "operator" per design §4 step 3).
    resume_at_action: str | None = None

    @property
    def claimable(self) -> bool:
        return self.claim_mode in (CLAIM_PAUSE, CLAIM_CLOSE)

    def step_priority(self, step: Step) -> str:
        return step.priority or self.priority


@dataclass(frozen=True)
class QuietHours:
    """The home's quiet window (local wall-clock ``HH:MM``), wraps midnight."""

    start: tuple[int, int]
    end: tuple[int, int]

    def contains_minute(self, minute_of_day: int) -> bool:
        start = self.start[0] * 60 + self.start[1]
        end = self.end[0] * 60 + self.end[1]
        if start == end:
            return False
        if start < end:
            return start <= minute_of_day < end
        return minute_of_day >= start or minute_of_day < end


@dataclass(frozen=True)
class Registry:
    """The loaded set of policies plus the home quiet window."""

    policies: dict[str, Policy]
    quiet_hours: QuietHours | None = None

    def get(self, type_: str) -> Policy:
        try:
            return self.policies[type_]
        except KeyError as exc:
            raise PolicyError(
                f"no policy for incident type {type_!r}; known types: "
                f"{sorted(self.policies)}"
            ) from exc

    def __contains__(self, type_: str) -> bool:
        return type_ in self.policies


# -- the v1 registry (canonical) --------------------------------------------
#
# Mirrors the BAT-31 registry table. Embedded as data so the service has working
# defaults with no file present; escalation_policies.example.yaml is the same.

DEFAULT_REGISTRY: dict = {
    "version": 1,
    "quiet_hours": {"start": "21:00", "end": "08:00"},
    "policies": [
        {
            "type": "fall.confirmed",
            "priority": "P1",
            "claim": {"mode": "pause", "confirm_window_secs": 900},
            "resume_at_action": "operator",
            "snoozeable": False,
            "auto_resolve": False,
            "quiet_hours": "page",
            "dedupe": {"window_secs": 300},
            # BAT-31 registry table: T+0 push circle + ops queue, T+1:00
            # SMS + voice priority ladder, T+2:00 operator (SLA 60s).
            "steps": [
                {"at_secs": 0, "action": "push"},
                {"at_secs": 0, "action": "ops_queue"},
                {"at_secs": 60, "action": "sms"},
                {"at_secs": 60, "action": "voice"},
                {"at_secs": 120, "action": "operator"},
            ],
        },
        {
            "type": "wellness.prolonged_stillness",
            "priority": "P2",
            "claim": {"mode": "close"},
            "snoozeable": True,
            "auto_resolve": True,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "push"},
                {"at_secs": 1800, "action": "sms"},
                {"at_secs": 7200, "action": "ops_queue"},
            ],
        },
        {
            "type": "system.sensing_gap",
            "priority": "P2",
            "claim": {"mode": "close"},
            "snoozeable": True,
            "auto_resolve": True,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "push"},
                {"at_secs": 0, "action": "ops_queue"},
                {"at_secs": 86400, "action": "ops_queue"},
            ],
        },
        {
            "type": "wellness.late_start",
            "priority": "P3",
            "claim": {"mode": "close"},
            "snoozeable": True,
            "auto_resolve": True,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "push"},
                {"at_secs": 14400, "action": "digest"},
            ],
        },
        {
            "type": "wellness.missed_meal",
            "priority": "P3",
            "claim": {"mode": "close"},
            "snoozeable": True,
            "auto_resolve": True,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "push"},
            ],
        },
        {
            "type": "wellness.bathroom_spike",
            "priority": "P4",
            "claim": {"mode": "none"},
            "snoozeable": True,
            "auto_resolve": False,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "digest"},
            ],
        },
        {
            "type": "wellness.night_wandering",
            "priority": "P4",
            "claim": {"mode": "none"},
            "snoozeable": True,
            "auto_resolve": False,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "digest"},
            ],
        },
        {
            "type": "wellness.activity_decline",
            "priority": "P4",
            "claim": {"mode": "none"},
            "snoozeable": True,
            "auto_resolve": False,
            "quiet_hours": "defer",
            "dedupe": {"one_open": True},
            "steps": [
                {"at_secs": 0, "action": "digest"},
            ],
        },
    ],
}


# -- parsing ----------------------------------------------------------------


def _parse_hhmm(value, default: str = "00:00") -> tuple[int, int]:
    raw = value if value is not None else default
    hh, _, mm = str(raw).partition(":")
    return int(hh), int(mm or 0)


def _parse_step(raw: dict) -> Step:
    if "action" not in raw:
        raise PolicyError(f"step requires an 'action': {raw!r}")
    return Step(
        at_secs=float(raw.get("at_secs", 0)),
        action=str(raw["action"]),
        priority=raw.get("priority"),
    )


def _parse_policy(raw: dict) -> Policy:
    if not isinstance(raw, dict) or "type" not in raw:
        raise PolicyError(f"each policy requires a 'type': {raw!r}")
    steps = tuple(_parse_step(s) for s in (raw.get("steps") or []))
    if not steps:
        raise PolicyError(f"policy {raw['type']!r} has no steps")

    claim = raw.get("claim") or {}
    claim_mode = str(claim.get("mode", CLAIM_NONE))
    if claim_mode not in CLAIM_MODES:
        raise PolicyError(
            f"policy {raw['type']!r}: invalid claim mode {claim_mode!r}; "
            f"expected one of {CLAIM_MODES}"
        )
    quiet = str(raw.get("quiet_hours", QUIET_PAGE))
    if quiet not in QUIET_MODES:
        raise PolicyError(
            f"policy {raw['type']!r}: invalid quiet_hours {quiet!r}; "
            f"expected one of {QUIET_MODES}"
        )
    dedupe = raw.get("dedupe") or {}
    resume_at = raw.get("resume_at_action")
    if resume_at is not None:
        resume_at = str(resume_at)
        if resume_at not in {s.action for s in steps}:
            raise PolicyError(
                f"policy {raw['type']!r}: resume_at_action {resume_at!r} "
                "names no step in this policy"
            )
    return Policy(
        type=str(raw["type"]),
        priority=str(raw.get("priority", "P3")),
        steps=steps,
        claim_mode=claim_mode,
        confirm_window_secs=float(claim.get("confirm_window_secs", DEFAULT_CONFIRM_WINDOW)),
        snoozeable=bool(raw.get("snoozeable", False)),
        auto_resolve=bool(raw.get("auto_resolve", False)),
        quiet_hours=quiet,
        one_open=bool(dedupe.get("one_open", False)),
        dedupe_window_secs=float(dedupe.get("window_secs", 0.0)),
        resume_at_action=resume_at,
    )


def parse_registry(data: dict) -> Registry:
    """Build a :class:`Registry` from a parsed YAML/dict document."""
    if not isinstance(data, dict):
        raise PolicyError("escalation policies config must be a mapping")
    raw_policies = data.get("policies") or []
    if not raw_policies:
        raise PolicyError("escalation policies config requires at least one policy")
    policies: dict[str, Policy] = {}
    for raw in raw_policies:
        policy = _parse_policy(raw)
        if policy.type in policies:
            raise PolicyError(f"duplicate policy for type {policy.type!r}")
        policies[policy.type] = policy

    quiet = None
    raw_quiet = data.get("quiet_hours")
    if raw_quiet:
        quiet = QuietHours(
            start=_parse_hhmm(raw_quiet.get("start"), "21:00"),
            end=_parse_hhmm(raw_quiet.get("end"), "08:00"),
        )
    return Registry(policies=policies, quiet_hours=quiet)


def default_registry() -> Registry:
    """The embedded v1 registry — used when no policies file is configured."""
    return parse_registry(DEFAULT_REGISTRY)


def load_registry_from_text(text: str) -> Registry:
    """Parse a registry from a YAML string (used by tests and custom types)."""
    return parse_registry(yaml.safe_load(text))


def load_registry(path: str | os.PathLike | None = None) -> Registry:
    """Load the registry from ``path`` / ``$HEARTH_POLICIES_FILE``, else default."""
    resolved = path or os.environ.get("HEARTH_POLICIES_FILE")
    if not resolved:
        return default_registry()
    text = Path(resolved).read_text(encoding="utf-8")
    return load_registry_from_text(text)
