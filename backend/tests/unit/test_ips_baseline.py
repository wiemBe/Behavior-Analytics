"""Pure IPS baseline exclusions and natural-key fingerprints."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.alerts.fingerprint import ips_anomaly_fingerprint
from app.ips.baseline import clean_entity_samples, clean_source_samples
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
    IpsBucketMeasurement,
    IpsDimensionFacts,
    IpsEntityFacts,
    IpsSourceBucketFacts,
)
from app.models.enums import IpsDimension

START = datetime(2026, 9, 7, 10, tzinfo=UTC)
STEP = timedelta(hours=1)


def dimension(
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


def bucket(at: datetime = START, **overrides: object) -> IpsSourceBucketFacts:
    values: dict[str, object] = {
        "bucket_start": at,
        "bucket_seconds": 3600,
        "total_events": 100,
        "destination_port": dimension(),
        "destination_ip": dimension(),
        "signature": dimension(),
        "source_ip": dimension(),
        "port_fanout": dimension(coverage=None),
        "action": dimension(),
        "source_events": 100,
        "top1_events": 25,
        "max_distinct_ports": 30,
        "blocked_events": 20,
        "allowed_events": 80,
        "unmapped_events": 0,
        "profile_version": 3,
        "normalizer_version": 1,
        "in_maintenance": False,
    }
    values.update(overrides)
    return IpsSourceBucketFacts(**values)  # type: ignore[arg-type]


def entity() -> IpsEntityFacts:
    return IpsEntityFacts(
        dimension=IpsDimension.DESTINATION_PORT,
        value_key="443/tcp",
        display_name="443/tcp",
        first_seen_at=START - STEP,
    )


def clean_entity(
    facts: IpsSourceBucketFacts, row: tuple[int, int] | None = (12, 3600), **overrides: object
):
    kwargs: dict[str, object] = {
        "train_through": START + STEP,
        "bucket_seconds": 3600,
        "profile_version": 3,
        "normalizer_version": 1,
        "min_coverage": 0.9,
    }
    kwargs.update(overrides)
    rows = {} if row is None else {facts.bucket_start: row}
    return clean_entity_samples(entity(), [facts], rows, **kwargs)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("facts", "overrides", "reason"),
    [
        (bucket(in_maintenance=True), {}, "maintenance"),
        (bucket(), {"train_through": START + timedelta(minutes=30)}, "unfinished"),
        (bucket(bucket_seconds=300), {}, "width_mismatch"),
        (bucket(profile_version=2), {}, "version_mismatch"),
        (
            bucket(),
            {"spans": [(START - timedelta(minutes=1), START + timedelta(minutes=1))]},
            "known_anomaly",
        ),
    ],
)
def test_entity_common_exclusions_are_counted_on_their_own(
    facts: IpsSourceBucketFacts, overrides: dict[str, object], reason: str
) -> None:
    samples, tally, candidates = clean_entity(facts, **overrides)
    assert samples == []
    assert candidates == 1
    assert tally.as_dict()[reason] == 1
    assert tally.total == 1


def test_lower_bound_entity_row_never_trains() -> None:
    facts = bucket(destination_port=dimension(IpsBucketMeasurement.LOWER_BOUND))
    samples, tally, _ = clean_entity(facts)
    assert samples == []
    assert tally.lower_bound == 1


def test_unknown_sparse_absence_never_trains() -> None:
    facts = bucket(destination_port=dimension(admission_truncated=True))
    samples, tally, _ = clean_entity(facts, row=None)
    assert samples == []
    assert tally.unknown == 1


def test_exact_sparse_absence_trains_a_measured_zero() -> None:
    samples, tally, _ = clean_entity(bucket(), row=None)
    assert [sample.value for sample in samples] == [0.0]
    assert tally.total == 0


def test_entity_coverage_below_floor_never_trains_even_with_a_row() -> None:
    facts = bucket(destination_port=dimension(coverage=0.5))
    samples, tally, _ = clean_entity(facts)
    assert samples == []
    assert tally.low_coverage == 1


@pytest.mark.parametrize(
    ("metric", "facts", "reason"),
    [
        (
            IPS_TOP_SOURCE_SHARE_METRIC,
            bucket(source_ip=dimension(IpsBucketMeasurement.LOWER_BOUND)),
            "lower_bound",
        ),
        (
            IPS_TOP_SOURCE_SHARE_METRIC,
            bucket(source_ip=dimension(coverage=0.5)),
            "low_coverage",
        ),
        (IPS_BLOCK_RATIO_METRIC, bucket(unmapped_events=1), "unmapped_actions"),
        (
            IPS_FANOUT_METRIC,
            bucket(port_fanout=dimension(IpsBucketMeasurement.NOT_COLLECTED, coverage=None)),
            "unknown",
        ),
        (
            IPS_FANOUT_METRIC,
            bucket(destination_port=dimension(coverage=0.5)),
            "low_coverage",
        ),
    ],
)
def test_source_metric_exclusions_are_counted_on_their_own(
    metric: str, facts: IpsSourceBucketFacts, reason: str
) -> None:
    samples, tally, candidates = clean_source_samples(
        [facts],
        metric,
        train_through=START + STEP,
        bucket_seconds=3600,
        profile_version=3,
        normalizer_version=1,
        min_coverage=0.9,
    )
    assert samples == []
    assert candidates == 1
    assert tally.as_dict()[reason] == 1
    assert tally.total == 1


def test_source_ratio_sample_retains_the_pooled_terms() -> None:
    samples, tally, _ = clean_source_samples(
        [bucket()],
        IPS_TOP_SOURCE_SHARE_METRIC,
        train_through=START + STEP,
        bucket_seconds=3600,
        profile_version=3,
        normalizer_version=1,
        min_coverage=0.9,
    )
    assert [(sample.value, sample.numerator, sample.denominator) for sample in samples] == [
        (0.25, 25, 100)
    ]
    assert tally.total == 0


def test_ips_fingerprint_format_is_pinned_and_stable() -> None:
    values = {
        "instance_id": uuid.UUID("11111111-1111-1111-1111-111111111111"),
        "log_source_id": uuid.UUID("22222222-2222-2222-2222-222222222222"),
        "dimension": "DESTINATION_PORT",
        "value_key": "443/tcp",
        "detector_type": "IPS_PORT_RATE_SPIKE",
    }
    assert ips_anomaly_fingerprint(**values) == ips_anomaly_fingerprint(**values)
    assert ips_anomaly_fingerprint(**values) == "ips_anomaly:a794b8a5a32ddf3f524c7a6d6ac73ed4"


def test_ips_fingerprint_uses_every_subject_part_but_never_a_measurement_or_time() -> None:
    source = ips_anomaly_fingerprint(
        instance_id="instance",
        log_source_id="source",
        detector_type="IPS_PORT_FANOUT_SPIKE",
    )
    entity_fp = ips_anomaly_fingerprint(
        instance_id="instance",
        log_source_id="source",
        dimension="DESTINATION_PORT",
        value_key="443/tcp",
        detector_type="IPS_PORT_RATE_SPIKE",
    )
    changed = ips_anomaly_fingerprint(
        instance_id="instance",
        log_source_id="source",
        dimension="DESTINATION_PORT",
        value_key="8443/tcp",
        detector_type="IPS_PORT_RATE_SPIKE",
    )
    assert len({source, entity_fp, changed}) == 3
    with pytest.raises(ValueError, match="dimension and value_key"):
        ips_anomaly_fingerprint(
            instance_id="instance",
            log_source_id="source",
            dimension="DESTINATION_PORT",
            detector_type="IPS_PORT_RATE_SPIKE",
        )
