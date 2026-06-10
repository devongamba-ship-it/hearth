"""Local SQLite persistence for the ingest service.

Single shared `hearth.db` for v1 (per BAT-20). Domain tables (vitals state,
alert events) are added by the issues that own them — this module only wires the
engine, session factory, and metadata so later runs have a home.
"""

from __future__ import annotations

import os

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

# Local-first: default to a file next to the service. Override with HEARTH_DB_URL
# (e.g. an in-memory URL in tests). Never point this at cloud storage.
DEFAULT_DB_URL = "sqlite:///hearth.db"


def db_url() -> str:
    return os.environ.get("HEARTH_DB_URL", DEFAULT_DB_URL)


class Base(DeclarativeBase):
    """Declarative base for all ingest models."""


def make_engine(url: str | None = None):
    return create_engine(url or db_url(), future=True)


def make_session_factory(engine=None):
    return sessionmaker(bind=engine or make_engine(), future=True)


def init_db(engine=None) -> None:
    """Create all registered tables. Idempotent."""
    Base.metadata.create_all(engine or make_engine())
