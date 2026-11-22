"""Source pause spans (design §5): one edge-written row per pause, not per bucket.

Half-open `[starts_at, ends_at)`, `ends_at` NULL while open; written only by
`app.services.source_pause_spans` and retained with the source.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

REASON_QRADAR_DISABLED = "QRADAR_DISABLED"
REASON_ABSENT = "ABSENT"
_REASONS = f"'{REASON_QRADAR_DISABLED}', '{REASON_ABSENT}'"


class LogSourcePause(Base):
    """One closed or open pause of a log source."""

    __tablename__ = "log_source_pause"
    __table_args__ = (
        CheckConstraint(f"reason IN ({_REASONS})", name="reason_known"),
        CheckConstraint("ends_at IS NULL OR ends_at > starts_at", name="ends_after_start"),
        Index("ix_log_source_pause_source_starts", "log_source_id", "starts_at"),
        Index(
            "uq_log_source_pause_open",
            "log_source_id",
            "reason",
            unique=True,
            postgresql_where=text("ends_at IS NULL"),
        ),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), primary_key=True
    )
    reason: Mapped[str] = mapped_column(String(32), primary_key=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
