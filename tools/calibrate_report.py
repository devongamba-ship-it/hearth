#!/usr/bin/env python3
"""Hearth calibration-day replay + threshold tuner (BAT-36).

Given the persisted readings DB and a ground-truth log (`tools/calibrate.py`),
correlate each ground-truth event against what the fall machine actually did and
sweep candidate ``detection.yaml`` thresholds to report the false-positive /
false-negative tradeoff. Prints per-event verdicts, a ranked candidate table,
and a recommended ``detection.yaml`` diff.

This works against ``packet_sim`` data today (so it is testable before any
hardware) and against real RuView node data on calibration day.

How time is bridged (HARDWARE ASSUMPTION — verify on the boards):
    Readings carry only ms-since-boot (``ts``); ground truth is wall-clock. The
    live fall machine persists each transition with BOTH its ``reading_ts`` and
    the wall-clock ``at`` it was recorded — so the persisted ``fall_transitions``
    give a per-node boot->wall anchor (``at - reading_ts/1000``). The replay
    re-stamps every reading with ``anchor + ts/1000`` to line readings up against
    ground truth. If a node produced no live transitions there is nothing to
    anchor it with; pass ``--anchor NODE=EPOCH`` (epoch of that node's ts=0).

Usage::

    python tools/calibrate_report.py \
        --db hearth.db --log calibration_log.jsonl --config detection.yaml
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
from dataclasses import dataclass, replace
from pathlib import Path

# Import the shipping fall machine + config so the sweep replays the *real*
# decision logic, never a re-implementation. `tools/` is at the repo root; the
# ingest package lives under apps/ingest (mirrors packet_sim.py adding the repo
# root for `hearth`).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "ingest"))

from ingest import db  # noqa: E402
from ingest.checkin import StubCheckInProvider  # noqa: E402
from ingest.detection import DetectionConfig, load_config  # noqa: E402
from ingest.escalator import NullEscalator  # noqa: E402
from ingest.fall_machine import FallMachine, Frame  # noqa: E402

# Ground-truth event vocabulary (kept in sync with tools/calibrate.py).
EXPECT_FALL = frozenset({"fall"})
EXPECT_NEAR_FALL = frozenset({"near_fall"})
EXPECT_NO_ALERT = frozenset(
    {"walk_through", "enter_room", "leave", "still_present", "out_of_home"})

# Verdict labels (the correctness call for one ground-truth event).
TP = "TP"            # real fall, machine confirmed — correct
FN = "FN"            # real fall, machine did NOT confirm — missed (the bad one)
FP = "FP"            # non-fall, machine confirmed — false alarm (the other bad one)
TN = "TN"            # non-fall, machine stayed quiet — correct
NEAR_FALL = "near_fall"  # non-fall, machine logged a near_fall — the quiet win
UNKNOWN = "UNKNOWN"  # ground-truth event name not in the vocabulary

# How strong an outcome each transition kind represents (pick the strongest in
# the window). Anything not listed (a plain SUSPECTED transition) is ignored.
_KIND_RANK = {db.CONFIRMED_FALL: 3, db.NEAR_FALL: 2, db.SUPPRESSED: 1}


@dataclass
class ReadingRow:
    """One persisted reading, plus the wall-clock it is bridged to for replay."""

    node_id: int
    ts: int  # ms since the node booted
    presence: bool
    motion: float
    fall_flag: bool
    breathing_bpm: float = 0.0
    hr_bpm: float = 0.0
    confidence: float = 0.0
    occupancy: int = 0
    wall: float = 0.0  # epoch secs == anchor[node] + ts/1000

    def frame(self) -> Frame:
        return Frame(
            node_id=self.node_id, ts=self.ts, presence=self.presence,
            motion=self.motion, fall_flag=self.fall_flag,
            breathing_bpm=self.breathing_bpm, hr_bpm=self.hr_bpm,
            confidence=self.confidence, occupancy=self.occupancy,
        )


@dataclass(frozen=True)
class GTEvent:
    """One ground-truth keypress from the calibration log."""

    ts: float  # wall-clock epoch secs
    event: str
    room: str = ""
    note: str = ""


@dataclass(frozen=True)
class Outcome:
    """What the (replayed) fall machine did, at a wall-clock instant."""

    node_id: int
    wall: float
    kind: str
    reason: str | None = None


@dataclass(frozen=True)
class EventVerdict:
    """A ground-truth event scored against the machine's nearest outcome."""

    gt: GTEvent
    outcome_kind: str  # confirmed_fall / near_fall / suppressed / "none"
    label: str         # TP / FP / FN / TN / near_fall / UNKNOWN
    reason: str | None = None


