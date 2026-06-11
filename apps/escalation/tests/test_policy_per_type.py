"""Per-type dry-run behavior (BAT-31 acceptance criterion 1).

Each incident type runs *its own* policy out of the registry — no bespoke code:

* ``fall.confirmed`` keeps the P1 pause/resume cascade semantics.
* ``wellness.prolonged_stillness`` auto-resolves when motion resumes.
* digest-only types (P4) never page (no SMS/voice/push reaches anyone).

All side-effect free: a DryRunNotifier records actions; nothing leaves the box.
"""

from __future__ import annotations

import pytest

from escalation import db
from escalation.engine import CascadeError
from escalation.policy import PAGING_CHANNELS

PAGING = set(PAGING_CHANNELS)


def _kinds(engine, incident_id):
    return [e.kind for e in engine.timeline(incident_id)]


# -- fall.confirmed: pause / resume (the P1 policy instance) -----------------


def test_fall_is_a_policy_instance_pause_then_resume(engine, clock):
    clock.set(0)
    incident_id = engine.escalate(room="kitchen")
    assert engine.get(incident_id).type == "fall.confirmed"

    # T+60 voice ladder fires from the policy steps, not hand-coded.
    clock.set(60)
    engine.process_due()
    assert "voice_calls" in _kinds(engine, incident_id)

    # Claim pauses the cascade; the T+120 operator step must NOT fire while paused.
    clock.set(95)
    claimed = engine.claim(incident_id, by="Dana")
    assert claimed.status == db.CLAIMED
    clock.set(120)
    assert engine.process_due() == 0
    assert "operator_escalation" not in _kinds(engine, incident_id)

    # Unconfirmed within the 15-min window -> resume to OPEN at the operator step.
    clock.set(95 + 15 * 60)
    assert engine.process_due() == 1
    kinds = _kinds(engine, incident_id)
    assert "claim_expired_resumed" in kinds and "operator_escalation" in kinds
    assert engine.get(incident_id).status == db.ACTIVE


def test_fall_dedupes_within_5min_window_per_home(engine, clock):
    clock.set(0)
    first = engine.escalate(room="kitchen")
    # A second confirmed fall 2 min later, same home, collapses onto the open one.
    clock.set(120)
    second, created = engine.raise_incident("fall.confirmed", room="bathroom")
    assert created is False and second.id == first


def test_fall_realerts_after_resolve_even_within_window(engine, clock):
    # Fail-safe: once handled, a new fall in the window must NOT be swallowed.
    clock.set(0)
    first = engine.escalate(room="kitchen")
    engine.resolve(first, disposition="recovered", reason_code="ok", now=30)
    clock.set(60)  # within the 5-min window, but the first is resolved
    second, created = engine.raise_incident("fall.confirmed", room="kitchen")
    assert created is True and second.id != first


def test_fall_is_not_snoozeable(engine):
    incident_id = engine.escalate(room="kitchen")
    with pytest.raises(CascadeError):
        engine.snooze(incident_id)


# -- wellness.prolonged_stillness: auto-resolve on motion --------------------


def test_prolonged_stillness_auto_resolves_on_motion(engine, clock):
    clock.set(0)
    incident, created = engine.raise_incident(
        "wellness.prolonged_stillness", room="home")
    assert created and incident.status == db.ACTIVE
    # T+0 push fired; the T+30m SMS step is scheduled but not yet due.
    assert engine.get(incident.id).next_action == "sms"

    # Motion resumes at T+10m: the trigger condition clears -> AUTO_RESOLVED.
    clock.set(600)
    resolved = engine.signal_clear(incident.id)
    assert resolved.status == db.AUTO_RESOLVED
    assert "auto_resolved" in _kinds(engine, incident.id)

    # The scheduled SMS escalation never fires after auto-resolve.
    clock.set(1800)
    assert engine.process_due() == 0
    sent_channels = {m.channel for m in engine.notifier.sent}
    assert "sms" not in sent_channels  # only the T+0 push happened


def test_prolonged_stillness_auto_resolve_is_logged(engine, clock):
    clock.set(0)
    incident, _ = engine.raise_incident("wellness.prolonged_stillness", room="home")
    engine.signal_clear(incident.id, now=300)
    events = [e for e in engine.timeline(incident.id) if e.kind == "auto_resolved"]
    assert len(events) == 1 and events[0].actor == "system"


# -- digest-only types never page -------------------------------------------


def test_digest_only_types_never_page(engine, clock):
    clock.set(0)
    for type_ in ("wellness.bathroom_spike", "wellness.night_wandering",
                  "wellness.activity_decline"):
        incident, _ = engine.raise_incident(type_, room="home")
        # Drive well past any conceivable deadline.
        clock.set(7 * 86400)
        engine.process_due()
        clock.set(0)
        sent_for_type = [m for m in engine.notifier.sent if m.channel in PAGING]
        assert sent_for_type == [], f"{type_} paged: {sent_for_type}"
        # The only audit action is the digest queueing.
        kinds = [e.kind for e in engine.timeline(incident.id)
                 if e.kind not in ("incident_created",)]
        assert kinds == ["digest_queued"]


def test_digest_types_are_not_claimable(engine):
    incident, _ = engine.raise_incident("wellness.bathroom_spike", room="home")
    with pytest.raises(CascadeError):
        engine.claim(incident.id, by="Dana")


# -- registry coverage: every v1 type loads + raises -------------------------


def test_all_v1_types_load_and_raise(engine):
    expected = {
        "fall.confirmed", "wellness.prolonged_stillness", "system.sensing_gap",
        "wellness.late_start", "wellness.missed_meal", "wellness.bathroom_spike",
        "wellness.night_wandering", "wellness.activity_decline",
    }
    assert set(engine.registry.policies) == expected
    for type_ in expected:
        incident, created = engine.raise_incident(type_, room="home")
        assert created and incident.type == type_
