"""BAT-37: /health per-node staleness + aggregate, and the stale→degraded rule."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hearth.packet import Vitals
from ingest import __version__
from ingest.app import app
from ingest.db import FallTransition
from ingest.detection import DetectionConfig
from ingest.health import (
    STATUS_DEGRADED,
    STATUS_DOWN,
    STATUS_OK,
    build_health,
)
from ingest.listener import persist

NOW = 1_000_000.0


def _v(node_id: int, ts: int, conf: float = 0.88, fall: bool = False) -> Vitals:
    return Vitals(
        node_id=node_id, presence=True, fall=fall, motion=True,
        breathing_bpm=15.0, heart_bpm=72.0, rssi=-55, occupancy=1,
        motion_energy=1500.0, presence_score=conf, timestamp_ms=ts,
    )


def _health(session_factory, **kwargs) -> dict:
    with session_factory() as session:
        return build_health(session, now=NOW, version=__version__, **kwargs)


# -- per-node staleness + the stale→degraded derivation ---------------------


def test_fresh_node_is_online(session_factory):
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 2)
    health = _health(session_factory, dropout=30.0)

    node = next(n for n in health["nodes"] if n["node_id"] == 1)
    assert node["online"] is True
    assert node["stale"] is False
    assert node["last_seen_age_secs"] == 2.0
    assert node["reading_count"] == 1
    assert node["confidence"] == 0.88
    assert health["status"] == STATUS_OK


def test_silent_node_flips_to_stale_and_degraded(session_factory):
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 2)      # fresh
    persist(session_factory, _v(2, ts=1000), received_at=NOW - 120)    # silent

    health = _health(session_factory, dropout=30.0)
    nodes = {n["node_id"]: n for n in health["nodes"]}

    assert nodes[1]["stale"] is False
    assert nodes[2]["stale"] is True
    assert nodes[2]["last_seen_age_secs"] == 120.0
    # One stale node flips the whole system to degraded (AC bullet 1).
    assert health["status"] == STATUS_DEGRADED
    assert health["aggregate"]["nodes_online"] == 1
    assert health["aggregate"]["nodes_stale"] == 1


def test_no_nodes_is_down(session_factory):
    health = _health(session_factory)
    assert health["status"] == STATUS_DOWN
    assert health["nodes"] == []
    assert health["aggregate"]["nodes_online"] == 0


def test_all_nodes_stale_is_down(session_factory):
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 500)
    health = _health(session_factory, dropout=30.0)
    assert health["status"] == STATUS_DOWN
    assert health["aggregate"]["nodes_online"] == 0


def test_dropout_window_boundary(session_factory):
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 30)  # exactly at window
    persist(session_factory, _v(2, ts=1000), received_at=NOW - 31)  # just past
    nodes = {n["node_id"]: n for n in _health(session_factory, dropout=30.0)["nodes"]}
    assert nodes[1]["stale"] is False  # age == window is still online
    assert nodes[2]["stale"] is True


# -- roster: configured-but-never-seen nodes show up ------------------------


def test_configured_node_that_never_reported_is_stale(session_factory):
    config = DetectionConfig(rooms={1: "Living Room", 2: "Kitchen", 3: "Bedroom"})
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 2)

    health = _health(session_factory, config=config, dropout=30.0)
    nodes = {n["node_id"]: n for n in health["nodes"]}

    assert set(nodes) == {1, 2, 3}
    assert nodes[1]["online"] is True and nodes[1]["room"] == "Living Room"
    # Never sent a packet: no readings, no age, stale.
    assert nodes[3]["reading_count"] == 0
    assert nodes[3]["last_seen_age_secs"] is None
    assert nodes[3]["confidence"] is None
    assert nodes[3]["stale"] is True
    # Expected counts the configured roster, not just what was seen.
    assert health["aggregate"]["nodes_expected"] == 3
    assert health["aggregate"]["nodes_online"] == 1
    assert health["status"] == STATUS_DEGRADED


# -- aggregate block --------------------------------------------------------


def test_aggregate_write_rate_counts_recent_rows(session_factory):
    # 3 within the last minute, 1 older.
    persist(session_factory, _v(1, ts=1), received_at=NOW - 5)
    persist(session_factory, _v(1, ts=2), received_at=NOW - 30)
    persist(session_factory, _v(1, ts=3), received_at=NOW - 59)
    persist(session_factory, _v(1, ts=4), received_at=NOW - 90)  # outside the window

    agg = _health(session_factory, dropout=120.0)["aggregate"]
    assert agg["write_rate_per_min"] == 3.0  # 3 rows / 60s * 60


def test_aggregate_active_incident_count(session_factory):
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 2)
    with session_factory() as session:
        # i1: open (latest is SUSPECTED). i2: closed (latest back to NORMAL).
        session.add_all([
            FallTransition(incident_id="i1", node_id=1, reading_ts=10, at=NOW - 9,
                           from_state="NORMAL", to_state="SUSPECTED", kind="transition"),
            FallTransition(incident_id="i2", node_id=2, reading_ts=10, at=NOW - 8,
                           from_state="NORMAL", to_state="SUSPECTED", kind="transition"),
            FallTransition(incident_id="i2", node_id=2, reading_ts=20, at=NOW - 7,
                           from_state="SUSPECTED", to_state="NORMAL", kind="near_fall"),
        ])
        session.commit()

    agg = _health(session_factory, dropout=30.0)["aggregate"]
    assert agg["active_incidents"] == 1


def test_aggregate_carries_uptime_and_home(session_factory):
    agg = _health(session_factory, uptime_secs=123.4, home="rosewood")["aggregate"]
    assert agg["uptime_secs"] == 123.4
    assert agg["home_id"] == "rosewood"


# -- endpoint shape ----------------------------------------------------------


def test_health_endpoint_shape(session_factory):
    persist(session_factory, _v(1, ts=1000), received_at=NOW - 2)
    app.state.session_factory = session_factory
    app.state.clock = lambda: NOW
    app.state.started_at = NOW - 60.0
    app.state.detection_config = DetectionConfig(rooms={1: "Living Room"})
    app.state.layout = None
    try:
        body = TestClient(app).get("/health").json()
    finally:
        for attr in ("session_factory", "clock", "started_at", "detection_config", "layout"):
            if hasattr(app.state, attr):
                delattr(app.state, attr)

    assert body["service"] == "ingest"
    assert body["version"] == __version__
    assert body["status"] == STATUS_OK
    assert isinstance(body["nodes"], list) and body["nodes"][0]["node_id"] == 1
    agg = body["aggregate"]
    for key in ("home_id", "nodes_online", "nodes_expected", "nodes_stale",
                "dropout_window_secs", "write_rate_per_min", "uptime_secs",
                "active_incidents"):
        assert key in agg
    assert agg["uptime_secs"] == 60.0
