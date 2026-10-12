"""Phase B.1.5: sparse WAF endpoint metrics, explicit measurement semantics.

ADDITIVE for every existing row's meaning. No Phase A table is altered.

  * `waf_source_bucket.request_count_measurement` -- EXACT or LOWER_BOUND.
    Backfilled from completeness: every existing PARTIAL bucket was PARTIAL
    because grouping was truncated or coverage fell below its floor, both of
    which leave the stored counts as lower bounds.
  * `waf_source_bucket.sparse_endpoint_rows` -- false for every existing bucket,
    which wrote explicit zero rows; the collector sets it true from now on.
  * `waf_endpoint.tracked_since` / `tracked_until` -- the tracking window that
    decides where an absent row may be read as zero. Backfilled so that no
    existing bucket's meaning changes: `tracked_since = first_seen_at`, and for
    an already-retired endpoint `tracked_until = last_seen_at`. Existing buckets
    are all dense, so neither value is consulted for them; the values only have
    to be correct for buckets collected after this revision, which they are.
  * `waf_anomaly.observed_measurement`, `waf_anomaly_transition.observed_measurement`
    -- whether a recorded reading is exact or a lower bound. Every existing
    reading came from a COMPLETE bucket, so EXACT.
  * Drops `ix_waf_endpoint_metric_entity_time`, which indexed exactly the
    primary key's columns in the primary key's order.

Downgrade restores 0008 semantics rather than just 0008 shape: it
re-materializes the zero rows sparse buckets omitted and copies each bucket's
identity onto the slim rows written since, so 0008 code reads the same history.
"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = '0009'
down_revision: str | None = '0008'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        'waf_source_bucket',
        sa.Column('request_count_measurement', sa.String(length=16), nullable=True),
    )
    op.add_column(
        'waf_source_bucket',
        sa.Column('sparse_endpoint_rows', sa.Boolean(), nullable=True),
    )
    op.execute(
        "UPDATE waf_source_bucket SET sparse_endpoint_rows = false, "
        "request_count_measurement = CASE WHEN completeness = 'COMPLETE' "
        "THEN 'EXACT' ELSE 'LOWER_BOUND' END"
    )
    op.alter_column('waf_source_bucket', 'request_count_measurement', nullable=False)
    op.alter_column('waf_source_bucket', 'sparse_endpoint_rows', nullable=False)
    op.create_check_constraint(
        op.f('ck_waf_source_bucket_request_count_measurement_known'),
        'waf_source_bucket',
        "request_count_measurement IN ('EXACT', 'LOWER_BOUND')",
    )

    op.add_column('waf_endpoint', sa.Column('tracked_since', sa.DateTime(timezone=True), nullable=True))
    op.add_column('waf_endpoint', sa.Column('tracked_until', sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE waf_endpoint SET tracked_since = first_seen_at")
    op.execute(
        "UPDATE waf_endpoint SET tracked_until = last_seen_at WHERE is_active = false"
    )
    op.alter_column('waf_endpoint', 'tracked_since', nullable=False)

    for table in ('waf_anomaly', 'waf_anomaly_transition'):
        op.add_column(
            table,
            sa.Column('observed_measurement', sa.String(length=16), nullable=True),
        )
        op.execute(f"UPDATE {table} SET observed_measurement = 'EXACT'")  # noqa: S608
        op.alter_column(table, 'observed_measurement', nullable=False)

    op.drop_index('ix_waf_endpoint_metric_entity_time', table_name='waf_endpoint_metric')


def downgrade() -> None:
    op.create_index(
        'ix_waf_endpoint_metric_entity_time',
        'waf_endpoint_metric',
        ['waf_endpoint_id', 'bucket_start'],
        unique=False,
    )
    # Slim rows inherited their bucket's identity; 0008 reads it from the row.
    op.execute(
        "UPDATE waf_endpoint_metric m SET metric_semantics = b.metric_semantics "
        "FROM waf_endpoint e, waf_source_bucket b "
        "WHERE m.metric_semantics IS NULL AND e.id = m.waf_endpoint_id "
        "AND b.log_source_id = e.log_source_id AND b.bucket_start = m.bucket_start"
    )
    # 0008 reads absence as "not tracked", so write back the zeros sparse
    # buckets implied -- with the bucket's completeness, as 0008 did.
    op.execute(
        "INSERT INTO waf_endpoint_metric (waf_endpoint_id, bucket_start, bucket_seconds, "
        "request_count, distinct_client_count, endpoint_coverage_ratio, completeness, "
        "collection_source, query_provenance, collection_duration_ms, collected_at, "
        "watermark_at, normalizer_version, metric_semantics) "
        "SELECT e.id, b.bucket_start, b.bucket_seconds, 0, 0, b.endpoint_coverage_ratio, "
        "b.completeness, b.collection_source, "
        "'{\"collector\": \"waf_endpoint_metric\", \"source_bucket\": true, "
        "\"zero_filled\": true}'::jsonb, "
        "b.collection_duration_ms, b.collected_at, b.watermark_at, e.normalizer_version, "
        "b.metric_semantics "
        "FROM waf_source_bucket b JOIN waf_endpoint e ON e.log_source_id = b.log_source_id "
        "WHERE b.sparse_endpoint_rows AND e.tracked_since <= b.bucket_start "
        "AND (e.tracked_until IS NULL OR b.bucket_start <= e.tracked_until) "
        "AND e.normalizer_version = (SELECT max(normalizer_version) FROM waf_endpoint) "
        "AND NOT EXISTS (SELECT 1 FROM waf_endpoint_metric m "
        "WHERE m.waf_endpoint_id = e.id AND m.bucket_start = b.bucket_start)"
    )
    for table in ('waf_anomaly_transition', 'waf_anomaly'):
        op.drop_column(table, 'observed_measurement')
    op.drop_column('waf_endpoint', 'tracked_until')
    op.drop_column('waf_endpoint', 'tracked_since')
    op.drop_constraint(
        op.f('ck_waf_source_bucket_request_count_measurement_known'),
        'waf_source_bucket',
        type_='check',
    )
    op.drop_column('waf_source_bucket', 'sparse_endpoint_rows')
    op.drop_column('waf_source_bucket', 'request_count_measurement')
