"""Fall confirmation state machine (BAT-22) — the core IP.

Turns raw ``fall_flag`` blips from a RuView node into trustworthy
CONFIRMED_FALL events. False positives are the product's biggest risk, so a
fall flag never alerts on its own: it must survive a stillness check, a
post-burst confirmation, and an (eventually real) resident check-in.

Canonical machine (design update v0.1, per the BAT-22 Linear thread):

    NORMAL --fall_flag--> SUSPECTED --stillness+presence--> PROBABLE
        ^                    |  ^                                |
        | near_fall          |  | (sustained normal motion)      | (immediate)
        |  (got up)          |  |                                v
        +--------------------+  +------------------------ CHECK_IN_ACTIVE
                                                                 |
                              response -> NORMAL (recovered)     | 45s, no answer
                                                                 v
                                                          CONFIRMED_FALL
                                                          (POST /escalate)

Explicit suppressions (logged ``suppressed``, never alerted) filter known
non-falls *before* SUSPECTED. Two of the four named in the design — sit/lie onto
furniture (floor-plane terminus) and pet-mass profile — need richer features
than the Tier-2 vitals packet carries (it has no floor-plane geometry or mass
class), so only the packet-expressible ones run in v1; see :meth:`_suppression`.

The machine is pure and reading-driven: feed it one :class:`Frame` per packet
via :meth:`step`. All elapsed-time logic uses the packet's own ``ts``
(ms-since-boot), so it is deterministic in tests; only the quiet-hours
threshold consults a wall clock. A node that goes *silent* does not advance —
node-offline detection is a separate concern.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from . import db
from .checkin import CheckInProvider, CheckInResult, StubCheckInProvider
from .detection import DetectionConfig
from .escalator import Escalation, Escalator, NullEscalator

log = logging.getLogger("hearth.ingest.fall_machine")

Clock = Callable[[], float]

# Canonical feature-snapshot keys persisted on every transition (BAT-41). The
# long game is fall *prediction* from longitudinal data, so every decision the
# machine makes must persist the full motion/breathing/confidence/window state
# at decision time — not just the state name — or that event is useless as
# training data later. :meth:`Frame.features` is the single producer; this tuple
# is the contract downstream (and the completeness test) checks against.
FEATURE_KEYS = (
    "ts", "presence", "motion", "fall_flag",
    "breathing_bpm", "hr_bpm", "confidence", "occupancy",
)


class State(str, Enum):
    NORMAL = "NORMAL"
    SUSPECTED = "SUSPECTED"
    PROBABLE = "PROBABLE"
    CHECK_IN_ACTIVE = "CHECK_IN_ACTIVE"
    CONFIRMED_FALL = "CONFIRMED_FALL"


@dataclass(frozen=True)
class Frame:
    """One reading as the machine sees it (subset of the vitals packet)."""

    node_id: int
    ts: int  # packet timestamp, ms since the node booted
    presence: bool
    motion: float  # analog motion energy
    fall_flag: bool
    breathing_bpm: float = 0.0
    hr_bpm: float = 0.0
    confidence: float = 0.0
    occupancy: int = 0

    @classmethod
    def from_vitals(cls, v) -> Frame:
        return cls(
            node_id=v.node_id, ts=v.timestamp_ms, presence=v.presence,
            motion=v.motion_energy, fall_flag=v.fall, breathing_bpm=v.breathing_bpm,
            hr_bpm=v.heart_bpm, confidence=v.presence_score, occupancy=v.occupancy,
        )

    @classmethod
    def from_reading(cls, r) -> Frame:
        return cls(
            node_id=r.node_id, ts=r.ts, presence=r.presence, motion=r.motion,
            fall_flag=r.fall_flag, breathing_bpm=r.breathing_bpm, hr_bpm=r.hr_bpm,
            confidence=r.confidence, occupancy=r.occupancy,
        )

    def features(self) -> dict:
        """The full decision-time snapshot persisted on every transition.

        Keyed by :data:`FEATURE_KEYS` so the persisted shape and the downstream
        contract can never drift apart (BAT-41 feature-snapshot completeness).
        """
        return {key: getattr(self, key) for key in FEATURE_KEYS}


@dataclass
class NodeState:
    """Per-node working state. One incident's worth of counters."""

    state: State = State.NORMAL
    incident_id: str | None = None
    suspected_since: int = 0  # reading ts when SUSPECTED began
    stillness_secs: int = 0  # cumulative stillness within SUSPECTED
    motion_run: int = 0  # consecutive normal-motion seconds (recovery)
    checkin_since: int = 0  # reading ts when CHECK_IN_ACTIVE began
    last_confirmed: int | None = None  # reading ts of last escalation (dedupe)
    last_ts: int = 0


