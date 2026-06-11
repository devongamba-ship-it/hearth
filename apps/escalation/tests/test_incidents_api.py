"""HTTP surface for the typed incident registry (BAT-31).

``POST /incidents`` (typed, idempotent on dedupe key), ``GET /incidents?state=open``,
and the per-incident ``claim`` / ``snooze`` / ``disposition`` actions and the
auto-resolve ``clear`` signal — all dry-run, no external side effects.
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


def test_post_incidents_typed_and_idempotent(client):
    first = client.post("/incidents", json={
        "type": "wellness.prolonged_stillness", "room": "home"})
    assert first.status_code == 200
    body = first.json()
    assert body["created"] is True
    assert body["type"] == "wellness.prolonged_stillness"
    assert body["status"] == db.ACTIVE

    # Re-raising the same open type is idempotent: same id, created False.
    second = client.post("/incidents", json={
        "type": "wellness.prolonged_stillness", "room": "home"})
    assert second.json()["created"] is False
    assert second.json()["id"] == body["id"]


def test_post_incidents_requires_type_and_room(client):
    assert client.post("/incidents", json={"room": "home"}).status_code == 422
    assert client.post("/incidents", json={"type": "x"}).status_code == 422


def test_post_incidents_unknown_type_is_422(client):
    resp = client.post("/incidents", json={"type": "nope.nope", "room": "home"})
    assert resp.status_code == 422


def test_get_incidents_state_open_filter(client):
    open_resp = client.post("/incidents", json={
        "type": "wellness.late_start", "room": "kitchen"}).json()
    closed = client.post("/incidents", json={
        "type": "wellness.missed_meal", "room": "kitchen"}).json()
    client.engine.signal_clear(closed["id"])  # -> AUTO_RESOLVED, leaves "open"

    all_ids = {i["id"] for i in client.get("/incidents").json()["incidents"]}
    assert all_ids == {open_resp["id"], closed["id"]}

    open_ids = {i["id"] for i in client.get("/incidents?state=open").json()["incidents"]}
    assert open_ids == {open_resp["id"]}


def test_get_incidents_type_filter(client):
    client.post("/incidents", json={"type": "wellness.late_start", "room": "kitchen"})
    client.post("/incidents", json={"type": "wellness.missed_meal", "room": "kitchen"})
    rows = client.get("/incidents?type=wellness.late_start").json()["incidents"]
    assert [r["type"] for r in rows] == ["wellness.late_start"]


def test_snooze_endpoint_for_caregiver_type(client):
    inc = client.post("/incidents", json={
        "type": "wellness.prolonged_stillness", "room": "home"}).json()
    resp = client.post(f"/incidents/{inc['id']}/snooze", json={"by": "Dana"})
    assert resp.status_code == 200
    assert resp.json()["snoozed_until"] is not None
    # The snooze is in the immutable audit log.
    kinds = [e.kind for e in client.engine.timeline(inc["id"])]
    assert "snoozed" in kinds


def test_snooze_refused_for_fall(client):
    inc_id = client.engine.escalate(room="kitchen")
    resp = client.post(f"/incidents/{inc_id}/snooze", json={"by": "Dana"})
    assert resp.status_code == 409


def test_disposition_endpoint_resolves(client):
    inc_id = client.engine.escalate(room="kitchen")
    resp = client.post(f"/incidents/{inc_id}/disposition", json={
        "disposition": "recovered", "reason_code": "stood_up_ok", "by": "Dana"})
    assert resp.status_code == 200
    assert resp.json()["status"] == db.RESOLVED
    assert client.engine.get(inc_id).disposition == "recovered"


def test_clear_endpoint_auto_resolves(client):
    inc = client.post("/incidents", json={
        "type": "wellness.prolonged_stillness", "room": "home"}).json()
    resp = client.post("/incidents/clear", json={
        "type": "wellness.prolonged_stillness"})
    assert resp.status_code == 200
    assert resp.json()["cleared"] is True
    assert client.engine.get(inc["id"]).status == db.AUTO_RESOLVED


def test_clear_endpoint_noop_when_nothing_open(client):
    resp = client.post("/incidents/clear", json={"type": "wellness.prolonged_stillness"})
    assert resp.status_code == 200 and resp.json()["cleared"] is False


def test_escalate_routes_wellness_type_through_same_pipe(client):
    # BAT-24's wellness emitter posts typed incidents to /escalate; the type must
    # route to that policy (not the fall cascade).
    resp = client.post("/escalate", json={
        "type": "wellness.prolonged_stillness", "room": "home", "severity": "soft"})
    assert resp.status_code == 200
    assert resp.json()["type"] == "wellness.prolonged_stillness"
    # No fall-style voice ladder was scheduled — the wellness policy leads with push.
    incident = client.engine.get(resp.json()["incident_id"])
    assert incident.next_action == "sms"  # push fired @0, sms @30m next


def test_escalate_default_type_is_fall(client):
    resp = client.post("/escalate", json={"room": "kitchen"})
    assert resp.json()["type"] == "fall.confirmed"
