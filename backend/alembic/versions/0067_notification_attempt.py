"""SOC-12e: delivery attempts of a notification (class and status only).

Revision ID: 0067
Revises: 0066
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067"
down_revision: str | None = "0066"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CLASSES = (
    "ok", "transport", "timeout", "http_status", "redirect_refused", "ssrf_refused",
    "no_notifier", "no_destination", "permanent", "payload_invalid",
)  # fmt: skip


def upgrade() -> None:
    op.create_table(
        "notification_attempt",
        sa.Column("notification_id", sa.UUID(), nullable=False),
        sa.Column("attempted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.Column("error_class", sa.String(length=24), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.CheckConstraint(
            "error_class IN (" + ", ".join(f"'{c}'" for c in _CLASSES) + ")",
            name=op.f("ck_notification_attempt_error_class_set"),
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR http_status BETWEEN 100 AND 599",
            name=op.f("ck_notification_attempt_http_status_range"),
        ),
        sa.ForeignKeyConstraint(
            ["notification_id"],
            ["alert_notification.id"],
            name=op.f("fk_notification_attempt_notification_id_alert_notification"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notification_attempt")),
    )
    op.create_index(
        "ix_notification_attempt_notification",
        "notification_attempt",
        ["notification_id", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notification_attempt_notification", table_name="notification_attempt")
    op.drop_table("notification_attempt")
