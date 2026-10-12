"""Per-source Phase A anomaly evaluation watermark.

ADDITIVE. Existing metric and anomaly tables are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anomaly_evaluation_watermark",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("evaluated_through", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f(
                "fk_anomaly_evaluation_watermark_log_source_id_log_source"
            ),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id", name=op.f("pk_anomaly_evaluation_watermark")
        ),
    )


def downgrade() -> None:
    op.drop_table("anomaly_evaluation_watermark")
