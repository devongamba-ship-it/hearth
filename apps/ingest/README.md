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

## Develop

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
ruff check .
pytest
uvicorn ingest.app:app --reload   # health at /health
```
