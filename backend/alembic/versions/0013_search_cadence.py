"""Operator-controlled QRadar search cadence.

ADDITIVE. Creates and seeds one global row for each search family. The cadence
CHECK is deliberately frozen here rather than imported from application code.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SEARCH_CADENCE_CHECK = (
    "cadence_seconds IN (300, 900, 1800, 3600, 7200, 14400, 28800)"
)


def upgrade() -> None:
    op.create_table(
        "search_cadence",
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("cadence_seconds", sa.Integer(), nullable=False),
        sa.Column("last_slot_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_run_outcome", sa.String(length=32), nullable=True),
        sa.Column("last_run_searches", sa.Integer(), nullable=True),
        sa.Column("last_run_buckets", sa.Integer(), nullable=True),
        sa.Column("last_run_splits", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            SEARCH_CADENCE_CHECK,
            name=op.f("ck_search_cadence_cadence_seconds_allowed"),
        ),
        sa.PrimaryKeyConstraint("family", name=op.f("pk_search_cadence")),
    )
    op.execute(
        sa.text(
            "INSERT INTO search_cadence (family, cadence_seconds, updated_at) VALUES "
            "('PHASE_A_VOLUME', 3600, now()), "
            "('WAF_COLLECTION', 3600, now()), "
            "('ANOMALY_EXPLANATION', 3600, now()), "
            "('RULE_MATCH', 3600, now())"
        )
    )


def downgrade() -> None:
    op.drop_table("search_cadence")
