"""Hearth ingest service (scaffold).

Receives RuView Tier-2 vitals packets and persists alert-relevant state to a
local SQLite database. The UDP listener and shared packet codec land in BAT-21;
this package is the scaffold (FastAPI app + local DB wiring) those build on.

Local-first: raw readings stay on the home device. Only alert events and coarse
status ever leave.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
