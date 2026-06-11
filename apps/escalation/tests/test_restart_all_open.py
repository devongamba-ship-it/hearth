"""Restart mid-cascade resumes the correct next step for ALL open incidents
(BAT-31 acceptance 4).

Several incidents of different types are mid-ladder when the process dies. A
brand-new engine over the same SQLite file (no in-memory timer state) re-drives
every persisted deadline and advances each incident to its correct next step.
"""

from __future__ import annotations

from datetime import datetime

from escalation import db
from escalation.notifier import DryRunNotifier

from .conftest import FakeClock, make_engine

# A daytime base (10:00 local) so the wellness types' quiet-hours deferral does
# not confound this test — here we are isolating restart/resume behavior.
DAY = datetime(2026, 6, 11, 10, 0, 0).timestamp()


def _kinds(engine, incident_id):
    return [e.kind for e in engine.timeline(incident_id)]


def test_restart_resumes_all_open_incidents_at_correct_step(db_url, config):
    # First process: three incidents of different types, each mid-ladder.
    clock1 = FakeClock(DAY)
    engine1 = make_engine(db_url, config, clock1, notifier=DryRunNotifier())

    fall = engine1.escalate(room="kitchen")                  # next: voice @60
    still, _ = engine1.raise_incident(
        "wellness.prolonged_stillness", room="home")          # next: sms @1800
    gap, _ = engine1.raise_incident(
        "system.sensing_gap", room="home")                    # next: ops_queue @86400

    # Sanity: each persisted its own next deadline before the "crash".
    assert engine1.get(fall).next_action == "voice"
    assert engine1.get(still.id).next_action == "sms"
    assert engine1.get(gap.id).next_action == "ops_queue"

    # Restart: fresh engine + fresh notifier, same DB file, clock now past the
    # fall's whole sub-2-min ladder AND the stillness SMS (@1800), but not the
    # gap's 24h step. Every persisted deadline already passed is re-driven.
    clock2 = FakeClock(DAY + 2000)
    engine2 = make_engine(db_url, config, clock2, notifier=DryRunNotifier())
    fired = engine2.process_due()
    # fall: voice@60 + operator@120 (cascade completes); stillness: sms@1800.
    assert fired == 3

    assert "voice_calls" in _kinds(engine2, fall)
    assert "operator_escalation" in _kinds(engine2, fall)
    assert "sms_sent" in _kinds(engine2, still.id)
    # sensing_gap fired push + ops_queue at T+0; its T+24h escalate is not yet due.
    assert _kinds(engine2, gap.id).count("ops_queued") == 1

    # Each advanced to its own correct next step, independently of the others.
    assert engine2.get(fall).next_action is None             # fall ladder exhausted
    assert engine2.get(still.id).next_action == "ops_queue"  # stillness: sms -> ops@7200
    assert engine2.get(gap.id).next_action == "ops_queue"    # gap: still pending @86400

    # Drive past the gap's 24h deadline: it too resumes correctly from SQLite.
    clock2.set(DAY + 90_000)
    engine2.process_due()
    assert _kinds(engine2, gap.id).count("ops_queued") == 2  # T+24h escalate fired


def test_pending_lists_all_unresolved_types_after_restart(db_url, config):
    clock1 = FakeClock(0)
    engine1 = make_engine(db_url, config, clock1)
    a = engine1.escalate(room="den")
    b, _ = engine1.raise_incident("wellness.late_start", room="kitchen")

    clock2 = FakeClock(10)
    engine2 = make_engine(db_url, config, clock2)
    pending_ids = {i.id for i in engine2.pending()}
    assert pending_ids == {a, b.id}
    # An auto-resolved one drops out of pending after restart.
    engine2.signal_clear(b.id, now=20)
    assert {i.id for i in engine2.pending()} == {a}
    assert engine2.get(b.id).status == db.AUTO_RESOLVED
