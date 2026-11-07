"""SOC-17d time-boxed alert suppression and its per-incident hit record.

``AlertSuppression`` is one human decision: "be quiet about this condition for a
while -- unless it gets extreme". The condition is ``(family, subject_id,
detector_type)``, where the subject is the anomaly producer's **log source** for
Phase A, WAF and both IPS kinds (``IpsEntity.log_source_id`` maps an entity
anomaly back) and the watch id for property watch (design §5.2). ``detector_type``
NULL means every detector of the subject.

The row is never deleted. An early end writes ``ended_at``/``ended_by``; a
natural expiry is closed by the create/extend service (``ended_at = ends_at``)
because the partial unique index below cannot test time (a partial-index
predicate must be immutable). The index holds across NULLs, so at most one open
row exists per condition. There is deliberately no FK to the four subject
tables (the ``anomaly_triage`` precedent, ``app/models/triage.py``): the family
names the table, and a suppression must outlive a deleted endpoint or entity.

``AlertSuppressionHit`` is one row per suppressed **incident**, not per bucket:
the engines refresh an incident in place, so the unique ``(suppression_id,
anomaly_id)`` key makes "12 incidents recorded while suppressed" a cheap, exact
count (design §5.2, §5.5). ``anomaly_id`` has no FK for the same reason as the
subject, and the row goes with its suppression (cascade).

Adds no SQL enum: ``kind`` reuses the Python ``SuppressionKind`` and the family
column reuses ``AnomalyFamily``, both validated by a CHECK (AGENTS.md rule 6).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import AnomalyFamily, SuppressionKind

_FAMILIES = ", ".join(f"'{value}'" for value in AnomalyFamily)
_KINDS = ", ".join(f"'{value}'" for value in SuppressionKind)


class AlertSuppression(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alert_suppression"
    __table_args__ = (
        CheckConstraint(f"family IN ({_FAMILIES})", name="family_known"),
        CheckConstraint(f"kind IN ({_KINDS})", name="kind_known"),
        CheckConstraint(
            "detector_type IS NULL OR length(detector_type) > 0",
            name="detector_type_not_empty",
        ),
        CheckConstraint("length(reason) > 0", name="reason_not_empty"),
        # A KNOWN_CHANGE window names its ticket; an ANALYST note never does.
        CheckConstraint(
            "kind <> 'KNOWN_CHANGE' OR (reference IS NOT NULL AND length(reference) > 0)",
            name="known_change_has_reference",
        ),
        # The window is a real span, and never longer than the 30-day cap that
        # keeps suppression a timer rather than a mute button (design §5.1).
        CheckConstraint("ends_at > starts_at", name="ends_after_start"),
        CheckConstraint(
            "ends_at <= starts_at + interval '30 days'", name="ends_within_30_days"
        ),
        # At most one open (not ended) row per condition, across a NULL
        # detector: an expired-but-unclosed row blocks a new one only until the
        # service closes it, which is why that close runs in the same
        # transaction as the create (design §5.2).
        Index(
            "uq_alert_suppression_open_condition",
            "family",
            "subject_id",
            "detector_type",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
            postgresql_nulls_not_distinct=True,
        ),
    )

    family: Mapped[AnomalyFamily] = mapped_column(String(32), nullable=False)
    #: The producer's log source, or the property-watch id (design §5.2).
    subject_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    #: NULL = every detector of the subject; otherwise the family's own enum value.
    detector_type: Mapped[str | None] = mapped_column(String(40))
    kind: Mapped[SuppressionKind] = mapped_column(String(16), nullable=False)
    #: The analyst's note. Stored here only -- never in the audit detail and
    #: never logged (the triage comment rule, `app/models/triage.py`).
    reason: Mapped[str] = mapped_column(String(1000), nullable=False)
    owner: Mapped[str] = mapped_column(String(255), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: An early end, or the service's close of a naturally expired row. The row
    #: itself is never deleted.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_by: Mapped[str | None] = mapped_column(String(255))
    #: The change/ticket id a KNOWN_CHANGE window names; NULL for ANALYST.
    reference: Mapped[str | None] = mapped_column(String(255))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)


class AlertSuppressionHit(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "alert_suppression_hit"
    __table_args__ = (
        CheckConstraint(f"family IN ({_FAMILIES})", name="family_known"),
        CheckConstraint("length(detector_type) > 0", name="detector_type_not_empty"),
        # One row per incident, not per bucket: a refresh of the same incident
        # must not add a second row (design §5.2).
        UniqueConstraint("suppression_id", "anomaly_id", name="uq_alert_suppression_hit"),
    )

    suppression_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("alert_suppression.id", ondelete="CASCADE"),
        nullable=False,
    )
    family: Mapped[AnomalyFamily] = mapped_column(String(32), nullable=False)
    #: The incident the gate stopped an alert over; the family names its table.
    anomaly_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    detector_type: Mapped[str] = mapped_column(String(40), nullable=False)
    #: The bucket whose alert was stopped, and when the hit was recorded.
    bucket_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # An insert-only row: no `updated_at`, because nothing about a hit changes.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
