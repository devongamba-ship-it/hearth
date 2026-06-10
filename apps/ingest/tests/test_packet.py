"""Unit tests for the shared RuView vitals codec (`hearth.packet`).

Covers the codec round-trip acceptance criterion, plus a golden byte-layout
check pinned to the documented offsets and the malformed-input contract.
"""

from __future__ import annotations

import struct

import pytest

from hearth.packet import (
    MAGIC,
    PACKET_SIZE,
    PacketError,
    Vitals,
    decode,
    encode,
)


def _sample(**over) -> Vitals:
    base = dict(
        node_id=7,
        presence=True,
        fall=False,
        motion=True,
        breathing_bpm=15.0,
        heart_bpm=72.0,
        rssi=-50,
        occupancy=2,
        # motion_energy / presence_score travel as f32 on the wire, so the
        # round-trip is exact only for f32-representable values (these are).
        motion_energy=1234.5,
        presence_score=0.5,
        timestamp_ms=123456,
    )
    base.update(over)
    return Vitals(**base)


def test_encode_is_32_bytes_with_magic_first():
    data = encode(_sample())
    assert len(data) == PACKET_SIZE == 32
    assert data[:4] == MAGIC.to_bytes(4, "little")


@pytest.mark.parametrize(
    "over",
    [
        {},
        {"presence": False, "fall": True, "motion": False},
        {"fall": True},
        {"node_id": 255, "occupancy": 4, "rssi": -90},
        {"breathing_bpm": 16.25, "heart_bpm": 58.0},  # exact at fixed-point res
        {"presence": False, "fall": False, "motion": False, "occupancy": 0},
    ],
)
def test_round_trip(over):
    v = _sample(**over)
    got = decode(encode(v))
    assert got == v


def test_golden_layout_matches_documented_offsets():
    # Hand-built per RuView firmware README / ADR-039, independent of encode().
    raw = struct.pack(
        "<IBBHIbB2xffI4x",
        MAGIC,
        7,           # node_id
        0b011,       # flags: presence + fall, motion clear
        1500,        # breathing 15.00 BPM x100
        720000,      # heart 72.0000 BPM x10000
        -50,         # rssi
        3,           # occupancy / persons
        1234.5,      # motion energy
        0.5,         # presence score
        987654,      # timestamp ms
    )
    v = decode(raw)
    assert v.node_id == 7
    assert v.presence is True
    assert v.fall is True
    assert v.motion is False
    assert v.breathing_bpm == 15.0
    assert v.heart_bpm == 72.0
    assert v.rssi == -50
    assert v.occupancy == 3
    assert v.motion_energy == pytest.approx(1234.5)
    assert v.presence_score == pytest.approx(0.5)
    assert v.timestamp_ms == 987654

    # encode() must reproduce exactly the documented bytes.
    assert encode(v) == raw


def test_float_fields_are_f32_precision():
    # 0.9 is not f32-exact; the round-trip is correct to f32 precision, not bit
    # exact. Document that so it isn't mistaken for a bug.
    v = _sample(presence_score=0.9, motion_energy=0.1)
    got = decode(encode(v))
    assert got.presence_score == pytest.approx(0.9, abs=1e-6)
    assert got.motion_energy == pytest.approx(0.1, abs=1e-6)


def test_decode_rejects_bad_magic():
    raw = bytearray(encode(_sample()))
    raw[0] ^= 0xFF
    with pytest.raises(PacketError):
        decode(bytes(raw))


@pytest.mark.parametrize("bad", [b"", b"too short", b"\x00" * 31, b"\x00" * 33])
def test_decode_rejects_wrong_length(bad):
    with pytest.raises(PacketError):
        decode(bad)
