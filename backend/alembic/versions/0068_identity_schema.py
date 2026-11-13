"""SOC-10a: identity profiles and the per-bucket aggregates the fold may store.

ADDITIVE. Phase A tables and enums are unchanged. `identity_bucket`,
`identity_event_count` and `identity_top_username` are TimescaleDB hypertables
on `bucket_start`. No retention or compression policy is installed; counts and
the top-N are kept by a later bounded delete. Anomaly tables are a later slice.
String CHECKs only: no PostgreSQL enum.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0068"
down_revision: str | None = "0067"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HYPERTABLES = ("identity_bucket", "identity_event_count", "identity_top_username")


def upgrade() -> None:
    op.create_table(
        "identity_bucket",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False),
        sa.Column("catalog_version", sa.Integer(), nullable=False),
        sa.Column("measurement", sa.String(length=16), nullable=False),
        sa.Column("population", sa.BigInteger(), nullable=True),
        sa.Column("admission_truncated", sa.Boolean(), nullable=False),
        sa.Column("query_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "(measurement <> 'NOT_COLLECTED' OR population IS NULL) "
            "AND (measurement <> 'EXACT' OR population IS NOT NULL) "
            "AND (population IS NULL OR population >= 0)",
            name=op.f("ck_identity_bucket_population_honest"),
        ),
        sa.CheckConstraint(
            "measurement IN ('EXACT', 'TRUNCATED', 'NOT_COLLECTED')",
            name=op.f("ck_identity_bucket_measurement_known"),
        ),
        sa.CheckConstraint(
            "bucket_seconds > 0",
            name=op.f("ck_identity_bucket_bucket_seconds_positive"),
        ),
        sa.CheckConstraint(
            "catalog_version >= 1",
            name=op.f("ck_identity_bucket_catalog_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_identity_bucket_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id", "bucket_start", name=op.f("pk_identity_bucket")
        ),
    )
    op.create_table(
        "identity_event_count",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("event_count", sa.BigInteger(), nullable=True),
        sa.Column("measurement", sa.String(length=16), nullable=False),
        sa.Column("names_measurement", sa.String(length=16), nullable=False),
        sa.Column("admission_truncated", sa.Boolean(), nullable=False),
        sa.CheckConstraint(
            "(measurement <> 'NOT_COLLECTED' OR event_count IS NULL) "
            "AND (measurement <> 'EXACT' OR event_count IS NOT NULL) "
            "AND (event_count IS NULL OR event_count >= 0)",
            name=op.f("ck_identity_event_count_count_honest"),
        ),
        sa.CheckConstraint(
            "measurement IN ('EXACT', 'TRUNCATED', 'NOT_COLLECTED')",
            name=op.f("ck_identity_event_count_measurement_known"),
        ),
        sa.CheckConstraint(
            "names_measurement <> 'NOT_COLLECTED' OR NOT admission_truncated",
            name=op.f("ck_identity_event_count_unrun_names_not_truncated"),
        ),
        sa.CheckConstraint(
            "names_measurement IN ('EXACT', 'TRUNCATED', 'NOT_COLLECTED')",
            name=op.f("ck_identity_event_count_names_measurement_known"),
        ),
        sa.CheckConstraint(
            "event_id IN (4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102)",
            name=op.f("ck_identity_event_count_event_id_known"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_identity_event_count_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id",
            "event_id",
            "bucket_start",
            name=op.f("pk_identity_event_count"),
        ),
    )
    op.create_table(
        "identity_source_profile",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("product_label", sa.String(length=128), nullable=True),
        sa.Column("event_id_property", sa.String(length=64), nullable=False),
        sa.Column("username_property", sa.String(length=64), nullable=False),
        sa.Column("logon_type_property", sa.String(length=64), nullable=True),
        sa.Column("enabled_event_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("validated_profile_version", sa.Integer(), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_report", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("validation_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("validation_requested_by", sa.String(length=255), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
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
            "jsonb_typeof(enabled_event_ids) = 'array'",
            name=op.f("ck_identity_source_profile_enabled_event_ids_array"),
        ),
        sa.CheckConstraint(
            "validation_status IN ('UNVALIDATED', 'VALID', 'INVALID', 'INCONCLUSIVE')",
            name=op.f("ck_identity_source_profile_validation_status_known"),
        ),
        sa.CheckConstraint(
            "length(event_id_property) > 0",
            name=op.f("ck_identity_source_profile_event_id_property_not_empty"),
        ),
        sa.CheckConstraint(
            "length(username_property) > 0",
            name=op.f("ck_identity_source_profile_username_property_not_empty"),
        ),
        sa.CheckConstraint(
            "logon_type_property IS NULL OR length(logon_type_property) > 0",
            name=op.f("ck_identity_source_profile_logon_type_property_not_empty"),
        ),
        sa.CheckConstraint(
            "profile_version >= 1",
            name=op.f("ck_identity_source_profile_profile_version_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_identity_source_profile_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_identity_source_profile_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_identity_source_profile")),
        sa.UniqueConstraint(
            "log_source_id", name=op.f("uq_identity_source_profile_log_source_id")
        ),
    )
    op.create_table(
        "identity_top_username",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        sa.Column("event_id", sa.Integer(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(length=255), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("event_count", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "char_length(username) <= 255",
            name=op.f("ck_identity_top_username_username_width"),
        ),
        sa.CheckConstraint(
            "event_count >= 1 AND (event_id NOT IN (4625, 4740) OR event_count >= 3)",
            name=op.f("ck_identity_top_username_name_admission_floor"),
        ),
        sa.CheckConstraint(
            "event_id IN (4625, 4740, 4720, 4726, 4728, 4732, 4756, 1102)",
            name=op.f("ck_identity_top_username_event_id_known"),
        ),
        sa.CheckConstraint(
            "length(username) > 0",
            name=op.f("ck_identity_top_username_username_not_empty"),
        ),
        sa.CheckConstraint(
            "rank BETWEEN 1 AND 20",
            name=op.f("ck_identity_top_username_rank_in_top_n"),
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_identity_top_username_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "log_source_id",
            "event_id",
            "bucket_start",
            "rank",
            name=op.f("pk_identity_top_username"),
        ),
    )
    for table in _HYPERTABLES:
        _create_hypertable(table)


def _create_hypertable(table: str) -> None:
    """Register a table with Timescale when the extension exists.

    The composite primary key already includes bucket_start. No retention or
    compression policy is added here.
    """
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
    op.drop_table("identity_top_username")
    op.drop_table("identity_event_count")
    op.drop_table("identity_bucket")
    op.drop_table("identity_source_profile")
