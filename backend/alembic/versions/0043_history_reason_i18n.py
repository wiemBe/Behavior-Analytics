"""Store the message reference beside the history-backfill scope reason.

ADDITIVE. One nullable JSONB column holds ``{"code", "params"}``, beside the English text column
it describes:

* ``history_backfill_scope.reason`` -- why a scope is in its state (the planner's step verdicts,
  the window fit and the lifecycle's transition table).

Existing rows stay NULL and keep rendering their stored English. Nothing is backfilled. The gap
descriptions inside ``history_backfill_scope.progress`` carry their code inside the JSONB (MSG-4b)
and need no column.

Written as 0041 on 0037 (the head of the branch this was cut from); renumbered 0043 at the merge,
after MSG-8b's 0042, so the revision ids rise along the chain.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "history_backfill_scope",
        sa.Column("reason_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("history_backfill_scope", "reason_i18n")
