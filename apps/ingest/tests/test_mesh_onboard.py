"""BAT-44: the onboarding discovery pre-fill and the mesh_check relocation CLI.

The discovery step pre-fills ``links`` from a readings DB (a short walk / a
calibration session) so the operator *confirms* the mesh instead of authoring the
ring blind, and falls back to the ring default when there is no data. The
``mesh_check`` CLI compares configured links against the observed graph read-only.
"""

from __future__ import annotations

import sys
from pathlib import Path

from ingest.db import Reading
from ingest.layout import load_layout, load_layout_from_text

# The CLIs live at the repo root under tools/, outside the installed packages.
_TOOLS = Path(__file__).resolve().parents[3] / "tools"
sys.path.insert(0, str(_TOOLS))

import mesh_check  # noqa: E402
import onboard  # noqa: E402


def _fire(session_factory, node_id: int, t: float, motion: float = 1500.0) -> None:
    with session_factory() as session:
        session.add(
            Reading(
                node_id=node_id, ts=int(t * 1000), presence=True, motion=motion,
                breathing_bpm=15.0, hr_bpm=72.0, confidence=0.9, fall_flag=False,
                occupancy=1, received_at=t,
            )
        )
        session.commit()


def _walk(session_factory) -> None:
    """Observed adjacency: node 1↔4 and node 3↔2 (kitchen→living, bath→bed)."""
    for base in (100.0, 110.0, 120.0, 130.0):
        _fire(session_factory, 1, base)
        _fire(session_factory, 4, base + 0.5)
    for base in (200.0, 210.0, 220.0, 230.0):
        _fire(session_factory, 3, base)
        _fire(session_factory, 2, base + 0.4)


def _plan_with_links(links) -> onboard.OnboardingPlan:
    """A four-node home (Rosewood-shaped) carrying the discovered ``links``."""
    return onboard.OnboardingPlan(
        home_name="Maple House",
        timezone="America/Chicago",
        rooms=[
            onboard.RoomSpec(id="kitchen", name="Kitchen"),
            onboard.RoomSpec(id="living", name="Living room"),
            onboard.RoomSpec(id="bedroom", name="Bedroom", bedroom=True),
            onboard.RoomSpec(id="bathroom", name="Bathroom"),
        ],
        nodes=[
            onboard.NodeSpec(id=1, room="kitchen"),
            onboard.NodeSpec(id=4, room="living"),
            onboard.NodeSpec(id=2, room="bedroom"),
            onboard.NodeSpec(id=3, room="bathroom"),
        ],
        links=list(links),
        contacts=[onboard.ContactSpec(name="Dana", phone="+15555550101", priority=1)],
    )


# --- AC: discovery pre-fills links and round-trips through the real loader -----
def test_discovery_prefills_links_and_round_trips(session_factory, tmp_path):
    _walk(session_factory)

    links, description = onboard.discover_links([1, 2, 3, 4], session_factory=session_factory)

    # Recovered exactly the two observed adjacencies, undirected (a < b).
    assert sorted(links) == [(1, 4), (2, 3)]
    assert "node 1 ↔ node 4" in description

    # The pre-filled links round-trip through the production layout loader.
    written = _plan_with_links(links).write(tmp_path)
    assert {p.name for p in written} == {"layout.yaml", "detection.yaml", "contacts.yaml"}
    layout = load_layout(tmp_path / "layout.yaml")
    assert sorted(tuple(sorted(p)) for p in layout.links) == [(1, 4), (2, 3)]


# --- AC: falls back to the ring default when there is no data ------------------
def test_discovery_falls_back_to_ring_with_no_data(session_factory, tmp_path):
    # No readings inserted → nothing to infer.
    links, description = onboard.discover_links([1, 2, 3, 4], session_factory=session_factory)

    assert links == onboard.ring_links([1, 2, 3, 4])
    assert "ring" in description.lower()

    # The ring fallback is itself a valid, writable, round-trippable layout.
    _plan_with_links(links).write(tmp_path)
    layout = load_layout(tmp_path / "layout.yaml")
    assert len(layout.links) == 4


def test_discovery_ignores_nodes_outside_the_operators_set(session_factory):
    _walk(session_factory)
    _fire(session_factory, 7, 100.2)  # a stray node co-firing with node 1

    # The operator only declared nodes 1..4; node 7 must not sneak into the links.
    links, _ = onboard.discover_links([1, 2, 3, 4], session_factory=session_factory)
    assert all(7 not in pair for pair in links)


# --- the mesh_check relocation CLI (read-only) --------------------------------
_LAYOUT_YAML = """
home_id: rosewood
name: Rosewood
rooms:
  - {id: kitchen, name: Kitchen}
  - {id: living, name: Living room}
  - {id: bedroom, name: Bedroom, bedroom: true}
  - {id: bathroom, name: Bathroom}
nodes:
  - {id: 1, room: kitchen}
  - {id: 4, room: living}
  - {id: 2, room: bedroom}
  - {id: 3, room: bathroom}
links: [[1, 2], [3, 4]]
"""


def test_mesh_check_reports_drift_against_configured_layout(session_factory):
    _walk(session_factory)  # observed: 1↔4, 2↔3 — but layout says 1↔2, 3↔4
    layout = load_layout_from_text(_LAYOUT_YAML)

    graph, drifts = mesh_check.check(layout, session_factory)
    assert {d.node_id for d in drifts} == {1, 2, 3, 4}

    report = mesh_check.render_report(graph, drifts)
    assert "Discovered mesh:" in report
    assert "relocated" in report
    assert "read-only" in report  # never rewrites layout.yaml


def test_mesh_check_silent_when_layout_matches_observation(session_factory):
    _walk(session_factory)  # observed: 1↔4, 2↔3
    matching = _LAYOUT_YAML.replace("links: [[1, 2], [3, 4]]", "links: [[1, 4], [2, 3]]")
    layout = load_layout_from_text(matching)

    _graph, drifts = mesh_check.check(layout, session_factory)
    assert drifts == []
