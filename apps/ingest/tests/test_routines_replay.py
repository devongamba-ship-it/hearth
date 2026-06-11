"""Acceptance criterion #1 — the ``quiet_morning`` 24h replay.

    packet_sim --scenario quiet_morning  (24h replay)
    -> raises exactly one morning-check concern

Drives the *real* simulator scenario frames (``tools/packet_sim.py``) through the
rules engine over a simulated 24 hours with an advancing wall clock — the same
"use the simulator's own frame generator for deterministic timing" approach as
``test_fall_scenarios``. Also confirms a ``normal`` morning raises nothing.
"""

from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

from ingest.fall_machine import Frame
from ingest.routines import LATE_START, NullIncidentEmitter, RoutinesEngine, load_config_from_text

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load_sim():
    spec = importlib.util.spec_from_file_location(
        "packet_sim", REPO_ROOT / "tools" / "packet_sim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIM = _load_sim()


def _replay(scenario: str, hours: int, node_id: int = 1):
    """Feed ``hours`` of a scenario through the engine starting at local midnight.

    Observes one frame per simulated second and evaluates every 5 minutes — the
    production poll cadence.
    """
    engine = RoutinesEngine(
        load_config_from_text("zones: {1: kitchen}"), emitter=NullIncidentEmitter()
    )
    start = datetime(2026, 6, 10, 0, 0, 0).timestamp()
    gen = SIM.frames(scenario)
    for i in range(hours * 3600):
        frame = next(gen)
        now = start + i
        vitals = SIM.frame_to_vitals(node_id, i * 1000, frame)
        engine.observe(Frame.from_vitals(vitals), now)
        if i % 300 == 0:
            engine.evaluate(now)
    return engine.emitter


def test_quiet_morning_replay_raises_exactly_one_morning_concern():
    emitter = _replay("quiet_morning", hours=24)
    late_starts = [c for c in emitter.calls if c.type == LATE_START]
    assert len(late_starts) == 1, [c.type for c in emitter.calls]
    # Acceptance #2 holds end-to-end: every concern raised is the soft SMS tier.
    assert all(c.severity == "soft" for c in emitter.calls)


def test_normal_morning_raises_no_concern():
    # A normal, active morning: real motion in the kitchen all along, so neither
    # the morning check nor the extended-stillness rule fires.
    emitter = _replay("normal", hours=12)
    assert emitter.calls == []
