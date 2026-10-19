"""History backfill foundations: the decision, the scope state and the row marker.

ADDITIVE (HISTORY-BACKFILL §B.7.1). Revision 0030 revises 0029. Applying it starts no
history work: every existing row of the four tables is marked LIVE, no decision exists
(undecided is not Yes), and no reader changes behaviour.

* ``history_backfill_decision``: one row per console, the operator's answer.
* ``history_backfill_scope``: one row per (console, family, scope key), the reading state.
* ``origin`` on ``waf_source_bucket``, ``ips_source_bucket``, ``catchall_bucket`` and
  ``rule_match_window``. The server default ``'LIVE'`` fills existing rows and is dropped,
  so a writer must say which (the 0010 pattern). ``log_source_metric`` keeps its columns:
  Phase A marks history in ``collection_source`` (rule 6).

Three of the four tables are TimescaleDB hypertables. TimescaleDB 2.17 accepts ADD COLUMN
on a hypertable with compressed chunks but refuses ADD/DROP CHECK while compression is
enabled. The application never enables compression on them (only the four Phase A metric
tables), but an operator may have, so compression is suspended around the CHECK changes
and restored, as 0029 does for ``pw_bucket``.

Downgrade deletes the history rows first, since they cannot be told apart without the
columns: ``origin = 'HISTORY'`` rows of the four tables, the child rows that hang on such a
bucket or window (``waf_endpoint_metric``, ``ips_entity_metric``, ``rule_match_metric``),
and the Phase A ``log_source_metric`` rows read from history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The two literals of `app.history.origin.PHASE_A_HISTORY_SOURCES` (a test pins them).
PHASE_A_HISTORY_SOURCES = ("ariel_history", "mock_history")

# The marker table of each family that gains an `origin` column.
ORIGIN_TABLES = (
    "waf_source_bucket",
    "ips_source_bucket",
    "catchall_bucket",
    "rule_match_window",
)
_ORIGIN_CHECK = "origin IN ('LIVE', 'HISTORY')"
_FAMILIES = "'PHASE_A', 'WAF', 'IPS', 'CATCHALL', 'RULE_MATCH'"
_STATES = "'WAITING', 'READING', 'COMPLETE', 'PARTIAL', 'UNAVAILABLE', 'NOT_ELIGIBLE'"

CompressionState = tuple[str, list[str], str | None, str | None]


def _suspend_compression(table: str) -> CompressionState | None:
    """Make CHECK changes legal on `table`, preserving its compressed chunks."""
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar()
    if available is None:
        return None
    enabled = bind.execute(
        sa.text(
            "SELECT compression_enabled FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = current_schema() AND hypertable_name = :t"
        ),
        {"t": table},
    ).scalar()
    if not enabled:
        return None

    rows = bind.execute(
        sa.text(
            "SELECT attname, segmentby_column_index, orderby_column_index, "
            "orderby_asc, orderby_nullsfirst "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_schema = current_schema() AND hypertable_name = :t"
        ),
        {"t": table},
    ).mappings()
    segment_rows: list[tuple[int, str]] = []
    order_rows: list[tuple[int, str]] = []
    quote = bind.dialect.identifier_preparer.quote
    for row in rows:
        if row["segmentby_column_index"] is not None:
            segment_rows.append((row["segmentby_column_index"], quote(row["attname"])))
        if row["orderby_column_index"] is not None:
            direction = "ASC" if row["orderby_asc"] else "DESC"
            nulls = "NULLS FIRST" if row["orderby_nullsfirst"] else "NULLS LAST"
            order_rows.append(
                (row["orderby_column_index"], f"{quote(row['attname'])} {direction} {nulls}")
            )
    segmentby = ", ".join(value for _, value in sorted(segment_rows)) or None
    orderby = ", ".join(value for _, value in sorted(order_rows)) or None
    chunks = list(
        bind.execute(
            sa.text(
                "SELECT format('%I.%I', chunk_schema, chunk_name) "
                "FROM timescaledb_information.chunks "
                "WHERE hypertable_schema = current_schema() AND hypertable_name = :t "
                "AND is_compressed ORDER BY chunk_name"
            ),
            {"t": table},
        ).scalars()
    )
    for chunk in chunks:
        bind.execute(sa.text("SELECT decompress_chunk(CAST(:chunk AS regclass))"), {"chunk": chunk})
    op.execute(f"ALTER TABLE {table} SET (timescaledb.compress = false)")
    return table, chunks, segmentby, orderby


def _restore_compression(state: CompressionState | None) -> None:
    if state is None:
        return
    table, chunks, segmentby, orderby = state

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    options = ["timescaledb.compress = true"]
    if segmentby is not None:
        options.append(f"timescaledb.compress_segmentby = {literal(segmentby)}")
    if orderby is not None:
        options.append(f"timescaledb.compress_orderby = {literal(orderby)}")
    op.execute(f"ALTER TABLE {table} SET ({', '.join(options)})")
    bind = op.get_bind()
    for chunk in chunks:
        bind.execute(
            sa.text("SELECT compress_chunk(CAST(:chunk AS regclass), if_not_compressed => true)"),
            {"chunk": chunk},
        )


def upgrade() -> None:
    op.create_table(
        "history_backfill_decision",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("answer", sa.String(length=3), nullable=False),
        sa.Column("days", sa.Integer(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("decided_by", sa.String(length=255), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.String(length=255), nullable=True),
        sa.CheckConstraint(
            "answer IN ('YES', 'NO')", name=op.f("ck_history_backfill_decision_answer_known")
        ),
        sa.CheckConstraint(
            "source IN ('INSTALLER', 'UI')", name=op.f("ck_history_backfill_decision_source_known")
        ),
        sa.CheckConstraint(
            "(answer = 'YES') = (days IS NOT NULL)",
            name=op.f("ck_history_backfill_decision_days_iff_yes"),
        ),
        sa.CheckConstraint(
            "days IS NULL OR days >= 7", name=op.f("ck_history_backfill_decision_days_minimum")
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_history_backfill_decision_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_history_backfill_decision")),
    )
    op.create_table(
        "history_backfill_scope",
        sa.Column("instance_id", sa.UUID(), nullable=False),
        sa.Column("family", sa.String(length=16), nullable=False),
        sa.Column("scope_key", sa.String(length=64), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("days_requested", sa.Integer(), nullable=True),
        sa.Column("days_target", sa.Integer(), nullable=True),
        sa.Column("target_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("floor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("through", sa.DateTime(timezone=True), nullable=True),
        sa.Column("anchor", sa.DateTime(timezone=True), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=True),
        sa.Column("progress", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.CheckConstraint(
            f"family IN ({_FAMILIES})", name=op.f("ck_history_backfill_scope_family_known")
        ),
        sa.CheckConstraint(
            f"state IN ({_STATES})", name=op.f("ck_history_backfill_scope_state_known")
        ),
        sa.CheckConstraint(
            "(family IN ('WAF', 'IPS')) = (scope_key <> '')",
            name=op.f("ck_history_backfill_scope_scope_key_shape"),
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["qradar_instance.id"],
            name=op.f("fk_history_backfill_scope_instance_id_qradar_instance"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "instance_id", "family", "scope_key", name=op.f("pk_history_backfill_scope")
        ),
    )

    for table in ORIGIN_TABLES:
        compression = _suspend_compression(table)
        op.add_column(
            table,
            sa.Column("origin", sa.String(length=8), server_default="LIVE", nullable=False),
        )
        op.create_check_constraint(op.f(f"ck_{table}_origin_known"), table, _ORIGIN_CHECK)
        op.alter_column(table, "origin", server_default=None)
        _restore_compression(compression)


def downgrade() -> None:
    # Children first: they hang on a history bucket or window and cannot be told apart
    # once the marker is gone. Each is keyed by (parent, bucket start).
    op.execute(
        sa.text(
            "DELETE FROM waf_endpoint_metric AS metric "
            "USING waf_endpoint AS endpoint, waf_source_bucket AS bucket "
            "WHERE metric.waf_endpoint_id = endpoint.id "
            "AND bucket.log_source_id = endpoint.log_source_id "
            "AND bucket.bucket_start = metric.bucket_start AND bucket.origin = 'HISTORY'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM ips_entity_metric AS metric "
            "USING ips_entity AS entity, ips_source_bucket AS bucket "
            "WHERE metric.ips_entity_id = entity.id "
            "AND bucket.log_source_id = entity.log_source_id "
            "AND bucket.bucket_start = metric.bucket_start AND bucket.origin = 'HISTORY'"
        )
    )
    op.execute(
        sa.text(
            "DELETE FROM rule_match_metric AS metric "
            "USING analytics_rule AS rule, rule_match_window AS win "
            "WHERE metric.rule_id = rule.id AND win.instance_id = rule.instance_id "
            "AND win.window_start = metric.bucket_start AND win.origin = 'HISTORY'"
        )
    )
    for table in ORIGIN_TABLES:
        op.execute(sa.text(f"DELETE FROM {table} WHERE origin = 'HISTORY'"))
    op.execute(
        sa.text("DELETE FROM log_source_metric WHERE collection_source IN :sources").bindparams(
            sa.bindparam("sources", value=list(PHASE_A_HISTORY_SOURCES), expanding=True)
        )
    )

    for table in reversed(ORIGIN_TABLES):
        compression = _suspend_compression(table)
        op.drop_constraint(op.f(f"ck_{table}_origin_known"), table, type_="check")
        op.drop_column(table, "origin")
        _restore_compression(compression)

    op.drop_table("history_backfill_scope")
    op.drop_table("history_backfill_decision")
