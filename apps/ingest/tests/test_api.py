"""Tests for the node-status API endpoints."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hearth.packet import Vitals
from ingest.app import app
from ingest.listener import persist


def _client(session_factory) -> TestClient:
    app.state.session_factory = session_factory
    return TestClient(app)


def _v(node_id: int, ts: int, fall: bool = False) -> Vitals:
    return Vitals(
        node_id=node_id,
        presence=True,
        fall=fall,
        motion=True,
        breathing_bpm=15.0,
        heart_bpm=72.0,
        rssi=-55,
        occupancy=1,
        motion_energy=1500.0,
        presence_score=0.88,
        timestamp_ms=ts,
    )


def test_nodes_lists_latest_per_node(session_factory):
    persist(session_factory, _v(1, ts=1000))
    persist(session_factory, _v(1, ts=2000, fall=True))  # newer for node 1
    persist(session_factory, _v(2, ts=1500))
    client = _client(session_factory)

    body = client.get("/api/nodes").json()
    nodes = {n["node_id"]: n for n in body["nodes"]}

    assert set(nodes) == {1, 2}
    assert nodes[1]["ts"] == 2000
    assert nodes[1]["fall_flag"] is True
    assert nodes[2]["ts"] == 1500


def test_node_latest_returns_most_recent(session_factory):
    persist(session_factory, _v(3, ts=10))
    persist(session_factory, _v(3, ts=20))
    client = _client(session_factory)

    body = client.get("/api/nodes/3/latest").json()
    assert body["ts"] == 20
    assert body["confidence"] == 0.88
    assert body["occupancy"] == 1


def test_node_latest_404_when_unknown(session_factory):
    client = _client(session_factory)
    assert client.get("/api/nodes/999/latest").status_code == 404
