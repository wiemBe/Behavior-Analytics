"""Real import commands for the custom rule, dashboard and dashboard preference sections (CFG-1i2).

Every case runs ``python -m app.cli.config import`` as a subprocess whose ``sitecustomize`` fails
on any provider build, Ariel search or encrypted-column read (the CFG-1i harness). A created rule
is disabled with zeroed hysteresis; a subject that does not exist yet is unresolved, never
inserted; a dashboard whose layout cannot be rewritten whole is skipped whole; a second import of
the same file is ``unchanged`` for every row.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select

from app.models.custom_rule import CustomRule
from app.models.dashboard import Dashboard, UserDashboardPref
from app.models.ips import IpsEntity
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch, PwValue
from app.models.search import ScheduledSearch
from app.models.waf import WafEndpoint
from tests.integration.test_config_import_content import (
    SEARCH,
    WAF,
    _count,
    _document,
    _fleet,
    _import,
    _lines,
)

pytestmark = pytest.mark.integration

RULE_LOG: dict[str, Any] = {
    "name": "Edge EPS floor", "description": "floor", "enabled_on_source": True,
    "subject_kind": "LOG_SOURCE", "log_source": WAF, "source_class": None, "waf_endpoint": None,
    "ips_entity": None, "pw_value": None, "metric": "average_eps", "operator": "LT",
    "threshold_value": 1.0, "sustain_buckets": 3, "recovery_buckets": 2, "severity": "HIGH",
}
ENDPOINT_REF: dict[str, Any] = {
    "log_source": WAF, "virtual_host": "app.example", "normalized_endpoint": "/login",
    "http_method": "POST", "normalizer_version": 1}
RULE_ENDPOINT = {**RULE_LOG, "name": "Login rate", "subject_kind": "WAF_ENDPOINT",
                 "log_source": None, "waf_endpoint": ENDPOINT_REF, "metric": "request_count",
                 "operator": "GT", "threshold_value": 50.0}
ENTITY_REF = {"log_source": WAF, "dimension": "SIGNATURE", "value_key": "sig-1"}
RULE_ENTITY = {**RULE_LOG, "name": "Signature rate", "subject_kind": "IPS_ENTITY",
               "log_source": None, "ips_entity": ENTITY_REF, "metric": "events",
               "operator": "GT", "threshold_value": 5.0}
VALUE_REF = {"watch": {"name": "Denied actions"}, "value_key": "blocked", "watch_version": 1}
RULE_VALUE = {**RULE_LOG, "name": "Blocked rate", "subject_kind": "PW_VALUE",
              "log_source": None, "pw_value": VALUE_REF, "metric": "pw_value_events",
              "operator": "GT", "threshold_value": 5.0}
RULE_CLASS = {**RULE_LOG, "name": "WAF class EPS", "subject_kind": "FLEET_CLASS",
              "log_source": None, "source_class": "WAF", "metric": "eps"}
RULE_IPS_SOURCE = {**RULE_LOG, "name": "IPS volume", "subject_kind": "IPS_SOURCE",
                   "metric": "total_events", "operator": "GT", "threshold_value": 100.0}


def _widget(widget_id: str, kind: str, **config: Any) -> dict[str, Any]:
    return {"id": widget_id, "type": kind, "x": 0, "y": 0, "w": 6, "h": 8, "range": None,
            "config": config}


def _layout(*widgets: dict[str, Any], v: int = 1) -> dict[str, Any]:
    return {"v": v, "range": "24h", "widgets": list(widgets)}


def _dash(name: str = "Edge", layout: dict[str, Any] | None = None,
          **fields: Any) -> dict[str, Any]:
    return {"owner_subject": "soc-lead", "name": name, "description": None,
            "visibility": "SHARED", "template_key": None, "template_version": None,
            "schema_version": 1, "layout": layout or _layout(), **fields}


PREF = {"owner_subject": "soc-lead", "dashboard_name": "Edge"}
FILE_SECTIONS = ("custom_rules", "dashboards", "dashboard_prefs")


async def _subjects(session: Any, fleet: dict[str, Any], *, version: int = 1) -> dict[str, Any]:
    """An endpoint, an IPS entity and a watch value, as collection would have admitted them."""
    now = datetime.now(UTC)
    endpoint = WafEndpoint(
        instance_id=fleet["console"].id, log_source_id=fleet["source"].id,
        virtual_host="app.example", normalized_endpoint="/login", http_method="POST",
        normalizer_version=version, first_seen_at=now, last_seen_at=now)
    entity = IpsEntity(
        instance_id=fleet["console"].id, log_source_id=fleet["source"].id, dimension="SIGNATURE",
        value_key="sig-1", first_seen_at=now, last_seen_at=now, normalizer_version=7)
    watch = PropertyWatch(
        instance_id=fleet["console"].id, name="Denied actions", scope_kind="LOG_SOURCE",
        log_source_id=fleet["source"].id,
        properties=[{"kind": "CUSTOM_PROPERTY", "name": "Action"}])
    session.add_all([endpoint, entity, watch])
    await session.flush()
    value = PwValue(watch_id=watch.id, watch_version=1, value_key="blocked")
    session.add(value)
    await session.commit()
    return {"endpoint": endpoint, "entity": entity, "watch": watch, "value": value}


async def _rules(session: Any) -> dict[str, CustomRule]:
    return {row.name: row for row in await session.scalars(select(CustomRule))}


# ------------------------------------------------------------------------------ custom rules
async def test_log_source_rule_is_created_disabled_with_zeroed_state_then_unchanged(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(custom_rules=[RULE_LOG]))
    assert _lines(report, "custom_rules", "created") == [{
        "key": "Edge EPS floor", "reason": "applied configuration"}]
    rule = (await _rules(db_session))["Edge EPS floor"]
    assert (rule.enabled, rule.consecutive_breaching, rule.consecutive_under) == (False, 0, 0)
    assert (rule.last_evaluated_bucket_start, rule.last_evaluated_at, rule.active_breach_id,
            rule.last_status) == (None, None, None, None)
    assert (rule.log_source_id, rule.waf_endpoint_id, rule.ips_entity_id, rule.pw_value_id,
            rule.source_class) == (fleet["source"].id, None, None, None, None)
    assert (rule.metric, rule.operator, rule.threshold_value, rule.sustain_buckets,
            rule.recovery_buckets, rule.severity, rule.description) == (
        "average_eps", "LT", 1.0, 3, 2, "HIGH", "floor")
    assert (rule.created_by, rule.updated_by) == ("soc", "soc")
    # The operator enabled it and the engine ran: neither is a difference to the file.
    rule.enabled, rule.consecutive_breaching, rule.last_evaluated_at = True, 4, datetime.now(UTC)
    await db_session.commit()
    again = _import(db_schema, tmp_path, _document(custom_rules=[RULE_LOG]))
    assert len(_lines(again, "custom_rules", "unchanged")) == 1
    await db_session.refresh(rule)
    assert (rule.enabled, rule.consecutive_breaching) == (True, 4)


async def test_rule_that_differs_is_a_conflict_and_writes_nothing(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(custom_rules=[RULE_LOG]))
    changed = {**RULE_LOG, "threshold_value": 9.0, "severity": "LOW"}
    report = _import(db_schema, tmp_path, _document(custom_rules=[changed]))
    assert len(_lines(report, "custom_rules", "conflict")) == 1
    assert report["committed"] is True
    rule = (await _rules(db_session))["Edge EPS floor"]
    await db_session.refresh(rule)
    assert (rule.threshold_value, rule.severity) == (1.0, "HIGH")
    assert await _count(db_session, CustomRule) == [1]


async def test_fleet_class_and_ips_source_resolve_immediately(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        custom_rules=[RULE_CLASS, RULE_IPS_SOURCE]))
    assert len(_lines(report, "custom_rules", "created")) == 2
    rules = await _rules(db_session)
    klass, ips = rules["WAF class EPS"], rules["IPS volume"]
    assert (klass.subject_kind, klass.source_class, klass.log_source_id) == (
        "FLEET_CLASS", "WAF", None)
    assert (ips.subject_kind, ips.log_source_id, ips.source_class) == (
        "IPS_SOURCE", fleet["source"].id, None)


async def test_endpoint_entity_and_value_rules_wait_for_collection_then_resolve(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    document = _document(custom_rules=[RULE_ENDPOINT, RULE_ENTITY, RULE_VALUE, RULE_LOG])
    report = _import(db_schema, tmp_path, document)
    # Nothing was admitted yet: the three rules are unresolved, the sibling rule commits, and no
    # endpoint, entity or value row is invented for them.
    assert len(_lines(report, "custom_rules", "unresolved")) == 3
    assert len(_lines(report, "custom_rules", "created")) == 1
    reasons = {line["key"]: line["reason"] for line in _lines(report, "custom_rules", "unresolved")}
    assert reasons["Login rate"] == "no WAF endpoint has this log source and these fields"
    assert await _count(db_session, WafEndpoint, IpsEntity, PwValue) == [0, 0, 0]
    subjects = await _subjects(db_session, fleet)
    again = _import(db_schema, tmp_path, document)
    assert len(_lines(again, "custom_rules", "created")) == 3
    assert len(_lines(again, "custom_rules", "unchanged")) == 1
    rules = await _rules(db_session)
    assert rules["Login rate"].waf_endpoint_id == subjects["endpoint"].id
    assert rules["Signature rate"].ips_entity_id == subjects["entity"].id
    assert rules["Blocked rate"].pw_value_id == subjects["value"].id
    assert await _count(db_session, WafEndpoint, IpsEntity, PwValue) == [1, 1, 1]


async def test_ips_entity_matches_without_the_normalizer_version(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    subjects = await _subjects(db_session, fleet)
    assert subjects["entity"].normalizer_version == 7
    entity = {**ENTITY_REF, "normalizer_version": 1}  # the old console's version, ignored
    report = _import(db_schema, tmp_path, _document(
        custom_rules=[{**RULE_ENTITY, "ips_entity": entity}]))
    assert len(_lines(report, "custom_rules", "created")) == 1
    assert (await _rules(db_session))["Signature rate"].ips_entity_id == subjects["entity"].id


async def test_endpoint_version_is_part_of_the_match(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    await _subjects(db_session, fleet, version=2)
    report = _import(db_schema, tmp_path, _document(custom_rules=[RULE_ENDPOINT]))
    assert len(_lines(report, "custom_rules", "unresolved")) == 1
    assert await _count(db_session, CustomRule) == [0]


async def test_pw_value_for_another_watch_version_is_unresolved(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    subjects = await _subjects(db_session, fleet)
    subjects["value"].watch_version = 3  # the value belongs to a later watch version
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(custom_rules=[RULE_VALUE]))
    unresolved = _lines(report, "custom_rules", "unresolved")
    assert unresolved[0]["reason"] == "no property watch value has this watch, key, and version"
    assert await _count(db_session, CustomRule, PwValue) == [0, 1]


async def test_ambiguous_log_source_is_not_guessed(db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    db_session.add(LogSource(instance_id=fleet["console"].id, qradar_id=99, name=WAF["name"],
                             type_name=WAF["type_name"]))
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(custom_rules=[RULE_LOG]))
    assert "2 log sources" in _lines(report, "custom_rules", "unresolved")[0]["reason"]
    assert await _count(db_session, CustomRule) == [0]


BAD_RULES = [
    {**RULE_LOG, "metric": "nope"},
    {**RULE_LOG, "metric": "eps"},  # a fleet metric on a log source subject
    {**RULE_LOG, "operator": "XX"},
    {**RULE_LOG, "threshold_value": -1.0},
    {**RULE_LOG, "threshold_value": float("nan")},
    {**RULE_LOG, "sustain_buckets": 0},
    {**RULE_LOG, "recovery_buckets": 0},
    {**RULE_CLASS, "source_class": "TOASTER"},
    {**RULE_CLASS, "source_class": None},
    {**RULE_IPS_SOURCE, "metric": "blocked_share", "threshold_value": 1.5},
    {**RULE_LOG, "severity": "SEVERE"},
    {**RULE_LOG, "subject_kind": "NOWHERE"},
    {**RULE_LOG, "source_class": "WAF"},  # a second subject beside the log source
    {**RULE_LOG, "waf_endpoint": ENDPOINT_REF},
    {**RULE_LOG, "log_source": None},
    {**RULE_LOG, "name": "x" * 81},
    {**RULE_LOG, "threshold_value": True},
    {**RULE_LOG, "sustain_buckets": True},
    {**RULE_LOG, "enabled_on_source": "yes"},
    {k: v for k, v in RULE_LOG.items() if k != "severity"},
]


@pytest.mark.parametrize("row", BAD_RULES, ids=range(len(BAD_RULES)))
async def test_invalid_rule_is_unresolved_never_an_error_and_a_sibling_commits(
        db_schema, db_session, tmp_path, row):
    await _fleet(db_session)
    sibling = {**RULE_CLASS, "name": "Sibling"}
    report = _import(db_schema, tmp_path, _document(custom_rules=[row, sibling]))
    assert len(_lines(report, "custom_rules", "unresolved")) == 1
    assert [line["key"] for line in _lines(report, "custom_rules", "created")] == ["Sibling"]
    assert report["committed"] is True
    assert list(await _rules(db_session)) == ["Sibling"]


async def test_draft_failure_reason_names_no_value(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        custom_rules=[{**RULE_LOG, "metric": "secret-metric-text"}]))
    assert _lines(report, "custom_rules", "unresolved")[0]["reason"] == (
        "custom rule failed the draft validation")
    assert "secret-metric-text" not in json.dumps(report)


# -------------------------------------------------------------------------------- dashboards
async def test_dashboard_is_created_with_local_ids_and_the_owner_unchanged(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    subjects = await _subjects(db_session, fleet)
    layout = _layout(
        _widget("w_eps01", "chart.eps_observed_expected", source=WAF),
        _widget("w_null1", "chart.eps_observed_expected", source=None),
        _widget("w_traf1", "waf.endpoint_traffic", waf_source=WAF, endpoint=ENDPOINT_REF),
        _widget("w_traf2", "waf.endpoint_traffic", waf_source=WAF, endpoint=None),
        _widget("w_watch", "chart.property_watch", watch_id={"name": "Denied actions"},
                metric_key="events"),
        _widget("w_unkn1", "vendor.custom", note="plain text", direction="up"))
    report = _import(db_schema, tmp_path, _document(
        dashboards=[_dash(layout=layout)], dashboard_prefs=[PREF]))
    assert _lines(report, "dashboards", "created") == [{
        "key": "soc-lead|Edge", "reason": "applied configuration"}]
    assert len(_lines(report, "dashboard_prefs", "created")) == 1
    row = await db_session.scalar(select(Dashboard))
    assert (row.owner_subject, row.revision, row.visibility, row.schema_version,
            row.updated_by) == ("soc-lead", 1, "SHARED", 1, "soc")
    widgets = {item["id"]: item["config"] for item in row.layout["widgets"]}
    assert widgets["w_eps01"] == {"source": str(fleet["source"].id)}
    assert widgets["w_null1"] == {"source": None}
    assert widgets["w_traf1"] == {"waf_source": str(fleet["source"].id),
                                  "endpoint": str(subjects["endpoint"].id)}
    assert widgets["w_traf2"] == {"waf_source": str(fleet["source"].id), "endpoint": None}
    assert widgets["w_watch"] == {"watch_id": str(subjects["watch"].id), "metric_key": "events"}
    assert widgets["w_unkn1"] == {"note": "plain text", "direction": "up"}
    pref = await db_session.scalar(select(UserDashboardPref))
    assert (pref.owner_subject, pref.default_dashboard_id) == ("soc-lead", row.id)
    again = _import(db_schema, tmp_path, _document(
        dashboards=[_dash(layout=layout)], dashboard_prefs=[PREF]))
    assert len(_lines(again, "dashboards", "unchanged")) == 1
    assert len(_lines(again, "dashboard_prefs", "unchanged")) == 1
    assert await _count(db_session, Dashboard, UserDashboardPref) == [1, 1]


async def test_search_widget_resolves_a_search_created_in_the_same_file(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    layout = _layout(_widget("w_srch1", "chart.search_results", search_id={"name": SEARCH["name"]}))
    report = _import(db_schema, tmp_path, _document(
        scheduled_searches=[SEARCH], dashboards=[_dash(layout=layout)]))
    assert len(_lines(report, "dashboards", "created")) == 1
    search = await db_session.scalar(select(ScheduledSearch))
    row = await db_session.scalar(select(Dashboard))
    assert row.layout["widgets"][0]["config"] == {"search_id": str(search.id)}


async def test_unknown_dashboard_visibility_is_unresolved(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        dashboards=[_dash(visibility="PUBLIC")], dashboard_prefs=[PREF],
        custom_rules=[RULE_LOG]))
    assert _lines(report, "dashboards", "unresolved") == [{
        "key": "soc-lead|Edge", "reason": "dashboard visibility is not PRIVATE or SHARED"}]
    assert len(_lines(report, "dashboard_prefs", "unresolved")) == 1
    assert report["committed"] is True
    assert await _count(db_session, Dashboard, UserDashboardPref) == [0, 0]
    assert await _count(db_session, CustomRule) == [1]


async def test_dashboard_with_a_missing_endpoint_is_skipped_whole_then_imported_later(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    layout = _layout(
        _widget("w_eps01", "chart.eps_observed_expected", source=WAF),
        _widget("w_traf1", "waf.endpoint_traffic", waf_source=WAF, endpoint=ENDPOINT_REF))
    document = _document(dashboards=[_dash(layout=layout)], dashboard_prefs=[PREF])
    report = _import(db_schema, tmp_path, document)
    assert "no WAF endpoint has" in _lines(report, "dashboards", "unresolved")[0]["reason"]
    assert len(_lines(report, "dashboard_prefs", "unresolved")) == 1
    assert await _count(db_session, Dashboard, UserDashboardPref) == [0, 0]
    await _subjects(db_session, fleet)
    again = _import(db_schema, tmp_path, document)
    assert len(_lines(again, "dashboards", "created")) == 1
    assert len(_lines(again, "dashboard_prefs", "created")) == 1


@pytest.mark.parametrize("widget", [
    _widget("w_eps01", "chart.eps_observed_expected",
            source={"name": "No such", "type_name": None}),
    _widget("w_eps01", "chart.eps_observed_expected", source=str(uuid.uuid4())),
    _widget("w_eps01", "chart.eps_observed_expected", source="not a ref"),
    _widget("w_watch", "chart.property_watch", watch_id={"name": "No such watch"}),
    _widget("w_srch1", "chart.search_results", search_id={"name": "No such search"}),
    _widget("w_unkn1", "vendor.custom", ref=str(uuid.uuid4())),
    _widget("w_unkn1", "vendor.custom", nested={"deep": [{"id": str(uuid.uuid4())}]}),
], ids=["log-source", "bare-uuid", "bare-text", "watch", "search", "unknown-uuid", "unknown-deep"])
async def test_a_widget_that_does_not_resolve_skips_the_whole_dashboard(
        db_schema, db_session, tmp_path, widget):
    await _fleet(db_session)
    good = _widget("w_good1", "chart.eps_observed_expected", source=WAF)
    other = _dash("Other", _layout(good))
    report = _import(db_schema, tmp_path, _document(
        dashboards=[_dash("Edge", _layout(good, widget)), other]))
    assert [line["key"] for line in _lines(report, "dashboards", "unresolved")] == [
        "soc-lead|Edge"]
    assert [line["key"] for line in _lines(report, "dashboards", "created")] == [
        "soc-lead|Other"]
    assert [row.name for row in await db_session.scalars(select(Dashboard))] == ["Other"]


@pytest.mark.parametrize("layout", [_layout(v=2), _layout(v=0), {**_layout(), "v": True},
                                    {**_layout(), "v": "1"}, {"range": "24h", "widgets": []}],
                         ids=["v2", "v0", "true", "text", "missing"])
async def test_unsupported_layout_version_skips_the_dashboard(
        db_schema, db_session, tmp_path, layout):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        dashboards=[_dash(layout=layout, schema_version=1)]))
    assert _lines(report, "dashboards", "unresolved")[0]["reason"] == (
        "layout version is not supported")
    assert await _count(db_session, Dashboard) == [0]


async def test_unknown_widget_type_without_an_id_is_kept_verbatim(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    layout = _layout(_widget("w_unkn1", "vendor.custom", label="x", nested={"n": 1}))
    _import(db_schema, tmp_path, _document(dashboards=[_dash(layout=layout)]))
    row = await db_session.scalar(select(Dashboard))
    assert row.layout == layout


def _too_wide() -> dict[str, Any]:
    wide = _widget("w_wide1", "vendor.custom")
    return _layout({**wide, "x": 8, "w": 6})


@pytest.mark.parametrize("row", [
    _dash(layout=_too_wide()),
    _dash(layout={**_layout(), "extra": 1}),
    _dash(layout={**_layout(), "range": "1y"}),
    _dash(name="x" * 81),
    _dash(name="  "),
    _dash(description="d" * 2001),
    _dash(owner_subject=""),
    _dash(owner_subject=None),
    _dash(visibility=["PRIVATE"]),
    _dash(schema_version=2),
    _dash(layout=_layout(_widget("w_dup01", "vendor.custom"), _widget("w_dup01", "vendor.custom"))),
    _dash(layout="not a layout"),
    _dash(layout={**_layout(), "widgets": "none"}),
    _dash(layout=_layout("not a widget")),
], ids=range(14))
async def test_invalid_dashboard_is_unresolved_and_a_sibling_commits(
        db_schema, db_session, tmp_path, row):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(
        dashboards=[row, _dash("Sibling")], custom_rules=[RULE_LOG]))
    assert len(_lines(report, "dashboards", "unresolved")) == 1
    assert [line["key"] for line in _lines(report, "dashboards", "created")] == [
        "soc-lead|Sibling"]
    assert report["committed"] is True
    assert [item.name for item in await db_session.scalars(select(Dashboard))] == ["Sibling"]


async def test_a_dashboard_that_differs_is_a_conflict_and_its_preference_is_unresolved(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(dashboards=[_dash()]))
    changed = _dash(description="changed", visibility="PRIVATE")
    report = _import(db_schema, tmp_path, _document(
        dashboards=[changed], dashboard_prefs=[PREF]))
    assert len(_lines(report, "dashboards", "conflict")) == 1
    assert len(_lines(report, "dashboard_prefs", "unresolved")) == 1
    row = await db_session.scalar(select(Dashboard))
    await db_session.refresh(row)
    assert (row.description, row.visibility, row.revision) == (None, "SHARED", 1)
    assert await _count(db_session, UserDashboardPref) == [0]


async def test_same_name_for_another_owner_is_a_separate_dashboard(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(dashboards=[
        _dash(), _dash(owner_subject="other-owner")]))
    assert len(_lines(report, "dashboards", "created")) == 2
    owners = {row.owner_subject for row in await db_session.scalars(select(Dashboard))}
    assert owners == {"soc-lead", "other-owner"}


async def test_duplicate_dashboard_key_is_reported_not_merged(db_schema, db_session, tmp_path):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(dashboards=[_dash(), _dash(description="b")]))
    assert len(_lines(report, "dashboards", "unresolved")) == 2
    assert await _count(db_session, Dashboard) == [0]


# ------------------------------------------------------------------- dashboard preferences
async def test_preference_for_an_existing_equal_dashboard_and_a_different_default(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(dashboards=[_dash(), _dash("Second")]))
    first = _import(db_schema, tmp_path, _document(dashboards=[_dash()], dashboard_prefs=[PREF]))
    assert len(_lines(first, "dashboard_prefs", "created")) == 1
    other = {**PREF, "dashboard_name": "Second"}
    report = _import(db_schema, tmp_path, _document(
        dashboards=[_dash("Second")], dashboard_prefs=[other]))
    assert len(_lines(report, "dashboard_prefs", "conflict")) == 1
    pref = await db_session.scalar(select(UserDashboardPref))
    await db_session.refresh(pref)
    edge = await db_session.scalar(select(Dashboard).where(Dashboard.name == "Edge"))
    assert pref.default_dashboard_id == edge.id


async def test_preference_for_a_dashboard_not_in_the_plan_is_unresolved(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    _import(db_schema, tmp_path, _document(dashboards=[_dash()]))
    # The dashboard exists locally, but this file does not name it: not created or matched here.
    report = _import(db_schema, tmp_path, _document(dashboard_prefs=[PREF]))
    assert _lines(report, "dashboard_prefs", "unresolved")[0]["reason"] == (
        "dashboard was not created or matched in this import")
    assert await _count(db_session, UserDashboardPref) == [0]


@pytest.mark.parametrize("row", [
    {"owner_subject": "soc-lead"}, {**PREF, "dashboard_name": None}, {**PREF, "owner_subject": ""},
    {**PREF, "extra": 1}, {**PREF, "owner_subject": "x" * 256},
], ids=range(5))
async def test_invalid_preference_is_unresolved(db_schema, db_session, tmp_path, row):
    await _fleet(db_session)
    report = _import(db_schema, tmp_path, _document(dashboards=[_dash()], dashboard_prefs=[row]))
    assert len(_lines(report, "dashboard_prefs", "unresolved")) == 1
    assert len(_lines(report, "dashboards", "created")) == 1
    assert await _count(db_session, UserDashboardPref) == [0]


# ------------------------------------------------------------------------- whole-file rules
async def test_dry_run_writes_nothing_and_no_section_is_left_unapplied(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    await _subjects(db_session, fleet)
    document = _document(
        custom_rules=[RULE_LOG, RULE_ENDPOINT], dashboards=[_dash()], dashboard_prefs=[PREF])
    report = _import(db_schema, tmp_path, document, "--dry-run")
    assert report["committed"] is False
    for section in FILE_SECTIONS:
        lines = [line for outcome in ("created", "unchanged", "conflict", "unresolved")
                 for line in _lines(report, section, outcome)]
        assert lines and all(line["reason"] != "not applied in this release" for line in lines)
    assert len(_lines(report, "custom_rules", "created")) == 2
    assert await _count(db_session, CustomRule, Dashboard, UserDashboardPref) == [0, 0, 0]


async def test_second_import_of_the_whole_file_is_unchanged_everywhere(
        db_schema, db_session, tmp_path):
    fleet = await _fleet(db_session)
    await _subjects(db_session, fleet)
    layout = _layout(_widget("w_traf1", "waf.endpoint_traffic", waf_source=WAF,
                             endpoint=ENDPOINT_REF))
    document = _document(
        custom_rules=[RULE_LOG, RULE_ENDPOINT, RULE_ENTITY, RULE_VALUE, RULE_CLASS],
        dashboards=[_dash(layout=layout)], dashboard_prefs=[PREF])
    first = _import(db_schema, tmp_path, document)
    assert len(_lines(first, "custom_rules", "created")) == 5
    again = _import(db_schema, tmp_path, copy.deepcopy(document))
    assert len(_lines(again, "custom_rules", "unchanged")) == 5
    assert len(_lines(again, "dashboards", "unchanged")) == 1
    assert len(_lines(again, "dashboard_prefs", "unchanged")) == 1
    assert again["committed"] is True
    assert await _count(db_session, CustomRule, Dashboard, UserDashboardPref) == [5, 1, 1]


async def test_rule_name_held_by_another_definition_is_a_conflict_not_an_integrity_error(
        db_schema, db_session, tmp_path):
    await _fleet(db_session)
    db_session.add(CustomRule(
        name=RULE_LOG["name"], subject_kind="FLEET_CLASS", source_class="WAF", metric="eps",
        operator="GT", threshold_value=1.0, sustain_buckets=1, recovery_buckets=1))
    await db_session.commit()
    report = _import(db_schema, tmp_path, _document(
        custom_rules=[RULE_LOG], dashboards=[_dash()]))
    assert len(_lines(report, "custom_rules", "conflict")) == 1
    assert len(_lines(report, "dashboards", "created")) == 1
    assert report["committed"] is True
