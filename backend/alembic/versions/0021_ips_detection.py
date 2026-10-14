"""Phase D IPS detector persistence and seasonal baselines.

ADDITIVE. Creates ordinary PostgreSQL aggregate/state tables; none is a
Timescale hypertable. Phase A and WAF tables and enums are unchanged. IPS
evaluation watermarks reuse ``collection_watermark`` with a per-source
collector key, exactly as the WAF pipeline does, so no watermark schema change
is needed.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SUBJECT_CHECK = (
    "(subject_kind = 'IPS_ENTITY' AND ips_entity_id IS NOT NULL "
    "AND log_source_id IS NULL) OR "
    "(subject_kind = 'IPS_SOURCE' AND ips_entity_id IS NULL "
    "AND log_source_id IS NOT NULL)"
)


def _timestamps() -> list[sa.Column]:
    return [
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
    ]


def _cell_columns(*, pooled: bool) -> list[sa.Column]:
    columns = [
        sa.Column("metric_name", sa.String(length=64), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("hour", sa.Integer(), nullable=False),
        sa.Column("median", sa.Float(), nullable=False),
        sa.Column("mad", sa.Float(), nullable=False),
        sa.Column("p05", sa.Float(), nullable=True),
        sa.Column("p95", sa.Float(), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("is_reliable", sa.Boolean(), nullable=False),
        sa.Column("completeness", sa.Float(), nullable=False),
        sa.Column("observations", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("window_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("baseline_version", sa.Integer(), nullable=False),
        sa.Column("excluded_sample_count", sa.Integer(), nullable=False),
        sa.Column("exclusion_counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metric_semantics", sa.String(length=160), nullable=True),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("normalizer_version", sa.Integer(), nullable=False),
    ]
    if pooled:
        columns.extend(
            (
                sa.Column("numerator_sum", sa.Float(), nullable=True),
                sa.Column("denominator_sum", sa.Float(), nullable=True),
            )
        )
    return columns


def upgrade() -> None:
    op.create_table(
        "ips_entity_baseline",
        sa.Column("ips_entity_id", sa.UUID(), nullable=False),
        *_cell_columns(pooled=False),
        *_timestamps(),
        sa.CheckConstraint(
            "weekday BETWEEN 1 AND 7", name=op.f("ck_ips_entity_baseline_weekday_range")
        ),
        sa.CheckConstraint(
            "hour BETWEEN 0 AND 23", name=op.f("ck_ips_entity_baseline_hour_range")
        ),
        sa.ForeignKeyConstraint(
            ["ips_entity_id"],
            ["ips_entity.id"],
            name=op.f("fk_ips_entity_baseline_ips_entity_id_ips_entity"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_entity_baseline")),
        sa.UniqueConstraint(
            "ips_entity_id",
            "metric_name",
            "weekday",
            "hour",
            name="uq_ips_entity_baseline_cell",
        ),
    )
    op.create_table(
        "ips_source_baseline",
        sa.Column("log_source_id", sa.UUID(), nullable=False),
        *_cell_columns(pooled=True),
        *_timestamps(),
        sa.CheckConstraint(
            "weekday BETWEEN 1 AND 7", name=op.f("ck_ips_source_baseline_weekday_range")
        ),
        sa.CheckConstraint(
            "hour BETWEEN 0 AND 23", name=op.f("ck_ips_source_baseline_hour_range")
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_ips_source_baseline_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_source_baseline")),
        sa.UniqueConstraint(
            "log_source_id",
            "metric_name",
            "weekday",
            "hour",
            name="uq_ips_source_baseline_cell",
        ),
    )
    op.create_table(
        "ips_detector_state",
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("ips_entity_id", sa.UUID(), nullable=True),
        sa.Column("log_source_id", sa.UUID(), nullable=True),
        sa.Column("detector_type", sa.String(length=40), nullable=False),
        sa.Column("consecutive_anomalous", sa.Integer(), nullable=False),
        sa.Column("consecutive_healthy", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("last_interval_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("active_anomaly_id", sa.UUID(), nullable=True),
        sa.Column("open_anomaly_id", sa.UUID(), nullable=True),
        *_timestamps(),
        sa.CheckConstraint(
            _SUBJECT_CHECK, name=op.f("ck_ips_detector_state_subject_matches_kind")
        ),
        sa.ForeignKeyConstraint(
            ["ips_entity_id"],
            ["ips_entity.id"],
            name=op.f("fk_ips_detector_state_ips_entity_id_ips_entity"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_ips_detector_state_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_detector_state")),
    )
    op.create_index(
        "uq_ips_detector_state_entity",
        "ips_detector_state",
        ["ips_entity_id", "detector_type"],
        unique=True,
        postgresql_where=sa.text("subject_kind = 'IPS_ENTITY'"),
    )
    op.create_index(
        "uq_ips_detector_state_source",
        "ips_detector_state",
        ["log_source_id", "detector_type"],
        unique=True,
        postgresql_where=sa.text("subject_kind = 'IPS_SOURCE'"),
    )
    op.create_table(
        "ips_anomaly",
        sa.Column("subject_kind", sa.String(length=16), nullable=False),
        sa.Column("ips_entity_id", sa.UUID(), nullable=True),
        sa.Column("log_source_id", sa.UUID(), nullable=True),
        sa.Column("detector_type", sa.String(length=40), nullable=False),
        sa.Column("fingerprint", sa.String(length=255), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anomaly_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anomaly_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("deviation_score", sa.Float(), nullable=True),
        sa.Column("deviation_ratio", sa.Float(), nullable=True),
        sa.Column("robust_z", sa.Float(), nullable=True),
        sa.Column("absolute_delta", sa.Float(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("consecutive_buckets", sa.Integer(), nullable=False),
        sa.Column("baseline_version", sa.Integer(), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("profile_version", sa.Integer(), nullable=False),
        sa.Column("normalizer_version", sa.Integer(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence_status", sa.String(length=16), nullable=False),
        sa.Column("suppressed", sa.Boolean(), nullable=False),
        sa.Column("suppression_reason", sa.String(length=255), nullable=True),
        sa.Column("observed_measurement", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(_SUBJECT_CHECK, name=op.f("ck_ips_anomaly_subject_matches_kind")),
        sa.ForeignKeyConstraint(
            ["ips_entity_id"],
            ["ips_entity.id"],
            name=op.f("fk_ips_anomaly_ips_entity_id_ips_entity"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["log_source_id"],
            ["log_source.id"],
            name=op.f("fk_ips_anomaly_log_source_id_log_source"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_anomaly")),
    )
    op.create_index("ix_ips_anomaly_detected", "ips_anomaly", ["detected_at"])
    op.create_index(
        "ix_ips_anomaly_state",
        "ips_anomaly",
        ["ips_entity_id", "log_source_id", "detector_type", "state"],
    )
    op.create_table(
        "ips_anomaly_transition",
        sa.Column("ips_anomaly_id", sa.UUID(), nullable=False),
        sa.Column("from_state", sa.String(length=24), nullable=True),
        sa.Column("to_state", sa.String(length=24), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("bucket_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("observed_value", sa.Float(), nullable=True),
        sa.Column("expected_value", sa.Float(), nullable=True),
        sa.Column("observed_measurement", sa.String(length=16), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["ips_anomaly_id"],
            ["ips_anomaly.id"],
            name=op.f("fk_ips_anomaly_transition_ips_anomaly_id_ips_anomaly"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ips_anomaly_transition")),
    )
    op.create_index(
        "ix_ips_anomaly_transition",
        "ips_anomaly_transition",
        ["ips_anomaly_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ips_anomaly_transition", table_name="ips_anomaly_transition")
    op.drop_table("ips_anomaly_transition")
    op.drop_index("ix_ips_anomaly_state", table_name="ips_anomaly")
    op.drop_index("ix_ips_anomaly_detected", table_name="ips_anomaly")
    op.drop_table("ips_anomaly")
    op.drop_index("uq_ips_detector_state_source", table_name="ips_detector_state")
    op.drop_index("uq_ips_detector_state_entity", table_name="ips_detector_state")
    op.drop_table("ips_detector_state")
    op.drop_table("ips_source_baseline")
    op.drop_table("ips_entity_baseline")
