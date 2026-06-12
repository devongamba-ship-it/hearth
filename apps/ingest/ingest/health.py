"""The ``/health`` status payload (BAT-37).

``GET /health`` is what an operator (and ``tools/hearth_status.py``) hits to see
whether the running system is actually healthy without SSHing into the Pi. This
module derives the payload from the local DB:

* **per-node** — last-seen age, reading count, latest confidence (the CSI-quality
  proxy), and an online/stale flag. A node silent longer than the dropout window
  is ``stale``. Nodes in the configured roster (the layout / detection rooms) that
  have never reported show up too, with ``reading_count: 0`` — so a node that
  never came up on flash day is visible, not silently missing.
* **aggregate** — nodes online / expected, DB write rate (readings/min), process
  uptime, active-incident count, and the configured ``home_id``.
* **status** — ``ok`` (every node online), ``degraded`` (≥1 node stale but ≥1
  still online), or ``down`` (nothing reporting at all — the 3 AM dead-Pi case).

Coarse status only — never a raw reading (CLAUDE.md: privacy is the brand).
"""

from __future__ import annotations

import os

from sqlalchemy import func, select

from .db import FallTransition, Reading

# Default seconds of silence before a node is reported ``stale``. A RuView node
# broadcasts at ~1 Hz, so 30 missed beats is unambiguously a dropout, not jitter.
# Tunable via ``HEARTH_NODE_DROPOUT_SECS`` (the operator may widen it for a flaky
# link). Distinct from the live mesh view's 10 s ticker dropout — that drives a
# UI blip; this drives an ops-health verdict, so it is deliberately less twitchy.
DEFAULT_DROPOUT_SECS = 30.0

# Window over which the DB write rate is measured.
WRITE_RATE_WINDOW_SECS = 60.0

# Fall-machine states (db ``to_state``) that mean an incident is still in flight.
# Anything other than NORMAL: the node left NORMAL and has not returned, so the
# incident is open from the ingest box's view (final resolution lives in the
# escalation service). Mirrors ``live._WATCH_STATES`` plus CONFIRMED_FALL.
_OPEN_STATES = {"SUSPECTED", "PROBABLE", "CHECK_IN_ACTIVE", "CONFIRMED_FALL"}

STATUS_OK = "ok"
STATUS_DEGRADED = "degraded"
STATUS_DOWN = "down"


def dropout_secs() -> float:
    """The configured stale window (``HEARTH_NODE_DROPOUT_SECS``)."""
    raw = os.environ.get("HEARTH_NODE_DROPOUT_SECS")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return DEFAULT_DROPOUT_SECS


def home_id(layout=None) -> str | None:
    """Configured home id: ``HEARTH_HOME_ID`` env, else the layout's id."""
    env = os.environ.get("HEARTH_HOME_ID")
    if env:
        return env
    return layout.home_id if layout is not None else None


def _roster(config, layout) -> set[int]:
    """Node ids the home is *expected* to have, from layout or detection rooms."""
    if layout is not None:
        return {n.id for n in layout.nodes}
    return set(config.rooms) if config is not None else set()


def _latest_per_node(session) -> dict[int, Reading]:
    latest_ids = (
        select(func.max(Reading.id)).group_by(Reading.node_id).scalar_subquery()
    )
    rows = session.execute(
        select(Reading).where(Reading.id.in_(latest_ids))
    ).scalars().all()
    return {r.node_id: r for r in rows}


def _counts_per_node(session) -> dict[int, int]:
    rows = session.execute(
        select(Reading.node_id, func.count()).group_by(Reading.node_id)
    ).all()
    return {node_id: count for node_id, count in rows}


def _write_rate_per_min(session, now: float, window: float) -> float:
    """Readings persisted in the last ``window`` seconds, scaled to per-minute.

    Counts only rows with a wall-clock ``received_at`` (rows written before this
    column existed are ignored rather than mis-dated)."""
    cutoff = now - window
    recent = session.scalar(
        select(func.count()).where(
            Reading.received_at.is_not(None), Reading.received_at >= cutoff
        )
    ) or 0
    return round(recent * 60.0 / window, 1)


def _active_incident_count(session) -> int:
    latest_ids = (
        select(func.max(FallTransition.id))
        .group_by(FallTransition.incident_id)
        .scalar_subquery()
    )
    states = session.scalars(
        select(FallTransition.to_state).where(FallTransition.id.in_(latest_ids))
    ).all()
    return sum(1 for s in states if s in _OPEN_STATES)


def build_health(
    session,
    *,
    now: float,
    version: str,
    config=None,
    layout=None,
    dropout: float | None = None,
    uptime_secs: float = 0.0,
    home: str | None = None,
) -> dict:
    """Assemble the full ``/health`` payload from the local DB.

    Pure read: issues a handful of aggregate queries and derives the verdict.
    ``now``/``uptime_secs`` are injected so the view is deterministic in tests.
    """
    window = dropout if dropout is not None else dropout_secs()
    latest = _latest_per_node(session)
    counts = _counts_per_node(session)
    roster = _roster(config, layout)
    seen = set(latest)
    all_nodes = sorted(seen | roster)

    nodes = []
    stale_count = 0
    online_count = 0
    for node_id in all_nodes:
        r = latest.get(node_id)
        if r is not None and r.received_at is not None:
            age = round(max(0.0, now - r.received_at), 1)
            stale = age > window
        else:
            # Never reported (roster-only) or pre-``received_at`` row: treat as
            # stale — we have no evidence it is alive.
            age = None
            stale = True
        room = config.room_for(node_id) if config is not None else None
        nodes.append({
            "node_id": node_id,
            "room": room,
            "reading_count": counts.get(node_id, 0),
            "last_seen_age_secs": age,
            "confidence": round(r.confidence, 3) if r is not None else None,
            "online": not stale,
            "stale": stale,
        })
        if stale:
            stale_count += 1
        else:
            online_count += 1

    expected = len(roster) if roster else len(seen)

    if online_count == 0:
        status = STATUS_DOWN
    elif stale_count > 0:
        status = STATUS_DEGRADED
    else:
        status = STATUS_OK

    return {
        "status": status,
        "service": "ingest",
        "version": version,
        "nodes": nodes,
        "aggregate": {
            "home_id": home,
            "nodes_online": online_count,
            "nodes_expected": expected,
            "nodes_stale": stale_count,
            "dropout_window_secs": window,
            "write_rate_per_min": _write_rate_per_min(session, now, WRITE_RATE_WINDOW_SECS),
            "uptime_secs": round(max(0.0, uptime_secs), 1),
            "active_incidents": _active_incident_count(session),
        },
    }
