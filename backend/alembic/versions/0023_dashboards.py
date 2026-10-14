"""UI redesign (UR.5.5): saved dashboards and each user's default dashboard.

ADDITIVE. Creates `dashboard` and `user_dashboard_pref`. Seeds nothing: the templates live in
the frontend, so the backend stores only what a user saved. Phase A, WAF, IPS, catch-all and
geo tables and enums are unchanged.

`owner_subject` is the principal's `subject`. Under the current auth every dev caller is
`local-dev` (an unauthenticated call) or `local-token` (any bearer token), so all dashboards
share one owner until OIDC lands (UR.10 Q3). Ownership is enforced all the same.

Revision id 0023, chained after 0022, the geo country buckets.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "dashboard",
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("template_key", sa.String(length=32), nullable=True),
        sa.Column("template_version", sa.Integer(), nullable=True),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("layout", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "visibility IN ('PRIVATE', 'SHARED')", name=op.f("ck_dashboard_visibility_known")
        ),
        sa.CheckConstraint(
            "schema_version >= 1", name=op.f("ck_dashboard_schema_version_positive")
        ),
        sa.CheckConstraint("revision >= 1", name=op.f("ck_dashboard_revision_positive")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dashboard")),
        sa.UniqueConstraint("owner_subject", "name", name="uq_dashboard_owner_subject_name"),
    )
    op.create_index(
        "ix_dashboard_owner", "dashboard", ["owner_subject", sa.text("updated_at DESC")]
    )
    op.create_table(
        "user_dashboard_pref",
        sa.Column("owner_subject", sa.String(length=255), nullable=False),
        sa.Column("default_dashboard_id", sa.UUID(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["default_dashboard_id"],
            ["dashboard.id"],
            name=op.f("fk_user_dashboard_pref_default_dashboard_id_dashboard"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("owner_subject", name=op.f("pk_user_dashboard_pref")),
    )


def downgrade() -> None:
    op.drop_table("user_dashboard_pref")
    op.drop_index("ix_dashboard_owner", table_name="dashboard")
    op.drop_table("dashboard")
