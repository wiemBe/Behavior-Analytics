"""Message-code pins for WAF/IPS validation and collection eligibility."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.api.routes.anomalies import _validation_report as analyst_validation_report
from app.api.routes.ips import _validation_out as ips_validation_out
from app.api.routes.waf import _validation_out as waf_validation_out
from app.core.config import Settings
from app.i18n.messages import MessageRef, render
from app.ips.profile import (
    IpsFieldMapping,
    IpsFieldReference,
    profile_ineligibility,
    profile_ineligibility_message,
)
from app.ips.validation import IpsProfileValidation
from app.ips.validation import evaluate_field as evaluate_ips_field
from app.ips.validation import evaluate_profile as evaluate_ips_profile
from app.models.enums import (
    IpsLogicalField,
    IpsProfileValidationStatus,
    IpsReferenceKind,
    WafLogicalField,
    WafProfileValidationStatus,
)
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.providers.dto import (
    IpsFieldProbe,
    IpsSourceProbe,
    WafPropertyProbe,
    WafPropertyValue,
    WafSourceProbe,
)
from app.schemas.messages import (
    stored_message_list,
    stored_message_parts,
    stored_reasons,
    validation_report_messages,
)
from app.services.ips_profiles import IpsProfileService
from app.services.waf_profiles import WafProfileService
from app.waf.profile import (
    WafFieldMapping,
    collection_ineligibility,
    collection_ineligibility_message,
)
from app.waf.validation import ProfileValidation
from app.waf.validation import evaluate_field as evaluate_waf_field
from app.waf.validation import evaluate_profile as evaluate_waf_profile

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _waf_field(
    logical: WafLogicalField,
    property_name: str | None,
    probe: WafPropertyProbe | None,
    *,
    total: int | None = 100,
    minimum: float = 0.9,
) -> tuple[str | None, str | None, dict[str, object] | None]:
    result = evaluate_waf_field(
        logical,
        property_name,
        probe,
        total_events=total,
        used_by_collection=True,
        min_ratio=minimum,
    )
    wire = result.detail_message.to_wire() if result.detail_message is not None else None
    return (
        result.detail,
        wire["code"] if wire is not None else None,  # type: ignore[return-value]
        wire["params"] if wire is not None else None,  # type: ignore[return-value]
    )


def test_waf_field_detail_sites_keep_legacy_english_and_carry_codes() -> None:
    assert _waf_field(WafLogicalField.URL, None, None)[:2] == (
        "no QRadar property is mapped to this field",
        "validation.waf.field.not_mapped",
    )
    assert _waf_field(
        WafLogicalField.URL,
        "URL",
        WafPropertyProbe(property_name="URL", rejected=True),
    )[:2] == (
        "QRadar rejected a query naming this property; it does not exist as named",
        "validation.waf.field.property_rejected",
    )
    assert _waf_field(
        WafLogicalField.URL, "URL", WafPropertyProbe(property_name="URL")
    )[:2] == (
        "population was not measured",
        "validation.field.population_not_measured",
    )
    assert _waf_field(
        WafLogicalField.URL,
        "URL",
        WafPropertyProbe(property_name="URL", error="ProviderError: timeout"),
    ) == ("ProviderError: timeout", None, None)

    one = WafPropertyProbe(
        property_name="URL",
        populated_events=1,
        values=[WafPropertyValue(value="/", events=1)],
    )
    assert _waf_field(WafLogicalField.URL, "URL", one, total=None)[:2] == (
        "the source's total event volume was not measured",
        "validation.field.total_not_measured",
    )
    assert _waf_field(WafLogicalField.URL, "URL", one, total=0)[:2] == (
        "the source produced no events in the window; nothing can be concluded",
        "validation.field.no_events",
    )
    assert _waf_field(
        WafLogicalField.URL,
        "URL",
        WafPropertyProbe(property_name="URL", populated_events=0),
    )[:2] == (
        "the property exists but no event in the window carried a value",
        "validation.waf.field.unpopulated",
    )

    coverage = _waf_field(WafLogicalField.URL, "URL", one, total=3)
    assert coverage[0] == "populated on 33.3\u00a0% of events, below the required 90\u00a0%"
    assert coverage[1] == "validation.field.insufficient_coverage"
    assert coverage[2] == {
        "populated": {"kind": "percent", "ratio": 0.3333, "precision": 1},
        "required": {"kind": "percent", "ratio": 0.9, "precision": 0},
    }

    implausible = WafPropertyProbe(
        property_name="HTTP_Method",
        populated_events=4,
        values=[
            WafPropertyValue(value="BREW", events=3),
            WafPropertyValue(value="GET", events=1),
        ],
    )
    assert _waf_field(WafLogicalField.METHOD, "HTTP_Method", implausible, total=4)[:2] == (
        "25.0\u00a0% of sampled events carry a plausible method value, "
        "below the required 90\u00a0%",
        "validation.waf.field.implausible_ratio",
    )
    no_sample = WafPropertyProbe(property_name="HTTP_Method", populated_events=1)
    assert _waf_field(WafLogicalField.METHOD, "HTTP_Method", no_sample, total=1)[:2] == (
        "no values of sampled events carry a plausible method value, below the required 90\u00a0%",
        "validation.waf.field.implausible_no_values",
    )


def _ips_field(
    reference: IpsFieldReference | None,
    probe: IpsFieldProbe | None,
    *,
    total: int | None = 100,
) -> tuple[str | None, str | None]:
    result = evaluate_ips_field(
        IpsLogicalField.SOURCE_IP,
        reference,
        probe,
        total_events=total,
        used_by_collection=True,
        min_ratio=0.9,
    )
    return result.detail, result.detail_message.code if result.detail_message else None


def test_ips_field_detail_sites_keep_legacy_english_and_carry_codes() -> None:
    reference = IpsFieldReference(IpsReferenceKind.NORMALIZED, "sourceip")
    assert _ips_field(None, None) == (
        "no QRadar field is mapped to this logical field",
        "validation.ips.field.not_mapped",
    )
    rejected = IpsFieldProbe(
        logical_field="SOURCE_IP",
        reference_kind="NORMALIZED",
        name="sourceip",
        rejected=True,
    )
    assert _ips_field(reference, rejected) == (
        "QRadar rejected the mapped field as named",
        "validation.ips.field.rejected",
    )
    missing = rejected.model_copy(update={"rejected": False})
    assert _ips_field(reference, missing) == (
        "population was not measured",
        "validation.field.population_not_measured",
    )
    failed = missing.model_copy(update={"error": "timeout"})
    assert _ips_field(reference, failed) == ("timeout", None)
    one = missing.model_copy(update={"populated_events": 1})
    assert _ips_field(reference, one, total=None) == (
        "the source's total event volume was not measured",
        "validation.field.total_not_measured",
    )
    assert _ips_field(reference, one, total=0) == (
        "the source produced no events in the window; nothing can be concluded",
        "validation.field.no_events",
    )
    zero = missing.model_copy(update={"populated_events": 0})
    assert _ips_field(reference, zero) == (
        "the field exists but no event in the window carried a usable value",
        "validation.ips.field.unpopulated",
    )
    assert _ips_field(reference, one, total=3) == (
        "populated on 33.3\u00a0% of events, below the required 90\u00a0%",
        "validation.field.insufficient_coverage",
    )


def _waf_source_probe(*properties: WafPropertyProbe, **updates: object) -> WafSourceProbe:
    values: dict[str, object] = {
        "qradar_log_source_id": 7,
        "window_start": T0,
        "window_end": T0,
        "total_events": 100,
        "properties": list(properties),
    }
    values.update(updates)
    return WafSourceProbe.model_validate(values)


def test_waf_profile_reason_sites_keep_english_and_parallel_codes() -> None:
    mapping = WafFieldMapping(url="URL", method="HTTP_Method")
    url = WafPropertyProbe(
        property_name="URL",
        populated_events=100,
        values=[WafPropertyValue(value="/", events=100)],
    )
    method = WafPropertyProbe(
        property_name="HTTP_Method",
        populated_events=100,
        values=[WafPropertyValue(value="GET", events=100)],
    )

    lookup = evaluate_waf_profile(
        mapping,
        _waf_source_probe(url, method),
        log_source_found=True,
        min_ratio=0.9,
        log_source_error="ProviderError: timeout",
    )
    assert lookup.reasons[0] == "the log source lookup failed (ProviderError: timeout)"
    assert lookup.reason_messages[0] is not None
    assert lookup.reason_messages[0].code == "validation.profile.lookup_failed"

    missing = evaluate_waf_profile(
        mapping,
        _waf_source_probe(url, method),
        log_source_found=False,
        min_ratio=0.9,
    )
    assert missing.reasons[0] == "the log source does not exist in QRadar"
    assert missing.reason_messages[0] is not None
    assert missing.reason_messages[0].code == "validation.profile.source_missing"

    total = evaluate_waf_profile(
        mapping,
        _waf_source_probe(url, method, total_error="ProviderError: unavailable"),
        log_source_found=True,
        min_ratio=0.9,
    )
    assert total.reasons[-1] == (
        "total event volume was not measured (ProviderError: unavailable)"
    )
    assert total.reason_messages[-1] is not None
    assert total.reason_messages[-1].code == "validation.profile.total_not_measured"

    sparse = url.model_copy(update={"populated_events": 1})
    composed = evaluate_waf_profile(
        mapping,
        _waf_source_probe(sparse, method),
        log_source_found=True,
        min_ratio=0.9,
    )
    assert composed.reasons[0] == (
        "URL: INSUFFICIENT_COVERAGE (populated on 1.0\u00a0% of events, "
        "below the required 90\u00a0%)"
    )
    wire = composed.as_report()["reason_codes"][0]
    assert wire["code"] == "validation.profile.field_reason"
    assert wire["params"]["detail"]["code"] == (
        "validation.field.insufficient_coverage"
    )

    failed = url.model_copy(update={"populated_events": None, "error": "timeout"})
    provider = evaluate_waf_profile(
        mapping,
        _waf_source_probe(failed, method),
        log_source_found=True,
        min_ratio=0.9,
    )
    assert provider.reasons[0] == "URL: QUERY_FAILED (timeout)"
    provider_wire = provider.as_report()["reason_codes"][0]
    assert provider_wire["params"]["detail"] == "timeout"


def test_ips_profile_reason_sites_keep_english_and_parallel_codes() -> None:
    def reference(name: str) -> IpsFieldReference:
        return IpsFieldReference(IpsReferenceKind.NORMALIZED, name)

    mapping = IpsFieldMapping(
        source_ip=reference("sourceip"),
        destination_ip=reference("destinationip"),
        destination_port=reference("destinationport"),
        signature=reference("qid"),
    )
    fields = [
        IpsFieldProbe(
            logical_field=logical.value,
            reference_kind="NORMALIZED",
            name=name,
            populated_events=100,
        )
        for logical, name in (
            (IpsLogicalField.SOURCE_IP, "sourceip"),
            (IpsLogicalField.DESTINATION_IP, "destinationip"),
            (IpsLogicalField.DESTINATION_PORT, "destinationport"),
            (IpsLogicalField.SIGNATURE, "qid"),
        )
    ]
    probe = IpsSourceProbe(
        qradar_log_source_id=7,
        window_start=T0,
        window_end=T0,
        total_events=100,
        fields=fields,
        truncated=True,
    )
    result = evaluate_ips_profile(
        mapping,
        probe,
        log_source_found=True,
        min_ratio=0.9,
    )
    assert result.reasons == ["the bounded validation probe was truncated"]
    assert result.reason_messages[0] is not None
    assert result.reason_messages[0].code == "validation.ips.profile.truncated"
    assert result.as_report()["reason_codes"] == [
        {"code": "validation.ips.profile.truncated", "params": {}}
    ]

    rejected = fields[0].model_copy(update={"rejected": True, "populated_events": None})
    rejected_probe = probe.model_copy(
        update={"fields": [rejected, *fields[1:]], "truncated": False}
    )
    invalid = evaluate_ips_profile(
        mapping,
        rejected_probe,
        log_source_found=True,
        min_ratio=0.9,
    )
    assert invalid.reasons[0] == (
        "SOURCE_IP: FIELD_REJECTED (QRadar rejected the mapped field as named)"
    )
    invalid_wire = invalid.as_report()["reason_codes"][0]
    assert invalid_wire["code"] == "validation.profile.field_reason"
    assert invalid_wire["params"]["detail"]["code"] == "validation.ips.field.rejected"


def _waf_profile(**updates: object) -> WafSourceProfile:
    values: dict[str, object] = {
        "instance_id": uuid.uuid4(),
        "log_source_id": uuid.uuid4(),
        "enabled": True,
        "profile_version": 12000,
        "validation_status": WafProfileValidationStatus.VALID,
        "validated_profile_version": 12000,
    }
    values.update(updates)
    return WafSourceProfile(**values)


def _source(**updates: object) -> LogSource:
    values: dict[str, object] = {
        "instance_id": uuid.uuid4(),
        "qradar_id": 7,
        "name": "edge-waf",
        "monitoring_enabled": True,
    }
    values.update(updates)
    return LogSource(**values)


def test_waf_ineligibility_sites_render_the_same_english_from_refs() -> None:
    cases = [
        (
            _waf_profile(),
            _source(),
            Settings(waf_collection_enabled=False),
            "WAF collection is disabled (WAF_COLLECTION_ENABLED=false)",
            "validation.waf.ineligible.collection_disabled",
        ),
        (
            _waf_profile(),
            None,
            Settings(waf_collection_enabled=True),
            "the profile's log source no longer exists",
            "validation.waf.ineligible.source_missing",
        ),
        (
            _waf_profile(enabled=False),
            _source(),
            Settings(waf_collection_enabled=True),
            "the profile is disabled",
            "validation.profile.ineligible.disabled",
        ),
        (
            _waf_profile(),
            _source(monitoring_enabled=False),
            Settings(waf_collection_enabled=True),
            "monitoring is disabled for the log source",
            "validation.waf.ineligible.monitoring_disabled",
        ),
        (
            _waf_profile(validated_profile_version=None),
            _source(),
            Settings(waf_collection_enabled=True),
            "profile version 12000 has never been validated",
            "validation.profile.ineligible.version_never_validated",
        ),
        (
            _waf_profile(validated_profile_version=9999),
            _source(),
            Settings(waf_collection_enabled=True),
            "profile version 12000 has not been validated (last validated: 9999)",
            "validation.profile.ineligible.version_not_validated",
        ),
        (
            _waf_profile(validation_status=WafProfileValidationStatus.INVALID),
            _source(),
            Settings(waf_collection_enabled=True),
            "profile validation is INVALID",
            "validation.profile.ineligible.status",
        ),
    ]
    for profile, source, settings, english, code in cases:
        message = collection_ineligibility_message(profile, source, settings)
        assert message is not None and message.code == code
        assert render(message) == english
        assert collection_ineligibility(profile, source, settings) == english


def test_ips_ineligibility_sites_render_the_same_english_from_refs() -> None:
    base = {
        "instance_id": uuid.uuid4(),
        "log_source_id": uuid.uuid4(),
        "enabled": True,
        "profile_version": 12000,
        "validation_status": IpsProfileValidationStatus.VALID,
        "validated_profile_version": 12000,
    }
    cases = [
        ({"enabled": False}, "the profile is disabled", "validation.profile.ineligible.disabled"),
        (
            {"validated_profile_version": None},
            "profile version 12000 has never been validated",
            "validation.profile.ineligible.version_never_validated",
        ),
        (
            {"validated_profile_version": 9999},
            "profile version 12000 has not been validated (last validated: 9999)",
            "validation.profile.ineligible.version_not_validated",
        ),
        (
            {"validation_status": IpsProfileValidationStatus.INCONCLUSIVE},
            "profile validation is INCONCLUSIVE",
            "validation.profile.ineligible.status",
        ),
    ]
    for changes, english, code in cases:
        profile = IpsSourceProfile(**{**base, **changes})
        message = profile_ineligibility_message(profile)
        assert message is not None and message.code == code
        assert render(message) == english
        assert profile_ineligibility(profile) == english


def test_composed_reason_accepts_a_nested_ref_or_provider_text() -> None:
    nested = MessageRef(
        "validation.profile.field_reason",
        {
            "field": "URL",
            "status": "INSUFFICIENT_COVERAGE",
            "detail": MessageRef(
                "validation.field.insufficient_coverage",
                {"populated": 0.0, "required": 0.9},
            ),
        },
    )
    assert render(nested) == (
        "URL: INSUFFICIENT_COVERAGE (populated on 0.0\u00a0% of events, "
        "below the required 90\u00a0%)"
    )
    assert isinstance(nested.to_wire()["params"], dict)

    provider = MessageRef(
        "validation.profile.field_reason",
        {"field": "URL", "status": "QUERY_FAILED", "detail": "ProviderError: timeout"},
    )
    assert render(provider) == "URL: QUERY_FAILED (ProviderError: timeout)"
    assert provider.to_wire()["params"] == {
        "field": "URL",
        "status": "QUERY_FAILED",
        "detail": "ProviderError: timeout",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("family", ["waf", "ips"])
async def test_validation_store_writes_codes_to_report_and_mapping_history(
    family: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    message = MessageRef("validation.profile.source_missing")
    session = AsyncMock()
    if family == "waf":
        profile = _waf_profile(profile_version=1)
        result = ProfileValidation(
            status=WafProfileValidationStatus.INVALID,
            reasons=[render(message)],
            reason_messages=[message],
            log_source_found=False,
            total_events=None,
            fields=[],
            window_start=T0.isoformat(),
            window_end=T0.isoformat(),
        )
        audit = AsyncMock()
        monkeypatch.setattr("app.services.waf_profiles.record_audit", audit)
        service = WafProfileService(session, settings=Settings(), clock=lambda: T0)
    else:
        profile = IpsSourceProfile(
            instance_id=uuid.uuid4(),
            log_source_id=uuid.uuid4(),
            enabled=True,
            profile_version=1,
            validation_status=IpsProfileValidationStatus.UNVALIDATED,
        )
        result = IpsProfileValidation(
            status=IpsProfileValidationStatus.INVALID,
            reasons=[render(message)],
            reason_messages=[message],
            warnings=[],
            log_source_found=False,
            total_events=None,
            fields=[],
            window_start=T0.isoformat(),
            window_end=T0.isoformat(),
            probe_truncated=False,
            ariel_searches=0,
        )
        audit = AsyncMock()
        monkeypatch.setattr("app.services.ips_profiles.record_audit", audit)
        service = IpsProfileService(session, settings=Settings(), clock=lambda: T0)

    await service.store_validation(profile, result, actor="operator", validated_at=T0)  # type: ignore[arg-type]
    expected = [{"code": "validation.profile.source_missing", "params": {}}]
    assert profile.validation_report["reason_codes"] == expected
    assert audit.await_args.kwargs["detail"]["reason_codes"] == expected


def test_stored_message_shape_guard_keeps_unknown_and_drops_malformed() -> None:
    unknown = {"code": "validation.future.code", "params": {}}
    report = validation_report_messages(
        {
            "reason_codes": [unknown, {"code": 7, "params": {}}],
            "fields": [
                {
                    "detail_code": "validation.future.detail",
                    "detail_params": {},
                },
                {"detail_code": "validation.bad", "detail_params": []},
            ],
        }
    )
    codes = report["reason_codes"]
    assert isinstance(codes, list)
    assert codes[0] is not None and codes[0].code == "validation.future.code"
    assert codes[1] is None
    fields = report["fields"]
    assert isinstance(fields, list)
    assert fields[0]["detail_code"] == "validation.future.detail"  # type: ignore[index]
    assert fields[1]["detail_code"] is None  # type: ignore[index]

    assert stored_message_parts("validation.future.detail", {})[0] == (
        "validation.future.detail"
    )
    assert stored_message_parts(1, {}) == (None, None)
    guarded = stored_message_list([unknown, None, {"code": "x", "params": []}])
    assert guarded is not None and guarded[0] is not None
    assert guarded[1:] == [None, None]


@pytest.mark.parametrize("family", ["waf", "ips", "analyst"])
def test_validation_api_wire_keeps_unknown_codes_and_drops_malformed(
    family: str,
) -> None:
    reason_codes = [
        {"code": "validation.future.reason", "params": {}},
        {"code": 7, "params": {}},
    ]
    fields = [
        {
            "field": "URL" if family != "ips" else "SOURCE_IP",
            "status": "QUERY_FAILED",
            "used_by_collection": True,
            "values_truncated": False,
            "detail": "old English",
            "detail_code": "validation.future.detail",
            "detail_params": {},
        },
        {
            "field": "METHOD" if family != "ips" else "DESTINATION_IP",
            "status": "QUERY_FAILED",
            "used_by_collection": True,
            "values_truncated": False,
            "detail": "old English",
            "detail_code": "validation.bad",
            "detail_params": [],
        },
    ]
    report: dict[str, object] = {
        "status": "INCONCLUSIVE",
        "reasons": ["future", "bad"],
        "reason_codes": reason_codes,
        "total_events": None,
        "window_start": T0.isoformat(),
        "window_end": T0.isoformat(),
        "profile_version": 1,
        "fields": fields,
    }
    if family == "waf":
        profile = _waf_profile(profile_version=1)
        profile.validation_report = report
        out = waf_validation_out(profile)
    elif family == "ips":
        profile = IpsSourceProfile(
            instance_id=uuid.uuid4(),
            log_source_id=uuid.uuid4(),
            enabled=True,
            profile_version=1,
            validation_status=IpsProfileValidationStatus.INCONCLUSIVE,
            validation_report={
                **report,
                "warnings": [],
                "probe_truncated": False,
                "ariel_searches": 0,
            },
        )
        out = ips_validation_out(profile)
    else:
        out = analyst_validation_report(report)

    assert out is not None
    assert out.reason_codes is not None
    assert out.reason_codes[0] is not None
    assert out.reason_codes[0].code == "validation.future.reason"
    assert out.reason_codes[1] is None
    assert out.fields[0].detail_code == "validation.future.detail"
    assert out.fields[1].detail_code is None
    assert out.fields[1].detail_params is None


def test_stored_reasons_keep_codes_parallel_when_a_malformed_reason_is_dropped() -> None:
    first = {"code": "validation.profile.source_missing", "params": {}}
    third = {"code": "validation.ips.profile.truncated", "params": {}}
    reasons, codes = stored_reasons(["a", 7, "c"], [first, first, third])
    assert reasons == ["a", "c"]
    assert codes is not None
    assert [c.code if c else None for c in codes] == [
        "validation.profile.source_missing",
        "validation.ips.profile.truncated",
    ]
    assert stored_reasons(["a"], None) == (["a"], None)
    assert stored_reasons("nope", None) == ([], None)
    short_reasons, short_codes = stored_reasons(["a", "b"], [first])
    assert short_reasons == ["a", "b"]
    assert short_codes is not None and short_codes[1] is None
