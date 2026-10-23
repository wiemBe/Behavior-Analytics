"""Phase C.2 outage alert policy.

One row says, for a class or a single log source, whether a ``NO_EVENTS``
anomaly opens an alert (``ALERT``) or is only recorded (``RECORD_ONLY``), and
optionally overrides the silence grace for that scope.

The table is additive (AGENTS.md rule 7): it touches no Phase A table or enum.
``instance_id`` NULL means the row is global; a row with an instance applies
only to that instance. The uniqueness holds across NULLs, so there is at most
one global row and at most one row per instance for a given scope.
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import OutagePolicyMode, OutagePolicyScope

#: Bounds the silence-grace override. 0 fires on the first empty bucket; 48 is
#: four hours of 5-minute buckets, beyond which a "grace" is really a policy of
#: never alerting, which RECORD_ONLY already expresses.
MAX_SILENCE_GRACE_BUCKETS = 48


class OutageAlertPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "outage_alert_policy"
    __table_args__ = (
        CheckConstraint(
            "scope_kind IN ('CLASS', 'SOURCE')", name="scope_kind_known"
        ),
        CheckConstraint("mode IN ('ALERT', 'RECORD_ONLY')", name="mode_known"),
        CheckConstraint(
            "silence_grace_buckets IS NULL OR "
            "(silence_grace_buckets >= 0 AND silence_grace_buckets <= "
            f"{MAX_SILENCE_GRACE_BUCKETS})",
            name="silence_grace_buckets_range",
        ),
        UniqueConstraint(
            "instance_id",
            "scope_kind",
            "scope_value",
            name="uq_outage_alert_policy_instance_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )

    #: NULL is global; set, the row governs only that instance.
    instance_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("qradar_instance.id", ondelete="CASCADE")
    )
    scope_kind: Mapped[OutagePolicyScope] = mapped_column(String(16), nullable=False)
    #: A ``SourceClass`` value for a CLASS row, a ``log_source.id`` for a SOURCE row.
    scope_value: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[OutagePolicyMode] = mapped_column(String(16), nullable=False)
    #: NULL falls through to ``anomaly_silence_grace_buckets``.
    silence_grace_buckets: Mapped[int | None] = mapped_column(Integer)
    updated_by: Mapped[str | None] = mapped_column(String(255))
