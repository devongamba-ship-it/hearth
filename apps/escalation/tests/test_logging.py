"""BAT-37: structured logging for the escalation service."""

from __future__ import annotations

import io
import json
import logging

import pytest

from escalation import logconfig


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    root = logging.getLogger(logconfig.ROOT_LOGGER)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root._hearth_configured = False  # type: ignore[attr-defined]
    root.propagate = True
    root.setLevel(logging.NOTSET)


def test_json_mode_carries_incident_context(monkeypatch):
    monkeypatch.setenv("HEARTH_LOG_FORMAT", "json")
    buf = io.StringIO()
    logconfig.configure_logging("escalation", stream=buf, force=True)

    log = logging.getLogger("hearth.escalation")
    log.info("dry-run notify", extra={"event": "notify_dry_run", "status": "dry_run",
                                      "incident_id": "i-42", "room": "Bathroom"})

    record = json.loads(buf.getvalue().strip())
    assert record["service"] == "escalation"
    assert record["event"] == "notify_dry_run"
    assert record["status"] == "dry_run"
    assert record["incident_id"] == "i-42"
    assert record["room"] == "Bathroom"


def test_text_mode_default(monkeypatch):
    monkeypatch.delenv("HEARTH_LOG_FORMAT", raising=False)
    buf = io.StringIO()
    logconfig.configure_logging("escalation", stream=buf, force=True)

    logging.getLogger("hearth.escalation").info("up", extra={"event": "ready"})
    out = buf.getvalue().strip()
    assert "[ready]" in out
    assert out.startswith("{") is False
