"""D-42: QRadar health in detail, the QRADAR_HEALTH search family and its stored windows.

ADDITIVE. Creates four hypertables: `qradar_health_run` (what each part of a collected window
established), `qradar_health_metric_bucket` (max / average / samples per host, metric and
mount), `qradar_health_notice_bucket` (the busiest system-notification groups and one
counted remainder) and `qradar_health_rest_sample` (the REST half, one row per console per
run). It seeds the cadence row of the new family (15 min) and lets `ariel_search_run.caller`
hold the new caller `QRADAR_HEALTH`. Phase A, WAF, IPS, catch-all and geography tables and
enums are unchanged.

The downgrade drops the four tables, the cadence row, and the load records of the new caller
(which the older caller check would refuse), then restores the older check.

Revision id 0050, chained after 0049, the geography bucket provenance.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_CALLERS = (
    "'LOG_SOURCE_VOLUME', 'ANOMALY_EXPLANATION', 'RULE_MATCH', 'WAF_COLLECTION', "
    "'IPS_COLLECTION', 'PROPERTY_WATCH', 'CATCHALL_SENDERS', 'GEO_COLLECTION', "
    "'HISTORY', 'PREVIEW', 'SCHEDULED_SEARCH', 'MANUAL_RUN'"
)
_NEW_CALLERS = (
    "'LOG_SOURCE_VOLUME', 'ANOMALY_EXPLANATION', 'RULE_MATCH', 'WAF_COLLECTION', "
    "'IPS_COLLECTION', 'PROPERTY_WATCH', 'CATCHALL_SENDERS', 'GEO_COLLECTION', "
    "'QRADAR_HEALTH', 'HISTORY', 'PREVIEW', 'SCHEDULED_SEARCH', 'MANUAL_RUN'"
)
_STATES = "'OK', 'PARTIAL', 'NOT_COLLECTED', 'FAILED'"
_TABLES = (
    ("qradar_health_run", "window_start"),
    ("qradar_health_metric_bucket", "bucket_start"),
    ("qradar_health_notice_bucket", "bucket_start"),
    ("qradar_health_rest_sample", "slot_start"),
)


def _instance_fk(table: str) -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(
        ["instance_id"],
        ["qradar_instance.id"],
        name=op.f(f"fk_{table}_instance_id_qradar_instance"),
        ondelete="CASCADE",
    )


def _timestamp(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table(
        "qradar_health_run",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        _timestamp("window_start"),
        _timestamp("window_end"),
        sa.Column("cadence_seconds", sa.Integer(), nullable=False),
        _timestamp("collected_at"),
        sa.Column("query_version", sa.Integer(), nullable=False),
        sa.Column("metrics_state", sa.String(length=16), nullable=False),
        sa.Column("metrics_reason_code", sa.String(length=96), nullable=True),
        sa.Column("metrics_reason_params", postgresql.JSONB(), nullable=True),
        sa.Column("notifications_state", sa.String(length=16), nullable=False),
        sa.Column("notifications_reason_code", sa.String(length=96), nullable=True),
        sa.Column("notifications_reason_params", postgresql.JSONB(), nullable=True),
        sa.Column("unparseable_hosts", sa.Integer(), nullable=False),
        sa.Column("dropped_mounts", sa.Integer(), nullable=False),
        sa.Column("dropped_metrics", sa.Integer(), nullable=False),
        sa.Column("null_values", sa.Integer(), nullable=False),
        sa.Column("invalid_values", sa.Integer(), nullable=False),
        sa.Column("dropped_events", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            f"metrics_state IN ({_STATES})", name=op.f("ck_qradar_health_run_metrics_state_known")
        ),
        sa.CheckConstraint(
            f"notifications_state IN ({_STATES})",
            name=op.f("ck_qradar_health_run_notifications_state_known"),
        ),
        sa.CheckConstraint(
            "window_end > window_start", name=op.f("ck_qradar_health_run_window_ordered")
        ),
        sa.CheckConstraint(
            "cadence_seconds > 0", name=op.f("ck_qradar_health_run_cadence_positive")
        ),
        sa.CheckConstraint(
            "unparseable_hosts >= 0 AND dropped_mounts >= 0 AND dropped_metrics >= 0 "
            "AND null_values >= 0 AND invalid_values >= 0 AND dropped_events >= 0",
            name=op.f("ck_qradar_health_run_counters_non_negative"),
        ),
        _instance_fk("qradar_health_run"),
        sa.PrimaryKeyConstraint("instance_id", "window_start", name=op.f("pk_qradar_health_run")),
    )

    op.create_table(
        "qradar_health_metric_bucket",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        _timestamp("bucket_start"),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("metric", sa.String(length=64), nullable=False),
        sa.Column("element", sa.String(length=255), nullable=False),
        _timestamp("bucket_end"),
        sa.Column("max_value", postgresql.DOUBLE_PRECISION(), nullable=False),
        sa.Column("avg_value", postgresql.DOUBLE_PRECISION(), nullable=True),
        sa.Column("samples", sa.BigInteger(), nullable=False),
        sa.CheckConstraint(
            "bucket_end > bucket_start",
            name=op.f("ck_qradar_health_metric_bucket_window_ordered"),
        ),
        sa.CheckConstraint(
            "max_value >= 0", name=op.f("ck_qradar_health_metric_bucket_max_value_non_negative")
        ),
        sa.CheckConstraint(
            "samples >= 0", name=op.f("ck_qradar_health_metric_bucket_samples_non_negative")
        ),
        _instance_fk("qradar_health_metric_bucket"),
        sa.PrimaryKeyConstraint(
            "instance_id",
            "bucket_start",
            "host",
            "metric",
            "element",
            name=op.f("pk_qradar_health_metric_bucket"),
        ),
    )

    op.create_table(
        "qradar_health_notice_bucket",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        _timestamp("bucket_start"),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("host", sa.String(length=255), nullable=False),
        sa.Column("qid_name", sa.String(length=255), nullable=False),
        _timestamp("bucket_end"),
        sa.Column("events", sa.BigInteger(), nullable=False),
        sa.Column("groups", sa.Integer(), nullable=False),
        sa.Column("max_severity", sa.SmallInteger(), nullable=True),
        _timestamp("last_seen", nullable=True),
        sa.CheckConstraint(
            "kind IN ('GROUP', 'REMAINDER')",
            name=op.f("ck_qradar_health_notice_bucket_kind_known"),
        ),
        sa.CheckConstraint(
            "kind = 'GROUP' OR (host = '' AND qid_name = '')",
            name=op.f("ck_qradar_health_notice_bucket_remainder_has_no_name"),
        ),
        sa.CheckConstraint(
            "kind = 'REMAINDER' OR host <> ''",
            name=op.f("ck_qradar_health_notice_bucket_group_has_host"),
        ),
        sa.CheckConstraint(
            "bucket_end > bucket_start",
            name=op.f("ck_qradar_health_notice_bucket_window_ordered"),
        ),
        sa.CheckConstraint(
            "events >= 0 AND groups >= 1",
            name=op.f("ck_qradar_health_notice_bucket_counts_valid"),
        ),
        sa.CheckConstraint(
            "max_severity IS NULL OR max_severity BETWEEN 0 AND 10",
            name=op.f("ck_qradar_health_notice_bucket_severity_range"),
        ),
        _instance_fk("qradar_health_notice_bucket"),
        sa.PrimaryKeyConstraint(
            "instance_id",
            "bucket_start",
            "kind",
            "host",
            "qid_name",
            name=op.f("pk_qradar_health_notice_bucket"),
        ),
    )

    op.create_table(
        "qradar_health_rest_sample",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        _timestamp("slot_start"),
        _timestamp("sampled_at"),
        sa.Column("reachable", sa.Boolean(), nullable=True),
        sa.Column("api_latency_ms", sa.Integer(), nullable=True),
        sa.Column("hosts_active", sa.Integer(), nullable=True),
        sa.Column("hosts_total", sa.Integer(), nullable=True),
        sa.Column("ha_problem_count", sa.Integer(), nullable=True),
        sa.Column("license_overallocated", sa.Boolean(), nullable=True),
        sa.CheckConstraint(
            "api_latency_ms IS NULL OR api_latency_ms >= 0",
            name=op.f("ck_qradar_health_rest_sample_latency_non_negative"),
        ),
        sa.CheckConstraint(
            "(hosts_active IS NULL) = (hosts_total IS NULL) "
            "AND (hosts_active IS NULL OR (hosts_active >= 0 AND hosts_active <= hosts_total))",
            name=op.f("ck_qradar_health_rest_sample_hosts_consistent"),
        ),
        sa.CheckConstraint(
            "ha_problem_count IS NULL OR ha_problem_count >= 0",
            name=op.f("ck_qradar_health_rest_sample_ha_problems_non_negative"),
        ),
        _instance_fk("qradar_health_rest_sample"),
        sa.PrimaryKeyConstraint(
            "instance_id", "slot_start", name=op.f("pk_qradar_health_rest_sample")
        ),
    )
    for table, column in _TABLES:
        _create_hypertable(table, column)

    op.execute(
        sa.text(
            "INSERT INTO search_cadence (family, cadence_seconds, updated_at) "
            "VALUES ('QRADAR_HEALTH', 900, now()) ON CONFLICT (family) DO NOTHING"
        )
    )
    _set_callers(_NEW_CALLERS)


def _create_hypertable(table: str, column: str) -> None:
    """Register a table with Timescale, when the extension exists.

    Each primary key already includes its time column, which Timescale requires of a
    partitioning column.
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
            "SELECT create_hypertable(:t, :c, if_not_exists => TRUE, migrate_data => TRUE)"
        ).bindparams(t=table, c=column)
    )


def _set_callers(callers: str) -> None:
    op.drop_constraint(
        op.f("ck_ariel_search_run_caller_known"), "ariel_search_run", type_="check"
    )
    op.create_check_constraint(
        op.f("ck_ariel_search_run_caller_known"),
        "ariel_search_run",
        f"caller IN ({callers})",
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM ariel_search_run WHERE caller = 'QRADAR_HEALTH'"))
    _set_callers(_OLD_CALLERS)
    op.execute(sa.text("DELETE FROM search_cadence WHERE family = 'QRADAR_HEALTH'"))
    for table, _column in reversed(_TABLES):
        op.drop_table(table)
