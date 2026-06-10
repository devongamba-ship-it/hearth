"""FastAPI application for the ingest service.

Health plus the two read endpoints from BAT-21:
  GET /api/nodes              — every node with its latest reading
  GET /api/nodes/{id}/latest  — the latest reading for one node (404 if none)

These are the ingest service's own local API. "Latest" is by insert order
(surrogate id), not the packet timestamp, because ms-since-boot resets on a node
reboot.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import func, select

from . import __version__
from .db import Reading, init_db, make_engine, make_session_factory

app = FastAPI(title="Hearth Ingest", version=__version__)

_default_factory = {}


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
