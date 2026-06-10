"""Shared fixtures: a dry-run engine with an injectable clock over a temp DB.

Everything here is side-effect free — a :class:`DryRunNotifier` records actions
and no credentials are required (the invariant CI relies on).
"""

from __future__ import annotations

import pytest

from escalation import db
from escalation.config import load_config_from_text
from escalation.engine import CascadeEngine
from escalation.notifier import DryRunNotifier

CONTACTS_YAML = """
home_name: "Rosewood"
contacts:
  - name: "Dana"
    sms: "+15555550101"
    priority: 1
    channels: ["sms", "push"]
  - name: "Marcus"
    sms: "+15555550102"
    priority: 2
    channels: ["sms"]
"""


class FakeClock:
    """A controllable wall clock for deterministic timer tests."""

    def __init__(self, start: float = 0.0) -> None:
        self.t = start

    def __call__(self) -> float:
        return self.t

    def set(self, t: float) -> None:
        self.t = t


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock(0.0)


@pytest.fixture
def config():
    return load_config_from_text(CONTACTS_YAML)


@pytest.fixture
def db_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'escalation.db'}"


def make_engine(db_url: str, config, clock, notifier=None) -> CascadeEngine:
    engine = db.make_engine(db_url)
    db.init_db(engine)
    sessions = db.make_session_factory(engine)
    return CascadeEngine(sessions, config, notifier=notifier or DryRunNotifier(), clock=clock)


@pytest.fixture
def engine(db_url, config, clock) -> CascadeEngine:
    return make_engine(db_url, config, clock)
