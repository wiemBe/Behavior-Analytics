"""Metric-semantics identity for WAF behavioral telemetry.

A baseline is a statistic over observations that mean the same thing. What a
WAF `request_count` means is decided by four things, and a change to any of them
makes old and new observations incomparable:

  * the definition of the metric itself;
  * how QRadar events are counted -- `SUM(eventcount)` versus a record count,
    the difference commit ae9932a corrected in Phase A;
  * the endpoint normalizer, which decides what an endpoint *is*;
  * the source profile's property mapping, which decides which QRadar property
    the endpoint and method are read from.

Every metric row, source bucket and baseline cell records the identity it was
produced under. The baseline builder trains only on rows matching the identity
it is rebuilding for, and the engine refuses a cell whose identity differs from
the bucket it judges. A change to any component therefore *fails closed* --
`INSUFFICIENT_DATA` until baselines are rebuilt from compatible rows -- rather
than quietly blending two populations, which is what happened to Phase A
baselines spanning ae9932a.

Rows written before identities existed carry NULL, and NULL matches nothing.
Old rows are not guessed at.
"""

from __future__ import annotations

#: Bump when the meaning of `request_count` changes in a way no other component
#: of the identity captures.
REQUEST_COUNT_DEFINITION_VERSION = 1


def request_count_semantics(
    *, aggregation: str | None, normalizer_version: int, profile_version: int
) -> str:
    """The identity of `request_count` under the given components."""
    return (
        f"request_count/v{REQUEST_COUNT_DEFINITION_VERSION}"
        f";agg={aggregation or 'unknown'}"
        f";normalizer/v{normalizer_version}"
        f";profile/v{profile_version}"
    )


#: Bump a breakdown's definition when what its counts mean changes in a way no
#: other component captures -- for example which response codes count as 5xx,
#: or how an action value is matched against the profile's map.
BREAKDOWN_DEFINITION_VERSIONS: dict[str, int] = {
    "ACTION": 1,
    "RESPONSE_CODE": 1,
    "SOURCE_IP": 1,
}


def breakdown_semantics(
    breakdown: str, *, aggregation: str | None, normalizer_version: int, profile_version: int
) -> str:
    """The identity of one per-endpoint breakdown (`app.models.enums.WafBreakdown`).

    Separate from `request_count`'s identity, so a ratio baseline can never be
    trained or judged across a change to the breakdown's own definition. The
    action map is part of the profile, so editing it bumps `profile_version`
    and with it every identity here.
    """
    return (
        f"{breakdown.lower()}/v{BREAKDOWN_DEFINITION_VERSIONS[breakdown]}"
        f";agg={aggregation or 'unknown'}"
        f";normalizer/v{normalizer_version}"
        f";profile/v{profile_version}"
    )


def semantics_incompatibility(bucket: str | None, baseline: str | None) -> str | None:
    """Why a baseline cannot judge a bucket, or None when it can."""
    if bucket is None:
        return (
            "the bucket carries no metric-semantics identity; it was collected before "
            "identities were recorded and cannot be judged"
        )
    if baseline is None:
        return "the baseline cell carries no metric-semantics identity; rebuild required"
    if bucket != baseline:
        return (
            f"the baseline was built under '{baseline}' but the bucket was collected "
            f"under '{bucket}'; rebuild required"
        )
    return None
