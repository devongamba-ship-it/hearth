"""Unit tests for the BAT-27 live mesh-view feed core (`ingest.live.LiveFeed`).

The feed is deterministic by construction — an injected clock and a session
factory — so every acceptance behaviour is exercised here with no sleeps and no
WebSocket. The thin async WS driver is covered in test_live_ws.py.
"""

from __future__ import annotations

import json

from hearth.packet import Vitals
from ingest.db import FallTransition
from ingest.layout import load_layout_from_text
from ingest.listener import persist
from ingest.live import DROPOUT_SECS, DWELL_SECS, LiveFeed, activity_index

LAYOUT_YAML = """
home_id: rosewood
name: Rosewood
rooms:
  - {id: kitchen, name: Kitchen}
  - {id: bedroom, name: Bedroom, bedroom: true}
  - {id: living, name: Living room}
nodes:
  - {id: 1, room: kitchen, x: 0.25, y: 0.25}
  - {id: 2, room: bedroom, x: 0.75, y: 0.25}
  - {id: 4, room: living, x: 0.25, y: 0.75}
links:
  - [1, 4]
  - [4, 2]
resident:
  track_label: A
  identity_confidence: 0.9
"""


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def _persist(sf, node_id, *, motion=2000.0, ts=1000, presence=True,
             occupancy=1, confidence=0.9, fall=False):
    persist(sf, Vitals(
        node_id=node_id, presence=presence, fall=fall,
        motion=motion > 200, breathing_bpm=15.0, heart_bpm=72.0, rssi=-55,
        occupancy=occupancy, motion_energy=float(motion),
        presence_score=confidence, timestamp_ms=ts,
    ))


def _feed(sf, clock):
    return LiveFeed(sf, load_layout_from_text(LAYOUT_YAML), clock=clock)


def _frame(messages):
    return next(m for m in messages if m["type"] == "room_activity")


def _events(messages, name):
    return [m for m in messages if m.get("type") == "event" and m.get("event") == name]


# -- activity index + room_activity frame -----------------------------------


def test_activity_index_scales_and_clamps():
    assert activity_index(0) == 0
    assert activity_index(3000) == 100
    assert activity_index(9000) == 100  # clamped
    assert activity_index(1500) == 50


def test_room_activity_frame_shape(session_factory):
    _persist(session_factory, 1, motion=3000, occupancy=1)
    frame = _frame(_feed(session_factory, FakeClock(0)).tick())

    assert frame["type"] == "room_activity"
    assert frame["home_id"] == "rosewood"
    rooms = {r["room"]: r for r in frame["rooms"]}
    # Every layout room is present so the floor plan renders fully.
    assert set(rooms) == {"kitchen", "bedroom", "living"}
    assert rooms["kitchen"]["activity_index"] == 100
    assert rooms["kitchen"]["occupancy_count"] == 1
    assert rooms["bedroom"]["activity_index"] == 0
    assert rooms["bedroom"]["occupancy_count"] == 0


def test_single_resident_track_with_identity_confidence_from_config(session_factory):
    _persist(session_factory, 1, motion=2500, occupancy=1, confidence=0.9)
    frame = _frame(_feed(session_factory, FakeClock(0)).tick())

    assert frame["tracking_degraded"] is False
    assert len(frame["tracks"]) == 1
    track = frame["tracks"][0]
    assert track["track_id"] == "A"
    assert track["label"] == "A"
    assert track["room"] == "kitchen"
    assert track["identity_confidence"] == 0.9  # from layout config
    assert track["state"] == "normal"


def test_extra_targets_become_guest_tracks(session_factory):
    # Two detected persons in the kitchen: resident A + one anonymous guest.
    _persist(session_factory, 1, motion=2500, occupancy=2)
    frame = _frame(_feed(session_factory, FakeClock(0)).tick())

    tracks = {t["track_id"]: t for t in frame["tracks"]}
    assert set(tracks) == {"A", "guest-1"}
    guest = tracks["guest-1"]
    assert guest["label"] == "guest-1"
    assert guest["room"] == "kitchen"
    assert guest["state"] == "guest"
    assert guest["identity_confidence"] == 0.0  # no baseline for a guest


def test_low_confidence_degrades_to_count_badges(session_factory):
    # Track separation is untrustworthy -> occupancy counts, no positional tracks.
    _persist(session_factory, 1, motion=2500, occupancy=1, confidence=0.3)
    frame = _frame(_feed(session_factory, FakeClock(0)).tick())

    assert frame["tracking_degraded"] is True
    assert frame["tracks"] == []
    rooms = {r["room"]: r for r in frame["rooms"]}
    assert rooms["kitchen"]["occupancy_count"] == 1  # count still reported


# -- dropout (acceptance criterion #3) --------------------------------------


def test_dropout_event_after_node_silent_over_10s(session_factory):
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    _persist(session_factory, 1)
    feed.tick()  # node 1 seen at t=0

    clock.t = DROPOUT_SECS  # exactly 10s — not yet "> 10s"
    assert _events(feed.tick(), "sensing_dropout") == []

    clock.t = DROPOUT_SECS + 1  # 11s of silence
    drops = _events(feed.tick(), "sensing_dropout")
    assert len(drops) == 1
    assert drops[0]["node_id"] == 1
    assert drops[0]["room"] == "kitchen"


