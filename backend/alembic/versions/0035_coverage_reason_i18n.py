"""Store the message reference beside a coverage verdict's reason.

ADDITIVE. ``detection_coverage.reason_i18n`` and ``detection_coverage_snapshot.reason_i18n``
hold ``{"code", "params"}`` in one JSONB value. Existing rows remain NULL and keep rendering
their stored English reason. The per-rule evidence reasons live in the ``evidence`` JSONB and
need no column.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for table in ("detection_coverage", "detection_coverage_snapshot"):
        op.add_column(
            table,
            sa.Column("reason_i18n", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        )


def downgrade() -> None:
    for table in ("detection_coverage_snapshot", "detection_coverage"):
        op.drop_column(table, "reason_i18n")
