# Hearth demo mode — recording guide (BAT-43)

A deterministic, screen-recordable scenario player. It feeds the **real** ingest
pipeline (the same `listener.persist` + fall machine the live system runs) with
seeded scenarios, so the real ops console, caregiver app, and command center
light up — there is no separate fake. Same sequence every take; clean reset
between runs.

Everything below is **simulated**. While demo mode is on, the live feed stamps
`source = "simulated"` on every frame, so the BAT-34 data-source badge reads
**simulated** and a demo can never be mistaken for live silicon.

## What plays

| Button            | What the room sees                                   | What the pipeline does                                  |
| ----------------- | ---------------------------------------------------- | ------------------------------------------------------- |
| `fall_confirmed`  | Normal activity → impact → sustained stillness       | Fall machine confirms one fall → escalation hand-off    |
| `fall_recovered`  | Normal activity → impact → gets back up (~10s later) | Fall machine logs a *near-fall*, never escalates        |
| `multi_occupancy` | A resident in the kitchen **and** a guest in living  | Live mesh shows a resident track + an anonymous `guest` |
| `quiet_morning`   | Present but near-still all "morning"                  | No fall (wellness signal, not an alert)                 |

`fall_confirmed` is the one that escalates exactly once. `fall_recovered` is the
asymmetry that makes the product trustworthy: a fall flag alone never alerts.

## One-time setup

Demo mode is **opt-in** and off in production. Turn it on by setting `HEARTH_DEMO`
when you start the ingest service:

```bash
# ingest (the demo player + APIs + live feed)
cd apps/ingest
HEARTH_DEMO=1 \
HEARTH_LAYOUT_FILE=layout.yaml \
HEARTH_DB_PATH=/tmp/hearth-demo.db \
uvicorn ingest.app:app --port 8000

# escalation service (dry-run by default — no real SMS/voice), so the ops
# queue lights up when a fall confirms:
cd apps/escalation
uvicorn escalation.app:app --port 8001

# ops console (the demo control panel lives here)
cd apps/ops-console
npm run dev
```

Notes:

- The demo player drives the fall machine itself. Do **not** also run the
  standalone `python -m ingest.fall_machine` poller against the same DB, or
  readings get stepped twice.
- `HEARTH_DEMO_SPEED` (default `8`) is simulated-seconds per real second. Lower
  it (`HEARTH_DEMO_SPEED=4`) for a slower, more cinematic take.

## Recording a scenario

1. Open the ops console with the demo panel: **`http://localhost:5173/?demo`**.
   The data-source badge in the header reads **simulated**.
2. Click **Reset** to start from a clean slate.
3. Refresh the page once after a reset if you are re-recording — this re-primes
   the live incident ticker so it streams the new run's transitions from the
   start. (The Queue and incident history poll, so they light up either way.)
4. Click the scenario button (`fall_confirmed`, `fall_recovered`,
   `multi_occupancy`, or `quiet_morning`).
5. Watch it play:
   - **Home detail / mesh** — room activity and tracks animate; `multi_occupancy`
     shows two tracks (`A` + a `guest`).
   - **Queue** — `fall_confirmed` raises one incident; open it for the
     NORMAL → SUSPECTED → … → CONFIRMED_FALL replay.
   - **caregiver app** — the "Mom is okay" card flips to the fall state on
     `fall_confirmed`.
6. Click **Reset** before the next take.

Because the scenarios are seeded, every recording of the same scenario is
identical — re-take freely.

## Resetting cleanly

**Reset** stops any in-flight playout and wipes the ingest pipeline (readings,
fall-flag events, fall-machine transitions) back to empty. The separate
escalation service keeps its own incident store; a prior demo's incident ages
out of its short dedupe window, or you can resolve it in the ops console queue.

## Driving it from the CLI (no browser)

The control panel is just calling these endpoints (only present when
`HEARTH_DEMO=1`):

```bash
curl localhost:8000/api/demo/status
curl -X POST localhost:8000/api/demo/play  -d '{"scenario":"fall_confirmed"}'
curl -X POST localhost:8000/api/demo/reset
```

The shipping simulator can also play any scenario deterministically straight at
the UDP listener with a fixed seed:

```bash
python tools/packet_sim.py --scenario fall_confirmed --seed 43 --speed 8
```
