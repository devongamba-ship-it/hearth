"""BAT-44: mesh link auto-discovery, neighbor-report contract, relocation drift.

Covers the issue's acceptance criteria:
  * ``discover_graph`` recovers a known adjacency from synthetic readings, and is
    deterministic and noise-robust (no spurious edges; isolated nodes reported).
  * the neighbor-report (``link_rssi``) path is preferred over co-activation.
  * ``detect_drift`` flags a relocated node and is silent when neighbours match.
  * topology only — node ids + 0..1 weights, no coordinates.
"""

from __future__ import annotations

from ingest.db import Reading
from ingest.mesh_graph import (
    MeshGraph,
    NeighborLink,
    NeighborReport,
    describe_graph,
    detect_drift,
    discover_graph,
    graph_from_reports,
    persist_neighbor_reports,
    rssi_to_weight,
)


def _fire(session_factory, node_id: int, t: float, motion: float = 1500.0) -> None:
    """Insert one synthetic reading for ``node_id`` at wall-clock time ``t``."""
    with session_factory() as session:
        session.add(
            Reading(
                node_id=node_id,
                ts=int(t * 1000),
                presence=True,
                motion=motion,
                breathing_bpm=15.0,
                hr_bpm=72.0,
                confidence=0.9,
                fall_flag=False,
                occupancy=1,
                received_at=t,
            )
        )
        session.commit()


def _walk(session_factory) -> None:
    """A labelled walk: node 1↔4 co-fire, then node 3↔2 co-fire; never crosswise.

    Mirrors a person walking kitchen(1)→living(4), then bathroom(3)→bedroom(2):
    each pair lights in sequence within the co-activation window, but the two
    pairs are seconds apart so 1/3, 1/2, 4/3 never co-activate.
    """
    for base in (100.0, 110.0, 120.0, 130.0):
        _fire(session_factory, 1, base)
        _fire(session_factory, 4, base + 0.5)
    for base in (200.0, 210.0, 220.0, 230.0):
        _fire(session_factory, 3, base)
        _fire(session_factory, 2, base + 0.4)


# --- AC: recovers a known adjacency, high weight on the right pairs -----------
def test_discover_recovers_known_adjacency(session_factory):
    _walk(session_factory)
    graph = _discover(session_factory)

    assert graph.weight(1, 4) >= 0.8
    assert graph.weight(3, 2) >= 0.8
    # The crosswise pairs never co-fire — no edge at all.
    assert graph.weight(1, 3) == 0.0
    assert graph.weight(1, 2) == 0.0
    assert graph.weight(4, 3) == 0.0

    links = set(graph.suggested_links)
    assert (1, 4) in links and (2, 3) in links
    assert (1, 3) not in links and (1, 2) not in links


def test_discover_is_deterministic(session_factory):
    _walk(session_factory)
    g1 = _discover(session_factory)
    g2 = _discover(session_factory)
    assert g1 == g2


# --- AC: noise robustness — isolated motion makes no edges, node reported lone -
def test_isolated_motion_makes_no_spurious_edges(session_factory):
    # Node 5 fires alone (single bump, no neighbour nearby in time).
    _fire(session_factory, 5, 500.0)
    # Node 6 only ever reports stillness — never fires.
    _fire(session_factory, 6, 600.0, motion=10.0)
    graph = _discover(session_factory)

    assert graph.edges == ()
    assert graph.suggested_links == ()
    assert graph.is_isolated(5)
    assert graph.is_isolated(6)
    # Both nodes are still *known* (they appear in the readings), just unlinked.
    assert 5 in graph.nodes and 6 in graph.nodes


def test_hub_node_makes_no_spurious_edges(session_factory):
    # PR #24 review #1: a continuously-firing hub (node 1 fires every second
    # across the whole window) coincides with every sparse node by temporal
    # density, not adjacency. The chance baseline must subtract that out.
    for t in range(100, 161):                  # node 1: a hub, 61 firings
        _fire(session_factory, 1, float(t))
    for t in (105.0, 125.0, 145.0):            # node 5: sparse, unrelated
        _fire(session_factory, 5, t)
    graph = _discover(session_factory)

    # Every node-5 firing has a node-1 firing within ±window — but that is
    # density, not adjacency, so the corrected weight is zero: no false edge.
    assert graph.weight(1, 5) == 0.0
    assert (1, 5) not in set(graph.suggested_links)
    assert (5, 1) not in set(graph.suggested_links)
    assert graph.is_isolated(5)


def test_genuine_adjacency_survives_a_nearby_hub(session_factory):
    # The correction must not erase a real edge: a true 3↔2 pair still links even
    # while a hub (node 1) fires throughout — chance is subtracted, lift remains.
    for t in range(100, 161):
        _fire(session_factory, 1, float(t))
    for base in (120.0, 130.0, 140.0):
        _fire(session_factory, 3, base)
        _fire(session_factory, 2, base + 0.4)
    graph = _discover(session_factory)

    assert graph.weight(3, 2) >= 0.5         # genuine adjacency preserved
    assert (2, 3) in set(graph.suggested_links)
    assert graph.weight(1, 5) == 0.0 or (1, 5) not in set(graph.suggested_links)


