"""X3a exercises the collector and doctor entry without writing ABSENT spans."""

import asyncio
import importlib.util
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.cli import doctor as doctor_cli
from app.collectors.log_source_collector import COLLECTOR_NAME, LogSourceCollector
from app.core.config import Settings
from app.models.log_source import LogSource
from app.models.log_source_pause import LogSourcePause
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderUnavailableError, QRadarProvider
from app.providers.dto import LogSourceDTO, LogSourceWalk
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.services import inventory_sync
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
NOW = datetime(2026, 11, 22, 12, tzinfo=UTC)


def settings(on=True):
    return Settings(_env_file=None, qradar_source_pause_enabled=on,
                    inventory_new_source_alerts_enabled=False)


class Inventory(MockQRadarProvider):
    def __init__(self, count=3):
        super().__init__()
        self.ids = list(range(1, count + 1))
        self.complete = True
        self.types_failed = self.failed = False

    async def list_log_source_types(self):
        if self.types_failed:
            raise ProviderUnavailableError("types unavailable")
        return []

    async def list_log_source_groups(self):
        return []

    async def walk_log_sources(self, **kwargs):
        if self.failed:
            raise ProviderUnavailableError("sources unavailable")
        return LogSourceWalk(
            sources=[LogSourceDTO(qradar_id=q, name=f"source-{q}") for q in self.ids],
            complete=self.complete, validated=True, pages_done=1,
        )


@pytest.fixture
async def rig(db_schema):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        yield engine, maker
    finally:
        await engine.dispose()


async def run(session, instance, provider, on=True):
    report = await LogSourceCollector(session, provider, settings=settings(on),
                                      clock=lambda: NOW).sync(instance)
    mark = await session.scalar(select(CollectionWatermark).where(
        CollectionWatermark.instance_id == instance.id,
        CollectionWatermark.collector == COLLECTOR_NAME,
    ))
    assert await session.scalar(select(func.count()).select_from(LogSourcePause).where(
        LogSourcePause.reason == "ABSENT",
    )) == 0
    return report, mark


async def test_two_complete_misses_confirm_without_spans(db_session):
    instance = await make_instance(db_session)
    provider = Inventory()
    await run(db_session, instance, provider)
    missing_id = await db_session.scalar(select(LogSource.id).where(LogSource.qradar_id == 3))
    provider.ids = [1, 2]
    first, mark = await run(db_session, instance, provider)
    assert first.absence == {"state": "evaluated", "reason": "complete", "candidates": 1,
                             "confirmed": 0, "held_count": 0}
    assert mark.collection_metadata["absence_candidates"] == [str(missing_id)]
    second, mark = await run(db_session, instance, provider)
    assert second.absence["confirmed"] == 1 and second.absence["candidates"] == 0
    assert mark.collection_metadata["inventory_pass"]["absence"] == second.absence
    assert mark.collection_metadata["absence_candidates"] == []


@pytest.mark.parametrize("bad", ["partial", "types", "zero", "failed"])
async def test_bad_pass_never_advances_and_keeps_candidates(db_session, bad):
    instance = await make_instance(db_session)
    provider = Inventory()
    await run(db_session, instance, provider)
    provider.ids = [1, 2]
    _, mark = await run(db_session, instance, provider)
    before = mark.collection_metadata["absence_candidates"]
    provider.complete = bad != "partial"
    provider.types_failed = bad == "types"
    provider.failed = bad == "failed"
    if bad == "zero":
        provider.ids = []
    report, mark = await run(db_session, instance, provider)
    assert report.absence["state"] == "skipped" and report.absence["confirmed"] == 0
    assert mark.collection_metadata["absence_candidates"] == before
    assert mark.collection_metadata["absence_complete_rows"] == 2


@pytest.mark.parametrize("partial", [True, False])
async def test_reappearance_on_any_pass_restarts_candidate(db_session, partial):
    instance = await make_instance(db_session)
    provider = Inventory()
    await run(db_session, instance, provider)
    provider.ids = [1, 2]
    await run(db_session, instance, provider)
    provider.ids = [1, 2, 3]
    provider.complete = not partial
    _, mark = await run(db_session, instance, provider)
    assert mark.collection_metadata["absence_candidates"] == []
    provider.ids = [1, 2]
    provider.complete = True
    report, _ = await run(db_session, instance, provider)
    assert report.absence["candidates"] == 1 and report.absence["confirmed"] == 0


