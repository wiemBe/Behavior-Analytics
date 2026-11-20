"""P8: live collection and history targets exclude sources QRadar has disabled (D-52, D-95).

Only with the switch on: a source whose stored `enabled` flag is false gets no new live row
(reported or zero-filled), leaves the shared target map (hence the estimate), and is not
searched by a frozen top-up. An enabled source that is merely unmonitored keeps its positive
samples. Stale inventory never gates anything. Switch off is today's behaviour exactly.
Real Postgres, the real collector and the real `collect_metrics` body.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, insert, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.log_source_collector import COLLECTOR_NAME as INVENTORY_COLLECTOR
from app.collectors.log_source_collector import INVENTORY_RUN_KEY
from app.collectors.metric_collector import (
    COLLECTOR_NAME,
    MetricCollector,
    monitored_source_map,
)
from app.core.config import Settings
from app.history.phase_a import TOPUP_SOURCES_KEY
from app.history.planner import ScopeProgress
from app.history.progress_codec import decode_progress, encode_progress
from app.history.source_floors import SourceFloors
from app.history.topup import TopupChunks
from app.models.base import utcnow
from app.models.enums import SearchFamily
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.providers.dto import LogSourceMetricBucketBatch, LogSourceMetricSample
from app.providers.mock import MockQRadarProvider
from app.services.history_backfill import HistoryBackfillService
from app.services.search_cadence import SearchCadenceService
from app.workers import tasks
from tests.integration.factories import make_instance
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    ScriptedMockProvider,
    history_rows,
    make_console,
    say_yes,
    scope_of,
    settings_for,
    tick,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)


def _settings(*, pause: bool, **kw) -> Settings:
    return settings_for(
        qradar_source_pause_enabled=pause, collection_max_backfill_intervals=0, **kw
    )


async def _rows_by_qid(session, console) -> dict[int, list[LogSourceMetric]]:
    found: dict[int, list[LogSourceMetric]] = {}
    for qid, source in console.sources.items():
        found[qid] = list(
            await session.scalars(
                select(LogSourceMetric).where(LogSourceMetric.log_source_id == source.id)
            )
        )
    return found


async def _collect_live(session, console, provider, *, pause: bool):
    return await tick(session, console.instance, provider, _settings(pause=pause), NOW)


async def _live_console(session, *, disabled=(), unmonitored=()):
    """qids 1-4: 1 enabled+monitored absent, 2 disabled reported, 3 disabled absent, 4 reported."""
    console = await make_console(session, [1, 2, 3, 4], unmonitored=unmonitored)
    for qid in disabled:
        console.sources[qid].enabled = False
    await session.flush()
    return console


async def test_actual_collector_disabled_reported_and_absent_not_written(
    db_session, db_schema, monkeypatch
) -> None:
    """The real `collect_metrics` path, switch on by a DB row over an env-off base."""
    console = await _live_console(db_session, disabled=(2, 3))
    session_maker = async_sessionmaker(create_async_engine(db_schema), expire_on_commit=False)
    db_session.add(
        FeatureSwitch(
            key="qradar_source_pause_enabled", enabled=True, updated_at=utcnow(),
            updated_by="alice",
        )
    )
    await db_session.commit()
    base = _settings(pause=False)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: session_maker)
    provider = ScriptedMockProvider([1, 2, 3, 4], silent=[1, 3])
    seen: list[Settings] = []

    class Spy(MetricCollector):
        def __init__(self, session, provider, *, settings=None, clock=None):
            seen.append(settings)
            super().__init__(session, provider, settings=settings, clock=lambda: NOW)

    async def one_instance(run, instance_name=None):
        async with session_maker() as session:
            instance = await session.get(type(console.instance), console.instance.id)
            result = await run(session, instance, provider)
            await session.commit()
        return {"results": [result]}

    monkeypatch.setattr(tasks, "MetricCollector", Spy)
    monkeypatch.setattr(tasks, "_for_each_instance", one_instance)

    result = await tasks.collect_metrics()

    assert result["results"][0]["status"] in {"ok", "partial"}, result
    assert seen and seen[0].qradar_source_pause_enabled is True
    rows = await _rows_by_qid(db_session, console)
    assert rows[2] == [] and rows[3] == []  # reported and absent: nothing written
    assert [r.event_count > 0 for r in rows[4]] == [True]
    assert [r.event_count for r in rows[1]] == [0]  # the enabled absent source is zero-filled


async def test_enabled_unmonitored_positive_preserved(db_session) -> None:
    console = await _live_console(db_session, disabled=(2,), unmonitored=(2, 4))
    provider = ScriptedMockProvider([1, 2, 3, 4])

    await _collect_live(db_session, console, provider, pause=True)

    rows = await _rows_by_qid(db_session, console)
    assert [r.event_count > 0 for r in rows[4]] == [True]  # enabled, unmonitored: stored
    assert rows[2] == []  # disabled and unmonitored: not stored
    assert [r.event_count > 0 for r in rows[1]] == [True]


async def test_stale_inventory_keeps_collection(db_session) -> None:
    console = await _live_console(db_session, disabled=(2, 3))
    db_session.add(
        CollectionWatermark(
            instance_id=console.instance.id,
            collector=INVENTORY_COLLECTOR,
            watermark_at=NOW - timedelta(hours=48),
            last_run_at=NOW - timedelta(hours=48),
            last_error="time_limit",
            consecutive_failures=3,
            collection_metadata={INVENTORY_RUN_KEY: {"status": "FAILED"}},
        )
    )
    await db_session.flush()
    provider = ScriptedMockProvider([1, 2, 3, 4], silent=[1, 3])

    report = await _collect_live(db_session, console, provider, pause=True)

    rows = await _rows_by_qid(db_session, console)
    assert report.intervals_collected == 1
    assert len(rows[1]) == 1 and len(rows[4]) == 1  # enabled sources keep collecting
    assert rows[2] == [] and rows[3] == []  # only the stored flag decides


async def test_switch_off_unchanged(db_session) -> None:
    """Off: a disabled reported source is stored, a disabled absent one is zero-filled."""
    console = await _live_console(db_session, disabled=(2, 3))
    provider = ScriptedMockProvider([1, 2, 3, 4], silent=[1, 3])

    await _collect_live(db_session, console, provider, pause=False)

    rows = await _rows_by_qid(db_session, console)
    assert all(len(rows[qid]) == 1 for qid in (1, 2, 3, 4))
    assert rows[2][0].event_count > 0 and rows[3][0].event_count == 0
    assert len(provider.calls) == 1


async def test_history_target_and_estimate_exclude_current_disabled(db_session) -> None:
    console = await _live_console(db_session, disabled=(3,), unmonitored=(4,))
    instance_id = console.instance.id

    off = await monitored_source_map(db_session, [instance_id], _settings(pause=False))
    on = await monitored_source_map(db_session, [instance_id], _settings(pause=True))
    legacy = await monitored_source_map(db_session, [instance_id])

    assert sorted(off[instance_id]) == [1, 2, 3] == sorted(legacy[instance_id])
    assert sorted(on[instance_id]) == [1, 2]

    async def phase_a_assumptions(pause: bool) -> str:
        service = HistoryBackfillService(db_session, _settings(pause=pause))
        console_estimate = next(
            item for item in await service.estimates() if item.instance_id == instance_id
        )
        family = next(f for f in console_estimate.families if f.family == "PHASE_A")
        return " ".join(family.assumptions)

    assert "3 monitored sources" in await phase_a_assumptions(False)
    assert "2 monitored sources" in await phase_a_assumptions(True)


def _chunk_scope(scope, keys: list[int], floors: list[int], *, chunked: bool):
    start = ANCHOR - 2 * STEP
    scope.target_start = start
    adapter = {
        **SourceFloors({str(q): start - timedelta(hours=1) for q in floors}).encode(),
        TOPUP_SOURCES_KEY: [str(q) for q in keys],
    }
    if chunked:  # frozen partition: the chunked path reads its stored ids
        adapter.update(
            TopupChunks.load(adapter, [str(q) for q in keys], start, ANCHOR, STEP, 250).encode()
        )
    scope.progress = encode_progress(
        ScopeProgress(target_start=start, started_at=scope.started_at, floor=start,
                      chunk_buckets=2, density_checked=True),
        adapter,
    )


async def _resumed_topup(
    session, qids, disabled, keys, floors, *, pause: bool, chunked: bool = False
):
    console = await make_console(session, qids)
    for qid in disabled:
        console.sources[qid].enabled = False
    await say_yes(session, console.instance)
    provider = ScriptedMockProvider(qids)
    settings = _settings(pause=pause)
    await tick(session, console.instance, provider, settings, FIRST_TICK)
    scope = await scope_of(session, console.instance)
    assert scope is not None and scope.state == "READING"
    _chunk_scope(scope, keys, floors, chunked=chunked)
    await session.flush()
    marked = len(provider.calls)
    report = await tick(session, console.instance, provider, settings, FIRST_TICK + STEP)
    filters = [
        sorted(filt or ()) for call, filt in zip(
            provider.calls[marked:], provider.source_filters[marked:], strict=True
        ) if call[1] <= ANCHOR
    ]
    return console, report, filters, await scope_of(session, console.instance)


async def test_frozen_chunk_rechecks_ids_empty_skips(db_session) -> None:
    # Chunked path: 251 frozen ids make a 250-id chunk and a one-id chunk. The last
    # source is disabled: its chunk is skipped, with no fleet-wide fallback.
    qids = list(range(1, 252))
    console, report, filters, scope = await _resumed_topup(
        db_session, qids, [251], qids, qids, pause=True, chunked=True
    )
    assert [len(f) for f in filters] == [250]
    assert 251 not in filters[0]
    assert report.history is not None and report.history.searches == 1
    assert scope is not None and scope.state == "COMPLETE"
    progress = decode_progress(scope.progress).progress
    assert progress.buckets_read == 2  # the skipped chunk's frontier still advanced
    rows = await history_rows(db_session, console.instance)
    assert len(rows) == 500 and {r.log_source_id for r in rows} == {
        console.sources[q].id for q in range(1, 251)
    }

    # A chunk of two ids, one disabled since it was frozen: only the live id is searched.
    qids = list(range(1, 254))
    _, _, filters, _ = await _resumed_topup(
        db_session, qids, [252], qids[:252], qids[252:], pause=True, chunked=True
    )
    assert filters and all(252 not in f and 253 not in f for f in filters)

    # Small path: every frozen id was disabled; the one enabled source already has a floor.
    # No search at all (not an unfiltered one) and the reading ends cleanly.
    console, report, filters, scope = await _resumed_topup(
        db_session, [1, 2, 3], [1, 2], [1, 2], [3], pause=True
    )
    assert filters == []
    assert report.history is not None and report.history.searches == 0
    assert scope is not None and scope.state != "READING"
    assert await history_rows(db_session, console.instance) == []

    # Small path, partly disabled: the search names the remaining frozen id only.
    _, _, filters, _ = await _resumed_topup(
        db_session, [1, 2, 3], [2], [1, 2], [3], pause=True
    )
    assert filters and all(f == [1] for f in filters)


async def test_frozen_chunk_switch_off_still_searches_every_frozen_id(db_session) -> None:
    qids = list(range(1, 252))
    _, _, filters, _ = await _resumed_topup(
        db_session, qids, [251], qids, qids, pause=False, chunked=True
    )
    assert [len(f) for f in filters] == [250, 1]


class _AllSources(MockQRadarProvider):
    """Reports two of every three sources, in every bucket, in one aggregate."""

    def __init__(self, sources: int) -> None:
        super().__init__(seed=1)
        self.sources = sources
        self.calls: list[int] = []

    def _samples(self, start: datetime) -> list[LogSourceMetricSample]:
        return [
            LogSourceMetricSample(
                qradar_id=q, bucket_start=start, bucket_seconds=300, event_count=1
            )
            for q in range(1, self.sources + 1) if q % 3
        ]

    async def get_log_source_metrics(self, window_start, window_end, *, qradar_ids=None):  # type: ignore[no-untyped-def]
        self.calls.append(1)
        return self._samples(window_start)

    async def get_log_source_metrics_bucketed(
        self, window_start, window_end, *, bucket_seconds, max_rows, qradar_ids=None
    ):  # type: ignore[no-untyped-def]
        count = int((window_end - window_start).total_seconds()) // bucket_seconds
        self.calls.append(count)
        buckets = {
            window_start + timedelta(seconds=bucket_seconds * i): self._samples(
                window_start + timedelta(seconds=bucket_seconds * i)
            )
            for i in range(count)
        }
        return LogSourceMetricBucketBatch(
            buckets=buckets, rows_returned=0, truncated=False, aql="scripted"
        )


async def _sized_run(session, sources: int, *, pause: bool) -> tuple[int, int, int]:
    instance = await make_instance(session)
    await session.execute(insert(LogSource), [
        {
            "instance_id": instance.id, "qradar_id": q, "name": f"s-{q}",
            "monitoring_enabled": q % 2 == 0, "enabled": q % 5 != 0,
        }
        for q in range(1, sources + 1)
    ])
    settings = _settings(pause=pause)
    await SearchCadenceService(session, settings).set_cadence(
        SearchFamily.PHASE_A_VOLUME, 3600, actor="t", source_ip=None
    )
    boundary = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    session.add(CollectionWatermark(
        instance_id=instance.id, collector=COLLECTOR_NAME,
        watermark_at=boundary - timedelta(minutes=65),
    ))
    await session.flush()
    provider = _AllSources(sources)
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        report = await MetricCollector(
            session, provider, settings=settings, clock=lambda: boundary
        ).collect(instance)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    stored = await session.scalar(
        select(func.count()).select_from(LogSourceMetric).join(LogSource).where(
            LogSource.instance_id == instance.id
        )
    )
    assert report.searches_sent == len(provider.calls)
    return len(statements), report.searches_sent, stored


async def test_8k_scan_sizing_and_policy_reads_constant(db_session) -> None:
    small_on = await _sized_run(db_session, 100, pause=True)
    big_on = await _sized_run(db_session, 8000, pause=True)
    big_off = await _sized_run(db_session, 8000, pause=False)

    # The same statement count at 100 and 8,000 sources and with the switch on or off, and
    # the same provider searches with it on or off: the policy adds no statement and no
    # search per source. (The search count itself follows the all-inventory row planning.)
    assert small_on[0] == big_on[0] == big_off[0]
    assert big_on[1] == big_off[1] and small_on[1] <= big_on[1]
    assert big_on[2] < big_off[2]  # disabled sources wrote nothing with the switch on
