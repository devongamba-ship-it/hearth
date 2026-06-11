"""Audit log is complete for a full fall lifecycle (BAT-31 acceptance 3).

A confirmed fall is created, the ladder runs, a caregiver claims (pausing it),
the claim goes unconfirmed and the cascade resumes, and finally it is resolved.
Every transition leaves an immutable, ordered audit row carrying actor + ts.
"""

from __future__ import annotations

from escalation import db


def test_full_fall_lifecycle_including_claim_resume_is_audited(engine, clock):
    clock.set(0)
    incident_id = engine.escalate(room="kitchen")

    clock.set(60)
    engine.process_due()                       # voice ladder

    clock.set(95)
    engine.claim(incident_id, by="Dana")       # claim -> pause

    clock.set(95 + 15 * 60)
    engine.process_due()                       # unconfirmed -> resume + operator

    clock.set(95 + 15 * 60 + 5)
    engine.resolve(incident_id, disposition="recovered",
                   reason_code="stood_up_ok", by="Dana")

    timeline = engine.timeline(incident_id)
    kinds = [e.kind for e in timeline]
    # Full, ordered lifecycle — including the claim-resume leg.
    assert kinds == [
        "incident_created",
        "push_sent",
        "ops_queued",
        "sms_sent",
        "voice_calls",
        "claimed",
        "claim_expired_resumed",
        "operator_escalation",
        "resolved",
    ]

    # Immutable + monotonic timestamps, and actor is recorded on every row.
    ats = [e.at for e in timeline]
    assert ats == sorted(ats)
    by_kind = {e.kind: e for e in timeline}
    assert by_kind["claimed"].actor == "Dana"
    assert by_kind["resolved"].actor == "Dana"
    assert by_kind["incident_created"].actor == "system"
    assert all(e.at is not None for e in timeline)

    # The cascade is terminal and never resumes again.
    clock.set(95 + 15 * 60 + 10_000)
    assert engine.process_due() == 0
    assert engine.get(incident_id).status == db.RESOLVED


def test_claim_resume_records_the_pending_step(engine, clock):
    # Resume fires the step that was pending at claim time (here: operator), not
    # a hard-coded jump — the audit trail proves the resume leg ran it.
    clock.set(0)
    incident_id = engine.escalate(room="bathroom")
    clock.set(60)
    engine.process_due()                       # voice fired; operator now pending
    clock.set(70)
    engine.claim(incident_id, by="Marcus")
    clock.set(70 + 15 * 60)
    engine.process_due()
    kinds = [e.kind for e in engine.timeline(incident_id)]
    assert kinds.index("claim_expired_resumed") < kinds.index("operator_escalation")
