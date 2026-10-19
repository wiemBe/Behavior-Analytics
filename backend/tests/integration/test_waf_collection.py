"""WAF collection and lifecycle, end to end against a real PostgreSQL.

The completeness assertions are the important ones. A collector that silently
turns "we could not parse this" into a measured zero produces a database full of
confident, wrong numbers and no error anywhere, so each of those paths is
asserted directly rather than inferred from the happy path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors import waf_endpoint_collector
from app.collectors.waf_endpoint_collector import (
    WafEndpointCollector,
    fold_groups,
    watermark_collector,
)
from app.core.config import Settings, get_settings
from app.models.enums import AnomalyState, BucketCompleteness, WafAnomalyType, WafMeasurement
from app.models.waf import (
    WafAnomaly,
    WafAnomalyTransition,
    WafDetectorState,
    WafEndpoint,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.providers.dto import WafObservationBatch, WafObservationBucketBatch, WafRequestGroup
from app.rules.matches import window_starts
from app.waf.baseline import WafBaselineBuilder
from app.waf.engine import WafAnomalyEngine
from app.waf.observations import BucketFacts, observe
from app.waf.profile import WafFieldMapping
from app.waf.semantics import request_count_semantics
from tests.integration.factories import make_instance, make_log_source

pytestmark = pytest.mark.integration

BUCKET = datetime(2026, 8, 3, 19, 25, tzinfo=UTC)
#: The lab site's property names, as a profile would supply them.
LAB_MAPPING = WafFieldMapping(url="URL", method="HTTP_Method")
LAB_SEMANTICS = request_count_semantics(
    aggregation="SUM(eventcount)", normalizer_version=1, profile_version=1
)


def settings(**overrides) -> Settings:
    base = get_settings().model_dump()
    base.update(
        {
            "waf_collection_enabled": True,
            "collection_interval_seconds": 300,
            "waf_min_endpoint_coverage": 0.9,
            "waf_endpoint_active_days": 7,
            "baseline_min_samples": 4,
            "anomaly_open_after_intervals": 1,
            "anomaly_resolve_after_intervals": 2,
            "waf_min_bucket_requests": 20.0,
            "waf_min_absolute_delta_requests": 50.0,
            "waf_spike_ratio": 2.0,
        }
    )
    base.update(overrides)
    return Settings(**base)


class StubProvider:
    """Serves batches by window, like the real provider does.

    Keyed on window_start rather than call order because the collector backfills:
    a run can legitimately ask for several intervals, and a window with no
    configured batch must return an empty *successful* result -- which is a
    measured zero -- rather than falling off the end of a list.
    """

    #: Stands in for the REST provider, so it declares the REST aggregation.
    event_count_aggregation = "SUM(eventcount)"

    def __init__(self, batches: list[WafObservationBatch | Exception]) -> None:
        self.error = next((b for b in batches if isinstance(b, Exception)), None)
        self.by_window = {
            b.window_start: b for b in batches if isinstance(b, WafObservationBatch)
        }
        self.calls = 0

    async def get_waf_observations(self, **kwargs) -> WafObservationBatch:
        self.calls += 1
        if self.error is not None:
            raise self.error
        start = kwargs["window_start"]
        found = self.by_window.get(start)
        if found is not None:
            return found
        return WafObservationBatch(
            qradar_log_source_id=kwargs["qradar_log_source_id"],
            window_start=start,
            window_end=kwargs["window_end"],
            groups=[],
            total_events=0,
            truncated=False,
            query="MOCK EMPTY",
        )

    async def get_waf_observations_bucketed(self, **kwargs) -> WafObservationBucketBatch:
        """The keyed answer, assembled from this stub's per-bucket answers."""
        size = kwargs["bucket_seconds"]
        buckets = {}
        for start in window_starts(kwargs["window_start"], kwargs["window_end"], size):
            buckets[start] = await self.get_waf_observations(
                **{**kwargs, "window_start": start,
                   "window_end": start + timedelta(seconds=size)}
            )
        return WafObservationBucketBatch(buckets=buckets, query="STUB KEYED")