@dataclass(frozen=True)
class Window:
    """Asymmetric match window around a ground-truth keypress (seconds).

    A confirmed fall lands well *after* the impact keypress (post-burst stillness
    + the check-in timeout), so the window looks much further forward than back.
    """

    pre: float = 15.0
    post: float = 180.0


@dataclass(frozen=True)
class Candidate:
    """One swept threshold combination and its scored outcome counts."""

    stillness_threshold: float
    confirm_stillness_secs: int
    tp: int
    fp: int
    fn: int
    near_fall: int
    score: float


@dataclass(frozen=True)
class SweepWeights:
    """Cost of each error class. A missed real fall dwarfs a false alarm."""

    fn: float = 5.0
    fp: float = 1.0


# -- replay -----------------------------------------------------------------


def replay_outcomes(readings: list[ReadingRow], config: DetectionConfig) -> list[Outcome]:
    """Drive the real fall machine over ``readings`` and return its outcomes.

    Each reading is stepped in insert order; the machine's wall clock is pinned
    to that reading's bridged ``wall`` so every recorded transition's ``at`` is
    the wall-clock instant we can match against ground truth. Only the
    decision-bearing transition kinds (confirmed / near_fall / suppressed) are
    returned.
    """
    holder = {"t": 0.0}
    machine = FallMachine(
        config, escalator=NullEscalator(), checkin=StubCheckInProvider(),
        clock=lambda: holder["t"],
    )
    for r in readings:
        holder["t"] = r.wall
        machine.step(r.frame())
    return [
        Outcome(node_id=t.node_id, wall=t.at, kind=t.kind, reason=t.reason)
        for t in machine.transitions
        if t.kind in _KIND_RANK
    ]


# -- correlation ------------------------------------------------------------


def _rooms_to_nodes(config: DetectionConfig) -> dict[str, set[int]]:
    out: dict[str, set[int]] = {}
    for node_id, room in config.rooms.items():
        out.setdefault(room.strip().lower(), set()).add(node_id)
    return out


def _outcome_for(gt: GTEvent, outcomes: list[Outcome], node_ids: set[int] | None,
                 window: Window) -> Outcome | None:
    """Strongest machine outcome within ``window`` of ``gt`` (same node if known)."""
    lo, hi = gt.ts - window.pre, gt.ts + window.post
    in_window = [
        o for o in outcomes
        if lo <= o.wall <= hi and (node_ids is None or o.node_id in node_ids)
    ]
    if not in_window:
        return None
    return max(in_window, key=lambda o: (_KIND_RANK[o.kind], -abs(o.wall - gt.ts)))


def _label(event: str, outcome_kind: str) -> str:
    confirmed = outcome_kind == db.CONFIRMED_FALL
    if event in EXPECT_FALL:
        return TP if confirmed else FN
    if event in EXPECT_NEAR_FALL or event in EXPECT_NO_ALERT:
        if confirmed:
            return FP
        return NEAR_FALL if outcome_kind == db.NEAR_FALL else TN
    return UNKNOWN


def correlate(gt_events: list[GTEvent], outcomes: list[Outcome],
              config: DetectionConfig, window: Window | None = None) -> list[EventVerdict]:
    """Score every ground-truth event against the nearest machine outcome."""
    window = window or Window()
    rooms = _rooms_to_nodes(config)
    verdicts: list[EventVerdict] = []
    for gt in gt_events:
        node_ids = rooms.get(gt.room.strip().lower())
        outcome = _outcome_for(gt, outcomes, node_ids, window)
        kind = outcome.kind if outcome else "none"
        verdicts.append(EventVerdict(
            gt=gt, outcome_kind=kind, label=_label(gt.event, kind),
            reason=outcome.reason if outcome else None,
        ))
    return verdicts


def counts(verdicts: list[EventVerdict]) -> dict[str, int]:
    """Tally verdict labels (TP/FP/FN/TN/near_fall/UNKNOWN)."""
    out = {TP: 0, FP: 0, FN: 0, TN: 0, NEAR_FALL: 0, UNKNOWN: 0}
    for v in verdicts:
        out[v.label] = out.get(v.label, 0) + 1
    return out


# -- threshold sweep --------------------------------------------------------


def default_grid(config: DetectionConfig) -> tuple[list[float], list[int]]:
    """Candidate grid centred near the current config (and the quiet-hours floor)."""
    stillness = sorted({120.0, 160.0, 200.0, 240.0, 300.0, config.stillness_threshold})
    confirm = sorted({10, 15, 20, 25, config.confirm_stillness_secs})
    return stillness, confirm


