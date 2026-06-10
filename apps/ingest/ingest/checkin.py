"""The check-in provider seam (BAT-22).

When the machine reaches PROBABLE it issues a check-in: a prompt asking the
resident "are you ok?" and a window to answer. In v1 there is **no microphone**
(a CLAUDE.md invariant), so there is nothing that can capture an answer — the
stub provider therefore never reports a response, and every issued check-in
times out into CONFIRMED_FALL. Real voice check-in is a separate issue (BAT-30);
it will ship a provider that returns ``responded()`` truthfully.

The machine owns the timeout clock (``checkin_timeout_secs``); a provider only
answers the single question "has the resident responded since the check-in for
this node started?". That keeps the timing deterministic and testable.
"""

from __future__ import annotations

from typing import Protocol


class CheckInProvider(Protocol):
    """A pluggable resident check-in channel."""

    def start(self, node_id: int, at_ms: int) -> None:
        """Issue a check-in prompt for ``node_id`` (idempotent per incident)."""

    def responded(self, node_id: int, now_ms: int) -> bool:
        """True once the resident has answered the active check-in."""

    def cancel(self, node_id: int) -> None:
        """Tear down the active check-in (resolved or no longer needed)."""


class StubCheckInProvider:
    """v1 provider: records that a prompt was issued, never reports a response.

    With no capture hardware there is nothing to answer the prompt, so this
    always returns ``False`` and the machine's timeout drives CONFIRMED_FALL.
    """

    def __init__(self) -> None:
        self.started: dict[int, int] = {}
        self.cancelled: list[int] = []

    def start(self, node_id: int, at_ms: int) -> None:
        self.started.setdefault(node_id, at_ms)

    def responded(self, node_id: int, now_ms: int) -> bool:  # noqa: ARG002
        return False

    def cancel(self, node_id: int) -> None:
        self.started.pop(node_id, None)
        self.cancelled.append(node_id)
