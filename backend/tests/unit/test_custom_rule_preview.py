"""Preview intervals are the fold's, from a clean counter (design §F.7).

The expected bucket is written out. The test does not ask the fold a second
time and compare the two answers.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.custom_rules.evaluate import InterpretedReading
from app.custom_rules.preview import preview_from_readings
from app.models.enums import CustomRuleMetric

T0 = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)
NOW = datetime(2026, 7, 15, 12, 0, tzinfo=UTC)


def _reading(
    minute: int,
    value: float | None,
    measurement: str | None = "EXACT",
    *,
    in_maintenance: bool = False,
) -> InterpretedReading:
    return InterpretedReading(
        bucket_start=T0 + timedelta(minutes=minute),
        value=value,
        measurement=None if value is None else measurement,
        in_maintenance=in_maintenance,
        reason=None if value is not None and not in_maintenance else "paused",
    )


def _preview(readings: list[InterpretedReading], **overrides: object):  # type: ignore[no-untyped-def]
    values: dict[str, object] = {
        "metric": CustomRuleMetric.EVENT_COUNT,
        "operator": "GT",
        "threshold_value": 100.0,
        "sustain_buckets": 2,
        "recovery_buckets": 2,
        "severity": "HIGH",
        "subject_kind": "LOG_SOURCE",
        "now": NOW,
    }
    values.update(overrides)
    return preview_from_readings(
        readings,
        completeness=[None] * len(readings),
        metric=values["metric"],  # type: ignore[arg-type]
        operator=str(values["operator"]),
        threshold_value=float(values["threshold_value"]),  # type: ignore[arg-type]
        sustain_buckets=int(values["sustain_buckets"]),  # type: ignore[arg-type]
        recovery_buckets=int(values["recovery_buckets"]),  # type: ignore[arg-type]
        severity=str(values["severity"]),
        subject_kind=str(values["subject_kind"]),
        now=NOW,
    )


def test_exact_sustain_opens_on_the_second_high_and_recovery_closes() -> None:
    """Three highs then two lows. Sustain 2 opens on the second high."""
    result = _preview(
        [
            _reading(0, 200),
            _reading(5, 200),
            _reading(10, 200),
            _reading(15, 1),
            _reading(20, 1),
        ]
    )
    assert result.fired_count == 1
    assert result.measured_count == 5
    assert result.unmeasured_count == 0
    assert result.unit == "events/bucket"
    assert len(result.breach_intervals) == 1
    assert result.breach_intervals[0].start == T0 + timedelta(minutes=5)
    assert result.breach_intervals[0].end == T0 + timedelta(minutes=20)
    assert [bucket.measurement for bucket in result.buckets] == ["EXACT"] * 5


def test_a_lower_bound_opens_and_a_later_low_bound_does_not_resolve() -> None:
    result = _preview(
        [
            _reading(0, 5.0, "LOWER_BOUND"),
            _reading(5, 0.1, "LOWER_BOUND"),
        ],
        metric=CustomRuleMetric.EPS,
        threshold_value=1.0,
        sustain_buckets=1,
        recovery_buckets=1,
        subject_kind="FLEET_CLASS",
    )
    assert result.fired_count == 1
    assert result.measured_count == 2
    assert result.unmeasured_count == 0
    assert result.unit == "events/s"
    assert len(result.breach_intervals) == 1
    assert result.breach_intervals[0].start == T0
    assert result.breach_intervals[0].end is None
    assert [bucket.measurement for bucket in result.buckets] == ["LOWER_BOUND", "LOWER_BOUND"]


def test_an_unmeasured_gap_pauses_and_does_not_reset() -> None:
    """Sustain 2: high, gap, high opens on the second high, not the gap."""
    result = _preview(
        [
            _reading(0, 200),
            _reading(5, None),
            _reading(10, 200),
        ]
    )
    assert result.fired_count == 1
    assert result.measured_count == 2
    assert result.unmeasured_count == 1
    assert result.buckets[1].measurement == "NOT_MEASURED"
    assert result.buckets[1].value is None
    assert result.breach_intervals[0].start == T0 + timedelta(minutes=10)
    assert result.breach_intervals[0].end is None


def test_a_gap_between_unders_does_not_reset_recovery() -> None:
    """Recovery 2 resolves on the second under when a gap sits between them."""
    result = _preview(
        [
            _reading(0, 200),
            _reading(5, 1),
            _reading(10, None),
            _reading(15, 1),
        ],
        sustain_buckets=1,
        recovery_buckets=2,
    )
    assert result.fired_count == 1
    assert result.unmeasured_count == 1
    assert result.breach_intervals[0].start == T0
    assert result.breach_intervals[0].end == T0 + timedelta(minutes=15)


def test_maintenance_pauses_sustain_and_keeps_the_measured_value() -> None:
    result = _preview(
        [
            _reading(0, 200),
            _reading(5, 200, in_maintenance=True),
            _reading(10, 200),
        ]
    )
    assert result.fired_count == 1
    assert result.measured_count == 3
    assert result.unmeasured_count == 0
    assert result.buckets[1].in_maintenance is True
    assert result.buckets[1].measurement == "EXACT"
    assert result.buckets[1].value == 200
    assert result.breach_intervals[0].start == T0 + timedelta(minutes=10)
    assert result.breach_intervals[0].end is None