def sweep(readings: list[ReadingRow], gt_events: list[GTEvent], config: DetectionConfig,
          *, stillness_grid: list[float] | None = None,
          confirm_grid: list[int] | None = None, window: Window | None = None,
          weights: SweepWeights | None = None) -> list[Candidate]:
    """Replay every (stillness_threshold, confirm_stillness_secs) combo and rank it.

    Lower score is better; ties break toward fewer misses, then fewer false
    alarms, then the candidate closest to the current stillness threshold (favour
    a small, stable change over a large one).
    """
    weights = weights or SweepWeights()
    grid_still, grid_confirm = (stillness_grid, confirm_grid)
    if grid_still is None or grid_confirm is None:
        ds, dc = default_grid(config)
        grid_still = grid_still or ds
        grid_confirm = grid_confirm or dc
    results: list[Candidate] = []
    for st in grid_still:
        for cs in grid_confirm:
            cfg = replace(config, stillness_threshold=st, confirm_stillness_secs=cs)
            outcomes = replay_outcomes(readings, cfg)
            c = counts(correlate(gt_events, outcomes, cfg, window))
            results.append(Candidate(
                stillness_threshold=st, confirm_stillness_secs=cs,
                tp=c[TP], fp=c[FP], fn=c[FN], near_fall=c[NEAR_FALL],
                score=weights.fn * c[FN] + weights.fp * c[FP],
            ))
    results.sort(key=lambda c: (
        c.score, c.fn, c.fp,
        # Stability tie-break: among equally-good candidates, prefer the smallest
        # change from the current config — and "no change" over any change — so a
        # clean run recommends keeping the thresholds rather than churning them.
        abs(c.stillness_threshold - config.stillness_threshold),
        abs(c.confirm_stillness_secs - config.confirm_stillness_secs),
    ))
    return results


def recommend(results: list[Candidate]) -> Candidate | None:
    return results[0] if results else None


def diff_for(config: DetectionConfig, rec: Candidate) -> list[tuple[str, object, object]]:
    """(key, old, new) for each threshold the recommendation would change."""
    changes: list[tuple[str, object, object]] = []
    if rec.stillness_threshold != config.stillness_threshold:
        changes.append(
            ("stillness_threshold", config.stillness_threshold, rec.stillness_threshold))
    if rec.confirm_stillness_secs != config.confirm_stillness_secs:
        changes.append(
            ("confirm_stillness_secs", config.confirm_stillness_secs,
             rec.confirm_stillness_secs))
    return changes


# -- DB / log loading -------------------------------------------------------


def load_gt_log(path: str | os.PathLike) -> list[GTEvent]:
    """Parse a ground-truth JSONL log into sorted :class:`GTEvent` records."""
    events: list[GTEvent] = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        events.append(GTEvent(
            ts=float(d["ts"]), event=str(d["event"]),
            room=str(d.get("room", "")), note=str(d.get("note", "")),
        ))
    events.sort(key=lambda e: e.ts)
    return events


def anchors_from_transitions(session_factory,
                             overrides: dict[int, float] | None = None) -> dict[int, float]:
    """Per-node boot->wall anchor from persisted transitions: median(at - ts/1000).

    ``overrides`` (from ``--anchor NODE=EPOCH``) win, for nodes that produced no
    live transitions to anchor from.
    """
    from sqlalchemy import select

    per_node: dict[int, list[float]] = {}
    with session_factory() as session:
        rows = session.execute(
            select(db.FallTransition.node_id, db.FallTransition.reading_ts,
                   db.FallTransition.at)
        ).all()
    for node_id, reading_ts, at in rows:
        per_node.setdefault(node_id, []).append(at - reading_ts / 1000.0)
    anchors = {node: statistics.median(vals) for node, vals in per_node.items()}
    anchors.update(overrides or {})
    return anchors


def load_readings(session_factory, anchors: dict[int, float]) -> list[ReadingRow]:
    """Load readings in insert order, bridging each to wall time via ``anchors``.

    Readings on a node with no anchor are dropped (they can't be aligned to
    ground truth); the count is reported by the caller so nothing is silently
    lost.
    """
    from sqlalchemy import select

    out: list[ReadingRow] = []
    with session_factory() as session:
        rows = session.execute(select(db.Reading).order_by(db.Reading.id)).scalars().all()
    for r in rows:
        if r.node_id not in anchors:
            continue
        out.append(ReadingRow(
            node_id=r.node_id, ts=r.ts, presence=r.presence, motion=r.motion,
            fall_flag=r.fall_flag, breathing_bpm=r.breathing_bpm, hr_bpm=r.hr_bpm,
            confidence=r.confidence, occupancy=r.occupancy,
            wall=anchors[r.node_id] + r.ts / 1000.0,
        ))
    return out


