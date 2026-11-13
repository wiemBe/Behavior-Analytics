"""Analyst-requested exclusion of a UTC interval from one baseline's training (SOC-13).

A row says "do not learn from `[starts_at, ends_at)` for this subject and scope". It is
reversible and never deleted: revocation fills the three revocation columns together.
The subject is the baseline's own subject (a log source for Phase A), not the
alert-suppression subject. `origin_anomaly_id` has no foreign key because it can name a
row of any of five anomaly tables. Strings plus CHECKs, no PostgreSQL enum.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Index, String, func, text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import AnomalyFamily

#: The baseline subjects a family trains on (design section 1).
SUBJECT_KINDS = ("log_source", "endpoint", "entity", "source", "value", "rule")
#: The longest interval one row may cover; seven days limits a careless wide removal.
MAX_INTERVAL_DAYS = 7
REASON_MAX_LENGTH = 1000

_FAMILY_VALUES = ", ".join(f"'{value}'" for value in AnomalyFamily)
_SUBJECT_VALUES = ", ".join(f"'{value}'" for value in SUBJECT_KINDS)


class BaselineTrainingExclusion(UUIDPrimaryKeyMixin, Base):
    __tablename__ = "baseline_training_exclusion"
    __table_args__ = (
        CheckConstraint(f"origin_family IN ({_FAMILY_VALUES})", name="family_known"),
        CheckConstraint(f"subject_kind IN ({_SUBJECT_VALUES})", name="subject_kind_known"),
        CheckConstraint(
            f"char_length(btrim(reason)) BETWEEN 1 AND {REASON_MAX_LENGTH}",
            name="reason_length",
        ),
        CheckConstraint("starts_at < ends_at", name="interval_ordered"),
        CheckConstraint(
            f"ends_at - starts_at <= interval '{MAX_INTERVAL_DAYS} days'",
            name="interval_max",
        ),
        CheckConstraint(
            "(revoked_by IS NULL AND revoked_at IS NULL AND revocation_reason IS NULL) "
            "OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL "
            "AND revocation_reason IS NOT NULL)",
            name="revocation_paired",
        ),
        CheckConstraint(
            "revocation_reason IS NULL OR "
            f"char_length(btrim(revocation_reason)) BETWEEN 1 AND {REASON_MAX_LENGTH}",
            name="revocation_reason_length",
        ),
        Index(
            "ix_baseline_training_exclusion_subject",
            "subject_kind",
            "subject_id",
            "scope_key",
            "starts_at",
            "ends_at",
        ),
        Index(
            "uq_baseline_training_exclusion_active",
            "subject_kind",
            "subject_id",
            "scope_key",
            "starts_at",
            "ends_at",
            unique=True,
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )

    origin_family: Mapped[str] = mapped_column(String(32), nullable=False)
    origin_anomaly_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    subject_kind: Mapped[str] = mapped_column(String(24), nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    scope_key: Mapped[str] = mapped_column(String(80), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reason: Mapped[str] = mapped_column(String(REASON_MAX_LENGTH), nullable=False)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, server_default=func.now(), nullable=False
    )
    revoked_by: Mapped[str | None] = mapped_column(String(255))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revocation_reason: Mapped[str | None] = mapped_column(String(REASON_MAX_LENGTH))
