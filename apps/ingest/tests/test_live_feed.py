"""Unit tests for the BAT-27 live mesh-view feed core (`ingest.live.LiveFeed`).

The feed is deterministic by construction — an injected clock and a session
factory — so every acceptance behaviour is exercised here with no sleeps and no
WebSocket. The thin async WS driver is covered in test_live_ws.py.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from hearth.packet import Vitals
from ingest.db import FallTransition
from ingest.detection import DetectionConfig
from ingest.escalator import NullEscalator
from ingest.fall_machine import FallMachine, Frame, State
from ingest.layout import load_layout_from_text
from ingest.listener import persist
from ingest.live import (
    DROPOUT_SECS,
    DWELL_SECS,
    RESP_MIN_SAMPLES,
    SOURCE_LIVE,
    SOURCE_SIMULATED,
    WINDOW_SECS,
    LiveFeed,
    activity_index,
    posture_estimate,
    resolve_source,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_sim():
    spec = importlib.util.spec_from_file_location(
        "packet_sim", REPO_ROOT / "tools" / "packet_sim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIM = _load_sim()

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
             occupancy=1, confidence=0.9, fall=False, breath=15.0):
    persist(sf, Vitals(
        node_id=node_id, presence=presence, fall=fall,
        motion=motion > 200, breathing_bpm=breath, heart_bpm=72.0, rssi=-55,
        occupancy=occupancy, motion_energy=float(motion),
        presence_score=confidence, timestamp_ms=ts,
    ))


def _feed(sf, clock):
    return LiveFeed(sf, load_layout_from_text(LAYOUT_YAML), clock=clock)


def _frame(messages):
    return next(m for m in messages if m["type"] == "room_activity")


def _events(messages, name):
    return [m for m in messages if m.get("type") == "event" and m.get("event") == name]


def _resident(messages):
    """The resident (track 'A') object from a tick's room_activity frame."""
    return next(t for t in _frame(messages)["tracks"] if t["track_id"] == "A")


def _drive_sim(feed, sf, scenario, ticks, *, node_id=1, machine=None):
    """Replay `ticks` of a real packet_sim scenario at 1 Hz into the feed.

    One persisted reading per simulated second, ticking the feed each time, so
    the rolling windows fill exactly as they would against the live listener. If
    a `machine` is given, every frame is also stepped through the fall machine
    (which persists its transitions to the same DB), so the feed picks up the
    real fall-state cascade. Returns the final tick's messages.
    """
    messages: list = []
    for t, f in zip(range(ticks), SIM.frames(scenario)):
        vitals = SIM.frame_to_vitals(node_id, t * 1000, f)
        persist(sf, vitals)
        if machine is not None:
            machine.step(Frame.from_vitals(vitals))
        messages = feed.tick()
    return messages


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


def test_frame_carries_authoritative_node_health(session_factory):
    """BAT-34 review: the badge can't be reconstructed from the one-shot
    sensing_dropout event. Every frame must carry nodes_reporting + nodes_offline
    so a sustained outage stays flagged for its whole duration."""
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    _persist(session_factory, 1, ts=1000)
    frame = _frame(feed.tick())
    assert frame["nodes_reporting"] == 1
    assert frame["nodes_offline"] == []

    # Node 1 goes silent and STAYS silent: offline must persist across ticks,
    # not flicker back after the single dropout event.
    for t in (11, 12, 20, 60):
        clock.t = t
        frame = _frame(feed.tick())
        assert frame["nodes_offline"] == [1], f"flickered back at t={t}"
        assert frame["nodes_reporting"] == 0

    # A fresh packet clears it — the real recovery signal.
    _persist(session_factory, 1, ts=2000)
    clock.t = 61
    frame = _frame(feed.tick())
    assert frame["nodes_offline"] == []
    assert frame["nodes_reporting"] == 1


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


# -- coarse vitals estimates (BAT-33) ----------------------------------------


