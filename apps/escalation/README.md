# apps/escalation

Hearth escalation service. Turns a confirmed fall into an SMS → phone-call
escalation ladder with caregiver acknowledgment (Twilio).

**Status:** scaffold (BAT-20). The cascade ladder + Twilio integration land in
BAT-23.

## Safety default

`ESCALATION_MODE` defaults to `dry_run` everywhere. Live Twilio delivery is
**human-set only** (`ESCALATION_MODE=live`) and never enabled in CI. Twilio
credentials are supplied via env only — never committed.

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
```
