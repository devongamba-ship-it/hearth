# apps/ingest

Hearth ingest service. Listens for RuView Tier-2 vitals packets, decodes them
with the shared codec, and persists alert-relevant state to a local SQLite
database (`hearth.db`).

**Status:** scaffold (BAT-20). The asyncio UDP listener and packet codec arrive
in BAT-21; the fall state machine and rules engine in later issues.

## Local-first

Raw readings never leave the home device. Only alert events and coarse status
are exposed downstream. `HEARTH_DB_URL` defaults to a local SQLite file — do not
point it at cloud storage.

## Live mesh view (BAT-27)

The live mesh view is fed by two endpoints, both aggregated-only (no raw CSI or
readings ever reach a client):

- `GET /homes/{id}/layout` — the floor plan: rooms, node markers, link topology.
  Hand-authored at onboarding in `layout.yaml`; point the service at it with
  `HEARTH_LAYOUT_FILE=/path/to/layout.yaml` (the shipped `apps/ingest/layout.yaml`
  is the "Rosewood" demo home). With no authored layout the endpoint falls back
  to a stub derived from the detection config.
- `WS /homes/{id}/live` — a 1 Hz feed of `room_activity` frames (per-room
  activity index + occupancy, plus an anonymous track array), `event` ticker
  items (room transitions, dwell starts, sensing dropouts), and `incident`
  fall-machine transitions. Each track may also carry two **optional** coarse
  vitals estimates (BAT-33), derived from the persisted breathing/motion
  aggregates — never raw readings: `respiration_bpm_estimate` (integer BPM,
  smoothed over ~30 s) and `posture_estimate` (`upright | seated | floor |
  unknown`). Both are absent when presence confidence is too low; clients render
  "—". Still aggregates only: nothing per-second, nothing raw, no waveform.

```bash
# drive the feed from the simulator (separate terminals)
uvicorn ingest.app:app --reload
python -m ingest.listener
python tools/packet_sim.py --scenario normal
```

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
uvicorn ingest.app:app --reload   # health at /health
```
