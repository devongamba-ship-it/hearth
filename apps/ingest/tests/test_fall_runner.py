"""The SQLite poller feeds new readings into the machine (the 'poll SQLite' driver)."""

from __future__ import annotations

from ingest.detection import DetectionConfig, load_config_from_text
from ingest.escalator import NullEscalator
from ingest.fall_machine import FallMachine, FallMachineRunner, State
from ingest.listener import persist
from hearth.packet import Vitals


def _v(node_id, fall, motion, ts, presence=True):
    return Vitals(
        node_id=node_id, presence=presence, fall=fall, motion=motion > 200,
        breathing_bpm=15.0, heart_bpm=72.0, rssi=-55, occupancy=1,
        motion_energy=float(motion), presence_score=0.9, timestamp_ms=ts,
    )


def test_runner_steps_new_readings_only(session_factory):
    # Two readings already persisted by the ingest listener path.
    persist(session_factory, _v(1, fall=False, motion=1000, ts=0))
    persist(session_factory, _v(1, fall=True, motion=9500, ts=1000))

    machine = FallMachine(DetectionConfig(), escalator=NullEscalator())
    runner = FallMachineRunner(machine, session_factory)

    assert runner.poll_once() == 2
    assert machine.state_of(1) is State.SUSPECTED
    # No new rows -> nothing to do, no double-processing.
    assert runner.poll_once() == 0
    assert machine.state_of(1) is State.SUSPECTED


def test_detection_config_overrides_from_yaml():
    cfg = load_config_from_text(
        """
        stillness_threshold: 150
        checkin_timeout_secs: 30
        rooms:
          1: "Living room"
        bedroom_nodes: [2]
        quiet_hours:
          enabled: true
          start_hour: 23
        """
    )
    assert cfg.stillness_threshold == 150
    assert cfg.checkin_timeout_secs == 30
    assert cfg.room_for(1) == "Living room"
    assert cfg.room_for(9) == "node-9"  # unmapped fallback
    assert cfg.is_bedroom(2)
    assert cfg.quiet_hours.enabled and cfg.quiet_hours.start_hour == 23


def test_detection_defaults_when_no_file():
    cfg = DetectionConfig()
    assert cfg.stillness_threshold == 200.0
    assert cfg.confirm_stillness_secs == 20
    assert cfg.checkin_timeout_secs == 45
    assert cfg.dedupe_window_secs == 300
    # Quiet-hours threshold boost: lower at night, default by day.
    assert cfg.stillness_threshold_at(3) == 200.0  # quiet hours disabled by default