def batch(
    groups: list[tuple[str, str, int]],
    *,
    total: int | None = None,
    truncated: bool = False,
    window_start: datetime = BUCKET,
) -> WafObservationBatch:
    parsed = sum(c for _, _, c in groups)
    return WafObservationBatch(
        qradar_log_source_id=312,
        window_start=window_start,
        window_end=window_start + timedelta(seconds=300),
        groups=[
            WafRequestGroup(
                raw_url=u, raw_method=m, request_count=c, record_count=c,
                distinct_client_count=3,
            )
            for u, m, c in groups
        ],
        total_events=total if total is not None else parsed,
        truncated=truncated,
        query="MOCK",
    )


class TestFolding:
    def test_many_raw_paths_fold_into_one_normalized_endpoint(self) -> None:
        """Normalization is many-to-one, so groups must be summed, not picked."""
        obs, unusable = fold_groups(
            batch([("/users/1", "GET", 10), ("/users/2", "GET", 7),
                   ("/users/3", "GET", 3)])
        )
        assert unusable == 0
        assert len(obs) == 1
        assert obs[0].endpoint == "/users/{id}"
        assert obs[0].request_count == 20

    def test_query_strings_fold_into_the_bare_endpoint(self) -> None:
        obs, _ = fold_groups(
            batch([("/api/v1/report?id=1", "GET", 5),
                   ("/api/v1/report?id=2", "GET", 5)])
        )
        assert len(obs) == 1
        assert obs[0].endpoint == "/api/v1/report"
        assert obs[0].request_count == 10

    def test_methods_stay_separate(self) -> None:
        obs, _ = fold_groups(
            batch([("/portal/login", "GET", 5), ("/portal/login", "POST", 9)])
        )
        assert {(o.endpoint, o.method, o.request_count) for o in obs} == {
            ("/portal/login", "GET", 5),
            ("/portal/login", "POST", 9),
        }

    def test_unusable_groups_are_counted_not_silently_dropped(self) -> None:
        """An unparseable method must not default into a bogus permanent entity."""
        obs, unusable = fold_groups(
            batch([("/ok", "GET", 5), ("/bad", "BREW", 9), ("", "GET", 4)])
        )
        assert unusable == 2
        assert [o.endpoint for o in obs] == ["/ok"]

    def test_distinct_clients_use_the_max_not_the_sum(self) -> None:
        """Summing would double-count a client that hit both raw paths."""
        obs, _ = fold_groups(
            batch([("/users/1", "GET", 5), ("/users/2", "GET", 5)])
        )
        assert obs[0].distinct_clients == 3


async def _collect(
    db_session: AsyncSession,
    provider: StubProvider,
    *,
    cfg: Settings | None = None,
    now: datetime = BUCKET + timedelta(seconds=300),
):
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=cfg or settings(), clock=lambda: now  # type: ignore[arg-type]
    )
    report = await collector.collect(instance, log_source=source)
    return instance, source, report


