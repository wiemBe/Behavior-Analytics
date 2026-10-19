"""Phase B.2 ratio and concentration detectors through the persisted engine path."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    WafAnomalyType,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.waf import (
    WafAnomaly,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.waf.baseline import (
    BLOCK_RATIO_METRIC,
    CLIENT_ERROR_RATIO_METRIC,
    SERVER_ERROR_RATIO_METRIC,
    TOP_CLIENT_SHARE_METRIC,
)
from app.waf.engine import WafAnomalyEngine
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import breakdown_semantics, request_count_semantics
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_waf_productionization import STEP, T0, cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REQUEST_SEMANTICS = request_count_semantics(
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)
ACTION_SEMANTICS = breakdown_semantics(
    "ACTION",
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)
RESPONSE_SEMANTICS = breakdown_semantics(
    "RESPONSE_CODE",
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)
CLIENT_SEMANTICS = breakdown_semantics(
    "SOURCE_IP",
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)


def behavior_settings(**overrides):  # type: ignore[no-untyped-def]
    values = {
        "anomaly_open_after_intervals": 2,
        "waf_outcome_collection_enabled": True,
        "waf_client_collection_enabled": True,
        "waf_ratio_min_denominator": 10,
        "waf_ratio_min_numerator": 2,
        "waf_ratio_min_increase": 0.10,
        "waf_ratio_min_multiple": 2.0,
        "waf_concentration_min_endpoint_requests": 10,
        "waf_concentration_min_client_requests": 2,
        "waf_concentration_min_increase": 0.20,
    }
    values.update(overrides)
    return cfg(**values)


async def seed_endpoint(session):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    source = await make_log_source(session, instance, qradar_id=312, name="waf-b2")
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host="portal.example",
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=T0 - timedelta(days=7),
        last_seen_at=T0,
        tracked_since=T0 - timedelta(days=7),
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    return source, endpoint


def add_cell(session, endpoint, metric: str, semantics: str) -> None:  # type: ignore[no-untyped-def]
    session.add(
        WafEndpointBaseline(
            waf_endpoint_id=endpoint.id,
            metric_name=metric,
            weekday=T0.isoweekday(),
            hour=T0.hour,
            median=0.10,
            mad=0.01,
            p05=0.08,
            p95=0.12,
            sample_count=12,
            is_reliable=True,
            baseline_version=4,
            normalizer_version=ENDPOINT_NORMALIZER_VERSION,
            observations=[0.10] * 12,
            completeness=1.0,
            numerator_sum=100.0,
            denominator_sum=1000.0,
            excluded_sample_count=0,
            exclusion_counts={},
            metric_semantics=semantics,
        )
    )


async def add_observation(
    session,
    endpoint,
    at,
    *,
    action_status=WafTelemetryStatus.NOT_COLLECTED,
    action_semantics: str | None = None,
    response_status=WafTelemetryStatus.NOT_COLLECTED,
    response_semantics: str | None = None,
    client_status=WafTelemetryStatus.NOT_COLLECTED,
    client_semantics: str | None = None,
    blocked: int | None = None,
    action_events: int | None = None,
    unmapped_actions: int | None = None,
    client_errors: int | None = None,
    server_errors: int | None = None,
    response_events: int | None = None,
    unclassified_responses: int | None = None,
    client_events: int | None = None,
    top1: int | None = None,
    top5: int | None = None,
):  # type: ignore[no-untyped-def]
    bucket = WafSourceBucket(
        log_source_id=endpoint.log_source_id,
        bucket_start=at,
        bucket_seconds=300,
        origin="LIVE",
        profile_version=1,
        metric_semantics=REQUEST_SEMANTICS,
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0,
        total_events=100,
        parsed_events=100,
        group_count=1,
        groups_truncated=False,
        unusable_groups=0,
        observed_endpoints=1,
        tracked_endpoints_observed=1,
        admitted_endpoints=0,
        below_admission_threshold=0,
        rejected_over_quota=0,
        untracked_endpoints=0,
        untracked_requests=0,
        admission_truncated=False,
        zero_filled_endpoints=0,
        request_count_measurement=WafMeasurement.EXACT,
        sparse_endpoint_rows=True,
        in_maintenance=False,
        action_measurement=action_status,
        action_semantics=action_semantics,
        action_events=action_events,
        response_code_measurement=response_status,
        response_code_semantics=response_semantics,
        response_code_events=response_events,
        client_measurement=client_status,
        client_semantics=client_semantics,
        client_events=client_events,
        collection_source="TEST",
        query_provenance={},
    )
    metric = WafEndpointMetric(
        waf_endpoint_id=endpoint.id,
        bucket_start=at,
        bucket_seconds=300,
        request_count=100,
        distinct_client_count=5 if client_events is not None else None,
        blocked_count=blocked,
        action_events=action_events,
        unmapped_action_count=unmapped_actions,
        response_code_events=response_events,
        client_error_count=client_errors,
        server_error_count=server_errors,
        unclassified_response_count=unclassified_responses,
        client_events=client_events,
        top1_client_requests=top1,
        top5_client_requests=top5,
        client_sources=5 if client_events is not None else None,
        top_clients=(
            [{"source_ip": "192.0.2.10", "requests": top1}] if top1 is not None else None
        ),
        endpoint_coverage_ratio=1.0,
        completeness=BucketCompleteness.COMPLETE,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    )
    session.add_all([bucket, metric])
    await session.flush()
    return bucket, metric


@dataclass(frozen=True)
class RatioCase:
    detector_type: WafAnomalyType
    metric: str
    semantics: str
    count_field: str
    denominator_field: str
    status_field: str
    semantics_field: str


RATIO_CASES = (
    RatioCase(
        WafAnomalyType.WAF_BLOCK_RATIO_SPIKE,
        BLOCK_RATIO_METRIC,
        ACTION_SEMANTICS,
        "blocked",
        "action_events",
        "action_status",
        "action_semantics",
    ),
    RatioCase(
        WafAnomalyType.WAF_ERROR_RATIO_SPIKE,
        SERVER_ERROR_RATIO_METRIC,
        RESPONSE_SEMANTICS,
        "server_errors",
        "response_events",
        "response_status",
        "response_semantics",
    ),
    RatioCase(
        WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE,
        CLIENT_ERROR_RATIO_METRIC,
        RESPONSE_SEMANTICS,
        "client_errors",
        "response_events",
        "response_status",
        "response_semantics",
    ),
)


@pytest.mark.parametrize("case", RATIO_CASES, ids=lambda case: case.metric)
async def test_ratio_detector_opens_and_persists_evidence(db_session, case: RatioCase) -> None:
    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, case.metric, case.semantics)
    engine = WafAnomalyEngine(db_session, settings=settings)

    for index, numerator in enumerate((60, 80)):
        kwargs = {
            case.status_field: WafTelemetryStatus.EXACT,
            case.semantics_field: case.semantics,
            case.count_field: numerator,
            case.denominator_field: 100,
        }
        bucket, metric = await add_observation(
            db_session, endpoint, T0 + STEP * index, **kwargs
        )
        report = await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)

    assert report.states[case.detector_type.value] == AnomalyState.OPEN.value
    anomaly = await db_session.scalar(
        select(WafAnomaly).where(WafAnomaly.detector_type == case.detector_type)
    )
    assert anomaly is not None
    assert anomaly.state == AnomalyState.OPEN
    assert anomaly.observed_value == 0.8
    assert anomaly.expected_value == 0.1
    assert anomaly.baseline_version == 4
    assert anomaly.details["extra"]["observed"]["numerator"] == 80
    assert anomaly.deviation_ratio == anomaly.details["extra"]["ratio"]
    assert anomaly.robust_z == anomaly.details["extra"]["robust_z"]
    assert anomaly.deviation_score == anomaly.details["extra"]["robust_z"]
    assert anomaly.absolute_delta == anomaly.details["extra"]["absolute_delta"]


@pytest.mark.parametrize("case", RATIO_CASES, ids=lambda case: case.metric)
async def test_ratio_rise_in_a_low_source_coverage_bucket_opens_no_incident(
    db_session, case: RatioCase
) -> None:
    # Decision D-3: the breakdown covers every visible request, but only 50% of the
    # source's events parsed to an endpoint, so the visible proportion bounds nothing.
    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, case.metric, case.semantics)
    engine = WafAnomalyEngine(db_session, settings=settings)

    for index, numerator in enumerate((60, 80)):
        kwargs = {
            case.status_field: WafTelemetryStatus.EXACT,
            case.semantics_field: case.semantics,
            case.count_field: numerator,
            case.denominator_field: 100,
        }
        bucket, metric = await add_observation(
            db_session, endpoint, T0 + STEP * index, **kwargs
        )
        bucket.endpoint_coverage_ratio = 0.5
        metric.endpoint_coverage_ratio = 0.5
        await db_session.flush()
        report = await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)

    assert report.states[case.detector_type.value] != AnomalyState.OPEN.value
    assert (
        await db_session.scalar(
            select(WafAnomaly).where(WafAnomaly.detector_type == case.detector_type)
        )
    ) is None
    state = await db_session.scalar(
        select(WafDetectorState).where(
            WafDetectorState.detector_type == case.detector_type.value
        )
    )
    assert state is not None and state.consecutive_anomalous == 0


@pytest.mark.parametrize(
    "status", (WafTelemetryStatus.NOT_COLLECTED, WafTelemetryStatus.FAILED)
)
async def test_unavailable_breakdown_is_insufficient_and_does_not_advance(
    db_session, status: WafTelemetryStatus
) -> None:
    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, BLOCK_RATIO_METRIC, ACTION_SEMANTICS)
    bucket, metric = await add_observation(
        db_session, endpoint, T0, action_status=status, action_semantics=ACTION_SEMANTICS
    )

    report = await WafAnomalyEngine(db_session, settings=settings).evaluate_interval(
        endpoint, metric, source_bucket=bucket
    )
    state = await db_session.scalar(
        select(WafDetectorState).where(
            WafDetectorState.detector_type == WafAnomalyType.WAF_BLOCK_RATIO_SPIKE
        )
    )
    assert report.states[WafAnomalyType.WAF_BLOCK_RATIO_SPIKE.value] == (
        AnomalyState.INSUFFICIENT_DATA.value
    )
    assert state is not None
    assert state.consecutive_anomalous == 0
    assert state.consecutive_healthy == 0
    assert await db_session.scalar(
        select(WafAnomaly).where(
            WafAnomaly.detector_type == WafAnomalyType.WAF_BLOCK_RATIO_SPIKE
        )
    ) is None


async def test_lower_bound_numerator_can_open_but_never_reports_healthy(db_session) -> None:
    settings = behavior_settings(anomaly_open_after_intervals=1)
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, BLOCK_RATIO_METRIC, ACTION_SEMANTICS)
    engine = WafAnomalyEngine(db_session, settings=settings)

    low_bucket, low_metric = await add_observation(
        db_session,
        endpoint,
        T0,
        action_status=WafTelemetryStatus.EXACT,
        action_semantics=ACTION_SEMANTICS,
        blocked=1,
        action_events=100,
        unmapped_actions=1,
    )
    low = await engine.evaluate_interval(endpoint, low_metric, source_bucket=low_bucket)
    assert low.states[WafAnomalyType.WAF_BLOCK_RATIO_SPIKE.value] == (
        AnomalyState.INSUFFICIENT_DATA.value
    )

    high_bucket, high_metric = await add_observation(
        db_session,
        endpoint,
        T0 + STEP,
        action_status=WafTelemetryStatus.EXACT,
        action_semantics=ACTION_SEMANTICS,
        blocked=80,
        action_events=100,
        unmapped_actions=1,
    )
    high = await engine.evaluate_interval(endpoint, high_metric, source_bucket=high_bucket)
    assert high.states[WafAnomalyType.WAF_BLOCK_RATIO_SPIKE.value] == AnomalyState.OPEN.value
    anomaly = await db_session.scalar(
        select(WafAnomaly).where(
            WafAnomaly.detector_type == WafAnomalyType.WAF_BLOCK_RATIO_SPIKE
        )
    )
    assert anomaly is not None
    assert anomaly.observed_measurement == WafMeasurement.LOWER_BOUND
    assert anomaly.details["extra"]["observed_measurement"] == WafMeasurement.LOWER_BOUND


async def test_breakdown_semantics_mismatch_is_insufficient(db_session) -> None:
    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, BLOCK_RATIO_METRIC, ACTION_SEMANTICS)
    bucket, metric = await add_observation(
        db_session,
        endpoint,
        T0,
        action_status=WafTelemetryStatus.EXACT,
        action_semantics=f"{ACTION_SEMANTICS};changed",
        blocked=80,
        action_events=100,
        unmapped_actions=0,
    )

    report = await WafAnomalyEngine(db_session, settings=settings).evaluate_interval(
        endpoint, metric, source_bucket=bucket
    )
    state = await db_session.scalar(
        select(WafDetectorState).where(
            WafDetectorState.detector_type == WafAnomalyType.WAF_BLOCK_RATIO_SPIKE
        )
    )
    assert report.states[WafAnomalyType.WAF_BLOCK_RATIO_SPIKE.value] == (
        AnomalyState.INSUFFICIENT_DATA.value
    )
    assert state is not None and state.consecutive_anomalous == 0


async def test_disabled_b2_detectors_create_no_states(db_session) -> None:
    settings = behavior_settings(
        waf_outcome_collection_enabled=False, waf_client_collection_enabled=False
    )
    _source, endpoint = await seed_endpoint(db_session)
    bucket, metric = await add_observation(db_session, endpoint, T0)

    def disabled_detector(_ctx):  # type: ignore[no-untyped-def]
        raise AssertionError("a disabled injected detector must not be evaluated")

    await WafAnomalyEngine(
        db_session,
        settings=settings,
        detectors={WafAnomalyType.WAF_BLOCK_RATIO_SPIKE: disabled_detector},
    ).evaluate_interval(endpoint, metric, source_bucket=bucket)
    assert not (await db_session.scalars(select(WafDetectorState))).all()

    await WafAnomalyEngine(db_session, settings=settings).evaluate_interval(
        endpoint, metric, source_bucket=bucket
    )
    detector_types = set(
        (await db_session.scalars(select(WafDetectorState.detector_type))).all()
    )
    # New-endpoint access has no enabling setting, so it always runs (T-006b).
    assert detector_types == {
        WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
        WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS.value,
    }


async def test_client_concentration_opens_on_top_client_share_rise(db_session) -> None:
    settings = behavior_settings()
    _source, endpoint = await seed_endpoint(db_session)
    add_cell(db_session, endpoint, TOP_CLIENT_SHARE_METRIC, CLIENT_SEMANTICS)
    engine = WafAnomalyEngine(db_session, settings=settings)

    for index, top1 in enumerate((60, 80)):
        bucket, metric = await add_observation(
            db_session,
            endpoint,
            T0 + STEP * index,
            client_status=WafTelemetryStatus.EXACT,
            client_semantics=CLIENT_SEMANTICS,
            client_events=100,
            top1=top1,
            top5=90,
        )
        report = await engine.evaluate_interval(endpoint, metric, source_bucket=bucket)

    detector = WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE
    assert report.states[detector.value] == AnomalyState.OPEN.value
    anomaly = await db_session.scalar(
        select(WafAnomaly).where(WafAnomaly.detector_type == detector)
    )
    assert anomaly is not None
    assert anomaly.observed_value == 0.8
    assert anomaly.details["extra"]["contributors"]["status"] == "AVAILABLE"
    assert anomaly.details["extra"]["observed"]["top5_share"] == 0.9
