"""Normalized entity mentions stored alongside their bounded parent evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

ENTITY_KINDS = ("IP", "HOST", "USER")
ENTITY_ROLES = ("SOURCE_IP", "DESTINATION_IP", "HOST", "USER")
ENTITY_SOURCES = (
    "EXPLANATION",
    "WAF_TOP",
    "IPS_TOP",
    "IPS_ENTITY",
    "OFFENSE",
    "CATCHALL",
    "PW_VALUE",
    "LOG_SOURCE",
    "IDENTITY_TOP",
)
ENTITY_BACKFILL_STATUSES = ("PENDING", "RUNNING", "COMPLETE", "FAILED")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


class EntityMention(Base):
    """One typed, normalized entity value named by a stored aggregate or incident."""

    __tablename__ = "entity_mention"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_quoted(ENTITY_KINDS)})", name="kind_known"),
        CheckConstraint(f"role IN ({_quoted(ENTITY_ROLES)})", name="role_known"),
        CheckConstraint(f"source IN ({_quoted(ENTITY_SOURCES)})", name="source_known"),
        Index("ix_entity_mention_lookup", "kind", "value_norm", "occurred_at"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    value_norm: Mapped[str] = mapped_column(String(255), primary_key=True)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    ref_table: Mapped[str] = mapped_column(String(64), primary_key=True)
    ref_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), primary_key=True
    )
    occurred_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EntityMentionBackfill(Base):
    """Resume state for backfilling one mention source on one QRadar instance."""

    __tablename__ = "entity_mention_backfill"
    __table_args__ = (
        CheckConstraint(f"source IN ({_quoted(ENTITY_SOURCES)})", name="source_known"),
        CheckConstraint(
            f"status IN ({_quoted(ENTITY_BACKFILL_STATUSES)})", name="status_known"
        ),
        CheckConstraint("rows_written >= 0", name="rows_written_non_negative"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="PENDING", nullable=False)
    cursor: Mapped[dict | None] = mapped_column(JSONB)
    rows_written: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Short reason code/text written by the task; never a secret or a raw event.
    last_error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )
