"""Persisted names and refresh diagnostics; never extraction logic or payloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PropertyCatalogState(Base):
    __tablename__ = "property_catalog_state"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qradar_instance.id", ondelete="CASCADE"), primary_key=True
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applicability_complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    property_count: Mapped[int | None] = mapped_column(Integer)
    expression_count: Mapped[int | None] = mapped_column(Integer)
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    failure: Mapped[dict[str, Any] | None] = mapped_column(JSONB)


class PropertyCatalogEntry(Base):
    __tablename__ = "property_catalog_entry"

    instance_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("qradar_instance.id", ondelete="CASCADE"), primary_key=True
    )
    qradar_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    identifier: Mapped[str] = mapped_column(String(512), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    property_type: Mapped[str] = mapped_column(String(32), nullable=False)
    use_for_rule_engine: Mapped[bool] = mapped_column(Boolean, nullable=False)
    auto_discovered: Mapped[bool] = mapped_column(Boolean, nullable=False)
    name_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    type_ids: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)
