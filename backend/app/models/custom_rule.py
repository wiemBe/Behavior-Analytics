"""Phase F — operator-authored threshold rules over collected aggregates.

A custom rule is a tripwire on data the platform already stores every bucket:
a subject (log source, fleet class, WAF endpoint, IPS entity or IPS source), a
metric from the catalog in `app.custom_rules.catalog`, an operator, a
threshold, a sustain and a recovery. Evaluation reads stored aggregates only —
never a new Ariel search — and persists nothing raw: breach rows carry counts,
shares and bounded labels (AGENTS.md rules 3 and 9).

Three tables (design §F.3.1, §F.4.1, §F.4.3, §F.10):

  * `custom_rule` — the rule and its hysteresis state;
  * `custom_rule_breach` — one lean observation trail per condition trip;
  * `custom_rule_watermark` — how far each subject kind has been judged.
"""

from __future__ import annotations

import uuid
from datetime import datetime

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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin, utcnow
from app.models.enums import AnomalyState, CustomRuleSubjectKind, Severity, WafMeasurement

#: The subject column pairing, in the `_IPS_SUBJECT_CHECK` pattern: exactly the
#: columns the subject kind names are set, every other subject column is NULL.
#: The metric is NOT checked here: the legal metric set differs per kind and is
#: validated against the in-code catalog (design §F.3.1, §F.3.2).
CUSTOM_RULE_SUBJECT_CHECK = (
    "(subject_kind = 'LOG_SOURCE' AND log_source_id IS NOT NULL "
    "AND source_class IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL) OR "
    "(subject_kind = 'FLEET_CLASS' AND source_class IS NOT NULL "
    "AND log_source_id IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL) OR "
    "(subject_kind = 'WAF_ENDPOINT' AND waf_endpoint_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND ips_entity_id IS NULL) OR "
    "(subject_kind = 'IPS_ENTITY' AND ips_entity_id IS NOT NULL "
    "AND log_source_id IS NULL AND source_class IS NULL AND waf_endpoint_id IS NULL) OR "
    "(subject_kind = 'IPS_SOURCE' AND log_source_id IS NOT NULL "
    "AND source_class IS NULL AND waf_endpoint_id IS NULL AND ips_entity_id IS NULL)"
)

_OPERATOR_CHECK = "('GT', 'GE', 'LT', 'LE', 'EQ')"
_SEVERITY_CHECK = "('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')"
_BREACH_STATE_CHECK = "('OPEN', 'RECOVERING', 'RESOLVED')"
_MEASUREMENT_CHECK = "('EXACT', 'LOWER_BOUND')"


class CustomRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One threshold rule and its hysteresis state (design §F.3.1).

    The lifecycle counters live on the rule row — the Phase A pattern
    (`log_source_detector_state`): `consecutive_breaching` opens after
    `sustain_buckets`, `consecutive_under` resolves after `recovery_buckets`,
    and `last_evaluated_bucket_start` makes re-processing a bucket a no-op.
    """

    __tablename__ = "custom_rule"
    __table_args__ = (
        CheckConstraint(CUSTOM_RULE_SUBJECT_CHECK, name="subject_matches_kind"),
        CheckConstraint(f"operator IN {_OPERATOR_CHECK}", name="operator_known"),
        CheckConstraint(f"severity IN {_SEVERITY_CHECK}", name="severity_known"),
        CheckConstraint("threshold_value >= 0", name="threshold_non_negative"),
        CheckConstraint("sustain_buckets >= 1", name="sustain_buckets_positive"),
        CheckConstraint("recovery_buckets >= 1", name="recovery_buckets_positive"),
        CheckConstraint("length(name) > 0", name="name_not_empty"),
        UniqueConstraint("name", name="uq_custom_rule_name"),
        Index("ix_custom_rule_subject", "subject_kind", "enabled"),
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    subject_kind: Mapped[CustomRuleSubjectKind] = mapped_column(String(16), nullable=False)
    #: Set iff LOG_SOURCE or IPS_SOURCE (the CHECK above).
    log_source_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE")
    )
    #: Set iff FLEET_CLASS; a `SourceClass` value.
    source_class: Mapped[str | None] = mapped_column(String(32))
    #: Set iff WAF_ENDPOINT.
    waf_endpoint_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("waf_endpoint.id", ondelete="CASCADE")
    )
    #: Set iff IPS_ENTITY.
    ips_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("ips_entity.id", ondelete="CASCADE")
    )

    #: One of `CustomRuleMetric`; validated against the catalog in code.
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    #: The scheduled-search operator set (`app/schemas/search.py`).
    operator: Mapped[str] = mapped_column(String(4), nullable=False)
    threshold_value: Mapped[float] = mapped_column(Float, nullable=False)
    sustain_buckets: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    recovery_buckets: Mapped[int] = mapped_column(Integer, default=2, nullable=False)
    severity: Mapped[Severity] = mapped_column(
        String(16), default=Severity.MEDIUM, nullable=False
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    # --- hysteresis and evaluation state -------------------------------------
    consecutive_breaching: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    consecutive_under: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_evaluated_bucket_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: The rule's one live breach, if any. A pointer, not an FK — the Phase A
    #: `active_anomaly_id` pattern: the breach row owns the cascade.
    active_breach_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    #: When a bucket was last judged. A tick that judges nothing leaves it
    #: where it is, so a stopped collection cannot look freshly evaluated.
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(16))
    last_status_reason: Mapped[str | None] = mapped_column(Text)

    created_by: Mapped[str | None] = mapped_column(String(255))
    updated_by: Mapped[str | None] = mapped_column(String(255))


class CustomRuleBreach(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One lean breach record: the observation trail of a rule trip (§F.4.3).

    An F.1 breach reuses the alert lifecycle only; it does not join the incident
    lifecycle and never appears in the unified anomaly list. The row persists
    counts, shares, bounded labels and bucket bounds — never a raw event value.
    """

    __tablename__ = "custom_rule_breach"
    __table_args__ = (
        CheckConstraint(f"state IN {_BREACH_STATE_CHECK}", name="state_known"),
        CheckConstraint(
            f"worst_measurement IN {_MEASUREMENT_CHECK}",
            name="worst_measurement_known",
        ),
        CheckConstraint(
            f"last_measurement IN {_MEASUREMENT_CHECK}", name="last_measurement_known"
        ),
        CheckConstraint("unmeasured_buckets >= 0", name="unmeasured_buckets_non_negative"),
        Index("ix_custom_rule_breach_rule", "rule_id", "state"),
        # One live breach per rule, structurally — the alert dedup shape.
        Index(
            "uq_custom_rule_breach_active",
            "rule_id",
            unique=True,
            postgresql_where=text("state <> 'RESOLVED'"),
        ),
    )

    rule_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("custom_rule.id", ondelete="CASCADE"),
        nullable=False,
    )
    #: OPEN | RECOVERING | RESOLVED — `AnomalyState` values reused, no enum change.
    state: Mapped[AnomalyState] = mapped_column(String(16), nullable=False)

    first_bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_bucket_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recovering_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Worst reading in the direction of the breach, and what it proves.
    worst_value: Mapped[float | None] = mapped_column(Float)
    worst_measurement: Mapped[WafMeasurement | None] = mapped_column(String(16))
    last_value: Mapped[float | None] = mapped_column(Float)
    last_measurement: Mapped[WafMeasurement | None] = mapped_column(String(16))
    #: Buckets during the breach that produced no verdict either way.
    unmeasured_buckets: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    #: Display snapshot (source name, endpoint display name, entity dimension
    #: and value) so the trail stays readable after the subject is retired or
    #: deleted (§F.3.3).
    subject_label: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class CustomRuleWatermark(Base):
    """The last bucket start every rule of one subject kind was judged on.

    The `anomaly_evaluation_watermark` pattern (design §F.4.1). The first run
    seeds the watermark at the latest complete bucket and judges nothing, so an
    upgrade never replays history into breaches. One row per subject kind.
    """

    __tablename__ = "custom_rule_watermark"

    subject_kind: Mapped[CustomRuleSubjectKind] = mapped_column(
        String(16), primary_key=True
    )
    evaluated_through: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


__all__ = [
    "CUSTOM_RULE_SUBJECT_CHECK",
    "CustomRule",
    "CustomRuleBreach",
    "CustomRuleWatermark",
]
