"""Replay a custom rule over buckets that are already stored (design §F.7).

Both preview routes call ``replay``. It calls ``fold_rule``, the same pure
function the evaluator uses, over one bulk read of the window. Nothing here
writes a rule, a breach, an alert or an audit row, and nothing here asks
QRadar.

The replay answers "would this have fired on stored data?", so it starts
from a clean counter and does not apply the operational halts (the rule
disabled, the subject retired, the family switched off, the platform
switch). Maintenance is applied, the same way the engine applies it: a
bucket in maintenance pauses both counters. A missing row is unmeasured.
It is never read as zero.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import in_maintenance
from app.custom_rules.catalog import catalog_entry
from app.custom_rules.evaluate import (
    BucketFacts,
    FoldInput,
    FoldOutput,
    InterpretedReading,
    fold_rule,
    interpret,
    latest_closed_bucket,
)
from app.custom_rules.loaders import (
    load_fleet_classes,
    load_ips_entities,
    load_ips_sources,
    load_log_sources,
    load_pw_values,
    load_waf_endpoints,
)
from app.models.enums import (
    BucketCompleteness,
    CustomRuleMetric,
    CustomRuleSubjectKind,
)
from app.models.ips import IpsEntity
from app.models.log_source import LogSource
from app.models.waf import WafEndpoint

MeasurementLabel = Literal["EXACT", "LOWER_BOUND", "NOT_MEASURED"]

_LOG_SOURCE = CustomRuleSubjectKind.LOG_SOURCE
_FLEET = CustomRuleSubjectKind.FLEET_CLASS
_WAF = CustomRuleSubjectKind.WAF_ENDPOINT
_IPS_ENTITY = CustomRuleSubjectKind.IPS_ENTITY
_IPS_SOURCE = CustomRuleSubjectKind.IPS_SOURCE
_PW_VALUE = CustomRuleSubjectKind.PW_VALUE


@dataclass(frozen=True)
class PreviewBucket:
    bucket_start: datetime
    value: float | None
    measurement: MeasurementLabel
    completeness: str | None
    in_maintenance: bool


@dataclass(frozen=True)
class BreachInterval:
    """Opening bucket through the resolving bucket. ``end`` is null while open."""

    start: datetime
    end: datetime | None


@dataclass(frozen=True)
class PreviewResult:
    threshold_value: float
    unit: str
    operator: str
    fired_count: int
    measured_count: int
    unmeasured_count: int
    buckets: tuple[PreviewBucket, ...]
    breach_intervals: tuple[BreachInterval, ...]


def bucket_grid(now: datetime, hours: int, interval_seconds: int) -> list[datetime]:
    """Every closed bucket start in ``(now - hours, latest_closed_bucket]``.

    Aligned to the collection epoch, the same arithmetic as
    ``latest_closed_bucket``. The bucket that starts at ``now - hours`` is
    outside the window. The bucket that starts at ``now`` is still in
    progress and is outside it too.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if hours <= 0:
        raise ValueError("hours must be positive")
    after = now - timedelta(hours=hours)
    closed = latest_closed_bucket(now, interval_seconds)
    after_epoch = int(after.timestamp())
    closed_epoch = int(closed.timestamp())
    first = (after_epoch // interval_seconds) * interval_seconds
    if first <= after_epoch:
        first += interval_seconds
    starts: list[datetime] = []
    cursor = first
    while cursor <= closed_epoch:
        starts.append(datetime.fromtimestamp(cursor, tz=UTC))
        cursor += interval_seconds
    return starts


def preview_from_readings(
    readings: Sequence[InterpretedReading],
    *,
    completeness: Sequence[str | None],
    metric: CustomRuleMetric,
    operator: str,
    threshold_value: float,
    sustain_buckets: int,
    recovery_buckets: int,
    severity: str,
    subject_kind: str,
    now: datetime,
    name: str = "preview",
) -> PreviewResult:
    """Fold ``readings`` from a clean counter and shape the chart response.

    ``completeness`` aligns with ``readings``. The fold does not see it; the
    chart does. A maintenance reading keeps the measurement ``interpret``
    gave it. An absent value is ``NOT_MEASURED``, never zero.
    """
    if len(completeness) != len(readings):
        raise ValueError("completeness must align with readings")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    output = fold_rule(
        FoldInput(
            rule_id=uuid.UUID(int=0),
            rule_name=name,
            severity=severity,
            metric=metric,
            operator=operator,
            threshold_value=threshold_value,
            sustain_buckets=sustain_buckets,
            recovery_buckets=recovery_buckets,
            consecutive_breaching=0,
            consecutive_under=0,
            last_evaluated_bucket_start=None,
            previous_status=None,
            previous_reason=None,
            subject_kind=subject_kind,
            subject_label="",
            platform_enabled=True,
            enabled=True,
            retired=False,
            retired_reason=None,
            family_collected=True,
            family_reason=None,
            breach=None,
            readings=tuple(readings),
            now=now,
            collection_stale_reason=None,
        )
    )
    buckets = tuple(
        _bucket(reading, mark) for reading, mark in zip(readings, completeness, strict=True)
    )
    measured = sum(1 for bucket in buckets if bucket.measurement != "NOT_MEASURED")
    return PreviewResult(
        threshold_value=float(threshold_value),
        unit=catalog_entry(metric).unit,
        operator=operator,
        fired_count=sum(1 for event in output.events if event.action == "open"),
        measured_count=measured,
        unmeasured_count=len(buckets) - measured,
        buckets=buckets,
        breach_intervals=tuple(_intervals(output)),
    )


async def replay(
    session: AsyncSession,
    *,
    metric: CustomRuleMetric,
    operator: str,
    threshold_value: float,
    sustain_buckets: int,
    recovery_buckets: int,
    severity: str,
    subject_kind: CustomRuleSubjectKind,
    log_source_id: uuid.UUID | None,
    source_class: str | None,
    waf_endpoint_id: uuid.UUID | None,
    ips_entity_id: uuid.UUID | None,
    hours: int,
    interval_seconds: int,
    now: datetime,
    name: str = "preview",
    pw_value_id: uuid.UUID | None = None,
    pw_coverage_floor: float | None = None,
) -> PreviewResult:
    """One bulk read of the window, then ``preview_from_readings``.

    The loader call does not grow with the number of buckets. A fleet class
    has no log source to load. Every other kind loads its source once, for
    the live maintenance window, and that is the whole per-request cost.
    """
    after = now - timedelta(hours=hours)
    through = latest_closed_bucket(now, interval_seconds)
    key = _subject_key(
        subject_kind,
        log_source_id=log_source_id,
        source_class=source_class,
        waf_endpoint_id=waf_endpoint_id,
        ips_entity_id=ips_entity_id,
        pw_value_id=pw_value_id,
    )
    loaded = await _load(
        session,
        subject_kind,
        key,
        after=after,
        through=through,
        pw_coverage_floor=pw_coverage_floor,
    )
    source = await _maintenance_source(
        session,
        subject_kind,
        log_source_id=log_source_id,
        waf_endpoint_id=waf_endpoint_id,
        ips_entity_id=ips_entity_id,
    )
    by_epoch = {
        int(facts.bucket_start.timestamp()): facts
        for facts in loaded.get(key or "", {}).values()
    }
    readings: list[InterpretedReading] = []
    marks: list[str | None] = []
    for start in bucket_grid(now, hours, interval_seconds):
        facts = by_epoch.get(int(start.timestamp()))
        if facts is None:
            facts = BucketFacts(bucket_start=start, present=False)
        reading = interpret(metric, facts)
        if source is not None and in_maintenance(
            start,
            maintenance_mode=bool(source.maintenance_mode),
            maintenance_until=source.maintenance_until,
        ):
            reading = InterpretedReading(
                bucket_start=reading.bucket_start,
                value=reading.value,
                measurement=reading.measurement,
                in_maintenance=True,
                reason="the source is in maintenance",
            )
        readings.append(reading)
        marks.append(_completeness(metric, facts))
    return preview_from_readings(
        readings,
        completeness=marks,
        metric=metric,
        operator=operator,
        threshold_value=threshold_value,
        sustain_buckets=sustain_buckets,
        recovery_buckets=recovery_buckets,
        severity=severity,
        subject_kind=subject_kind.value,
        now=now,
        name=name,
    )


def _bucket(reading: InterpretedReading, completeness: str | None) -> PreviewBucket:
    return PreviewBucket(
        bucket_start=reading.bucket_start,
        value=reading.value,
        measurement=_label(reading),
        completeness=completeness,
        in_maintenance=reading.in_maintenance,
    )


def _label(reading: InterpretedReading) -> MeasurementLabel:
    if reading.value is None or reading.measurement is None:
        return "NOT_MEASURED"
    if reading.measurement == "EXACT":
        return "EXACT"
    if reading.measurement == "LOWER_BOUND":
        return "LOWER_BOUND"
    return "NOT_MEASURED"


def _intervals(output: FoldOutput) -> list[BreachInterval]:
    """Open events start an interval. Resolve events close one. A refresh does neither."""
    open_at: datetime | None = None
    found: list[BreachInterval] = []
    for event in output.events:
        if event.action == "open":
            open_at = event.first_bucket_start
        elif event.action == "resolve" and open_at is not None:
            found.append(BreachInterval(start=open_at, end=event.last_bucket_start))
            open_at = None
    if open_at is not None:
        found.append(BreachInterval(start=open_at, end=None))
    return found


def _completeness(metric: CustomRuleMetric, facts: BucketFacts) -> str | None:
    """The bucket's own completeness, or null when the row is absent.

    Log-source metrics keep the Phase A value. A fleet class is COMPLETE
    only when every enabled, monitored source of the class has a COMPLETE
    bucket. WAF and IPS keep the measurement column the metric reads.
    A total-events row is COMPLETE whenever it exists.
    """
    if not facts.present:
        return None
    if metric in (CustomRuleMetric.EVENT_COUNT, CustomRuleMetric.AVERAGE_EPS):
        return facts.completeness
    if metric is CustomRuleMetric.EPS:
        expected = facts.expected_sources or 0
        complete = facts.complete_sources or 0
        if expected > 0 and complete == expected:
            return BucketCompleteness.COMPLETE.value
        return BucketCompleteness.PARTIAL.value
    if metric is CustomRuleMetric.REQUEST_COUNT:
        return facts.request_count_measurement
    if metric in (CustomRuleMetric.BLOCK_RATIO, CustomRuleMetric.BLOCKED_SHARE):
        return facts.action_measurement
    if metric in (CustomRuleMetric.SERVER_ERROR_RATIO, CustomRuleMetric.CLIENT_ERROR_RATIO):
        return facts.response_code_measurement
    if metric in (CustomRuleMetric.EVENTS, CustomRuleMetric.PW_VALUE_EVENTS):
        return facts.dimension_measurement
    if metric is CustomRuleMetric.TOTAL_EVENTS:
        return BucketCompleteness.COMPLETE.value
    return None


def _subject_key(
    kind: CustomRuleSubjectKind,
    *,
    log_source_id: uuid.UUID | None,
    source_class: str | None,
    waf_endpoint_id: uuid.UUID | None,
    ips_entity_id: uuid.UUID | None,
    pw_value_id: uuid.UUID | None = None,
) -> str | None:
    if kind in (_LOG_SOURCE, _IPS_SOURCE):
        return None if log_source_id is None else str(log_source_id)
    if kind is _FLEET:
        return source_class
    if kind is _WAF:
        return None if waf_endpoint_id is None else str(waf_endpoint_id)
    if kind is _IPS_ENTITY:
        return None if ips_entity_id is None else str(ips_entity_id)
    if kind is _PW_VALUE:
        return None if pw_value_id is None else str(pw_value_id)
    return None


async def _load(
    session: AsyncSession,
    kind: CustomRuleSubjectKind,
    key: str | None,
    *,
    after: datetime,
    through: datetime,
    pw_coverage_floor: float | None = None,
) -> dict[str, dict[datetime, BucketFacts]]:
    """The one metric statement for this preview. An empty key issues none.

    A PW value without a coverage floor loads nothing, so it reads unmeasured (fail closed).
    """
    if key is None:
        return {}
    if kind is _LOG_SOURCE:
        return await load_log_sources(
            session, [uuid.UUID(key)], after=after, through=through, include_history=True
        )
    if kind is _FLEET:
        return await load_fleet_classes(
            session, [key], after=after, through=through, include_history=True
        )
    if kind is _WAF:
        return await load_waf_endpoints(
            session,
            [uuid.UUID(key)],
            after=after,
            through=through,
            include_history=True,
        )
    if kind is _IPS_ENTITY:
        return await load_ips_entities(
            session, [uuid.UUID(key)], after=after, through=through
        )
    if kind is _IPS_SOURCE:
        return await load_ips_sources(
            session, [uuid.UUID(key)], after=after, through=through
        )
    if kind is _PW_VALUE:
        if pw_coverage_floor is None:
            return {}
        return await load_pw_values(
            session,
            [uuid.UUID(key)],
            after=after,
            through=through,
            coverage_floor=pw_coverage_floor,
            include_history=True,
        )
    return {}


async def _maintenance_source(
    session: AsyncSession,
    kind: CustomRuleSubjectKind,
    *,
    log_source_id: uuid.UUID | None,
    waf_endpoint_id: uuid.UUID | None,
    ips_entity_id: uuid.UUID | None,
) -> LogSource | None:
    """The source whose live maintenance window pauses this preview.

    A fleet class has no single window. WAF and IPS pause on the source
    that owns the subject, which is also where collection stored its own
    maintenance flag for ``interpret`` to read.
    """
    if kind in (_FLEET, _PW_VALUE):
        # A PW value has no live window; the bucket's stored flag is what interpret reads.
        return None
    source_id = log_source_id
    if kind is _WAF:
        if waf_endpoint_id is None:
            return None
        endpoint = await session.get(WafEndpoint, waf_endpoint_id)
        source_id = None if endpoint is None else endpoint.log_source_id
    elif kind is _IPS_ENTITY:
        if ips_entity_id is None:
            return None
        entity = await session.get(IpsEntity, ips_entity_id)
        source_id = None if entity is None else entity.log_source_id
    if source_id is None:
        return None
    return await session.get(LogSource, source_id)
