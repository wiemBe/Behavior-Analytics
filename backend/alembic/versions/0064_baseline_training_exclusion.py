"""SOC-13a: baseline training exclusions and their two triage event kinds.

ADDITIVE. A new table, `baseline_training_exclusion` (strings plus CHECKs, no
PostgreSQL enum), and two new `anomaly_triage_event.kind` values. The kind column
grows from varchar(16) to varchar(32) because `BASELINE_EXCLUSION_CREATED` is 26
characters; the existing values all fit, so the widening loses nothing.

Downgrade deletes the events of the two new kinds (the old check would reject them),
restores the old check, narrows the column and drops the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0064"
down_revision: str | None = "0063"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_KINDS = "'STATUS', 'OWNER', 'DISPOSITION', 'NOTE', 'COMMENT'"
_NEW_KINDS = _OLD_KINDS + ", 'BASELINE_EXCLUSION_CREATED', 'BASELINE_EXCLUSION_REVOKED'"
_FAMILIES = "'log_source', 'waf', 'ips', 'property_watch', 'rule'"
_SUBJECTS = "'log_source', 'endpoint', 'entity', 'source', 'value', 'rule'"
_KIND_CHECK = "ck_anomaly_triage_event_kind_known"


def upgrade() -> None:
    op.create_table(
        "baseline_training_exclusion",
        sa.Column("origin_family", sa.String(length=32), nullable=False),
        sa.Column("origin_anomaly_id", sa.UUID(), nullable=False),
        sa.Column("subject_kind", sa.String(length=24), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("scope_key", sa.String(length=80), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.String(length=1000), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("revoked_by", sa.String(length=255), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revocation_reason", sa.String(length=1000), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            f"origin_family IN ({_FAMILIES})",
            name=op.f("ck_baseline_training_exclusion_family_known"),
        ),
        sa.CheckConstraint(
            f"subject_kind IN ({_SUBJECTS})",
            name=op.f("ck_baseline_training_exclusion_subject_kind_known"),
        ),
        sa.CheckConstraint(
            "char_length(btrim(reason)) BETWEEN 1 AND 1000",
            name=op.f("ck_baseline_training_exclusion_reason_length"),
        ),
        sa.CheckConstraint(
            "starts_at < ends_at",
            name=op.f("ck_baseline_training_exclusion_interval_ordered"),
        ),
        sa.CheckConstraint(
            "ends_at - starts_at <= interval '7 days'",
            name=op.f("ck_baseline_training_exclusion_interval_max"),
        ),
        sa.CheckConstraint(
            "(revoked_by IS NULL AND revoked_at IS NULL AND revocation_reason IS NULL) "
            "OR (revoked_by IS NOT NULL AND revoked_at IS NOT NULL "
            "AND revocation_reason IS NOT NULL)",
            name=op.f("ck_baseline_training_exclusion_revocation_paired"),
        ),
        sa.CheckConstraint(
            "revocation_reason IS NULL OR "
            "char_length(btrim(revocation_reason)) BETWEEN 1 AND 1000",
            name=op.f("ck_baseline_training_exclusion_revocation_reason_length"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_baseline_training_exclusion")),
    )
    op.create_index(
        "ix_baseline_training_exclusion_subject",
        "baseline_training_exclusion",
        ["subject_kind", "subject_id", "scope_key", "starts_at", "ends_at"],
        unique=False,
    )
    op.create_index(
        "uq_baseline_training_exclusion_active",
        "baseline_training_exclusion",
        ["subject_kind", "subject_id", "scope_key", "starts_at", "ends_at"],
        unique=True,
        postgresql_where=sa.text("revoked_at IS NULL"),
    )
    op.alter_column(
        "anomaly_triage_event",
        "kind",
        existing_type=sa.String(length=16),
        type_=sa.String(length=32),
        existing_nullable=False,
    )
    op.drop_constraint(op.f(_KIND_CHECK), "anomaly_triage_event", type_="check")
    op.create_check_constraint(
        op.f(_KIND_CHECK), "anomaly_triage_event", f"kind IN ({_NEW_KINDS})")


def downgrade() -> None:
    op.drop_constraint(op.f(_KIND_CHECK), "anomaly_triage_event", type_="check")
    op.execute(
        "DELETE FROM anomaly_triage_event WHERE kind IN "
        "('BASELINE_EXCLUSION_CREATED', 'BASELINE_EXCLUSION_REVOKED')"
    )
    op.create_check_constraint(
        op.f(_KIND_CHECK), "anomaly_triage_event", f"kind IN ({_OLD_KINDS})")
    op.alter_column(
        "anomaly_triage_event",
        "kind",
        existing_type=sa.String(length=32),
        type_=sa.String(length=16),
        existing_nullable=False,
    )
    op.drop_index("uq_baseline_training_exclusion_active", table_name="baseline_training_exclusion")
    op.drop_index("ix_baseline_training_exclusion_subject", table_name="baseline_training_exclusion")
    op.drop_table("baseline_training_exclusion")
