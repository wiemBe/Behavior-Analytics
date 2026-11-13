"""Delivery attempts of one notification (SOC-12e): class and status only."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin

#: Every class the dispatcher records. Never a body, URL, header or secret.
ATTEMPT_CLASSES = (
    "ok",
    "transport",
    "timeout",
    "http_status",
    "redirect_refused",
    "ssrf_refused",
    "no_notifier",
    "no_destination",
    "permanent",
    "payload_invalid",
)
#: At most this many attempts are kept per notification, newest first.
MAX_ATTEMPTS_KEPT = 8


class NotificationAttempt(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "notification_attempt"
    __table_args__ = (
        Index("ix_notification_attempt_notification", "notification_id", "attempted_at"),
        CheckConstraint(
            "error_class IN (" + ", ".join(f"'{c}'" for c in ATTEMPT_CLASSES) + ")",
            name="error_class_set",
        ),
        CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599", name="http_status_range"
        ),
    )

    notification_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("alert_notification.id", ondelete="CASCADE"),
        nullable=False,
    )
    attempted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    error_class: Mapped[str] = mapped_column(String(24), nullable=False)
