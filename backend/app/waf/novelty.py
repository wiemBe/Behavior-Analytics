"""Pure verification of the history behind a new-endpoint claim."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from app.waf.inputs import HistoryFacts, history_incomplete
from app.waf.observations import BucketFacts


def semantics_normalizer_version(semantics: str | None) -> int | None:
    """The endpoint-normalizer version an identity was produced under, if it says.

    Identities are ``;``-separated components (`app.waf.semantics`), one of which
    is ``normalizer/v<N>``. Read as a component, never as a substring.
    """
    if semantics is None:
        return None
    for part in semantics.split(";"):
        prefix, _, number = part.partition("/v")
        if prefix == "normalizer" and number.isdigit():
            return int(number)
    return None


def verify_endpoint_history(
    buckets: Sequence[BucketFacts],
    *,
    history_start: datetime,
    history_end: datetime,
    bucket_seconds: int,
    profile_version: int,
    normalizer_version: int,
) -> HistoryFacts:
    """Prove every bucket in ``[history_start, history_end)`` was comparable.

    The order of these guards is the order reported to operators: absence is
    established before the contents of the buckets that did arrive are trusted.
    """
    window_seconds = int((history_end - history_start).total_seconds())
    if bucket_seconds <= 0 or window_seconds % bucket_seconds:
        return history_incomplete("not_divisible")
    expected_count = window_seconds // bucket_seconds
    expected_starts = {
        history_start + timedelta(seconds=bucket_seconds * index)
        for index in range(expected_count)
    }
    starts = {bucket.bucket_start for bucket in buckets}
    missing = len(expected_starts - starts)
    if missing:
        return history_incomplete("never_collected", missing=missing, expected=expected_count)
    if len(buckets) != expected_count:
        return history_incomplete("record_count", records=len(buckets), expected=expected_count)

    ordered = sorted(buckets, key=lambda bucket: bucket.bucket_start)
    for bucket in ordered:
        if bucket.bucket_seconds != bucket_seconds:
            return history_incomplete("bucket_width", bucket=bucket.bucket_start)
    for bucket in ordered:
        if bucket.groups_truncated:
            return history_incomplete("discovery_truncated", bucket=bucket.bucket_start)
    for bucket in ordered:
        if bucket.admission_truncated:
            return history_incomplete("admission_truncated", bucket=bucket.bucket_start)
    for bucket in ordered:
        if bucket.profile_version != profile_version:
            return history_incomplete(
                "profile_version",
                bucket=bucket.bucket_start,
                found=str(bucket.profile_version),
                expected=profile_version,
            )

    for bucket in ordered:
        if semantics_normalizer_version(bucket.metric_semantics) != normalizer_version:
            return history_incomplete(
                "normalizer_version", bucket=bucket.bucket_start, version=normalizer_version
            )
    return HistoryFacts(complete=True)
