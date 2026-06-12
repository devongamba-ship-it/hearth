"""BAT-37: structured logging — parseable JSON in prod mode, coarse context only."""

from __future__ import annotations

import io
import json
import logging

import pytest

from ingest import logconfig


@pytest.fixture(autouse=True)
def _reset_logging():
    """Keep the shared ``hearth`` logger tree from leaking between tests."""
    yield
    root = logging.getLogger(logconfig.ROOT_LOGGER)
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root._hearth_configured = False  # type: ignore[attr-defined]
    root.propagate = True
    root.setLevel(logging.NOTSET)


def _capture(monkeypatch, fmt: str, level: str = "INFO") -> io.StringIO:
    monkeypatch.setenv("HEARTH_LOG_FORMAT", fmt)
    monkeypatch.setenv("HEARTH_LOG_LEVEL", level)
    buf = io.StringIO()
    logconfig.configure_logging("ingest", stream=buf, force=True)
    return buf


def test_json_mode_emits_one_parseable_object_with_context(monkeypatch):
    buf = _capture(monkeypatch, "json")
    log = logging.getLogger("hearth.ingest.fall_machine")
    log.info("node fell", extra={"event": "fall_transition", "node_id": 3,
                                 "incident_id": "abc", "room": "Kitchen"})

    line = buf.getvalue().strip()
    record = json.loads(line)  # must be valid JSON (AC: parseable JSON in prod)
    assert record["level"] == "INFO"
    assert record["service"] == "ingest"
    assert record["component"] == "hearth.ingest.fall_machine"
    assert record["event"] == "fall_transition"
    assert record["msg"] == "node fell"
    assert record["node_id"] == 3
    assert record["incident_id"] == "abc"
    assert record["room"] == "Kitchen"
    assert "ts" in record


def test_json_mode_drops_non_allowlisted_extras(monkeypatch):
    # A stray raw-reading value passed as extra must never reach the log — only
    # the curated coarse context fields are serialized (privacy is the brand).
    buf = _capture(monkeypatch, "json")
    log = logging.getLogger("hearth.ingest.listener")
    log.warning("oops", extra={"event": "x", "node_id": 1,
                               "breathing_bpm": 15.0, "motion_energy": 1500.0,
                               "auth_token": "sekret"})

    record = json.loads(buf.getvalue().strip())
    assert record["node_id"] == 1
    assert "breathing_bpm" not in record
    assert "motion_energy" not in record
    assert "auth_token" not in record
    assert "sekret" not in buf.getvalue()


def test_text_mode_is_human_readable(monkeypatch):
    buf = _capture(monkeypatch, "text")
    log = logging.getLogger("hearth.ingest.listener")
    log.info("listener up", extra={"event": "listener_up", "node_id": 7})

    out = buf.getvalue().strip()
    assert out.startswith("{") is False  # not JSON
    assert "[listener_up]" in out
    assert "listener up" in out
    assert "node_id=7" in out


def test_level_is_configurable(monkeypatch):
    buf = _capture(monkeypatch, "json", level="WARNING")
    log = logging.getLogger("hearth.ingest.listener")
    log.info("quiet", extra={"event": "noise"})
    log.warning("loud", extra={"event": "signal"})

    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    assert len(lines) == 1
    assert json.loads(lines[0])["event"] == "signal"
