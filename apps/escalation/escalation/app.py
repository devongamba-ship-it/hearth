"""FastAPI application for the escalation service.

Endpoints:

* ``GET  /health`` — liveness.
* ``POST /escalate`` — called by the fall confirmation machine on CONFIRMED_FALL;
  starts the cascade and returns the incident id + initial timeline.
* ``POST /incidents/{id}/resolve`` — record an immutable disposition.
* ``POST /admin/process-due`` — drive persisted timers (a local poller/cron hits
  this; it is also run on startup to recover pending cascades after a restart).
* ``POST /webhooks/twilio/sms`` and ``/webhooks/twilio/voice`` — ack/claim
  callbacks; every request's Twilio signature is validated.

The engine is built from env at startup: contacts from ``HEARTH_CONTACTS_FILE``,
DB from ``HEARTH_ESCALATION_DB_URL``, notifier from ``ESCALATION_MODE`` (dry_run
default). Tests inject their own engine via :func:`attach_engine`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, Form, HTTPException, Request, Response

from . import __version__, db
from .cascade import mode
from .config import load_config
from .engine import CascadeEngine, CascadeError
from .notifier import make_notifier
from .twilio_webhook import auth_token, is_valid_signature


@asynccontextmanager
async def lifespan(app: FastAPI):
    if getattr(app.state, "engine", None) is None:
        attach_engine(build_engine())
    # Recover pending cascades: re-drive any persisted deadline already passed.
    app.state.engine.process_due()
    yield


app = FastAPI(title="Hearth Escalation", version=__version__, lifespan=lifespan)


def build_engine() -> CascadeEngine:
    """Construct the engine from environment configuration."""
    engine = db.make_engine()
    db.init_db(engine)
    sessions = db.make_session_factory(engine)
    return CascadeEngine(sessions, load_config(), notifier=make_notifier())


def attach_engine(engine: CascadeEngine) -> None:
    """Install an engine (used by startup and by tests)."""
    app.state.engine = engine


def _engine(request: Request) -> CascadeEngine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        raise HTTPException(status_code=503, detail="escalation engine not configured")
    return engine


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "escalation", "version": __version__, "mode": mode()}


def _timeline_json(engine: CascadeEngine, incident_id: str) -> list[dict]:
    return [
        {"at": e.at, "kind": e.kind, "priority": e.priority, "detail": e.detail}
        for e in engine.timeline(incident_id)
    ]


@app.post("/escalate")
def escalate(payload: dict, request: Request) -> dict:
    engine = _engine(request)
    room = payload.get("room")
    if not room:
        raise HTTPException(status_code=422, detail="'room' is required")
    incident_id = engine.escalate(
        room=room,
        home_name=payload.get("home_name"),
        source_incident_id=payload.get("fall_incident_id"),
    )
    return {
        "incident_id": incident_id,
        "status": db.ACTIVE,
        "timeline": _timeline_json(engine, incident_id),
    }


@app.post("/incidents/{incident_id}/resolve")
def resolve(incident_id: str, payload: dict, request: Request) -> dict:
    engine = _engine(request)
    try:
        incident = engine.resolve(
            incident_id,
            disposition=payload.get("disposition", ""),
            reason_code=payload.get("reason_code", ""),
            by=payload.get("by"),
        )
    except CascadeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"incident_id": incident_id, "status": incident.status,
            "disposition": incident.disposition}


@app.post("/admin/process-due")
def process_due(request: Request) -> dict:
    engine = _engine(request)
    return {"fired": engine.process_due()}


# -- caregiver read API (BAT-25) --------------------------------------------
#
# The caregiver web app polls these. They expose only alert events and coarse
# incident status — never raw sensor readings (CLAUDE.md: privacy is the brand).


def _incident_json(incident: db.Incident) -> dict:
    """A coarse incident summary safe to leave the home."""
    return {
        "id": incident.id,
        "home_name": incident.home_name,
        "room": incident.room,
        "status": incident.status,
        "created_at": incident.created_at,
        "source_incident_id": incident.source_incident_id,
        "claimed_by": incident.claimed_by,
        "claimed_at": incident.claimed_at,
        "claim_deadline": incident.claim_deadline,
        "disposition": incident.disposition,
        "disposition_reason": incident.disposition_reason,
        "resolved_at": incident.resolved_at,
        "next_action": incident.next_action,
        "next_action_at": incident.next_action_at,
    }


@app.get("/api/incidents")
def list_incidents(request: Request) -> dict:
    """All incidents, newest first — the Alerts screen's history."""
    engine = _engine(request)
    return {"incidents": [_incident_json(i) for i in engine.incidents()]}