def test_posture_unit_mapping():
    # Fall watch dominates regardless of residual motion.
    assert posture_estimate(5000.0, present=True, watch=True) == "floor"
    assert posture_estimate(0.0, present=True, watch=True) == "floor"
    # Present + sustained motion = upright; present + still = seated.
    assert posture_estimate(2500.0, present=True, watch=False) == "upright"
    assert posture_estimate(40.0, present=True, watch=False) == "seated"
    # Nobody to classify.
    assert posture_estimate(0.0, present=False, watch=False) == "unknown"


def test_respiration_estimate_within_2bpm_after_smoothing_window(session_factory):
    """AC#1: the packet_sim feed yields a respiration estimate within ±2 BPM of
    the sim's configured breathing rate, once the smoothing window has filled."""
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)

    # Before the window warms up the estimate is withheld (not fabricated).
    early = _drive_sim(feed, session_factory, "normal", ticks=RESP_MIN_SAMPLES - 1)
    assert "respiration_bpm_estimate" not in _resident(early)

    # After >= WINDOW_SECS samples it appears, rounded to an integer, within ±2
    # BPM of the normal scenario's configured ~15 BPM (uniform 13-17).
    later = _drive_sim(feed, session_factory, "normal", ticks=10)
    resident = _resident(later)
    resp = resident["respiration_bpm_estimate"]
    assert isinstance(resp, int)
    assert abs(resp - 15) <= 2


def test_respiration_estimate_is_smoothed_not_a_single_reading(session_factory):
    """PR #13 review nit: the published estimate is the window mean, so a single
    wildly-off sample barely moves it — proving the smoothing, not just that one
    reading lands in a wide band."""
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    for t in range(RESP_MIN_SAMPLES + 2):
        _persist(session_factory, 1, ts=1000 + t, breath=15.0)
        messages = feed.tick()
    assert _resident(messages)["respiration_bpm_estimate"] == 15

    _persist(session_factory, 1, ts=2000, breath=45.0)  # one outlier
    messages = feed.tick()
    assert abs(_resident(messages)["respiration_bpm_estimate"] - 15) <= 1


def test_respiration_ages_out_when_breath_lock_lost(session_factory):
    """PR #13 review concern #1: a node that keeps reporting presence but loses
    its breathing lock (breathing=0) must let the respiration estimate age out to
    absent within a window — not republish the last good mean forever."""
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    for t in range(RESP_MIN_SAMPLES + 2):  # warm up a steady lock
        _persist(session_factory, 1, ts=1000 + t, breath=15.0)
        messages = feed.tick()
    assert _resident(messages)["respiration_bpm_estimate"] == 15

    for t in range(WINDOW_SECS):  # presence persists, breath lock gone
        _persist(session_factory, 1, ts=2000 + t, breath=0.0)
        messages = feed.tick()
    assert "respiration_bpm_estimate" not in _resident(messages)

    for t in range(2):  # lock returns -> estimate comes back
        _persist(session_factory, 1, ts=3000 + t, breath=15.0)
        messages = feed.tick()
    assert _resident(messages)["respiration_bpm_estimate"] == 15


def test_posture_upright_during_normal_scenario(session_factory):
    """AC#2 (normal half): a moving resident reads as upright."""
    feed = _feed(session_factory, FakeClock(0))
    messages = _drive_sim(feed, session_factory, "normal", ticks=RESP_MIN_SAMPLES + 5)
    assert _resident(messages)["posture_estimate"] == "upright"


def test_posture_floor_during_fall_confirmed_scenario(session_factory):
    """AC#2 (fall half): driving the real fall_confirmed scenario through the
    fall machine drives the resident's posture estimate to floor."""
    feed = _feed(session_factory, FakeClock(0))
    machine = FallMachine(
        DetectionConfig(), escalator=NullEscalator(),
        clock=lambda: 1_700_000_000.0, session_factory=session_factory,
    )
    # 110s reaches CONFIRMED_FALL (impact at t=30, 45s check-in window after).
    messages = _drive_sim(
        feed, session_factory, "fall_confirmed", ticks=110, machine=machine
    )
    assert machine.state_of(1) is State.CONFIRMED_FALL
    resident = _resident(messages)
    assert resident["state"] == "fall_watch"
    assert resident["posture_estimate"] == "floor"


