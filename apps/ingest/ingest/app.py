"""FastAPI application for the ingest service.

BAT-21 read endpoints:
  GET /api/nodes              — every node with its latest reading
  GET /api/nodes/{id}/latest  — the latest reading for one node (404 if none)

BAT-25 caregiver-app endpoints (coarse status + activity blocks only, never raw
readings — CLAUDE.md: privacy is the brand):
  GET /api/status                     — the "Mom is okay" Home status card
  GET /api/fall-incidents             — confirmed-fall incidents (alert history)
  GET /api/fall-incidents/{id}        — one incident's state-machine replay
  GET /homes/{home_id}/layout         — room layout (rooms real, geometry stub)
  GET /homes/{home_id}/trends         — pre-aggregated weekly trend cards (stub)
  GET /api/activity                   — today's coarse activity blocks (stub)

These are the ingest service's own local API. "Latest" is by insert order
(surrogate id), not the packet timestamp, because ms-since-boot resets on a node
reboot.
"""

from __future__ import annotations

import time

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import func, select

from . import __version__, caregiver
from .db import CONFIRMED_FALL, FallTransition, Reading, init_db, make_engine, make_session_factory
from .detection import load_config

app = FastAPI(title="Hearth Ingest", version=__version__)

_default_factory = {}


def _now(request: Request) -> float:
    """Wall clock for the status view (tests override via app.state.clock)."""
    clock = getattr(request.app.state, "clock", None)
    return clock() if clock is not None else time.time()


def _config(request: Request):
    """Detection config (rooms, quiet hours, thresholds). Tests may override."""
    override = getattr(request.app.state, "detection_config", None)
    return override if override is not None else load_config()


def _session_factory(request: Request):
    """The app's session factory: an override on `app.state` (tests set this),
    otherwise a lazily-built env-driven default."""
    override = getattr(request.app.state, "session_factory", None)
    if override is not None:
        return override
    if "sf" not in _default_factory:
        engine = make_engine()
        init_db(engine)
        _default_factory["sf"] = make_session_factory(engine)
    return _default_factory["sf"]


