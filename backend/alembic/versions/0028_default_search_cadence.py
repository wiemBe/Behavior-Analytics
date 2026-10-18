"""Move untouched search-family defaults from one hour to five minutes (D-28).

ADDITIVE, data only. Operator-set rows are identified by ``updated_by`` and
are never changed. Every moved row gets an append-only audit record; downgrade
records the reverse instead of deleting either audit trail.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _move_default(old: int, new: int, reason: str) -> None:
    op.execute(
        sa.text(
            "WITH moved AS ("
            " UPDATE search_cadence SET cadence_seconds = :new, updated_at = now()"
            " WHERE cadence_seconds = :old AND updated_by IS NULL"
            " RETURNING family)"
            " INSERT INTO audit_log (id, created_at, actor, action, object_type, object_id,"
            " outcome, detail)"
            " SELECT gen_random_uuid(), now(), 'system:migration-0028',"
            " 'search_cadence.update', 'search_cadence', family, 'SUCCESS',"
            " jsonb_build_object('old', :old, 'new', :new, 'reason', :reason)"
            " FROM moved"
        ).bindparams(old=old, new=new, reason=reason)
    )


def upgrade() -> None:
    _move_default(3600, 300, "D-28 default")


def downgrade() -> None:
    _move_default(300, 3600, "downgrade of 0028")
