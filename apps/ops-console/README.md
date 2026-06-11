# Hearth Ops Console (BAT-28)

The monitoring-center surface: a desktop, high-density, queue-driven console for
operators watching homes. Clinical and neutral in tone — the opposite of the
warm caregiver app. React + Vite, same stack as `apps/caregiver-web`.

## Surfaces

- **Queue** — alerts across homes, P1 falls on top with live age timers,
  `j`/`k` to move, `c` to claim, `Enter` to open.
- **Incident view** — live mesh view of the home · state-machine timeline ·
  contact panel (care circle in priority order) · disposition form with the four
  canonical reason codes · the escalation cascade timeline.
- **Home detail** — live view · trends · sensing health (node uptime) ·
  detection thresholds (read-only).
- **History** — 14-day daily-active-minutes chart and a unified event timeline
  (falls + wellness + system) with type pills; resolved incidents append with
  response time; 90-day window in the UI; **Export audit log** for the full log.

## Binding copy + identity rules

All surfaces render through `src/copy.js`, which encodes the v0.2 design update:

- **Alert copy (§4.1):** primary copy is plain language a stressed family member
  understands; the technical state lives in a small code chip beside it; no
  internal jargon in primary copy; numbers only when they help a human decide.
- **Identity honesty:** track labels (A / B / guest-N), never names; each track
  carries the identity-heuristic caption + confidence; co-located tracks get
  deterministic offset slots and colored halos (A teal, B violet); room cards
  read "2 present · A, B".

## Data

Reads the two local services (no new backend):

- ingest `:8000` — `/homes/<id>/layout`, `/api/nodes`, `/homes/<id>/trends`,
  `/api/fall-incidents/<id>`, and the 1 Hz WebSocket `/homes/<id>/live`.
- escalation `:8001` — `/api/incidents`, `/api/incidents/active`,
  `/api/incidents/<id>`, `/incidents/<id>/claim`, `/incidents/<id>/resolve`.

In dev, `vite.config.js` proxies `/ingest` and `/escalation` (and upgrades the
live WebSocket) so the browser stays same-origin. `VITE_USE_MOCK=1` renders from
`src/mockData.js` with no backend.

### Surfaces without a live backend yet

Flagged in the UI so an operator never mistakes them for live data:

- **14-day active-minutes chart** — the ingest trends endpoint is itself a stub
  and the readings table is wall-clock-less, so the series is representative.
- **Detection thresholds** — read-only from `detection.yaml` on the home device;
  there is no remote write API (a follow-up).
- **Care circle** — no persistence endpoint yet (BAT-31 owns the registry);
  seeded from mock data, as the caregiver app does.

## Develop

```
npm install
npm run dev      # http://localhost:5173, proxying the two local services
npm test         # vitest (jsdom + testing-library + jest-axe)
npm run build
```

Run the console against simulated data: start the ingest + escalation services,
drive `python tools/packet_sim.py --scenario fall_confirmed --speed 10`, then
`npm run dev`.
