"""Keep the last failed history step on its scope row.

ADDITIVE (HISTORY-BACKFILL §B.12; HB-16). The two nullable columns expose only
the exception's bounded class name and the collector's failure time. Existing
scope rows start clear; no message or QRadar value is copied into the table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "history_backfill_scope",
        sa.Column("last_error_class", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "history_backfill_scope",
        sa.Column("last_error_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("history_backfill_scope", "last_error_at")
    op.drop_column("history_backfill_scope", "last_error_class")
