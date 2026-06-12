#!/usr/bin/env python3
"""Hearth mesh relocation self-heal check (BAT-44).

A product meant to run untouched for years still has nodes that get unplugged,
moved to a new outlet, or bumped to a different room. This read-only CLI compares
the mesh links a home's ``layout.yaml`` *says* it has against the adjacency the
nodes' own data reveals, and flags any node whose neighbours drifted ("node 3's
neighbours changed — relocated?").

It **suggests, never rewrites**: the operator decides whether to update
``layout.yaml``. Topology only, pure read of the local DB — nothing leaves the
device (CLAUDE.md: local-first, privacy is the brand).

Usage::

    python tools/mesh_check.py --layout layout.yaml --db sqlite:///hearth.db

The testable core (:func:`check` / :func:`render_report`) takes already-loaded
objects so CI drives it without a TTY or a real file.
"""

from __future__ import annotations

import argparse

from ingest.mesh_graph import (
    DEFAULT_LINK_CONFIDENCE,
    NodeDrift,
    describe_drift,
    describe_graph,
    detect_drift,
    discover_graph,
)


def check(
    layout,
    session_factory,
    *,
    window_secs: float = 2.0,
    min_confidence: float = DEFAULT_LINK_CONFIDENCE,
) -> tuple[object, list[NodeDrift]]:
    """Discover the mesh from the DB and diff it against ``layout.links``.

    Returns ``(graph, drifts)``. Pure read — opens one session, runs no writes.
    """
    with session_factory() as session:
        graph = discover_graph(session, window_secs=window_secs)
    drifts = detect_drift(layout.links, graph, min_confidence=min_confidence)
    return graph, drifts


def render_report(graph, drifts: list[NodeDrift]) -> str:
    """Human-readable report: discovered adjacencies, then the drift verdict."""
    return (
        "Discovered mesh:\n"
        + describe_graph(graph)
        + "\n\nRelocation check:\n"
        + describe_drift(drifts)
        + "\n\n(read-only — layout.yaml is never modified; update it yourself if this looks right)"
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - entrypoint
    from ingest.db import make_engine, make_session_factory
    from ingest.layout import load_layout

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--layout", required=True, help="path to the home's layout.yaml")
    ap.add_argument("--db", default=None, help="readings DB URL (default: $HEARTH_DB_URL / local file)")
    ap.add_argument("--window-secs", type=float, default=2.0, help="co-activation window")
    args = ap.parse_args(argv)

    layout = load_layout(args.layout)
    if layout is None:
        print("[mesh_check] no layout configured — nothing to check against")
        return 1
    factory = make_session_factory(make_engine(args.db))
    graph, drifts = check(layout, factory, window_secs=args.window_secs)
    print(render_report(graph, drifts))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
