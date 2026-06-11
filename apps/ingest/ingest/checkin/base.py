"""The check-in provider seam (BAT-22, extended by BAT-30).

When the machine reaches PROBABLE it issues a check-in: a prompt asking the
resident "Are you OK?" and a window to answer. The provider's only job is to
answer one question per poll — *what has the resident said so far* — as a
three-way outcome:

* ``PENDING`` — no answer yet (keep waiting; the machine owns the timeout clock)
* ``OK`` — the resident answered they're fine → dismiss to NORMAL (recovered)
* ``HELP`` — the resident called for help → escalate **immediately**

The third outcome is the BAT-30 extension (CANONICAL DESIGN UPDATE, Option A on
the Linear thread): an audible "help" is an active distress call, so it must not
have to wait out the remaining timeout window. The machine still owns the
timeout clock — ``PENDING`` until the window elapses is a CONFIRMED_FALL exactly
as before. Only the fast-paths (``OK``/``HELP``) are new.

Keeping all routing in this single enum is what makes "swap the provider, change
no machine code" (BAT-22 invariant / AC#3) hold: ``StubCheckInProvider`` and
``LocalAudioCheckInProvider`` share the exact same ``poll()`` contract.
"""

from __future__ import annotations

from enum import Enum
from typing import Protocol


class CheckInResult(str, Enum):
    """The outcome of polling an active check-in."""

    PENDING = "pending"  # no answer yet — keep the window open
    OK = "ok"  # resident is fine — dismiss as recovered
    HELP = "help"  # resident called for help — escalate now


class CheckInProvider(Protocol):
    """A pluggable resident check-in channel."""

    def start(self, node_id: int, at_ms: int) -> None:
        """Issue a check-in prompt for ``node_id`` (idempotent per incident)."""

    def poll(self, node_id: int, now_ms: int) -> CheckInResult:
        """Three-way outcome of the active check-in as of ``now_ms``."""

    def cancel(self, node_id: int) -> None:
        """Tear down the active check-in (resolved or no longer needed)."""


class StubCheckInProvider:
    """v1 provider: records that a prompt was issued, never reports a response.

    With no capture hardware there is nothing to answer the prompt, so this
    always returns ``PENDING`` and the machine's timeout drives CONFIRMED_FALL —
    the original BAT-22 behaviour, now expressed in the three-way contract.
    """

    def __init__(self) -> None:
        self.started: dict[int, int] = {}
        self.cancelled: list[int] = []

    def start(self, node_id: int, at_ms: int) -> None:
        self.started.setdefault(node_id, at_ms)

    def poll(self, node_id: int, now_ms: int) -> CheckInResult:  # noqa: ARG002
        return CheckInResult.PENDING

    def cancel(self, node_id: int) -> None:
        self.started.pop(node_id, None)
        self.cancelled.append(node_id)
