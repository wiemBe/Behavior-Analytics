"""The measured inputs IPS detectors read, and what each one is known to be.

Mirrors `app.waf.inputs` and `app.waf.observations` for IPS (design D.6, D.8).
Each input has its own measurement, decided by the search that produced it, and
its own mathematical requirement, declared once per detector in
`app.anomaly.capabilities`. This module answers the first half -- what each input
*is* for one observation -- and decides what a missing entity row means.

**Entity rows are sparse.** An admitted entity has an `IpsEntityMetric` row only
in buckets where collection wrote one. Its absence is a measured zero only where
collection would have written the row had there been traffic:

  * the dimension's search was whole (`EXACT`): a lower bound may hide the entity
    in its unseen tail;
  * the dimension's coverage met the floor: below it, events the search could
    not attribute may have belonged to the entity;
  * admission was not truncated for the dimension: the top-N cut and the
    per-bucket quota drop entities without a row;
  * the entity was active and already admitted in the bucket: a retired entity
    is no longer written, and an earlier bucket predates its first row.

Otherwise the count is unknown (`None`), never 0. A row in a `LOWER_BOUND`
dimension is a lower-bound count. Pure; no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from app.models.enums import IpsDimension, WafMeasurement
from app.waf.inputs import HistoryFacts

#: Baseline metric names (design D.7) the IPS baseline builder will train.
IPS_ENTITY_EVENTS_METRIC = "entity_events"
IPS_TOP_SOURCE_SHARE_METRIC = "top1_source_share"
IPS_FANOUT_METRIC = "max_distinct_ports"
IPS_BLOCK_RATIO_METRIC = "block_ratio"

#: The three dimensions whose values are admitted as entities.
ENTITY_DIMENSIONS = (
    IpsDimension.DESTINATION_PORT,
    IpsDimension.DESTINATION_IP,
    IpsDimension.SIGNATURE,
)


class IpsInput(StrEnum):
    ENTITY_EVENTS = "ENTITY_EVENTS"
    DIMENSION_EVENTS = "DIMENSION_EVENTS"
    DESTINATION_PORT_EVENTS = "DESTINATION_PORT_EVENTS"
    SOURCE_IP_TOP1_EVENTS = "SOURCE_IP_TOP1_EVENTS"
    SOURCE_IP_EVENTS = "SOURCE_IP_EVENTS"
    MAX_DISTINCT_PORTS = "MAX_DISTINCT_PORTS"
    BLOCKED_EVENTS = "BLOCKED_EVENTS"
    ACTION_EVENTS = "ACTION_EVENTS"
    PORT_HISTORY = "PORT_HISTORY"


class IpsBucketMeasurement(StrEnum):
    """A dimension's measurement as stored in `IpsSourceBucket.*_measurement`."""

    EXACT = "EXACT"
    LOWER_BOUND = "LOWER_BOUND"
    NOT_COLLECTED = "NOT_COLLECTED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"


_MEASURED = (IpsBucketMeasurement.EXACT, IpsBucketMeasurement.LOWER_BOUND)


def _waf(measurement: IpsBucketMeasurement) -> WafMeasurement | None:
    if measurement is IpsBucketMeasurement.EXACT:
        return WafMeasurement.EXACT
    if measurement is IpsBucketMeasurement.LOWER_BOUND:
        return WafMeasurement.LOWER_BOUND
    return None


@dataclass(frozen=True)
class IpsDimensionFacts:
    """What collection established about one dimension of one (source, bucket)."""

    measurement: IpsBucketMeasurement = IpsBucketMeasurement.NOT_COLLECTED
    #: Dimension events over the bucket's totals. None: not measurable.
    coverage_ratio: float | None = None
    #: The ratio is a lower bound (the dimension's search was truncated).
    coverage_lower_bound: bool | None = None
    #: Admission lost entities (quota, ceiling, top-N cut, lower bound). None:
    #: not recorded, which is never read as "not truncated".
    admission_truncated: bool | None = None

    @property
    def measured(self) -> bool:
        return self.measurement in _MEASURED

    @property
    def waf_measurement(self) -> WafMeasurement | None:
        return _waf(self.measurement)

    def coverage_met(self, floor: float) -> bool:
        return self.measured and self.coverage_ratio is not None and self.coverage_ratio >= floor

    @property
    def state(self) -> str:
        """Operator wording for a dimension that has no measurement."""
        return {
            IpsBucketMeasurement.NOT_COLLECTED: "not collected",
            IpsBucketMeasurement.FAILED: "failed",
            IpsBucketMeasurement.REJECTED: "rejected",
        }.get(self.measurement, self.measurement.value.lower())


