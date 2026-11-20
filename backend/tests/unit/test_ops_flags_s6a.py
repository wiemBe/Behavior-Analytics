"""Operator messages and the doctor's single effective switch snapshot (S6a)."""

from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cli import doctor as doctor_module
from app.cli import doctor_aql
from app.cli.doctor import CheckResult, Doctor, Status, exit_code, render_json, render_text
from app.core.config import Settings
from app.custom_rules.engine import _family_collected_message
from app.history.families import FAMILY_SPECS
from app.i18n.messages import REGISTRY, MessageRef, render
from app.models.enums import WafBreakdown
from app.models.feature_switch import FEATURE_KEYS
from app.services import feature_switches
from app.services.history_backfill import collection_state
from app.waf.profile import breakdown_plan, collection_ineligibility_message
from tests.unit.test_validation_messages import _source, _waf_profile


@pytest.mark.parametrize("status", [Status.INFO, Status.PASS, Status.SKIP])
def test_info_has_zero_rank_and_prints_whole(status: Status) -> None:
    line = CheckResult("family", status, "off")
    assert doctor_module._worst([line]) is Status.PASS
    assert exit_code([line]) == 0
    assert exit_code([line, CheckResult("warn", Status.WARN, "warn")]) == 1
    assert exit_code([line, CheckResult("fail", Status.FAIL, "fail")]) == 2
    assert render_text([line]).startswith(status.value)
    assert json.loads(render_json([line]))[0]["status"] == status.value


def test_labels_cover_every_switch_and_new_templates_have_no_env_names() -> None:
    assert set(feature_switches.FAMILY_LABELS) == set(FEATURE_KEYS)
    specs = [spec for spec in REGISTRY if spec.code.endswith(("feature_off", "feature_on"))]
    assert len(specs) == 8
    for spec in specs:
        assert "_ENABLED" not in spec.template
        assert "_ENABLED" not in render(MessageRef(spec.code, spec.sample))


def test_legacy_codes_keep_their_original_params_and_sentences() -> None:
    for code, params, text in [
        ("validation.waf.ineligible.collection_disabled", {},
         "WAF collection is disabled (WAF_COLLECTION_ENABLED=false)"),
        ("custom_rule.status.platform_off", {}, "CUSTOM_RULES_ENABLED is off"),
        ("custom_rule.family.collection_off", {"setting": "waf_collection_enabled"},
         "waf_collection_enabled is off"),
        ("history.family.switch_on", {"setting": "WAF_COLLECTION_ENABLED"},
         "WAF_COLLECTION_ENABLED is on"),
        ("history.family.switch_off", {"setting": "IPS_COLLECTION_ENABLED"},
         "IPS_COLLECTION_ENABLED is off"),
        ("history.estimate.no_collection", {"reason": MessageRef(
            "history.family.switch_off", {"setting": "IPS_COLLECTION_ENABLED"}
        )}, "no estimate: collection is switched off (IPS_COLLECTION_ENABLED is off)"),
    ]:
        assert render(MessageRef(code, params)) == text


@pytest.mark.parametrize("kind,key", [
    ("WAF_ENDPOINT", "waf_collection_enabled"),
    ("IPS_ENTITY", "ips_collection_enabled"),
    ("IPS_SOURCE", "ips_collection_enabled"),
    ("PW_VALUE", "pw_collection_enabled"),
])
def test_custom_rule_families_emit_new_codes(kind: str, key: str) -> None:
    settings = Settings().model_copy(update={key: False})
    on, ref = _family_collected_message(kind, settings)
    assert on is False
    assert ref is not None
    assert ref.code == "custom_rule.family.feature_off"
    assert ref.params == {"family": feature_switches.FAMILY_LABELS[key]}
    assert "Administration → Features" in render(ref)
    assert _family_collected_message(kind, settings.model_copy(update={key: True})) == (True, None)


def test_waf_producer_and_breakdown_reasons_name_the_page() -> None:
    settings = Settings(waf_collection_enabled=False)
    ref = collection_ineligibility_message(_waf_profile(), _source(), settings)
    assert ref is not None
    assert ref.code == "validation.waf.ineligible.feature_off"
    assert ref.params == {"family": "WAF collection"}
    off = settings.model_copy(update={
        "waf_outcome_collection_enabled": False, "waf_client_collection_enabled": False,
    })
    reasons = breakdown_plan(_waf_profile(), off).skipped
    suffix = " is off. An administrator can turn it on under Administration → Features."
    assert reasons == {
        WafBreakdown.ACTION: "outcome collection" + suffix,
        WafBreakdown.RESPONSE_CODE: "outcome collection" + suffix,
        WafBreakdown.SOURCE_IP: "client collection" + suffix,
    }
    for reason in reasons.values():
        assert "is off. An administrator can turn it on under Administration → Features." in reason
        assert "_ENABLED" not in reason


