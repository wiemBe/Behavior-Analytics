"""SOC-14a: immutable shift handover notes and frozen queue snapshots."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0063"
down_revision: str | None = "0062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shift_handover",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("author_subject", sa.String(length=255), nullable=False),
        sa.Column("written_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("window_started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_id", sa.UUID(), nullable=True),
        sa.Column("note", sa.String(length=4000), nullable=False),
        sa.Column("body", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            "char_length(note) BETWEEN 1 AND 4000",
            name=op.f("ck_shift_handover_note_length"),
        ),
        sa.ForeignKeyConstraint(
            ["previous_id"],
            ["shift_handover.id"],
            name=op.f("fk_shift_handover_previous_id_shift_handover"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_shift_handover")),
    )
    op.create_index(
        "ix_shift_handover_written_at",
        "shift_handover",
        [sa.text("written_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_shift_handover_written_at", table_name="shift_handover")
    op.drop_table("shift_handover")
