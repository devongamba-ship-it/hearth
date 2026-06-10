"""The escalation cascade engine.

Drives a CONFIRMED_FALL through the canonical ladder (design update v0.1):

    T+0s    alert      SMS/push to every circle contact + P1 event
    T+60s   voice      voice-call contacts in priority order (TTS, press 1)
    T+120s  operator   operator/monitoring escalation — v1 stub: P1 event only

Claim semantics: an ack **claims** the incident and **pauses (not cancels)** the
cascade. The claimer must confirm resolution within ``CLAIM_CONFIRM_WINDOW`` or
the cascade **resumes at the operator step**. All contacts get a "Claimed by …"
SMS. Resolution records an immutable disposition + reason code.

Timers are persisted as ``next_action_at`` deadlines in SQLite and re-driven by
:meth:`CascadeEngine.process_due`, so they survive a restart — the engine holds
no in-memory timer state.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable

from . import db
from .config import CircleConfig
from .notifier import PUSH, SMS, VOICE, DryRunNotifier, Notifier

# Ladder timing (seconds from incident creation).
VOICE_DELAY = 60
OPERATOR_DELAY = 120
# A claim must be confirmed (resolved) within this window or the cascade resumes.
CLAIM_CONFIRM_WINDOW = 15 * 60

# Allowed dispositions (canonical design update, point 5). Each requires a
# non-empty reason code, which feeds detection-threshold tuning.
DISPOSITIONS = (
    "recovered",
    "caregiver_responding",
    "advised_call_ems",
    "false_positive",
)

Clock = Callable[[], float]


class CascadeError(Exception):
    """Raised on an invalid cascade transition (e.g. bad disposition)."""


class CascadeEngine:
    def __init__(
        self,
        session_factory,
        config: CircleConfig,
        notifier: Notifier | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._notifier = notifier or DryRunNotifier()
        self._clock = clock or time.time

    @property
    def notifier(self) -> Notifier:
        return self._notifier

    def now(self) -> float:
        return self._clock()

    # -- audit log -----------------------------------------------------------

    def _log(self, session, incident_id: str, kind: str, detail: str, *, at: float,
             priority: str | None = None) -> None:
        session.add(
            db.CascadeEvent(
                incident_id=incident_id, at=at, kind=kind, priority=priority, detail=detail
            )
        )

    def timeline(self, incident_id: str) -> list[db.CascadeEvent]:
        """The immutable audit trail for an incident, in order."""
        from sqlalchemy import select

        with self._sessions() as session:
            rows = session.scalars(
                select(db.CascadeEvent)
                .where(db.CascadeEvent.incident_id == incident_id)
                .order_by(db.CascadeEvent.id)
            ).all()
            return list(rows)

    def get(self, incident_id: str) -> db.Incident | None:
        with self._sessions() as session:
            return session.get(db.Incident, incident_id)

    # -- cascade entry -------------------------------------------------------

    def escalate(self, room: str, home_name: str | None = None) -> str:
        """Start a cascade for a CONFIRMED_FALL. Runs the T+0 alert step now."""
        now = self.now()
        incident_id = uuid.uuid4().hex
        home = home_name or self._config.home_name
        with self._sessions() as session:
            incident = db.Incident(
                id=incident_id,
                home_name=home,
                room=room,
                created_at=now,
                status=db.ACTIVE,
                next_action=db.STEP_VOICE,
                next_action_at=now + VOICE_DELAY,
            )
            session.add(incident)
            self._log(session, incident_id, "incident_created", f"fall in {room}",
                      at=now, priority="P1")
            self._broadcast_alert(session, incident, now)
            session.commit()
        return incident_id

    def _broadcast_alert(self, session, incident: db.Incident, now: float) -> None:
        body = (
            f"Possible fall detected in {incident.room} at {incident.home_name}. "
            "Reply OK to acknowledge, HELP to escalate now."
        )
        for contact in self._config.contacts:
            for channel in contact.channels:
                if channel in (SMS, PUSH):
                    self._notifier.send(channel, contact.sms, body)
        self._log(session, incident.id, "alert_broadcast",
                  f"alerted {len(self._config.contacts)} contacts", at=now, priority="P1")

    # -- timer driver --------------------------------------------------------

    def process_due(self, now: float | None = None) -> int:
        """Fire every action whose persisted deadline has passed.

        Re-drives from SQLite, so it both advances live cascades and recovers
        pending ones after a restart. Returns the number of actions fired.
        """
        from sqlalchemy import or_, select

        now = self.now() if now is None else now
        fired = 0
        with self._sessions() as session:
            # Loop until nothing else is due (a single tick may cross several
            # deadlines if the process was down).
            while True:
                incident = session.scalars(
                    select(db.Incident)
                    .where(
                        or_(
                            (db.Incident.status == db.ACTIVE)
                            & (db.Incident.next_action_at.is_not(None))
                            & (db.Incident.next_action_at <= now),
                            (db.Incident.status == db.CLAIMED)
                            & (db.Incident.claim_deadline.is_not(None))
                            & (db.Incident.claim_deadline <= now),
                        )
                    )
                    .order_by(db.Incident.next_action_at)
                    .limit(1)
                ).first()
                if incident is None:
                    break
                if incident.status == db.CLAIMED:
                    self._resume_after_claim(session, incident, now)
                else:
                    self._fire_next(session, incident, now)
                fired += 1
            session.commit()
        return fired

    def _fire_next(self, session, incident: db.Incident, now: float) -> None:
        step = incident.next_action
        if step == db.STEP_VOICE:
            for contact in self._config.by_priority():
                self._notifier.send(
                    VOICE,
                    contact.voice,
                    f"Possible fall in {incident.room} at {incident.home_name}. "
                    "Press 1 to acknowledge.",
                )
            self._log(session, incident.id, "voice_calls",
                      "voice ladder in priority order", at=now, priority="P1")
            incident.next_action = db.STEP_OPERATOR
            incident.next_action_at = incident.created_at + OPERATOR_DELAY
        elif step == db.STEP_OPERATOR:
            self._operator_escalation(session, incident, now)
        else:  # pragma: no cover - defensive
            incident.next_action = None
            incident.next_action_at = None

    def _operator_escalation(self, session, incident: db.Incident, now: float) -> None:
        # v1 stub: the monitoring center does not exist yet; this is its plug
        # point. We only log a P1 operator_escalation event. No EMS, ever.
        self._log(session, incident.id, "operator_escalation",
                  "operator/monitoring step (v1 stub)", at=now, priority="P1")
        incident.next_action = None
        incident.next_action_at = None

    # -- claim / resume / resolve -------------------------------------------

    def claim(self, incident_id: str, by: str, now: float | None = None) -> db.Incident:
        """Claim an incident: pause the cascade and start the confirm window."""
        now = self.now() if now is None else now
        with self._sessions() as session:
            incident = session.get(db.Incident, incident_id)
            if incident is None:
                raise CascadeError(f"unknown incident {incident_id}")
            if incident.status == db.RESOLVED:
                raise CascadeError("incident already resolved")
            incident.status = db.CLAIMED
            incident.claimed_by = by
            incident.claimed_at = now
            incident.claim_deadline = now + CLAIM_CONFIRM_WINDOW
            # Pause (do not cancel) the ladder.
            incident.next_action = None
            incident.next_action_at = None
            self._log(session, incident_id, "claimed", f"claimed by {by}",
                      at=now, priority="P1")
            self._broadcast_claim(session, incident, by, now)
            session.commit()
            session.refresh(incident)
            session.expunge(incident)
            return incident

    def _broadcast_claim(self, session, incident: db.Incident, by: str, now: float) -> None:
        body = f"Possible fall in {incident.room}: claimed by {by}."
        for contact in self._config.contacts:
            self._notifier.send(SMS, contact.sms, body)

    def _resume_after_claim(self, session, incident: db.Incident, now: float) -> None:
        """An unconfirmed claim expired — resume the cascade at the operator step."""
        self._log(session, incident.id, "claim_expired_resumed",
                  f"claim by {incident.claimed_by} unconfirmed after "
                  f"{CLAIM_CONFIRM_WINDOW // 60}m", at=now, priority="P1")
        incident.status = db.ACTIVE
        incident.claim_deadline = None
        self._operator_escalation(session, incident, now)

    def resolve(self, incident_id: str, disposition: str, reason_code: str,
                by: str | None = None, now: float | None = None) -> db.Incident:
        """Confirm resolution with an immutable disposition + reason code."""
        now = self.now() if now is None else now
        if disposition not in DISPOSITIONS:
            raise CascadeError(
                f"invalid disposition {disposition!r}; expected one of {DISPOSITIONS}"
            )
        if not reason_code or not reason_code.strip():
            raise CascadeError("disposition requires a non-empty reason code")
        with self._sessions() as session:
            incident = session.get(db.Incident, incident_id)
            if incident is None:
                raise CascadeError(f"unknown incident {incident_id}")
            if incident.status == db.RESOLVED:
                raise CascadeError("incident already resolved")
            incident.status = db.RESOLVED
            incident.disposition = disposition
            incident.disposition_reason = reason_code
            incident.resolved_at = now
            incident.next_action = None
            incident.next_action_at = None
            incident.claim_deadline = None
            self._log(session, incident_id, "resolved",
                      f"{disposition} ({reason_code})"
                      + (f" by {by}" if by else ""), at=now, priority="P1")
            session.commit()
            session.refresh(incident)
            session.expunge(incident)
            return incident

    def pending(self) -> list[db.Incident]:
        """Incidents that are not yet resolved (recovered on startup)."""
        from sqlalchemy import select

        with self._sessions() as session:
            rows = session.scalars(
                select(db.Incident).where(db.Incident.status != db.RESOLVED)
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)
