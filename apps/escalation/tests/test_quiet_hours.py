"""Per-type quiet-hours behavior (BAT-31 goal: each type defines its own).

A ``quiet_hours: defer`` policy holds its *delayed* paging steps until the home's
quiet window ends; its immediate (T+0) step still goes out. A ``quiet_hours: page``
policy (P1 fall) ignores quiet hours entirely. Times are built from local
wall-clock via ``datetime`` so the test is timezone-independent.
"""

from __future__ import annotations

from datetime import datetime

from .conftest import FakeClock, make_engine


def _epoch(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, 0).timestamp()


def _kinds(engine, incident_id):
    return [e.kind for e in engine.timeline(incident_id)]


def test_defer_policy_holds_delayed_step_until_window_end(db_url, config):
    # 23:00 local — inside the default 21:00–08:00 quiet window.
    clock = FakeClock(_epoch(2026, 6, 11, 23, 0))
    engine = make_engine(db_url, config, clock)

    incident, _ = engine.raise_incident("wellness.prolonged_stillness", room="home")
    # The immediate T+0 push still pages, even in quiet hours.
    assert "push_sent" in _kinds(engine, incident.id)

    # 23:31 — the T+30m SMS comes due but is deferred (delayed paging step).
    clock.set(_epoch(2026, 6, 11, 23, 31))
    engine.process_due()
    assert "sms_sent" not in _kinds(engine, incident.id)
    assert "deferred_quiet_hours" in _kinds(engine, incident.id)
    assert "sms" not in {m.channel for m in engine.notifier.sent}

    # The deferred deadline lands at the window's end (08:00 next morning).
    expected_end = _epoch(2026, 6, 12, 8, 0)
    assert engine.get(incident.id).next_action_at == expected_end

    # Once the window ends, the held SMS finally fires.
    clock.set(expected_end + 1)
    engine.process_due()
    assert "sms_sent" in _kinds(engine, incident.id)


def test_fall_pages_through_quiet_hours(db_url, config):
    # P1 fall.confirmed is quiet_hours: page — the voice ladder fires at T+60
    # even at 02:00, with no deferral.
    clock = FakeClock(_epoch(2026, 6, 11, 2, 0))
    engine = make_engine(db_url, config, clock)
    incident_id = engine.escalate(room="kitchen")
    clock.set(_epoch(2026, 6, 11, 2, 0) + 60)
    engine.process_due()
    kinds = _kinds(engine, incident_id)
    assert "voice_calls" in kinds
    assert "deferred_quiet_hours" not in kinds
