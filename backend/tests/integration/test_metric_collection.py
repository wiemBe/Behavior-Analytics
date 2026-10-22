"""Metric collection: idempotency, watermark, bounded backfill, advisory lock.
DB-gated."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from sqlalchemy import create_engine, delete, event, func, insert, select, text

from app.collectors.metric_collector import (
    COLLECTOR_NAME,
    MetricCollector,
    floor_to_interval,
)
from app.core.config import Settings
from app.models.enums import SearchFamily
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError
from app.providers.dto import LogSourceMetricSample
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.services.ariel_permit import ArielPermitUnavailable
from app.services.inventory_sync import InventorySyncService
from app.services.locks import collector_lock_key
from app.services.search_cadence import SearchCadenceService
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings(**kw) -> Settings:
    base = dict(encryption_key="x" * 44, collection_interval_seconds=300,
                collection_max_backfill_intervals=3)
    base.update(kw)
    return Settings(**base)


@contextmanager
def _recorded_statements(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _seed_inventory(session):
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(session, provider)
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)
    return instance


async def _set_phase_a_cadence(session, settings: Settings, seconds: int) -> None:
    await SearchCadenceService(session, settings).set_cadence(
        SearchFamily.PHASE_A_VOLUME,
        seconds,
        actor="metric-collection-test",
        source_ip=None,
    )


@pytest.mark.asyncio(loop_scope="function")
async def test_floor_to_interval_is_utc_and_deterministic() -> None:
    # Made async only to satisfy the module-level asyncio mark cleanly.
    dt = datetime(2026, 7, 15, 10, 7, 42, tzinfo=UTC)
    floored = floor_to_interval(dt, 300)
    assert floored == datetime(2026, 7, 15, 10, 5, 0, tzinfo=UTC)


async def test_first_run_collects_one_interval(db_session) -> None:
    instance = await _seed_inventory(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                settings=_settings(), clock=lambda: now)
    report = await collector.collect(instance)
    assert report.intervals_collected == 1
    assert report.samples_written > 0
    assert report.watermark_at is not None


async def test_ariel_permit_refusal_fails_phase_a_closed(db_session) -> None:
    """A refused permit surfaces as the typed provider error the task layer records by
    class: no bucket, no watermark move, nothing on the wire."""
    instance = await _seed_inventory(db_session)
    prior_watermark = datetime(2026, 10, 22, 9, 50, tzinfo=UTC)
    watermark = await _seed_watermark(db_session, instance, prior_watermark)
    await db_session.commit()
    requests = 0

    class RefusingBudget:
        wait_seconds = 0.0
        instance_limit = 3
        global_limit = 6

        @asynccontextmanager
        async def permit(self) -> AsyncIterator[None]:
            yield None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://qradar.example.internal/api",
    )
    provider = QRadarRestProvider(
        base_url="https://qradar.example.internal",
        sec_token="test-token",
        client=client,
        permit=RefusingBudget(),  # type: ignore[arg-type]
    )
    settings = _settings(history_backfill_enabled=False)
    try:
        with pytest.raises(ArielPermitUnavailable) as raised:
            await MetricCollector(
                db_session,
                provider,
                settings=settings,
                clock=lambda: datetime(2026, 10, 22, 10, 3, tzinfo=UTC),
            ).collect(instance)
    finally:
        await provider.aclose()

    assert isinstance(raised.value, ProviderError)
    assert type(raised.value).__name__ == "ArielPermitUnavailable"
    assert requests == 0
    assert await db_session.scalar(select(func.count()).select_from(LogSourceMetric)) == 0
    await db_session.refresh(watermark)
    assert watermark.watermark_at == prior_watermark


async def test_tick_writes_more_metrics_than_one_statement_can_bind(db_session) -> None:
    instance = await make_instance(db_session)
    # `first_event_at` is the one table column this live row shape does not bind.
    columns = len(LogSourceMetric.__table__.columns) - 1
    row_count = 32_767 // columns + 1
    await db_session.execute(
        insert(LogSource),
        [
            {
                "instance_id": instance.id,
                "qradar_id": index,
                "name": f"source-{index}",
            }
            for index in range(1, row_count + 1)
        ],
    )
    await db_session.flush()

    class ManyMetricsProvider(MockQRadarProvider):
        async def get_log_source_metrics(
            self, window_start: datetime, window_end: datetime
        ) -> list[LogSourceMetricSample]:
            return [
                LogSourceMetricSample(
                    qradar_id=index,
                    bucket_start=window_start,
                    bucket_seconds=int((window_end - window_start).total_seconds()),
                    event_count=index,
                )
                for index in range(1, row_count + 1)
            ]

    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    with _recorded_statements(db_session) as statements:
        report = await MetricCollector(
            db_session,
            ManyMetricsProvider(seed=1337),
            settings=_settings(),
            clock=lambda: now,
        ).collect(instance)

    assert row_count * columns > 32_767
    assert report.samples_written == row_count
    assert await db_session.scalar(select(func.count()).select_from(LogSourceMetric)) == row_count
    assert sum("INSERT INTO log_source_metric " in sql for sql in statements) == 1


async def test_collection_is_idempotent(db_session) -> None:
    instance = await _seed_inventory(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                settings=_settings(), clock=lambda: now)
    await collector.collect(instance)
    count1 = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
    # Re-running at the same clock collects nothing new (watermark unchanged).
    await collector.collect(instance)
    count2 = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
    assert count1 == count2


async def test_bounded_backfill(db_session) -> None:
    instance = await _seed_inventory(db_session)
    # First run establishes a watermark well in the past.
    t0 = datetime(2026, 7, 15, 8, 3, tzinfo=UTC)
    collector0 = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                 settings=_settings(), clock=lambda: t0)
    await collector0.collect(instance)

    # Now jump 2 hours ahead: 24 intervals missed, but backfill is capped at 3+1.
    t1 = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    settings = _settings(collection_max_backfill_intervals=3)
    await _set_phase_a_cadence(db_session, settings, 300)
    collector1 = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                 settings=settings,
                                 clock=lambda: t1)
    report = await collector1.collect(instance)
    assert report.intervals_collected <= 4  # never unbounded


async def test_default_hour_cadence_adds_backfill_beyond_its_twelve_buckets(
    db_session,
) -> None:
    instance = await _seed_inventory(db_session)
    await _set_phase_a_cadence(db_session, _settings(), 3600)  # pre-D-28 default
    settings = _settings(collection_max_backfill_intervals=3)
    first = datetime(2026, 7, 15, 8, 3, tzinfo=UTC)
    await MetricCollector(
        db_session,
        MockQRadarProvider(seed=1337),
        settings=settings,
        clock=lambda: first,
    ).collect(instance)

    report = await MetricCollector(
        db_session,
        MockQRadarProvider(seed=1337),
        settings=settings,
        clock=lambda: datetime(2026, 7, 15, 10, 3, tzinfo=UTC),
    ).collect(instance)

    assert report.intervals_collected == 15
    assert report.searches_sent == 1


class RecordingBucketProvider(MockQRadarProvider):
    def __init__(self, truncate) -> None:  # type: ignore[no-untyped-def]
        super().__init__(seed=1337)
        self.truncate = truncate
        self.windows: list[tuple[datetime, datetime]] = []

    async def get_log_source_metrics_bucketed(
        self, window_start, window_end, *, bucket_seconds, max_rows
    ):  # type: ignore[no-untyped-def]
        self.windows.append((window_start, window_end))
        batch = await super().get_log_source_metrics_bucketed(
            window_start,
            window_end,
            bucket_seconds=bucket_seconds,
            max_rows=max_rows,
        )
        if self.truncate(window_start, window_end, len(self.windows)):
            return batch.model_copy(update={"truncated": True})
        return batch


async def _seed_watermark(session, instance, at: datetime) -> CollectionWatermark:
    watermark = CollectionWatermark(
        instance_id=instance.id,
        collector=COLLECTOR_NAME,
        watermark_at=at,
    )
    session.add(watermark)
    await session.flush()
    return watermark


async def test_twelve_buckets_use_one_search(db_session) -> None:
    instance = await _seed_inventory(db_session)
    await _set_phase_a_cadence(db_session, _settings(), 3600)  # pre-D-28 default
    boundary = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    await _seed_watermark(db_session, instance, boundary - timedelta(minutes=65))
    report = await MetricCollector(
        db_session,
        MockQRadarProvider(seed=1337),
        settings=_settings(collection_max_backfill_intervals=0),
        clock=lambda: boundary,
    ).collect(instance)

    assert report.buckets_collected == 12
    assert report.searches_sent == 1
    assert report.splits == 0


async def test_truncation_splits_cover_window_without_overlap(db_session) -> None:
    instance = await _seed_inventory(db_session)
    await _set_phase_a_cadence(db_session, _settings(), 3600)  # pre-D-28 default
    boundary = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    start = boundary - timedelta(hours=1)
    await _seed_watermark(db_session, instance, start - timedelta(minutes=5))
    provider = RecordingBucketProvider(
        lambda _start, _end, call: call == 1
    )
    report = await MetricCollector(
        db_session,
        provider,
        settings=_settings(collection_max_backfill_intervals=0),
        clock=lambda: boundary,
    ).collect(instance)

    assert report.buckets_collected == 12
    assert report.searches_sent == 3
    assert report.splits == 1
    assert provider.windows[1:] == [
        (start, start + timedelta(minutes=30)),
        (start + timedelta(minutes=30), boundary),
    ]
    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    left = next(row for row in rows if row.bucket_start == start)
    right = next(row for row in rows if row.bucket_start == start + timedelta(minutes=30))
    assert left.query_provenance["search_window_start"] == start.isoformat()
    assert left.query_provenance["search_window_end"] == (
        start + timedelta(minutes=30)
    ).isoformat()
    assert left.query_provenance["search_bucket_count"] == 6
    assert left.query_provenance["bucket_key"] == "starttime / 300000"
    assert left.query_provenance["split_depth"] == 1
    assert right.query_provenance["search_window_start"] == (
        start + timedelta(minutes=30)
    ).isoformat()


async def test_single_bucket_truncation_writes_nothing_and_holds_watermark(
    db_session,
) -> None:
    instance = await _seed_inventory(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    provider = RecordingBucketProvider(lambda *_args: True)

    async def truncated_bucket(_start, _end):  # type: ignore[no-untyped-def]
        raise ProviderError("QRadar Ariel metric search was truncated at 5000 rows")

    provider.get_log_source_metrics = truncated_bucket  # type: ignore[method-assign]
    with pytest.raises(ProviderError, match="truncated at 5000 rows"):
        await MetricCollector(
            db_session, provider, settings=_settings(), clock=lambda: now
        ).collect(instance)

    assert await db_session.scalar(select(func.count()).select_from(LogSourceMetric)) == 0
    watermark = await db_session.scalar(select(CollectionWatermark))
    assert watermark is not None and watermark.watermark_at is None


async def test_failed_single_bucket_keeps_prefix_and_holds_watermark_before_it(
    db_session,
) -> None:
    instance = await _seed_inventory(db_session)
    await _set_phase_a_cadence(db_session, _settings(), 3600)  # pre-D-28 default
    boundary = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    start = boundary - timedelta(minutes=20)
    await _seed_watermark(db_session, instance, start - timedelta(minutes=5))

    class ThirdBucketTruncated(RecordingBucketProvider):
        in_batch = False

        async def get_log_source_metrics_bucketed(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            self.in_batch = True
            try:
                return await super().get_log_source_metrics_bucketed(*args, **kwargs)
            finally:
                self.in_batch = False

        async def get_log_source_metrics(self, window_start, window_end):  # type: ignore[no-untyped-def]
            if not self.in_batch and window_start == start + timedelta(minutes=10):
                raise ProviderError("QRadar Ariel metric search was truncated at 5000 rows")
            return await super().get_log_source_metrics(window_start, window_end)

    provider = ThirdBucketTruncated(lambda *_args: True)
    report = await MetricCollector(
        db_session,
        provider,
        settings=_settings(collection_max_backfill_intervals=0),
        clock=lambda: boundary,
    ).collect(instance)

    assert report.buckets_collected == 2
    assert report.partial is True
    starts = {
        row.bucket_start for row in (await db_session.scalars(select(LogSourceMetric))).all()
    }
    assert starts == {start, start + timedelta(minutes=5)}
    watermark = await db_session.scalar(select(CollectionWatermark))
    assert watermark is not None
    assert watermark.watermark_at == start + timedelta(minutes=5)


async def test_search_cap_keeps_only_contiguous_complete_prefix(db_session) -> None:
    instance = await _seed_inventory(db_session)
    await _set_phase_a_cadence(db_session, _settings(), 3600)  # pre-D-28 default
    boundary = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
    start = boundary - timedelta(hours=1)
    await _seed_watermark(db_session, instance, start - timedelta(minutes=5))
    provider = RecordingBucketProvider(
        lambda _start, _end, call: call in {1, 3}
    )
    report = await MetricCollector(
        db_session,
        provider,
        settings=_settings(
            collection_max_backfill_intervals=0,
            search_cadence_max_searches_per_tick=3,
        ),
        clock=lambda: boundary,
    ).collect(instance)

    assert report.buckets_collected == 6
    assert report.searches_sent == 3
    assert report.partial is True
    watermark = await db_session.scalar(select(CollectionWatermark))
    assert watermark is not None
    assert watermark.watermark_at == start + timedelta(minutes=25)


async def test_eight_hour_cadence_collects_ninety_six_buckets_in_one_search(
    db_session,
) -> None:
    instance = await _seed_inventory(db_session)
    boundary = datetime(2026, 7, 15, 16, 0, tzinfo=UTC)
    await _seed_watermark(db_session, instance, boundary - timedelta(hours=8, minutes=5))
    settings = _settings(collection_max_backfill_intervals=0)
    await _set_phase_a_cadence(db_session, settings, 28_800)
    report = await MetricCollector(
        db_session,
        MockQRadarProvider(seed=1337),
        settings=settings,
        clock=lambda: boundary,
    ).collect(instance)

    assert report.buckets_collected == 96
    assert report.searches_sent == 1


async def test_bucketed_collection_matches_private_per_bucket_control_for_24_buckets(
    db_session,
) -> None:
    instance = await _seed_inventory(db_session)
    await _set_phase_a_cadence(db_session, _settings(), 3600)  # pre-D-28 default
    provider = MockQRadarProvider(seed=1337)
    original = provider.get_log_source_metrics

    async def without_one_silent_source(window_start, window_end):  # type: ignore[no-untyped-def]
        samples = await original(window_start, window_end)
        return [sample for sample in samples if sample.qradar_id != 1001]

    provider.get_log_source_metrics = without_one_silent_source  # type: ignore[method-assign]
    settings = _settings(collection_max_backfill_intervals=12)
    start = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    end = start + timedelta(hours=2)
    control = MetricCollector(
        db_session, provider, settings=settings, clock=lambda: end
    )
    last_done = start - timedelta(minutes=5)
    for index in range(24):
        bucket_start = start + timedelta(minutes=5 * index)
        await control._collect_interval(
            instance,
            bucket_start,
            bucket_start + timedelta(minutes=5),
            watermark_at=last_done,
        )
        last_done = bucket_start

    excluded = {
        "query_provenance",
        "collected_at",
        "collection_duration_ms",
        "watermark_at",
    }

    async def snapshot() -> dict:  # type: ignore[no-untyped-def]
        sources = {
            source.id: source.qradar_id
            for source in (await db_session.scalars(select(LogSource))).all()
        }
        rows = (await db_session.scalars(select(LogSourceMetric))).all()
        return {
            (sources[row.log_source_id], row.bucket_start): {
                column.name: getattr(row, column.name)
                for column in LogSourceMetric.__table__.columns
                if column.name not in excluded
            }
            for row in rows
        }

    expected = await snapshot()
    assert len({key[0] for key in expected}) >= 3
    assert all(expected[(1001, start + timedelta(minutes=5 * index))]["event_count"] == 0
               for index in range(24))

    await db_session.execute(delete(LogSourceMetric))
    await db_session.execute(delete(CollectionWatermark))
    await db_session.flush()
    await _seed_watermark(db_session, instance, start - timedelta(minutes=5))
    report = await MetricCollector(
        db_session, provider, settings=settings, clock=lambda: end
    ).collect(instance)

    assert report.buckets_collected == 24
    assert report.searches_sent == 1
    assert await snapshot() == expected


async def test_advisory_lock_prevents_overlap(db_session) -> None:
    instance = await _seed_inventory(db_session)
    settings = _settings()
    # Derive the key the same way the collector does; it is per
    # (instance, collector) so collectors do not contend with each other.
    key = collector_lock_key(instance.id, COLLECTOR_NAME)

    url = os.environ["TEST_DATABASE_URL"].replace("+asyncpg", "+psycopg")
    holder = create_engine(url, future=True)
    conn = holder.connect()
    conn.execute(
        text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
            ns=settings.collection_advisory_lock_namespace, key=key
        )
    )
    try:
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
        collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                    settings=settings, clock=lambda: now)
        report = await collector.collect(instance)
        assert report.skipped_locked is True  # another holder -> skip, don't block
    finally:
        conn.execute(
            text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=settings.collection_advisory_lock_namespace, key=key
            )
        )
        conn.close()
        holder.dispose()


async def test_provenance_records_what_the_count_counts_and_what_was_measured(
    db_session,
) -> None:
    """Every bucket states its event aggregation and its measured telemetry.

    Without these a reader cannot tell events from Ariel records, or a measured
    zero from a DTO default -- and the anomaly engine gates detectors on the
    second. Zero-filled rows must carry the same declaration as reported ones.
    """
    instance = await _seed_inventory(db_session)
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    collector = MetricCollector(db_session, MockQRadarProvider(seed=1337),
                                settings=_settings(), clock=lambda: now)
    await collector.collect(instance)

    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    assert rows
    expected = sorted(t.value for t in MockQRadarProvider.log_source_telemetry)
    for row in rows:
        assert row.query_provenance["event_count_aggregation"] == "synthetic"
        assert row.query_provenance["telemetry"] == expected


class TestZeroFill:
    """A silent source must leave an explicit zero bucket, not nothing at all.

    QRadar omits a source with no events from the `GROUP BY logsourceid`
    aggregate. Without an explicit zero row the interval leaves no trace, and
    the anomaly engine -- which evaluates the newest row it can find -- would
    re-judge the last busy bucket forever and never observe the silence. That
    made live NO_EVENTS unreachable: `detect_no_events` requires a COMPLETE
    bucket whose count is zero.
    """

    @staticmethod
    async def _collect_with(session, instance, samples, now):
        provider = MockQRadarProvider(seed=1337)

        async def _metrics(window_start, window_end):
            return samples

        provider.get_log_source_metrics = _metrics  # type: ignore[method-assign]
        collector = MetricCollector(
            session, provider, settings=_settings(), clock=lambda: now
        )
        return await collector.collect(instance)

    async def test_monitored_source_absent_from_ariel_gets_a_zero_bucket(
        self, db_session
    ) -> None:
        instance = await _seed_inventory(db_session)
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)

        # QRadar reports nothing at all for this window: every monitored source
        # was silent.
        await self._collect_with(db_session, instance, [], now)

        rows = list(
            (await db_session.scalars(select(LogSourceMetric))).all()
        )
        assert rows, "a silent interval must still leave evidence it was observed"
        for row in rows:
            assert row.event_count == 0
            assert row.average_eps == 0.0
            # COMPLETE because the query succeeded and returned no events -- an
            # observation of silence, not an unobserved window.
            assert row.is_complete
            assert row.query_provenance.get("zero_filled") is True

    async def test_zero_rows_are_upserted_not_duplicated(self, db_session) -> None:
        instance = await _seed_inventory(db_session)
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)

        await self._collect_with(db_session, instance, [], now)
        first = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
        # Re-collecting the same interval overwrites rather than duplicating.
        await self._collect_with(db_session, instance, [], now)
        second = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
        assert first == second

    async def test_unmonitored_sources_are_not_zero_filled(self, db_session) -> None:
        instance = await _seed_inventory(db_session)
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)

        from app.models.log_source import LogSource

        sources = list((await db_session.scalars(select(LogSource))).all())
        for src in sources:
            src.monitoring_enabled = False
        await db_session.flush()

        await self._collect_with(db_session, instance, [], now)

        count = await db_session.scalar(select(func.count()).select_from(LogSourceMetric))
        assert count == 0, "an unmonitored source is never evaluated, so it needs no row"


class TestTruncatedAggregate:
    """A capped Ariel aggregate must not become bucket rows (T-033).

    The sources beyond the cap would be zero-filled as complete silence, which
    reads as a false NO_EVENTS. The provider raises instead, and the collector's
    contract for a raising interval is: no rows, watermark stays put.
    """

    async def test_truncated_result_writes_no_rows_and_holds_the_watermark(
        self, db_session
    ) -> None:
        import random

        import httpx

        from app.models.monitoring import CollectionWatermark
        from app.providers.base import ProviderError
        from app.providers.qradar_rest import QRadarRestProvider

        instance = await _seed_inventory(db_session)

        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if request.method == "POST" and path.endswith("/ariel/searches"):
                return httpx.Response(201, json={"search_id": "m-1", "status": "WAIT"})
            if path.endswith("/ariel/searches/m-1/results"):
                first, last = (
                    int(n) for n in request.headers["Range"].removeprefix("items=").split("-")
                )
                rows = [
                    {"qradar_id": str(i), "event_count": "9", "last_event_time": 1_784_000_000_000}
                    for i in range(1, 40)
                ]
                return httpx.Response(200, json={"events": rows[first : last + 1]})
            if path.endswith("/ariel/searches/m-1"):
                return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
            raise AssertionError(f"unexpected request: {request.method} {request.url}")

        async def _nosleep(_s: float) -> None:
            return None

        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="https://qradar.example.internal/api",
            headers={"SEC": "t", "Version": "20.0", "Accept": "application/json"},
        )
        provider = QRadarRestProvider(
            base_url="https://qradar.example.internal", sec_token="t", client=client,
            page_size=10, max_pages=2, rng=random.Random(1), sleep=_nosleep,
        )
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
        collector = MetricCollector(
            db_session, provider, settings=_settings(), clock=lambda: now
        )

        with pytest.raises(ProviderError, match="truncated"):
            await collector.collect(instance)

        assert await db_session.scalar(select(func.count()).select_from(LogSourceMetric)) == 0
        watermark = await db_session.scalar(select(CollectionWatermark))
        assert watermark is None or watermark.watermark_at is None
