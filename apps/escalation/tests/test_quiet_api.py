"""HTTP surface for the resident quiet window (BAT-39).

``POST /api/quiet`` (start, idempotent), ``GET /api/quiet`` (countdown status),
``POST /api/quiet/cancel`` — all dry-run, no external side effects. Proves the
app contract the caregiver/resident UI calls, and the hardware-button plug point.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from escalation import app as app_module
from escalation import db

from .conftest import FakeClock, make_engine

T0 = 1_000_000_000.0


@pytest.fixture
def client(monkeypatch, db_url, config):
    monkeypatch.delenv("ESCALATION_MODE", raising=False)
    engine = make_engine(db_url, config, FakeClock(T0))
    app_module.attach_engine(engine)
    with TestClient(app_module.app) as c:
        c.engine = engine  # type: ignore[attr-defined]
        c.clock = engine._clock  # type: ignore[attr-defined]
        yield c
    app_module.app.state.engine = None


def test_post_quiet_starts_window_and_reports_remaining(client):
    resp = client.post("/api/quiet", json={"hours": 2})
    assert resp.status_code == 200
    body = resp.json()
    assert body["created"] is True
    assert body["remaining_seconds"] == 2 * 3600
    assert body["source"] == "app"
    assert body["actor"] == "resident"


def test_post_quiet_minutes_and_actor(client):
    resp = client.post("/api/quiet", json={"minutes": 90, "actor": "Mom"})
    body = resp.json()
    assert body["remaining_seconds"] == 90 * 60
    assert body["actor"] == "Mom"


def test_post_quiet_requires_positive_duration(client):
    assert client.post("/api/quiet", json={}).status_code == 422
    assert client.post("/api/quiet", json={"minutes": 0}).status_code == 422


def test_get_quiet_status_active_and_countdown(client):
    client.post("/api/quiet", json={"hours": 2})
    client.clock.set(T0 + 1800)  # 30 min later
    status = client.get("/api/quiet").json()
    assert status["active"] is True
    assert status["remaining_seconds"] == 90 * 60
    assert status["window"]["remaining_seconds"] == 90 * 60


def test_get_quiet_status_inactive_when_none(client):
    status = client.get("/api/quiet").json()
    assert status == {"active": False, "window": None, "remaining_seconds": 0}


def test_post_quiet_is_idempotent(client):
    first = client.post("/api/quiet", json={"hours": 2}).json()
    client.clock.set(T0 + 60)
    second = client.post("/api/quiet", json={"hours": 2}).json()
    assert second["created"] is False
    assert second["id"] == first["id"]
    assert second["ends_at"] == first["ends_at"]


def test_cancel_quiet_endpoint(client):
    client.post("/api/quiet", json={"hours": 2})
    resp = client.post("/api/quiet/cancel", json={"by": "Dana"})
    assert resp.status_code == 200
    assert resp.json()["canceled"] is True
    assert client.get("/api/quiet").json()["active"] is False


def test_cancel_quiet_noop_when_none(client):
    resp = client.post("/api/quiet/cancel", json={})
    assert resp.status_code == 200 and resp.json()["canceled"] is False


def test_wellness_suppressed_while_window_active_via_http(client):
    client.post("/api/quiet", json={"hours": 2})
    inc = client.post("/incidents", json={
        "type": "wellness.prolonged_stillness", "room": "bedroom"}).json()
    assert inc["status"] == db.SUPPRESSED
    assert inc["created"] is True


def test_fall_still_escalates_while_window_active_via_http(client):
    # Safety invariant over HTTP: the fall pipe is unaffected by a quiet window.
    client.post("/api/quiet", json={"hours": 8})
    resp = client.post("/escalate", json={"room": "kitchen"})
    assert resp.json()["type"] == "fall.confirmed"
    assert client.engine.get(resp.json()["incident_id"]).status == db.ACTIVE
    paged = [m for m in client.engine.notifier.sent if m.channel in ("sms", "push", "voice")]
    assert paged, "fall.confirmed must page even during a resident quiet window"


def test_hardware_button_source_accepted(client):
    # The future hardware button POSTs the same body with source=button.
    body = client.post("/api/quiet", json={"minutes": 120, "source": "button"}).json()
    assert body["created"] is True and body["source"] == "button"
