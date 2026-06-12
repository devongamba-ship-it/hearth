# apps/escalation

Hearth escalation service. A **per-type policy engine**: every incident type is
defined independently in a registry (`escalation_policies.yaml`) — its own
trigger (owned by the emitter), priority, escalation path, claim semantics,
auto-resolve, quiet-hours, and dedupe. The engine executes whatever the registry
says; adding an incident type is a YAML edit, never a code change.

**Status:** policy engine implemented (BAT-31). The fall cascade (BAT-23) is now
the `fall.confirmed` policy instance, not bespoke code.

## Incident lifecycle (all types)

`OPEN → (claim) CLAIMED → (disposition) RESOLVED`. Pause-mode claims resume to
OPEN if not confirmed within `confirm_window`. Types with `auto_resolve` go
`OPEN → AUTO_RESOLVED` when their trigger condition clears (motion resumes, node
recovers, …). Every transition is an immutable audit-log row (actor, ts, channel).
(On the wire, OPEN is the status value `active`, unchanged from BAT-23/BAT-25.)

## Registry (v1)

The 8 shipped types — see `escalation_policies.example.yaml` for the full table.
`fall.confirmed` (P1) keeps the canonical ladder, now as data:

| When    | Step      | Action                                                        |
|---------|-----------|---------------------------------------------------------------|
| T+0s    | broadcast | SMS + push to every circle contact; log a P1 event            |
| T+60s   | voice     | voice-call contacts in priority order (TTS, "press 1")        |
| T+120s  | operator  | operator/monitoring escalation — v1 stub: P1 event only       |

- **Pluggable senders** (dry-run by default): `broadcast` / `push` / `sms` /
  `voice` / `operator` / `ops_queue` / `digest`. `digest`/`ops_queue` never page.
- **Claim modes:** `pause` (fall — pause & resume), `close` (caregiver ack stops
  the ladder), `none` (digest-only, not claimable).
- **Snooze-for-today** on all caregiver types except `fall.confirmed`.
- **Dedupe:** `fall.confirmed` 5 min/home; wellness types one open instance each.
- **Dispositions** (immutable audit log, each with a required reason code):
  `recovered` / `caregiver_responding` / `advised_call_ems` / `false_positive`.
- **No EMS integration, ever** — the cascade ends at humans who decide to call
  911 themselves.
- **Timers survive restarts:** next-action deadlines are persisted in SQLite and
  re-driven by `process-due`; pending incidents of *all* types are recovered on
  startup.

## Safety default

`ESCALATION_MODE` defaults to `dry_run` everywhere. Live Twilio delivery is
**human-set only** (`ESCALATION_MODE=live`) and never enabled in CI. Twilio
credentials are supplied via env only — never committed. Webhook callbacks are
rejected unless their `X-Twilio-Signature` validates.

## Configuration

- `HEARTH_CONTACTS_FILE` — path to `contacts.yaml` (see `contacts.example.yaml`).
- `HEARTH_POLICIES_FILE` — path to `escalation_policies.yaml` (see
  `escalation_policies.example.yaml`). Unset → the embedded v1 registry is used.
- `HEARTH_ESCALATION_DB_URL` — SQLite URL (default `sqlite:///escalation.db`).
- `ESCALATION_MODE` — `dry_run` (default) or `live`.
- Live mode env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.
- `HEARTH_LOG_FORMAT` — `json` (prod, one structured JSON object per line) or
  `text` (dev, default); level via `HEARTH_LOG_LEVEL` (default `INFO`). Log lines
  carry coarse context (`event`, `incident_id`, `room`, `status`) — never raw
  readings or secrets (BAT-37).

## Endpoints

- `GET  /health`
- `POST /incidents` — typed incident entry: `{ "type", "room", "home_name"? }`.
  Idempotent on the policy's dedupe key (returns the open instance, `created`
  false). The fall machine and BAT-24 wellness rules also reach this via
  `POST /escalate` (defaults `type` to `fall.confirmed`; honors a `type` field).
- `GET  /incidents?state=open` — list incidents (`state=open|all`, `type=` filter).
- `POST /incidents/{id}/claim` — `{ "by" }` (idempotent).
- `POST /incidents/{id}/snooze` — `{ "by" }` (snoozeable types only; 409 on fall).
- `POST /incidents/{id}/disposition` (alias `…/resolve`) — `{ "disposition",
  "reason_code", "by" }`.
- `POST /incidents/clear` — `{ "type", "home_name"? }`: trigger cleared →
  auto-resolve the matching open incident (for `auto_resolve` types).
- `POST /escalate` — legacy fall entry (see above).
- `GET  /api/incidents`, `/api/incidents/active`, `/api/incidents/{id}` —
  caregiver read API (BAT-25).
- `POST /admin/process-due` — drive persisted timers (local poller/cron).
- `POST /webhooks/twilio/sms`, `POST /webhooks/twilio/voice` — ack/claim
  callbacks (signature-validated).

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
```
