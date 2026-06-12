"""Structured logging for the ingest service (BAT-37).

When a node goes silent at 3 AM you must be able to reconstruct what happened
from the logs alone — without SSHing into the Pi. So every service log line is a
structured record: a level, an ISO-8601 timestamp, the component (logger name),
a short machine-readable ``event`` code, the human ``msg``, and whatever coarse
context the call site attached (``node_id``, ``room``, ``incident_id`` …).

Two renderings, selected by ``HEARTH_LOG_FORMAT``:

* ``text`` (default) — human-readable, for a developer tailing the console.
* ``json``  — one JSON object per line, for prod log shipping / grep / jq.

Level comes from ``HEARTH_LOG_LEVEL`` (default ``INFO``).

Privacy (CLAUDE.md: privacy is the brand): only the curated :data:`CONTEXT_FIELDS`
are promoted into the structured payload, so a stray ``extra=`` can never leak a
raw reading. Call sites pass coarse identifiers only — never breathing/heart
rate, motion energy, or any secret. The same discipline applies to ``msg``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

# The root of the service's logger tree; every component logs under "hearth.*".
ROOT_LOGGER = "hearth"

# Coarse context keys a call site may attach via ``extra=``; only these are
# emitted, so an accidental extra can't leak a raw reading or secret. Keep this
# to operational identifiers — never vitals (breathing/hr), motion energy, or
# credentials.
CONTEXT_FIELDS = (
    "node_id",
    "room",
    "incident_id",
    "home_id",
    "home_name",
    "from_state",
    "to_state",
    "kind",
    "status",
    "attempt",
    "count",
    "peer",
)

# LogRecord attributes that are always present; never treated as context.
_RESERVED = set(
    logging.makeLogRecord({}).__dict__
) | {"message", "asctime", "event", "taskName"}


def _iso(created: float) -> str:
    return datetime.fromtimestamp(created, tz=timezone.utc).isoformat()


def _context(record: logging.LogRecord) -> dict:
    """The coarse context fields the call site attached, allowlisted."""
    return {
        field: record.__dict__[field]
        for field in CONTEXT_FIELDS
        if field in record.__dict__ and record.__dict__[field] is not None
    }


class JsonFormatter(logging.Formatter):
    """One JSON object per line: level, ts, service, component, event, msg + context."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": _iso(record.created),
            "level": record.levelname,
            "service": self.service,
            "component": record.name,
            "event": getattr(record, "event", None),
            "msg": record.getMessage(),
        }
        payload.update(_context(record))
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # default=str keeps a surprising value from blowing up a log call.
        return json.dumps(payload, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable: ``<ts> LEVEL component [event] msg key=val …``."""

    def __init__(self, service: str) -> None:
        super().__init__()
        self.service = service

    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        head = f"{_iso(record.created)} {record.levelname:<7} {record.name}"
        if event:
            head += f" [{event}]"
        line = f"{head} {record.getMessage()}"
        ctx = _context(record)
        if ctx:
            line += " " + " ".join(f"{k}={v}" for k, v in ctx.items())
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


def _formatter(service: str) -> logging.Formatter:
    fmt = os.environ.get("HEARTH_LOG_FORMAT", "text").strip().lower()
    if fmt == "json":
        return JsonFormatter(service)
    return TextFormatter(service)


def _level() -> int:
    name = os.environ.get("HEARTH_LOG_LEVEL", "INFO").strip().upper()
    return logging.getLevelNamesMapping().get(name, logging.INFO)


def configure_logging(service: str = "ingest", *, stream=None, force: bool = False) -> logging.Logger:
    """Install the structured handler on the ``hearth`` logger tree.

    Idempotent: a second call is a no-op unless ``force`` (tests use ``force``
    with a captured ``stream`` to re-point output). Returns the root logger.
    """
    root = logging.getLogger(ROOT_LOGGER)
    if getattr(root, "_hearth_configured", False) and not force:
        return root
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(stream)
    handler.setFormatter(_formatter(service))
    root.addHandler(handler)
    root.setLevel(_level())
    # Don't double-emit through the python root logger's default handler.
    root.propagate = False
    root._hearth_configured = True  # type: ignore[attr-defined]
    return root