def test_vitals_absent_below_confidence_floor_but_track_still_placed(session_factory):
    """Spec: a track can be placed (>= TRACK_CONF_MIN) while presence confidence
    is too low to estimate vitals — then both estimates are simply absent."""
    feed = _feed(session_factory, FakeClock(0))
    # 0.45 is above TRACK_CONF_MIN (0.4) but below VITALS_CONF_MIN (0.5).
    for t in range(RESP_MIN_SAMPLES + 5):
        _persist(session_factory, 1, motion=2500, confidence=0.45, ts=1000 + t)
        messages = feed.tick()
    resident = _resident(messages)
    assert "respiration_bpm_estimate" not in resident
    assert "posture_estimate" not in resident


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


def test_respiration_estimate_is_smoothed_never_a_raw_reading(session_factory):
    """AC#4: the coarse respiration estimate is a rounded, smoothed aggregate —
    the raw per-second breathing value never appears in the frame."""
    clock = FakeClock(0)
    feed = _feed(session_factory, clock)
    # A distinctive raw breathing rate that must NOT leak verbatim.
    for t in range(RESP_MIN_SAMPLES + 2):
        _persist(session_factory, 1, motion=2500, confidence=0.9,
                 breath=15.37, ts=1000 + t)
        messages = feed.tick()
    frame = _frame(messages)
    resident = _resident(messages)

    assert resident["respiration_bpm_estimate"] == 15  # rounded, integer
    blob = json.dumps(frame)
    assert "15.37" not in blob  # the raw breathing value never appears
    # Raw reading field names still never leave the home.
    for forbidden in ("motion_energy", "breathing", "breathing_bpm", "hr_bpm",
                      "presence_score", "fall_flag"):
        assert forbidden not in blob


# -- BAT-34: data-source label on the frame ----------------------------------


def test_resolve_source_maps_sim_aliases_to_simulated():
    """The operator-set HEARTH_SOURCE value normalises to the two frame labels."""
    for alias in ("sim", "simulated", "SIMULATED", "Packet_Sim", "  packet-sim  "):
        assert resolve_source(alias) == SOURCE_SIMULATED


def test_resolve_source_defaults_to_live():
    """Unset / unknown / live -> "live": real silicon is the production default."""
    for value in (None, "", "   ", "live", "esp32", "hardware"):
        assert resolve_source(value) == SOURCE_LIVE


def test_frame_defaults_to_live_source(session_factory):
    """A feed with no configured source advertises itself as live."""
    _persist(session_factory, 1, motion=2500, occupancy=1)
    frame = _frame(_feed(session_factory, FakeClock(0)).tick())
    assert frame["source"] == SOURCE_LIVE


def test_frame_carries_simulated_source_when_configured(session_factory):
    """source="simulated" surfaces verbatim on the room_activity frame."""
    _persist(session_factory, 1, motion=2500, occupancy=1)
    feed = LiveFeed(
        session_factory, load_layout_from_text(LAYOUT_YAML),
        source="packet_sim", clock=FakeClock(0),
    )
    frame = _frame(feed.tick())
    assert frame["source"] == SOURCE_SIMULATED


def test_source_label_is_not_a_raw_reading(session_factory):
    """The source field is a coarse, fixed label — never a raw reading (privacy)."""
    _persist(session_factory, 1, motion=3000, occupancy=1, confidence=0.9)
    feed = LiveFeed(
        session_factory, load_layout_from_text(LAYOUT_YAML),
        source="simulated", clock=FakeClock(0),
    )
    frame = _frame(feed.tick())
    assert frame["source"] in (SOURCE_LIVE, SOURCE_SIMULATED)
    # Adding the label introduced no raw reading fields.
    blob = json.dumps(frame)
    for forbidden in ("motion_energy", "breathing", "hr_bpm", "presence_score",
                      "fall_flag"):
        assert forbidden not in blob
