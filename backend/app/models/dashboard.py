"""Saved dashboards (UI redesign, UR.5.5): a user's widget layout, and their default.

Additive (AGENTS.md rule 7): two new tables, no Phase A, WAF, IPS or catch-all table or enum
changes. Templates are not rows; they live in the frontend. The backend stores what a user
saved and validates the structure of the layout, never its meaning.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow


class DashboardVisibility(StrEnum):
    """`PRIVATE` is the owner's alone; `SHARED` is read-only to every dashboard reader."""

    PRIVATE = "PRIVATE"
    SHARED = "SHARED"


class Dashboard(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "dashboard"
    __table_args__ = (
        CheckConstraint("visibility IN ('PRIVATE', 'SHARED')", name="visibility_known"),
        CheckConstraint("schema_version >= 1", name="schema_version_positive"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        UniqueConstraint("owner_subject", "name", name="uq_dashboard_owner_subject_name"),
        Index("ix_dashboard_owner", "owner_subject", text("updated_at DESC")),
    )

    #: The principal's `subject`: `local-dev` today, the OIDC subject later.
    owner_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    visibility: Mapped[DashboardVisibility] = mapped_column(
        String(16), nullable=False, default=DashboardVisibility.PRIVATE.value
    )
    #: The frontend template it was created from. Informational.
    template_key: Mapped[str | None] = mapped_column(String(32))
    template_version: Mapped[int | None] = mapped_column(Integer)
    #: The layout JSON version; always equal to the layout's own `v`.
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    layout: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    #: Optimistic concurrency: every successful write increments it.
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(255))


class UserDashboardPref(Base):
    __tablename__ = "user_dashboard_pref"

    owner_subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: SET NULL, so deleting a dashboard clears the default pointing at it.
    default_dashboard_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("dashboard.id", ondelete="SET NULL")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )
