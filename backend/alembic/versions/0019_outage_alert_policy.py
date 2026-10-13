"""Phase C.2a: the outage alert policy.

ADDITIVE. One new table; no existing table or enum is altered (AGENTS.md
rule 7). A row says whether a class or a single source opens a `NO_EVENTS`
alert, and optionally overrides the silence grace for that scope.

Chained after 0017 for now. A parallel task (T-050b) adds 0018 on the same
parent; the orchestrator re-chains at merge. 0019 therefore sits at 0017 until
then, and `alembic heads` is a single head either way.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outage_alert_policy",
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_value", sa.String(length=64), nullable=False),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("silence_grace_buckets", sa.Integer(), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
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
        sa.CheckConstraint(
            "mode IN ('ALERT', 'RECORD_ONLY')",
            name=op.f("ck_outage_alert_policy_mode_known"),
        ),
        sa.CheckConstraint(
            "scope_kind IN ('CLASS', 'SOURCE')",
            name=op.f("ck_outage_alert_policy_scope_kind_known"),
        ),
        sa.CheckConstraint(
            "silence_grace_buckets IS NULL OR "
            "(silence_grace_buckets >= 0 AND silence_grace_buckets <= 48)",
            name=op.f("ck_outage_alert_policy_silence_grace_buckets_range"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_outage_alert_policy_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outage_alert_policy")),
        sa.UniqueConstraint(
            "instance_id",
            "scope_kind",
            "scope_value",
            name=op.f("uq_outage_alert_policy_instance_scope"),
            postgresql_nulls_not_distinct=True,
        ),
    )


def downgrade() -> None:
    op.drop_table("outage_alert_policy")
