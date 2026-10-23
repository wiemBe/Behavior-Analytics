"""Operational state for background collection: watermarks and job locks."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class CollectionWatermark(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """How far collection has progressed for a (instance, collector kind).

    The watermark is the UTC start of the last fully-collected interval. The
    collector resumes from here, bounded backfill fills gaps, and lag is the
    distance between the watermark and now. Idempotency and gap detection both
    key off this row.
    """

    __tablename__ = "collection_watermark"
    __table_args__ = (
        UniqueConstraint("instance_id", "collector", name="uq_collection_watermark_key"),
        Index("ix_collection_watermark_instance", "instance_id"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    # e.g. "log_source_metric", "offense_snapshot"
    collector: Mapped[str] = mapped_column(String(64), nullable=False)

    # UTC start of the last fully-collected interval.
    watermark_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lag_seconds: Mapped[int | None] = mapped_column(BigInteger)
    intervals_collected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(String(512))
    # Collector-specific, non-secret evidence about how a watermark was
    # produced.  Rule metrics use this to distinguish a complete observation
    # window from the deliberately incomplete offense-contribution feed.
    collection_metadata: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class AnomalyEvaluationWatermark(Base):
    """Last Phase A bucket evaluated for one monitored log source."""

    __tablename__ = "anomaly_evaluation_watermark"

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    evaluated_through: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class AnomalyEvaluationFault(UUIDPrimaryKeyMixin, Base):
    """A bucket the anomaly engine tried to judge and could not.

    The row is the only record of a bucket that has no verdict: a given-up
    bucket writes nothing into `log_source_detector_state` or
    `log_source_anomaly`, so "no row" must never be read as "no anomaly". One
    row per (source, bucket): `attempts` counts evaluation ticks,
    `given_up_at` marks the bucket the engine stopped retrying, and
    `resolved_at` marks an open fault a later successful bucket cleared.

    An open fault is `resolved_at IS NULL AND given_up_at IS NULL`. There is at
    most one per source, because the engine breaks out of a source's loop while
    a fault is still retryable.
    """

    __tablename__ = "anomaly_evaluation_fault"
    __table_args__ = (
        UniqueConstraint(
            "log_source_id", "bucket_start", name="uq_anomaly_evaluation_fault_bucket"
        ),
        CheckConstraint("attempts >= 1", name="attempts_positive"),
        Index("ix_anomaly_evaluation_fault_source_open", "log_source_id", "resolved_at"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: UTC start of the bucket that failed.
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Failed evaluation ticks so far; the engine gives up at the configured K.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    first_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The exception's class name only. A message can carry a data value.
    error_class: Mapped[str] = mapped_column(String(64), nullable=False)
    #: Set when the attempt limit was reached: the bucket stays "not judged".
    given_up_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Set when a later bucket was judged, so the retry succeeded.
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
