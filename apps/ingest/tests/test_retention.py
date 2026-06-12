"""BAT-41: feature-snapshot completeness + data-retention policy.

Two acceptance criteria are covered here:

  * Every fall-machine ``transition`` / ``suppressed`` / ``near_fall`` row
    persists a non-null feature snapshot with the expected keys.
  * Retention is config-driven, the default keeps everything, and there is no
    silent deletion anywhere in the codebase.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from sqlalchemy import select

from ingest import db
from ingest.checkin import StubCheckInProvider
from ingest.detection import DetectionConfig, QuietHours
from ingest.escalator import NullEscalator
from ingest.fall_machine import FEATURE_KEYS, FallMachine, Frame, State
from ingest.retention import (
    PURGEABLE,
    RetentionConfig,
    load_retention_config,
    parse_retention,
    purge,
)

# Fixed wall clock so quiet-hours suppression is deterministic.
NOON = 1_700_000_000.0 - (1_700_000_000.0 % 86400) + 12 * 3600
NIGHT = NOON + 11 * 3600  # 23:00 — inside the default quiet-hours window


def _frame(node_id, t, *, presence=True, motion=1000.0, fall=False):
    return Frame(node_id=node_id, ts=t * 1000, presence=presence, motion=motion,
                 fall_flag=fall, breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9,
                 occupancy=1)


def _machine(session_factory, *, clock=lambda: NOON, **cfg):
    return FallMachine(
        DetectionConfig(**cfg), escalator=NullEscalator(),
        checkin=StubCheckInProvider(), session_factory=session_factory, clock=clock,
    )


# --------------------------------------------------------------------------- #
# AC 1: feature-snapshot completeness
# --------------------------------------------------------------------------- #


def test_every_transition_kind_persists_a_complete_feature_snapshot(session_factory):
    """Drive transition, near_fall, AND suppressed events, then assert every
    persisted row carries a full, non-null feature snapshot."""
    expected = set(FEATURE_KEYS)

    # node 1 -> a near_fall (got up): NORMAL -> SUSPECTED -> NORMAL(near_fall)
    m = _machine(session_factory, recovery_motion_secs=3)
    m.step(_frame(1, 0, motion=9500, fall=True))   # transition into SUSPECTED
    for t in range(1, 5):                           # sustained motion -> got_up
        m.step(_frame(1, t, motion=9000))
    assert m.state_of(1) is State.NORMAL

    # node 2 -> a suppressed event: fall flag with nobody present.
    m.step(_frame(2, 10, presence=False, fall=True))

    # node 3 -> an in-bed suppression during quiet hours (a second suppress path).
    mn = _machine(session_factory, clock=lambda: NIGHT, bedroom_nodes=(3,),
                  quiet_hours=QuietHours(enabled=True))
    mn.step(_frame(3, 0, motion=10.0, fall=True))

    with session_factory() as s:
        rows = s.execute(select(db.FallTransition)).scalars().all()

    kinds = {r.kind for r in rows}
    assert db.TRANSITION in kinds
    assert db.NEAR_FALL in kinds
    assert db.SUPPRESSED in kinds

    assert rows, "expected persisted transitions"
    for r in rows:
        assert r.features is not None, f"row {r.id} ({r.kind}) has no feature snapshot"
        snap = json.loads(r.features)
        assert set(snap) == expected, f"row {r.id} ({r.kind}) keys {set(snap)} != {expected}"
        # The snapshot is the real reading state, not placeholders.
        assert snap["ts"] == r.reading_ts


def test_feature_keys_match_frame_snapshot_producer():
    """FEATURE_KEYS is the single contract; Frame.features() must honor it."""
    snap = _frame(1, 5).features()
    assert set(snap) == set(FEATURE_KEYS)


# --------------------------------------------------------------------------- #
# AC 2: retention is config-driven; default keeps everything
# --------------------------------------------------------------------------- #


def test_default_policy_keeps_everything():
    assert parse_retention(None).keep_all is True
    assert parse_retention("").keep_all is True
    assert parse_retention("  all ").keep_all is True
    assert parse_retention("keep-all").keep_all is True


def test_load_retention_config_defaults_to_keep_all(monkeypatch):
    monkeypatch.delenv("HEARTH_RETENTION", raising=False)
    assert load_retention_config().keep_all is True


def test_bare_integer_applies_one_window_to_all_purgeable_tables():
    cfg = parse_retention("180")
    assert not cfg.keep_all
    assert cfg.max_age_days("readings") == 180
    assert cfg.max_age_days("transitions") == 180


def test_per_table_windows_parse():
    cfg = parse_retention("readings=90,transitions=730")
    assert cfg.max_age_days("readings") == 90
    assert cfg.max_age_days("transitions") == 730


def test_per_table_all_keeps_that_table_forever():
    cfg = parse_retention("readings=90,transitions=all")
    assert cfg.max_age_days("readings") == 90
    assert cfg.max_age_days("transitions") is None  # kept forever


def test_unknown_table_is_a_hard_error():
    with pytest.raises(ValueError, match="unknown table"):
        parse_retention("cameras=30")


def test_malformed_and_negative_values_are_hard_errors():
    with pytest.raises(ValueError):
        parse_retention("readings=soon")
    with pytest.raises(ValueError):
        parse_retention("readings=-5")


# --------------------------------------------------------------------------- #
# AC 2: no silent deletion — the only delete path is explicit + opt-in
# --------------------------------------------------------------------------- #


def _seed(session_factory, now):
    """Two readings + two transitions: one old, one fresh, in each table."""
    old = now - 400 * 86_400
    fresh = now - 1 * 86_400
    with session_factory() as s:
        s.add_all([
            db.Reading(node_id=1, ts=1, presence=True, motion=10.0, breathing_bpm=15.0,
                       hr_bpm=70.0, confidence=0.9, fall_flag=False, occupancy=1,
                       received_at=old),
            db.Reading(node_id=1, ts=2, presence=True, motion=10.0, breathing_bpm=15.0,
                       hr_bpm=70.0, confidence=0.9, fall_flag=False, occupancy=1,
                       received_at=fresh),
            db.Reading(node_id=1, ts=3, presence=True, motion=10.0, breathing_bpm=15.0,
                       hr_bpm=70.0, confidence=0.9, fall_flag=False, occupancy=1,
                       received_at=None),  # undated: never purged
            db.FallTransition(incident_id="a", node_id=1, reading_ts=1, at=old,
                              from_state="NORMAL", to_state="SUSPECTED",
                              kind=db.TRANSITION, reason=None, features="{}"),
            db.FallTransition(incident_id="b", node_id=1, reading_ts=2, at=fresh,
                              from_state="NORMAL", to_state="SUSPECTED",
                              kind=db.TRANSITION, reason=None, features="{}"),
        ])
        s.commit()


def _counts(session_factory):
    with session_factory() as s:
        return (len(s.execute(select(db.Reading)).scalars().all()),
                len(s.execute(select(db.FallTransition)).scalars().all()))


def test_keep_all_deletes_nothing_even_with_confirm(session_factory):
    now = 1_700_000_000.0
    _seed(session_factory, now)
    report = purge(session_factory, RetentionConfig(), now=now, confirm=True)
    assert report.total_deleted == 0
    assert _counts(session_factory) == (3, 2)  # untouched


def test_dry_run_reports_but_deletes_nothing(session_factory):
    now = 1_700_000_000.0
    _seed(session_factory, now)
    cfg = parse_retention("90")
    report = purge(session_factory, cfg, now=now, confirm=False)
    assert report.dry_run is True
    assert report.total_matched == 2  # the two old rows
    assert report.total_deleted == 0
    assert _counts(session_factory) == (3, 2)  # nothing actually removed


def test_confirm_purges_only_rows_older_than_window(session_factory):
    now = 1_700_000_000.0
    _seed(session_factory, now)
    cfg = parse_retention("90")
    report = purge(session_factory, cfg, now=now, confirm=True)
    assert report.total_deleted == 2
    # Fresh rows AND the undated (NULL received_at) reading survive.
    assert _counts(session_factory) == (2, 1)


def test_per_table_window_scopes_the_purge(session_factory):
    now = 1_700_000_000.0
    _seed(session_factory, now)
    # Only readings have a window; transitions are kept forever.
    cfg = parse_retention("readings=90,transitions=all")
    purge(session_factory, cfg, now=now, confirm=True)
    readings, transitions = _counts(session_factory)
    assert readings == 2     # old readings purged, fresh + undated kept
    assert transitions == 2  # transitions untouched


def test_events_table_is_never_purgeable():
    # events has no real wall-clock column, so it is not a purge target at all.
    assert "events" not in PURGEABLE
    assert db.Event.__tablename__ == "events"


def test_no_silent_deletion_anywhere_in_source():
    """The only deletion path in the service is retention.py. No other module
    may delete rows (DELETE / session.delete / drop_all) behind the operator."""
    src = pathlib.Path(__file__).resolve().parents[1] / "ingest"
    offenders = []
    for path in src.rglob("*.py"):
        if path.name == "retention.py":
            continue
        text = path.read_text(encoding="utf-8").lower()
        for needle in ("delete(", ".delete(", "drop_all", "delete from", "truncate"):
            if needle in text:
                offenders.append(f"{path.name}: {needle!r}")
    assert not offenders, f"unexpected deletion code outside retention.py: {offenders}"


# --------------------------------------------------------------------------- #
# AC 3: the policy doc exists and states policy + privacy posture
# --------------------------------------------------------------------------- #


def test_data_retention_doc_exists_and_states_policy_and_privacy():
    doc = pathlib.Path(__file__).resolve().parents[3] / "docs" / "data-retention.md"
    assert doc.exists(), "docs/data-retention.md is required by BAT-41"
    text = doc.read_text(encoding="utf-8")
    assert "HEARTH_RETENTION" in text          # the knob
    assert "keep" in text.lower()              # default keeps everything
    assert "--confirm" in text                 # opt-in purge tool
    assert "Local-first" in text or "local-first" in text.lower()  # privacy posture
    assert "off-device" in text.lower() or "off the home" in text.lower()
