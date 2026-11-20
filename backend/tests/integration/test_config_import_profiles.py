"""Real import commands for the WAF, IPS and identity profile sections (CFG-1g2).

Every case runs ``python -m app.cli.config import`` as a subprocess. A created profile is
disabled and ``UNVALIDATED``; a model failure after the adapter is reported ``unresolved`` with
a fixed reason and inserts nothing; an existing profile that differs is a conflict.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, build_document
from app.identity import reference
from app.models.identity import AuditLog
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
IPS = {"name": "Edge IPS", "type_name": "Cisco Firepower"}
WIN = {"name": "DC Security", "type_name": "Microsoft Windows Security Event Log"}
PROFILES = ("waf_source_profiles", "ips_source_profiles", "identity_source_profiles")
FTD_MAPPING: dict[str, Any] = {
    "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
    "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
    "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "destinationport"},
    "PROTOCOL": {"kind": "NORMALIZED", "name": "protocolid"},
    "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
    "ACTION": None,
    "SEVERITY": {"kind": "NORMALIZED", "name": "severity"},
}
WAF_ROW: dict[str, Any] = {
    "log_source": WAF, "enabled_on_source": True, "product_label": "F5 ASM",
    "url_property": "URL", "method_property": "Method", "virtual_host_property": None,
    "action_property": "Action", "response_code_property": "ResponseCode",
    "rule_id_property": None, "user_agent_property": None,
    "action_value_map": {"BLOCKED": ["blocked"], "ALLOWED": ["allowed"]},
}
IPS_ROW: dict[str, Any] = {
    "log_source": IPS, "enabled_on_source": True, "product_label": "FTD", "preset": "CISCO_FTD",
    "mapping": FTD_MAPPING, "action_value_map": {},
}
ID_ROW: dict[str, Any] = {
    "log_source": WIN, "enabled_on_source": False, "product_label": "Windows",
    "event_id_property": "EventID", "username_property": "username",
    "logon_type_property": None, "enabled_event_ids": [4625, 4740, 4720],
}
#: The three-way provider and secret tripwire the 1g tests use, in the child process only.
HOOK = '''
from app.providers import factory
from app.models.base import EncryptedString
def fail(*args, **kwargs):
    raise AssertionError("provider or secret was read")
factory.build_provider = factory.build_provider_for_instance = fail
EncryptedString.process_result_value = fail
'''


def _document(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    return build_document(product_version="test", exported_at=datetime.now(UTC),
                          source_label="preprod", sections=sections)


def _all(**overrides: dict[str, Any]) -> dict[str, Any]:
    """The three documented rows, each replaceable by name (``waf``, ``ips``, ``identity``)."""
    rows = {"waf": WAF_ROW, "ips": IPS_ROW, "identity": ID_ROW} | overrides
    return _document(waf_source_profiles=[rows["waf"]], ips_source_profiles=[rows["ips"]],
                     identity_source_profiles=[rows["identity"]])


def _cli(url: str, tmp_path: Path, command: str, *args: str,
         document: dict[str, Any] | None = None) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    (tmp_path / "sitecustomize.py").write_text(HOOK)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432),
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(BACKEND))),
    }
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", command, *args], cwd=BACKEND, env=env,
        input=json.dumps(document) if document is not None else None,
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert "Error in sitecustomize" not in done.stderr, done.stderr
    return done


def _import(url: str, tmp_path: Path, document: dict[str, Any], mode: str = "--apply") -> Any:
    args = (mode, "--yes", "--actor", "soc") if mode == "--apply" else (mode,)
    done = _cli(url, tmp_path, "import", *args, document=document)
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr
    report = json.loads(done.stdout)
    assert list(report["sections"]) == list(SECTION_ORDER)
    return report


def _lines(report: Any, section: str, outcome: str) -> list[dict[str, str]]:
    lines: list[dict[str, str]] = report["sections"][section][outcome]
    return lines


async def _fleet(session: AsyncSession, *, duplicate: str | None = None,
                 skip: tuple[str, ...] = ()) -> dict[str, LogSource]:
    console = QRadarInstance(name="prod", console_host="https://lab.invalid", sec_token="secret")
    session.add(console)
    await session.flush()
    sources: dict[str, LogSource] = {}
    for number, (label, ref) in enumerate((("waf", WAF), ("ips", IPS), ("identity", WIN)), 1):
        if label in skip:
            continue
        for copy_number in range(2 if duplicate == label else 1):
            source = LogSource(instance_id=console.id, qradar_id=number * 10 + copy_number,
                               name=ref["name"], type_name=ref["type_name"])
            session.add(source)
            sources[label] = source
    await session.commit()
    return sources


async def _count(session: AsyncSession) -> list[int]:
    return [await session.scalar(select(func.count()).select_from(model)) or 0
            for model in (WafSourceProfile, IpsSourceProfile, IdentitySourceProfile)]


async def test_import_waf_profile_adapts_property_columns(db_schema, db_session, tmp_path):
    sources = await _fleet(db_session)
    row = {**WAF_ROW, "virtual_host_property": "Host", "user_agent_property": "UserAgent",
           "rule_id_property": "RuleId", "action_value_map": {"BLOCKED": ["b"], "ALLOWED": ["a"]}}
    report = _import(db_schema, tmp_path, _document(waf_source_profiles=[row]))
    assert report["committed"] is True
    assert _lines(report, "waf_source_profiles", "created") == [{
        "key": "Edge WAF|F5 Networks BIG-IP ASM", "local_id": str(sources["waf"].id),
        "reason": "profile inserted disabled and UNVALIDATED"}]
    profile = await db_session.scalar(select(WafSourceProfile))
    assert (profile.url_property, profile.method_property, profile.virtual_host_property,
            profile.action_property, profile.response_code_property, profile.rule_id_property,
            profile.user_agent_property) == (
        "URL", "Method", "Host", "Action", "ResponseCode", "RuleId", "UserAgent")
    assert profile.action_value_map == {"BLOCKED": ["b"], "ALLOWED": ["a"]}
    assert profile.product_label == "F5 ASM"
    assert profile.log_source_id == sources["waf"].id


async def test_import_waf_profile_with_no_action_map_stores_an_empty_map(
    db_schema, db_session, tmp_path
):
    """Export writes `{}` for a profile with no action lists; import must accept its own file."""
    await _fleet(db_session)
    row = {**WAF_ROW, "action_property": None, "action_value_map": {}}
    report = _import(db_schema, tmp_path, _document(waf_source_profiles=[row]))
    assert len(_lines(report, "waf_source_profiles", "created")) == 1
    assert (await db_session.scalar(select(WafSourceProfile))).action_value_map == {}


async def test_import_cisco_ftd_empty_action_map(db_schema, db_session, tmp_path):
    sources = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(ips_source_profiles=[IPS_ROW]))
    assert _lines(report, "ips_source_profiles", "created")[0]["local_id"] == str(
        sources["ips"].id)
    profile = await db_session.scalar(select(IpsSourceProfile))
    assert profile.action_value_map == {}
    assert profile.mapping == FTD_MAPPING and profile.mapping["ACTION"] is None
    assert str(profile.preset) == "CISCO_FTD"
    # A missing map (not just `{}`) takes the same path.
    await db_session.delete(profile)
    await db_session.commit()
    without = {key: value for key, value in IPS_ROW.items() if key != "action_value_map"}
    assert _lines(_import(db_schema, tmp_path, _document(ips_source_profiles=[without])),
                  "ips_source_profiles", "created")
    await db_session.rollback()
    assert (await db_session.scalar(select(IpsSourceProfile))).action_value_map == {}


async def test_created_profiles_are_disabled_and_unvalidated(db_schema, db_session, tmp_path):
    sources = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _all())
    for name in PROFILES:
        assert len(_lines(report, name, "created")) == 1
    for model in (WafSourceProfile, IpsSourceProfile, IdentitySourceProfile):
        profile = await db_session.scalar(select(model))
        assert profile.enabled is False and profile.profile_version == 1
        assert str(profile.validation_status) == "UNVALIDATED"
        assert profile.validated_profile_version is None and profile.validated_at is None
        assert profile.validation_report == {}
        assert profile.validation_requested_at is None
        assert profile.validation_requested_by is None and profile.updated_by == "soc"
        assert profile.instance_id == sources["waf"].instance_id
    identity = await db_session.scalar(select(IdentitySourceProfile))
    assert identity.enabled_event_ids == [4625, 4720, 4740]
    assert [audit.action for audit in await db_session.scalars(select(AuditLog))] == [
        "config.import.apply"]


async def test_second_apply_is_unchanged_even_after_the_operator_enabled_it(
    db_schema, db_session, tmp_path
):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _all())
    for model in (WafSourceProfile, IpsSourceProfile, IdentitySourceProfile):
        profile = await db_session.scalar(select(model))
        profile.enabled, profile.validation_status = True, "VALID"
        profile.validated_profile_version, profile.profile_version = 1, 1
    await db_session.commit()
    second = _import(db_schema, tmp_path, _all())
    assert second["committed"] is True
    for name in PROFILES:
        assert len(_lines(second, name, "unchanged")) == 1
        assert _lines(second, name, "created") == []
    assert (await _count(db_session)) == [1, 1, 1]
    assert (await db_session.scalar(select(WafSourceProfile))).enabled is True


async def test_identity_event_ids_in_catalog_order_are_not_a_conflict(
    db_schema, db_session, tmp_path
):
    """The model default stores catalog order; the file is sorted. The same set is unchanged."""
    sources = await _fleet(db_session)
    db_session.add(IdentitySourceProfile(
        instance_id=sources["identity"].instance_id, log_source_id=sources["identity"].id,
        product_label="Windows", event_id_property="EventID", username_property="username",
        enabled_event_ids=[4740, 4625, 4720]))
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(identity_source_profiles=[ID_ROW]))
    assert len(_lines(report, "identity_source_profiles", "unchanged")) == 1


async def test_a_different_existing_profile_is_a_conflict_and_is_not_written(
    db_schema, db_session, tmp_path
):
    sources = await _fleet(db_session)
    instance_id = sources["waf"].instance_id
    db_session.add_all([
        WafSourceProfile(instance_id=instance_id, log_source_id=sources["waf"].id,
                         url_property="Other", method_property="Method", action_value_map={}),
        IpsSourceProfile(instance_id=instance_id, log_source_id=sources["ips"].id,
                         mapping=FTD_MAPPING, action_value_map={}, product_label="Local"),
        IdentitySourceProfile(instance_id=instance_id, log_source_id=sources["identity"].id,
                              event_id_property="qid", username_property="username",
                              enabled_event_ids=[4625]),
    ])
    await db_session.commit()
    report = _import(db_schema, tmp_path, _all())
    for name in PROFILES:
        line = _lines(report, name, "conflict")[0]
        assert line["reason"] == "a profile for this log source already has a different mapping"
        assert line["local_id"]
    assert (await db_session.scalar(select(WafSourceProfile))).url_property == "Other"
    assert (await db_session.scalar(select(IpsSourceProfile))).product_label == "Local"
    assert (await db_session.scalar(select(IdentitySourceProfile))).enabled_event_ids == [4625]


def _bad(base: dict[str, Any], **changes: Any) -> dict[str, Any]:
    return {**copy.deepcopy(base), **changes}


BAD_ROWS = [
    ("waf", _bad(WAF_ROW, url_property='bad"name'), "validation.waf.input.property_name"),
    ("waf", _bad(WAF_ROW, url_property=None), "row validation failed"),
    ("waf", _bad(WAF_ROW, action_property=None), "validation.waf.input.action_needs_property"),
    ("waf", _bad(WAF_ROW, action_value_map={"BLOCKED": ["x"], "ALLOWED": ["X"]}),
     "validation.waf.input.action_overlap"),
    ("waf", _bad(WAF_ROW, action_value_map={"BLOCKED": ["x"]}), "row validation failed"),
    ("waf", _bad(WAF_ROW, product_label="x" * 129), "row validation failed"),
    ("waf", _bad(WAF_ROW, enabled_on_source="yes"), "enabled_on_source must be a boolean"),
    ("waf", {**WAF_ROW, "extra": 1}, "row fields do not match the section"),
    ("ips", _bad(IPS_ROW, mapping={**FTD_MAPPING, "SIGNATURE": {"kind": "NORMALIZED",
                                                              "name": "nope"}}),
     "validation.ips.input.normalized_field"),
    ("ips", _bad(IPS_ROW, mapping={**FTD_MAPPING, "SOURCE_IP": None}), "row validation failed"),
    ("ips", _bad(IPS_ROW, mapping={**FTD_MAPPING, "EXTRA": None}),
     "IPS mapping keys are not logical fields"),
    ("ips", _bad(IPS_ROW, action_value_map={"BLOCKED": ["drop"], "ALLOWED": []}),
     "validation.ips.input.action_needs_field"),
    ("ips", _bad(IPS_ROW, preset="NOT_A_PRESET"), "row validation failed"),
    ("identity", _bad(ID_ROW, username_property='bad"name'), reference._USERNAME_REFUSED),
    ("identity", _bad(ID_ROW, enabled_event_ids=[1]), "event ids must be catalog integers"),
    ("identity", _bad(ID_ROW, enabled_event_ids="4625"), "enabled_event_ids must be a list"),
    ("identity", _bad(ID_ROW, product_label="x" * 129),
     "product label is not text of at most 128 characters"),
]


@pytest.mark.parametrize(("label", "row", "reason"), BAD_ROWS,
                         ids=[f"{row[0]}-{index}" for index, row in enumerate(BAD_ROWS)])
async def test_a_model_failure_is_unresolved_and_inserts_nothing(
    db_schema, db_session, tmp_path, label, row, reason
):
    sources = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _all(**{label: row}))
    section = {"waf": PROFILES[0], "ips": PROFILES[1], "identity": PROFILES[2]}[label]
    lines = _lines(report, section, "unresolved")
    assert [line["reason"] for line in lines] == [reason]
    assert lines[0]["local_id"] == str(sources[label].id)
    assert (await _count(db_session)).count(0) == 1 and sum(await _count(db_session)) == 2
    for other in set(PROFILES) - {section}:
        assert len(_lines(report, other, "created")) == 1
    assert 'bad"name' not in json.dumps(report) and "nope" not in json.dumps(report)


async def test_missing_and_ambiguous_log_sources_are_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session, duplicate="ips", skip=("identity",))
    report = _import(db_schema, tmp_path, _all())
    assert len(_lines(report, "waf_source_profiles", "created")) == 1
    for name in PROFILES[1:]:
        (line,) = _lines(report, name, "unresolved")
        assert "local_id" not in line
    assert "2 log sources" in _lines(report, "ips_source_profiles", "unresolved")[0]["reason"]
    assert await _count(db_session) == [1, 0, 0]


async def test_dry_run_reports_the_write_but_makes_none(db_schema, db_session, tmp_path):
    sources = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _all(), mode="--dry-run")
    assert report["mode"] == "dry-run" and report["committed"] is False
    for name, label in zip(PROFILES, ("waf", "ips", "identity"), strict=True):
        (line,) = _lines(report, name, "created")
        assert line["local_id"] == str(sources[label].id)
        assert line["reason"] == (
            "no profile for this log source; would insert disabled and UNVALIDATED")
    assert await _count(db_session) == [0, 0, 0]


async def test_round_trip_export_wipe_import_export_is_equal(db_schema, db_session, tmp_path):
    sources = await _fleet(db_session)
    instance_id = sources["waf"].instance_id
    db_session.add_all([
        WafSourceProfile(instance_id=instance_id, log_source_id=sources["waf"].id,
                         enabled=True, url_property="URL", method_property="Method",
                         action_property="Action", action_value_map={"BLOCKED": ["b"],
                                                                     "ALLOWED": ["a"]}),
        IpsSourceProfile(instance_id=instance_id, log_source_id=sources["ips"].id,
                         preset="CISCO_FTD", mapping=FTD_MAPPING, action_value_map={}),
        IdentitySourceProfile(instance_id=instance_id, log_source_id=sources["identity"].id,
                              event_id_property="EventID", username_property="username",
                              logon_type_property="LogonType",
                              enabled_event_ids=[4625, 4720, 4740]),
    ])
    await db_session.commit()

    def export() -> dict[str, list[dict[str, Any]]]:
        done = _cli(db_schema, tmp_path, "export")
        assert done.returncode == 0, done.stderr
        sections = json.loads(done.stdout)["sections"]
        return {name: sections[name] for name in PROFILES}

    before = export()
    for model in (WafSourceProfile, IpsSourceProfile, IdentitySourceProfile):
        for profile in await db_session.scalars(select(model)):
            await db_session.delete(profile)
    await db_session.commit()
    assert await _count(db_session) == [0, 0, 0]
    report = _import(db_schema, tmp_path, _document(**before))
    assert sum(len(_lines(report, name, "created")) for name in PROFILES) == 3
    after = export()
    # `enabled_on_source` is the old console's flag: an imported profile starts disabled.
    for name in PROFILES:
        assert [row["enabled_on_source"] for row in after[name]] == [False]
        assert [{k: v for k, v in row.items() if k != "enabled_on_source"}
                for row in after[name]] == [
            {k: v for k, v in row.items() if k != "enabled_on_source"} for row in before[name]]
