"""Timers survive restarts (canonical design update, point 7).

A cascade is started, the process "restarts" (a fresh engine over the same
SQLite file), and the new engine resumes from the persisted ``next_action_at``
deadline — no in-memory timer state is required.
"""

from __future__ import annotations

from escalation import db
from escalation.engine import OPERATOR_DELAY, VOICE_DELAY
from escalation.notifier import DryRunNotifier

from .conftest import FakeClock, make_engine


def test_restart_resumes_from_persisted_deadline(db_url, config):
    # First process: start a cascade at T+0 and shut down before the voice step.
    clock1 = FakeClock(0)
    engine1 = make_engine(db_url, config, clock1, notifier=DryRunNotifier())
    incident_id = engine1.escalate(room="kitchen")

    persisted = engine1.get(incident_id)
    assert persisted.next_action == db.STEP_VOICE
    assert persisted.next_action_at == VOICE_DELAY  # deadline written to SQLite

    # Restart: brand-new engine, brand-new notifier, same DB file. Clock is now
    # past the voice deadline.
    clock2 = FakeClock(VOICE_DELAY + 5)
    engine2 = make_engine(db_url, config, clock2, notifier=DryRunNotifier())

    # Recovery re-drives the persisted deadline.
    fired = engine2.process_due()
    assert fired == 1
    kinds = [e.kind for e in engine2.timeline(incident_id)]
    assert "voice_calls" in kinds
    assert [m.channel for m in engine2.notifier.sent].count("voice") == 2

    # And it keeps advancing on the recovered timeline.
    clock2.set(OPERATOR_DELAY)
    assert engine2.process_due() == 1
    assert "operator_escalation" in [e.kind for e in engine2.timeline(incident_id)]


def test_pending_lists_unresolved_after_restart(db_url, config):
    clock1 = FakeClock(0)
    engine1 = make_engine(db_url, config, clock1)
    incident_id = engine1.escalate(room="den")

    clock2 = FakeClock(10)
    engine2 = make_engine(db_url, config, clock2)
    pending = engine2.pending()
    assert [i.id for i in pending] == [incident_id]
