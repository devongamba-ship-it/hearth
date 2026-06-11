"""Integration tests for the asyncio UDP listener.

Sends real datagrams to a bound listener and checks the rows land and that
malformed packets are dropped (counted) without taking the listener down.
"""

from __future__ import annotations

import asyncio
import socket

from sqlalchemy import select

from hearth.packet import MAGIC, PACKET_SIZE, Vitals, encode
from ingest.db import Reading
from ingest.listener import create_listener


def _send(port: int, payload: bytes) -> None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(payload, ("127.0.0.1", port))
    finally:
        sock.close()


def _vitals(node_id: int, fall: bool = False) -> Vitals:
    return Vitals(
        node_id=node_id,
        presence=True,
        fall=fall,
        motion=True,
        breathing_bpm=15.0,
        heart_bpm=72.0,
        rssi=-55,
        occupancy=1,
        motion_energy=1500.0,
        presence_score=0.9,
        timestamp_ms=1000,
    )


async def _run(session_factory, payloads):
    transport, protocol = await create_listener(session_factory, host="127.0.0.1", port=0)
    port = transport.get_extra_info("sockname")[1]
    try:
        for p in payloads:
            _send(port, p)
        # let the event loop drain the datagrams
        for _ in range(50):
            await asyncio.sleep(0.02)
            if protocol.received + protocol.malformed >= len(payloads):
                break
    finally:
        transport.close()
    return protocol


def test_valid_packets_produce_rows(session_factory):
    payloads = [encode(_vitals(1)), encode(_vitals(1)), encode(_vitals(2))]
    protocol = asyncio.run(_run(session_factory, payloads))

    assert protocol.received == 3
    assert protocol.malformed == 0
    with session_factory() as s:
        rows = s.execute(select(Reading)).scalars().all()
    assert len(rows) == 3
    assert {r.node_id for r in rows} == {1, 2}


def test_malformed_packets_dropped_not_crashing(session_factory):
    bad_magic = bytearray(encode(_vitals(1)))
    bad_magic[0] ^= 0xFF
    # Every malformed fixture is a non-empty datagram: a zero-length UDP send is
    # not reliably delivered on every OS (Windows may drop it at the socket layer
    # before it reaches the protocol), which made the count flaky. "Too long" and
    # "too short" cover both sides of the size check (BAT-32).
    payloads = [
        b"\x00" * (PACKET_SIZE + 1),  # too long
        b"\x00" * 31,                 # too short
        bytes(bad_magic),             # right size, wrong magic
        b"junk-not-a-packet-aaaa",    # wrong size garbage
        encode(_vitals(5)),           # a good one still gets through afterwards
    ]
    protocol = asyncio.run(_run(session_factory, payloads))

    assert protocol.malformed == 4
    assert protocol.received == 1
    with session_factory() as s:
        rows = s.execute(select(Reading)).scalars().all()
    assert len(rows) == 1
    assert rows[0].node_id == 5


def test_packets_use_documented_magic():
    # Guards against the listener ever being fed the old assumed layout.
    assert encode(_vitals(1))[:4] == MAGIC.to_bytes(4, "little")
