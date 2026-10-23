"""PW-13c1: the collector reads a watch's past (PHASE-G §H.2, §H.4, §H.5, §H.6).

Everything runs through `PwCollector.collect` (and the pipeline) on the mock provider: the real
query builder, parser, split driver and fold; only the events are scripted. The tests read the
rows a tick wrote, never a helper's return value.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, func, select, text

from app.collectors.pw_collector import PwCollector, _bucket_row, _stamp, not_collected_fact
from app.core.config import Settings
from app.history.origin import HistoryOrigin
from app.history.planner import ScopeProgress
from app.models.enums import (
    PwBucketOrigin,
    PwHistoryState,
    PwScopeKind,
    PwWatchStatus,
)
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.property_watch.bootstrap import (
    UNREADABLE_PROGRESS,
    WAITING_FOR_BUDGET,
    history_columns,
    read_progress,
)
from app.providers.base import ProviderError
from app.providers.dto import PwSearchKind
from app.providers.mock import MockPwSearch, MockQRadarProvider
from app.repositories.property_watch import PropertyWatchRepository
from app.services.ariel_permit import _ARIEL_PRIORITY, ArielPermitUnavailable
from app.services.locks import collector_lock_key
from app.services.pw_pipeline import COLLECTOR_NAME, PwInstancePipeline
from tests.integration.factories import make_instance
from tests.integration.test_pw_collection import (
    NOW,
    STEP,
    _group,
    add_watch,
    buckets_of,
    cfg,
    reload,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DAY = timedelta(days=1)
#: `NOW` is 10:07 with a 120 s settle: the latest closed bucket starts at 10:00 and ends here.
BOUNDARY = datetime(2026, 10, 13, 10, 5, tzinfo=UTC)
T0_1D = BOUNDARY - DAY


def hcfg(**overrides: object) -> Settings:
    """The collection settings of these tests: a roomy tick, the bootstrap on."""
    values: dict[str, object] = {
        "pw_bootstrap_enabled": True,
        "pw_max_searches_per_tick": 60,
        "pw_max_searches_per_watch_per_tick": 12,
        "pw_bootstrap_searches_per_tick": 12,
    }
    values.update(overrides)
    return cfg(**values)


def key_of(at: datetime) -> int:
    return int(at.timestamp()) // 300


def script_of(
    events: Callable[[datetime], dict[str, int]],
) -> Callable[[int, tuple[str, ...]], list[dict]]:
    """A mock script from `bucket start -> {value: events}`; the first property carries the
    value, the others a constant."""

    def script(key: int, names: tuple[str, ...]) -> list[dict]:
        at = datetime.fromtimestamp(key * 300, tz=UTC)
        return [
            {"values": {n: v if i == 0 else "x" for i, n in enumerate(names)}, "events": c}
            for v, c in events(at).items()
        ]

    return script


def flat_provider(events: dict[str, int] | None = None) -> MockQRadarProvider:
    provider = MockQRadarProvider(seed=1)
    fixed = events or {"alice": 10}
    provider.pw_script = script_of(lambda _at: dict(fixed))
    return provider


async def tick(  # type: ignore[no-untyped-def]
    session,
    instance,
    provider,
    *,
    now: datetime = NOW,
    settings: Settings | None = None,
    monotonic: Callable[[], float] | None = None,
):
    report = await PwCollector(
        session,
        provider,
        settings=settings or hcfg(),
        clock=lambda: now,
        monotonic=monotonic,
    ).collect(instance)
    await session.commit()
    return report


def progress_of(watch: PropertyWatch) -> tuple[ScopeProgress, str]:
    read = read_progress(watch)  # type: ignore[arg-type]
    assert read is not None
    return read


def state_of(watch: PropertyWatch) -> PwHistoryState:
    return PwHistoryState(watch.history_state)


async def history_rows(session, watch) -> list[PwBucket]:  # type: ignore[no-untyped-def]
    return [b for b in await buckets_of(session, watch) if b.origin == PwBucketOrigin.HISTORY.value]


def starts(rows: list[PwBucket]) -> list[datetime]:
    return [b.bucket_start for b in rows]


def tiling(first: datetime, count: int) -> list[datetime]:
    return [first + STEP * i for i in range(count)]


def seed_reading(
    watch: PropertyWatch,
    *,
    target: datetime,
    floor: datetime,
    through: datetime | None,
    started: datetime,
    chunk: int | None = None,
    ceiling: int | None = None,
    failures: int = 0,
    rows_seen: int = 0,
    non_silent: int = 0,
    days: int | None = None,
) -> ScopeProgress:
    """Put a watch in `READING` with a stored progress, as a previous tick would have."""
    progress = ScopeProgress(
        target_start=target,
        started_at=started,
        floor=floor,
        first_event_bucket=floor - DAY,
        through=through,
        scan_cursor=floor,
        chunk_buckets=chunk,
        chunk_ceiling=ceiling,
        failures=failures,
        rows_seen=rows_seen,
        non_silent_buckets=non_silent,
    )
    columns = history_columns(progress, state=PwHistoryState.READING, reason="seeded")
    _stamp(columns, progress, reason="seeded", version=watch.watch_version)
    for name, value in columns.items():
        setattr(watch, name, value)
    watch.history_days = days or max(1, int((BOUNDARY - target) / DAY))
    return progress


# ------------------------------------------------------------------ begin and hand-off
async def test_a_new_watch_with_history_reads_it_and_hands_off_with_no_gap_and_no_overlap(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    report = await tick(db_session, instance, provider)
    await reload(db_session, watch)

    rows = await buckets_of(db_session, watch)
    assert starts(rows) == tiling(T0_1D, 288)
    assert {b.origin for b in rows} == {PwBucketOrigin.HISTORY.value}
    assert all(b.scope_changed is False and b.in_maintenance is False for b in rows)
    assert rows[0].query_provenance["history"] is True
    assert rows[0].query_provenance["membership"] == "current"
    assert rows[0].query_provenance["floor"] == T0_1D.isoformat()
    last = BOUNDARY - STEP
    assert state_of(watch) is PwHistoryState.COMPLETE
    assert watch.collected_through == last and watch.evaluated_through == last
    assert watch.history_through == last and watch.history_end == BOUNDARY
    assert watch.history_floor == T0_1D and watch.history_target_start == T0_1D
    assert watch.history_finished_at == NOW
    assert watch.last_status is None and watch.last_status_reason is None
    # the scan is the first TOTALS (reused), then one GROUPED chunk of the whole day
    assert report.history_searches == 2 and provider.pw_searches[0].search is PwSearchKind.TOTALS
    assert report.watches_reading == 1 and report.watches_due == 0
    # the values it admitted are tracked from the first history bucket
    value = await db_session.scalar(select(PwValue).where(PwValue.watch_id == watch.id))
    assert value is not None and value.first_seen_at == T0_1D == value.tracked_since
    assert await db_session.scalar(select(func.count()).select_from(PwValueMetric)) == 288

    # zero gap, zero overlap: the first live bucket starts at history_end, and no history
    # row is written again
    before = {b.bucket_start: b.collected_at for b in rows}
    await tick(db_session, instance, provider, now=NOW + STEP)
    await reload(db_session, watch)
    after = await buckets_of(db_session, watch)
    assert starts(after) == tiling(T0_1D, 289)
    assert after[-1].bucket_start == watch.history_end
    assert after[-1].origin == PwBucketOrigin.LIVE.value
    assert {b.bucket_start: b.collected_at for b in after[:-1]} == before
    assert {b.origin for b in after[:-1]} == {PwBucketOrigin.HISTORY.value}
    assert watch.collected_through == BOUNDARY


async def test_with_the_switch_off_the_watch_is_partial_and_collects_live_from_this_tick(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=3)
    provider = flat_provider()
    await tick(db_session, instance, provider, settings=hcfg(pw_bootstrap_enabled=False))
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    assert starts(rows) == [BOUNDARY - STEP]
    assert rows[0].origin == PwBucketOrigin.LIVE.value
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert watch.history_progress == {} and watch.history_target_start is None
    assert watch.history_through is None and watch.history_end is None
    assert watch.history_finished_at == NOW
    assert watch.collected_through == BOUNDARY - STEP
    assert all(s.search is not PwSearchKind.GROUPED or s.window_end - s.window_start == STEP
               for s in provider.pw_searches), "no history search was sent"


async def test_a_watch_with_stored_buckets_is_not_read_even_with_history_days_set(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(
        db_session, instance, history_days=3, through=BOUNDARY - STEP * 2
    )
    watch.evaluated_through = BOUNDARY - STEP * 2
    provider = flat_provider()
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.NOT_REQUESTED
    assert starts(await buckets_of(db_session, watch)) == [BOUNDARY - STEP]
    assert watch.history_progress == {} and watch.history_through is None


async def test_a_watch_that_asked_for_no_history_is_live_as_before(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    await tick(db_session, instance, flat_provider())
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.NOT_REQUESTED
    assert watch.history_days is None and watch.collected_through == BOUNDARY - STEP


# ------------------------------------------------------------------ a tick that is half done
async def test_a_tick_that_settles_only_a_prefix_moves_through_only_past_it(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    t0 = BOUNDARY - 2 * DAY

    first = await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert first.history_searches == 2  # the scan (reused as the TOTALS) and one chunk
    assert state_of(watch) is PwHistoryState.READING
    assert watch.history_through == t0 + STEP * 287 and watch.history_floor == t0
    assert watch.collected_through is None and watch.evaluated_through is None
    assert watch.history_end is None and watch.history_finished_at is None
    assert watch.last_status == PwWatchStatus.READING_HISTORY.value
    assert watch.last_status_reason == (
        "Reading history from QRadar: 1.0 of 2.0 days; nothing is judged until it finishes"
    )
    progress, _ = progress_of(watch)
    assert progress.searches == 2 and progress.buckets_read == 288
    assert starts(await buckets_of(db_session, watch)) == tiling(t0, 288)

    # the next tick continues at through + one bucket, and the end is reached together
    second = await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert second.history_searches == 2  # TOTALS and one chunk
    assert state_of(watch) is PwHistoryState.COMPLETE
    assert starts(await buckets_of(db_session, watch)) == tiling(t0, 576)
    assert watch.collected_through == BOUNDARY - STEP == watch.history_through
    assert watch.last_status is None


async def test_a_crash_inside_the_write_leaves_the_watch_where_the_stored_rows_are(
    db_session, monkeypatch
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    watch_id = watch.id
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    through = watch.history_through
    assert through is not None

    async def explode(self, rows):  # type: ignore[no-untyped-def]
        raise RuntimeError("worker died")

    monkeypatch.setattr(PropertyWatchRepository, "write_metrics", explode)
    with pytest.raises(RuntimeError):
        await PwCollector(db_session, provider, settings=thin, clock=lambda: NOW).collect(instance)
    await db_session.rollback()
    monkeypatch.undo()
    stored = await db_session.get(PropertyWatch, watch_id, populate_existing=True)
    assert stored is not None and stored.history_through == through
    assert len(await buckets_of(db_session, stored)) == 288, "nothing of the failed step stayed"
    await db_session.refresh(instance)

    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, stored)
    assert state_of(stored) is PwHistoryState.COMPLETE
    assert len(await buckets_of(db_session, stored)) == 576


async def test_a_crash_while_beginning_leaves_the_watch_not_requested(
    db_session, monkeypatch
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    watch_id = watch.id
    await db_session.commit()

    async def explode(self, rows):  # type: ignore[no-untyped-def]
        raise RuntimeError("worker died")

    monkeypatch.setattr(PropertyWatchRepository, "update_watches", explode)
    with pytest.raises(RuntimeError):
        await PwCollector(
            db_session, flat_provider(), settings=hcfg(), clock=lambda: NOW
        ).collect(instance)
    await db_session.rollback()
    monkeypatch.undo()
    stored = await db_session.get(PropertyWatch, watch_id, populate_existing=True)
    assert stored is not None and state_of(stored) is PwHistoryState.NOT_REQUESTED
    assert stored.history_progress == {}
    assert await buckets_of(db_session, stored) == []


async def test_two_overlapping_ticks_the_second_skips_and_writes_nothing(db_session) -> None:
    """T-136's one pipeline lock is what §H.2.4's per-watch lock was for (no second lock)."""
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    watch_id = watch.id
    await db_session.commit()
    settings = hcfg()
    key = collector_lock_key(instance.id, COLLECTOR_NAME)
    url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    holder = create_engine(url, future=True)
    conn = holder.connect()
    conn.execute(
        text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
            ns=settings.collection_advisory_lock_namespace, key=key
        )
    )
    provider = flat_provider()
    try:
        skipped = await PwInstancePipeline(
            db_session, provider, settings=settings, clock=lambda: NOW
        ).run(instance)
        assert skipped.collection.status == "skipped_locked" and skipped.steps == []
        assert provider.pw_searches == []
        await db_session.rollback()
        stored = await db_session.get(PropertyWatch, watch_id, populate_existing=True)
        assert stored is not None and state_of(stored) is PwHistoryState.NOT_REQUESTED
        assert await buckets_of(db_session, stored) == []
    finally:
        conn.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=settings.collection_advisory_lock_namespace, key=key
            )
        )
        conn.close()
        holder.dispose()
    await db_session.refresh(instance)
    ran = await PwInstancePipeline(
        db_session, provider, settings=settings, clock=lambda: NOW
    ).run(instance)
    await db_session.commit()
    assert ran.collection.status == "ok" and ran.steps[0] == "collect"
    stored = await db_session.get(PropertyWatch, watch_id, populate_existing=True)
    assert stored is not None and len(await buckets_of(db_session, stored)) == 288


# ------------------------------------------------------------------ budget order
async def test_live_watches_spend_first_and_a_starved_reading_waits(db_session) -> None:
    instance = await make_instance(db_session)
    live_a = await add_watch(db_session, instance)
    live_b = await add_watch(db_session, instance)
    reading = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    # two searches each (grouped, totals): the live watches use the whole tick
    report = await tick(
        db_session, instance, provider, settings=hcfg(
            pw_max_searches_per_tick=4,
            pw_max_searches_per_watch_per_tick=4,
            pw_bootstrap_searches_per_tick=4,
        ),
    )
    for w in (live_a, live_b, reading):
        await reload(db_session, w)
    assert len(await buckets_of(db_session, live_a)) == 1
    assert len(await buckets_of(db_session, live_b)) == 1
    assert report.history_searches == 0 and report.searches_sent == 4
    assert state_of(reading) is PwHistoryState.READING
    assert await buckets_of(db_session, reading) == []
    assert reading.collected_through is None, "a READING watch is skipped by the live plan"
    assert reading.last_status == PwWatchStatus.READING_HISTORY.value
    progress, reason = progress_of(reading)
    assert reason == WAITING_FOR_BUDGET == "waiting for search budget"
    assert progress.floor is None and progress.through is None and progress.searches == 0

    # one search left over: the floor scan runs, and the read waits for the next tick
    report = await tick(
        db_session, instance, provider, now=NOW + STEP, settings=hcfg(
            pw_max_searches_per_tick=5,
            pw_max_searches_per_watch_per_tick=5,
            pw_bootstrap_searches_per_tick=5,
        ),
    )
    await reload(db_session, reading)
    assert report.history_searches == 1
    progress, reason = progress_of(reading)
    assert progress.floor == T0_1D and progress.through is None and progress.searches == 1
    assert reason == WAITING_FOR_BUDGET
    assert await history_rows(db_session, reading) == []


async def test_a_pinned_reading_that_cannot_pay_for_its_step_says_which_limit_starves_it(
    db_session,
) -> None:
    """A pinned step needs 3 searches a tick; at 2 the reading waits and says so (T-152).

    The reading is not ended: it keeps waiting, and the time limit still hands it off.
    """
    instance = await make_instance(db_session)
    reading = await add_watch(db_session, instance, history_days=2, pinned=(("alice",),))
    # a reading under way: the floor is known, nothing is read yet
    seed_reading(
        reading,
        target=BOUNDARY - 2 * DAY,
        floor=BOUNDARY - 2 * DAY,
        through=None,
        started=NOW - timedelta(hours=1),
    )
    await db_session.commit()
    provider = flat_provider()

    # the setting is below the step: the reading waits, spends nothing, and says why
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    report = await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, reading)
    assert report.history_searches == 0
    assert provider.pw_searches == [], "a starved reading spends no search"
    assert state_of(reading) is PwHistoryState.READING, "the reading is not ended"
    progress, reason = progress_of(reading)
    assert "PW_BOOTSTRAP_SEARCHES_PER_TICK is 2" in reason
    assert "at least 3 searches per tick" in reason
    assert progress.buckets_read == 0, "the reading stands where it was"

    # a budget that pays for the step lets the reading go on
    roomy = hcfg(pw_bootstrap_searches_per_tick=3)
    report = await tick(db_session, instance, provider, now=NOW + STEP, settings=roomy)
    await reload(db_session, reading)
    assert report.history_searches == 3, "the TOTALS, the PINNED and one grouped chunk"
    progress, _ = progress_of(reading)
    assert progress.buckets_read == 288
    assert state_of(reading) is PwHistoryState.READING


async def test_a_reading_that_cannot_pay_for_its_step_spends_no_floor_scan(
    db_session,
) -> None:
    """A reading that has not found its floor yet spends nothing either (T-152).

    The scan would find the floor, but the setting cannot pay for the step that follows, so
    the reading waits for the operator instead of spending a search a tick.
    """
    instance = await make_instance(db_session)
    reading = await add_watch(db_session, instance, history_days=2, pinned=(("alice",),))
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    report = await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, reading)
    assert report.history_searches == 0
    assert provider.pw_searches == [], "not even the floor scan"
    assert state_of(reading) is PwHistoryState.READING
    progress, reason = progress_of(reading)
    assert progress.floor is None
    assert "PW_BOOTSTRAP_SEARCHES_PER_TICK is 2" in reason
    assert "at least 3 searches per tick" in reason


async def test_the_seconds_per_tick_budget_stops_new_searches(db_session) -> None:
    instance = await make_instance(db_session)
    reading = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    clock = iter([0.0])

    def monotonic() -> float:
        return next(clock, 10_000.0)  # the tick started at 0; every later look is past 180 s

    report = await tick(db_session, instance, provider, monotonic=monotonic)
    await reload(db_session, reading)
    assert provider.pw_searches == [] and report.history_searches == 0
    assert state_of(reading) is PwHistoryState.READING
    assert progress_of(reading)[1] == WAITING_FOR_BUDGET


async def test_no_search_starts_after_the_deadline_passes_mid_step(db_session) -> None:
    instance = await make_instance(db_session)
    reading = await add_watch(db_session, instance, history_days=2)
    provider = flat_provider()
    calls = {"n": 0}
    real = provider.get_pw_keyed

    async def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = await real(*args, **kwargs)
        calls["n"] += 1
        return result

    provider.get_pw_keyed = counted  # type: ignore[method-assign]

    def monotonic() -> float:
        # the deadline passes once the first search is back
        return 10_000.0 if calls["n"] else 0.0

    report = await tick(db_session, instance, provider, monotonic=monotonic)
    await reload(db_session, reading)
    assert report.history_searches == 1, "the scan ran; nothing started after the deadline"
    assert progress_of(reading)[0].floor == BOUNDARY - 2 * DAY
    assert await buckets_of(db_session, reading) == []


async def test_a_split_does_not_start_once_the_deadline_has_passed(db_session) -> None:
    instance = await make_instance(db_session)
    reading = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    provider.pw_force_truncated = (
        lambda s: s.search is PwSearchKind.GROUPED and s.window_end - s.window_start > STEP
    )
    calls = {"n": 0}
    real = provider.get_pw_keyed

    async def counted(*args, **kwargs):  # type: ignore[no-untyped-def]
        result = await real(*args, **kwargs)
        calls["n"] += 1
        return result

    provider.get_pw_keyed = counted  # type: ignore[method-assign]

    def monotonic() -> float:
        # the scan and the first GROUPED search run in time; the deadline passes after them
        return 10_000.0 if calls["n"] >= 2 else 0.0

    report = await tick(db_session, instance, provider, monotonic=monotonic)
    await reload(db_session, reading)
    # the first GROUPED search was truncated, and its split (which would be a third search)
    # never started: the time budget covers the splits of a search too
    assert report.history_searches == 2
    assert await buckets_of(db_session, reading) == []
    assert state_of(reading) is PwHistoryState.READING


# ------------------------------------------------------------------ the origin CASE
async def test_a_write_keeps_the_marker_of_a_same_version_row_and_replaces_another_versions(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch_id = (await add_watch(db_session, instance)).id
    at = T0_1D
    repo = PropertyWatchRepository(db_session)

    def row(version: int, origin: HistoryOrigin) -> dict:
        fact = not_collected_fact(
            bucket_start=at, bucket_seconds=300, watch_version=version, pinned=False
        )
        return _bucket_row(watch_id, fact, provenance={}, collected_at=NOW, origin=origin)

    async def stored() -> tuple[int, str]:
        await db_session.commit()
        db_session.expire_all()
        bucket = await db_session.get(PwBucket, (watch_id, at))
        assert bucket is not None
        return bucket.watch_version, bucket.origin

    await repo.write_buckets([row(1, HistoryOrigin.HISTORY)])
    assert await stored() == (1, "HISTORY")
    await repo.write_buckets([row(1, HistoryOrigin.LIVE)])
    assert await stored() == (1, "HISTORY"), "same version: the marker is never changed"
    await repo.write_buckets([row(2, HistoryOrigin.LIVE)])
    assert await stored() == (2, "LIVE"), "another version: the whole row, marker included"
    await repo.write_buckets([row(2, HistoryOrigin.HISTORY)])
    assert await stored() == (2, "LIVE"), "same version again: kept"
    await repo.write_buckets([row(3, HistoryOrigin.HISTORY)])
    assert await stored() == (3, "HISTORY")


async def test_a_live_write_onto_a_history_row_of_the_same_version_keeps_history(
    db_session,
) -> None:
    """The collector's own path: a live tick that re-collects a history bucket."""
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    # a restored watermark makes the live path collect the last history bucket again
    watch.collected_through = BOUNDARY - 2 * STEP
    await db_session.commit()
    await tick(db_session, instance, provider, now=NOW + STEP)
    rows = {b.bucket_start: b for b in await buckets_of(db_session, watch)}
    rewritten = rows[BOUNDARY - STEP]
    assert rewritten.collected_at == NOW + STEP, "the live path really wrote this row again"
    assert rewritten.origin == PwBucketOrigin.HISTORY.value


