"""SOC-10c: identity incidents, detector state, and the identity alert family.

ADDITIVE. Creates `identity_anomaly`, `identity_detector_state` and
`identity_anomaly_transition`. No SQL enum (AGENTS.md rule 6): detector type,
state and measurement are strings plus CHECKs. The series tables stay
hypertables and are not foreign-key targets.

`AnomalyFamily.IDENTITY` widens the triage, alert-policy, both suppression and
the baseline-training-exclusion family CHECKs. `EMAIL_ALERT_TYPES` gains
`identity_source` before `digest`, so the e-mail-template CHECK is widened too.

The downgrade refuses while any `identity` row exists in those shared tables,
or any `identity_source` template exists. The old CHECKs cannot hold them, and
silently deleting an analyst's record or an administrator's template would
destroy work (the 0060 pattern). Once those rows are gone it drops the three
tables and restores the previous lists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0070"
down_revision: str | None = "0069"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_FAMILIES = "'log_source', 'waf', 'ips', 'property_watch', 'rule'"
_NEW_FAMILIES = _OLD_FAMILIES + ", 'identity'"

_TEMPLATE_CHECK = "ck_email_template_alert_type_known"
_OLD_ALERT_TYPES = (
    "'log_source', 'waf_endpoint', 'ips_entity', 'custom_rule', 'pw_value', "
    "'catchall_sender', 'qradar_instance', 'scheduled_search', 'analytics_rule', "
    "'digest'"
)
_NEW_ALERT_TYPES = (
    "'log_source', 'waf_endpoint', 'ips_entity', 'custom_rule', 'pw_value', "
    "'catchall_sender', 'qradar_instance', 'scheduled_search', 'analytics_rule', "
    "'identity_source', 'digest'"
)

_FAMILY_CHECKS = (
    ("anomaly_triage", "ck_anomaly_triage_family_known", "family"),
    ("alert_policy", "ck_alert_policy_family_known", "family"),
    ("alert_suppression", "ck_alert_suppression_family_known", "family"),
    ("alert_suppression_hit", "ck_alert_suppression_hit_family_known", "family"),
    (
        "baseline_training_exclusion",
        "ck_baseline_training_exclusion_family_known",
        "origin_family",
    ),
)

_DETECTORS = (
    "'IDENTITY_AUDIT_LOG_CLEARED', 'IDENTITY_PRIVILEGED_GROUP_ADD', "
    "'IDENTITY_NEW_PRIVILEGED_ACCOUNT', 'IDENTITY_FAILED_LOGON_SPIKE', "
    "'IDENTITY_LOCKOUT_SPIKE', 'IDENTITY_ACCOUNT_CREATED', "
    "'IDENTITY_ACCOUNT_DELETED'"
)
_STATES = (
    "'INSUFFICIENT_DATA', 'NORMAL', 'CANDIDATE', 'OPEN', 'RECOVERING', "
    "'RESOLVED', 'SUPPRESSED'"
)
_SEVERITIES = "'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'"
_MEASUREMENTS = "'EXACT', 'TRUNCATED', 'NOT_COLLECTED'"
_EVIDENCE = "'NOT_REQUESTED', 'PENDING', 'COMPLETE', 'PARTIAL', 'UNAVAILABLE', 'FAILED'"
_EVENT_IDS = "4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102"


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def _set_family_checks(families: str) -> None:
    for table, name, column in _FAMILY_CHECKS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(op.f(name), table, f"{column} IN ({families})")


def _set_template_check(alert_types: str) -> None:
    op.drop_constraint(op.f(_TEMPLATE_CHECK), "email_template", type_="check")
    op.create_check_constraint(
        op.f(_TEMPLATE_CHECK),
        "email_template",
        f"alert_type IS NULL OR alert_type IN ({alert_types})",
    )


def upgrade() -> None:
    op.create_table(
        "identity_anomaly",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("detector_type", sa.String(length=40), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anomaly_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anomaly_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("deviation_score", sa.Float(), nullable=True),
        sa.Column("deviation_ratio", sa.Float(), nullable=True),
        sa.Column("robust_z", sa.Float(), nullable=True),
        sa.Column("absolute_delta", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("consecutive_buckets", sa.Integer(), nullable=False),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "explanation_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_status", sa.String(length=16), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "suppression_reason_i18n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("observed_measurement", sa.String(length=16), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"event_id IN ({_EVENT_IDS})",
            name=op.f("ck_identity_anomaly_event_id_known"),
        ),
        sa.CheckConstraint(
            f"detector_type IN ({_DETECTORS})",
            name=op.f("ck_identity_anomaly_detector_known"),
        ),
        sa.CheckConstraint(
            f"state IN ({_STATES})", name=op.f("ck_identity_anomaly_state_known")
        ),
        sa.CheckConstraint(
            f"severity IN ({_SEVERITIES})",
            name=op.f("ck_identity_anomaly_severity_known"),
        ),
        sa.CheckConstraint(
            f"observed_measurement IN ({_MEASUREMENTS})",
            name=op.f("ck_identity_anomaly_measurement_known"),
        ),
        sa.CheckConstraint(
            f"evidence_status IN ({_EVIDENCE})",
            name=op.f("ck_identity_anomaly_evidence_status_known"),
        ),
        sa.CheckConstraint(
            "catalog_version >= 1",
            name=op.f("ck_identity_anomaly_catalog_version_positive"),
        ),
        sa.CheckConstraint(
            "profile_version >= 1",
            name=op.f("ck_identity_anomaly_profile_version_positive"),
        ),
        sa.CheckConstraint(
            "policy_version >= 1",
            name=op.f("ck_identity_anomaly_policy_version_positive"),
        ),
        sa.CheckConstraint(
            "consecutive_buckets >= 0",
            name=op.f("ck_identity_anomaly_buckets_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_identity_anomaly_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_anomaly")),
    )
    op.create_index(
        "ix_identity_anomaly_state",
        "identity_anomaly",
        ["log_source_id", "event_id", "detector_type", "state"],
        unique=False,
    )
    op.create_index(
        "ix_identity_anomaly_detected",
        "identity_anomaly",
        ["detected_at"],
        unique=False,
    )
    op.create_table(
        "identity_detector_state",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("detector_type", sa.String(length=40), nullable=False),
        sa.Column("consecutive_anomalous", sa.Integer(), nullable=False),
        sa.Column("consecutive_healthy", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("last_interval_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_anomaly_id", sa.UUID(), nullable=True),
        sa.Column("open_anomaly_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"event_id IN ({_EVENT_IDS})",
            name=op.f("ck_identity_detector_state_event_id_known"),
        ),
        sa.CheckConstraint(
            f"detector_type IN ({_DETECTORS})",
            name=op.f("ck_identity_detector_state_detector_known"),
        ),
        sa.CheckConstraint(
            f"state IN ({_STATES})",
            name=op.f("ck_identity_detector_state_state_known"),
        ),
        sa.CheckConstraint(
            "consecutive_anomalous >= 0",
            name=op.f("ck_identity_detector_state_anomalous_non_negative"),
        ),
        sa.CheckConstraint(
            "consecutive_healthy >= 0",
            name=op.f("ck_identity_detector_state_healthy_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_identity_detector_state_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_detector_state")),
        sa.UniqueConstraint(
            "log_source_id",
            "event_id",
            "detector_type",
            name="uq_identity_detector_state_series",
        ),
    )
    op.create_table(
        "identity_anomaly_transition",
        sa.Column("identity_anomaly_id", sa.UUID(), nullable=False),
        sa.Column("from_state", sa.String(length=24), nullable=True),
        sa.Column("to_state", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("reason_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("observed_measurement", sa.String(length=16), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"to_state IN ({_STATES})",
            name=op.f("ck_identity_anomaly_transition_to_state_known"),
        ),
        sa.CheckConstraint(
            f"from_state IS NULL OR from_state IN ({_STATES})",
            name=op.f("ck_identity_anomaly_transition_from_state_known"),
        ),
        sa.CheckConstraint(
            f"observed_measurement IN ({_MEASUREMENTS})",
            name=op.f("ck_identity_anomaly_transition_measurement_known"),
        ),
        sa.ForeignKeyConstraint(
            ["identity_anomaly_id"],
            ["identity_anomaly.id"],
            name=op.f(
                "fk_identity_anomaly_transition_identity_anomaly_id_identity_anomaly"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_anomaly_transition")),
    )
    op.create_index(
        "ix_identity_anomaly_transition",
        "identity_anomaly_transition",
        ["identity_anomaly_id", "occurred_at"],
        unique=False,
    )
    _set_family_checks(_NEW_FAMILIES)
    _set_template_check(_NEW_ALERT_TYPES)


def _refuse(probe: sa.TextClause, rows_table: str, what: str) -> None:
    if op.get_bind().scalar(probe):
        raise RuntimeError(
            f"cannot downgrade 0070 while {rows_table} contains {what}; "
            "review and remove those rows first"
        )


def downgrade() -> None:
    _refuse(
        sa.text("SELECT EXISTS (SELECT 1 FROM anomaly_triage WHERE family = 'identity')"),
        "anomaly_triage",
        "identity rows",
    )
    _refuse(
        sa.text("SELECT EXISTS (SELECT 1 FROM alert_policy WHERE family = 'identity')"),
        "alert_policy",
        "identity rows",
    )
    _refuse(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM alert_suppression WHERE family = 'identity')"
        ),
        "alert_suppression",
        "identity rows",
    )
    _refuse(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM alert_suppression_hit WHERE family = 'identity')"
        ),
        "alert_suppression_hit",
        "identity rows",
    )
    _refuse(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM baseline_training_exclusion "
            "WHERE origin_family = 'identity')"
        ),
        "baseline_training_exclusion",
        "identity rows",
    )
    _refuse(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM email_template "
            "WHERE alert_type = 'identity_source')"
        ),
        "email_template",
        "an identity_source row",
    )
    op.drop_index("ix_identity_anomaly_transition", table_name="identity_anomaly_transition")
    op.drop_table("identity_anomaly_transition")
    op.drop_table("identity_detector_state")
    op.drop_index("ix_identity_anomaly_detected", table_name="identity_anomaly")
    op.drop_index("ix_identity_anomaly_state", table_name="identity_anomaly")
    op.drop_table("identity_anomaly")
    _set_family_checks(_OLD_FAMILIES)
    _set_template_check(_OLD_ALERT_TYPES)
