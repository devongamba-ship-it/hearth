"""Frozen-time unit tests for the BAT-24 wellness rules engine (acceptance #3).

Every rule is exercised with explicit wall-clock epochs (no real clock), so the
time-of-day logic is fully deterministic. Also pins the soft-severity contract
(acceptance #2): nothing this engine emits can trigger a voice cascade.
"""

from __future__ import annotations

from collections import deque
from datetime import date, datetime

from ingest.fall_machine import Frame
from ingest.routines import (
    LATE_START,
    PROLONGED_STILLNESS,
    SOFT,
    HttpIncidentEmitter,
    NullIncidentEmitter,
    RoutinesEngine,
    WellnessIncident,
    load_config_from_text,
)


def epoch(y, mo, d, h=0, mi=0, s=0) -> float:
    return datetime(y, mo, d, h, mi, s).timestamp()


def _frame(node_id, motion, presence=True) -> Frame:
    return Frame(node_id=node_id, ts=0, presence=presence, motion=float(motion),
                 fall_flag=False)


def _engine(zones="zones: {1: kitchen, 3: bathroom}") -> tuple[RoutinesEngine, NullIncidentEmitter]:
    emitter = NullIncidentEmitter()
    return RoutinesEngine(load_config_from_text(zones), emitter=emitter), emitter


# -- rule 1: morning check --------------------------------------------------


def test_morning_check_fires_once_when_present_and_still():
    eng, em = _engine()
    # 09:00 — present, only faint motion (below the active threshold).
    eng.observe(_frame(1, motion=40), now=epoch(2026, 6, 10, 9, 0))
    assert eng.evaluate(now=epoch(2026, 6, 10, 9, 0)) == []  # before deadline

    eng.observe(_frame(1, motion=40), now=epoch(2026, 6, 10, 10, 31))
    fired = eng.evaluate(now=epoch(2026, 6, 10, 10, 31))
    assert [i.type for i in fired] == [LATE_START]
    assert fired[0].severity == SOFT

    # Deduped for the rest of the day.
    eng.observe(_frame(1, motion=40), now=epoch(2026, 6, 10, 10, 45))
    assert eng.evaluate(now=epoch(2026, 6, 10, 10, 45)) == []


def test_morning_check_silent_when_they_were_up():
    eng, em = _engine()
    # Real motion in the kitchen at 08:00 — a normal morning.
    eng.observe(_frame(1, motion=900), now=epoch(2026, 6, 10, 8, 0))
    eng.observe(_frame(1, motion=40), now=epoch(2026, 6, 10, 10, 31))
    assert eng.evaluate(now=epoch(2026, 6, 10, 10, 31)) == []


def test_morning_check_silent_when_nobody_home():
    eng, em = _engine()
    # Presence absent (they're out) — no concern.
    eng.observe(_frame(1, motion=40, presence=False), now=epoch(2026, 6, 10, 10, 31))
    assert eng.evaluate(now=epoch(2026, 6, 10, 10, 31)) == []


# -- rule 2: overnight stillness -------------------------------------------


def test_overnight_fires_when_tonight_far_below_baseline():
    eng, em = _engine()
    eng._baseline = deque([2, 2, 3], maxlen=14)
    eng._night_key = date(2026, 6, 9)
    eng._night_count = 0  # nobody got up at all last night
    fired = eng.evaluate(now=epoch(2026, 6, 10, 8, 1))
    assert [i.type for i in fired] == [PROLONGED_STILLNESS]
    assert fired[0].severity == SOFT
    # Tonight is learned into the trailing baseline.
    assert list(eng._baseline) == [2, 2, 3, 0]


def test_overnight_silent_when_night_is_normal():
    eng, em = _engine()
    eng._baseline = deque([2, 2, 3], maxlen=14)
    eng._night_key = date(2026, 6, 9)
    eng._night_count = 2  # a normal couple of bathroom trips
    assert eng.evaluate(now=epoch(2026, 6, 10, 8, 1)) == []


def test_overnight_silent_without_enough_baseline():
    eng, em = _engine()
    eng._baseline = deque([2], maxlen=14)  # only one night of history
    eng._night_key = date(2026, 6, 9)
    eng._night_count = 0
    assert eng.evaluate(now=epoch(2026, 6, 10, 8, 1)) == []


def test_overnight_silent_before_window_ends():
    eng, em = _engine()
    eng._baseline = deque([2, 2, 3], maxlen=14)
    eng._night_key = date(2026, 6, 9)
    eng._night_count = 0
    # 03:00 — still inside the overnight window, not yet judged.
    assert eng.evaluate(now=epoch(2026, 6, 10, 3, 0)) == []


def test_overnight_accumulates_bathroom_motion_via_observe():
    eng, em = _engine()
    eng.observe(_frame(3, motion=600), now=epoch(2026, 6, 9, 23, 0))  # bathroom trip
    eng.observe(_frame(3, motion=30), now=epoch(2026, 6, 10, 2, 0))   # still
    eng.observe(_frame(3, motion=700), now=epoch(2026, 6, 10, 3, 0))  # another trip
    assert eng._night_key == date(2026, 6, 9)
    assert eng._night_count == 2


# -- rule 3: extended daytime absence of motion ----------------------------


