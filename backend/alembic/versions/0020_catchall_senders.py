"""Phase C.2b: catch-all senders and the CATCHALL_SENDERS search family.

ADDITIVE. Creates `catchall_sender` and the `catchall_bucket` hypertable, and seeds
the cadence row of the new search family (1 h). Phase A, WAF and IPS tables and
enums are unchanged.

Revision id 0020, chained after 0019, the outage alert policy.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "catchall_sender",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("catchall_log_source_id", sa.UUID(), nullable=False),
        sa.Column("identifier", sa.String(length=255), nullable=False),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("events_24h", sa.BigInteger(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_status", sa.String(length=16), nullable=False),
        sa.Column("reviewed_by", sa.String(length=255), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
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
            "review_status IN ('NEW', 'REVIEWED', 'DISMISSED')",
            name=op.f("ck_catchall_sender_review_status_known"),
        ),
        sa.CheckConstraint(
            "char_length(identifier) BETWEEN 1 AND 255",
            name=op.f("ck_catchall_sender_identifier_length"),
        ),
        sa.CheckConstraint(
            "events_24h IS NULL OR events_24h >= 0",
            name=op.f("ck_catchall_sender_events_24h_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_catchall_sender_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["catchall_log_source_id"],
            ["log_source.id"],
            name=op.f("fk_catchall_sender_catchall_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_catchall_sender")),
        sa.UniqueConstraint(
            "instance_id",
            "catchall_log_source_id",
            "identifier",
            name="uq_catchall_sender_key",
        ),
    )
    op.create_index(
        "ix_catchall_sender_review", "catchall_sender", ["instance_id", "review_status"]
    )
    op.create_index("ix_catchall_sender_source", "catchall_sender", ["catchall_log_source_id"])

    op.create_table(
        "catchall_bucket",
        sa.Column("catchall_log_source_id", sa.UUID(), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_seconds", sa.Integer(), nullable=False),
        sa.Column("events", sa.BigInteger(), nullable=False),
        sa.Column("records", sa.BigInteger(), nullable=False),
        sa.Column("distinct_senders", sa.Integer(), nullable=False),
        sa.Column("distinct_senders_lower_bound", sa.Boolean(), nullable=False),
        sa.Column("measurement", sa.String(length=16), nullable=False),
        sa.Column("top_senders", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("admitted", sa.Integer(), nullable=False),
        sa.Column("below_admission_threshold", sa.Integer(), nullable=False),
        sa.Column("rejected_over_quota", sa.Integer(), nullable=False),
        sa.Column("untracked_senders", sa.Integer(), nullable=False),
        sa.Column("untracked_events", sa.BigInteger(), nullable=False),
        sa.Column("query_provenance", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("collected_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "measurement IN ('EXACT', 'LOWER_BOUND')",
            name=op.f("ck_catchall_bucket_measurement_known"),
        ),
        sa.CheckConstraint(
            "bucket_seconds > 0", name=op.f("ck_catchall_bucket_bucket_seconds_positive")
        ),
        sa.CheckConstraint(
            "events >= 0 AND records >= 0 AND distinct_senders >= 0 "
            "AND admitted >= 0 AND below_admission_threshold >= 0 "
            "AND rejected_over_quota >= 0 AND untracked_senders >= 0 "
            "AND untracked_events >= 0",
            name=op.f("ck_catchall_bucket_counts_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["catchall_log_source_id"],
            ["log_source.id"],
            name=op.f("fk_catchall_bucket_catchall_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "catchall_log_source_id", "bucket_start", name=op.f("pk_catchall_bucket")
        ),
    )
    _create_hypertable()

    op.execute(
        sa.text(
            "INSERT INTO search_cadence (family, cadence_seconds, updated_at) "
            "VALUES ('CATCHALL_SENDERS', 3600, now()) ON CONFLICT (family) DO NOTHING"
        )
    )


def _create_hypertable() -> None:
    """Register catchall_bucket with Timescale, when the extension exists.

    The composite primary key already includes bucket_start, which Timescale
    requires of a partitioning column.
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
            "SELECT create_hypertable(:t, :c, "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        ).bindparams(t="catchall_bucket", c="bucket_start")
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM search_cadence WHERE family = 'CATCHALL_SENDERS'"))
    op.drop_table("catchall_bucket")
    op.drop_index("ix_catchall_sender_source", table_name="catchall_sender")
    op.drop_index("ix_catchall_sender_review", table_name="catchall_sender")
    op.drop_table("catchall_sender")
