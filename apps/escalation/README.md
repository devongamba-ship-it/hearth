# apps/escalation

Hearth escalation service. Turns a confirmed fall into an SMS/push → voice →
operator cascade with caregiver acknowledgment (Twilio).

**Status:** cascade engine implemented (BAT-23).

## Cascade ladder (canonical design v0.1)

| When    | Step      | Action                                                        |
|---------|-----------|---------------------------------------------------------------|
| T+0s    | alert     | SMS + push to every circle contact; log a P1 event            |
| T+60s   | voice     | voice-call contacts in priority order (TTS, "press 1")        |
| T+120s  | operator  | operator/monitoring escalation — v1 stub: P1 event only       |

- **Claim semantics:** an ack (SMS `OK`, or voice digit `1`) **claims** the
  incident and **pauses (not cancels)** the cascade. The claimer must confirm
  resolution within 15 minutes or the cascade **resumes at the operator step**.
  All contacts get a "Claimed by [name]" SMS.
- **Dispositions** (immutable audit log, each with a required reason code):
  `recovered` / `caregiver_responding` / `advised_call_ems` / `false_positive`.
- **No EMS integration, ever** — the cascade ends at humans who decide to call
  911 themselves.
- **Timers survive restarts:** next-action deadlines are persisted in SQLite and
  re-driven by `process-due`; pending cascades are recovered on startup.

## Safety default

`ESCALATION_MODE` defaults to `dry_run` everywhere. Live Twilio delivery is
**human-set only** (`ESCALATION_MODE=live`) and never enabled in CI. Twilio
credentials are supplied via env only — never committed. Webhook callbacks are
rejected unless their `X-Twilio-Signature` validates.

## Configuration

- `HEARTH_CONTACTS_FILE` — path to `contacts.yaml` (see `contacts.example.yaml`).
- `HEARTH_ESCALATION_DB_URL` — SQLite URL (default `sqlite:///escalation.db`).
- `ESCALATION_MODE` — `dry_run` (default) or `live`.
- Live mode env: `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`.

## Endpoints

- `GET  /health`
- `POST /escalate` — `{ "room": "kitchen", "home_name": "Rosewood" }` (called by
  the fall confirmation machine on CONFIRMED_FALL).
- `POST /incidents/{id}/resolve` — `{ "disposition", "reason_code", "by" }`.
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
