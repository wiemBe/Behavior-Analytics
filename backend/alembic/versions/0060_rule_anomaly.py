"""SOC-6a: rule silence and storm incidents, the `rule` family and its e-mail type.

ADDITIVE. Creates three new tables and widens five CHECKs. No SQL enum is created
(AGENTS.md rule 6): `detector_type` is a string validated by the API against the
Python `RuleAnomalyType`, exactly as `pw_anomaly.detector_type` is.

`rule_anomaly` is the subject-anchored incident: `rule_id` references
`analytics_rule` with `ON DELETE CASCADE`, and `qradar_id` / `is_building_block`
are denormalised copies so a list row explains itself without a join.
`rule_detector_state` keeps the hysteresis counters of one (rule, detector).
`rule_anomaly_transition` is the auditable lifecycle log.

`AnomalyFamily.RULE` widens the triage, alert-policy and both suppression family
CHECKs; `EMAIL_ALERT_TYPES` gains `analytics_rule`, so the e-mail-template CHECK is
widened too. `digest` stays where 0057 put it.

The downgrade refuses while any `rule` row exists in triage, alert policy,
suppression or suppression hit, or any `analytics_rule` e-mail row: the old CHECKs
cannot hold them, and silently deleting an analyst's record or an administrator's
template would destroy work (the 0056 pattern). Once those rows are gone it drops
the tables and restores the previous lists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0060"
down_revision: str | None = "0059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_FAMILIES = "'log_source', 'waf', 'ips', 'property_watch'"
_NEW_FAMILIES = _OLD_FAMILIES + ", 'rule'"

_TEMPLATE_CHECK = "ck_email_template_alert_type_known"
_OLD_ALERT_TYPES = (
    "'log_source', 'waf_endpoint', 'ips_entity', 'custom_rule', 'pw_value', "
    "'catchall_sender', 'qradar_instance', 'scheduled_search', 'digest'"
)
_NEW_ALERT_TYPES = (
    "'log_source', 'waf_endpoint', 'ips_entity', 'custom_rule', 'pw_value', "
    "'catchall_sender', 'qradar_instance', 'scheduled_search', 'analytics_rule', "
    "'digest'"
)

_FAMILY_CHECKS = (
    ("anomaly_triage", "ck_anomaly_triage_family_known"),
    ("alert_policy", "ck_alert_policy_family_known"),
    ("alert_suppression", "ck_alert_suppression_family_known"),
    ("alert_suppression_hit", "ck_alert_suppression_hit_family_known"),
)


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
    for table, name in _FAMILY_CHECKS:
        op.drop_constraint(op.f(name), table, type_="check")
        op.create_check_constraint(
            op.f(name), table, f"family IN ({families})"
        )


def _set_template_check(alert_types: str) -> None:
    op.drop_constraint(op.f(_TEMPLATE_CHECK), "email_template", type_="check")
    op.create_check_constraint(
        op.f(_TEMPLATE_CHECK),
        "email_template",
        f"alert_type IS NULL OR alert_type IN ({alert_types})",
    )


def upgrade() -> None:
    op.create_table(
        "rule_anomaly",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("qradar_id", sa.Integer(), nullable=False),
        sa.Column("is_building_block", sa.Boolean(), nullable=False),
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
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column(
            "explanation_i18n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column(
            "details", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("evidence_status", sa.String(length=16), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=255), nullable=True),
        sa.Column(
            "suppression_reason_i18n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["analytics_rule.id"],
            name=op.f("fk_rule_anomaly_rule_id_analytics_rule"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_anomaly")),
    )
    op.create_index(
        "ix_rule_anomaly_state",
        "rule_anomaly",
        ["rule_id", "detector_type", "state"],
        unique=False,
    )
    op.create_index(
        "ix_rule_anomaly_detected", "rule_anomaly", ["detected_at"], unique=False
    )
    op.create_table(
        "rule_detector_state",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("detector_type", sa.String(length=40), nullable=False),
        sa.Column("consecutive_anomalous", sa.Integer(), nullable=False),
        sa.Column("consecutive_healthy", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("last_interval_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_anomaly_id", sa.UUID(), nullable=True),
        sa.Column("open_anomaly_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["analytics_rule.id"],
            name=op.f("fk_rule_detector_state_rule_id_analytics_rule"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_detector_state")),
        sa.UniqueConstraint(
            "rule_id", "detector_type", name="uq_rule_detector_state_rule"
        ),
    )
    op.create_table(
        "rule_anomaly_transition",
        sa.Column("rule_anomaly_id", sa.UUID(), nullable=False),
        sa.Column("from_state", sa.String(length=24), nullable=True),
        sa.Column("to_state", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "reason_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("observed_measurement", sa.String(length=16), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["rule_anomaly_id"],
            ["rule_anomaly.id"],
            name=op.f("fk_rule_anomaly_transition_rule_anomaly_id_rule_anomaly"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_rule_anomaly_transition")),
    )
    op.create_index(
        "ix_rule_anomaly_transition",
        "rule_anomaly_transition",
        ["rule_anomaly_id", "occurred_at"],
        unique=False,
    )

    _set_family_checks(_NEW_FAMILIES)
    _set_template_check(_NEW_ALERT_TYPES)


def _refuse(probe: sa.TextClause, rows_table: str, what: str) -> None:
    if op.get_bind().scalar(probe):
        raise RuntimeError(
            f"cannot downgrade 0060 while {rows_table} contains {what}; "
            "review and remove those rows first"
        )


def downgrade() -> None:
    _refuse(
        sa.text("SELECT EXISTS (SELECT 1 FROM anomaly_triage WHERE family = 'rule')"),
        "anomaly_triage",
        "rule rows",
    )
    _refuse(
        sa.text("SELECT EXISTS (SELECT 1 FROM alert_policy WHERE family = 'rule')"),
        "alert_policy",
        "rule rows",
    )
    _refuse(
        sa.text("SELECT EXISTS (SELECT 1 FROM alert_suppression WHERE family = 'rule')"),
        "alert_suppression",
        "rule rows",
    )
    _refuse(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM alert_suppression_hit WHERE family = 'rule')"
        ),
        "alert_suppression_hit",
        "rule rows",
    )
    _refuse(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM email_template "
            "WHERE alert_type = 'analytics_rule')"
        ),
        "email_template",
        "an analytics_rule row",
    )

    op.drop_index("ix_rule_anomaly_transition", table_name="rule_anomaly_transition")
    op.drop_table("rule_anomaly_transition")
    op.drop_index("ix_rule_anomaly_detected", table_name="rule_anomaly")
    op.drop_index("ix_rule_anomaly_state", table_name="rule_anomaly")
    op.drop_table("rule_anomaly")
    op.drop_table("rule_detector_state")

    _set_family_checks(_OLD_FAMILIES)
    _set_template_check(_OLD_ALERT_TYPES)