@pytest.mark.parametrize("confirm", [True, False])
@pytest.mark.parametrize("missing", [20, 21])
async def test_breaker_edges_and_doctor_entry(rig, monkeypatch, capsys, confirm, missing):
    _, maker = rig
    async with maker() as session:
        instance = await make_instance(session)
        provider = Inventory(missing + 1)
        await run(session, instance, provider)
        provider.ids = [1]
        if confirm:
            # Simulate an earlier valid candidate set; candidate-stage overflow stores none.
            mark = await session.scalar(select(CollectionWatermark))
            missing_ids = list(await session.scalars(select(LogSource.id).where(
                LogSource.qradar_id != 1,
            )))
            mark.collection_metadata = {**mark.collection_metadata,
                                       "absence_candidates": sorted(map(str, missing_ids))}
        report, mark = await run(session, instance, provider)
        assert report.absence["state"] == ("held" if missing == 21 else "evaluated")
        assert report.absence["confirmed"] == (20 if confirm and missing == 20 else 0)
        if missing == 21:
            assert report.absence["reason"] == "mass_absence_held"
            assert len(mark.collection_metadata["absence_candidates"]) == (21 if confirm else 0)
        await session.commit()

    doctor = doctor_cli.Doctor(settings=settings(), sessionmaker=maker, now=lambda: NOW)
    # Use the actual entry, run/render/exit logic; isolate unrelated external probes.
    for name, _ in doctor_cli._CHECK_RUNBOOKS:
        if name != "watermarks":
            monkeypatch.setattr(doctor, f"check_{name}", AsyncMock(return_value=
                doctor_cli.CheckResult(name, doctor_cli.Status.PASS, "fixture")))
    monkeypatch.setattr(doctor_cli, "Doctor", lambda: doctor)
    monkeypatch.setattr(doctor_cli, "dispose_engine", AsyncMock())
    code = await doctor_cli._async_main(False)
    output = capsys.readouterr().out
    if missing == 21:
        assert code == 1
        assert "WARN" in output
        expected = (
            "QRadar inventory: 21 sources would be marked absent at once; none were. "
            "If the API token lost visibility, fix it; if the sources were removed from QRadar "
            "on purpose, see the runbook for accepting the held set."
        )
        assert expected in output
    else:
        assert code == 0 and "sources would be marked absent" not in output


@contextmanager
def statements(engine):
    captured = []

    def record(conn, cursor, statement, parameters, context, executemany):
        captured.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        yield captured
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", record)


async def test_constant_statements_at_3_and_800_and_off_has_no_metadata(rig, monkeypatch):
    engine, maker = rig
    shapes = {}
    for on in (False, True):
        for count in (3, 800):
            async with maker() as session:
                instance = await make_instance(session)
                provider = Inventory(count)
                await run(session, instance, provider, on)
                collector = LogSourceCollector(session, provider, settings=settings(on),
                                                clock=lambda: NOW)
                with statements(engine) as seen:
                    report = await collector.sync(instance)
                shapes[on, count] = seen
                mark = await session.scalar(select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == instance.id,
                ))
                if not on:
                    assert report.absence is None
                    assert not any(k.startswith("absence") for k in mark.collection_metadata)
                    assert "absence" not in mark.collection_metadata["inventory_pass"]
                await session.rollback()
        assert len(shapes[on, 3]) == len(shapes[on, 800])
        print(f"policy={on}: 3 rows={len(shapes[on, 3])}; 800 rows={len(shapes[on, 800])}")
    for count in (3, 800):
        assert sum(sql.endswith(" FOR UPDATE") for sql in shapes[True, count]) == 1
        assert shapes[False, count] == [sql.removesuffix(" FOR UPDATE")
                                       for sql in shapes[True, count]]
    # A disabled policy does not call the evaluator, even with retained on-policy metadata.
    monkeypatch.setattr(inventory_sync, "evaluate_absence", lambda *a, **kw: pytest.fail("off"))
    async with maker() as session:
        instance = await make_instance(session)
        provider = Inventory()
        await run(session, instance, provider, False)


