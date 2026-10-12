"""Phase E.1a: measured rule matches from `creEventList`.

ADDITIVE. Two new tables; no existing table or enum is altered.

  * `rule_match_metric` -- sparse events-matched per rule or building block per
    window. A row exists only for an id that matched. Registered as a Timescale
    hypertable from the shared HYPERTABLES registry, with the same guard 0005
    uses. The composite primary key already includes `bucket_start`, which
    Timescale requires of a partitioning column.
  * `rule_match_window` -- what each collection established about its window
    (COMPLETE / TRUNCATED / FAILED). It is what gives a rule's absence meaning.

Neither table has a backfill: nothing was measured before this revision, and an
empty `rule_match_window` is exactly how that reads. No retention or compression
policy is set here, matching 0001, 0002 and 0005.

`down_revision` is '0010' while T-022's '0011' is unmerged; the orchestrator
re-chains it at merge.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0012'
down_revision: str | None = '0010'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rule_match_window',
    sa.Column('instance_id', sa.UUID(), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('window_seconds', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=16), nullable=False),
    sa.Column('rows_returned', sa.Integer(), nullable=False),
    sa.Column('row_cap', sa.Integer(), nullable=False),
    sa.Column('unknown_ids', sa.Integer(), nullable=False),
    sa.Column('duration_ms', sa.Integer(), nullable=True),
    sa.Column('query_version', sa.String(length=64), nullable=False),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('attempts', sa.Integer(), nullable=False),
    sa.Column('last_error', sa.String(length=512), nullable=True),
    sa.CheckConstraint('attempts >= 0', name=op.f('ck_rule_match_window_attempts_non_negative')),
    sa.CheckConstraint('rows_returned >= 0', name=op.f('ck_rule_match_window_rows_returned_non_negative')),
    sa.CheckConstraint("status IN ('COMPLETE', 'TRUNCATED', 'FAILED')", name=op.f('ck_rule_match_window_status_known')),
    sa.CheckConstraint('unknown_ids >= 0', name=op.f('ck_rule_match_window_unknown_ids_non_negative')),
    sa.CheckConstraint('window_seconds > 0', name=op.f('ck_rule_match_window_window_seconds_positive')),
    sa.ForeignKeyConstraint(['instance_id'], ['qradar_instance.id'], name=op.f('fk_rule_match_window_instance_id_qradar_instance'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('instance_id', 'window_start', name=op.f('pk_rule_match_window'))
    )
    op.create_table('rule_match_metric',
    sa.Column('rule_id', sa.UUID(), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('bucket_seconds', sa.Integer(), nullable=False),
    sa.Column('match_records', sa.BigInteger(), nullable=False),
    sa.Column('matched_events', sa.BigInteger(), nullable=False),
    sa.CheckConstraint('bucket_seconds > 0', name=op.f('ck_rule_match_metric_bucket_seconds_positive')),
    sa.CheckConstraint('match_records >= 0', name=op.f('ck_rule_match_metric_match_records_non_negative')),
    sa.CheckConstraint('matched_events >= 0', name=op.f('ck_rule_match_metric_matched_events_non_negative')),
    sa.ForeignKeyConstraint(['rule_id'], ['analytics_rule.id'], name=op.f('fk_rule_match_metric_rule_id_analytics_rule'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('rule_id', 'bucket_start', name=op.f('pk_rule_match_metric'))
    )
    _create_hypertable()


def _create_hypertable() -> None:
    """Register rule_match_metric with Timescale, when the extension exists."""
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
        ).bindparams(t="rule_match_metric", c="bucket_start")
    )


def downgrade() -> None:
    op.drop_table('rule_match_metric')
    op.drop_table('rule_match_window')
