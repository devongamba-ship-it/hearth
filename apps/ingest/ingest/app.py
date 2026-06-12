"""FastAPI application for the ingest service.

BAT-21 read endpoints:
  GET /api/nodes              — every node with its latest reading
  GET /api/nodes/{id}/latest  — the latest reading for one node (404 if none)

BAT-25 caregiver-app endpoints (coarse status + activity blocks only, never raw
readings — CLAUDE.md: privacy is the brand):
  GET /api/status                     — the "Mom is okay" Home status card
  GET /api/fall-incidents             — confirmed-fall incidents (alert history)
  GET /api/fall-incidents/{id}        — one incident's state-machine replay
  GET /homes/{home_id}/layout         — floor-plan layout (BAT-27 when authored)
  GET /homes/{home_id}/trends         — pre-aggregated weekly trend cards (stub)
  GET /api/activity                   — today's coarse activity blocks (stub)

BAT-42 caregiver weekly wellness digest:
  GET /api/digest?week=YYYY-MM-DD     — plain-language weekly summary (coarse)

BAT-27 live mesh view:
  WS  /homes/{home_id}/live           — 1 Hz aggregated room/track/event feed

These are the ingest service's own local API. "Latest" is by insert order
(surrogate id), not the packet timestamp, because ms-since-boot resets on a node
reboot.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from sqlalchemy import func, select

from . import __version__, caregiver, digest as digest_mod
from .db import CONFIRMED_FALL, FallTransition, Reading, init_db, make_engine, make_session_factory
from .detection import load_config
from .health import build_health
from .health import home_id as resolve_home_id
from .layout import HomeLayout, load_layout
from .live import LiveFeed, resolve_source
from .logconfig import configure_logging

# Process start, captured at import so /health can report uptime. Tests that
# need a deterministic uptime set ``app.state.started_at``.
_STARTED_AT = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging("ingest")
    yield


app = FastAPI(title="Hearth Ingest", version=__version__, lifespan=lifespan)

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
def health(request: Request) -> dict:
    """Real status payload (BAT-37): per-node staleness + an aggregate block, so
    an operator can tell whether the running system is healthy without SSHing in.

    ``status`` is ``ok`` (all nodes online), ``degraded`` (≥1 node stale), or
    ``down`` (nothing reporting). Coarse status only — never a raw reading."""
    factory = _session_factory(request)
    now = _now(request)
    started = getattr(request.app.state, "started_at", _STARTED_AT)
    layout = _layout(request)
    with factory() as session:
        return build_health(
            session,
            now=now,
            version=__version__,
            config=_config(request),
            layout=layout,
            uptime_secs=now - started,
            home=resolve_home_id(layout),
        )


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


def _layout(request: Request) -> HomeLayout | None:
    """The configured home layout: an ``app.state.layout`` override (tests set
    this) or one loaded from ``$HEARTH_LAYOUT_FILE``. ``None`` when neither is
    set — the layout endpoint then serves its derived-from-config fallback and
    the live feed synthesises a layout from the detection config."""
    override = getattr(request.app.state, "layout", None)
    return override if override is not None else load_layout()


@app.get("/homes/{home_id}/layout")
def home_layout(home_id: str, request: Request) -> dict:
    """Floor-plan layout for the live mesh view (BAT-27): rooms, node markers,
    link topology, hand-authored at onboarding (``layout.yaml``).

    When a layout is configured it is served as-is (topology only, never raw
    readings). With no authored layout, falls back to the BAT-25 derived stub:
    rooms inferred from the detection config and seen node ids, geometry stubbed.
    """
    layout = _layout(request)
    if layout is not None:
        body = layout.to_json()
        body["home_id"] = home_id
        return body

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


# -- BAT-27 live mesh view feed ---------------------------------------------


def _build_live_feed(websocket: WebSocket, home_id: str) -> LiveFeed:
    """Assemble the per-connection :class:`LiveFeed`.

    Layout precedence mirrors :func:`_layout`: an ``app.state.layout`` override,
    then ``$HEARTH_LAYOUT_FILE``, then a layout synthesised from the detection
    config plus seen node ids so the feed still maps nodes to rooms with no
    authored floor plan. The clock is shared with the rest of the app (tests
    drive ``app.state.clock`` to exercise dropout/dwell timing deterministically).
    """
    factory = _session_factory(websocket)
    config = _config(websocket)
    override = getattr(websocket.app.state, "layout", None)
    layout = override if override is not None else load_layout()
    if layout is None:
        with factory() as session:
            node_ids = session.scalars(select(Reading.node_id).distinct()).all()
        layout = HomeLayout.from_detection(config, node_ids, home_id=home_id)
    clock = getattr(websocket.app.state, "clock", None)
    # Data-source label (BAT-34): an app.state override (tests set this) else the
    # operator-set HEARTH_SOURCE env. Defaults to "live" — real silicon is the
    # production case; the operator flips HEARTH_SOURCE=simulated for sim runs.
    source = getattr(websocket.app.state, "source", None) or os.environ.get("HEARTH_SOURCE")
    return LiveFeed(
        factory, layout, home_id=home_id, source=resolve_source(source), clock=clock
    )


@app.websocket("/homes/{home_id}/live")
async def home_live(websocket: WebSocket, home_id: str) -> None:
    """1 Hz aggregated live feed for the mesh view (room activity, anonymous
    tracks, event ticker, incident transitions). Aggregated frames only — raw
    CSI/readings never leave the home (CLAUDE.md: privacy is the brand)."""
    await websocket.accept()
    feed = _build_live_feed(websocket, home_id)
    interval = getattr(websocket.app.state, "live_interval", 1.0)
    try:
        while True:
            for message in feed.tick():
                await websocket.send_json(message)
            await asyncio.sleep(interval)
    except WebSocketDisconnect:
        return


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


# -- BAT-42 caregiver weekly wellness digest --------------------------------


@app.get("/api/digest")
def weekly_digest(request: Request, week: str | None = None) -> dict:
    """The caregiver weekly wellness digest: a plain-language, non-medical summary
    of the week aggregated from the local readings table — never raw readings.

    ``week`` is the ISO date (YYYY-MM-DD) of the week's first day; omitted, it
    defaults to the most recent complete 7-day window. Coarse summary only
    (CLAUDE.md: privacy is the brand). Delivery (email, dry-run by default) is a
    separate scheduled path; this endpoint only renders for the app.
    """
    from zoneinfo import ZoneInfo

    config = _config(request)
    tz = ZoneInfo(config.timezone) if config.timezone else None
    if week is None:
        week_start = digest_mod.default_week_start(_now(request), tz)
    else:
        try:
            from datetime import date as _date

            week_start = _date.fromisoformat(week)
        except ValueError:
            raise HTTPException(  # noqa: B904
                status_code=422, detail="week must be an ISO date (YYYY-MM-DD)"
            )

    factory = _session_factory(request)
    with factory() as session:
        aggregate = digest_mod.aggregate_week(session, week_start, config)
    return digest_mod.generate_digest(aggregate).to_json()
