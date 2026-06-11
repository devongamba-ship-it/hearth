"""Tests for the BAT-27 layout endpoint upgrade (`GET /homes/{id}/layout`).

Acceptance criterion #2: the layout endpoint serves the demo home (rooms, node
markers, link topology) from the hand-authored layout.yaml. The BAT-25 derived
stub remains the fallback when no layout is authored, so its consumer/test stay
green — that compatibility is asserted here too.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ingest.app import app
from ingest.detection import load_config_from_text
from ingest.layout import load_layout

DEMO_LAYOUT = Path(__file__).resolve().parents[1] / "layout.yaml"


@pytest.fixture(autouse=True)
def _reset_app_state():
    yield
    for attr in ("layout", "session_factory", "detection_config"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_layout_serves_authored_demo_home(session_factory):
    app.state.session_factory = session_factory
    app.state.layout = load_layout(DEMO_LAYOUT)
    body = TestClient(app).get("/homes/rosewood/layout").json()

    assert body["home_id"] == "rosewood"
    assert body["source"] == "layout.yaml"

    rooms = {r["id"]: r for r in body["rooms"]}
    assert {"kitchen", "living", "bedroom", "bathroom"} <= set(rooms)
    assert rooms["bedroom"]["bedroom"] is True
    assert rooms["kitchen"]["bedroom"] is False

    nodes = {n["id"]: n for n in body["nodes"]}
    assert nodes[1]["room"] == "kitchen"
    assert nodes[1]["x"] is not None and nodes[1]["y"] is not None

    # Link topology is a list of node-id pairs.
    assert {"a", "b"} == set(body["links"][0])
    assert all(isinstance(link["a"], int) for link in body["links"])

    # Resident-track config the live feed reads identity confidence from.
    assert body["resident"]["track_label"] == "A"
    assert body["resident"]["identity_confidence"] == 0.9


def test_layout_falls_back_to_derived_stub_without_authored_layout(session_factory):
    # No app.state.layout and no HEARTH_LAYOUT_FILE -> the BAT-25 shape, so the
    # existing caregiver-app consumer keeps working.
    app.state.session_factory = session_factory
    app.state.detection_config = load_config_from_text("rooms: {1: Kitchen}\n")
    body = TestClient(app).get("/homes/home-1/layout").json()

    assert body["geometry_stub"] is True
    rooms = {r["node_id"]: r for r in body["rooms"]}
    assert rooms[1]["room"] == "Kitchen"