class TestCollectionCompleteness:
    async def test_full_coverage_yields_complete_buckets(
        self, db_session: AsyncSession
    ) -> None:
        provider = StubProvider([batch([("/portal/login", "GET", 40)])])
        _, _, report = await _collect(db_session, provider)
        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert len(rows) == 1
        assert rows[0].completeness == BucketCompleteness.COMPLETE
        assert rows[0].request_count == 40
        assert report.endpoints_created == 1
        bucket = (await db_session.scalars(select(WafSourceBucket))).one()
        assert bucket.origin == "LIVE"

    async def test_low_endpoint_coverage_marks_the_bucket_partial(
        self, db_session: AsyncSession
    ) -> None:
        """Two thirds parsed is not a whole observation of any endpoint in it."""
        provider = StubProvider([batch([("/portal/login", "GET", 40)], total=120)])
        _, _, report = await _collect(db_session, provider)
        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert rows[0].completeness == BucketCompleteness.PARTIAL
        assert rows[0].endpoint_coverage_ratio == pytest.approx(1 / 3)
        assert report.partial_buckets

    async def test_a_truncated_result_marks_the_bucket_partial(
        self, db_session: AsyncSession
    ) -> None:
        """Hitting the group cap means the stored rows are a prefix of the truth."""
        provider = StubProvider(
            [batch([("/portal/login", "GET", 40)], truncated=True)]
        )
        await _collect(db_session, provider)
        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert rows[0].completeness == BucketCompleteness.PARTIAL

    async def test_an_empty_successful_window_is_a_complete_measured_zero(
        self, db_session: AsyncSession
    ) -> None:
        """Coverage of an empty window is 1.0: we observed all of nothing."""
        provider = StubProvider([batch([], total=0)])
        await _collect(db_session, provider)
        # No endpoints exist yet, so there is nothing to zero-fill -- absence is
        # not an observation until an endpoint has been seen at least once.
        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert rows == []

    async def test_a_provider_failure_writes_no_rows_and_holds_the_watermark(
        self, db_session: AsyncSession
    ) -> None:
        """A failed query must never degrade into a zero-volume observation."""
        provider = StubProvider([RuntimeError("ariel exploded")])
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=settings(),  # type: ignore[arg-type]
            clock=lambda: BUCKET + timedelta(seconds=300),
        )
        with pytest.raises(RuntimeError):
            await collector.collect(instance, log_source=source)
        await db_session.rollback()
        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert rows == []

    async def test_unavailable_counts_are_null_not_zero(
        self, db_session: AsyncSession
    ) -> None:
        provider = StubProvider([batch([("/portal/login", "GET", 40)])])
        await _collect(db_session, provider)
        row = (await db_session.scalars(select(WafEndpointMetric))).one()
        assert row.blocked_count is None
        assert row.error_count is None


class TestZeroMaterializationIsBounded:
    async def test_a_previously_seen_endpoint_gets_a_measured_zero(
        self, db_session: AsyncSession
    ) -> None:
        """Absence from an exact bucket is a measured zero -- now without a row.

        Until B.1.5 the zero was materialized as a row. Storage is sparse now, so
        the zero is asserted where it is read: the bucket record says the counts
        are exact and rows are sparse, the endpoint was tracked, and the shared
        reconstruction yields an exact, inferred 0.
        """
        provider = StubProvider(
            [
                batch([("/portal/login", "GET", 40)]),
                batch([("/other", "GET", 10)], window_start=BUCKET + timedelta(seconds=300)),
            ]
        )
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        cfg = settings()
        for tick in (0, 300):
            collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=cfg,  # type: ignore[arg-type]
                clock=lambda t=tick: BUCKET + timedelta(seconds=300 + t),
            )
            await collector.collect(instance, log_source=source)

        endpoint = (
            await db_session.scalars(
                select(WafEndpoint).where(
                    WafEndpoint.normalized_endpoint == "/portal/login"
                )
            )
        ).one()
        rows = (
            await db_session.scalars(
                select(WafEndpointMetric).where(
                    WafEndpointMetric.waf_endpoint_id == endpoint.id
                )
            )
        ).all()
        counts = {r.bucket_start: r.request_count for r in rows}
        assert counts == {BUCKET: 40}
        # The measured zero for the bucket it did not appear in.
        second = BUCKET + timedelta(seconds=300)
        bucket = await db_session.get(WafSourceBucket, (source.id, second))
        assert bucket is not None and bucket.sparse_endpoint_rows
        assert bucket.request_count_measurement == WafMeasurement.EXACT
        assert endpoint.tracked_at(second)
        zero = observe(None, BucketFacts.of(bucket), tracked=True, normalizer_version=1)
        assert zero is not None and zero.inferred
        assert (zero.request_count, zero.measurement) == (0, WafMeasurement.EXACT)

    async def test_an_unseen_endpoint_is_never_zero_filled(
        self, db_session: AsyncSession
    ) -> None:
        """Absence before first sighting is not an observation.

        This is the difference from Phase A: log sources are enumerable in
        advance, endpoints are not, so filling every possible endpoint would be
        unbounded.
        """
        provider = StubProvider([batch([("/portal/login", "GET", 40)])])
        await _collect(db_session, provider)
        endpoints = (await db_session.scalars(select(WafEndpoint))).all()
        assert len(endpoints) == 1

    async def test_a_stale_endpoint_is_retired_and_stops_being_filled(
        self, db_session: AsyncSession
    ) -> None:
        provider = StubProvider([batch([("/portal/login", "GET", 40)])])
        instance, source, _ = await _collect(db_session, provider)
        endpoint = (await db_session.scalars(select(WafEndpoint))).one()

        # Collect again, far in the future, with different traffic.
        later = BUCKET + timedelta(days=30)
        provider2 = StubProvider([batch([("/new", "GET", 10)], window_start=later)])
        collector = WafEndpointCollector(
        db_session, provider2, mapping=LAB_MAPPING, profile_version=1,
        settings=settings(),  # type: ignore[arg-type]
            clock=lambda: later + timedelta(seconds=300),
        )
        report = await collector.collect(instance, log_source=source)

        await db_session.refresh(endpoint)
        assert endpoint.is_active is False
        assert endpoint.retired_at is not None
        assert report.retired == 1


