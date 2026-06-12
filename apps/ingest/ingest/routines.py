"""Absence-of-normal-life rules engine (BAT-24).

Falls are loud; the failures fall detection *misses* are quiet — the "fell hours
ago," the medical event in bed, the no-show morning. This engine watches for the
*absence* of a person's normal rhythm and raises a soft wellness concern when the
day stops looking like a normal day.

Three rules ship in v1 (all tunable per home via ``routines.yaml``):

  1. **Morning check** — no real motion in the kitchen/living zones by
     ``morning_deadline`` (default 10:30) while presence is detected in the home
     → ``wellness.late_start``.
  2. **Overnight stillness** — bathroom-zone motion between 21:00–08:00 that
     deviates far below the trailing-``baseline_nights`` baseline (the classic
     case: someone who normally gets up at night didn't at all tonight)
     → ``wellness.prolonged_stillness``.
  3. **Extended absence of motion** — presence is true but motion is below
     threshold on *every* node for longer than ``max_still_hours`` (default 4h,
     daytime only) → ``wellness.prolonged_stillness``.

Canonical design update (platform design v0.1 §4.5, per the BAT-24 Linear
thread): each rule emits a **typed incident** (``wellness.late_start``,
``wellness.prolonged_stillness``, …), not a generic ``WELLNESS_CONCERN``. Rules
own *triggers only*; the response path (push/SMS/digest/ops, claim semantics,
auto-resolve) is defined per type in the incident registry (BAT-31). Until that
lands we emit typed events through the existing ``POST /escalate`` pipe with the
type string included and ``severity="soft"``; we do **not** decide the response
here. Every wellness incident is ``severity="soft"`` — the rules engine never
triggers a voice cascade.

Time model: the engine is **clock-driven**. Readings carry only ms-since-boot
(``ts``), not wall-clock, so — exactly like the fall machine's quiet-hours check
— the engine stamps each observation with an injected wall ``clock`` and reasons
about time-of-day from that. State is held in memory across the 5-minute poll
ticks (the same shape as :class:`ingest.fall_machine.FallMachineRunner`); nothing
about a wellness *reading* is persisted, only the soft alert leaves the home.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from pathlib import Path
from typing import Protocol

import yaml

from .fall_machine import Frame

log = logging.getLogger("hearth.ingest.routines")

Clock = Callable[[], float]

# Incident type strings (canonical design update §4.5).
LATE_START = "wellness.late_start"
PROLONGED_STILLNESS = "wellness.prolonged_stillness"

# Every rule in this engine is a soft, SMS-only-tier concern: it must never
# trigger a voice cascade. The escalation registry (BAT-31) routes on this.
SOFT = "soft"


# -- config -----------------------------------------------------------------


def _parse_hhmm(value: str | None, default: str) -> tuple[int, int]:
    raw = value if value is not None else default
    hh, _, mm = str(raw).partition(":")
    return int(hh), int(mm or 0)


@dataclass(frozen=True)
class RoutinesConfig:
    """Per-home wellness tunables. Loadable from ``routines.yaml``.

    ``zones`` maps a ``node_id`` to a coarse zone label ("kitchen", "bathroom",
    …). Rules reference zones by name; an unmapped node has no zone and only
    counts toward the all-nodes rule 3. ``*_deadline`` / window fields are local
    wall-clock ``"HH:MM"`` strings.
    """

    home_name: str | None = None
    zones: dict[int, str] = field(default_factory=dict)

    # Motion energy at/above this is "real motion"; below it is stillness. Shared
    # default with the fall machine's stillness_threshold so the two agree.
    active_motion_threshold: float = 200.0

    # A reading older than this (secs) no longer counts as "presence right now".
    presence_staleness_secs: float = 15 * 60

    # Rule 1 — morning check.
    morning_deadline: tuple[int, int] = (10, 30)
    morning_zones: tuple[str, ...] = ("kitchen", "living")

    # Rule 2 — overnight stillness.
    overnight_start: tuple[int, int] = (21, 0)
    overnight_end: tuple[int, int] = (8, 0)
    overnight_zones: tuple[str, ...] = ("bathroom",)
    baseline_nights: int = 14
    # Don't judge a night until we have at least this many baseline nights.
    min_baseline_nights: int = 3
    # Fire when (baseline mean - tonight) is at least this far below normal.
    overnight_deviation: float = 1.0

    # Rule 3 — extended daytime absence of motion.
    max_still_hours: float = 4.0
    day_start: tuple[int, int] = (8, 0)
    day_end: tuple[int, int] = (21, 0)

    # How often the production poller evaluates (operational only).
    eval_interval_secs: float = 300.0

    def zone_for(self, node_id: int) -> str | None:
        return self.zones.get(node_id)


def _parse(data: dict | None) -> RoutinesConfig:
    if data is None:
        return RoutinesConfig()
    if not isinstance(data, dict):
        raise ValueError("routines config must be a mapping")
    base = RoutinesConfig()
    overrides: dict = {}
    for key in (
        "home_name",
        "active_motion_threshold",
        "presence_staleness_secs",
        "baseline_nights",
        "min_baseline_nights",
        "overnight_deviation",
        "max_still_hours",
        "eval_interval_secs",
    ):
        if key in data and data[key] is not None:
            overrides[key] = data[key]
    if data.get("zones"):
        overrides["zones"] = {int(k): str(v) for k, v in data["zones"].items()}
    for key in ("morning_zones", "overnight_zones"):
        if data.get(key):
            overrides[key] = tuple(str(z) for z in data[key])
    for key, default in (
        ("morning_deadline", "10:30"),
        ("overnight_start", "21:00"),
        ("overnight_end", "08:00"),
        ("day_start", "08:00"),
        ("day_end", "21:00"),
    ):
        if data.get(key) is not None:
            overrides[key] = _parse_hhmm(data[key], default)
    return replace(base, **overrides)


def load_config(path: str | os.PathLike | None = None) -> RoutinesConfig:
    """Load tunables from ``path`` / ``$HEARTH_ROUTINES_FILE``, else defaults."""
    resolved = path or os.environ.get("HEARTH_ROUTINES_FILE")
    if not resolved:
        return RoutinesConfig()
    text = Path(resolved).read_text(encoding="utf-8")
    return _parse(yaml.safe_load(text))


def load_config_from_text(text: str) -> RoutinesConfig:
    """Parse a config from a YAML string (used by tests)."""
    return _parse(yaml.safe_load(text))


# -- the emitted incident + delivery seam -----------------------------------


@dataclass(frozen=True)
class WellnessIncident:
    """A typed, soft wellness concern handed to the escalation pipe.

    ``severity`` is always :data:`SOFT`; the engine never produces anything that
    a correct registry would voice-call. ``zone`` doubles as the ``room`` label
    the existing ``/escalate`` endpoint requires.
    """

    type: str
    zone: str
    detail: str
    detected_at: float
    home_name: str | None = None
    severity: str = SOFT
    dedupe_key: str = ""

    def payload(self) -> dict:
        return {
            "type": self.type,
            "severity": self.severity,
            "room": self.zone,
            "zone": self.zone,
            "home_name": self.home_name,
            "source": "routines",
            "detail": self.detail,
            "detected_at_epoch": self.detected_at,
        }


class IncidentEmitter(Protocol):
    """Delivers a wellness incident toward escalation. Never raises."""

    def emit(self, incident: WellnessIncident) -> bool: ...


class NullIncidentEmitter:
    """Records incidents without leaving the process. Safe default / test double."""

    def __init__(self) -> None:
        self.calls: list[WellnessIncident] = []

    def emit(self, incident: WellnessIncident) -> bool:
        self.calls.append(incident)
        log.info("wellness concern %s zone=%s (NullIncidentEmitter: not delivered)",
                 incident.type, incident.zone)
        return True


class HttpIncidentEmitter:
    """POSTs the typed incident to the local escalation ``/escalate`` pipe.

    Mirrors :class:`ingest.escalator.HttpEscalator`: fire-and-forget with a
    couple of retries, failures logged and swallowed. The ``type`` and
    ``severity="soft"`` fields ride along so the incident registry (BAT-31) can
    route it SMS-only; today's ``/escalate`` reads ``room``/``home_name`` and
    ignores the rest, so this is forward-compatible. No real-world side effects
    in CI: the escalation service is itself dry-run unless a human sets live mode.
    """

    def __init__(self, url: str | None = None, *, retries: int = 2,
                 timeout: float = 3.0) -> None:
        self.url = url or os.environ.get(
            "HEARTH_ESCALATION_URL", "http://127.0.0.1:8001/escalate")
        self.retries = retries
        self.timeout = timeout

    def emit(self, incident: WellnessIncident) -> bool:
        body = json.dumps(incident.payload()).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.url, data=body, headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
                    last_err = RuntimeError(f"escalation returned HTTP {resp.status}")
            except (urllib.error.URLError, OSError) as exc:  # noqa: PERF203
                last_err = exc
            log.warning("wellness emit attempt %d failed: %s", attempt + 1, last_err)
        log.error("wellness emit gave up after %d attempts: %s",
                  self.retries + 1, last_err)
        return False


# -- the engine -------------------------------------------------------------


class RoutinesEngine:
    """Evaluates the wellness rules against a stream of observations + a clock.

    Feed each fresh reading to :meth:`observe`, then call :meth:`evaluate` once
    per tick (the production poller does both every 5 min). Both take an optional
    ``now`` so tests can drive frozen / replayed wall-clock time deterministically.
    """

    def __init__(
        self,
        config: RoutinesConfig | None = None,
        *,
        emitter: IncidentEmitter | None = None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config or RoutinesConfig()
        self.emitter = emitter or NullIncidentEmitter()
        import time as _time
        self._clock = clock or _time.time

        # Wall epoch of the last "real motion" seen, per zone and across all nodes.
        self._last_active_zone: dict[str, float] = {}
        self._last_active_any: float | None = None
        self._observing_since: float | None = None
        self._presence_seen_at: float | None = None

        # Overnight (rule 2) accumulation + learned baseline of nightly counts.
        self._night_key: object | None = None  # date currently accumulating
        self._night_count: int = 0
        self._baseline: deque[int] = deque(maxlen=self.config.baseline_nights)
        self._finalized_nights: set[str] = set()

        # Per-day / per-episode de-duplication of fired concerns.
        self._fired: dict[str, str] = {}

    # -- clock / time helpers ------------------------------------------------

    def _now(self, now: float | None) -> float:
        return self._clock() if now is None else now

    @staticmethod
    def _minutes(now: float) -> int:
        dt = datetime.fromtimestamp(now)
        return dt.hour * 60 + dt.minute

    @staticmethod
    def _at(now: float, hm: tuple[int, int], *, day_offset: int = 0) -> float:
        """Local wall epoch for ``hm`` on the date of ``now`` (± day_offset)."""
        dt = datetime.fromtimestamp(now) + timedelta(days=day_offset)
        return dt.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0).timestamp()

    def _is_daytime(self, now: float) -> bool:
        mins = self._minutes(now)
        start = self.config.day_start[0] * 60 + self.config.day_start[1]
        end = self.config.day_end[0] * 60 + self.config.day_end[1]
        return start <= mins < end

    def _night_for(self, now: float):
        """Date the overnight window covering ``now`` belongs to, else None.

        The window wraps midnight (21:00 → 08:00); the evening side keeps its own
        date, the early-morning side belongs to the previous evening.
        """
        mins = self._minutes(now)
        start = self.config.overnight_start[0] * 60 + self.config.overnight_start[1]
        end = self.config.overnight_end[0] * 60 + self.config.overnight_end[1]
        in_window = mins >= start or mins < end
        if not in_window:
            return None
        d = datetime.fromtimestamp(now).date()
        return d if mins >= start else d - timedelta(days=1)

    def _presence_now(self, now: float) -> bool:
        if self._presence_seen_at is None:
            return False
        return now - self._presence_seen_at <= self.config.presence_staleness_secs

    # -- ingest --------------------------------------------------------------

    def observe(self, frame: Frame, now: float | None = None) -> None:
        """Fold one reading into the engine's state at wall time ``now``."""
        now = self._now(now)
        if self._observing_since is None:
            self._observing_since = now
        zone = self.config.zone_for(frame.node_id)
        active = frame.motion >= self.config.active_motion_threshold

        if frame.presence:
            self._presence_seen_at = now
        if active:
            self._last_active_any = now
            if zone is not None:
                self._last_active_zone[zone] = now

        night = self._night_for(now)
        if night is not None:
            if night != self._night_key:
                self._night_key = night
                self._night_count = 0
            if active and zone in self.config.overnight_zones:
                self._night_count += 1

    # -- evaluation ----------------------------------------------------------

    def evaluate(self, now: float | None = None) -> list[WellnessIncident]:
        """Apply every rule at wall time ``now``; emit + return what fired."""
        now = self._now(now)
        fired: list[WellnessIncident] = []
        for rule in (self._morning_check, self._overnight_check, self._extended_stillness):
            fired.extend(rule(now))
        for incident in fired:
            self.emitter.emit(incident)
        return fired

    def tick(self, frames, now: float | None = None) -> list[WellnessIncident]:
        """Observe a batch of readings then evaluate — the poller's unit of work."""
        now = self._now(now)
        for frame in frames:
            self.observe(frame, now)
        return self.evaluate(now)

    # -- rule 1: morning check ----------------------------------------------

    def _morning_check(self, now: float) -> list[WellnessIncident]:
        if not self.config.morning_zones:
            return []
        if now < self._at(now, self.config.morning_deadline):
            return []
        day = datetime.fromtimestamp(now).date().isoformat()
        if self._fired.get("morning") == day:
            return []
        if not self._presence_now(now):
            return []
        midnight = self._at(now, (0, 0))
        for zone in self.config.morning_zones:
            seen = self._last_active_zone.get(zone)
            if seen is not None and seen >= midnight:
                return []  # they were up and about — normal morning
        self._fired["morning"] = day
        return [self._incident(
            LATE_START, now, ",".join(self.config.morning_zones),
            "no motion in morning zones by deadline while presence detected",
            dedupe_key=f"morning:{day}")]

    # -- rule 2: overnight stillness ----------------------------------------

    def _overnight_check(self, now: float) -> list[WellnessIncident]:
        if self._night_key is None:
            return []
        # The night ends at overnight_end (08:00) on the morning after its date.
        end = self._at(self._night_key_epoch() + 86400 + 1, self.config.overnight_end)
        if now < end:
            return []
        key = self._night_key.isoformat()
        if key in self._finalized_nights:
            return []
        self._finalized_nights.add(key)
        count = self._night_count
        fired: list[WellnessIncident] = []
        if self._overnight_fires(count):
            mean = sum(self._baseline) / len(self._baseline)
            fired.append(self._incident(
                PROLONGED_STILLNESS, now, ",".join(self.config.overnight_zones),
                f"overnight motion {count} far below baseline {mean:.1f}",
                dedupe_key=f"overnight:{key}"))
        self._baseline.append(count)  # learn tonight into the trailing baseline
        return fired

    def _overnight_fires(self, count: int) -> bool:
        if len(self._baseline) < self.config.min_baseline_nights:
            return False
        mean = sum(self._baseline) / len(self._baseline)
        return (mean - count) >= self.config.overnight_deviation

    def _night_key_epoch(self) -> float:
        d = self._night_key
        return datetime(d.year, d.month, d.day).timestamp()

    # -- rule 3: extended daytime absence of motion -------------------------

    def _extended_stillness(self, now: float) -> list[WellnessIncident]:
        if not self._is_daytime(now) or not self._presence_now(now):
            return []
        anchor = self._last_active_any
        if anchor is None:
            anchor = self._observing_since
        if anchor is None:
            return []
        if now - anchor < self.config.max_still_hours * 3600:
            return []
        episode = f"{anchor:.0f}"
        if self._fired.get("stillness") == episode:
            return []
        self._fired["stillness"] = episode
        hours = (now - anchor) / 3600
        return [self._incident(
            PROLONGED_STILLNESS, now, "home",
            f"presence with no motion on any node for {hours:.1f}h (daytime)",
            dedupe_key=f"stillness:{episode}")]

    # -- helpers -------------------------------------------------------------

    def _incident(self, type_: str, now: float, zone: str, detail: str,
                  *, dedupe_key: str) -> WellnessIncident:
        return WellnessIncident(
            type=type_, zone=zone, detail=detail, detected_at=now,
            home_name=self.config.home_name, severity=SOFT, dedupe_key=dedupe_key)


