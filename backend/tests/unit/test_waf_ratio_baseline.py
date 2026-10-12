"""Pure eligibility rules for Phase B.2 proportion baselines."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly.capabilities import WAF_BASELINE_METRICS
from app.anomaly.seasonal import summarize_cell
from app.models.enums import (
    BucketCompleteness,
    WafMeasurement,
    WafTelemetryStatus,
)
from app.waf.baseline import (
    BLOCK_RATIO_METRIC,
    RATIO_METRICS,
    WAF_BASELINE_METRIC,
    clean_ratio_samples,
)
from app.waf.observations import BreakdownCounts, BreakdownStatus, EndpointObservation

T0 = datetime(2026, 1, 5, 10, tzinfo=UTC)
STEP = timedelta(minutes=5)
IDENTITY = "action/v1;agg=SUM(eventcount);normalizer/v1;profile/v1"
METRIC = RATIO_METRICS[BLOCK_RATIO_METRIC]


def observation(**changes: object) -> EndpointObservation:
    base = EndpointObservation(
        bucket_start=T0,
        bucket_seconds=300,
        request_count=100,
        distinct_client_count=4,
        completeness=BucketCompleteness.COMPLETE,
        endpoint_coverage_ratio=1.0,
        metric_semantics="request-count-identity",
        measurement=WafMeasurement.EXACT,
        in_maintenance=False,
        normalizer_version=1,
        breakdowns=BreakdownStatus(
            action=WafTelemetryStatus.EXACT,
            action_semantics=IDENTITY,
        ),
        counts=BreakdownCounts(action_events=100, blocked=20, unmapped_actions=0),
    )
    return replace(base, **changes)


def clean(
    obs: EndpointObservation, *, end: datetime = T0 + STEP,
    spans: tuple[tuple[datetime, datetime], ...] = (),
):  # type: ignore[no-untyped-def]
    return clean_ratio_samples(
        [obs],
        METRIC,
        end=end,
        semantics=IDENTITY,
        min_coverage=0.9,
        min_denominator=50,
        spans=spans,
        maintenance_rule=lambda _start: False,
    )


def test_unfinished_bucket_is_excluded_first() -> None:
    samples, tally, candidates = clean(observation(), end=T0 + timedelta(minutes=4))

    assert samples == [] and candidates == 1
    assert tally.unfinished == 1


def test_incompatible_breakdown_semantics_is_excluded() -> None:
    breakdowns = replace(observation().breakdowns, action_semantics="action/v2")
    samples, tally, _ = clean(observation(breakdowns=breakdowns))

    assert samples == [] and tally.incompatible_semantics == 1


def test_maintenance_bucket_is_excluded() -> None:
    samples, tally, _ = clean(observation(in_maintenance=True))

    assert samples == [] and tally.maintenance == 1


@pytest.mark.parametrize(
    "status", [WafTelemetryStatus.NOT_COLLECTED, WafTelemetryStatus.FAILED]
)
def test_unavailable_breakdown_is_excluded(status: WafTelemetryStatus) -> None:
    breakdowns = replace(observation().breakdowns, action=status, action_semantics=None)
    samples, tally, _ = clean(observation(breakdowns=breakdowns))

    assert samples == [] and tally.unavailable == 1


def test_lower_bound_breakdown_is_incomplete() -> None:
    breakdowns = replace(observation().breakdowns, action=WafTelemetryStatus.LOWER_BOUND)
    samples, tally, _ = clean(observation(breakdowns=breakdowns))

    assert samples == [] and tally.incomplete == 1


def test_uncertain_values_exclude_a_bucket() -> None:
    counts = replace(observation().counts, unmapped_actions=1)
    samples, tally, _ = clean(observation(counts=counts))

    assert samples == [] and tally.uncertain_values == 1


def test_inexact_request_count_excludes_a_bucket() -> None:
    samples, tally, _ = clean(observation(measurement=WafMeasurement.LOWER_BOUND))

    assert samples == [] and tally.inexact_request_count == 1


def test_breakdown_coverage_below_the_detector_floor_is_excluded() -> None:
    counts = replace(observation().counts, action_events=50, blocked=10)
    samples, tally, _ = clean(observation(counts=counts))

    assert samples == [] and tally.low_coverage == 1


def test_low_source_coverage_is_excluded_and_not_called_breakdown_coverage() -> None:
    # The breakdown covers every visible request; only 85% of the source's events parsed.
    samples, tally, _ = clean(observation(endpoint_coverage_ratio=0.85))

    assert samples == []
    assert tally.low_source_coverage == 1 and tally.low_coverage == 0
    assert tally.as_dict()["low_source_coverage"] == 1


def test_source_coverage_exactly_at_the_floor_trains() -> None:
    samples, tally, _ = clean(observation(endpoint_coverage_ratio=0.9))

    assert len(samples) == 1 and tally.total == 0


def test_inexact_request_count_is_attributed_before_low_source_coverage() -> None:
    samples, tally, _ = clean(
        observation(measurement=WafMeasurement.LOWER_BOUND, endpoint_coverage_ratio=0.5)
    )

    assert samples == []
    assert tally.inexact_request_count == 1 and tally.low_source_coverage == 0


def test_low_source_coverage_is_attributed_before_breakdown_coverage() -> None:
    counts = replace(observation().counts, action_events=50, blocked=10)
    samples, tally, _ = clean(observation(counts=counts, endpoint_coverage_ratio=0.5))

    assert samples == []
    assert tally.low_source_coverage == 1 and tally.low_coverage == 0


def test_immaterial_denominator_is_not_a_zero_proportion_sample() -> None:
    counts = replace(observation().counts, action_events=0, blocked=0)
    samples, tally, _ = clean(observation(request_count=0, counts=counts))

    assert samples == [] and tally.immaterial == 1


def test_known_anomaly_bucket_is_excluded() -> None:
    samples, tally, _ = clean(observation(), spans=((T0, T0 + STEP),))

    assert samples == [] and tally.known_anomaly == 1


def test_samples_preserve_pooled_sums_and_statistics_use_proportions() -> None:
    first = observation(counts=BreakdownCounts(
        action_events=50, blocked=5, unmapped_actions=0
    ), request_count=50)
    second = replace(
        observation(),
        bucket_start=T0 + timedelta(days=7),
        counts=BreakdownCounts(action_events=100, blocked=90, unmapped_actions=0),
    )
    samples, tally, candidates = clean_ratio_samples(
        [first, second],
        METRIC,
        end=second.bucket_end,
        semantics=IDENTITY,
        min_coverage=0.9,
        min_denominator=50,
        spans=(),
        maintenance_rule=lambda _start: False,
    )

    proportions = [sample.proportion for sample in samples]
    cell = summarize_cell(proportions, min_samples=1)
    assert (sum(s.numerator for s in samples), sum(s.denominator for s in samples)) == (95, 150)
    assert proportions == [0.1, 0.9]
    assert cell.median == pytest.approx(0.5)
    assert tally.total == 0 and candidates == 2


def test_capability_registry_claims_only_metrics_the_builder_writes() -> None:
    assert frozenset({WAF_BASELINE_METRIC, *RATIO_METRICS}) == WAF_BASELINE_METRICS
