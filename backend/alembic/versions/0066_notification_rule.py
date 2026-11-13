"""SOC-12c: notification routing rules.

Revision ID: 0066
Revises: 0065
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0066"
down_revision: str | None = "0065"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_rule",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("min_severity", sa.String(length=16), nullable=False),
        sa.Column("families", postgresql.ARRAY(sa.String(length=32)), nullable=True),
        sa.Column("owners", postgresql.ARRAY(sa.String(length=255)), nullable=True),
        sa.Column("source_classes", postgresql.ARRAY(sa.String(length=64)), nullable=True),
        sa.Column("group_ids", postgresql.ARRAY(sa.Integer()), nullable=True),
        sa.Column("window_start_minute", sa.Integer(), nullable=True),
        sa.Column("window_end_minute", sa.Integer(), nullable=True),
        sa.Column("destination_ids", postgresql.ARRAY(sa.UUID()), nullable=False),
        sa.Column("stop_default", sa.Boolean(), nullable=False),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
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
            "char_length(btrim(name)) BETWEEN 1 AND 64",
            name=op.f("ck_notification_rule_name_length"),
        ),
        sa.CheckConstraint(
            "min_severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW')",
            name=op.f("ck_notification_rule_min_severity_set"),
        ),
        sa.CheckConstraint(
            "(window_start_minute IS NULL AND window_end_minute IS NULL) OR "
            "(window_start_minute IS NOT NULL AND window_end_minute IS NOT NULL AND "
            "window_start_minute BETWEEN 0 AND 1439 AND window_end_minute BETWEEN 0 AND 1439 "
            "AND window_start_minute <> window_end_minute)",
            name=op.f("ck_notification_rule_window_valid"),
        ),
        sa.CheckConstraint(
            "cardinality(destination_ids) BETWEEN 1 AND 10",
            name=op.f("ck_notification_rule_destination_count"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_rule")),
        sa.UniqueConstraint("name", name=op.f("uq_notification_rule_name")),
    )


def downgrade() -> None:
    op.drop_table("notification_rule")
