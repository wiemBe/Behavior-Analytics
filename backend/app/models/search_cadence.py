"""Operator-selected cadence and last-run state for each QRadar search family."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import SearchCadence, SearchFamily

_ALLOWED_CADENCES = ", ".join(str(int(cadence)) for cadence in SearchCadence)


class SearchCadenceSetting(Base):
    __tablename__ = "search_cadence"
    __table_args__ = (
        CheckConstraint(
            f"cadence_seconds IN ({_ALLOWED_CADENCES})",
            name="cadence_seconds_allowed",
        ),
    )

    family: Mapped[str] = mapped_column(String(32), primary_key=True)
    cadence_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    last_slot_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255))

    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_run_outcome: Mapped[str | None] = mapped_column(String(32))
    last_run_searches: Mapped[int | None] = mapped_column(Integer)
    last_run_buckets: Mapped[int | None] = mapped_column(Integer)
    last_run_splits: Mapped[int | None] = mapped_column(Integer)

    @property
    def search_family(self) -> SearchFamily:
        return SearchFamily(self.family)
