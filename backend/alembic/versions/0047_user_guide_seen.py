"""The guide sections an account has already been shown (GUIDE-3).

ADDITIVE. Revision 0047 revises 0046. Applying it adds ``app_user.guide_seen``, a JSONB array of
guide section ids, ``NOT NULL`` and empty for every existing row. A page-header "Guide" link shows
only while its section is not in the array, so the link appears once per account and follows the
analyst to every browser.

The server default that backfills existing rows is transient, following 0033's ``ui_language``:
a fresh install carries none, and the ORM default supplies the empty list instead, so an upgraded
database and a fresh one match. Downgrade drops the column, losing the stored list with it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column(
            "guide_seen",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("app_user", "guide_seen", server_default=None)


def downgrade() -> None:
    op.drop_column("app_user", "guide_seen")
