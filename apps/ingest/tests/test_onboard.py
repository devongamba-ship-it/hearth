"""BAT-40: tools/onboard.py writes a valid three-file home config.

Drives the onboarding *core* (and the prompt flow via a scripted fixture answer
set — no interactive prompt in CI) and round-trips the generated files through
the real loaders: ``ingest.layout`` / ``ingest.detection`` (installed here) and
``escalation.config`` (loaded by file path, since the escalation package is not
installed in the ingest test environment).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# The CLI lives at the repo root under tools/, outside the installed packages.
_TOOLS = Path(__file__).resolve().parents[3] / "tools"
sys.path.insert(0, str(_TOOLS))

import onboard  # noqa: E402

from ingest.detection import load_config as load_detection  # noqa: E402
from ingest.layout import load_layout  # noqa: E402


def _load_contacts_parser():
    """The real escalation contacts parser, loaded by path (pkg not installed)."""
    path = (
        Path(__file__).resolve().parents[3]
        / "apps"
        / "escalation"
        / "escalation"
        / "config.py"
    )
    spec = importlib.util.spec_from_file_location("_test_contacts", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["_test_contacts"] = module  # dataclasses needs it resolvable
    spec.loader.exec_module(module)
    return module


def _valid_plan() -> onboard.OnboardingPlan:
    """A representative, valid onboarding answer set (Maple House)."""
    return onboard.OnboardingPlan(
        home_name="Maple House",
        timezone="America/Chicago",
        rooms=[
            onboard.RoomSpec(id="kitchen", name="Kitchen"),
            onboard.RoomSpec(id="bedroom", name="Bedroom", bedroom=True),
            onboard.RoomSpec(id="bathroom", name="Bathroom"),
        ],
        nodes=[
            onboard.NodeSpec(id=1, room="kitchen"),
            onboard.NodeSpec(id=2, room="bedroom"),
            onboard.NodeSpec(id=3, room="bathroom"),
        ],
        links=onboard.ring_links([1, 2, 3]),
        contacts=[
            onboard.ContactSpec(name="Dana", phone="+15555550101", priority=1),
            onboard.ContactSpec(
                name="Marcus", phone="+15555550102", priority=2, channels=("sms",)
            ),
        ],
    )


# --- AC: produces three files the existing loaders accept (round-trip) --------
def test_writes_three_files_the_real_loaders_accept(tmp_path):
    written = _valid_plan().write(tmp_path)

    names = {p.name for p in written}
    assert names == {"layout.yaml", "detection.yaml", "contacts.yaml"}
    for path in written:
        assert path.exists()

    # Round-trip through the production loaders, independently of onboard.
    layout = load_layout(tmp_path / "layout.yaml")
    assert layout.name == "Maple House"
    assert layout.room_for_node(2) == "bedroom"
    # Default grid filled positions for every node; ring mesh wired all three.
    assert all(n.x is not None and n.y is not None for n in layout.nodes)
    assert len(layout.links) == 3

    detection = load_detection(tmp_path / "detection.yaml")
    assert detection.home_name == "Maple House"
    assert detection.timezone == "America/Chicago"
    assert detection.room_for(2) == "Bedroom"
    assert detection.is_bedroom(2) and not detection.is_bedroom(1)

    contacts_mod = _load_contacts_parser()
    circle = contacts_mod.load_config(tmp_path / "contacts.yaml")
    assert circle.home_name == "Maple House"
    assert [c.name for c in circle.by_priority()] == ["Dana", "Marcus"]
    # voice defaults to the sms number; channels carried through.
    dana = circle.by_priority()[0]
    assert dana.voice == "+15555550101"
    assert circle.by_priority()[1].channels == ("sms",)


# --- AC: rejects invalid input with a clear message ---------------------------
def test_rejects_bad_phone(tmp_path):
    plan = _valid_plan()
    plan.contacts[0] = onboard.ContactSpec(name="Dana", phone="555-0101", priority=1)
    with pytest.raises(onboard.OnboardingError, match="E.164"):
        plan.write(tmp_path)
    assert not list(tmp_path.iterdir())  # nothing written on rejection


def test_rejects_duplicate_node_id(tmp_path):
    plan = _valid_plan()
    plan.nodes.append(onboard.NodeSpec(id=1, room="bathroom"))  # id 1 already used
    with pytest.raises(onboard.OnboardingError, match="duplicate node id 1"):
        plan.write(tmp_path)
    assert not list(tmp_path.iterdir())


def test_rejects_node_in_unknown_room(tmp_path):
    plan = _valid_plan()
    plan.nodes.append(onboard.NodeSpec(id=9, room="garage"))  # no such room
    with pytest.raises(onboard.OnboardingError, match="unknown room 'garage'"):
        plan.write(tmp_path)
    assert not list(tmp_path.iterdir())


def test_rejects_unknown_timezone():
    plan = _valid_plan()
    plan.timezone = "Mars/Olympus_Mons"
    with pytest.raises(onboard.OnboardingError, match="unknown timezone"):
        plan.validate()


def test_rejects_link_to_unknown_node():
    plan = _valid_plan()
    plan.links.append((1, 99))
    with pytest.raises(onboard.OnboardingError, match="unknown node id 99"):
        plan.validate()


def test_rejects_unknown_channel():
    plan = _valid_plan()
    plan.contacts[0] = onboard.ContactSpec(
        name="Dana", phone="+15555550101", priority=1, channels=("carrier-pigeon",)
    )
    with pytest.raises(onboard.OnboardingError, match="unknown channel"):
        plan.validate()


# --- AC: re-runnable; confirms before overwrite -------------------------------
def test_refuses_overwrite_without_confirmation(tmp_path):
    plan = _valid_plan()
    plan.write(tmp_path)  # first run succeeds
    before = (tmp_path / "layout.yaml").read_text()

    # Second run, no force, confirm says no -> raises and leaves files untouched.
    with pytest.raises(onboard.OnboardingError, match="refusing to overwrite"):
        plan.write(tmp_path, confirm=lambda existing: False)
    assert (tmp_path / "layout.yaml").read_text() == before


def test_overwrite_when_confirmed_or_forced(tmp_path):
    _valid_plan().write(tmp_path)

    # A different home, confirmed -> files are replaced.
    plan2 = _valid_plan()
    plan2.home_name = "Birch Cottage"
    plan2.write(tmp_path, confirm=lambda existing: True)
    assert load_layout(tmp_path / "layout.yaml").name == "Birch Cottage"

    # force=True overwrites without asking at all.
    plan3 = _valid_plan()
    plan3.home_name = "Cedar Flat"
    plan3.write(tmp_path, force=True)
    assert load_layout(tmp_path / "layout.yaml").name == "Cedar Flat"


def test_overwrite_confirm_reports_only_existing_files(tmp_path):
    plan = _valid_plan()
    plan.write(tmp_path)
    (tmp_path / "detection.yaml").unlink()  # only two of three remain

    seen: dict[str, list[str]] = {}

    def confirm(existing):
        seen["files"] = existing
        return True

    plan.write(tmp_path, confirm=confirm)
    assert sorted(seen["files"]) == ["contacts.yaml", "layout.yaml"]


# --- AC: drive the prompt flow via a fixture answer set (no TTY in CI) ---------
def test_collect_plan_from_scripted_answers(tmp_path):
    ask_answers = iter(
        [
            "Maple House",       # home name
            "America/Chicago",   # timezone
            "Kitchen",           # room #1 name
            "Bedroom",           # room #2 name
            "",                  # finish rooms
            "1",                 # node #1 id
            "kitchen",           # node #1 room
            "2",                 # node #2 id
            "bedroom",           # node #2 room
            "",                  # finish nodes
            "Dana",              # contact #1 name
            "+1 555-555-0101",   # phone (gets cleaned to E.164)
            "1",                 # priority
            "sms,push",          # channels
            "",                  # finish contacts
        ]
    )
    yn_answers = iter([False, True, True])  # kitchen?, bedroom?, ring mesh?

    plan = onboard.collect_plan(
        ask=lambda *a, **k: next(ask_answers),
        ask_yes_no=lambda *a, **k: next(yn_answers),
    )

    assert plan.home_id == "maple-house"
    assert [r.id for r in plan.rooms] == ["kitchen", "bedroom"]
    assert plan.rooms[1].bedroom is True
    assert [n.id for n in plan.nodes] == [1, 2]
    assert plan.contacts[0].phone == "+15555550101"  # cleaned
    assert plan.links == [(1, 2), (2, 1)]  # ring over two nodes

    # The scripted plan is itself valid and writable.
    written = plan.write(tmp_path)
    assert len(written) == 3
    assert load_detection(tmp_path / "detection.yaml").room_for(2) == "Bedroom"


# --- helpers + the env/start hint --------------------------------------------
def test_default_grid_positions_are_in_unit_square():
    grid = onboard.default_grid([1, 2, 3, 4])
    assert set(grid) == {1, 2, 3, 4}
    for x, y in grid.values():
        assert 0.0 <= x <= 1.0 and 0.0 <= y <= 1.0


def test_start_hint_names_the_three_env_vars(tmp_path):
    hint = onboard.start_hint(tmp_path)
    assert "HEARTH_LAYOUT_FILE" in hint
    assert "HEARTH_DETECTION_FILE" in hint
    assert "HEARTH_CONTACTS_FILE" in hint
    assert "uvicorn ingest.app:app" in hint
