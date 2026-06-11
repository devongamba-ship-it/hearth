"""Local SQLite persistence for the escalation service.

Two tables:

* ``incidents`` — one row per CONFIRMED_FALL cascade, holding the *mutable*
  cascade state including the persisted ``next_action_at`` deadline so timers
  survive a restart (canonical design update, point 7).
* ``cascade_events`` — the **immutable audit log**. Rows are only ever inserted,
  never updated or deleted: who was contacted, when, who claimed, the final
  disposition + reason code.

Local-first (CLAUDE.md): the DB is a file on the home device. Override with
``HEARTH_ESCALATION_DB_URL`` (tests use an in-memory URL). Never cloud storage.
"""

from __future__ import annotations

import os

from sqlalchemy import Float, ForeignKey, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

DEFAULT_DB_URL = "sqlite:///escalation.db"

# Incident status values.
ACTIVE = "active"
CLAIMED = "claimed"
RESOLVED = "resolved"

# Next-action steps in the cascade ladder.
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

    # The ingest fall-machine incident id this cascade was raised for, when the
    # hand-off carried one. Lets the caregiver app (BAT-25) join the cascade
    # timeline to the upstream NORMAL→…→CONFIRMED_FALL state-machine replay.
    source_incident_id: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False, default=ACTIVE)

    # Persisted timer: the next ladder step and the epoch deadline it fires at.
    # Both NULL means the cascade is paused (claimed) or terminal.
    next_action: Mapped[str | None] = mapped_column(String, nullable=True)
    next_action_at: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Claim state. A claim pauses (does not cancel) the cascade; claim_deadline
    # is when an unconfirmed claim expires and the cascade resumes at operator.
    claimed_by: Mapped[str | None] = mapped_column(String, nullable=True)
    claimed_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    claim_deadline: Mapped[float | None] = mapped_column(Float, nullable=True)

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


def make_engine(url: str | None = None):
    return create_engine(url or db_url(), future=True)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), future=True)


def init_db(engine=None) -> None:
    """Create all registered tables. Idempotent."""
    Base.metadata.create_all(engine or make_engine())