@pytest.mark.parametrize("bad", [None, {"id": 9}, {"id": 1, "name": "duplicate"},
                                {"id": 9, "name": "bad", "description": [1]}])
async def test_rest_invalid_walk_does_not_advance_absence(db_session, bad):
    instance = await make_instance(db_session)
    provider = Inventory()
    await run(db_session, instance, provider)
    provider.ids = [1, 2]
    _, mark = await run(db_session, instance, provider)
    before = mark.collection_metadata["absence_candidates"]

    def handler(request):
        if request.url.path.endswith("/system/about"):
            return httpx.Response(200, json={"external_version": "7.5"})
        if request.url.path.endswith("/log_sources"):
            return httpx.Response(200, json=[{"id": 1, "name": "valid"}, bad])
        return httpx.Response(200, json=[])

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                              base_url="https://example.invalid/api")
    rest = QRadarRestProvider(base_url="https://example.invalid", sec_token="t", client=client,
                              strict_source_enabled=True)
    try:
        report, mark = await run(db_session, instance, rest)
    finally:
        await rest.aclose()
    assert report.absence["state"] == "skipped" and report.absence["confirmed"] == 0
    assert report.absence["reason"] == ("duplicate_ids" if bad and bad.get("id") == 1
                                        else "rows_dropped")
    assert report.status == "COMPLETE" and mark.watermark_at == NOW
    assert mark.collection_metadata["absence_complete_rows"] == 2
    assert mark.collection_metadata["absence_candidates"] == before


@pytest.fixture
def base_code(tmp_path, monkeypatch):
    """Load the actual pre-task implementations without replacing application modules."""
    root = Path(__file__).resolve().parents[3]
    modules = {}
    git = shutil.which("git")
    assert git is not None
    for key, path in (
        ("provider", "providers/qradar_rest.py"),
        ("service", "services/inventory_sync.py"),
        ("collector", "collectors/log_source_collector.py"),
    ):
        source = subprocess.run(  # noqa: S603 — fixed read-only revision and paths
            [git, "show", f"94b5d8fd:backend/app/{path}"], cwd=root,
            check=True, capture_output=True, text=True,
        ).stdout
        copy = tmp_path / f"base_{key}.py"
        copy.write_text(source)
        spec = importlib.util.spec_from_file_location(f"_jdbc1x3a_base_{key}", copy)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, module.__name__, module)
        spec.loader.exec_module(module)
        modules[key] = module
    modules["collector"].InventorySyncService = modules["service"].InventorySyncService
    return modules


@pytest.mark.parametrize("case", [3, 800, "dropped", "duplicate", "coerced_id",
                                  "mapper_error", "zero", "ceiling"])
async def test_switch_off_statement_baseline(rig, base_code, case):
    """Pin SQL, legacy walk outcome, run status and watermark against 94b5d8fd."""
    engine, maker = rig
    rows = [{"id": q, "name": f"source-{q}"}
            for q in range(1, (case if type(case) is int else 3) + 1)]
    if case == "zero":
        rows = []
    elif case == "dropped":
        rows += [None, {}, {"id": 9}, {"id": 9, "name": " "}]
    elif case == "duplicate":
        rows += [rows[-1]]
    elif case == "coerced_id":
        rows += [{"id": "9", "name": "source-9"}, {"id": True, "name": "source-1"}]
    elif case == "mapper_error":
        rows += [{"id": 9, "name": "bad", "description": [1]}]

    def handler(request):
        if request.url.path.endswith("/system/about"):
            return httpx.Response(200, json={"external_version": "7.5"})
        return httpx.Response(200, json=rows if request.url.path.endswith("/log_sources") else [])

    outcomes = []
    for provider_class, collector_class in (
        (base_code["provider"].QRadarRestProvider, base_code["collector"].LogSourceCollector),
        (QRadarRestProvider, LogSourceCollector),
    ):
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler),
                                  base_url="https://example.invalid/api")
        provider = provider_class(base_url="https://example.invalid", sec_token="t", client=client,
                                  page_size=len(rows) + (0 if case == "ceiling" else 1))
        if case == "ceiling":
            provider._max_pages = 1
        try:
            async with maker() as session:
                instance = await make_instance(session)
                collector = collector_class(session, provider, settings=settings(False),
                                            clock=lambda: NOW)
                if case == "mapper_error":
                    with statements(engine) as captured, pytest.raises(ValueError):
                        await collector.sync(instance)
                    outcomes.append((captured, "mapper_error"))
                else:
                    walk = await provider.walk_log_sources()
                    await collector.sync(instance)
                    with statements(engine) as captured:
                        report = await collector.sync(instance)
                    mark = await session.scalar(select(CollectionWatermark))
                    assert getattr(report, "absence", None) is None
                    assert not any(k.startswith("absence") for k in mark.collection_metadata)
                    assert await session.scalar(select(func.count()).select_from(LogSourcePause)
                                                .where(LogSourcePause.reason == "ABSENT")) == 0
                    outcomes.append((
                        captured, mark.collection_metadata, mark.watermark_at,
                        report.status, report.log_sources_seen, walk.complete,
                        [source.model_dump() for source in walk.sources],
                    ))
                await session.rollback()
        finally:
            await provider.aclose()
    assert outcomes[0] == outcomes[1]


