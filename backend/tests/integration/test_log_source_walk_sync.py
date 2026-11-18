"""FIX-74: the inventory pass on a console of thousands, against a fake paginated QRadar.

The provider is the real REST provider on an `httpx.MockTransport`: the `fields=` it sends, the
per-page retry and the page skipping are exercised for real, the console is a counter and a
list. Database-gated.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.cli import sync as sync_cli
from app.collectors.log_source_collector import (
    COLLECTOR_NAME,
    INVENTORY_RUN_KEY,
    RUN_COMPLETE,
    RUN_FAILED,
    RUN_PARTIAL,
    LogSourceCollector,
)
from app.core.config import Settings
from app.models.entity import EntityMention
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.qradar_rest import LOG_SOURCE_LIST_FIELDS, QRadarRestProvider
from app.services.locks import CollectorAdvisoryLock
from app.workers import tasks
from tests.integration.factories import make_instance

pytestmark = pytest.mark.integration

TOTAL = 8_000
PAGE = 500
CLOCK = [datetime(2026, 11, 18, 22, 10, tzinfo=UTC)]


def _clock() -> datetime:
    CLOCK[0] += timedelta(seconds=1)
    return CLOCK[0]


class FakeConsole:
    """Types, groups, about and a paginated log source list of `total` rows."""

    def __init__(self, total: int = TOTAL) -> None:
        self.total = total
        # page (1-based) -> how many times it times out before it answers; None = for good.
        self.slow: dict[int, int | None] = {}
        self.forbid_all_pages_after: int | None = None
        self.log_source_requests: list[httpx.Request] = []
        self.hits: dict[int, int] = {}

    def row(self, index: int, request: httpx.Request) -> dict:
        full = {
            "id": index,
            "name": f"f{index % 90:02d}-edge-{index:05d}",
            "description": None,
            "type_id": 7,
            "protocol_type_id": 0,
            "enabled": True,
            "status": {"status": "SUCCESS", "last_updated": 0, "messages": []},
            "credibility": 5,
            "target_event_collector_id": 1,
            "last_event_time": 1_784_649_600_000,
            "creation_date": 1_784_476_800_000,
            "modified_date": 1_784_563_200_000,
            "auto_discovered": True,
            "group_ids": [],
            "log_source_identifiers": [{"identifier": f"10.1.{index // 250}.{index % 250}"}],
            "internal": False,
            "protocol_parameters": [{"name": "p", "value": "x" * 300}],
        }
        wanted = set(request.url.params["fields"].split(","))
        return {k: v for k, v in full.items() if k in wanted}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/system/about"):
            return httpx.Response(200, json={"external_version": "7.5.0", "build_version": "1"})
        if path.endswith("/log_source_types"):
            return httpx.Response(200, json=[{"id": 7, "name": "F5 BIG-IP ASM"}])
        if path.endswith("/log_source_groups"):
            return httpx.Response(200, json=[])
        assert path.endswith("/log_sources"), path
        self.log_source_requests.append(request)
        start, end = request.headers["Range"].removeprefix("items=").split("-")
        page = int(start) // PAGE + 1
        self.hits[page] = self.hits.get(page, 0) + 1
        if page in self.slow:
            budget = self.slow[page]
            if budget is None or self.hits[page] <= budget:
                raise httpx.ReadTimeout("slow", request=request)
        # ids start at 1, so page 1 is ids 1..500
        ids = range(int(start) + 1, min(int(end) + 2, self.total + 1))
        return httpx.Response(200, json=[self.row(i, request) for i in ids])


def _provider(console: FakeConsole) -> QRadarRestProvider:
    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(console), base_url="https://qradar.example/api"
    )
    return QRadarRestProvider(
        base_url="https://qradar.example",
        sec_token="t",
        client=client,
        page_size=PAGE,
        max_retries=1,
        sleep=no_sleep,
    )


def _settings(**kw: object) -> Settings:
    return Settings(_env_file=None, log_source_page_attempts=2, **kw)  # type: ignore[arg-type]


async def _run(db_session, instance, console: FakeConsole, **settings: object):  # type: ignore[no-untyped-def]
    provider = _provider(console)
    try:
        return await LogSourceCollector(
            db_session, provider, settings=_settings(**settings), clock=_clock
        ).sync(instance)
    finally:
        await provider.aclose()


async def _count(session, instance) -> int:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(func.count()).select_from(LogSource).where(LogSource.instance_id == instance.id)
    )


async def _mark(session, instance) -> CollectionWatermark:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )


@pytest.mark.asyncio
async def test_a_complete_pass_of_8000_sources_asks_for_slim_rows_and_records_the_run(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    console = FakeConsole()
    walker = _provider(FakeConsole())
    begun = time.perf_counter()
    walked = await walker.walk_log_sources()
    walk_only = time.perf_counter() - begun
    await walker.aclose()
    assert walked.complete and len(walked.sources) == TOTAL
    begun = time.perf_counter()
    report = await _run(db_session, instance, console)
    elapsed = time.perf_counter() - begun
    print(
        f"\nFIX-74 measured (fake console, in-process transport): walk of 8,000 sources "
        f"{walk_only:.2f} s; full pass (walk + upsert + mentions + classification) {elapsed:.2f} s"
    )

    assert (report.status, report.log_sources_seen, report.created) == (RUN_COMPLETE, TOTAL, TOTAL)
    # 16 full pages and the empty page that shows the list ended (8,000 is a whole number of pages).
    assert report.pages_done == 17 and report.pages_failed == 0
    assert await _count(db_session, instance) == TOTAL
    # Every page asked for the slim field list and nothing carrying protocol parameters.
    assert len(console.log_source_requests) == 17
    assert {r.url.params["fields"] for r in console.log_source_requests} == {
        LOG_SOURCE_LIST_FIELDS
    }
    assert all(
        "protocol_parameters" not in r.url.query.decode() for r in console.log_source_requests
    )
    assert {r.url.params["sort"] for r in console.log_source_requests} == {"+id"}
    mark = await _mark(db_session, instance)
    run = mark.collection_metadata[INVENTORY_RUN_KEY]
    assert (run["status"], run["seen"], run["created"], run["updated"]) == (
        RUN_COMPLETE, TOTAL, TOTAL, 0
    )
    assert (run["pages_done"], run["pages_failed"], run["failed_page"]) == (17, 0, None)
    assert run["failure_kind"] is None and run["elapsed_ms"] >= 0 and run["finished_at"]
    assert mark.watermark_at is not None and mark.consecutive_failures == 0
    assert mark.last_error is None


@pytest.mark.asyncio
async def test_a_timeout_on_page_9_that_succeeds_on_retry_keeps_the_pass_complete(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    console = FakeConsole()
    console.slow = {9: 1}
    report = await _run(db_session, instance, console)
    assert report.status == RUN_COMPLETE and report.created == TOTAL
    assert console.hits[9] == 2 and report.pages_failed == 0


@pytest.mark.asyncio
async def test_a_page_that_fails_for_good_makes_a_partial_pass_that_loses_nothing(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    # Everything is known first (a complete pass), then names change upstream and page 9 dies.
    first = await _run(db_session, instance, FakeConsole())
    assert first.status == RUN_COMPLETE
    before = await _mark(db_session, instance)
    complete_at = before.watermark_at
    mentions_before = await db_session.scalar(
        select(func.count()).select_from(EntityMention).where(EntityMention.source == "LOG_SOURCE")
    )
    assert mentions_before and mentions_before >= TOTAL

    console = FakeConsole(total=TOTAL + 300)  # 300 newly visible sources, ids 8001..8300
    console.slow = {9: None}
    report = await _run(db_session, instance, console)

    assert report.status == RUN_PARTIAL and not report.partial_failure
    assert (report.failed_page, report.failure_kind) == (9, "timeout")
    assert report.pages_done == 16 and report.pages_failed == 1  # 17 pages: 8,300 rows
    # Page 9 (ids 4001..4500) was not read; nothing was marked missing or deleted.
    assert report.log_sources_seen == TOTAL + 300 - PAGE
    assert report.created == 300 and report.updated == TOTAL - PAGE
    assert await _count(db_session, instance) == TOTAL + 300
    # The unseen page's mentions stayed (absence is concluded only from a complete walk); the
    # 300 new sources added theirs.
    mentions_after = await db_session.scalar(
        select(func.count()).select_from(EntityMention).where(EntityMention.source == "LOG_SOURCE")
    )
    assert mentions_after >= mentions_before
    mark = await _mark(db_session, instance)
    assert mark.watermark_at == complete_at  # a partial pass is not a completed observation
    assert mark.consecutive_failures == 1
    assert mark.last_error == "partial: timeout on page 9"
    run = mark.collection_metadata[INVENTORY_RUN_KEY]
    assert run["status"] == RUN_PARTIAL and run["failed_page"] == 9

    # A complete pass afterwards reconciles: page 9 is read again, the watermark moves.
    console.slow = {}
    final = await _run(db_session, instance, console)
    assert final.status == RUN_COMPLETE and final.log_sources_seen == TOTAL + 300
    assert final.created == 0 and final.updated == TOTAL + 300
    mark = await _mark(db_session, instance)
    assert mark.watermark_at is not None and mark.watermark_at > complete_at
    assert mark.consecutive_failures == 0 and mark.last_error is None


@pytest.mark.asyncio
async def test_a_partial_first_pass_does_not_make_the_rest_of_the_console_new(db_session) -> None:
    """The first pass is a bootstrap until one completes, so no review alerts flood later."""
    from app.models.alert import Alert

    instance = await make_instance(db_session)
    console = FakeConsole(total=2_000)
    console.slow = {2: None}
    partial = await _run(db_session, instance, console)
    assert partial.status == RUN_PARTIAL and partial.created == 1_500
    console.slow = {}
    complete = await _run(db_session, instance, console)
    assert complete.status == RUN_COMPLETE and complete.created == 500
    alerts = await db_session.scalar(select(func.count()).select_from(Alert))
    assert alerts == 0


@pytest.mark.asyncio
async def test_a_pass_that_reads_nothing_is_failed_and_changes_no_source(db_session) -> None:
    instance = await make_instance(db_session)
    assert (await _run(db_session, instance, FakeConsole(total=400))).status == RUN_COMPLETE
    mark = await _mark(db_session, instance)
    complete_at = mark.watermark_at

    broken = FakeConsole(total=400)
    broken.slow = {1: None}
    report = await _run(db_session, instance, broken)
    assert report.status == RUN_FAILED and report.partial_failure
    assert (report.failed_page, report.failure_kind) == (1, "timeout")
    assert await _count(db_session, instance) == 400
    mark = await _mark(db_session, instance)
    assert mark.watermark_at == complete_at and mark.consecutive_failures == 1
    assert mark.last_error == "failed: timeout on page 1"
    assert mark.collection_metadata[INVENTORY_RUN_KEY]["status"] == RUN_FAILED


@pytest.mark.asyncio
async def test_a_failure_before_the_walk_is_failed_with_its_kind(db_session) -> None:
    instance = await make_instance(db_session)

    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401)

    async def no_sleep(_s: float) -> None:
        return None

    provider = QRadarRestProvider(
        base_url="https://qradar.example",
        sec_token="t",
        client=httpx.AsyncClient(
            transport=httpx.MockTransport(refuse), base_url="https://qradar.example/api"
        ),
        sleep=no_sleep,
    )
    try:
        report = await LogSourceCollector(
            db_session, provider, settings=_settings(), clock=_clock
        ).sync(instance)
    finally:
        await provider.aclose()
    assert (report.status, report.failure_kind) == (RUN_FAILED, "auth")
    assert report.error == "auth"
    assert (await _mark(db_session, instance)).last_error == "failed: auth"


@pytest.mark.asyncio
async def test_a_pass_that_finds_the_lock_taken_does_nothing_and_says_so(db_session) -> None:
    instance = await make_instance(db_session)
    settings = _settings()
    async with CollectorAdvisoryLock(db_session, settings, instance.id, COLLECTOR_NAME) as got:
        assert got
        console = FakeConsole(total=10)
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as other:
                provider = _provider(console)
                report = await LogSourceCollector(
                    other, provider, settings=settings, clock=_clock
                ).sync(instance)
                await provider.aclose()
        finally:
            await engine.dispose()
    assert report.skipped_locked and report.status is None
    assert console.log_source_requests == []


def _wire_task(monkeypatch: pytest.MonkeyPatch, console: FakeConsole) -> None:
    def maker():  # type: ignore[no-untyped-def]
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"], poolclass=NullPool)
        return async_sessionmaker(engine, expire_on_commit=False)

    monkeypatch.setattr(tasks, "get_sessionmaker", maker)
    monkeypatch.setattr(
        tasks, "build_provider_for_instance", lambda *a, **k: _provider(console)
    )


async def _commit_instance(db_session) -> str:  # type: ignore[no-untyped-def]
    row = QRadarInstance(
        name="cli-lab", console_host="https://qradar.example", provider_kind="rest",
        sec_token="t",
    )
    db_session.add(row)
    await db_session.commit()
    return row.name


@pytest.mark.parametrize(
    ("slow", "total", "word", "code", "absent", "present"),
    [
        ({}, 700, "ok", 0, ["error=", "unknown"], ["seen=700", "created=700", "pages_done=2"]),
        (
            {2: None},
            1_200,
            "partial",
            1,
            ["unknown"],
            ["seen=700", "failed_page=2", "failure_kind=timeout", "error=timeout on page 2"],
        ),
        (
            {1: None},
            400,
            "failed",
            1,
            [],
            ["seen=0", "failed_page=1", "failure_kind=timeout", "error=timeout on page 1"],
        ),
    ],
)
@pytest.mark.asyncio
async def test_the_cli_prints_the_real_status_counts_and_error_class(
    db_session, monkeypatch, capsys, slow, total, word, code, absent, present
) -> None:  # type: ignore[no-untyped-def]
    name = await _commit_instance(db_session)
    console = FakeConsole(total=total)
    console.slow = slow
    _wire_task(monkeypatch, console)
    exit_code = await sync_cli._run(["log-sources"], name, False)
    out = capsys.readouterr().out
    assert exit_code == code
    assert f" {word} " in out
    for needle in present:
        assert needle in out, out
    for needle in absent:
        assert needle not in out, out


@pytest.mark.asyncio
async def test_the_cli_json_keeps_every_key(db_session, monkeypatch, capsys) -> None:  # type: ignore[no-untyped-def]
    import json

    name = await _commit_instance(db_session)
    _wire_task(monkeypatch, FakeConsole(total=10))
    assert await sync_cli._run(["log-sources"], name, True) == 0
    entry = json.loads(capsys.readouterr().out)["log-sources"]["results"][0]
    assert entry["status"] == "ok" and entry["error"] is None
    assert {"pages_done", "pages_failed", "failed_page", "failure_kind"} <= set(entry)

