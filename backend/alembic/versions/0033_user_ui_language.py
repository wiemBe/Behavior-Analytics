"""The account's interface language (D-37, I18N-1).

ADDITIVE. Revision 0033 revises 0032. Applying it adds ``app_user.ui_language``,
``VARCHAR(2) NOT NULL``; every existing row reads ``'en'``. The check constraint
``ck_app_user_ui_language_known`` allows exactly ``'en'`` and ``'tr'``, the same
set the request schema validates, so a value cannot reach the table unchecked.

The server default that backfills existing rows is transient, following 0025's
``auth_source``: a fresh install carries none, and the ORM default supplies
``'en'`` instead, so an upgraded database and a fresh one match. Downgrade drops
the constraint and the column, losing stored choices with it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "app_user",
        sa.Column(
            "ui_language",
            sa.String(length=2),
            nullable=False,
            server_default=sa.text("'en'"),
        ),
    )
    op.alter_column("app_user", "ui_language", server_default=None)
    op.create_check_constraint(
        op.f("ck_app_user_ui_language_known"),
        "app_user",
        "ui_language IN ('en', 'tr')",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_app_user_ui_language_known"), "app_user", type_="check")
    op.drop_column("app_user", "ui_language")
