"""Pure verification of the history behind a new-endpoint claim."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta

from app.waf.inputs import HistoryFacts
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
    missing = len(expected_starts - starts)
    if missing:
        return HistoryFacts(
            complete=False,
            reason=f"{missing} of {expected_count} history buckets were never collected",
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
    for bucket in ordered:
        if bucket.bucket_seconds != bucket_seconds:
            return HistoryFacts(
                complete=False,
                reason=(
                    f"history bucket {bucket.bucket_start.isoformat()} used a different "
                    "collection bucket width"
                ),
            )
    for bucket in ordered:
        if bucket.groups_truncated:
            return HistoryFacts(
                complete=False,
                reason=(
                    f"history bucket {bucket.bucket_start.isoformat()} had truncated "
                    "endpoint discovery"
                ),
            )
    for bucket in ordered:
        if bucket.admission_truncated:
            return HistoryFacts(
                complete=False,
                reason=(
                    f"history bucket {bucket.bucket_start.isoformat()} had truncated "
                    "endpoint admission"
                ),
            )
    for bucket in ordered:
        if bucket.profile_version != profile_version:
            return HistoryFacts(
                complete=False,
                reason=(
                    f"history bucket {bucket.bucket_start.isoformat()} used profile version "
                    f"{bucket.profile_version}, not first-appearance version {profile_version}"
                ),
            )

    for bucket in ordered:
        if semantics_normalizer_version(bucket.metric_semantics) != normalizer_version:
            return HistoryFacts(
                complete=False,
                reason=(
                    f"history bucket {bucket.bucket_start.isoformat()} is incompatible with "
                    f"endpoint normalizer version {normalizer_version}"
                ),
            )
    return HistoryFacts(complete=True)
