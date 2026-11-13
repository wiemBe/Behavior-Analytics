"""Notification routing rules (SOC-12 slice 3a)."""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, CheckConstraint, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class NotificationRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One routing rule. A NULL filter array matches any value.

    Rules **add** named destinations to an alert's routes; nothing here replaces the
    environment routes unless ``stop_default`` is set (D-72). INFO is excluded from
    ``min_severity`` because INFO alerts are never routed.
    """

    __tablename__ = "notification_rule"
    __table_args__ = (
        CheckConstraint("char_length(btrim(name)) BETWEEN 1 AND 64", name="name_length"),
        CheckConstraint(
            "min_severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')", name="min_severity_set"
        ),
        CheckConstraint(
            "(window_start_minute IS NULL AND window_end_minute IS NULL) OR "
            "(window_start_minute IS NOT NULL AND window_end_minute IS NOT NULL AND "
            "window_start_minute BETWEEN 0 AND 1439 AND window_end_minute BETWEEN 0 AND 1439 "
            "AND window_start_minute <> window_end_minute)",
            name="window_valid",
        ),
        CheckConstraint(
            "cardinality(destination_ids) BETWEEN 1 AND 10", name="destination_count"
        ),
    )

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, default=100, nullable=False)
    min_severity: Mapped[str] = mapped_column(String(16), nullable=False)
    families: Mapped[list[str] | None] = mapped_column(ARRAY(String(32)))
    owners: Mapped[list[str] | None] = mapped_column(ARRAY(String(255)))
    source_classes: Mapped[list[str] | None] = mapped_column(ARRAY(String(64)))
    #: QRadar log-source group ids, as `log_source.group_ids` and `alert.context` carry them.
    group_ids: Mapped[list[int] | None] = mapped_column(ARRAY(Integer))
    window_start_minute: Mapped[int | None] = mapped_column(Integer)
    window_end_minute: Mapped[int | None] = mapped_column(Integer)
    destination_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(PgUUID(as_uuid=True)), nullable=False
    )
    stop_default: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
