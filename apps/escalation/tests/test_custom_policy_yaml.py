"""A YAML edit changes behavior with zero code changes (BAT-31 acceptance 2).

We define a brand-new incident type that exists in *no* Python file — only in a
policy document — and show the engine schedules and fires its steps, applies its
claim/auto-resolve/dedupe rules, all from data.
"""

from __future__ import annotations

import pytest

from escalation import db
from escalation.engine import CascadeError
from escalation.policy import load_registry_from_text

from .conftest import make_engine

# A novel type the codebase has never heard of, defined entirely in YAML.
CUSTOM_YAML = """
version: 1
policies:
  - type: pet.feeder_jam
    priority: P3
    claim: { mode: pause, confirm_window_secs: 120 }
    snoozeable: true
    auto_resolve: true
    dedupe: { one_open: true }
    steps:
      - { at_secs: 0,  action: push }
      - { at_secs: 30, action: sms }
      - { at_secs: 90, action: ops_queue }
"""


@pytest.fixture
def custom_engine(db_url, config, clock):
    registry = load_registry_from_text(CUSTOM_YAML)
    return make_engine(db_url, config, clock, registry=registry)


def _kinds(engine, incident_id):
    return [e.kind for e in engine.timeline(incident_id)]


def test_custom_type_schedules_and_fires_its_steps(custom_engine, clock):
    clock.set(0)
    incident, created = custom_engine.raise_incident("pet.feeder_jam", room="kitchen")
    assert created and incident.priority == "P3"
    # T+0 push fired; T+30 sms scheduled next — purely from the YAML.
    assert "push_sent" in _kinds(custom_engine, incident.id)
    assert custom_engine.get(incident.id).next_action == "sms"

    clock.set(30)
    custom_engine.process_due()
    assert "sms_sent" in _kinds(custom_engine, incident.id)

    clock.set(90)
    custom_engine.process_due()
    assert "ops_queued" in _kinds(custom_engine, incident.id)


def test_custom_type_claim_pause_and_auto_resolve_from_yaml(custom_engine, clock):
    clock.set(0)
    incident, _ = custom_engine.raise_incident("pet.feeder_jam", room="kitchen")

    # The YAML says claim mode = pause with a 120s confirm window.
    claimed = custom_engine.claim(incident.id, by="Dana", now=10)
    assert claimed.status == db.CLAIMED and claimed.claim_deadline == 10 + 120

    # auto_resolve: true in the YAML — a clear signal closes it.
    resolved = custom_engine.clear_by_type("pet.feeder_jam", now=20)
    assert resolved is not None and resolved.status == db.AUTO_RESOLVED


def test_custom_type_dedupe_one_open_from_yaml(custom_engine, clock):
    clock.set(0)
    first, c1 = custom_engine.raise_incident("pet.feeder_jam", room="kitchen")
    second, c2 = custom_engine.raise_incident("pet.feeder_jam", room="kitchen")
    assert c1 is True and c2 is False  # second deduped onto the open first
    assert first.id == second.id


def test_unknown_type_is_rejected(custom_engine):
    # A type with no policy in the loaded registry is a hard error, not a guess.
    with pytest.raises(CascadeError):
        custom_engine.raise_incident("does.not.exist", room="kitchen")
