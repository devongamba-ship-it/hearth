"""Local SQLite persistence for the ingest service.

Single shared `hearth.db` for v1 (per BAT-20). BAT-21 adds the two domain
tables: `readings` (one row per decoded vitals packet) and `events` (one row per
fall-flag transition).

Local-first: this is a file on the home device. Never point it at cloud storage.
"""

from __future__ import annotations

import os

from sqlalchemy import Boolean, Float, Integer, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# Local-first: default to a file next to the service.
DEFAULT_DB_URL = "sqlite:///hearth.db"

# Event kinds for the `events` table.
FALL_DETECTED = "fall_detected"
FALL_CLEARED = "fall_cleared"

# Notable kinds for the BAT-22 `fall_transitions` table. Every state change is
# persisted as a row; `kind` flags the ones downstream cares about. A plain
# transition with no special meaning is recorded as TRANSITION.
TRANSITION = "transition"
NEAR_FALL = "near_fall"  # a suspected fall that resolved by the person getting up
SUPPRESSED = "suppressed"  # a fall signature filtered out before alerting
CONFIRMED_FALL = "confirmed_fall"  # check-in unanswered -> escalation handed off


def db_url() -> str:
    """Resolve the SQLite URL from the environment.

    Precedence: explicit `HEARTH_DB_URL` (used by tests for in-memory DBs) >
    `HEARTH_DB_PATH` (the operator knob named in BAT-21, a filesystem path) >
    the local default file.
    """
    url = os.environ.get("HEARTH_DB_URL")
    if url:
        return url
    path = os.environ.get("HEARTH_DB_PATH")
    if path:
        return f"sqlite:///{path}"
    return DEFAULT_DB_URL


class Base(DeclarativeBase):
    """Declarative base for all ingest models."""


class Reading(Base):
    """One decoded vitals packet. Columns mirror the BAT-21 spec exactly.

    `ts` is the packet's own timestamp (ms since the node booted, from the wire
    format). `motion` holds the analog motion-energy; `confidence` holds the
    firmware presence score. "Latest" ordering uses the surrogate `id` (insert
    order), since ms-since-boot is not monotonic across node reboots.
    """

    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ts: Mapped[int] = mapped_column(Integer, nullable=False)
    presence: Mapped[bool] = mapped_column(Boolean, nullable=False)
    motion: Mapped[float] = mapped_column(Float, nullable=False)
    breathing_bpm: Mapped[float] = mapped_column(Float, nullable=False)
    hr_bpm: Mapped[float] = mapped_column(Float, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    fall_flag: Mapped[bool] = mapped_column(Boolean, nullable=False)
    occupancy: Mapped[int] = mapped_column(Integer, nullable=False)


class Event(Base):
    """A fall-flag transition: `kind` is FALL_DETECTED (0->1) or FALL_CLEARED
    (1->0). `ts` is the packet timestamp that caused the transition."""

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    ts: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)


class FallTransition(Base):
    """One step of the BAT-22 fall state machine, with a feature snapshot.

    Every transition (including suppressions) is recorded so the model can be
    tuned later and so a caregiver UI (BAT-25) can replay an incident. Rows are
    grouped by `incident_id`: a fresh id is minted each time a node leaves
    NORMAL, and shared by every transition until it returns. `reading_ts` is the
    packet's ms-since-boot that drove the change; `at` is the wall-clock epoch
    seconds when it was recorded (ms-since-boot is not a real timestamp).
    `features` is a JSON snapshot of the triggering reading.
    """

    __tablename__ = "fall_transitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    incident_id: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    node_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    reading_ts: Mapped[int] = mapped_column(Integer, nullable=False)
    at: Mapped[float] = mapped_column(Float, nullable=False)
    from_state: Mapped[str] = mapped_column(String(24), nullable=False)
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    kind: Mapped[str] = mapped_column(String(24), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    features: Mapped[str | None] = mapped_column(Text, nullable=True)


def make_engine(url: str | None = None):
    return create_engine(url or db_url(), future=True)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), future=True)


def init_db(engine=None) -> None:
    """Create all registered tables. Idempotent."""
    Base.metadata.create_all(engine or make_engine())
