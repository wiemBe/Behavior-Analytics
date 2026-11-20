"""Real import commands for the rule SOC, property watch and scheduled search sections (CFG-1i).

Every case runs ``python -m app.cli.config import`` as a subprocess whose ``sitecustomize``
fails on any provider build, any Ariel search call and any encrypted-column read. A created watch
or search is disabled; a failing validator reports a fixed reason and stores nothing; a second
import of the same file is ``unchanged``, also after the operator enabled the row.
"""

from __future__ import annotations

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
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch, PwValue
from app.models.rule import AnalyticsRule, TechniqueMapping
from app.models.search import ScheduledSearch, SearchQueryVersion

pytestmark = pytest.mark.integration

BACKEND = Path(__file__).resolve().parents[2]
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
RULE = {"name": "Excessive Firewall Denies", "rule_type": "EVENT", "is_building_block": False}
HOOK = '''
from app.providers import base, factory, mock, qradar_rest
from app.models.base import EncryptedString
def fail(*args, **kwargs):
    raise AssertionError("provider, Ariel search or secret was used")
factory.build_provider = factory.build_provider_for_instance = fail
for cls in (base.QRadarProvider, mock.MockQRadarProvider, qradar_rest.QRadarRestProvider):
    cls.create_ariel_search = fail
EncryptedString.process_result_value = fail
'''

RULE_ROW: dict[str, Any] = {
    "rule": RULE, "owner": "soc", "mitre_techniques": ["T1046"], "soc_notes": None,
    "expected_daily_firings": 20.0, "health_monitoring_enabled": False,
}
MAPPING: dict[str, Any] = {
    "technique_id": "T1046", "technique_name": "Network Service Discovery", "tactic": "discovery",
    "rule": RULE, "source": "EXPLICIT", "confidence": 0.8, "provenance": None,
}
WATCH: dict[str, Any] = {
    "name": "Denied actions", "description": None, "enabled_on_source": True,
    "scope_kind": "LOG_SOURCE", "log_source": WAF, "scope_type": None, "scope_group": None,
    "properties": [{"kind": "CUSTOM_PROPERTY", "name": "Action"}],
    "pinned_values": [["blocked"]], "detectors": ["PW_NEW_VALUE"], "severity": "MEDIUM",
}
AQL = ("SELECT SUM(eventcount) FROM events WHERE LOGSOURCETYPENAME(devicetype) = "
       "'F5 Networks BIG-IP ASM' LAST 1 HOURS")
SEARCH: dict[str, Any] = {
    "name": "Denied by WAF", "description": None, "owner": "soc", "category": None,
    "mitre_techniques": [], "aql_query": AQL, "schedule_cron": "0 * * * *",
    "schedule_timezone": "UTC", "threshold_value": None, "threshold_operator": "GT",
    "severity": "MEDIUM", "timeout_seconds": 300, "max_time_range_hours": 24,
    "max_result_rows": 10000, "visualization_type": "TABLE", "enabled_on_source": True,
}


def _document(**sections: list[dict[str, Any]]) -> dict[str, Any]:
    return build_document(product_version="test", exported_at=datetime.now(UTC),
                          source_label="preprod", sections=sections)


def _cli(url: str, tmp_path: Path, *args: str,
         document: dict[str, Any]) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    (tmp_path / "sitecustomize.py").write_text(HOOK)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432),
        "PYTHONPATH": os.pathsep.join((str(tmp_path), str(BACKEND))),
    }
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", "import", *args], cwd=BACKEND, env=env,
        input=json.dumps(document), capture_output=True, text=True, timeout=120, check=False,
    )
    assert "Error in sitecustomize" not in done.stderr, done.stderr
    return done


def _import(url: str, tmp_path: Path, document: dict[str, Any], mode: str = "--apply",
            *extra: str) -> Any:
    args = (mode, "--yes", "--actor", "soc") if mode == "--apply" else (mode,)
    done = _cli(url, tmp_path, *args, *extra, document=document)
    assert done.returncode == 0, done.stderr
    assert "Traceback" not in done.stderr
    report = json.loads(done.stdout)
    assert list(report["sections"]) == list(SECTION_ORDER)
    return report


def _lines(report: Any, section: str, outcome: str) -> list[dict[str, str]]:
    return report["sections"][section][outcome]


