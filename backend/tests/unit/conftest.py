"""Unit-test seam for the worker tasks' feature-switch read (OPS-FLAGS S3a).

Every task in `app.workers.tasks` now resolves its switches from the database
before it decides. The older unit tests drive those tasks with a fake sessionmaker
and a patched `tasks.get_settings`, and some assert that a disabled task opens no
session at all. They pin the gate itself, not the switch table, so by default the
seam hands them the patched settings unchanged. `tests/unit/test_worker_feature_switches.py`
puts the real function back and tests the database-backed read.
"""

from __future__ import annotations

import sys

import pytest


async def _settings_as_patched(task: str):  # type: ignore[no-untyped-def]
    tasks = sys.modules["app.workers.tasks"]
    return tasks.get_settings(), None


@pytest.fixture(autouse=True)
def _tasks_read_the_patched_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = sys.modules.get("app.workers.tasks")
    if tasks is not None:
        monkeypatch.setattr(tasks, "_effective_settings", _settings_as_patched)