def test_dropout_not_repeated_then_clears_on_recovery(session_factory):
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    _persist(session_factory, 1, ts=1000)
    feed.tick()

    clock.t = 11
    assert len(_events(feed.tick(), "sensing_dropout")) == 1
    clock.t = 12
    assert _events(feed.tick(), "sensing_dropout") == []  # not repeated

    # A fresh packet clears the dropout; going silent again re-arms it.
    _persist(session_factory, 1, ts=2000)
    clock.t = 13
    feed.tick()
    clock.t = 24
    assert len(_events(feed.tick(), "sensing_dropout")) == 1


# -- ticker events: room transition + dwell ----------------------------------


def test_room_transition_event_when_resident_moves(session_factory):
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)

    _persist(session_factory, 1, ts=1000, presence=True, occupancy=1)
    _persist(session_factory, 2, ts=1000, presence=False, occupancy=0)
    # Resident already present at connect is not a movement (PR #7 review #2).
    assert _events(feed.tick(), "room_transition") == []

    clock.t = 1
    _persist(session_factory, 1, ts=2000, presence=False, occupancy=0)
    _persist(session_factory, 2, ts=2000, presence=True, occupancy=1)
    moves = _events(feed.tick(), "room_transition")
    assert len(moves) == 1
    assert moves[0]["from_room"] == "kitchen"
    assert moves[0]["room"] == "bedroom"
    assert moves[0]["track_id"] == "A"


def test_dwell_start_after_threshold(session_factory):
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)

    _persist(session_factory, 1, ts=1000)
    assert _events(feed.tick(), "dwell_start") == []  # just arrived

    clock.t = DWELL_SECS - 1
    _persist(session_factory, 1, ts=2000)  # fresh packet, same room
    assert _events(feed.tick(), "dwell_start") == []

    clock.t = DWELL_SECS
    _persist(session_factory, 1, ts=3000)
    dwell = _events(feed.tick(), "dwell_start")
    assert len(dwell) == 1
    assert dwell[0]["room"] == "kitchen"
    assert dwell[0]["track_id"] == "A"


# -- incident transitions + fall_watch track state ---------------------------


def test_incident_messages_stream_new_transitions(session_factory):
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    _persist(session_factory, 1)
    feed.tick()  # prime: high-water mark set past any existing transitions

    with session_factory() as s:
        s.add(FallTransition(
            incident_id="inc-1", node_id=1, reading_ts=1000, at=123.0,
            from_state="NORMAL", to_state="SUSPECTED", kind="transition",
            reason=None, features=None,
        ))
        s.commit()

    messages = feed.tick()
    incidents = [m for m in messages if m["type"] == "incident"]
    assert len(incidents) == 1
    assert incidents[0]["incident_id"] == "inc-1"
    assert incidents[0]["to_state"] == "SUSPECTED"
    assert incidents[0]["room"] == "kitchen"

    # The resident track in that room now reads as fall_watch.
    track = _frame(messages)["tracks"][0]
    assert track["state"] == "fall_watch"

    # Already-streamed transitions are not re-emitted next tick.
    assert [m for m in feed.tick() if m["type"] == "incident"] == []


def test_priming_tick_does_not_replay_incident_history(session_factory):
    """PR #7 review concern #1: transitions already in the table at connect
    must not stream as live incidents on the priming tick."""
    clock = FakeClock(0)
    _persist(session_factory, 1)
    with session_factory() as s:
        for i, state in enumerate(["SUSPECTED", "PROBABLE"]):
            s.add(FallTransition(
                incident_id="inc-old", node_id=1, reading_ts=900 + i,
                at=float(i), from_state="NORMAL", to_state=state,
                kind="transition", reason=None, features=None,
            ))
        s.commit()

    feed = _feed(session_factory, clock)
    assert [m for m in feed.tick() if m["type"] == "incident"] == []

    # A genuinely new transition after priming still streams, exactly once.
    with session_factory() as s:
        s.add(FallTransition(
            incident_id="inc-new", node_id=1, reading_ts=1000, at=10.0,
            from_state="PROBABLE", to_state="CHECK_IN_ACTIVE",
            kind="transition", reason=None, features=None,
        ))
        s.commit()
    incidents = [m for m in feed.tick() if m["type"] == "incident"]
    assert [m["incident_id"] for m in incidents] == ["inc-new"]


def test_no_room_transition_on_connect(session_factory):
    """PR #7 review concern #2: presence at connect is not a movement."""
    _persist(session_factory, 1)
    feed = _feed(session_factory, FakeClock(0))
    assert _events(feed.tick(), "room_transition") == []


# -- privacy invariant -------------------------------------------------------


def test_frame_carries_no_raw_readings(session_factory):
    _persist(session_factory, 1, motion=3000, occupancy=1, confidence=0.9)
    frame = _frame(_feed(session_factory, FakeClock(0)).tick())
    blob = json.dumps(frame)

    # No raw reading fields leave the home — aggregates only.
    for forbidden in ("motion_energy", "breathing", "hr_bpm", "presence_score",
                      "fall_flag"):
        assert forbidden not in blob
    assert "3000" not in blob  # the raw motion-energy value never appears
