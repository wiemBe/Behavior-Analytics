"""Phase A history, phase 2: the step inside `MetricCollector.collect` (HB-3, §B.4.1, §B.6).

Every test runs `collect`, the call the collector task makes, so the lifecycle, the lock, the
live pass and the step are exercised together on each exit the operator can reach.
"""

from __future__ import annotations

import copy
import os
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text

from app.anomaly.baseline import BaselineBuilder
from app.collectors.metric_collector import COLLECTOR_NAME, MetricCollector
from app.history.progress_codec import decode_progress
from app.history.source_floors import SourceFloors
from app.models.log_source import LogSource, LogSourceBaseline, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderUnavailableError
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.ariel_permit import _ARIEL_PRIORITY, ArielPermitUnavailable
from app.services.locks import collector_lock_key
from tests.integration.factories import FakeClock
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    Console,
    ScriptedArielProvider,
    ScriptedMockProvider,
    history_rows,
    make_console,
    read_all,
    say_yes,
    scope_of,
    settings_for,
    tick,
)
from tests.integration.test_history_backfill_api import ADMIN, BASE, Client, family_of
from tests.integration.test_waf_scale import counting

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

QIDS = [1, 2, 3]
#: A small row cap and one history search per tick: a reading that takes many ticks.
SLOW = dict(ariel_multi_bucket_max_rows=500, history_backfill_searches_per_tick=1)
WINDOW = timedelta(days=7)
T0 = ANCHOR - WINDOW
BUCKETS = int(WINDOW.total_seconds() // 300)  # 2016


async def _metric_rows(session, console: Console) -> list[LogSourceMetric]:  # type: ignore[no-untyped-def]
    rows = await session.scalars(
        select(LogSourceMetric)
        .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
        .where(LogSource.instance_id == console.instance.id)
        .order_by(LogSourceMetric.bucket_start)
        .execution_options(populate_existing=True)
    )
    return list(rows)


async def _decoded(session, console):  # type: ignore[no-untyped-def]
    scope = await scope_of(session, console.instance)
    return decode_progress(scope.progress)


# ------------------------------------------------------------------ the lifecycle exits


async def test_no_decision_reads_nothing(db_session) -> None:
    console = await make_console(db_session, QIDS)
    provider = ScriptedMockProvider(QIDS)

    report = await tick(db_session, console.instance, provider, settings_for(), FIRST_TICK)

    assert report.history is not None and report.history.state is None
    assert await scope_of(db_session, console.instance) is None
    assert len(provider.calls) == 1  # the live pass only


async def test_a_no_decision_reads_nothing(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await HistoryBackfillRepository(db_session).record_decision(
        console.instance.id,
        answer="NO",
        days=None,
        source="UI",
        decided_by="test",
        at=datetime(2026, 7, 1, tzinfo=UTC),
    )
    provider = ScriptedMockProvider(QIDS)

    await tick(db_session, console.instance, provider, settings_for(), FIRST_TICK)
    await tick(db_session, console.instance, provider, settings_for(), FIRST_TICK + STEP)

    assert await scope_of(db_session, console.instance) is None
    assert await history_rows(db_session, console.instance) == []


async def test_yes_before_the_first_live_bucket_waits_then_reads(db_session) -> None:
    """No inventory at the first tick, so no live row: WAITING; a live bucket wakes it."""
    from tests.integration.factories import make_instance, make_log_source

    instance = await make_instance(db_session)
    await say_yes(db_session, instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()

    first = await tick(db_session, instance, provider, settings, FIRST_TICK)
    scope = await scope_of(db_session, instance)
    assert scope is not None and scope.state == "WAITING"
    assert first.history is not None and first.history.state == "WAITING"
    assert scope.anchor is None and scope.progress == {}

    for qid in QIDS:
        await make_log_source(db_session, instance, qradar_id=qid, name=f"src-{qid}")
    await tick(db_session, instance, provider, settings, FIRST_TICK + STEP)
    scope = await scope_of(db_session, instance)
    assert scope is not None and scope.state == "READING"
    assert scope.anchor == ANCHOR + STEP  # the oldest live bucket: the first one collected
    assert scope.progress == {}  # this tick only moved the state; nothing was searched
    assert await history_rows(db_session, instance) == []

    searches_before = len(provider.calls)
    await tick(db_session, instance, provider, settings, FIRST_TICK + 2 * STEP)
    assert len(provider.calls) > searches_before + 1  # a live search and the history searches
    assert len(await history_rows(db_session, instance)) > 0


async def test_a_reading_that_finishes_complete(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()

    scope, ticks, _ = await read_all(db_session, console, provider, settings)

    assert scope.state == "COMPLETE"
    assert scope.anchor == ANCHOR
    assert scope.target_start == T0 and scope.days_target == 7 and scope.days_requested == 7
    assert scope.floor == T0  # the earliest source floor
    assert scope.through == ANCHOR - STEP
    assert scope.finished_at is not None
    rows = await history_rows(db_session, console.instance)
    assert len(rows) == len(QIDS) * BUCKETS
    assert {r.collection_source for r in rows} == {"mock_history"}
    assert min(r.bucket_start for r in rows) == T0
    assert max(r.bucket_start for r in rows) == ANCHOR - STEP  # the anchor is exclusive
    assert all(r.query_provenance["history"] is True for r in rows)
    assert all(r.query_provenance["floor"] == T0.isoformat() for r in rows)
    assert all(r.watermark_at is None for r in rows)
    assert ticks >= 3  # created, planned and read, finished


async def test_the_ariel_provider_marks_history_ariel_history(db_session) -> None:
    console = await make_console(db_session, [1])
    await say_yes(db_session, console.instance)
    provider = ScriptedArielProvider([1])

    await read_all(db_session, console, provider, settings_for())

    rows = await history_rows(db_session, console.instance)
    assert rows and {r.collection_source for r in rows} == {"ariel_history"}


async def test_history_never_touches_the_collection_watermark(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    mark = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == console.instance.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )
    assert mark is not None and mark.watermark_at == ANCHOR

    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP)

    await db_session.refresh(mark)
    assert mark.watermark_at == ANCHOR + 2 * STEP  # live ran on, one bucket per tick
    assert await history_rows(db_session, console.instance)  # while history was written


async def test_a_cut_source_and_a_silent_source_end_partial_with_counts(db_session) -> None:
    qids = [1, 2, 3, 4]
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    late = ANCHOR - timedelta(days=3)  # source 2's first event: QRadar holds less than asked
    provider = ScriptedMockProvider(qids, first_event={2: late, 4: ANCHOR}, silent=[3])
    # Source 4 emits only from the anchor on: its first event is not in the window at all.
    settings = settings_for()

    scope, _, _ = await read_all(db_session, console, provider, settings)

    assert scope.state == "PARTIAL"
    assert "QRadar holds less than asked for 1 of 4 sources" in scope.reason
    assert "2 sources had no event in the window" in scope.reason
    floors = SourceFloors.decode((await _decoded(db_session, console)).adapter)
    assert set(floors.first_buckets) == {"1", "2"}
    rows = await history_rows(db_session, console.instance)
    ids = {source.id: qid for qid, source in console.sources.items()}
    by_source: dict[int, list[datetime]] = {}
    for row in rows:
        by_source.setdefault(ids[row.log_source_id], []).append(row.bucket_start)
    margin = timedelta(hours=1)
    assert min(by_source[1]) == T0
    # Never before F_s: not a sample, not a zero (§B.3.2 rule 3).
    assert min(by_source[2]) == late + margin
    assert 3 not in by_source and 4 not in by_source  # silent: no row, not even a zero
    assert max(by_source[2]) == ANCHOR - STEP
    assert scope.floor == T0  # the earliest source floor


async def test_a_console_with_no_event_in_the_window_is_unavailable(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS, first_event=dict.fromkeys(QIDS, ANCHOR))

    scope, _, _ = await read_all(db_session, console, provider, settings_for())

    assert scope.state == "UNAVAILABLE"
    assert "no event" in scope.reason
    assert await history_rows(db_session, console.instance) == []


async def test_a_console_with_no_monitored_source_is_unavailable_without_searching(
    db_session,
) -> None:
    console = await make_console(db_session, QIDS, unmonitored=QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # live row exists
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    searches = len(provider.calls)

    await tick(db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP)

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "UNAVAILABLE"
    assert "monitored" in scope.reason
    assert len(provider.calls) == searches + 1  # only the live search of that tick


async def test_the_window_is_refused_below_seven_days(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    # Retention on: a 7-day window plus the reading would outlive a 9-day retention.
    settings = settings_for(retention_enabled=True, retention_log_source_metric_days=9)

    scope, _, _ = await read_all(db_session, console, provider, settings)

    assert scope.state == "UNAVAILABLE"
    assert "below the 7-day minimum" in scope.reason
    assert await history_rows(db_session, console.instance) == []


async def test_the_window_is_reduced_to_what_the_retention_allows(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance, days=14)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for(
        history_backfill_days=14,
        history_backfill_max_hours=24,
        retention_enabled=True,
        retention_log_source_metric_days=11,  # the largest d with d x 24 + 24 < 11 x 24 is 9
    )

    scope, _, _ = await read_all(db_session, console, provider, settings)

    assert scope.days_requested == 14 and scope.days_target == 9
    assert scope.target_start == ANCHOR - timedelta(days=9)
    assert scope.state == "COMPLETE"


async def test_the_search_budget_the_live_pass_left_can_be_zero(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for(search_cadence_max_searches_per_tick=1)  # live uses the one
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    searches = len(provider.calls)

    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP)

    assert len(provider.calls) == searches + 1  # the live search; history had no budget
    assert report.history is not None and report.history.searches == 0
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING" and scope.through is None
    assert await history_rows(db_session, console.instance) == []
    decoded = await _decoded(db_session, console)  # the plan was stored on the waiting step
    assert decoded.progress.target_start == T0


async def test_the_time_budget_stops_the_searches_of_a_step(db_session) -> None:
    qids = list(range(1, 6))
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    # 5 sources and a 500-row cap: 64-bucket chunks, ten searches for a step.
    provider = ScriptedMockProvider(qids)
    settings = settings_for(ariel_multi_bucket_max_rows=500, history_backfill_seconds_per_tick=10)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    searches = len(provider.calls)

    # Every clock read costs a second: the ten seconds run out inside the step.
    clock = FakeClock(FIRST_TICK + 2 * STEP, step_seconds=1.0)
    report = await tick(
        db_session, console.instance, provider, settings, FIRST_TICK, clock=clock
    )

    spent = len(provider.calls) - searches - 1
    assert report.history is not None and report.history.searches == spent
    assert 0 < spent < 10
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING"
    assert scope.through is not None and scope.through < ANCHOR - STEP


async def test_the_switch_off_ends_a_reading_partial(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    on = settings_for(**SLOW)
    await tick(db_session, console.instance, provider, on, FIRST_TICK)
    await tick(db_session, console.instance, provider, on, FIRST_TICK + STEP)
    await tick(db_session, console.instance, provider, on, FIRST_TICK + 2 * STEP)
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING" and scope.through is not None
    written = len(await history_rows(db_session, console.instance))
    searches = len(provider.calls)

    off = settings_for(history_backfill_enabled=False)
    await tick(db_session, console.instance, provider, off, FIRST_TICK + 3 * STEP)

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "PARTIAL"
    assert scope.reason == "history reading was switched off"
    assert len(provider.calls) == searches + 1  # live only
    assert len(await history_rows(db_session, console.instance)) == written
    # Switched off again later: nothing is created or moved.
    await tick(db_session, console.instance, provider, off, FIRST_TICK + 4 * STEP)
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "PARTIAL"


async def test_a_stop_request_ends_a_reading_partial(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for(**SLOW)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP)
    await HistoryBackfillRepository(db_session).request_stop(
        console.instance.id, by="analyst@example.test", at=FIRST_TICK + 2 * STEP
    )
    searches = len(provider.calls)

    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + 3 * STEP)

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "PARTIAL"
    assert scope.reason == "stopped by analyst@example.test"
    assert len(provider.calls) == searches + 1
    # The rows it wrote are evidence: their sources were queued and rebuilt in this tick.
    assert report.history is not None and report.history.rebuilt == len(QIDS)
    assert (await _decoded(db_session, console)).adapter["rebuild_pending"] == []


async def test_a_start_request_after_complete_tops_up(db_session) -> None:
    """RESTART keeps the anchor, clears the window, re-reads `[T0, anchor)` and adds rows only."""
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    scope, _, at = await read_all(db_session, console, provider, settings)
    assert scope.state == "COMPLETE"
    before = {
        (r.log_source_id, r.bucket_start): r.collected_at
        for r in await history_rows(db_session, console.instance)
    }
    # A source added after the anchor, with its own history only before the anchor.
    from tests.integration.factories import make_log_source

    late = await make_log_source(db_session, console.instance, qradar_id=9, name="src-9")
    provider.qids = [*QIDS, 9]
    await HistoryBackfillRepository(db_session).request_start(
        console.instance.id, by="analyst@example.test", at=at
    )
    for index in range(1, 12):
        await tick(db_session, console.instance, provider, settings, at + index * STEP)
        scope = await scope_of(db_session, console.instance)
        assert scope is not None
        if scope.state == "COMPLETE" and scope.finished_at > at:
            break

    assert scope.state == "COMPLETE" and scope.anchor == ANCHOR
    rows = await history_rows(db_session, console.instance)
    after = {(r.log_source_id, r.bucket_start): r.collected_at for r in rows}
    assert all(after[key] == stamp for key, stamp in before.items())  # DO NOTHING
    mine = [r.bucket_start for r in rows if r.log_source_id == late.id]
    assert min(mine) == T0 and max(mine) == ANCHOR - STEP  # before the anchor only
    assert len(rows) == (len(QIDS) + 1) * BUCKETS


async def test_the_lock_not_acquired_skips_live_and_history(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    before = (await scope_of(db_session, console.instance)).progress  # type: ignore[union-attr]
    key = collector_lock_key(console.instance.id, COLLECTOR_NAME)
    url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    holder = create_engine(url, future=True)
    conn = holder.connect()
    conn.execute(
        text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
            ns=settings.collection_advisory_lock_namespace, key=key
        )
    )
    searches = len(provider.calls)
    try:
        report = await tick(
            db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP
        )
    finally:
        conn.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=settings.collection_advisory_lock_namespace, key=key
            )
        )
        conn.close()
        holder.dispose()

    assert report.skipped_locked is True and report.history is None
    assert len(provider.calls) == searches
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING" and scope.progress == before


# ---------------------------------------------------------------------- failures


async def test_a_failed_first_chunk_writes_nothing_and_halves_the_width(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    history_start = ANCHOR  # history searches end at or before the anchor

    def fail(start: datetime, end: datetime, count: int) -> bool:
        return end <= history_start and count > 1

    provider = ScriptedMockProvider(QIDS, fail=fail)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope

    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING"
    assert scope.through is None  # unmoved
    assert await history_rows(db_session, console.instance) == []
    progress = (await _decoded(db_session, console)).progress
    assert progress.chunk_buckets is not None and progress.chunk_buckets < 1333 // 2 + 1
    assert progress.chunk_ceiling == progress.chunk_buckets  # W halved from the failed width
    assert report.history is not None and report.history.searches == 1
    # The live pass of that tick was not lost to the failed history search.
    mark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.instance_id == console.instance.id)
    )
    assert mark is not None and mark.watermark_at == ANCHOR + STEP


async def test_a_failure_after_a_completed_chunk_keeps_the_prefix_only(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    # 3 sources: chunks of 1 066 buckets. The second search of the step fails.
    seen: list[int] = []

    def fail(start: datetime, end: datetime, count: int) -> bool:
        if end > ANCHOR or count == 1:
            return False
        seen.append(count)
        return len(seen) == 2

    provider = ScriptedMockProvider(QIDS, fail=fail)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope

    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING"
    progress = (await _decoded(db_session, console)).progress
    first_chunk = seen[0]
    assert scope.through == T0 - timedelta(hours=1) + (first_chunk - 1) * STEP
    assert progress.through == scope.through
    rows = await history_rows(db_session, console.instance)
    # Whole buckets of the first chunk only, from T0 (the margin before it writes nothing).
    assert rows and max(r.bucket_start for r in rows) <= scope.through
    assert not any(r.bucket_start > scope.through for r in rows)


async def test_a_fault_in_the_history_step_never_takes_the_live_buckets(
    db_session, monkeypatch
) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope

    original = MetricCollector._history_step

    async def broken(self, *args, **kwargs):  # type: ignore[no-untyped-def]
        await original(self, *args, **kwargs)  # the step writes its rows ...
        raise RuntimeError("SELECT sensitive_property FROM events")  # ... and then fails

    monkeypatch.setattr(MetricCollector, "_history_step", broken)
    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    assert report.history is not None and report.history.error == "RuntimeError"
    assert report.intervals_collected == 1
    mark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.instance_id == console.instance.id)
    )
    assert mark is not None and mark.watermark_at == ANCHOR + STEP
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING" and scope.progress == {}
    assert scope.last_error_class == "RuntimeError"
    assert scope.last_error_at == FIRST_TICK + STEP
    assert await history_rows(db_session, console.instance) == []  # rolled back with the step

    monkeypatch.setattr(MetricCollector, "_history_step", original)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP)
    cleared = await scope_of(db_session, console.instance)
    assert cleared is not None
    assert cleared.last_error_class is None and cleared.last_error_at is None


