"""Store the message reference beside a search run's fixed error message.

ADDITIVE. Existing rows remain NULL and continue to show their stored English
``error_message``. A run that fails with one of the fixed sentences (the Ariel
search failed, the timeout, the refused permit, ...) stores ``{"code", "params"}``
in one JSONB value; an exception's or Ariel's own words stay uncoded.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0042"
down_revision: str | None = "0040"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "search_execution",
        sa.Column(
            "error_message_i18n",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("search_execution", "error_message_i18n")
