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
from sqlalchemy import event, insert, select

from app.anomaly import capabilities
from app.anomaly.capabilities import BehaviorDomain, DetectorSpec
from app.anomaly.evidence import healthy
from app.models.log_source import LogSource
from app.models.waf import (
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.services.waf_pipeline import run_waf_source
from app.waf import engine as engine_module
from app.waf.baseline import WafBaselineBuilder
from app.waf.detector import WafSpikeVerdict
from app.waf.engine import WafAnomalyEngine
from app.waf.measurement import WafMeasurementRequirement
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import request_count_semantics
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
        assert await count(db_session, WafDetectorState) == 2 * (40 + 800)

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