# ------------------------------------------------------------- never over live


async def test_a_history_insert_never_replaces_a_live_row(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope
    source = console.sources[1]
    bucket = T0 + timedelta(hours=5)
    db_session.add(
        LogSourceMetric(
            log_source_id=source.id,
            bucket_start=bucket,
            event_count=999_999,
            collection_source="ariel_aggregate",
            query_provenance={"live": True},
        )
    )
    await db_session.flush()

    await read_all(db_session, console, provider, settings, first=FIRST_TICK + STEP)

    row = await db_session.scalar(
        select(LogSourceMetric)
        .where(LogSourceMetric.log_source_id == source.id, LogSourceMetric.bucket_start == bucket)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    assert row.event_count == 999_999 and row.collection_source == "ariel_aggregate"
    assert row.query_provenance == {"live": True}


async def test_a_later_live_upsert_replaces_a_history_row_and_it_reads_as_live(
    db_session,
) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await read_all(db_session, console, provider, settings)
    bucket = T0 + timedelta(hours=7)
    source = console.sources[2]
    old = [r for r in await history_rows(db_session, console.instance)
           if r.log_source_id == source.id and r.bucket_start == bucket]
    assert len(old) == 1 and old[0].collection_source == "mock_history"

    collector = MetricCollector(db_session, provider, settings=settings, clock=lambda: ANCHOR)
    await collector._write_bucket(
        console.instance,
        bucket,
        bucket + STEP,
        [ScriptedMockProvider.sample(2, bucket, 300)],
        watermark_at=None,
        duration_ms=1,
        collected_at=ANCHOR,
    )

    row = await db_session.scalar(
        select(LogSourceMetric)
        .where(LogSourceMetric.log_source_id == source.id, LogSourceMetric.bucket_start == bucket)
        .execution_options(populate_existing=True)
    )
    assert row is not None
    assert row.collection_source == "mock"  # the live marker
    assert "history" not in row.query_provenance


# --------------------------------------------------------------- equivalence


async def test_history_equals_live_column_for_column(db_session) -> None:
    """The same scripted data, collected live bucket by bucket and as history in chunks."""
    qids = [1, 2, 3, 4, 5]
    stop = {4: ANCHOR - timedelta(days=2) + timedelta(hours=1)}  # source 4 falls silent
    live = await make_console(db_session, qids, unmonitored=[5])
    hist = await make_console(db_session, qids, unmonitored=[5])
    await say_yes(db_session, hist.instance)
    settings = settings_for()

    live_provider = ScriptedMockProvider(qids, stop_at=stop)
    start = ANCHOR - timedelta(days=2) + timedelta(minutes=8)  # first bucket: ANCHOR - 2 days
    for index in range(36):
        await tick(db_session, live.instance, live_provider, settings, start + index * STEP)
    live_rows = {
        (qid, r.bucket_start): r
        for r in await _metric_rows(db_session, live)
        for qid, source in live.sources.items()
        if source.id == r.log_source_id
    }
    assert min(b for _, b in live_rows) == ANCHOR - timedelta(days=2)

    hist_provider = ScriptedMockProvider(qids, stop_at=stop)
    await read_all(db_session, hist, hist_provider, settings)
    hist_rows = {
        (qid, r.bucket_start): r
        for r in await history_rows(db_session, hist.instance)
        for qid, source in hist.sources.items()
        if source.id == r.log_source_id
    }

    skipped = {"collection_source", "query_provenance", "collected_at",
               "collection_duration_ms", "watermark_at", "log_source_id"}
    columns = [c.name for c in LogSourceMetric.__table__.columns if c.name not in skipped]
    compared = zeros = 0
    for (qid, bucket), row in live_rows.items():
        if qid == 5 or bucket >= ANCHOR:
            continue  # unmonitored: live writes it, history has no reader for it
        other = hist_rows[(qid, bucket)]
        for name in columns:
            assert getattr(row, name) == getattr(other, name), (qid, bucket, name)
        assert row.query_provenance.get("zero_filled") == other.query_provenance.get("zero_filled")
        compared += 1
        zeros += 1 if row.query_provenance.get("zero_filled") else 0
    assert compared >= 4 * 30
    assert zeros > 0  # the silent source's zero-fill is part of what was compared
    assert not any(qid == 5 for qid, _ in hist_rows)


# ---------------------------------------------------------------------- statements


async def _step_statements(db_session, qids, *, earlier: int = 0, **overrides) -> int:  # type: ignore[no-untyped-def]
    """The statements of one tick that reads (and writes) a step, after `earlier` steps."""
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(qids)
    settings = settings_for(**overrides)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope
    for index in range(earlier):
        await tick(
            db_session, console.instance, provider, settings, FIRST_TICK + (index + 1) * STEP
        )
    with counting(db_session) as statements:
        await tick(
            db_session, console.instance, provider, settings,
            FIRST_TICK + (earlier + 1) * STEP,
        )
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING" and scope.through is not None
    return len(statements)


async def test_a_history_step_costs_the_same_statements_for_any_sources_and_span(
    db_session,
) -> None:
    """Rule 9: 1 or 100 sources, a 12-bucket or a 2 016-bucket span: the same statements."""
    one_wide = await _step_statements(db_session, [1])  # one 2 016-bucket search
    # Cap 19 and one search a tick: 12-bucket steps. The first step is only the margin (the
    # floor proof) and writes no row, so the second one is counted.
    one_narrow = await _step_statements(
        db_session,
        [1],
        earlier=1,
        ariel_multi_bucket_max_rows=19,
        history_backfill_searches_per_tick=1,
    )
    hundred = await _step_statements(db_session, list(range(1, 101)))  # 320 buckets

    assert one_wide == one_narrow == hundred, (one_wide, one_narrow, hundred)


# ------------------------------------------------------------------- rebuild


async def test_a_finished_reading_rebuilds_baselines_in_batches_once_each(
    db_session, monkeypatch
) -> None:
    qids = [1, 2, 3, 4, 5]
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(qids)
    settings = settings_for(history_backfill_baseline_sources_per_tick=2)
    rebuilt: list[int] = []
    original = BaselineBuilder.rebuild_for_source

    async def record(self, log_source, *, now=None):  # type: ignore[no-untyped-def]
        rebuilt.append(log_source.qradar_id)
        return await original(self, log_source, now=now)

    monkeypatch.setattr(BaselineBuilder, "rebuild_for_source", record)

    scope, _, at = await read_all(db_session, console, provider, settings)
    assert scope.state == "COMPLETE"
    assert len(rebuilt) == 2  # the finishing tick's batch
    decoded = await _decoded(db_session, console)
    assert len(decoded.adapter["rebuild_pending"]) == 3

    await tick(db_session, console.instance, provider, settings, at)
    await tick(db_session, console.instance, provider, settings, at + STEP)
    await tick(db_session, console.instance, provider, settings, at + 2 * STEP)

    assert sorted(rebuilt) == qids  # all five, each exactly once
    decoded = await _decoded(db_session, console)
    assert decoded.adapter["rebuild_pending"] == []
    cells = await db_session.scalar(select(func.count()).select_from(LogSourceBaseline))
    assert cells and cells > 0


async def test_the_rebuild_costs_statements_per_source_rebuilt_not_per_tick(
    db_session, monkeypatch
) -> None:
    """Pinned: a rebuild tick is a fixed overhead plus a constant per source rebuilt."""
    qids = [1, 2, 3, 4, 5]
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(qids)
    one = settings_for(history_backfill_baseline_sources_per_tick=1)
    scope, _, at = await read_all(db_session, console, provider, one)
    assert scope.state == "COMPLETE"
    # The finishing tick rebuilt one source; four are pending.
    with counting(db_session) as single:
        await tick(db_session, console.instance, provider, one, at)
    two = settings_for(history_backfill_baseline_sources_per_tick=2)
    with counting(db_session) as double:
        await tick(db_session, console.instance, provider, two, at + STEP)

    assert len(double) > len(single)
    per_source = len(double) - len(single)
    # HB-12 D2: two bulk reads (observations, exclusion spans) and one bulk
    # upsert, whatever the number of cells.
    assert per_source == 3
    # One source is left (4 - 1 - 2): one rebuild again, so the cost is that of `single`.
    with counting(db_session) as last:
        await tick(db_session, console.instance, provider, two, at + 2 * STEP)
    assert len(last) == len(single)


# ------------------------------------------------------------- more exits


async def test_the_switch_off_from_the_start_creates_no_scope(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    off = settings_for(history_backfill_enabled=False)

    await tick(db_session, console.instance, provider, off, FIRST_TICK)
    await tick(db_session, console.instance, provider, off, FIRST_TICK + STEP)

    assert await scope_of(db_session, console.instance) is None
    assert len(provider.calls) == 2  # the live searches only


async def test_a_stop_request_before_any_scope_creates_none(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    await HistoryBackfillRepository(db_session).request_stop(
        console.instance.id, by="analyst@example.test", at=datetime(2026, 7, 2, tzinfo=UTC)
    )
    provider = ScriptedMockProvider(QIDS)

    await tick(db_session, console.instance, provider, settings_for(), FIRST_TICK)

    assert await scope_of(db_session, console.instance) is None


async def test_a_span_that_cannot_be_read_becomes_a_gap_and_the_scope_ends_partial(
    db_session,
) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    bad = ANCHOR - timedelta(hours=1)  # late in the window: the planner never widens after a gap

    def fail(start: datetime, end: datetime, count: int) -> bool:
        return start <= bad < end and end <= ANCHOR

    provider = ScriptedMockProvider(QIDS, fail=fail)

    scope, _, _ = await read_all(
        db_session, console, provider, settings_for(), max_ticks=60
    )

    assert scope.state == "PARTIAL"
    assert "1 span(s) could not be read" in scope.reason
    progress = decode_progress(scope.progress).progress
    assert [(g.start, g.end) for g in progress.gaps] == [(bad, bad + STEP)]
    starts = {r.bucket_start for r in await history_rows(db_session, console.instance)}
    assert bad not in starts  # nothing was written, not even a zero, for the unread bucket
    assert bad - STEP in starts and bad + STEP in starts
    assert scope.through == ANCHOR - STEP


async def test_the_time_limit_ends_a_reading_partial(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for(**SLOW)
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    await tick(
        db_session, console.instance, provider, settings,
        FIRST_TICK + timedelta(hours=73),
    )

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "PARTIAL"
    assert "did not finish in 72 h" in scope.reason


async def test_no_history_row_reaches_the_anchor(db_session) -> None:
    """Guard 1: history ends at the anchor (exclusive), and live buckets start at it."""
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)

    scope, _, _ = await read_all(db_session, console, provider, settings_for())

    rows = await history_rows(db_session, console.instance)
    assert scope.anchor == ANCHOR
    assert rows and all(r.bucket_start < ANCHOR for r in rows)
    live = [
        r for r in await _metric_rows(db_session, console)
        if r.collection_source not in ("mock_history", "ariel_history")
    ]
    assert min(r.bucket_start for r in live) == ANCHOR
    # No gap and no overlap at the hand-off: the last history bucket is right before it.
    assert max(r.bucket_start for r in rows) == ANCHOR - STEP


async def test_a_lone_bucket_that_fails_or_stays_truncated_writes_nothing() -> None:
    """The live rule, kept for history: no row from a one-bucket search that fails."""
    from app.history import phase_a
    from app.history.planner import PlannerSettings, SearchStatus, Span

    start = T0
    provider = ScriptedMockProvider(
        QIDS,
        truncate_multi=True,  # every multi-bucket search is truncated, so it splits ...
        fail=lambda s, e, count: count == 1 and s == start + STEP,  # ... to a failing lone bucket
    )
    collector = MetricCollector(None, provider, settings=settings_for())  # type: ignore[arg-type]

    found = await collector._search_window(start, 2, 300, max_searches=10)

    assert found.completed.keys() == {start}  # the first lone bucket was read
    assert found.failure is not None and found.failed == Span(start + STEP, start + 2 * STEP)
    results = phase_a.chunk_results(
        Span(start, start + 2 * STEP), found.completed,
        settings=PlannerSettings(), searches=found.searches, failed=found.failed,
    )
    assert [r.status for r in results] == [SearchStatus.COMPLETE, SearchStatus.FAILED]
    assert all(r.lower_bound_buckets == 0 for r in results)


async def test_a_step_larger_than_the_driver_parameter_limit_stores_every_row(
    db_session,
) -> None:
    """asyncpg refuses 32 767 bind parameters; a step is one executemany, never `.values(rows)`."""
    qids = list(range(1, 101))
    columns = len(LogSourceMetric.__table__.columns)
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(qids)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)  # creates the scope

    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    assert report.history is not None and report.history.error is None
    assert report.history.rows * columns > 32_767  # more parameters than one query may bind
    stored = await db_session.scalar(
        select(func.count())
        .select_from(LogSourceMetric)
        .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
        .where(
            LogSource.instance_id == console.instance.id,
            LogSourceMetric.collection_source == "mock_history",
        )
    )
    assert stored == report.history.rows


# ------------------------------------------------------- HB-22 provider fault


def _scope_writes(statements: list[str]) -> list[str]:
    return [
        statement
        for statement in statements
        if "history_backfill_scope" in statement
        and statement.lstrip().upper().startswith(("INSERT", "UPDATE"))
    ]


async def test_a_provider_outage_marks_the_scope_and_the_next_step_clears_it(db_session) -> None:
    """HB-22 D1: a history search that fails at the provider leaves its class and the tick."""
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    down = True

    def fail(start: datetime, end: datetime, count: int) -> bool:
        return down and end <= ANCHOR and count > 1

    provider = ScriptedMockProvider(
        QIDS, fail=fail, failure_type=ProviderUnavailableError
    )
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)

    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING"
    assert scope.last_error_class == "ProviderUnavailableError"
    assert scope.last_error_at == FIRST_TICK + STEP
    assert "the width halves to" in (scope.reason or "")
    progress = (await _decoded(db_session, console)).progress
    assert progress.chunk_buckets is not None and progress.chunk_buckets < 1333 // 2 + 1
    assert progress.chunk_ceiling == progress.chunk_buckets
    assert report.history is not None and report.history.searches == 1
    assert await history_rows(db_session, console.instance) == []
    mark = await db_session.scalar(
        select(CollectionWatermark).where(CollectionWatermark.instance_id == console.instance.id)
    )
    assert mark is not None and mark.watermark_at == ANCHOR + STEP

    down = False
    await tick(db_session, console.instance, provider, settings, FIRST_TICK + 2 * STEP)
    cleared = await scope_of(db_session, console.instance)
    assert cleared is not None
    assert cleared.last_error_class is None and cleared.last_error_at is None
    assert cleared.state == "READING"


async def test_capacity_refusal_yields_without_halving_or_marking_the_scope(db_session) -> None:
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    before = await scope_of(db_session, console.instance)
    assert before is not None
    before_progress = copy.deepcopy(before.progress)
    before_through = before.through
    before.last_error_class = "EarlierProviderError"
    before.last_error_at = FIRST_TICK
    await db_session.commit()

    def refuse_history(start: datetime, end: datetime, count: int) -> bool:
        if end <= ANCHOR and count > 1:
            assert _ARIEL_PRIORITY.get() == "background"
            return True
        return False

    provider.fail = refuse_history
    provider.failure_type = lambda _message: ArielPermitUnavailable(7, 2, 4)  # type: ignore[assignment]
    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)

    yielded = await scope_of(db_session, console.instance)
    assert yielded is not None and yielded.state == "READING"
    assert yielded.progress == before_progress and yielded.through == before_through
    assert yielded.last_error_class == "EarlierProviderError"
    assert yielded.last_error_at == FIRST_TICK
    assert yielded.reason == "deferred: no Ariel permit within 7 s"
    assert report.history is not None and report.history.searches == 0
    assert await history_rows(db_session, console.instance) == []
    assert _ARIEL_PRIORITY.get() == "live"


async def test_a_provider_outage_adds_one_scope_write_and_success_does_not(db_session) -> None:
    """The failure path writes the marker; the success path's statement count stays put."""
    clean = await _step_statements(db_session, QIDS)
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)

    def fail(start: datetime, end: datetime, count: int) -> bool:
        return end <= ANCHOR and count > 1

    provider = ScriptedMockProvider(
        QIDS, fail=fail, failure_type=ProviderUnavailableError
    )
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    with counting(db_session) as failed:
        await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.last_error_class == "ProviderUnavailableError"
    failed_writes = _scope_writes(failed)
    assert len(failed_writes) == 2  # the step's upsert, then the marker
    assert any("last_error_class" in statement for statement in failed_writes)
    # Measured on this suite: a successful history step is 14 statements, and D1 adds
    # none of them. The failure path's extra statement is the marker update above.
    assert clean == 14, clean


