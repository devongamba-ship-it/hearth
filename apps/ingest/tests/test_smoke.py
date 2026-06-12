"""Smoke tests for the ingest scaffold."""

from __future__ import annotations

from fastapi.testclient import TestClient

from ingest import __version__
from ingest.app import app
from ingest.db import init_db, make_engine


def test_health_endpoint_responds(session_factory) -> None:
    # BAT-37 made /health a real status payload; an empty DB has no live nodes,
    # so the verdict is "down". The shape + service/version contract still holds.
    app.state.session_factory = session_factory
    try:
        resp = TestClient(app).get("/health")
    finally:
        del app.state.session_factory
    assert resp.status_code == 200
    body = resp.json()
    assert body["service"] == "ingest"
    assert body["version"] == __version__
    assert body["status"] == "down"  # nothing reporting yet
    assert body["nodes"] == []
    assert "aggregate" in body


def test_init_db_is_idempotent_on_local_sqlite() -> None:
    # In-memory keeps the smoke test from touching the real hearth.db file.
    engine = make_engine("sqlite:///:memory:")
    init_db(engine)
    init_db(engine)  # second call must not raise
    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT 1").scalar() == 1
