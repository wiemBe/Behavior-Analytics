"""Phase G property watches: track a property's values on a scope (design §G.3, §G.7).

An operator picks one log source, log source type or log source group and one or two
properties; the platform counts the properties' values per bucket. A value becomes an
aggregate key (`pw_value`) only once admitted or pinned, exactly as IPS entities and WAF
endpoints do, so a value that never reached admission exists only inside the fold and its
events are summed into `pw_bucket.untracked_events` (AGENTS.md rule 3).

Eight tables:

  * `property_watch` -- the definition, its watermarks and its status;
  * `pw_bucket` -- one row per (watch, bucket): totals, coverage, admission outcomes
    (TimescaleDB hypertable on `bucket_start`);
  * `pw_value` -- the tracked value and its tracking window;
  * `pw_value_metric` -- sparse per-bucket events of a tracked value (hypertable);
  * `pw_value_baseline` -- weekday x hour seasonal cells;
  * `pw_detector_state`, `pw_anomaly`, `pw_anomaly_transition` -- the lifecycle.

A missing `pw_value_metric` row is neither a zero nor an unknown on its own: the bucket
row decides which (design §G.8.0, `app.property_watch.facts.reading_for`).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AnomalyState,
    EvidenceStatus,
    PwAnomalyType,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    PwScopeKind,
    PwWatchStatus,
    Severity,
    WafMeasurement,
)
from app.models.waf import METRIC_SEMANTICS_LENGTH


def _in_list(
    values: type[PwBucketMeasurement]
    | type[PwPinnedMeasurement]
    | type[PwWatchStatus]
    | type[Severity],
) -> str:
    return "(" + ", ".join(f"'{member.value}'" for member in values) + ")"


#: Exactly the columns the scope kind names are set, the other two are NULL (the
#: `CUSTOM_RULE_SUBJECT_CHECK` pattern). A watch with no scope, or two, is refused here.
PROPERTY_WATCH_SCOPE_CHECK = (
    "(scope_kind = 'LOG_SOURCE' AND log_source_id IS NOT NULL "
    "AND scope_type_id IS NULL AND scope_group_id IS NULL) OR "
    "(scope_kind = 'LOG_SOURCE_TYPE' AND scope_type_id IS NOT NULL "
    "AND log_source_id IS NULL AND scope_group_id IS NULL) OR "
    "(scope_kind = 'LOG_SOURCE_GROUP' AND scope_group_id IS NOT NULL "
    "AND log_source_id IS NULL AND scope_type_id IS NULL)"
)
#: One property is the base case, two the combination (§G.3). `CASE` keeps the length
#: call off a non-array, which Postgres would raise on rather than evaluate to false.
PROPERTY_WATCH_PROPERTIES_CHECK = (
    "CASE WHEN jsonb_typeof(properties) = 'array' "
    "THEN jsonb_array_length(properties) BETWEEN 1 AND 2 ELSE false END"
)
_ARRAY_CHECK = "jsonb_typeof({column}) = 'array'"


class PropertyWatch(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One watch: a scope, one or two property references, pinned values, detectors.

    `watch_version` is the semantics identity: changing the scope or the property set
    bumps it, and baselines, novelty history and value rows are stamped with it and never
    compared across it. Changing only `pinned_values` or `enabled` does not.
    """

    __tablename__ = "property_watch"
    __table_args__ = (
        CheckConstraint(PROPERTY_WATCH_SCOPE_CHECK, name="scope_matches_kind"),
        CheckConstraint(PROPERTY_WATCH_PROPERTIES_CHECK, name="properties_one_or_two"),
        CheckConstraint(_ARRAY_CHECK.format(column="pinned_values"), name="pinned_values_array"),
        CheckConstraint(_ARRAY_CHECK.format(column="detectors"), name="detectors_array"),
        CheckConstraint(f"severity IN {_in_list(Severity)}", name="severity_known"),
        CheckConstraint(
            f"last_status IS NULL OR last_status IN {_in_list(PwWatchStatus)}",
            name="last_status_known",
        ),
        CheckConstraint("watch_version >= 1", name="watch_version_positive"),
        CheckConstraint("length(name) > 0", name="name_not_empty"),
        UniqueConstraint("name", name="uq_property_watch_name"),
        Index("ix_property_watch_instance", "instance_id", "enabled"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    scope_kind: Mapped[PwScopeKind] = mapped_column(String(16), nullable=False)
    #: Set iff LOG_SOURCE (the CHECK above).
    log_source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE")
    )
    #: Set iff LOG_SOURCE_TYPE: a QRadar `devicetype` id (`LogSourceType.type_id`).
    scope_type_id: Mapped[int | None] = mapped_column(Integer)
    #: Set iff LOG_SOURCE_GROUP: a QRadar group id (`LogSourceGroup.qradar_id`).
    scope_group_id: Mapped[int | None] = mapped_column(Integer)

    #: Ordered `[{"kind": "CUSTOM_PROPERTY" | "NORMALIZED", "name": str}, ...]`, one or two.
    properties: Mapped[list[dict[str, str]]] = mapped_column(JSONB, nullable=False)
    #: One entry per pinned value, one string per property; each passes
    #: `PW_PINNED_VALUE_PATTERN`. A single-property watch pins `[["DENY"]]`.
    pinned_values: Mapped[list[list[str]]] = mapped_column(JSONB, default=list, nullable=False)
    #: The `PwAnomalyType` values this watch evaluates.
    detectors: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)
    severity: Mapped[Severity] = mapped_column(
        String(16), default=Severity.MEDIUM, nullable=False
    )
    watch_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    #: Off until the operator turns it on; the preview runs on a disabled watch.
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    #: Bucket start of the last collected bucket. Advances only past completed buckets.
    collected_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Bucket start of the last evaluated bucket.
    evaluated_through: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: A `PwWatchStatus`; NULL means the watch has never run, which is not `OK`.
    last_status: Mapped[PwWatchStatus | None] = mapped_column(String(16))
    last_status_reason: Mapped[str | None] = mapped_column(Text)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class PwBucket(Base):
    """What collection established for one watch and bucket.

    The row exists whenever a bucket was judged collectable; every count is nullable so a
    failed totals search cannot read as a measured zero. `unparsed_events` is the remainder
    (`total - parsed`): never a value row, never admitted, never a share's denominator. It
    is exact when `values_measurement` is `EXACT` and an upper bound when `LOWER_BOUND`.
    Value texts occur only in `pw_value`; nothing here carries one.

    TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "pw_bucket"
    __table_args__ = (
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
        CheckConstraint("watch_version >= 1", name="watch_version_positive"),
        CheckConstraint(
            f"values_measurement IN {_in_list(PwBucketMeasurement)}",
            name="values_measurement_known",
        ),
        CheckConstraint(
            f"pinned_measurement IN {_in_list(PwPinnedMeasurement)}",
            name="pinned_measurement_known",
        ),
        # An incomplete totals search leaves the totals unknown, never 0.
        CheckConstraint(
            "totals_complete OR (total_events IS NULL AND unparsed_events IS NULL)",
            name="totals_unknown_when_incomplete",
        ),
        *(
            CheckConstraint(f"{name} IS NULL OR {name} >= 0", name=f"{name}_non_negative")
            for name in (
                "total_events",
                "parsed_events",
                "unparsed_events",
                "untracked_events",
                "scope_member_count",
            )
        ),
        *(
            CheckConstraint(f"{name} >= 0", name=f"{name}_non_negative")
            for name in (
                "observed_values",
                "tracked_values_observed",
                "admitted_values",
                "below_admission_threshold",
                "rejected_over_quota",
            )
        ),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("property_watch.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    watch_version: Mapped[int] = mapped_column(Integer, nullable=False)

    #: Every event in scope: its own ungrouped aggregate. NULL when the totals search
    #: did not complete.
    total_events: Mapped[int | None] = mapped_column(BigInteger)
    #: Events the grouped search saw carrying every watched property.
    parsed_events: Mapped[int | None] = mapped_column(BigInteger)
    unparsed_events: Mapped[int | None] = mapped_column(BigInteger)
    #: Events of values the fold cut or that were not admitted; set only when the grouped
    #: search was whole, otherwise NULL (unknown).
    untracked_events: Mapped[int | None] = mapped_column(BigInteger)
    values_measurement: Mapped[PwBucketMeasurement] = mapped_column(String(16), nullable=False)
    pinned_measurement: Mapped[PwPinnedMeasurement] = mapped_column(String(16), nullable=False)
    totals_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)

    #: Admission outcomes: how the new-value detector tells "not new" from "refused".
    observed_values: Mapped[int] = mapped_column(Integer, nullable=False)
    tracked_values_observed: Mapped[int] = mapped_column(Integer, nullable=False)
    admitted_values: Mapped[int] = mapped_column(Integer, nullable=False)
    below_admission_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_over_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    admission_truncated: Mapped[bool] = mapped_column(Boolean, nullable=False)

    scope_member_count: Mapped[int | None] = mapped_column(Integer)
    #: Hash of the sorted resolved source ids; NULL for a type scope.
    scope_fingerprint: Mapped[str | None] = mapped_column(String(64))
    scope_changed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    in_maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: The generated AQL, read-only provenance (the catch-all pattern).
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PwValue(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A tracked value: pinned by the operator or admitted by the top-N rules.

    The only value texts that persist are admitted and pinned ones (design §G.7.1). The
    natural key carries `watch_version`, so a value of `Username` under another property
    set is another row. For a combination watch `value_key` is the components joined by
    U+001F and `component_0` / `component_1` hold the split, indexed for the novelty lookup.
    """

    __tablename__ = "pw_value"
    __table_args__ = (
        UniqueConstraint("watch_id", "watch_version", "value_key", name="uq_pw_value_natural_key"),
        CheckConstraint("length(value_key) > 0", name="value_key_not_empty"),
        CheckConstraint("watch_version >= 1", name="watch_version_positive"),
        Index("ix_pw_value_active", "watch_id", "watch_version", "active"),
        Index("ix_pw_value_component_0", "watch_id", "watch_version", "component_0"),
        Index("ix_pw_value_component_1", "watch_id", "watch_version", "component_1"),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("property_watch.id", ondelete="CASCADE"), nullable=False
    )
    watch_version: Mapped[int] = mapped_column(Integer, nullable=False)
    value_key: Mapped[str] = mapped_column(String(255), nullable=False)
    component_0: Mapped[str | None] = mapped_column(String(255))
    component_1: Mapped[str | None] = mapped_column(String(255))
    #: The fold cut the text to the stored width.
    truncated: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    first_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The tracking window: before `tracked_since` and after `tracked_until` an absent row
    #: is not a measured zero (WAF §29.4).
    tracked_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tracked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PwValueMetric(Base):
    """Sparse per-bucket event count for a tracked value.

    No row means unknown or a measured zero; the bucket row decides which.

    TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "pw_value_metric"
    __table_args__ = (
        CheckConstraint("events >= 0", name="events_non_negative"),
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
    )

    pw_value_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pw_value.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class PwValueBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Seasonal aggregate for one tracked value and metric cell (weekday x hour).

    Robust statistics and bounded numeric observations only; never a value text.
    """

    __tablename__ = "pw_value_baseline"
    __table_args__ = (
        UniqueConstraint(
            "pw_value_id", "metric_name", "weekday", "hour", name="uq_pw_value_baseline_cell"
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_range"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="hour_range"),
    )

    pw_value_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pw_value.id", ondelete="CASCADE"), nullable=False
    )
    metric_name: Mapped[str] = mapped_column(String(64), nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    hour: Mapped[int] = mapped_column(Integer, nullable=False)
    median: Mapped[float] = mapped_column(Float, nullable=False)
    mad: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    p05: Mapped[float | None] = mapped_column(Float)
    p95: Mapped[float | None] = mapped_column(Float)
    sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_reliable: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    completeness: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    observations: Mapped[list[float]] = mapped_column(JSONB, default=list, nullable=False)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    computed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    baseline_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    excluded_sample_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: The exclusion tally: `low_coverage`, `scope_changed`, `incomplete`, ...
    exclusion_counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metric_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))
    watch_version: Mapped[int] = mapped_column(Integer, nullable=False)


