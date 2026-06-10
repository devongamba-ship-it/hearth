#!/usr/bin/env python3
"""Hearth packet simulator — emulates RuView Tier-2 ESP32 vitals packets.

Lets the whole downstream stack (ingest -> fall machine -> escalation ->
caregiver app) be built and tested BEFORE any hardware arrives.

The byte layout used to live here as an ASSUMED guess. As of BAT-21 the real
layout was read from RuView's `firmware/esp32-csi-node/README.md` + ADR-039 and
moved into the shared `hearth.packet` codec, which this simulator and the ingest
listener now both import — so the two can never drift. See `hearth/packet.py`.

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
import os
import random
import socket
import sys
import time

# Allow `python tools/packet_sim.py` from the repo root to import the shared
# codec without an install step (the ingest service installs `hearth` properly).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from hearth.packet import MAGIC, Vitals, encode  # noqa: E402

# Constant the simulator stamps onto every packet — the real packet carries an
# RSSI the firmware measures; scenarios don't model link quality.
SIM_RSSI = -55
# Motion-energy above this trips the flags bit2 "motion" indicator.
MOTION_FLAG_THRESHOLD = 200.0


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


def frame_to_vitals(node_id: int, uptime_ms: int, f: dict) -> Vitals:
    """Map a scenario frame onto the real wire fields.

    The scenarios speak in presence/fall/motion/breath/hr/occupancy. The real
    packet has no separate "vitals confidence" field, so `conf_v` is not
    transmitted; `conf_p` becomes the firmware's presence score (0..1).
    """
    motion_energy = float(f["motion"])
    return Vitals(
        node_id=node_id,
        presence=bool(f["presence"]),
        fall=bool(f["fall"]),
        motion=motion_energy > MOTION_FLAG_THRESHOLD,
        breathing_bpm=float(f["breath"]),
        heart_bpm=float(f["hr"]),
        rssi=SIM_RSSI,
        occupancy=int(f["occ"]),
        motion_energy=motion_energy,
        presence_score=f["conf_p"] / 100.0,
        timestamp_ms=uptime_ms,
    )


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
          f"node={args.node_id} speed={args.speed}x magic={MAGIC:#010x}")
    for i, f in enumerate(frames(args.scenario)):
        pkt = encode(frame_to_vitals(args.node_id, uptime, f))
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