# --------------------------------------------------------------- HB-22 top-up


async def test_a_restart_searches_only_sources_without_a_floor(db_session) -> None:
    """Read history now searches the new source and keeps the floors already proved."""
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    scope, _, at = await read_all(db_session, console, provider, settings)
    assert scope.state == "COMPLETE"
    before = {
        (row.log_source_id, row.bucket_start): row.collected_at
        for row in await history_rows(db_session, console.instance)
    }
    previous = copy.deepcopy(scope.progress)
    previous_through = scope.through
    previous_days = scope.days_target
    finished_at = scope.finished_at
    from tests.integration.factories import make_log_source

    late = await make_log_source(db_session, console.instance, qradar_id=9, name="src-9")
    provider.qids = [*QIDS, 9]
    await HistoryBackfillRepository(db_session).request_start(
        console.instance.id, by="analyst@example.test", at=at
    )
    marked = len(provider.calls)
    await tick(db_session, console.instance, provider, settings, at)
    restarted = await scope_of(db_session, console.instance)
    assert restarted is not None and restarted.state == "READING"
    assert restarted.progress == previous
    assert restarted.through == previous_through
    assert restarted.days_target == previous_days

    for index in range(1, 12):
        await tick(db_session, console.instance, provider, settings, at + index * STEP)
        restarted = await scope_of(db_session, console.instance)
        assert restarted is not None
        if (
            restarted.state == "COMPLETE"
            and restarted.finished_at is not None
            and finished_at is not None
            and restarted.finished_at > finished_at
        ):
            break
    else:
        raise AssertionError("the top-up did not finish")

    history_filters = [
        filt
        for call, filt in zip(
            provider.calls[marked:], provider.source_filters[marked:], strict=True
        )
        if call[1] <= ANCHOR
    ]
    assert history_filters
    assert set(history_filters) == {frozenset({9})}
    floors = SourceFloors.decode((await _decoded(db_session, console)).adapter)
    for qid in QIDS:
        assert str(qid) in floors.first_buckets
    rows = await history_rows(db_session, console.instance)
    after = {(row.log_source_id, row.bucket_start): row.collected_at for row in rows}
    assert all(after[key] == stamp for key, stamp in before.items())
    mine = [row.bucket_start for row in rows if row.log_source_id == late.id]
    assert min(mine) == T0 and max(mine) == ANCHOR - STEP


