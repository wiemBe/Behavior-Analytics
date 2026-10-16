"""Phase F.1: custom threshold rules over collected aggregates (design §F.10).

ADDITIVE. Creates `custom_rule`, `custom_rule_breach` and `custom_rule_watermark`.
No Phase A, WAF, IPS, catch-all, geo, search or dashboard table or enum is
touched, and no hypertable is declared: rules are few (capped in settings) and
breaches are bounded to one live row per rule. Nothing evaluates until
CUSTOM_RULES_ENABLED is set, so an upgrade changes no behaviour.

The subject pairing CHECK (the `_IPS_SUBJECT_CHECK` pattern) keeps exactly the
subject columns a kind names set; the legal metric set differs per kind and is
validated against the in-code catalog, not the schema. The partial unique index
`uq_custom_rule_breach_active` makes one live breach per rule structural — the
alert dedup shape. The breach FK cascades with its rule.

Revision id 0024, chained after 0023, the saved dashboards.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBJECT_CHECK = (
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


def upgrade() -> None:
    op.create_table(
        "custom_rule",
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("log_source_id", sa.UUID(), nullable=True),
        sa.Column("source_class", sa.String(length=32), nullable=True),
        sa.Column("waf_endpoint_id", sa.UUID(), nullable=True),
        sa.Column("ips_entity_id", sa.UUID(), nullable=True),
        sa.Column("metric", sa.String(length=32), nullable=False),
        sa.Column("operator", sa.String(length=4), nullable=False),
        sa.Column("threshold_value", sa.Float(), nullable=False),
        sa.Column("sustain_buckets", sa.Integer(), nullable=False),
        sa.Column("recovery_buckets", sa.Integer(), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("consecutive_breaching", sa.Integer(), nullable=False),
        sa.Column("consecutive_under", sa.Integer(), nullable=False),
        sa.Column("last_evaluated_bucket_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_breach_id", sa.UUID(), nullable=True),
        sa.Column("last_evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status", sa.String(length=16), nullable=True),
        sa.Column("last_status_reason", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(_SUBJECT_CHECK, name=op.f("ck_custom_rule_subject_matches_kind")),
        sa.CheckConstraint(
            "operator IN ('GT', 'GE', 'LT', 'LE', 'EQ')", name=op.f("ck_custom_rule_operator_known")
        ),
        sa.CheckConstraint(
            "severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')",
            name=op.f("ck_custom_rule_severity_known"),
        ),
        sa.CheckConstraint(
            "threshold_value >= 0", name=op.f("ck_custom_rule_threshold_non_negative")
        ),
        sa.CheckConstraint(
            "sustain_buckets >= 1", name=op.f("ck_custom_rule_sustain_buckets_positive")
        ),
        sa.CheckConstraint(
            "recovery_buckets >= 1", name=op.f("ck_custom_rule_recovery_buckets_positive")
        ),
        sa.CheckConstraint("length(name) > 0", name=op.f("ck_custom_rule_name_not_empty")),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_custom_rule_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        # `source_class` carries a SourceClass value, not a row reference: a
        # class with no classified sources yet is still a legal subject.
        sa.ForeignKeyConstraint(
            ["waf_endpoint_id"],
            ["waf_endpoint.id"],
            name=op.f("fk_custom_rule_waf_endpoint_id_waf_endpoint"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["ips_entity_id"],
            ["ips_entity.id"],
            name=op.f("fk_custom_rule_ips_entity_id_ips_entity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_rule")),
        sa.UniqueConstraint("name", name="uq_custom_rule_name"),
    )
    op.create_index(
        "ix_custom_rule_subject", "custom_rule", ["subject_kind", "enabled"]
    )
    op.create_table(
        "custom_rule_breach",
        sa.Column("rule_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("first_bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recovering_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("worst_value", sa.Float(), nullable=True),
        sa.Column("worst_measurement", sa.String(length=16), nullable=True),
        sa.Column("last_value", sa.Float(), nullable=True),
        sa.Column("last_measurement", sa.String(length=16), nullable=True),
        sa.Column("unmeasured_buckets", sa.Integer(), nullable=False),
        sa.Column("subject_label", sa.String(length=255), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "state IN ('OPEN', 'RECOVERING', 'RESOLVED')",
            name=op.f("ck_custom_rule_breach_state_known"),
        ),
        sa.CheckConstraint(
            "worst_measurement IN ('EXACT', 'LOWER_BOUND')",
            name=op.f("ck_custom_rule_breach_worst_measurement_known"),
        ),
        sa.CheckConstraint(
            "last_measurement IN ('EXACT', 'LOWER_BOUND')",
            name=op.f("ck_custom_rule_breach_last_measurement_known"),
        ),
        sa.CheckConstraint(
            "unmeasured_buckets >= 0",
            name=op.f("ck_custom_rule_breach_unmeasured_buckets_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["rule_id"],
            ["custom_rule.id"],
            name=op.f("fk_custom_rule_breach_rule_id_custom_rule"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_custom_rule_breach")),
    )
    op.create_index(
        "ix_custom_rule_breach_rule", "custom_rule_breach", ["rule_id", "state"]
    )
    op.create_index(
        "uq_custom_rule_breach_active",
        "custom_rule_breach",
        ["rule_id"],
        unique=True,
        postgresql_where=sa.text("state <> 'RESOLVED'"),
    )
    op.create_table(
        "custom_rule_watermark",
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("evaluated_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("subject_kind", name=op.f("pk_custom_rule_watermark")),
    )


def downgrade() -> None:
    op.drop_table("custom_rule_watermark")
    op.drop_index(
        "uq_custom_rule_breach_active", table_name="custom_rule_breach"
    )
    op.drop_index("ix_custom_rule_breach_rule", table_name="custom_rule_breach")
    op.drop_table("custom_rule_breach")
    op.drop_index("ix_custom_rule_subject", table_name="custom_rule")
    op.drop_table("custom_rule")