# -- reporting --------------------------------------------------------------


def _fmt_verdicts(verdicts: list[EventVerdict]) -> str:  # pragma: no cover - formatting
    lines = ["ground-truth verdicts (event -> machine outcome = label):"]
    for v in verdicts:
        reason = f" [{v.reason}]" if v.reason else ""
        lines.append(
            f"  {v.gt.ts:>14.0f}  {v.gt.event:<13} {v.gt.room:<10} "
            f"-> {v.outcome_kind:<14}{reason} = {v.label}")
    c = counts(verdicts)
    lines.append(f"  totals: TP={c[TP]} FP={c[FP]} FN={c[FN]} TN={c[TN]} "
                 f"near_fall={c[NEAR_FALL]} unknown={c[UNKNOWN]}")
    return "\n".join(lines)


def _fmt_sweep(results: list[Candidate]) -> str:  # pragma: no cover - formatting
    lines = ["threshold sweep (ranked best-first):",
             "  rank  stillness  confirm_secs   TP  FP  FN  near  score"]
    for i, c in enumerate(results, start=1):
        lines.append(
            f"  {i:>4}  {c.stillness_threshold:>9.0f}  {c.confirm_stillness_secs:>12}"
            f"  {c.tp:>3} {c.fp:>3} {c.fn:>3} {c.near_fall:>5}  {c.score:>6.1f}")
    return "\n".join(lines)


def _fmt_diff(config: DetectionConfig, rec: Candidate) -> str:  # pragma: no cover
    changes = diff_for(config, rec)
    if not changes:
        lines = ["recommended detection.yaml: no change — current thresholds win."]
    else:
        lines = ["recommended detection.yaml diff:"]
        for key, old, new in changes:
            lines.append(f"  - {key}: {old}")
            lines.append(f"  + {key}: {new}")
        lines.append("# apply by editing detection.yaml with the + values above.")
    return "\n".join(lines)


def main() -> None:  # pragma: no cover - operational entrypoint
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=os.environ.get("HEARTH_DB_PATH", "hearth.db"),
                    help="SQLite readings DB path")
    ap.add_argument("--log", default="calibration_log.jsonl",
                    help="ground-truth JSONL log path")
    ap.add_argument("--config", default=os.environ.get("HEARTH_DETECTION_FILE"),
                    help="detection.yaml to use as the baseline (defaults built in)")
    ap.add_argument("--anchor", action="append", default=[],
                    metavar="NODE=EPOCH",
                    help="boot->wall anchor for a node with no live transitions "
                         "(epoch secs of that node's ts=0); repeatable")
    ap.add_argument("--pre", type=float, default=Window.pre,
                    help="seconds before a keypress to match an outcome")
    ap.add_argument("--post", type=float, default=Window.post,
                    help="seconds after a keypress to match an outcome")
    args = ap.parse_args()

    config = load_config(args.config)
    window = Window(pre=args.pre, post=args.post)
    overrides: dict[int, float] = {}
    for spec in args.anchor:
        node_s, _, epoch_s = spec.partition("=")
        overrides[int(node_s)] = float(epoch_s)

    sessions = db.make_session_factory(db.make_engine(f"sqlite:///{args.db}"))
    anchors = anchors_from_transitions(sessions, overrides)
    if not anchors:
        raise SystemExit(
            "no boot->wall anchor available: the DB has no fall_transitions and "
            "no --anchor was given. Run the live fall machine during calibration, "
            "or pass --anchor NODE=EPOCH.")
    readings = load_readings(sessions, anchors)
    gt_events = load_gt_log(args.log)

    baseline_outcomes = replay_outcomes(readings, config)
    verdicts = correlate(gt_events, baseline_outcomes, config, window)
    results = sweep(readings, gt_events, config, window=window)
    rec = recommend(results)

    print(f"[calibrate_report] anchors={ {k: round(v, 1) for k, v in anchors.items()} } "
          f"readings={len(readings)} ground_truth={len(gt_events)}")
    print(_fmt_verdicts(verdicts))
    print()
    print(_fmt_sweep(results))
    print()
    if rec is not None:
        print(_fmt_diff(config, rec))


if __name__ == "__main__":  # pragma: no cover
    main()