def _phase_a_page(url: str, instance_id: object) -> dict:  # type: ignore[type-arg]
    page = Client(url, ADMIN).get(BASE).json()
    console = next(item for item in page if item["instance_id"] == str(instance_id))
    return family_of(console, "PHASE_A")["scopes"][0]


_KEPT = (
    "days_read",
    "searches",
    "buckets_read",
    "through",
    "floor",
    "target_start",
    "days_target",
    "sources_silent",
    "sources_silent_count",
    "sources_cut",
    "sources_cut_count",
)


async def test_read_history_now_then_stop_keeps_the_finished_evidence(
    db_session, db_schema
) -> None:
    """The operator's path: start, one tick, stop, one tick, and the page still shows it."""
    console = await make_console(db_session, QIDS)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(QIDS)
    settings = settings_for()
    scope, _, at = await read_all(db_session, console, provider, settings)
    assert scope.state == "COMPLETE"
    assert scope.finished_at is not None
    instance = console.instance
    instance_id = instance.id
    await db_session.commit()

    before = _phase_a_page(db_schema, instance_id)
    assert before["state"] == "COMPLETE"
    assert before["searches"] is not None and before["days_read"] is not None
    started = Client(db_schema, ADMIN).post(f"{BASE}/{instance_id}/start")
    assert started.status_code == 200, started.text
    await db_session.commit()
    # The API wrote the request on its own connection. Drop this session's copy
    # and reload the instance the next tick collects.
    db_session.expire_all()
    await db_session.refresh(instance)

    await tick(db_session, instance, provider, settings, at)
    await db_session.commit()
    reading = _phase_a_page(db_schema, instance_id)
    assert reading["state"] == "READING"
    assert "topping up" in (reading["reason"] or "")
    assert {name: reading[name] for name in _KEPT} == {name: before[name] for name in _KEPT}

    stopped = Client(db_schema, ADMIN).post(f"{BASE}/{instance_id}/stop")
    assert stopped.status_code == 200, stopped.text
    await db_session.commit()
    db_session.expire_all()
    await db_session.refresh(instance)
    await tick(db_session, instance, provider, settings, at + STEP)
    await db_session.commit()

    partial = _phase_a_page(db_schema, instance_id)
    assert partial["state"] == "PARTIAL"
    assert partial["reason"] == "stopped by admin"
    assert {name: partial[name] for name in _KEPT} == {name: before[name] for name in _KEPT}
