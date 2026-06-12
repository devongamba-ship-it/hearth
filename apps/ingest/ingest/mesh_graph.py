"""Mesh link auto-discovery (BAT-44) — confirm-not-author onboarding.

Onboarding a home should be "confirm what the mesh discovered," not "draw the
node graph from scratch." This module builds the node **adjacency graph** from
observed data and derives suggested ``layout.yaml`` links, so the operator nudges
a pre-filled topology instead of authoring the ring blind. It also flags when a
node looks relocated (its neighbours drifted from what was configured).

Topology only — never geometry. The graph is node ids + 0..1 link weights; no
coordinates, no dimensions, no floor plan. Pure read of the local DB; nothing
leaves the device (CLAUDE.md: local-first, privacy is the brand).

Two sources, in priority order:

1. **Neighbor reports (forward-compatible, source stubbed).** A node reports
   ``{node_id, neighbors: [{node_id, rssi}]}``; rows land in the ``link_reports``
   table (:class:`ingest.db.LinkReport`). When any are present, measured link
   strength (RSSI → weight) wins. v1 ships the contract + persistence; no
   firmware emits them yet, so in practice the table is empty and we fall back to:

2. **Co-activation inference (buildable + testable today).** A person walking
   kitchen→living lights adjacent nodes in sequence, so *frequent co-activation
   ⇒ adjacency* (the "a labeled walk traces the topology" insight from
   calibration day, BAT-36). Two nodes whose motion fires within ``window_secs``
   of each other are co-active; the normalised co-active fraction is the link
   weight. Deterministic and pure given a session + the readings' own clock.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from sqlalchemy import select

from .db import LinkReport, Reading
from .detection import DetectionConfig

# Default link-weight cutoff for calling a pair "adjacent" (a suggested link) and
# for a node's neighbour set in drift detection. Co-activation weights cluster
# near 1.0 for genuinely adjacent nodes and near 0.0 for the rest, so the midpoint
# separates cleanly while leaving margin for noise.
DEFAULT_LINK_CONFIDENCE = 0.5

# RSSI (dBm) window mapped onto a 0..1 link weight for the neighbor-report path.
# -30 dBm (very strong, adjacent) → 1.0; -90 dBm (at the noise floor) → 0.0.
RSSI_STRONG_DBM = -30.0
RSSI_FLOOR_DBM = -90.0


def _ordered(a: int, b: int) -> tuple[int, int]:
    """An unordered node pair as a canonical ``(low, high)`` tuple."""
    return (a, b) if a <= b else (b, a)


@dataclass(frozen=True)
class MeshGraph:
    """A discovered mesh: node ids, weighted undirected edges, suggested links.

    Topology only. ``edges`` is sorted ``(a, b, weight)`` triples with ``a < b``
    and ``weight`` in 0..1; ``suggested_links`` is the subset at/above
    :data:`DEFAULT_LINK_CONFIDENCE`, the pre-fill an operator confirms. ``source``
    records which inference produced it (``"co_activation"`` / ``"link_rssi"``).
    """

    nodes: tuple[int, ...]
    edges: tuple[tuple[int, int, float], ...]
    suggested_links: tuple[tuple[int, int], ...]
    source: str = "co_activation"

    def weight(self, a: int, b: int) -> float:
        """Link weight for an unordered node pair, or 0.0 if they never co-fire."""
        lo, hi = _ordered(a, b)
        for ea, eb, w in self.edges:
            if ea == lo and eb == hi:
                return w
        return 0.0

    def neighbors(self, node: int, *, min_confidence: float = DEFAULT_LINK_CONFIDENCE) -> tuple[int, ...]:
        """Node ids linked to ``node`` at/above ``min_confidence`` (sorted)."""
        out = []
        for a, b, w in self.edges:
            if w < min_confidence:
                continue
            if a == node:
                out.append(b)
            elif b == node:
                out.append(a)
        return tuple(sorted(out))

    def is_isolated(self, node: int, *, min_confidence: float = DEFAULT_LINK_CONFIDENCE) -> bool:
        """True if ``node`` has no neighbour at/above ``min_confidence``."""
        return not self.neighbors(node, min_confidence=min_confidence)

    def strongest_links(
        self,
        *,
        min_confidence: float = DEFAULT_LINK_CONFIDENCE,
        among: list[int] | None = None,
    ) -> list[tuple[int, int]]:
        """Undirected ``(a, b)`` links at/above ``min_confidence``.

        ``among`` restricts to links whose endpoints are both in the given node
        set (the onboarding step passes the operator's node ids so a stray node
        in the readings DB can't inject an edge the layout doesn't know about).
        """
        allowed = set(among) if among is not None else None
        out = []
        for a, b, w in self.edges:
            if w < min_confidence:
                continue
            if allowed is not None and (a not in allowed or b not in allowed):
                continue
            out.append((a, b))
        return out


# --- neighbor-report path (forward-compatible contract; source stubbed) ------
@dataclass(frozen=True)
class NeighborLink:
    """One entry of a node's neighbor report: a heard node and its link RSSI."""

    node_id: int
    rssi: float


