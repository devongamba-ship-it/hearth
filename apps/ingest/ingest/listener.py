"""Asyncio UDP listener for RuView Tier-2 vitals packets.

Binds a datagram endpoint, decodes each packet with the shared `hearth.packet`
codec, and persists one `readings` row per packet plus an `events` row whenever
a node's fall flag transitions. Malformed datagrams are counted and dropped —
they must never take the listener down.

Config (env): `HEARTH_UDP_PORT` (default 5599), `HEARTH_DB_PATH` (see db.py).
Run standalone with `python -m ingest.listener`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time

from sqlalchemy import select

from hearth.packet import PacketError, Vitals, decode

from .db import (
    FALL_CLEARED,
    FALL_DETECTED,
    Event,
    Reading,
    init_db,
    make_engine,
    make_session_factory,
)
from .logconfig import configure_logging

log = logging.getLogger("hearth.ingest.listener")

DEFAULT_UDP_PORT = 5599


def udp_port() -> int:
    return int(os.environ.get("HEARTH_UDP_PORT", DEFAULT_UDP_PORT))


def persist(session_factory, v: Vitals, received_at: float | None = None) -> None:
    """Insert a reading, and an event row if the node's fall flag just changed.

    The previous fall state is read back from the DB (not held in memory) so the
    transition logic survives a listener restart. A node's first-ever packet is
    treated as a transition from an implicit not-fallen baseline.

    ``received_at`` is the wall-clock epoch stamped on the row (defaults to now);
    ``/health`` reads it for per-node last-seen age. Tests pass it explicitly to
    make staleness deterministic.
    """
    stamp = time.time() if received_at is None else received_at
    with session_factory() as session:
        prev = session.execute(
            select(Reading.fall_flag)
            .where(Reading.node_id == v.node_id)
            .order_by(Reading.id.desc())
            .limit(1)
        ).scalar_one_or_none()

        session.add(
            Reading(
                node_id=v.node_id,
                ts=v.timestamp_ms,
                presence=v.presence,
                motion=v.motion_energy,
                breathing_bpm=v.breathing_bpm,
                hr_bpm=v.heart_bpm,
                confidence=v.presence_score,
                fall_flag=v.fall,
                occupancy=v.occupancy,
                received_at=stamp,
            )
        )

        prev_fall = bool(prev) if prev is not None else False
        if prev_fall != v.fall:
            kind = FALL_DETECTED if v.fall else FALL_CLEARED
            session.add(Event(node_id=v.node_id, ts=v.timestamp_ms, kind=kind))

        session.commit()


class VitalsProtocol(asyncio.DatagramProtocol):
    """Decodes and persists each datagram; tallies received vs. malformed."""

    def __init__(self, session_factory) -> None:
        self.session_factory = session_factory
        self.received = 0
        self.malformed = 0

    def datagram_received(self, data: bytes, addr) -> None:
        peer = addr[0] if isinstance(addr, tuple) else str(addr)
        try:
            v = decode(data)
        except PacketError as exc:
            self.malformed += 1
            log.warning("dropped malformed packet: %s", exc,
                        extra={"event": "packet_malformed", "peer": peer})
            return
        try:
            persist(self.session_factory, v)
        except Exception:  # noqa: BLE001 - a bad write must not kill the listener
            self.malformed += 1
            log.exception("failed to persist packet",
                          extra={"event": "persist_failed", "peer": peer,
                                 "node_id": v.node_id})
            return
        self.received += 1


async def create_listener(session_factory, host: str = "0.0.0.0", port: int | None = None):
    """Create the datagram endpoint. Returns (transport, protocol)."""
    loop = asyncio.get_running_loop()
    bind_port = udp_port() if port is None else port
    transport, protocol = await loop.create_datagram_endpoint(
        lambda: VitalsProtocol(session_factory),
        local_addr=(host, bind_port),
    )
    return transport, protocol


async def _run() -> None:
    engine = make_engine()
    init_db(engine)
    session_factory = make_session_factory(engine)
    transport, _protocol = await create_listener(session_factory)
    log.info("ingest listener up on UDP :%d", udp_port(),
             extra={"event": "listener_up"})
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        transport.close()


def main() -> None:
    configure_logging("ingest")
    asyncio.run(_run())


if __name__ == "__main__":
    main()
