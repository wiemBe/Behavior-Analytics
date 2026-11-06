"""SOC-17c: the alert policy and the daily digest's run record.

ADDITIVE. Two new tables and one widened CHECK on ``email_template``; no Phase A
table or enum is touched and no new SQL enum is created. Detector values are
validated by the API against the four families' existing Python enums (AGENTS.md
rule 6), never by a database enum.

``alert_policy`` is one row per
``(instance_id, scope_kind, scope_value, family, detector_type)``; NULL
``instance_id`` is global and NULL ``detector_type`` means every detector of the
family. The unique constraint holds across NULLs, exactly like
``outage_alert_policy``. ``alert_digest_run`` is one row per
``(instance_id, digest_date)``, which is what makes the daily digest idempotent.

The e-mail template CHECK gains ``'digest'`` in place: the digest owns a template
whose body describes a daily summary, so it must not fall back to the default
alert template. The downgrade refuses while a ``digest`` row exists, because
deleting an administrator's template would destroy their work; once it has been
removed, the old list is restored.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057"
down_revision: str | None = "0056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TEMPLATE_CHECK = "ck_email_template_alert_type_known"
_OLD_ALERT_TYPES = (
    "'log_source', 'waf_endpoint', 'ips_entity', 'custom_rule', 'pw_value', "
    "'catchall_sender', 'qradar_instance', 'scheduled_search'"
)
_NEW_ALERT_TYPES = _OLD_ALERT_TYPES + ", 'digest'"
_SCOPES = "'SOURCE', 'CLASS'"
_FAMILIES = "'log_source', 'waf', 'ips', 'property_watch'"
_MODES = "'ALERT', 'RECORD_ONLY', 'DIGEST'"
_STATES = "'SUCCESS', 'EMPTY', 'NOT_CONFIGURED', 'NO_RECIPIENTS', 'FAILED'"
_ERROR_CODES = (
    "'not_configured', 'insecure_login', 'connection_refused', 'timeout', "
    "'connection_failed', 'tls_failed', 'auth_failed', 'recipient_refused', "
    "'sender_refused', 'server_error'"
)


def _timestamps(with_updated: bool) -> list[sa.Column]:
    columns = [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        )
    ]
    if with_updated:
        columns.append(
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("now()"),
                nullable=False,
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "alert_policy",
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("scope_kind", sa.String(length=16), nullable=False),
        sa.Column("scope_value", sa.String(length=128), nullable=False),
        sa.Column("family", sa.String(length=32), nullable=False),
        sa.Column("detector_type", sa.String(length=40), nullable=True),
        sa.Column("mode", sa.String(length=16), nullable=False),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(with_updated=True),
        sa.CheckConstraint(
            f"scope_kind IN ({_SCOPES})",
            name=op.f("ck_alert_policy_scope_kind_known"),
        ),
        sa.CheckConstraint(
            "length(scope_value) > 0",
            name=op.f("ck_alert_policy_scope_value_not_empty"),
        ),
        sa.CheckConstraint(
            f"family IN ({_FAMILIES})",
            name=op.f("ck_alert_policy_family_known"),
        ),
        sa.CheckConstraint(
            "detector_type IS NULL OR length(detector_type) > 0",
            name=op.f("ck_alert_policy_detector_type_not_empty"),
        ),
        sa.CheckConstraint(
            f"mode IN ({_MODES})",
            name=op.f("ck_alert_policy_mode_known"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_alert_policy_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_policy")),
        sa.UniqueConstraint(
            "instance_id",
            "scope_kind",
            "scope_value",
            "family",
            "detector_type",
            name="uq_alert_policy_instance_scope",
            postgresql_nulls_not_distinct=True,
        ),
    )
    op.create_table(
        "alert_digest_run",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("digest_date", sa.Date(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("recipient_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=32), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        *_timestamps(with_updated=False),
        sa.CheckConstraint(
            f"state IN ({_STATES})",
            name=op.f("ck_alert_digest_run_state_known"),
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR error_code IN ({_ERROR_CODES})",
            name=op.f("ck_alert_digest_run_error_code_known"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_alert_digest_run_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_alert_digest_run")),
        sa.UniqueConstraint(
            "instance_id",
            "digest_date",
            name="uq_alert_digest_run_instance_date",
        ),
    )
    op.drop_constraint(op.f(_TEMPLATE_CHECK), "email_template", type_="check")
    op.create_check_constraint(
        op.f(_TEMPLATE_CHECK),
        "email_template",
        f"alert_type IS NULL OR alert_type IN ({_NEW_ALERT_TYPES})",
    )


def downgrade() -> None:
    digest_rows = op.get_bind().scalar(
        sa.text(
            "SELECT EXISTS (SELECT 1 FROM email_template WHERE alert_type = 'digest')"
        )
    )
    if digest_rows:
        raise RuntimeError(
            "cannot downgrade 0057 while email_template contains a digest row; "
            "remove that template first"
        )
    op.drop_constraint(op.f(_TEMPLATE_CHECK), "email_template", type_="check")
    op.create_check_constraint(
        op.f(_TEMPLATE_CHECK),
        "email_template",
        f"alert_type IS NULL OR alert_type IN ({_OLD_ALERT_TYPES})",
    )
    op.drop_table("alert_digest_run")
    op.drop_table("alert_policy")
