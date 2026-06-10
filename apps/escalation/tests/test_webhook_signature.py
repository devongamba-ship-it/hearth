"""Twilio webhook signature validation (BAT-23 acceptance).

Validation runs regardless of escalation mode — it protects the ack/claim
endpoints from forgery. A valid signature claims the incident; a bad or missing
one is rejected with 403.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from escalation import app as app_module
from escalation.twilio_webhook import compute_signature, is_valid_signature

from .conftest import FakeClock, make_engine

TOKEN = "test_auth_token_do_not_use"
SMS_URL = "http://testserver/webhooks/twilio/sms"
VOICE_URL = "http://testserver/webhooks/twilio/voice"


def test_compute_and_validate_signature_roundtrip():
    params = {"From": "+15555550101", "Body": "OK"}
    sig = compute_signature(TOKEN, SMS_URL, params)
    assert is_valid_signature(TOKEN, SMS_URL, params, sig)
    # Tampered body or wrong token fails.
    assert not is_valid_signature(TOKEN, SMS_URL, {**params, "Body": "HELP"}, sig)
    assert not is_valid_signature("wrong", SMS_URL, params, sig)
    assert not is_valid_signature(TOKEN, SMS_URL, params, None)


@pytest.fixture
def client(monkeypatch, db_url, config):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", TOKEN)
    monkeypatch.delenv("ESCALATION_MODE", raising=False)
    engine = make_engine(db_url, config, FakeClock(0))
    app_module.attach_engine(engine)
    with TestClient(app_module.app) as c:
        c.engine = engine  # type: ignore[attr-defined]
        yield c
    app_module.app.state.engine = None


def test_valid_signature_claims_incident(client):
    incident_id = client.engine.escalate(room="kitchen")
    params = {"From": "+15555550101", "Body": "OK"}
    sig = compute_signature(TOKEN, SMS_URL, params)

    resp = client.post("/webhooks/twilio/sms", data=params,
                       headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 200
    assert "Acknowledged" in resp.text

    incident = client.engine.get(incident_id)
    assert incident.status == "claimed"
    assert incident.claimed_by == "Dana"  # resolved from the caller's number


def test_invalid_signature_is_rejected(client):
    client.engine.escalate(room="kitchen")
    params = {"From": "+15555550101", "Body": "OK"}
    resp = client.post("/webhooks/twilio/sms", data=params,
                       headers={"X-Twilio-Signature": "definitely-not-valid"})
    assert resp.status_code == 403


def test_missing_signature_is_rejected(client):
    client.engine.escalate(room="kitchen")
    resp = client.post("/webhooks/twilio/sms", data={"From": "+15555550101", "Body": "OK"})
    assert resp.status_code == 403


def test_voice_digit_one_claims(client):
    incident_id = client.engine.escalate(room="kitchen")
    params = {"From": "+15555550102", "Digits": "1"}
    sig = compute_signature(TOKEN, VOICE_URL, params)
    resp = client.post("/webhooks/twilio/voice", data=params,
                       headers={"X-Twilio-Signature": sig})
    assert resp.status_code == 200
    assert client.engine.get(incident_id).status == "claimed"
