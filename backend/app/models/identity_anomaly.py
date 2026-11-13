"""Identity incidents, their transitions, and per-series detector state.

Shaped like the IPS anomaly tables: one incident per (log source, catalog event
ID, detector), a string lifecycle, and an evidence snapshot that may hold only
the stored top-N usernames. These are ordinary tables. The identity series are
hypertables and cannot be foreign-key targets, so nothing here points at a
bucket. `evidence_snapshot` on the alert is the copy that outlives the row.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.enums import (
    IDENTITY_EVENT_CATALOG,
    AnomalyState,
    EvidenceStatus,
    IdentityAnomalyType,
    IdentityMeasurement,
    Severity,
)
from app.models.message_ref import stored_code, stored_params

_DETECTORS = ", ".join(f"'{member.value}'" for member in IdentityAnomalyType)
_STATES = ", ".join(f"'{member.value}'" for member in AnomalyState)
_SEVERITIES = ", ".join(f"'{member.value}'" for member in Severity)
_MEASUREMENTS = ", ".join(f"'{member.value}'" for member in IdentityMeasurement)
_EVIDENCE = ", ".join(f"'{member.value}'" for member in EvidenceStatus)
_EVENT_IDS = ", ".join(str(event_id) for event_id, _kind in IDENTITY_EVENT_CATALOG)


class IdentityDetectorState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hysteresis for one (log source, catalog event ID, detector)."""

    __tablename__ = "identity_detector_state"
    __table_args__ = (
        UniqueConstraint(
            "log_source_id",
            "event_id",
            "detector_type",
            name="uq_identity_detector_state_series",
        ),
        CheckConstraint(f"event_id IN ({_EVENT_IDS})", name="event_id_known"),
        CheckConstraint(f"detector_type IN ({_DETECTORS})", name="detector_known"),
        CheckConstraint(f"state IN ({_STATES})", name="state_known"),
        CheckConstraint("consecutive_anomalous >= 0", name="anomalous_non_negative"),
        CheckConstraint("consecutive_healthy >= 0", name="healthy_non_negative"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    detector_type: Mapped[str] = mapped_column(String(40), nullable=False)
    consecutive_anomalous: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_healthy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[str] = mapped_column(String(24), default=AnomalyState.NORMAL, nullable=False)
    last_interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: Plain UUID. The series tables are hypertables, and this id names an
    #: incident, not a bucket. No foreign key, matching IPS detector state.
    active_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    open_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class IdentityAnomaly(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One identity incident. Usernames appear only inside `details`, bounded."""

    __tablename__ = "identity_anomaly"
    __table_args__ = (
        CheckConstraint(f"event_id IN ({_EVENT_IDS})", name="event_id_known"),
        CheckConstraint(f"detector_type IN ({_DETECTORS})", name="detector_known"),
        CheckConstraint(f"state IN ({_STATES})", name="state_known"),
        CheckConstraint(f"severity IN ({_SEVERITIES})", name="severity_known"),
        CheckConstraint(
            f"observed_measurement IN ({_MEASUREMENTS})", name="measurement_known"
        ),
        CheckConstraint(f"evidence_status IN ({_EVIDENCE})", name="evidence_status_known"),
        CheckConstraint("catalog_version >= 1", name="catalog_version_positive"),
        CheckConstraint("profile_version >= 1", name="profile_version_positive"),
        CheckConstraint("policy_version >= 1", name="policy_version_positive"),
        CheckConstraint("consecutive_buckets >= 0", name="buckets_non_negative"),
        Index(
            "ix_identity_anomaly_state",
            "log_source_id",
            "event_id",
            "detector_type",
            "state",
        ),
        Index("ix_identity_anomaly_detected", "detected_at"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("log_source.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_id: Mapped[int] = mapped_column(Integer, nullable=False)
    detector_type: Mapped[str] = mapped_column(String(40), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    anomaly_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    deviation_score: Mapped[float | None] = mapped_column(Float)
    deviation_ratio: Mapped[float | None] = mapped_column(Float)
    robust_z: Mapped[float | None] = mapped_column(Float)
    absolute_delta: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    consecutive_buckets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    catalog_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: English, uncoded. `explanation_i18n` stays null; no catalog entry is added.
    explanation: Mapped[str | None] = mapped_column(Text)
    explanation_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    #: Bounded username list only: rank, username, count. Never a raw event.
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_status: Mapped[str] = mapped_column(String(16), nullable=False)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(255))
    suppression_reason_i18n: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )
    observed_measurement: Mapped[str] = mapped_column(String(16), nullable=False)

    @property
    def explanation_code(self) -> str | None:
        return stored_code(self.explanation_i18n)

    @property
    def explanation_params(self) -> dict[str, Any] | None:
        return stored_params(self.explanation_i18n)


class IdentityAnomalyTransition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One lifecycle move. Usernames stay on the incident, not on the transition."""

    __tablename__ = "identity_anomaly_transition"
    __table_args__ = (
        CheckConstraint(f"to_state IN ({_STATES})", name="to_state_known"),
        CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_STATES})", name="from_state_known"
        ),
        CheckConstraint(
            f"observed_measurement IN ({_MEASUREMENTS})", name="measurement_known"
        ),
        Index("ix_identity_anomaly_transition", "identity_anomaly_id", "occurred_at"),
    )

    identity_anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("identity_anomaly.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_state: Mapped[str | None] = mapped_column(String(24))
    to_state: Mapped[str] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    reason_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    observed_measurement: Mapped[str] = mapped_column(String(16), nullable=False)

    @property
    def reason_code(self) -> str | None:
        return stored_code(self.reason_i18n)
