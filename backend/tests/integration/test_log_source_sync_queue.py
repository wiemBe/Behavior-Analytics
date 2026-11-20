"""SCALE-2a: `POST /log-sources/sync` queues; no provider is built in the request. DB-gated."""

from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.collectors.log_source_collector import COLLECTOR_NAME
from app.core.config import get_settings
from app.providers.factory import get_provider
from app.services.locks import collector_lock_key
from app.workers import tasks
from tests.integration.factories import make_instance
from tests.integration.test_fleet_api import (
    ADMIN,
    Client,
    run,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run log-source sync queue integration tests",
    ),
]

URL = "/api/v1/log-sources/sync"
QUEUED = {"queued": True, "already_running": False, "task_id": "task-123"}


@pytest.fixture
def delays(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    calls: list[tuple] = []

    def delay(*args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((args, kwargs))
        return SimpleNamespace(id="task-123")

    monkeypatch.setattr(tasks.sync_log_sources_task, "delay", delay)
    return calls


def _instance(db_schema) -> str:  # type: ignore[no-untyped-def]
    async def setup(s):  # type: ignore[no-untyped-def]
        return await make_instance(s)

    return str(run(setup).id)


def test_provider_is_not_built_in_the_request(db_schema, delays) -> None:
    _instance(db_schema)
    client = Client(ADMIN)

    def explode():  # type: ignore[no-untyped-def]
        raise AssertionError("the route must not build a provider")

    # The old route resolved ProviderDep. With it overridden to raise, a 202 proves
    # nothing in the request built a provider or walked QRadar.
    client.http.app.dependency_overrides[get_provider] = explode

    resp = client.post(URL)

    assert resp.status_code == 202
    assert resp.json() == QUEUED
    assert len(delays) == 1


def test_post_while_running_queues_nothing(db_schema, delays) -> None:
    instance_id = _instance(db_schema)
    key = collector_lock_key(uuid.UUID(instance_id), COLLECTOR_NAME)
    ns = get_settings().collection_advisory_lock_namespace
    loop = asyncio.new_event_loop()
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    conn = loop.run_until_complete(engine.connect())
    loop.run_until_complete(
        conn.execute(text("SELECT pg_advisory_lock(:ns, :key)").bindparams(ns=ns, key=key))
    )
    try:
        resp = Client(ADMIN).post(URL)
        assert resp.status_code == 202
        assert resp.json() == {
            "queued": False,
            "already_running": True,
            "task_id": None,
        }
        assert delays == []
    finally:
        loop.run_until_complete(conn.close())
        loop.run_until_complete(engine.dispose())
        loop.close()


def test_enqueue_failure_is_503(db_schema, monkeypatch) -> None:
    _instance(db_schema)

    def broken(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("broker down")

    monkeypatch.setattr(tasks.sync_log_sources_task, "delay", broken)

    resp = Client(ADMIN).post(URL)

    assert resp.status_code == 503
    assert "broker" not in resp.text
