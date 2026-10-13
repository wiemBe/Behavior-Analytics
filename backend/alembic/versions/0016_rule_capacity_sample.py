"""Phase E.1b: rule capacity history.

ADDITIVE. One new table; no existing table or enum is altered.

  * `rule_capacity_sample` -- QRadar's capacity benchmark of a rule, one row per
    distinct `capacity_timestamp`. The primary key (rule_id, capacity_timestamp)
    is what makes "store only when the timestamp changes" an `ON CONFLICT DO
    NOTHING`.

No backfill: nothing was recorded before this revision.

Chained after 0014 for now (0015 is the WAF migration on another branch); the
orchestrator re-chains it after 0015 at merge.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0016'
down_revision: str | None = '0014'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table('rule_capacity_sample',
    sa.Column('rule_id', sa.UUID(), nullable=False),
    sa.Column('capacity_timestamp', sa.DateTime(timezone=True), nullable=False),
    sa.Column('average_capacity', sa.Float(), nullable=True),
    sa.Column('base_capacity', sa.Float(), nullable=True),
    sa.Column('base_host_id', sa.Integer(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['rule_id'], ['analytics_rule.id'], name=op.f('fk_rule_capacity_sample_rule_id_analytics_rule'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('rule_id', 'capacity_timestamp', name=op.f('pk_rule_capacity_sample'))
    )


def downgrade() -> None:
    op.drop_table('rule_capacity_sample')