class TestIdempotency:
    async def test_recollecting_the_same_bucket_overwrites(
        self, db_session: AsyncSession
    ) -> None:
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        cfg = settings()
        for count in (40, 99):
            provider = StubProvider([batch([("/portal/login", "GET", count)])])
            collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=cfg,  # type: ignore[arg-type]
                clock=lambda: BUCKET + timedelta(seconds=300),
            )
            # Reset the watermark so the same interval is collected twice.
            await collector.collect(instance, log_source=source)
            from app.models.monitoring import CollectionWatermark

            wm = await db_session.scalar(
                select(CollectionWatermark).where(
                    CollectionWatermark.collector == watermark_collector(source)
                )
            )
            assert wm is not None
            wm.watermark_at = None
            await db_session.flush()

        rows = (await db_session.scalars(select(WafEndpointMetric))).all()
        assert len(rows) == 1
        assert rows[0].request_count == 99

    async def test_a_live_upsert_keeps_the_origin_of_an_existing_bucket(
        self, db_session: AsyncSession
    ) -> None:
        from sqlalchemy import update

        from app.models.monitoring import CollectionWatermark

        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        cfg = settings()
        for count in (40, 99):
            provider = StubProvider([batch([("/portal/login", "GET", count)])])
            collector = WafEndpointCollector(
                db_session, provider, mapping=LAB_MAPPING, profile_version=1,
                settings=cfg,  # type: ignore[arg-type]
                clock=lambda: BUCKET + timedelta(seconds=300),
            )
            await collector.collect(instance, log_source=source)
            if count == 40:
                assert (await db_session.scalars(select(WafSourceBucket))).one().origin == "LIVE"
                # The row is later marked as history; a live re-collection must not undo that.
                await db_session.execute(update(WafSourceBucket).values(origin="HISTORY"))
                wm = await db_session.scalar(
                    select(CollectionWatermark).where(
                        CollectionWatermark.collector == watermark_collector(source)
                    )
                )
                assert wm is not None
                wm.watermark_at = None
                await db_session.flush()

        row = (
            await db_session.scalars(
                select(WafSourceBucket).execution_options(populate_existing=True)
            )
        ).one()
        assert row.origin == "HISTORY"
        assert row.total_events == 99  # the rest of the row was overwritten as before

    async def test_a_hostless_endpoint_does_not_multiply_across_buckets(
        self, db_session: AsyncSession
    ) -> None:
        """The NULLS NOT DISTINCT contract, exercised through the collector.

        If the lookup used `== None` instead of `IS NULL`, every bucket would
        create a fresh entity holding one observation and no baseline would ever
        reach its sample minimum.
        """
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        cfg = settings()
        for i in range(5):
            start = BUCKET + timedelta(seconds=300 * i)
            provider = StubProvider(
                [batch([("/portal/login", "GET", 40)], window_start=start)]
            )
            collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=cfg,  # type: ignore[arg-type]
                clock=lambda s=start: s + timedelta(seconds=300),
            )
            await collector.collect(instance, log_source=source)

        endpoints = (await db_session.scalars(select(WafEndpoint))).all()
        assert len(endpoints) == 1
        assert endpoints[0].virtual_host is None

    async def test_each_source_keeps_its_own_watermark(
        self, db_session: AsyncSession
    ) -> None:
        """Two WAF sources collected in the same cycle must both be collected.

        With one watermark per instance, the first source's run advanced it past
        the bucket and the second source found nothing left to collect -- every
        cycle, without an error.
        """
        instance = await make_instance(db_session)
        first = await make_log_source(db_session, instance, qradar_id=312, name="waf-a")
        second = await make_log_source(db_session, instance, qradar_id=313, name="waf-b")
        cfg = settings()

        for source, endpoint in ((first, "/portal/login"), (second, "/api/v1/status")):
            provider = StubProvider([batch([(endpoint, "GET", 40)])])
            collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=cfg,  # type: ignore[arg-type]
                clock=lambda: BUCKET + timedelta(seconds=300),
            )
            report = await collector.collect(instance, log_source=source)
            assert report.intervals_collected == 1, source.name

        collected = {
            (e.log_source_id, e.normalized_endpoint)
            for e in (await db_session.scalars(select(WafEndpoint))).all()
        }
        assert collected == {(first.id, "/portal/login"), (second.id, "/api/v1/status")}

    async def test_a_normalizer_version_bump_starts_fresh_entities(
        self, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A version bump must not collide with the endpoints of the old version.

        The collector looks endpoints up per version. While the natural key
        omitted the version, the first bump raised an IntegrityError for every
        unchanged endpoint and stopped collection outright.
        """
        later = BUCKET + timedelta(seconds=300)
        provider = StubProvider(
            [
                batch([("/portal/login", "GET", 40)]),
                batch([("/portal/login", "GET", 45)], window_start=later),
            ]
        )
        instance, source, _ = await _collect(db_session, provider)

        monkeypatch.setattr(waf_endpoint_collector, "ENDPOINT_NORMALIZER_VERSION", 2)
        collector = WafEndpointCollector(
        db_session, provider, mapping=LAB_MAPPING, profile_version=1,
        settings=settings(),  # type: ignore[arg-type]
            clock=lambda: later + timedelta(seconds=300),
        )
        await collector.collect(instance, log_source=source)

        endpoints = {
            e.normalizer_version: e
            for e in (await db_session.scalars(select(WafEndpoint))).all()
        }
        assert set(endpoints) == {1, 2}
        later_rows = (
            await db_session.scalars(
                select(WafEndpointMetric).where(WafEndpointMetric.bucket_start == later)
            )
        ).all()
        # The new version's entity holds the observation; the old version's is
        # not zero-filled, because its observations are not comparable any more.
        assert [(r.waf_endpoint_id, r.request_count) for r in later_rows] == [
            (endpoints[2].id, 45)
        ]


class TestLifecycle:
    async def _seed(
        self, db_session: AsyncSession, counts: list[int], *, cfg: Settings
    ) -> tuple[WafEndpoint, list[WafEndpointMetric]]:
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance)
        now = datetime(2026, 8, 3, 19, 0, tzinfo=UTC)
        endpoint = WafEndpoint(
            instance_id=instance.id,
            log_source_id=source.id,
            virtual_host=None,
            normalized_endpoint="/portal/login",
            http_method="GET",
            normalizer_version=1,
            first_seen_at=now - timedelta(days=7),
            last_seen_at=now,
        )
        db_session.add(endpoint)
        await db_session.flush()

        metrics = []
        for i, count in enumerate(counts):
            m = WafEndpointMetric(
                waf_endpoint_id=endpoint.id,
                bucket_start=now + timedelta(seconds=300 * i),
                bucket_seconds=300,
                request_count=count,
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                normalizer_version=1,
                metric_semantics=LAB_SEMANTICS,
            )
            db_session.add(m)
            metrics.append(m)
        await db_session.flush()
        return endpoint, metrics

    async def test_open_recover_resolve(self, db_session: AsyncSession) -> None:
        cfg = settings()
        # Eight steady buckets build the baseline, then a spike, then recovery.
        endpoint, metrics = await self._seed(
            db_session, [100] * 8 + [900, 900, 100, 100], cfg=cfg
        )
        builder = WafBaselineBuilder(db_session, settings=cfg)
        await builder.rebuild_for_endpoint(
            endpoint, semantics=LAB_SEMANTICS, now=metrics[8].bucket_start
        )

        engine = WafAnomalyEngine(db_session, settings=cfg)
        states = []
        for m in metrics[8:]:
            report = await engine.evaluate_interval(endpoint, m)
            states.append(report.states.get(
                WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value
            ))

        assert AnomalyState.OPEN.value in states
        assert states[-1] == AnomalyState.RESOLVED.value

        anomaly = (await db_session.scalars(select(WafAnomaly))).one()
        assert anomaly.state == AnomalyState.RESOLVED
        assert anomaly.resolved_at is not None
        assert anomaly.observed_value == 900.0

        transitions = (
            await db_session.scalars(select(WafAnomalyTransition))
        ).all()
        assert len(transitions) >= 2
        assert all(t.actor == "waf-anomaly-engine" for t in transitions)

    async def test_an_incomplete_bucket_does_not_resolve_an_open_incident(
        self, db_session: AsyncSession
    ) -> None:
        """Losing visibility mid-incident is not recovery."""
        cfg = settings()
        endpoint, metrics = await self._seed(
            db_session, [100] * 8 + [900, 100], cfg=cfg
        )
        await WafBaselineBuilder(db_session, settings=cfg).rebuild_for_endpoint(
            endpoint, semantics=LAB_SEMANTICS, now=metrics[8].bucket_start
        )
        engine = WafAnomalyEngine(db_session, settings=cfg)
        await engine.evaluate_interval(endpoint, metrics[8])

        metrics[9].completeness = BucketCompleteness.PARTIAL
        await db_session.flush()
        report = await engine.evaluate_interval(endpoint, metrics[9])

        assert report.states[WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value] == (
            AnomalyState.OPEN.value
        )
        anomaly = (await db_session.scalars(select(WafAnomaly))).one()
        assert anomaly.resolved_at is None

    async def test_re_evaluating_a_bucket_is_a_no_op(
        self, db_session: AsyncSession
    ) -> None:
        cfg = settings()
        endpoint, metrics = await self._seed(
            db_session, [100] * 8 + [900], cfg=cfg
        )
        await WafBaselineBuilder(db_session, settings=cfg).rebuild_for_endpoint(
            endpoint, semantics=LAB_SEMANTICS, now=metrics[8].bucket_start
        )
        engine = WafAnomalyEngine(db_session, settings=cfg)
        await engine.evaluate_interval(endpoint, metrics[8])
        await engine.evaluate_interval(endpoint, metrics[8])

        state = (await db_session.scalars(
            select(WafDetectorState).where(
                WafDetectorState.detector_type == WafAnomalyType.WAF_REQUEST_RATE_SPIKE
            )
        )).one()
        assert state.consecutive_anomalous == 1
        assert len((await db_session.scalars(select(WafAnomaly))).all()) == 1

    async def test_no_duplicate_active_incident_per_endpoint_and_detector(
        self, db_session: AsyncSession
    ) -> None:
        cfg = settings()
        endpoint, metrics = await self._seed(
            db_session, [100] * 8 + [900, 950, 1000], cfg=cfg
        )
        await WafBaselineBuilder(db_session, settings=cfg).rebuild_for_endpoint(
            endpoint, semantics=LAB_SEMANTICS, now=metrics[8].bucket_start
        )
        engine = WafAnomalyEngine(db_session, settings=cfg)
        for m in metrics[8:]:
            await engine.evaluate_interval(endpoint, m)

        anomalies = (await db_session.scalars(select(WafAnomaly))).all()
        assert len(anomalies) == 1
        # The worst reading is retained, not the latest.
        assert anomalies[0].observed_value == 1000.0

    async def test_a_low_coverage_bucket_is_excluded_from_the_baseline(
        self, db_session: AsyncSession
    ) -> None:
        cfg = settings()
        endpoint, metrics = await self._seed(db_session, [100] * 8, cfg=cfg)
        metrics[0].endpoint_coverage_ratio = 0.2
        await db_session.flush()

        builder = WafBaselineBuilder(db_session, settings=cfg)
        await builder.rebuild_for_endpoint(
            endpoint, semantics=LAB_SEMANTICS, now=metrics[-1].bucket_start + timedelta(seconds=300)
        )
        from app.models.waf import WafEndpointBaseline

        cells = (await db_session.scalars(select(WafEndpointBaseline))).all()
        assert cells
        assert any(
            c.exclusion_counts.get("low_coverage", 0) == 1 for c in cells
        )
