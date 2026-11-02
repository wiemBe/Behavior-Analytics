"""Every entry point imports in a fresh interpreter (FIX-15).

The unit suite imports modules in an order of its own, so an import cycle that
only bites when one particular module is imported first stays hidden until the
API, the worker or a CLI starts. Each case here is a new process that imports
exactly one entry point and nothing else.
"""

from __future__ import annotations

import os
import pkgutil
import subprocess
import sys
from pathlib import Path

import pytest

import app.cli

BACKEND_DIR = Path(__file__).resolve().parents[2]

ENTRY_POINTS = [
    "app.core.config",
    "app.main",
    "app.workers.celery_app",
    "app.workers.tasks",
    *sorted(m.name for m in pkgutil.iter_modules(app.cli.__path__, "app.cli.")),
    # Modules that sit in a cycle with `app.providers` and broke when imported first.
    "app.catchall.keyed_rows",
    "app.collectors.bucketed_metrics",
    "app.rules.matches",
    "app.services.ariel_permit",
]


def test_cli_modules_are_discovered() -> None:
    assert {"app.cli.doctor", "app.cli.sync"} <= set(ENTRY_POINTS)


@pytest.mark.parametrize("module", ENTRY_POINTS)
def test_module_imports_in_a_fresh_process(module: str, tmp_path: Path) -> None:
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND_DIR),
        "ENCRYPTION_KEY": "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A=",
    }
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", f"import {module}"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert "ImportError" not in result.stderr, result.stderr
    assert result.returncode == 0, result.stderr
