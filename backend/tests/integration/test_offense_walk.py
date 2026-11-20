"""SCALE-2k: direct REST pages through the collector into PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from app.collectors.offense_collector import OffenseCollector
from app.core.config import Settings
from tests.integration.factories import make_instance
from tests.integration.test_offense_collection import (
    NOW,
    StubProvider,
    offense,
    snapshots,
    watermark,
)
from tests.unit.test_rule_inventory_walk import provider

pytestmark = pytest.mark.integration
BOUNDARY = NOW - timedelta(minutes=5)


def row(i, *, time=BOUNDARY):
    return {
        "id": i, "status": "OPEN", "description": f"Offense {i}",
        "last_persisted_time": int(time.timestamp() * 1000),
    }


def configured(monkeypatch):
    settings = Settings(offense_page_attempts=1, offense_walk_max_seconds=10)
    monkeypatch.setattr("app.core.config.get_settings", lambda: settings)
    return settings


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,kind", [
    (503, "upstream"), ([{"id": 6, "description": ["invalid"]}], "malformed"),
])
async def test_page_three_failure_then_boundary_replay_is_idempotent(
    db_session, monkeypatch, failure, kind,
):
    settings = configured(monkeypatch)
    inst = await make_instance(db_session)
    unseen = offense(99, last_updated_time=BOUNDARY - timedelta(minutes=1))
    await OffenseCollector(
        db_session, StubProvider([[unseen]]),
        settings=settings, clock=lambda: NOW - timedelta(minutes=1),
    ).collect(inst)
    rows = [row(i, time=BOUNDARY - timedelta(seconds=1) if i < 4 else BOUNDARY)
            for i in range(1, 6)]
    fail = True
    filters = []
    requests = []

    def handler(request):
        requests.append(request)
        filter_value = request.url.params["filter"]
        filters.append(filter_value)
        since = int(filter_value.split(">")[1])
        selected = [r for r in rows if r["last_persisted_time"] > since]
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        if fail and start == 4:
            if isinstance(failure, int):
                return httpx.Response(failure, json={"message": "untrusted"})
            return httpx.Response(200, json=failure)
        return httpx.Response(200, json=selected[start:start + 2])

    walker = provider(handler)
    try:
        first = await OffenseCollector(
            db_session, walker, settings=settings, clock=lambda: NOW,
        ).collect(inst)
        assert first.snapshots_written == first.offenses_seen == 4
        assert first.partial_failure and not first.truncated
        assert (first.pages_done, first.pages_failed, first.failed_page) == (2, 1, 3)
        assert first.failure_kind == first.error == kind
        mark = await watermark(db_session, inst.id)
        assert mark.watermark_at == BOUNDARY - timedelta(milliseconds=1)
        assert mark.collection_metadata["offense_pass"] == {
            "status": "PARTIAL", "pages_done": 2, "pages_failed": 1,
            "failed_page": 3, "failure_kind": kind,
        }
        assert {r.status for r in await snapshots(db_session, inst.id)} == {"OPEN"}
        fail = False
        second = await OffenseCollector(
            db_session, walker, settings=settings, clock=lambda: NOW + timedelta(minutes=5),
        ).collect(inst)
    finally:
        await walker.aclose()
    assert filters[3] == f"last_persisted_time>{int(BOUNDARY.timestamp() * 1000) - 1}"
    assert second.offenses_seen == 2 and second.snapshots_written == 1 and second.unchanged == 1
    assert not second.partial_failure and second.failure_kind is None
    stored = await snapshots(db_session, inst.id)
    assert sorted(r.qradar_offense_id for r in stored) == [1, 2, 3, 4, 5, 99]
    assert {r.status for r in stored} == {"OPEN"}
    assert mark.watermark_at == BOUNDARY
    assert mark.consecutive_failures == 0 and mark.last_error is None
    assert mark.collection_metadata["offense_pass"]["status"] == "COMPLETE"
    assert len(requests) == 5


@pytest.mark.asyncio
@pytest.mark.parametrize("seed_watermark", [False, True])
async def test_first_page_failure_keeps_watermark_and_stores_nothing(
    db_session, monkeypatch, seed_watermark,
):
    settings = configured(monkeypatch)
    inst = await make_instance(db_session)
    walker = provider(lambda request: httpx.Response(200, json=[]))
    try:
        if seed_watermark:
            await OffenseCollector(
                db_session, walker, settings=settings, clock=lambda: NOW,
            ).collect(inst)
        old = NOW if seed_watermark else None
        walker._client._transport = httpx.MockTransport(
            lambda request: httpx.Response(401, json={"message": "never persist"})
        )
        report = await OffenseCollector(
            db_session, walker, settings=settings, clock=lambda: NOW + timedelta(minutes=5),
        ).collect(inst)
    finally:
        await walker.aclose()
    assert report.snapshots_written == report.offenses_seen == 0
    assert report.partial_failure and report.failure_kind == "auth"
    assert report.pages_done == 0 and report.pages_failed == report.failed_page == 1
    mark = await watermark(db_session, inst.id)
    assert mark.watermark_at == old and mark.last_error == "auth"
    assert mark.consecutive_failures == 1
    assert mark.collection_metadata["offense_pass"]["status"] == "FAILED"
    assert mark.intervals_collected == (1 if seed_watermark else 0)
    assert await snapshots(db_session, inst.id) == []


@pytest.mark.asyncio
async def test_wall_cap_stores_prefix_and_stops_before_boundary(db_session, monkeypatch):
    settings = configured(monkeypatch)
    settings.offense_walk_max_seconds = 0.02
    inst = await make_instance(db_session)
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=[row(1), row(2)])
        await asyncio.sleep(1)
        raise AssertionError("request escaped deadline")

    walker = provider(handler)
    try:
        report = await OffenseCollector(
            db_session, walker, settings=settings, clock=lambda: NOW,
        ).collect(inst)
    finally:
        await walker.aclose()
    assert report.snapshots_written == 2 and report.partial_failure
    assert report.failure_kind == "timeout" and report.pages_done == 1
    assert report.pages_failed == 1 and report.failed_page == 2 and calls == 2
    mark = await watermark(db_session, inst.id)
    assert mark.watermark_at == BOUNDARY - timedelta(milliseconds=1)
    assert mark.collection_metadata["offense_pass"]["status"] == "PARTIAL"


@pytest.mark.asyncio
@pytest.mark.parametrize("missing_timestamp,dropped", [(True, False), (False, True)])
async def test_incomplete_stored_prefix_cannot_advance(db_session, monkeypatch,
                                                    missing_timestamp, dropped):
    settings = configured(monkeypatch)
    inst = await make_instance(db_session)
    rows = [row(1), row(2)]
    if missing_timestamp:
        rows[0].pop("last_persisted_time")
        rows[0]["last_updated_time"] = int(BOUNDARY.timestamp() * 1000)

    def handler(request):
        if request.headers["Range"] == "items=0-1":
            return httpx.Response(200, json=rows)
        return httpx.Response(503)

    walker = provider(handler)
    collector = OffenseCollector(db_session, walker, settings=settings, clock=lambda: NOW)
    store = collector._store

    async def store_with_failure(instance, dto, *, captured_at):
        if dropped and dto.qradar_id == 1:
            raise ValueError("bad row")
        return await store(instance, dto, captured_at=captured_at)

    monkeypatch.setattr(collector, "_store", store_with_failure)
    try:
        report = await collector.collect(inst)
    finally:
        await walker.aclose()
    assert report.partial_failure
    assert report.snapshots_written == (1 if dropped else 2)
    assert (await watermark(db_session, inst.id)).watermark_at is None


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,status", [
    ("complete", "ok"), ("partial", "partial"), ("failed", "failed"),
])
async def test_worker_exposes_actual_collection_outcome(db_session, monkeypatch, mode, status):
    from app.workers import tasks

    configured(monkeypatch)
    inst = await make_instance(db_session)

    def handler(request):
        if mode == "failed" or request.headers["Range"] != "items=0-1":
            return httpx.Response(503)
        rows = [row(1), row(2)] if mode == "partial" else [row(1)]
        return httpx.Response(200, json=rows)

    walker = provider(handler)

    async def each(run, name):
        return await run(db_session, inst, walker)

    monkeypatch.setattr(tasks, "_for_each_instance", each)
    try:
        result = await tasks.collect_offenses()
    finally:
        await walker.aclose()
    assert result["status"] == status
    assert result["pages_done"] == (0 if mode == "failed" else 1)
    assert result["pages_failed"] == (0 if mode == "complete" else 1)
    assert result["failure_kind"] == (None if mode == "complete" else "upstream")
