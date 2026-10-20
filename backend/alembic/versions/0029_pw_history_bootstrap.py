"""Property-watch history bootstrap foundations: provenance, state and progress.

ADDITIVE. Revision 0029 revises 0028. Existing watches remain live-only and every
existing bucket is marked LIVE, so applying this migration starts no history work and
changes no reader behaviour. Server defaults used for the backfill are transient.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS_CHECK = "ck_property_watch_last_status_known"
_OLD_STATUSES = (
    "'NOT_ENABLED', 'DISABLED', 'SCOPE_EMPTY', 'SCOPE_STALE', 'SCOPE_TOO_LARGE', "
    "'NOT_APPLICABLE', 'STALE', 'OPEN', 'RECOVERING', 'OK'"
)
_NEW_STATUSES = (
    "'NOT_ENABLED', 'DISABLED', 'SCOPE_EMPTY', 'SCOPE_STALE', 'SCOPE_TOO_LARGE', "
    "'NOT_APPLICABLE', 'READING_HISTORY', 'STALE', 'OPEN', 'RECOVERING', 'OK'"
)


def _suspend_pw_bucket_compression() -> tuple[list[str], str | None, str | None] | None:
    """Make CHECK changes legal on TimescaleDB 2.x, preserving compressed chunks.

    The application does not configure compression for ``pw_bucket``, but an operator
    may have enabled it manually. TimescaleDB 2.17 accepts ADD COLUMN on that table but
    refuses ADD/DROP CHECK while compression is enabled.
    """
    bind = op.get_bind()
    available = bind.execute(
        sa.text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar()
    if available is None:
        return None
    enabled = bind.execute(
        sa.text(
            "SELECT compression_enabled FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = current_schema() AND hypertable_name = 'pw_bucket'"
        )
    ).scalar()
    if not enabled:
        return None

    rows = bind.execute(
        sa.text(
            "SELECT attname, segmentby_column_index, orderby_column_index, "
            "orderby_asc, orderby_nullsfirst "
            "FROM timescaledb_information.compression_settings "
            "WHERE hypertable_schema = current_schema() AND hypertable_name = 'pw_bucket'"
        )
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
                "WHERE hypertable_schema = current_schema() AND hypertable_name = 'pw_bucket' "
                "AND is_compressed ORDER BY chunk_name"
            )
        ).scalars()
    )
    for chunk in chunks:
        bind.execute(sa.text("SELECT decompress_chunk(CAST(:chunk AS regclass))"), {"chunk": chunk})
    op.execute("ALTER TABLE pw_bucket SET (timescaledb.compress = false)")
    return chunks, segmentby, orderby


def _restore_pw_bucket_compression(
    state: tuple[list[str], str | None, str | None] | None,
) -> None:
    if state is None:
        return
    chunks, segmentby, orderby = state

    def literal(value: str) -> str:
        return "'" + value.replace("'", "''") + "'"

    options = ["timescaledb.compress = true"]
    if segmentby is not None:
        options.append(f"timescaledb.compress_segmentby = {literal(segmentby)}")
    if orderby is not None:
        options.append(f"timescaledb.compress_orderby = {literal(orderby)}")
    op.execute(f"ALTER TABLE pw_bucket SET ({', '.join(options)})")
    bind = op.get_bind()
    for chunk in chunks:
        bind.execute(
            sa.text(
                "SELECT compress_chunk(CAST(:chunk AS regclass), if_not_compressed => true)"
            ),
            {"chunk": chunk},
        )


def upgrade() -> None:
    op.add_column("property_watch", sa.Column("history_days", sa.Integer(), nullable=True))
    op.add_column(
        "property_watch",
        sa.Column(
            "history_state",
            sa.String(length=16),
            server_default="NOT_REQUESTED",
            nullable=False,
        ),
    )
    for name in (
        "history_target_start",
        "history_floor",
        "history_through",
        "history_end",
        "history_started_at",
        "history_finished_at",
    ):
        op.add_column(
            "property_watch", sa.Column(name, sa.DateTime(timezone=True), nullable=True)
        )
    op.add_column(
        "property_watch",
        sa.Column(
            "history_progress",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        op.f("ck_property_watch_history_state_known"),
        "property_watch",
        "history_state IN ('NOT_REQUESTED', 'READING', 'COMPLETE', 'PARTIAL', 'UNAVAILABLE')",
    )
    op.create_check_constraint(
        op.f("ck_property_watch_history_days_range"),
        "property_watch",
        "history_days IS NULL OR history_days >= 0",
    )

    compression = _suspend_pw_bucket_compression()
    op.add_column(
        "pw_bucket",
        sa.Column("origin", sa.String(length=8), server_default="LIVE", nullable=False),
    )
    op.create_check_constraint(
        op.f("ck_pw_bucket_origin_known"),
        "pw_bucket",
        "origin IN ('LIVE', 'HISTORY')",
    )

    op.drop_constraint(op.f(_STATUS_CHECK), "property_watch", type_="check")
    op.create_check_constraint(
        op.f(_STATUS_CHECK),
        "property_watch",
        f"last_status IS NULL OR last_status IN ({_NEW_STATUSES})",
    )

    op.alter_column("property_watch", "history_state", server_default=None)
    op.alter_column("property_watch", "history_progress", server_default=None)
    op.alter_column("pw_bucket", "origin", server_default=None)
    _restore_pw_bucket_compression(compression)


def downgrade() -> None:
    compression = _suspend_pw_bucket_compression()
    # Metrics do not FK to buckets. Remove those aligned with HISTORY buckets first.
    op.execute(
        sa.text(
            "DELETE FROM pw_value_metric AS metric USING pw_value AS value, pw_bucket AS bucket "
            "WHERE metric.pw_value_id = value.id AND bucket.watch_id = value.watch_id "
            "AND bucket.bucket_start = metric.bucket_start AND bucket.origin = 'HISTORY'"
        )
    )
    op.execute(sa.text("DELETE FROM pw_bucket WHERE origin = 'HISTORY'"))

    op.drop_constraint(op.f("ck_pw_bucket_origin_known"), "pw_bucket", type_="check")
    op.drop_column("pw_bucket", "origin")

    op.drop_constraint(op.f("ck_property_watch_history_days_range"), "property_watch", type_="check")
    op.drop_constraint(
        op.f("ck_property_watch_history_state_known"), "property_watch", type_="check"
    )
    op.drop_column("property_watch", "history_progress")
    for name in (
        "history_finished_at",
        "history_started_at",
        "history_end",
        "history_through",
        "history_floor",
        "history_target_start",
    ):
        op.drop_column("property_watch", name)
    op.drop_column("property_watch", "history_state")
    op.drop_column("property_watch", "history_days")

    op.drop_constraint(op.f(_STATUS_CHECK), "property_watch", type_="check")
    op.execute(
        sa.text(
            "UPDATE property_watch SET last_status = NULL "
            "WHERE last_status = 'READING_HISTORY'"
        )
    )
    op.create_check_constraint(
        op.f(_STATUS_CHECK),
        "property_watch",
        f"last_status IS NULL OR last_status IN ({_OLD_STATUSES})",
    )
    _restore_pw_bucket_compression(compression)
