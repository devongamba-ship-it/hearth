"""Local SQLite persistence for the ingest service.

Single shared `hearth.db` for v1 (per BAT-20). BAT-21 adds the two domain
tables: `readings` (one row per decoded vitals packet) and `events` (one row per
fall-flag transition).

Local-first: this is a file on the home device. Never point it at cloud storage.
"""

from __future__ import annotations

import os

from sqlalchemy import Boolean, Float, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

# Local-first: default to a file next to the service.
DEFAULT_DB_URL = "sqlite:///hearth.db"

# Event kinds for the `events` table.
FALL_DETECTED = "fall_detected"
FALL_CLEARED = "fall_cleared"


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


def make_engine(url: str | None = None):
    return create_engine(url or db_url(), future=True)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), future=True)


def init_db(engine=None) -> None:
    """Create all registered tables. Idempotent."""
    Base.metadata.create_all(engine or make_engine())
