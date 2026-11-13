"""SOC-13a: `validate_interval` and `coalesce`, every exit path."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly import training_exclusions as te

BUCKET = 900
INC_START = datetime(2026, 11, 4, 8, 0, tzinfo=UTC)
INC_END = datetime(2026, 11, 4, 10, 0, tzinfo=UTC)


def _check(start: datetime, end: datetime, bucket: int = BUCKET) -> str | None:
    return te.validate_interval(
        start, end, bucket_seconds=bucket, incident_start=INC_START, incident_end=INC_END
    )


def test_exact_incident_span_is_accepted() -> None:
    assert _check(INC_START, INC_END) is None


def test_widening_is_accepted() -> None:
    assert _check(INC_START - timedelta(seconds=BUCKET), INC_END + timedelta(hours=1)) is None


def test_naive_datetime_is_invalid() -> None:
    assert _check(INC_START.replace(tzinfo=None), INC_END) == te.INTERVAL_INVALID


def test_non_positive_bucket_is_invalid() -> None:
    assert _check(INC_START, INC_END, bucket=0) == te.INTERVAL_INVALID


@pytest.mark.parametrize("delta", [timedelta(0), timedelta(seconds=-BUCKET)])
def test_start_not_before_end_is_invalid(delta: timedelta) -> None:
    assert _check(INC_END, INC_END + delta) == te.INTERVAL_INVALID


def test_unaligned_start_is_invalid() -> None:
    assert _check(INC_START - timedelta(seconds=60), INC_END) == te.INTERVAL_INVALID


def test_unaligned_end_is_invalid() -> None:
    assert _check(INC_START, INC_END + timedelta(seconds=60)) == te.INTERVAL_INVALID


def test_seven_days_is_allowed_and_more_is_too_long() -> None:
    start = INC_START
    assert _check(start, start + timedelta(days=7)) is None
    assert _check(start, start + timedelta(days=7, seconds=BUCKET)) == te.INTERVAL_TOO_LONG


def test_start_after_incident_start_is_not_widened() -> None:
    assert _check(INC_START + timedelta(seconds=BUCKET), INC_END) == te.INTERVAL_NOT_WIDENED


def test_end_before_incident_end_is_not_widened() -> None:
    assert _check(INC_START, INC_END - timedelta(seconds=BUCKET)) == te.INTERVAL_NOT_WIDENED


def test_coalesce_merges_overlap_and_touching_keeps_gaps() -> None:
    t = INC_START
    h = timedelta(hours=1)
    spans = [(t + 5 * h, t + 6 * h), (t, t + 2 * h), (t + h, t + 3 * h), (t + 3 * h, t + 4 * h)]
    assert te.coalesce(spans) == [(t, t + 4 * h), (t + 5 * h, t + 6 * h)]


def test_coalesce_contained_span_does_not_shrink() -> None:
    t = INC_START
    h = timedelta(hours=1)
    assert te.coalesce([(t, t + 5 * h), (t + h, t + 2 * h)]) == [(t, t + 5 * h)]
