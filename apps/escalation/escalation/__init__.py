"""Hearth escalation service (scaffold).

Turns a confirmed fall into an SMS -> call escalation ladder with caregiver
acknowledgment. The cascade ladder and Twilio integration land in BAT-23; this
package is the scaffold, and it ships the safety default the whole system relies
on: escalation runs in ``dry_run`` unless a human explicitly enables live mode.
"""

__all__ = ["__version__", "DRY_RUN", "LIVE", "mode", "is_dry_run", "notify"]

__version__ = "0.1.0"

from .cascade import DRY_RUN, LIVE, is_dry_run, mode, notify
