# docs

Design notes and architecture decisions for Hearth.

The binding agent rules and architecture invariants live in
[`/CLAUDE.md`](../CLAUDE.md). The product concept doc is private (kept off-repo).

## Layout

| Path                  | What                                                          |
| --------------------- | ------------------------------------------------------------ |
| `apps/ingest/`        | UDP listener, packet codec, fall state machine, rules engine |
| `apps/escalation/`    | SMS/voice cascade (Twilio, dry-run by default)               |
| `apps/caregiver-web/` | React + Vite caregiver status dashboard                      |
| `tools/packet_sim.py` | hardware-free packet simulator                               |

## Principles

- **Local-first.** Raw sensing data stays on the home device; only alert events
  and coarse status leave.
- **Privacy is the brand.** No cameras or microphones; the caregiver app sees
  coarse status only.
- **Single SQLite database** (`hearth.db`) for v1.
