"""BAT-43 — deterministic, screen-recordable demo mode.

Covers every acceptance criterion:

* Each scenario plays end-to-end through the REAL pipeline (the same
  ``listener.persist`` + ``FallMachine`` the live system uses) and resets to a
  clean state.
* Deterministic: the same seeded sequence every run.
* Demo state is visibly flagged — the player forces the live feed's BAT-34
  data-source label to ``simulated``, and the endpoints advertise it — so a demo
  is never confusable with live silicon.
* The recording guide ``docs/demo.md`` exists and documents every scenario.

The player tests drive it synchronously (no sleeps → no flaky timing, which is
the whole point of demo mode); endpoint tests use a synchronous run hook for the
same reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from ingest import demo
from ingest.app import app
from ingest.db import (
    CONFIRMED_FALL,
    Event,
    FallTransition,
    NEAR_FALL,
    Reading,
    init_db,
    make_engine,
    make_session_factory,
)
from ingest.detection import DetectionConfig
from ingest.escalator import NullEscalator
from ingest.fall_machine import State
from ingest.layout import load_layout

REPO_ROOT = Path(__file__).resolve().parents[3]
DEMO_LAYOUT = REPO_ROOT / "apps" / "ingest" / "layout.yaml"


@pytest.fixture
def sessions(tmp_path):
    engine = make_engine(f"sqlite:///{tmp_path / 'demo.db'}")
    init_db(engine)
    return make_session_factory(engine)


def _count(sessions, model, *where) -> int:
    stmt = select(func.count()).select_from(model)
    for clause in where:
        stmt = stmt.where(clause)
    with sessions() as s:
        return s.execute(stmt).scalar()


def _readings(sessions) -> list[tuple]:
    """The persisted reading sequence, minus the wall-clock-stamped fields."""
    with sessions() as s:
        rows = s.execute(
            select(
                Reading.node_id, Reading.ts, Reading.presence, Reading.motion,
                Reading.breathing_bpm, Reading.hr_bpm, Reading.fall_flag,
                Reading.occupancy,
            ).order_by(Reading.id)
        ).all()
    return [tuple(r) for r in rows]


def _play(sessions, scenario, **kw):
    player = demo.ScenarioPlayer(
        sessions, scenario, config=DetectionConfig(), escalator=NullEscalator(), **kw
    )
    player.play_sync()
    return player


# -- acceptance criterion #1: real pipeline, end to end ----------------------


def test_fall_confirmed_escalates_once_through_real_pipeline(sessions):
    player = _play(sessions, "fall_confirmed")
    # Real readings were persisted by the real persist() path.
    assert _count(sessions, Reading) == player.duration + 1
    # The real fall machine confirmed exactly one fall and handed it off once.
    assert _count(sessions, FallTransition, FallTransition.kind == CONFIRMED_FALL) == 1
    assert player.machine.state_of(1) is State.CONFIRMED_FALL


def test_fall_recovered_does_not_escalate(sessions):
    player = _play(sessions, "fall_recovered")
    assert _count(sessions, FallTransition, FallTransition.kind == CONFIRMED_FALL) == 0
    # They got up: exactly one near_fall, machine back to NORMAL.
    assert _count(sessions, FallTransition, FallTransition.kind == NEAR_FALL) == 1
    assert player.machine.state_of(4) is State.NORMAL


def test_quiet_morning_raises_no_fall(sessions):
    player = _play(sessions, "quiet_morning")
    assert _count(sessions, Reading) > 0
    assert _count(sessions, FallTransition, FallTransition.kind == CONFIRMED_FALL) == 0
    assert player.machine.state_of(2) is State.NORMAL


def test_multi_occupancy_lights_two_rooms(sessions):
    """multi_occupancy puts a present, moving node in two rooms at once so the
    live mesh emits a resident track plus an anonymous guest track."""
    _play(sessions, "multi_occupancy")
    with sessions() as s:
        nodes = sorted(set(s.scalars(select(Reading.node_id).distinct()).all()))
    assert nodes == [1, 4]
    assert _count(sessions, FallTransition, FallTransition.kind == CONFIRMED_FALL) == 0

    # The real live feed turns the two occupied rooms into two tracks.
    from ingest.layout import HomeLayout
    from ingest.live import LiveFeed

    layout = HomeLayout.from_detection(DetectionConfig(), [1, 2, 3, 4], home_id="rosewood")
    feed = LiveFeed(sessions, layout, home_id="rosewood", source="simulated", clock=lambda: 0.0)
    frame = next(m for m in feed.tick() if m["type"] == "room_activity")
    track_ids = {t["track_id"] for t in frame["tracks"]}
    assert len(frame["tracks"]) == 2
    assert "A" in track_ids and any(t.startswith("guest-") for t in track_ids)


@pytest.mark.parametrize("scenario", demo.scenario_names())
def test_every_scenario_plays_then_resets_clean(sessions, scenario):
    _play(sessions, scenario)
    assert _count(sessions, Reading) > 0

    counts = demo.reset(sessions)
    assert counts["readings"] > 0  # it reported what it cleared
    # Clean slate: nothing left in any pipeline table.
    assert _count(sessions, Reading) == 0
    assert _count(sessions, Event) == 0
    assert _count(sessions, FallTransition) == 0


# -- acceptance criterion #2: deterministic --------------------------------


@pytest.mark.parametrize("scenario", demo.scenario_names())
def test_scenario_is_deterministic(sessions, tmp_path, scenario):
    """Two independent runs of the same scenario produce the identical sequence."""
    _play(sessions, scenario)
    first = _readings(sessions)

    engine = make_engine(f"sqlite:///{tmp_path / 'demo2.db'}")
    init_db(engine)
    second_sessions = make_session_factory(engine)
    _play(second_sessions, scenario)
    second = _readings(second_sessions)

    assert first == second
    assert first  # and it actually produced something


def test_packet_sim_seeding_is_reproducible():
    """The simulator's seeded jitter is byte-for-byte reproducible (the demo's
    determinism rests on this)."""
    from random import Random

    sim = demo.load_sim()

    def take(n):
        gen = sim.frames("normal", Random(99))
        return [next(gen) for _ in range(n)]

    assert take(20) == take(20)
    # A different seed diverges (so it is really the seed driving it).
    other = [next(sim.frames("normal", Random(7))) for _ in range(1)]
    assert take(1) != other


# -- acceptance criterion #3: visibly flagged as simulated -----------------


@pytest.fixture
def _clean_app_state():
    yield
    for attr in (
        "session_factory", "detection_config", "layout", "live_interval",
        "source", "demo_enabled", "demo_escalator", "demo_runner",
        "demo_player", "demo_task",
    ):
        if hasattr(app.state, attr):
            delattr(app.state, attr)


def test_live_feed_is_simulated_in_demo_mode(sessions, _clean_app_state):
    """With demo mode on and no explicit source set, the live feed badges itself
    simulated (BAT-34) — a demo can never read as live silicon."""
    app.state.session_factory = sessions
    app.state.layout = load_layout(DEMO_LAYOUT)
    app.state.live_interval = 0.01
    app.state.demo_enabled = True

    client = TestClient(app)
    with client.websocket_connect("/homes/rosewood/live") as ws:
        frame = _first_room_activity(ws)
    assert frame["source"] == "simulated"


def test_demo_mode_overrides_an_explicit_live_source(sessions, _clean_app_state):
    """PR #22 review #1: demo mode HARD-overrides the source. A box left with
    source='live' that is flipped into demo must still badge frames simulated —
    simulated data can never read as live silicon (AC3, privacy-is-the-brand)."""
    app.state.session_factory = sessions
    app.state.layout = load_layout(DEMO_LAYOUT)
    app.state.live_interval = 0.01
    app.state.source = "live"        # the box was running live...
    app.state.demo_enabled = True    # ...then flipped into demo

    client = TestClient(app)
    with client.websocket_connect("/homes/rosewood/live") as ws:
        frame = _first_room_activity(ws)
    assert frame["source"] == "simulated"  # demo wins, unconditionally


def _first_room_activity(ws):
    for _ in range(10):
        msg = ws.receive_json()
        if msg["type"] == "room_activity":
            return msg
    raise AssertionError("no room_activity frame")


# -- endpoints ---------------------------------------------------------------


@pytest.fixture
def demo_client(sessions, _clean_app_state):
    app.state.session_factory = sessions
    app.state.detection_config = DetectionConfig()
    app.state.demo_escalator = NullEscalator()
    # Run the playout synchronously so the endpoint tests never wait on a clock.
    app.state.demo_runner = lambda player, speed: player.play_sync()
    return TestClient(app)


def test_endpoints_404_when_demo_disabled(demo_client):
    # demo_enabled not set -> off.
    assert demo_client.get("/api/demo/status").status_code == 404
    assert demo_client.post("/api/demo/play", json={"scenario": "fall_confirmed"}).status_code == 404
    assert demo_client.post("/api/demo/reset").status_code == 404


def test_status_lists_scenarios(demo_client):
    app.state.demo_enabled = True
    body = demo_client.get("/api/demo/status").json()
    assert body["enabled"] is True
    assert body["playing"] is False
    assert body["source"] == "simulated"
    assert body["home_id"] == "rosewood"
    assert body["scenarios"] == ["fall_confirmed", "fall_recovered", "multi_occupancy", "quiet_morning"]


def test_play_unknown_scenario_is_422(demo_client):
    app.state.demo_enabled = True
    assert demo_client.post("/api/demo/play", json={"scenario": "nope"}).status_code == 422


def test_play_runs_scenario_then_reset_clears(demo_client, sessions):
    app.state.demo_enabled = True

    played = demo_client.post("/api/demo/play", json={"scenario": "fall_confirmed"})
    assert played.status_code == 200
    body = played.json()
    assert body["playing"] is True and body["scenario"] == "fall_confirmed"
    assert body["source"] == "simulated"

    # The real pipeline ran via the endpoint: readings + one confirmed fall.
    assert _count(sessions, Reading) > 0
    assert _count(sessions, FallTransition, FallTransition.kind == CONFIRMED_FALL) == 1
    # The escalation hand-off went through the (overridden) escalator once.
    assert len(app.state.demo_escalator.calls) == 1

    reset = demo_client.post("/api/demo/reset").json()
    assert reset["reset"] is True and reset["playing"] is False
    assert _count(sessions, Reading) == 0
    assert _count(sessions, FallTransition) == 0


# -- acceptance criterion #4: the recording guide exists --------------------


def test_demo_doc_exists_and_documents_every_scenario():
    doc = REPO_ROOT / "docs" / "demo.md"
    assert doc.exists(), "docs/demo.md (the recording guide) is missing"
    text = doc.read_text()
    for scenario in demo.scenario_names():
        assert scenario in text, f"docs/demo.md does not document {scenario}"