@dataclass(frozen=True)
class NeighborReport:
    """A single node's neighbor report — the ``link_rssi`` contract.

    Wire shape (future RuView packet / ``POST /link-reports`` endpoint, both
    stubbed in v1)::

        {"node_id": 1, "neighbors": [{"node_id": 4, "rssi": -47}, ...]}
    """

    node_id: int
    neighbors: tuple[NeighborLink, ...]


def rssi_to_weight(rssi: float) -> float:
    """Map a link RSSI (dBm) onto a 0..1 weight, clamped to the usable window."""
    span = RSSI_STRONG_DBM - RSSI_FLOOR_DBM
    frac = (rssi - RSSI_FLOOR_DBM) / span
    return max(0.0, min(1.0, frac))


def graph_from_reports(
    reports: list[NeighborReport],
    *,
    min_confidence: float = DEFAULT_LINK_CONFIDENCE,
) -> MeshGraph:
    """Build a :class:`MeshGraph` from neighbor reports (measured link strength).

    A link's weight is the mean of whatever directional RSSIs were reported for
    it (a→b and/or b→a), so a one-sided report still yields an edge.
    """
    nodes: set[int] = set()
    directional: dict[tuple[int, int], list[float]] = {}
    for report in reports:
        nodes.add(report.node_id)
        for link in report.neighbors:
            nodes.add(link.node_id)
            if link.node_id == report.node_id:
                continue
            directional.setdefault(_ordered(report.node_id, link.node_id), []).append(
                rssi_to_weight(link.rssi)
            )
    edges = tuple(
        sorted((a, b, round(sum(ws) / len(ws), 4)) for (a, b), ws in directional.items())
    )
    suggested = tuple((a, b) for a, b, w in edges if w >= min_confidence)
    return MeshGraph(
        nodes=tuple(sorted(nodes)),
        edges=edges,
        suggested_links=suggested,
        source="link_rssi",
    )


def _load_neighbor_reports(session) -> list[NeighborReport]:
    """Read persisted ``link_reports`` rows into :class:`NeighborReport` objects."""
    rows = session.execute(
        select(LinkReport.node_id, LinkReport.neighbor_id, LinkReport.rssi)
    ).all()
    by_node: dict[int, list[NeighborLink]] = {}
    for node_id, neighbor_id, rssi in rows:
        by_node.setdefault(node_id, []).append(NeighborLink(node_id=neighbor_id, rssi=rssi))
    return [
        NeighborReport(node_id=nid, neighbors=tuple(links))
        for nid, links in sorted(by_node.items())
    ]


