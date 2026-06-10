"""Smoke tests for the escalation scaffold.

These also pin the safety invariant: with no env set, escalation is dry-run and
produces no side effect.
"""

from __future__ import annotations

import pytest

from escalation import DRY_RUN, is_dry_run, mode, notify


def test_defaults_to_dry_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ESCALATION_MODE", raising=False)
    assert mode() == DRY_RUN
    assert is_dry_run() is True


def test_notify_in_dry_run_has_no_side_effect(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ESCALATION_MODE", raising=False)
    record = notify("+15555550123", "Fall detected in the living room.")
    assert record["status"] == "dry_run"
    assert record["recipient"] == "+15555550123"


def test_live_mode_is_not_implemented_in_scaffold(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESCALATION_MODE", "live")
    assert is_dry_run() is False
    with pytest.raises(NotImplementedError):
        notify("+15555550123", "Fall detected.")
