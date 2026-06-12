"""BAT-38 — golden-path end-to-end integration test.

One test that drives the WHOLE chain and fails loudly on a regression anywhere
in the pipeline:

    packet_sim frames
        -> ingest persist (real ``listener.persist`` into a temp SQLite)
        -> fall state machine (real ``FallMachine`` via the SQLite poller)
        -> escalation engine (the REAL ``PolicyEngine``, dry-run, temp SQLite)
        -> assert the escalation intent + first-step channels

This is the test that would have caught the BAT-31 ladder bug early: it asserts
the canonical ``fall.confirmed`` channel ladder, not just that *something* fired.
Pure insurance — fully in-process, deterministic (packet ``ts`` drives the fall
machine, an injected clock drives the escalation timers), no network, no Twilio.

Wiring note (why an in-process bridge, not a mock): the issue allows the real
``/escalate`` contract *or* the in-process call path. CI installs ``ingest`` and
``escalation`` as independent per-directory packages, so a cross-package test
reaches the escalation tree the same way the repo's other E2E tests reach the
simulator — a ``sys.path`` insert (see ``test_sim_e2e.py`` / ``test_fall_scenarios``
). The bridge below builds the exact JSON payload ``HttpEscalator`` would POST and
runs the same body as the ``/escalate`` endpoint against the *real* engine and a
*real* ``DryRunNotifier`` — nothing about the escalation logic is stubbed.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

from ingest import db as ingest_db
from ingest.checkin.base import StubCheckInProvider
from ingest.detection import DetectionConfig
from ingest.escalator import Escalation
from ingest.fall_machine import FallMachine, FallMachineRunner, State
from ingest.listener import persist

REPO_ROOT = Path(__file__).resolve().parents[3]

# Reach the escalation package in-process. CI installs each app independently,
# so escalation is not pip-installed in the ingest env; it imports cleanly from
# source because the engine path depends only on sqlalchemy + pyyaml (both ingest
# deps) — never on fastapi / python-multipart (which the FastAPI app would pull).
sys.path.insert(0, str(REPO_ROOT / "apps" / "escalation"))

from escalation import db as esc_db  # noqa: E402
from escalation.config import load_config_from_text  # noqa: E402
from escalation.engine import PolicyEngine  # noqa: E402
from escalation.notifier import PUSH, DryRunNotifier  # noqa: E402
from escalation.policy import default_registry  # noqa: E402


def _load_sim():
    """Load the shipping simulator by path (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location(
        "packet_sim", REPO_ROOT / "tools" / "packet_sim.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIM = _load_sim()

# Two contacts with deliberately different channel sets so the first-step
# assertion is sharp: only Dana receives the T+0 push; the ops-queue notice is
# channel-independent. Marcus is push-less, so a stray push would change the count.
CONTACTS_YAML = """
home_name: "Rosewood"
contacts:
  - name: "Dana"
    sms: "+15555550101"
    voice: "+15555550111"
    priority: 1
    channels: ["sms", "push"]
  - name: "Marcus"
    sms: "+15555550102"
    priority: 2
    channels: ["sms"]
"""

# Fixed daytime epoch for the fall machine's quiet-hours clock (quiet hours are
# disabled by default anyway; this just pins it). The escalation engine gets its
# own injected clock so its T+0/T+60/T+120 timers are deterministic.
FALL_CLOCK = 1_700_000_000.0
ESC_T0 = 1_000_000.0


class FakeClock:
    """A controllable wall clock for the escalation engine's persisted timers."""

    def __init__(self, start: float) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def set(self, t: float) -> None:
        self.t = t


class InProcessEscalator:
    """Bridges the ingest fall machine to the REAL escalation ``PolicyEngine``.

    Implements the ingest ``Escalator`` protocol. On a CONFIRMED_FALL it builds
    the same JSON payload :class:`ingest.escalator.HttpEscalator` would POST to
    ``/escalate`` and runs the same body the ``/escalate`` endpoint runs
    (``type`` defaults to ``fall.confirmed``; ``fall_incident_id`` becomes the
    cascade's ``source_incident_id``). No mock: the real engine + real dry-run
    notifier execute the cascade.
    """

    def __init__(self, engine: PolicyEngine) -> None:
        self.engine = engine
        self.calls: list[dict] = []
        self.incident_ids: list[str] = []

    def escalate(self, escalation: Escalation) -> bool:
        payload = escalation.payload()  # the real wire payload
        self.calls.append(payload)
        room = payload.get("room")
        if not room:  # mirrors the endpoint's 422 guard
            return False
        type_ = payload.get("type") or "fall.confirmed"
        incident, _created = self.engine.raise_incident(
            type_,
            room=room,
            home_name=payload.get("home_name"),
            source_incident_id=payload.get("fall_incident_id"),
            detail=payload.get("detail"),
            dedupe_key=payload.get("dedupe_key") or None,
        )
        self.incident_ids.append(incident.id)
        return True


class GoldenPath:
    """The assembled chain plus the artifacts each layer produced."""

    def __init__(self, machine, escalator, esc_engine, esc_clock, notifier,
                 ingest_sessions):
        self.machine = machine
        self.escalator = escalator
        self.esc_engine = esc_engine
        self.esc_clock = esc_clock
        self.notifier = notifier
        self.ingest_sessions = ingest_sessions

    def confirmed_transitions(self):
        return [t for t in self.machine.transitions
                if t.kind == ingest_db.CONFIRMED_FALL]

    def near_fall_transitions(self):
        return [t for t in self.machine.transitions
                if t.kind == ingest_db.NEAR_FALL]

    def escalation_incidents(self):
        return self.esc_engine.incidents()

    def reading_count(self) -> int:
        from sqlalchemy import func, select

        with self.ingest_sessions() as s:
            return s.execute(
                select(func.count()).select_from(ingest_db.Reading)
            ).scalar()


def run_golden_path(scenario: str, seconds: int, tmp_path, node_id: int = 1) -> GoldenPath:
    """Drive ``seconds`` of a simulator scenario through the entire pipeline."""
    # --- ingest side: real persist into a temp SQLite, real poller driver ---
    ingest_engine = ingest_db.make_engine(f"sqlite:///{tmp_path / 'ingest.db'}")
    ingest_db.init_db(ingest_engine)
    ingest_sessions = ingest_db.make_session_factory(ingest_engine)

    # --- escalation side: the REAL engine, dry-run notifier, temp SQLite ----
    esc_engine_db = esc_db.make_engine(f"sqlite:///{tmp_path / 'escalation.db'}")
    esc_db.init_db(esc_engine_db)
    esc_sessions = esc_db.make_session_factory(esc_engine_db)
    esc_clock = FakeClock(ESC_T0)
    notifier = DryRunNotifier()
    esc_engine = PolicyEngine(
        esc_sessions,
        load_config_from_text(CONTACTS_YAML),
        notifier=notifier,
        clock=esc_clock,
        registry=default_registry(),
    )
    escalator = InProcessEscalator(esc_engine)

    machine = FallMachine(
        DetectionConfig(),
        escalator=escalator,
        checkin=StubCheckInProvider(),
        session_factory=ingest_sessions,
        clock=lambda: FALL_CLOCK,
    )
    runner = FallMachineRunner(machine, ingest_sessions)

    # sim -> ingest persist -> poll -> fall machine, one simulated second at a time.
    for t, frame in enumerate(SIM.frames(scenario)):
        vitals = SIM.frame_to_vitals(node_id, t * 1000, frame)
        persist(ingest_sessions, vitals, received_at=float(t))
        runner.poll_once()
        if t >= seconds:
            break

    return GoldenPath(machine, escalator, esc_engine, esc_clock, notifier,
                      ingest_sessions)


def test_golden_path_fall_confirmed_escalates_once_with_first_step_channels(tmp_path):
    """fall_confirmed: exactly ONE CONFIRMED_FALL -> exactly ONE escalation, and
    the dry-run notifier records the expected T+0 first-step channels."""
    gp = run_golden_path("fall_confirmed", seconds=115, tmp_path=tmp_path)

    # The whole chain ran: readings were persisted by the ingest path.
    assert gp.reading_count() >= 100

    # Fall machine: exactly one CONFIRMED_FALL, machine ends in CONFIRMED_FALL.
    confirmed = gp.confirmed_transitions()
    assert len(confirmed) == 1
    assert gp.machine.state_of(1) is State.CONFIRMED_FALL

    # Hand-off: exactly one escalation raised, carrying the upstream incident id.
    assert len(gp.escalator.calls) == 1
    incident_id = confirmed[0].incident_id
    assert gp.escalator.calls[0]["fall_incident_id"] == incident_id

    # Escalation engine: exactly one fall.confirmed incident, still open, joined
    # back to the ingest incident via source_incident_id.
    incidents = gp.escalation_incidents()
    assert len(incidents) == 1
    incident = incidents[0]
    assert incident.type == "fall.confirmed"
    assert incident.priority == "P1"
    assert incident.status == esc_db.ACTIVE
    assert incident.room == "node-1"  # DetectionConfig default room label
    assert incident.source_incident_id == incident_id

    # First-step channels (the acceptance criterion). The fall.confirmed policy's
    # T+0 steps are push + ops_queue; SMS/voice are T+60 and must NOT have fired
    # yet (no process_due call). DryRunNotifier means no real Twilio.
    channels = [m.channel for m in gp.notifier.sent]
    assert set(channels) == {"push", "ops"}
    push_recipients = {m.to for m in gp.notifier.sent if m.channel == PUSH}
    assert push_recipients == {"+15555550101"}  # only Dana opted into push
    assert "sms" not in channels and "voice" not in channels

    # Insurance against a BAT-31-style ladder bug: advance the engine through the
    # canonical fall.confirmed ladder and assert the full channel order.
    gp.esc_clock.set(ESC_T0 + 60)
    assert gp.esc_engine.process_due() == 2  # T+60: sms + voice
    gp.esc_clock.set(ESC_T0 + 120)
    assert gp.esc_engine.process_due() == 1  # T+120: operator
    kinds = [e.kind for e in gp.esc_engine.timeline(incident.id)]
    assert kinds == [
        "incident_created",
        "push_sent",
        "ops_queued",
        "sms_sent",
        "voice_calls",
        "operator_escalation",
    ]


def test_golden_path_fall_recovered_does_not_escalate(tmp_path):
    """fall_recovered: ZERO escalations + exactly one near_fall (they got up)."""
    gp = run_golden_path("fall_recovered", seconds=60, tmp_path=tmp_path)

    assert gp.escalator.calls == []
    assert gp.escalation_incidents() == []
    assert gp.notifier.sent == []

    near = gp.near_fall_transitions()
    assert len(near) == 1
    assert near[0].reason == "got_up"
    assert gp.confirmed_transitions() == []
    assert gp.machine.state_of(1) is State.NORMAL


def test_golden_path_normal_does_nothing(tmp_path):
    """normal: a person moving around triggers nothing through the whole chain."""
    gp = run_golden_path("normal", seconds=60, tmp_path=tmp_path)

    assert gp.escalator.calls == []
    assert gp.escalation_incidents() == []
    assert gp.notifier.sent == []
    assert gp.machine.transitions == []
    assert gp.machine.state_of(1) is State.NORMAL
    # The chain still ran end-to-end (readings were persisted), it just stayed quiet.
    assert gp.reading_count() >= 50


def test_golden_path_quiet_morning_raises_no_fall(tmp_path):
    """quiet_morning carries no fall flag, so the fall pipeline correctly stays
    silent end-to-end.

    The wellness interpretation of quiet_morning (a prolonged-stillness concern)
    belongs to the BAT-24 absence-of-normal-life rules engine, which is a
    *separate* driver from the fall state machine and is NOT part of the
    sim -> ingest -> fall -> escalation path under test here. Per the issue, that
    wellness path is documented as out of scope for this golden-path test; BAT-24
    has its own coverage. What this asserts is the fall pipeline's correct
    non-reaction: still presence with no fall flag is never a fall.
    """
    gp = run_golden_path("quiet_morning", seconds=120, tmp_path=tmp_path)

    assert gp.escalator.calls == []
    assert gp.escalation_incidents() == []
    assert gp.notifier.sent == []
    assert gp.confirmed_transitions() == []
    assert gp.machine.state_of(1) is State.NORMAL
