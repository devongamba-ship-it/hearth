"""Acceptance criterion #1 — the packet_sim scenarios drive the right outcome.

Feeds the *real* simulator scenario frames (``tools/packet_sim.py``) through the
fall machine and asserts the headline behaviour:

    fall_confirmed  -> exactly 1 escalation, fired after the 45s check-in window
    fall_recovered  -> 0 escalations + exactly 1 near_fall event
    normal          -> nothing

Using the simulator's own frame generator (rather than a UDP subprocess) keeps
the timing deterministic while still proving the machine agrees with the shipping
scenario definitions.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from ingest import db
from ingest.checkin import StubCheckInProvider
from ingest.detection import DetectionConfig
from ingest.escalator import NullEscalator
from ingest.fall_machine import FallMachine, Frame, State

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_sim():
    spec = importlib.util.spec_from_file_location(
        "packet_sim", REPO_ROOT / "tools" / "packet_sim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIM = _load_sim()


def run_scenario(scenario: str, seconds: int, node_id: int = 1) -> FallMachine:
    """Drive ``seconds`` of a simulator scenario through a fresh machine."""
    machine = FallMachine(
        DetectionConfig(), escalator=NullEscalator(), checkin=StubCheckInProvider(),
        clock=lambda: 1_700_000_000.0,  # fixed daytime clock (no quiet hours)
    )
    for t, f in enumerate(SIM.frames(scenario)):
        vitals = SIM.frame_to_vitals(node_id, t * 1000, f)
        machine.step(Frame.from_vitals(vitals))
        if t >= seconds:
            break
    return machine


def test_fall_confirmed_escalates_exactly_once_after_window():
    m = run_scenario("fall_confirmed", seconds=110)
    assert len(m.escalator.calls) == 1
    assert m.state_of(1) is State.CONFIRMED_FALL

    confirm = next(t for t in m.transitions if t.kind == db.CONFIRMED_FALL)
    checkin = next(
        t for t in m.transitions if t.to_state is State.CHECK_IN_ACTIVE
    )
    # The escalation lands a full 45s after the check-in was issued.
    assert confirm.reading_ts - checkin.reading_ts == 45 * 1000


def test_fall_recovered_does_not_escalate_and_logs_one_near_fall():
    m = run_scenario("fall_recovered", seconds=60)
    assert m.escalator.calls == []
    near = [t for t in m.transitions if t.kind == db.NEAR_FALL]
    assert len(near) == 1
    assert near[0].reason == "got_up"
    assert m.state_of(1) is State.NORMAL


def test_normal_scenario_does_nothing():
    m = run_scenario("normal", seconds=60)
    assert m.escalator.calls == []
    assert m.transitions == []
    assert m.state_of(1) is State.NORMAL


def test_empty_scenario_does_nothing():
    m = run_scenario("empty", seconds=60)
    assert m.escalator.calls == []
    assert m.transitions == []
