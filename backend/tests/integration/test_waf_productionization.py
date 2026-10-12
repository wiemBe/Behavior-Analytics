"""Phase B.1: WAF collection made schedulable, end to end against PostgreSQL.

Every run below goes through `run_waf_source` -- the function the scheduled
Celery task calls -- with only the provider and the clock substituted.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    SearchFamily,
    WafAnomalyType,
    WafFieldStatus,
    WafLogicalField,
    WafProfileValidationStatus,
)
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import (
    WafAnomaly,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
    WafSourceProfile,
)
from app.providers.base import ProviderUnavailableError
from app.providers.dto import LogSourceDTO, WafObservationBatch, WafRequestGroup
from app.providers.mock import MockQRadarProvider
from app.services.search_cadence import SearchCadenceService
from app.services.waf_pipeline import (
    eligible_profile_ids,
    evaluation_collector,
    run_waf_source,
)
from app.services.waf_profiles import WafProfileService
from app.services.waf_retention import WafRetention
from app.services.waf_status import waf_source_statuses
from app.waf.baseline import WafBaselineBuilder
from app.waf.profile import WafFieldMapping, WafProfileError
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

T0 = datetime(2026, 8, 3, 19, 0, tzinfo=UTC)  # Monday, hour 19
STEP = timedelta(seconds=300)
LAB_MAPPING = WafFieldMapping(
    url="URL",
    method="HTTP_Method",
    action="WAF_Action",
    response_code="WAF_ResponseCode",
    user_agent="WAF_USERAGENT",
)


def word(i: int) -> str:
    """A four-letter segment the normalizer leaves alone.

    Digits would be templated to `{id}` and a-f runs can read as hex, so an
    attack built from either would fold into one endpoint and prove nothing
    about admission. These letters survive normalization verbatim.
    """
    letters = "ghjkmnpqrstvwxyz"
    out = ""
    for _ in range(4):
        out = letters[i % 16] + out
        i //= 16
    return out


def cfg(**overrides) -> Settings:  # type: ignore[no-untyped-def]
    base = get_settings().model_dump()
    base.update(
        {
            "waf_collection_enabled": True,
            "collection_interval_seconds": 300,
            "collection_max_backfill_intervals": 12,
            "baseline_min_samples": 4,
            "anomaly_open_after_intervals": 1,
            "anomaly_resolve_after_intervals": 2,
            "waf_min_endpoint_coverage": 0.9,
            "waf_min_bucket_requests": 20.0,
            "waf_min_absolute_delta_requests": 50.0,
            "waf_spike_ratio": 2.0,
            "waf_max_new_endpoints_per_bucket": 20,
            "waf_max_active_endpoints_per_source": 2000,
            "waf_min_admission_requests": 3,
            "waf_max_groups_per_bucket": 5000,
        }
    )
    base.update(overrides)
    return Settings(**base)


def batch(
    start: datetime,
    groups: list[tuple[str, str, int]],
    *,
    total: int | None = None,
    truncated: bool = False,
) -> WafObservationBatch:
    return WafObservationBatch(
        qradar_log_source_id=0,
        window_start=start,
        window_end=start + STEP,
        groups=[
            WafRequestGroup(raw_url=u, raw_method=m, request_count=c, record_count=c,
                            distinct_client_count=3)
            for u, m, c in groups
        ],
        total_events=total if total is not None else sum(c for _, _, c in groups),
        truncated=truncated,
        query="STUB GROUPED",
        total_query="STUB TOTAL",
    )


class StubWafProvider(MockQRadarProvider):
    """Mock QRadar for WAF: batches by window, lab property names, known sources."""

    event_count_aggregation = "SUM(eventcount)"

    def __init__(
        self,
        batches: list[WafObservationBatch] | None = None,
        *,
        error: Exception | None = None,
        qradar_ids: frozenset[int] = frozenset({312, 313}),
    ) -> None:
        super().__init__(seed=1337)
        self.batches = {b.window_start: b for b in batches or []}
        self.error = error
        self.qradar_ids = qradar_ids
        self.observation_calls = 0
        self.probe_calls = 0

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        if qradar_id in self.qradar_ids:
            return LogSourceDTO(qradar_id=qradar_id, name=f"waf-{qradar_id}")
        return None

    async def get_waf_observations(  # type: ignore[no-untyped-def, override]
        self, **kw
    ) -> WafObservationBatch:
        self.observation_calls += 1
        if self.error is not None:
            raise self.error
        found = self.batches.get(kw["window_start"])
        if found is None:
            found = batch(kw["window_start"], [])
        return found.model_copy(update={"qradar_log_source_id": kw["qradar_log_source_id"]})

    async def probe_waf_properties(self, **kw):  # type: ignore[no-untyped-def, override]
        self.probe_calls += 1
        return await super().probe_waf_properties(**kw)


async def source_with_profile(
    session: AsyncSession, provider: StubWafProvider, settings: Settings, *,
    qradar_id: int = 312,
    instance: QRadarInstance | None = None,
    mapping: WafFieldMapping = LAB_MAPPING,
    enable: bool = True,
) -> tuple[WafSourceProfile, LogSource]:
    instance = instance or await make_instance(session)
    source = await make_log_source(session, instance, qradar_id=qradar_id, name=f"waf-{qradar_id}")
    service = WafProfileService(session, settings=settings, clock=lambda: T0)
    profile, _ = await service.upsert(log_source=source, mapping=mapping, actor="tester")
    result = await service.validate(profile, provider, actor="tester")
    if enable:
        assert result.status is WafProfileValidationStatus.VALID, result.reasons
        await service.set_enabled(profile, True, actor="tester")
    await session.commit()
    return profile, source


async def run_at(session, profile, provider, settings, start):  # type: ignore[no-untyped-def]
    """One scheduled run whose clock sits just after `start`'s bucket closed.

    Accepts a profile id as well as a profile: a failed run rolls the shared test
    session back, which expires every loaded object, and touching one afterwards
    would lazy-load outside the async context.
    """
    profile_id = profile if isinstance(profile, uuid.UUID) else profile.id
    return await run_waf_source(
        session, profile_id, provider_factory=lambda _i: provider,
        settings=settings, clock=lambda: start + STEP + timedelta(seconds=5),
    )


async def count(session: AsyncSession, model, *where) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(model).where(*where)) or 0)


# ============================================================ profiles
class TestProfiles:
    async def test_lifecycle_is_versioned_and_audited(self, db_session: AsyncSession) -> None:
        settings = cfg()
        provider = StubWafProvider()
        profile, source = await source_with_profile(db_session, provider, settings, enable=False)
        service = WafProfileService(db_session, settings=settings, clock=lambda: T0)

        assert profile.profile_version == 1
        assert profile.validation_status == WafProfileValidationStatus.VALID
        await service.set_enabled(profile, True, actor="tester")

        changed = WafFieldMapping(url="URL", method="HTTP_Method")  # optional fields dropped
        _, did_change = await service.upsert(log_source=source, mapping=changed, actor="admin2")
        assert did_change
        assert profile.profile_version == 2
        assert profile.validation_status == WafProfileValidationStatus.UNVALIDATED
        # Still flagged enabled, but a changed mapping must not collect until revalidated.
        assert await eligible_profile_ids(db_session, settings) == (
            [], {"profile validation is UNVALIDATED": 1}
        )

        actions = [
            a.action for a in (await db_session.scalars(
                select(AuditLog).order_by(AuditLog.created_at, AuditLog.action)
            )).all()
        ]
        assert {"waf.profile.created", "waf.profile.validated", "waf.profile.enabled",
                "waf.profile.mapping_changed"} <= set(actions)
        change = await db_session.scalar(
            select(AuditLog).where(AuditLog.action == "waf.profile.mapping_changed")
        )
        assert change.detail["mapping_before"]["ACTION"] == "WAF_Action"
        assert change.detail["mapping_after"]["ACTION"] is None
        assert change.actor == "admin2"

    async def test_an_unmapped_field_is_reported_unavailable(self, db_session) -> None:
        settings = cfg()
        profile, _ = await source_with_profile(db_session, StubWafProvider(), settings)
        fields = {f["field"]: f for f in profile.validation_report["fields"]}
        assert fields["VIRTUAL_HOST"]["status"] == WafFieldStatus.NOT_MAPPED.value
        assert fields["VIRTUAL_HOST"]["populated_events"] is None
        assert fields["RULE_ID"]["status"] == WafFieldStatus.NOT_MAPPED.value
        assert fields["URL"]["status"] == WafFieldStatus.AVAILABLE.value

    async def test_a_wrong_property_name_fails_validation_and_blocks_collection(
        self, db_session
    ) -> None:
        settings = cfg()
        provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40)])])
        wrong = WafFieldMapping(url="WAF Request URL", method="HTTP_Method")
        profile, _ = await source_with_profile(
            db_session, provider, settings, mapping=wrong, enable=False
        )
        assert profile.validation_status == WafProfileValidationStatus.INVALID
        fields = {f["field"]: f["status"] for f in profile.validation_report["fields"]}
        assert fields["URL"] == WafFieldStatus.PROPERTY_REJECTED.value

        service = WafProfileService(db_session, settings=settings)
        with pytest.raises(WafProfileError):
            await service.set_enabled(profile, True, actor="tester")
        # Even forced on, a scheduled run refuses it.
        profile.enabled = True
        await db_session.commit()
        result = await run_at(db_session, profile, provider, settings, T0)
        assert result["status"] == "skipped"
        assert "INVALID" in result["reason"]
        assert provider.observation_calls == 0

    async def test_a_source_qradar_does_not_know_is_invalid_and_not_probed(
        self, db_session
    ) -> None:
        settings = cfg()
        provider = StubWafProvider(qradar_ids=frozenset())
        profile, _ = await source_with_profile(db_session, provider, settings, enable=False)
        assert profile.validation_status == WafProfileValidationStatus.INVALID
        assert provider.probe_calls == 0


# ============================================================ scheduling
class TestScheduling:
    async def test_two_sources_collect_independently(self, db_session) -> None:
        settings = cfg()
        instance = await make_instance(db_session)
        pa = StubWafProvider([batch(T0, [("/portal/login", "POST", 40)])])
        pb = StubWafProvider([batch(T0, [("/api/v1/status", "GET", 60)])])
        a, sa = await source_with_profile(
            db_session, pa, settings, qradar_id=312, instance=instance
        )
        b, sb = await source_with_profile(
            db_session, pb, settings, qradar_id=313, instance=instance
        )

        assert [r["status"] for r in (
            await run_at(db_session, a, pa, settings, T0),
            await run_at(db_session, b, pb, settings, T0),
        )] == ["ok", "ok"]
        for source in (sa, sb):
            owned = WafSourceBucket.log_source_id == source.id
            assert await count(db_session, WafSourceBucket, owned) == 1
        eligible, _ = await eligible_profile_ids(db_session, settings)
        assert set(eligible) == {a.id, b.id}

    async def test_one_failing_source_does_not_stop_another(self, db_session) -> None:
        settings = cfg()
        instance = await make_instance(db_session)
        ok_provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40)])])
        failing = StubWafProvider(error=ProviderUnavailableError("QRadar request timed out"))
        good, good_source = await source_with_profile(
            db_session, ok_provider, settings, qradar_id=312, instance=instance
        )
        bad, bad_source = await source_with_profile(
            db_session, failing, settings, qradar_id=313, instance=instance
        )

        good_id, bad_id = good.id, bad.id
        good_source_id, bad_source_id = good_source.id, bad_source.id
        bad_result = await run_at(db_session, bad_id, failing, settings, T0)
        good_result = await run_at(db_session, good_id, ok_provider, settings, T0)
        assert bad_result["status"] == "failed"
        assert good_result["status"] == "ok"

        # The failure manufactured nothing: no bucket, no metric, watermark held.
        owner = WafSourceBucket.log_source_id
        assert await count(db_session, WafSourceBucket, owner == bad_source_id) == 0
        assert await count(db_session, WafSourceBucket, owner == good_source_id) == 1
        statuses = {s.qradar_log_source_id: s for s in await waf_source_statuses(
            db_session, settings=settings, now=T0 + timedelta(minutes=10)
        )}
        assert statuses[313].consecutive_failures == 1
        assert statuses[313].last_error == "ProviderUnavailableError: QRadar request timed out"
        assert statuses[313].collection_watermark_at is None
        assert statuses[313].last_failure_at is not None
        assert statuses[312].consecutive_failures == 0
        assert statuses[312].lag_seconds == 300

    async def test_reruns_and_duplicate_delivery_are_idempotent(self, db_session) -> None:
        settings = cfg()
        provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40)])])
        profile, _source = await source_with_profile(db_session, provider, settings)
        for _ in range(3):
            await run_at(db_session, profile, provider, settings, T0)
        assert await count(db_session, WafEndpointMetric) == 1
        assert await count(db_session, WafSourceBucket) == 1

        # Force a re-collection of the same bucket, as an operator reset would.
        wm = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == "waf_endpoint_metric:312"
            )
        )
        wm.watermark_at = None
        await db_session.commit()
        await run_at(db_session, profile, provider, settings, T0)
        assert await count(db_session, WafEndpointMetric) == 1
        state = await db_session.scalar(select(WafDetectorState))
        assert state.last_interval_start == T0

    async def test_evaluation_never_runs_ahead_of_collection(self, db_session) -> None:
        settings = cfg(waf_max_evaluation_buckets_per_run=1)
        starts = [T0 + STEP * i for i in range(3)]
        provider = StubWafProvider([batch(s, [("/portal/login", "GET", 40)]) for s in starts])
        profile, _source = await source_with_profile(db_session, provider, settings)
        # At a 5-minute cadence a run is one bucket, so the configured cap applies as set.
        await SearchCadenceService(db_session, settings).set_cadence(
            SearchFamily.WAF_COLLECTION, 300, actor="waf-test", source_ip=None
        )
        # First run: one bucket collected and evaluated.
        await run_at(db_session, profile, provider, settings, starts[0])
        # A run two buckets later collects two, but may evaluate only one.
        result = await run_at(db_session, profile, provider, settings, starts[2])
        assert result["intervals_collected"] == 2
        assert result["evaluated_buckets"] == 1
        assert result["evaluated_through"] == starts[1].isoformat()
        # Training is bounded by what was evaluated, not by what was collected.
        assert result["baseline_train_through"] == (starts[1] + STEP).isoformat()


# ============================================================ cardinality
class TestAdmission:
    async def test_numeric_enumeration_folds_before_admission_is_needed(
        self, db_session
    ) -> None:
        settings = cfg()
        ids = [(f"/users/{i}", "GET", 5) for i in range(5000)]
        provider = StubWafProvider([batch(T0, ids)])
        profile, _ = await source_with_profile(db_session, provider, settings)
        await run_at(db_session, profile, provider, settings, T0)
        assert await count(db_session, WafEndpoint) == 1
        row = await db_session.scalar(select(WafEndpointMetric))
        assert row.request_count == 25000

    async def test_a_path_enumeration_attack_is_bounded_and_recorded(self, db_session) -> None:
        settings = cfg()
        attack = [(f"/scan/{word(i)}", "GET", 5 + (i % 7)) for i in range(5000)]
        provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40), *attack])])
        profile, _source = await source_with_profile(db_session, provider, settings)
        await run_at(db_session, profile, provider, settings, T0)

        assert await count(db_session, WafEndpoint) == 20
        bucket = await db_session.scalar(select(WafSourceBucket))
        assert bucket.observed_endpoints == 5001
        assert bucket.admitted_endpoints == 20
        assert bucket.untracked_endpoints == 4981
        assert bucket.admission_truncated is True
        assert bucket.untracked_requests > 0
        # Admission truncation does not blind the bucket.
        assert bucket.completeness == BucketCompleteness.COMPLETE
        # No refused path is stored anywhere.
        admitted = set((await db_session.scalars(select(WafEndpoint.normalized_endpoint))).all())
        stored = json.dumps(bucket.query_provenance)
        refused = [p for p, _, _ in attack if p not in admitted]
        assert refused and all(p not in stored for p in refused[:50])
        # The endpoint with the most traffic won a slot.
        assert "/portal/login" in admitted

    async def test_admission_is_deterministic_across_arrival_order(self, db_session) -> None:
        settings = cfg()
        instance = await make_instance(db_session)
        groups = [(f"/p/{word(i)}", "GET", 10 + (i % 3)) for i in range(300)]
        pa = StubWafProvider([batch(T0, groups)])
        pb = StubWafProvider([batch(T0, list(reversed(groups)))])
        a, sa = await source_with_profile(
            db_session, pa, settings, qradar_id=312, instance=instance
        )
        b, sb = await source_with_profile(
            db_session, pb, settings, qradar_id=313, instance=instance
        )
        await run_at(db_session, a, pa, settings, T0)
        await run_at(db_session, b, pb, settings, T0)

        async def names(source: LogSource) -> list[str]:
            return sorted((await db_session.scalars(
                select(WafEndpoint.normalized_endpoint).where(
                    WafEndpoint.log_source_id == source.id
                )
            )).all())

        assert await names(sa) == await names(sb)
        assert len(await names(sa)) == 20

    async def test_tracked_endpoints_survive_an_exhausted_quota(self, db_session) -> None:
        settings = cfg(waf_max_active_endpoints_per_source=1)
        second = T0 + STEP
        flood = [(f"/flood/{word(i)}", "GET", 500) for i in range(2000)]
        provider = StubWafProvider([
            batch(T0, [("/portal/login", "GET", 40)]),
            batch(second, [("/portal/login", "GET", 5), *flood]),
        ])
        profile, _source = await source_with_profile(db_session, provider, settings)
        await run_at(db_session, profile, provider, settings, T0)
        await run_at(db_session, profile, provider, settings, second)

        endpoint = await db_session.scalar(select(WafEndpoint))
        assert await count(db_session, WafEndpoint) == 1
        row = await db_session.scalar(
            select(WafEndpointMetric).where(WafEndpointMetric.bucket_start == second)
        )
        assert row.waf_endpoint_id == endpoint.id and row.request_count == 5
        bucket = await db_session.scalar(
            select(WafSourceBucket).where(WafSourceBucket.bucket_start == second)
        )
        assert bucket.tracked_endpoints_observed == 1
        assert bucket.rejected_over_quota == 2000
        assert bucket.admission_truncated is True


# ============================================================ detection order
NORMAL = [100, 104, 98, 102, 101, 99, 103, 97]


async def run_series(session, profile, provider, settings, counts, *, start=T0):
    results = []
    for i, _ in enumerate(counts):
        results.append(await run_at(session, profile, provider, settings, start + STEP * i))
    return results


def series(counts: list[int], *, start: datetime = T0, path: str = "/portal/login"):
    return [batch(start + STEP * i, [(path, "GET", c)]) for i, c in enumerate(counts)]


class TestDetectionPipeline:
    async def test_a_spike_is_judged_before_it_can_train_the_baseline(self, db_session) -> None:
        settings = cfg()
        counts = [*NORMAL[:6], 900]
        provider = StubWafProvider(series(counts))
        profile, _ = await source_with_profile(db_session, provider, settings)
        await run_series(db_session, profile, provider, settings, counts)

        anomaly = await db_session.scalar(select(WafAnomaly))
        assert anomaly is not None
        assert anomaly.detector_type == WafAnomalyType.WAF_REQUEST_RATE_SPIKE
        assert anomaly.state == AnomalyState.OPEN
        assert anomaly.observed_value == 900
        cell = await db_session.scalar(select(WafEndpointBaseline))
        assert 900.0 not in cell.observations
        assert cell.exclusion_counts["known_anomaly"] >= 1

    async def test_partial_and_missing_buckets_never_train(self, db_session) -> None:
        settings = cfg()
        starts = [T0 + STEP * i for i in range(8)]
        batches = [
            batch(s, [("/portal/login", "GET", c)])
            for s, c in zip(starts, NORMAL, strict=True)
        ]
        batches[2] = batch(starts[2], [("/portal/login", "GET", 30)], total=300)   # low coverage
        batches[3] = batch(starts[3], [("/portal/login", "GET", 5000)], truncated=True)
        provider = StubWafProvider(batches)
        profile, _ = await source_with_profile(db_session, provider, settings)
        profile_id = profile.id
        for i, s in enumerate(starts):
            if i == 5:
                # A failed run is a MISSING bucket: nothing written, watermark held.
                provider.error = ProviderUnavailableError("QRadar request timed out")
                failed = await run_at(db_session, profile_id, provider, settings, s)
                assert failed["status"] == "failed"
                provider.error = None
            await run_at(db_session, profile_id, provider, settings, s)

        cell = await db_session.scalar(select(WafEndpointBaseline))
        assert 30.0 not in cell.observations and 5000.0 not in cell.observations
        assert cell.exclusion_counts["incomplete"] == 2
        # The failed run was retried by the next one, not skipped.
        assert await count(db_session, WafSourceBucket) == 8


# ============================================================ semantics
class TestMetricSemantics:
    async def test_a_mapping_change_fails_closed_until_rebuilt(self, db_session) -> None:
        settings = cfg(waf_baseline_endpoints_per_run=0 + 1)
        counts = [*NORMAL[:6], 900]
        provider = StubWafProvider(series(counts))
        profile, source = await source_with_profile(db_session, provider, settings)
        await run_series(db_session, profile, provider, settings, counts[:6])
        old_cell = await db_session.scalar(select(WafEndpointBaseline))
        assert old_cell.is_reliable

        # The operator remaps a property: profile v2, validated again.
        service = WafProfileService(db_session, settings=settings, clock=lambda: T0)
        await service.upsert(
            log_source=source, mapping=WafFieldMapping(url="URL", method="HTTP_Method"), actor="t"
        )
        await service.validate(profile, provider, actor="t")
        await db_session.commit()

        builder_calls: list[str] = []
        original = WafBaselineBuilder.rebuild_endpoints

        async def spy(self, endpoints, **kw):  # type: ignore[no-untyped-def]
            builder_calls.append(kw["semantics"])
            # do not rebuild: prove the detector refuses the old cell
            return {e.id: 0 for e in endpoints}

        WafBaselineBuilder.rebuild_endpoints = spy  # type: ignore[method-assign]
        try:
            await run_at(db_session, profile, provider, settings, T0 + STEP * 6)
        finally:
            WafBaselineBuilder.rebuild_endpoints = original  # type: ignore[method-assign]

        # A 9x spike, judged against a v1 cell under v2 semantics: no verdict.
        assert await count(db_session, WafAnomaly) == 0
        state = await db_session.scalar(select(WafDetectorState))
        assert state.state in (AnomalyState.NORMAL, AnomalyState.INSUFFICIENT_DATA)
        assert builder_calls and all("profile/v2" in s for s in builder_calls)

        # And a real v2 rebuild trains on none of the v1 rows.
        endpoint = await db_session.scalar(select(WafEndpoint))
        builder = WafBaselineBuilder(db_session, settings=settings)
        await builder.rebuild_for_endpoint(
            endpoint, semantics=builder_calls[0], now=T0 + STEP * 7, log_source=source
        )
        cell = await db_session.scalar(select(WafEndpointBaseline))
        assert cell.exclusion_counts["incompatible_semantics"] == 6
        assert cell.metric_semantics == builder_calls[0]

    async def test_rows_without_an_identity_never_train(self, db_session) -> None:
        settings = cfg()
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance, qradar_id=312)
        endpoint = WafEndpoint(
            instance_id=instance.id, log_source_id=source.id, normalized_endpoint="/legacy",
            http_method="GET", normalizer_version=1, first_seen_at=T0, last_seen_at=T0,
        )
        db_session.add(endpoint)
        await db_session.flush()
        for i, c in enumerate(NORMAL):
            db_session.add(WafEndpointMetric(
                waf_endpoint_id=endpoint.id, bucket_start=T0 + STEP * i, bucket_seconds=300,
                request_count=c, endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
            ))
        await db_session.flush()
        written = await WafBaselineBuilder(db_session, settings=settings).rebuild_for_endpoint(
            endpoint, semantics="request_count/v1;agg=SUM(eventcount);normalizer/v1;profile/v1",
            now=T0 + timedelta(hours=1), log_source=source,
        )
        assert written == 0


# ============================================================ maintenance
class TestMaintenance:
    async def _prime(self, db_session, counts):  # type: ignore[no-untyped-def]
        settings = cfg()
        provider = StubWafProvider(series(counts))
        profile, source = await source_with_profile(db_session, provider, settings)
        await run_series(db_session, profile, provider, settings, counts[:6])
        return settings, provider, profile, source

    async def _states(self, db_session, profile, provider, settings, starts):
        out = []
        for s in starts:
            await run_at(db_session, profile, provider, settings, s)
            state = await db_session.scalar(
                select(WafDetectorState).where(
                    WafDetectorState.detector_type == WafAnomalyType.WAF_REQUEST_RATE_SPIKE
                )
            )
            out.append(AnomalyState(state.state))
        return out

    async def test_full_and_partial_overlap_suppress_then_detection_resumes(
        self, db_session
    ) -> None:
        counts = [*NORMAL[:6], 900, 900, 900, 900]
        settings, provider, profile, source = await self._prime(db_session, counts)
        b6, b7, b8, b9 = (T0 + STEP * i for i in range(6, 10))
        # Maintenance ends 60 s into bucket 7: bucket 6 is fully inside, bucket 7
        # partially, bucket 8 is the first complete bucket after it.
        source.maintenance_mode = True
        source.maintenance_until = b7 + timedelta(seconds=60)
        await db_session.commit()

        states = await self._states(db_session, profile, provider, settings, [b6, b7, b8, b9])
        assert states == [
            AnomalyState.SUPPRESSED,  # full bucket in maintenance: no incident
            AnomalyState.SUPPRESSED,  # partial overlap counts as maintenance
            AnomalyState.NORMAL,      # first bucket after: suppression lifted
            AnomalyState.OPEN,        # detection has resumed
        ]
        buckets = {
            b.bucket_start: b.in_maintenance
            for b in (await db_session.scalars(select(WafSourceBucket))).all()
        }
        # Entering maintenance did not erase the history collected before it:
        # six normal buckets plus the lift bucket train; only 6 and 7 are out.
        cell = await db_session.scalar(select(WafEndpointBaseline))
        assert cell.exclusion_counts["maintenance"] == 2
        assert cell.sample_count == 7
        assert (buckets[b6], buckets[b7], buckets[b8], buckets[b9]) == (True, True, False, False)
        anomaly = await db_session.scalar(select(WafAnomaly))
        assert anomaly.anomaly_start == b9

    async def test_clearing_the_flag_does_not_readmit_maintenance_buckets(
        self, db_session
    ) -> None:
        counts = [*NORMAL[:6], 5000, 5000]
        settings, provider, profile, source = await self._prime(db_session, counts)
        source.maintenance_mode = True
        source.maintenance_until = None
        await db_session.commit()
        await run_series(db_session, profile, provider, settings, counts[6:], start=T0 + STEP * 6)
        assert await count(db_session, WafAnomaly) == 0

        # Maintenance ends mid-series and the flag is cleared entirely.
        source.maintenance_mode = False
        await db_session.commit()
        endpoint = await db_session.scalar(select(WafEndpoint))
        builder = WafBaselineBuilder(db_session, settings=settings)
        semantics = (await db_session.scalar(select(WafSourceBucket.metric_semantics).limit(1)))
        await builder.rebuild_for_endpoint(
            endpoint, semantics=semantics, now=T0 + STEP * 8, log_source=source
        )
        cell = await db_session.scalar(select(WafEndpointBaseline))
        assert cell.exclusion_counts["maintenance"] == 2
        assert 5000.0 not in cell.observations


# ============================================================ retention
class TestRetention:
    async def test_retention_is_bounded_and_never_removes_live_incidents(
        self, db_session
    ) -> None:
        now = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)
        old = now - timedelta(days=60)
        settings = cfg(waf_retention_batch_size=100, waf_retention_max_batches=1)
        instance = await make_instance(db_session)
        source = await make_log_source(db_session, instance, qradar_id=312)

        def endpoint(path: str, *, active: bool = True) -> WafEndpoint:
            e = WafEndpoint(
                instance_id=instance.id, log_source_id=source.id, normalized_endpoint=path,
                http_method="GET", normalizer_version=1, first_seen_at=old, last_seen_at=old,
                is_active=active, retired_at=None if active else old,
            )
            db_session.add(e)
            return e

        live = endpoint("/live")
        retired_clean = endpoint("/retired-clean", active=False)
        retired_with_open = endpoint("/retired-open", active=False)
        await db_session.flush()

        for i in range(250):  # old metrics: more than one batch
            db_session.add(WafEndpointMetric(
                waf_endpoint_id=live.id, bucket_start=old + STEP * i, bucket_seconds=300,
                request_count=1, endpoint_coverage_ratio=1.0,
                completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
            ))
        db_session.add(WafEndpointMetric(
            waf_endpoint_id=live.id, bucket_start=now - timedelta(days=1), bucket_seconds=300,
            request_count=1, endpoint_coverage_ratio=1.0,
            completeness=BucketCompleteness.COMPLETE, normalizer_version=1,
        ))

        def anomaly(e: WafEndpoint, state: AnomalyState, ended: datetime) -> WafAnomaly:
            a = WafAnomaly(
                waf_endpoint_id=e.id, detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
                severity="HIGH", state=state, detected_at=ended - timedelta(hours=1),
                anomaly_end=ended, resolved_at=ended if state == AnomalyState.RESOLVED else None,
                normalizer_version=1, details={},
            )
            db_session.add(a)
            return a

        old_resolved = anomaly(live, AnomalyState.RESOLVED, now - timedelta(days=400))
        recent_resolved = anomaly(live, AnomalyState.RESOLVED, now - timedelta(days=10))
        old_open = anomaly(retired_with_open, AnomalyState.OPEN, now - timedelta(days=400))
        await db_session.flush()
        db_session.add(WafDetectorState(
            waf_endpoint_id=retired_with_open.id,
            detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE,
            state=AnomalyState.OPEN, active_anomaly_id=old_open.id, open_anomaly_id=old_open.id,
        ))
        db_session.add(WafEndpointBaseline(
            waf_endpoint_id=live.id, metric_name="request_count", weekday=1, hour=19,
            median=1, mad=0, sample_count=1, is_reliable=False, computed_at=old,
            normalizer_version=1,
        ))
        await db_session.commit()
        ids = (old_resolved.id, recent_resolved.id, old_open.id,
               retired_clean.id, retired_with_open.id)

        first = await WafRetention(db_session, settings=settings, clock=lambda: now).run()
        assert "waf_endpoint_metric" in first.incomplete  # bounded: 100 of 250 this run
        assert first.deleted["waf_endpoint_metric"] == 100
        await WafRetention(db_session, settings=settings, clock=lambda: now).run()
        await WafRetention(db_session, settings=settings, clock=lambda: now).run()

        assert await count(db_session, WafEndpointMetric) == 1  # only the recent row
        assert await count(db_session, WafEndpointBaseline) == 0
        remaining = set((await db_session.scalars(select(WafAnomaly.id))).all())
        assert remaining == {ids[1], ids[2]}  # the old OPEN incident is never removed
        endpoints = set((await db_session.scalars(select(WafEndpoint.id))).all())
        assert ids[3] not in endpoints       # retired, unreferenced: purged
        assert ids[4] in endpoints           # retired but holding an OPEN incident
        assert await count(db_session, WafDetectorState) == 1


# ============================================================ status API
class TestStatus:
    async def test_status_reports_operations_without_request_values(self, db_session) -> None:
        settings = cfg()
        attack = [(f"/scan/{word(i)}", "GET", 9) for i in range(100)]
        provider = StubWafProvider([batch(T0, [("/portal/login?session=abc", "GET", 40), *attack])])
        profile, _ = await source_with_profile(db_session, provider, settings)
        await run_at(db_session, profile, provider, settings, T0)

        (status,) = await waf_source_statuses(db_session, settings=settings, now=T0 + STEP * 2)
        assert status.collection_eligible and status.baseline_active and status.detector_active
        assert status.validation_status == "VALID"
        assert status.collection_watermark_at == T0
        assert status.evaluated_through == T0
        assert status.pending_evaluation_buckets == 0
        assert status.last_bucket is not None and status.last_bucket.admission_truncated
        assert status.admission_truncated_buckets_24h == 1
        assert status.active_endpoints == 20
        assert status.lag_seconds == 300
        dumped = status.model_dump_json()
        assert "session=abc" not in dumped and "/scan/" not in dumped

    async def test_the_route_requires_admin_waf(self, db_schema) -> None:
        from fastapi.testclient import TestClient

        from app.main import create_app
        from app.security.auth import get_principal
        from app.security.rbac import Principal

        def client(perms: set[str]) -> TestClient:
            app = create_app()
            app.dependency_overrides[get_principal] = lambda: Principal(
                subject="t", permissions=frozenset(perms)
            )
            return TestClient(app)

        assert client({"read:*"}).get("/api/v1/waf/sources").status_code == 403
        assert client({"read:anomalies"}).get("/api/v1/waf/sources").status_code == 403


async def test_evaluation_watermark_is_per_source(db_session) -> None:
    settings = cfg()
    provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40)])])
    profile, source = await source_with_profile(db_session, provider, settings)
    await run_at(db_session, profile, provider, settings, T0)
    wm = await db_session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.collector == evaluation_collector(source)
        )
    )
    assert wm is not None and wm.watermark_at == T0
    assert (await db_session.execute(text("SELECT 1"))).scalar() == 1
    assert WafLogicalField.URL.value == "URL"
