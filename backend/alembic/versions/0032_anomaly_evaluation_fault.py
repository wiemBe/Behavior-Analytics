"""The anomaly evaluation fault row: a bucket the engine could not judge.

ADDITIVE (T-170 §3, option B; T-171). Revision 0032 revises 0031. Applying it
changes no existing row and no reader's behaviour: the engine writes a row only
when an evaluation fails, and `anomaly_evaluation_watermark` keeps its shape.

One row per (source, bucket). `attempts` counts evaluation ticks, `error_class`
holds the exception's class name only (never a message, which can carry a data
value), `given_up_at` marks the bucket the engine stopped retrying, and
`resolved_at` marks an open fault a later attempt cleared. Not a hypertable: it
is state, not a series, and the retention task prunes closed rows.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "anomaly_evaluation_fault",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("first_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_failed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("error_class", sa.String(length=64), nullable=False),
        sa.Column("given_up_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "attempts >= 1",
            name=op.f("ck_anomaly_evaluation_fault_attempts_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_anomaly_evaluation_fault_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anomaly_evaluation_fault")),
        sa.UniqueConstraint(
            "log_source_id",
            "bucket_start",
            name=op.f("uq_anomaly_evaluation_fault_bucket"),
        ),
    )
    op.create_index(
        "ix_anomaly_evaluation_fault_source_open",
        "anomaly_evaluation_fault",
        ["log_source_id", "resolved_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_anomaly_evaluation_fault_source_open",
        table_name="anomaly_evaluation_fault",
    )
    op.drop_table("anomaly_evaluation_fault")
