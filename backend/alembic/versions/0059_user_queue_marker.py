"""SOC-4a: the per-user "last visit" marker for the triage queue.

ADDITIVE. Creates `user_queue_marker`, one row per principal subject. No existing table or
enum is touched and no new SQL enum is created. No foreign key: the subject is a plain string
(`local-dev` has no account), the `user_dashboard_pref` precedent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0059"
down_revision: str | None = "0058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_queue_marker",
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("marked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("previous_marked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("owner_subject", name=op.f("pk_user_queue_marker")),
    )


def downgrade() -> None:
    op.drop_table("user_queue_marker")
