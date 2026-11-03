"""The latest QRadar access-check result per console and catalog entry (ACC-1).

ADDITIVE. One table, no Phase A table or enum is touched.

``qradar_access_result`` holds one row per (console, catalog entry): the status class
(``ok``, ``forbidden``, ``unauthorized``, ``not_found``, ``unreachable``, ``error``), the HTTP
status QRadar answered, when the check ran, the status before it, and the syncs it started. Each
check overwrites the row; there is no history and no retention. It stores no response body.

The model defaults (``syncs_started`` an empty list) live in the model, not in the table: the
migration suite pins that no server default outlives a revision except the timestamps.

Written on the 0049 head of its branch; chained onto 0050 (QRADAR_HEALTH, HLT-2) at the merge.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUSES = "'ok', 'forbidden', 'unauthorized', 'not_found', 'unreachable', 'error'"


def upgrade() -> None:
    op.create_table(
        "qradar_access_result",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("entry_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_status", sa.String(length=16), nullable=True),
        sa.Column("syncs_started", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            f"status IN ({_STATUSES})", name=op.f("ck_qradar_access_result_status_known")
        ),
        sa.CheckConstraint(
            f"previous_status IS NULL OR previous_status IN ({_STATUSES})",
            name=op.f("ck_qradar_access_result_previous_status_known"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_qradar_access_result_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_qradar_access_result")),
        sa.UniqueConstraint(
            "instance_id", "entry_id", name=op.f("uq_qradar_access_result_key")
        ),
    )
    op.create_table(
        "qradar_access_run",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_qradar_access_run_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_qradar_access_run")),
    )


def downgrade() -> None:
    op.drop_table("qradar_access_run")
    op.drop_table("qradar_access_result")