@pytest.mark.parametrize("on", [False, True])
def test_history_producer_uses_family_labels(on: bool) -> None:
    for spec in FAMILY_SPECS.values():
        if spec.collection_switch is None:
            continue
        enabled, text, ref = collection_state(
            spec, Settings().model_copy(update={spec.collection_switch: on})
        )
        assert enabled is on
        assert ref is not None
        assert ref.code == f"history.family.feature_{'on' if on else 'off'}"
        assert ref.params == {"family": feature_switches.FAMILY_LABELS[spec.collection_switch]}
        assert text == render(ref)
        assert "_ENABLED" not in text


def _doctor(monkeypatch: pytest.MonkeyPatch, base: Settings, rows: list[Any]) -> tuple[Doctor, Any]:
    session = MagicMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=None)
    session.scalars = AsyncMock(return_value=SimpleNamespace(all=lambda: rows))
    doctor = Doctor(settings=base, sessionmaker=cast(Any, lambda: session))
    retained = {"feature_switch_read", "families_off", "geo_country_source",
                "rule_match_coverage", "qradar_disk", "qradar_pipeline"}
    for check_id, _ in doctor_module._CHECK_RUNBOOKS:
        if check_id not in retained:
            monkeypatch.setattr(doctor, f"check_{check_id}", AsyncMock(
                return_value=CheckResult(check_id, Status.PASS, "healthy")
            ))
    return doctor, session


@pytest.mark.asyncio
async def test_doctor_reads_once_per_run_and_table_overrides_env(monkeypatch) -> None:
    keys = ["geo_collection_enabled", "rule_match_collection_enabled",
            "qradar_health_collection_enabled"]
    base = Settings().model_copy(update=dict.fromkeys(keys, True))
    rows = [SimpleNamespace(key=key, enabled=False, updated_at=None, updated_by="operator")
            for key in keys]
    doctor, session = _doctor(monkeypatch, base, rows)
    for call in (1, 2):
        results = {line.id: line for line in await doctor.run()}
        assert session.scalars.await_count == call
        assert results["feature_switch_read"].status is Status.PASS
        for check_id in (
            "geo_country_source", "rule_match_coverage", "qradar_disk", "qradar_pipeline"
        ):
            assert results[check_id].status is Status.INFO
            assert "Administration → Features" in results[check_id].message
        families = results["families_off"]
        assert families.status is Status.PASS
        assert "GEO_COLLECTION_ENABLED=off" in families.message.split("; on:")[0]
        assert "GEO_COLLECTION_ENABLED=table" in families.message.split("; source: ")[1]
        assert ", " not in families.message.split("; source: ")[1]


@pytest.mark.asyncio
async def test_table_on_overrides_env_off_and_missing_rows_use_env(monkeypatch) -> None:
    base = Settings(geo_collection_enabled=False, geo_country_source="lookup")
    rows = [SimpleNamespace(key="geo_collection_enabled", enabled=True,
                            updated_at=None, updated_by="operator")]
    doctor, session = _doctor(monkeypatch, base, rows)
    # Other feature checks are isolated: only geography's lookup mode needs no stored rows.
    for check_id in ("rule_match_coverage", "qradar_disk", "qradar_pipeline"):
        monkeypatch.setattr(doctor, f"check_{check_id}", AsyncMock(
            return_value=CheckResult(check_id, Status.PASS, "healthy")
        ))
    results = {line.id: line for line in await doctor.run()}
    assert session.scalars.await_count == 1
    assert results["geo_country_source"].status is Status.PASS
    assert doctor.settings.geo_collection_enabled is True
    assert "CUSTOM_RULES_ENABLED=env" in results["families_off"].message
    assert "GEO_COLLECTION_ENABLED=table" in results["families_off"].message


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["query", "session"])
async def test_unreadable_switches_fail_closed_without_exception_text(monkeypatch, failure) -> None:
    doctor, session = _doctor(monkeypatch, Settings(), [])
    error = RuntimeError("password=planted-secret")
    if failure == "query":
        session.scalars.side_effect = error
    else:
        session.__aenter__.side_effect = error
    results = {line.id: line for line in await doctor.run()}
    for check_id in ("feature_switch_read", "geo_country_source", "rule_match_coverage",
                     "qradar_disk", "qradar_pipeline"):
        assert results[check_id].status is Status.FAIL
        assert feature_switches.UNREADABLE_REASON in results[check_id].message
    assert all(not getattr(doctor.settings, key) for key in FEATURE_KEYS)
    assert "GEO_COLLECTION_ENABLED=unreadable" in results["families_off"].message
    assert "planted-secret" not in render_json(list(results.values()))


