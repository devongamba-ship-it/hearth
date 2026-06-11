"""Dry-run integration test for the cascade timeline (BAT-23 acceptance).

Simulated fall -> timeline logged with correct timing -> claim at T+95s pauses
the cascade -> no confirmation within 15 min resumes at the operator step.
"""

from __future__ import annotations

from escalation import db
from escalation.engine import CLAIM_CONFIRM_WINDOW, OPERATOR_DELAY, VOICE_DELAY
from escalation.notifier import PUSH, SMS, VOICE


def _kinds(engine, incident_id):
    return [e.kind for e in engine.timeline(incident_id)]


def test_fall_t0_pushes_circle_and_queues_ops(engine, clock):
    # BAT-31 registry table: T+0 is push circle + ops queue; SMS holds to T+60.
    clock.set(0)
    incident_id = engine.escalate(room="kitchen")

    timeline = engine.timeline(incident_id)
    kinds = [e.kind for e in timeline]
    assert kinds == ["incident_created", "push_sent", "ops_queued"]
    # P1 events logged at T+0.
    assert all(e.at == 0 for e in timeline)
    assert all(e.priority == "P1" for e in timeline)

    # Push reached the push-capable contact (Dana), with the ack instructions;
    # the ops queue got its notice; nobody got SMS yet.
    sent = engine.notifier.sent
    pushes = [m for m in sent if m.channel == PUSH]
    assert [m.to for m in pushes] == ["+15555550101"]
    assert all("Reply OK to acknowledge" in m.body for m in pushes)
    assert [m for m in sent if m.channel == SMS] == []
    assert [m.to for m in sent if m.channel == "ops"] == ["ops-queue"]


def test_voice_ladder_then_operator_timing(engine, clock):
    clock.set(0)
    incident_id = engine.escalate(room="kitchen")

    # Nothing is due before T+60.
    clock.set(VOICE_DELAY - 1)
    assert engine.process_due() == 0
    assert "voice_calls" not in _kinds(engine, incident_id)

    # T+60 fires the SMS step and the voice ladder (registry order).
    clock.set(VOICE_DELAY)
    assert engine.process_due() == 2
    sms = [m for m in engine.notifier.sent if m.channel == SMS]
    assert [m.to for m in sms] == ["+15555550101", "+15555550102"]
    voice_calls = [m for m in engine.notifier.sent if m.channel == VOICE]
    assert [m.to for m in voice_calls] == ["+15555550101", "+15555550102"]

    # Operator escalation fires at T+120.
    clock.set(OPERATOR_DELAY)
    assert engine.process_due() == 1
    timeline = engine.timeline(incident_id)
    by_kind = {e.kind: e.at for e in timeline}
    assert by_kind["voice_calls"] == VOICE_DELAY
    assert by_kind["operator_escalation"] == OPERATOR_DELAY


def test_claim_at_t95_pauses_then_resumes_at_operator(engine, clock):
    clock.set(0)
    incident_id = engine.escalate(room="bathroom")

    # Voice ladder fires at T+60.
    clock.set(VOICE_DELAY)
    engine.process_due()

    # Claim at T+95 pauses the cascade.
    clock.set(95)
    claimed = engine.claim(incident_id, by="Dana")
    assert claimed.status == db.CLAIMED
    assert claimed.next_action is None and claimed.next_action_at is None
    assert "claimed" in _kinds(engine, incident_id)
    # Every contact got a "claimed by" SMS.
    claim_sms = [m for m in engine.notifier.sent if "claimed by dana" in m.body.lower()]
    assert {m.to for m in claim_sms} == {"+15555550101", "+15555550102"}

    # The operator step would have been at T+120 — it must NOT fire while paused.
    clock.set(OPERATOR_DELAY)
    assert engine.process_due() == 0
    assert "operator_escalation" not in _kinds(engine, incident_id)

    # Still paused just before the confirm window closes.
    clock.set(95 + CLAIM_CONFIRM_WINDOW - 1)
    assert engine.process_due() == 0
    assert "operator_escalation" not in _kinds(engine, incident_id)

    # No confirmation within 15 min -> resume at the operator step.
    clock.set(95 + CLAIM_CONFIRM_WINDOW)
    assert engine.process_due() == 1
    kinds = _kinds(engine, incident_id)
    assert "claim_expired_resumed" in kinds
    assert "operator_escalation" in kinds
    assert engine.get(incident_id).status == db.ACTIVE


def test_confirmed_resolution_stops_cascade(engine, clock):
    clock.set(0)
    incident_id = engine.escalate(room="hall")
    clock.set(95)
    engine.claim(incident_id, by="Dana")

    # Claimer confirms resolution within the window.
    clock.set(200)
    resolved = engine.resolve(incident_id, disposition="recovered",
                              reason_code="stood_up_ok", by="Dana")
    assert resolved.status == db.RESOLVED

    # The cascade never resumes, even past the old claim deadline.
    clock.set(95 + CLAIM_CONFIRM_WINDOW + 10)
    assert engine.process_due() == 0
    assert "operator_escalation" not in _kinds(engine, incident_id)
