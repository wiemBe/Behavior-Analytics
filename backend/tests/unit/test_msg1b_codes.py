"""MSG-1b: authoring reasons and the remaining conflicts carry message codes.

Every converted site renders, through the registry, the sentence the old code built. The legacy
sentences below are written out by hand (not rebuilt from the registry), so a template edit fails
here. A user value inside a sentence (a metric, a property name, an action value) is a text
parameter and keeps the `repr` quoting of the old f-string.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.api.routes import ips as ips_routes
from app.api.routes import waf as waf_routes
from app.core.config import get_settings
from app.custom_rules.validation import (
    CustomRuleDraft,
    validate_rule_draft,
    validate_rule_draft_coded,
)
from app.i18n import messages
from app.i18n.messages import MessageRef, render
from app.i18n.refusal import CodedHTTPException
from app.ips.profile import IpsFieldMapping, IpsFieldReference, IpsProfileError
from app.models.enums import (
    CustomRuleMetric,
    CustomRuleSubjectKind,
    IpsProfileValidationStatus,
    IpsReferenceKind,
    WafProfileValidationStatus,
)
from app.schemas.search import (
    ThresholdUnreachableError,
    authoring_detail_for_body,
    unreachable_threshold_reason,
)
from app.services.aql_validator import AQLValidationError, validate_aql
from app.services.ips_profiles import IpsProfileService
from app.services.waf_profiles import WafProfileService
from app.waf.profile import WafFieldMapping, WafProfileError, validate_property_name

FAMILIES = (
    "ruledraft.",
    "search.aql.",
    "search.threshold.",
    "search.enabled_cap",
    "classification.rule.",
    "validation.profile.conflict.",
    "validation.waf.input.",
    "validation.ips.input.",
)

#: code, params, the sentence the old code built.
PINS: list[tuple[str, dict[str, Any], str]] = [
    (
        "ruledraft.subject_needs_column",
        {"kind": "LOG_SOURCE", "column": "log_source_id"},
        "LOG_SOURCE rules need log_source_id set",
    ),
    (
        "ruledraft.column_must_be_empty",
        {"column": "waf_endpoint_id", "kind": "FLEET_CLASS"},
        "waf_endpoint_id must stay empty for a FLEET_CLASS rule",
    ),
    (
        "ruledraft.source_class_unknown",
        {"value": "'X'"},
        "source_class 'X' is not a known source class",
    ),
    (
        "ruledraft.metric_unknown",
        {"metric": "'nope'"},
        "metric 'nope' is not in the custom-rule catalog",
    ),
    (
        "ruledraft.metric_wrong_kind",
        {"metric": "block_ratio", "metric_kind": "WAF_ENDPOINT", "kind": "LOG_SOURCE"},
        "metric block_ratio measures a WAF_ENDPOINT subject, not a LOG_SOURCE one",
    ),
    ("ruledraft.threshold_not_finite", {}, "threshold_value must be a finite number"),
    ("ruledraft.threshold_negative", {}, "threshold_value must be at least 0"),
    (
        "ruledraft.threshold_share",
        {"metric": "block_ratio"},
        "a block_ratio threshold is a share and must lie strictly between 0 and 1",
    ),
    (
        "ruledraft.operator_unknown",
        {"operator": "'NE'"},
        "operator 'NE' is not one of GT, GE, LT, LE, EQ",
    ),
    ("ruledraft.sustain_min", {}, "sustain_buckets must be at least 1"),
    ("ruledraft.recovery_min", {}, "recovery_buckets must be at least 1"),
    (
        "ruledraft.enabled_cap",
        {"max_enabled": 1, "enabled_count": 1},
        "at most 1 rules may be enabled at once (1 already are)",
    ),
    (
        "ruledraft.enabled_cap",
        {"max_enabled": 1204, "enabled_count": 0},
        "at most 1204 rules may be enabled at once (0 already are)",
    ),
    ("ruledraft.null_field", {"field": "metric"}, "metric must not be null"),
    ("ruledraft.log_source_missing", {"id": "ID"}, "log source ID does not exist"),
    ("ruledraft.waf_endpoint_missing", {"id": "ID"}, "waf endpoint ID does not exist"),
    ("ruledraft.ips_entity_missing", {"id": "ID"}, "ips entity ID does not exist"),
    ("ruledraft.pw_value_missing", {"id": "ID"}, "property-watch value ID does not exist"),
    (
        "ruledraft.pw_value_not_pinned",
        {"id": "ID"},
        "property-watch value ID is not pinned; a rule can watch only a pinned value, "
        "so pin it on its property watch first",
    ),
    ("search.aql.empty", {}, "query is empty"),
    ("search.aql.multiple_statements", {}, "multiple statements are not allowed"),
    ("search.aql.not_select", {}, "query must be a single SELECT statement"),
    (
        "search.aql.disallowed_keywords",
        {"keywords": "DROP, INSERT"},
        "disallowed keyword(s): DROP, INSERT",
    ),
    ("search.aql.no_dataset", {}, "query must select FROM events or flows"),
    (
        "search.aql.dataset_not_permitted",
        {"datasets": "USERS"},
        "dataset(s) not permitted: USERS",
    ),
    (
        "search.aql.no_time_window",
        {},
        "query must include a bounded time window (LAST N ..., or START/STOP)",
    ),
    (
        "search.aql.time_range_exceeded",
        {"window_hours": "48.0", "max_hours": 24},
        "time range 48.0h exceeds the maximum 24h",
    ),
    (
        "search.aql.limit_exceeded",
        {"limit": 20000, "max_rows": 10000},
        "LIMIT 20000 exceeds the maximum 10000 rows",
    ),
    (
        "search.threshold.unreachable",
        {"operator": "GT", "threshold": "10001", "max_result_rows": 10000},
        "threshold GT 10001 can never be reached: a run counts at most max_result_rows "
        "(10000) rows; raise max_result_rows above the threshold or lower the threshold",
    ),
    ("search.enabled_cap", {"limit": 1204}, "the limit is 1204 enabled searches"),
    (
        "classification.rule.duplicate",
        {},
        "a rule with this instance, kind and pattern already exists",
    ),
    (
        "classification.rule.built_in_edit",
        {},
        "a built-in rule can only be enabled or disabled",
    ),
    (
        "classification.rule.built_in_delete",
        {},
        "a built-in rule cannot be deleted; disable it instead",
    ),
    (
        "validation.profile.conflict.missing",
        {},
        "the profile does not exist; create it with expected_profile_version null",
    ),
    (
        "validation.profile.conflict.version",
        {"current": 12},
        "profile version conflict; current version is 12",
    ),
    (
        "validation.profile.conflict.running",
        {"profile_version": 3, "since": "2026-10-28T10:00:00+00:00"},
        "validation of version 3 is already running since 2026-10-28T10:00:00+00:00",
    ),
    (
        "validation.profile.conflict.not_validated",
        {"profile_version": 3, "validation_status": "INVALID"},
        "profile version 3 has not validated (status INVALID); run validation first",
    ),
    (
        "validation.waf.input.property_name",
        {"name": "'bad;name'"},
        "invalid QRadar property name 'bad;name': letters, digits, underscore, space and "
        "hyphen only, at most 64 characters",
    ),
    (
        "validation.waf.input.action_value",
        {"outcome": "BLOCKED", "value": "''", "max_length": 64},
        "invalid BLOCKED action value '': a printable token of at most 64 characters",
    ),
    (
        "validation.waf.input.action_overlap",
        {"values": "['deny']"},
        "action values mapped to both BLOCKED and ALLOWED: ['deny']",
    ),
    (
        "validation.waf.input.action_needs_property",
        {},
        "an action map needs an action property",
    ),
    (
        "validation.ips.input.normalized_field",
        {},
        "normalized field is not in the IPS allowlist",
    ),
    (
        "validation.ips.input.property_name",
        {},
        "invalid QRadar property name: letters, digits, underscore, space and hyphen only, "
        "at most 64 characters",
    ),
    (
        "validation.ips.input.action_count",
        {"outcome": "BLOCKED", "max_count": 20},
        "BLOCKED action values exceed the 20-value limit",
    ),
    (
        "validation.ips.input.action_value",
        {"outcome": "ALLOWED", "max_length": 64},
        "invalid ALLOWED action value: use a printable token of at most 64 characters",
    ),
    (
        "validation.ips.input.action_overlap",
        {},
        "an action value is mapped to both BLOCKED and ALLOWED",
    ),
    (
        "validation.ips.input.action_needs_field",
        {},
        "an action map needs an ACTION field mapping",
    ),
]


@pytest.mark.parametrize(("code", "params", "english"), PINS)
def test_each_code_renders_the_old_sentence(
    code: str, params: dict[str, Any], english: str
) -> None:
    assert render(MessageRef(code, params)) == english


def test_every_msg1b_code_is_pinned_above() -> None:
    registered = {spec.code for spec in messages.REGISTRY if spec.code.startswith(FAMILIES)}
    assert registered == {code for code, _, _ in PINS}


def test_the_block_sits_between_its_markers() -> None:
    text = Path(messages.__file__).read_text(encoding="utf-8")
    start = text.index("# --- MSG-1b")
    end = text.index("# --- end MSG-1b ---")
    block = text[start:end]
    assert block.count('"ruledraft.') >= 18
    assert "MSG-2" not in block and "MSG-4" not in block


# --- custom-rule draft validation -------------------------------------------------------------


def _draft(**changes: Any) -> CustomRuleDraft:
    base: dict[str, Any] = {
        "subject_kind": CustomRuleSubjectKind.LOG_SOURCE,
        "metric": CustomRuleMetric.EVENT_COUNT,
        "operator": "GT",
        "threshold_value": 10.0,
        "sustain_buckets": 1,
        "recovery_buckets": 1,
        "log_source_id": uuid.UUID(int=1),
    }
    base.update(changes)
    return CustomRuleDraft(**base)


def test_a_legal_draft_has_no_reasons_in_either_form() -> None:
    assert validate_rule_draft(_draft()) == []
    assert validate_rule_draft_coded(_draft()) == []


def test_every_reason_of_one_bad_draft_is_coded_and_renders_the_old_sentence() -> None:
    draft = _draft(
        metric="nope",
        operator="NE",
        threshold_value=-5,
        sustain_buckets=0,
        recovery_buckets=0,
        waf_endpoint_id=uuid.UUID(int=2),
    )
    coded = validate_rule_draft_coded(draft, enabled_count=100, max_enabled=100)
    assert validate_rule_draft(draft, enabled_count=100, max_enabled=100) == [
        render(ref) for ref in coded
    ]
    assert [ref.code for ref in coded] == [
        "ruledraft.column_must_be_empty",
        "ruledraft.metric_unknown",
        "ruledraft.threshold_negative",
        "ruledraft.operator_unknown",
        "ruledraft.sustain_min",
        "ruledraft.recovery_min",
    ]
    assert validate_rule_draft(draft) == [
        "waf_endpoint_id must stay empty for a LOG_SOURCE rule",
        "metric 'nope' is not in the custom-rule catalog",
        "threshold_value must be at least 0",
        "operator 'NE' is not one of GT, GE, LT, LE, EQ",
        "sustain_buckets must be at least 1",
        "recovery_buckets must be at least 1",
    ]


def test_a_value_with_a_quote_keeps_the_repr_quoting() -> None:
    reasons = validate_rule_draft(_draft(metric="it's", operator='say "hi"'))
    assert reasons[0] == 'metric "it\'s" is not in the custom-rule catalog'
    assert "operator 'say \"hi\"' is not one of GT, GE, LT, LE, EQ" in reasons
    wire = [ref.to_wire() for ref in validate_rule_draft_coded(_draft(metric="it's"))]
    assert wire[0]["params"] == {"metric": "\"it's\""}


def test_the_pairing_class_kind_threshold_and_cap_reasons() -> None:
    fleet = _draft(
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        log_source_id=None,
        source_class="NOT_A_CLASS",
        metric=CustomRuleMetric.EVENT_COUNT,
    )
    assert "source_class 'NOT_A_CLASS' is not a known source class" in validate_rule_draft(fleet)
    assert validate_rule_draft(_draft(log_source_id=None)) == [
        "LOG_SOURCE rules need log_source_id set"
    ]
    waf = _draft(
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        log_source_id=None,
        waf_endpoint_id=uuid.UUID(int=3),
        metric=CustomRuleMetric.BLOCK_RATIO,
        threshold_value=1.0,
    )
    assert validate_rule_draft(waf) == [
        "a block_ratio threshold is a share and must lie strictly between 0 and 1"
    ]
    assert validate_rule_draft(_draft(metric=CustomRuleMetric.BLOCK_RATIO)) == [
        "metric block_ratio measures a WAF_ENDPOINT subject, not a LOG_SOURCE one",
        "a block_ratio threshold is a share and must lie strictly between 0 and 1",
    ]
    assert validate_rule_draft(_draft(threshold_value=float("nan"))) == [
        "threshold_value must be a finite number"
    ]
    capped = validate_rule_draft(_draft(enabled=True), enabled_count=1204, max_enabled=1204)
    assert capped == ["at most 1204 rules may be enabled at once (1204 already are)"]
    kept = validate_rule_draft(
        _draft(enabled=True), enabled_count=3, previously_enabled=True, max_enabled=3
    )
    assert kept == []


# --- the reasons body --------------------------------------------------------------------------


def test_a_reasons_body_keeps_message_and_reasons_and_adds_parallel_codes() -> None:
    refs = [MessageRef("ruledraft.sustain_min"), "free text", MessageRef("ruledraft.recovery_min")]
    exc = CodedHTTPException(422, reasons=refs)
    assert exc.detail == {
        "message": "sustain_buckets must be at least 1; free text; "
        "recovery_buckets must be at least 1",
        "reasons": [
            "sustain_buckets must be at least 1",
            "free text",
            "recovery_buckets must be at least 1",
        ],
        "reason_codes": [
            {"code": "ruledraft.sustain_min", "params": {}},
            None,
            {"code": "ruledraft.recovery_min", "params": {}},
        ],
    }
    assert exc.body() == {"detail": exc.detail}


def test_a_reasons_body_takes_a_compatibility_message_and_no_other_shape() -> None:
    exc = CodedHTTPException(422, reasons=[MessageRef("search.aql.empty")], message="unsafe AQL: x")
    assert exc.detail["message"] == "unsafe AQL: x"
    with pytest.raises(ValueError):
        CodedHTTPException(422, MessageRef("search.aql.empty"), reasons=[])
    with pytest.raises(ValueError):
        CodedHTTPException(422, MessageRef("search.aql.empty"), message="x")
    with pytest.raises(ValueError):
        CodedHTTPException(422, reasons=[], fields={"a": 1})


# --- AQL validation ---------------------------------------------------------------------------


def _aql_refusal(aql: str, **limits: Any) -> AQLValidationError:
    with pytest.raises(AQLValidationError) as caught:
        validate_aql(aql, **limits)
    return caught.value


def test_every_aql_reason_is_coded_and_keeps_its_english() -> None:
    exc = _aql_refusal("DROP TABLE x; SELECT 1 FROM users LIMIT 99999999")
    assert exc.reasons == [
        "multiple statements are not allowed",
        "query must be a single SELECT statement",
        "disallowed keyword(s): DROP",
        "dataset(s) not permitted: USERS",
        "query must include a bounded time window (LAST N ..., or START/STOP)",
        "LIMIT 99999999 exceeds the maximum 10000 rows",
    ]
    assert [ref.code for ref in exc.refs if ref is not None] == [
        "search.aql.multiple_statements",
        "search.aql.not_select",
        "search.aql.disallowed_keywords",
        "search.aql.dataset_not_permitted",
        "search.aql.no_time_window",
        "search.aql.limit_exceeded",
    ]
    assert str(exc) == "; ".join(exc.reasons)


def test_aql_edge_sentences() -> None:
    assert _aql_refusal("  ").reasons == ["query is empty"]
    assert _aql_refusal(
        "SELECT x FROM events LAST 2 DAYS", max_time_range_hours=24
    ).reasons == [
        "time range 48.0h exceeds the maximum 24h"
    ]
    assert _aql_refusal("SELECT x FROM events LAST 90 MINUTES", max_time_range_hours=1).reasons == [
        "time range 1.5h exceeds the maximum 1h"
    ]
    assert _aql_refusal("SELECT x FROM events LAST 1 HOURS LIMIT 20000").reasons == [
        "LIMIT 20000 exceeds the maximum 10000 rows"
    ]
    assert _aql_refusal("SELECT x LAST 1 HOURS").reasons == [
        "query must select FROM events or flows"
    ]
    assert _aql_refusal("INSERT INTO t SELECT x FROM events LAST 1 HOURS").reasons[1] == (
        "disallowed keyword(s): INSERT, INTO"
    )


def test_a_plain_text_reason_stays_plain() -> None:
    exc = AQLValidationError(["bad"])
    assert exc.reasons == ["bad"]
    assert exc.refs == [None]


def test_the_authoring_detail_carries_parallel_codes_and_an_uncoded_message() -> None:
    detail = authoring_detail_for_body(
        {"aql_query": "SELECT 1 FROM events", "threshold_operator": "GT", "threshold_value": 5000,
         "max_result_rows": 100}
    )
    assert detail is not None
    assert detail["reasons"] == [
        "query must include a bounded time window (LAST N ..., or START/STOP)",
        "threshold GT 5000 can never be reached: a run counts at most max_result_rows (100) "
        "rows; raise max_result_rows above the threshold or lower the threshold",
    ]
    assert detail["message"] == (
        "unsafe AQL: query must include a bounded time window (LAST N ..., or START/STOP); "
        + detail["reasons"][1]
    )
    assert detail["reason_codes"] == [
        {"code": "search.aql.no_time_window", "params": {}},
        {
            "code": "search.threshold.unreachable",
            "params": {"operator": "GT", "threshold": "5000", "max_result_rows": 100},
        },
    ]


def test_the_threshold_sentence_edges() -> None:
    assert unreachable_threshold_reason("GT", 10001, 10000) is not None
    assert unreachable_threshold_reason("GT", 10000, 10000) is None
    assert unreachable_threshold_reason("GE", 10001, 10000) is None
    assert unreachable_threshold_reason("GE", 10002.5, 10000) == (
        "threshold GE 10002.5 can never be reached: a run counts at most max_result_rows "
        "(10000) rows; raise max_result_rows above the threshold or lower the threshold"
    )
    assert unreachable_threshold_reason("LT", 10**9, 10) is None
    assert unreachable_threshold_reason("GT", None, 10) is None
    error = ThresholdUnreachableError(MessageRef("search.enabled_cap", {"limit": 2}))
    assert str(error) == "the limit is 2 enabled searches"
    assert ThresholdUnreachableError("plain").ref is None


# --- WAF and IPS input refusals and conflicts -------------------------------------------------


def test_the_waf_input_refusals_are_coded() -> None:
    with pytest.raises(WafProfileError) as bad:
        validate_property_name("it's;bad")
    assert str(bad.value) == (
        "invalid QRadar property name \"it's;bad\": letters, digits, underscore, space and "
        "hyphen only, at most 64 characters"
    )
    assert bad.value.ref == MessageRef(
        "validation.waf.input.property_name", {"name": "\"it's;bad\""}
    )

    def mapping(**changes: Any) -> WafFieldMapping:
        return WafFieldMapping(url="URL", method="Method", **changes)

    with pytest.raises(WafProfileError) as value:
        mapping(action="Action", blocked_actions=("x" * 65,))
    assert str(value.value).startswith("invalid BLOCKED action value '" + "x" * 65 + "': ")
    assert str(value.value).endswith("a printable token of at most 64 characters")
    with pytest.raises(WafProfileError) as overlap:
        mapping(action="Action", blocked_actions=("Deny",), allowed_actions=("deny",))
    assert str(overlap.value) == "action values mapped to both BLOCKED and ALLOWED: ['deny']"
    with pytest.raises(WafProfileError) as needs:
        mapping(blocked_actions=("deny",))
    assert needs.value.ref == MessageRef("validation.waf.input.action_needs_property")
    assert waf_routes._input_detail(overlap.value) == {
        "message": "action values mapped to both BLOCKED and ALLOWED: ['deny']",
        "code": "validation.waf.input.action_overlap",
        "params": {"values": "['deny']"},
    }
    assert waf_routes._input_detail(WafProfileError("plain words")) == {"message": "plain words"}


def _ips_ref(name: str = "Signature") -> IpsFieldReference:
    return IpsFieldReference(IpsReferenceKind.CUSTOM_PROPERTY, name)


def test_the_ips_input_refusals_are_coded() -> None:
    with pytest.raises(IpsProfileError) as name:
        _ips_ref("bad;name")
    assert name.value.ref == MessageRef("validation.ips.input.property_name")
    with pytest.raises(IpsProfileError) as field:
        IpsFieldReference(IpsReferenceKind.NORMALIZED, "nope")
    assert str(field.value) == "normalized field is not in the IPS allowlist"

    def mapping(**changes: Any) -> IpsFieldMapping:
        return IpsFieldMapping(
            source_ip=_ips_ref("a"),
            destination_ip=_ips_ref("b"),
            destination_port=_ips_ref("c"),
            signature=_ips_ref("d"),
            **changes,
        )

    with pytest.raises(IpsProfileError) as count:
        mapping(action=_ips_ref("e"), blocked_actions=tuple(f"v{i}" for i in range(21)))
    assert str(count.value) == "BLOCKED action values exceed the 20-value limit"
    with pytest.raises(IpsProfileError) as value:
        mapping(action=_ips_ref("e"), allowed_actions=("",))
    assert str(value.value) == (
        "invalid ALLOWED action value: use a printable token of at most 64 characters"
    )
    with pytest.raises(IpsProfileError) as overlap:
        mapping(action=_ips_ref("e"), blocked_actions=("a",), allowed_actions=("A",))
    assert overlap.value.ref == MessageRef("validation.ips.input.action_overlap")
    with pytest.raises(IpsProfileError) as needs:
        mapping(blocked_actions=("a",))
    assert str(needs.value) == "an action map needs an ACTION field mapping"
    assert ips_routes._input_detail(needs.value) == {
        "message": "an action map needs an ACTION field mapping",
        "code": "validation.ips.input.action_needs_field",
        "params": {},
    }


def test_a_conflict_body_gains_code_and_params_beside_its_other_keys() -> None:
    profile = SimpleNamespace(
        profile_version=7, updated_by="alice", validation_requested_at=None
    )
    for routes in (waf_routes, ips_routes):
        body = routes._conflict_detail(
            MessageRef("validation.profile.conflict.version", {"current": 7}), profile
        )
        assert body == {
            "message": "profile version conflict; current version is 7",
            "code": "validation.profile.conflict.version",
            "params": {"current": 7},
            "current_version": 7,
            "updated_by": "alice",
            "pending_since": None,
        }
        assert routes._conflict_detail("old words") == {
            "message": "old words",
            "current_version": None,
            "updated_by": None,
            "pending_since": None,
        }


@pytest.mark.asyncio
async def test_enabling_an_unvalidated_profile_is_refused_with_its_code() -> None:
    for service_cls, status_cls in (
        (WafProfileService, WafProfileValidationStatus),
        (IpsProfileService, IpsProfileValidationStatus),
    ):
        service = service_cls(None, settings=get_settings())  # type: ignore[arg-type]
        profile = SimpleNamespace(
            validation_status=status_cls.INVALID,
            validated_profile_version=None,
            profile_version=1204,
            enabled=False,
        )
        error_type = WafProfileError if service_cls is WafProfileService else IpsProfileError
        with pytest.raises(error_type) as caught:
            await service.set_enabled(profile, True, actor="a")  # type: ignore[arg-type]
        assert str(caught.value) == (
            "profile version 1204 has not validated (status INVALID); run validation first"
        )
        assert caught.value.ref == MessageRef(
            "validation.profile.conflict.not_validated",
            {"profile_version": 1204, "validation_status": "INVALID"},
        )
