"""SOC-16f2: resumable entity mention backfill state.

ADDITIVE. ``entity_mention_backfill`` is a regular PostgreSQL table, not a
hypertable. It stores one watermark per instance and source; the migration
inserts no state rows and starts no backfill work.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0062"
down_revision: str | None = "0061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCES = (
    "'EXPLANATION', 'WAF_TOP', 'IPS_TOP', 'IPS_ENTITY', 'OFFENSE', "
    "'CATCHALL', 'PW_VALUE', 'LOG_SOURCE'"
)
_STATUSES = "'PENDING', 'RUNNING', 'COMPLETE', 'FAILED'"


def upgrade() -> None:
    op.create_table(
        "entity_mention_backfill",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("source", sa.String(length=24), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("cursor", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("rows_written", sa.BigInteger(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            f"source IN ({_SOURCES})",
            name=op.f("ck_entity_mention_backfill_source_known"),
        ),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})",
            name=op.f("ck_entity_mention_backfill_status_known"),
        ),
        sa.CheckConstraint(
            "rows_written >= 0",
            name=op.f("ck_entity_mention_backfill_rows_written_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_entity_mention_backfill_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id",
            "source",
            name=op.f("pk_entity_mention_backfill"),
        ),
    )


def downgrade() -> None:
    op.drop_table("entity_mention_backfill")
