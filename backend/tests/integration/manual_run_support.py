"""Run the queued manual-search task the way the worker would (SCALE-2h2).

`POST /searches/{id}/run` only queues. These helpers capture the queued execution ids and run
`tasks.run_manual_search` against the test database with a stand-in provider and permit budget.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.workers import tasks


def capture_queue(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the task's `.delay`: queued execution ids land in the returned list."""
    queued: list[str] = []
    monkeypatch.setattr(tasks.run_manual_search_task, "delay", queued.append)
    return queued


def run_queued(
    db_url: str,
    monkeypatch: pytest.MonkeyPatch,
    execution_ids: list[str],
    *,
    provider: Any,
    budget: Any,
) -> list[dict]:
    """Run the task for each id, with `provider` and `budget` standing in for the real ones."""

    async def work() -> list[dict]:
        engine = create_async_engine(db_url, future=True, poolclass=NullPool)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
        monkeypatch.setattr(tasks, "build_provider", lambda: provider)
        monkeypatch.setattr(tasks, "_manual_search_permit", lambda _session, _settings: budget)
        try:
            return [await tasks.run_manual_search(each) for each in execution_ids]
        finally:
            await engine.dispose()

    return asyncio.run(work())
