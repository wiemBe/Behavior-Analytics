"""Move untouched search-family defaults from one hour to the D-28 default.

WAF and IPS move to 15 minutes, every other family to five minutes.

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


_FIFTEEN_MINUTE_FAMILIES = ("WAF_COLLECTION", "IPS_COLLECTION")


def _move_default(old: int, new: int, reason: str, *, fifteen_minute: bool) -> None:
    """Move untouched rows of one group: WAF and IPS, or every other family."""
    op.execute(
        sa.text(
            "WITH moved AS ("
            " UPDATE search_cadence SET cadence_seconds = :new, updated_at = now()"
            " WHERE cadence_seconds = :old AND updated_by IS NULL"
            " AND (family IN :fifteen) = :is_fifteen"
            " RETURNING family)"
            " INSERT INTO audit_log (id, created_at, actor, action, object_type, object_id,"
            " outcome, detail)"
            " SELECT gen_random_uuid(), now(), 'system:migration-0028',"
            " 'search_cadence.update', 'search_cadence', family, 'SUCCESS',"
            " jsonb_build_object('old', :old, 'new', :new, 'reason', :reason)"
            " FROM moved"
        ).bindparams(
            sa.bindparam("fifteen", expanding=True),
            old=old,
            new=new,
            reason=reason,
            is_fifteen=fifteen_minute,
            fifteen=list(_FIFTEEN_MINUTE_FAMILIES),
        )
    )


def upgrade() -> None:
    _move_default(3600, 900, "D-28 default", fifteen_minute=True)
    _move_default(3600, 300, "D-28 default", fifteen_minute=False)


def downgrade() -> None:
    _move_default(900, 3600, "downgrade of 0028", fifteen_minute=True)
    _move_default(300, 3600, "downgrade of 0028", fifteen_minute=False)
