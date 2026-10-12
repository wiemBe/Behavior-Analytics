"""Phase B.1.5: collection resilience against raw-path floods, and sparse storage.

Every flood below is served the way the REST provider serves it: groups ordered
by request volume, cut at `WAF_MAX_GROUPS_PER_BUCKET`, `truncated` set when the
cut was reached, and a total from the separate ungrouped aggregate. Runs go
through `run_waf_source`, the function the scheduled task calls.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    SearchFamily,
    WafAnomalyType,
    WafMeasurement,
)
from app.models.waf import (
    WafAnomaly,
    WafAnomalyTransition,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.providers.base import ProviderUnavailableError
from app.providers.dto import WafObservationBatch
from app.services.search_cadence import SearchCadenceService
from app.services.waf_retention import WafRetention
from app.services.waf_status import waf_source_statuses
from app.waf.baseline import WafBaselineBuilder
from tests.integration.test_waf_productionization import (
    NORMAL,
    STEP,
    T0,
    StubWafProvider,
    batch,
    cfg,
    count,
    run_at,
    source_with_profile,
    word,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CAP = 500


def capped(
    start: datetime, groups: list[tuple[str, str, int]], cap: int = CAP
) -> WafObservationBatch:
    """What the REST provider returns for this traffic under a group cap."""
    ordered = sorted(groups, key=lambda g: -g[2])
    visible = ordered[:cap]
    return batch(
        start, visible, total=sum(c for *_, c in groups), truncated=len(visible) >= cap
    )


class RecordingProvider(StubWafProvider):
    def __init__(self, batches=None, **kw) -> None:  # type: ignore[no-untyped-def]
        super().__init__(batches, **kw)
        self.max_groups: list[int] = []

    async def get_waf_observations(self, **kw):  # type: ignore[no-untyped-def, override]
        self.max_groups.append(kw["max_groups"])
        return await super().get_waf_observations(**kw)


async def endpoint_named(session, path: str) -> WafEndpoint:  # type: ignore[no-untyped-def]
    return await session.scalar(select(WafEndpoint).where(WafEndpoint.normalized_endpoint == path))


async def state_of(session, path: str) -> str:  # type: ignore[no-untyped-def]
    endpoint = await session.scalar(
        select(WafEndpoint).where(WafEndpoint.normalized_endpoint == path)
    )
    state = await session.scalar(
        select(WafDetectorState).where(
            WafDetectorState.waf_endpoint_id == endpoint.id,
            WafDetectorState.detector_type == WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
        )
    )
    return str(state.state)


async def prime(session, traffic, *, buckets: int = 6, settings=None):  # type: ignore[no-untyped-def]
    """`buckets` normal buckets so every path in `traffic` has a reliable cell."""
    settings = settings or cfg(waf_max_groups_per_bucket=CAP)
    provider = RecordingProvider([
        batch(T0 + STEP * i, [(p, m, NORMAL[i % len(NORMAL)] * c // 100) for p, m, c in traffic])
        for i in range(buckets)
    ])
    profile, source = await source_with_profile(session, provider, settings)
    for i in range(buckets):
        result = await run_at(session, profile, provider, settings, T0 + STEP * i)
        assert result["status"] == "ok", result
    return settings, provider, profile, source


def spray(n: int, count_each: int, prefix: str = "/spray") -> list[tuple[str, str, int]]:
    return [(f"{prefix}/{word(i + 7000)}", "GET", count_each) for i in range(n)]


# ============================================================ adversarial floods
class TestPathFloods:
    async def test_thousands_of_unique_paths_do_not_blind_the_source(self, db_session) -> None:
        tracked = [("/portal/login", "POST", 100), ("/api/v1/status", "GET", 100)]
        settings, provider, profile, source = await prime(db_session, tracked)
        flood_at = T0 + STEP * 6
        traffic = [("/portal/login", "POST", 111), ("/api/v1/status", "GET", 93), *spray(5000, 5)]
        provider.batches[flood_at] = capped(flood_at, traffic)

        result = await run_at(db_session, profile, provider, settings, flood_at)

        assert result["status"] == "ok"
        # The query stayed bounded.
        assert set(provider.max_groups) == {CAP}
        bucket = await db_session.get(WafSourceBucket, (source.id, flood_at))
        assert bucket.groups_truncated and not bucket.discovery_complete
        assert bucket.request_count_measurement == WafMeasurement.LOWER_BOUND
        assert bucket.completeness == BucketCompleteness.PARTIAL
        # Source volume is exact: it comes from its own ungrouped aggregate.
        assert bucket.total_events == 111 + 93 + 5000 * 5
        # Admission stayed bounded despite 5,000 candidates.
        assert bucket.admitted_endpoints <= settings.waf_max_new_endpoints_per_bucket
        assert await count(db_session, WafEndpoint) <= 2 + settings.waf_max_new_endpoints_per_bucket
        # Uncertainty is not HEALTHY: both tracked endpoints are unjudgeable, not normal.
        assert await state_of(db_session, "/portal/login") == AnomalyState.INSUFFICIENT_DATA
        assert await state_of(db_session, "/api/v1/status") == AnomalyState.INSUFFICIENT_DATA
        assert await count(db_session, WafAnomaly) == 0
        # The flooded bucket trains nothing.
        cell = await db_session.scalar(
            select(WafEndpointBaseline).join(WafEndpoint).where(
                WafEndpoint.normalized_endpoint == "/portal/login"
            )
        )
        assert cell.exclusion_counts["incomplete"] >= 1
        assert 111.0 not in cell.observations

        # Visibility returns with the next exact bucket.
        after = flood_at + STEP
        provider.batches[after] = batch(after, [("/portal/login", "POST", 101),
                                                ("/api/v1/status", "GET", 99)])
        await run_at(db_session, profile, provider, settings, after)
        assert await state_of(db_session, "/portal/login") == AnomalyState.NORMAL

    async def test_raw_paths_collapsing_into_a_tracked_endpoint_prove_a_spike(
        self, db_session
    ) -> None:
        # Normal: 100 distinct user ids, one request each -> /users/{id} = 100.
        settings = cfg(waf_max_groups_per_bucket=CAP)
        provider = RecordingProvider([
            batch(T0 + STEP * i, [(f"/users/{u}", "GET", 1) for u in range(NORMAL[i])])
            for i in range(6)
        ])
        profile, _source = await source_with_profile(db_session, provider, settings)
        for i in range(6):
            await run_at(db_session, profile, provider, settings, T0 + STEP * i)

        flood_at = T0 + STEP * 6
        provider.batches[flood_at] = capped(
            flood_at, [(f"/users/{u}", "GET", 3) for u in range(5000)]
        )
        await run_at(db_session, profile, provider, settings, flood_at)

        assert await count(db_session, WafEndpoint) == 1
        row = await db_session.scalar(
            select(WafEndpointMetric).where(WafEndpointMetric.bucket_start == flood_at)
        )
        # 500 visible groups x 3: a lower bound on the true 15,000.
        assert row.request_count == 1500 and row.completeness == BucketCompleteness.PARTIAL
        anomaly = await db_session.scalar(select(WafAnomaly))
        assert anomaly is not None and anomaly.state == AnomalyState.OPEN
        assert anomaly.observed_measurement == WafMeasurement.LOWER_BOUND
        assert anomaly.explanation.startswith("at least 1500 requests")
        assert anomaly.details["extra"]["request_count_measurement"] == "LOWER_BOUND"
        transition = await db_session.scalar(select(WafAnomalyTransition))
        assert transition.observed_measurement == WafMeasurement.LOWER_BOUND

    async def test_a_tracked_spike_survives_an_unrelated_path_flood(self, db_session) -> None:
        tracked = [("/portal/login", "POST", 100), ("/api/v1/status", "GET", 100)]
        settings, provider, profile, _source = await prime(db_session, tracked)
        flood_at = T0 + STEP * 6
        traffic = [("/portal/login", "POST", 900), ("/api/v1/status", "GET", 100),
                   *spray(5000, 1)]
        provider.batches[flood_at] = capped(flood_at, traffic)

        await run_at(db_session, profile, provider, settings, flood_at)

        anomalies = (await db_session.scalars(select(WafAnomaly))).all()
        assert len(anomalies) == 1
        (spike,) = anomalies
        endpoint = await db_session.get(WafEndpoint, spike.waf_endpoint_id)
        assert endpoint.normalized_endpoint == "/portal/login"
        assert spike.state == AnomalyState.OPEN
        assert spike.observed_value == 900
        assert spike.observed_measurement == WafMeasurement.LOWER_BOUND
        # The sibling at its normal volume is not called normal under a flood.
        assert await state_of(db_session, "/api/v1/status") == AnomalyState.INSUFFICIENT_DATA

    async def test_low_volume_endpoints_under_a_spray_are_unjudged_and_nothing_resolves(
        self, db_session
    ) -> None:
        tracked = [("/portal/login", "POST", 100), ("/api/v1/status", "GET", 100),
                   ("/rare", "GET", 100)]
        settings, provider, profile, _source = await prime(db_session, tracked)
        spike_at = T0 + STEP * 6
        provider.batches[spike_at] = batch(spike_at, [
            ("/portal/login", "POST", 900), ("/api/v1/status", "GET", 100), ("/rare", "GET", 100)
        ])
        await run_at(db_session, profile, provider, settings, spike_at)
        incident = await db_session.scalar(select(WafAnomaly))
        assert incident.state == AnomalyState.OPEN

        # Login falls back to normal during a spray heavy enough to push the
        # low-volume /rare group below the cap entirely.
        for i in (7, 8, 9):
            at = T0 + STEP * i
            provider.batches[at] = capped(at, [
                ("/portal/login", "POST", 100), ("/api/v1/status", "GET", 30),
                ("/rare", "GET", 3), *spray(3000, 10),
            ])
            await run_at(db_session, profile, provider, settings, at)
            await db_session.refresh(incident)
            # An unprovable "normal" is not recovery.
            assert incident.state == AnomalyState.OPEN
            assert incident.resolved_at is None
            assert await state_of(db_session, "/api/v1/status") == AnomalyState.INSUFFICIENT_DATA
            assert await state_of(db_session, "/rare") == AnomalyState.INSUFFICIENT_DATA

        rare = await endpoint_named(db_session, "/rare")
        # Cut off by the cap: no row, and its absence was not read as zero.
        assert await count(
            db_session, WafEndpointMetric, WafEndpointMetric.waf_endpoint_id == rare.id,
            WafEndpointMetric.bucket_start >= T0 + STEP * 7,
        ) == 0

        # The spray stops: exact buckets let the incident recover and resolve.
        for i in (10, 11):
            at = T0 + STEP * i
            provider.batches[at] = batch(at, [
                ("/portal/login", "POST", 100), ("/api/v1/status", "GET", 100),
                ("/rare", "GET", 100),
            ])
            await run_at(db_session, profile, provider, settings, at)
        await db_session.refresh(incident)
        assert incident.state == AnomalyState.RESOLVED


# ============================================================ sparse storage
class TestSparseSemantics:
    async def test_absent_from_an_exact_bucket_is_a_judged_measured_zero(self, db_session) -> None:
        tracked = [("/portal/login", "POST", 100), ("/other", "GET", 100)]
        settings, provider, profile, source = await prime(db_session, tracked)
        at = T0 + STEP * 6
        provider.batches[at] = batch(at, [("/other", "GET", 100)])
        result = await run_at(db_session, profile, provider, settings, at)

        login = await endpoint_named(db_session, "/portal/login")
        assert await count(db_session, WafEndpointMetric, WafEndpointMetric.bucket_start == at,
                           WafEndpointMetric.waf_endpoint_id == login.id) == 0
        bucket = await db_session.get(WafSourceBucket, (source.id, at))
        assert bucket.sparse_endpoint_rows and bucket.zero_filled_endpoints == 0
        # Both endpoints were judged; one from a reconstructed zero.
        assert result["endpoint_evaluations"] == 2 and result["inferred_evaluations"] == 1
        state = await db_session.scalar(
            select(WafDetectorState).where(
                WafDetectorState.waf_endpoint_id == login.id,
                WafDetectorState.detector_type == WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
            )
        )
        assert state.last_interval_start == at and state.state == AnomalyState.NORMAL

    async def test_absent_from_a_partial_bucket_is_never_zero(self, db_session) -> None:
        tracked = [("/portal/login", "POST", 100), ("/other", "GET", 100)]
        settings, provider, profile, _source = await prime(db_session, tracked)
        at = T0 + STEP * 6
        # Low coverage, not truncation: still a lower-bound bucket.
        provider.batches[at] = batch(at, [("/other", "GET", 100)], total=1000)
        await run_at(db_session, profile, provider, settings, at)
        assert await state_of(db_session, "/portal/login") == AnomalyState.INSUFFICIENT_DATA
        cell = await db_session.scalar(
            select(WafEndpointBaseline).join(WafEndpoint)
            .where(WafEndpoint.normalized_endpoint == "/portal/login")
        )
        assert 0.0 not in cell.observations and cell.exclusion_counts["incomplete"] == 1

    async def test_absent_during_maintenance_is_suppressed_and_never_trains(
        self, db_session
    ) -> None:
        tracked = [("/portal/login", "POST", 100), ("/other", "GET", 100)]
        settings, provider, profile, source = await prime(db_session, tracked)
        source.maintenance_mode = True
        source.maintenance_until = None
        await db_session.commit()
        at = T0 + STEP * 6
        provider.batches[at] = batch(at, [("/other", "GET", 100)])
        await run_at(db_session, profile, provider, settings, at)
        assert await state_of(db_session, "/portal/login") == AnomalyState.SUPPRESSED
        cell = await db_session.scalar(
            select(WafEndpointBaseline).join(WafEndpoint)
            .where(WafEndpoint.normalized_endpoint == "/portal/login")
        )
        assert cell.exclusion_counts["maintenance"] == 1 and 0.0 not in cell.observations

    async def test_an_endpoint_that_goes_silent_trains_zeros_and_its_incident_resolves(
        self, db_session
    ) -> None:
        tracked = [("/portal/login", "POST", 100), ("/other", "GET", 100)]
        settings, provider, profile, _source = await prime(db_session, tracked)
        spike = T0 + STEP * 6
        provider.batches[spike] = batch(
            spike, [("/portal/login", "POST", 900), ("/other", "GET", 100)]
        )
        await run_at(db_session, profile, provider, settings, spike)
        incident = await db_session.scalar(select(WafAnomaly))
        assert incident.state == AnomalyState.OPEN

        for i in range(7, 12):  # login receives nothing at all
            at = T0 + STEP * i
            provider.batches[at] = batch(at, [("/other", "GET", 100)])
            await run_at(db_session, profile, provider, settings, at)
            if i == 8:
                # Recovery ran on reconstructed zeros: no row was ever written.
                await db_session.refresh(incident)
                assert incident.state == AnomalyState.RESOLVED

        cell = await db_session.scalar(
            select(WafEndpointBaseline).join(WafEndpoint)
            .where(WafEndpoint.normalized_endpoint == "/portal/login")
        )
        # Silent buckets after the incident's span are measured zeros and train
        # as such; those inside it are excluded like any incident bucket.
        assert cell.observations.count(0.0) == 2
        assert cell.exclusion_counts["known_anomaly"] == 4

    async def test_a_failed_collection_infers_nothing(self, db_session) -> None:
        tracked = [("/portal/login", "POST", 100)]
        settings, provider, profile, _source = await prime(db_session, tracked)
        before = await db_session.scalar(select(WafDetectorState))
        last = before.last_interval_start
        provider.error = ProviderUnavailableError("QRadar request timed out")
        result = await run_at(db_session, profile.id, provider, settings, T0 + STEP * 6)
        assert result["status"] == "failed"
        assert await count(db_session, WafSourceBucket) == 6
        state = await db_session.scalar(
            select(WafDetectorState).execution_options(populate_existing=True)
        )
        assert state.last_interval_start == last

    async def test_retirement_ends_zero_inference_and_reactivation_restarts_it(
        self, db_session
    ) -> None:
        settings = cfg(waf_endpoint_active_days=1, waf_max_groups_per_bucket=CAP)
        tracked = [("/portal/login", "POST", 100), ("/other", "GET", 100)]
        settings, provider, profile, source = await prime(db_session, tracked, settings=settings)
        # The window of a gap is one cadence plus the backfill bound. This test
        # pins the bound at its 5-minute-cadence value (1 + backfill).
        await SearchCadenceService(db_session, settings).set_cadence(
            SearchFamily.WAF_COLLECTION, 300, actor="waf-resilience-test", source_ip=None
        )
        login = await endpoint_named(db_session, "/portal/login")
        login_id = login.id

        # Two days later, only /other is requested: login retires at the end of
        # this run's collection, after the buckets it was still tracked for.
        later = T0 + timedelta(days=2)
        for i in range(13):
            at = T0 + STEP * (6 + i)
            provider.batches[at] = batch(at, [("/other", "GET", 100)])
        await run_waf_at(db_session, profile, provider, settings, later)
        login = await db_session.get(WafEndpoint, login_id, populate_existing=True)
        assert not login.is_active and login.tracked_until is not None
        tracked_until = login.tracked_until
        state = await db_session.scalar(
            select(WafDetectorState).where(WafDetectorState.waf_endpoint_id == login_id)
            .execution_options(populate_existing=True)
        )
        # It was judged through its last tracked bucket, and not beyond it.
        assert state.last_interval_start == tracked_until

        # The next bucket, collected after retirement, does not judge it.
        await run_waf_at(db_session, profile, provider, settings, later + STEP)
        state = await db_session.scalar(
            select(WafDetectorState).where(WafDetectorState.waf_endpoint_id == login_id)
            .execution_options(populate_existing=True)
        )
        assert state.last_interval_start == tracked_until
        # Nor does a rebuild read its absence after `tracked_until` as zero:
        # buckets 0..18 are candidates, the thirteen empty buckets after are not.
        semantics = await db_session.scalar(select(WafSourceBucket.metric_semantics).limit(1))
        await WafBaselineBuilder(db_session, settings=settings).rebuild_for_endpoint(
            login, semantics=semantics, now=later + STEP * 20, log_source=source
        )
        cells = (await db_session.scalars(
            select(WafEndpointBaseline).where(WafEndpointBaseline.waf_endpoint_id == login_id)
        )).all()
        candidates = sum(c.sample_count for c in cells) + cells[0].excluded_sample_count
        assert candidates == 19
        assert await count(db_session, WafSourceBucket) == 32

        # Requested again: reactivated with a fresh tracking period and a
        # rebuild queued first.
        wm_bucket = await db_session.scalar(
            select(WafSourceBucket.bucket_start).order_by(WafSourceBucket.bucket_start.desc()).limit(1)
        )
        back = wm_bucket + STEP
        provider.batches[back] = batch(
            back, [("/portal/login", "POST", 50), ("/other", "GET", 100)]
        )
        await run_waf_at(db_session, profile, provider, settings, back + STEP)
        login = await db_session.get(WafEndpoint, login_id, populate_existing=True)
        assert login.is_active and login.tracked_since == back and login.tracked_until is None
        assert login.first_seen_at == T0  # novelty keeps the first sighting

    async def test_rebuild_across_sparse_history_matches_the_dense_series(self, db_session) -> None:
        # No detection: an incident would exclude buckets and hide the comparison.
        settings = cfg(baseline_min_samples=1, waf_min_absolute_delta_requests=100000)
        pattern = [40, 0, 0, 25, 0, 60, 0, 0, 0, 35, 0, 50]  # intermittent
        provider = RecordingProvider([
            batch(T0 + STEP * i, [("/intermittent", "GET", c), ("/steady", "GET", 100)] if c
                  else [("/steady", "GET", 100)])
            for i, c in enumerate(pattern)
        ])
        profile, source = await source_with_profile(db_session, provider, settings)
        for i in range(len(pattern)):
            await run_at(db_session, profile, provider, settings, T0 + STEP * i)

        endpoint = await endpoint_named(db_session, "/intermittent")
        semantics = await db_session.scalar(select(WafSourceBucket.metric_semantics).limit(1))
        await WafBaselineBuilder(db_session, settings=settings).rebuild_for_endpoint(
            endpoint, semantics=semantics, now=T0 + STEP * len(pattern), log_source=source
        )
        cell = await db_session.scalar(
            select(WafEndpointBaseline).where(WafEndpointBaseline.waf_endpoint_id == endpoint.id)
        )
        # The dense series B.1 would have stored: every bucket from first sighting on.
        dense = [float(c) for c in pattern]
        assert cell.sample_count == len(dense)
        assert sorted(cell.observations) == sorted(dense)
        assert await count(db_session, WafEndpointMetric,
                           WafEndpointMetric.waf_endpoint_id == endpoint.id) == 5

    async def test_retention_after_sparse_storage(self, db_session) -> None:
        tracked = [("/portal/login", "POST", 100), ("/other", "GET", 100)]
        settings, provider, profile, source = await prime(db_session, tracked)
        at = T0 + STEP * 6
        provider.batches[at] = batch(at, [("/other", "GET", 100)])
        await run_at(db_session, profile, provider, settings, at)

        now = T0 + timedelta(days=settings.waf_metric_retention_days, minutes=40)
        report = await WafRetention(db_session, settings=settings, clock=lambda: now).run()
        assert report.deleted["waf_endpoint_metric"] == 6 * 2 + 1
        # Every bucket record went with its rows, so no stale record is left
        # to vouch for an absence.
        assert await count(db_session, WafSourceBucket) == 0
        assert await count(db_session, WafEndpointMetric) == 0
        builder = WafBaselineBuilder(db_session, settings=settings)
        facts = await builder.source_bucket_facts(source.id, T0 - STEP, now)
        assert facts == {}


async def run_waf_at(session, profile, provider, settings, now):  # type: ignore[no-untyped-def]
    """A run whose clock is `now` itself (for runs far from the primed buckets)."""
    from app.services.waf_pipeline import run_waf_source

    return await run_waf_source(
        session, profile.id if hasattr(profile, "id") else profile,
        provider_factory=lambda _i: provider, settings=settings,
        clock=lambda: now + timedelta(seconds=5),
    )


async def test_status_distinguishes_lower_bound_buckets_and_discovery(db_session) -> None:
    tracked = [("/portal/login", "POST", 100)]
    settings, provider, profile, _source = await prime(db_session, tracked)
    at = T0 + STEP * 6
    provider.batches[at] = capped(at, [("/portal/login", "POST", 100), *spray(1000, 2)])
    await run_at(db_session, profile, provider, settings, at)
    (status,) = await waf_source_statuses(db_session, settings=settings, now=at + STEP)
    last = status.last_bucket
    assert last is not None
    assert last.request_count_measurement == "LOWER_BOUND"
    assert last.groups_truncated and not last.discovery_complete
    assert last.coverage_measurement == "LOWER_BOUND"
    assert last.sparse_endpoint_rows
    assert status.lower_bound_buckets_24h == 1
    assert "/spray/" not in status.model_dump_json()
