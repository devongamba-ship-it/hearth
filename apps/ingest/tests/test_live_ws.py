"""WebSocket-level tests for the BAT-27 live mesh feed (`WS /homes/{id}/live`).

Covers acceptance criterion #1: packet_sim driving ingest produces a live WS
feed a browser client can render. One test drives the full chain (the real
packet_sim subprocess -> the real UDP listener -> SQLite -> the WS feed); a
lighter test asserts the streamed frame is render-ready.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hearth.packet import Vitals
from ingest.app import app
from ingest.db import init_db, make_engine, make_session_factory
from ingest.layout import load_layout
from ingest.listener import create_listener, persist

REPO_ROOT = Path(__file__).resolve().parents[3]
SIM = REPO_ROOT / "tools" / "packet_sim.py"
DEMO_LAYOUT = REPO_ROOT / "apps" / "ingest" / "layout.yaml"


@pytest.fixture(autouse=True)
def _reset_app_state():
    """Keep the module-level FastAPI app's state from leaking across tests."""
    yield
    for attr in ("layout", "live_interval", "clock", "session_factory", "source"):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def _configure(session_factory):
    app.state.session_factory = session_factory
    app.state.layout = load_layout(DEMO_LAYOUT)  # the demo home
    app.state.live_interval = 0.01  # fast ticks so tests don't wait on 1 Hz


def _read_room_activity(ws, tries: int = 10) -> dict:
    """Pull messages until the first room_activity frame (events may precede)."""
    for _ in range(tries):
        msg = ws.receive_json()
        if msg["type"] == "room_activity":
            return msg
    raise AssertionError("no room_activity frame received")


def _assert_renderable(frame: dict) -> None:
    assert frame["type"] == "room_activity"
    assert isinstance(frame["rooms"], list) and frame["rooms"]
    for room in frame["rooms"]:
        assert set(room) == {"room", "activity_index", "occupancy_count"}
        assert 0 <= room["activity_index"] <= 100
    assert isinstance(frame["tracks"], list)
    assert isinstance(frame["tracking_degraded"], bool)
    # BAT-34: the data-source label rides every frame so clients can badge it.
    assert frame["source"] in ("simulated", "live")


def test_ws_streams_renderable_frame(session_factory):
    # node 1 maps to "kitchen" in the demo layout.
    persist(session_factory, Vitals(
        node_id=1, presence=True, fall=False, motion=True, breathing_bpm=15.0,
        heart_bpm=72.0, rssi=-55, occupancy=1, motion_energy=2500.0,
        presence_score=0.9, timestamp_ms=1000,
    ))
    _configure(session_factory)

    with TestClient(app).websocket_connect("/homes/rosewood/live") as ws:
        frame = _read_room_activity(ws)

    _assert_renderable(frame)
    assert frame["source"] == "live"  # default when HEARTH_SOURCE is unset
    kitchen = next(r for r in frame["rooms"] if r["room"] == "kitchen")
    assert kitchen["occupancy_count"] == 1
    assert any(t["track_id"] == "A" and t["room"] == "kitchen" for t in frame["tracks"])


def test_ws_source_label_follows_app_state(session_factory):
    """BAT-34: app.state.source (operator's HEARTH_SOURCE) reaches the frame."""
    persist(session_factory, Vitals(
        node_id=1, presence=True, fall=False, motion=True, breathing_bpm=15.0,
        heart_bpm=72.0, rssi=-55, occupancy=1, motion_energy=2500.0,
        presence_score=0.9, timestamp_ms=1000,
    ))
    _configure(session_factory)
    app.state.source = "packet_sim"

    with TestClient(app).websocket_connect("/homes/rosewood/live") as ws:
        frame = _read_room_activity(ws)

    assert frame["source"] == "simulated"


# -- full chain: packet_sim -> listener -> SQLite -> WS feed ------------------


def _serve(url: str, state: dict, ready: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine = make_engine(url)
    init_db(engine)
    session_factory = make_session_factory(engine)

    async def go() -> None:
        transport, _protocol = await create_listener(
            session_factory, host="127.0.0.1", port=0
        )
        state["port"] = transport.get_extra_info("sockname")[1]
        ready.set()
        while not state.get("stop"):
            await asyncio.sleep(0.05)
        transport.close()

    loop.run_until_complete(go())
    loop.close()


def test_packet_sim_produces_renderable_live_feed(tmp_path):
    url = f"sqlite:///{tmp_path / 'live_e2e.db'}"
    state: dict = {}
    ready = threading.Event()
    listener = threading.Thread(target=_serve, args=(url, state, ready), daemon=True)
    listener.start()
    assert ready.wait(5), "listener did not start"

    proc = subprocess.run(
        [
            sys.executable, str(SIM),
            "--scenario", "normal",
            "--target", "127.0.0.1",
            "--port", str(state["port"]),
            "--duration", "5",
            "--speed", "50",
        ],
        cwd=str(REPO_ROOT), capture_output=True, text=True, timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    time.sleep(0.5)  # let the listener drain the last datagrams
    state["stop"] = True
    listener.join(5)

    # Now serve the live feed off the same DB the simulator filled.
    _configure(make_session_factory(make_engine(url)))
    with TestClient(app).websocket_connect("/homes/rosewood/live") as ws:
        frame = _read_room_activity(ws)

    _assert_renderable(frame)
    # The "normal" scenario is an active resident in node 1's room (kitchen).
    kitchen = next(r for r in frame["rooms"] if r["room"] == "kitchen")
    assert kitchen["occupancy_count"] >= 1
    assert kitchen["activity_index"] > 0