@dataclass(frozen=True)
class Transition:
    """An in-memory record of one state change (mirrors db.FallTransition)."""

    incident_id: str
    node_id: int
    reading_ts: int
    at: float
    from_state: State
    to_state: State
    kind: str
    reason: str | None
    features: dict


class FallMachine:
    """Drives one or more nodes through the fall-confirmation states.

    Dependencies are injected (the pattern the escalation engine uses): an
    :class:`Escalator` for the CONFIRMED_FALL hand-off, a
    :class:`CheckInProvider` for the resident prompt, a wall ``clock`` (only for
    quiet-hours), and an optional ``session_factory`` to persist transitions.
    Transitions are always appended to :attr:`transitions` in memory too.
    """

    def __init__(
        self,
        config: DetectionConfig | None = None,
        *,
        escalator: Escalator | None = None,
        checkin: CheckInProvider | None = None,
        session_factory=None,
        clock: Clock | None = None,
    ) -> None:
        self.config = config or DetectionConfig()
        self.escalator = escalator or NullEscalator()
        self.checkin = checkin or StubCheckInProvider()
        self._sessions = session_factory
        self._clock = clock or time.time
        self.nodes: dict[int, NodeState] = {}
        self.transitions: list[Transition] = []

    # -- public driver -------------------------------------------------------

    def step(self, frame: Frame) -> State:
        """Advance ``frame.node_id`` by one reading. Returns its new state."""
        ns = self.nodes.setdefault(frame.node_id, NodeState())
        ns.last_ts = frame.ts
        handler = {
            State.NORMAL: self._on_normal,
            State.SUSPECTED: self._on_suspected,
            State.PROBABLE: self._on_probable,
            State.CHECK_IN_ACTIVE: self._on_check_in,
            State.CONFIRMED_FALL: self._on_confirmed,
        }[ns.state]
        handler(ns, frame)
        return ns.state

    def step_vitals(self, vitals) -> State:
        return self.step(Frame.from_vitals(vitals))

    def step_reading(self, reading) -> State:
        return self.step(Frame.from_reading(reading))

    def state_of(self, node_id: int) -> State:
        return self.nodes.get(node_id, NodeState()).state

    # -- thresholds ----------------------------------------------------------

    def _stillness_threshold(self) -> float:
        return self.config.threshold_for_clock(self._clock())

    def _is_still(self, frame: Frame) -> bool:
        return frame.motion < self._stillness_threshold()

    # -- per-state handlers --------------------------------------------------

    def _on_normal(self, ns: NodeState, frame: Frame) -> None:
        if not frame.fall_flag:
            return
        reason = self._suppression(frame)
        if reason is not None:
            # Mint a throwaway incident id so the suppressed event is groupable.
            incident_id = uuid.uuid4().hex
            self._record(incident_id, frame, State.NORMAL, State.NORMAL,
                         db.SUPPRESSED, reason)
            return
        ns.incident_id = uuid.uuid4().hex
        ns.suspected_since = frame.ts
        ns.stillness_secs = 1 if self._is_still(frame) else 0
        ns.motion_run = 0
        self._enter(ns, frame, State.SUSPECTED, db.TRANSITION)

    def _on_suspected(self, ns: NodeState, frame: Frame) -> None:
        # The person walked out of the room — not a fall, dismiss it.
        if not frame.presence:
            self._dismiss(ns, frame, reason="presence_lost")
            return

        if self._is_still(frame):
            ns.stillness_secs += 1
            ns.motion_run = 0
        else:
            ns.motion_run += 1
            # Sustained normal motion: they got up. Dismiss as a near-fall.
            if ns.motion_run >= self.config.recovery_motion_secs:
                self._dismiss(ns, frame, reason="got_up")
                return

        # Enough post-burst stillness, presence intact -> probable fall.
        if ns.stillness_secs >= self.config.confirm_stillness_secs:
            self._enter(ns, frame, State.PROBABLE, db.TRANSITION)
            self._on_probable(ns, frame)  # PROBABLE auto-advances immediately
            return

        # Window elapsed without resolving -> dismiss (still a near-fall worth
        # logging: the burst was real but never confirmed).
        if frame.ts - ns.suspected_since >= self.config.suspected_window_secs * 1000:
            self._dismiss(ns, frame, reason="suspected_window_elapsed")

    def _on_probable(self, ns: NodeState, frame: Frame) -> None:
        # PROBABLE is transient: issue the check-in and advance the same tick.
        self.checkin.start(frame.node_id, frame.ts)
        ns.checkin_since = frame.ts
        self._enter(ns, frame, State.CHECK_IN_ACTIVE, db.TRANSITION)

    def _on_check_in(self, ns: NodeState, frame: Frame) -> None:
        result = self.checkin.poll(frame.node_id, frame.ts)
        if result is CheckInResult.OK:
            # Resident answered "I'm fine" — recovered, never alert.
            self.checkin.cancel(frame.node_id)
            self._dismiss(ns, frame, reason="checkin_answered", kind=db.NEAR_FALL)
            return
        if result is CheckInResult.HELP:
            # Active distress call — escalate immediately, don't wait out the
            # window (BAT-30 fast-path; audit the reason as help_keyword).
            self.checkin.cancel(frame.node_id)
            self._confirm(ns, frame, reason="help_keyword")
            return
        if frame.ts - ns.checkin_since >= self.config.checkin_timeout_secs * 1000:
            self.checkin.cancel(frame.node_id)
            self._confirm(ns, frame, reason="checkin_timeout")

    def _on_confirmed(self, ns: NodeState, frame: Frame) -> None:
        # Already escalated. Stay put until the person is clearly active again,
        # then reset to NORMAL so a later, distinct fall can be detected. The
        # dedupe window guards against a re-escalation in the meantime.
        if not self._is_still(frame):
            ns.motion_run += 1
            if ns.motion_run >= self.config.recovery_motion_secs:
                self._enter(ns, frame, State.NORMAL, db.TRANSITION)
                ns.incident_id = None
        else:
            ns.motion_run = 0

    # -- transition helpers --------------------------------------------------

    def _enter(self, ns: NodeState, frame: Frame, to: State, kind: str,
               reason: str | None = None) -> None:
        frm = ns.state
        ns.state = to
        self._record(ns.incident_id or "", frame, frm, to, kind, reason)

    def _dismiss(self, ns: NodeState, frame: Frame, *, reason: str,
                 kind: str = db.NEAR_FALL) -> None:
        """Return a node to NORMAL, logging a near_fall (the IP's quiet win)."""
        self._enter(ns, frame, State.NORMAL, kind, reason)
        ns.incident_id = None
        ns.stillness_secs = 0
        ns.motion_run = 0

    def _confirm(self, ns: NodeState, frame: Frame, *, reason: str | None = None) -> None:
        self._enter(ns, frame, State.CONFIRMED_FALL, db.CONFIRMED_FALL, reason)
        ns.motion_run = 0
        if self._deduped(ns, frame):
            log.info("CONFIRMED_FALL node=%s deduped (within %ds window)",
                     frame.node_id, self.config.dedupe_window_secs,
                     extra={"event": "fall_deduped", "node_id": frame.node_id,
                            "incident_id": ns.incident_id or None})
            return
        ns.last_confirmed = frame.ts
        room = self.config.room_for(frame.node_id)
        ok = self.escalator.escalate(Escalation(
            incident_id=ns.incident_id or "",
            node_id=frame.node_id, room=room,
            home_name=self.config.home_name, at_ms=frame.ts,
        ))
        if not ok:
            log.error("escalation hand-off failed for node=%s", frame.node_id,
                      extra={"event": "escalation_handoff_failed",
                             "node_id": frame.node_id,
                             "incident_id": ns.incident_id or None, "room": room})

    def _deduped(self, ns: NodeState, frame: Frame) -> bool:
        if ns.last_confirmed is None:
            return False
        return frame.ts - ns.last_confirmed < self.config.dedupe_window_secs * 1000

    # -- suppressions --------------------------------------------------------

    def _suppression(self, frame: Frame) -> str | None:
        """Reason code if this fall signature is a known non-fall, else None.

        Only the suppressions the Tier-2 packet can actually express are live:
          * ``no_presence`` — a fall flag with nobody home is a dropped object,
            a pet, or sensor noise, never a person falling.
          * ``in_bed_sleep_window`` — stillness on a bedroom node during the
            configured quiet hours is someone asleep, not collapsed.
        Floor-plane "sit/lie onto furniture" and "pet-mass profile" need
        geometry/mass features the packet does not carry; they are config hooks
        awaiting a richer node feed (flagged for a follow-up issue).
        """
        if not frame.presence:
            return "no_presence"
        qh = self.config.quiet_hours
        if (qh.in_bed_suppression and qh.contains(self._wall_hour())
                and self.config.is_bedroom(frame.node_id) and self._is_still(frame)):
            return "in_bed_sleep_window"
        return None

    def _wall_hour(self) -> int:
        return self.config.hour_at(self._clock())

    # -- persistence ---------------------------------------------------------

    def _record(self, incident_id: str, frame: Frame, frm: State, to: State,
                kind: str, reason: str | None) -> None:
        at = self._clock()
        self.transitions.append(Transition(
            incident_id=incident_id, node_id=frame.node_id, reading_ts=frame.ts,
            at=at, from_state=frm, to_state=to, kind=kind, reason=reason,
            features=frame.features(),
        ))
        log.info("node=%s %s -> %s (%s%s)", frame.node_id, frm.value, to.value,
                 kind, f": {reason}" if reason else "",
                 extra={"event": "fall_transition", "node_id": frame.node_id,
                        "incident_id": incident_id or None, "from_state": frm.value,
                        "to_state": to.value, "kind": kind})
        if self._sessions is None:
            return
        with self._sessions() as session:
            session.add(db.FallTransition(
                incident_id=incident_id, node_id=frame.node_id, reading_ts=frame.ts,
                at=at, from_state=frm.value, to_state=to.value, kind=kind,
                reason=reason, features=json.dumps(frame.features()),
            ))
            session.commit()

    # -- queries (BAT-25 replays these) -------------------------------------

    def incident(self, incident_id: str) -> list[db.FallTransition]:
        """Every persisted transition for one incident, in order."""
        from sqlalchemy import select

        if self._sessions is None:
            return []
        with self._sessions() as session:
            rows = session.scalars(
                select(db.FallTransition)
                .where(db.FallTransition.incident_id == incident_id)
                .order_by(db.FallTransition.id)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)


