"""Database coverage for Phase B.2 ratio baseline persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.models.enums import (
    BucketCompleteness,
    WafBreakdown,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.models.waf import (
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.waf.baseline import WAF_BASELINE_METRIC, WafBaselineBuilder
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.semantics import breakdown_semantics, request_count_semantics
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

T0 = datetime(2026, 1, 5, 10, tzinfo=UTC)
STEP = timedelta(minutes=5)
WEEK = timedelta(days=7)
REQUEST_IDENTITY = request_count_semantics(
    aggregation="SUM(eventcount)",
    normalizer_version=ENDPOINT_NORMALIZER_VERSION,
    profile_version=1,
)
IDENTITIES = {
    breakdown: breakdown_semantics(
        breakdown.value,
        aggregation="SUM(eventcount)",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        profile_version=1,
    )
    for breakdown in WafBreakdown
}


def settings() -> Settings:
    values = get_settings().model_dump()
    values.update(
        baseline_min_samples=1,
        waf_min_endpoint_coverage=0.5,
        waf_ratio_min_denominator=1,
        waf_concentration_min_endpoint_requests=1,
    )
    return Settings(**values)


def source_bucket(
    source_id,
    start: datetime,
    *,
    action: WafTelemetryStatus = WafTelemetryStatus.EXACT,
    response: WafTelemetryStatus = WafTelemetryStatus.EXACT,
    client: WafTelemetryStatus = WafTelemetryStatus.EXACT,
    action_identity: str | None = IDENTITIES[WafBreakdown.ACTION],
) -> WafSourceBucket:
    return WafSourceBucket(
        log_source_id=source_id,
        bucket_start=start,
        bucket_seconds=300,
        origin="LIVE",
        profile_version=1,
        metric_semantics=REQUEST_IDENTITY,
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0,
        total_events=200,
        parsed_events=200,
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
        action_measurement=action,
        action_semantics=action_identity,
        action_events=200 if action is WafTelemetryStatus.EXACT else None,
        response_code_measurement=response,
        response_code_semantics=IDENTITIES[WafBreakdown.RESPONSE_CODE],
        response_code_events=200 if response is WafTelemetryStatus.EXACT else None,
        client_measurement=client,
        client_semantics=IDENTITIES[WafBreakdown.SOURCE_IP],
        client_events=200 if client is WafTelemetryStatus.EXACT else None,
        query_provenance={},
    )


def metric_row(
    endpoint_id, start: datetime, request_count: int, **counts: int
) -> WafEndpointMetric:
    return WafEndpointMetric(
        waf_endpoint_id=endpoint_id,
        bucket_start=start,
        bucket_seconds=300,
        request_count=request_count,
        endpoint_coverage_ratio=1.0,
        completeness=BucketCompleteness.COMPLETE,
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        **counts,
    )


async def seed_endpoint(db_session):  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=312)
    endpoint = WafEndpoint(
        instance_id=instance.id,
        log_source_id=source.id,
        normalized_endpoint="/login",
        http_method="POST",
        normalizer_version=ENDPOINT_NORMALIZER_VERSION,
        first_seen_at=T0 - WEEK,
        last_seen_at=T0 + 3 * WEEK,
        tracked_since=T0 - WEEK,
        is_active=True,
    )
    db_session.add(endpoint)
    await db_session.flush()
    return source, endpoint


async def rebuild(db_session, source, endpoint, now, identities=IDENTITIES):  # type: ignore[no-untyped-def]
    return await WafBaselineBuilder(db_session, settings=settings()).rebuild_for_endpoint(
        endpoint,
        semantics=REQUEST_IDENTITY,
        breakdown_identities=identities,
        now=now,
        log_source=source,
    )


async def cells_by_metric(db_session) -> dict[str, WafEndpointBaseline]:  # type: ignore[no-untyped-def]
    cells = (await db_session.scalars(select(WafEndpointBaseline))).all()
    return {cell.metric_name: cell for cell in cells}


async def test_exact_breakdowns_train_all_ratio_cells_with_pooled_sums(db_session) -> None:
    source, endpoint = await seed_endpoint(db_session)
    starts = [T0, T0 + WEEK]
    db_session.add_all([source_bucket(source.id, start) for start in starts])
    db_session.add_all([
        metric_row(
            endpoint.id,
            starts[0],
            100,
            action_events=100,
            blocked_count=10,
            unmapped_action_count=0,
            response_code_events=90,
            client_error_count=18,
            server_error_count=9,
            unclassified_response_count=0,
            client_events=80,
            top1_client_requests=20,
        ),
        metric_row(
            endpoint.id,
            starts[1],
            200,
            action_events=180,
            blocked_count=36,
            unmapped_action_count=0,
            response_code_events=180,
            client_error_count=54,
            server_error_count=36,
            unclassified_response_count=0,
            client_events=160,
            top1_client_requests=80,
        ),
    ])
    await db_session.flush()

    written = await rebuild(db_session, source, endpoint, starts[-1] + STEP)
    cells = await cells_by_metric(db_session)

    assert written == 5
    assert set(cells) == {
        "request_count", "block_ratio", "server_error_ratio",
        "client_error_ratio", "top1_client_share",
    }
    assert (cells["block_ratio"].numerator_sum, cells["block_ratio"].denominator_sum) == (
        46, 280
    )
    assert (
        cells["server_error_ratio"].numerator_sum,
        cells["server_error_ratio"].denominator_sum,
    ) == (45, 270)
    assert (
        cells["client_error_ratio"].numerator_sum,
        cells["client_error_ratio"].denominator_sum,
    ) == (72, 270)
    assert (
        cells["top1_client_share"].numerator_sum,
        cells["top1_client_share"].denominator_sum,
    ) == (100, 240)
    assert cells["block_ratio"].observations == [0.1, 0.2]
    assert cells["block_ratio"].median == pytest.approx(0.15)
    assert cells["request_count"].observations == [100.0, 200.0]
    assert cells["request_count"].numerator_sum is None
    assert cells["request_count"].denominator_sum is None
    assert cells["request_count"].exclusion_counts == {
        "known_anomaly": 0,
        "incomplete": 0,
        "unfinished": 0,
        "low_coverage": 0,
        "maintenance": 0,
        "incompatible_semantics": 0,
    }


async def test_non_exact_and_unavailable_breakdowns_are_tallied(db_session) -> None:
    source, endpoint = await seed_endpoint(db_session)
    statuses = [
        WafTelemetryStatus.EXACT,
        WafTelemetryStatus.LOWER_BOUND,
        WafTelemetryStatus.NOT_COLLECTED,
        WafTelemetryStatus.FAILED,
    ]
    for i, status in enumerate(statuses):
        start = T0 + i * WEEK
        db_session.add(source_bucket(
            source.id,
            start,
            action=status,
            action_identity=(
                IDENTITIES[WafBreakdown.ACTION]
                if status in (WafTelemetryStatus.EXACT, WafTelemetryStatus.LOWER_BOUND)
                else None
            ),
        ))
        db_session.add(metric_row(
            endpoint.id,
            start,
            100,
            action_events=100 if status is WafTelemetryStatus.EXACT else None,
            blocked_count=20 if status is WafTelemetryStatus.EXACT else None,
            unmapped_action_count=0 if status is WafTelemetryStatus.EXACT else None,
        ))
    await db_session.flush()

    await rebuild(
        db_session,
        source,
        endpoint,
        T0 + 3 * WEEK + STEP,
        {WafBreakdown.ACTION: IDENTITIES[WafBreakdown.ACTION]},
    )
    cells = await cells_by_metric(db_session)

    assert cells["block_ratio"].sample_count == 1
    assert cells["block_ratio"].exclusion_counts["incomplete"] == 1
    assert cells["block_ratio"].exclusion_counts["unavailable"] == 2
    assert cells[WAF_BASELINE_METRIC].sample_count == 4
    assert set(cells) == {WAF_BASELINE_METRIC, "block_ratio"}


async def test_uncertain_values_are_excluded(db_session) -> None:
    source, endpoint = await seed_endpoint(db_session)
    for i, uncertain in enumerate((0, 3)):
        start = T0 + i * WEEK
        db_session.add(source_bucket(source.id, start))
        db_session.add(metric_row(
            endpoint.id,
            start,
            100,
            action_events=100,
            blocked_count=20,
            unmapped_action_count=uncertain,
        ))
    await db_session.flush()

    await rebuild(
        db_session,
        source,
        endpoint,
        T0 + WEEK + STEP,
        {WafBreakdown.ACTION: IDENTITIES[WafBreakdown.ACTION]},
    )
    cell = (await cells_by_metric(db_session))["block_ratio"]

    assert cell.sample_count == 1
    assert cell.exclusion_counts["uncertain_values"] == 1


async def test_breakdown_semantics_change_is_excluded(db_session) -> None:
    source, endpoint = await seed_endpoint(db_session)
    for i, identity in enumerate((IDENTITIES[WafBreakdown.ACTION], "action/v2")):
        start = T0 + i * WEEK
        db_session.add(source_bucket(source.id, start, action_identity=identity))
        db_session.add(metric_row(
            endpoint.id,
            start,
            100,
            action_events=100,
            blocked_count=20,
            unmapped_action_count=0,
        ))
    await db_session.flush()

    await rebuild(
        db_session,
        source,
        endpoint,
        T0 + WEEK + STEP,
        {WafBreakdown.ACTION: IDENTITIES[WafBreakdown.ACTION]},
    )
    cell = (await cells_by_metric(db_session))["block_ratio"]

    assert cell.sample_count == 1
    assert cell.metric_semantics == IDENTITIES[WafBreakdown.ACTION]
    assert cell.exclusion_counts["incompatible_semantics"] == 1
