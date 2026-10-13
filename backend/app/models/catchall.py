"""Catch-all senders (Phase C.2b): who feeds SIM Generic and the Universal DSMs.

Phase A already judges a catch-all source's total volume. These tables add who is
behind it. A sender name is chosen by whoever sends, so a sender row exists only
for a name that passed admission (`app.catchall.fold`); a refused name is counted
in the bucket and never stored.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
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

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import CatchallMeasurement, CatchallReviewStatus


class CatchallSender(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One admitted sender of one catch-all log source.

    `events_24h` is recomputed per run from the stored buckets' top lists, so it
    is a lower bound; NULL means no stored top list in the last 24 hours named
    the sender, which is unknown rather than zero.
    """

    __tablename__ = "catchall_sender"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "catchall_log_source_id",
            "identifier",
            name="uq_catchall_sender_key",
        ),
        CheckConstraint(
            "review_status IN ('NEW', 'REVIEWED', 'DISMISSED')",
            name="review_status_known",
        ),
        CheckConstraint(
            "char_length(identifier) BETWEEN 1 AND 255", name="identifier_length"
        ),
        CheckConstraint("events_24h IS NULL OR events_24h >= 0", name="events_24h_non_negative"),
        Index("ix_catchall_sender_review", "instance_id", "review_status"),
        Index("ix_catchall_sender_source", "catchall_log_source_id"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        nullable=False,
    )
    catchall_log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: Sanitized: printable, stripped, at most 255.
    identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    events_24h: Mapped[int | None] = mapped_column(BigInteger)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_status: Mapped[CatchallReviewStatus] = mapped_column(String(16), nullable=False)
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CatchallBucket(Base):
    """What the senders search established about one catch-all source and bucket.

    One row per (catch-all log source, measured bucket). A bucket that was not
    measured (failed, rejected, over budget) has no row, so absence is never a
    zero; `measurement` is LOWER_BOUND when the search was truncated at one
    bucket, and then `events`, `records` and `distinct_senders` are lower bounds.
    `top_senders` holds only admitted or active senders, busiest first.

    TimescaleDB hypertable on `bucket_start`.
    """

    __tablename__ = "catchall_bucket"
    __table_args__ = (
        CheckConstraint("measurement IN ('EXACT', 'LOWER_BOUND')", name="measurement_known"),
        CheckConstraint("bucket_seconds > 0", name="bucket_seconds_positive"),
        CheckConstraint(
            "events >= 0 AND records >= 0 AND distinct_senders >= 0 "
            "AND admitted >= 0 AND below_admission_threshold >= 0 "
            "AND rejected_over_quota >= 0 AND untracked_senders >= 0 "
            "AND untracked_events >= 0",
            name="counts_non_negative",
        ),
    )

    catchall_log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    bucket_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    records: Mapped[int] = mapped_column(BigInteger, nullable=False)
    distinct_senders: Mapped[int] = mapped_column(Integer, nullable=False)
    distinct_senders_lower_bound: Mapped[bool] = mapped_column(Boolean, nullable=False)
    measurement: Mapped[CatchallMeasurement] = mapped_column(String(16), nullable=False)
    #: At most `catchall_top_senders` `{"identifier", "events"}`, busiest first.
    top_senders: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    admitted: Mapped[int] = mapped_column(Integer, nullable=False)
    below_admission_threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    rejected_over_quota: Mapped[int] = mapped_column(Integer, nullable=False)
    untracked_senders: Mapped[int] = mapped_column(Integer, nullable=False)
    untracked_events: Mapped[int] = mapped_column(BigInteger, nullable=False)
    query_provenance: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
