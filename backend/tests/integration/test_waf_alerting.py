"""WAF incidents drive the generic alert and notification lifecycle."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.dispatcher import NotificationDispatcher
from app.alerts.fingerprint import waf_anomaly_fingerprint
from app.alerts.routing import RoutingPolicy, RoutingRule
from app.anomaly.evidence import AnomalyEvidence
from app.core.config import Settings
from app.models.alert import Alert, AlertNotification
from app.models.enums import (
    AlertStatus,
    AnomalyState,
    BucketCompleteness,
    DetectorSignal,
    NotificationChannel,
    Severity,
    WafAnomalyType,
    WafMeasurement,
)
from app.models.waf import WafEndpoint, WafEndpointBaseline, WafEndpointMetric, WafSourceBucket
from app.services.waf_pipeline import run_waf_source
from app.waf.detector import WafSpikeVerdict, detect_request_rate_spike
from app.waf.engine import WafAnomalyEngine
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import request_count_semantics
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_waf_productionization import (
    NORMAL,
    STEP,
    T0,
    StubWafProvider,
    cfg,
    series,
    source_with_profile,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

SEMANTICS = request_count_semantics(
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)


def alert_settings(**overrides) -> Settings:  # type: ignore[no-untyped-def]
    values = {
        "waf_alerting_enabled": True,
        "anomaly_open_after_intervals": 1,
        "anomaly_resolve_after_intervals": 1,
    }
    values.update(overrides)
    return cfg(**values)


def routing_policy() -> RoutingPolicy:
    return RoutingPolicy(
        rules=[
            RoutingRule(
                channel=NotificationChannel.SLACK,
                target="primary",
                alert_types=frozenset({"waf_anomaly"}),
            ),
            RoutingRule(
                channel=NotificationChannel.EMAIL,
                target="soc@example.test",
                alert_types=frozenset({"waf_anomaly"}),
            ),
            RoutingRule(
                channel=NotificationChannel.WEBHOOK,
                target="phase-a-only",
                alert_types=frozenset({"anomaly"}),
            ),
        ]
    )


async def seed_endpoint(session, *, with_baseline: bool = True):  # type: ignore[no-untyped-def]
    instance = await make_instance(session)
    source = await make_log_source(
        session,
        instance,
        qradar_id=312,
        name="edge-waf",
        owner="payments",
    )
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        virtual_host="portal.example",
        normalized_endpoint="/login/{id}",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=T0 - timedelta(days=30),
        last_seen_at=T0,
        tracked_since=T0 - timedelta(days=30),
        is_active=True,
    )
    session.add(endpoint)
    await session.flush()
    if with_baseline:
        session.add(
            WafEndpointBaseline(
                waf_endpoint_id=endpoint.id,
                metric_name="request_count",
                weekday=T0.isoweekday(),
                hour=T0.hour,
                median=100.0,
                mad=10.0,
                p05=80.0,
                p95=120.0,
                sample_count=12,
                is_reliable=True,
                baseline_version=4,
                normalizer_version=ENDPOINT_NORMALIZER_VERSION,
                observations=[100.0] * 12,
                completeness=1.0,
                excluded_sample_count=0,
                exclusion_counts={},
                metric_semantics=SEMANTICS,
            )
        )
        await session.flush()
    return instance, source, endpoint


async def add_observation(
    session,
    endpoint: WafEndpoint,
    at,
    request_count: int,
    *,
    in_maintenance: bool = False,
    completeness: BucketCompleteness = BucketCompleteness.COMPLETE,
):  # type: ignore[no-untyped-def]
    bucket = WafSourceBucket(
        log_source_id=endpoint.log_source_id,
        bucket_start=at,
        bucket_seconds=300,
        origin="LIVE",
        profile_version=1,
        metric_semantics=SEMANTICS,
        completeness=completeness,
        endpoint_coverage_ratio=1.0,
        total_events=request_count,
        parsed_events=request_count,
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
        in_maintenance=in_maintenance,
        collection_source="TEST",
        query_provenance={},
    )
    metric = WafEndpointMetric(
        waf_endpoint_id=endpoint.id,
        bucket_start=at,
        bucket_seconds=300,
        request_count=request_count,
        distinct_client_count=3,
        endpoint_coverage_ratio=1.0,
        completeness=completeness,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    )
    session.add_all([bucket, metric])
    await session.flush()
    return bucket, metric


async def evaluate(
    session,
    settings: Settings,
    source,
    endpoint,
    at,
    count: int,
    *,
    enqueuer=None,
    maintenance: bool = False,
    detectors=None,
):  # type: ignore[no-untyped-def]
    bucket, metric = await add_observation(
        session, endpoint, at, count, in_maintenance=maintenance
    )
    engine = WafAnomalyEngine(
        session,
        settings=settings,
        enqueuer=enqueuer,
        detectors=(
            {WafAnomalyType.WAF_REQUEST_RATE_SPIKE: detect_request_rate_spike}
            if detectors is None
            else detectors
        ),
    )
    return await engine.evaluate_interval(
        endpoint, metric, source_bucket=bucket, log_source=source
    )


async def notification_count(session) -> int:  # type: ignore[no-untyped-def]
    return int(
        await session.scalar(select(func.count()).select_from(AlertNotification)) or 0
    )


async def test_open_refresh_resolve_and_recur(db_session) -> None:
    settings = alert_settings()
    instance, source, endpoint = await seed_endpoint(db_session)
    dispatcher = NotificationDispatcher(
        db_session, {}, routing_policy(), settings=settings
    )

    await evaluate(db_session, settings, source, endpoint, T0, 500, enqueuer=dispatcher)
    alerts = list((await db_session.scalars(select(Alert))).all())
    assert len(alerts) == 1
    first = alerts[0]
    assert first.fingerprint == waf_anomaly_fingerprint(
        instance_id=endpoint.instance_id,
        log_source_id=endpoint.log_source_id,
        virtual_host=endpoint.virtual_host,
        normalized_endpoint=endpoint.normalized_endpoint,
        http_method=endpoint.http_method,
        normalizer_version=endpoint.normalizer_version,
        detector_type=WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value,
    )
    assert first.title == "WAF_REQUEST_RATE_SPIKE on edge-waf: POST /login/{id}"
    assert first.source_type == "waf_endpoint" and first.source_id == endpoint.id
    assert first.context == {
        "log_source": "edge-waf",
        "owner": "payments",
        "criticality": "MEDIUM",
        "instance": instance.name,
        "virtual_host": "portal.example",
        "endpoint": "/login/{id}",
        "method": "POST",
        "detector_type": "WAF_REQUEST_RATE_SPIKE",
    }
    assert first.description == first.evidence_snapshot["reason"]
    assert len(first.source_anomaly_ids) == 1
    assert await notification_count(db_session) == 2

    await evaluate(
        db_session, settings, source, endpoint, T0 + STEP, 600, enqueuer=dispatcher
    )
    assert first.occurrence_count == 2
    assert await notification_count(db_session) == 2

    await evaluate(
        db_session, settings, source, endpoint, T0 + STEP * 2, 100, enqueuer=dispatcher
    )
    assert first.status == AlertStatus.RESOLVED
    assert await notification_count(db_session) == 4

    await evaluate(
        db_session, settings, source, endpoint, T0 + STEP * 3, 700, enqueuer=dispatcher
    )
    alerts = list((await db_session.scalars(select(Alert).order_by(Alert.opened_at))).all())
    assert len(alerts) == 2
    assert alerts[1].id != first.id and alerts[1].status == AlertStatus.OPEN
    assert await notification_count(db_session) == 6


def always_error_spike(ctx) -> WafSpikeVerdict:  # type: ignore[no-untyped-def]
    detector_type = WafAnomalyType.WAF_ERROR_RATIO_SPIKE
    return WafSpikeVerdict(
        detector_type=detector_type,
        evidence=AnomalyEvidence(
            anomaly_type=detector_type,
            signal=DetectorSignal.ANOMALOUS,
            severity=Severity.HIGH,
            observed_value=0.8,
            expected_value=0.1,
            baseline_low=0.05,
            baseline_high=0.15,
            deviation_score=7.0,
            threshold=3.5,
            sample_count=12,
            interval_start=ctx.point.bucket_start,
            interval_end=ctx.point.bucket_end,
            confidence=1.0,
            reason="server error ratio is above baseline",
        ),
    )


async def test_detectors_have_independent_alerts_and_recovery(db_session) -> None:
    settings = alert_settings(waf_outcome_collection_enabled=True)
    _instance, source, endpoint = await seed_endpoint(db_session)
    detectors = {
        WafAnomalyType.WAF_REQUEST_RATE_SPIKE: detect_request_rate_spike,
        WafAnomalyType.WAF_ERROR_RATIO_SPIKE: always_error_spike,
    }

    await evaluate(
        db_session, settings, source, endpoint, T0, 500, detectors=detectors
    )
    alerts = list((await db_session.scalars(select(Alert))).all())
    assert len(alerts) == 2
    assert len({alert.fingerprint for alert in alerts}) == 2

    await evaluate(
        db_session, settings, source, endpoint, T0 + STEP, 100, detectors=detectors
    )
    by_detector = {
        alert.context["detector_type"]: alert for alert in alerts
    }
    assert by_detector[WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value].status == (
        AlertStatus.RESOLVED
    )
    assert by_detector[WafAnomalyType.WAF_ERROR_RATIO_SPIKE.value].status == AlertStatus.OPEN


async def test_shadow_mode_creates_no_alerts_or_notifications(db_session) -> None:
    settings = alert_settings(waf_alerting_enabled=False)
    _instance, source, endpoint = await seed_endpoint(db_session)
    dispatcher = NotificationDispatcher(db_session, {}, routing_policy(), settings=settings)

    report = await evaluate(
        db_session, settings, source, endpoint, T0, 500, enqueuer=dispatcher
    )
    assert report.states[WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value] == AnomalyState.OPEN
    assert not (await db_session.scalars(select(Alert))).all()
    assert await notification_count(db_session) == 0


async def test_maintenance_neither_opens_nor_refreshes(db_session) -> None:
    settings = alert_settings()
    _instance, source, endpoint = await seed_endpoint(db_session)
    await evaluate(db_session, settings, source, endpoint, T0, 500)
    alert = await db_session.scalar(select(Alert))
    assert alert is not None and alert.occurrence_count == 1

    await evaluate(
        db_session, settings, source, endpoint, T0 + STEP, 600, maintenance=True
    )
    assert alert.occurrence_count == 1

    _other_instance, other_source, other_endpoint = await seed_endpoint(db_session)
    await evaluate(
        db_session,
        settings,
        other_source,
        other_endpoint,
        T0,
        500,
        maintenance=True,
    )
    assert int(await db_session.scalar(select(func.count()).select_from(Alert)) or 0) == 1


async def test_candidate_and_insufficient_data_raise_nothing(db_session) -> None:
    candidate_settings = alert_settings(anomaly_open_after_intervals=2)
    _instance, source, endpoint = await seed_endpoint(db_session)
    candidate = await evaluate(db_session, candidate_settings, source, endpoint, T0, 500)
    assert candidate.states[WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value] == (
        AnomalyState.CANDIDATE
    )

    _other_instance, other_source, other_endpoint = await seed_endpoint(
        db_session, with_baseline=False
    )
    insufficient = await evaluate(
        db_session, alert_settings(), other_source, other_endpoint, T0, 500
    )
    assert insufficient.states[WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value] == (
        AnomalyState.INSUFFICIENT_DATA
    )
    assert not (await db_session.scalars(select(Alert))).all()


async def test_scheduled_run_opens_an_alert_end_to_end(db_session) -> None:
    settings = alert_settings(baseline_min_samples=4)
    counts = [*NORMAL[:6], 900]
    provider = StubWafProvider(series(counts))
    profile, _source = await source_with_profile(db_session, provider, settings)
    dispatcher = NotificationDispatcher(db_session, {}, routing_policy(), settings=settings)

    for index in range(len(counts)):
        at = T0 + STEP * index
        report = await run_waf_source(
            db_session,
            profile.id,
            provider_factory=lambda _instance: provider,
            settings=settings,
            clock=lambda at=at: at + STEP + timedelta(seconds=5),
            enqueuer=dispatcher,
        )
        assert report["status"] == "ok"

    alert = await db_session.scalar(select(Alert))
    assert alert is not None
    assert alert.context["detector_type"] == WafAnomalyType.WAF_REQUEST_RATE_SPIKE.value
    assert alert.status == AlertStatus.OPEN
    assert await notification_count(db_session) == 2
