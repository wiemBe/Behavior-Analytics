"""Named generic-webhook destinations (SOC-12 slice 2)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, EncryptedString, TimestampMixin, UUIDPrimaryKeyMixin


class NotificationDestination(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One encrypted webhook target administered independently of routing rules."""

    __tablename__ = "notification_destination"
    __table_args__ = (
        CheckConstraint(
            "char_length(btrim(name)) BETWEEN 1 AND 64", name="name_length"
        ),
        CheckConstraint(
            "last_test_status IS NULL OR last_test_status BETWEEN 100 AND 599",
            name="last_test_status_range",
        ),
    )

    name: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    url: Mapped[str] = mapped_column(EncryptedString(4096), nullable=False)
    allow_http: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    allow_private: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    hmac_secret: Mapped[str | None] = mapped_column(EncryptedString(8192))
    #: Compact JSON object. It is encrypted as one value so names and values are
    #: both absent from a raw database read; the API returns names only.
    headers: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_status: Mapped[int | None] = mapped_column(Integer)
    last_test_class: Mapped[str | None] = mapped_column(String(32))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(255), nullable=False)
