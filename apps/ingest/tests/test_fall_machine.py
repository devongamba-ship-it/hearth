"""Unit tests for every transition of the BAT-22 fall state machine + dedupe.

The machine is reading-driven and deterministic: we feed synthetic frames with
explicit timestamps and assert the resulting state, the escalation calls, and
the persisted transition log.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from ingest import db
from ingest.checkin import CheckInResult, StubCheckInProvider
from ingest.detection import DetectionConfig, QuietHours
from ingest.escalator import NullEscalator
from ingest.fall_machine import FallMachine, Frame, State

# A fixed wall clock so quiet-hours logic is deterministic (noon = not night).
NOON = 1_700_000_000.0 - (1_700_000_000.0 % 86400) + 12 * 3600


def frame(node_id, t, *, presence=True, motion=1000.0, fall=False):
    """One frame at second ``t`` (ts in ms-since-boot)."""
    return Frame(node_id=node_id, ts=t * 1000, presence=presence, motion=motion,
                 fall_flag=fall, breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9,
                 occupancy=1)


def make_machine(session_factory=None, **cfg):
    config = DetectionConfig(**cfg)
    return FallMachine(
        config, escalator=NullEscalator(), checkin=StubCheckInProvider(),
        session_factory=session_factory, clock=lambda: NOON,
    )


# -- the happy(ish) path: NORMAL -> SUSPECTED -> PROBABLE -> CHECK_IN -> CONFIRM


def test_fall_flag_enters_suspected():
    m = make_machine()
    assert m.step(frame(1, 0, motion=9500, fall=True)) is State.SUSPECTED


def test_sustained_stillness_confirms_and_escalates():
    m = make_machine()
    m.step(frame(1, 0, motion=9500, fall=True))  # impact -> SUSPECTED
    for t in range(1, 21):  # 20s of stillness -> PROBABLE -> CHECK_IN_ACTIVE
        m.step(frame(1, t, motion=20.0))
    assert m.state_of(1) is State.CHECK_IN_ACTIVE
    assert m.escalator.calls == []  # nothing yet — the check-in window is open

    # Drive the 45s stub check-in window to its end.
    for t in range(21, 67):
        m.step(frame(1, t, motion=20.0))
    assert m.state_of(1) is State.CONFIRMED_FALL
    assert len(m.escalator.calls) == 1
    call = m.escalator.calls[0]
    assert call.node_id == 1
    assert call.room == "node-1"


def test_confirm_fires_after_full_checkin_timeout_not_before():
    m = make_machine()
    m.step(frame(1, 0, motion=9500, fall=True))
    for t in range(1, 21):
        m.step(frame(1, t, motion=20.0))
    # check-in started at t=20 (ts=20000); timeout is 45s.
    m.step(frame(1, 64, motion=20.0))  # 44s elapsed -> still open
    assert m.state_of(1) is State.CHECK_IN_ACTIVE
    assert m.escalator.calls == []
    m.step(frame(1, 65, motion=20.0))  # 45s elapsed -> confirm
    assert m.state_of(1) is State.CONFIRMED_FALL
    assert len(m.escalator.calls) == 1


# -- the dismissals (near_fall): every way SUSPECTED/CHECK_IN goes back to NORMAL


def test_getting_up_dismisses_as_near_fall():
    m = make_machine()
    m.step(frame(1, 0, motion=9500, fall=True))
    for t in range(1, 6):  # brief stillness
        m.step(frame(1, t, motion=20.0))
    for t in range(6, 16):  # 10s of sustained normal motion -> got up
        m.step(frame(1, t, motion=2000.0))
    assert m.state_of(1) is State.NORMAL
    assert m.escalator.calls == []
    near = [t for t in m.transitions if t.kind == db.NEAR_FALL]
    assert len(near) == 1
    assert near[0].reason == "got_up"


def test_presence_lost_dismisses():
    m = make_machine()
    m.step(frame(1, 0, motion=9500, fall=True))
    m.step(frame(1, 1, motion=20.0))
    m.step(frame(1, 2, motion=20.0, presence=False))  # walked out of the room
    assert m.state_of(1) is State.NORMAL
    near = [t for t in m.transitions if t.kind == db.NEAR_FALL]
    assert near and near[0].reason == "presence_lost"
    assert m.escalator.calls == []


def test_suspected_window_elapses_without_confirmation():
    # Motion oscillates: never 20s cumulative stillness, never 10s run of motion.
    m = make_machine()
    m.step(frame(1, 0, motion=9500, fall=True))
    for t in range(1, 35):
        motion = 20.0 if t % 2 else 2000.0  # alternate still / moving
        m.step(frame(1, t, motion=motion))
    assert m.state_of(1) is State.NORMAL
    assert m.escalator.calls == []
    assert any(tr.reason == "suspected_window_elapsed" for tr in m.transitions)


def test_checkin_answered_dismisses_without_escalating():
    class AnsweringProvider(StubCheckInProvider):
        def poll(self, node_id, now_ms):
            # Resident says "I'm fine" 5s into the check-in.
            return CheckInResult.OK if now_ms >= 25_000 else CheckInResult.PENDING

    m = make_machine()
    m.checkin = AnsweringProvider()
    m.step(frame(1, 0, motion=9500, fall=True))
    for t in range(1, 21):
        m.step(frame(1, t, motion=20.0))
    assert m.state_of(1) is State.CHECK_IN_ACTIVE
    m.step(frame(1, 26, motion=20.0))  # provider now reports a response
    assert m.state_of(1) is State.NORMAL
    assert m.escalator.calls == []
    assert any(tr.reason == "checkin_answered" for tr in m.transitions)


# -- dedupe: one CONFIRMED_FALL per node per window


def _drive_to_confirm(m, node, start_t):
    """Feed a full impact->confirm sequence; return the second after confirm."""
    m.step(frame(node, start_t, motion=9500, fall=True))
    t = start_t + 1
    while m.state_of(node) is not State.CONFIRMED_FALL:
        m.step(frame(node, t, motion=20.0))
        t += 1
    return t


def test_dedupe_suppresses_second_escalation_within_window():
    m = make_machine(dedupe_window_secs=300)
    t = _drive_to_confirm(m, 1, 0)
    assert len(m.escalator.calls) == 1
    # Person briefly moves (reset to NORMAL), then falls again 60s later.
    for _ in range(10):
        m.step(frame(1, t, motion=3000.0))
        t += 1
    assert m.state_of(1) is State.NORMAL
    _drive_to_confirm(m, 1, t)
    # Second confirm happened well within 5 min -> escalation deduped.
    assert len(m.escalator.calls) == 1


def test_second_fall_outside_window_escalates_again():
    m = make_machine(dedupe_window_secs=60)  # short window for the test
    t = _drive_to_confirm(m, 1, 0)
    assert len(m.escalator.calls) == 1
    for _ in range(10):
        m.step(frame(1, t, motion=3000.0))
        t += 1
    # Jump well past the 60s dedupe window before the next fall.
    t += 120
    _drive_to_confirm(m, 1, t)
    assert len(m.escalator.calls) == 2


def test_nodes_are_independent():
    m = make_machine()
    m.step(frame(1, 0, motion=9500, fall=True))
    m.step(frame(2, 0, motion=1000.0))  # node 2 just lives its life
    assert m.state_of(1) is State.SUSPECTED
    assert m.state_of(2) is State.NORMAL


# -- suppressions (the expressible ones)


def test_fall_flag_without_presence_is_suppressed():
    m = make_machine()
    state = m.step(frame(1, 0, motion=9500, fall=True, presence=False))
    assert state is State.NORMAL  # never even entered SUSPECTED
    sup = [t for t in m.transitions if t.kind == db.SUPPRESSED]
    assert len(sup) == 1
    assert sup[0].reason == "no_presence"
    assert m.escalator.calls == []


def test_in_bed_stillness_during_quiet_hours_is_suppressed():
    night = NOON + 12 * 3600  # midnight UTC
    # Pin the home's timezone explicitly so "night" is night on every host —
    # without this the suppression evaluates in the host's local time and the
    # test is green only on a UTC machine (BAT-32).
    m = FallMachine(
        DetectionConfig(
            bedroom_nodes=(2,),
            quiet_hours=QuietHours(enabled=True, start_hour=22, end_hour=7),
            timezone="UTC",
        ),
        escalator=NullEscalator(), clock=lambda: night,
    )
    state = m.step(frame(2, 0, motion=10.0, fall=True))  # still, in bed, at night
    assert state is State.NORMAL
    sup = [t for t in m.transitions if t.kind == db.SUPPRESSED]
    assert sup and sup[0].reason == "in_bed_sleep_window"
    assert m.escalator.calls == []


def test_quiet_hours_resolves_hour_in_configured_timezone_not_host():
    # One UTC instant, two homes: 14:00 UTC is mid-afternoon in UTC (awake) but
    # ~02:00 the next day in Auckland (asleep). The suppression must follow the
    # configured zone, not whatever timezone the test host happens to run in —
    # so this assertion holds identically on a UTC CI box and an ET dev laptop.
    instant = datetime(2026, 1, 15, 14, 0, tzinfo=timezone.utc).timestamp()

    def machine(tz):
        return FallMachine(
            DetectionConfig(
                bedroom_nodes=(2,),
                quiet_hours=QuietHours(enabled=True, start_hour=22, end_hour=7),
                timezone=tz,
            ),
            escalator=NullEscalator(), clock=lambda: instant,
        )

    # 14:00 UTC -> daytime -> a fall flag is taken seriously (not suppressed).
    awake = machine("UTC")
    assert awake.step(frame(2, 0, motion=10.0, fall=True)) is State.SUSPECTED
    assert [t for t in awake.transitions if t.kind == db.SUPPRESSED] == []

    # Same instant, Auckland (NZDT, UTC+13 in January) -> ~03:00, inside quiet
    # hours -> in-bed stillness is suppressed.
    asleep = machine("Pacific/Auckland")
    assert asleep.step(frame(2, 0, motion=10.0, fall=True)) is State.NORMAL
    sup = [t for t in asleep.transitions if t.kind == db.SUPPRESSED]
    assert sup and sup[0].reason == "in_bed_sleep_window"


# -- persistence: every transition is queryable per incident (for BAT-25)


def test_transitions_persisted_per_incident(session_factory):
    m = make_machine(session_factory=session_factory)
    _drive_to_confirm(m, 1, 0)
    incident_id = m.transitions[0].incident_id
    rows = m.incident(incident_id)
    states = [(r.from_state, r.to_state) for r in rows]
    assert ("NORMAL", "SUSPECTED") in states
    assert ("PROBABLE", "CHECK_IN_ACTIVE") in states
    assert ("CHECK_IN_ACTIVE", "CONFIRMED_FALL") in states
    # Feature snapshot is stored as JSON for later model tuning.
    assert all(r.features for r in rows)
    assert all(r.at > 0 for r in rows)  # wall-clock timestamps for replay


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
