"""Disposition audit-log rules (canonical design update, point 5)."""

from __future__ import annotations

import pytest

from escalation import db
from escalation.engine import DISPOSITIONS, CascadeError


@pytest.mark.parametrize("disposition", DISPOSITIONS)
def test_each_disposition_is_recorded(engine, clock, disposition):
    incident_id = engine.escalate(room="kitchen")
    incident = engine.resolve(incident_id, disposition=disposition,
                              reason_code="code_42", by="Dana")
    assert incident.status == db.RESOLVED
    assert incident.disposition == disposition
    assert incident.disposition_reason == "code_42"
    # Disposition is captured in the immutable audit log.
    resolved_events = [e for e in engine.timeline(incident_id) if e.kind == "resolved"]
    assert len(resolved_events) == 1
    assert disposition in resolved_events[0].detail


def test_invalid_disposition_rejected(engine):
    incident_id = engine.escalate(room="kitchen")
    with pytest.raises(CascadeError):
        engine.resolve(incident_id, disposition="made_up", reason_code="x")


def test_reason_code_is_required(engine):
    incident_id = engine.escalate(room="kitchen")
    with pytest.raises(CascadeError):
        engine.resolve(incident_id, disposition="recovered", reason_code="  ")


def test_double_resolve_rejected(engine):
    incident_id = engine.escalate(room="kitchen")
    engine.resolve(incident_id, disposition="recovered", reason_code="ok")
    with pytest.raises(CascadeError):
        engine.resolve(incident_id, disposition="false_positive", reason_code="dup")


def test_no_ems_disposition_exists():
    # No EMS integration, ever — there is no "called_ems"/"dispatched" disposition.
    assert "advised_call_ems" in DISPOSITIONS
    assert not any("dispatch" in d or d == "called_ems" for d in DISPOSITIONS)
