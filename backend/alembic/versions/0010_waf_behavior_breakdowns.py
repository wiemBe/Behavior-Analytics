"""Phase B.2: per-endpoint WAF breakdowns and ratio baselines.

ADDITIVE for every existing row's meaning. No Phase A table is altered.

  * `waf_source_profile.action_value_map` -- site-authored action values by
    outcome. `{}` for every existing profile, which leaves the action breakdown
    uncollectable until an operator authors a map (and so bumps the version).
  * `waf_source_bucket.{action,response_code,client}_measurement` -- per
    breakdown completeness. NOT_COLLECTED for every existing bucket: nothing
    was measured, and that is what they must say.
  * `waf_source_bucket.{action,response_code,client}_semantics` and `*_events`
    -- NULL for existing buckets. NULL semantics matches nothing, so no
    existing bucket can train or be judged by a breakdown metric.
  * `waf_endpoint_metric` breakdown counts -- NULL for every existing row. NULL
    is the correct value: none of them was measured.
  * `waf_endpoint_baseline.numerator_sum` / `denominator_sum` -- NULL for the
    existing `request_count` cells, which are not ratios.

Downgrade drops the columns. 0009 code never read them, so it reads the same
history it wrote; profiles lose their authored action maps.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = '0010'
down_revision: str | None = '0009'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BREAKDOWNS = ('action', 'response_code', 'client')
_STATUSES = "('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'FAILED')"

_METRIC_COUNTS = (
    ('action_events', sa.BigInteger()),
    ('unmapped_action_count', sa.BigInteger()),
    ('response_code_events', sa.BigInteger()),
    ('client_error_count', sa.BigInteger()),
    ('server_error_count', sa.BigInteger()),
    ('unclassified_response_count', sa.BigInteger()),
    ('client_events', sa.BigInteger()),
    ('top1_client_requests', sa.BigInteger()),
    ('top5_client_requests', sa.BigInteger()),
    ('client_sources', sa.Integer()),
)


def upgrade() -> None:
    op.add_column(
        'waf_source_profile',
        sa.Column('action_value_map', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )
    op.execute("UPDATE waf_source_profile SET action_value_map = '{}'::jsonb")
    op.alter_column('waf_source_profile', 'action_value_map', nullable=False)

    for name in _BREAKDOWNS:
        op.add_column(
            'waf_source_bucket',
            sa.Column(f'{name}_measurement', sa.String(length=16), nullable=True),
        )
        op.add_column(
            'waf_source_bucket',
            sa.Column(f'{name}_semantics', sa.String(length=160), nullable=True),
        )
        op.add_column(
            'waf_source_bucket', sa.Column(f'{name}_events', sa.BigInteger(), nullable=True)
        )
    op.execute(
        "UPDATE waf_source_bucket SET action_measurement = 'NOT_COLLECTED', "
        "response_code_measurement = 'NOT_COLLECTED', client_measurement = 'NOT_COLLECTED'"
    )
    for name in _BREAKDOWNS:
        op.alter_column('waf_source_bucket', f'{name}_measurement', nullable=False)
        op.create_check_constraint(
            op.f(f'ck_waf_source_bucket_{name}_measurement_known'),
            'waf_source_bucket',
            f"{name}_measurement IN {_STATUSES}",
        )

    for column, type_ in _METRIC_COUNTS:
        op.add_column('waf_endpoint_metric', sa.Column(column, type_, nullable=True))
    op.add_column(
        'waf_endpoint_metric',
        sa.Column('top_clients', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )

    op.add_column('waf_endpoint_baseline', sa.Column('numerator_sum', sa.Float(), nullable=True))
    op.add_column(
        'waf_endpoint_baseline', sa.Column('denominator_sum', sa.Float(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column('waf_endpoint_baseline', 'denominator_sum')
    op.drop_column('waf_endpoint_baseline', 'numerator_sum')
    op.drop_column('waf_endpoint_metric', 'top_clients')
    for column, _type in reversed(_METRIC_COUNTS):
        op.drop_column('waf_endpoint_metric', column)
    for name in reversed(_BREAKDOWNS):
        op.drop_constraint(
            op.f(f'ck_waf_source_bucket_{name}_measurement_known'),
            'waf_source_bucket',
            type_='check',
        )
        op.drop_column('waf_source_bucket', f'{name}_events')
        op.drop_column('waf_source_bucket', f'{name}_semantics')
        op.drop_column('waf_source_bucket', f'{name}_measurement')
    op.drop_column('waf_source_profile', 'action_value_map')
