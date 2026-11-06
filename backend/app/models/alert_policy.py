"""SOC-17c alert policy and daily digest bookkeeping.

``AlertPolicy`` answers one question for one source (or class) and one detector:
``ALERT``, ``RECORD_ONLY`` or ``DIGEST``. It changes nothing about detection or
recording; it changes only what leaves the platform. ``detector_type`` NULL means
"every detector of this family", so one row can govern a whole family. The table
is additive (AGENTS.md rule 7) and adds no SQL enum: ``family`` reuses
``AnomalyFamily`` and the detector value is validated by the API against the
family's existing Python enum (AGENTS.md rule 6).

The uniqueness holds across NULLs, so at most one global and one per-instance row
exist for a condition. ``instance_id`` NULL is global, exactly like
``outage_alert_policy``.

``AlertDigestRun`` is one row per (instance, UTC date). Its unique key is what
makes the daily digest idempotent: a run that already happened is not repeated,
whatever the beat does. ``error_code`` is a delivery code from
``email_delivery.ERROR_CODES`` and never the relay's reply text.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    AlertDigestState,
    AlertPolicyMode,
    AlertPolicyScope,
    AnomalyFamily,
)

_FAMILIES = ", ".join(f"'{value}'" for value in AnomalyFamily)
_SCOPES = ", ".join(f"'{value}'" for value in AlertPolicyScope)
_MODES = ", ".join(f"'{value}'" for value in AlertPolicyMode)
_DIGEST_STATES = ", ".join(f"'{value}'" for value in AlertDigestState)

#: Mirrors ``app.services.email_delivery.ERROR_CODES``. The model package is
#: imported while ``app.core.config`` is still initialising, so importing the
#: service here would be circular; a unit test pins the two tuples together.
DIGEST_ERROR_CODES: tuple[str, ...] = (
    "not_configured",
    "insecure_login",
    "connection_refused",
    "timeout",
    "connection_failed",
    "tls_failed",
    "auth_failed",
    "recipient_refused",
    "sender_refused",
    "server_error",
)
_ERROR_CODES = ", ".join(f"'{code}'" for code in DIGEST_ERROR_CODES)


class AlertPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "alert_policy"
    __table_args__ = (
        CheckConstraint(f"scope_kind IN ({_SCOPES})", name="scope_kind_known"),
        CheckConstraint("length(scope_value) > 0", name="scope_value_not_empty"),
        CheckConstraint(f"family IN ({_FAMILIES})", name="family_known"),
        CheckConstraint(
            "detector_type IS NULL OR length(detector_type) > 0",
            name="detector_type_not_empty",
        ),
        CheckConstraint(f"mode IN ({_MODES})", name="mode_known"),
        UniqueConstraint(
            "instance_id",
            "scope_kind",
            "scope_value",
            "family",
            "detector_type",
            name="uq_alert_policy_instance_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )

    #: NULL is global; set, the row governs only that instance.
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE")
    )
    scope_kind: Mapped[AlertPolicyScope] = mapped_column(String(16), nullable=False)
    #: A ``SourceClass`` value for a CLASS row; the source id for a SOURCE row.
    scope_value: Mapped[str] = mapped_column(String(128), nullable=False)
    family: Mapped[AnomalyFamily] = mapped_column(String(32), nullable=False)
    #: NULL = every detector of ``family``; otherwise that family's enum value.
    detector_type: Mapped[str | None] = mapped_column(String(40))
    mode: Mapped[AlertPolicyMode] = mapped_column(String(16), nullable=False)
    updated_by: Mapped[str | None] = mapped_column(String(255))


class AlertDigestRun(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "alert_digest_run"
    __table_args__ = (
        CheckConstraint(f"state IN ({_DIGEST_STATES})", name="state_known"),
        CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_ERROR_CODES})",
            name="error_code_known",
        ),
        UniqueConstraint(
            "instance_id",
            "digest_date",
            name="uq_alert_digest_run_instance_date",
        ),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: UTC calendar date the run belongs to; part of the idempotency key.
    digest_date: Mapped[date] = mapped_column(Date, nullable=False)
    state: Mapped[AlertDigestState] = mapped_column(String(16), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False)
    recipient_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: One of ``email_delivery.ERROR_CODES``; NULL unless the run failed or was
    #: not attempted because e-mail or recipients were missing.
    error_code: Mapped[str | None] = mapped_column(String(32))
    #: When delivery was attempted, UTC. NULL when nothing was sent.
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
