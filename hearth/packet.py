"""Shared codec for the RuView Tier-2 vitals packet (magic ``0xC5110002``).

This is the single source of truth for the wire format. Both the simulator
(``tools/packet_sim.py``) and the ingest listener (``apps/ingest``) import this
module — nobody re-declares the byte layout.

Layout (authoritative: RuView ``firmware/esp32-csi-node/README.md`` "Vitals
Packet" + ADR-039 "Wire Protocols"; the two agree exactly). 32 bytes,
little-endian::

    Offset  Size  Type     Field
    0       4     u32      Magic = 0xC5110002
    4       1     u8       Node ID
    5       1     u8       Flags (bit0=presence, bit1=fall, bit2=motion)
    6       2     u16      Breathing rate (BPM x 100, fixed-point)
    8       4     u32      Heart rate (BPM x 10000, fixed-point)
    12      1     i8       RSSI (dBm)
    13      1     u8       Number of detected persons (occupancy)
    14      2     --       Reserved
    16      4     f32      Motion energy
    20      4     f32      Presence score
    24      4     u32      Timestamp (ms since boot)
    28      4     --       Reserved

NOTE: the layout that shipped in ``tools/packet_sim.py`` before BAT-21 was an
*assumed* one and did not match these docs (it packed node_id as u16, presence
and fall as standalone bytes, motion/heart as small ints, etc.). This module
implements the documented layout; the simulator now imports it, so the two can
no longer drift.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAGIC = 0xC5110002
PACKET_SIZE = 32

# Flag bits in the byte at offset 5.
PRESENCE_BIT = 0b001
FALL_BIT = 0b010
MOTION_BIT = 0b100

# Fixed-point scales the firmware uses for the two vitals.
_BREATHING_SCALE = 100  # BPM x 100  -> u16
_HEART_SCALE = 10000  # BPM x 10000 -> u32

# struct format for the 32-byte little-endian packet. The two reserved regions
# (offset 14, 2 bytes; offset 28, 4 bytes) are pad bytes ("x"): zero on encode,
# ignored on decode.
_FORMAT = "<IBBHIbB2xffI4x"
assert struct.calcsize(_FORMAT) == PACKET_SIZE


class PacketError(ValueError):
    """Raised when bytes are not a valid vitals packet (bad size or magic)."""


@dataclass(frozen=True)
class Vitals:
    """One decoded 1 Hz vitals reading from a RuView Tier-2 node."""

    node_id: int
    presence: bool
    fall: bool
    motion: bool
    breathing_bpm: float
    heart_bpm: float
    rssi: int
    occupancy: int  # firmware field: "number of detected persons"
    motion_energy: float
    presence_score: float
    timestamp_ms: int


def encode(v: Vitals) -> bytes:
    """Serialize a :class:`Vitals` to the 32-byte wire packet."""
    flags = 0
    if v.presence:
        flags |= PRESENCE_BIT
    if v.fall:
        flags |= FALL_BIT
    if v.motion:
        flags |= MOTION_BIT
    return struct.pack(
        _FORMAT,
        MAGIC,
        v.node_id & 0xFF,
        flags,
        round(v.breathing_bpm * _BREATHING_SCALE),
        round(v.heart_bpm * _HEART_SCALE),
        v.rssi,
        v.occupancy & 0xFF,
        v.motion_energy,
        v.presence_score,
        v.timestamp_ms & 0xFFFFFFFF,
    )


def decode(data: bytes) -> Vitals:
    """Parse a wire packet into :class:`Vitals`.

    Raises :class:`PacketError` on the wrong length or a bad magic number. The
    caller is expected to catch this, count the drop, and keep going — a
    malformed datagram must never take the listener down.
    """
    if len(data) != PACKET_SIZE:
        raise PacketError(f"expected {PACKET_SIZE} bytes, got {len(data)}")
    (
        magic,
        node_id,
        flags,
        breathing_cbpm,
        heart_dbpm,
        rssi,
        occupancy,
        motion_energy,
        presence_score,
        timestamp_ms,
    ) = struct.unpack(_FORMAT, data)
    if magic != MAGIC:
        raise PacketError(f"bad magic: {magic:#010x} (want {MAGIC:#010x})")
    return Vitals(
        node_id=node_id,
        presence=bool(flags & PRESENCE_BIT),
        fall=bool(flags & FALL_BIT),
        motion=bool(flags & MOTION_BIT),
        breathing_bpm=breathing_cbpm / _BREATHING_SCALE,
        heart_bpm=heart_dbpm / _HEART_SCALE,
        rssi=rssi,
        occupancy=occupancy,
        motion_energy=motion_energy,
        presence_score=presence_score,
        timestamp_ms=timestamp_ms,
    )
