"""Bulk metric loaders for custom rules (design §F.4.2).

One statement per subject kind per tick, over the union of that kind's
subjects. The SQL does not grow with the number of rules (AGENTS.md rule 9).
An empty subject list is not sent: an ``IN ()`` is a statement we do not need,
and it is not legal SQL.

Rows come back as facts keyed by subject, then bucket start. A subject with
no row is simply absent; the fold reads that as unmeasured, never as zero.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import and_, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.custom_rules.evaluate import BucketFacts
from app.history.origin import is_live_clause
from app.models.enums import (
    BucketCompleteness,
    CustomRuleSubjectKind,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket
from app.models.log_source import LogSource, LogSourceMetric
from app.models.property_watch import PwBucket, PwValue, PwValueMetric
from app.models.waf import WafEndpoint, WafEndpointMetric, WafSourceBucket

_UNCLASSIFIED = SourceClass.UNCLASSIFIED.value


def implemented_loaders() -> frozenset[str]:
    """The subject kinds that have a bulk loader. The catalog guard pins this."""
    return frozenset(kind.value for kind in CustomRuleSubjectKind)


def _class_of() -> ColumnElement[str]:
    """A source with no classification row is UNCLASSIFIED, never absent.

    The same expression ``app.fleet.summary`` uses. Classification writes the
    winner, including an override, into ``source_class``.
    """
    return func.coalesce(LogSourceClassification.source_class, literal(_UNCLASSIFIED))


def _index(
    rows: list[tuple[str, datetime, BucketFacts]],
) -> dict[str, dict[datetime, BucketFacts]]:
    facts: dict[str, dict[datetime, BucketFacts]] = {}
    for key, bucket_start, fact in rows:
        facts.setdefault(key, {})[bucket_start] = fact
    return facts


async def load_log_sources(
    session: AsyncSession,
    log_source_ids: list[uuid.UUID],
    *,
    after: datetime,
    through: datetime,
    include_history: bool = False,
) -> dict[str, dict[datetime, BucketFacts]]:
    """``log_source_metric`` rows for the named sources in the open window.

    A LIVE-only reader by default (HISTORY-BACKFILL §B.4.1, R5): a row read back from QRadar
    history (`collection_source = 'ariel_history'`) is learning evidence, never a bucket an F.1
    rule may breach on, also when the kind's watermark lies before it. Only the preview, which
    replays and persists nothing, passes ``include_history=True``.
    """
    if not log_source_ids:
        return {}
    rows = (
        await session.execute(
            select(
                LogSourceMetric.log_source_id,
                LogSourceMetric.bucket_start,
                LogSourceMetric.event_count,
                LogSourceMetric.average_eps,
                LogSourceMetric.completeness,
            ).where(
                LogSourceMetric.log_source_id.in_(log_source_ids),
                LogSourceMetric.bucket_start > after,
                LogSourceMetric.bucket_start <= through,
                *([] if include_history else [is_live_clause(LogSourceMetric)]),
            )
        )
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for source_id, bucket_start, event_count, average_eps, completeness in rows:
        built.append(
            (
                str(source_id),
                bucket_start,
                BucketFacts(
                    bucket_start=bucket_start,
                    present=True,
                    event_count=int(event_count),
                    average_eps=float(average_eps),
                    completeness=str(completeness),
                ),
            )
        )
    return _index(built)


async def load_fleet_classes(
    session: AsyncSession,
    source_classes: list[str],
    *,
    after: datetime,
    through: datetime,
    include_history: bool = False,
) -> dict[str, dict[datetime, BucketFacts]]:
    """One ``GROUP BY class, bucket`` over enabled, monitored sources.

    LIVE-only by default, like `load_log_sources`: a history row would add to a bucket's sum
    and to its complete-source count. ``include_history=True`` is the preview's.

    The bucket is exact only when ``complete_sources == expected_sources``.
    Both the sum and the expected population exclude disabled and unmonitored
    sources, so one of those cannot inflate the rate or the denominator.
    """
    if not source_classes:
        return {}
    source_class = _class_of()
    monitored = and_(LogSource.enabled.is_(True), LogSource.monitoring_enabled.is_(True))
    complete = LogSourceMetric.completeness == BucketCompleteness.COMPLETE.value
    expected = (
        select(
            source_class.label("source_class"),
            func.count().label("expected_sources"),
        )
        .select_from(LogSource)
        .outerjoin(
            LogSourceClassification,
            LogSourceClassification.log_source_id == LogSource.id,
        )
        .where(monitored)
        .group_by(source_class)
        .subquery()
    )
    metric_class = _class_of()
    rows = (
        await session.execute(
            select(
                metric_class.label("source_class"),
                LogSourceMetric.bucket_start,
                func.coalesce(func.sum(LogSourceMetric.event_count).filter(complete), 0),
                func.coalesce(func.sum(LogSourceMetric.bucket_seconds).filter(complete), 0),
                func.count(LogSource.id.distinct()).filter(complete),
                expected.c.expected_sources,
            )
            .select_from(LogSourceMetric)
            .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
            .outerjoin(
                LogSourceClassification,
                LogSourceClassification.log_source_id == LogSource.id,
            )
            .join(expected, expected.c.source_class == metric_class)
            .where(
                monitored,
                metric_class.in_(source_classes),
                LogSourceMetric.bucket_start > after,
                LogSourceMetric.bucket_start <= through,
                *([] if include_history else [is_live_clause(LogSourceMetric)]),
            )
            .group_by(metric_class, LogSourceMetric.bucket_start, expected.c.expected_sources)
        )
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for cls, bucket_start, events, seconds, complete_sources, expected_sources in rows:
        built.append(
            (
                str(cls),
                bucket_start,
                BucketFacts(
                    bucket_start=bucket_start,
                    present=True,
                    class_events=int(events),
                    class_seconds=int(seconds),
                    complete_sources=int(complete_sources),
                    expected_sources=int(expected_sources),
                ),
            )
        )
    return _index(built)


async def load_waf_endpoints(
    session: AsyncSession,
    endpoint_ids: list[uuid.UUID],
    *,
    after: datetime,
    through: datetime,
    include_history: bool = False,
) -> dict[str, dict[datetime, BucketFacts]]:
    """Endpoint metrics joined to the source bucket that says what they prove.

    A metric whose bucket row is missing drops out of the join. The fold then
    sees no row and does not invent a zero.
    """
    if not endpoint_ids:
        return {}
    statement = (
        select(
            WafEndpointMetric.waf_endpoint_id,
            WafEndpointMetric.bucket_start,
            WafEndpointMetric.request_count,
            WafEndpointMetric.blocked_count,
            WafEndpointMetric.action_events,
            WafEndpointMetric.unmapped_action_count,
            WafEndpointMetric.server_error_count,
            WafEndpointMetric.client_error_count,
            WafEndpointMetric.response_code_events,
            WafEndpointMetric.unclassified_response_count,
            WafSourceBucket.request_count_measurement,
            WafSourceBucket.action_measurement,
            WafSourceBucket.response_code_measurement,
            WafSourceBucket.in_maintenance,
        )
        .join(WafEndpoint, WafEndpoint.id == WafEndpointMetric.waf_endpoint_id)
        .join(
            WafSourceBucket,
            and_(
                WafSourceBucket.log_source_id == WafEndpoint.log_source_id,
                WafSourceBucket.bucket_start == WafEndpointMetric.bucket_start,
            ),
        )
        .where(
            WafEndpointMetric.waf_endpoint_id.in_(endpoint_ids),
            WafEndpointMetric.bucket_start > after,
            WafEndpointMetric.bucket_start <= through,
        )
    )
    if not include_history:
        statement = statement.where(is_live_clause(WafSourceBucket))
    rows = (
        await session.execute(
            statement
        )
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for row in rows:
        # A plain tuple: the typed Row of a 14-column select is variadic on
        # SQLAlchemy 2.1, and mypy refuses to unpack that without a star target.
        values: tuple[Any, ...] = tuple(row)
        (
            endpoint_id,
            bucket_start,
            request_count,
            blocked_count,
            action_events,
            unmapped_action_count,
            server_error_count,
            client_error_count,
            response_code_events,
            unclassified_response_count,
            request_count_measurement,
            action_measurement,
            response_code_measurement,
            in_maintenance,
        ) = values
        built.append(
            (
                str(endpoint_id),
                bucket_start,
                BucketFacts(
                    bucket_start=bucket_start,
                    present=True,
                    in_maintenance=bool(in_maintenance),
                    request_count=int(request_count),
                    request_count_measurement=_text(request_count_measurement),
                    blocked_count=_optional_int(blocked_count),
                    action_events=_optional_int(action_events),
                    unmapped_action_count=_optional_int(unmapped_action_count),
                    server_error_count=_optional_int(server_error_count),
                    client_error_count=_optional_int(client_error_count),
                    response_code_events=_optional_int(response_code_events),
                    unclassified_response_count=_optional_int(unclassified_response_count),
                    action_measurement=_text(action_measurement),
                    response_code_measurement=_text(response_code_measurement),
                ),
            )
        )
    return _index(built)


async def load_ips_entities(
    session: AsyncSession,
    entity_ids: list[uuid.UUID],
    *,
    after: datetime,
    through: datetime,
) -> dict[str, dict[datetime, BucketFacts]]:
    """Entity counts joined to the source bucket, one statement for every dimension.

    The dimension's own ``*_measurement`` is chosen in Python. A missing bucket
    row drops the metric, which the fold reads as unmeasured.
    """
    if not entity_ids:
        return {}
    rows = (
        await session.execute(
            select(
                IpsEntityMetric.ips_entity_id,
                IpsEntityMetric.bucket_start,
                IpsEntityMetric.events,
                IpsEntity.dimension,
                IpsSourceBucket.destination_port_measurement,
                IpsSourceBucket.destination_ip_measurement,
                IpsSourceBucket.signature_measurement,
                IpsSourceBucket.in_maintenance,
            )
            .join(IpsEntity, IpsEntity.id == IpsEntityMetric.ips_entity_id)
            .join(
                IpsSourceBucket,
                and_(
                    IpsSourceBucket.log_source_id == IpsEntity.log_source_id,
                    IpsSourceBucket.bucket_start == IpsEntityMetric.bucket_start,
                ),
            )
            .where(
                IpsEntityMetric.ips_entity_id.in_(entity_ids),
                IpsEntityMetric.bucket_start > after,
                IpsEntityMetric.bucket_start <= through,
            )
        )
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for (
        entity_id,
        bucket_start,
        events,
        dimension,
        port_measurement,
        ip_measurement,
        signature_measurement,
        in_maintenance,
    ) in rows:
        by_dimension = {
            "DESTINATION_PORT": port_measurement,
            "DESTINATION_IP": ip_measurement,
            "SIGNATURE": signature_measurement,
        }
        built.append(
            (
                str(entity_id),
                bucket_start,
                BucketFacts(
                    bucket_start=bucket_start,
                    present=True,
                    in_maintenance=bool(in_maintenance),
                    events=int(events),
                    dimension_measurement=_text(by_dimension.get(str(dimension))),
                ),
            )
        )
    return _index(built)


async def load_ips_sources(
    session: AsyncSession,
    log_source_ids: list[uuid.UUID],
    *,
    after: datetime,
    through: datetime,
) -> dict[str, dict[datetime, BucketFacts]]:
    """Source-level IPS totals and the action triple for the named sources."""
    if not log_source_ids:
        return {}
    rows = (
        await session.execute(
            select(
                IpsSourceBucket.log_source_id,
                IpsSourceBucket.bucket_start,
                IpsSourceBucket.total_events,
                IpsSourceBucket.blocked_events,
                IpsSourceBucket.allowed_events,
                IpsSourceBucket.unmapped_events,
                IpsSourceBucket.action_measurement,
                IpsSourceBucket.in_maintenance,
            ).where(
                IpsSourceBucket.log_source_id.in_(log_source_ids),
                IpsSourceBucket.bucket_start > after,
                IpsSourceBucket.bucket_start <= through,
            )
        )
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for (
        source_id,
        bucket_start,
        total_events,
        blocked_events,
        allowed_events,
        unmapped_events,
        action_measurement,
        in_maintenance,
    ) in rows:
        built.append(
            (
                str(source_id),
                bucket_start,
                BucketFacts(
                    bucket_start=bucket_start,
                    present=True,
                    in_maintenance=bool(in_maintenance),
                    total_events=int(total_events),
                    blocked_events=_optional_int(blocked_events),
                    allowed_events=_optional_int(allowed_events),
                    unmapped_events=_optional_int(unmapped_events),
                    action_measurement=_text(action_measurement),
                ),
            )
        )
    return _index(built)


@dataclass(frozen=True)
class PwValueBucket:
    """One (pinned value, bucket) pair as the PW loader read it from the database."""

    bucket_start: datetime
    in_maintenance: bool
    values_measurement: str
    pinned_measurement: str
    totals_complete: bool
    total_events: int | None
    parsed_events: int | None
    #: ``pw_value_metric.events``; ``None`` when the value has no row in this bucket.
    events: int | None
    value_pinned: bool
    tracked_since: datetime | None
    tracked_until: datetime | None


def _pw_coverage_ok(row: PwValueBucket, floor: float) -> bool:
    """A zero is a zero of *parsed* events (design §G.5): unknown coverage proves nothing.

    A scope that sent nothing is covered: no event went unparsed. A totals search that did not
    complete leaves coverage unknown, which never reaches the floor.
    """
    if not row.totals_complete or row.total_events is None or row.parsed_events is None:
        return False
    if row.total_events == 0:
        return True
    return row.parsed_events / row.total_events >= floor


def _pw_tracked_at(row: PwValueBucket) -> bool:
    """The tracking window (WAF §29.4): outside it an absent row is not a zero."""
    if row.tracked_since is None or row.bucket_start < row.tracked_since:
        return False
    return row.tracked_until is None or row.bucket_start <= row.tracked_until


def pw_value_facts(row: PwValueBucket, *, coverage_floor: float) -> BucketFacts | None:
    """What one bucket proves for one pinned value, or ``None`` when it proves nothing.

    A row carries the value's count under the bucket's measurement: ``EXACT`` for a pinned value
    whose small search was whole, else the grouped search's. No row is a **measured zero** only
    for a pinned value, tracked at the bucket, in a bucket whose ``pinned_measurement`` is
    ``EXACT`` and whose parsed share reaches the floor (design §G.8.0, §G.10.2). Every other
    absence is unmeasured: ``None``, never a zero.
    """
    pinned_exact = row.value_pinned and row.pinned_measurement == PwPinnedMeasurement.EXACT.value
    if row.events is not None:
        if pinned_exact:
            measurement = PwBucketMeasurement.EXACT.value
        elif row.values_measurement in (
            PwBucketMeasurement.EXACT.value,
            PwBucketMeasurement.LOWER_BOUND.value,
        ):
            measurement = row.values_measurement
        else:
            # A count inside a bucket that measured nothing contradicts itself.
            return None
        return BucketFacts(
            bucket_start=row.bucket_start,
            present=True,
            in_maintenance=row.in_maintenance,
            events=row.events,
            dimension_measurement=measurement,
        )
    if not (pinned_exact and _pw_tracked_at(row) and _pw_coverage_ok(row, coverage_floor)):
        return None
    return BucketFacts(
        bucket_start=row.bucket_start,
        present=True,
        in_maintenance=row.in_maintenance,
        events=0,
        dimension_measurement=PwBucketMeasurement.EXACT.value,
    )


async def load_pw_values(
    session: AsyncSession,
    pw_value_ids: list[uuid.UUID],
    *,
    after: datetime,
    through: datetime,
    coverage_floor: float,
    include_history: bool = False,
) -> dict[str, dict[datetime, BucketFacts]]:
    """Pinned-value counts joined to their watch's bucket row, one statement for every rule.

    Unlike the IPS entity loader this reads the bucket for the buckets that have no metric row
    too (an outer join from the bucket), because a measured zero is decided by the bucket. A
    bucket stored under another ``watch_version`` than the value's is not the value's bucket.
    """
    if not pw_value_ids:
        return {}
    rows = (
        await session.execute(
            select(
                PwValue.id,
                PwValue.pinned,
                PwValue.tracked_since,
                PwValue.tracked_until,
                PwBucket.bucket_start,
                PwBucket.in_maintenance,
                PwBucket.values_measurement,
                PwBucket.pinned_measurement,
                PwBucket.totals_complete,
                PwBucket.total_events,
                PwBucket.parsed_events,
                PwValueMetric.events,
            )
            .select_from(PwValue)
            .join(
                PwBucket,
                and_(
                    PwBucket.watch_id == PwValue.watch_id,
                    PwBucket.watch_version == PwValue.watch_version,
                    PwBucket.bucket_start > after,
                    PwBucket.bucket_start <= through,
                    *([] if include_history else [is_live_clause(PwBucket)]),
                ),
            )
            .outerjoin(
                PwValueMetric,
                and_(
                    PwValueMetric.pw_value_id == PwValue.id,
                    PwValueMetric.bucket_start == PwBucket.bucket_start,
                    PwValueMetric.bucket_start > after,
                    PwValueMetric.bucket_start <= through,
                    *([] if include_history else [is_live_clause(PwBucket)]),
                ),
            )
            .where(PwValue.id.in_(pw_value_ids))
        )
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for row in rows:
        values: tuple[Any, ...] = tuple(row)
        (
            value_id,
            pinned,
            tracked_since,
            tracked_until,
            bucket_start,
            in_maintenance,
            values_measurement,
            pinned_measurement,
            totals_complete,
            total_events,
            parsed_events,
            events,
        ) = values
        facts = pw_value_facts(
            PwValueBucket(
                bucket_start=bucket_start,
                in_maintenance=bool(in_maintenance),
                values_measurement=str(values_measurement),
                pinned_measurement=str(pinned_measurement),
                totals_complete=bool(totals_complete),
                total_events=_optional_int(total_events),
                parsed_events=_optional_int(parsed_events),
                events=_optional_int(events),
                value_pinned=bool(pinned),
                tracked_since=tracked_since,
                tracked_until=tracked_until,
            ),
            coverage_floor=coverage_floor,
        )
        if facts is not None:
            built.append((str(value_id), bucket_start, facts))
    return _index(built)


def _text(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        raise TypeError(f"expected an integer count, got {type(value).__name__}")
    return int(value)


__all__ = [
    "PwValueBucket",
    "implemented_loaders",
    "load_fleet_classes",
    "load_ips_entities",
    "load_ips_sources",
    "load_log_sources",
    "load_pw_values",
    "load_waf_endpoints",
    "pw_value_facts",
]
