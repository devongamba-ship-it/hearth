"""FastAPI application for the ingest service (scaffold).

Exposes a health endpoint so CI, the caregiver app, and operators can confirm
the service is up. Packet ingestion endpoints/listeners are added in BAT-21.
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__

app = FastAPI(title="Hearth Ingest", version=__version__)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "ingest", "version": __version__}