async def _fleet(session: AsyncSession) -> dict[str, Any]:
    console = QRadarInstance(name="prod", console_host="https://lab.invalid", sec_token="secret")
    session.add(console)
    await session.flush()
    source = LogSource(instance_id=console.id, qradar_id=10, name=WAF["name"],
                       type_name=WAF["type_name"])
    rule = AnalyticsRule(instance_id=console.id, qradar_id=11, name=RULE["name"],
                         rule_type=RULE["rule_type"], is_building_block=False)
    session.add_all([source, rule, LogSourceType(
        instance_id=console.id, type_id=5, name=WAF["type_name"], internal=False, custom=False,
        synced_at=datetime.now(UTC)), LogSourceGroup(
        instance_id=console.id, qradar_id=7, name="DMZ", parent_qradar_id=None, assignable=True,
        synced_at=datetime.now(UTC))])
    await session.commit()
    return {"console": console, "source": source, "rule": rule}


async def _count(session: AsyncSession, *models: Any) -> list[int]:
    return [await session.scalar(select(func.count()).select_from(model)) or 0 for model in models]


# --------------------------------------------------------------------- rule SOC sections
async def test_rule_settings_apply_to_a_default_rule_then_are_unchanged(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(analytics_rule_settings=[RULE_ROW]))
    assert _lines(report, "analytics_rule_settings", "created") == [{
        "key": "Excessive Firewall Denies|EVENT|false", "reason": "applied configuration"}]
    await db_session.refresh(fleet["rule"])
    rule = fleet["rule"]
    assert (rule.owner, rule.mitre_techniques, rule.expected_daily_firings,
            rule.health_monitoring_enabled) == ("soc", ["T1046"], 20.0, False)
    again = _import(db_schema, tmp_path, _document(analytics_rule_settings=[RULE_ROW]))
    assert len(_lines(again, "analytics_rule_settings", "unchanged")) == 1


async def test_rule_settings_non_default_difference_conflicts_without_writing(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    fleet["rule"].owner = "operator"
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(analytics_rule_settings=[RULE_ROW]))
    assert len(_lines(report, "analytics_rule_settings", "conflict")) == 1
    await db_session.refresh(fleet["rule"])
    assert fleet["rule"].owner == "operator" and fleet["rule"].mitre_techniques == []
    assert fleet["rule"].health_monitoring_enabled is True


async def test_rule_that_does_not_exist_is_unresolved_and_never_created(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    other = {**RULE_ROW, "rule": {**RULE, "name": "Not synced"}}
    mapping = {**MAPPING, "rule": {**RULE, "rule_type": None}}
    report = _import(db_schema, tmp_path, _document(
        analytics_rule_settings=[other], technique_mappings=[mapping]))
    assert len(_lines(report, "analytics_rule_settings", "unresolved")) == 1
    assert len(_lines(report, "technique_mappings", "unresolved")) == 1
    assert await _count(db_session, AnalyticsRule, TechniqueMapping) == [1, 0]


async def test_ambiguous_rule_is_not_guessed(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    db_session.add(AnalyticsRule(instance_id=fleet["console"].id, qradar_id=12, name=RULE["name"],
                                 rule_type="EVENT", is_building_block=False))
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(
        analytics_rule_settings=[RULE_ROW], technique_mappings=[MAPPING]))
    assert len(_lines(report, "analytics_rule_settings", "unresolved")) == 1
    assert len(_lines(report, "technique_mappings", "unresolved")) == 1
    assert "2 analytics rules" in _lines(report, "technique_mappings", "unresolved")[0]["reason"]
    assert await _count(db_session, TechniqueMapping) == [0]
    rows = list(await db_session.scalars(select(AnalyticsRule)))
    assert all(row.owner is None for row in rows)


BAD_RULE_ROWS = [
    {**RULE_ROW, "health_monitoring_enabled": None},
    {**RULE_ROW, "health_monitoring_enabled": "yes"},
    {**RULE_ROW, "mitre_techniques": None},
    {**RULE_ROW, "mitre_techniques": "T1046"},
    {**RULE_ROW, "expected_daily_firings": float("nan")},
    {**RULE_ROW, "expected_daily_firings": True},
    {**RULE_ROW, "owner": "x" * 256},
    {k: v for k, v in RULE_ROW.items() if k != "owner"},
]


@pytest.mark.parametrize("row", BAD_RULE_ROWS, ids=range(len(BAD_RULE_ROWS)))
async def test_invalid_rule_settings_are_unresolved_and_a_sibling_commits(
        db_schema, db_session, tmp_path, row):
    fleet = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        analytics_rule_settings=[row], technique_mappings=[MAPPING]))
    assert len(_lines(report, "analytics_rule_settings", "unresolved")) == 1
    assert len(_lines(report, "technique_mappings", "created")) == 1
    assert report["committed"] is True
    await db_session.refresh(fleet["rule"])
    assert fleet["rule"].owner is None and fleet["rule"].health_monitoring_enabled is True


