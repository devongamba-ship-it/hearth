# Hearth Command Center (BAT-29)

A real-time **3D spatial view** of a Hearth home — a dark "sensing field" floor,
room outlines with activity heat, and glowing presence figures that mirror the
fall state machine. Built on Three.js.

**Audience: admin + sales demos. This is not a caregiver surface.** It shows the
system's spatial awareness for demos and operations; it deliberately has none of
the caregiver app's "Mom is okay" framing. Like every Hearth client it consumes
**aggregates only** — no raw readings, no per-second vitals (see Privacy below).

## What it draws

- **Sensing-field floor** (canonical design v0.2): dark base, fine + major
  blueprint grid, glowing octahedron node markers, floor-level mesh link lines,
  and sensing ripples pulsing out of each node.
- **Rooms**: translucent floor tiles tinted by each room's activity index
  (cool/quiet → warm/busy), with outlines.
- **Presence figures**, one per track, in three switchable styles (the
  **Figures** control): **Capsule** (default; rotates prone + turns red on a
  fall), **Column** (calm energy column for wall displays), **Marker** (flat
  floor puck — most honest about positional uncertainty). Every figure carries a
  colored ID tag (A = teal, B = violet, guests muted) and a halo. A track in
  `fall_watch` always renders red with a fast pulse regardless of style.
- **Multi-occupancy**: co-located tracks take deterministic ring slots so
  figures never intersect. When `tracking_degraded` arrives, figures drop out
  and rooms show occupancy **count badges** instead.
- **Views**: bird's-eye, ground-level (~1.4 m eye height, tracks the resident),
  and a slow orbit for demos.
- **HUD**: per-room activity, resident track + confidence, motion (activity
  index), and a FALL WATCH badge. Plus an **event ticker** (room transitions,
  dwell, sensing dropouts, incidents).

## Data contract

Consumes the BAT-27 endpoints on the ingest service:

- `GET /homes/{id}/layout` — rooms, node markers (`x`/`y` in a 0..1 unit
  square), mesh links, resident track config. Drawn once.
- `WS /homes/{id}/live` — 1 Hz `room_activity` (per-room activity/occupancy +
  a `tracks` array), `event`, and `incident` frames (v0.2 track-array shape).

Position is interpolated **client-side** between the 1 Hz frames, so figures
glide rather than teleport.

## Privacy

The live feed exposes aggregates only. **Respiration and posture are not emitted
by it** — the HUD shows them as "—" rather than fabricating a vital sign. Motion
is the activity index the feed actually provides, and the resident track is
anonymous (label, not identity).

## Run it

```bash
cd apps/command-center
npm install

# Backendless demo (scripted scenario incl. a fall + degraded tick):
VITE_USE_MOCK=1 npm run dev

# Against a live ingest service (proxies /ingest → :8000 for HTTP + WS):
npm run dev
# point elsewhere with: HEARTH_INGEST_URL=http://host:8000 npm run dev
```

Config via env: `VITE_USE_MOCK=1` (mock data), `VITE_INGEST_BASE` (HTTP/WS base,
default `/ingest`), `VITE_HOME_ID` (default `rosewood`).

## Test / build

```bash
npm test          # vitest — pure scene logic + component smoke tests
npm run build     # vite production build
```

WebGL has no context under jsdom, so the imperative `src/scene/renderer.js` is
not unit-tested; all render *decisions* (geometry, track→figure mapping, heat,
camera, interpolation, store reducer) live in pure modules under `src/lib/` and
are fully tested, with the React shell smoke-tested against a mocked scene.

## Assumptions / human verification

- **Room footprints are derived from node positions** (the layout endpoint
  carries node `x`/`y` but no room polygons): each room is the padded bounding
  box of its nodes, or a fixed tile for a single-node room. Verify this reads
  well against `dashboard-mockup.html` (cmd tab) and adjust the padding/tiling
  constants in `src/lib/layout.js` if the demo home wants hand-tuned rooms.
- Visual fidelity (colors, ripple cadence, figure proportions) is tuned to the
  textual v0.2 spec; compare against Devon's `dashboard-mockup.html` reference
  and tweak the constants in `renderer.js` as desired.
- Per-limb pose is explicitly v2 (the model does not emit keypoints yet).
