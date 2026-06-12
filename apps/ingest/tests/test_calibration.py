"""BAT-36 — calibration tooling: ground-truth logger + replay/tune correlation.

Covers the two acceptance criteria that carry logic:

  * ``calibrate.py`` writes timestamped ground-truth entries and is safe to
    Ctrl-C and resume (append mode) — :class:`GroundTruthLog`.
  * ``calibrate_report.py`` correlates ground truth against what the fall machine
    actually did (TP/FP/FN/near_fall) and the threshold sweep reports FP/FN for a
    grid of ``stillness_threshold`` / ``confirm_stillness_secs`` candidates and
    recommends one — :func:`correlate` / :func:`sweep`.

Both tools live under ``tools/`` (next to ``packet_sim.py``); they are loaded via
importlib exactly like ``test_fall_scenarios`` loads the simulator, so the report
runs the *real* shipped fall machine over synthetic readings + ground truth.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
from pathlib import Path

from ingest import db
from ingest.detection import DetectionConfig

REPO_ROOT = Path(__file__).resolve().parents[3]


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: dataclass field-type resolution looks the module up
    # in sys.modules (it is None there otherwise under importlib).
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


CAL = _load("calibrate")
REPORT = _load("calibrate_report")


# -- synthetic readings -----------------------------------------------------


def _row(node_id, ts_ms, motion, fall, wall, presence=True):
    return REPORT.ReadingRow(
        node_id=node_id, ts=ts_ms, presence=presence, motion=motion,
        fall_flag=fall, wall=wall,
    )


def _fall_node(node_id, base_wall, *, still_motion):
    """A real fall: normal activity, an impact, then sustained stillness."""
    rows, ts = [], 0
    for _ in range(30):  # normal activity
        rows.append(_row(node_id, ts, 1500.0, False, base_wall + ts / 1000.0))
        ts += 1000
    rows.append(_row(node_id, ts, 9000.0, True, base_wall + ts / 1000.0))  # impact
    ts += 1000
    for _ in range(90):  # collapsed, still
        rows.append(_row(node_id, ts, still_motion, False, base_wall + ts / 1000.0))
        ts += 1000
    return rows


def _walk_node(node_id, base_wall):
    """A plain walk-through: motion, never a fall flag — no transitions at all."""
    rows, ts = [], 0
    for _ in range(40):
        rows.append(_row(node_id, ts, 1500.0, False, base_wall + ts / 1000.0))
        ts += 1000
    return rows


def _scenario():
    """Three nodes/rooms, one ground-truth event each.

    node 1 (living):  real fall              -> should CONFIRM (TP)
    node 2 (bathroom): deliberate sit-down   -> a fidget at motion 150: 'still'
        under the default 200 threshold (so it false-confirms, FP) but 'moving'
        under a 120 threshold (so it resolves as a near_fall — the quiet win).
    node 3 (kitchen): walk-through           -> never trips (TN)
    """
    readings = (
        _fall_node(1, 1000.0, still_motion=20.0)
        + _fall_node(2, 2000.0, still_motion=150.0)
        + _walk_node(3, 3000.0)
    )
    gt = [
        REPORT.GTEvent(ts=1000.0 + 30, event="fall", room="living"),
        REPORT.GTEvent(ts=2000.0 + 30, event="near_fall", room="bathroom"),
        REPORT.GTEvent(ts=3000.0 + 10, event="walk_through", room="kitchen"),
    ]
    config = DetectionConfig(rooms={1: "living", 2: "bathroom", 3: "kitchen"})
    return readings, gt, config


# -- ground-truth logger ----------------------------------------------------


def test_logger_writes_timestamped_entries(tmp_path):
    log = CAL.GroundTruthLog(tmp_path / "calibration_log.jsonl")
    entry = log.append("fall", "bathroom", note="crash mat", ts=1_700_000_000.0)
    assert entry == {
        "ts": 1_700_000_000.0, "event": "fall", "room": "bathroom",
        "note": "crash mat",
    }
    assert log.entries() == [entry]
    assert log.tally()["fall"] == 1


def test_logger_appends_and_resumes(tmp_path):
    path = tmp_path / "calibration_log.jsonl"
    first = CAL.GroundTruthLog(path)
    first.append("enter_room", "kitchen", ts=1.0)
    first.append("fall", "kitchen", ts=2.0)

    # A fresh logger over the same path (a resumed session after Ctrl-C) keeps
    # the earlier entries and adds to them — append mode, no truncation.
    resumed = CAL.GroundTruthLog(path)
    assert len(resumed.entries()) == 2
    assert resumed.tally()["enter_room"] == 1
    resumed.append("leave", "kitchen", ts=3.0)
    assert [e["event"] for e in CAL.GroundTruthLog(path).entries()] == [
        "enter_room", "fall", "leave",
    ]


def test_report_reads_logger_output(tmp_path):
    # The two tools agree on the on-disk format: what calibrate.py writes,
    # calibrate_report.py loads back (sorted by time).
    log = CAL.GroundTruthLog(tmp_path / "log.jsonl")
    log.append("fall", "bathroom", ts=20.0)
    log.append("walk_through", "kitchen", ts=10.0)
    events = REPORT.load_gt_log(tmp_path / "log.jsonl")
    assert [e.event for e in events] == ["walk_through", "fall"]  # time-sorted
    assert events[0].room == "kitchen"


# -- correlation ------------------------------------------------------------


def test_correlation_labels_each_ground_truth_event():
    readings, gt, config = _scenario()
    outcomes = REPORT.replay_outcomes(readings, config)
    verdicts = REPORT.correlate(gt, outcomes, config)
    labels = {v.gt.event: v.label for v in verdicts}

    # Real fall confirmed; the deliberate sit-down false-confirms under the
    # default threshold (the exact FP calibration day exists to find); the
    # walk-through stays quiet.
    assert labels["fall"] == REPORT.TP
    assert labels["near_fall"] == REPORT.FP
    assert labels["walk_through"] == REPORT.TN

    c = REPORT.counts(verdicts)
    assert (c[REPORT.TP], c[REPORT.FP], c[REPORT.FN]) == (1, 1, 0)


def test_confirmed_outcome_matches_real_fall_only():
    readings, gt, config = _scenario()
    outcomes = REPORT.replay_outcomes(readings, config)
    confirmed = [o for o in outcomes if o.kind == db.CONFIRMED_FALL]
    # The genuine fall (node 1) and the false-confirming sit-down (node 2) both
    # reach CONFIRMED_FALL under the default threshold; the walk-through (node 3)
    # never does.
    assert {o.node_id for o in confirmed} == {1, 2}


# -- threshold sweep --------------------------------------------------------


def test_sweep_reports_fp_fn_grid_and_recommends_a_fix():
    readings, gt, config = _scenario()
    results = REPORT.sweep(readings, gt, config)

    # Every grid combination is scored with FP/FN counts.
    grid_still, grid_confirm = REPORT.default_grid(config)
    assert len(results) == len(grid_still) * len(grid_confirm)
    assert all(hasattr(c, "fp") and hasattr(c, "fn") for c in results)

    baseline = next(c for c in results
                    if c.stillness_threshold == config.stillness_threshold
                    and c.confirm_stillness_secs == config.confirm_stillness_secs)
    assert (baseline.fp, baseline.fn) == (1, 0)  # the default leaves the FP in

    rec = REPORT.recommend(results)
    # Lowering the stillness threshold turns the sit-down's false-confirm into a
    # near_fall while keeping the real fall — so the recommendation clears the FP
    # without introducing a miss.
    assert rec.fp == 0
    assert rec.fn == 0
    assert rec.stillness_threshold < config.stillness_threshold

    diff = REPORT.diff_for(config, rec)
    keys = {k for k, _old, _new in diff}
    assert "stillness_threshold" in keys


def test_sweep_recovers_fall_under_every_candidate():
    # A miss (FN) is the worst error; confirm the real fall is never lost across
    # the swept grid (the recommendation is never paid for with a missed fall).
    readings, gt, config = _scenario()
    for c in REPORT.sweep(readings, gt, config):
        assert c.fn == 0


def test_lower_threshold_resolves_sit_down_as_near_fall():
    readings, gt, config = _scenario()
    tuned = replace(config, stillness_threshold=120.0)
    outcomes = REPORT.replay_outcomes(readings, tuned)
    verdicts = REPORT.correlate(gt, outcomes, tuned)
    labels = {v.gt.event: v.label for v in verdicts}
    assert labels["near_fall"] == REPORT.NEAR_FALL  # no longer a false confirm
    assert labels["fall"] == REPORT.TP              # real fall still caught


# -- the DB bridge (the "testable before hardware" join, PR #15 review #1) ----


def test_db_bridge_anchors_then_loads_readings(tmp_path):
    """anchors_from_transitions computes the per-node boot→wall median; an
    override wins for a transition-less node; load_readings re-stamps
    wall = anchor + ts/1000 and drops readings on unanchored nodes."""
    engine = db.make_engine(f"sqlite:///{tmp_path / 'cal.db'}")
    db.init_db(engine)
    sf = db.make_session_factory(engine)

    with sf() as s:
        # Node 1 produced two transitions. anchor = median(at - reading_ts/1000):
        #   1000.5 - 0.5 = 1000.0 ;  1002.5 - 1.5 = 1001.0  -> median 1000.5
        s.add(db.FallTransition(incident_id="i1", node_id=1, reading_ts=500, at=1000.5,
                                from_state="NORMAL", to_state="SUSPECTED",
                                kind="transition", reason=None, features=None))
        s.add(db.FallTransition(incident_id="i1", node_id=1, reading_ts=1500, at=1002.5,
                                from_state="SUSPECTED", to_state="PROBABLE",
                                kind="transition", reason=None, features=None))
        # Node 1 reading (anchorable); node 2 reading (no transition -> unanchored).
        s.add(db.Reading(node_id=1, ts=500, presence=True, motion=1500.0,
                         breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9,
                         fall_flag=False, occupancy=1))
        s.add(db.Reading(node_id=2, ts=2000, presence=True, motion=800.0,
                         breathing_bpm=14.0, hr_bpm=70.0, confidence=0.8,
                         fall_flag=False, occupancy=1))
        s.commit()

    assert REPORT.anchors_from_transitions(sf) == {1: 1000.5}  # median of the two

    # An --anchor override supplies the boot→wall for a transition-less node.
    overridden = REPORT.anchors_from_transitions(sf, overrides={2: 5000.0})
    assert overridden == {1: 1000.5, 2: 5000.0}

    # Only anchored nodes load; wall is re-stamped from the anchor.
    rows = REPORT.load_readings(sf, {1: 1000.5})
    assert [r.node_id for r in rows] == [1]              # node 2 dropped (no anchor)
    assert rows[0].wall == 1000.5 + 500 / 1000.0        # 1001.0

    both = REPORT.load_readings(sf, {1: 1000.5, 2: 5000.0})
    assert {r.node_id for r in both} == {1, 2}
    node2 = next(r for r in both if r.node_id == 2)
    assert node2.wall == 5000.0 + 2000 / 1000.0         # 5002.0


# -- false negatives: the verdict + the 5:1 cost (PR #15 review #2) -----------


def _missed_fall_node(node_id, base_wall, *, still_motion):
    """A real fall whose post-impact 'stillness' stays at ``still_motion`` — high
    enough to read as MOVING under a given threshold, so the machine never
    confirms it. The miss calibration exists to catch."""
    rows, ts = [], 0
    for _ in range(30):
        rows.append(_row(node_id, ts, 1500.0, False, base_wall + ts / 1000.0))
        ts += 1000
    rows.append(_row(node_id, ts, 9000.0, True, base_wall + ts / 1000.0))  # impact
    ts += 1000
    for _ in range(90):
        rows.append(_row(node_id, ts, still_motion, False, base_wall + ts / 1000.0))
        ts += 1000
    return rows


def test_missed_real_fall_is_labeled_fn():
    # Post-impact motion 500 stays above the default 200 stillness threshold, so
    # the machine reads "still moving" and never confirms — a missed real fall.
    readings = _missed_fall_node(1, 1000.0, still_motion=500.0)
    config = DetectionConfig(rooms={1: "living"})
    outcomes = REPORT.replay_outcomes(readings, config)
    assert not any(o.kind == db.CONFIRMED_FALL for o in outcomes)
    gt = [REPORT.GTEvent(ts=1030.0, event="fall", room="living")]
    verdict = REPORT.correlate(gt, outcomes, config)[0]
    assert verdict.label == REPORT.FN


def test_fn_scores_strictly_worse_than_an_equal_fp():
    """The 5:1 policy the recommendation hinges on: one missed fall costs more
    than one false alarm, so the sweep never trades a real miss for a cleared FP."""
    assert REPORT.SweepWeights().fn > REPORT.SweepWeights().fp
    readings, gt, config = _scenario()
    # At stillness 10 the real fall (still=20) reads as moving -> missed -> FN, fp=0.
    # At stillness 200 the sit-down (still=150) false-confirms -> FP, fn=0.
    results = REPORT.sweep(readings, gt, config,
                           stillness_grid=[10.0, 200.0], confirm_grid=[20])
    by = {(c.stillness_threshold, c.confirm_stillness_secs): c for c in results}
    miss, false_alarm = by[(10.0, 20)], by[(200.0, 20)]
    assert (miss.fn, miss.fp) == (1, 0)
    assert (false_alarm.fn, false_alarm.fp) == (0, 1)
    assert miss.score > false_alarm.score                 # FN costs strictly more
    assert REPORT.recommend(results) is false_alarm       # never recommend the miss