async def test_technique_mapping_is_explicit_and_merges_by_rule_and_technique(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(technique_mappings=[MAPPING]))
    assert _lines(report, "technique_mappings", "created") == [{
        "key": "T1046|Excessive Firewall Denies|EVENT|false", "reason": "applied configuration"}]
    row = await db_session.scalar(select(TechniqueMapping))
    assert (row.rule_id, row.instance_id, row.source, row.confidence, row.tactic) == (
        fleet["rule"].id, fleet["console"].id, "EXPLICIT", 0.8, "discovery")
    again = _import(db_schema, tmp_path, _document(technique_mappings=[MAPPING]))
    assert len(_lines(again, "technique_mappings", "unchanged")) == 1
    different = {**MAPPING, "confidence": 0.5}
    third = _import(db_schema, tmp_path, _document(technique_mappings=[different]))
    assert len(_lines(third, "technique_mappings", "conflict")) == 1
    await db_session.refresh(row)
    assert row.confidence == 0.8
    assert await _count(db_session, TechniqueMapping) == [1]


async def test_inferred_mapping_under_the_same_key_is_a_conflict_not_an_error(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    db_session.add(TechniqueMapping(
        instance_id=fleet["console"].id, technique_id="T1046", rule_id=fleet["rule"].id,
        source="INFERRED", confidence=0.4, provenance="rule text"))
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(technique_mappings=[MAPPING]))
    assert len(_lines(report, "technique_mappings", "conflict")) == 1
    assert report["committed"] is True
    assert (await db_session.scalar(select(TechniqueMapping))).source == "INFERRED"


BAD_MAPPINGS = [
    {**MAPPING, "source": "INFERRED"},
    {**MAPPING, "confidence": 1.5},
    {**MAPPING, "confidence": True},
    {**MAPPING, "technique_id": "T"},
    {**MAPPING, "tactic": "x" * 129},
]


@pytest.mark.parametrize("row", BAD_MAPPINGS, ids=range(len(BAD_MAPPINGS)))
async def test_invalid_technique_mappings_are_unresolved(db_schema, db_session, tmp_path, row):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(technique_mappings=[row],
                                                    analytics_rule_settings=[RULE_ROW]))
    assert len(_lines(report, "technique_mappings", "unresolved")) == 1
    assert len(_lines(report, "analytics_rule_settings", "created")) == 1
    assert await _count(db_session, TechniqueMapping) == [0]


# --------------------------------------------------------------------------- property watches
async def test_watch_is_created_disabled_without_history_or_values(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(property_watches=[WATCH]))
    assert _lines(report, "property_watches", "created") == [{
        "key": "Denied actions", "reason": "applied configuration"}]
    watch = await db_session.scalar(select(PropertyWatch))
    assert (watch.enabled, watch.watch_version, watch.history_days, watch.history_state) == (
        False, 1, None, "NOT_REQUESTED")
    assert (watch.collected_through, watch.evaluated_through, watch.last_status,
            watch.last_run_at, watch.history_through) == (None, None, None, None, None)
    assert (watch.log_source_id, watch.scope_type_id, watch.scope_group_id) == (
        fleet["source"].id, None, None)
    assert (watch.instance_id, watch.created_by, watch.updated_by) == (
        fleet["console"].id, "soc", "soc")
    assert watch.pinned_values == [["blocked"]] and watch.detectors == ["PW_NEW_VALUE"]
    assert await _count(db_session, PwValue) == [0]


async def test_second_import_is_unchanged_after_the_operator_enabled_the_watch(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(property_watches=[WATCH]))
    watch = await db_session.scalar(select(PropertyWatch))
    watch.enabled, watch.history_days, watch.last_status = True, 7, "OK"
    await db_session.commit()
    again = _import(db_schema, tmp_path, _document(property_watches=[WATCH]))
    assert len(_lines(again, "property_watches", "unchanged")) == 1
    await db_session.refresh(watch)
    assert (watch.enabled, watch.history_days, watch.watch_version) == (True, 7, 1)


