"""Pure custom-rule draft validation: pairing, catalog, ratios, operators, cap."""

from __future__ import annotations

import math
import uuid

from app.custom_rules.validation import CustomRuleDraft, validate_rule_draft
from app.models.enums import CustomRuleMetric, CustomRuleSubjectKind, SourceClass


def _draft(**overrides: object) -> CustomRuleDraft:
    values: dict[str, object] = {
        "subject_kind": CustomRuleSubjectKind.LOG_SOURCE,
        "metric": CustomRuleMetric.EVENT_COUNT,
        "operator": "GT",
        "threshold_value": 10.0,
        "sustain_buckets": 1,
        "recovery_buckets": 2,
        "log_source_id": uuid.uuid4(),
    }
    values.update(overrides)
    return CustomRuleDraft(**values)  # type: ignore[arg-type]


def test_each_kind_accepts_its_own_subject_column_and_metric() -> None:
    source = uuid.uuid4()
    endpoint = uuid.uuid4()
    entity = uuid.uuid4()
    drafts = [
        _draft(),
        _draft(
            subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
            metric=CustomRuleMetric.EPS,
            log_source_id=None,
            source_class=SourceClass.WAF.value,
        ),
        _draft(
            subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
            metric=CustomRuleMetric.REQUEST_COUNT,
            log_source_id=None,
            waf_endpoint_id=endpoint,
        ),
        _draft(
            subject_kind=CustomRuleSubjectKind.IPS_ENTITY,
            metric=CustomRuleMetric.EVENTS,
            log_source_id=None,
            ips_entity_id=entity,
        ),
        _draft(
            subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
            metric=CustomRuleMetric.TOTAL_EVENTS,
            log_source_id=source,
        ),
    ]
    for draft in drafts:
        assert validate_rule_draft(draft) == []


def test_each_kind_rejects_a_missing_subject_column_and_an_extra_one() -> None:
    missing = [
        _draft(log_source_id=None),
        _draft(
            subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
            metric=CustomRuleMetric.EPS,
            log_source_id=None,
            source_class=None,
        ),
        _draft(
            subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
            metric=CustomRuleMetric.REQUEST_COUNT,
            log_source_id=None,
        ),
        _draft(
            subject_kind=CustomRuleSubjectKind.IPS_ENTITY,
            metric=CustomRuleMetric.EVENTS,
            log_source_id=None,
        ),
        _draft(
            subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
            metric=CustomRuleMetric.TOTAL_EVENTS,
            log_source_id=None,
        ),
    ]
    for draft in missing:
        reasons = validate_rule_draft(draft)
        assert any("need" in reason for reason in reasons)

    extra = _draft(source_class=SourceClass.WAF.value)
    reasons = validate_rule_draft(extra)
    assert any("must stay empty" in reason for reason in reasons)

    # IPS_SOURCE sets log_source_id, not ips_entity_id. The other column is extra.
    swapped = _draft(
        subject_kind=CustomRuleSubjectKind.IPS_SOURCE,
        metric=CustomRuleMetric.TOTAL_EVENTS,
        log_source_id=None,
        ips_entity_id=uuid.uuid4(),
    )
    reasons = validate_rule_draft(swapped)
    assert any("need log_source_id" in reason for reason in reasons)
    assert any("ips_entity_id must stay empty" in reason for reason in reasons)


def test_source_class_must_be_a_known_class() -> None:
    draft = _draft(
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        metric=CustomRuleMetric.EPS,
        log_source_id=None,
        source_class="NOT_A_CLASS",
    )
    reasons = validate_rule_draft(draft)
    assert any("not a known source class" in reason for reason in reasons)


def test_metric_must_be_in_the_catalog_and_belong_to_the_kind() -> None:
    unknown = _draft(metric="not_a_metric")
    reasons = validate_rule_draft(unknown)
    assert any("not in the custom-rule catalog" in reason for reason in reasons)

    wrong_kind = _draft(
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        metric=CustomRuleMetric.EVENT_COUNT,
        log_source_id=None,
        source_class=SourceClass.LINUX.value,
    )
    reasons = validate_rule_draft(wrong_kind)
    assert any("not a FLEET_CLASS" in reason for reason in reasons)


def test_ratio_thresholds_lie_strictly_between_zero_and_one() -> None:
    def ratio(threshold: float) -> list[str]:
        return validate_rule_draft(
            _draft(
                subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
                metric=CustomRuleMetric.BLOCK_RATIO,
                log_source_id=None,
                waf_endpoint_id=uuid.uuid4(),
                threshold_value=threshold,
            )
        )

    assert ratio(0.5) == []
    for bad in (0.0, 1.0, 1.5):
        reasons = ratio(bad)
        assert any("strictly between 0 and 1" in reason for reason in reasons)


def test_a_non_finite_threshold_is_one_reason_and_not_a_ratio_reason() -> None:
    for bad in (math.nan, math.inf, -math.inf):
        reasons = validate_rule_draft(
            _draft(
                subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
                metric=CustomRuleMetric.BLOCK_RATIO,
                log_source_id=None,
                waf_endpoint_id=uuid.uuid4(),
                threshold_value=bad,
            )
        )
        assert any("finite" in reason for reason in reasons)
        assert not any("share" in reason for reason in reasons)


def test_a_negative_threshold_is_rejected_for_every_metric() -> None:
    reasons = validate_rule_draft(_draft(threshold_value=-1.0))
    assert any("at least 0" in reason for reason in reasons)


def test_operator_set_and_sustain_and_recovery() -> None:
    assert any("operator" in reason for reason in validate_rule_draft(_draft(operator="NE")))
    assert any("operator" in reason for reason in validate_rule_draft(_draft(operator="gt")))
    assert any("sustain" in reason for reason in validate_rule_draft(_draft(sustain_buckets=0)))
    assert any(
        "recovery" in reason for reason in validate_rule_draft(_draft(recovery_buckets=0))
    )
    # Every problem is reported together, not only the first.
    reasons = validate_rule_draft(_draft(operator="NE", sustain_buckets=0, recovery_buckets=0))
    named = [
        reason
        for reason in reasons
        if "operator" in reason or "sustain" in reason or "recovery" in reason
    ]
    assert len(named) == 3


def test_enabled_cap_names_the_limit_and_keeps_an_existing_slot() -> None:
    over = validate_rule_draft(
        _draft(enabled=True), enabled_count=100, previously_enabled=False, max_enabled=100
    )
    assert any("at most 100" in reason and "100 already" in reason for reason in over)

    assert (
        validate_rule_draft(
            _draft(enabled=True),
            enabled_count=100,
            previously_enabled=True,
            max_enabled=100,
        )
        == []
    )
    assert (
        validate_rule_draft(
            _draft(enabled=False), enabled_count=100, max_enabled=100
        )
        == []
    )
    assert (
        validate_rule_draft(
            _draft(enabled=True), enabled_count=99, max_enabled=100
        )
        == []
    )
