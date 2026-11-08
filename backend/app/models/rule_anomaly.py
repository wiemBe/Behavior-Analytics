"""Rule silence and storm incidents (SOC-6 design §5.1).

Three tables, separate from `pw_anomaly` and from Phase A's `log_source_anomaly`:

  * `rule_anomaly` -- one incident on one analytics rule or building block;
  * `rule_anomaly_transition` -- its auditable lifecycle transitions;
  * `rule_detector_state` -- the hysteresis counters of one (rule, detector).

The subject is `analytics_rule.id`. A rule id in `pw_value_id` would be a false
subject, and a rule detector in `AnomalyType` would be a value no Phase A detector
can produce (AGENTS.md rule 6). `qradar_id` and `is_building_block` are
denormalised so a list row says what the subject is without a join; both are
overwritten by the inventory sync on `analytics_rule`, and the incident keeps the
values it saw. The incident goes with its rule (`ON DELETE CASCADE`).

Only aggregate measurements are persisted: a match count, a median and a deviation
never a raw event (AGENTS.md rule 3). There is no baseline table in this slice; the
seasonal cell is computed from `rule_match_metric` by the engine.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
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
    AnomalyState,
    EvidenceStatus,
    RuleAnomalyType,
    Severity,
    WafMeasurement,
)
from app.models.message_ref import stored_code, stored_params


class RuleDetectorState(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Hysteresis state for one (rule, detector). Counters only, no rule text."""

    __tablename__ = "rule_detector_state"
    __table_args__ = (
        UniqueConstraint(
            "rule_id", "detector_type", name="uq_rule_detector_state_rule"
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("analytics_rule.id", ondelete="CASCADE"),
        nullable=False,
    )
    detector_type: Mapped[RuleAnomalyType] = mapped_column(String(40), nullable=False)
    consecutive_anomalous: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_healthy: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.NORMAL, nullable=False
    )
    last_interval_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    active_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    open_anomaly_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))


class RuleAnomaly(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A detected silence or storm on one analytics rule or building block.

    `rule_id` is the subject. `qradar_id` and `is_building_block` repeat what the
    inventory said when the incident was opened, so a row explains itself after a
    re-sync or after the rule is deleted. `suppressed` is written false by the
    engine; analyst suppression is a separate table (SOC-17d) and does not use it.
    """

    __tablename__ = "rule_anomaly"
    __table_args__ = (
        Index("ix_rule_anomaly_state", "rule_id", "detector_type", "state"),
        Index("ix_rule_anomaly_detected", "detected_at"),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("analytics_rule.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: The QRadar id the subject had when this incident was opened.
    qradar_id: Mapped[int] = mapped_column(Integer, nullable=False)
    is_building_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    detector_type: Mapped[RuleAnomalyType] = mapped_column(String(40), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[Severity] = mapped_column(String(16), nullable=False)
    state: Mapped[AnomalyState] = mapped_column(
        String(24), default=AnomalyState.CANDIDATE, nullable=False
    )
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
    policy_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    explanation: Mapped[str | None] = mapped_column(Text)
    #: `{code, params}` of `explanation` (NULL on earlier rows and for an uncoded sentence).
    explanation_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    evidence_status: Mapped[EvidenceStatus] = mapped_column(
        String(16), default=EvidenceStatus.NOT_REQUESTED, nullable=False
    )
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    suppression_reason: Mapped[str | None] = mapped_column(String(255))
    #: `{code, params}` of `suppression_reason`. The rule engine writes no suppression
    #: reason today; analyst suppression lives in `alert_suppression`.
    suppression_reason_i18n: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB(none_as_null=True)
    )

    @property
    def explanation_code(self) -> str | None:
        return stored_code(self.explanation_i18n)

    @property
    def explanation_params(self) -> dict[str, Any] | None:
        return stored_params(self.explanation_i18n)

    @property
    def suppression_reason_code(self) -> str | None:
        return stored_code(self.suppression_reason_i18n)

    @property
    def suppression_reason_params(self) -> dict[str, Any] | None:
        return stored_params(self.suppression_reason_i18n)


class RuleAnomalyTransition(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Auditable lifecycle transition for one rule anomaly.

    Repeats only bounded aggregate measurements needed to explain the state change.
    """

    __tablename__ = "rule_anomaly_transition"
    __table_args__ = (
        Index("ix_rule_anomaly_transition", "rule_anomaly_id", "occurred_at"),
    )

    rule_anomaly_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("rule_anomaly.id", ondelete="CASCADE"),
        nullable=False,
    )
    from_state: Mapped[AnomalyState | None] = mapped_column(String(24))
    to_state: Mapped[AnomalyState] = mapped_column(String(24), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    bucket_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reason: Mapped[str | None] = mapped_column(Text)
    #: `{code, params}` of `reason` when the engine had a message code (the lifecycle's
    #: own reasons are uncoded, so NULL).
    reason_i18n: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    actor: Mapped[str] = mapped_column(String(64), default="rule-engine", nullable=False)
    observed_value: Mapped[float | None] = mapped_column(Float)
    expected_value: Mapped[float | None] = mapped_column(Float)
    observed_measurement: Mapped[WafMeasurement] = mapped_column(
        String(16), default=WafMeasurement.EXACT, nullable=False
    )

    @property
    def reason_code(self) -> str | None:
        return stored_code(self.reason_i18n)

    @property
    def reason_params(self) -> dict[str, Any] | None:
        return stored_params(self.reason_i18n)
