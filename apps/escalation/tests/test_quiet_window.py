"""Resident "I'm OK / pause" quiet window (BAT-39).

A resident-initiated quiet window suppresses NON-fall wellness alerts for its
duration — alert fatigue is what kills these products. The safety invariant is
non-negotiable: ``fall.confirmed`` (P1) is NEVER suppressed; a real fall always
pages, quiet window or not.

All side-effect free: a DryRunNotifier records actions, an injectable clock
drives expiry; nothing leaves the box and no credentials are needed.
"""

from __future__ import annotations

import pytest

from escalation import db
from escalation.engine import CascadeError
from escalation.policy import PAGING_CHANNELS
from escalation.policy_engine import MAX_QUIET_SECS

PAGING = set(PAGING_CHANNELS)

# Far from the epoch: Windows cannot timestamp() pre-1970 local datetimes, and
# the engine's quiet-hours math uses local wall-clock (mirrors the BAT-31 tests).
T0 = 1_000_000_000


def _paged(engine):
    return [m for m in engine.notifier.sent if m.channel in PAGING]


def _kinds(engine, incident_id):
    return [e.kind for e in engine.timeline(incident_id)]


# -- AC1: suppresses wellness; fall still escalates --------------------------


def test_quiet_window_suppresses_wellness_incident(engine, clock):
    clock.set(T0)
    engine.start_quiet(2 * 3600, actor="resident")  # "I'm fine for 2 hours"

    inc, created = engine.raise_incident("wellness.prolonged_stillness", room="bedroom")
    assert created is True
    # Recorded for the audit/digest, but terminal and never paged.
    assert inc.status == db.SUPPRESSED
    assert "suppressed_resident_quiet" in _kinds(engine, inc.id)

    # Drive well past every step deadline: still no page reaches anyone.
    clock.set(T0 + 7200)
    engine.process_due()
    assert _paged(engine) == []


def test_system_health_alert_not_suppressed_by_quiet_window(engine, clock):
    # PR #17 review: the window is scoped to *wellness* nudges. A system.* health
    # alert (sensing dropped — a fall can't even be detected while it's down) must
    # still reach the caregiver during a window, not be silently muted.
    clock.set(T0)
    engine.start_quiet(8 * 3600, actor="resident")

    inc, created = engine.raise_incident("system.sensing_gap", room="home")
    assert created is True
    assert inc.status == db.ACTIVE  # NOT suppressed
    assert "suppressed_resident_quiet" not in _kinds(engine, inc.id)
    assert _paged(engine), "system.sensing_gap must still page during a quiet window"


def test_fall_confirmed_never_suppressed_by_quiet_window(engine, clock):
    # The safety invariant. A real fall pages during a resident quiet window.
    clock.set(T0)
    engine.start_quiet(8 * 3600, actor="resident")

    incident_id = engine.escalate(room="kitchen")
    inc = engine.get(incident_id)
    assert inc.type == "fall.confirmed"
    assert inc.status == db.ACTIVE  # NOT suppressed
    assert "suppressed_resident_quiet" not in _kinds(engine, incident_id)

    # The T+0 push fired immediately and the ladder is armed despite the window.
    assert "push_sent" in _kinds(engine, incident_id)
    assert _paged(engine), "fall.confirmed must page even inside a quiet window"

    # The delayed voice ladder still fires too.
    clock.set(T0 + 60)
    engine.process_due()
    assert "voice_calls" in _kinds(engine, incident_id)


def test_suppression_only_affects_window_home(engine, clock):
    # A quiet window for one home does not silence another home's wellness alert.
    clock.set(T0)
    engine.start_quiet(3600, home_name="Rosewood")
    other, _ = engine.raise_incident(
        "wellness.prolonged_stillness", room="bedroom", home_name="Lakeshore")
    assert other.status == db.ACTIVE
    assert _paged(engine), "a different home is unaffected by the window"


# -- AC2: auto-expires and is logged (start/end, actor) ---------------------


def test_quiet_window_auto_expires(engine, clock):
    clock.set(T0)
    window, _ = engine.start_quiet(3600, actor="resident")

    # During the window: suppressed.
    during, _ = engine.raise_incident("wellness.missed_meal", room="kitchen")
    assert during.status == db.SUPPRESSED

    # After ``ends_at`` the window lapses with no timer — a new wellness alert
    # pages normally again.
    clock.set(window.ends_at + 1)
    assert engine.active_quiet_window() is None
    after, _ = engine.raise_incident("wellness.prolonged_stillness", room="bedroom")
    assert after.status == db.ACTIVE
    assert _paged(engine)


def test_quiet_window_records_start_end_actor(engine, clock):
    clock.set(T0)
    window, created = engine.start_quiet(2 * 3600, actor="Mom", source="app",
                                         reason="watching TV")
    assert created is True
    # The row IS the audit record: start, end, actor, source.
    assert window.started_at == T0
    assert window.ends_at == T0 + 2 * 3600
    assert window.actor == "Mom"
    assert window.source == "app"
    assert window.reason == "watching TV"


def test_quiet_window_capped_at_max(engine, clock):
    clock.set(T0)
    window, _ = engine.start_quiet(10 * 86400)  # ask for 10 days
    assert window.ends_at - window.started_at == MAX_QUIET_SECS  # clamped to 24h


def test_start_quiet_rejects_nonpositive_duration(engine, clock):
    clock.set(T0)
    with pytest.raises(CascadeError):
        engine.start_quiet(0)


# -- AC3: set + cancel + remaining time -------------------------------------


def test_quiet_window_is_idempotent(engine, clock):
    clock.set(T0)
    first, created1 = engine.start_quiet(3600)
    # A second tap while active returns the SAME window — windows never stack.
    clock.set(T0 + 60)
    second, created2 = engine.start_quiet(3600)
    assert created1 is True and created2 is False
    assert second.id == first.id
    assert second.ends_at == first.ends_at  # not extended


def test_cancel_quiet_reopens_alerting(engine, clock):
    clock.set(T0)
    engine.start_quiet(4 * 3600, actor="resident")
    canceled = engine.cancel_quiet(by="Dana")
    assert canceled is not None
    assert canceled.canceled_at == T0
    assert canceled.canceled_by == "Dana"
    assert engine.active_quiet_window() is None

    # Immediately after cancel, a wellness alert pages again.
    inc, _ = engine.raise_incident("wellness.prolonged_stillness", room="bedroom")
    assert inc.status == db.ACTIVE
    assert _paged(engine)


def test_cancel_with_no_active_window_is_noop(engine, clock):
    clock.set(T0)
    assert engine.cancel_quiet(by="Dana") is None


def test_active_window_reports_remaining_via_ends_at(engine, clock):
    clock.set(T0)
    window, _ = engine.start_quiet(2 * 3600)
    clock.set(T0 + 1800)  # 30 min elapsed
    active = engine.active_quiet_window()
    assert active is not None
    assert int(active.ends_at - engine.now()) == 90 * 60  # 90 min remaining


# -- AC4: hardware-button plug point ----------------------------------------


def test_hardware_button_uses_the_same_path(engine, clock):
    # A future ESP32/BLE "I'm OK" button posts the same call with source=button.
    clock.set(T0)
    window, created = engine.start_quiet(2 * 3600, actor="resident", source="button")
    assert created is True and window.source == "button"
    inc, _ = engine.raise_incident("wellness.missed_meal", room="kitchen")
    assert inc.status == db.SUPPRESSED
