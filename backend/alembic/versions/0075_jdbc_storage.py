"""JDBC-1 J1c1: additive storage for safe JDBC configuration and sync state.

ADDITIVE. Creates `jdbc_source_config`, one safe snapshot per protocol-8 log
source, and `jdbc_sync_state`, one pass/lease per console. Both are keyed by an
existing row's UUID and cascade with it; no other table or enum changes.

Only the 18 allow-listed configuration members from design §2 have columns.
There is no raw parameter bag, credential, URL, connection string or free-text
status column. `field_states` is a JSON object of fixed codes; Postgres cannot
express its per-key rule in a CHECK, so the application's pure validator is the
fine guard and the CHECK only fixes the coarse object shape. Defaults for
`field_states` and the counters are application-side so a bare upgrade leaves
no backfill defaults behind (the migration round-trip tests forbid them).

Revision id 0075, chained after 0074.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0075"
down_revision: str | None = "0074"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONFIG_STATES = "'COMPLETE', 'PARTIAL', 'NOT_COLLECTED', 'UNVERIFIED_PROTOCOL', 'NOT_JDBC'"
_SYNC_STATES = (
    "'IDLE', 'QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED', "
    "'NOT_COLLECTED', 'UNSUPPORTED'"
)
_FAILURE_CODE = "failure_code ~ '^[a-z][a-z0-9_]{0,47}$'"


def upgrade() -> None:
    op.create_table(
        "jdbc_source_config",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("protocol_type_id", sa.Integer(), nullable=False),
        sa.Column("database_type", sa.String(length=32), nullable=True),
        sa.Column("database_host", sa.String(length=253), nullable=True),
        sa.Column("tls_hostname", sa.String(length=253), nullable=True),
        sa.Column("database_port", sa.Integer(), nullable=True),
        sa.Column("database_name", sa.String(length=255), nullable=True),
        sa.Column("schema_name", sa.String(length=255), nullable=True),
        sa.Column("table_name", sa.String(length=255), nullable=True),
        sa.Column("select_list", sa.String(length=2048), nullable=True),
        sa.Column("compare_field", sa.String(length=255), nullable=True),
        sa.Column("polling_interval_seconds", sa.Integer(), nullable=True),
        sa.Column("start_time_local", sa.String(length=16), nullable=True),
        sa.Column("prepared_statements", sa.Boolean(), nullable=True),
        sa.Column("predefined_query", sa.String(length=128), nullable=True),
        sa.Column("eps_throttle", sa.BigInteger(), nullable=True),
        sa.Column("source_label", sa.String(length=255), nullable=True),
        sa.Column("use_ssl", sa.Boolean(), nullable=True),
        sa.Column("allow_untrusted", sa.Boolean(), nullable=True),
        sa.Column("advanced_options", sa.Boolean(), nullable=True),
        sa.Column(
            "field_states",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("allowlist_version", sa.Integer(), nullable=False),
        sa.Column("config_state", sa.String(length=24), nullable=False),
        sa.Column("failure_code", sa.String(length=48), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "protocol_type_id = 8",
            name=op.f("ck_jdbc_source_config_protocol_8"),
        ),
        sa.CheckConstraint(
            "database_port IS NULL OR database_port BETWEEN 1 AND 65535",
            name=op.f("ck_jdbc_source_config_port_range"),
        ),
        sa.CheckConstraint(
            "polling_interval_seconds IS NULL OR polling_interval_seconds BETWEEN 1 AND 604800",
            name=op.f("ck_jdbc_source_config_interval_range"),
        ),
        sa.CheckConstraint(
            "eps_throttle IS NULL OR eps_throttle >= 0",
            name=op.f("ck_jdbc_source_config_eps_non_negative"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(field_states) = 'object'",
            name=op.f("ck_jdbc_source_config_fields_object"),
        ),
        sa.CheckConstraint(
            "allowlist_version >= 1",
            name=op.f("ck_jdbc_source_config_version_positive"),
        ),
        sa.CheckConstraint(
            f"config_state IN ({_CONFIG_STATES})",
            name=op.f("ck_jdbc_source_config_config_state_known"),
        ),
        sa.CheckConstraint(
            _FAILURE_CODE,
            name=op.f("ck_jdbc_source_config_failure_code_fixed"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_jdbc_source_config_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("log_source_id", name=op.f("pk_jdbc_source_config")),
    )
    op.create_table(
        "jdbc_sync_state",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_complete_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_id", sa.UUID(), nullable=True),
        sa.Column("pages", sa.Integer(), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("elapsed_ms", sa.Integer(), nullable=False),
        sa.Column("response_bytes", sa.BigInteger(), nullable=True),
        sa.Column("failed_page", sa.Integer(), nullable=True),
        sa.Column("failure_code", sa.String(length=48), nullable=True),
        sa.Column("fallback_resume_id", sa.BigInteger(), nullable=True),
        sa.CheckConstraint(
            f"state IN ({_SYNC_STATES})",
            name=op.f("ck_jdbc_sync_state_state_known"),
        ),
        sa.CheckConstraint(
            "pages >= 0",
            name=op.f("ck_jdbc_sync_state_pages_non_negative"),
        ),
        sa.CheckConstraint(
            "rows >= 0",
            name=op.f("ck_jdbc_sync_state_rows_non_negative"),
        ),
        sa.CheckConstraint(
            "failures >= 0",
            name=op.f("ck_jdbc_sync_state_failures_non_negative"),
        ),
        sa.CheckConstraint(
            "elapsed_ms >= 0",
            name=op.f("ck_jdbc_sync_state_elapsed_ms_non_negative"),
        ),
        sa.CheckConstraint(
            "response_bytes IS NULL OR response_bytes >= 0",
            name=op.f("ck_jdbc_sync_state_response_bytes_non_negative"),
        ),
        sa.CheckConstraint(
            "failed_page IS NULL OR failed_page >= 0",
            name=op.f("ck_jdbc_sync_state_failed_page_non_negative"),
        ),
        sa.CheckConstraint(
            _FAILURE_CODE,
            name=op.f("ck_jdbc_sync_state_failure_code_fixed"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_jdbc_sync_state_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_jdbc_sync_state")),
    )


def downgrade() -> None:
    op.drop_table("jdbc_sync_state")
    op.drop_table("jdbc_source_config")
