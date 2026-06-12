"""Demo mode (BAT-43): a deterministic, screen-recordable scenario player.

The demo is the product's best sales/fundraising asset, so it has to be boring:
the same thing every take, no flaky timing, a clean reset between runs. This
module is that player.

It feeds the **real** ingest pipeline — the same :func:`ingest.listener.persist`
the UDP listener uses, then the real :class:`ingest.fall_machine.FallMachine`
via :class:`~ingest.fall_machine.FallMachineRunner` — with deterministic,
*seeded* scenarios built by reusing the shipping simulator's scenario
generators (``tools/packet_sim.py``). Because the real services read the local
SQLite this writes to, the real ops console / caregiver app / command center
light up — there is no separate fake.

Determinism: every actor's per-frame jitter is driven by a seeded
``random.Random``, and the playout cadence is a fixed multiplier, so a recording
looks identical every take. The fall machine's own timers run off the packet
timestamp (not a wall clock), so the confirm/recover outcome is reproducible
regardless of how fast the player runs.

Safety / privacy: this is a dev-and-demo tool, gated by ``HEARTH_DEMO`` (the
endpoints 404 otherwise) so a production box can never have its DB wiped by a
stray demo button. It only ever writes *simulated* sensor readings into the
local DB the services already read — nothing new leaves the home, no mic is
opened (the check-in stub is used). While demo mode is on the live feed
advertises ``source="simulated"`` (BAT-34), so a demo can never be mistaken for
live silicon.
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from random import Random
from types import ModuleType

from .checkin.base import StubCheckInProvider
from .detection import DetectionConfig, load_config
from .escalator import Escalator, NullEscalator
from .fall_machine import FallMachine, FallMachineRunner
from .retention import purge_demo_data

log = logging.getLogger("hearth.ingest.demo")

# The demo home id (matches layout.yaml and the ops console's DEMO_HOME). The
# four nodes below are that home's kitchen / bedroom / bathroom / living nodes.
DEMO_HOME = "rosewood"

# Fixed seed for the per-frame jitter: change it and every recording changes,
# but a given build always plays the same bytes.
DEMO_SEED = 20240611

# A fixed daytime wall clock for the fall machine (it only consults a wall clock
# for the quiet-hours threshold, which is off by default — this just pins it so
# nothing about the run depends on when it was recorded).
DEMO_CLOCK = 1_700_000_000.0

# Default playout cadence: simulated-seconds per real second. 8x makes the
# ~2-minute fall scenario play in ~15s — long enough to watch unfold, short
# enough to record in one take. Override per run, or globally with the env var.
DEFAULT_SPEED = float(os.environ.get("HEARTH_DEMO_SPEED", "8.0"))


@dataclass(frozen=True)
class Actor:
    """One simulated node in a scenario: which node id plays which sim scenario."""

    node_id: int
    scenario: str


# The buttons the ops console offers (BAT-43 scope). Each demo scenario is one or
# more actors; multi_occupancy is two occupied rooms at once so the live mesh
# emits a resident track plus an anonymous guest track (see live.py `_tracks`).
DEMO_SCENARIOS: dict[str, tuple[Actor, ...]] = {
    "fall_confirmed": (Actor(node_id=1, scenario="fall_confirmed"),),
    "fall_recovered": (Actor(node_id=4, scenario="fall_recovered"),),
    "multi_occupancy": (
        Actor(node_id=1, scenario="normal"),   # resident in the kitchen
        Actor(node_id=4, scenario="normal"),   # a guest in the living room
    ),
    "quiet_morning": (Actor(node_id=2, scenario="quiet_morning"),),
}

# Simulated seconds to play per scenario — long enough to reach the
# characteristic end state. fall_confirmed must outlast SUSPECTED→PROBABLE
# stillness (20s) + the check-in timeout (45s) after the t=30 impact;
# fall_recovered just needs to get past the ~t=40 stand-up.
DEMO_DURATIONS: dict[str, int] = {
    "fall_confirmed": 115,
    "fall_recovered": 60,
    "multi_occupancy": 45,
    "quiet_morning": 90,
}


def scenario_names() -> list[str]:
    """The demo scenarios the player (and the ops console buttons) offer."""
    return list(DEMO_SCENARIOS)


def is_enabled(flag: str | None = None) -> bool:
    """Whether demo mode is on. ``flag`` overrides the ``HEARTH_DEMO`` env var.

    Truthy values: ``1 true yes on`` (case-insensitive). Everything else, and
    unset, is off — demo is opt-in so it is never live in production by default.
    """
    raw = os.environ.get("HEARTH_DEMO") if flag is None else flag
    return bool(raw) and raw.strip().lower() in {"1", "true", "yes", "on"}


# -- simulator loading -------------------------------------------------------

_REPO_ROOT = Path(__file__).resolve().parents[3]
_sim_module: ModuleType | None = None


def load_sim() -> ModuleType:
    """Load the shipping packet simulator (a script, not a package) by path.

    Reuses the exact scenario generators the hardware-free test driver ships, so
    the demo and the simulator can never describe different "falls". Path is
    overridable with ``HEARTH_PACKET_SIM`` for non-standard layouts; defaults to
    ``<repo>/tools/packet_sim.py``.
    """
    global _sim_module
    if _sim_module is not None:
        return _sim_module
    sim_path = Path(os.environ.get("HEARTH_PACKET_SIM", _REPO_ROOT / "tools" / "packet_sim.py"))
    if not sim_path.exists():
        raise FileNotFoundError(f"packet simulator not found at {sim_path}")
    spec = importlib.util.spec_from_file_location("hearth_packet_sim", sim_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _sim_module = module
    return module


# -- reset -------------------------------------------------------------------

def reset(session_factory) -> dict:
    """Wipe the ingest pipeline back to a clean slate.

    Clears the simulated readings, fall-flag events, and fall-machine transitions
    so the next scenario starts from nothing. The actual deletion lives in
    ``retention.py`` — the codebase's single, logged deletion path (BAT-41) — so
    a demo reset is as auditable as any other purge. This is the ingest-side
    state the player owns; the separate escalation service keeps its own incident
    store (its short dedupe window ages prior demo incidents out, or resolve them
    in the ops console).
    """
    return purge_demo_data(session_factory)


# -- player ------------------------------------------------------------------

class ScenarioPlayer:
    """Drives one deterministic scenario through the real ingest pipeline.

    Stateful and seeded: build it, then call :meth:`step` once per simulated
    second (no sleeps — fully deterministic and unit-testable), or
    :meth:`play_sync` to run to completion, or :meth:`play_async` for the
    real-time-ish playout the WS-fed UIs animate against.
    """

    def __init__(
        self,
        session_factory,
        scenario: str,
        *,
        escalator: Escalator | None = None,
        config: DetectionConfig | None = None,
        clock: Callable[[], float] | None = None,
        seed: int = DEMO_SEED,
        duration: int | None = None,
    ) -> None:
        if scenario not in DEMO_SCENARIOS:
            raise ValueError(f"unknown demo scenario: {scenario}")
        self.scenario = scenario
        self._sessions = session_factory
        self._sim = load_sim()
        self._actors = DEMO_SCENARIOS[scenario]
        self.duration = DEMO_DURATIONS[scenario] if duration is None else duration

        cfg = config if config is not None else load_config()
        fixed = clock or (lambda: DEMO_CLOCK)
        self._machine = FallMachine(
            cfg,
            escalator=escalator or NullEscalator(),
            checkin=StubCheckInProvider(),
            session_factory=session_factory,
            clock=fixed,
        )
        self._runner = FallMachineRunner(self._machine, session_factory)

        # One independent, seeded stream per actor so each node's jitter is
        # reproducible and the actors don't share (and thus correlate) a stream.
        self._streams = {
            actor.node_id: self._sim.frames(actor.scenario, Random(seed + actor.node_id))
            for actor in self._actors
        }
        self._t = 0
        self._cancelled = False

    @property
    def machine(self) -> FallMachine:
        return self._machine

    @property
    def done(self) -> bool:
        return self._t > self.duration

    @property
    def active(self) -> bool:
        """Still has seconds left to play and has not been cancelled."""
        return not self.done and not self._cancelled

    def step(self) -> bool:
        """Play one simulated second into the real pipeline. Returns ``not done``.

        Persists this second's reading for every actor through the same
        ``persist`` path the UDP listener uses, then steps the fall machine over
        the new rows. The packet timestamp (``self._t`` seconds) drives the
        machine's timers, so the outcome is independent of real-time cadence.
        """
        if self.done:
            return False
        # Imported here so the module has no import cycle with listener (which
        # imports db just like this does); cheap, called once per second.
        from .listener import persist

        ts_ms = self._t * 1000
        for actor in self._actors:
            frame = next(self._streams[actor.node_id])
            vitals = self._sim.frame_to_vitals(actor.node_id, ts_ms, frame)
            persist(self._sessions, vitals)
        self._runner.poll_once()
        self._t += 1
        return not self.done

    def play_sync(self) -> int:
        """Run to completion with no sleeps. Returns the simulated seconds played."""
        while self.step():
            pass
        return self._t

    async def play_async(self, speed: float = DEFAULT_SPEED) -> None:
        """Run with a real-time-ish cadence so the WS-fed UIs animate.

        ``speed`` is simulated-seconds per real second. Cancellable via
        :meth:`cancel` (a fresh play or a reset stops the previous one).
        """
        delay = 1.0 / speed if speed > 0 else 0.0
        log.info(
            "demo play start scenario=%s duration=%ss speed=%sx",
            self.scenario, self.duration, speed,
            extra={"event": "demo_play", "scenario": self.scenario},
        )
        while not self._cancelled and self.step():
            if delay:
                await asyncio.sleep(delay)
        log.info(
            "demo play %s scenario=%s",
            "cancelled" if self._cancelled else "complete", self.scenario,
            extra={"event": "demo_play_end", "scenario": self.scenario},
        )

    def cancel(self) -> None:
        """Ask :meth:`play_async` to stop after the current second."""
        self._cancelled = True