@app.get("/api/incidents/active")
def active_incidents(request: Request) -> dict:
    """Unresolved incidents — what the live status card watches."""
    engine = _engine(request)
    return {"incidents": [_incident_json(i) for i in engine.incidents(active_only=True)]}


@app.get("/api/incidents/{incident_id}")
def incident_detail(incident_id: str, request: Request) -> dict:
    """One incident plus its immutable cascade timeline (who/when/claim/disposition)."""
    engine = _engine(request)
    incident = engine.get(incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
    body = _incident_json(incident)
    body["cascade"] = _timeline_json(engine, incident_id)
    return body


@app.post("/incidents/{incident_id}/claim")
def claim_incident(incident_id: str, payload: dict, request: Request) -> dict:
    """Claim an incident from the caregiver app. Idempotent: a repeat claim on an
    already-claimed incident returns the current state without re-broadcasting."""
    engine = _engine(request)
    by = (payload or {}).get("by")
    if not by:
        raise HTTPException(status_code=422, detail="'by' is required")
    try:
        incident, claimed_now = engine.claim_idempotent(incident_id, by=by)
    except CascadeError as exc:
        # Unknown incident -> 404; already-resolved -> 409 (cannot claim).
        detail = str(exc)
        status = 404 if "unknown incident" in detail else 409
        raise HTTPException(status_code=status, detail=detail) from exc
    body = _incident_json(incident)
    body["claimed_now"] = claimed_now
    return body


# -- Twilio webhooks --------------------------------------------------------


def _validate_twilio(request: Request, params: dict[str, str]) -> None:
    token = auth_token()
    if not token:
        raise HTTPException(status_code=503, detail="Twilio webhooks not configured")
    signature = request.headers.get("X-Twilio-Signature")
    if not is_valid_signature(token, str(request.url), params, signature):
        raise HTTPException(status_code=403, detail="invalid Twilio signature")


def _claimer_name(engine: CascadeEngine, from_number: str) -> str:
    contact = engine._config.find_by_phone(from_number)
    return contact.name if contact else from_number


@app.post("/webhooks/twilio/sms")
async def twilio_sms(request: Request, From: str = Form(...), Body: str = Form("")) -> Response:
    engine = _engine(request)
    form = await request.form()
    _validate_twilio(request, {k: str(v) for k, v in form.items()})

    incident = _latest_unresolved(engine)
    reply = "Thanks."
    if incident is not None:
        command = Body.strip().upper()
        if command == "OK":
            engine.claim(incident.id, by=_claimer_name(engine, From))
            reply = "Acknowledged. Please confirm resolution in the app within 15 minutes."
        elif command == "HELP":
            engine.process_due(now=engine.now() + 10_000)  # force operator step now
            reply = "Escalating now."
    return Response(content=f"<Response><Message>{reply}</Message></Response>",
                    media_type="application/xml")


@app.post("/webhooks/twilio/voice")
async def twilio_voice(request: Request, From: str = Form(...), Digits: str = Form("")) -> Response:
    engine = _engine(request)
    form = await request.form()
    _validate_twilio(request, {k: str(v) for k, v in form.items()})

    incident = _latest_unresolved(engine)
    if incident is not None and Digits.strip() == "1":
        engine.claim(incident.id, by=_claimer_name(engine, From))
        say = "Acknowledged. Please confirm resolution within fifteen minutes."
    else:
        say = "No acknowledgment received."
    return Response(content=f"<Response><Say>{say}</Say></Response>",
                    media_type="application/xml")


def _latest_unresolved(engine: CascadeEngine):
    pending = engine.pending()
    if not pending:
        return None
    return max(pending, key=lambda i: i.created_at)
