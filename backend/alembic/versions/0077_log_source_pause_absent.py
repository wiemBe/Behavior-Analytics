"""Allow ABSENT spans before enabling any absence writer.

Downgrade removes ABSENT history before restoring the original reason CHECK.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0077"
down_revision: str | None = "0076"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "ck_log_source_pause_reason_known"


def upgrade() -> None:
    op.drop_constraint(op.f(_CONSTRAINT), "log_source_pause", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "log_source_pause", "reason IN ('QRADAR_DISABLED', 'ABSENT')"
    )


def downgrade() -> None:
    op.execute("DELETE FROM log_source_pause WHERE reason = 'ABSENT'")
    op.drop_constraint(op.f(_CONSTRAINT), "log_source_pause", type_="check")
    op.create_check_constraint(
        op.f(_CONSTRAINT), "log_source_pause", "reason IN ('QRADAR_DISABLED')"
    )