def persist_neighbor_reports(
    session_factory,
    reports: list[NeighborReport],
    *,
    received_at: float | None = None,
) -> int:
    """Persist neighbor reports into ``link_reports``; return rows written.

    The "accept + persist if present" half of the contract. No firmware emits
    reports in v1, so this is exercised by tests and a future ingest endpoint.
    """
    written = 0
    with session_factory() as session:
        for report in reports:
            for link in report.neighbors:
                session.add(
                    LinkReport(
                        node_id=report.node_id,
                        neighbor_id=link.node_id,
                        rssi=link.rssi,
                        received_at=received_at,
                    )
                )
                written += 1
        session.commit()
    return written


# --- co-activation inference (the v1 buildable path) -------------------------
def _firings(session, threshold: float) -> dict[int, list[float]]:
    """Per-node sorted wall-clock times at which the node's motion fired.

    A reading "fires" when its motion is at/above ``threshold``. Co-activation is
    a wall-clock relation, so only rows with a real ``received_at`` count (``ts``
    is ms-since-boot, per-node and reset on reboot — not comparable across nodes).
    """
    rows = session.execute(
        select(Reading.node_id, Reading.received_at).where(
            Reading.received_at.is_not(None), Reading.motion >= threshold
        )
    ).all()
    by_node: dict[int, list[float]] = {}
    for node_id, received_at in rows:
        by_node.setdefault(node_id, []).append(received_at)
    for times in by_node.values():
        times.sort()
    return by_node


def _all_nodes(session) -> set[int]:
    """Every node id that appears in the readings table (fired or not)."""
    rows = session.execute(select(Reading.node_id).distinct()).all()
    return {node_id for (node_id,) in rows}


def _coactive_fraction(anchor_times: list[float], other_times: list[float], window: float) -> float:
    """Fraction of ``anchor_times`` with an ``other_times`` event within ±window.

    The anchor is the node with fewer firings, so the fraction is in 0..1 and the
    pair's weight is symmetric. Binary search keeps it O(n log m).
    """
    if not anchor_times:
        return 0.0
    matched = 0
    for t in anchor_times:
        i = bisect.bisect_left(other_times, t - window)
        if i < len(other_times) and other_times[i] <= t + window:
            matched += 1
    return matched / len(anchor_times)


def discover_graph(
    session,
    *,
    window_secs: float = 2.0,
    motion_threshold: float | None = None,
) -> MeshGraph:
    """Infer the mesh adjacency graph from the local DB.

    Prefers persisted neighbor reports (measured link RSSI) when present; else
    falls back to co-activation inference over the ``readings`` table. Pure and
    deterministic given the session contents + ``window_secs``.

    ``motion_threshold`` defaults to :attr:`DetectionConfig.stillness_threshold`
    — the same line between stillness and real motion the fall machine uses.
    """
    reports = _load_neighbor_reports(session)
    if reports:
        return graph_from_reports(reports)

    threshold = (
        motion_threshold
        if motion_threshold is not None
        else DetectionConfig().stillness_threshold
    )
    firings = _firings(session, threshold)
    nodes = _all_nodes(session) | set(firings)

    firing_nodes = sorted(n for n in nodes if firings.get(n))
    edges: list[tuple[int, int, float]] = []
    for i, a in enumerate(firing_nodes):
        for b in firing_nodes[i + 1 :]:
            ta, tb = firings[a], firings[b]
            # Anchor on the node with fewer firings so the weight stays in 0..1.
            if len(ta) <= len(tb):
                frac = _coactive_fraction(ta, tb, window_secs)
            else:
                frac = _coactive_fraction(tb, ta, window_secs)
            if frac > 0.0:
                edges.append((a, b, round(frac, 4)))

    edges.sort()
    suggested = tuple((a, b) for a, b, w in edges if w >= DEFAULT_LINK_CONFIDENCE)
    return MeshGraph(
        nodes=tuple(sorted(nodes)),
        edges=tuple(edges),
        suggested_links=suggested,
        source="co_activation",
    )


