"""Unit tests for persistence + the fall_flag event insertion criterion."""

from __future__ import annotations

from sqlalchemy import select

from hearth.packet import Vitals
from ingest.db import FALL_CLEARED, FALL_DETECTED, Event, Reading
from ingest.listener import persist


def _v(node_id: int, fall: bool, ts: int) -> Vitals:
    return Vitals(
        node_id=node_id,
        presence=True,
        fall=fall,
        motion=not fall,
        breathing_bpm=15.0,
        heart_bpm=72.0,
        rssi=-55,
        occupancy=1,
        motion_energy=900.0 if not fall else 12.0,
        presence_score=0.9,
        timestamp_ms=ts,
    )


def test_reading_columns_populated(session_factory):
    persist(session_factory, _v(1, fall=False, ts=1000))
    with session_factory() as s:
        r = s.execute(select(Reading)).scalars().one()
    assert r.node_id == 1
    assert r.ts == 1000
    assert r.presence is True
    assert r.fall_flag is False
    assert r.breathing_bpm == 15.0
    assert r.hr_bpm == 72.0
    assert r.occupancy == 1
    assert r.confidence == 0.9


def test_fall_transition_inserts_events(session_factory):
    # fall flag over time: 0, 0, 1, 1, 0  -> detect at step 3, clear at step 5.
    for i, fall in enumerate([False, False, True, True, False]):
        persist(session_factory, _v(1, fall=fall, ts=1000 + i))

    with session_factory() as s:
        readings = s.execute(select(Reading)).scalars().all()
        events = s.execute(select(Event).order_by(Event.id)).scalars().all()

    assert len(readings) == 5
    assert [(e.kind, e.ts) for e in events] == [
        (FALL_DETECTED, 1002),
        (FALL_CLEARED, 1004),
    ]


def test_no_event_when_fall_unchanged(session_factory):
    for i in range(3):
        persist(session_factory, _v(1, fall=False, ts=i))
    with session_factory() as s:
        assert s.execute(select(Event)).scalars().all() == []


def test_first_packet_already_fallen_emits_detected(session_factory):
    persist(session_factory, _v(9, fall=True, ts=42))
    with session_factory() as s:
        events = s.execute(select(Event)).scalars().all()
    assert len(events) == 1
    assert events[0].kind == FALL_DETECTED
    assert events[0].node_id == 9


def test_transitions_are_per_node(session_factory):
    persist(session_factory, _v(1, fall=False, ts=0))
    persist(session_factory, _v(2, fall=False, ts=0))
    persist(session_factory, _v(1, fall=True, ts=1))  # only node 1 falls
    with session_factory() as s:
        events = s.execute(select(Event)).scalars().all()
    assert len(events) == 1
    assert events[0].node_id == 1
    assert events[0].kind == FALL_DETECTED
