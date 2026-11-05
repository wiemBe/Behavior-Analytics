"""Analyst triage state and its append-only activity log.

Triage is the analyst's record about an anomaly -- who owns it, whether it is being
worked, and what was decided -- and is deliberately separate from the detector's own
lifecycle (`AnomalyState`) on every anomaly table. A detector resolving an incident
must never clear an analyst's disposition, and an analyst closing a triage item must
never change what the detector observed.

The key is polymorphic: `(family, anomaly_id)`, unique. There is no foreign key
across the four anomaly tables, because a single column cannot point at four
parents. Retention handles the consequence: each anomaly retention task deletes the
triage row of an anomaly it removed, so the table stays bounded rather than
accumulating rows for incidents that no longer exist.

`anomaly_triage_event` is append-only. Comments are events of kind `COMMENT`; their
text is stored only on that row, never on the triage row, never in an audit detail and
never at INFO in a log. Comment text is an analyst note and may hold sensitive
material.

The models are intentionally small: a row exists only once somebody has touched the
anomaly. An anomaly with no row reads as `NEW`, unowned, version 0 in the API.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import AnomalyFamily, TriageDisposition, TriageEventKind, TriageStatus

_FAMILY_VALUES = ", ".join(f"'{value}'" for value in AnomalyFamily)
_STATUS_VALUES = ", ".join(f"'{value}'" for value in TriageStatus)
_DISPOSITION_VALUES = ", ".join(f"'{value}'" for value in TriageDisposition)
_KIND_VALUES = ", ".join(f"'{value}'" for value in TriageEventKind)

#: The note explains one disposition, so it is short enough to read in a timeline.
DISPOSITION_NOTE_MAX_LENGTH = 1000
#: A comment is an analyst's own words; long enough for a handover note, still bounded.
COMMENT_MAX_LENGTH = 2000
#: Owner and actor are principal subjects (a username or an e-mail address).
SUBJECT_MAX_LENGTH = 255


class AnomalyTriage(UUIDPrimaryKeyMixin, Base):
    """The current analyst-owned state of one anomaly."""

    __tablename__ = "anomaly_triage"
    __table_args__ = (
        UniqueConstraint(
            "family", "anomaly_id", name="uq_anomaly_triage_family_anomaly_id"
        ),
        CheckConstraint(f"family IN ({_FAMILY_VALUES})", name="family_known"),
        CheckConstraint(f"status IN ({_STATUS_VALUES})", name="status_known"),
        CheckConstraint(
            f"disposition IS NULL OR disposition IN ({_DISPOSITION_VALUES})",
            name="disposition_known",
        ),
        CheckConstraint(
            "status <> 'CLOSED' OR disposition IS NOT NULL",
            name="closed_has_disposition",
        ),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(
            f"disposition_note IS NULL OR char_length(disposition_note) <= "
            f"{DISPOSITION_NOTE_MAX_LENGTH}",
            name="disposition_note_length",
        ),
        Index("ix_anomaly_triage_status", "status"),
        Index("ix_anomaly_triage_owner", "owner"),
    )

    #: One of AnomalyFamily: the table `anomaly_id` names.
    family: Mapped[AnomalyFamily] = mapped_column(String(32), nullable=False)
    anomaly_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: The principal subject that took the anomaly. NULL means unowned.
    owner: Mapped[str | None] = mapped_column(String(SUBJECT_MAX_LENGTH))
    status: Mapped[TriageStatus] = mapped_column(
        String(16), default=TriageStatus.NEW, nullable=False
    )
    #: NULL until the analyst records a decision; required while status is CLOSED.
    disposition: Mapped[TriageDisposition | None] = mapped_column(String(32))
    disposition_note: Mapped[str | None] = mapped_column(
        String(DISPOSITION_NOTE_MAX_LENGTH)
    )
    #: The principal subject that made the last state change. A comment does not
    #: change the state and therefore does not touch this pair.
    updated_by: Mapped[str] = mapped_column(String(SUBJECT_MAX_LENGTH), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        onupdate=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    #: Optimistic concurrency. Every state-changing PATCH increments it by one; a
    #: comment does not, because a comment changes no state and a client that posted
    #: one must not have its next PATCH refused as stale.
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    events: Mapped[list[AnomalyTriageEvent]] = relationship(
        back_populates="triage",
        cascade="all, delete-orphan",
        order_by="AnomalyTriageEvent.created_at",
    )


class AnomalyTriageEvent(UUIDPrimaryKeyMixin, Base):
    """One append-only row of the triage activity log."""

    __tablename__ = "anomaly_triage_event"
    __table_args__ = (
        CheckConstraint(f"kind IN ({_KIND_VALUES})", name="kind_known"),
        CheckConstraint(
            f"comment IS NULL OR char_length(comment) <= {COMMENT_MAX_LENGTH}",
            name="comment_length",
        ),
        CheckConstraint(
            "kind <> 'COMMENT' OR (comment IS NOT NULL AND char_length(comment) > 0)",
            name="comment_present",
        ),
        CheckConstraint("triage_version >= 1", name="version_positive"),
        Index("ix_anomaly_triage_event_triage", "triage_id", "created_at"),
    )

    triage_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("anomaly_triage.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[TriageEventKind] = mapped_column(String(16), nullable=False)
    #: The principal subject that performed the change.
    actor: Mapped[str] = mapped_column(String(SUBJECT_MAX_LENGTH), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        server_default=func.now(),
        nullable=False,
    )
    #: The value before and after for STATUS, OWNER and DISPOSITION events.
    from_value: Mapped[str | None] = mapped_column(String(SUBJECT_MAX_LENGTH))
    to_value: Mapped[str | None] = mapped_column(String(SUBJECT_MAX_LENGTH))
    #: A COMMENT's text, or the new note attached to a DISPOSITION event. NULL on a
    #: NOTE event that clears the note.
    comment: Mapped[str | None] = mapped_column(String(COMMENT_MAX_LENGTH))
    #: The triage version this event belongs to. Several field events of one PATCH
    #: share a version; a comment stays at the current version.
    triage_version: Mapped[int] = mapped_column(Integer, nullable=False)

    triage: Mapped[AnomalyTriage] = relationship(back_populates="events")