async def test_switch_off_keeps_retained_candidates_and_does_not_evaluate(db_session):
    instance = await make_instance(db_session)
    provider = Inventory()
    await run(db_session, instance, provider)
    provider.ids = [1, 2]
    _, mark = await run(db_session, instance, provider)
    before = mark.collection_metadata["absence_candidates"]
    provider.ids = [1, 2, 3]
    report, mark = await run(db_session, instance, provider, False)
    assert report.absence is None
    assert mark.collection_metadata["absence_candidates"] == before
    assert "absence" not in mark.collection_metadata["inventory_pass"]


async def test_activation_uses_existing_complete_row_count(db_session):
    instance = await make_instance(db_session)
    provider = Inventory(1001)
    _, mark = await run(db_session, instance, provider, False)
    assert "absence_complete_rows" not in mark.collection_metadata
    provider.ids = provider.ids[21:]
    report, mark = await run(db_session, instance, provider)
    # ceil(2% of the previous 1001 rows) is 21, rather than the default floor of 20.
    assert report.absence["state"] == "evaluated"
    assert report.absence["candidates"] == 21 and report.absence["confirmed"] == 0
    assert mark.collection_metadata["absence_complete_rows"] == 980


async def test_base_walk_never_evaluates_absence(db_session, monkeypatch):
    instance = await make_instance(db_session)
    provider = Inventory()
    await run(db_session, instance, provider)
    provider.ids = [1, 2]
    _, mark = await run(db_session, instance, provider)
    before = mark.collection_metadata["absence_candidates"]
    provider.ids = [1]
    # MCP inherits this same non-validating base walk; no provider-type inference.
    monkeypatch.setattr(provider, "list_log_sources", AsyncMock(return_value=[
        LogSourceDTO(qradar_id=q, name=f"source-{q}") for q in provider.ids
    ]))
    monkeypatch.setattr(provider, "walk_log_sources",
                        QRadarProvider.walk_log_sources.__get__(provider))
    walk = await provider.walk_log_sources()
    assert walk.complete and not walk.validated
    report, mark = await run(db_session, instance, provider)
    assert report.status == "COMPLETE"
    assert report.absence == {"state": "skipped", "reason": "walk_not_validated",
                              "candidates": 1, "confirmed": 0, "held_count": 0}
    assert mark.collection_metadata["absence_candidates"] == before
    assert mark.collection_metadata["absence_complete_rows"] == 2


