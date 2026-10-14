"""IPS mapping, validation and bounded read-only provider probes."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.config import Settings
from app.ips.profile import (
    IPS_PRESETS,
    NORMALIZED_FIELD_ALLOWLIST,
    IpsFieldMapping,
    IpsFieldReference,
    IpsProfileError,
    profile_ineligibility,
)
from app.ips.validation import evaluate_profile
from app.models.enums import (
    IpsFieldStatus,
    IpsLogicalField,
    IpsPreset,
    IpsProfileValidationStatus,
    IpsReferenceKind,
)
from app.models.ips import IpsSourceProfile
from app.providers.base import CapabilityNotSupportedError, ProviderCapability
from app.providers.dto import (
    IpsFieldProbe,
    IpsFieldProbeRequest,
    IpsProbeValue,
    IpsSourceProbe,
)
from app.providers.mock import MockQRadarProvider
from tests.unit.test_qradar_rest_provider import build_provider

T0 = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def normalized(name: str) -> IpsFieldReference:
    return IpsFieldReference(IpsReferenceKind.NORMALIZED, name)


def custom(name: str) -> IpsFieldReference:
    return IpsFieldReference(IpsReferenceKind.CUSTOM_PROPERTY, name)


MAPPING = IpsFieldMapping(
    source_ip=normalized("sourceip"),
    destination_ip=normalized("destinationip"),
    destination_port=normalized("destinationport"),
    protocol=normalized("protocolid"),
    signature=normalized("qid"),
    action=custom("IPS_Action"),
    severity=normalized("severity"),
    blocked_actions=("BLOCK",),
    allowed_actions=("ALLOW",),
)


class TestMapping:
    @pytest.mark.parametrize("name", NORMALIZED_FIELD_ALLOWLIST)
    def test_every_normalized_allowlist_name_is_accepted(self, name: str) -> None:
        assert normalized(name).name == name

    @pytest.mark.parametrize("name", ["username", "SourceIP", 'x"; DROP'])
    def test_other_normalized_text_is_rejected_safely(self, name: str) -> None:
        with pytest.raises(IpsProfileError, match="allowlist") as caught:
            normalized(name)
        assert name not in str(caught.value)

    def test_unsafe_custom_property_is_rejected_safely(self) -> None:
        unsafe = 'x"; DROP'
        with pytest.raises(IpsProfileError, match="invalid QRadar property name") as caught:
            custom(unsafe)
        assert unsafe not in str(caught.value)

    def test_required_fields_have_no_implicit_default(self) -> None:
        with pytest.raises(TypeError):
            IpsFieldMapping()  # type: ignore[call-arg]

    def test_action_map_guards(self) -> None:
        kwargs = {
            "source_ip": normalized("sourceip"),
            "destination_ip": normalized("destinationip"),
            "destination_port": normalized("destinationport"),
            "signature": normalized("qid"),
        }
        with pytest.raises(IpsProfileError, match="both BLOCKED and ALLOWED"):
            IpsFieldMapping(
                **kwargs, action=custom("IPS_Action"), blocked_actions=("DROP",),
                allowed_actions=("drop",),
            )
        with pytest.raises(IpsProfileError, match="needs an ACTION"):
            IpsFieldMapping(**kwargs, blocked_actions=("DROP",))
        with pytest.raises(IpsProfileError, match="20-value limit"):
            IpsFieldMapping(
                **kwargs,
                action=custom("IPS_Action"),
                blocked_actions=tuple(f"v{i}" for i in range(21)),
            )
        with pytest.raises(IpsProfileError, match="64 characters"):
            IpsFieldMapping(
                **kwargs, action=custom("IPS_Action"), blocked_actions=("x" * 65,)
            )

    def test_both_presets_are_identical_safe_prefills(self) -> None:
        assert set(IPS_PRESETS) == {IpsPreset.CISCO_FTD, IpsPreset.PALO_ALTO_THREAT}
        expected = {
            "SOURCE_IP": "sourceip",
            "DESTINATION_IP": "destinationip",
            "DESTINATION_PORT": "destinationport",
            "PROTOCOL": "protocolid",
            "SIGNATURE": "qid",
            "ACTION": None,
            "SEVERITY": "severity",
        }
        for mapping in IPS_PRESETS.values():
            assert {
                field.value: mapping.reference_for(field).name
                if mapping.reference_for(field)
                else None
                for field in IpsLogicalField
            } == expected
            assert mapping.action_value_map() == {}


def field_probe(
    logical: IpsLogicalField,
    *,
    populated: int = 100,
    error: str | None = None,
    generic: int | None = None,
) -> IpsFieldProbe:
    reference = MAPPING.reference_for(logical)
    assert reference is not None
    return IpsFieldProbe(
        logical_field=logical.value,
        reference_kind=reference.kind.value,
        name=reference.name,
        populated_events=None if error else populated,
        error=error,
        generic_name_events=generic,
        action_values=(
            [IpsProbeValue(value="BLOCK", events=50)]
            if logical is IpsLogicalField.ACTION
            else []
        ),
    )


def source_probe(*fields: IpsFieldProbe, **updates: object) -> IpsSourceProbe:
    values = {
        "qradar_log_source_id": 413,
        "window_start": T0,
        "window_end": T0 + timedelta(minutes=15),
        "total_events": 100,
        "fields": list(fields),
    }
    values.update(updates)
    return IpsSourceProbe.model_validate(values)


GOOD = [field_probe(field, generic=10 if field is IpsLogicalField.SIGNATURE else None)
        for field in IpsLogicalField]


class TestValidation:
    def judge(self, probe: IpsSourceProbe):
        return evaluate_profile(MAPPING, probe, log_source_found=True, min_ratio=0.9)

    def test_valid_mapping_and_generic_qid_warning(self) -> None:
        result = self.judge(source_probe(*GOOD))
        assert result.status is IpsProfileValidationStatus.VALID
        assert result.field_status(IpsLogicalField.SIGNATURE) is IpsFieldStatus.AVAILABLE
        assert result.warnings and "Unknown QID" in result.warnings[0]

    def test_required_low_coverage_is_invalid(self) -> None:
        sparse = field_probe(IpsLogicalField.DESTINATION_PORT, populated=89)
        others = [f for f in GOOD if f.logical_field != "DESTINATION_PORT"]
        result = self.judge(source_probe(*others, sparse))
        assert result.status is IpsProfileValidationStatus.INVALID
        assert result.field_status(IpsLogicalField.DESTINATION_PORT) is (
            IpsFieldStatus.INSUFFICIENT_COVERAGE
        )

    def test_action_unmapped_still_valid(self) -> None:
        mapping = IpsFieldMapping(
            source_ip=normalized("sourceip"),
            destination_ip=normalized("destinationip"),
            destination_port=normalized("destinationport"),
            protocol=normalized("protocolid"),
            signature=normalized("qid"),
            severity=normalized("severity"),
        )
        probes = [f for f in GOOD if f.logical_field != "ACTION"]
        result = evaluate_profile(
            mapping, source_probe(*probes), log_source_found=True, min_ratio=0.9
        )
        assert result.status is IpsProfileValidationStatus.VALID
        assert result.field_status(IpsLogicalField.ACTION) is IpsFieldStatus.NOT_MAPPED

    @pytest.mark.parametrize(
        "probe",
        [
            source_probe(*GOOD, total_error="ProviderUnavailableError: timeout"),
            source_probe(*GOOD, truncated=True),
            source_probe(
                *[f for f in GOOD if f.logical_field != "SEVERITY"],
                field_probe(IpsLogicalField.SEVERITY, error="timeout"),
            ),
        ],
    )
    def test_failed_or_truncated_probe_is_never_valid(self, probe: IpsSourceProbe) -> None:
        assert self.judge(probe).status is IpsProfileValidationStatus.INCONCLUSIVE

    def test_report_discloses_only_bounded_action_values(self) -> None:
        report = json.dumps(self.judge(source_probe(*GOOD)).as_report())
        assert '"BLOCK"' in report
        assert "SELECT" not in report

    def test_missing_qradar_source_is_invalid_not_inconclusive(self) -> None:
        probe = source_probe(*GOOD, total_error="not probed")
        result = evaluate_profile(
            MAPPING, probe, log_source_found=False, min_ratio=0.9
        )
        assert result.status is IpsProfileValidationStatus.INVALID
        assert "does not exist" in result.reasons[0]


# --------------------------------------------------------- profile eligibility
def ineligible_reason(*, profile_version: int, validated: int | None) -> str | None:
    profile = IpsSourceProfile(
        instance_id=uuid.uuid4(),
        log_source_id=uuid.uuid4(),
        enabled=True,
        profile_version=profile_version,
        validation_status=IpsProfileValidationStatus.UNVALIDATED,
        validated_profile_version=validated,
    )
    return profile_ineligibility(profile)


class TestProfileIneligibility:
    def test_a_never_validated_profile_says_so_not_last_validated_none(self) -> None:
        assert ineligible_reason(profile_version=3, validated=None) == (
            "profile version 3 has never been validated"
        )

    def test_a_stale_profile_names_the_version_it_was_last_validated_at(self) -> None:
        assert ineligible_reason(profile_version=2, validated=1) == (
            "profile version 2 has not been validated (last validated: 1)"
        )


def _probe_handler():  # type: ignore[no-untyped-def]
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            query = request.url.params["query_expression"]
            queries.append(query)
            return httpx.Response(
                201, json={"search_id": f"ips-{len(queries)}", "status": "WAIT"}
            )
        if path.endswith("/results"):
            query = queries[int(path.split("/")[-2].removeprefix("ips-")) - 1]
            if '"total_events"' in query:
                rows = [{"total_events": 120}]
            elif '"generic_events"' in query:
                rows = [{"generic_events": 12}]
            elif 'AS "value"' in query:
                rows = [{"value": "BLOCK", "events": 70}, {"value": "ALLOW", "events": 50}]
            else:
                rows = [{"populated": 120}]
            return httpx.Response(200, json={"events": rows})
        if "/ariel/searches/ips-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, queries


class TestProviderProbe:
    @pytest.mark.asyncio
    async def test_rest_quotes_each_kind_and_applies_ips_population_semantics(self) -> None:
        handler, queries = _probe_handler()
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        fields = [
            IpsFieldProbeRequest(
                logical_field=logical.value,
                reference_kind=reference.kind.value,
                name=reference.name,
            )
            for logical, reference in MAPPING.mapped().items()
        ]
        result = await provider.probe_ips_fields(
            qradar_log_source_id=413,
            window_start=T0,
            window_end=T0 + timedelta(minutes=15),
            fields=fields,
            max_action_values=20,
        )
        joined = "\n".join(queries)
        assert "NOT INCIDR('127.0.0.0/8', \"sourceip\")" in joined
        assert "NOT INCIDR('127.0.0.0/8', \"destinationip\")" in joined
        # QRadar rejects LIKE on an IP-typed field (T-065).
        assert "LIKE '127." not in joined
        assert '"sourceip" != \'::1\'' in joined
        assert '"destinationport" > 0' in joined
        assert '"protocolid" > 0 AND "protocolid" < 255' in joined
        assert 'QIDNAME("qid") LIKE \'Unknown %\'' in joined
        assert '"IPS_Action" AS "value"' in joined
        assert "SUM(CASE" not in joined
        assert result.ariel_searches == len(queries) == 10
        assert result.ariel_searches <= 24
        action = next(field for field in result.fields if field.logical_field == "ACTION")
        assert [value.value for value in action.action_values] == ["BLOCK", "ALLOW"]

    @pytest.mark.asyncio
    async def test_every_search_carries_the_padded_ariel_scope(self) -> None:
        handler, queries = _probe_handler()
        provider, _ = build_provider(
            handler, page_size=50, max_pages=1, ariel_stop_pad_seconds=60
        )
        end = T0 + timedelta(minutes=15)
        provider._clock = lambda: end + timedelta(hours=1)
        await provider.probe_ips_fields(
            qradar_log_source_id=413,
            window_start=T0,
            window_end=end,
            fields=[
                IpsFieldProbeRequest(
                    logical_field=logical.value,
                    reference_kind=reference.kind.value,
                    name=reference.name,
                )
                for logical, reference in MAPPING.mapped().items()
            ],
        )
        start_ms = int(T0.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        assert queries
        for query in queries:
            # STOP is padded; the WHERE bound stays exact.
            assert f"START {start_ms} STOP {end_ms + 60_000}" in query
            assert f"starttime < {end_ms}" in query

    @pytest.mark.asyncio
    async def test_action_value_result_is_bounded_and_marks_truncation(self) -> None:
        handler, _ = _probe_handler()
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        result = await provider.probe_ips_fields(
            qradar_log_source_id=413,
            window_start=T0,
            window_end=T0 + timedelta(minutes=15),
            fields=[
                IpsFieldProbeRequest(
                    logical_field="ACTION",
                    reference_kind="CUSTOM_PROPERTY",
                    name="IPS_Action",
                )
            ],
            max_action_values=1,
        )
        assert result.truncated
        assert len(result.fields[0].action_values) == 1

    @pytest.mark.asyncio
    async def test_unsafe_reference_is_rejected_before_it_reaches_aql(self) -> None:
        handler, queries = _probe_handler()
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        unsafe = 'x"; DROP'
        result = await provider.probe_ips_fields(
            qradar_log_source_id=413,
            window_start=T0,
            window_end=T0 + timedelta(minutes=15),
            fields=[
                IpsFieldProbeRequest(
                    logical_field="ACTION",
                    reference_kind="CUSTOM_PROPERTY",
                    name=unsafe,
                )
            ],
        )
        assert result.fields[0].rejected
        assert all(unsafe not in query for query in queries)

    @pytest.mark.asyncio
    async def test_mock_parity_and_mcp_style_unsupported_provider(self) -> None:
        request = IpsFieldProbeRequest(
            logical_field="ACTION", reference_kind="CUSTOM_PROPERTY", name="IPS_Action"
        )
        result = await MockQRadarProvider(now=T0).probe_ips_fields(
            qradar_log_source_id=413,
            window_start=T0,
            window_end=T0 + timedelta(minutes=15),
            fields=[request],
        )
        assert result.total_events == 120
        assert [value.value for value in result.fields[0].action_values] == ["BLOCK", "ALLOW"]

        class Unsupported(MockQRadarProvider):
            capabilities = frozenset({ProviderCapability.INVENTORY})

            probe_ips_fields = MockQRadarProvider.__mro__[1].probe_ips_fields

        with pytest.raises(CapabilityNotSupportedError):
            await Unsupported(now=T0).probe_ips_fields(
                qradar_log_source_id=413,
                window_start=T0,
                window_end=T0 + timedelta(minutes=15),
                fields=[request],
            )


def test_ips_validation_settings_are_bounded() -> None:
    assert Settings().ips_validation_max_action_values == 20
    assert Settings().ips_validation_lock_retry_delay_seconds == 30
    assert Settings().ips_validation_lock_max_retries == 20
    with pytest.raises(ValueError, match="less than or equal to 20"):
        Settings(ips_validation_max_action_values=21)
    with pytest.raises(ValueError, match="greater than or equal to 5"):
        Settings(ips_validation_lock_retry_delay_seconds=4)
    with pytest.raises(ValueError, match="less than or equal to 100"):
        Settings(ips_validation_lock_max_retries=101)
