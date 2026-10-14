"""Phase D: IPS collection buckets, admitted entities and cadence gate.

ADDITIVE. Phase A and WAF tables and enums are unchanged. No retention policy
is installed by the migration; retention is a bounded scheduled task.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MEASUREMENT_CHECK = "('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'FAILED', 'REJECTED')"


def upgrade() -> None:
    op.create_table(
        "ips_source_bucket",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False),
        sa.Column("total_events", sa.BigInteger(), nullable=False),
        sa.Column("total_records", sa.BigInteger(), nullable=False),
        *[
            column
            for name in ("destination_port", "destination_ip", "signature", "source_ip")
            for column in (
                sa.Column(f"{name}_measurement", sa.String(length=16), nullable=False),
                sa.Column(f"{name}_coverage_ratio", sa.Float(), nullable=True),
                sa.Column(f"{name}_coverage_lower_bound", sa.Boolean(), nullable=True),
            )
        ],
        sa.Column("port_fanout_measurement", sa.String(length=16), nullable=False),
        sa.Column("action_measurement", sa.String(length=16), nullable=False),
        sa.Column("action_coverage_ratio", sa.Float(), nullable=True),
        sa.Column("action_coverage_lower_bound", sa.Boolean(), nullable=True),
        sa.Column("source_events", sa.BigInteger(), nullable=True),
        sa.Column("distinct_sources", sa.Integer(), nullable=True),
        sa.Column("distinct_sources_lower_bound", sa.Boolean(), nullable=True),
        sa.Column("top1_events", sa.BigInteger(), nullable=True),
        sa.Column("top5_events", sa.BigInteger(), nullable=True),
        sa.Column("max_distinct_ports", sa.Integer(), nullable=True),
        sa.Column("blocked_events", sa.BigInteger(), nullable=True),
        sa.Column("allowed_events", sa.BigInteger(), nullable=True),
        sa.Column("unmapped_events", sa.BigInteger(), nullable=True),
        sa.Column("contributors", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("admitted", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "below_admission_threshold", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("rejected_over_quota", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("untracked_entities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("untracked_events", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("admission_truncated", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("normalizer_version", sa.Integer(), nullable=False),
        sa.Column("query_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("in_maintenance", sa.Boolean(), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "bucket_seconds > 0", name=op.f("ck_ips_source_bucket_bucket_seconds_positive")
        ),
        sa.CheckConstraint(
            "total_events >= 0", name=op.f("ck_ips_source_bucket_total_events_non_negative")
        ),
        sa.CheckConstraint(
            "total_records >= 0", name=op.f("ck_ips_source_bucket_total_records_non_negative")
        ),
        sa.CheckConstraint(
            "jsonb_array_length(COALESCE(contributors->'SOURCE_IP', '[]'::jsonb)) <= 10 "
            "AND jsonb_array_length(COALESCE(contributors->'PORT_FANOUT', '[]'::jsonb)) <= 10",
            name=op.f("ck_ips_source_bucket_contributors_bounded"),
        ),
        *[
            sa.CheckConstraint(
                f"{name} IS NULL OR {name} >= 0",
                name=op.f(f"ck_ips_source_bucket_{name}_non_negative"),
            )
            for name in (
                "source_events", "distinct_sources", "top1_events", "top5_events",
                "max_distinct_ports", "blocked_events", "allowed_events", "unmapped_events",
            )
        ],
        *[
            sa.CheckConstraint(
                f"{name}_measurement IN {MEASUREMENT_CHECK}",
                name=op.f(f"ck_ips_source_bucket_{name}_measurement_known"),
            )
            for name in (
                "destination_port", "destination_ip", "signature", "source_ip",
                "port_fanout", "action",
            )
        ],
        *[
            sa.CheckConstraint(
                f"{name}_coverage_ratio IS NULL OR "
                f"({name}_coverage_ratio >= 0.0 AND {name}_coverage_ratio <= 1.0)",
                name=op.f(f"ck_ips_source_bucket_{name}_coverage_ratio_range"),
            )
            for name in ("destination_port", "destination_ip", "signature", "source_ip", "action")
        ],
        sa.ForeignKeyConstraint(
            ["log_source_id"], ["log_source.id"],
            name=op.f("fk_ips_source_bucket_log_source_id_log_source"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id", "bucket_start", name=op.f("pk_ips_source_bucket")
        ),
    )
    op.create_table(
        "ips_entity",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("dimension", sa.String(length=32), nullable=False),
        sa.Column("value_key", sa.String(length=255), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("normalizer_version", sa.Integer(), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.CheckConstraint(
            "dimension IN ('DESTINATION_PORT', 'DESTINATION_IP', 'SIGNATURE')",
            name=op.f("ck_ips_entity_dimension_entity_known"),
        ),
        sa.CheckConstraint(
            "length(value_key) > 0", name=op.f("ck_ips_entity_value_key_not_empty")
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["qradar_instance.id"],
            name=op.f("fk_ips_entity_instance_id_qradar_instance"), ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"], ["log_source.id"],
            name=op.f("fk_ips_entity_log_source_id_log_source"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_entity")),
        sa.UniqueConstraint(
            "instance_id", "log_source_id", "dimension", "value_key",
            name="uq_ips_entity_natural_key",
        ),
    )
    op.create_index(
        "ix_ips_entity_active", "ips_entity", ["log_source_id", "dimension", "active"]
    )
    op.create_table(
        "ips_entity_metric",
        sa.Column("ips_entity_id", sa.UUID(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events", sa.BigInteger(), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "events >= 0", name=op.f("ck_ips_entity_metric_events_non_negative")
        ),
        sa.CheckConstraint(
            "bucket_seconds > 0", name=op.f("ck_ips_entity_metric_bucket_seconds_positive")
        ),
        sa.ForeignKeyConstraint(
            ["ips_entity_id"], ["ips_entity.id"],
            name=op.f("fk_ips_entity_metric_ips_entity_id_ips_entity"), ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "ips_entity_id", "bucket_start", name=op.f("pk_ips_entity_metric")
        ),
    )
    _create_hypertable("ips_source_bucket")
    _create_hypertable("ips_entity_metric")
    op.execute(
        sa.text(
            "INSERT INTO search_cadence (family, cadence_seconds, updated_at) "
            "VALUES ('IPS_COLLECTION', 3600, now()) ON CONFLICT (family) DO NOTHING"
        )
    )


def _create_hypertable(table: str) -> None:
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    ).scalar()
    if not available:
        return
    op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
    op.execute(
        sa.text(
            "SELECT create_hypertable(:table, 'bucket_start', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        ).bindparams(table=table)
    )


def downgrade() -> None:
    op.execute("DELETE FROM search_cadence WHERE family = 'IPS_COLLECTION'")
    op.drop_table("ips_entity_metric")
    op.drop_index("ix_ips_entity_active", table_name="ips_entity")
    op.drop_table("ips_entity")
    op.drop_table("ips_source_bucket")
