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

## Observability (BAT-37)

`GET /health` is a real status payload, not just a liveness ping — so when a
node goes silent at 3 AM you can see *why* without SSHing into the Pi:

- **per-node**: last-seen age, reading count, latest confidence (the CSI-quality
  proxy), and an `online`/`stale` flag. A node configured in the layout /
  detection rooms but never heard from shows up too, with `reading_count: 0`.
- **aggregate**: nodes online / expected, DB write rate (readings/min), uptime,
  active-incident count, and the configured `home_id`.
- **status**: `ok` (all nodes online) · `degraded` (≥1 node stale) · `down`
  (nothing reporting).

A node is `stale` once it has been silent past the dropout window
(`HEARTH_NODE_DROPOUT_SECS`, default 30s). `home_id` comes from `HEARTH_HOME_ID`
(else the layout). Coarse status only — never a raw reading.

Eyeball it on flash day:

```bash
python tools/hearth_status.py --url http://pi.local:8000   # pretty node table
python tools/hearth_status.py --raw                        # the JSON payload
```

Logs are **structured**: `HEARTH_LOG_FORMAT=json` (prod, one JSON object per
line) or `text` (dev, default); level via `HEARTH_LOG_LEVEL` (default `INFO`).
Each line carries coarse context (`event`, `node_id`, `room`, `incident_id`) —
never raw readings or secrets.

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
uvicorn ingest.app:app --reload   # health at /health
```
