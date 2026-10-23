"""A real PostgreSQL permit stays held across the provider lifecycle."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.providers.qradar_rest import QRadarRestProvider
from app.services.ariel_permit import ArielPermitBudget, ArielPermitUnavailable

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

BASE_URL = "https://qradar.example.internal"
START = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)


def provider(handler, budget: ArielPermitBudget) -> QRadarRestProvider:  # type: ignore[no-untyped-def]
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL + "/api",
        headers={"SEC": "test-token", "Version": "20.0", "Accept": "application/json"},
    )
    return QRadarRestProvider(
        base_url=BASE_URL,
        sec_token="test-token",
        client=client,
        permit=budget,
    )


async def test_global_cap_blocks_second_post_until_first_lifecycle_releases(
    db_schema: str,
) -> None:
    settings = Settings(
        _env_file=None,
        ariel_max_concurrent_searches=1,
        ariel_global_max_concurrent_searches=1,
        ariel_permit_reserved_live_slots=0,
        ariel_permit_wait_seconds=0.1,
        ariel_permit_poll_seconds=0.02,
    )
    first_engine = create_async_engine(db_schema)
    second_engine = create_async_engine(db_schema)
    first_budget = ArielPermitBudget(first_engine, settings, instance_id=None)
    second_budget = ArielPermitBudget(second_engine, settings, instance_id=None)
    first_post_started = asyncio.Event()
    release_first_post = asyncio.Event()
    second_posts = 0

    async def first_handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            first_post_started.set()
            await release_first_post.wait()
            return httpx.Response(201, json={"search_id": "first", "status": "WAIT"})
        if path.endswith("/ariel/searches/first/results"):
            return httpx.Response(200, json={"events": []})
        if path.endswith("/ariel/searches/first"):
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    def second_handler(request: httpx.Request) -> httpx.Response:
        nonlocal second_posts
        if request.method == "POST" and request.url.path.endswith("/ariel/searches"):
            second_posts += 1
            return httpx.Response(201, json={"search_id": "second", "status": "WAIT"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    first = provider(first_handler, first_budget)
    second = provider(second_handler, second_budget)
    first_task = asyncio.create_task(first.get_log_source_metrics(START, END))
    try:
        await asyncio.wait_for(first_post_started.wait(), timeout=2)
        with pytest.raises(ArielPermitUnavailable):
            await second.get_log_source_metrics(START, END)
        assert second_posts == 0

        release_first_post.set()
        assert await asyncio.wait_for(first_task, timeout=2) == []
    finally:
        release_first_post.set()
        if not first_task.done():
            first_task.cancel()
        await asyncio.gather(first_task, return_exceptions=True)
        await first.aclose()
        await second.aclose()
        await first_engine.dispose()
        await second_engine.dispose()


async def test_second_lifecycle_waits_and_posts_only_after_the_first_releases(
    db_schema: str,
) -> None:
    settings = Settings(
        _env_file=None,
        ariel_max_concurrent_searches=1,
        ariel_global_max_concurrent_searches=1,
        ariel_permit_reserved_live_slots=0,
        ariel_permit_wait_seconds=5.0,
        ariel_permit_poll_seconds=0.02,
    )
    first_engine = create_async_engine(db_schema)
    second_engine = create_async_engine(db_schema)
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    first_released = False
    posts_before_release = 0

    async def first_handler(request: httpx.Request) -> httpx.Response:
        nonlocal first_released
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            first_started.set()
            await release_first.wait()
            return httpx.Response(201, json={"search_id": "s", "status": "WAIT"})
        first_released = True
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": []})
        return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})

    def second_handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts_before_release
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            if not first_released:
                posts_before_release += 1
            return httpx.Response(201, json={"search_id": "s", "status": "WAIT"})
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": []})
        return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})

    first = provider(first_handler, ArielPermitBudget(first_engine, settings, instance_id=None))
    second = provider(second_handler, ArielPermitBudget(second_engine, settings, instance_id=None))
    first_task = asyncio.create_task(first.get_log_source_metrics(START, END))
    second_task: asyncio.Task | None = None
    try:
        await asyncio.wait_for(first_started.wait(), timeout=2)
        second_task = asyncio.create_task(second.get_log_source_metrics(START, END))
        await asyncio.sleep(0.3)
        assert not second_task.done()
        assert posts_before_release == 0

        release_first.set()
        assert await asyncio.wait_for(first_task, timeout=3) == []
        assert await asyncio.wait_for(second_task, timeout=3) == []
    finally:
        release_first.set()
        tasks = [t for t in (first_task, second_task) if t is not None]
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await first.aclose()
        await second.aclose()
        await first_engine.dispose()
        await second_engine.dispose()
