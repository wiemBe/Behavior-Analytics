"""Phase D IPS source profiles, collection facts and admitted entities.

Raw IPS event values never live here. Collection folds them first, then stores
source-level aggregates, bounded contributor lists and admitted entity metrics.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AnomalyState,
    EvidenceStatus,
    IpsAnomalyType,
    IpsDimension,
    IpsPreset,
    IpsProfileValidationStatus,
    Severity,
    WafMeasurement,
)
from app.models.history_backfill import ORIGIN_CHECK
from app.models.message_ref import stored_code, stored_params
from app.models.waf import METRIC_SEMANTICS_LENGTH

IPS_NORMALIZER_VERSION = 1
_MEASUREMENT_CHECK = "('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'FAILED', 'REJECTED')"


class IpsSourceProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A versioned logical-field mapping for one IPS log source.

    Mapping changes reset validation. Collection may later use the profile only
    when the current version is enabled and ``VALID``.
    """

    __tablename__ = "ips_source_profile"
    __table_args__ = (
        CheckConstraint("profile_version >= 1", name="profile_version_positive"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        nullable=False,
    )
    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    product_label: Mapped[str | None] = mapped_column(String(128))
    preset: Mapped[IpsPreset | None] = mapped_column(String(32))
    mapping: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    action_value_map: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    validation_status: Mapped[IpsProfileValidationStatus] = mapped_column(
        String(16), default=IpsProfileValidationStatus.UNVALIDATED, nullable=False
    )
    validated_profile_version: Mapped[int | None] = mapped_column(Integer)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_report: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    validation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_requested_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class IpsSourceBucket(Base):
    """What collection established for one IPS source and bucket.

    The row exists whenever TOTALS was complete, including when no entity was
    admitted. Every other dimension has its own measurement state and nullable
    facts, so a failed or uncollected search cannot read as a measured zero.
    Source addresses occur only in the two contributor lists in ``contributors``.

    TimescaleDB hypertable on ``bucket_start``.
    """

    __tablename__ = "ips_source_bucket"
    __table_args__ = (
        CheckConstraint(ORIGIN_CHECK, name="origin_known"),
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
        CheckConstraint("total_events >= 0", name="total_events_non_negative"),
        CheckConstraint("total_records >= 0", name="total_records_non_negative"),
        CheckConstraint(
            "jsonb_array_length(COALESCE(contributors->'SOURCE_IP', '[]'::jsonb)) <= 10 "
            "AND jsonb_array_length(COALESCE(contributors->'PORT_FANOUT', '[]'::jsonb)) <= 10",
            name="contributors_bounded",
        ),
        *(
            CheckConstraint(
                f"{name} IS NULL OR {name} >= 0", name=f"{name}_non_negative"
            )
            for name in (
                "source_events",
                "distinct_sources",
                "top1_events",
                "top5_events",
                "max_distinct_ports",
                "blocked_events",
                "allowed_events",
                "unmapped_events",
            )
        ),
        *(
            CheckConstraint(
                f"{name}_measurement IN {_MEASUREMENT_CHECK}",
                name=f"{name}_measurement_known",
            )
            for name in (
                "destination_port",
                "destination_ip",
                "signature",
                "source_ip",
                "port_fanout",
                "action",
            )
        ),
        *(
            CheckConstraint(
                f"{name}_coverage_ratio IS NULL OR "
                f"({name}_coverage_ratio >= 0.0 AND {name}_coverage_ratio <= 1.0)",
                name=f"{name}_coverage_ratio_range",
            )
            for name in (
                "destination_port", "destination_ip", "signature", "source_ip", "action"
            )
        ),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    #: 'LIVE' for a row the cadence collection wrote, 'HISTORY' for one the history
    #: backfill read (HISTORY-BACKFILL §B.4). No default: a writer must say which.
    origin: Mapped[str] = mapped_column(String(8), nullable=False)
    total_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    total_records: Mapped[int] = mapped_column(BigInteger, nullable=False)

    destination_port_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_port_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    destination_port_coverage_lower_bound: Mapped[bool | None] = mapped_column(Boolean)
    destination_ip_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    destination_ip_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    destination_ip_coverage_lower_bound: Mapped[bool | None] = mapped_column(Boolean)
    signature_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    signature_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    signature_coverage_lower_bound: Mapped[bool | None] = mapped_column(Boolean)
    source_ip_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    source_ip_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    source_ip_coverage_lower_bound: Mapped[bool | None] = mapped_column(Boolean)
    port_fanout_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    action_measurement: Mapped[str] = mapped_column(String(16), nullable=False)
    action_coverage_ratio: Mapped[float | None] = mapped_column(Float)
    action_coverage_lower_bound: Mapped[bool | None] = mapped_column(Boolean)

    source_events: Mapped[int | None] = mapped_column(BigInteger)
    distinct_sources: Mapped[int | None] = mapped_column(Integer)
    distinct_sources_lower_bound: Mapped[bool | None] = mapped_column(Boolean)
    top1_events: Mapped[int | None] = mapped_column(BigInteger)
    top5_events: Mapped[int | None] = mapped_column(BigInteger)
    max_distinct_ports: Mapped[int | None] = mapped_column(Integer)
    blocked_events: Mapped[int | None] = mapped_column(BigInteger)
    allowed_events: Mapped[int | None] = mapped_column(BigInteger)
    unmapped_events: Mapped[int | None] = mapped_column(BigInteger)

    #: ``{"SOURCE_IP": [{"source_ip": address, "events": count}, ...], ...}``;
    #: each list is bounded to ten by the pure folds.
    contributors: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    #: Admission outcomes keyed by the three entity dimension names. Missing
    #: keys mean that dimension had no measurement; they are never filled with 0.
    admitted: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    below_admission_threshold: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    rejected_over_quota: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    untracked_entities: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    untracked_events: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    admission_truncated: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalizer_version: Mapped[int] = mapped_column(
        Integer, default=IPS_NORMALIZER_VERSION, nullable=False
    )
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    in_maintenance: Mapped[bool] = mapped_column(Boolean, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IpsEntity(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An admitted destination port, destination IP or signature."""

    __tablename__ = "ips_entity"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "log_source_id",
            "dimension",
            "value_key",
            name="uq_ips_entity_natural_key",
        ),
        Index("ix_ips_entity_active", "log_source_id", "dimension", "active"),
        CheckConstraint(
            "dimension IN ('DESTINATION_PORT', 'DESTINATION_IP', 'SIGNATURE')",
            name="dimension_entity_known",
        ),
        CheckConstraint("length(value_key) > 0", name="value_key_not_empty"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), nullable=False
    )
    dimension: Mapped[IpsDimension] = mapped_column(String(32), nullable=False)
    value_key: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    normalizer_version: Mapped[int] = mapped_column(
        Integer, default=IPS_NORMALIZER_VERSION, nullable=False
    )


class IpsEntityMetric(Base):
    """Sparse per-bucket event count for an admitted IPS entity."""

    __tablename__ = "ips_entity_metric"
    __table_args__ = (
        CheckConstraint("events >= 0", name="events_non_negative"),
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
    )

    ips_entity_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ips_entity.id", ondelete="CASCADE"), primary_key=True
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)


class IpsEntityBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Seasonal aggregate for one admitted IPS entity and metric cell.

    Holds robust statistics and bounded numeric observations only. It never
    stores the entity's raw QRadar value or any source address.
    """

    __tablename__ = "ips_entity_baseline"
    __table_args__ = (
        UniqueConstraint(
            "ips_entity_id",
            "metric_name",
            "weekday",
            "hour",
            name="uq_ips_entity_baseline_cell",
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_range"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="hour_range"),
    )

    ips_entity_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ips_entity.id", ondelete="CASCADE"), nullable=False
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
    exclusion_counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metric_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, nullable=False)


class IpsSourceBaseline(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Seasonal aggregate for one IPS source-level metric cell.

    Ratio cells retain pooled aggregate sums, never the raw action values or
    source IPs from which collection computed them.
    """

    __tablename__ = "ips_source_baseline"
    __table_args__ = (
        UniqueConstraint(
            "log_source_id",
            "metric_name",
            "weekday",
            "hour",
            name="uq_ips_source_baseline_cell",
        ),
        CheckConstraint("weekday BETWEEN 1 AND 7", name="weekday_range"),
        CheckConstraint("hour BETWEEN 0 AND 23", name="hour_range"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), nullable=False
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
    exclusion_counts: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    metric_semantics: Mapped[str | None] = mapped_column(String(METRIC_SEMANTICS_LENGTH))
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, nullable=False)
    numerator_sum: Mapped[float | None] = mapped_column(Float)
    denominator_sum: Mapped[float | None] = mapped_column(Float)


_IPS_SUBJECT_CHECK = (
    "(subject_kind = 'IPS_ENTITY' AND ips_entity_id IS NOT NULL "
    "AND log_source_id IS NULL) OR "
    "(subject_kind = 'IPS_SOURCE' AND ips_entity_id IS NULL "
    "AND log_source_id IS NOT NULL)"
)


class IpsDetectorState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hysteresis state for exactly one IPS entity or IPS source detector.

    It contains lifecycle counters only, never event values or contributor
    identities. Partial unique indexes enforce one state per typed subject.
    """

    __tablename__ = "ips_detector_state"
    __table_args__ = (
        CheckConstraint(_IPS_SUBJECT_CHECK, name="subject_matches_kind"),
        Index(
            "uq_ips_detector_state_entity",
            "ips_entity_id",
            "detector_type",
            unique=True,
            postgresql_where=text("subject_kind = 'IPS_ENTITY'"),
        ),
        Index(
            "uq_ips_detector_state_source",
            "log_source_id",
            "detector_type",
            unique=True,
            postgresql_where=text("subject_kind = 'IPS_SOURCE'"),
        ),
    )

    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ips_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ips_entity.id", ondelete="CASCADE")
    )
    log_source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE")
    )
    detector_type: Mapped[IpsAnomalyType] = mapped_column(String(40), nullable=False)
    consecutive_anomalous: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_healthy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.NORMAL, nullable=False
    )
    last_interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    open_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class IpsAnomaly(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A detected IPS deviation on exactly one entity or source.

    The incident persists aggregate measurements, versions and bounded details.
    It never persists raw IPS events or unbounded source-IP values.
    """

    __tablename__ = "ips_anomaly"
    __table_args__ = (
        CheckConstraint(_IPS_SUBJECT_CHECK, name="subject_matches_kind"),
        Index(
            "ix_ips_anomaly_state",
            "ips_entity_id",
            "log_source_id",
            "detector_type",
            "state",
        ),
        Index("ix_ips_anomaly_detected", "detected_at"),
    )

    subject_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    ips_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ips_entity.id", ondelete="CASCADE")
    )
    log_source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE")
    )
    detector_type: Mapped[IpsAnomalyType] = mapped_column(String(40), nullable=False)
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
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    normalizer_version: Mapped[int] = mapped_column(Integer, nullable=False)
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


class IpsAnomalyTransition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Auditable lifecycle transition for one IPS anomaly.

    It repeats only bounded aggregate measurements needed to explain the state
    change and never stores a raw IPS event.
    """

    __tablename__ = "ips_anomaly_transition"
    __table_args__ = (
        Index("ix_ips_anomaly_transition", "ips_anomaly_id", "occurred_at"),
    )

    ips_anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ips_anomaly.id", ondelete="CASCADE"), nullable=False
    )
    from_state: Mapped[AnomalyState | None] = mapped_column(String(24))
    to_state: Mapped[AnomalyState] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    #: `{code, params}` of `reason` when the lifecycle supplied one (NULL on earlier rows).
    reason_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    actor: Mapped[str] = mapped_column(
        String(64), default="ips-anomaly-engine", nullable=False
    )
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    observed_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), default=WafMeasurement.EXACT, nullable=False
    )

    @property
    def reason_code(self) -> str | None:
        return stored_code(self.reason_i18n)

    @property
    def reason_params(self) -> dict[str, Any] | None:
        return stored_params(self.reason_i18n)
