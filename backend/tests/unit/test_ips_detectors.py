"""Pure IPS detectors: every fail-closed guard and supported verdict scope."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from enum import StrEnum

import pytest

from app.ips.behavior import (
    IPS_DETECTOR_FUNCTIONS,
    appearance_flood,
    detect_block_ratio_shift,
    detect_new_destination_port,
    detect_port_fanout_spike,
    detect_source_concentration_spike,
    verify_port_history,
)
from app.ips.detector import (
    EVIDENCE_VERSION,
    IpsBaselineCell,
    IpsDetectionContext,
    IpsNoveltyFacts,
    IpsVerdict,
    detect_destination_rate_spike,
    detect_port_rate_spike,
    detect_signature_burst,
)
from app.ips.inputs import (
    EntityCount,
    IpsBucketMeasurement,
    IpsDimensionFacts,
    IpsEntityFacts,
    IpsObservation,
    IpsSourceBucketFacts,
)
from app.models.enums import (
    AnomalyType,
    DetectorSignal,
    IpsAnomalyType,
    IpsDimension,
    WafAnomalyType,
    WafMeasurement,
)
from app.waf.inputs import HistoryFacts

START = datetime(2026, 10, 13, 12, tzinfo=UTC)
HOUR = 3600


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
        "bucket_seconds": HOUR,
        "total_events": 100,
        "destination_port": exact,
        "destination_ip": exact,
        "signature": exact,
        "source_ip": exact,
        "port_fanout": _dimension(coverage=None, admission_truncated=None),
        "action": exact,
        "source_events": 100,
        "distinct_sources": 8,
        "top1_events": 70,
        "top5_events": 95,
        "max_distinct_ports": 60,
        "blocked_events": 60,
        "allowed_events": 40,
        "unmapped_events": 0,
        "contributors": {
            "SOURCE_IP": [{"source_ip": "192.0.2.4", "events": 70}],
            "PORT_FANOUT": [{"source_ip": "192.0.2.4", "distinct_ports": 60}],
        },
        "profile_version": 3,
        "normalizer_version": 2,
        "in_maintenance": False,
    }
    values.update(changes)
    return IpsSourceBucketFacts(**values)  # type: ignore[arg-type]


def _entity(
    dimension: IpsDimension = IpsDimension.DESTINATION_PORT, **changes: object
) -> IpsEntityFacts:
    default_key = {
        IpsDimension.DESTINATION_PORT: "443/tcp",
        IpsDimension.DESTINATION_IP: "10.10.20.15",
        IpsDimension.SIGNATURE: "qid:5000123",
    }[dimension]
    values: dict[str, object] = {
        "dimension": dimension,
        "value_key": default_key,
        "display_name": "Example signature" if dimension is IpsDimension.SIGNATURE else None,
        "first_seen_at": START - timedelta(days=2),
        "last_seen_at": START,
        "active": True,
        "normalizer_version": 2,
    }
    values.update(changes)
    return IpsEntityFacts(**values)  # type: ignore[arg-type]


def _cell(metric: str = "count", **changes: object) -> IpsBaselineCell:
    ratio = metric == "ratio"
    values: dict[str, object] = {
        "median": 0.1 if ratio else 10.0,
        "mad": 0.02 if ratio else 2.0,
        "p05": 0.05 if ratio else 5.0,
        "p95": 0.15 if ratio else 15.0,
        "sample_count": 12,
        "is_reliable": True,
        "completeness": 1.0,
        "baseline_version": 1,
        "numerator_sum": 10.0 if ratio else None,
        "denominator_sum": 100.0 if ratio else None,
        "metric_semantics": "ips/v1",
        "profile_version": 3,
        "normalizer_version": 2,
    }
    values.update(changes)
    return IpsBaselineCell(**values)  # type: ignore[arg-type]


def _entity_context(
    dimension: IpsDimension,
    *,
    events: int | None = 100,
    measurement: WafMeasurement | None = WafMeasurement.EXACT,
    bucket: IpsSourceBucketFacts | None = None,
    entity: IpsEntityFacts | None = None,
    baseline: IpsBaselineCell | None = None,
) -> IpsDetectionContext:
    return IpsDetectionContext(
        observation=IpsObservation(
            bucket=bucket or _bucket(),
            entity=entity or _entity(dimension),
            count=EntityCount(events, measurement, "count unknown" if events is None else None),
        ),
        baseline=_cell() if baseline is None else baseline,
    )


ENTITY_CASES = (
    (
        detect_port_rate_spike,
        IpsDimension.DESTINATION_PORT,
        IpsAnomalyType.IPS_PORT_RATE_SPIKE,
    ),
    (
        detect_destination_rate_spike,
        IpsDimension.DESTINATION_IP,
        IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE,
    ),
    (
        detect_signature_burst,
        IpsDimension.SIGNATURE,
        IpsAnomalyType.IPS_SIGNATURE_BURST,
    ),
)


def _assert_insufficient(verdict: IpsVerdict, text: str | None = None) -> None:
    assert verdict.signal is DetectorSignal.UNKNOWN
    assert verdict.evidence.insufficient_data is True
    if text is not None:
        assert text in verdict.evidence.reason


@pytest.mark.parametrize(("detector", "dimension", "anomaly_type"), ENTITY_CASES)
def test_each_entity_spike_has_normal_and_anomalous_paths(
    detector, dimension: IpsDimension, anomaly_type: IpsAnomalyType
) -> None:
    normal = detector(_entity_context(dimension, events=15))
    anomalous = detector(_entity_context(dimension, events=100))
    assert normal.signal is DetectorSignal.HEALTHY
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    assert anomalous.detector_type is anomaly_type
    assert anomalous.evidence.anomaly_type is anomaly_type
    assert anomalous.evidence.extra["evidence_version"] == EVIDENCE_VERSION


@pytest.mark.parametrize(("detector", "dimension", "_"), ENTITY_CASES)
@pytest.mark.parametrize(
    ("events", "baseline", "reason"),
    [
        (15, _cell(), "below the 20 minimum"),
        (50, _cell(median=40.0), "ratio"),
        (25, _cell(), "minimum absolute delta"),
        (70, _cell(mad=50.0), "robust z"),
        (70, _cell(mad=0.0, p95=100.0), "historical p95"),
    ],
)
def test_each_entity_spike_has_an_exact_normal_exit_for_each_conjunctive_guard(
    detector,
    dimension: IpsDimension,
    _: IpsAnomalyType,
    events: int,
    baseline: IpsBaselineCell,
    reason: str,
) -> None:
    verdict = detector(_entity_context(dimension, events=events, baseline=baseline))
    assert verdict.signal is DetectorSignal.HEALTHY
    assert reason in verdict.evidence.reason


@pytest.mark.parametrize(("detector", "dimension", "_"), ENTITY_CASES)
def test_each_entity_spike_fails_closed_for_maintenance_unknown_count_and_low_coverage(
    detector, dimension: IpsDimension, _: IpsAnomalyType
) -> None:
    _assert_insufficient(
        detector(_entity_context(dimension, bucket=_bucket(in_maintenance=True))), "maintenance"
    )
    _assert_insufficient(detector(_entity_context(dimension, events=None)), "count unknown")
    field = {
        IpsDimension.DESTINATION_PORT: "destination_port",
        IpsDimension.DESTINATION_IP: "destination_ip",
        IpsDimension.SIGNATURE: "signature",
    }[dimension]
    low = _bucket(**{field: _dimension(coverage=0.89)})
    _assert_insufficient(detector(_entity_context(dimension, bucket=low)), "coverage")
    unmeasured = _bucket(
        **{
            field: _dimension(
                IpsBucketMeasurement.FAILED,
                coverage=None,
                admission_truncated=None,
            )
        }
    )
    _assert_insufficient(detector(_entity_context(dimension, bucket=unmeasured)), "failed")


@pytest.mark.parametrize(("detector", "dimension", "_"), ENTITY_CASES)
@pytest.mark.parametrize(
    ("baseline", "reason"),
    [
        (False, "no entity_events baseline"),
        (_cell(is_reliable=False), "below the minimum"),
        (_cell(sample_count=7), "below the minimum"),
        (_cell(profile_version=4), "profile version"),
        (_cell(normalizer_version=9), "normalizer version"),
    ],
)
def test_each_entity_spike_fails_closed_for_every_baseline_guard(
    detector,
    dimension: IpsDimension,
    _: IpsAnomalyType,
    baseline: IpsBaselineCell | bool,
    reason: str,
) -> None:
    context = _entity_context(dimension)
    context.baseline = None if baseline is False else baseline
    _assert_insufficient(detector(context), reason)


@pytest.mark.parametrize(("detector", "dimension", "_"), ENTITY_CASES)
def test_each_entity_spike_uses_increase_only_scope_for_a_lower_bound(
    detector, dimension: IpsDimension, _: IpsAnomalyType
) -> None:
    field = {
        IpsDimension.DESTINATION_PORT: "destination_port",
        IpsDimension.DESTINATION_IP: "destination_ip",
        IpsDimension.SIGNATURE: "signature",
    }[dimension]
    lower_bucket = _bucket(
        **{field: _dimension(IpsBucketMeasurement.LOWER_BOUND, coverage=0.95)}
    )
    anomalous = detector(
        _entity_context(
            dimension,
            events=100,
            measurement=WafMeasurement.LOWER_BOUND,
            bucket=lower_bucket,
        )
    )
    not_normal = detector(
        _entity_context(
            dimension,
            events=15,
            measurement=WafMeasurement.LOWER_BOUND,
            bucket=lower_bucket,
        )
    )
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    assert anomalous.evidence.extra["observed_measurement"] == "LOWER_BOUND"
    _assert_insufficient(not_normal, "normal verdict cannot be concluded")


@pytest.mark.parametrize("value_key", ["0/tcp", "443/255"])
def test_a_port_absent_value_is_never_judged(value_key: str) -> None:
    context = _entity_context(
        IpsDimension.DESTINATION_PORT,
        entity=_entity(IpsDimension.DESTINATION_PORT, value_key=value_key),
    )
    _assert_insufficient(detect_port_rate_spike(context), "absent values")


def test_an_optional_unknown_protocol_is_a_valid_port_key() -> None:
    context = _entity_context(
        IpsDimension.DESTINATION_PORT,
        entity=_entity(IpsDimension.DESTINATION_PORT, value_key="443/unknown"),
    )
    assert detect_port_rate_spike(context).signal is DetectorSignal.ANOMALOUS


def test_a_generic_signature_name_is_never_judged() -> None:
    context = _entity_context(
        IpsDimension.SIGNATURE,
        entity=_entity(IpsDimension.SIGNATURE, display_name="Unknown Event"),
    )
    _assert_insufficient(detect_signature_burst(context), "generic")


def test_an_entity_detector_refuses_the_wrong_subject_dimension() -> None:
    context = _entity_context(IpsDimension.DESTINATION_IP)
    _assert_insufficient(detect_port_rate_spike(context), "not judged")


def _source_context(
    detector_type: IpsAnomalyType,
    *,
    bucket: IpsSourceBucketFacts | None = None,
    baseline: IpsBaselineCell | None = None,
) -> IpsDetectionContext:
    ratio = detector_type in {
        IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
        IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
    }
    return IpsDetectionContext(
        observation=IpsObservation(bucket=bucket or _bucket()),
        baseline=_cell("ratio" if ratio else "count") if baseline is None else baseline,
    )


@pytest.mark.parametrize(
    ("detector", "detector_type", "normal_bucket"),
    [
        (
            detect_source_concentration_spike,
            IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
            _bucket(top1_events=15),
        ),
        (
            detect_port_fanout_spike,
            IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
            _bucket(max_distinct_ports=15),
        ),
        (
            detect_block_ratio_shift,
            IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
            _bucket(blocked_events=5, allowed_events=95),
        ),
    ],
)
def test_each_baselined_source_detector_has_normal_and_anomalous_paths(
    detector, detector_type: IpsAnomalyType, normal_bucket: IpsSourceBucketFacts
) -> None:
    normal = detector(_source_context(detector_type, bucket=normal_bucket))
    anomalous = detector(_source_context(detector_type))
    assert normal.signal is DetectorSignal.HEALTHY
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    assert anomalous.evidence.anomaly_type is detector_type
    assert anomalous.evidence.extra["evidence_version"] == EVIDENCE_VERSION


@pytest.mark.parametrize(
    ("detector", "detector_type"),
    [
        (detect_source_concentration_spike, IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE),
        (detect_port_fanout_spike, IpsAnomalyType.IPS_PORT_FANOUT_SPIKE),
        (detect_block_ratio_shift, IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT),
    ],
)
@pytest.mark.parametrize(
    ("baseline", "reason"),
    [
        (False, "no "),
        (_cell(is_reliable=False), "below the minimum"),
        (_cell(sample_count=7), "below the minimum"),
        (_cell(profile_version=99), "profile version"),
        (_cell(normalizer_version=99), "normalizer version"),
    ],
)
def test_each_baselined_source_detector_fails_closed_for_every_baseline_guard(
    detector,
    detector_type: IpsAnomalyType,
    baseline: IpsBaselineCell | bool,
    reason: str,
) -> None:
    context = _source_context(detector_type)
    context.baseline = None if baseline is False else baseline
    _assert_insufficient(detector(context), reason)


@pytest.mark.parametrize(
    ("detector", "detector_type"),
    [
        (detect_source_concentration_spike, IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE),
        (detect_port_fanout_spike, IpsAnomalyType.IPS_PORT_FANOUT_SPIKE),
        (detect_block_ratio_shift, IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT),
    ],
)
def test_each_source_detector_fails_closed_in_maintenance(
    detector, detector_type: IpsAnomalyType
) -> None:
    verdict = detector(_source_context(detector_type, bucket=_bucket(in_maintenance=True)))
    _assert_insufficient(verdict, "maintenance")


def test_concentration_requires_exact_top_source_and_total() -> None:
    lower = _bucket(source_ip=_dimension(IpsBucketMeasurement.LOWER_BOUND))
    _assert_insufficient(
        detect_source_concentration_spike(
            _source_context(IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE, bucket=lower)
        ),
        "needs exact counts",
    )
    for changes in ({"top1_events": None}, {"source_events": None}):
        verdict = detect_source_concentration_spike(
            _source_context(
                IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE, bucket=_bucket(**changes)
            )
        )
        _assert_insufficient(verdict, "source-IP totals")


def test_concentration_fails_closed_for_low_coverage_and_low_total() -> None:
    low_coverage = _bucket(source_ip=_dimension(coverage=0.5))
    _assert_insufficient(
        detect_source_concentration_spike(
            _source_context(
                IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE, bucket=low_coverage
            )
        ),
        "coverage",
    )
    low_total = _bucket(source_events=40, top1_events=25, top5_events=40)
    _assert_insufficient(
        detect_source_concentration_spike(
            _source_context(IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE, bucket=low_total)
        ),
        "minimum",
    )


def test_fanout_checks_both_its_search_and_destination_port_coverage() -> None:
    missing = _bucket(max_distinct_ports=None)
    _assert_insufficient(
        detect_port_fanout_spike(
            _source_context(IpsAnomalyType.IPS_PORT_FANOUT_SPIKE, bucket=missing)
        ),
        "does not state",
    )
    low_coverage = _bucket(destination_port=_dimension(coverage=0.5))
    _assert_insufficient(
        detect_port_fanout_spike(
            _source_context(IpsAnomalyType.IPS_PORT_FANOUT_SPIKE, bucket=low_coverage)
        ),
        "destination-port coverage",
    )


def test_fanout_lower_bound_can_only_prove_an_increase() -> None:
    lower = _dimension(IpsBucketMeasurement.LOWER_BOUND, coverage=None)
    anomalous = detect_port_fanout_spike(
        _source_context(
            IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
            bucket=_bucket(port_fanout=lower, max_distinct_ports=60),
        )
    )
    not_normal = detect_port_fanout_spike(
        _source_context(
            IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
            bucket=_bucket(port_fanout=lower, max_distinct_ports=15),
        )
    )
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    _assert_insufficient(not_normal, "normal verdict cannot be concluded")


@pytest.mark.parametrize(
    ("ports", "baseline", "reason"),
    [
        (15, _cell(), "below the 20 minimum"),
        (50, _cell(median=40.0), "ratio"),
        (20, _cell(), "minimum absolute delta"),
        (70, _cell(mad=50.0), "robust z"),
        (70, _cell(mad=0.0, p95=100.0), "historical p95"),
    ],
)
def test_fanout_has_an_exact_normal_exit_for_each_conjunctive_guard(
    ports: int, baseline: IpsBaselineCell, reason: str
) -> None:
    verdict = detect_port_fanout_spike(
        _source_context(
            IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
            bucket=_bucket(max_distinct_ports=ports),
            baseline=baseline,
        )
    )
    assert verdict.signal is DetectorSignal.HEALTHY
    assert reason in verdict.evidence.reason


def test_fanout_fails_closed_when_either_required_search_is_unmeasured() -> None:
    failed = _dimension(
        IpsBucketMeasurement.FAILED, coverage=None, admission_truncated=None
    )
    for bucket, reason in (
        (_bucket(port_fanout=failed), "port fan-out search was failed"),
        (_bucket(destination_port=failed), "destination-port search was failed"),
    ):
        verdict = detect_port_fanout_spike(
            _source_context(IpsAnomalyType.IPS_PORT_FANOUT_SPIKE, bucket=bucket)
        )
        _assert_insufficient(verdict, reason)


def test_block_ratio_names_an_unmapped_action_property_exactly() -> None:
    not_collected = _dimension(
        IpsBucketMeasurement.NOT_COLLECTED, coverage=None, admission_truncated=None
    )
    verdict = detect_block_ratio_shift(
        _source_context(
            IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
            bucket=_bucket(
                action=not_collected,
                blocked_events=None,
                allowed_events=None,
                unmapped_events=None,
            ),
        )
    )
    assert verdict.evidence.reason == "no action property mapped"
    _assert_insufficient(verdict)


def test_block_ratio_fails_closed_for_failed_low_coverage_missing_and_small_denominator() -> None:
    failed = _dimension(IpsBucketMeasurement.FAILED, coverage=None)
    cases = (
        (_bucket(action=failed), "failed"),
        (_bucket(action=_dimension(coverage=0.5)), "coverage"),
        (_bucket(blocked_events=None), "does not state"),
        (_bucket(blocked_events=20, allowed_events=20), "minimum denominator"),
    )
    for bucket, reason in cases:
        verdict = detect_block_ratio_shift(
            _source_context(IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT, bucket=bucket)
        )
        _assert_insufficient(verdict, reason)


def test_unmapped_actions_make_blocked_events_increase_only() -> None:
    anomalous = detect_block_ratio_shift(
        _source_context(
            IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
            bucket=_bucket(blocked_events=60, allowed_events=20, unmapped_events=20),
        )
    )
    not_normal = detect_block_ratio_shift(
        _source_context(
            IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
            bucket=_bucket(blocked_events=5, allowed_events=75, unmapped_events=20),
        )
    )
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    assert anomalous.evidence.extra["observed_measurement"] == "LOWER_BOUND"
    _assert_insufficient(not_normal, "normal verdict cannot be concluded")


def test_pooled_detectors_refuse_a_baseline_without_a_denominator() -> None:
    no_denominator = _cell("ratio", denominator_sum=0.0)
    for detector, detector_type in (
        (detect_source_concentration_spike, IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE),
        (detect_block_ratio_shift, IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT),
    ):
        verdict = detector(_source_context(detector_type, baseline=no_denominator))
        _assert_insufficient(verdict, "no pooled denominator")


def test_concentration_has_normal_exits_for_rise_and_seasonal_score() -> None:
    rise = detect_source_concentration_spike(
        _source_context(
            IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
            bucket=_bucket(top1_events=20),
        )
    )
    score = detect_source_concentration_spike(
        _source_context(
            IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
            bucket=_bucket(top1_events=40),
            baseline=_cell("ratio", mad=0.2),
        )
    )
    assert rise.signal is DetectorSignal.HEALTHY
    assert "minimum rise" in rise.evidence.reason
    assert score.signal is DetectorSignal.HEALTHY
    assert "robust z" in score.evidence.reason


def test_block_ratio_has_normal_exits_for_rise_multiple_and_seasonal_score() -> None:
    cases = (
        (
            _bucket(blocked_events=15, allowed_events=85),
            _cell("ratio"),
            "minimum rise",
        ),
        (
            _bucket(blocked_events=35, allowed_events=65),
            _cell("ratio", numerator_sum=20.0),
            "below 2.00x",
        ),
        (
            _bucket(blocked_events=40, allowed_events=60),
            _cell("ratio", mad=0.2),
            "robust z",
        ),
    )
    for bucket, baseline, reason in cases:
        verdict = detect_block_ratio_shift(
            _source_context(
                IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
                bucket=bucket,
                baseline=baseline,
            )
        )
        assert verdict.signal is DetectorSignal.HEALTHY
        assert reason in verdict.evidence.reason


def _history_bucket(index: int, **changes: object) -> IpsSourceBucketFacts:
    return _bucket(bucket_start=START + timedelta(hours=index), **changes)


def _verify(buckets: list[IpsSourceBucketFacts], **changes: object) -> HistoryFacts:
    values: dict[str, object] = {
        "history_start": START,
        "history_end": START + timedelta(hours=2),
        "bucket_seconds": HOUR,
        "profile_version": 3,
        "normalizer_version": 2,
        "min_coverage": 0.9,
    }
    values.update(changes)
    return verify_port_history(buckets, **values)  # type: ignore[arg-type]


def test_verify_port_history_accepts_only_a_complete_comparable_window() -> None:
    assert _verify([_history_bucket(1), _history_bucket(0)]).complete is True


@pytest.mark.parametrize(
    ("buckets", "changes", "reason"),
    [
        ([_history_bucket(0), _history_bucket(1)], {"bucket_seconds": 0}, "not divisible"),
        ([_history_bucket(0)], {}, "never collected"),
        (
            [_history_bucket(0), _history_bucket(1), _history_bucket(1)],
            {},
            "bucket records",
        ),
        (
            [_history_bucket(0, bucket_seconds=300), _history_bucket(1)],
            {},
            "different collection bucket width",
        ),
        (
            [
                _history_bucket(
                    0,
                    destination_port=_dimension(IpsBucketMeasurement.LOWER_BOUND),
                ),
                _history_bucket(1),
            ],
            {},
            "not exact",
        ),
        (
            [_history_bucket(0, destination_port=_dimension(coverage=0.5)), _history_bucket(1)],
            {},
            "coverage below",
        ),
        (
            [
                _history_bucket(
                    0,
                    destination_port=_dimension(admission_truncated=True),
                ),
                _history_bucket(1),
            ],
            {},
            "truncated or unrecorded",
        ),
        ([_history_bucket(0, profile_version=4), _history_bucket(1)], {}, "profile version"),
        (
            [_history_bucket(0, normalizer_version=4), _history_bucket(1)],
            {},
            "normalizer version",
        ),
        ([_history_bucket(0, in_maintenance=True), _history_bucket(1)], {}, "maintenance"),
    ],
    ids=[
        "bad-window",
        "missing",
        "duplicate",
        "width",
        "measurement",
        "coverage",
        "admission",
        "profile",
        "normalizer",
        "maintenance",
    ],
)
def test_verify_port_history_names_each_failing_condition(
    buckets: list[IpsSourceBucketFacts], changes: dict[str, object], reason: str
) -> None:
    facts = _verify(buckets, **changes)
    assert facts.complete is False
    assert reason in (facts.reason or "")


def _novelty(**changes: object) -> IpsNoveltyFacts:
    values: dict[str, object] = {
        "first_seen_at": START,
        "window_end": START + timedelta(hours=12),
        "within_window": True,
        "readmitted": False,
        "history": HistoryFacts(complete=True),
        "history_start": START - timedelta(days=7),
        "history_end": START,
        "history_buckets": 168,
        "appearance_flood": None,
        "events_since_first_seen": 30,
        "buckets_with_traffic": 2,
        "lower_bound": False,
    }
    values.update(changes)
    return IpsNoveltyFacts(**values)  # type: ignore[arg-type]


def _novelty_context(
    *,
    novelty: IpsNoveltyFacts | None = None,
    bucket: IpsSourceBucketFacts | None = None,
    count: EntityCount | None = None,
    entity: IpsEntityFacts | None = None,
) -> IpsDetectionContext:
    return IpsDetectionContext(
        observation=IpsObservation(
            bucket=bucket or _bucket(),
            entity=entity or _entity(),
            count=count or EntityCount(30, WafMeasurement.EXACT),
        ),
        novelty=novelty,
    )


def test_new_port_has_normal_anomalous_and_re_admitted_paths() -> None:
    anomalous = detect_new_destination_port(_novelty_context(novelty=_novelty()))
    quiet = detect_new_destination_port(
        _novelty_context(novelty=_novelty(events_since_first_seen=5, buckets_with_traffic=1))
    )
    established = detect_new_destination_port(
        _novelty_context(novelty=_novelty(within_window=False))
    )
    readmitted = detect_new_destination_port(
        _novelty_context(novelty=_novelty(readmitted=True))
    )
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    assert "stayed below" in anomalous.evidence.reason
    assert quiet.signal is DetectorSignal.HEALTHY
    assert established.signal is DetectorSignal.HEALTHY
    assert readmitted.signal is DetectorSignal.HEALTHY
    assert "not new" in readmitted.evidence.reason


def test_new_port_fails_closed_for_every_novelty_guard() -> None:
    lower_dimension = _dimension(IpsBucketMeasurement.LOWER_BOUND)
    cases = (
        (_novelty_context(novelty=_novelty(), bucket=_bucket(in_maintenance=True)), "maintenance"),
        (_novelty_context(novelty=None), "first appearance"),
        (
            _novelty_context(
                novelty=_novelty(), entity=_entity(IpsDimension.DESTINATION_PORT, value_key="0/tcp")
            ),
            "absent values",
        ),
        (
            _novelty_context(
                novelty=_novelty(history=HistoryFacts(False, "bucket missing"))
            ),
            "bucket missing",
        ),
        (
            _novelty_context(novelty=_novelty(appearance_flood="appearance flood")),
            "appearance flood",
        ),
        (
            _novelty_context(
                novelty=_novelty(),
                bucket=_bucket(destination_port=_dimension(coverage=0.5)),
            ),
            "coverage",
        ),
        (
            _novelty_context(
                novelty=_novelty(),
                bucket=_bucket(destination_port=lower_dimension),
                count=EntityCount(None, None, "current count unknown"),
            ),
            "does not state whether its counts are exact",
        ),
    )
    for context, reason in cases:
        _assert_insufficient(detect_new_destination_port(context), reason)


def test_new_port_lower_bound_can_fire_but_never_returns_normal() -> None:
    lower = _dimension(IpsBucketMeasurement.LOWER_BOUND)
    anomalous = detect_new_destination_port(
        _novelty_context(
            novelty=_novelty(lower_bound=True),
            bucket=_bucket(destination_port=lower),
            count=EntityCount(30, WafMeasurement.LOWER_BOUND),
        )
    )
    not_normal = detect_new_destination_port(
        _novelty_context(
            novelty=_novelty(
                events_since_first_seen=5,
                buckets_with_traffic=1,
                lower_bound=True,
            ),
            bucket=_bucket(destination_port=lower),
            count=EntityCount(5, WafMeasurement.LOWER_BOUND),
        )
    )
    assert anomalous.signal is DetectorSignal.ANOMALOUS
    _assert_insufficient(not_normal, "lower bounds")


def test_appearance_flood_requires_exact_covered_discovery_and_whole_admission() -> None:
    assert appearance_flood(_bucket(), min_coverage=0.9) is None
    assert "discovery was incomplete" in (
        appearance_flood(
            _bucket(destination_port=_dimension(IpsBucketMeasurement.LOWER_BOUND)),
            min_coverage=0.9,
        )
        or ""
    )
    assert "admission was truncated" in (
        appearance_flood(
            _bucket(destination_port=_dimension(admission_truncated=True)), min_coverage=0.9
        )
        or ""
    )


def test_every_ips_type_has_exactly_one_pure_detector_function() -> None:
    assert set(IPS_DETECTOR_FUNCTIONS) == set(IpsAnomalyType)


def test_ips_anomaly_values_do_not_collide_with_existing_persisted_taxonomies() -> None:
    ips = {item.value for item in IpsAnomalyType}
    assert ips.isdisjoint({item.value for item in AnomalyType})
    assert ips.isdisjoint({item.value for item in WafAnomalyType})
    assert len(ips) == len(IpsAnomalyType)


def test_ips_anomaly_type_is_a_string_enum_for_evidence_serialization() -> None:
    assert all(isinstance(item, StrEnum) for item in IpsAnomalyType)
