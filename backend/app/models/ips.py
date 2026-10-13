"""Phase D IPS source profiles, collection facts and admitted entities.

Raw IPS event values never live here. Collection folds them first, then stores
source-level aggregates, bounded contributor lists and admitted entity metrics.
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
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import IpsDimension, IpsPreset, IpsProfileValidationStatus

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
