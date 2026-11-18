"""UIQ-7b: queue the log-source inventory sync and read its last run. DB-gated."""

from __future__ import annotations

import asyncio
import os
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.collectors.log_source_collector import COLLECTOR_NAME
from app.core.config import get_settings
from app.models.monitoring import CollectionWatermark
from app.services.locks import collector_lock_key
from app.workers import tasks
from tests.integration.factories import make_instance
from tests.integration.test_fleet_api import (  # noqa: F401  (fixtures and helpers)
    ADMIN,
    CLOSED,
    NOTHING,
    VIEWER,
    Client,
    _fixed_clock,
    audit_rows,
    run,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run fleet source sync integration tests",
    ),
]

URL = "/api/v1/fleet/sources/sync"


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


def test_sync_needs_admin(db_schema, delays) -> None:
    for principal in (NOTHING, VIEWER):
        client = Client(principal)
        assert client.post(URL).status_code == 403
        assert client.get(URL).status_code == 403
    assert delays == []
    assert audit_rows("fleet.sources_sync") == []


def test_post_queues_once_and_audits(db_schema, delays) -> None:
    _instance(db_schema)
    resp = Client(ADMIN).post(URL)
    assert resp.status_code == 202
    assert resp.json() == {"queued": True, "already_running": False, "task_id": "task-123"}
    assert len(delays) == 1
    rows = audit_rows("fleet.sources_sync")
    assert len(rows) == 1 and rows[0].actor == "admin"
    assert rows[0].detail["queued"] is True


def test_post_while_running_queues_nothing(db_schema, delays) -> None:
    instance_id = _instance(db_schema)
    import uuid

    key = collector_lock_key(uuid.UUID(instance_id), COLLECTOR_NAME)
    ns = get_settings().collection_advisory_lock_namespace
    loop = asyncio.new_event_loop()
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
    conn = loop.run_until_complete(engine.connect())
    loop.run_until_complete(
        conn.execute(text("SELECT pg_advisory_lock(:ns, :key)").bindparams(ns=ns, key=key))
    )
    try:
        client = Client(ADMIN)
        body = client.get(URL).json()
        assert body["running"] is True and body["instances"][0]["status"] == "RUNNING"
        resp = client.post(URL)
        assert resp.status_code == 202
        assert resp.json() == {"queued": False, "already_running": True, "task_id": None}
        assert delays == []
        assert len(audit_rows("fleet.sources_sync")) == 1
    finally:
        loop.run_until_complete(conn.close())
        loop.run_until_complete(engine.dispose())
        loop.close()
    # Released: the next request queues again.
    assert Client(ADMIN).post(URL).json()["queued"] is True
    assert len(delays) == 1


def test_enqueue_failure_is_503_and_audited(db_schema, monkeypatch) -> None:
    _instance(db_schema)

    def broken(*a, **k):  # type: ignore[no-untyped-def]
        raise RuntimeError("broker down")

    monkeypatch.setattr(tasks.sync_log_sources_task, "delay", broken)
    resp = Client(ADMIN).post(URL)
    assert resp.status_code == 503
    assert "broker" not in resp.text
    rows = audit_rows("fleet.sources_sync")
    assert len(rows) == 1 and rows[0].outcome == "FAILURE"


def test_status_shape_per_state(db_schema, delays) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        never = await make_instance(s)
        ok = await make_instance(s)
        bad = await make_instance(s)
        off = await make_instance(s)
        off.enabled = False
        for inst, fails in ((ok, 0), (bad, 2)):
            s.add(CollectionWatermark(
                instance_id=inst.id, collector=COLLECTOR_NAME,
                last_run_at=CLOSED, watermark_at=CLOSED - timedelta(hours=1) if fails else CLOSED,
                consecutive_failures=fails, last_error="secret detail"))
        return {"never": never.name, "ok": ok.name, "bad": bad.name, "off": off.name}

    names = run(setup)
    resp = Client(ADMIN).get(URL)
    assert resp.status_code == 200
    body = resp.json()
    assert body["running"] is False
    by = {i["instance_name"]: i for i in body["instances"]}
    assert names["off"] not in by
    assert by[names["never"]]["status"] == "NEVER" and by[names["never"]]["last_run_at"] is None
    assert by[names["ok"]]["status"] == "OK"
    assert by[names["ok"]]["last_success_at"].startswith("2026-10-13T10:05")
    assert by[names["bad"]]["status"] == "FAILED"
    assert by[names["bad"]]["consecutive_failures"] == 2
    assert "secret detail" not in resp.text
    assert delays == []


def _pass_metadata(**over):  # type: ignore[no-untyped-def]
    run = {
        "status": "COMPLETE",
        "finished_at": "2026-10-13T10:05:00+00:00",
        "elapsed_ms": 83000,
        "seen": 8012,
        "created": 12,
        "updated": 7990,
        "pages_done": 17,
        "pages_failed": 0,
        "failed_page": None,
        "failure_kind": None,
    }
    run.update(over)
    return {"inventory_pass": run}


def test_status_carries_the_last_pass_and_says_partial(db_schema, delays) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        done = await make_instance(s)
        part = await make_instance(s)
        none = await make_instance(s)
        s.add(CollectionWatermark(
            instance_id=done.id, collector=COLLECTOR_NAME, last_run_at=CLOSED,
            watermark_at=CLOSED, consecutive_failures=0, collection_metadata=_pass_metadata()))
        # A partial pass failed to finish, so the failure streak counts it: PARTIAL still wins.
        s.add(CollectionWatermark(
            instance_id=part.id, collector=COLLECTOR_NAME, last_run_at=CLOSED,
            watermark_at=CLOSED - timedelta(hours=1), consecutive_failures=1,
            collection_metadata=_pass_metadata(
                status="PARTIAL", seen=4000, pages_done=8, pages_failed=1, failed_page=9,
                failure_kind="timeout")))
        s.add(CollectionWatermark(
            instance_id=none.id, collector=COLLECTOR_NAME, last_run_at=CLOSED,
            watermark_at=CLOSED, consecutive_failures=0))
        return {"done": done.name, "part": part.name, "none": none.name}

    names = run(setup)
    resp = Client(ADMIN).get(URL)
    assert resp.status_code == 200
    by = {i["instance_name"]: i for i in resp.json()["instances"]}
    assert by[names["done"]]["status"] == "OK"
    assert by[names["done"]]["last_pass"]["seen"] == 8012
    assert by[names["done"]]["last_pass"]["status"] == "COMPLETE"
    part = by[names["part"]]
    assert part["status"] == "PARTIAL"
    assert (part["last_pass"]["failed_page"], part["last_pass"]["failure_kind"]) == (9, "timeout")
    assert (part["last_pass"]["seen"], part["last_pass"]["pages_failed"]) == (4000, 1)
    # A watermark that never recorded a pass has no pass to show.
    assert by[names["none"]]["last_pass"] is None
