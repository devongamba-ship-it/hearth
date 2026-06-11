"""Back-compat surface for the escalation engine.

BAT-31 generalized the hard-coded fall cascade into the per-type
:class:`~escalation.policy_engine.PolicyEngine`. The fall ladder is now just the
``fall.confirmed`` policy instance. This module preserves the historical import
path and names (``CascadeEngine``, the ladder-timing constants, ``DISPOSITIONS``,
``CascadeError``) so BAT-23/BAT-25 callers and tests keep working unchanged.
"""

from __future__ import annotations

from .policy_engine import (
    CLAIM_CONFIRM_WINDOW,
    DISPOSITIONS,
    OPERATOR_DELAY,
    VOICE_DELAY,
    CascadeError,
    PolicyEngine,
)

# Historical name: the cascade engine is the policy engine.
CascadeEngine = PolicyEngine

__all__ = [
    "CascadeEngine",
    "PolicyEngine",
    "CascadeError",
    "DISPOSITIONS",
    "VOICE_DELAY",
    "OPERATOR_DELAY",
    "CLAIM_CONFIRM_WINDOW",
]
