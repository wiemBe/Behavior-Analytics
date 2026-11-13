"""SOC-12b: encrypted named webhook destinations.

Revision ID: 0065
Revises: 0064
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0065"
down_revision: str | None = "0064"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notification_destination",
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        # EncryptedString has String as its DDL representation. Encryption is
        # applied by the ORM and intentionally imports no live model into a migration.
        sa.Column("url", sa.String(length=4096), nullable=False),
        sa.Column("allow_http", sa.Boolean(), nullable=False),
        sa.Column("allow_private", sa.Boolean(), nullable=False),
        sa.Column("hmac_secret", sa.String(length=8192), nullable=True),
        sa.Column("headers", sa.String(), nullable=False),
        sa.Column("last_test_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_test_status", sa.Integer(), nullable=True),
        sa.Column("last_test_class", sa.String(length=32), nullable=True),
        sa.Column("created_by", sa.String(length=255), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=False),
        sa.Column(
            "id", sa.UUID(), nullable=False
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
            "char_length(btrim(name)) BETWEEN 1 AND 64",
            name=op.f("ck_notification_destination_name_length"),
        ),
        sa.CheckConstraint(
            "last_test_status IS NULL OR last_test_status BETWEEN 100 AND 599",
            name=op.f("ck_notification_destination_last_test_status_range"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_destination")),
        sa.UniqueConstraint("name", name=op.f("uq_notification_destination_name")),
    )


def downgrade() -> None:
    op.drop_table("notification_destination")
