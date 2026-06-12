"""How a CONFIRMED_FALL leaves the ingest box (BAT-22).

The state machine does not know about Twilio, cascades, or contacts — it only
knows it must hand a confirmed fall to the escalation service. That hand-off is
a single local HTTP POST to ``/escalate``, fire-and-forget with a couple of
retries, behind the :class:`Escalator` seam so tests (and CI) never touch the
network.

Local-first / no-CI-side-effects: the default machine uses a
:class:`NullEscalator` (logs only). A real deployment wires an
:class:`HttpEscalator` pointed at the local escalation service via
``HEARTH_ESCALATION_URL``; the escalation service is itself dry-run unless a
human sets ``ESCALATION_MODE=live``.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("hearth.ingest.fall_machine")

DEFAULT_ESCALATION_URL = "http://127.0.0.1:8001/escalate"


@dataclass(frozen=True)
class Escalation:
    """The payload handed to the escalation service for a confirmed fall."""

    incident_id: str
    node_id: int
    room: str
    home_name: str | None
    at_ms: int

    def payload(self) -> dict:
        return {
            "room": self.room,
            "home_name": self.home_name,
            "source": "fall_machine",
            "node_id": self.node_id,
            "fall_incident_id": self.incident_id,
            "detected_at_ms": self.at_ms,
        }


class Escalator(Protocol):
    """Delivers a CONFIRMED_FALL to the escalation service."""

    def escalate(self, escalation: Escalation) -> bool:
        """Best-effort deliver. Returns True on success, never raises."""


class NullEscalator:
    """Records escalations without leaving the process. Safe default / test double."""

    def __init__(self) -> None:
        self.calls: list[Escalation] = []

    def escalate(self, escalation: Escalation) -> bool:
        self.calls.append(escalation)
        log.info(
            "CONFIRMED_FALL node=%s room=%s incident=%s (NullEscalator: not delivered)",
            escalation.node_id, escalation.room, escalation.incident_id,
            extra={"event": "escalate_null", "node_id": escalation.node_id,
                   "room": escalation.room, "incident_id": escalation.incident_id},
        )
        return True


class HttpEscalator:
    """POSTs to the local escalation ``/escalate`` endpoint, with retries.

    Fire-and-forget: a delivery failure is logged and swallowed (returns False)
    so a flaky escalation service can never take the fall machine down. The
    escalation service dedupes/queues on its side; the per-node 5-minute dedupe
    here keeps us from hammering it.
    """

    def __init__(self, url: str | None = None, *, retries: int = 2, timeout: float = 3.0) -> None:
        self.url = url or os.environ.get("HEARTH_ESCALATION_URL", DEFAULT_ESCALATION_URL)
        self.retries = retries
        self.timeout = timeout

    def escalate(self, escalation: Escalation) -> bool:
        body = json.dumps(escalation.payload()).encode("utf-8")
        last_err: Exception | None = None
        for attempt in range(self.retries + 1):
            req = urllib.request.Request(
                self.url, data=body, headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    if 200 <= resp.status < 300:
                        return True
                    last_err = RuntimeError(f"escalation returned HTTP {resp.status}")
            except (urllib.error.URLError, OSError) as exc:  # noqa: PERF203
                last_err = exc
            log.warning("escalate attempt %d failed: %s", attempt + 1, last_err)
        log.error("escalation POST gave up after %d attempts: %s", self.retries + 1, last_err)
        return False
