"""Shared test fixtures for the ingest service."""

from __future__ import annotations

import pytest

from ingest.db import init_db, make_engine, make_session_factory


@pytest.fixture
def session_factory(tmp_path):
    """A session factory bound to a throwaway on-disk SQLite DB.

    On-disk (not :memory:) so every connection the listener and the test open
    sees the same rows.
    """
    engine = make_engine(f"sqlite:///{tmp_path / 'hearth_test.db'}")
    init_db(engine)
    return make_session_factory(engine)
