"""Caregiver read API + idempotent claim (BAT-25).

The caregiver web app polls these endpoints. They expose only coarse incident
status and the immutable cascade audit trail — never raw sensor data. Claiming
is idempotent so a double-tap or a retried POST never re-broadcasts.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from escalation import app as app_module
from escalation import db

from .conftest import FakeClock, make_engine


@pytest.fixture
def client(monkeypatch, db_url, config):
    monkeypatch.delenv("ESCALATION_MODE", raising=False)
    engine = make_engine(db_url, config, FakeClock(1000.0))
    app_module.attach_engine(engine)
    with TestClient(app_module.app) as c:
        c.engine = engine  # type: ignore[attr-defined]
        yield c
    app_module.app.state.engine = None


def test_active_and_history_listing(client):
    # Distinct homes so the new fall.confirmed 5-min/home dedupe (BAT-31) keeps
    # these as two separate incidents rather than collapsing them.
    a = client.engine.escalate(room="kitchen", home_name="HomeA")
    b = client.engine.escalate(room="bathroom", home_name="HomeB")
    client.engine.resolve(b, disposition="recovered", reason_code="ok")

    active = client.get("/api/incidents/active").json()["incidents"]
    assert {i["id"] for i in active} == {a}  # resolved b is gone from active
    assert active[0]["room"] == "kitchen"

    history = client.get("/api/incidents").json()["incidents"]
    assert {i["id"] for i in history} == {a, b}  # both present in history


def test_incident_detail_replays_cascade_timeline(client):
    incident_id = client.engine.escalate(room="kitchen")
    body = client.get(f"/api/incidents/{incident_id}").json()

    assert body["id"] == incident_id
    assert body["status"] == db.ACTIVE
    kinds = [e["kind"] for e in body["cascade"]]
    # The T+0 alert step is on the timeline with wall-clock timestamps.
    assert "incident_created" in kinds and "alert_broadcast" in kinds
    assert all("at" in e for e in body["cascade"])


def test_incident_detail_404_for_unknown(client):
    assert client.get("/api/incidents/nope").status_code == 404


def test_escalate_persists_source_fall_incident_id(client):
    resp = client.post("/escalate", json={"room": "kitchen", "fall_incident_id": "fall-abc"})
    incident_id = resp.json()["incident_id"]
    detail = client.get(f"/api/incidents/{incident_id}").json()
    assert detail["source_incident_id"] == "fall-abc"


def test_claim_is_idempotent(client):
    incident_id = client.engine.escalate(room="kitchen")

    first = client.post(f"/incidents/{incident_id}/claim", json={"by": "Dana"})
    assert first.status_code == 200
    assert first.json()["claimed_now"] is True
    assert first.json()["status"] == db.CLAIMED
    assert first.json()["claimed_by"] == "Dana"

    # A repeat claim is a no-op: same state, claimed_now False, no second
    # "claimed" event appended to the immutable audit log.
    second = client.post(f"/incidents/{incident_id}/claim", json={"by": "Dana"})
    assert second.status_code == 200
    assert second.json()["claimed_now"] is False
    assert second.json()["status"] == db.CLAIMED

    claimed_events = [e for e in client.engine.timeline(incident_id) if e.kind == "claimed"]
    assert len(claimed_events) == 1


def test_claim_requires_by(client):
    incident_id = client.engine.escalate(room="kitchen")
    assert client.post(f"/incidents/{incident_id}/claim", json={}).status_code == 422


def test_claim_unknown_is_404(client):
    assert client.post("/incidents/nope/claim", json={"by": "Dana"}).status_code == 404


def test_claim_resolved_is_409(client):
    incident_id = client.engine.escalate(room="kitchen")
    client.engine.resolve(incident_id, disposition="recovered", reason_code="ok")
    resp = client.post(f"/incidents/{incident_id}/claim", json={"by": "Dana"})
    assert resp.status_code == 409
