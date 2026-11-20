"""JDBC-1 P2: `log_source_pause`, the edge-written QRadar-disabled spans.

ADDITIVE. One new table keyed `(log_source_id, reason, starts_at)`, cascading
with its source. A half-open `[starts_at, ends_at)` span has `ends_at` NULL
while open; a partial unique index allows one open span per source and reason.
No column default, so a bare upgrade leaves none behind (the round-trip tests
forbid them). The D-94 follow-up adds the `ABSENT` reason by changing the one
`_REASONS` literal in a later migration.

Revision id 0076, chained after 0075.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0076"
down_revision: str | None = "0075"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_REASONS = "'QRADAR_DISABLED'"


def upgrade() -> None:
    op.create_table(
        "log_source_pause",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"reason IN ({_REASONS})",
            name=op.f("ck_log_source_pause_reason_known"),
        ),
        sa.CheckConstraint(
            "ends_at IS NULL OR ends_at > starts_at",
            name=op.f("ck_log_source_pause_ends_after_start"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_log_source_pause_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id", "reason", "starts_at", name=op.f("pk_log_source_pause")
        ),
    )
    op.create_index(
        "ix_log_source_pause_source_starts", "log_source_pause", ["log_source_id", "starts_at"]
    )
    op.create_index(
        "uq_log_source_pause_open",
        "log_source_pause",
        ["log_source_id", "reason"],
        unique=True,
        postgresql_where=sa.text("ends_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_table("log_source_pause")
