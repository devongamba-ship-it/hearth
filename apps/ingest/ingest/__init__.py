"""Hearth ingest service.

Receives RuView Tier-2 vitals packets over UDP (`listener.py`), decodes them
with the shared `hearth.packet` codec, and persists readings + fall events to a
local SQLite database (`db.py`). The FastAPI app (`app.py`) exposes node status.

Local-first: raw readings stay on the home device. Only alert events and coarse
status ever leave.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