async def test_watch_that_differs_is_a_conflict_and_writes_nothing(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(property_watches=[WATCH]))
    changed = {**WATCH, "severity": "HIGH", "pinned_values": [["allowed"]]}
    report = _import(db_schema, tmp_path, _document(property_watches=[changed]))
    assert len(_lines(report, "property_watches", "conflict")) == 1
    watch = await db_session.scalar(select(PropertyWatch))
    await db_session.refresh(watch)
    assert (watch.severity, watch.pinned_values) == ("MEDIUM", [["blocked"]])


async def test_watch_scopes_resolve_to_this_consoles_type_and_group(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    by_type = {**WATCH, "name": "by type", "scope_kind": "LOG_SOURCE_TYPE", "log_source": None,
               "scope_type": {"name": WAF["type_name"]}}
    by_group = {**WATCH, "name": "by group", "scope_kind": "LOG_SOURCE_GROUP",
                "log_source": None, "scope_group": {"name": "DMZ", "parent_name": None}}
    report = _import(db_schema, tmp_path, _document(property_watches=[by_type, by_group]))
    assert len(_lines(report, "property_watches", "created")) == 2
    rows = {row.name: row for row in await db_session.scalars(select(PropertyWatch))}
    assert (rows["by type"].scope_type_id, rows["by type"].log_source_id) == (5, None)
    assert (rows["by group"].scope_group_id, rows["by group"].log_source_id) == (7, None)


BAD_WATCHES = [
    {**WATCH, "log_source": {"name": "Nope", "type_name": None}},
    {**WATCH, "scope_kind": "LOG_SOURCE_TYPE"},
    {**WATCH, "scope_kind": "LOG_SOURCE", "scope_type": {"name": "x"}},
    {**WATCH, "scope_kind": "SOURCE"},
    {**WATCH, "scope_kind": "LOG_SOURCE_TYPE", "log_source": None,
     "scope_type": {"name": "Unknown type"}},
    {**WATCH, "scope_kind": "LOG_SOURCE_GROUP", "log_source": None,
     "scope_group": {"name": "Unknown", "parent_name": None}},
    {**WATCH, "pinned_values": [[f"v{i}"] for i in range(21)]},
    {**WATCH, "pinned_values": [["a", "b"]]},
    {**WATCH, "pinned_values": [["has a quote '"]]},
    {**WATCH, "pinned_values": [["blocked"], ["blocked"]]},
    {**WATCH, "properties": []},
    {**WATCH, "properties": [{"kind": "CUSTOM_PROPERTY", "name": "bad name'"}]},
    {**WATCH, "properties": [{"kind": "CUSTOM_PROPERTY", "name": "a"},
                             {"kind": "CUSTOM_PROPERTY", "name": "a"}]},
    {**WATCH, "detectors": []},
    {**WATCH, "detectors": None},
    {**WATCH, "detectors": ["PW_NEW_COMBINATION"]},
    {**WATCH, "detectors": ["NOPE"]},
    {**WATCH, "severity": "SEVERE"},
    {**WATCH, "name": ""},
    {**WATCH, "name": "n" * 81},
    {**WATCH, "enabled_on_source": "yes"},
]


@pytest.mark.parametrize("row", BAD_WATCHES, ids=range(len(BAD_WATCHES)))
async def test_invalid_watches_are_unresolved_and_a_sibling_commits(
        db_schema, db_session, tmp_path, row):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        property_watches=[row], scheduled_searches=[SEARCH]))
    assert len(_lines(report, "property_watches", "unresolved")) == 1, report
    assert await _count(db_session, PropertyWatch) == [0]
    assert len(_lines(report, "scheduled_searches", "created")) == 1
    assert report["committed"] is True
    assert "bad name" not in json.dumps(report) and "has a quote" not in json.dumps(report)


