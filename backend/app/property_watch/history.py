"""Pure verification for property-watch novelty claims (design §G.8.3)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from app.models.enums import PwBucketMeasurement
from app.property_watch.facts import PwBucketFact
from app.waf.inputs import HistoryFacts


def verify_value_history(
    buckets: Sequence[PwBucketFact],
    *,
    history_start: datetime,
    history_end: datetime,
    bucket_seconds: int,
    watch_version: int,
) -> HistoryFacts:
    """Prove the pre-appearance window had whole discovery and admission."""
    window_seconds = int((history_end - history_start).total_seconds())
    if bucket_seconds <= 0 or window_seconds <= 0 or window_seconds % bucket_seconds:
        return HistoryFacts(
            complete=False,
            reason="the history window is not divisible by the collection bucket width",
        )
    expected_count = window_seconds // bucket_seconds
    expected_starts = {
        history_start + timedelta(seconds=bucket_seconds * index)
        for index in range(expected_count)
    }
    starts = {bucket.bucket_start for bucket in buckets}
    missing = sorted(expected_starts - starts)
    if missing:
        return HistoryFacts(
            complete=False,
            reason=(
                f"history bucket {missing[0].isoformat()} was never collected "
                f"({len(missing)} of {expected_count} missing)"
            ),
        )
    if len(buckets) != expected_count:
        return HistoryFacts(
            complete=False,
            reason=(
                f"the history contains {len(buckets)} bucket records for "
                f"{expected_count} expected buckets"
            ),
        )

    ordered = sorted(buckets, key=lambda bucket: bucket.bucket_start)
    checks = (
        (
            lambda bucket: bucket.bucket_seconds != bucket_seconds,
            "used a different collection bucket width",
        ),
        (
            lambda bucket: bucket.values_measurement is PwBucketMeasurement.NOT_COLLECTED,
            "was NOT_COLLECTED",
        ),
        (
            lambda bucket: bucket.values_measurement is PwBucketMeasurement.LOWER_BOUND,
            "had truncated value discovery",
        ),
        (
            lambda bucket: bucket.admission_truncated or bucket.rejected_over_quota > 0,
            "had truncated value admission",
        ),
        (
            lambda bucket: bucket.watch_version != watch_version,
            f"used another watch version, not first-appearance version {watch_version}",
        ),
    )
    for failing, wording in checks:
        for bucket in ordered:
            if failing(bucket):
                return HistoryFacts(
                    complete=False,
                    reason=f"history bucket {bucket.bucket_start.isoformat()} {wording}",
                )
    return HistoryFacts(complete=True)
