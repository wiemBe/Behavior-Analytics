"""Phase B.1.5: WAF collection, evaluation and training issue a bounded number of
SQL statements, whatever the number of endpoints or detectors.

Before B.1.5 a scheduled run cost about three statements per active endpoint
(6,273 at the 2,000-endpoint ceiling), almost all of them in evaluation. These
tests pin the batch-oriented shape by counting every statement the database
driver executes: if a future change reintroduces a per-endpoint (or
per-endpoint-per-detector) query, the counts stop being equal across sizes.
"""

from __future__ import annotations

import time
import uuid
from contextlib import contextmanager
from datetime import timedelta
from enum import StrEnum

import pytest
from sqlalchemy import event, func, insert, select, update

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.anomaly import capabilities
from app.anomaly.capabilities import BehaviorDomain, DetectorSpec
from app.anomaly.evidence import healthy
from app.history import HistoryFamily, HistoryState, encode_progress
from app.history.planner import ScopeProgress
from app.models.alert import Alert
from app.models.enums import (
    BucketCompleteness,
    NotificationChannel,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.log_source import LogSource
from app.models.waf import (
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.waf_pipeline import run_waf_source
from app.waf import engine as engine_module
from app.waf.baseline import WafBaselineBuilder
from app.waf.detector import WafSpikeVerdict
from app.waf.engine import WafAnomalyEngine
from app.waf.measurement import WafMeasurementRequirement
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import breakdown_semantics, request_count_semantics
from tests.integration.factories import make_instance
from tests.integration.test_waf_productionization import (
    STEP,
    T0,
    StubWafProvider,
    batch,
    cfg,
    count,
    source_with_profile,
    word,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SEMANTICS = request_count_semantics(
    aggregation="SUM(eventcount)", normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)


@contextmanager
def counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def path(i: int) -> str:
    return f"/app/{word(i)}"


def settings_for_scale():  # type: ignore[no-untyped-def]
    return cfg(
        waf_max_active_endpoints_per_source=50000,
        waf_max_groups_per_bucket=5000,
        waf_baseline_endpoints_per_run=50,
        waf_baseline_rebuild_chunk_size=25,
    )


async def seeded(session, instance, qradar_id: int, n: int, settings):  # type: ignore[no-untyped-def]
    """A validated source with `n` tracked endpoints, each with a reliable cell for T0's hour."""
    observed = max(1, n // 10)
    provider = StubWafProvider([
        batch(T0 + STEP * k, [(path(i), "GET", 40) for i in range(observed)]) for k in range(6)
    ])
    profile, source = await source_with_profile(
        session, provider, settings, qradar_id=qradar_id, instance=instance
    )
    ids = [uuid.uuid4() for _ in range(n)]
    await session.execute(insert(WafEndpoint), [
        dict(
            id=ids[i], instance_id=instance.id, log_source_id=source.id, virtual_host=None,
            normalized_endpoint=path(i), http_method="GET",
            normalizer_version=ENDPOINT_NORMALIZER_VERSION, first_seen_at=T0 - timedelta(days=1),
            last_seen_at=T0 - timedelta(hours=1), tracked_since=T0 - timedelta(days=1),
            is_active=True,
        )
        for i in range(n)
    ])
    await session.execute(insert(WafEndpointBaseline), [
        dict(
            id=uuid.uuid4(), waf_endpoint_id=ids[i], metric_name="request_count",
            weekday=T0.isoweekday(), hour=T0.hour, median=40.0, mad=4.0, p05=30.0, p95=50.0,
            sample_count=12, is_reliable=True, baseline_version=1,
            normalizer_version=ENDPOINT_NORMALIZER_VERSION, observations=[40.0] * 12,
            completeness=1.0, excluded_sample_count=0, exclusion_counts={},
            metric_semantics=SEMANTICS,
        )
        for i in range(n)
    ])
    await session.commit()
    return profile.id, source.id, provider


async def run(session, profile_id, provider, settings, k: int) -> dict:  # type: ignore[no-untyped-def]
    at = T0 + STEP * k
    return await run_waf_source(
        session, profile_id, provider_factory=lambda _i: provider, settings=settings,
        clock=lambda: at + STEP + timedelta(seconds=5),
    )


async def add_full_bucket(
    session, source_id, at, endpoint_ids, *, request_count: int
):  # type: ignore[no-untyped-def]
    """A dense evaluation bucket with one row for every seeded endpoint."""
    await session.execute(
        insert(WafSourceBucket),
        [
            dict(
                log_source_id=source_id,
                bucket_start=at,
                bucket_seconds=300,
                origin="LIVE",
                profile_version=1,
                metric_semantics=SEMANTICS,
                completeness=BucketCompleteness.COMPLETE,
                endpoint_coverage_ratio=1.0,
                total_events=request_count * len(endpoint_ids),
                parsed_events=request_count * len(endpoint_ids),
                group_count=len(endpoint_ids),
                groups_truncated=False,
                unusable_groups=0,
                observed_endpoints=len(endpoint_ids),
                tracked_endpoints_observed=len(endpoint_ids),
                admitted_endpoints=0,
                below_admission_threshold=0,
                rejected_over_quota=0,
                untracked_endpoints=0,
                untracked_requests=0,
                admission_truncated=False,
                zero_filled_endpoints=0,
                request_count_measurement=WafMeasurement.EXACT,
                sparse_endpoint_rows=False,
                in_maintenance=False,
                collection_source="TEST",
                query_provenance={},
            )
        ],
    )
    await session.execute(
        insert(WafEndpointMetric),
        [
            dict(
                waf_endpoint_id=endpoint_id,
                bucket_start=at,
                bucket_seconds=300,
                request_count=request_count,
                distinct_client_count=3,
                endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE,
                normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            )
            for endpoint_id in endpoint_ids
        ],
    )
    await session.flush()


class TestStatementCounts:
    async def test_a_scheduled_run_does_not_grow_with_endpoints(self, db_session) -> None:
        settings = settings_for_scale()
        instance = await make_instance(db_session)
        small = await seeded(db_session, instance, 312, 60, settings)
        large = await seeded(db_session, instance, 313, 1200, settings)

        per_run: dict[int, list[int]] = {}
        for size, (profile_id, _source_id, provider) in ((60, small), (1200, large)):
            await run(db_session, profile_id, provider, settings, 0)  # creates detector states
            await run(db_session, profile_id, provider, settings, 1)
            counts = []
            for k in (2, 3):
                with counting(db_session) as statements:
                    result = await run(db_session, profile_id, provider, settings, k)
                assert result["status"] == "ok"
                assert result["endpoint_evaluations"] == size
                counts.append(len(statements))
            per_run[size] = counts

        # Twenty times the endpoints, the same statements.
        assert per_run[60] == per_run[1200]
        assert max(per_run[1200]) <= 40, per_run
        print(f"no-decision scheduled-run statements: {per_run}")

    async def test_a_run_for_a_yes_console_with_a_finished_scope_does_not_grow(
        self, db_session
    ) -> None:
        """The history lifecycle of an active Yes costs the same at 60 and 1,200 endpoints.

        The decision and the scope row are read; the live-bucket EXISTS is not, because a
        source with a row never needs it.
        """
        settings = settings_for_scale()
        instance = await make_instance(db_session)
        repository = HistoryBackfillRepository(db_session)
        await repository.record_decision(
            instance.id, answer="YES", days=7, source="UI", decided_by="operator",
            at=T0 - timedelta(days=3),
        )
        small = await seeded(db_session, instance, 312, 60, settings)
        large = await seeded(db_session, instance, 313, 1200, settings)
        for source_id in (small[1], large[1]):
            qradar_id = await db_session.scalar(
                select(LogSource.qradar_id).where(LogSource.id == source_id)
            )
            await repository.write_scopes([{
                "instance_id": instance.id,
                "family": HistoryFamily.WAF.value,
                "scope_key": str(qradar_id),
                "state": HistoryState.COMPLETE.value,
                "days_requested": 7,
                "days_target": 7,
                "target_start": T0 - timedelta(days=8),
                "floor": T0 - timedelta(days=8),
                "through": T0 - timedelta(days=1),
                "anchor": T0 - timedelta(days=1),
                "started_at": T0 - timedelta(days=2),
                "finished_at": T0 - timedelta(days=2),
                "reason": "test: a finished reading",
                "reason_i18n": None,
                "last_error_class": None,
                "last_error_at": None,
                "progress": encode_progress(
                    ScopeProgress(
                        target_start=T0 - timedelta(days=8),
                        started_at=T0 - timedelta(days=2),
                        floor=T0 - timedelta(days=8),
                        through=T0 - timedelta(days=1),
                        scan_cursor=None,
                    )
                ),
            }])
        await db_session.commit()

        per_run: dict[int, list[int]] = {}
        for size, (profile_id, _source_id, provider) in ((60, small), (1200, large)):
            await run(db_session, profile_id, provider, settings, 0)
            await run(db_session, profile_id, provider, settings, 1)
            counts = []
            for k in (2, 3):
                with counting(db_session) as statements:
                    result = await run(db_session, profile_id, provider, settings, k)
                assert result["status"] == "ok"
                assert result["history_state"] == HistoryState.COMPLETE.value
                counts.append(len(statements))
            per_run[size] = counts

        assert per_run[60] == per_run[1200], per_run
        # The decision, the scope row and nothing more than the no-decision run plus them.
        assert max(per_run[1200]) <= 41, per_run
        print(f"active-Yes COMPLETE-scope statements: {per_run}")

    async def test_evaluation_does_not_multiply_by_endpoints_times_detectors(
        self, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        settings = settings_for_scale()
        instance = await make_instance(db_session)
        small = await seeded(db_session, instance, 312, 40, settings)
        large = await seeded(db_session, instance, 313, 800, settings)
        for profile_id, _s, provider in (small, large):
            for k in range(5):
                await run(db_session, profile_id, provider, settings, k)

        async def rewind(to: int) -> None:
            """Make every detector state judge bucket `to + 1` again."""
            for state in (await db_session.scalars(select(WafDetectorState))).all():
                state.last_interval_start = T0 + STEP * to
            await db_session.flush()

        class Fake(StrEnum):
            WAF_FAKE_DETECTOR = "WAF_FAKE_DETECTOR"

        def fake_detector(ctx):  # type: ignore[no-untyped-def]
            return WafSpikeVerdict(
                detector_type=Fake.WAF_FAKE_DETECTOR,  # type: ignore[arg-type]
                evidence=healthy(
                    Fake.WAF_FAKE_DETECTOR, interval_start=ctx.point.bucket_start,
                    interval_end=ctx.point.bucket_end, observed=ctx.point.request_count,
                ),
            )

        monkeypatch.setitem(capabilities.WAF_DETECTORS, Fake.WAF_FAKE_DETECTOR, DetectorSpec(  # type: ignore[arg-type]
            domain=BehaviorDomain.WAF_ENDPOINT, detector_type=Fake.WAF_FAKE_DETECTOR.value,
            telemetry=frozenset(), baseline_metrics=frozenset({"request_count"}),
            scheduled_task=None, measurement=WafMeasurementRequirement.EXACT,
        ))
        two_detectors = {
            **engine_module.WAF_DETECTOR_FUNCTIONS, Fake.WAF_FAKE_DETECTOR: fake_detector,
        }

        async def evaluate(source_id, k: int, detectors) -> int:  # type: ignore[no-untyped-def]
            source = await db_session.get(LogSource, source_id)
            bucket = await db_session.get(WafSourceBucket, (source_id, T0 + STEP * k))
            engine = WafAnomalyEngine(db_session, settings=settings, detectors=detectors)
            with counting(db_session) as statements:
                result = await engine.evaluate_bucket(source, bucket)
            assert len(result.endpoint_reports) in (40, 800)
            return len(statements)

        sizes = ((40, small), (800, large))
        await rewind(1)
        one = {n: await evaluate(src, 2, None) for n, (_p, src, _pr) in sizes}
        assert one[40] == one[800]
        # States for the second detector do not exist yet: created in one insert.
        await rewind(2)
        two_first = {n: await evaluate(src, 3, two_detectors) for n, (_p, src, _pr) in sizes}
        assert two_first[40] == two_first[800]
        await rewind(3)
        two = {n: await evaluate(src, 4, two_detectors) for n, (_p, src, _pr) in sizes}
        assert two[40] == two[800]
        assert two[800] <= one[800] + 1, (one, two)
        # Request rate, the always-on new-endpoint detector (T-006b), and the fake one.
        assert await count(db_session, WafDetectorState) == 3 * (40 + 800)

    async def test_evaluation_cost_is_constant_with_every_b2_detector_enabled(
        self, db_session
    ) -> None:
        collection_settings = settings_for_scale()
        enabled_settings = collection_settings.model_copy(
            update={
                "waf_outcome_collection_enabled": True,
                "waf_client_collection_enabled": True,
            }
        )
        instance = await make_instance(db_session)
        small = await seeded(db_session, instance, 312, 40, collection_settings)
        large = await seeded(db_session, instance, 313, 800, collection_settings)
        for profile_id, _source_id, provider in (small, large):
            for k in range(3):
                await run(db_session, profile_id, provider, collection_settings, k)

        async def rewind(source_id, to: int) -> None:  # type: ignore[no-untyped-def]
            states = await db_session.scalars(
                select(WafDetectorState)
                .join(WafEndpoint)
                .where(WafEndpoint.log_source_id == source_id)
            )
            for state in states:
                state.last_interval_start = T0 + STEP * to
            await db_session.flush()

        async def evaluate(source_id, k: int, settings) -> int:  # type: ignore[no-untyped-def]
            source = await db_session.get(LogSource, source_id)
            bucket = await db_session.get(WafSourceBucket, (source_id, T0 + STEP * k))
            with counting(db_session) as statements:
                result = await WafAnomalyEngine(
                    db_session, settings=settings
                ).evaluate_bucket(source, bucket)
            assert len(result.endpoint_reports) in (40, 800)
            return len(statements)

        off: dict[int, int] = {}
        first_on: dict[int, int] = {}
        steady_on: dict[int, int] = {}
        for size, (_profile_id, source_id, _provider) in ((40, small), (800, large)):
            await rewind(source_id, 0)
            off[size] = await evaluate(source_id, 1, collection_settings)
            await rewind(source_id, 0)
            first_on[size] = await evaluate(source_id, 1, enabled_settings)
            await rewind(source_id, 1)
            steady_on[size] = await evaluate(source_id, 2, enabled_settings)

        assert off[40] == off[800]
        assert first_on[40] == first_on[800]
        assert steady_on[40] == steady_on[800]
        assert first_on[800] <= off[800] + 1, (off, first_on)
        assert steady_on[800] <= off[800] + 1, (off, steady_on)

    async def test_novelty_cost_is_constant_with_and_without_in_window_endpoints(
        self, db_session
    ) -> None:
        settings = settings_for_scale()
        instance = await make_instance(db_session)
        small = await seeded(db_session, instance, 312, 40, settings)
        large = await seeded(db_session, instance, 313, 800, settings)
        for profile_id, _s, provider in (small, large):
            for k in range(4):
                await run(db_session, profile_id, provider, settings, k)

        async def evaluate(source_id, k: int) -> int:  # type: ignore[no-untyped-def]
            for state in (await db_session.scalars(
                select(WafDetectorState)
                .join(WafEndpoint)
                .where(WafEndpoint.log_source_id == source_id)
            )).all():
                state.last_interval_start = T0 + STEP * (k - 1)
            await db_session.flush()
            source = await db_session.get(LogSource, source_id)
            bucket = await db_session.get(WafSourceBucket, (source_id, T0 + STEP * k))
            with counting(db_session) as statements:
                result = await WafAnomalyEngine(
                    db_session, settings=settings
                ).evaluate_bucket(source, bucket)
            assert len(result.endpoint_reports) in (40, 800)
            return len(statements)

        established: dict[int, int] = {}
        in_window: dict[int, int] = {}
        for size, (_p, source_id, _pr) in ((40, small), (800, large)):
            established[size] = await evaluate(source_id, 3)
            # Every endpoint first appeared at T0, inside its novelty window.
            await db_session.execute(
                update(WafEndpoint)
                .where(WafEndpoint.log_source_id == source_id)
                .values(first_seen_at=T0)
            )
            db_session.expire_all()
            in_window[size] = await evaluate(source_id, 3)

        assert established[40] == established[800]
        assert in_window[40] == in_window[800]
        # The bucket records and one chunk of traffic aggregates: two statements.
        assert in_window[800] - established[800] == 2, (established, in_window)

    async def test_alerting_without_transitions_adds_at_most_one_statement(
        self, db_session
    ) -> None:
        settings = settings_for_scale()
        alerting = settings.model_copy(update={"waf_alerting_enabled": True})
        instance = await make_instance(db_session)
        small = await seeded(db_session, instance, 312, 40, settings)
        large = await seeded(db_session, instance, 313, 800, settings)
        for profile_id, _source_id, provider in (small, large):
            for k in range(3):
                await run(db_session, profile_id, provider, settings, k)

        async def rewind(source_id) -> None:  # type: ignore[no-untyped-def]
            states = await db_session.scalars(
                select(WafDetectorState)
                .join(WafEndpoint)
                .where(WafEndpoint.log_source_id == source_id)
            )
            for state in states:
                state.last_interval_start = T0
            await db_session.flush()

        async def evaluate(source_id, configured) -> int:  # type: ignore[no-untyped-def]
            source = await db_session.get(LogSource, source_id)
            bucket = await db_session.get(WafSourceBucket, (source_id, T0 + STEP))
            with counting(db_session) as statements:
                result = await WafAnomalyEngine(
                    db_session, settings=configured
                ).evaluate_bucket(source, bucket)
            assert len(result.endpoint_reports) in (40, 800)
            return len(statements)

        off: dict[int, int] = {}
        on: dict[int, int] = {}
        for size, (_profile_id, source_id, _provider) in ((40, small), (800, large)):
            await rewind(source_id)
            off[size] = await evaluate(source_id, settings)
            await rewind(source_id)
            on[size] = await evaluate(source_id, alerting)

        assert off[40] == off[800]
        assert on[40] == on[800]
        assert on[800] <= off[800] + 1, (off, on)

    async def test_refreshing_every_open_alert_is_constant_cost(self, db_session) -> None:
        settings = settings_for_scale().model_copy(
            update={
                "waf_alerting_enabled": True,
                "anomaly_open_after_intervals": 1,
            }
        )
        instance = await make_instance(db_session)
        small = await seeded(db_session, instance, 312, 40, settings)
        large = await seeded(db_session, instance, 313, 800, settings)
        costs: dict[int, int] = {}
        for size, (_profile_id, source_id, _provider) in ((40, small), (800, large)):
            endpoint_ids = list(
                (
                    await db_session.scalars(
                        select(WafEndpoint.id).where(
                            WafEndpoint.log_source_id == source_id
                        )
                    )
                ).all()
            )
            open_at = T0 + STEP * 10
            refresh_at = open_at + STEP
            await add_full_bucket(
                db_session, source_id, open_at, endpoint_ids, request_count=500
            )
            source = await db_session.get(LogSource, source_id)
            bucket = await db_session.get(WafSourceBucket, (source_id, open_at))
            opened = await WafAnomalyEngine(
                db_session, settings=settings
            ).evaluate_bucket(source, bucket)
            assert opened.opened == size
            assert int(
                await db_session.scalar(
                    select(func.count()).select_from(Alert).where(
                        Alert.source_id.in_(endpoint_ids)
                    )
                )
                or 0
            ) == size

            await add_full_bucket(
                db_session, source_id, refresh_at, endpoint_ids, request_count=600
            )
            bucket = await db_session.get(WafSourceBucket, (source_id, refresh_at))
            with counting(db_session) as statements:
                refreshed = await WafAnomalyEngine(
                    db_session, settings=settings
                ).evaluate_bucket(source, bucket)
            assert refreshed.opened == 0
            costs[size] = len(statements)

        assert costs[40] == costs[800], costs

    async def test_alert_transition_statement_cost_is_bounded(self, db_session) -> None:
        base = settings_for_scale().model_copy(
            update={"anomaly_open_after_intervals": 1, "anomaly_resolve_after_intervals": 1}
        )
        alerting = base.model_copy(update={"waf_alerting_enabled": True})
        instance = await make_instance(db_session)
        shadow = await seeded(db_session, instance, 312, 1, base)
        paging = await seeded(db_session, instance, 313, 1, alerting)
        route = RoutingPolicy(
            rules=[
                RoutingRule(
                    channel=NotificationChannel.SLACK,
                    target="scale-test",
                    alert_types=frozenset({"waf_anomaly"}),
                )
            ]
        )

        async def evaluate(
            seeded_source, configured, at, count, *, enqueuer=None
        ) -> int:  # type: ignore[no-untyped-def]
            _profile_id, source_id, _provider = seeded_source
            endpoint_ids = list(
                (
                    await db_session.scalars(
                        select(WafEndpoint.id).where(WafEndpoint.log_source_id == source_id)
                    )
                ).all()
            )
            await add_full_bucket(
                db_session, source_id, at, endpoint_ids, request_count=count
            )
            source = await db_session.get(LogSource, source_id)
            bucket = await db_session.get(WafSourceBucket, (source_id, at))
            with counting(db_session) as statements:
                await WafAnomalyEngine(
                    db_session, settings=configured, enqueuer=enqueuer
                ).evaluate_bucket(source, bucket)
            return len(statements)

        dispatcher = NotificationDispatcher(db_session, {}, route, settings=alerting)
        open_at = T0 + STEP * 10
        shadow_open = await evaluate(shadow, base, open_at, 500)
        paging_open = await evaluate(
            paging, alerting, open_at, 500, enqueuer=dispatcher
        )
        resolve_at = open_at + STEP
        shadow_resolve = await evaluate(shadow, base, resolve_at, 40)
        paging_resolve = await evaluate(
            paging, alerting, resolve_at, 40, enqueuer=dispatcher
        )

        open_cost = paging_open - shadow_open
        resolve_cost = paging_resolve - shadow_resolve
        assert 0 < open_cost <= 8, (shadow_open, paging_open)
        assert 0 < resolve_cost <= 6, (shadow_resolve, paging_resolve)

    async def test_training_scales_with_chunks_not_endpoints(self, db_session) -> None:
        settings = cfg(waf_baseline_rebuild_chunk_size=25, baseline_min_samples=1)
        instance = await make_instance(db_session)
        profile_id, source_id, provider = await seeded(db_session, instance, 312, 125, settings)
        for k in range(4):
            await run(db_session, profile_id, provider, settings, k)
        source = await db_session.get(LogSource, source_id)
        endpoints = list((await db_session.scalars(
            select(WafEndpoint)
            .where(WafEndpoint.log_source_id == source_id)
            .order_by(WafEndpoint.id)
        )).all())
        builder = WafBaselineBuilder(db_session, settings=settings)

        async def statements_for(chunk: list[WafEndpoint]) -> int:
            with counting(db_session) as statements:
                written = await builder.rebuild_endpoints(
                    chunk, semantics=SEMANTICS, now=T0 + STEP * 4, log_source=source
                )
            assert all(v >= 1 for v in written.values())
            return len(statements)

        one_chunk = await statements_for(endpoints[:25])
        four_chunks = await statements_for(endpoints[25:125])
        # One bucket-record load; then, per chunk of 25, its rows, its incident
        # spans, one upsert of all its cells, and one batched update of the
        # endpoints' rebuild time. 25 or 100 endpoints: only the chunk count moves.
        assert one_chunk == 5
        assert four_chunks - one_chunk == 3 * 4, (one_chunk, four_chunks)

    async def test_training_statement_count_is_unchanged_with_breakdowns(self, db_session) -> None:
        settings = cfg(
            waf_baseline_rebuild_chunk_size=25,
            baseline_min_samples=1,
            waf_ratio_min_denominator=1,
            waf_concentration_min_endpoint_requests=1,
        )
        instance = await make_instance(db_session)
        profile_id, source_id, provider = await seeded(db_session, instance, 312, 25, settings)
        for k in range(4):
            await run(db_session, profile_id, provider, settings, k)
        source = await db_session.get(LogSource, source_id)
        endpoints = list((await db_session.scalars(
            select(WafEndpoint).where(WafEndpoint.log_source_id == source_id)
        )).all())
        builder = WafBaselineBuilder(db_session, settings=settings)

        with counting(db_session) as statements:
            await builder.rebuild_endpoints(
                endpoints, semantics=SEMANTICS, now=T0 + STEP * 4, log_source=source
            )
        without_breakdowns = len(statements)

        identities = {
            breakdown: breakdown_semantics(
                breakdown.value,
                aggregation="SUM(eventcount)",
                normalizer_version=ENDPOINT_NORMALIZER_VERSION,
                profile_version=1,
            )
            for breakdown in WafBreakdown
        }
        await db_session.execute(
            update(WafSourceBucket)
            .where(WafSourceBucket.log_source_id == source_id)
            .values(
                action_measurement=WafTelemetryStatus.EXACT,
                action_semantics=identities[WafBreakdown.ACTION],
                response_code_measurement=WafTelemetryStatus.EXACT,
                response_code_semantics=identities[WafBreakdown.RESPONSE_CODE],
                client_measurement=WafTelemetryStatus.EXACT,
                client_semantics=identities[WafBreakdown.SOURCE_IP],
            )
        )
        await db_session.execute(
            update(WafEndpointMetric)
            .where(WafEndpointMetric.waf_endpoint_id.in_([endpoint.id for endpoint in endpoints]))
            .values(
                action_events=WafEndpointMetric.request_count,
                blocked_count=1,
                unmapped_action_count=0,
                response_code_events=WafEndpointMetric.request_count,
                client_error_count=1,
                server_error_count=1,
                unclassified_response_count=0,
                client_events=WafEndpointMetric.request_count,
                top1_client_requests=1,
            )
        )
        await db_session.flush()

        with counting(db_session) as statements:
            await builder.rebuild_endpoints(
                endpoints,
                semantics=SEMANTICS,
                breakdown_identities=identities,
                now=T0 + STEP * 5,
                log_source=source,
            )
        with_breakdowns = len(statements)

        assert with_breakdowns == without_breakdowns == 5


async def test_collection_at_the_default_ceiling_is_bulk(db_session) -> None:
    """2,000 tracked endpoints, all observed in one bucket: a bounded run."""
    settings = settings_for_scale()
    instance = await make_instance(db_session)
    profile_id, _source_id, provider = await seeded(db_session, instance, 312, 2000, settings)
    at = T0 + STEP * 6
    provider.batches[at] = batch(at, [(path(i), "GET", 40 + i % 7) for i in range(2000)])
    await run(db_session, profile_id, provider, settings, 5)

    started = time.perf_counter()
    with counting(db_session) as statements:
        result = await run(db_session, profile_id, provider, settings, 6)
    elapsed = time.perf_counter() - started

    assert result["status"] == "ok"
    assert result["rows_written"] == 2000
    assert result["endpoint_evaluations"] == 2000
    assert await count(db_session, WafEndpointMetric, WafEndpointMetric.bucket_start == at) == 2000
    assert len(statements) <= 40, len(statements)
    # Informational bound on a developer workstation; the statement count is the guard.
    assert elapsed < 30


async def test_the_evaluation_batch_includes_reconstructed_zeros(db_session) -> None:
    """The batch-preparation path loads the tracked endpoints a sparse bucket has no row for."""
    settings = settings_for_scale()
    instance = await make_instance(db_session)
    profile_id, source_id, provider = await seeded(db_session, instance, 312, 300, settings)
    await run(db_session, profile_id, provider, settings, 0)
    source = await db_session.get(LogSource, source_id)
    bucket = await db_session.get(WafSourceBucket, (source_id, T0 + STEP * 1))
    assert bucket is None
    await run(db_session, profile_id, provider, settings, 1)
    bucket = await db_session.get(WafSourceBucket, (source_id, T0 + STEP * 1))
    for state in (await db_session.scalars(select(WafDetectorState))).all():
        state.last_interval_start = T0
    await db_session.flush()
    with counting(db_session) as statements:
        engine = WafAnomalyEngine(db_session, settings=settings)
        result = await engine.evaluate_bucket(source, bucket)
    assert len(result.endpoint_reports) == 300
    assert result.inferred_observations == 270  # 30 observed, 270 measured zeros
    assert len(statements) <= 6, statements

