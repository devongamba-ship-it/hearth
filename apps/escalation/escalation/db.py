"""Local SQLite persistence for the escalation service.

Three tables:

* ``incidents`` — one row per CONFIRMED_FALL cascade, holding the *mutable*
  cascade state including the persisted ``next_action_at`` deadline so timers
  survive a restart (canonical design update, point 7).
* ``cascade_events`` — the **immutable audit log**. Rows are only ever inserted,
  never updated or deleted: who was contacted, when, who claimed, the final
  disposition + reason code.
* ``quiet_windows`` — resident-initiated "I'm OK, stand down" windows (BAT-39).
  The row *is* the audit record: start, end, actor, source, and any cancel. A
  window auto-expires at ``ends_at`` (no timer needed — expiry is computed from
  the clock), and never suppresses a P1 ``fall.confirmed`` page.

Local-first (CLAUDE.md): the DB is a file on the home device. Override with
``HEARTH_ESCALATION_DB_URL`` (tests use an in-memory URL). Never cloud storage.
"""

from __future__ import annotations

import os

from sqlalchemy import Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DEFAULT_DB_URL = "sqlite:///escalation.db"

# Incident status values. ``ACTIVE`` is the lifecycle's OPEN state — kept under
# its original name so the caregiver app's live contract (status == "active")
# does not change. ``AUTO_RESOLVED`` is the terminal state for auto-resolving
# types whose trigger condition cleared (BAT-31).
ACTIVE = "active"
CLAIMED = "claimed"
RESOLVED = "resolved"
AUTO_RESOLVED = "auto_resolved"
# Opened during a resident quiet window (BAT-39): recorded for the digest/audit,
# but never paged. Terminal so it does not linger in the caregiver "active" list.
SUPPRESSED = "suppressed"

# Terminal (closed) states — never paged again, excluded from ``state=open``.
TERMINAL = (RESOLVED, AUTO_RESOLVED, SUPPRESSED)

# Cascade ladder step action names (back-compat aliases for the fall policy).
STEP_VOICE = "voice"
STEP_OPERATOR = "operator"


def db_url() -> str:
    return os.environ.get("HEARTH_ESCALATION_DB_URL", DEFAULT_DB_URL)


class Base(DeclarativeBase):
    """Declarative base for escalation models."""


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    home_name: Mapped[str] = mapped_column(String, nullable=False)
    room: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[float] = mapped_column(Float, nullable=False)

    # Incident type (registry key) + its priority, and the dedupe key used to
    # collapse repeat raises. ``type`` defaults to the fall cascade so rows
    # written by the legacy ``/escalate`` path remain well-typed.
    type: Mapped[str] = mapped_column(String, nullable=False, default="fall.confirmed")
    priority: Mapped[str | None] = mapped_column(String, nullable=True)
    dedupe_key: Mapped[str | None] = mapped_column(String, nullable=True, index=True)

    # The ingest fall-machine incident id this cascade was raised for, when the
    # hand-off carried one. Lets the caregiver app (BAT-25) join the cascade
    # timeline to the upstream NORMAL→…→CONFIRMED_FALL state-machine replay.
    source_incident_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False, default=ACTIVE)

    # Persisted timer: the next ladder step (by index into the policy's steps,
    # with ``next_action`` carrying its action name for display/back-compat) and
    # the epoch deadline it fires at. Both NULL means the ladder is paused
    # (claimed), snoozed, or exhausted.
    next_action: Mapped[str | None] = mapped_column(String, nullable=True)
    next_action_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    next_step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Claim state. A pause-mode claim pauses (does not cancel) the ladder;
    # claim_deadline is when an unconfirmed claim expires and the ladder resumes
    # at the step that was pending (recorded in ``paused_step_index``).
    claimed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claim_deadline: Mapped[float | None] = mapped_column(Float, nullable=True)
    paused_step_index: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Snooze-for-today: while set (epoch), the ladder is suppressed; the incident
    # stays open but does not page again until the snooze passes.
    snoozed_until: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Final disposition (immutable once set) + required reason code.
    disposition: Mapped[str | None] = mapped_column(String, nullable=True)
    disposition_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    resolved_at: Mapped[float | None] = mapped_column(Float, nullable=True)


class CascadeEvent(Base):
    """Append-only audit record. Never updated or deleted."""

    __tablename__ = "cascade_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(
        String, ForeignKey("incidents.id"), nullable=False, index=True
    )
    at: Mapped[float] = mapped_column(Float, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    priority: Mapped[str | None] = mapped_column(String, nullable=True)
    detail: Mapped[str] = mapped_column(String, nullable=False, default="")
    # Every transition records who acted and on what channel (BAT-31): a
    # caregiver name, "system", or a channel like sms/voice/digest. Nullable so
    # pre-existing rows and purely-internal events need not carry both.
    actor: Mapped[str | None] = mapped_column(String, nullable=True)
    channel: Mapped[str | None] = mapped_column(String, nullable=True)


class QuietWindow(Base):
    """A resident-initiated "I'm OK, stand down" quiet window (BAT-39).

    While active (``started_at <= now < ends_at`` and not canceled), non-fall
    wellness alerts are suppressed. The row is the audit record: who set it
    (``actor``), from where (``source`` — ``app`` or ``button``), the window
    bounds, and any cancel. Append-only in spirit; only the cancel fields are
    ever updated after creation.
    """

    __tablename__ = "quiet_windows"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    home_name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    started_at: Mapped[float] = mapped_column(Float, nullable=False)
    ends_at: Mapped[float] = mapped_column(Float, nullable=False)
    # Who asked for quiet ("resident" by default) and where the request came
    # from: "app" (one-tap) or "button" (future hardware plug point, BAT-39).
    actor: Mapped[str] = mapped_column(String, nullable=False, default="resident")
    source: Mapped[str] = mapped_column(String, nullable=False, default="app")
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    # Set when a resident/caregiver cancels early; an un-canceled window simply
    # lapses at ``ends_at``.
    canceled_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    canceled_by: Mapped[str | None] = mapped_column(String, nullable=True)


def make_engine(url: str | None = None):
    return create_engine(url or db_url(), future=True)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), future=True)


def init_db(engine=None) -> None:
    """Create all registered tables. Idempotent."""
    Base.metadata.create_all(engine or make_engine())
