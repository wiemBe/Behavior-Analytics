"""Phase D: versioned IPS source profiles and validation state.

ADDITIVE. Phase A and WAF tables and enums are unchanged.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ips_source_profile",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("product_label", sa.String(length=128), nullable=True),
        sa.Column("preset", sa.String(length=32), nullable=True),
        sa.Column("mapping", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("action_value_map", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("validated_profile_version", sa.Integer(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_requested_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column(
            "id",
            sa.UUID(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "profile_version >= 1",
            name=op.f("ck_ips_source_profile_profile_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_ips_source_profile_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_ips_source_profile_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_source_profile")),
        sa.UniqueConstraint(
            "log_source_id", name=op.f("uq_ips_source_profile_log_source_id")
        ),
    )


def downgrade() -> None:
    op.drop_table("ips_source_profile")
