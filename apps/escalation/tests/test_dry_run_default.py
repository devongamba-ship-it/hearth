"""No real Twilio calls in CI (CLAUDE.md #7, BAT-23 acceptance).

With no ESCALATION_MODE set, the cascade selects the side-effect-free dry-run
notifier — Twilio is never constructed and no credentials are read.
"""

from __future__ import annotations

import pytest

from escalation.notifier import DryRunNotifier, TwilioNotifier, make_notifier


def test_make_notifier_defaults_to_dry_run(monkeypatch):
    monkeypatch.delenv("ESCALATION_MODE", raising=False)
    assert isinstance(make_notifier(), DryRunNotifier)


def test_live_mode_requires_credentials(monkeypatch):
    monkeypatch.setenv("ESCALATION_MODE", "live")
    for var in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER"):
        monkeypatch.delenv(var, raising=False)
    # Live mode without creds refuses to construct — it cannot silently send.
    with pytest.raises(RuntimeError):
        make_notifier()


def test_twilio_notifier_does_not_import_sdk_at_construction(monkeypatch):
    # Constructing TwilioNotifier with creds must not require the twilio SDK
    # (it is lazy-imported only when actually sending). This keeps CI clean.
    monkeypatch.setenv("TWILIO_ACCOUNT_SID", "AC_test")
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "tok_test")
    monkeypatch.setenv("TWILIO_FROM_NUMBER", "+15555550000")
    notifier = TwilioNotifier()
    assert notifier is not None
