"""Store the message reference beside an explanation dimension's detail.

ADDITIVE. Existing rows remain NULL and continue to render their stored English
detail. New coded details store ``{"code", "params"}`` in one JSONB value.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "anomaly_explanation_dimension",
        sa.Column(
            "detail_i18n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("anomaly_explanation_dimension", "detail_i18n")