class PwDetectorState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hysteresis state for one (tracked value, detector). Counters only, no value text."""

    __tablename__ = "pw_detector_state"
    __table_args__ = (
        UniqueConstraint("pw_value_id", "detector_type", name="uq_pw_detector_state_value"),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("property_watch.id", ondelete="CASCADE"), nullable=False
    )
    pw_value_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pw_value.id", ondelete="CASCADE"), nullable=False
    )
    detector_type: Mapped[PwAnomalyType] = mapped_column(String(40), nullable=False)
    consecutive_anomalous: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_healthy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.NORMAL, nullable=False
    )
    last_interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    open_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class PwAnomaly(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A detected deviation on one tracked value of one watch.

    It persists aggregate measurements, versions and bounded details. `value_key` repeats
    the value's text (design §G.7.2, D-22: clear text, bounded to 255) so the incident
    outlives the value row: `pw_value_id` is set NULL when retention deletes the value, and
    the incident goes with its watch.
    """

    __tablename__ = "pw_anomaly"
    __table_args__ = (
        CheckConstraint("length(value_key) > 0", name="value_key_not_empty"),
        Index("ix_pw_anomaly_state", "watch_id", "detector_type", "state"),
        Index("ix_pw_anomaly_detected", "detected_at"),
    )

    watch_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("property_watch.id", ondelete="CASCADE"), nullable=False
    )
    pw_value_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pw_value.id", ondelete="SET NULL")
    )
    value_key: Mapped[str] = mapped_column(String(255), nullable=False)
    detector_type: Mapped[PwAnomalyType] = mapped_column(String(40), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[Severity] = mapped_column(String(16), nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.CANDIDATE, nullable=False
    )
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    deviation_score: Mapped[float | None] = mapped_column(Float)
    deviation_ratio: Mapped[float | None] = mapped_column(Float)
    robust_z: Mapped[float | None] = mapped_column(Float)
    absolute_delta: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    consecutive_buckets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    baseline_version: Mapped[int | None] = mapped_column(Integer)
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    watch_version: Mapped[int] = mapped_column(Integer, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_status: Mapped[EvidenceStatus] = mapped_column(
        String(16), default=EvidenceStatus.NOT_REQUESTED, nullable=False
    )
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(255))
    observed_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), default=WafMeasurement.EXACT, nullable=False
    )


class PwAnomalyTransition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Auditable lifecycle transition for one property-watch anomaly.

    Repeats only bounded aggregate measurements needed to explain the state change.
    """

    __tablename__ = "pw_anomaly_transition"
    __table_args__ = (Index("ix_pw_anomaly_transition", "pw_anomaly_id", "occurred_at"),)

    pw_anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("pw_anomaly.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[AnomalyState | None] = mapped_column(String(24))
    to_state: Mapped[AnomalyState] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(64), default="pw-engine", nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    observed_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), default=WafMeasurement.EXACT, nullable=False
    )
