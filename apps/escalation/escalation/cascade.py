"""Escalation mode + notification stub.

Binding rule (CLAUDE.md #7): ``ESCALATION_MODE=dry_run`` is the default
everywhere; live Twilio mode is human-set only. CI and tests must never produce
a real-world side effect. The actual SMS -> call ladder is built in BAT-23 — here
we only guarantee the safe default and a side-effect-free dry-run path.
"""

from __future__ import annotations

import os

DRY_RUN = "dry_run"
LIVE = "live"


def mode() -> str:
    """Current escalation mode, defaulting to the safe ``dry_run``."""
    return os.environ.get("ESCALATION_MODE", DRY_RUN).strip().lower() or DRY_RUN


def is_dry_run() -> bool:
    return mode() != LIVE


def notify(recipient: str, message: str) -> dict[str, str]:
    """Send (or, in dry-run, pretend to send) one escalation notification.

    Returns a record describing what happened. In dry-run no external call is
    made and no credentials are required — this is the path CI exercises. Live
    Twilio delivery is implemented in BAT-23 behind ``ESCALATION_MODE=live``.
    """
    if is_dry_run():
        return {"status": "dry_run", "recipient": recipient, "message": message}
    raise NotImplementedError(
        "live escalation (Twilio) is implemented in BAT-23; "
        "set ESCALATION_MODE=dry_run for the scaffold"
    )
