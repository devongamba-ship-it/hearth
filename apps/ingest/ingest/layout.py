"""Home layout (BAT-27) — the floor plan behind the live mesh view.

The layout is the schematic the live mesh view draws: rooms, the RuView node
markers placed in them, and the mesh link topology between nodes. It is
hand-authored at onboarding (v1: a ``layout.yaml``, env ``HEARTH_LAYOUT_FILE``)
and is the source of truth for mapping a node id to a room name in the live
feed.

Topology only — never raw readings. ``x``/``y`` are 0..1 schematic positions,
not a measured floor plan; the client draws node markers on a unit square.

When no layout file is configured the live feed still has to map nodes to
rooms, so :meth:`HomeLayout.from_detection` synthesises a minimal layout from
the detection config's ``rooms`` map plus whatever node ids have been seen.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

# v1 resident-track defaults when the layout omits a `resident:` block.
DEFAULT_TRACK_LABEL = "A"
DEFAULT_IDENTITY_CONFIDENCE = 0.9


@dataclass(frozen=True)
class Room:
    id: str
    name: str
    bedroom: bool = False


@dataclass(frozen=True)
class Node:
    id: int
    room: str  # room id
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class HomeLayout:
    """A home's rooms, node markers, and mesh link topology."""

    home_id: str
    name: str
    rooms: tuple[Room, ...]
    nodes: tuple[Node, ...]
    links: tuple[tuple[int, int], ...]
    track_label: str = DEFAULT_TRACK_LABEL
    identity_confidence: float = DEFAULT_IDENTITY_CONFIDENCE

    def room_name(self, room_id: str) -> str:
        for r in self.rooms:
            if r.id == room_id:
                return r.name
        return room_id

    def room_for_node(self, node_id: int) -> str | None:
        """The room id a node covers, or None if the node is not placed."""
        for n in self.nodes:
            if n.id == node_id:
                return n.room
        return None

    def room_ids(self) -> list[str]:
        return [r.id for r in self.rooms]

    def nodes_in(self, room_id: str) -> list[int]:
        return [n.id for n in self.nodes if n.room == room_id]

    def to_json(self) -> dict:
        """The wire shape served by ``GET /homes/<id>/layout``."""
        return {
            "home_id": self.home_id,
            "name": self.name,
            "rooms": [
                {"id": r.id, "name": r.name, "bedroom": r.bedroom} for r in self.rooms
            ],
            "nodes": [
                {"id": n.id, "room": n.room, "x": n.x, "y": n.y} for n in self.nodes
            ],
            "links": [{"a": a, "b": b} for a, b in self.links],
            "resident": {
                "track_label": self.track_label,
                "identity_confidence": self.identity_confidence,
            },
            "source": "layout.yaml",
        }

    @classmethod
    def from_detection(cls, config, node_ids, home_id: str = "home") -> HomeLayout:
        """Synthesise a layout from the detection config + seen node ids.

        Used when no ``layout.yaml`` is configured: every known node becomes its
        own room (named via ``config.room_for``), with no geometry or links. The
        live feed needs a node->room map; this gives it one without an authored
        floor plan.
        """
        seen = sorted(set(node_ids) | set(getattr(config, "rooms", {})))
        rooms = tuple(
            Room(
                id=config.room_for(nid),
                name=config.room_for(nid),
                bedroom=config.is_bedroom(nid),
            )
            for nid in seen
        )
        nodes = tuple(Node(id=nid, room=config.room_for(nid)) for nid in seen)
        return cls(home_id=home_id, name=home_id, rooms=rooms, nodes=nodes, links=())


def _parse(data: dict) -> HomeLayout:
    if not isinstance(data, dict):
        raise ValueError("layout must be a mapping")
    rooms = tuple(
        Room(id=str(r["id"]), name=str(r.get("name", r["id"])),
             bedroom=bool(r.get("bedroom", False)))
        for r in data.get("rooms", [])
    )
    nodes = tuple(
        Node(id=int(n["id"]), room=str(n["room"]),
             x=n.get("x"), y=n.get("y"))
        for n in data.get("nodes", [])
    )
    links = tuple(
        (int(pair[0]), int(pair[1])) for pair in data.get("links", [])
    )
    resident = data.get("resident") or {}
    return HomeLayout(
        home_id=str(data.get("home_id", "home")),
        name=str(data.get("name", data.get("home_id", "home"))),
        rooms=rooms,
        nodes=nodes,
        links=links,
        track_label=str(resident.get("track_label", DEFAULT_TRACK_LABEL)),
        identity_confidence=float(
            resident.get("identity_confidence", DEFAULT_IDENTITY_CONFIDENCE)
        ),
    )


def load_layout(path: str | os.PathLike | None = None) -> HomeLayout | None:
    """Load a layout from ``path`` / ``$HEARTH_LAYOUT_FILE``.

    Returns ``None`` when no layout file is configured — the caller decides
    whether to fall back to :meth:`HomeLayout.from_detection`. A configured but
    missing/empty file is an error, not a silent fallback.
    """
    resolved = path or os.environ.get("HEARTH_LAYOUT_FILE")
    if not resolved:
        return None
    text = Path(resolved).read_text(encoding="utf-8")
    return _parse(yaml.safe_load(text))


def load_layout_from_text(text: str) -> HomeLayout:
    """Parse a layout from a YAML string (used by tests)."""
    return _parse(yaml.safe_load(text))
