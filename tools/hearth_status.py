#!/usr/bin/env python3
"""Pretty-print the ingest service's ``/health`` (BAT-37).

Flash-day tool: point it at a running ingest service and eyeball whether every
RuView node is reporting, how fresh each one is, and the overall verdict —
without SSHing into the Pi or reading raw JSON.

Usage:
  python tools/hearth_status.py                         # http://127.0.0.1:8000
  python tools/hearth_status.py --url http://pi.local:8000
  HEARTH_INGEST_URL=http://pi.local:8000 python tools/hearth_status.py
  python tools/hearth_status.py --raw                   # dump the JSON instead

Exit code mirrors the verdict so it composes in scripts: 0 ``ok``,
1 ``degraded``, 2 ``down`` (or unreachable).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
_EXIT_CODES = {"ok": 0, "degraded": 1, "down": 2}


def fetch(base_url: str, timeout: float = 5.0) -> dict:
    """GET ``{base_url}/health`` and parse the JSON body."""
    url = base_url.rstrip("/") + "/health"
    with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 - operator-supplied URL
        return json.loads(resp.read().decode("utf-8"))


def _fmt_age(secs) -> str:
    """A compact human age: ``-`` (never), ``8s``, ``3m``, ``1h2m``."""
    if secs is None:
        return "-"
    secs = int(round(secs))
    if secs < 60:
        return f"{secs}s"
    if secs < 3600:
        return f"{secs // 60}m{secs % 60:02d}s"
    return f"{secs // 3600}h{(secs % 3600) // 60:02d}m"


def _fmt_conf(conf) -> str:
    return "-" if conf is None else f"{conf:.2f}"


def render(health: dict) -> str:
    """Render the health payload as an aligned, human-readable report."""
    status = str(health.get("status", "unknown")).upper()
    agg = health.get("aggregate", {})
    nodes = health.get("nodes", [])

    lines: list[str] = []
    home = agg.get("home_id") or "—"
    lines.append(f"Hearth ingest — {status}   (home={home}, v{health.get('version', '?')})")
    lines.append(
        f"  nodes online {agg.get('nodes_online', 0)}/{agg.get('nodes_expected', 0)}"
        f"   stale {agg.get('nodes_stale', 0)}"
        f"   write {agg.get('write_rate_per_min', 0)}/min"
        f"   active incidents {agg.get('active_incidents', 0)}"
        f"   uptime {_fmt_age(agg.get('uptime_secs'))}"
    )
    lines.append("")

    if not nodes:
        lines.append("  (no nodes have reported yet)")
        return "\n".join(lines)

    header = f"  {'NODE':>4}  {'ROOM':<16}  {'READINGS':>8}  {'LAST SEEN':>9}  {'CONF':>5}  STATE"
    lines.append(header)
    for n in nodes:
        state = "online" if n.get("online") else "STALE"
        room = (n.get("room") or "—")[:16]
        lines.append(
            f"  {n.get('node_id'):>4}  {room:<16}  {n.get('reading_count', 0):>8}"
            f"  {_fmt_age(n.get('last_seen_age_secs')):>9}  {_fmt_conf(n.get('confidence')):>5}  {state}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pretty-print the ingest /health endpoint.")
    parser.add_argument(
        "--url",
        default=os.environ.get("HEARTH_INGEST_URL", DEFAULT_BASE_URL),
        help="Base URL of the ingest service (default: %(default)s).",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout (s).")
    parser.add_argument("--raw", action="store_true", help="Print the raw JSON payload.")
    args = parser.parse_args(argv)

    try:
        health = fetch(args.url, timeout=args.timeout)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        print(f"hearth_status: could not reach {args.url}: {exc}", file=sys.stderr)
        return 2

    if args.raw:
        print(json.dumps(health, indent=2))
    else:
        print(render(health))
    return _EXIT_CODES.get(str(health.get("status")), 2)


if __name__ == "__main__":
    raise SystemExit(main())
