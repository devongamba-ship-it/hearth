#!/usr/bin/env python3
"""Hearth calibration-day ground-truth logger (BAT-36).

Runs alongside the live ingest while Devon walks the rooms doing scripted
actions. Each keypress stamps a timestamped ground-truth event into
``calibration_log.jsonl`` so the readings the nodes actually produced can later
be tuned against *truth* (`tools/calibrate_report.py`) instead of vibes.

Each entry is one JSON line::

    {"ts": <wall-clock epoch secs>, "event": "fall", "room": "bathroom", "note": ""}

The file is opened in **append mode** and flushed per line, so the session is
safe to Ctrl-C and resume: re-running against the same log keeps the earlier
entries and seeds the running tally from them.

Usage::

    python tools/calibrate.py --log calibration_log.jsonl \
        --rooms kitchen,living,bathroom,bedroom

Then follow the on-screen key list / scripted routine. This tool has no network
or sensing side effects — it only appends ground-truth keypresses to a file.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

# Canonical ground-truth event vocabulary. The single key Devon presses maps to
# one of these stable event names; `tools/calibrate_report.py` scores each name
# against what the fall machine actually did. Keep the two in sync.
EVENT_KEYS: dict[str, str] = {
    "f": "fall",            # a real (controlled) fall onto the crash mat
    "n": "near_fall",       # sat or lay down deliberately — should NOT confirm
    "w": "walk_through",    # walked across the room
    "e": "enter_room",      # entered the room
    "l": "leave",           # left the room
    "s": "still_present",   # present but still (sitting/reading)
    "o": "out_of_home",     # left the home entirely
}

# A suggested scripted routine printed at startup. Devon advances it with SPACE;
# it is guidance only — every event is logged from the keypress, not the step.
DEFAULT_ROUTINE: tuple[str, ...] = (
    "enter kitchen, potter about (press e, then w)",
    "walk kitchen -> living (press l, then e)",
    "sit still in living for ~30s (press s)",
    "controlled near-fall onto sofa (press n)",
    "walk to bathroom (press l, then e)",
    "controlled FALL onto crash mat in bathroom (press f)",
    "lie still ~90s so the check-in window elapses",
    "get up, walk out (press l)",
    "leave the home (press o)",
)


@dataclass
class GroundTruthLog:
    """Append-only JSONL ground-truth log; the testable core of the logger.

    Every :meth:`append` writes one self-contained JSON line and flushes, so an
    interrupted session loses nothing and :meth:`entries` / :meth:`tally` can
    resume from whatever is already on disk.
    """

    path: Path

    def append(self, event: str, room: str, note: str = "",
               ts: float | None = None) -> dict:
        """Append one ground-truth entry and return it.

        ``ts`` defaults to the current wall clock; tests pass it for determinism.
        """
        entry = {
            "ts": time.time() if ts is None else ts,
            "event": event,
            "room": room,
            "note": note,
        }
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
            fh.flush()
        return entry

    def entries(self) -> list[dict]:
        """Every entry currently on disk (empty if the log does not exist yet)."""
        path = Path(self.path)
        if not path.exists():
            return []
        out: list[dict] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                out.append(json.loads(line))
        return out

    def tally(self) -> Counter:
        """Running per-event counts across everything logged so far."""
        return Counter(e["event"] for e in self.entries())


def _read_key() -> str:  # pragma: no cover - needs a real TTY
    """Read a single keypress without waiting for Enter (POSIX raw mode).

    Falls back to a line read on platforms / pipes without termios so the tool
    still functions (one event per Enter) when stdin is not a raw-capable TTY.
    """
    try:
        import termios
        import tty
    except ImportError:
        return sys.stdin.readline().strip()[:1]
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    return ch


def _print_help(rooms: list[str], room: str) -> None:  # pragma: no cover - I/O
    print("\nHearth calibration ground-truth logger")
    print("  event keys:")
    for key, event in EVENT_KEYS.items():
        print(f"    {key} -> {event}")
    print("  rooms (digit selects current room):")
    for i, r in enumerate(rooms, start=1):
        marker = " *" if r == room else ""
        print(f"    {i} -> {r}{marker}")
    print("  SPACE -> next routine step   ?-> this help   q -> quit\n")


def _run_interactive(log: GroundTruthLog, rooms: list[str],
                     routine: tuple[str, ...]) -> None:  # pragma: no cover - TTY
    room = rooms[0] if rooms else "unknown"
    tally = log.tally()
    if tally:
        print(f"[calibrate] resuming — {sum(tally.values())} prior entries: "
              f"{dict(tally)}")
    _print_help(rooms, room)
    step = 0
    if routine:
        print(f"now: {routine[step]}")
    while True:
        ch = _read_key()
        if ch in ("q", "\x03"):  # q or Ctrl-C
            print("\n[calibrate] done.")
            return
        if ch == "?":
            _print_help(rooms, room)
            continue
        if ch == " ":
            step = min(step + 1, len(routine) - 1) if routine else 0
            if routine:
                print(f"now: {routine[step]}")
            continue
        if ch.isdigit() and rooms:
            idx = int(ch) - 1
            if 0 <= idx < len(rooms):
                room = rooms[idx]
                print(f"[calibrate] current room -> {room}")
            continue
        event = EVENT_KEYS.get(ch)
        if event is None:
            continue
        entry = log.append(event, room)
        tally[event] += 1
        print(f"[calibrate] +{event} room={room} ts={entry['ts']:.0f}  "
              f"tally={dict(tally)}")


def main() -> None:  # pragma: no cover - operational entrypoint
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--log", default="calibration_log.jsonl",
                    help="ground-truth JSONL path (append mode; resumes)")
    ap.add_argument("--rooms", default="kitchen,living,bathroom,bedroom",
                    help="comma-separated room names selectable with digit keys")
    args = ap.parse_args()
    rooms = [r.strip() for r in args.rooms.split(",") if r.strip()]
    log = GroundTruthLog(Path(args.log))
    try:
        _run_interactive(log, rooms, DEFAULT_ROUTINE)
    except KeyboardInterrupt:
        print("\n[calibrate] interrupted — log saved, safe to resume.")


if __name__ == "__main__":  # pragma: no cover
    main()
