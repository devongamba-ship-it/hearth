"""Coarse home-status derivation for the caregiver app (BAT-25).

The caregiver web app never sees raw readings — only states and coarse activity
blocks (CLAUDE.md: privacy is the brand). This module turns the local
``readings`` + ``fall_transitions`` tables into the binary "Mom is okay" answer
the Home status card shows, plus the state-machine replay the Alerts screen
draws.

Two honest limitations shape what is *derived* vs. *stubbed*:

* Readings carry only ``ts`` (ms since the node booted), never wall-clock time,
  so per-day / time-of-day aggregation (the activity ring, the day timeline, the
  weekly trend cards) cannot be computed from what is stored. Those are returned
  as clearly-flagged ``stub`` buckets until a wall-clock-stamped activity store
  exists (a follow-up issue). Recency ("2 min ago") is computed as a delta of
  ms-since-boot against the newest reading, which tracks real elapsed seconds
  within one contiguous run (e.g. the packet simulator).
* The live status card also reflects an active fall cascade and "claimed by …",
  but those live in the escalation service's incident table. The caregiver app
  composes the two sources client-side; this module owns only the ingest half:
  ``all_good`` / ``sleeping`` / ``checking_in`` / ``no_activity`` plus the most
  severe fall-machine state across nodes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .detection import DetectionConfig

# Fall-machine states (mirrors ingest.fall_machine.State without importing the
# whole machine). Ordered by severity for "most severe across nodes".
_STATE_SEVERITY = {
    "NORMAL": 0,
    "SUSPECTED": 1,
    "PROBABLE": 2,
    "CHECK_IN_ACTIVE": 3,
    "CONFIRMED_FALL": 4,
}

PRIVACY_NOTICE = "Coarse status only. No cameras, no audio recording."


@dataclass(frozen=True)
class LatestReading:
    """The fields of a node's latest reading the status view needs."""

    node_id: int
    ts: int
    presence: bool
    motion: float


@dataclass(frozen=True)
class LastMotion:
    """Where and when motion was last seen (ms-since-boot)."""

    node_id: int
    ts: int


def most_severe_state(states: dict[int, str]) -> str:
    """The highest-severity fall-machine state across nodes (default NORMAL)."""
    if not states:
        return "NORMAL"
    return max(states.values(), key=lambda s: _STATE_SEVERITY.get(s, 0))


def derive_status(
    latest: list[LatestReading],
    node_states: dict[int, str],
    last_motion: LastMotion | None,
    config: DetectionConfig,
    *,
    now_hour: int,
) -> dict:
    """Build the coarse Home status-card payload from the local tables.

    ``now_hour`` is the wall-clock hour (0–23); only the quiet-hours / "sleeping"
    decision consults it, exactly like the fall machine's stillness boost.
    """
    fall_state = most_severe_state(node_states)
    presence = any(r.presence for r in latest)
    now_ts = max((r.ts for r in latest), default=0)

    last_activity = None
    if last_motion is not None:
        last_activity = {
            "room": config.room_for(last_motion.node_id),
            "node_id": last_motion.node_id,
            "seconds_ago": max(0, (now_ts - last_motion.ts) // 1000),
        }

    state, headline, detail = _classify(
        latest, fall_state, presence, last_activity, config, now_hour
    )

    return {
        "home_id": config.home_name or "home",
        "state": state,
        "headline": headline,
        "detail": detail,
        "presence": presence,
        "fall_state": fall_state,
        "last_activity": last_activity,
        "activity_ring": _activity_ring_stub(latest, config),
        "as_of_uptime_ms": now_ts,
        "privacy": PRIVACY_NOTICE,
    }


def _classify(latest, fall_state, presence, last_activity, config, now_hour):
    """(state, headline, detail) for the status card. Pure, total over inputs."""
    if not latest:
        return "no_activity", "No data yet", "Waiting for the first reading."

    # An in-flight (but not yet confirmed) fall: the resident check-in is running.
    if fall_state in ("PROBABLE", "CHECK_IN_ACTIVE"):
        return ("checking_in", "Quiet — checking in",
                "A possible fall is being checked. No alert has gone out yet.")

    if not presence:
        return ("no_activity", "Quiet — no one home",
                "No presence detected right now.")

    still = _all_still(latest, config, now_hour)
    if config.quiet_hours.contains(now_hour) and still:
        return "sleeping", "Sleeping", "Resting quietly during the night."

    if last_activity is not None:
        room = last_activity["room"]
        ago = _humanize_ago(last_activity["seconds_ago"])
        return "all_good", "All good", f"Last activity: {room}, {ago}."
    return "all_good", "All good", "At home and settled."


def _all_still(latest, config: DetectionConfig, now_hour: int) -> bool:
    threshold = config.stillness_threshold_at(now_hour)
    return all(r.motion < threshold for r in latest)


def _humanize_ago(seconds: int) -> str:
    if seconds < 60:
        return "just now" if seconds < 30 else "under a minute ago"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} min ago"
    hours = minutes // 60
    return f"{hours} hr ago" if hours < 24 else f"{hours // 24} day(s) ago"


def _activity_ring_stub(latest, config: DetectionConfig) -> dict:
    """Today's activity ring vs. a typical day.

    STUB: a per-day active-minutes total needs wall-clock-stamped history the
    ``readings`` table does not carry. We surface a coarse *recent* active
    fraction (share of the latest per-node readings that show real motion) so the
    ring is not wholly synthetic, and flag it ``stub`` so the client can label it.
    """
    threshold = config.stillness_threshold
    active_nodes = sum(1 for r in latest if r.motion >= threshold)
    fraction = active_nodes / len(latest) if latest else 0.0
    typical = 160
    today = int(round(fraction * 180))
    return {
        "stub": True,
        "active_minutes_today": today,
        "typical_active_minutes": typical,
        "pct_of_typical": int(round(100 * today / typical)) if typical else 0,
        "active_fraction_recent": round(fraction, 2),
    }


def current_hour(clock) -> int:
    """Wall-clock hour from an injected clock (testable)."""
    return datetime.fromtimestamp(clock()).hour
