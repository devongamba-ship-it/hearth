#!/usr/bin/env python3
"""Hearth onboarding / config-writer flow (BAT-40).

Setting up a home today means hand-editing three YAML files. For a second home
or a pilot family that is a non-starter. This is the guided flow that writes a
valid ``layout.yaml`` + ``detection.yaml`` + ``contacts.yaml`` set so a new home
can be brought online without touching YAML by hand.

The flow has two layers:

* a pure, testable core (:class:`OnboardingPlan`) that validates the collected
  answers, renders the three files, and — before writing anything — round-trips
  every file through the **real** loaders (``ingest.layout`` / ``ingest.detection``
  / ``escalation.config``). If a loader would reject the output, the plan refuses
  to write. The CI tests drive this core directly with a fixture answer set.
* an interactive prompt layer (:func:`collect_plan` / :func:`main`) that asks the
  questions and builds an :class:`OnboardingPlan`. It is ``# pragma: no cover`` —
  CI never drives a TTY.

The loaders live inside two separate apps (``apps/ingest`` and
``apps/escalation``) that are installed independently. Rather than depend on
either being importable, we load the three parser modules **by file path** with
:mod:`importlib` — each is import-clean (stdlib + ``yaml`` only, no intra-package
imports), so this validates against the production parser code without dragging
in the FastAPI app each package's ``__init__`` would.

This tool has no network or sensing side effects — it only reads answers and
writes the three config files to a target directory.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml

# --- locate + load the real loaders by file path -----------------------------
# Repo layout is fixed: tools/onboard.py -> repo root is one level up.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_LAYOUT_PARSER = _REPO_ROOT / "apps" / "ingest" / "ingest" / "layout.py"
_DETECTION_PARSER = _REPO_ROOT / "apps" / "ingest" / "ingest" / "detection.py"
_CONTACTS_PARSER = _REPO_ROOT / "apps" / "escalation" / "escalation" / "config.py"

# Channels the escalation cascade understands for the T+0 broadcast.
VALID_CHANNELS = ("sms", "push")
# E.164: a leading '+', a non-zero first digit, then 1..14 more digits (<=15).
_E164 = re.compile(r"^\+[1-9]\d{1,14}$")


class OnboardingError(ValueError):
    """A collected answer (or generated file) is invalid; message is for humans."""


def _load_parser(path: Path, name: str) -> ModuleType:
    """Import a loader module straight from its file, bypassing its package.

    The parser modules are import-clean, so loading them under a synthetic name
    never triggers ``ingest/__init__`` or ``escalation/__init__`` (which pull in
    FastAPI and friends). Lets the onboarder validate against the real parsers
    from either app's environment.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:  # pragma: no cover - defensive
        raise OnboardingError(f"could not load parser module at {path}")
    module = importlib.util.module_from_spec(spec)
    # Register before exec: ``dataclasses`` resolves these modules' string
    # annotations (``from __future__ import annotations``) via sys.modules.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --- collected answers -------------------------------------------------------
@dataclass(frozen=True)
class RoomSpec:
    id: str
    name: str
    bedroom: bool = False


@dataclass(frozen=True)
class NodeSpec:
    id: int
    room: str  # room id
    x: float | None = None
    y: float | None = None


@dataclass(frozen=True)
class ContactSpec:
    name: str
    phone: str  # E.164; used as the contact's sms (voice defaults to it)
    priority: int
    channels: tuple[str, ...] = VALID_CHANNELS


