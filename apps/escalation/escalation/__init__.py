"""Hearth escalation service.

Turns a confirmed fall into an SMS/push → voice → operator cascade with
caregiver acknowledgment. A claim *pauses* the cascade and starts a 15-minute
confirm window; an unconfirmed claim resumes at the operator step. Every action
is written to an immutable audit log, and persisted timers survive restarts.

Safety default the whole system relies on: escalation runs in ``dry_run`` and
makes no external call unless a human explicitly sets ``ESCALATION_MODE=live``.
"""

__all__ = [
    "__version__",
    "DRY_RUN",
    "LIVE",
    "mode",
    "is_dry_run",
    "notify",
    "CascadeEngine",
    "CascadeError",
    "DISPOSITIONS",
    "load_config",
    "load_config_from_text",
]

__version__ = "0.2.0"

from .cascade import DRY_RUN, LIVE, is_dry_run, mode, notify
from .config import load_config, load_config_from_text
from .engine import DISPOSITIONS, CascadeEngine, CascadeError