@dataclass
class FallMachineRunner:
    """Thin SQLite poller: feed new `readings` rows into a :class:`FallMachine`.

    Keeps a high-water mark on the readings surrogate id and steps the machine
    once per new row. This is the "poll SQLite" driver the issue allows; run it
    standalone with ``python -m ingest.fall_machine``.
    """

    machine: FallMachine
    session_factory: object
    last_id: int = 0
    _seen: int = field(default=0, init=False)

    def poll_once(self) -> int:
        """Step every readings row newer than the high-water mark. Returns count."""
        from sqlalchemy import select

        from .db import Reading

        n = 0
        with self.session_factory() as session:
            rows = session.scalars(
                select(Reading).where(Reading.id > self.last_id).order_by(Reading.id)
            ).all()
            for r in rows:
                self.machine.step_reading(r)
                self.last_id = r.id
                n += 1
        self._seen += n
        return n


def main() -> None:  # pragma: no cover - operational entrypoint
    import os
    import time as _time

    from .checkin import build_checkin_provider
    from .db import init_db, make_engine, make_session_factory
    from .detection import load_config
    from .escalator import HttpEscalator
    from .logconfig import configure_logging

    configure_logging("ingest")
    engine = make_engine()
    init_db(engine)
    sessions = make_session_factory(engine)
    config = load_config()
    machine = FallMachine(
        config, escalator=HttpEscalator(), session_factory=sessions,
        checkin=build_checkin_provider(config),
    )
    runner = FallMachineRunner(machine, sessions)
    interval = float(os.environ.get("HEARTH_FALL_POLL_SECS", "1.0"))
    log.info("fall machine poller up (every %.1fs)", interval)
    while True:
        runner.poll_once()
        _time.sleep(interval)


if __name__ == "__main__":  # pragma: no cover
    main()