# ------------------------------------------------------------------ the version restart
async def test_a_version_bump_restarts_the_reading_and_the_new_history_replaces_old_rows(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider({"alice": 10, "old": 5})
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert progress_of(watch)[0].searches == 2
    # the version-1 rows are live ones, as an earlier live run would have left them
    await db_session.execute(text("UPDATE pw_bucket SET origin = 'LIVE'"))
    # PW-13d's patch route bumps the version and touches nothing of `history_*`
    watch.watch_version = 2
    await db_session.commit()

    later = NOW + STEP
    provider = flat_provider({"alice": 10, "new": 7})
    await tick(db_session, instance, provider, now=later)
    await reload(db_session, watch)
    boundary = BOUNDARY + STEP
    rows = await buckets_of(db_session, watch)
    assert starts(rows) == tiling(T0_1D, 1) + tiling(boundary - DAY, 288)
    # rows of the new reading: version 2 and HISTORY, also where an old LIVE row stood
    new = [b for b in rows if b.bucket_start >= boundary - DAY]
    assert {(b.watch_version, b.origin) for b in new} == {(2, "HISTORY")}
    assert {(b.watch_version, b.origin) for b in rows[:1]} == {(1, "LIVE")}
    assert state_of(watch) is PwHistoryState.COMPLETE
    assert watch.history_target_start == boundary - DAY
    assert watch.history_started_at == later
    assert watch.collected_through == boundary - STEP == watch.evaluated_through
    assert watch.history_end == boundary
    assert watch.history_progress["adapter"]["watch_version"] == 2  # type: ignore[index]
    # the old version's values retire; the new version's are its own
    values = (await db_session.scalars(select(PwValue).where(PwValue.watch_id == watch.id))).all()
    assert {(v.value_key, v.watch_version, v.active) for v in values if v.value_key == "old"} == {
        ("old", 1, False)
    }
    assert any(v.watch_version == 2 and v.value_key == "new" and v.active for v in values)


async def test_a_reading_in_progress_restarts_when_the_version_moves(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert watch.history_through is not None
    watch.watch_version = 2
    await db_session.commit()
    await tick(db_session, instance, provider, now=NOW + STEP, settings=thin)
    await reload(db_session, watch)
    progress, _ = progress_of(watch)
    # restarted: progress of the new version, from the new target
    assert state_of(watch) is PwHistoryState.READING
    assert progress.target_start == BOUNDARY + STEP - 2 * DAY
    assert progress.started_at == NOW + STEP and progress.buckets_read == 288
    assert watch.history_progress["adapter"]["watch_version"] == 2  # type: ignore[index]
    assert watch.collected_through is None and watch.evaluated_through is None


# ------------------------------------------------------------------ an unreadable progress
@pytest.mark.parametrize("damage", ["garbage", "no_version", "column_differs"])
async def test_a_progress_that_does_not_read_back_hands_off_partial_from_the_column(
    db_session, damage: str
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    through = BOUNDARY - 3 * STEP
    seed_reading(
        watch,
        target=BOUNDARY - 2 * DAY,
        floor=BOUNDARY - 2 * DAY,
        through=through,
        started=NOW - timedelta(hours=1),
    )
    if damage == "garbage":
        watch.history_progress = {"nonsense": True}
    elif damage == "no_version":
        watch.history_progress = {
            **watch.history_progress,
            "adapter": {"reason": "seeded"},
        }
    else:
        watch.history_floor = BOUNDARY - 2 * DAY + STEP
    await db_session.commit()
    provider = flat_provider()
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert provider.pw_searches == [], "an unreadable reading searches nothing"
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert progress_of(watch)[1] == UNREADABLE_PROGRESS
    assert watch.collected_through == BOUNDARY - STEP == watch.evaluated_through
    assert watch.history_end == BOUNDARY and watch.history_through == through
    assert watch.last_status is None


async def test_an_unreadable_reading_that_settled_nothing_hands_off_with_null_watermarks(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    watch.history_state = PwHistoryState.READING.value
    watch.history_progress = {"nonsense": True}
    await db_session.commit()
    await tick(db_session, instance, flat_provider())
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert watch.collected_through is None and watch.history_end is None
    assert watch.history_progress == {}


# ------------------------------------------------------------------ equivalence (§H.2.5)
EQUIVALENCE_COLUMNS = [
    "bucket_seconds",
    "watch_version",
    "total_events",
    "parsed_events",
    "unparsed_events",
    "untracked_events",
    "values_measurement",
    "pinned_measurement",
    "totals_complete",
    "observed_values",
    "tracked_values_observed",
    "admitted_values",
    "below_admission_threshold",
    "rejected_over_quota",
    "admission_truncated",
    "scope_member_count",
    "scope_fingerprint",
    "scope_changed",
    "in_maintenance",
]
VALUE_COLUMNS = [
    "component_0",
    "component_1",
    "truncated",
    "pinned",
    "first_seen_at",
    "last_seen_at",
    "tracked_since",
    "tracked_until",
    "active",
    "retired_at",
]


def churn(at: datetime) -> dict[str, int]:
    """Values that come, go and sit under the admission threshold, bucket by bucket."""
    index = key_of(at)
    out = {"alice": 10}
    if index % 3 == 0:
        out["bob"] = 6
    if index % 7 == 0:
        out["dave"] = 2  # under pw_min_admission_events
    if at >= T0_1D + 12 * timedelta(hours=1):
        out["carol"] = 4
    return out


async def test_history_rows_equal_what_live_collection_writes_bucket_by_bucket(
    db_session,
) -> None:
    from tests.integration.factories import make_log_source

    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=4242)
    pins = [["zed"]]
    live = await add_watch(
        db_session, instance, source=source, pinned=pins, through=T0_1D - STEP
    )
    reading = await add_watch(
        db_session, instance, source=source, pinned=pins, history_days=1, enabled=False
    )
    live_id, reading_id = live.id, reading.id
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = script_of(churn)
    # the live control: one tick per bucket, 288 of them (the reading watch is disabled)
    for k in range(288):
        await tick(
            db_session, instance, provider, now=T0_1D + STEP * k + timedelta(minutes=7)
        )
    reading = await db_session.get(PropertyWatch, reading_id)
    assert reading is not None
    reading.enabled = True
    await db_session.commit()
    await tick(db_session, instance, provider)  # history, in chunks (one search covers a day)

    live_rows = {b.bucket_start: b for b in await buckets_of(db_session, live)}
    hist_rows = {b.bucket_start: b for b in await buckets_of(db_session, reading)}
    assert set(live_rows) == set(hist_rows) == set(tiling(T0_1D, 288))
    for at, expected in live_rows.items():
        got = hist_rows[at]
        for column in EQUIVALENCE_COLUMNS:
            assert getattr(got, column) == getattr(expected, column), (at, column)
        assert got.origin == "HISTORY" and expected.origin == "LIVE"
        assert got.query_provenance["history"] is True
        assert "history" not in expected.query_provenance
    # the same values, with the same tracking windows and the same counts per bucket
    async def values_of(watch_id: uuid.UUID) -> dict[str, PwValue]:
        rows = await db_session.scalars(
            select(PwValue).where(PwValue.watch_id == watch_id)
        )
        return {v.value_key: v for v in rows.all()}

    live_values, hist_values = await values_of(live_id), await values_of(reading_id)
    assert set(live_values) == set(hist_values) and "zed" in hist_values
    assert "dave" not in hist_values, "under the admission threshold in every bucket"
    for key, expected_value in live_values.items():
        for column in VALUE_COLUMNS:
            assert getattr(hist_values[key], column) == getattr(expected_value, column), (
                key,
                column,
            )

    async def metrics_of(values: dict[str, PwValue]) -> set[tuple[str, datetime, int, int]]:
        by_id = {v.id: k for k, v in values.items()}
        rows = await db_session.scalars(
            select(PwValueMetric).where(PwValueMetric.pw_value_id.in_(list(by_id)))
        )
        return {(by_id[m.pw_value_id], m.bucket_start, m.events, m.bucket_seconds) for m in rows}

    live_metrics, hist_metrics = await metrics_of(live_values), await metrics_of(hist_values)
    assert live_metrics == hist_metrics and len(hist_metrics) > 288


# ------------------------------------------------------------------ §H.6 rows
async def test_row_1_nothing_is_written_before_the_floor_and_the_reading_is_partial(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=3)
    t0 = BOUNDARY - 3 * DAY
    first_event = t0 + timedelta(hours=12)
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = script_of(lambda at: {"alice": 10} if at >= first_event else {})
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    floor = first_event + DAY  # the first event plus the 24 h margin
    rows = await buckets_of(db_session, watch)
    assert starts(rows) == tiling(floor, int((BOUNDARY - floor) / STEP))
    assert watch.history_floor == floor and watch.history_target_start == t0
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert "less than asked" in progress_of(watch)[1]
    assert watch.collected_through == BOUNDARY - STEP and watch.history_end == BOUNDARY
    assert not [b for b in rows if b.bucket_start < floor], "no zero and no gap row before F"
    assert all(b.query_provenance["floor"] == floor.isoformat() for b in rows)


async def test_row_1_a_scope_with_no_event_in_the_window_is_unavailable_and_starts_live(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = script_of(lambda at: {})
    report = await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert report.history_searches == 1
    assert state_of(watch) is PwHistoryState.UNAVAILABLE
    assert await buckets_of(db_session, watch) == []
    assert watch.collected_through is None and watch.evaluated_through is None
    assert watch.history_end is None and watch.history_finished_at == NOW
    assert "no event of the scope" in progress_of(watch)[1]
    assert watch.last_status is None
    # the existing first-run seed starts the live path
    await tick(db_session, instance, provider, now=NOW + STEP)
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    assert starts(rows) == [BOUNDARY] and rows[0].origin == "LIVE"


async def test_row_1_a_scan_that_fails_proves_nothing_and_three_failures_are_unavailable(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    provider.pw_fault = lambda s: ProviderError("boom") if s.search is PwSearchKind.TOTALS else None
    for k in range(3):
        report = await tick(db_session, instance, provider, now=NOW + STEP * k)
        await reload(db_session, watch)
        assert report.history_searches == 1, "a failed scan is not retried inside the tick"
        assert await buckets_of(db_session, watch) == []
        if k < 2:
            assert state_of(watch) is PwHistoryState.READING
            assert progress_of(watch)[0].scan_failures == k + 1
    assert state_of(watch) is PwHistoryState.UNAVAILABLE
    assert watch.collected_through is None and watch.history_end is None


async def test_row_3_a_failed_chunk_writes_no_row_holds_through_and_halves_the_width(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    t0 = BOUNDARY - 2 * DAY
    second = t0 + STEP * 288
    provider = flat_provider()

    def fail_second_chunk(search: MockPwSearch) -> Exception | None:
        late = search.window_start >= second
        return ProviderError("boom") if late and search.search is PwSearchKind.GROUPED else None

    provider.pw_fault = fail_second_chunk
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    # no row for the failed chunk
    assert starts(await buckets_of(db_session, watch)) == tiling(t0, 288)
    progress, _ = progress_of(watch)
    assert watch.history_through == second - STEP
    assert progress.chunk_buckets == 144 == progress.chunk_ceiling and progress.failures == 0
    assert state_of(watch) is PwHistoryState.READING and watch.collected_through is None

    provider.pw_fault = None
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.COMPLETE
    assert starts(await buckets_of(db_session, watch)) == tiling(t0, 576)


async def test_capacity_refusal_yields_without_halving_or_advancing(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    floor = BOUNDARY - DAY
    seeded = seed_reading(
        watch,
        target=floor,
        floor=floor,
        through=None,
        started=NOW - timedelta(minutes=10),
        chunk=288,
        ceiling=288,
        days=1,
    )
    await db_session.commit()
    provider = flat_provider()

    def refuse_grouped(search: MockPwSearch) -> Exception | None:
        if search.search is PwSearchKind.GROUPED:
            assert _ARIEL_PRIORITY.get() == "background"
            return ArielPermitUnavailable(7, 2, 4)
        return None

    provider.pw_fault = refuse_grouped
    report = await tick(db_session, instance, provider)
    await reload(db_session, watch)

    progress, reason = progress_of(watch)
    assert state_of(watch) is PwHistoryState.READING
    assert progress == seeded and watch.history_through is None
    assert reason == "deferred: no Ariel permit within 7 s"
    assert report.watches[0].searches_sent == 0
    assert await history_rows(db_session, watch) == []
    assert _ARIEL_PRIORITY.get() == "live"


async def test_row_3_a_chunk_cut_short_by_the_budget_keeps_nothing_and_halves_the_width(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    provider = flat_provider()
    provider.pw_force_truncated = (
        lambda s: s.search is PwSearchKind.GROUPED and s.window_end - s.window_start > STEP
    )
    # the scan, then three searches of 288, 144 and 72 buckets, each truncated; no fourth
    report = await tick(
        db_session, instance, provider, settings=hcfg(pw_bootstrap_searches_per_tick=4)
    )
    await reload(db_session, watch)
    assert report.history_searches == 4
    assert await buckets_of(db_session, watch) == [], "a truncated search is never written"
    progress, _ = progress_of(watch)
    assert progress.through is None and progress.chunk_buckets == 144
    assert progress.searches == 4, "the splits are in the record of what was spent"
    assert state_of(watch) is PwHistoryState.READING


async def test_row_3_three_failures_at_twelve_buckets_make_a_gap_and_the_reading_goes_on(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    floor = BOUNDARY - 24 * STEP
    seed_reading(
        watch,
        target=floor,
        floor=floor,
        through=None,
        started=NOW - timedelta(minutes=10),
        chunk=12,
        ceiling=12,
        failures=2,
        days=1,
    )
    await db_session.commit()
    provider = flat_provider()
    hole = floor

    def fail_the_hole(search: MockPwSearch) -> Exception | None:
        in_hole = search.window_start == hole
        return ProviderError("boom") if in_hole and search.search is PwSearchKind.GROUPED else None

    provider.pw_fault = fail_the_hole
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    progress, _ = progress_of(watch)
    assert [(g.start, g.end) for g in progress.gaps] == [(floor, floor + STEP * 12)]
    assert watch.history_through == floor + STEP * 11 and progress.failures == 0
    assert await buckets_of(db_session, watch) == []
    assert state_of(watch) is PwHistoryState.READING

    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert starts(await buckets_of(db_session, watch)) == tiling(floor + STEP * 12, 12)
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert "1 span(s) could not be read" in progress_of(watch)[1]
    assert watch.collected_through == BOUNDARY - STEP and watch.history_end == BOUNDARY


async def test_row_12_the_density_gate_ends_the_reading_and_keeps_what_was_read(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    floor = BOUNDARY - 24 * STEP
    seed_reading(
        watch,
        target=floor,
        floor=floor,
        through=None,
        started=NOW - timedelta(minutes=10),
        chunk=12,
        ceiling=12,
        days=1,
    )
    await db_session.commit()
    dense = {floor + STEP * 3, floor + STEP * 9}
    provider = flat_provider()
    # two single buckets whose own search is cut: only their top rows are known
    provider.pw_force_truncated = lambda s: s.search is PwSearchKind.GROUPED and any(
        s.window_start <= at < s.window_end for at in dense
    )
    await tick(db_session, instance, provider, settings=hcfg(pw_bootstrap_searches_per_tick=20))
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert "lower bounds" in progress_of(watch)[1]
    assert {b.bucket_start for b in rows if b.values_measurement == "LOWER_BOUND"} == dense
    assert len(rows) == 24, "what was read stays"
    assert watch.collected_through == BOUNDARY - STEP and watch.history_end == BOUNDARY


# ------------------------------------------------------------------ row 7: the scope
async def test_row_7_a_stale_group_takes_no_step_and_leaves_through_where_it_was(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1, synced=NOW - timedelta(minutes=5))
    from tests.integration.factories import make_log_source

    await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    watch = await add_watch(
        db_session,
        instance,
        scope_kind=PwScopeKind.LOG_SOURCE_GROUP,
        scope_group_id=1,
        history_days=2,
    )
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    through = watch.history_through
    assert through is not None and state_of(watch) is PwHistoryState.READING
    searched = len(provider.pw_searches)

    await db_session.execute(
        text("UPDATE log_source_group SET synced_at = :t"), {"t": NOW - timedelta(hours=30)}
    )
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert len(provider.pw_searches) == searched, "no search for a scope that cannot be trusted"
    assert watch.history_through == through and state_of(watch) is PwHistoryState.READING
    assert watch.last_status == PwWatchStatus.SCOPE_STALE.value
    assert watch.collected_through is None

    await db_session.execute(
        text("UPDATE log_source_group SET synced_at = :t"), {"t": NOW - timedelta(minutes=5)}
    )
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.COMPLETE and watch.last_status is None


async def test_row_7_a_new_watch_on_a_stale_scope_still_begins_and_waits(db_session) -> None:
    instance = await make_instance(db_session)
    await _group(db_session, instance, 1, synced=NOW - timedelta(hours=30))
    from tests.integration.factories import make_log_source

    await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    watch = await add_watch(
        db_session,
        instance,
        scope_kind=PwScopeKind.LOG_SOURCE_GROUP,
        scope_group_id=1,
        history_days=1,
    )
    provider = flat_provider()
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert provider.pw_searches == []
    assert state_of(watch) is PwHistoryState.READING
    assert watch.history_target_start == T0_1D and watch.history_through is None
    assert watch.last_status == PwWatchStatus.SCOPE_STALE.value


async def test_row_7_an_empty_or_too_large_scope_takes_no_step(db_session) -> None:
    from tests.integration.factories import make_log_source

    instance = await make_instance(db_session)
    await _group(db_session, instance, 1)
    await _group(db_session, instance, 2)
    for qid in (11, 12, 13):
        await make_log_source(db_session, instance, qradar_id=qid, group_ids=[2])
    empty = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=1,
        history_days=1,
    )
    big = await add_watch(
        db_session, instance, scope_kind=PwScopeKind.LOG_SOURCE_GROUP, scope_group_id=2,
        history_days=1,
    )
    provider = flat_provider()
    await tick(
        db_session,
        instance,
        provider,
        settings=hcfg(pw_max_sources_per_search=1, pw_max_chunks_per_watch=2),
    )
    await reload(db_session, empty)
    await reload(db_session, big)
    assert provider.pw_searches == []
    assert empty.last_status == PwWatchStatus.SCOPE_EMPTY.value
    assert big.last_status == PwWatchStatus.SCOPE_TOO_LARGE.value
    assert state_of(empty) is state_of(big) is PwHistoryState.READING
    assert await db_session.scalar(select(func.count()).select_from(PwBucket)) == 0


# ------------------------------------------------------------------ row 9: limits and the switch
async def test_row_9_the_time_limit_hands_off_partial_and_live_starts_at_the_boundary(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    t0 = BOUNDARY - 2 * DAY
    through = t0 + STEP * 287
    seed_reading(
        watch,
        target=t0,
        floor=t0,
        through=through,
        started=NOW - timedelta(hours=73),
        days=2,
    )
    await db_session.commit()
    provider = flat_provider()
    await tick(db_session, instance, provider)
    await reload(db_session, watch)
    assert provider.pw_searches == [], "past the limit the next step is the hand-off"
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert progress_of(watch)[1] == "the reading did not finish in 72 h"
    assert watch.history_through == through
    assert watch.collected_through == BOUNDARY - STEP == watch.evaluated_through
    assert watch.history_end == BOUNDARY, "the unread span is an implicit gap"


async def test_row_9_switching_the_bootstrap_off_mid_reading_hands_off_at_once(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    provider = flat_provider()
    thin = hcfg(pw_bootstrap_searches_per_tick=2)
    await tick(db_session, instance, provider, settings=thin)
    await reload(db_session, watch)
    through = watch.history_through
    searched = len(provider.pw_searches)
    await tick(db_session, instance, provider, settings=hcfg(pw_bootstrap_enabled=False))
    await reload(db_session, watch)
    assert len(provider.pw_searches) == searched
    assert state_of(watch) is PwHistoryState.PARTIAL
    assert progress_of(watch)[1] == "history reading was switched off"
    assert watch.history_through == through and len(await buckets_of(db_session, watch)) == 288
    assert watch.collected_through == BOUNDARY - STEP and watch.history_end == BOUNDARY


# ------------------------------------------------------------------ disable and re-enable
async def test_a_disabled_reading_keeps_its_history_columns_and_resumes_after_re_enable(
    db_session, db_schema: str
) -> None:
    from app.models.instance import QRadarInstance
    from tests.integration.test_property_watches_api import PREFIX
    from tests.integration.test_pw_reenable import _route_watch, _watch_row

    client, created = await _route_watch(db_session, db_schema)
    watch = await _watch_row(db_session, created["id"])
    watch_id = watch.id
    instance = await db_session.get(QRadarInstance, watch.instance_id)
    assert instance is not None
    t0 = BOUNDARY - 2 * DAY
    through = t0 + STEP * 287
    watch.enabled = True
    seed_reading(
        watch, target=t0, floor=t0, through=through, started=NOW - timedelta(hours=1), days=2
    )
    await db_session.commit()
    before = {
        c: getattr(watch, c)
        for c in (
            "history_state",
            "history_target_start",
            "history_floor",
            "history_through",
            "history_started_at",
            "history_progress",
            "history_days",
        )
    }

    disabled = client.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": watch.updated_at.isoformat(), "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    provider = flat_provider()
    await db_session.refresh(instance)
    await tick(db_session, instance, provider)
    assert provider.pw_searches == [], "a disabled watch is not loaded, so it does not read"
    enabled = client.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": disabled.json()["updated_at"], "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    watch = await _watch_row(db_session, str(watch_id))
    # T-136's re-enable rule resets the watermarks and leaves every `history_*` column alone
    assert watch.collected_through is None and watch.evaluated_through is None
    assert {c: getattr(watch, c) for c in before} == before

    later = NOW + timedelta(hours=2)
    await db_session.refresh(instance)
    await tick(db_session, instance, provider, now=later)
    await reload(db_session, watch)
    boundary = BOUNDARY + timedelta(hours=2)
    assert state_of(watch) is PwHistoryState.COMPLETE
    # it resumed at `history_through` + one bucket (the earlier rows were never stored here)
    resumed = through + STEP
    assert starts(await buckets_of(db_session, watch)) == tiling(
        resumed, int((boundary - resumed) / STEP)
    )
    assert watch.collected_through == boundary - STEP and watch.history_end == boundary


# ------------------------------------------------------------------ nothing judges a reading
async def test_a_reading_watch_has_no_pending_bucket_and_no_live_bucket(db_session) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=2)
    provider = flat_provider()
    await tick(db_session, instance, provider, settings=hcfg(pw_bootstrap_searches_per_tick=2))
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.READING
    assert len(await buckets_of(db_session, watch)) == 288
    assert await PropertyWatchRepository(db_session).pending_buckets(instance.id, limit=10) == []
    assert await db_session.scalar(
        select(func.count()).select_from(PwBucket).where(PwBucket.origin == "LIVE")
    ) == 0


# ------------------------------------------------------------------ end to end
async def test_end_to_end_a_watch_reads_a_day_hands_off_and_only_live_buckets_are_judged(
    db_session,
) -> None:
    from app.models.custom_rule import CustomRuleBreach
    from app.models.enums import PwAnomalyType
    from app.models.property_watch import PwAnomaly, PwAnomalyTransition, PwValueBaseline

    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=1)
    watch.detectors = [d.value for d in PwAnomalyType]
    watch_id = watch.id
    await db_session.commit()
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = script_of(
        lambda at: {"alice": 100, "bob": 400, **({"mallory": 50} if at >= BOUNDARY else {})}
    )
    settings = hcfg()

    async def run(now: datetime):  # type: ignore[no-untyped-def]
        report = await PwInstancePipeline(
            db_session, provider, settings=settings, clock=lambda: now
        ).run(instance)
        await db_session.commit()
        return report

    first = await run(NOW)
    assert first.steps == ["collect", "evaluate", "train"]
    assert first.evaluation is not None and first.evaluation.buckets_evaluated == 0
    watch = await db_session.get(PropertyWatch, watch_id, populate_existing=True)
    assert watch is not None
    assert state_of(watch) is PwHistoryState.COMPLETE
    assert watch.evaluated_through == BOUNDARY - STEP == watch.collected_through
    # training reads the history buckets (evidence), and nothing was judged
    baselines = await db_session.scalar(select(func.count()).select_from(PwValueBaseline))
    assert baselines and baselines > 0

    second = await run(NOW + STEP)
    assert second.evaluation is not None and second.evaluation.buckets_evaluated == 1
    watch = await db_session.get(PropertyWatch, watch_id, populate_existing=True)
    assert watch is not None and watch.history_end == BOUNDARY
    assert watch.evaluated_through == BOUNDARY == watch.collected_through
    end = watch.history_end
    assert end is not None
    assert await db_session.scalar(
        select(func.count()).select_from(PwAnomaly).where(
            func.coalesce(PwAnomaly.anomaly_start, PwAnomaly.detected_at) < end
        )
    ) == 0
    assert await db_session.scalar(
        select(func.count()).select_from(PwAnomalyTransition).where(
            PwAnomalyTransition.bucket_start < end
        )
    ) == 0
    assert await db_session.scalar(
        select(func.count()).select_from(CustomRuleBreach).where(
            CustomRuleBreach.first_bucket_start < end
        )
    ) == 0


# ------------------------------------------------------------------ statements per tick (§H.5.6)
async def test_a_history_tick_costs_the_same_statements_at_every_size(
    db_session, capsys
) -> None:
    from tests.integration.test_rule_match_collection import recorded_statements

    counts: dict[tuple[int, int, int], int] = {}
    cases = ((1, 6, 12), (10, 6, 12), (1, 200, 12), (10, 200, 12), (1, 6, 2016), (10, 6, 2016))
    settings = hcfg(
        pw_max_searches_per_tick=240,
        pw_bootstrap_searches_per_tick=240,
        pw_max_active_values_per_watch=300,
        pw_max_values_per_bucket=300,
        pw_max_new_values_per_bucket=300,
    )
    for watches, values, buckets in cases:
        instance = await make_instance(db_session)
        rows = []
        for _ in range(watches):
            watch = await add_watch(db_session, instance, history_days=8)
            seed_reading(
                watch,
                target=BOUNDARY - buckets * STEP,
                floor=BOUNDARY - buckets * STEP,
                through=None,
                started=NOW - timedelta(minutes=10),
                days=8,
            )
            rows.append(watch)
        await db_session.commit()
        provider = MockQRadarProvider(seed=1)
        provider.pw_script = script_of(lambda at, n=values: {f"user{i:03d}": 5 for i in range(n)})
        with recorded_statements(db_session) as statements:
            report = await tick(db_session, instance, provider, settings=settings)
        counts[(watches, values, buckets)] = len(statements)
        # the work was really done at this size
        assert report.buckets_written == watches * buckets, (watches, values, buckets)
        assert report.values_written == watches * values
        for watch in rows:
            await reload(db_session, watch)
            assert state_of(watch) is PwHistoryState.COMPLETE
    with capsys.disabled():
        print("\nPW history tick statements (watches, values, buckets):", counts)
    assert len(set(counts.values())) == 1, counts
    assert next(iter(counts.values())) <= 40, counts


# ------------------------------------------------------------------ the differences from live
async def test_a_membership_change_during_the_reading_is_flagged_as_in_live(db_session) -> None:
    from tests.integration.factories import make_log_source

    instance = await make_instance(db_session)
    await _group(db_session, instance, 1)
    await make_log_source(db_session, instance, qradar_id=11, group_ids=[1])
    watch = await add_watch(
        db_session,
        instance,
        scope_kind=PwScopeKind.LOG_SOURCE_GROUP,
        scope_group_id=1,
        history_days=2,
    )
    provider = flat_provider()
    settings = hcfg(pw_bootstrap_searches_per_tick=2, pw_scope_change_grace_buckets=2)
    await tick(db_session, instance, provider, settings=settings)
    await make_log_source(db_session, instance, qradar_id=12, group_ids=[1])
    await tick(db_session, instance, provider, settings=settings)
    await reload(db_session, watch)
    rows = await buckets_of(db_session, watch)
    flags = [b.scope_changed for b in rows]
    # the first bucket under the new fingerprint and the two after it, none before
    assert flags == [False] * 288 + [True] * 3 + [False] * 285
    assert rows[287].scope_fingerprint != rows[288].scope_fingerprint
    assert {b.query_provenance["membership"] for b in rows} == {"current"}


async def test_history_buckets_carry_no_maintenance_state_while_live_ones_do(db_session) -> None:
    from tests.integration.factories import make_log_source

    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=31, maintenance_mode=True)
    watch = await add_watch(db_session, instance, source=source, history_days=1)
    provider = flat_provider()
    await tick(db_session, instance, provider)
    await tick(db_session, instance, provider, now=NOW + STEP)
    rows = await buckets_of(db_session, watch)
    assert {b.in_maintenance for b in rows if b.origin == "HISTORY"} == {False}
    assert [b.in_maintenance for b in rows if b.origin == "LIVE"] == [True]


async def test_a_scope_of_several_chunks_reads_and_counts_every_search(db_session) -> None:
    from tests.integration.factories import make_log_source

    instance = await make_instance(db_session)
    await _group(db_session, instance, 1)
    for qid in (11, 12, 13):
        await make_log_source(db_session, instance, qradar_id=qid, group_ids=[1])
    watch = await add_watch(
        db_session,
        instance,
        scope_kind=PwScopeKind.LOG_SOURCE_GROUP,
        scope_group_id=1,
        history_days=1,
    )
    provider = flat_provider()
    report = await tick(db_session, instance, provider, settings=hcfg(pw_max_sources_per_search=1))
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.COMPLETE
    assert len(await buckets_of(db_session, watch)) == 288
    # a scan and one chunk, each over three scope chunks: the record is what was sent
    assert len(provider.pw_searches) == 6 == report.history_searches
    assert progress_of(watch)[0].searches == 6
    assert {s.scope_ids for s in provider.pw_searches} == {(11,), (12,), (13,)}


async def test_values_retire_at_the_bucket_that_ends_their_window_not_at_the_end_of_the_chunk(
    db_session,
) -> None:
    """§H.7.3: `retire_stale` after every bucket, so the ceiling frees when live's would."""
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance, history_days=3)
    t0 = BOUNDARY - 3 * DAY

    def events(at: datetime) -> dict[str, int]:
        out = {"alice": 10}
        if at < t0 + timedelta(hours=2):
            out["early"] = 10
        if at >= t0 + timedelta(hours=40):
            out["late"] = 10
        return out

    provider = MockQRadarProvider(seed=1)
    provider.pw_script = script_of(events)
    # two active values at most, one day of tracking: `early` must free its place for `late`
    settings = hcfg(
        pw_value_active_days=1, pw_max_active_values_per_watch=2, pw_max_pinned_values=1
    )
    await tick(db_session, instance, provider, settings=settings)
    await reload(db_session, watch)
    assert state_of(watch) is PwHistoryState.COMPLETE and watch.history_floor == t0
    rows = (await db_session.scalars(select(PwValue).where(PwValue.watch_id == watch.id))).all()
    values = {v.value_key: v for v in rows}
    retired_at = t0 + timedelta(hours=26)  # the first bucket more than a day after its last
    assert values["early"].active is False
    assert values["early"].retired_at == retired_at == values["early"].tracked_until
    assert values["late"].active is True
    assert values["late"].first_seen_at == t0 + timedelta(hours=40)
    buckets = await buckets_of(db_session, watch)
    assert not [b for b in buckets if b.rejected_over_quota], "the ceiling freed in time"
