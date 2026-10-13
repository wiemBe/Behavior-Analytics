"""Phase D IPS source profile configuration.

Only the per-source mapping and its read-only validation state live here. IPS
collection, entities and observations are introduced by later Phase D tasks.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import IpsPreset, IpsProfileValidationStatus


class IpsSourceProfile(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A versioned logical-field mapping for one IPS log source.

    Mapping changes reset validation. Collection may later use the profile only
    when the current version is enabled and ``VALID``.
    """

    __tablename__ = "ips_source_profile"
    __table_args__ = (
        CheckConstraint("profile_version >= 1", name="profile_version_positive"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        nullable=False,
    )
    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    product_label: Mapped[str | None] = mapped_column(String(128))
    preset: Mapped[IpsPreset | None] = mapped_column(String(32))
    mapping: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    action_value_map: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    validation_status: Mapped[IpsProfileValidationStatus] = mapped_column(
        String(16), default=IpsProfileValidationStatus.UNVALIDATED, nullable=False
    )
    validated_profile_version: Mapped[int | None] = mapped_column(Integer)
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_report: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    validation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_requested_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))