# --- relocation self-heal ----------------------------------------------------
@dataclass(frozen=True)
class NodeDrift:
    """A node whose discovered neighbours diverged from its configured links.

    ``distance`` is the Jaccard distance between the configured and discovered
    neighbour sets (0.0 = identical, 1.0 = disjoint). ``message`` is the plain
    sentence surfaced to the operator. Read-only — drift *suggests*, never
    rewrites ``layout.yaml``.
    """

    node_id: int
    configured: tuple[int, ...]
    discovered: tuple[int, ...]
    added: tuple[int, ...]
    removed: tuple[int, ...]
    distance: float
    message: str


def _neighbors_from_links(links, node: int) -> set[int]:
    out: set[int] = set()
    for a, b in links:
        if a == node:
            out.add(b)
        elif b == node:
            out.add(a)
    return out


def detect_drift(
    configured_links,
    discovered_graph: MeshGraph,
    *,
    min_confidence: float = DEFAULT_LINK_CONFIDENCE,
    max_distance: float = 0.5,
) -> list[NodeDrift]:
    """Flag nodes whose discovered neighbours drifted from the configured links.

    Compares each node's configured neighbour set (from ``configured_links``,
    e.g. ``layout.links``) to its discovered set, and flags it when the Jaccard
    distance exceeds ``max_distance``. Silent when the sets match. Considers every
    node that appears in either the configured links or the discovered graph, so a
    node that lost *all* its links (went isolated) is flagged too.
    """
    config_links = [tuple(pair) for pair in configured_links]
    candidates = set(discovered_graph.nodes)
    for a, b in config_links:
        candidates.update((a, b))

    drifts: list[NodeDrift] = []
    for node in sorted(candidates):
        configured = _neighbors_from_links(config_links, node)
        discovered = set(discovered_graph.neighbors(node, min_confidence=min_confidence))
        if not configured and not discovered:
            continue
        union = configured | discovered
        distance = len(configured ^ discovered) / len(union) if union else 0.0
        if distance <= max_distance:
            continue
        added = tuple(sorted(discovered - configured))
        removed = tuple(sorted(configured - discovered))
        drifts.append(
            NodeDrift(
                node_id=node,
                configured=tuple(sorted(configured)),
                discovered=tuple(sorted(discovered)),
                added=added,
                removed=removed,
                distance=round(distance, 4),
                message=_drift_message(node, added, removed, discovered),
            )
        )
    return drifts


def _drift_message(node: int, added, removed, discovered) -> str:
    parts = []
    if added:
        parts.append("now hears " + ", ".join(f"node {n}" for n in added))
    if removed:
        parts.append("no longer hears " + ", ".join(f"node {n}" for n in removed))
    detail = "; ".join(parts) if parts else "neighbours changed"
    suffix = " — relocated?" if not discovered else " — relocated or remeshed?"
    return f"node {node} {detail}{suffix}"


# --- plain-language rendering (shared by onboarding + mesh_check) -------------
def _strength_label(weight: float) -> str:
    if weight >= 0.8:
        return "strong"
    if weight >= DEFAULT_LINK_CONFIDENCE:
        return "moderate"
    return "weak"


def describe_graph(graph: MeshGraph, *, min_confidence: float = DEFAULT_LINK_CONFIDENCE) -> str:
    """Render the discovered adjacencies in plain language for an operator."""
    lines: list[str] = []
    for a, b, w in graph.edges:
        if w >= min_confidence:
            lines.append(f"node {a} ↔ node {b} ({_strength_label(w)})")
    isolated = [n for n in graph.nodes if graph.is_isolated(n, min_confidence=min_confidence)]
    for n in isolated:
        lines.append(f"node {n} looks isolated")
    if not lines:
        return "no adjacencies discovered (no co-activation in this data)"
    return "\n".join(lines)


def describe_drift(drifts: list[NodeDrift]) -> str:
    """Render drift findings in plain language (empty → an all-clear line)."""
    if not drifts:
        return "no node drift detected — configured links match what was observed"
    return "\n".join(d.message for d in drifts)
