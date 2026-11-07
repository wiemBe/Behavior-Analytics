"""The per-user "last visit" marker for the triage queue (SOC-4a, design `SOC-4.design.md` §2).

Additive (AGENTS.md rule 7): one new table. One row per principal subject holds a single
instant, the moment before which the analyst has already been shown the queue. Not per kind
and not per view. No FK, by the `user_dashboard_pref` precedent: `local-dev` has no account.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class UserQueueMarker(Base):
    __tablename__ = "user_queue_marker"

    #: The principal's `subject`: `local-dev` today, the OIDC subject later.
    owner_subject: Mapped[str] = mapped_column(String(255), primary_key=True)
    #: Rows after this instant are NEW to the caller. Only ever moves forward.
    marked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The value before the last advance. Null until the first advance after the first write.
    previous_marked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=utcnow, nullable=False
    )
