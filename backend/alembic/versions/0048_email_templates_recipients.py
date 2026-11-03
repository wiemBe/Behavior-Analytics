"""Administered alert e-mail: templates and recipients (MAIL-1).

ADDITIVE. Two tables, no Phase A table or enum is touched.

``email_template`` holds one subject/HTML/text triple per row. ``alert_type``
NULL is the default template (``NULLS NOT DISTINCT`` keeps it unique, so at
most one default row exists and "reset to the shipped default" is one DELETE);
a set value is one of the eight ``alert.source_type`` values, enforced by a
check constraint. The shipped default template lives in code
(``app/services/email_templates.py``) and is used when no enabled row matches.

``email_recipient`` is one mailbox routing decides for, with a minimum severity
and an optional JSONB array of alert types (NULL = every type). Routing loads
every enabled row once per dispatch run — one query, never one per alert.

The defaults (``enabled`` true, ``min_severity`` LOW) live in the model, not in
the table: the migration suite pins that no server default outlives a revision
except the timestamps.

Neither table is a hypertable and neither has retention: a site has a handful
of rows, and losing one to retention would silently stop a mailbox's alerts.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ALERT_TYPES = (
    "'log_source', 'waf_endpoint', 'ips_entity', 'custom_rule', 'pw_value', "
    "'catchall_sender', 'qradar_instance', 'scheduled_search'"
)


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "email_template",
        sa.Column("alert_type", sa.String(length=32), nullable=True),
        sa.Column("subject", sa.String(length=512), nullable=False),
        sa.Column("html_body", sa.Text(), nullable=False),
        sa.Column("text_body", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"alert_type IS NULL OR alert_type IN ({_ALERT_TYPES})",
            name=op.f("ck_email_template_alert_type_known"),
        ),
        sa.CheckConstraint(
            "length(subject) > 0", name=op.f("ck_email_template_subject_not_empty")
        ),
        sa.CheckConstraint(
            "length(html_body) > 0", name=op.f("ck_email_template_html_body_not_empty")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_template")),
        sa.UniqueConstraint(
            "alert_type",
            name=op.f("uq_email_template_alert_type"),
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "email_recipient",
        sa.Column("address", sa.String(length=255), nullable=False),
        sa.Column("min_severity", sa.String(length=16), nullable=False),
        sa.Column(
            "alert_types",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "length(address) > 0", name=op.f("ck_email_recipient_address_not_empty")
        ),
        sa.CheckConstraint(
            "min_severity IN ('INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL')",
            name=op.f("ck_email_recipient_min_severity_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_email_recipient")),
        sa.UniqueConstraint("address", name=op.f("uq_email_recipient_address")),
    )


def downgrade() -> None:
    op.drop_table("email_recipient")
    op.drop_table("email_template")
