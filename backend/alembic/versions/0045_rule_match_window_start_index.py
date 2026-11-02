"""Index ``rule_match_window.window_start`` for the retention delete.

ADDITIVE. ``rule_match_retention`` deletes with ``WHERE window_start < :cutoff``.
The primary key is ``(instance_id, window_start)``, which does not serve a
predicate on the time column alone, so each batch seq-scans the table. One
btree on ``window_start`` serves that scan. No row changes.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "ix_rule_match_window_window_start"
_TABLE = "rule_match_window"


def upgrade() -> None:
    op.create_index(_INDEX, _TABLE, ["window_start"], unique=False)


def downgrade() -> None:
    op.drop_index(_INDEX, table_name=_TABLE)
