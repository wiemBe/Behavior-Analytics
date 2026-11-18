"""FIX-72: shared property snapshot and bounded refresh state."""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision = "0073"
down_revision = "0071"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "property_catalog_state",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_started_at", sa.DateTime(timezone=True)),
        sa.Column("last_completed_at", sa.DateTime(timezone=True)),
        sa.Column("as_of", sa.DateTime(timezone=True)),
        sa.Column("applicability_complete", sa.Boolean(), nullable=False),
        sa.Column("property_count", sa.Integer()),
        sa.Column("expression_count", sa.Integer()),
        sa.Column("duration_seconds", sa.Float()),
        sa.Column("failure", postgresql.JSONB()),
        sa.ForeignKeyConstraint(["instance_id"], ["qradar_instance.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instance_id"),
    )
    op.create_table(
        "property_catalog_entry",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("qradar_id", sa.Integer(), nullable=False),
        sa.Column("identifier", sa.String(512), nullable=False),
        sa.Column("name", sa.String(512), nullable=False),
        sa.Column("property_type", sa.String(32), nullable=False),
        sa.Column("use_for_rule_engine", sa.Boolean(), nullable=False),
        sa.Column("auto_discovered", sa.Boolean(), nullable=False),
        sa.Column("name_allowed", sa.Boolean(), nullable=False),
        sa.Column("type_ids", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.ForeignKeyConstraint(["instance_id"], ["qradar_instance.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("instance_id", "qradar_id"),
    )


def downgrade() -> None:
    op.drop_table("property_catalog_entry")
    op.drop_table("property_catalog_state")
