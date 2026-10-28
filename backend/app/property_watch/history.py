"""Pure verification for property-watch novelty claims (design §G.8.3)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.i18n.messages import MessageRef, render
from app.models.enums import PwBucketMeasurement, PwBucketOrigin
from app.property_watch.facts import PwBucketFact
from app.waf.inputs import HistoryFacts


@dataclass(frozen=True)
class PwHistoryFacts(HistoryFacts):
    """`HistoryFacts` whose reason is also a registry reference (the English is its render)."""

    message: MessageRef | None = None


def _incomplete(message: MessageRef) -> PwHistoryFacts:
    return PwHistoryFacts(complete=False, reason=render(message), message=message)


def _check(name: str, **params: object) -> MessageRef:
    return MessageRef(f"pw.history_check.{name}", params)


def verify_value_history(
    buckets: Sequence[PwBucketFact],
    *,
    history_start: datetime,
    history_end: datetime,
    bucket_seconds: int,
    watch_version: int,
    min_coverage: float,
) -> HistoryFacts:
    """Prove the pre-appearance window had whole discovery and admission."""
    if not 0.0 <= min_coverage <= 1.0:
        raise ValueError("coverage must be between zero and one")
    window_seconds = int((history_end - history_start).total_seconds())
    if bucket_seconds <= 0 or window_seconds <= 0 or window_seconds % bucket_seconds:
        return _incomplete(_check("not_divisible"))
    expected_count = window_seconds // bucket_seconds
    expected_starts = {
        history_start + timedelta(seconds=bucket_seconds * index)
        for index in range(expected_count)
    }
    starts = {bucket.bucket_start for bucket in buckets}
    missing = sorted(expected_starts - starts)
    if missing:
        return _incomplete(
            _check(
                "bucket_never_collected",
                bucket=missing[0],
                missing=len(missing),
                expected=expected_count,
            )
        )
    if len(buckets) != expected_count:
        return _incomplete(_check("record_count", records=len(buckets), expected=expected_count))

    ordered = sorted(buckets, key=lambda bucket: bucket.bucket_start)
    checks: tuple[tuple[Callable[[PwBucketFact], bool], str, dict[str, object]], ...] = (
        (lambda bucket: bucket.bucket_seconds != bucket_seconds, "bucket_width", {}),
        (
            lambda bucket: bucket.values_measurement is PwBucketMeasurement.NOT_COLLECTED,
            "bucket_not_collected",
            {},
        ),
        (
            lambda bucket: bucket.values_measurement is PwBucketMeasurement.LOWER_BOUND,
            "discovery_truncated",
            {},
        ),
        (
            lambda bucket: bucket.admission_truncated or bucket.rejected_over_quota > 0,
            "admission_truncated",
            {},
        ),
        (
            lambda bucket: bucket.watch_version != watch_version,
            "bucket_version",
            {"version": watch_version},
        ),
    )
    for failing, name, extra in checks:
        for bucket in ordered:
            if failing(bucket):
                return _incomplete(_check(name, bucket=bucket.bucket_start, **extra))
    for bucket in ordered:
        if (
            bucket.origin is PwBucketOrigin.HISTORY
            and bool(bucket.total_events)
            and not bucket.coverage.reaches(min_coverage)
        ):
            return _incomplete(_check("bucket_coverage", bucket=bucket.bucket_start))
    return HistoryFacts(complete=True)
