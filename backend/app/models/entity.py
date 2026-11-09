"""Normalized entity mentions stored alongside their bounded parent evidence."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

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
)


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