@dataclass(frozen=True)
class IpsSourceBucketFacts:
    """One IPS source in one bucket, from plain values (the `IpsSourceBucket` columns)."""

    bucket_start: datetime
    bucket_seconds: int
    total_events: int
    destination_port: IpsDimensionFacts = field(default_factory=IpsDimensionFacts)
    destination_ip: IpsDimensionFacts = field(default_factory=IpsDimensionFacts)
    signature: IpsDimensionFacts = field(default_factory=IpsDimensionFacts)
    source_ip: IpsDimensionFacts = field(default_factory=IpsDimensionFacts)
    port_fanout: IpsDimensionFacts = field(default_factory=IpsDimensionFacts)
    action: IpsDimensionFacts = field(default_factory=IpsDimensionFacts)
    source_events: int | None = None
    distinct_sources: int | None = None
    top1_events: int | None = None
    top5_events: int | None = None
    max_distinct_ports: int | None = None
    blocked_events: int | None = None
    allowed_events: int | None = None
    unmapped_events: int | None = None
    #: ``{"SOURCE_IP": [{"source_ip", "events"}], "PORT_FANOUT": [{"source_ip",
    #: "distinct_ports"}]}``, each list bounded to ten by the pure folds.
    contributors: Mapping[str, Any] = field(default_factory=dict)
    profile_version: int = 1
    normalizer_version: int = 1
    in_maintenance: bool = False

    @property
    def bucket_end(self) -> datetime:
        return self.bucket_start + timedelta(seconds=self.bucket_seconds)

    def dimension(self, dimension: IpsDimension) -> IpsDimensionFacts | None:
        """The dimension's facts, or None for TOTALS (which is not a dimension here)."""
        return {
            IpsDimension.DESTINATION_PORT: self.destination_port,
            IpsDimension.DESTINATION_IP: self.destination_ip,
            IpsDimension.SIGNATURE: self.signature,
            IpsDimension.SOURCE_IP: self.source_ip,
            IpsDimension.PORT_FANOUT: self.port_fanout,
            IpsDimension.ACTION: self.action,
        }.get(dimension)

    @property
    def action_events(self) -> int | None:
        """Every action-bearing event: blocked, allowed and unmapped. None if any is unknown."""
        parts = (self.blocked_events, self.allowed_events, self.unmapped_events)
        if any(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)

    @classmethod
    def of(cls, bucket: Any) -> IpsSourceBucketFacts:
        """Build from an `IpsSourceBucket` row (or anything with its columns)."""
        truncated: Mapping[str, Any] = getattr(bucket, "admission_truncated", None) or {}

        def dim(name: str, admission_key: str | None = None) -> IpsDimensionFacts:
            raw = getattr(bucket, f"{name}_measurement", None)
            try:
                measurement = IpsBucketMeasurement(str(raw))
            except ValueError:
                measurement = IpsBucketMeasurement.NOT_COLLECTED
            flag = truncated.get(admission_key) if admission_key else None
            return IpsDimensionFacts(
                measurement=measurement,
                coverage_ratio=getattr(bucket, f"{name}_coverage_ratio", None),
                coverage_lower_bound=getattr(bucket, f"{name}_coverage_lower_bound", None),
                admission_truncated=flag if isinstance(flag, bool) else None,
            )

        return cls(
            bucket_start=bucket.bucket_start,
            bucket_seconds=bucket.bucket_seconds,
            total_events=bucket.total_events,
            destination_port=dim("destination_port", IpsDimension.DESTINATION_PORT.value),
            destination_ip=dim("destination_ip", IpsDimension.DESTINATION_IP.value),
            signature=dim("signature", IpsDimension.SIGNATURE.value),
            source_ip=dim("source_ip"),
            port_fanout=dim("port_fanout"),
            action=dim("action"),
            source_events=bucket.source_events,
            distinct_sources=bucket.distinct_sources,
            top1_events=bucket.top1_events,
            top5_events=bucket.top5_events,
            max_distinct_ports=bucket.max_distinct_ports,
            blocked_events=bucket.blocked_events,
            allowed_events=bucket.allowed_events,
            unmapped_events=bucket.unmapped_events,
            contributors=getattr(bucket, "contributors", None) or {},
            profile_version=bucket.profile_version,
            normalizer_version=bucket.normalizer_version,
            in_maintenance=bool(bucket.in_maintenance),
        )


@dataclass(frozen=True)
class IpsEntityFacts:
    """An admitted entity, from the `IpsEntity` columns."""

    dimension: IpsDimension
    value_key: str
    display_name: str | None
    first_seen_at: datetime
    active: bool = True
    last_seen_at: datetime | None = None
    normalizer_version: int = 1


@dataclass(frozen=True)
class EntityCount:
    """An entity's event count in one bucket. `events` is None when unknown."""

    events: int | None
    measurement: WafMeasurement | None
    #: Why the count is unknown. None when it is known.
    reason: str | None = None


def entity_count(
    entity: IpsEntityFacts,
    bucket: IpsSourceBucketFacts,
    metric_events: int | None,
    *,
    min_coverage: float,
) -> EntityCount:
    """The entity's count in the bucket: its row, a measured zero, or unknown.

    `metric_events` is the entity's `IpsEntityMetric.events`, or None when it has
    no row in the bucket. See the module docstring for when no row is a zero.
    """
    dim = bucket.dimension(entity.dimension)
    if dim is None or entity.dimension not in ENTITY_DIMENSIONS:
        return EntityCount(None, None, f"{entity.dimension.value} is not an entity dimension")
    if not dim.measured:
        return EntityCount(
            None, None,
            f"the {entity.dimension.value} search was {dim.state} for this bucket: "
            "the count is unavailable, not zero",
        )
    measurement = dim.waf_measurement
    if metric_events is not None:
        return EntityCount(metric_events, measurement)

    if not entity.active:
        return EntityCount(None, None, "the entity is retired, so collection no longer writes it")
    if bucket.bucket_start < entity.first_seen_at:
        return EntityCount(None, None, "the bucket predates the entity's first admission")
    if measurement is not WafMeasurement.EXACT:
        return EntityCount(
            None, None,
            f"the {entity.dimension.value} search was a lower bound: the entity may be in "
            "its unseen tail",
        )
    if not dim.coverage_met(min_coverage):
        return EntityCount(
            None, None,
            f"{entity.dimension.value} coverage is below the {min_coverage:.0%} floor: events "
            "the search could not attribute may have been the entity's",
        )
    if dim.admission_truncated is not False:
        why = "was not recorded" if dim.admission_truncated is None else "lost entities"
        return EntityCount(
            None, None,
            f"admission for {entity.dimension.value} {why} in this bucket, so the entity may "
            "have been dropped without a row",
        )
    return EntityCount(0, WafMeasurement.EXACT)


@dataclass(frozen=True)
class IpsObservation:
    """What a detector judges: one source's bucket, and for entity detectors one entity."""

    bucket: IpsSourceBucketFacts
    entity: IpsEntityFacts | None = None
    count: EntityCount | None = None


def observe_entity(
    entity: IpsEntityFacts,
    bucket: IpsSourceBucketFacts,
    metric_events: int | None,
    *,
    min_coverage: float,
) -> IpsObservation:
    return IpsObservation(
        bucket=bucket,
        entity=entity,
        count=entity_count(entity, bucket, metric_events, min_coverage=min_coverage),
    )


def _numerator(base: WafMeasurement | None, uncertain: int | None) -> WafMeasurement | None:
    """A numerator is a lower bound when its search was, or when some of the
    denominator's events carried a value that could not be classified."""
    if base is WafMeasurement.EXACT and uncertain:
        return WafMeasurement.LOWER_BOUND
    return base


def input_measurement(
    source: IpsInput, obs: IpsObservation, history: HistoryFacts | None = None
) -> WafMeasurement | None:
    """What one input is for one observation: exact, a lower bound, or unknown."""
    b = obs.bucket
    match source:
        case IpsInput.ENTITY_EVENTS:
            if obs.count is None or obs.count.events is None:
                return None
            return obs.count.measurement
        case IpsInput.DIMENSION_EVENTS:
            if obs.entity is None:
                return None
            dim = b.dimension(obs.entity.dimension)
            return dim.waf_measurement if dim is not None else None
        case IpsInput.DESTINATION_PORT_EVENTS:
            return b.destination_port.waf_measurement
        case IpsInput.SOURCE_IP_TOP1_EVENTS:
            return b.source_ip.waf_measurement if b.top1_events is not None else None
        case IpsInput.SOURCE_IP_EVENTS:
            return b.source_ip.waf_measurement if b.source_events is not None else None
        case IpsInput.MAX_DISTINCT_PORTS:
            return b.port_fanout.waf_measurement if b.max_distinct_ports is not None else None
        case IpsInput.BLOCKED_EVENTS:
            if b.blocked_events is None or b.unmapped_events is None:
                return None
            return _numerator(b.action.waf_measurement, b.unmapped_events)
        case IpsInput.ACTION_EVENTS:
            return b.action.waf_measurement if b.action_events is not None else None
        case IpsInput.PORT_HISTORY:
            if history is None or not history.complete:
                return None
            return WafMeasurement.EXACT