@pytest.mark.parametrize("reappeared", [False, True])
async def test_candidate_watermark_waits_and_refreshes_after_commit(rig, reappeared):
    _, maker = rig
    async with maker() as seed:
        instance = await make_instance(seed)
        instance_id = instance.id
        provider = Inventory()
        await run(seed, instance, provider)
        if reappeared:
            provider.ids = [1, 2]
            await run(seed, instance, provider)
        await seed.commit()

    async with maker() as first, maker() as second:
        first_instance = await first.get(type(instance), instance_id)
        second_instance = await second.get(type(instance), instance_id)
        # Keep an old ORM copy as well as an old READ COMMITTED snapshot.
        stale_mark = await second.scalar(select(CollectionWatermark))
        old_candidates = stale_mark.collection_metadata["absence_candidates"]
        first_pid = await first.scalar(text("SELECT pg_backend_pid()"))
        second_pid = await second.scalar(text("SELECT pg_backend_pid()"))
        await second.execute(text("SET LOCAL lock_timeout = '8s'"))
        first_provider = Inventory()
        if not reappeared:
            first_provider.ids = [1, 2]
        first_report, _ = await run(first, first_instance, first_provider)
        assert first_report.absence["candidates"] == (0 if reappeared else 1)
        assert stale_mark.collection_metadata["absence_candidates"] == old_candidates
        next_provider = Inventory()
        next_provider.ids = [1, 2]
        next_provider.walk_log_sources = AsyncMock(wraps=next_provider.walk_log_sources)
        operation = asyncio.create_task(run(second, second_instance, next_provider))
        try:
            async with maker() as observer:
                async with asyncio.timeout(4):
                    while True:
                        blockers = await observer.scalar(
                            text("SELECT pg_blocking_pids(:pid)"), {"pid": second_pid}
                        )
                        if first_pid in blockers:
                            assert not operation.done()
                            next_provider.walk_log_sources.assert_not_awaited()
                            break
                        if operation.done():
                            await operation
                            pytest.fail("second sync did not wait on the candidate watermark")
                        await asyncio.sleep(0.01)
            await first.commit()
            report, mark = await asyncio.wait_for(operation, 5)
            assert report.absence["confirmed"] == (0 if reappeared else 1)
            assert report.absence["candidates"] == (1 if reappeared else 0)
            assert mark is stale_mark
            assert len(mark.collection_metadata["absence_candidates"]) == (1 if reappeared else 0)
            await second.commit()
        finally:
            if not operation.done():
                operation.cancel()
            await asyncio.gather(operation, return_exceptions=True)


@pytest.mark.parametrize("failed", [False, True])
async def test_doctor_held_keeps_stall_lag_and_off_clauses(rig, monkeypatch, capsys, failed):
    _, maker = rig
    async with maker() as session:
        instance = await make_instance(session)
        provider = Inventory(22)
        await run(session, instance, provider)
        provider.ids = [1]
        await run(session, instance, provider)
        for collector, values in (
            ("offense_snapshot", {"collection_metadata": {"watermark_stalled": True}}),
            ("log_source_metric", {"lag_seconds": 100000}),
            ("waf_endpoint_metric", {"last_error": "fixture off"}),
        ):
            session.add(CollectionWatermark(instance_id=instance.id, collector=collector, **values))
        if failed:
            session.add(CollectionWatermark(instance_id=instance.id, collector="rule_inventory",
                                            last_error="fixture failure"))
        await session.commit()
    effective = settings().model_copy(update={"waf_collection_enabled": False})
    doctor = doctor_cli.Doctor(settings=effective, sessionmaker=maker, now=lambda: NOW)
    for name, _ in doctor_cli._CHECK_RUNBOOKS:
        if name != "watermarks":
            monkeypatch.setattr(doctor, f"check_{name}", AsyncMock(return_value=
                doctor_cli.CheckResult(name, doctor_cli.Status.PASS, "fixture")))
    monkeypatch.setattr(doctor_cli, "Doctor", lambda: doctor)
    monkeypatch.setattr(doctor_cli, "dispose_engine", AsyncMock())
    assert await doctor_cli._async_main(False) == (2 if failed else 1)
    output = capsys.readouterr().out
    assert ("FAIL" if failed else "WARN") in output
    assert "21 sources would be marked absent at once; none were." in output
    assert "If the API token lost visibility, fix it" in output
    assert "removed from QRadar on purpose, see the runbook for accepting the held set." in output
    assert "1 watermark(s) stalled at the page ceiling: offense_snapshot" in output
    assert "1 watermark(s) exceed twice their cadence: log_source_metric" in output
    assert "not judged (family off): waf_endpoint_metric" in output
    if failed:
        assert "1 watermark(s) failed: rule_inventory" in output
    else:
        assert "OPS-R66" in output
