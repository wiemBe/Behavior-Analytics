"""Store the message reference beside a rule-health snapshot's reason.

ADDITIVE. ``rule_health_snapshot.reason_i18n`` holds ``{"code", "params"}`` in one JSONB value.
Existing rows remain NULL and keep rendering their stored English reason. The table is a plain
table, not a hypertable.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "rule_health_snapshot",
        sa.Column("reason_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("rule_health_snapshot", "reason_i18n")
