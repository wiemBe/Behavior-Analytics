"""Store message references beside alert text and custom-rule status reasons.

ADDITIVE. Existing rows keep their English text and receive NULL metadata; nothing is
backfilled. The revision was cut on 0037 while 0038 and 0039 were being developed in parallel,
so the merge changes only ``down_revision``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COLUMNS: tuple[tuple[str, str], ...] = (
    ("alert", "title_i18n"),
    ("alert", "description_i18n"),
    ("alert", "resolution_reason_i18n"),
    ("custom_rule", "last_status_reason_i18n"),
)


def upgrade() -> None:
    for table, column in _COLUMNS:
        op.add_column(
            table,
            sa.Column(column, postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for table, column in reversed(_COLUMNS):
        op.drop_column(table, column)
