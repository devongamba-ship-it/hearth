"""Hearth shared library.

The one module that both the packet simulator (`tools/packet_sim.py`) and the
ingest listener (`apps/ingest`) import, so the RuView Tier-2 wire format is
defined in exactly one place. See `hearth.packet`.
"""

__all__ = ["__version__"]

__version__ = "0.1.0"
