# apps/caregiver-web

Hearth caregiver status dashboard — the "Mom is okay" at-a-glance app. Built
with React + Vite (per the architecture invariant in `CLAUDE.md`).

**Status:** v1 (BAT-25). Five screens: **Home** status card, **Activity**
(day timeline + weekly trends), **Alerts** (fall-incident replay), **Care
circle** (cascade order + channels), **Settings** (sensitivity, quiet hours,
privacy statement).

## Privacy

The caregiver app shows **coarse status and activity blocks only** — never
cameras, microphones, or per-second sensor data. That constraint is the product.
The activity timeline is room-level blocks ("Kitchen 8:12–8:40"), never a raw
motion graph.

## Develop

```bash
npm install
npm run dev      # local dev server
npm run build    # production build — the CI smoke gate
npm test         # vitest: status transitions, replay, accessibility (axe)
```

## Running against the simulator (end-to-end, no hardware)

The Home card polls the **ingest** service (`/api/status`) and the
**escalation** service (`/api/incidents/active`) and composes them. In dev,
Vite proxies `/ingest → :8000` and `/escalation → :8001` (see `vite.config.js`),
so the browser makes same-origin requests.

From the repo root, in separate terminals:

```bash
# 1. escalation API (dry-run by default — no real SMS/voice)
cd apps/escalation && HEARTH_CONTACTS_FILE=contacts.example.yaml \
  python -m uvicorn escalation.app:app --port 8001

# 2. ingest API + UDP listener + fall-machine runner (share one DB)
cd apps/ingest && HEARTH_DB_PATH=/tmp/hearth.db \
  python -m uvicorn ingest.app:app --port 8000          # status/activity/alerts
cd apps/ingest && HEARTH_DB_PATH=/tmp/hearth.db python -m ingest.listener
cd apps/ingest && HEARTH_DB_PATH=/tmp/hearth.db \
  HEARTH_ESCALATION_URL=http://127.0.0.1:8001/escalate python -m ingest.fall_machine

# 3. the app
cd apps/caregiver-web && npm run dev

# 4. drive a fall through the whole stack
python tools/packet_sim.py --scenario fall_confirmed --speed 10
```

The Home card goes green → red ("Possible fall — cascade running") and the
**Alerts** screen replays the state machine (NORMAL→…→CONFIRMED_FALL) next to
the escalation cascade. Tap **I've got it** to claim; the card shows
"Claimed by …".

Point the proxies elsewhere with `HEARTH_INGEST_URL` / `HEARTH_ESCALATION_URL`
(dev server env). Override the API bases in the browser build with
`VITE_INGEST_BASE` / `VITE_ESCALATION_BASE`.

## No-backend preview

```bash
VITE_USE_MOCK=1 npm run dev   # renders from src/mockData.js, no services needed
```

## What's real vs. stubbed

Real, derived from the local tables: the status card (presence, last activity,
fall-machine state), the fall-incident replay, and the room layout. Stubbed
(clearly flagged `stub` in the payload) until a wall-clock-stamped activity
store lands: the day activity blocks, the activity ring totals, and the weekly
trend cards — the `readings` table carries only ms-since-boot, so per-day
aggregation isn't yet computable. See the BAT-25 PR for the follow-up note.