def test_extended_stillness_fires_after_threshold_daytime():
    eng, em = _engine()
    # Last real motion at 10:00; present but still since.
    eng.observe(_frame(1, motion=900), now=epoch(2026, 6, 10, 10, 0))
    eng.observe(_frame(1, motion=30), now=epoch(2026, 6, 10, 13, 30))
    assert eng.evaluate(now=epoch(2026, 6, 10, 13, 30)) == []  # only 3.5h
    eng.observe(_frame(1, motion=30), now=epoch(2026, 6, 10, 14, 30))
    fired = eng.evaluate(now=epoch(2026, 6, 10, 14, 30))  # 4.5h
    assert [i.type for i in fired] == [PROLONGED_STILLNESS]
    assert fired[0].severity == SOFT
    # Deduped until motion resumes.
    eng.observe(_frame(1, motion=30), now=epoch(2026, 6, 10, 15, 0))
    assert eng.evaluate(now=epoch(2026, 6, 10, 15, 0)) == []


def test_extended_stillness_resets_when_motion_resumes():
    eng, em = _engine()
    eng.observe(_frame(1, motion=900), now=epoch(2026, 6, 10, 10, 0))
    eng.observe(_frame(1, motion=30), now=epoch(2026, 6, 10, 14, 30))
    assert eng.evaluate(now=epoch(2026, 6, 10, 14, 30))  # fires
    # They get up — fresh episode anchor.
    eng.observe(_frame(1, motion=900), now=epoch(2026, 6, 10, 15, 0))
    eng.observe(_frame(1, motion=30), now=epoch(2026, 6, 10, 19, 30))
    fired = eng.evaluate(now=epoch(2026, 6, 10, 19, 30))  # 4.5h since 15:00
    assert [i.type for i in fired] == [PROLONGED_STILLNESS]


def test_extended_stillness_silent_at_night():
    eng, em = _engine()
    eng.observe(_frame(1, motion=900), now=epoch(2026, 6, 10, 10, 0))
    eng.observe(_frame(1, motion=30), now=epoch(2026, 6, 10, 23, 0))
    assert eng.evaluate(now=epoch(2026, 6, 10, 23, 0)) == []  # not daytime


def test_extended_stillness_silent_without_presence():
    eng, em = _engine()
    eng.observe(_frame(1, motion=900, presence=False), now=epoch(2026, 6, 10, 10, 0))
    eng.observe(_frame(1, motion=30, presence=False), now=epoch(2026, 6, 10, 15, 0))
    assert eng.evaluate(now=epoch(2026, 6, 10, 15, 0)) == []


# -- acceptance #2: soft alerts never trigger voice -------------------------


def test_every_emitted_incident_is_soft_sms_tier():
    """The engine must only ever emit soft, wellness-typed incidents — the
    SMS-only tier. A soft incident is what the registry (BAT-31) routes without
    a voice cascade; the rules engine has no voice path of its own."""
    eng, em = _engine()
    # Drive every rule to fire at least once.
    eng._baseline = deque([2, 2, 3], maxlen=14)
    eng._night_key = date(2026, 6, 9)
    eng._night_count = 0
    eng.observe(_frame(1, motion=40), now=epoch(2026, 6, 10, 10, 31))
    eng.evaluate(now=epoch(2026, 6, 10, 10, 31))
    eng.evaluate(now=epoch(2026, 6, 10, 13, 1))  # extended stillness too

    assert em.calls, "expected the engine to have emitted concerns"
    for incident in em.calls:
        assert incident.severity == SOFT
        assert incident.type.startswith("wellness.")
        # Nothing in the payload requests a voice channel.
        assert "voice" not in incident.payload()
        assert incident.payload()["severity"] == SOFT


def test_http_emitter_payload_carries_type_and_soft_severity():
    incident = WellnessIncident(
        type=LATE_START, zone="kitchen", detail="x", detected_at=0.0,
        home_name="Rosewood")
    payload = incident.payload()
    assert payload["type"] == LATE_START
    assert payload["severity"] == SOFT
    assert payload["room"] == "kitchen"  # /escalate requires 'room'
    assert payload["source"] == "routines"


def test_http_emitter_swallows_delivery_failure():
    # No escalation service listening -> fire-and-forget returns False, no raise.
    emitter = HttpIncidentEmitter(url="http://127.0.0.1:1/escalate", retries=0, timeout=1.0)
    incident = WellnessIncident(type=LATE_START, zone="kitchen", detail="x",
                                detected_at=0.0)
    assert emitter.emit(incident) is False


# -- config -----------------------------------------------------------------


def test_config_parses_zones_and_clock_fields():
    cfg = load_config_from_text(
        """
        home_name: Rosewood
        zones: {1: kitchen, 2: living, 3: bathroom}
        morning_deadline: "11:00"
        overnight_start: "22:00"
        max_still_hours: 6
        baseline_nights: 21
        """
    )
    assert cfg.home_name == "Rosewood"
    assert cfg.zone_for(3) == "bathroom"
    assert cfg.morning_deadline == (11, 0)
    assert cfg.overnight_start == (22, 0)
    assert cfg.max_still_hours == 6
    assert cfg.baseline_nights == 21


def test_config_defaults_when_empty():
    cfg = load_config_from_text("{}")
    assert cfg.morning_deadline == (10, 30)
    assert cfg.morning_zones == ("kitchen", "living")
    assert cfg.active_motion_threshold == 200.0
    assert cfg.max_still_hours == 4.0
