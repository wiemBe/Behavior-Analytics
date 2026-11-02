"""Load log for every Ariel search the platform submits.

ADDITIVE. One row per submitted search: caller, outcome, wall time, and the
numeric cost fields from QRadar's search-status payload. No AQL text, no
result rows, no raw values. Not a hypertable; ``apply_ariel_search_run_retention``
deletes by ``submitted_at``.

The row is INSERTed at submit time with outcome ``IN_FLIGHT`` (no
``finished_at``, no ``wall_ms``) and closed by one UPDATE at the terminal
write, so the current-load read can see a search while it runs. A row whose
worker died stays ``IN_FLIGHT`` and is reported as stale by that read until
retention removes it.

``instance_id`` and ``scheduled_search_id`` are plain UUIDs. A foreign key
would reject the side-connection insert whenever the referenced row is still
uncommitted, and that failure is swallowed, so the load row would be lost.

The summary window does not filter on ``instance_id``. The composite index
cannot serve that predicate, so ``submitted_at`` has its own index.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CALLERS = (
    "'LOG_SOURCE_VOLUME', 'ANOMALY_EXPLANATION', 'RULE_MATCH', 'WAF_COLLECTION', "
    "'IPS_COLLECTION', 'PROPERTY_WATCH', 'CATCHALL_SENDERS', 'GEO_COLLECTION', "
    "'HISTORY', 'PREVIEW', 'SCHEDULED_SEARCH', 'MANUAL_RUN'"
)
_OUTCOMES = (
    "'IN_FLIGHT', 'COMPLETED', 'ERROR', 'CANCELED', 'TIMEOUT', 'REFUSED', "
    "'PROVIDER_UNAVAILABLE'"
)


def upgrade() -> None:
    op.create_table(
        "ariel_search_run",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("instance_id", sa.UUID(), nullable=True),
        sa.Column("caller", sa.String(length=64), nullable=False),
        sa.Column("scheduled_search_id", sa.UUID(), nullable=True),
        sa.Column("query_version", sa.Integer(), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("wall_ms", sa.Integer(), nullable=True),
        sa.Column("qradar_execution_ms", sa.BigInteger(), nullable=True),
        sa.Column("qradar_record_count", sa.BigInteger(), nullable=True),
        sa.Column("processed_record_count", sa.BigInteger(), nullable=True),
        sa.Column("data_file_count", sa.BigInteger(), nullable=True),
        sa.Column("data_total_size", sa.BigInteger(), nullable=True),
        sa.Column("compressed_data_file_count", sa.BigInteger(), nullable=True),
        sa.Column("compressed_data_total_size", sa.BigInteger(), nullable=True),
        sa.Column("index_file_count", sa.BigInteger(), nullable=True),
        sa.Column("index_total_size", sa.BigInteger(), nullable=True),
        sa.Column("record_count", sa.BigInteger(), nullable=True),
        sa.Column("range_seconds", sa.Integer(), nullable=True),
        sa.Column("polls", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "wall_ms >= 0",
            name=op.f("ck_ariel_search_run_wall_ms_non_negative"),
        ),
        sa.CheckConstraint(
            "polls >= 0",
            name=op.f("ck_ariel_search_run_polls_non_negative"),
        ),
        sa.CheckConstraint(
            f"caller IN ({_CALLERS})",
            name=op.f("ck_ariel_search_run_caller_known"),
        ),
        sa.CheckConstraint(
            f"outcome IN ({_OUTCOMES})",
            name=op.f("ck_ariel_search_run_outcome_known"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ariel_search_run")),
    )
    op.create_index(
        "ix_ariel_search_run_instance_id_submitted_at",
        "ariel_search_run",
        ["instance_id", "submitted_at"],
        unique=False,
    )
    op.create_index(
        "ix_ariel_search_run_submitted_at",
        "ariel_search_run",
        ["submitted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ariel_search_run_submitted_at",
        table_name="ariel_search_run",
    )
    op.drop_index(
        "ix_ariel_search_run_instance_id_submitted_at",
        table_name="ariel_search_run",
    )
    op.drop_table("ariel_search_run")
