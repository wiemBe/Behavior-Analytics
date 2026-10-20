"""The custom-rule catalog is exactly §F.1.1, and every metric names a loader."""

from __future__ import annotations

from app.custom_rules.catalog import (
    CUSTOM_RULE_METRICS,
    MeasurementRule,
    catalog_entry,
    metrics_for_kind,
)
from app.custom_rules.loaders import implemented_loaders
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind

# Columns §F.1.1 names as reads. Completeness and measurement columns are
# conditions, not reads, and must not appear here.
_LEGAL_READS = frozenset(
    {
        "log_source_metric.event_count",
        "log_source_metric.average_eps",
        "log_source_metric.bucket_seconds",
        "waf_endpoint_metric.request_count",
        "waf_endpoint_metric.blocked_count",
        "waf_endpoint_metric.action_events",
        "waf_endpoint_metric.unmapped_action_count",
        "waf_endpoint_metric.server_error_count",
        "waf_endpoint_metric.client_error_count",
        "waf_endpoint_metric.response_code_events",
        "waf_endpoint_metric.unclassified_response_count",
        "ips_entity_metric.events",
        "ips_source_bucket.total_events",
        "ips_source_bucket.blocked_events",
        "ips_source_bucket.allowed_events",
        "ips_source_bucket.unmapped_events",
        "pw_value_metric.events",
    }
)

_EXACT_ONLY = frozenset(
    {
        CustomRuleMetric.EVENT_COUNT,
        CustomRuleMetric.AVERAGE_EPS,
        CustomRuleMetric.TOTAL_EVENTS,
    }
)

_RATIOS = frozenset(
    {
        CustomRuleMetric.BLOCK_RATIO,
        CustomRuleMetric.SERVER_ERROR_RATIO,
        CustomRuleMetric.CLIENT_ERROR_RATIO,
        CustomRuleMetric.BLOCKED_SHARE,
    }
)


def test_catalog_has_one_entry_per_metric_and_nothing_else() -> None:
    assert set(CUSTOM_RULE_METRICS) == set(CustomRuleMetric)
    assert len(CUSTOM_RULE_METRICS) == 11  # the ten of §F.1.1 and Phase G's PW_VALUE_EVENTS


def test_every_metric_declares_kind_unit_reader_measurement_and_loader() -> None:
    loaders = implemented_loaders()
    reads: set[str] = set()
    for metric, entry in CUSTOM_RULE_METRICS.items():
        assert entry.metric is metric
        assert entry.subject_kind in CustomRuleSubjectKind
        assert entry.unit
        assert entry.reads
        assert entry.measurement in (
            MeasurementRule.EXACT_ONLY,
            MeasurementRule.LOWER_BOUND_PROVES_INCREASE,
        )
        assert entry.bulk_loader == entry.subject_kind.value
        assert entry.bulk_loader in loaders
        assert entry.note
        assert catalog_entry(metric) is entry
        reads.update(entry.reads)
    assert reads == _LEGAL_READS


def test_measurement_rule_and_ratio_flag_match_the_catalog() -> None:
    for metric, entry in CUSTOM_RULE_METRICS.items():
        if metric in _EXACT_ONLY:
            assert entry.measurement is MeasurementRule.EXACT_ONLY
        else:
            assert entry.measurement is MeasurementRule.LOWER_BOUND_PROVES_INCREASE
        assert entry.is_ratio is (metric in _RATIOS)


def test_every_subject_kind_has_at_least_one_metric() -> None:
    for kind in CustomRuleSubjectKind:
        assert metrics_for_kind(kind)


def test_no_metric_reads_a_column_outside_the_design() -> None:
    for entry in CUSTOM_RULE_METRICS.values():
        unknown = set(entry.reads) - _LEGAL_READS
        assert not unknown, entry.metric