def _slug(text: str) -> str:
    """A filesystem/id-safe slug, e.g. "The Maple House" -> "the-maple-house"."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.strip().lower()).strip("-")
    return slug


def default_grid(node_ids: list[int]) -> dict[int, tuple[float, float]]:
    """Lay nodes out on a centred unit-square grid (the "default grid" offer).

    Positions are schematic (0..1), not measured. Nodes fill row-major in a
    roughly-square grid so the live mesh view has something sensible to draw.
    """
    n = len(node_ids)
    if n == 0:
        return {}
    cols = max(1, round(n ** 0.5))
    rows = max(1, -(-n // cols))  # ceil(n / cols)
    out: dict[int, tuple[float, float]] = {}
    for i, nid in enumerate(node_ids):
        r, c = divmod(i, cols)
        x = (c + 0.5) / cols
        y = (r + 0.5) / rows
        out[nid] = (round(x, 3), round(y, 3))
    return out


def ring_links(node_ids: list[int]) -> list[tuple[int, int]]:
    """A sensible default mesh: connect the nodes in a ring (the ring default)."""
    uniq = list(dict.fromkeys(node_ids))
    if len(uniq) < 2:
        return []
    return [(uniq[i], uniq[(i + 1) % len(uniq)]) for i in range(len(uniq))]


def _as_db_url(value: str) -> str:
    """Accept either a SQLAlchemy URL (``scheme://...``) or a bare file path."""
    return value if "://" in value else f"sqlite:///{value}"


def discover_links(
    node_ids: list[int],
    *,
    session_factory=None,
    db_url: str | None = None,
    window_secs: float = 2.0,
    min_confidence: float | None = None,
) -> tuple[list[tuple[int, int]], str]:
    """Pre-fill mesh links from a readings DB (a short walk / calibration session).

    The "confirm what the mesh discovered" step (BAT-44): instead of authoring the
    ring blind, run :func:`ingest.mesh_graph.discover_graph` over a readings DB and
    hand the operator the inferred adjacencies to confirm/nudge. Returns
    ``(links, human_description)``; falls back to the existing ring default when
    there is no co-activation data.

    Imports the ingest package lazily so onboard.py stays import-clean for the
    escalation environment (which never runs discovery). ``among`` restricts
    suggested links to the operator's own ``node_ids``, so a stray node in the
    readings DB cannot inject an edge the layout does not know about.
    """
    from ingest.db import make_engine, make_session_factory  # lazy: ingest-only
    from ingest.mesh_graph import (  # lazy: ingest-only
        DEFAULT_LINK_CONFIDENCE,
        describe_graph,
        discover_graph,
    )

    threshold = DEFAULT_LINK_CONFIDENCE if min_confidence is None else min_confidence
    ids = list(node_ids)
    factory = session_factory or make_session_factory(make_engine(db_url))
    with factory() as session:
        graph = discover_graph(session, window_secs=window_secs)
    links = graph.strongest_links(min_confidence=threshold, among=ids)
    if not links:
        return ring_links(ids), "no co-activation data — using the default ring mesh"
    return links, describe_graph(graph, min_confidence=threshold)


def validate_phone(phone: str) -> str:
    """Return the cleaned E.164 number, or raise ``OnboardingError``.

    Spaces, hyphens and parentheses are stripped for convenience; the result
    must still be strict E.164 (``+`` then 2..15 digits, first digit non-zero).
    """
    cleaned = re.sub(r"[\s\-()]", "", phone or "")
    if not _E164.match(cleaned):
        raise OnboardingError(
            f"invalid phone {phone!r}: expected E.164 like +15555550123 "
            "(a '+', country code, then up to 15 digits)"
        )
    return cleaned


@dataclass
class OnboardingPlan:
    """The full set of onboarding answers — the testable core of the flow."""

    home_name: str
    timezone: str
    rooms: list[RoomSpec]
    nodes: list[NodeSpec]
    links: list[tuple[int, int]] = field(default_factory=list)
    contacts: list[ContactSpec] = field(default_factory=list)
    home_id: str = ""

    def __post_init__(self) -> None:
        if not self.home_id:
            self.home_id = _slug(self.home_name) or "home"

    # -- validation -----------------------------------------------------------
    def validate(self) -> None:
        """Raise :class:`OnboardingError` (human message) on the first problem.

        Covers everything the downstream loaders need plus the cases the issue
        calls out: bad phone, duplicate node id, node in an unknown room.
        """
        if not self.home_name.strip():
            raise OnboardingError("home name must not be empty")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise OnboardingError(
                f"unknown timezone {self.timezone!r}: expected an IANA name "
                "like 'America/New_York'"
            ) from None

        if not self.rooms:
            raise OnboardingError("at least one room is required")
        seen_rooms: set[str] = set()
        for room in self.rooms:
            if not room.id.strip():
                raise OnboardingError("every room needs a non-empty id")
            if room.id in seen_rooms:
                raise OnboardingError(f"duplicate room id {room.id!r}")
            seen_rooms.add(room.id)

        if not self.nodes:
            raise OnboardingError("at least one node is required")
        seen_nodes: set[int] = set()
        for node in self.nodes:
            if node.id in seen_nodes:
                raise OnboardingError(f"duplicate node id {node.id}")
            seen_nodes.add(node.id)
            if node.room not in seen_rooms:
                raise OnboardingError(
                    f"node {node.id} is in unknown room {node.room!r} "
                    f"(known rooms: {', '.join(sorted(seen_rooms))})"
                )
            for axis, val in (("x", node.x), ("y", node.y)):
                if val is not None and not (0.0 <= val <= 1.0):
                    raise OnboardingError(
                        f"node {node.id} {axis}={val} is outside the 0..1 schematic square"
                    )

        for a, b in self.links:
            if a == b:
                raise OnboardingError(f"mesh link cannot connect node {a} to itself")
            for endpoint in (a, b):
                if endpoint not in seen_nodes:
                    raise OnboardingError(
                        f"mesh link references unknown node id {endpoint}"
                    )

        if not self.contacts:
            raise OnboardingError("at least one contact is required")
        for contact in self.contacts:
            if not contact.name.strip():
                raise OnboardingError("every contact needs a non-empty name")
            validate_phone(contact.phone)  # raises on bad E.164
            for channel in contact.channels:
                if channel not in VALID_CHANNELS:
                    raise OnboardingError(
                        f"contact {contact.name!r} has unknown channel {channel!r} "
                        f"(expected one of: {', '.join(VALID_CHANNELS)})"
                    )

    # -- rendering ------------------------------------------------------------
    def _layout_data(self) -> dict:
        grid = default_grid([n.id for n in self.nodes])
        nodes = []
        for n in self.nodes:
            x = n.x if n.x is not None else grid[n.id][0]
            y = n.y if n.y is not None else grid[n.id][1]
            nodes.append({"id": n.id, "room": n.room, "x": x, "y": y})
        return {
            "home_id": self.home_id,
            "name": self.home_name,
            "rooms": [
                {"id": r.id, "name": r.name, "bedroom": r.bedroom} for r in self.rooms
            ],
            "nodes": nodes,
            "links": [[a, b] for a, b in self.links],
        }

    def _detection_data(self) -> dict:
        room_name = {r.id: r.name for r in self.rooms}
        bedroom_ids = {r.id for r in self.rooms if r.bedroom}
        rooms = {n.id: room_name[n.room] for n in self.nodes}
        bedroom_nodes = sorted(n.id for n in self.nodes if n.room in bedroom_ids)
        return {
            "home_name": self.home_name,
            "timezone": self.timezone,
            "rooms": rooms,
            "bedroom_nodes": bedroom_nodes,
        }

    def _contacts_data(self) -> dict:
        contacts = []
        for c in self.contacts:
            contacts.append(
                {
                    "name": c.name,
                    "sms": validate_phone(c.phone),
                    "priority": c.priority,
                    "channels": list(c.channels),
                }
            )
        return {"home_name": self.home_name, "contacts": contacts}

    def render_all(self) -> dict[str, str]:
        """Render the three files as ``{filename: yaml-text}`` (validated first).

        Validates the answers, then round-trips every rendered file through the
        real loader. Raises :class:`OnboardingError` if either step fails — the
        tool never writes output a loader would reject.
        """
        self.validate()
        files = {
            "layout.yaml": _dump(
                self._layout_data(),
                "Hearth home layout (BAT-27) — generated by tools/onboard.py.\n"
                "Topology only; x/y are 0..1 schematic positions, not a measured plan.\n"
                "Point ingest at it with HEARTH_LAYOUT_FILE.",
            ),
            "detection.yaml": _dump(
                self._detection_data(),
                "Hearth fall-confirmation config (BAT-22) — generated by tools/onboard.py.\n"
                "Only the per-home fields the onboarder collects are written; every other\n"
                "tunable keeps its built-in default. See apps/ingest/detection.yaml for the\n"
                "full annotated set. Point ingest at it with HEARTH_DETECTION_FILE.",
            ),
            "contacts.yaml": _dump(
                self._contacts_data(),
                "Hearth escalation contact circle — generated by tools/onboard.py.\n"
                "priority: lower is called first. voice defaults to the sms number.\n"
                "Point escalation at it with HEARTH_CONTACTS_FILE. Real numbers — keep private.",
            ),
        }
        self._roundtrip(files)
        return files

    def _roundtrip(self, files: dict[str, str]) -> None:
        """Parse each rendered file through the real loader; refuse bad output."""
        layout_mod = _load_parser(_LAYOUT_PARSER, "_hearth_layout")
        detection_mod = _load_parser(_DETECTION_PARSER, "_hearth_detection")
        contacts_mod = _load_parser(_CONTACTS_PARSER, "_hearth_contacts")
        try:
            layout = layout_mod.load_layout_from_text(files["layout.yaml"])
            detection = detection_mod.load_config_from_text(files["detection.yaml"])
            circle = contacts_mod.load_config_from_text(files["contacts.yaml"])
        except Exception as exc:  # the loaders raise ValueError on bad shape
            raise OnboardingError(
                f"generated config was rejected by its loader: {exc}"
            ) from exc
        # Semantic round-trip: the parsed objects must agree with the answers.
        if len(layout.nodes) != len(self.nodes):
            raise OnboardingError("layout round-trip lost nodes")  # pragma: no cover
        for node in self.nodes:
            room_id = layout.room_for_node(node.id)
            expected = {r.id: r.name for r in self.rooms}[node.room]
            if detection.room_for(node.id) != expected or room_id != node.room:
                raise OnboardingError(  # pragma: no cover
                    f"round-trip mismatch for node {node.id}"
                )
        if len(circle.contacts) != len(self.contacts):
            raise OnboardingError("contacts round-trip lost entries")  # pragma: no cover

    # -- writing --------------------------------------------------------------
    def write(
        self,
        out_dir: str | Path,
        *,
        force: bool = False,
        confirm: Callable[[list[str]], bool] | None = None,
    ) -> list[Path]:
        """Validate, render, round-trip, then write the three files to ``out_dir``.

        Never overwrites silently: if any target file already exists and
        ``force`` is not set, ``confirm`` is asked (with the list of files that
        would be clobbered) and a falsey answer raises :class:`OnboardingError`,
        leaving every existing file untouched. Renders before touching disk, so
        invalid input fails without creating partial output.
        """
        files = self.render_all()  # validates + round-trips before any write
        out = Path(out_dir)
        existing = [name for name in files if (out / name).exists()]
        if existing and not force:
            approved = confirm(existing) if confirm is not None else False
            if not approved:
                raise OnboardingError(
                    "refusing to overwrite existing files: "
                    + ", ".join(sorted(existing))
                    + " (re-run with --force or confirm the overwrite)"
                )
        out.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        for name, text in files.items():
            path = out / name
            path.write_text(text, encoding="utf-8")
            written.append(path)
        return written


def _dump(data: dict, header: str) -> str:
    """Render a config dict to YAML with a leading comment header."""
    comment = "".join(f"# {line}\n" for line in header.splitlines())
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
    return comment + "\n" + body


def start_hint(out_dir: str | Path) -> str:
    """The exact env vars + start command for the freshly written config."""
    out = Path(out_dir).resolve()
    return (
        "Point the services at the generated config:\n\n"
        f"  export HEARTH_LAYOUT_FILE={out / 'layout.yaml'}\n"
        f"  export HEARTH_DETECTION_FILE={out / 'detection.yaml'}\n"
        f"  export HEARTH_CONTACTS_FILE={out / 'contacts.yaml'}\n\n"
        "Then start the ingest service:\n\n"
        "  cd apps/ingest && uvicorn ingest.app:app --host 0.0.0.0 --port 8000\n"
    )


# --- interactive layer (no coverage: CI never drives a TTY) ------------------
def _ask(prompt: str, default: str | None = None) -> str:  # pragma: no cover - TTY
    suffix = f" [{default}]" if default else ""
    raw = input(f"{prompt}{suffix}: ").strip()
    return raw or (default or "")


def _ask_yes_no(prompt: str, default: bool = False) -> bool:  # pragma: no cover - TTY
    d = "Y/n" if default else "y/N"
    raw = input(f"{prompt} [{d}]: ").strip().lower()
    if not raw:
        return default
    return raw in ("y", "yes")


def collect_plan(ask=_ask, ask_yes_no=_ask_yes_no) -> OnboardingPlan:  # pragma: no cover - TTY
    """Walk the operator through the questions and build an :class:`OnboardingPlan`.

    ``ask`` / ``ask_yes_no`` are injectable so the flow can be driven by a
    scripted answer set in tests; the defaults read from stdin.
    """
    print("\nHearth home onboarding — let's write your config.\n")
    home_name = ask("Home name", "Rosewood")
    timezone = ask("Timezone (IANA)", "America/New_York")

    rooms: list[RoomSpec] = []
    print("\nRooms — blank name finishes the list.")
    while True:
        name = ask(f"  Room #{len(rooms) + 1} name", "" if rooms else "Living room")
        if not name:
            break
        bedroom = ask_yes_no(f"    Is {name!r} a bedroom?", False)
        rooms.append(RoomSpec(id=_slug(name), name=name, bedroom=bedroom))

    nodes: list[NodeSpec] = []
    room_ids = [r.id for r in rooms]
    print("\nNodes — assign each RuView node to a room. Blank id finishes.")
    while True:
        raw_id = ask(f"  Node #{len(nodes) + 1} id (number)", "")
        if not raw_id:
            break
        node_id = int(raw_id)
        print(f"    rooms: {', '.join(room_ids)}")
        room = ask("    Room id for this node", room_ids[0] if room_ids else "")
        nodes.append(NodeSpec(id=node_id, room=room))

    node_ids = [n.id for n in nodes]
    # Discover-not-author (BAT-44): if the operator has a readings DB from a short
    # walk / calibration session, pre-fill links from the observed mesh and let
    # them confirm. Falls back to the ring default when there's no DB or no data.
    links: list[tuple[int, int]] = []
    discover_db = ask(
        "\nReadings DB to discover the mesh from (path/URL, blank to skip)", ""
    )
    if discover_db:
        links, description = discover_links(node_ids, db_url=_as_db_url(discover_db))
        print("\nDiscovered mesh:\n" + description)
        if not ask_yes_no("Use these discovered links?", True):
            links = []
    if not links and ask_yes_no("\nUse the default ring mesh topology?", True):
        links = ring_links(node_ids)

    contacts: list[ContactSpec] = []
    print("\nContacts — caregiver circle. Blank name finishes the list.")
    while True:
        name = ask(f"  Contact #{len(contacts) + 1} name", "")
        if not name:
            break
        phone = validate_phone(ask("    Phone (E.164)", ""))
        priority = int(ask("    Priority (lower = called first)", str(len(contacts) + 1)))
        ch_raw = ask("    Channels (comma-separated)", "sms,push")
        channels = tuple(c.strip() for c in ch_raw.split(",") if c.strip())
        contacts.append(ContactSpec(name=name, phone=phone, priority=priority, channels=channels))

    return OnboardingPlan(
        home_name=home_name,
        timezone=timezone,
        rooms=rooms,
        nodes=nodes,
        links=links,
        contacts=contacts,
    )


def main(argv: list[str] | None = None) -> int:  # pragma: no cover - entrypoint
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=".", help="directory to write the three config files to")
    ap.add_argument("--force", action="store_true", help="overwrite existing files without asking")
    args = ap.parse_args(argv)

    try:
        plan = collect_plan()
        written = plan.write(
            args.out,
            force=args.force,
            confirm=lambda existing: _ask_yes_no(
                f"Overwrite existing {', '.join(existing)}?", False
            ),
        )
    except OnboardingError as exc:
        print(f"\n[onboard] {exc}", file=sys.stderr)
        return 1

    print("\n[onboard] wrote:")
    for path in written:
        print(f"  {path}")
    print()
    print(start_hint(args.out))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
