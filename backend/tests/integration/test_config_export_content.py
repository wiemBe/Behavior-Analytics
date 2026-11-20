"""`python -m app.cli.config export`: watches, searches, rules, dashboards and rule SOC.

Slice 1e. The command runs as a subprocess, as the wrapper runs it. Collection state stays out
(``history_days``, hysteresis, run cursors), a scheduled search carries its current query and no
version section, only EXPLICIT technique mappings travel, and a widget id becomes a name ref while
a null binding stays null. A dangling ref keeps the 1d2 ``warnings`` entry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession

from app.config_transfer.document import SECTION_ORDER, secret_key_paths
from app.models.custom_rule import CustomRule
from app.models.dashboard import Dashboard, UserDashboardPref
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.instance import QRadarInstance
from app.models.ips import IpsEntity
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch, PwValue
from app.models.rule import AnalyticsRule, TechniqueMapping
from app.models.search import ScheduledSearch, SearchQueryVersion
from app.models.waf import WafEndpoint

pytestmark = pytest.mark.integration
BACKEND = Path(__file__).resolve().parents[2]
NEW = ("analytics_rule_settings", "technique_mappings", "property_watches",
       "scheduled_searches", "custom_rules", "dashboards", "dashboard_prefs")
WAF = {"name": "Edge WAF", "type_name": "F5 Networks BIG-IP ASM"}
AQL = "SELECT SUM(eventcount) FROM events LAST 1 HOURS"
PROP = [{"kind": "CUSTOM_PROPERTY", "name": "Action"}]


def _run(url: str, *args: str) -> subprocess.CompletedProcess[str]:
    parsed = make_url(url)
    env = dict(os.environ) | {
        "POSTGRES_USER": parsed.username or "", "POSTGRES_PASSWORD": parsed.password or "",
        "POSTGRES_DB": parsed.database or "", "POSTGRES_HOST": parsed.host or "localhost",
        "POSTGRES_PORT": str(parsed.port or 5432), "PYTHONPATH": str(BACKEND)}
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "app.cli.config", *args], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=120, check=False)


async def _console(session: AsyncSession) -> QRadarInstance:
    console = QRadarInstance(name="preprod", console_host="https://qradar.lab.internal",
                             sec_token="S3CRET-TOKEN-h4rdc0ded")
    session.add(console)
    await session.flush()
    return console


async def _source(session: AsyncSession, console: QRadarInstance) -> LogSource:
    source = LogSource(instance_id=console.id, qradar_id=1, **WAF)
    session.add(source)
    await session.flush()
    return source


def _section(document: str, name: str) -> list[dict[str, object]]:
    return json.loads(document)["sections"][name]


def _assert_identical(first: str, second: str) -> None:
    one, two = json.loads(first), json.loads(second)
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", one.pop("exported_at"))
    two.pop("exported_at")
    assert one == two
    pairs = zip(first.splitlines(), second.splitlines(), strict=True)
    assert all(left == right for index, (left, right) in enumerate(pairs) if index != 3)


async def test_empty_console_exports_every_new_section_empty(
    db_schema: str, db_session: AsyncSession
) -> None:
    await _console(db_session)
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    document = json.loads(done.stdout)
    assert list(document["sections"]) == list(SECTION_ORDER)
    assert [document["sections"][name] for name in NEW] == [[] for _ in NEW]
    assert "search_query_version" not in document["sections"]


async def test_watches_searches_and_rules_drop_collection_state(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    now = datetime.now(UTC)
    watch = PropertyWatch(
        instance_id=console.id, name="Denied actions", description=None, scope_kind="LOG_SOURCE",
        log_source_id=source.id, detectors=["PW_NEW_VALUE"], history_days=30,
        properties=[{"kind": "CUSTOM_PROPERTY", "name": "Action"}],
        pinned_values=[["blocked"]], collected_through=now, last_status="OK",
        created_by="old-operator", updated_by="old-operator")
    search = ScheduledSearch(name="Denied by WAF", owner="soc", mitre_techniques=[],
                             aql_query=AQL, query_version=3, schedule_cron="0 * * * *",
                             last_run_at=now, consecutive_failures=2)
    rule = CustomRule(
        name="Edge EPS floor", subject_kind="LOG_SOURCE", log_source_id=source.id,
        metric="average_eps", operator="LT", threshold_value=1.0, sustain_buckets=3,
        recovery_buckets=2, severity="HIGH", consecutive_breaching=4, consecutive_under=1,
        active_breach_id=uuid.uuid4(), last_evaluated_at=now, last_status="OK",
        created_by="old-operator", updated_by="old-operator")
    db_session.add_all([watch, search, rule])
    await db_session.flush()
    db_session.add(SearchQueryVersion(search_id=search.id, version=3, aql_query="SELECT 1"))
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")

    assert _section(done.stdout, "property_watches") == [{
        "name": "Denied actions", "description": None, "enabled_on_source": False,
        "scope_kind": "LOG_SOURCE", "log_source": WAF, "scope_type": None,
        "scope_group": None, "properties": [{"kind": "CUSTOM_PROPERTY", "name": "Action"}],
        "pinned_values": [["blocked"]], "detectors": ["PW_NEW_VALUE"], "severity": "MEDIUM"}]
    assert _section(done.stdout, "scheduled_searches") == [{
        "name": "Denied by WAF", "description": None, "owner": "soc", "category": None,
        "mitre_techniques": [], "aql_query": AQL, "schedule_cron": "0 * * * *",
        "schedule_timezone": "UTC", "threshold_value": None, "threshold_operator": "GT",
        "severity": "MEDIUM", "timeout_seconds": 300, "max_time_range_hours": 24,
        "max_result_rows": 10000, "visualization_type": "TABLE", "enabled_on_source": True}]
    assert _section(done.stdout, "custom_rules") == [{
        "name": "Edge EPS floor", "description": None, "enabled_on_source": False,
        "subject_kind": "LOG_SOURCE", "log_source": WAF, "source_class": None,
        "waf_endpoint": None, "ips_entity": None, "pw_value": None, "metric": "average_eps",
        "operator": "LT", "threshold_value": 1.0, "sustain_buckets": 3, "recovery_buckets": 2,
        "severity": "HIGH"}]
    for absent in ("history_days", "query_version", "last_run_at", "consecutive_breaching",
                   "active_breach_id", "old-operator"):
        assert absent not in done.stdout
    assert secret_key_paths(json.loads(done.stdout)) == []
    _assert_identical(done.stdout, _run(db_schema, "export").stdout)


async def test_rule_soc_columns_and_only_explicit_technique_mappings(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    rule = AnalyticsRule(
        instance_id=console.id, qradar_id=7, name="Excessive Firewall Denies",
        rule_type="EVENT", is_building_block=False, description="mirrored QRadar text",
        owner="soc", mitre_techniques=["T1046"], soc_notes="tuned", expected_daily_firings=20.0,
        health_monitoring_enabled=False)
    db_session.add(rule)
    await db_session.flush()
    db_session.add_all([
        TechniqueMapping(instance_id=console.id, technique_id="T1046", rule_id=rule.id,
                         source="EXPLICIT", confidence=1.0),
        TechniqueMapping(instance_id=console.id, technique_id="T1059", rule_id=rule.id,
                         source="INFERRED", confidence=0.4, provenance="scored by coverage"),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")
    triple = {"name": "Excessive Firewall Denies", "rule_type": "EVENT",
              "is_building_block": False}
    assert _section(done.stdout, "analytics_rule_settings") == [{
        "rule": triple, "owner": "soc", "mitre_techniques": ["T1046"], "soc_notes": "tuned",
        "expected_daily_firings": 20.0, "health_monitoring_enabled": False}]
    assert _section(done.stdout, "technique_mappings") == [{
        "technique_id": "T1046", "technique_name": None, "tactic": None, "rule": triple,
        "source": "EXPLICIT", "confidence": 1.0, "provenance": None}]
    assert "T1059" not in done.stdout and "mirrored QRadar text" not in done.stdout


async def test_dashboards_rewrite_ids_to_names_and_drop_opaque_ids(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    watch = PropertyWatch(instance_id=console.id, name="Denied actions",
                          scope_kind="LOG_SOURCE", log_source_id=source.id,
                          detectors=["PW_NEW_VALUE"], pinned_values=[],
                          properties=[{"kind": "CUSTOM_PROPERTY", "name": "Action"}])
    search = ScheduledSearch(name="Denied by WAF", aql_query=AQL, schedule_cron="0 * * * *")
    db_session.add_all([watch, search])
    await db_session.flush()
    edge = Dashboard(owner_subject="soc-lead", name="Edge", visibility="SHARED",
                     schema_version=1, layout={"v": 1, "range": "24h", "widgets": [
                         {"id": "w_src", "type": "chart.eps_observed_expected",
                          "config": {"source": str(source.id)}},
                         {"id": "w_null", "type": "chart.eps_observed_expected",
                          "config": {"source": None}},
                         {"id": "w_watch", "type": "chart.property_watch",
                          "config": {"watch_id": str(watch.id)}},
                         {"id": "w_search", "type": "chart.search_results",
                          "config": {"search_id": str(search.id)}}]})
    dangling = uuid.uuid4()
    broken = Dashboard(owner_subject="soc-lead", name="Broken", visibility="PRIVATE",
                       schema_version=1, layout={"v": 1, "widgets": [
                           {"id": "w_missing", "type": "chart.eps_observed_expected",
                            "config": {"source": str(dangling)}}]})
    opaque = Dashboard(owner_subject="soc-lead", name="Opaque", visibility="PRIVATE",
                       schema_version=1, layout={"v": 1, "widgets": [
                           {"id": "w_opaque", "type": "custom.unknown",
                            "config": {"target": str(uuid.uuid4())}}]})
    db_session.add_all([edge, broken, opaque])
    await db_session.flush()
    db_session.add(UserDashboardPref(owner_subject="soc-lead", default_dashboard_id=edge.id))
    await db_session.commit()
    done = _run(db_schema, "export")
    assert done.returncode == 0
    assert done.stderr.splitlines() == [
        "warning: dashboard 'Opaque' was not exported: layout holds an opaque id"]

    dashboards = {row["name"]: row for row in _section(done.stdout, "dashboards")}
    assert set(dashboards) == {"Broken", "Edge"}
    configs = {w["id"]: w["config"] for w in dashboards["Edge"]["layout"]["widgets"]}
    assert configs["w_src"] == {"source": WAF}
    assert configs["w_null"] == {"source": None}
    assert configs["w_watch"] == {"watch_id": {"name": "Denied actions"}}
    assert configs["w_search"] == {"search_id": {"name": "Denied by WAF"}}
    assert configs["w_src"]["source"] is not None
    assert dashboards["Broken"]["layout"]["widgets"][0]["config"]["source"] == {
        "name": None, "type_name": None}
    assert dashboards["Broken"]["warnings"] == ["widget log source ref does not resolve"]
    assert _section(done.stdout, "dashboard_prefs") == [
        {"owner_subject": "soc-lead", "dashboard_name": "Edge"}]
    for absent in (str(source.id), str(watch.id), str(search.id), str(dangling)):
        assert absent not in done.stdout
    assert secret_key_paths(json.loads(done.stdout)) == []


async def test_exported_sections_are_ordered_deterministically(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    db_session.add_all([
        # Inserted in reverse order, so an export without its ORDER BY cannot pass.
        PropertyWatch(instance_id=console.id, name="Zeta watch", scope_kind="LOG_SOURCE",
                      log_source_id=source.id, properties=PROP),
        PropertyWatch(instance_id=console.id, name="Alpha watch", scope_kind="LOG_SOURCE",
                      log_source_id=source.id, properties=PROP),
        ScheduledSearch(name="Zeta search", aql_query=AQL, schedule_cron="0 * * * *"),
        ScheduledSearch(name="Alpha search", aql_query=AQL, schedule_cron="0 * * * *"),
        CustomRule(name="Zeta rule", subject_kind="LOG_SOURCE", log_source_id=source.id,
                   metric="average_eps", operator="GT", threshold_value=1.0),
        CustomRule(name="Alpha rule", subject_kind="LOG_SOURCE", log_source_id=source.id,
                   metric="average_eps", operator="GT", threshold_value=1.0),
    ])
    rule = AnalyticsRule(instance_id=console.id, qradar_id=1, name="Rule", rule_type="EVENT",
                         is_building_block=False)
    db_session.add(rule)
    await db_session.flush()
    db_session.add_all([
        TechniqueMapping(instance_id=console.id, technique_id="T1059", rule_id=rule.id),
        TechniqueMapping(instance_id=console.id, technique_id="T1046", rule_id=rule.id),
    ])
    zeta = Dashboard(owner_subject="zoe", name="Zeta", visibility="PRIVATE",
                     schema_version=1, layout={"v": 1, "widgets": []})
    alpha = Dashboard(owner_subject="amy", name="Alpha", visibility="PRIVATE",
                      schema_version=1, layout={"v": 1, "widgets": []})
    db_session.add_all([zeta, alpha])
    await db_session.flush()
    db_session.add_all([
        UserDashboardPref(owner_subject="zoe", default_dashboard_id=zeta.id),
        UserDashboardPref(owner_subject="amy", default_dashboard_id=alpha.id),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")

    assert [row["name"] for row in _section(done.stdout, "property_watches")] == [
        "Alpha watch", "Zeta watch"]
    assert [row["name"] for row in _section(done.stdout, "scheduled_searches")] == [
        "Alpha search", "Zeta search"]
    assert [row["name"] for row in _section(done.stdout, "custom_rules")] == [
        "Alpha rule", "Zeta rule"]
    assert [row["technique_id"] for row in _section(done.stdout, "technique_mappings")] == [
        "T1046", "T1059"]
    assert [(row["owner_subject"], row["name"])
            for row in _section(done.stdout, "dashboards")] == [
        ("amy", "Alpha"), ("zoe", "Zeta")]
    assert [row["owner_subject"] for row in _section(done.stdout, "dashboard_prefs")] == [
        "amy", "zoe"]


async def test_dashboard_with_unsupported_layout_version_is_skipped(
    db_schema: str, db_session: AsyncSession
) -> None:
    await _console(db_session)
    db_session.add_all([
        Dashboard(owner_subject="soc", name="Current", visibility="PRIVATE",
                  schema_version=1, layout={"v": 1, "widgets": []}),
        Dashboard(owner_subject="soc", name="Legacy", visibility="PRIVATE",
                  schema_version=1, layout={"v": 2, "widgets": []}),
        # A hand-edited row can hold a non-hashable v; the export must not crash on it.
        Dashboard(owner_subject="soc", name="Malformed", visibility="PRIVATE",
                  schema_version=1, layout={"v": [], "widgets": []}),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert done.returncode == 0
    assert [row["name"] for row in _section(done.stdout, "dashboards")] == ["Current"]
    assert done.stderr.splitlines() == [
        "warning: dashboard 'Legacy' was not exported: layout version is not supported",
        "warning: dashboard 'Malformed' was not exported: layout version is not supported",
    ]


async def test_watch_scope_kinds_and_unresolved_scope_warn(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    other = QRadarInstance(name="other", console_host="https://other.lab.internal",
                           sec_token="S3CRET-TOKEN-other")
    db_session.add(other)
    await db_session.flush()
    foreign = LogSource(instance_id=other.id, qradar_id=1, **WAF)
    db_session.add(foreign)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add_all([
        LogSourceType(instance_id=console.id, type_id=10, name=WAF["type_name"],
                      internal=False, custom=False, synced_at=now),
        LogSourceGroup(instance_id=console.id, qradar_id=1, name="Edge", parent_qradar_id=2,
                       assignable=True, synced_at=now),
        LogSourceGroup(instance_id=console.id, qradar_id=2, name="Perimeter",
                       assignable=True, synced_at=now),
        PropertyWatch(instance_id=console.id, name="Type scope", scope_kind="LOG_SOURCE_TYPE",
                      scope_type_id=10, properties=PROP),
        PropertyWatch(instance_id=console.id, name="Group scope", scope_kind="LOG_SOURCE_GROUP",
                      scope_group_id=1, properties=PROP),
        PropertyWatch(instance_id=console.id, name="Dangling type", scope_kind="LOG_SOURCE_TYPE",
                      scope_type_id=999, properties=PROP),
        PropertyWatch(instance_id=console.id, name="Dangling group",
                      scope_kind="LOG_SOURCE_GROUP", scope_group_id=999, properties=PROP),
        PropertyWatch(instance_id=console.id, name="Dangling source", scope_kind="LOG_SOURCE",
                      log_source_id=foreign.id, properties=PROP),
    ])
    await db_session.commit()
    done = _run(db_schema, "export", "--instance", "preprod")
    assert (done.returncode, done.stderr) == (0, "")

    watches = {row["name"]: row for row in _section(done.stdout, "property_watches")}
    assert watches["Type scope"]["scope_type"] == {"name": WAF["type_name"]}
    assert watches["Group scope"]["scope_group"] == {
        "name": "Edge", "parent_name": "Perimeter"}
    assert watches["Dangling type"]["warnings"] == [
        "log source type ref does not resolve"]
    assert watches["Dangling group"]["warnings"] == [
        "log source group ref does not resolve"]
    assert watches["Dangling source"]["warnings"] == ["log source ref does not resolve"]


async def test_custom_rule_subject_refs(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    other = QRadarInstance(name="other", console_host="https://other.lab.internal",
                           sec_token="S3CRET-TOKEN-other")
    db_session.add(other)
    await db_session.flush()
    foreign = LogSource(instance_id=other.id, qradar_id=1, **WAF)
    db_session.add(foreign)
    await db_session.flush()
    now = datetime.now(UTC)
    endpoint = WafEndpoint(instance_id=console.id, log_source_id=source.id,
                           normalized_endpoint="/login", http_method="POST",
                           normalizer_version=1, first_seen_at=now, last_seen_at=now)
    entity = IpsEntity(instance_id=console.id, log_source_id=source.id,
                       dimension="SIGNATURE", value_key="sig-1",
                       first_seen_at=now, last_seen_at=now)
    watch = PropertyWatch(instance_id=console.id, name="Denied actions",
                          scope_kind="LOG_SOURCE", log_source_id=source.id, properties=PROP)
    foreign_watch = PropertyWatch(instance_id=other.id, name="Foreign watch",
                                  scope_kind="LOG_SOURCE", log_source_id=foreign.id,
                                  properties=PROP)
    # The subject row exists, but the log source its ref needs is on the other console.
    dangling_endpoint = WafEndpoint(instance_id=console.id, log_source_id=foreign.id,
                                    normalized_endpoint="/orphan", http_method="GET",
                                    normalizer_version=1, first_seen_at=now, last_seen_at=now)
    dangling_entity = IpsEntity(instance_id=console.id, log_source_id=foreign.id,
                                dimension="SIGNATURE", value_key="sig-2",
                                first_seen_at=now, last_seen_at=now)
    db_session.add_all([endpoint, entity, watch, foreign_watch, dangling_endpoint,
                        dangling_entity])
    await db_session.flush()
    value = PwValue(watch_id=watch.id, watch_version=1, value_key="blocked")
    foreign_value = PwValue(watch_id=foreign_watch.id, watch_version=1, value_key="seen")
    db_session.add_all([value, foreign_value])
    await db_session.flush()
    db_session.add_all([
        CustomRule(name="Endpoint rule", subject_kind="WAF_ENDPOINT",
                   waf_endpoint_id=endpoint.id, metric="average_eps", operator="GT",
                   threshold_value=1.0),
        CustomRule(name="Entity rule", subject_kind="IPS_ENTITY",
                   ips_entity_id=entity.id, metric="average_eps", operator="GT",
                   threshold_value=1.0),
        CustomRule(name="Value rule", subject_kind="PW_VALUE", pw_value_id=value.id,
                   metric="average_eps", operator="GT", threshold_value=1.0),
        CustomRule(name="Class rule", subject_kind="FLEET_CLASS", source_class="WAF",
                   metric="average_eps", operator="GT", threshold_value=1.0),
        CustomRule(name="Dangling endpoint rule", subject_kind="WAF_ENDPOINT",
                   waf_endpoint_id=dangling_endpoint.id, metric="average_eps", operator="GT",
                   threshold_value=1.0),
        CustomRule(name="Dangling entity rule", subject_kind="IPS_ENTITY",
                   ips_entity_id=dangling_entity.id, metric="average_eps", operator="GT",
                   threshold_value=1.0),
        CustomRule(name="Dangling value rule", subject_kind="PW_VALUE",
                   pw_value_id=foreign_value.id, metric="average_eps", operator="GT",
                   threshold_value=1.0),
    ])
    await db_session.commit()
    done = _run(db_schema, "export", "--instance", "preprod")
    assert (done.returncode, done.stderr) == (0, "")

    rules = {row["name"]: row for row in _section(done.stdout, "custom_rules")}
    assert rules["Endpoint rule"]["waf_endpoint"] == {
        "log_source": WAF, "virtual_host": None, "normalized_endpoint": "/login",
        "http_method": "POST", "normalizer_version": 1}
    assert rules["Entity rule"]["ips_entity"] == {
        "log_source": WAF, "dimension": "SIGNATURE", "value_key": "sig-1"}
    assert rules["Value rule"]["pw_value"] == {
        "watch": {"name": "Denied actions"}, "value_key": "blocked", "watch_version": 1}
    assert rules["Class rule"]["source_class"] == "WAF"
    assert rules["Class rule"]["log_source"] is None
    assert rules["Dangling endpoint rule"]["waf_endpoint"]["log_source"] is None
    assert rules["Dangling endpoint rule"]["warnings"] == [
        "WAF endpoint log source ref does not resolve"]
    assert rules["Dangling entity rule"]["warnings"] == [
        "IPS entity log source ref does not resolve"]
    assert rules["Dangling value rule"]["pw_value"]["watch"] == {"name": None}
    assert rules["Dangling value rule"]["warnings"] == [
        "property watch ref does not resolve"]
    for absent in (str(endpoint.id), str(entity.id), str(value.id), str(watch.id)):
        assert absent not in done.stdout


async def test_waf_endpoint_traffic_widget_refs(
    db_schema: str, db_session: AsyncSession
) -> None:
    console = await _console(db_session)
    source = await _source(db_session, console)
    now = datetime.now(UTC)
    endpoint = WafEndpoint(instance_id=console.id, log_source_id=source.id,
                           virtual_host="app.example", normalized_endpoint="/login",
                           http_method="POST", normalizer_version=1,
                           first_seen_at=now, last_seen_at=now)
    db_session.add(endpoint)
    await db_session.flush()
    db_session.add(Dashboard(
        owner_subject="soc", name="Traffic", visibility="PRIVATE", schema_version=1,
        layout={"v": 1, "widgets": [
            {"id": "w_both", "type": "waf.endpoint_traffic",
             "config": {"waf_source": str(source.id), "endpoint": str(endpoint.id)}},
            {"id": "w_null", "type": "waf.endpoint_traffic",
             "config": {"waf_source": str(source.id), "endpoint": None}}]}))
    await db_session.commit()
    done = _run(db_schema, "export")
    assert (done.returncode, done.stderr) == (0, "")

    layout = _section(done.stdout, "dashboards")[0]["layout"]
    configs = {widget["id"]: widget["config"] for widget in layout["widgets"]}
    assert configs["w_both"] == {"waf_source": WAF, "endpoint": {
        "log_source": WAF, "virtual_host": "app.example",
        "normalized_endpoint": "/login", "http_method": "POST",
        "normalizer_version": 1}}
    assert configs["w_null"] == {"waf_source": WAF, "endpoint": None}
    assert str(endpoint.id) not in done.stdout


async def test_pref_for_skipped_dashboard(
    db_schema: str, db_session: AsyncSession
) -> None:
    await _console(db_session)
    kept = Dashboard(owner_subject="amy", name="Kept", visibility="PRIVATE",
                     schema_version=1, layout={"v": 1, "widgets": []})
    skipped = Dashboard(owner_subject="zoe", name="Skipped", visibility="PRIVATE",
                        schema_version=1, layout={"v": 2, "widgets": []})
    db_session.add_all([kept, skipped])
    await db_session.flush()
    db_session.add_all([
        UserDashboardPref(owner_subject="amy", default_dashboard_id=kept.id),
        UserDashboardPref(owner_subject="zoe", default_dashboard_id=skipped.id),
    ])
    await db_session.commit()
    done = _run(db_schema, "export")
    assert done.returncode == 0
    assert _section(done.stdout, "dashboard_prefs") == [
        {"owner_subject": "amy", "dashboard_name": "Kept"}]
    assert done.stderr.splitlines() == [
        "warning: dashboard 'Skipped' was not exported: layout version is not supported"]