# -- production poller -------------------------------------------------------


@dataclass
class RoutinesRunner:
    """Polls the latest reading per node from SQLite and ticks the engine.

    The wellness rules sample state (current presence + motion per node) on each
    interval rather than replaying every packet — they reason about absence over
    hours, so a 5-minute sample is plenty and keeps the poll cheap.
    """

    engine: RoutinesEngine
    session_factory: object

    def tick(self, now: float | None = None) -> list[WellnessIncident]:
        from sqlalchemy import func, select

        from .db import Reading

        now = self.engine._now(now)
        with self.session_factory() as session:
            latest_ids = (
                select(func.max(Reading.id)).group_by(Reading.node_id).scalar_subquery()
            )
            rows = session.execute(
                select(Reading).where(Reading.id.in_(latest_ids))
            ).scalars().all()
            frames = [Frame.from_reading(r) for r in rows]
        return self.engine.tick(frames, now)


def main() -> None:  # pragma: no cover - operational entrypoint
    import time as _time

    from .db import make_engine, make_session_factory
    from .logconfig import configure_logging

    configure_logging("ingest")
    config = load_config()
    engine = RoutinesEngine(config, emitter=HttpIncidentEmitter())
    sessions = make_session_factory(make_engine())
    runner = RoutinesRunner(engine, sessions)
    interval = float(os.environ.get(
        "HEARTH_ROUTINES_POLL_SECS", config.eval_interval_secs))
    log.info("wellness routines poller up (every %.0fs)", interval)
    while True:
        runner.tick()
        _time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
