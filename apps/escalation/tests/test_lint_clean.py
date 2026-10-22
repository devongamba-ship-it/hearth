"""Lint gate: `ruff check .` must exit 0 for this app.

Encodes BAT-57 acceptance criterion #1 ("the failing command(s) pass") as a
test, so a future ruff/rule drift that reintroduces the regression is caught by
the suite rather than only by the nightly Watchtower QA job.

The app pins ruff exactly and selects its lint rules explicitly (see
pyproject.toml), so this check is deterministic regardless of what ruff version
happens to be resolvable from PyPI.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]


def test_ruff_check_is_clean() -> None:
    ruff = shutil.which("ruff")
    if ruff is None:
        pytest.skip("ruff not installed; install the app's [dev] extra to run the lint gate")

    result = subprocess.run(
        [ruff, "check", "."],
        cwd=APP_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, (
        "ruff check . reported violations:\n"
        f"{result.stdout}\n{result.stderr}"
    )
