"""Log-source fleet inventory dimensions and classification state."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    ClassificationReviewStatus,
    ClassificationRuleKind,
    SourceClass,
)


class LogSourceType(Base):
    __tablename__ = "log_source_type"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    type_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    internal: Mapped[bool] = mapped_column(Boolean, nullable=False)
    custom: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LogSourceGroup(Base):
    __tablename__ = "log_source_group"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    qradar_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    parent_qradar_id: Mapped[int | None] = mapped_column(Integer)
    assignable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClassificationRule(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "classification_rule"
    __table_args__ = (
        UniqueConstraint(
            "instance_id",
            "kind",
            "pattern",
            name="uq_classification_rule_instance_kind_pattern",
            postgresql_nulls_not_distinct=True,
        ),
    )

    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE")
    )
    kind: Mapped[ClassificationRuleKind] = mapped_column(String(32), nullable=False)
    pattern: Mapped[str] = mapped_column(String(256), nullable=False)
    target_class: Mapped[SourceClass] = mapped_column(String(32), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    built_in: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str | None] = mapped_column(String(255))


class LogSourceClassification(Base):
    __tablename__ = "log_source_classification"

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        primary_key=True,
    )
    source_class: Mapped[SourceClass] = mapped_column(String(32), nullable=False)
    basis: Mapped[str] = mapped_column(String(32), nullable=False)
    rule_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("classification_rule.id", ondelete="SET NULL")
    )
    override_class: Mapped[SourceClass | None] = mapped_column(String(32))
    override_by: Mapped[str | None] = mapped_column(String(255))
    override_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    conflicts: Mapped[list[dict]] = mapped_column(JSONB, default=list, nullable=False)
    classified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    review_status: Mapped[ClassificationReviewStatus] = mapped_column(
        String(16), nullable=False
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reviewed_by: Mapped[str | None] = mapped_column(String(255))
