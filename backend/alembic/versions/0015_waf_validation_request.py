"""Track asynchronous WAF profile validation requests.

ADDITIVE. Existing profile validation state and reports are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "waf_source_profile",
        sa.Column("validation_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "waf_source_profile",
        sa.Column("validation_requested_by", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("waf_source_profile", "validation_requested_by")
    op.drop_column("waf_source_profile", "validation_requested_at")