def _reading_dict(r: Reading) -> dict:
    return {
        "node_id": r.node_id,
        "ts": r.ts,
        "presence": r.presence,
        "motion": r.motion,
        "breathing_bpm": r.breathing_bpm,
        "hr_bpm": r.hr_bpm,
        "confidence": r.confidence,
        "fall_flag": r.fall_flag,
        "occupancy": r.occupancy,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ingest", "version": __version__}


@app.get("/api/nodes")
def list_nodes(request: Request) -> dict:
    factory = _session_factory(request)
    with factory() as session:
        latest_ids = (
            select(func.max(Reading.id)).group_by(Reading.node_id).scalar_subquery()
        )
        rows = (
            session.execute(select(Reading).where(Reading.id.in_(latest_ids)))
            .scalars()
            .all()
        )
        nodes = sorted((_reading_dict(r) for r in rows), key=lambda d: d["node_id"])
    return {"nodes": nodes}


@app.get("/api/nodes/{node_id}/latest")
def node_latest(node_id: int, request: Request) -> dict:
    factory = _session_factory(request)
    with factory() as session:
        row = (
            session.execute(
                select(Reading)
                .where(Reading.node_id == node_id)
                .order_by(Reading.id.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
    if row is None:
        raise HTTPException(status_code=404, detail=f"no readings for node {node_id}")
    return _reading_dict(row)


# -- BAT-25 caregiver-app endpoints -----------------------------------------


def _latest_readings(session) -> list[caregiver.LatestReading]:
    latest_ids = (
        select(func.max(Reading.id)).group_by(Reading.node_id).scalar_subquery()
    )
    rows = session.execute(
        select(Reading).where(Reading.id.in_(latest_ids))
    ).scalars().all()
    return [
        caregiver.LatestReading(
            node_id=r.node_id, ts=r.ts, presence=r.presence, motion=r.motion
        )
        for r in rows
    ]


def _node_states(session) -> dict[int, str]:
    """The latest fall-machine state per node, from the transitions log."""
    latest_ids = (
        select(func.max(FallTransition.id))
        .group_by(FallTransition.node_id)
        .scalar_subquery()
    )
    rows = session.execute(
        select(FallTransition).where(FallTransition.id.in_(latest_ids))
    ).scalars().all()
    return {r.node_id: r.to_state for r in rows}


def _last_motion(session, threshold: float) -> caregiver.LastMotion | None:
    row = session.execute(
        select(Reading)
        .where(Reading.motion >= threshold)
        .order_by(Reading.id.desc())
        .limit(1)
    ).scalars().first()
    return caregiver.LastMotion(node_id=row.node_id, ts=row.ts) if row else None


@app.get("/api/status")
def status(request: Request) -> dict:
    """The coarse Home status card. Composed with the escalation service's
    /api/incidents/active on the client for the fall / claimed states."""
    factory = _session_factory(request)
    config = _config(request)
    with factory() as session:
        latest = _latest_readings(session)
        node_states = _node_states(session)
        last_motion = _last_motion(session, config.stillness_threshold)
    return caregiver.derive_status(
        latest, node_states, last_motion, config,
        now_hour=caregiver.current_hour(lambda: _now(request)),
    )


def _transition_json(t: FallTransition) -> dict:
    # No raw feature snapshot leaves the home — states + timestamps only.
    return {
        "at": t.at,
        "reading_ts": t.reading_ts,
        "node_id": t.node_id,
        "from_state": t.from_state,
        "to_state": t.to_state,
        "kind": t.kind,
        "reason": t.reason,
    }


@app.get("/api/fall-incidents")
def fall_incidents(request: Request) -> dict:
    """Confirmed-fall incidents (the alert history's ingest half), newest first.

    A fall incident is a transition group that reached CONFIRMED_FALL; near-falls
    and suppressions are quiet wins and are not surfaced as alerts here.
    """
    factory = _session_factory(request)
    config = _config(request)
    with factory() as session:
        confirmed_ids = session.scalars(
            select(FallTransition.incident_id)
            .where(FallTransition.kind == CONFIRMED_FALL)
            .distinct()
        ).all()
        if not confirmed_ids:
            return {"incidents": []}
        rows = session.scalars(
            select(FallTransition)
            .where(FallTransition.incident_id.in_(confirmed_ids))
            .order_by(FallTransition.id)
        ).all()

    by_incident: dict[str, list[FallTransition]] = {}
    for t in rows:
        by_incident.setdefault(t.incident_id, []).append(t)

    incidents = []
    for incident_id, steps in by_incident.items():
        confirmed = next((s for s in steps if s.kind == CONFIRMED_FALL), steps[-1])
        incidents.append({
            "incident_id": incident_id,
            "node_id": steps[0].node_id,
            "room": config.room_for(steps[0].node_id),
            "started_at": steps[0].at,
            "confirmed_at": confirmed.at,
            "num_transitions": len(steps),
        })
    incidents.sort(key=lambda i: i["confirmed_at"], reverse=True)
    return {"incidents": incidents}


@app.get("/api/fall-incidents/{incident_id}")
def fall_incident_detail(incident_id: str, request: Request) -> dict:
    """One incident's NORMAL→…→CONFIRMED_FALL state-machine replay."""
    factory = _session_factory(request)
    config = _config(request)
    with factory() as session:
        steps = session.scalars(
            select(FallTransition)
            .where(FallTransition.incident_id == incident_id)
            .order_by(FallTransition.id)
        ).all()
        if not steps:
            raise HTTPException(status_code=404, detail=f"unknown incident {incident_id}")
        node_id = steps[0].node_id
        transitions = [_transition_json(t) for t in steps]
    return {
        "incident_id": incident_id,
        "node_id": node_id,
        "room": config.room_for(node_id),
        "transitions": transitions,
    }


@app.get("/homes/{home_id}/layout")
def home_layout(home_id: str, request: Request) -> dict:
    """Room layout for the activity view. Rooms are real (from detection config /
    seen node ids); per-room geometry is stubbed until a floor-plan store exists."""
    factory = _session_factory(request)
    config = _config(request)
    with factory() as session:
        node_ids = session.scalars(select(Reading.node_id).distinct()).all()
    seen = sorted(set(node_ids) | set(config.rooms))
    rooms = [
        {"node_id": nid, "room": config.room_for(nid),
         "bedroom": config.is_bedroom(nid)}
        for nid in seen
    ]
    return {"home_id": home_id, "rooms": rooms, "geometry_stub": True}


@app.get("/homes/{home_id}/trends")
def home_trends(home_id: str, request: Request, window: str = "7d") -> dict:
    """Weekly trend cards (sleep window, active minutes, bathroom count).

    STUB: pre-aggregated buckets with plain-language deltas. Per-day history is
    not computable from the wall-clock-less readings table; these are
    representative buckets, flagged ``stub``, never raw readings.
    """
    if window not in ("24h", "7d", "30d"):
        raise HTTPException(status_code=422, detail="window must be 24h|7d|30d")
    cards = [
        {"key": "sleep_window", "label": "Sleep window",
         "value": "11:10pm – 6:50am", "delta": "about the same as usual"},
        {"key": "active_minutes", "label": "Active minutes",
         "value": "150 / day", "delta": "a little more than usual"},
        {"key": "bathroom_count", "label": "Bathroom visits",
         "value": "6 / day", "delta": "about the same as usual"},
    ]
    return {"home_id": home_id, "window": window, "stub": True, "cards": cards}


@app.get("/api/activity")
def activity(request: Request, date: str | None = None) -> dict:
    """Today's coarse activity blocks for the day timeline.

    STUB: coarse room blocks (never per-second motion). Wall-clock-stamped
    activity history is a follow-up; flagged ``stub`` so the client labels it.
    """
    config = _config(request)

    def room(nid: int) -> str:
        return config.room_for(nid)

    blocks = [
        {"room": room(1), "start": "07:05", "end": "07:40", "label": "Morning routine"},
        {"room": room(1), "start": "08:12", "end": "08:40", "label": "Breakfast"},
        {"room": room(2), "start": "10:30", "end": "11:15", "label": "Resting"},
        {"room": room(1), "start": "12:20", "end": "12:55", "label": "Lunch"},
    ]
    return {"date": date, "stub": True, "blocks": blocks}
