#!/usr/bin/env python3
"""Hearth packet simulator — emulates RuView Tier-2 ESP32 vitals packets.

Lets the whole downstream stack (ingest -> fall machine -> escalation ->
caregiver app) be built and tested BEFORE any hardware arrives.

⚠ LAYOUT IS ASSUMED. RuView docs specify: 32-byte packet, magic 0xC5110002,
1 Hz, containing presence, motion level, breathing BPM, heart rate BPM,
confidence scores, fall flag, occupancy. The exact byte order must be
reconciled against firmware/esp32-csi-node/README.md + ADR-039/ADR-044 in
https://github.com/ruvnet/RuView (tracked in BAT-21). Until then, this file
defines the contract and the ingest codec MUST import from the same layout.

Assumed little-endian layout (32 bytes):
  offset  size  field
  0       4     magic           uint32  = 0xC5110002
  4       2     node_id         uint16
  6       4     uptime_ms       uint32
  10      1     presence        uint8   (0/1)
  11      1     fall_flag       uint8   (0/1)
  12      2     motion_score    uint16  (0-10000)
  14      2     breathing_cbpm  uint16  (BPM x 100)
  16      2     heart_cbpm      uint16  (BPM x 100)
  18      1     conf_presence   uint8   (0-100)
  19      1     conf_vitals     uint8   (0-100)
  20      1     occupancy       uint8
  21      11    reserved        zeros

Scenarios:
  normal          person moving around, no events
  empty           empty room
  fall_confirmed  normal -> fall flag -> sustained stillness (should escalate)
  fall_recovered  normal -> fall flag -> motion resumes (should NOT escalate)
  quiet_morning   presence but near-zero motion all "morning" (wellness rule)

Usage:
  python tools/packet_sim.py --scenario normal --target 127.0.0.1 --port 5599
  python tools/packet_sim.py --scenario fall_confirmed --speed 10   # 10x time
"""

from __future__ import annotations

import argparse
import random
import socket
import struct
import time

MAGIC = 0xC5110002
FMT = "<IHIBBHHHBBB11x"  # 32 bytes
assert struct.calcsize(FMT) == 32


def pack(node_id: int, uptime_ms: int, presence: int, fall: int,
         motion: int, breath_bpm: float, hr_bpm: float,
         conf_p: int, conf_v: int, occupancy: int) -> bytes:
    return struct.pack(
        FMT, MAGIC, node_id, uptime_ms, presence, fall,
        max(0, min(10000, motion)),
        int(breath_bpm * 100), int(hr_bpm * 100),
        conf_p, conf_v, occupancy,
    )


def unpack(data: bytes) -> dict:
    """Reference decoder — the ingest codec should match this exactly."""
    (magic, node_id, uptime_ms, presence, fall, motion,
     breath_cbpm, hr_cbpm, conf_p, conf_v, occupancy) = struct.unpack(FMT, data)
    if magic != MAGIC:
        raise ValueError(f"bad magic: {magic:#x}")
    return dict(node_id=node_id, uptime_ms=uptime_ms, presence=presence,
                fall_flag=fall, motion_score=motion,
                breathing_bpm=breath_cbpm / 100, heart_bpm=hr_cbpm / 100,
                conf_presence=conf_p, conf_vitals=conf_v, occupancy=occupancy)


def frames(scenario: str):
    """Yield one frame dict per simulated second, forever or until done."""
    t = 0
    while True:
        if scenario == "empty":
            yield dict(presence=0, fall=0, motion=random.randint(0, 40),
                       breath=0, hr=0, conf_p=92, conf_v=0, occ=0)
        elif scenario == "normal":
            yield dict(presence=1, fall=0,
                       motion=random.randint(800, 4000),
                       breath=random.uniform(13, 17), hr=random.uniform(62, 78),
                       conf_p=90, conf_v=70, occ=1)
        elif scenario == "quiet_morning":
            yield dict(presence=1, fall=0, motion=random.randint(20, 120),
                       breath=random.uniform(11, 14), hr=random.uniform(55, 65),
                       conf_p=88, conf_v=72, occ=1)
        elif scenario in ("fall_confirmed", "fall_recovered"):
            if t < 30:        # normal activity
                yield dict(presence=1, fall=0, motion=random.randint(800, 4000),
                           breath=15, hr=72, conf_p=90, conf_v=70, occ=1)
            elif t == 30:     # impact
                yield dict(presence=1, fall=1, motion=9500,
                           breath=18, hr=95, conf_p=90, conf_v=60, occ=1)
            elif scenario == "fall_confirmed":   # sustained stillness
                yield dict(presence=1, fall=0, motion=random.randint(5, 60),
                           breath=14, hr=88, conf_p=85, conf_v=65, occ=1)
            else:             # fall_recovered: gets back up after ~10s
                m = random.randint(5, 60) if t < 40 else random.randint(900, 3500)
                yield dict(presence=1, fall=0, motion=m,
                           breath=15, hr=80, conf_p=90, conf_v=70, occ=1)
        else:
            raise SystemExit(f"unknown scenario: {scenario}")
        t += 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--scenario", default="normal")
    ap.add_argument("--target", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=5599)
    ap.add_argument("--node-id", type=int, default=1)
    ap.add_argument("--speed", type=float, default=1.0,
                    help="time multiplier (10 = 10x faster than real time)")
    ap.add_argument("--duration", type=int, default=0,
                    help="seconds of simulated time (0 = run forever)")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    uptime = 0
    print(f"[sim] scenario={args.scenario} -> {args.target}:{args.port} "
          f"node={args.node_id} speed={args.speed}x")
    for i, f in enumerate(frames(args.scenario)):
        pkt = pack(args.node_id, uptime, f["presence"], f["fall"], f["motion"],
                   f["breath"], f["hr"], f["conf_p"], f["conf_v"], f["occ"])
        sock.sendto(pkt, (args.target, args.port))
        if f["fall"]:
            print(f"[sim] t={i}s  *** FALL FLAG SENT ***")
        uptime += 1000
        if args.duration and i >= args.duration:
            break
        time.sleep(1.0 / args.speed)
    print("[sim] done")


if __name__ == "__main__":
    main()
