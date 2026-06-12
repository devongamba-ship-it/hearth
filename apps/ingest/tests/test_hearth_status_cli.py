"""BAT-37: tools/hearth_status.py renders the /health payload readably."""

from __future__ import annotations

import sys
import urllib.error
from pathlib import Path

# The CLI lives at the repo root under tools/, outside the installed packages.
_TOOLS = Path(__file__).resolve().parents[3] / "tools"
sys.path.insert(0, str(_TOOLS))

import hearth_status  # noqa: E402


SAMPLE = {
    "status": "degraded",
    "service": "ingest",
    "version": "0.1.0",
    "nodes": [
        {"node_id": 1, "room": "Living Room", "reading_count": 1843,
         "last_seen_age_secs": 2.0, "confidence": 0.88, "online": True, "stale": False},
        {"node_id": 2, "room": "Kitchen", "reading_count": 920,
         "last_seen_age_secs": 95.0, "confidence": 0.71, "online": False, "stale": True},
        {"node_id": 3, "room": "Bedroom", "reading_count": 0,
         "last_seen_age_secs": None, "confidence": None, "online": False, "stale": True},
    ],
    "aggregate": {
        "home_id": "rosewood", "nodes_online": 1, "nodes_expected": 3,
        "nodes_stale": 2, "dropout_window_secs": 30.0, "write_rate_per_min": 58.0,
        "uptime_secs": 3725.0, "active_incidents": 0,
    },
}


def test_render_shows_verdict_nodes_and_aggregate():
    out = hearth_status.render(SAMPLE)
    assert "DEGRADED" in out
    assert "home=rosewood" in out
    assert "nodes online 1/3" in out
    assert "Living Room" in out and "Kitchen" in out and "Bedroom" in out
    # The never-seen node renders its missing age/confidence as "-", not a crash.
    assert "online" in out and "STALE" in out


def test_fmt_age_humanizes():
    assert hearth_status._fmt_age(None) == "-"
    assert hearth_status._fmt_age(8) == "8s"
    assert hearth_status._fmt_age(95) == "1m35s"
    assert hearth_status._fmt_age(3725) == "1h02m"


def test_main_exit_code_tracks_status(monkeypatch, capsys):
    monkeypatch.setattr(hearth_status, "fetch", lambda *a, **k: SAMPLE)
    code = hearth_status.main(["--url", "http://x"])
    assert code == 1  # degraded
    assert "DEGRADED" in capsys.readouterr().out


def test_main_raw_passthrough(monkeypatch, capsys):
    ok = {**SAMPLE, "status": "ok"}
    monkeypatch.setattr(hearth_status, "fetch", lambda *a, **k: ok)
    assert hearth_status.main(["--raw"]) == 0
    assert '"status": "ok"' in capsys.readouterr().out


def test_main_unreachable_is_exit_2(monkeypatch, capsys):
    def boom(*a, **k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(hearth_status, "fetch", boom)
    assert hearth_status.main(["--url", "http://nope"]) == 2
    assert "could not reach" in capsys.readouterr().err
