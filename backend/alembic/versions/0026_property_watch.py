"""Phase G: property watches, the PW-1 half (tables, hypertables, the family seed).

ADDITIVE. Revision 0026 revises 0025. The chain has one head.

Creates `property_watch` and the seven `pw_*` tables of design §G.7.1, registers
`pw_bucket` and `pw_value_metric` as hypertables on `bucket_start`, and seeds the
`PROPERTY_WATCH` cadence row at 1 h. Phase A, WAF, IPS, geo and catch-all tables and enums
are untouched. The `custom_rule.pw_value_id` column and the recreated subject `CHECK`
(design §G.10.2) belong to PW-6's migration, not this one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HYPERTABLES = ("pw_bucket", "pw_value_metric")


def upgrade() -> None:
    op.create_table('property_watch',
    sa.Column('instance_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=80), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('scope_kind', sa.String(length=16), nullable=False),
    sa.Column('log_source_id', sa.UUID(), nullable=True),
    sa.Column('scope_type_id', sa.Integer(), nullable=True),
    sa.Column('scope_group_id', sa.Integer(), nullable=True),
    sa.Column('properties', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('pinned_values', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('detectors', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('watch_version', sa.Integer(), nullable=False),
    sa.Column('enabled', sa.Boolean(), nullable=False),
    sa.Column('collected_through', sa.DateTime(timezone=True), nullable=True),
    sa.Column('evaluated_through', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_status', sa.String(length=16), nullable=True),
    sa.Column('last_status_reason', sa.Text(), nullable=True),
    sa.Column('last_run_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_by', sa.String(length=255), nullable=True),
    sa.Column('updated_by', sa.String(length=255), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("(scope_kind = 'LOG_SOURCE' AND log_source_id IS NOT NULL AND scope_type_id IS NULL AND scope_group_id IS NULL) OR (scope_kind = 'LOG_SOURCE_TYPE' AND scope_type_id IS NOT NULL AND log_source_id IS NULL AND scope_group_id IS NULL) OR (scope_kind = 'LOG_SOURCE_GROUP' AND scope_group_id IS NOT NULL AND log_source_id IS NULL AND scope_type_id IS NULL)", name=op.f('ck_property_watch_scope_matches_kind')),
    sa.CheckConstraint("CASE WHEN jsonb_typeof(properties) = 'array' THEN jsonb_array_length(properties) BETWEEN 1 AND 2 ELSE false END", name=op.f('ck_property_watch_properties_one_or_two')),
    sa.CheckConstraint("jsonb_typeof(detectors) = 'array'", name=op.f('ck_property_watch_detectors_array')),
    sa.CheckConstraint("jsonb_typeof(pinned_values) = 'array'", name=op.f('ck_property_watch_pinned_values_array')),
    sa.CheckConstraint("last_status IS NULL OR last_status IN ('NOT_ENABLED', 'DISABLED', 'SCOPE_EMPTY', 'SCOPE_STALE', 'SCOPE_TOO_LARGE', 'NOT_APPLICABLE', 'STALE', 'OPEN', 'RECOVERING', 'OK')", name=op.f('ck_property_watch_last_status_known')),
    sa.CheckConstraint("severity IN ('CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO')", name=op.f('ck_property_watch_severity_known')),
    sa.CheckConstraint('length(name) > 0', name=op.f('ck_property_watch_name_not_empty')),
    sa.CheckConstraint('watch_version >= 1', name=op.f('ck_property_watch_watch_version_positive')),
    sa.ForeignKeyConstraint(['instance_id'], ['qradar_instance.id'], name=op.f('fk_property_watch_instance_id_qradar_instance'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['log_source_id'], ['log_source.id'], name=op.f('fk_property_watch_log_source_id_log_source'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_property_watch')),
    sa.UniqueConstraint('name', name='uq_property_watch_name')
    )
    op.create_index('ix_property_watch_instance', 'property_watch', ['instance_id', 'enabled'], unique=False)
    op.create_table('pw_bucket',
    sa.Column('watch_id', sa.UUID(), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('bucket_seconds', sa.Integer(), nullable=False),
    sa.Column('watch_version', sa.Integer(), nullable=False),
    sa.Column('total_events', sa.BigInteger(), nullable=True),
    sa.Column('parsed_events', sa.BigInteger(), nullable=True),
    sa.Column('unparsed_events', sa.BigInteger(), nullable=True),
    sa.Column('untracked_events', sa.BigInteger(), nullable=True),
    sa.Column('values_measurement', sa.String(length=16), nullable=False),
    sa.Column('pinned_measurement', sa.String(length=16), nullable=False),
    sa.Column('totals_complete', sa.Boolean(), nullable=False),
    sa.Column('observed_values', sa.Integer(), nullable=False),
    sa.Column('tracked_values_observed', sa.Integer(), nullable=False),
    sa.Column('admitted_values', sa.Integer(), nullable=False),
    sa.Column('below_admission_threshold', sa.Integer(), nullable=False),
    sa.Column('rejected_over_quota', sa.Integer(), nullable=False),
    sa.Column('admission_truncated', sa.Boolean(), nullable=False),
    sa.Column('scope_member_count', sa.Integer(), nullable=True),
    sa.Column('scope_fingerprint', sa.String(length=64), nullable=True),
    sa.Column('scope_changed', sa.Boolean(), nullable=False),
    sa.Column('in_maintenance', sa.Boolean(), nullable=False),
    sa.Column('query_provenance', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('collected_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("pinned_measurement IN ('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED', 'NOT_REQUESTED')", name=op.f('ck_pw_bucket_pinned_measurement_known')),
    sa.CheckConstraint("values_measurement IN ('EXACT', 'LOWER_BOUND', 'NOT_COLLECTED')", name=op.f('ck_pw_bucket_values_measurement_known')),
    sa.CheckConstraint('admitted_values >= 0', name=op.f('ck_pw_bucket_admitted_values_non_negative')),
    sa.CheckConstraint('below_admission_threshold >= 0', name=op.f('ck_pw_bucket_below_admission_threshold_non_negative')),
    sa.CheckConstraint('bucket_seconds > 0', name=op.f('ck_pw_bucket_bucket_seconds_positive')),
    sa.CheckConstraint('observed_values >= 0', name=op.f('ck_pw_bucket_observed_values_non_negative')),
    sa.CheckConstraint('parsed_events IS NULL OR parsed_events >= 0', name=op.f('ck_pw_bucket_parsed_events_non_negative')),
    sa.CheckConstraint('rejected_over_quota >= 0', name=op.f('ck_pw_bucket_rejected_over_quota_non_negative')),
    sa.CheckConstraint('scope_member_count IS NULL OR scope_member_count >= 0', name=op.f('ck_pw_bucket_scope_member_count_non_negative')),
    sa.CheckConstraint('total_events IS NULL OR total_events >= 0', name=op.f('ck_pw_bucket_total_events_non_negative')),
    sa.CheckConstraint('totals_complete OR (total_events IS NULL AND unparsed_events IS NULL)', name=op.f('ck_pw_bucket_totals_unknown_when_incomplete')),
    sa.CheckConstraint('tracked_values_observed >= 0', name=op.f('ck_pw_bucket_tracked_values_observed_non_negative')),
    sa.CheckConstraint('unparsed_events IS NULL OR unparsed_events >= 0', name=op.f('ck_pw_bucket_unparsed_events_non_negative')),
    sa.CheckConstraint('untracked_events IS NULL OR untracked_events >= 0', name=op.f('ck_pw_bucket_untracked_events_non_negative')),
    sa.CheckConstraint('watch_version >= 1', name=op.f('ck_pw_bucket_watch_version_positive')),
    sa.ForeignKeyConstraint(['watch_id'], ['property_watch.id'], name=op.f('fk_pw_bucket_watch_id_property_watch'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('watch_id', 'bucket_start', name=op.f('pk_pw_bucket'))
    )
    op.create_table('pw_value',
    sa.Column('watch_id', sa.UUID(), nullable=False),
    sa.Column('watch_version', sa.Integer(), nullable=False),
    sa.Column('value_key', sa.String(length=255), nullable=False),
    sa.Column('component_0', sa.String(length=255), nullable=True),
    sa.Column('component_1', sa.String(length=255), nullable=True),
    sa.Column('truncated', sa.Boolean(), nullable=False),
    sa.Column('pinned', sa.Boolean(), nullable=False),
    sa.Column('first_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('last_seen_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tracked_since', sa.DateTime(timezone=True), nullable=True),
    sa.Column('tracked_until', sa.DateTime(timezone=True), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('retired_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(value_key) > 0', name=op.f('ck_pw_value_value_key_not_empty')),
    sa.CheckConstraint('watch_version >= 1', name=op.f('ck_pw_value_watch_version_positive')),
    sa.ForeignKeyConstraint(['watch_id'], ['property_watch.id'], name=op.f('fk_pw_value_watch_id_property_watch'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pw_value')),
    sa.UniqueConstraint('watch_id', 'watch_version', 'value_key', name='uq_pw_value_natural_key')
    )
    op.create_index('ix_pw_value_active', 'pw_value', ['watch_id', 'watch_version', 'active'], unique=False)
    op.create_index('ix_pw_value_component_0', 'pw_value', ['watch_id', 'watch_version', 'component_0'], unique=False)
    op.create_index('ix_pw_value_component_1', 'pw_value', ['watch_id', 'watch_version', 'component_1'], unique=False)
    op.create_table('pw_anomaly',
    sa.Column('watch_id', sa.UUID(), nullable=False),
    sa.Column('pw_value_id', sa.UUID(), nullable=True),
    sa.Column('value_key', sa.String(length=255), nullable=False),
    sa.Column('detector_type', sa.String(length=40), nullable=False),
    sa.Column('fingerprint', sa.String(length=255), nullable=False),
    sa.Column('severity', sa.String(length=16), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('opened_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('anomaly_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('anomaly_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('observed_value', sa.Float(), nullable=True),
    sa.Column('expected_value', sa.Float(), nullable=True),
    sa.Column('deviation_score', sa.Float(), nullable=True),
    sa.Column('deviation_ratio', sa.Float(), nullable=True),
    sa.Column('robust_z', sa.Float(), nullable=True),
    sa.Column('absolute_delta', sa.Float(), nullable=True),
    sa.Column('confidence', sa.Float(), nullable=True),
    sa.Column('consecutive_buckets', sa.Integer(), nullable=False),
    sa.Column('baseline_version', sa.Integer(), nullable=True),
    sa.Column('policy_version', sa.Integer(), nullable=False),
    sa.Column('watch_version', sa.Integer(), nullable=False),
    sa.Column('explanation', sa.Text(), nullable=True),
    sa.Column('details', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('evidence_status', sa.String(length=16), nullable=False),
    sa.Column('suppressed', sa.Boolean(), nullable=False),
    sa.Column('suppression_reason', sa.String(length=255), nullable=True),
    sa.Column('observed_measurement', sa.String(length=16), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('length(value_key) > 0', name=op.f('ck_pw_anomaly_value_key_not_empty')),
    sa.ForeignKeyConstraint(['pw_value_id'], ['pw_value.id'], name=op.f('fk_pw_anomaly_pw_value_id_pw_value'), ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['watch_id'], ['property_watch.id'], name=op.f('fk_pw_anomaly_watch_id_property_watch'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pw_anomaly'))
    )
    op.create_index('ix_pw_anomaly_detected', 'pw_anomaly', ['detected_at'], unique=False)
    op.create_index('ix_pw_anomaly_state', 'pw_anomaly', ['watch_id', 'detector_type', 'state'], unique=False)
    op.create_table('pw_detector_state',
    sa.Column('watch_id', sa.UUID(), nullable=False),
    sa.Column('pw_value_id', sa.UUID(), nullable=False),
    sa.Column('detector_type', sa.String(length=40), nullable=False),
    sa.Column('consecutive_anomalous', sa.Integer(), nullable=False),
    sa.Column('consecutive_healthy', sa.Integer(), nullable=False),
    sa.Column('state', sa.String(length=24), nullable=False),
    sa.Column('last_interval_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('active_anomaly_id', sa.UUID(), nullable=True),
    sa.Column('open_anomaly_id', sa.UUID(), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pw_value_id'], ['pw_value.id'], name=op.f('fk_pw_detector_state_pw_value_id_pw_value'), ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['watch_id'], ['property_watch.id'], name=op.f('fk_pw_detector_state_watch_id_property_watch'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pw_detector_state')),
    sa.UniqueConstraint('pw_value_id', 'detector_type', name='uq_pw_detector_state_value')
    )
    op.create_table('pw_value_baseline',
    sa.Column('pw_value_id', sa.UUID(), nullable=False),
    sa.Column('metric_name', sa.String(length=64), nullable=False),
    sa.Column('weekday', sa.Integer(), nullable=False),
    sa.Column('hour', sa.Integer(), nullable=False),
    sa.Column('median', sa.Float(), nullable=False),
    sa.Column('mad', sa.Float(), nullable=False),
    sa.Column('p05', sa.Float(), nullable=True),
    sa.Column('p95', sa.Float(), nullable=True),
    sa.Column('sample_count', sa.Integer(), nullable=False),
    sa.Column('is_reliable', sa.Boolean(), nullable=False),
    sa.Column('completeness', sa.Float(), nullable=False),
    sa.Column('observations', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('window_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('window_end', sa.DateTime(timezone=True), nullable=True),
    sa.Column('computed_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('baseline_version', sa.Integer(), nullable=False),
    sa.Column('excluded_sample_count', sa.Integer(), nullable=False),
    sa.Column('exclusion_counts', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('metric_semantics', sa.String(length=160), nullable=True),
    sa.Column('watch_version', sa.Integer(), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint('hour BETWEEN 0 AND 23', name=op.f('ck_pw_value_baseline_hour_range')),
    sa.CheckConstraint('weekday BETWEEN 1 AND 7', name=op.f('ck_pw_value_baseline_weekday_range')),
    sa.ForeignKeyConstraint(['pw_value_id'], ['pw_value.id'], name=op.f('fk_pw_value_baseline_pw_value_id_pw_value'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pw_value_baseline')),
    sa.UniqueConstraint('pw_value_id', 'metric_name', 'weekday', 'hour', name='uq_pw_value_baseline_cell')
    )
    op.create_table('pw_value_metric',
    sa.Column('pw_value_id', sa.UUID(), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=False),
    sa.Column('events', sa.BigInteger(), nullable=False),
    sa.Column('bucket_seconds', sa.Integer(), nullable=False),
    sa.CheckConstraint('bucket_seconds > 0', name=op.f('ck_pw_value_metric_bucket_seconds_positive')),
    sa.CheckConstraint('events >= 0', name=op.f('ck_pw_value_metric_events_non_negative')),
    sa.ForeignKeyConstraint(['pw_value_id'], ['pw_value.id'], name=op.f('fk_pw_value_metric_pw_value_id_pw_value'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('pw_value_id', 'bucket_start', name=op.f('pk_pw_value_metric'))
    )
    op.create_table('pw_anomaly_transition',
    sa.Column('pw_anomaly_id', sa.UUID(), nullable=False),
    sa.Column('from_state', sa.String(length=24), nullable=True),
    sa.Column('to_state', sa.String(length=24), nullable=False),
    sa.Column('occurred_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('bucket_start', sa.DateTime(timezone=True), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('actor', sa.String(length=64), nullable=False),
    sa.Column('observed_value', sa.Float(), nullable=True),
    sa.Column('expected_value', sa.Float(), nullable=True),
    sa.Column('observed_measurement', sa.String(length=16), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['pw_anomaly_id'], ['pw_anomaly.id'], name=op.f('fk_pw_anomaly_transition_pw_anomaly_id_pw_anomaly'), ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id', name=op.f('pk_pw_anomaly_transition'))
    )
    op.create_index('ix_pw_anomaly_transition', 'pw_anomaly_transition', ['pw_anomaly_id', 'occurred_at'], unique=False)

    for table in _HYPERTABLES:
        _create_hypertable(table)
    op.execute(
        sa.text(
            "INSERT INTO search_cadence (family, cadence_seconds, updated_at) "
            "VALUES ('PROPERTY_WATCH', 3600, now()) ON CONFLICT (family) DO NOTHING"
        )
    )


def _create_hypertable(table: str) -> None:
    """Register a table with Timescale, when the extension exists.

    The composite primary key already includes bucket_start, which Timescale requires of a
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
            "SELECT create_hypertable(:table, 'bucket_start', "
            "if_not_exists => TRUE, migrate_data => TRUE)"
        ).bindparams(table=table)
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM search_cadence WHERE family = 'PROPERTY_WATCH'"))
    op.drop_index('ix_pw_anomaly_transition', table_name='pw_anomaly_transition')
    op.drop_table('pw_anomaly_transition')
    op.drop_table('pw_value_metric')
    op.drop_table('pw_value_baseline')
    op.drop_table('pw_detector_state')
    op.drop_index('ix_pw_anomaly_state', table_name='pw_anomaly')
    op.drop_index('ix_pw_anomaly_detected', table_name='pw_anomaly')
    op.drop_table('pw_anomaly')
    op.drop_index('ix_pw_value_component_1', table_name='pw_value')
    op.drop_index('ix_pw_value_component_0', table_name='pw_value')
    op.drop_index('ix_pw_value_active', table_name='pw_value')
    op.drop_table('pw_value')
    op.drop_table('pw_bucket')
    op.drop_index('ix_property_watch_instance', table_name='property_watch')
    op.drop_table('property_watch')
