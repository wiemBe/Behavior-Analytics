"""Immutable shift handover notes and their frozen aggregate queue snapshots."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, String, desc
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, utcnow


class ShiftHandover(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "shift_handover"
    __table_args__ = (
        CheckConstraint("char_length(note) BETWEEN 1 AND 4000", name="note_length"),
        Index("ix_shift_handover_written_at", desc("written_at")),
    )

    author_subject: Mapped[str] = mapped_column(String(255), nullable=False)
    written_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    window_started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    previous_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("shift_handover.id", ondelete="SET NULL"),
    )
    note: Mapped[str] = mapped_column(String(4000), nullable=False)
    body: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
