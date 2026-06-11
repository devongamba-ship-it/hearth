"""Tunable thresholds for the BAT-22 fall-confirmation state machine.

Every number the machine uses to make a decision lives here, not in code — the
concept doc is explicit that these *will* be tuned per home, so they must be
data, loadable from a ``detection.yaml`` (env ``HEARTH_DETECTION_FILE``). The
class ships sensible defaults so the machine runs out of the box and tests can
construct a config without a file.

Quiet-hours sensitivity boost: at night we *lower* the stillness threshold (a
smaller motion energy still counts as "moving"), so a real fall is confirmed
from a fainter struggle — while the in-bed sleep-window suppression keeps a
sleeper at rest from tripping it. See :mod:`ingest.fall_machine`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml


@dataclass(frozen=True)
class QuietHours:
    """A nightly window with its own (lower) stillness threshold.

    ``start_hour`` may be greater than ``end_hour`` to wrap past midnight
    (e.g. 22 → 7). When ``in_bed_suppression`` is set, stillness inside the
    window on a bedroom node is logged ``suppressed`` rather than alerted.
    """

    enabled: bool = False
    start_hour: int = 22
    end_hour: int = 7
    stillness_threshold: float = 120.0
    in_bed_suppression: bool = True

    def contains(self, hour: int) -> bool:
        if not self.enabled:
            return False
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


@dataclass(frozen=True)
class DetectionConfig:
    """All fall-machine tunables. Seconds unless noted."""

    # Motion energy below this is "stillness"; at/above it is "normal motion".
    stillness_threshold: float = 200.0
    # SUSPECTED is abandoned (→ NORMAL) if nothing resolves within this window.
    suspected_window_secs: int = 30
    # Cumulative stillness within SUSPECTED that advances to PROBABLE.
    confirm_stillness_secs: int = 20
    # Consecutive seconds of normal motion that mean "they got up" (→ NORMAL).
    recovery_motion_secs: int = 10
    # CHECK_IN_ACTIVE wait before an unanswered check-in becomes CONFIRMED_FALL.
    checkin_timeout_secs: int = 45
    # At most one CONFIRMED_FALL per node within this window (dedupe).
    dedupe_window_secs: int = 300
    # Optional home label forwarded to the escalation service.
    home_name: str | None = None
    # node_id -> human room name, used in the /escalate payload.
    rooms: dict[int, str] = field(default_factory=dict)
    # node_ids that are bedrooms (eligible for in-bed sleep-window suppression).
    bedroom_nodes: tuple[int, ...] = ()
    quiet_hours: QuietHours = field(default_factory=QuietHours)
    # IANA name (e.g. "America/New_York") for the home's wall clock; quiet-hours
    # logic resolves the hour in this zone. ``None`` means the host's local time
    # — correct on a device that lives in the home, but explicit injection makes
    # the behavior deterministic regardless of where the process runs (BAT-32).
    timezone: str | None = None

    def room_for(self, node_id: int) -> str:
        return self.rooms.get(node_id, f"node-{node_id}")

    def hour_at(self, now: float) -> int:
        """Wall-clock hour (0-23) of epoch ``now`` in the home's timezone.

        The single place an epoch becomes an hour-of-day; both the quiet-hours
        suppression and the stillness-threshold boost go through here so they
        never disagree, and neither silently inherits the host's timezone.
        """
        tz = ZoneInfo(self.timezone) if self.timezone else None
        return datetime.fromtimestamp(now, tz).hour

    def stillness_threshold_at(self, hour: int) -> float:
        """Stillness threshold for the given wall-clock hour (quiet-hours boost)."""
        if self.quiet_hours.contains(hour):
            return self.quiet_hours.stillness_threshold
        return self.stillness_threshold

    def threshold_for_clock(self, now: float) -> float:
        return self.stillness_threshold_at(self.hour_at(now))

    def is_bedroom(self, node_id: int) -> bool:
        return node_id in self.bedroom_nodes


def _parse(data: dict | None) -> DetectionConfig:
    if data is None:
        return DetectionConfig()
    if not isinstance(data, dict):
        raise ValueError("detection config must be a mapping")
    base = DetectionConfig()
    overrides: dict = {}
    for key in (
        "stillness_threshold",
        "suspected_window_secs",
        "confirm_stillness_secs",
        "recovery_motion_secs",
        "checkin_timeout_secs",
        "dedupe_window_secs",
        "home_name",
        "timezone",
    ):
        if key in data and data[key] is not None:
            overrides[key] = data[key]
    if "rooms" in data and data["rooms"]:
        overrides["rooms"] = {int(k): str(v) for k, v in data["rooms"].items()}
    if "bedroom_nodes" in data and data["bedroom_nodes"]:
        overrides["bedroom_nodes"] = tuple(int(n) for n in data["bedroom_nodes"])
    if "quiet_hours" in data and data["quiet_hours"]:
        qh = data["quiet_hours"]
        overrides["quiet_hours"] = replace(
            QuietHours(),
            **{k: v for k, v in qh.items() if v is not None and k in QuietHours().__dict__},
        )
    return replace(base, **overrides)


def load_config(path: str | os.PathLike | None = None) -> DetectionConfig:
    """Load tunables from ``path`` / ``$HEARTH_DETECTION_FILE``, else defaults."""
    resolved = path or os.environ.get("HEARTH_DETECTION_FILE")
    if not resolved:
        return DetectionConfig()
    text = Path(resolved).read_text(encoding="utf-8")
    return _parse(yaml.safe_load(text))


def load_config_from_text(text: str) -> DetectionConfig:
    """Parse a config from a YAML string (used by tests)."""
    return _parse(yaml.safe_load(text))