@pytest.mark.asyncio
async def test_aql_uses_the_same_snapshot_and_unreadable_reason(monkeypatch) -> None:
    base = Settings().model_copy(update=dict.fromkeys(FEATURE_KEYS, True))
    doctor, session = _doctor(monkeypatch, base, [])
    session.scalars.side_effect = RuntimeError("lost database")
    await doctor.check_feature_switch_read()
    console = SimpleNamespace(provider_kind="rest")
    lines = await doctor_aql.run_aql_checks(
        settings=doctor.settings, sessionmaker=doctor.sessionmaker, instances=[console],
        provider_factory=lambda _: pytest.fail("provider built"), now=doctor.now(),
        families=tuple(doctor_aql._SWITCHES),
        feature_read_reason=doctor.resolved_features.reason,
    )
    assert len(lines) == len(doctor_aql._SWITCHES)
    assert {line.verdict for line in lines} == {doctor_aql.FAIL}
    assert all(feature_switches.UNREADABLE_REASON in line.message for line in lines)
    assert session.scalars.await_count == 1


def test_identity_aql_switch_uses_the_search_family_name() -> None:
    from app.models.enums import SearchFamily

    assert (
        doctor_aql._SWITCHES[SearchFamily.IDENTITY_COLLECTION.value.lower()]
        == "identity_collection_enabled"
    )


@pytest.mark.parametrize("db_status,returncode", [(Status.PASS, 0), (Status.INFO, 1)])
def test_upgrade_preflight_uses_real_grep_with_info_report(tmp_path, db_status, returncode) -> None:
    # Extract only the actual shell function, just as the shell-focused unit tests do.
    source = (Path(__file__).resolve().parents[3] / "qradar-dash").read_text()
    start = source.index("upgrade_doctor_preflight() {")
    end = source.index("\n}\n", start) + 3
    report = render_text([
        CheckResult("db_connect", db_status, "database accepted SELECT 1"),
        CheckResult("migration_head", Status.PASS, "database and image agree"),
        CheckResult("geo_country_source", Status.INFO,
                    doctor_aql.feature_off_message("geo_collection_enabled")),
    ])
    (tmp_path / "doctor.txt").write_text(report)
    script = tmp_path / "preflight.sh"
    script.write_text(
        'cmd_doctor() { cat doctor.txt; }\n'
        'print_deciding_checks() { :; }\n'
        'fail() { printf "%s\\n" "$1"; }\n'
        + source[start:end] + '\nupgrade_doctor_preflight\n'
    )
    result = subprocess.run(  # noqa: S603 - repository shell function, fixture report
        ["bash", str(script)], cwd=tmp_path, capture_output=True, text=True  # noqa: S607
    )
    assert result.returncode == returncode
    if returncode:
        assert "doctor did not report db_connect and migration_head as PASS" in result.stdout


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["run", "run_aql"])
async def test_switch_read_timeout_fails_closed(monkeypatch, entrypoint) -> None:
    doctor, session = _doctor(monkeypatch, Settings(), [])

    async def too_slow(*args):
        await asyncio.sleep(1)

    session.scalars.side_effect = too_slow
    monkeypatch.setattr(doctor_module, "CHECK_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(doctor, "_enabled_instances", AsyncMock(return_value=[]))
    results = {line.id: line for line in await getattr(doctor, entrypoint)()}
    assert session.scalars.await_count == 1
    assert results["feature_switch_read"].status is Status.FAIL
    assert feature_switches.UNREADABLE_REASON in results["feature_switch_read"].message
    assert all(not getattr(doctor.settings, key) for key in FEATURE_KEYS)
    if entrypoint == "run":
        assert results["geo_country_source"].status is Status.FAIL
    else:
        assert results["aql_waf_collection"].status is Status.FAIL


@pytest.mark.asyncio
async def test_unreadable_switch_reason_survives_unreadable_consoles(monkeypatch) -> None:
    doctor, session = _doctor(monkeypatch, Settings(), [])
    session.scalars.side_effect = RuntimeError("lost database")
    monkeypatch.setattr(doctor, "_enabled_instances", AsyncMock(side_effect=RuntimeError("lost")))
    results = {line.id: line for line in await doctor.run_aql()}
    assert session.scalars.await_count == 1
    assert results["feature_switch_read"].status is Status.FAIL
    for family in doctor_aql.FAMILIES:
        if family in doctor_aql._SWITCHES:
            assert results[f"aql_{family}"].status is Status.FAIL
            assert feature_switches.UNREADABLE_REASON in results[f"aql_{family}"].message


@pytest.mark.asyncio
async def test_aql_on_run_reuses_the_successful_switch_read(monkeypatch) -> None:
    base = Settings().model_copy(update=dict.fromkeys(FEATURE_KEYS, False))
    doctor, session = _doctor(monkeypatch, base, [])
    doctor.check_aql = True
    monkeypatch.setattr(doctor, "_enabled_instances", AsyncMock(return_value=[]))
    results = await doctor.run()
    assert session.scalars.await_count == 1
    assert sum(line.id == "feature_switch_read" for line in results) == 1
    assert all(line.status is Status.SKIP for line in results if line.id.startswith("aql_"))
