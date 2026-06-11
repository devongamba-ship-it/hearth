"""Caregiver-app endpoints (BAT-25): coarse status + state-machine replay.

These exercise the real ingest tables — readings persisted by the listener path,
fall transitions written by the BAT-22 machine — so the status card and the
alert replay reflect genuine pipeline output, not fixtures.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from hearth.packet import Vitals
from ingest.app import app
from ingest.detection import DetectionConfig, QuietHours
from ingest.fall_machine import FallMachine, FallMachineRunner, State
from ingest.listener import persist


def _client(session_factory, *, config=None, now=None) -> TestClient:
    app.state.session_factory = session_factory
    app.state.detection_config = config
    app.state.clock = (lambda: now) if now is not None else None
    return TestClient(app)


def _v(node_id, fall, motion, ts, presence=True) -> Vitals:
    return Vitals(
        node_id=node_id, presence=presence, fall=fall, motion=motion > 200,
        breathing_bpm=15.0, heart_bpm=72.0, rssi=-55, occupancy=1,
        motion_energy=float(motion), presence_score=0.9, timestamp_ms=ts,
    )


# A bedroom-aware config so we can exercise the "sleeping" branch.
def _config(**kw) -> DetectionConfig:
    base = dict(rooms={1: "Kitchen", 2: "Bedroom"}, bedroom_nodes=(2,))
    base.update(kw)
    return DetectionConfig(**base)


# -- status card ------------------------------------------------------------


def test_status_all_good_reports_last_activity(session_factory):
    persist(session_factory, _v(1, fall=False, motion=2000, ts=10_000))
    persist(session_factory, _v(1, fall=False, motion=20, ts=130_000))  # newest, still
    client = _client(session_factory, config=_config())

    body = client.get("/api/status").json()
    assert body["state"] == "all_good"
    assert body["presence"] is True
    assert body["fall_state"] == "NORMAL"
    # Last *motion* was node 1 (Kitchen) two minutes before the newest reading.
    assert body["last_activity"]["room"] == "Kitchen"
    assert body["last_activity"]["seconds_ago"] == 120
    assert "Kitchen" in body["detail"]
    assert body["privacy"] == "Coarse status only. No cameras, no audio recording."


def test_status_no_one_home(session_factory):
    persist(session_factory, _v(1, fall=False, motion=10, ts=1000, presence=False))
    client = _client(session_factory, config=_config())
    body = client.get("/api/status").json()
    assert body["state"] == "no_activity"
    assert body["presence"] is False


def test_status_sleeping_during_quiet_hours(session_factory):
    persist(session_factory, _v(2, fall=False, motion=15, ts=1000))  # still, bedroom
    qh = QuietHours(enabled=True, start_hour=22, end_hour=7, stillness_threshold=120.0)
    client = _client(session_factory, config=_config(quiet_hours=qh), now=_at_hour(3))
    body = client.get("/api/status").json()
    assert body["state"] == "sleeping"
    assert body["headline"] == "Sleeping"


def test_status_checking_in_while_fall_unconfirmed(session_factory):
    # Drive the machine into CHECK_IN_ACTIVE (a possible fall, no alert yet).
    machine = FallMachine(DetectionConfig(confirm_stillness_secs=2),
                          session_factory=session_factory)
    persist(session_factory, _v(1, fall=True, motion=9500, ts=0))
    persist(session_factory, _v(1, fall=False, motion=10, ts=1000))
    persist(session_factory, _v(1, fall=False, motion=10, ts=2000))
    runner = FallMachineRunner(machine, session_factory)
    runner.poll_once()
    assert machine.state_of(1) is State.CHECK_IN_ACTIVE

    client = _client(session_factory, config=_config())
    body = client.get("/api/status").json()
    assert body["state"] == "checking_in"
    assert body["fall_state"] == "CHECK_IN_ACTIVE"


def test_status_empty_when_no_readings(session_factory):
    client = _client(session_factory, config=_config())
    body = client.get("/api/status").json()
    assert body["state"] == "no_activity"
    assert body["last_activity"] is None


# -- fall-incident replay ---------------------------------------------------


def _drive_confirmed_fall(session_factory) -> str:
    """Run the machine to a CONFIRMED_FALL and return its incident id."""
    cfg = DetectionConfig(confirm_stillness_secs=2, checkin_timeout_secs=3)
    machine = FallMachine(cfg, session_factory=session_factory)
    persist(session_factory, _v(1, fall=True, motion=9500, ts=0))     # impact
    for i in range(1, 12):  # sustained stillness past stillness + check-in timeout
        persist(session_factory, _v(1, fall=False, motion=10, ts=i * 1000))
    runner = FallMachineRunner(machine, session_factory)
    runner.poll_once()
    assert machine.state_of(1) is State.CONFIRMED_FALL
    return machine.nodes[1].incident_id  # may be None after reset; capture below


def test_fall_incidents_lists_only_confirmed(session_factory):
    _drive_confirmed_fall(session_factory)
    client = _client(session_factory, config=_config())

    body = client.get("/api/fall-incidents").json()
    assert len(body["incidents"]) == 1
    inc = body["incidents"][0]
    assert inc["room"] == "Kitchen"
    assert inc["confirmed_at"] >= inc["started_at"]
    assert inc["num_transitions"] >= 4  # NORMAL→SUSPECTED→PROBABLE→CHECK_IN→CONFIRMED


def test_fall_incident_detail_replays_state_machine(session_factory):
    _drive_confirmed_fall(session_factory)
    client = _client(session_factory, config=_config())

    incident_id = client.get("/api/fall-incidents").json()["incidents"][0]["incident_id"]
    detail = client.get(f"/api/fall-incidents/{incident_id}").json()

    states = [t["to_state"] for t in detail["transitions"]]
    assert "SUSPECTED" in states
    assert "CHECK_IN_ACTIVE" in states
    assert states[-1] == "CONFIRMED_FALL"
    assert detail["room"] == "Kitchen"
    # Every step carries wall-clock + ms-since-boot timestamps; no raw features.
    for t in detail["transitions"]:
        assert "at" in t and "reading_ts" in t
        assert "features" not in t


def test_fall_incident_detail_404(session_factory):
    client = _client(session_factory, config=_config())
    assert client.get("/api/fall-incidents/nope").status_code == 404


# -- layout / trends / activity (stubs, privacy-safe) -----------------------


def test_layout_lists_rooms_from_config_and_readings(session_factory):
    persist(session_factory, _v(1, fall=False, motion=2000, ts=1000))
    persist(session_factory, _v(3, fall=False, motion=2000, ts=1000))
    client = _client(session_factory, config=_config())
    body = client.get("/homes/home-1/layout").json()
    rooms = {r["node_id"]: r for r in body["rooms"]}
    assert rooms[1]["room"] == "Kitchen"
    assert rooms[2]["bedroom"] is True   # from config even with no readings yet
    assert rooms[3]["room"] == "node-3"  # unmapped fallback


def test_trends_validates_window(session_factory):
    client = _client(session_factory, config=_config())
    assert client.get("/homes/home-1/trends?window=7d").status_code == 200
    bad = client.get("/homes/home-1/trends?window=bogus")
    assert bad.status_code == 422


def test_trends_returns_plain_language_cards(session_factory):
    client = _client(session_factory, config=_config())
    body = client.get("/homes/home-1/trends?window=30d").json()
    assert body["stub"] is True
    keys = {c["key"] for c in body["cards"]}
    assert {"sleep_window", "active_minutes", "bathroom_count"} <= keys
    assert all(c["delta"] for c in body["cards"])


def test_activity_blocks_are_coarse(session_factory):
    client = _client(session_factory, config=_config())
    body = client.get("/api/activity?date=2026-06-11").json()
    assert body["stub"] is True
    assert body["blocks"]
    # Coarse blocks only — start/end labels, never per-second motion values.
    for block in body["blocks"]:
        assert set(block) == {"room", "start", "end", "label"}


# -- helpers ----------------------------------------------------------------


def _at_hour(hour: int) -> float:
    """An epoch whose local wall-clock hour is `hour` (for quiet-hours tests)."""
    from datetime import datetime
    base = datetime.now().replace(hour=hour, minute=0, second=0, microsecond=0)
    return base.timestamp()