async def test_watch_created_in_the_file_is_resolved_by_a_policy_in_the_same_file(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    policy = {"scope": "global", "scope_kind": "SOURCE", "family": "property_watch",
              "log_source": None, "source_class": None, "watch": {"name": WATCH["name"]},
              "detector_type": "PW_NEW_VALUE", "mode": "RECORD_ONLY"}
    report = _import(db_schema, tmp_path, _document(
        property_watches=[WATCH], alert_policies=[policy]))
    assert len(_lines(report, "alert_policies", "created")) == 1
    again = _import(db_schema, tmp_path, _document(
        property_watches=[WATCH], alert_policies=[policy]))
    assert len(_lines(again, "alert_policies", "unchanged")) == 1


async def test_watch_name_taken_on_another_console_is_a_conflict_not_an_error(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    other = QRadarInstance(name="other", console_host="https://other.invalid", sec_token="secret")
    db_session.add(other)
    await db_session.flush()
    # Same definition as the file's row (type scope 5), so only the owning console differs.
    db_session.add(PropertyWatch(
        instance_id=other.id, name=WATCH["name"], scope_kind="LOG_SOURCE_TYPE", scope_type_id=5,
        properties=WATCH["properties"], pinned_values=WATCH["pinned_values"],
        detectors=WATCH["detectors"], severity="MEDIUM"))
    await db_session.commit()
    row = {**WATCH, "scope_kind": "LOG_SOURCE_TYPE", "log_source": None,
           "scope_type": {"name": WAF["type_name"]}}
    done = _cli(db_schema, tmp_path, "--apply", "--yes", "--actor", "soc", "--instance", "prod",
                document=_document(property_watches=[row]))
    assert done.returncode == 0, done.stderr
    report = json.loads(done.stdout)
    assert len(_lines(report, "property_watches", "conflict")) == 1
    assert await _count(db_session, PropertyWatch) == [1]


# ---------------------------------------------------------------------- scheduled searches
async def test_search_is_stored_disabled_with_one_initial_version(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(scheduled_searches=[SEARCH]))
    assert _lines(report, "scheduled_searches", "created") == [{
        "key": "Denied by WAF", "reason": "applied configuration"}]
    search = await db_session.scalar(select(ScheduledSearch))
    assert (search.enabled, search.query_version, search.last_run_at, search.next_run_at,
            search.consecutive_failures) == (False, 1, None, None, 0)
    assert (search.aql_query, search.schedule_cron, search.severity, search.owner) == (
        AQL, "0 * * * *", "MEDIUM", "soc")
    versions = list(await db_session.scalars(select(SearchQueryVersion)))
    assert [(v.search_id, v.version, v.change_note, v.changed_by, v.aql_query)
            for v in versions] == [(search.id, 1, "initial version", "soc", AQL)]


async def test_second_import_is_unchanged_after_the_operator_enabled_the_search(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(scheduled_searches=[SEARCH]))
    search = await db_session.scalar(select(ScheduledSearch))
    search.enabled = True
    await db_session.commit()
    again = _import(db_schema, tmp_path, _document(scheduled_searches=[SEARCH]))
    assert len(_lines(again, "scheduled_searches", "unchanged")) == 1
    await db_session.refresh(search)
    assert search.enabled is True
    assert await _count(db_session, ScheduledSearch, SearchQueryVersion) == [1, 1]


async def test_search_that_differs_is_a_conflict_and_writes_nothing(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(scheduled_searches=[SEARCH]))
    for change in ({"schedule_cron": "5 * * * *"}, {"aql_query": AQL.replace("1 HOURS", "2 HOURS")},
                   {"threshold_value": 5.0}):
        report = _import(db_schema, tmp_path, _document(scheduled_searches=[{**SEARCH, **change}]))
        assert len(_lines(report, "scheduled_searches", "conflict")) == 1, change
    search = await db_session.scalar(select(ScheduledSearch))
    await db_session.refresh(search)
    assert (search.aql_query, search.schedule_cron, search.query_version) == (AQL, "0 * * * *", 1)
    assert await _count(db_session, SearchQueryVersion) == [1]


async def test_cron_below_the_floor_stores_nothing_with_a_fixed_reason(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    fast = {**SEARCH, "schedule_cron": "* * * * *"}
    bad_cron = {**SEARCH, "name": "garbled", "schedule_cron": "not a cron"}
    bad_zone = {**SEARCH, "name": "zone", "schedule_timezone": "Nowhere/Land"}
    report = _import(db_schema, tmp_path, _document(
        scheduled_searches=[fast, bad_cron, bad_zone], property_watches=[WATCH]))
    unresolved = _lines(report, "scheduled_searches", "unresolved")
    assert [line["reason"] for line in unresolved] == ["search schedule fails the cron floor"] * 3
    assert await _count(db_session, ScheduledSearch, SearchQueryVersion) == [0, 0]
    assert len(_lines(report, "property_watches", "created")) == 1
    assert "not a cron" not in json.dumps(report) and "Nowhere" not in json.dumps(report)


async def test_unsafe_aql_stores_nothing_and_is_never_echoed(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    secret_table = "SELECT * FROM secret_table_zz"
    rows = [{**SEARCH, "name": "not events", "aql_query": secret_table},
            {**SEARCH, "name": "empty", "aql_query": " "},
            {**SEARCH, "name": "number", "aql_query": 5}]
    report = _import(db_schema, tmp_path, _document(scheduled_searches=rows))
    unresolved = _lines(report, "scheduled_searches", "unresolved")
    assert [line["reason"] for line in unresolved[:2]] == ["search AQL failed validation"] * 2
    assert len(unresolved) == 3
    assert "secret_table_zz" not in json.dumps(report)
    assert await _count(db_session, ScheduledSearch, SearchQueryVersion) == [0, 0]


BAD_SEARCHES = [
    {**SEARCH, "timeout_seconds": 0},
    {**SEARCH, "max_result_rows": -1},
    {**SEARCH, "severity": "SEVERE"},
    {**SEARCH, "threshold_operator": "NE"},
    {**SEARCH, "threshold_value": float("nan")},
    {**SEARCH, "threshold_operator": "GT", "threshold_value": 10001.0},
    {**SEARCH, "visualization_type": "RADAR"},
    {**SEARCH, "name": ""},
    {**SEARCH, "name": "n" * 256},
    {**SEARCH, "owner": "o" * 256},
    {**SEARCH, "enabled_on_source": 1},
    {**SEARCH, "mitre_techniques": None},
    {k: v for k, v in SEARCH.items() if k != "category"},
]


@pytest.mark.parametrize("row", BAD_SEARCHES, ids=range(len(BAD_SEARCHES)))
async def test_invalid_searches_are_unresolved_and_a_sibling_commits(
        db_schema, db_session, tmp_path, row):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        scheduled_searches=[row], property_watches=[WATCH]))
    assert len(_lines(report, "scheduled_searches", "unresolved")) == 1, report
    assert await _count(db_session, ScheduledSearch, SearchQueryVersion) == [0, 0]
    assert len(_lines(report, "property_watches", "created")) == 1
    assert report["committed"] is True


# ----------------------------------------------------------------------------- whole command
async def test_dry_run_reports_every_section_and_writes_nothing(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    document = _document(analytics_rule_settings=[RULE_ROW], technique_mappings=[MAPPING],
                         property_watches=[WATCH], scheduled_searches=[SEARCH])
    report = _import(db_schema, tmp_path, document, mode="--dry-run")
    assert report["mode"] == "dry-run" and report["committed"] is False
    for section in ("analytics_rule_settings", "technique_mappings", "property_watches",
                    "scheduled_searches"):
        assert len(_lines(report, section, "created")) == 1, section
        assert _lines(report, section, "created")[0]["reason"] == "would apply configuration"
    assert await _count(db_session, TechniqueMapping, PropertyWatch, ScheduledSearch,
                        SearchQueryVersion) == [0, 0, 0, 0]
    await db_session.refresh(fleet["rule"])
    assert fleet["rule"].owner is None


async def test_duplicate_names_in_one_file_are_not_applied(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        property_watches=[WATCH, {**WATCH, "severity": "HIGH"}],
        scheduled_searches=[SEARCH, {**SEARCH, "owner": "other"}],
        analytics_rule_settings=[RULE_ROW, {**RULE_ROW, "owner": "other"}],
        technique_mappings=[MAPPING, {**MAPPING, "confidence": 0.1}]))
    for section in ("property_watches", "scheduled_searches", "analytics_rule_settings",
                    "technique_mappings"):
        assert len(_lines(report, section, "unresolved")) == 2, section
        assert _lines(report, section, "created") == []
    assert await _count(db_session, PropertyWatch, ScheduledSearch, TechniqueMapping) == [0, 0, 0]


async def test_exported_row_with_warnings_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        property_watches=[{**WATCH, "warnings": ["log source ref does not resolve"]}]))
    assert _lines(report, "property_watches", "unresolved")[0]["reason"] == (
        "export reported an unresolved reference")
    assert await _count(db_session, PropertyWatch) == [0]
