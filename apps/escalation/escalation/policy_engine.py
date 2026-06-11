"""The per-type escalation **policy engine** (BAT-31).

Executes the incident :mod:`~escalation.policy` registry. Where BAT-23 hard-coded
the fall ladder, this engine runs *whatever* the policy for an incident's type
says: it schedules each step's persisted deadline, fires pluggable channel
senders (push/SMS/voice/ops-queue/digest, dry-run by default), and applies the
per-type claim, snooze, auto-resolve, dedupe, and quiet-hours behavior. Adding an
incident type is a registry edit — never a change here.

Lifecycle (every type): ``OPEN → (claim) CLAIMED → (disposition) RESOLVED``;
pause-mode claims resume to OPEN if unconfirmed within ``confirm_window``; types
with ``auto_resolve`` go ``OPEN → AUTO_RESOLVED`` when their trigger clears. Every
transition is an immutable audit-log row (actor, ts, channel). All timers are
persisted ``next_action_at`` deadlines in SQLite and re-driven by
:meth:`process_due`, so they survive a restart for *all* open incidents.

The legacy fall surface (``escalate``/``claim``/``resolve``/``timeline`` …) is
preserved so BAT-23/BAT-25 callers and tests keep working: the fall cascade is
now simply the ``fall.confirmed`` policy instance.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable
from datetime import datetime, timedelta

from . import db
from .config import CircleConfig
from .notifier import PUSH, SMS, VOICE, DryRunNotifier, Notifier
from .policy import (
    CLAIM_PAUSE,
    QUIET_DEFER,
    Policy,
    PolicyError,
    Registry,
    default_registry,
)

# Back-compat constants (the fall.confirmed policy's canonical timings). Existing
# BAT-23 tests import these; they mirror the embedded registry.
VOICE_DELAY = 60
OPERATOR_DELAY = 120
CLAIM_CONFIRM_WINDOW = 15 * 60

# Allowed dispositions (canonical design update, point 5). Each requires a
# non-empty reason code, which feeds detection-threshold tuning. No EMS, ever.
DISPOSITIONS = (
    "recovered",
    "caregiver_responding",
    "advised_call_ems",
    "false_positive",
)

# Non-paging channels used by digest/ops actions — recorded, never a page.
CH_DIGEST = "digest"
CH_OPS = "ops"

Clock = Callable[[], float]


class CascadeError(Exception):
    """Raised on an invalid transition (bad disposition, unknown incident, …)."""


class PolicyEngine:
    """Runs the incident registry over a SQLite-backed incident table."""

    def __init__(
        self,
        session_factory,
        config: CircleConfig,
        notifier: Notifier | None = None,
        clock: Clock | None = None,
        registry: Registry | None = None,
    ) -> None:
        self._sessions = session_factory
        self._config = config
        self._notifier = notifier or DryRunNotifier()
        self._clock = clock or time.time
        self._registry = registry or default_registry()

    @property
    def notifier(self) -> Notifier:
        return self._notifier

    @property
    def registry(self) -> Registry:
        return self._registry

    def now(self) -> float:
        return self._clock()

    def policy_for(self, type_: str) -> Policy:
        try:
            return self._registry.get(type_)
        except PolicyError as exc:
            raise CascadeError(str(exc)) from exc

    # -- audit log -----------------------------------------------------------

    def _log(self, session, incident_id: str, kind: str, detail: str, *, at: float,
             priority: str | None = None, actor: str | None = None,
             channel: str | None = None) -> None:
        session.add(
            db.CascadeEvent(
                incident_id=incident_id, at=at, kind=kind, priority=priority,
                detail=detail, actor=actor, channel=channel,
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

    # -- raising incidents ---------------------------------------------------

    def raise_incident(
        self,
        type_: str,
        room: str,
        *,
        home_name: str | None = None,
        source_incident_id: str | None = None,
        detail: str | None = None,
        dedupe_key: str | None = None,
        now: float | None = None,
    ) -> tuple[db.Incident, bool]:
        """Open an incident of ``type_`` and run its T+0 steps.

        Idempotent on the dedupe key: if a matching incident is already open
        (per the policy's dedupe rule) this is a no-op that returns it. Returns
        ``(incident, created)`` — ``created=False`` means it was deduped.
        """
        policy = self.policy_for(type_)
        now = self.now() if now is None else now
        home = home_name or self._config.home_name
        key = dedupe_key or f"{type_}:{home}"

        with self._sessions() as session:
            existing = self._find_dedupe(session, policy, key, now)
            if existing is not None:
                self._log(session, existing.id, "deduped",
                          f"repeat {type_} collapsed onto open incident",
                          at=now, priority=policy.priority, actor="system")
                session.commit()
                session.refresh(existing)
                session.expunge(existing)
                return existing, False

            incident = db.Incident(
                id=uuid.uuid4().hex,
                home_name=home,
                room=room,
                created_at=now,
                type=type_,
                priority=policy.priority,
                dedupe_key=key,
                source_incident_id=source_incident_id,
                status=db.ACTIVE,
            )
            session.add(incident)
            created_detail = detail or (
                f"fall in {room}" if type_ == "fall.confirmed" else f"{type_} in {room}"
            )
            self._log(session, incident.id, "incident_created", created_detail,
                      at=now, priority=policy.priority, actor="system")

            # Run every immediate (at_secs == 0) step now, then schedule the rest.
            idx = 0
            while idx < len(policy.steps) and policy.steps[idx].at_secs == 0:
                self._run_step(session, incident, policy, idx, now)
                idx += 1
            self._schedule(incident, policy, idx)
            session.commit()
            session.refresh(incident)
            session.expunge(incident)
            return incident, True

    def _find_dedupe(self, session, policy: Policy, key: str, now: float):
        from sqlalchemy import select

        if not (policy.one_open or policy.dedupe_window_secs > 0):
            return None
        candidates = session.scalars(
            select(db.Incident)
            .where(db.Incident.dedupe_key == key)
            .order_by(db.Incident.created_at.desc())
        ).all()
        for incident in candidates:
            # Only ever collapse onto a still-open incident. Deduping onto a
            # resolved one would silently drop a genuinely new alert (a P1 fall
            # after the prior was handled must page again — fail safe).
            if incident.status in db.TERMINAL:
                continue
            if policy.one_open:
                return incident
            if (policy.dedupe_window_secs > 0
                    and now - incident.created_at <= policy.dedupe_window_secs):
                return incident
        return None

    # -- legacy fall surface (BAT-23/BAT-25 back-compat) ---------------------

    def escalate(self, room: str, home_name: str | None = None,
                 source_incident_id: str | None = None) -> str:
        """Start a ``fall.confirmed`` cascade. Returns the incident id."""
        incident, _ = self.raise_incident(
            "fall.confirmed", room, home_name=home_name,
            source_incident_id=source_incident_id,
        )
        return incident.id

    # -- step scheduling + execution ----------------------------------------

    def _schedule(self, incident: db.Incident, policy: Policy, idx: int) -> None:
        """Point the incident's persisted timer at step ``idx`` (or clear it)."""
        if idx < len(policy.steps):
            step = policy.steps[idx]
            incident.next_step_index = idx
            incident.next_action = step.action
            incident.next_action_at = incident.created_at + step.at_secs
        else:
            incident.next_step_index = None
            incident.next_action = None
            incident.next_action_at = None

    def _run_step(self, session, incident: db.Incident, policy: Policy,
                  idx: int, now: float) -> None:
        step = policy.steps[idx]
        sender = getattr(self, f"_send_{step.action}", None)
        if sender is None:
            raise CascadeError(
                f"policy {policy.type!r} references unknown action {step.action!r}"
            )
        sender(session, incident, policy, step, now)

    def process_due(self, now: float | None = None) -> int:
        """Fire every action whose persisted deadline has passed, across ALL
        open incidents. Re-drives from SQLite, so it both advances live ladders
        and recovers pending ones after a restart. Returns actions fired."""
        from sqlalchemy import or_, select

        now = self.now() if now is None else now
        fired = 0
        with self._sessions() as session:
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
                    self._advance(session, incident, now)
                fired += 1
            session.commit()
        return fired

    def _advance(self, session, incident: db.Incident, now: float) -> None:
        """Fire the incident's pending step, applying quiet-hours deferral."""
        policy = self.policy_for(incident.type)
        idx = incident.next_step_index
        if idx is None or idx >= len(policy.steps):
            incident.next_action = None
            incident.next_action_at = None
            return
        step = policy.steps[idx]
        if self._defer_for_quiet_hours(session, incident, policy, step, now):
            return
        self._run_step(session, incident, policy, idx, now)
        self._schedule(incident, policy, idx + 1)

    def _defer_for_quiet_hours(self, session, incident: db.Incident, policy: Policy,
                               step, now: float) -> bool:
        """Defer a delayed paging step to the quiet window's end. Returns True if
        deferred (the caller should not fire the step this tick)."""
        quiet = self._registry.quiet_hours
        if quiet is None or policy.quiet_hours != QUIET_DEFER:
            return False
        if step.at_secs == 0:  # an immediate step pages even in quiet hours
            return False
        dt = datetime.fromtimestamp(now)
        if not quiet.contains_minute(dt.hour * 60 + dt.minute):
            return False
        resume_at = self._quiet_window_end(now, quiet)
        incident.next_action_at = resume_at
        self._log(session, incident.id, "deferred_quiet_hours",
                  f"{step.action} held until quiet hours end", at=now,
                  priority=policy.step_priority(step), actor="system")
        return True

    @staticmethod
    def _quiet_window_end(now: float, quiet) -> float:
        dt = datetime.fromtimestamp(now)
        end = dt.replace(hour=quiet.end[0], minute=quiet.end[1], second=0, microsecond=0)
        if end.timestamp() <= now:  # the window's end is tomorrow morning
            end = end + timedelta(days=1)
        return end.timestamp()

    # -- channel senders (pluggable; dry-run by default) --------------------

    def _send_broadcast(self, session, incident, policy, step, now) -> None:
        """T+0 fall alert: SMS + push to every circle contact's chosen channels."""
        body = (
            f"Possible fall detected in {incident.room} at {incident.home_name}. "
            "Reply OK to acknowledge, HELP to escalate now."
        )
        for contact in self._config.contacts:
            for channel in contact.channels:
                if channel in (SMS, PUSH):
                    self._notifier.send(channel, contact.sms, body)
        self._log(session, incident.id, "alert_broadcast",
                  f"alerted {len(self._config.contacts)} contacts", at=now,
                  priority=policy.step_priority(step), actor="system", channel="broadcast")

    def _send_sms(self, session, incident, policy, step, now) -> None:
        body = self._concern_body(incident)
        for contact in self._config.contacts:
            self._notifier.send(SMS, contact.sms, body)
        self._log(session, incident.id, "sms_sent", "SMS to circle", at=now,
                  priority=policy.step_priority(step), actor="system", channel=SMS)

    def _send_push(self, session, incident, policy, step, now) -> None:
        body = self._concern_body(incident)
        for contact in self._config.contacts:
            if PUSH in contact.channels:
                self._notifier.send(PUSH, contact.sms, body)
        self._log(session, incident.id, "push_sent", "push to circle", at=now,
                  priority=policy.step_priority(step), actor="system", channel=PUSH)

    def _send_voice(self, session, incident, policy, step, now) -> None:
        for contact in self._config.by_priority():
            self._notifier.send(
                VOICE, contact.voice,
                f"Possible fall in {incident.room} at {incident.home_name}. "
                "Press 1 to acknowledge.",
            )
        self._log(session, incident.id, "voice_calls",
                  "voice ladder in priority order", at=now,
                  priority=policy.step_priority(step), actor="system", channel=VOICE)

    def _send_operator(self, session, incident, policy, step, now) -> None:
        # v1 stub: the monitoring center does not exist yet; this is its plug
        # point. We only log the operator step. No EMS, ever.
        self._log(session, incident.id, "operator_escalation",
                  "operator/monitoring step (v1 stub)", at=now,
                  priority=policy.step_priority(step), actor="system", channel=CH_OPS)

    def _send_ops_queue(self, session, incident, policy, step, now) -> None:
        # Courtesy/ops-queue notice — recorded, never a page.
        self._notifier.send(CH_OPS, "ops-queue", self._concern_body(incident))
        self._log(session, incident.id, "ops_queued", "queued for ops review", at=now,
                  priority=policy.step_priority(step), actor="system", channel=CH_OPS)

    def _send_digest(self, session, incident, policy, step, now) -> None:
        # Digest-only types never page: the entry rides the daily digest channel.
        self._notifier.send(CH_DIGEST, "digest", self._concern_body(incident))
        self._log(session, incident.id, "digest_queued", "added to daily digest",
                  at=now, priority=policy.step_priority(step), actor="system",
                  channel=CH_DIGEST)

    @staticmethod
    def _concern_body(incident: db.Incident) -> str:
        return f"Hearth: {incident.type} at {incident.home_name} ({incident.room})."

    # -- claim / resume ------------------------------------------------------

    def claim(self, incident_id: str, by: str, now: float | None = None) -> db.Incident:
        """Claim an incident per its policy's claim mode.

        ``pause`` pauses the ladder and starts the confirm window; ``close``
        acknowledges and stops the ladder with no resume; ``none`` is not
        claimable. Broadcasts a "claimed by …" notice for paging (fall) types.
        """
        now = self.now() if now is None else now
        with self._sessions() as session:
            incident = session.get(db.Incident, incident_id)
            if incident is None:
                raise CascadeError(f"unknown incident {incident_id}")
            if incident.status in db.TERMINAL:
                raise CascadeError("incident already resolved")
            policy = self.policy_for(incident.type)
            if not policy.claimable:
                raise CascadeError(f"incident type {incident.type!r} is not claimable")

            incident.paused_step_index = incident.next_step_index
            incident.status = db.CLAIMED
            incident.claimed_by = by
            incident.claimed_at = now
            incident.next_action = None
            incident.next_action_at = None
            incident.next_step_index = None
            if policy.claim_mode == CLAIM_PAUSE:
                incident.claim_deadline = now + policy.confirm_window_secs
            else:  # CLAIM_CLOSE — acknowledged, no resume
                incident.claim_deadline = None
            self._log(session, incident_id, "claimed", f"claimed by {by}",
                      at=now, priority=policy.priority, actor=by)
            if policy.claim_mode == CLAIM_PAUSE:
                self._broadcast_claim(session, incident, by, now)
            session.commit()
            session.refresh(incident)
            session.expunge(incident)
            return incident

    def claim_idempotent(self, incident_id: str, by: str,
                         now: float | None = None) -> tuple[db.Incident, bool]:
        """Claim idempotently: a repeat claim on an already-claimed incident is a
        no-op returning ``(incident, False)`` — no re-broadcast, no second event."""
        incident = self.get(incident_id)
        if incident is None:
            raise CascadeError(f"unknown incident {incident_id}")
        if incident.status in db.TERMINAL:
            raise CascadeError("incident already resolved")
        if incident.status == db.CLAIMED:
            return incident, False
        return self.claim(incident_id, by=by, now=now), True

    def _broadcast_claim(self, session, incident: db.Incident, by: str, now: float) -> None:
        body = f"Possible fall in {incident.room}: claimed by {by}."
        for contact in self._config.contacts:
            self._notifier.send(SMS, contact.sms, body)

    def _resume_after_claim(self, session, incident: db.Incident, now: float) -> None:
        """An unconfirmed pause-claim expired — resume the ladder (to OPEN) at the
        step that was pending when it was claimed."""
        policy = self.policy_for(incident.type)
        window_m = int(policy.confirm_window_secs // 60)
        self._log(session, incident.id, "claim_expired_resumed",
                  f"claim by {incident.claimed_by} unconfirmed after {window_m}m",
                  at=now, priority=policy.priority, actor="system")
        incident.status = db.ACTIVE
        incident.claim_deadline = None
        incident.next_step_index = incident.paused_step_index
        incident.paused_step_index = None
        self._advance(session, incident, now)

    # -- snooze --------------------------------------------------------------

    def snooze(self, incident_id: str, by: str | None = None,
               now: float | None = None) -> db.Incident:
        """Snooze a caregiver-type incident for the rest of the local day.

        Suppresses further steps (the incident stays open); the snooze is logged.
        ``fall.confirmed`` (and any non-snoozeable type) refuses to snooze."""
        now = self.now() if now is None else now
        with self._sessions() as session:
            incident = session.get(db.Incident, incident_id)
            if incident is None:
                raise CascadeError(f"unknown incident {incident_id}")
            if incident.status in db.TERMINAL:
                raise CascadeError("incident already resolved")
            policy = self.policy_for(incident.type)
            if not policy.snoozeable:
                raise CascadeError(f"incident type {incident.type!r} cannot be snoozed")
            incident.snoozed_until = self._end_of_day(now)
            incident.next_action = None
            incident.next_action_at = None
            incident.next_step_index = None
            self._log(session, incident_id, "snoozed", "snoozed for today",
                      at=now, priority=policy.priority,
                      actor=by or "caregiver")
            session.commit()
            session.refresh(incident)
            session.expunge(incident)
            return incident

    @staticmethod
    def _end_of_day(now: float) -> float:
        dt = datetime.fromtimestamp(now)
        end = dt.replace(hour=23, minute=59, second=59, microsecond=0)
        return end.timestamp()

    # -- auto-resolve --------------------------------------------------------

    def signal_clear(self, incident_id: str, now: float | None = None) -> db.Incident:
        """The trigger condition cleared (e.g. motion resumed): auto-resolve an
        ``auto_resolve`` incident (OPEN/CLAIMED → AUTO_RESOLVED). Logged."""
        now = self.now() if now is None else now
        with self._sessions() as session:
            incident = session.get(db.Incident, incident_id)
            if incident is None:
                raise CascadeError(f"unknown incident {incident_id}")
            policy = self.policy_for(incident.type)
            if not policy.auto_resolve:
                raise CascadeError(f"incident type {incident.type!r} does not auto-resolve")
            if incident.status in db.TERMINAL:
                return self._detach(session, incident)
            incident.status = db.AUTO_RESOLVED
            incident.resolved_at = now
            incident.next_action = None
            incident.next_action_at = None
            incident.next_step_index = None
            incident.claim_deadline = None
            self._log(session, incident_id, "auto_resolved",
                      "trigger condition cleared", at=now,
                      priority=policy.priority, actor="system")
            return self._commit_detach(session, incident)

    def clear_by_type(self, type_: str, home_name: str | None = None,
                      dedupe_key: str | None = None,
                      now: float | None = None) -> db.Incident | None:
        """Auto-resolve the open incident matching ``type_``+home (or an explicit
        dedupe key). Returns the resolved incident, or None if none was open."""
        home = home_name or self._config.home_name
        key = dedupe_key or f"{type_}:{home}"
        with self._sessions() as session:
            from sqlalchemy import select

            incident = session.scalars(
                select(db.Incident)
                .where(db.Incident.dedupe_key == key)
                .where(db.Incident.status.not_in(db.TERMINAL))
                .order_by(db.Incident.created_at.desc())
            ).first()
            incident_id = incident.id if incident is not None else None
        if incident_id is None:
            return None
        return self.signal_clear(incident_id, now=now)

    # -- resolve / disposition ----------------------------------------------

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
            if incident.status in db.TERMINAL:
                raise CascadeError("incident already resolved")
            policy = self.policy_for(incident.type)
            incident.status = db.RESOLVED
            incident.disposition = disposition
            incident.disposition_reason = reason_code
            incident.resolved_at = now
            incident.next_action = None
            incident.next_action_at = None
            incident.next_step_index = None
            incident.claim_deadline = None
            self._log(session, incident_id, "resolved",
                      f"{disposition} ({reason_code})" + (f" by {by}" if by else ""),
                      at=now, priority=policy.priority, actor=by or "caregiver")
            return self._commit_detach(session, incident)

    # -- read helpers --------------------------------------------------------

    def pending(self) -> list[db.Incident]:
        """Incidents that are not yet terminal (recovered on startup)."""
        from sqlalchemy import select

        with self._sessions() as session:
            rows = session.scalars(
                select(db.Incident).where(db.Incident.status.not_in(db.TERMINAL))
            ).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    def incidents(self, *, active_only: bool = False,
                  type_: str | None = None) -> list[db.Incident]:
        """All incidents (or only open ones / one type), newest first."""
        from sqlalchemy import select

        stmt = select(db.Incident).order_by(db.Incident.created_at.desc())
        if active_only:
            stmt = stmt.where(db.Incident.status.not_in(db.TERMINAL))
        if type_ is not None:
            stmt = stmt.where(db.Incident.type == type_)
        with self._sessions() as session:
            rows = session.scalars(stmt).all()
            for row in rows:
                session.expunge(row)
            return list(rows)

    # -- session detach helpers ---------------------------------------------

    @staticmethod
    def _detach(session, incident: db.Incident) -> db.Incident:
        session.expunge(incident)
        return incident

    @staticmethod
    def _commit_detach(session, incident: db.Incident) -> db.Incident:
        session.commit()
        session.refresh(incident)
        session.expunge(incident)
        return incident
