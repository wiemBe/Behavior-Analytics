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


#: The statuses one access-check result can hold (ACC-1).
ACCESS_STATUSES: tuple[str, ...] = (
    "ok",
    "forbidden",
    "unauthorized",
    "not_found",
    "unreachable",
    "error",
)
_ACCESS_STATUSES_SQL = ", ".join(f"'{name}'" for name in ACCESS_STATUSES)


class QRadarAccessResult(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """The latest access-check result of one console and one catalog entry (ACC-1).

    One row per (console, entry), overwritten by each check; there is no history. It keeps the
    status class, the HTTP status code, when the check ran, the status before this one, and the
    syncs this check started. It never keeps a response body, a header or a query: the check
    reads a status code and nothing else.
    """

    __tablename__ = "qradar_access_result"
    __table_args__ = (
        UniqueConstraint("instance_id", "entry_id", name="uq_qradar_access_result_key"),
        CheckConstraint(f"status IN ({_ACCESS_STATUSES_SQL})", name="status_known"),
        CheckConstraint(
            f"previous_status IS NULL OR previous_status IN ({_ACCESS_STATUSES_SQL})",
            name="previous_status_known",
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE"), nullable=False
    )
    #: The `AccessEntry.id` in `app/providers/access_catalog.py`.
    entry_id: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    #: The status QRadar answered; None when it did not answer.
    http_status: Mapped[int | None] = mapped_column(Integer)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The status the previous check stored; None on the first check of this entry.
    previous_status: Mapped[str | None] = mapped_column(String(16))
    #: Sync names this check started because the entry became reachable (a JSON list).
    syncs_started: Mapped[list[str]] = mapped_column(JSONB, default=list, nullable=False)


class QRadarAccessRun(Base):
    """When the access check of one console started and finished (ACC-1).

    One row per console, overwritten by each run, however it was started (beat, worker start-up
    or "check now"). A run is running while `finished_at` is empty or older than `started_at`; a
    run started more than `ACCESS_RUN_STALE_SECONDS` ago is treated as not running, so a worker
    that died mid-check cannot hold the flag forever. It keeps two times and nothing else.
    """

    __tablename__ = "qradar_access_run"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