def test_lone_node_among_a_pair_is_reported_isolated(session_factory):
    _walk(session_factory)  # 1↔4 and 3↔2
    _fire(session_factory, 9, 800.0)  # node 9 fires once, alone
    graph = _discover(session_factory)

    assert graph.is_isolated(9)
    assert not graph.is_isolated(1)
    description = describe_graph(graph)
    assert "node 9 looks isolated" in description
    assert "node 1 ↔ node 4 (strong)" in description


# --- AC: topology only — node ids + weights, no coordinates -------------------
def test_graph_is_topology_only(session_factory):
    _walk(session_factory)
    graph = _discover(session_factory)

    assert all(isinstance(n, int) for n in graph.nodes)
    for a, b, w in graph.edges:
        assert isinstance(a, int) and isinstance(b, int)
        assert 0.0 <= w <= 1.0
    # No geometry anywhere on the graph object.
    assert not any(field in MeshGraph.__dataclass_fields__ for field in ("x", "y", "coords"))


# --- AC: neighbor-report (link_rssi) path is preferred when present -----------
def test_neighbor_reports_preferred_over_coactivation(session_factory):
    # Co-activation in the readings would say 1↔4 / 3↔2 ...
    _walk(session_factory)
    # ... but a measured neighbor report says 1↔3. The report must win.
    reports = [
        NeighborReport(1, (NeighborLink(3, -40.0),)),
        NeighborReport(3, (NeighborLink(1, -42.0),)),
    ]
    written = persist_neighbor_reports(session_factory, reports)
    assert written == 2

    graph = _discover(session_factory)
    assert graph.source == "link_rssi"
    assert (1, 3) in graph.suggested_links
    assert (1, 4) not in graph.suggested_links  # co-activation was overridden


def test_rssi_to_weight_clamps_and_scales():
    assert rssi_to_weight(-30.0) == 1.0  # strong, adjacent
    assert rssi_to_weight(-90.0) == 0.0  # at the noise floor
    assert rssi_to_weight(-10.0) == 1.0  # clamped above
    assert 0.0 < rssi_to_weight(-60.0) < 1.0  # mid-strength


def test_graph_from_reports_averages_directional_rssi():
    reports = [
        NeighborReport(1, (NeighborLink(2, -30.0),)),  # weight 1.0 one way
        NeighborReport(2, (NeighborLink(1, -90.0),)),  # weight 0.0 the other
    ]
    graph = graph_from_reports(reports)
    assert graph.weight(1, 2) == 0.5  # mean of the two directions


# --- AC: detect_drift flags a relocated node, silent when neighbours match ----
ROSEWOOD_LINKS = [(1, 4), (4, 3), (3, 2), (1, 2)]


def _matching_graph() -> MeshGraph:
    edges = tuple(sorted((a, b, 1.0) for a, b in ROSEWOOD_LINKS))
    return MeshGraph(
        nodes=(1, 2, 3, 4),
        edges=edges,
        suggested_links=tuple(sorted(ROSEWOOD_LINKS)),
    )


def test_drift_silent_when_neighbours_match():
    assert detect_drift(ROSEWOOD_LINKS, _matching_graph()) == []


def test_drift_flags_relocated_node():
    # Node 3 was wired to {4, 2}; the data now shows it only hears node 1.
    edges = ((1, 3, 0.9),)
    moved = MeshGraph(nodes=(1, 2, 3, 4), edges=edges, suggested_links=((1, 3),))
    drifts = detect_drift(ROSEWOOD_LINKS, moved)

    by_node = {d.node_id: d for d in drifts}
    assert 3 in by_node
    drift = by_node[3]
    assert drift.configured == (2, 4)
    assert drift.discovered == (1,)
    assert 1 in drift.added and set(drift.removed) == {2, 4}
    assert "relocated" in drift.message


def test_drift_detected_end_to_end_from_readings(session_factory):
    """Drift over a real discovered graph: configured ring vs. an observed walk."""
    _walk(session_factory)  # observed adjacency: 1↔4, 2↔3
    # Configured layout claims a different ring (1↔2, 3↔4) — every node drifted.
    configured = [(1, 2), (3, 4)]
    graph = _discover(session_factory)
    drifts = detect_drift(configured, graph)
    assert {d.node_id for d in drifts} == {1, 2, 3, 4}


# helper: the conftest fixture yields a sessionmaker; discover_graph wants a live
# session. Open/close one cleanly and return the (pure) graph it produced.
def _discover(factory, **kwargs):
    with factory() as session:
        return discover_graph(session, **kwargs)
