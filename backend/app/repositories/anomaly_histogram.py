"""The anomaly histogram behind `GET /anomalies/histogram`.

Counts per UTC hour and severity over a caller-supplied range, for exactly the
population `GET /anomalies` would list for the same `since`, `until` and
`subject_kind` -- but complete, where the list is capped at a page. The
complete count is what makes an empty hour a measured zero rather than "none
seen on this page".

T-085 (the IPS analyst read API) is merged; this module now includes the IPS
branch alongside Phase A and WAF. A new family is one more branch here,
nothing else.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    String,
    cast,
    func,
    literal_column,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.waf import WafAnomaly
from app.repositories.anomaly import AnomalyRepository
from app.schemas.anomaly import SubjectKind

#: The histogram's bucket is the UTC hour.
BUCKET_SECONDS = 3600
#: Beyond a week the result is not an hour chart. Exactly 168 hours is allowed.
MAX_RANGE = timedelta(hours=168)


def check_range(start: datetime, end: datetime) -> None:
    """Reject a range past the histogram's ceiling, naming the limit.

    The ceiling is on the range, not on the bucket count: a 168-hour span that
    starts mid-hour covers 169 hour boundaries, and that is still a week.
    """
    if end - start > MAX_RANGE:
        raise ValueError(
            "range exceeds the hour histogram's maximum of 168 hours (7 days)"
        )


def hour_span(since: datetime, until: datetime) -> list[datetime]:
    """Every UTC hour of the range, in order.

    The span runs from `since` floored to its hour through the hour containing
    `until - 1 µs`: the population is half-open (`since <= detected_at <
    until`), so an `until` exactly on an hour boundary does not open that hour.
    """
    first = since.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    last = (until - timedelta(microseconds=1)).astimezone(UTC).replace(
        minute=0, second=0, microsecond=0
    )
    if last < first:
        return []
    hours = [first]
    while hours[-1] < last:
        hours.append(hours[-1] + timedelta(hours=1))
    return hours


@dataclass(frozen=True)
class HourCounts:
    """One hour of the histogram: its severities and their counts."""

    start: datetime
    #: Only severities with a non-zero count in this hour. A severity absent
    #: from the mapping counted zero in it; the hour itself is always present.
    counts: dict[str, int]
    total: int


@dataclass(frozen=True)
class Histogram:
    """The complete hour-by-hour count, ready for `AnomalyHistogramOut`."""

    families: list[str]
    hours: list[HourCounts]
    total: int


def _hour_of(column: Any) -> ColumnElement[datetime]:
    """`date_trunc` pinned to UTC, whatever the server session's TimeZone is.

    `date_trunc` on a `timestamptz` truncates in the session zone, and a
    half-hour-offset zone would silently shift every bucket boundary.
    """
    return func.date_trunc("hour", func.timezone("UTC", column))


def _phase_a_branch(since: datetime, until: datetime) -> Select:
    """One GROUP BY per family, as the design's bulk-read shape requires."""
    a = LogSourceAnomaly
    hour = _hour_of(a.detected_at)
    return (
        select(
            hour.label("hour"),
            cast(literal_column("'LOG_SOURCE'"), String).label("family"),
            a.severity.label("severity"),
            func.count().label("anomalies"),
        )
        .where(a.detected_at >= since, a.detected_at < until)
        .group_by(hour, a.severity)
    )


def _waf_branch(since: datetime, until: datetime) -> Select:
    w = WafAnomaly
    hour = _hour_of(w.detected_at)
    return (
        select(
            hour.label("hour"),
            cast(literal_column("'WAF_ENDPOINT'"), String).label("family"),
            w.severity.label("severity"),
            func.count().label("anomalies"),
        )
        .where(w.detected_at >= since, w.detected_at < until)
        .group_by(hour, w.severity)
    )


def _ips_branch(
    since: datetime, until: datetime, subject_kind: SubjectKind | None = None
) -> Select:
    """IPS anomalies grouped by hour and severity, filtered by subject_kind.

    When `subject_kind` is IPS_ENTITY or IPS_SOURCE, filters
    `IpsAnomaly.subject_kind == subject_kind.value`, matching the list
    behavior. No joins needed: the histogram only counts rows.
    """
    a = IpsAnomaly
    hour = _hour_of(a.detected_at)
    conditions = [a.detected_at >= since, a.detected_at < until]
    if subject_kind in (SubjectKind.IPS_ENTITY, SubjectKind.IPS_SOURCE):
        conditions.append(a.subject_kind == subject_kind.value)
    return (
        select(
            hour.label("hour"),
            cast(literal_column("'IPS'"), String).label("family"),
            a.severity.label("severity"),
            func.count().label("anomalies"),
        )
        .where(*conditions)
        .group_by(hour, a.severity)
    )


def fill_hours(
    span: list[datetime], counts: dict[datetime, dict[str, int]]
) -> list[HourCounts]:
    """Gap-fill the grouped counts: every hour of the span is present.

    The count is complete -- no cap, no page -- so an hour with nothing in it
    is a measured zero, unlike the same hour in the capped list.
    """
    return [
        HourCounts(
            start=start,
            counts=counts.get(start, {}),
            total=sum(counts.get(start, {}).values()),
        )
        for start in span
    ]


async def histogram(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
    subject_kind: SubjectKind | None = None,
) -> Histogram:
    """Count anomalies per UTC hour and severity: one statement, any range.

    A `UNION ALL` of one `GROUP BY` per family over `date_trunc('hour',
    detected_at)` and severity; the hour axis and the gap-fill happen in
    Python. Every state is counted -- the list's default, no `active_only` --
    and the families are the same ones `GET /anomalies` would serve for the
    same `subject_kind`, so `total` equals the list's `total` for the same
    arguments.
    """
    phase_a, waf, ips = AnomalyRepository._applicable_kinds(
        subject_kind=subject_kind, waf_endpoint_id=None, ips_entity_id=None, anomaly_type=None
    )
    branches: list[Select] = []
    families: list[str] = []
    if phase_a:
        branches.append(_phase_a_branch(since, until))
        families.append(SubjectKind.LOG_SOURCE.value)
    if waf:
        branches.append(_waf_branch(since, until))
        families.append(SubjectKind.WAF_ENDPOINT.value)
    if ips:
        branches.append(_ips_branch(since, until, subject_kind))
        if subject_kind in (SubjectKind.IPS_ENTITY, SubjectKind.IPS_SOURCE):
            families.append(subject_kind.value)
        else:
            families.extend([SubjectKind.IPS_ENTITY.value, SubjectKind.IPS_SOURCE.value])
    span = hour_span(since, until)
    if not branches:
        # Reachable only if a future SubjectKind is added without a branch here.
        return Histogram(families=[], hours=fill_hours(span, {}), total=0)

    rows = (await session.execute(union_all(*branches))).all()
    by_hour: dict[datetime, dict[str, int]] = {}
    for hour, _family, severity, count in rows:
        # `timezone('UTC', ...)` yields a naive UTC stamp; the span is aware.
        key = hour if hour.tzinfo is not None else hour.replace(tzinfo=UTC)
        bucket = by_hour.setdefault(key, {})
        # One row per family: the same (hour, severity) arrives once per branch
        # and the families must add up, not overwrite one another.
        bucket[str(severity)] = bucket.get(str(severity), 0) + int(count)
    hours = fill_hours(span, by_hour)
    return Histogram(
        families=families, hours=hours, total=sum(hour.total for hour in hours)
    )
