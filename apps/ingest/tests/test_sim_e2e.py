"""End-to-end test for acceptance criterion #1:

    python tools/packet_sim.py --scenario normal  (against a running listener)
    -> produces rows in SQLite

Runs the real simulator as a subprocess against a real bound listener, proving
the simulator and the ingest codec agree on the wire format.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
import threading
import time
from pathlib import Path

from sqlalchemy import func, select

from ingest.db import Reading, init_db, make_engine, make_session_factory
from ingest.listener import create_listener

REPO_ROOT = Path(__file__).resolve().parents[3]
SIM = REPO_ROOT / "tools" / "packet_sim.py"


def _serve(url: str, state: dict, ready: threading.Event) -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    engine = make_engine(url)
    init_db(engine)
    session_factory = make_session_factory(engine)

    async def go() -> None:
        transport, _protocol = await create_listener(
            session_factory, host="127.0.0.1", port=0
        )
        state["port"] = transport.get_extra_info("sockname")[1]
        ready.set()
        while not state.get("stop"):
            await asyncio.sleep(0.05)
        transport.close()

    loop.run_until_complete(go())
    loop.close()


def test_packet_sim_normal_produces_rows(tmp_path):
    url = f"sqlite:///{tmp_path / 'e2e.db'}"
    state: dict = {}
    ready = threading.Event()
    listener = threading.Thread(target=_serve, args=(url, state, ready), daemon=True)
    listener.start()
    assert ready.wait(5), "listener did not start"

    proc = subprocess.run(
        [
            sys.executable,
            str(SIM),
            "--scenario", "normal",
            "--target", "127.0.0.1",
            "--port", str(state["port"]),
            "--duration", "5",
            "--speed", "50",
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr

    time.sleep(0.5)  # let the listener drain the last datagrams
    state["stop"] = True
    listener.join(5)

    session_factory = make_session_factory(make_engine(url))
    with session_factory() as s:
        count = s.execute(select(func.count()).select_from(Reading)).scalar()
    assert count >= 1, "simulator produced no rows in SQLite"
