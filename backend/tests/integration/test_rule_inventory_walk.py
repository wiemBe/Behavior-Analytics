"""SCALE-2f: exercise the collector through real paginated REST reads and SQL writes."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from time import perf_counter

import httpx
import pytest
from sqlalchemy import select

from app.collectors.rule_collector import COLLECTOR_NAME, RuleCollector
from app.core.config import Settings
from app.models.monitoring import CollectionWatermark
from app.models.rule import AnalyticsRule, RuleDependency
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import recorded_statements
from tests.unit.test_rule_inventory_walk import provider, row

pytestmark = pytest.mark.integration
NOW = datetime(2026, 11, 20, tzinfo=UTC)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["timeout", "wall", "ceiling", "auth", "building_block"])
async def test_partial_upserts_keeps_unseen_and_complete_watermark(db_session, failure):
    instance = await make_instance(db_session)
    old = AnalyticsRule(instance_id=instance.id, qradar_id=999, name="Keep SOC curation")
    seen = AnalyticsRule(
        instance_id=instance.id, qradar_id=0, name="Old name", owner="SOC", soc_notes="Keep notes"
    )
    watermark = CollectionWatermark(
        instance_id=instance.id, collector=COLLECTOR_NAME, watermark_at=NOW - timedelta(days=1),
        intervals_collected=1, collection_metadata={"other": "preserved"},
    )
    db_session.add_all([old, seen, watermark])
    await db_session.flush()
    calls = []

    async def handler(request):
        calls.append(request)
        if request.url.path.endswith("building_blocks"):
            if failure == "building_block":
                raise httpx.ReadTimeout("secret upstream body", request=request)
            return httpx.Response(200, json=[])
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        if start == 4 and failure not in {"ceiling", "building_block"}:
            if failure == "wall":
                await asyncio.Event().wait()
            if failure == "auth":
                return httpx.Response(403)
            raise httpx.ReadTimeout("secret upstream body", request=request)
        return httpx.Response(200, json=[row(i) for i in range(start, min(start + 2, 4))])

    walker = provider(handler, max_pages=2 if failure == "ceiling" else 20)
    settings = Settings(rule_walk_max_seconds=0.05 if failure == "wall" else 10)
    try:
        report = await RuleCollector(
            db_session, walker, settings=settings, clock=lambda: NOW
        ).sync(instance)
    finally:
        await walker.aclose()
    assert report.status == "PARTIAL" and report.partial_failure
    assert (report.rules_seen, report.created, report.updated) == (4, 3, 1)
    assert watermark.watermark_at == NOW - timedelta(days=1)
    assert watermark.intervals_collected == 1 and watermark.consecutive_failures == 1
    assert seen.owner == "SOC" and seen.soc_notes == "Keep notes"
    assert seen.name == "Rule 0" and old.name == "Keep SOC curation" and old.enabled
    assert len((await db_session.scalars(select(AnalyticsRule))).all()) == 5
    run = watermark.collection_metadata["inventory_pass"]
    assert watermark.collection_metadata["other"] == "preserved"
    assert run["status"] == "PARTIAL" and run["seen"] == 4
    assert run["pages_done"] == report.pages_done >= 2
    assert run["pages_failed"] == report.pages_failed >= 1
    assert run["failed_page"] == 3 + (failure == "building_block")
    assert run["failure_kind"] == (
        "upstream" if failure == "ceiling" else "auth" if failure == "auth" else "timeout"
    )
    assert "secret" not in watermark.last_error
    if failure == "auth":
        assert not any(r.url.path.endswith("building_blocks") for r in calls)


@pytest.mark.asyncio
async def test_complete_walk_merges_building_blocks_and_remains_idempotent(db_session):
    instance = await make_instance(db_session)

    def handler(request):
        if request.url.path.endswith("building_blocks"):
            return httpx.Response(200, json=[row(1)])
        return httpx.Response(200, json=[row(1), row(2)])

    walker = provider(handler, page_size=500)
    try:
        collector = RuleCollector(db_session, walker, clock=lambda: NOW)
        first = await collector.sync(instance)
        second = await collector.sync(instance)
    finally:
        await walker.aclose()
    assert first.status == second.status == "COMPLETE"
    assert first.created == 2 and second.created == second.updated == 0 and second.unchanged == 2
    assert first.building_blocks == 1 and first.pages_done == 2
    rows = (await db_session.scalars(select(AnalyticsRule).order_by(AnalyticsRule.qradar_id))).all()
    assert [r.is_building_block for r in rows] == [True, False]
    watermark = await db_session.scalar(select(CollectionWatermark))
    assert watermark.watermark_at == NOW and watermark.intervals_collected == 2
    assert watermark.last_error is None and watermark.consecutive_failures == 0


@pytest.mark.asyncio
async def test_failure_before_any_page_is_failed_and_does_not_claim_zero_inventory(db_session):
    instance = await make_instance(db_session)
    walker = provider(lambda r: httpx.Response(401))
    try:
        report = await RuleCollector(db_session, walker, clock=lambda: NOW).sync(instance)
    finally:
        await walker.aclose()
    watermark = await db_session.scalar(select(CollectionWatermark))
    assert report.status == "FAILED" and report.partial_failure
    assert report.pages_done == 0 and report.failure_kind == "auth"
    assert watermark.watermark_at is None and watermark.intervals_collected == 0
    assert watermark.collection_metadata["inventory_pass"]["status"] == "FAILED"
    assert report.pages_failed == 1


@pytest.mark.asyncio
async def test_sql_statement_count_is_constant_within_a_page(db_session):
    counts = []
    for size in (1, 500):
        instance = await make_instance(db_session)

        def handler(request, total=size):
            rows = (
                [] if request.url.path.endswith("building_blocks")
                else [row(i) for i in range(total)]
            )
            return httpx.Response(200, json=rows)

        walker = provider(handler, page_size=501)
        started = perf_counter()
        try:
            with recorded_statements(db_session) as statements:
                report = await RuleCollector(db_session, walker, clock=lambda: NOW).sync(instance)
            counts.append(len(statements))
        finally:
            await walker.aclose()
        assert report.created == size and report.dependencies_written == size
        assert len((await db_session.scalars(
            select(RuleDependency).join(AnalyticsRule).where(
                AnalyticsRule.instance_id == instance.id
            )
        )).all()) == size
        print(f"rules={size} statements={counts[-1]} elapsed={perf_counter() - started:.3f}s")
    assert counts[0] == counts[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["COMPLETE", "PARTIAL", "FAILED"])
async def test_worker_exposes_inventory_status(db_session, monkeypatch, status):
    from app.collectors.rule_collector import RuleSyncReport
    from app.workers import tasks

    instance = await make_instance(db_session)

    async def sync(self, selected):
        return RuleSyncReport(
            instance_id=selected.id, status=status, partial_failure=status != "COMPLETE"
        )

    async def each(run, name, **kwargs):
        return await run(db_session, instance, object())

    monkeypatch.setattr(RuleCollector, "sync", sync)
    monkeypatch.setattr(tasks, "_for_each_instance", each)
    assert (await tasks.sync_rule_inventory())["status"] == {
        "COMPLETE": "ok", "PARTIAL": "partial", "FAILED": "failed"
    }[status]
