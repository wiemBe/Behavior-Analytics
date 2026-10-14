"""IPS detector inputs: sparse entity rows and per-input measurement semantics."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.ips.inputs import (
    EntityCount,
    IpsBucketMeasurement,
    IpsDimensionFacts,
    IpsEntityFacts,
    IpsInput,
    IpsObservation,
    IpsSourceBucketFacts,
    entity_count,
    input_measurement,
    observe_entity,
)
from app.models.enums import IpsDimension, WafMeasurement
from app.waf.inputs import HistoryFacts

START = datetime(2026, 10, 13, 12, tzinfo=UTC)
FLOOR = 0.9


def _dimension(
    measurement: IpsBucketMeasurement = IpsBucketMeasurement.EXACT,
    *,
    coverage: float | None = 1.0,
    admission_truncated: bool | None = False,
) -> IpsDimensionFacts:
    return IpsDimensionFacts(
        measurement=measurement,
        coverage_ratio=coverage,
        coverage_lower_bound=measurement is IpsBucketMeasurement.LOWER_BOUND,
        admission_truncated=admission_truncated,
    )


def _bucket(**changes: object) -> IpsSourceBucketFacts:
    exact = _dimension()
    values: dict[str, object] = {
        "bucket_start": START,
        "bucket_seconds": 3600,
        "total_events": 100,
        "destination_port": exact,
        "destination_ip": exact,
        "signature": exact,
        "source_ip": exact,
        "port_fanout": _dimension(coverage=None, admission_truncated=None),
        "action": exact,
        "source_events": 95,
        "distinct_sources": 4,
        "top1_events": 50,
        "top5_events": 90,
        "max_distinct_ports": 30,
        "blocked_events": 20,
        "allowed_events": 75,
        "unmapped_events": 0,
        "profile_version": 3,
        "normalizer_version": 2,
    }
    values.update(changes)
    return IpsSourceBucketFacts(**values)  # type: ignore[arg-type]


def _entity(**changes: object) -> IpsEntityFacts:
    values: dict[str, object] = {
        "dimension": IpsDimension.DESTINATION_PORT,
        "value_key": "443/tcp",
        "display_name": None,
        "first_seen_at": START - timedelta(days=2),
        "last_seen_at": START,
        "active": True,
        "normalizer_version": 2,
    }
    values.update(changes)
    return IpsEntityFacts(**values)  # type: ignore[arg-type]


def test_a_missing_sparse_row_is_a_measured_zero_only_when_every_condition_holds() -> None:
    count = entity_count(_entity(), _bucket(), None, min_coverage=FLOOR)
    assert count == EntityCount(events=0, measurement=WafMeasurement.EXACT)


@pytest.mark.parametrize(
    ("entity_changes", "dimension"),
    [
        ({"active": False}, _dimension()),
        ({"first_seen_at": START + timedelta(seconds=1)}, _dimension()),
        ({}, _dimension(IpsBucketMeasurement.LOWER_BOUND)),
        ({}, _dimension(coverage=FLOOR - 0.01)),
        ({}, _dimension(coverage=None)),
        ({}, _dimension(admission_truncated=True)),
        ({}, _dimension(admission_truncated=None)),
    ],
    ids=[
        "retired",
        "before-admission",
        "lower-bound",
        "coverage-below-floor",
        "coverage-unknown",
        "admission-truncated",
        "admission-unrecorded",
    ],
)
def test_each_absence_condition_on_its_own_makes_a_missing_row_unknown(
    entity_changes: dict[str, object], dimension: IpsDimensionFacts
) -> None:
    count = entity_count(
        _entity(**entity_changes),
        _bucket(destination_port=dimension),
        None,
        min_coverage=FLOOR,
    )
    assert count.events is None
    assert count.measurement is None
    assert count.reason


@pytest.mark.parametrize(
    "measurement",
    [
        IpsBucketMeasurement.NOT_COLLECTED,
        IpsBucketMeasurement.FAILED,
        IpsBucketMeasurement.REJECTED,
    ],
)
def test_an_unmeasured_dimension_never_turns_an_absent_row_into_zero(
    measurement: IpsBucketMeasurement,
) -> None:
    count = entity_count(
        _entity(),
        _bucket(destination_port=_dimension(measurement, coverage=None)),
        None,
        min_coverage=FLOOR,
    )
    assert count.events is None
    assert "unavailable, not zero" in (count.reason or "")


def test_a_present_row_in_a_lower_bound_dimension_keeps_its_lower_bound() -> None:
    count = entity_count(
        _entity(),
        _bucket(destination_port=_dimension(IpsBucketMeasurement.LOWER_BOUND)),
        27,
        min_coverage=FLOOR,
    )
    assert count == EntityCount(events=27, measurement=WafMeasurement.LOWER_BOUND)


def test_observe_entity_builds_the_plain_detector_observation() -> None:
    entity = _entity()
    bucket = _bucket()
    observation = observe_entity(entity, bucket, 12, min_coverage=FLOOR)
    assert observation.entity is entity
    assert observation.bucket is bucket
    assert observation.count == EntityCount(12, WafMeasurement.EXACT)


def test_a_non_entity_dimension_cannot_gain_a_sparse_zero() -> None:
    count = entity_count(
        _entity(dimension=IpsDimension.SOURCE_IP), _bucket(), None, min_coverage=FLOOR
    )
    assert count.events is None
    assert "not an entity dimension" in (count.reason or "")


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (IpsInput.ENTITY_EVENTS, WafMeasurement.EXACT),
        (IpsInput.DIMENSION_EVENTS, WafMeasurement.EXACT),
        (IpsInput.DESTINATION_PORT_EVENTS, WafMeasurement.EXACT),
        (IpsInput.SOURCE_IP_TOP1_EVENTS, WafMeasurement.EXACT),
        (IpsInput.SOURCE_IP_EVENTS, WafMeasurement.EXACT),
        (IpsInput.MAX_DISTINCT_PORTS, WafMeasurement.EXACT),
        (IpsInput.BLOCKED_EVENTS, WafMeasurement.EXACT),
        (IpsInput.ACTION_EVENTS, WafMeasurement.EXACT),
        (IpsInput.PORT_HISTORY, WafMeasurement.EXACT),
    ],
)
def test_each_declared_input_reports_its_exact_measurement(
    source: IpsInput, expected: WafMeasurement
) -> None:
    observation = IpsObservation(
        bucket=_bucket(),
        entity=_entity(),
        count=EntityCount(12, WafMeasurement.EXACT),
    )
    assert input_measurement(source, observation, HistoryFacts(complete=True)) is expected


def test_unmapped_action_values_make_only_the_blocked_numerator_a_lower_bound() -> None:
    observation = IpsObservation(bucket=_bucket(unmapped_events=4))
    assert input_measurement(IpsInput.BLOCKED_EVENTS, observation) is WafMeasurement.LOWER_BOUND
    assert input_measurement(IpsInput.ACTION_EVENTS, observation) is WafMeasurement.EXACT


def test_a_lower_bound_search_is_carried_by_each_relevant_input() -> None:
    lower = _dimension(IpsBucketMeasurement.LOWER_BOUND)
    observation = IpsObservation(
        bucket=_bucket(
            destination_port=lower,
            source_ip=lower,
            port_fanout=replace(lower, coverage_ratio=None),
            action=lower,
        ),
        entity=_entity(),
        count=EntityCount(12, WafMeasurement.LOWER_BOUND),
    )
    for source in (
        IpsInput.ENTITY_EVENTS,
        IpsInput.DIMENSION_EVENTS,
        IpsInput.DESTINATION_PORT_EVENTS,
        IpsInput.SOURCE_IP_TOP1_EVENTS,
        IpsInput.SOURCE_IP_EVENTS,
        IpsInput.MAX_DISTINCT_PORTS,
        IpsInput.BLOCKED_EVENTS,
        IpsInput.ACTION_EVENTS,
    ):
        assert input_measurement(source, observation) is WafMeasurement.LOWER_BOUND, source


@pytest.mark.parametrize(
    ("source", "observation", "history"),
    [
        (IpsInput.ENTITY_EVENTS, IpsObservation(bucket=_bucket()), None),
        (IpsInput.DIMENSION_EVENTS, IpsObservation(bucket=_bucket()), None),
        (
            IpsInput.SOURCE_IP_TOP1_EVENTS,
            IpsObservation(bucket=_bucket(top1_events=None)),
            None,
        ),
        (
            IpsInput.SOURCE_IP_EVENTS,
            IpsObservation(bucket=_bucket(source_events=None)),
            None,
        ),
        (
            IpsInput.MAX_DISTINCT_PORTS,
            IpsObservation(bucket=_bucket(max_distinct_ports=None)),
            None,
        ),
        (
            IpsInput.BLOCKED_EVENTS,
            IpsObservation(bucket=_bucket(blocked_events=None)),
            None,
        ),
        (
            IpsInput.ACTION_EVENTS,
            IpsObservation(bucket=_bucket(allowed_events=None)),
            None,
        ),
        (
            IpsInput.PORT_HISTORY,
            IpsObservation(bucket=_bucket()),
            HistoryFacts(complete=False, reason="missing bucket"),
        ),
    ],
)
def test_an_unknown_input_reports_no_measurement(
    source: IpsInput, observation: IpsObservation, history: HistoryFacts | None
) -> None:
    assert input_measurement(source, observation, history) is None
