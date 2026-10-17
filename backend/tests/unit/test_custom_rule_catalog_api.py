"""The catalog response is the catalog and the validator, not a second copy."""

from __future__ import annotations

import uuid
from dataclasses import replace

from app.api.routes.custom_rules import (
    PREVIEW_HOURS_MAX,
    PREVIEW_HOURS_MIN,
    build_custom_rule_catalog,
)
from app.custom_rules.alerts import alerting_mode
from app.custom_rules.catalog import CUSTOM_RULE_METRICS, catalog_entry, metrics_for_kind
from app.custom_rules.validation import (
    _SUBJECT_COLUMNS,
    RULE_OPERATORS,
    CustomRuleDraft,
    validate_rule_draft,
)
from app.main import create_app
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind, SourceClass
from app.schemas.custom_rule import CatalogMetricOut, CustomRuleCatalogOut

_COLUMNS = ("log_source_id", "source_class", "waf_endpoint_id", "ips_entity_id", "pw_value_id")


def _catalog(
    *,
    evaluation_enabled: bool = False,
    alerting_enabled: bool = False,
    max_enabled: int = 100,
    enabled_count: int = 0,
) -> CustomRuleCatalogOut:
    return build_custom_rule_catalog(
        evaluation_enabled=evaluation_enabled,
        alerting_enabled=alerting_enabled,
        max_enabled=max_enabled,
        enabled_count=enabled_count,
    )


def _paired(kind: CustomRuleSubjectKind, column: str) -> CustomRuleDraft:
    """A draft that sets only `column`. Validation accepts it when that is the pairing."""
    if column not in _COLUMNS:
        raise AssertionError(column)
    metric = metrics_for_kind(kind)[0]
    return CustomRuleDraft(
        subject_kind=kind,
        metric=metric,
        operator="GT",
        threshold_value=0.5 if catalog_entry(metric).is_ratio else 1.0,
        sustain_buckets=1,
        recovery_buckets=1,
        log_source_id=uuid.uuid4() if column == "log_source_id" else None,
        source_class=SourceClass.LINUX.value if column == "source_class" else None,
        waf_endpoint_id=uuid.uuid4() if column == "waf_endpoint_id" else None,
        ips_entity_id=uuid.uuid4() if column == "ips_entity_id" else None,
        pw_value_id=uuid.uuid4() if column == "pw_value_id" else None,
    )


def _fleet(source_class: str) -> CustomRuleDraft:
    return CustomRuleDraft(
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        metric=CustomRuleMetric.EPS,
        operator="GT",
        threshold_value=1.0,
        sustain_buckets=1,
        recovery_buckets=1,
        source_class=source_class,
    )


def test_catalog_metrics_match_the_catalog_and_hide_internals() -> None:
    catalog = _catalog()
    assert set(CatalogMetricOut.model_fields) == {
        "metric",
        "subject_kind",
        "unit",
        "is_ratio",
        "measurement",
        "note",
    }
    assert [row.metric for row in catalog.metrics] == [
        entry.metric.value for entry in CUSTOM_RULE_METRICS.values()
    ]
    pairs = zip(catalog.metrics, CUSTOM_RULE_METRICS.values(), strict=True)
    for row, entry in pairs:
        assert row.subject_kind == entry.subject_kind.value
        assert row.unit == entry.unit
        assert row.is_ratio is entry.is_ratio
        assert row.measurement is entry.measurement
        assert row.note == entry.note
        dumped = row.model_dump(mode="json")
        assert set(dumped) == set(CatalogMetricOut.model_fields)
        assert dumped["measurement"] == entry.measurement.value
        assert "reads" not in dumped
        assert "bulk_loader" not in dumped


def test_catalog_subject_kinds_match_the_validator_pairing() -> None:
    catalog = _catalog()
    assert [row.subject_kind for row in catalog.subject_kinds] == [
        kind.value for kind in CustomRuleSubjectKind
    ]
    assert set(_SUBJECT_COLUMNS) == set(CustomRuleSubjectKind)
    pairs = zip(catalog.subject_kinds, CustomRuleSubjectKind, strict=True)
    for row, kind in pairs:
        assert _SUBJECT_COLUMNS[kind] == (row.column,)
        assert validate_rule_draft(_paired(kind, row.column)) == []


def test_catalog_source_classes_are_what_validation_accepts() -> None:
    catalog = _catalog()
    accepted: list[str] = []
    for member in SourceClass:
        reasons = validate_rule_draft(_fleet(member.value))
        assert not any("known source class" in reason for reason in reasons), member
        accepted.append(member.value)
    stranger = validate_rule_draft(_fleet("NOT_A_CLASS"))
    assert any("known source class" in reason for reason in stranger)
    assert "NOT_A_CLASS" not in catalog.source_classes
    assert catalog.source_classes == accepted


def test_catalog_operators_are_the_validator_set() -> None:
    catalog = _catalog()
    base = _paired(CustomRuleSubjectKind.LOG_SOURCE, "log_source_id")
    accepted = sorted(RULE_OPERATORS)
    for operator in accepted:
        reasons = validate_rule_draft(replace(base, operator=operator))
        assert not any(reason.startswith("operator ") for reason in reasons), operator
    rejected = validate_rule_draft(replace(base, operator="NE"))
    assert any(reason.startswith("operator ") for reason in rejected)
    assert catalog.operators == accepted
    assert set(catalog.operators) == set(RULE_OPERATORS)
    assert "NE" not in catalog.operators


def _published_hours(path: str, method: str) -> tuple[int, int]:
    operation = create_app().openapi()["paths"][path][method]
    hours = next(item for item in operation["parameters"] if item["name"] == "hours")
    schema = hours["schema"]
    return int(schema["minimum"]), int(schema["maximum"])


def test_catalog_reports_switches_and_the_preview_bounds() -> None:
    off = _catalog()
    assert off.evaluation_enabled is False
    assert off.alerting_mode == alerting_mode(False) == "shadow"
    assert off.max_enabled == 100
    assert off.enabled_count == 0
    assert PREVIEW_HOURS_MIN == 1
    assert PREVIEW_HOURS_MAX == 168
    assert off.preview_hours_min == PREVIEW_HOURS_MIN
    assert off.preview_hours_max == PREVIEW_HOURS_MAX
    assert _published_hours("/api/v1/custom-rules/preview", "post") == (
        PREVIEW_HOURS_MIN,
        PREVIEW_HOURS_MAX,
    )
    assert _published_hours("/api/v1/custom-rules/{rule_id}/preview", "get") == (
        PREVIEW_HOURS_MIN,
        PREVIEW_HOURS_MAX,
    )

    on = _catalog(
        evaluation_enabled=True,
        alerting_enabled=True,
        max_enabled=7,
        enabled_count=3,
    )
    assert on.evaluation_enabled is True
    assert on.alerting_mode == alerting_mode(True) == "alerting"
    assert on.max_enabled == 7
    assert on.enabled_count == 3
