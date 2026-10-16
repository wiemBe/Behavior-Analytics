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
from datetime import datetime
from decimal import Decimal

from sqlalchemy import and_, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.custom_rules.evaluate import BucketFacts
from app.models.enums import BucketCompleteness, CustomRuleSubjectKind, SourceClass
from app.models.fleet import LogSourceClassification
from app.models.ips import IpsEntity, IpsEntityMetric, IpsSourceBucket
from app.models.log_source import LogSource, LogSourceMetric
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
) -> dict[str, dict[datetime, BucketFacts]]:
    """``log_source_metric`` rows for the named sources in the open window."""
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
) -> dict[str, dict[datetime, BucketFacts]]:
    """One ``GROUP BY class, bucket`` over enabled, monitored sources.

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
) -> dict[str, dict[datetime, BucketFacts]]:
    """Endpoint metrics joined to the source bucket that says what they prove.

    A metric whose bucket row is missing drops out of the join. The fold then
    sees no row and does not invent a zero.
    """
    if not endpoint_ids:
        return {}
    rows = (
        await session.execute(
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
    ).all()
    built: list[tuple[str, datetime, BucketFacts]] = []
    for (
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
    ) in rows:
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
    "implemented_loaders",
    "load_fleet_classes",
    "load_ips_entities",
    "load_ips_sources",
    "load_log_sources",
    "load_waf_endpoints",
]
