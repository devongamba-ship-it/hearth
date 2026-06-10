"""Twilio webhook signature validation.

Twilio signs each webhook with ``X-Twilio-Signature``: base64(HMAC-SHA1(authToken,
fullURL + concat(sorted POST params as key+value))). We recompute it and compare
in constant time. This is a security control independent of escalation mode — it
runs even in dry-run, since it protects the ack/claim endpoints from forgery.

Reference: Twilio "Validating Signatures from Twilio".
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os


def compute_signature(auth_token: str, url: str, params: dict[str, str]) -> str:
    """Compute the expected ``X-Twilio-Signature`` for a request."""
    data = url
    for key in sorted(params):
        data += key + (params[key] if params[key] is not None else "")
    digest = hmac.new(
        auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def is_valid_signature(
    auth_token: str, url: str, params: dict[str, str], signature: str | None
) -> bool:
    """True iff ``signature`` matches the request, compared in constant time."""
    if not auth_token or not signature:
        return False
    expected = compute_signature(auth_token, url, params)
    return hmac.compare_digest(expected, signature)


def auth_token() -> str | None:
    """The Twilio auth token used to validate webhooks (env only)."""
    return os.environ.get("TWILIO_AUTH_TOKEN")
