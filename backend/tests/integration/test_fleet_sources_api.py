"""UIQ-7a: fleet source list search, filters, sort, facets, bulk class and CSV. DB-gated."""

from __future__ import annotations

import os
import uuid
from datetime import timedelta

import pytest

from app.models.enums import (
    AnomalyState,
    AnomalyType,
    IdentityProfileValidationStatus,
    IpsProfileValidationStatus,
    Severity,
    WafProfileValidationStatus,
)
from app.models.fleet import LogSourceGroup
from app.models.identity_detection import IdentitySourceProfile
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline
from app.models.waf import WafSourceProfile
from tests.integration.factories import add_metric, make_instance, make_log_source
from tests.integration.test_fleet_api import (  # noqa: F401  (fixtures and helpers)
    ADMIN,
    CLOSED,
    EDITOR,
    MISSING_ID,
    NOTHING,
    NOW,
    VIEWER,
    Client,
    _fixed_clock,
    audit_rows,
    classification,
    run,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run fleet source API integration tests",
    ),
]

URL = "/api/v1/fleet/sources"


def _fleet(db_schema):  # type: ignore[no-untyped-def]
    """Six sources covering every flow, profile, class, group and EPS state."""

    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add_all([
            LogSourceGroup(instance_id=inst.id, qradar_id=10, name="Perimeter",
                           assignable=True, synced_at=CLOSED),
            LogSourceGroup(instance_id=inst.id, qradar_id=11, name="Linux",
                           assignable=True, synced_at=CLOSED),
        ])
        recent = NOW - timedelta(minutes=2)
        old = NOW - timedelta(days=2)
        a = await make_log_source(s, inst, qradar_id=1, name="a-waf", type_id=100,
                                  type_name="F5 BIG-IP", identifier="10.0.0.1",
                                  group_ids=[10], last_event_time=recent)
        b = await make_log_source(s, inst, qradar_id=2, name="b-ips", type_id=200,
                                  type_name="Cisco FTD", identifier="ftd.local",
                                  group_ids=[10, 11], last_event_time=old)
        c = await make_log_source(s, inst, qradar_id=3, name="c-off", type_id=200,
                                  type_name="Cisco FTD", enabled=False,
                                  last_event_time=old)
        d = await make_log_source(s, inst, qradar_id=4, name="d-never", type_id=300,
                                  type_name="Windows", identifier="=cmd|calc")
        e = await make_log_source(s, inst, qradar_id=5, name="e-id", type_id=300,
                                  type_name="Windows", last_event_time=recent)
        f = await make_log_source(s, inst, qradar_id=6, name="f-none", type_id=None,
                                  type_name=None, last_event_time=recent)
        s.add_all([
            classification(a, "WAF"), classification(b, "IPS", review="NEW"),
            classification(c, "FIREWALL"), classification(d, "OTHER"),
            classification(e, "OTHER"),
        ])
        s.add(LogSourceAnomaly(
            log_source_id=b.id, anomaly_type=AnomalyType.NO_EVENTS, severity=Severity.HIGH,
            detected_at=CLOSED, state=AnomalyState.OPEN))
        # A suppressed and a resolved NO_EVENTS finding are not silence.
        s.add(LogSourceAnomaly(
            log_source_id=e.id, anomaly_type=AnomalyType.NO_EVENTS, severity=Severity.HIGH,
            detected_at=CLOSED, state=AnomalyState.OPEN, suppressed=True))
        s.add(LogSourceAnomaly(
            log_source_id=a.id, anomaly_type=AnomalyType.NO_EVENTS, severity=Severity.HIGH,
            detected_at=CLOSED, state=AnomalyState.RESOLVED))
        s.add(WafSourceProfile(
            instance_id=inst.id, log_source_id=a.id, enabled=True, url_property="url",
            method_property="method",
            validation_status=WafProfileValidationStatus.VALID))
        s.add(IpsSourceProfile(
            instance_id=inst.id, log_source_id=b.id, enabled=False,
            validation_status=IpsProfileValidationStatus.UNVALIDATED))
        s.add(IdentitySourceProfile(
            instance_id=inst.id, log_source_id=e.id, enabled=True,
            event_id_property="EventID", username_property="username",
            validation_status=IdentityProfileValidationStatus.INCONCLUSIVE))
        # Current EPS: a 50 (below its band), e 5 (above its band), f 10 (no baseline).
        await add_metric(s, a, CLOSED - timedelta(minutes=5), average_eps=50.0)
        await add_metric(s, e, CLOSED - timedelta(minutes=5), average_eps=5.0)
        await add_metric(s, f, CLOSED - timedelta(minutes=5), average_eps=10.0)
        for src, med, lo, hi in ((a, 100.0, 80.0, 120.0), (e, 1.0, 0.5, 2.0)):
            s.add(LogSourceBaseline(
                log_source_id=src.id, metric_name="average_eps", weekday=NOW.isoweekday(),
                hour=NOW.hour, median=med, p05=lo, p95=hi, is_reliable=True, sample_count=30))
        return {x.name: str(x.id) for x in (a, b, c, d, e, f)}

    return run(setup)


def _names(client, query: str) -> list[str]:  # type: ignore[no-untyped-def]
    resp = client.get(f"{URL}{query}")
    assert resp.status_code == 200, resp.text
    return [i["name"] for i in resp.json()["items"]]


def test_filters_alone_and_combined(db_schema) -> None:
    _fleet(db_schema)
    c = Client(VIEWER)
    assert _names(c, "?class=WAF&class=IPS") == ["a-waf", "b-ips"]
    assert _names(c, "?type_id=200") == ["b-ips", "c-off"]
    assert _names(c, "?type_id=100&type_id=300") == ["a-waf", "d-never", "e-id"]
    assert _names(c, "?group=11") == ["b-ips"]
    assert _names(c, "?group=10&group=11") == ["a-waf", "b-ips"]
    assert _names(c, "?flow=FLOWING") == ["a-waf", "e-id", "f-none"]
    assert _names(c, "?flow=SILENT") == ["b-ips"]
    assert _names(c, "?flow=DISABLED_IN_QRADAR") == ["c-off"]
    assert _names(c, "?flow=NEVER") == ["d-never"]
    assert _names(c, "?flow=SILENT&flow=NEVER") == ["b-ips", "d-never"]
    assert _names(c, "?eps=BELOW_EXPECTED") == ["a-waf"]
    assert _names(c, "?eps=ABOVE_EXPECTED") == ["e-id"]
    assert _names(c, "?eps=NO_BASELINE") == ["b-ips", "c-off", "d-never", "f-none"]
    assert _names(c, "?profile=WAF") == ["a-waf"]
    assert _names(c, "?profile=IPS&profile=IDENTITY") == ["b-ips", "e-id"]
    assert _names(c, "?profile=NONE") == ["c-off", "d-never", "f-none"]
    assert _names(c, "?review_status=NEW") == ["b-ips"]
    assert _names(c, "?class=OTHER&profile=IDENTITY") == ["e-id"]
    assert _names(c, "?class=OTHER&flow=NEVER&type_id=300") == ["d-never"]
    assert _names(c, "?class=WAF&flow=NEVER") == []
    for bad in ("?flow=GONE", "?eps=HIGH", "?profile=X", "?sort=bogus", "?order=up",
                "?type_id=abc", "?group=-1"):
        assert c.get(f"{URL}{bad}").status_code == 422, bad


def test_q_fields_digits_and_wildcards(db_schema) -> None:
    _fleet(db_schema)
    c = Client(VIEWER)
    assert _names(c, "?q=WAF") == ["a-waf"]  # name, case-insensitive
    assert _names(c, "?q=big-ip") == ["a-waf"]  # type name
    assert _names(c, "?q=ftd.local") == ["b-ips"]  # identifier
    assert _names(c, "?q=3") == ["c-off"]  # digits: the QRadar id, exact
    assert _names(c, "?q=10.0") == ["a-waf"]  # not all digits: substring only
    assert _names(c, "?q=%25") == []  # wildcards are literal
    assert _names(c, "?q=_") == []
    assert _names(c, "?q=99999999999999999999") == []  # beyond int4: no overflow


def test_sort_orders_nulls_last_and_tie_break(db_schema) -> None:
    _fleet(db_schema)
    c = Client(VIEWER)
    assert _names(c, "?sort=name&order=desc") == [
        "f-none", "e-id", "d-never", "c-off", "b-ips", "a-waf"]
    # type: null last in both directions; equal types fall back to the QRadar id.
    asc = _names(c, "?sort=type&order=asc")
    assert asc == ["b-ips", "c-off", "a-waf", "d-never", "e-id", "f-none"]
    desc = _names(c, "?sort=type&order=desc")
    assert desc[:2] == ["e-id", "d-never"] and desc[-1] == "f-none"
    assert desc[2:5] == ["a-waf", "c-off", "b-ips"]
    assert _names(c, "?sort=eps&order=desc")[:3] == ["a-waf", "f-none", "e-id"]
    assert _names(c, "?sort=eps&order=asc")[:3] == ["e-id", "f-none", "a-waf"]
    # last_event: never-seen last either way.
    assert _names(c, "?sort=last_event&order=asc")[-1] == "d-never"
    assert _names(c, "?sort=last_event&order=desc")[-1] == "d-never"
    # silent_for: only the SILENT source has a value; the rest sort last.
    assert _names(c, "?sort=silent_for&order=desc")[0] == "b-ips"
    assert _names(c, "?sort=silent_for&order=asc")[0] == "b-ips"
    assert _names(c, "?sort=class&order=asc")[0] == "c-off"  # FIREWALL


def test_row_fields_profiles_flow_and_eps(db_schema) -> None:
    ids = _fleet(db_schema)
    body = Client(VIEWER).get(URL).json()
    rows = {r["name"]: r for r in body["items"]}
    a, b, e = rows["a-waf"], rows["b-ips"], rows["e-id"]
    assert a["waf_profile"] == {"status": "VALID", "enabled": True}
    assert a["ips_profile"] is None and a["identity_profile"] is None
    assert b["ips_profile"] == {"status": "UNVALIDATED", "enabled": False}
    assert e["identity_profile"] == {"status": "INCONCLUSIVE", "enabled": True}
    assert a["group_names"] == ["Perimeter"] and b["group_names"] == ["Linux", "Perimeter"]
    assert b["group_count"] == 2 and rows["d-never"]["group_names"] == []
    assert a["identifier"] == "10.0.0.1" and a["type_id"] == 100
    assert a["flow"] == "FLOWING" and a["silent_for_seconds"] is None
    assert b["flow"] == "SILENT"
    assert b["silent_for_seconds"] > 86400
    assert rows["d-never"]["flow"] == "NEVER" and rows["d-never"]["last_event_at"] is None
    assert a["current_eps"] == 50.0 and a["expected_eps"] == 100.0
    assert (a["expected_eps_low"], a["expected_eps_high"]) == (80.0, 120.0)
    assert a["eps_state"] == "BELOW_EXPECTED" and e["eps_state"] == "ABOVE_EXPECTED"
    assert rows["f-none"]["eps_state"] == "NO_BASELINE"
    # No current measurement is null, never zero.
    assert b["current_eps"] is None and b["eps_state"] == "NO_BASELINE"
    assert a["id"] == ids["a-waf"]


def test_facets_count_under_the_other_filters(db_schema) -> None:
    _fleet(db_schema)
    c = Client(VIEWER)
    facets = c.get(URL).json()["facets"]
    assert {f["value"]: f["count"] for f in facets["source_class"]} == {
        "WAF": 1, "IPS": 1, "FIREWALL": 1, "OTHER": 2, "UNCLASSIFIED": 1}
    assert {f["value"]: f["count"] for f in facets["flow"]} == {
        "FLOWING": 3, "SILENT": 1, "DISABLED_IN_QRADAR": 1, "NEVER": 1}
    assert {f["value"]: f["count"] for f in facets["profile"]} == {
        "NONE": 3, "WAF": 1, "IPS": 1, "IDENTITY": 1}
    assert facets["type"][0] == {"type_id": 200, "name": "Cisco FTD", "count": 2}
    # Under class=OTHER: the class facet ignores its own filter, the others obey it.
    f2 = c.get(f"{URL}?class=OTHER").json()["facets"]
    assert {x["value"]: x["count"] for x in f2["source_class"]} == {
        "WAF": 1, "IPS": 1, "FIREWALL": 1, "OTHER": 2, "UNCLASSIFIED": 1}
    assert {x["value"]: x["count"] for x in f2["flow"]} == {"NEVER": 1, "FLOWING": 1}
    assert {x["value"]: x["count"] for x in f2["profile"]} == {
        "NONE": 1, "WAF": 0, "IPS": 0, "IDENTITY": 1}
    f3 = c.get(f"{URL}?flow=NEVER").json()
    assert {x["value"]: x["count"] for x in f3["facets"]["source_class"]} == {"OTHER": 1}
    assert f3["total"] == 1
    f5 = c.get(f"{URL}?flow=NEVER").json()["facets"]
    assert {x["value"]: x["count"] for x in f5["flow"]} == {
        "FLOWING": 3, "SILENT": 1, "DISABLED_IN_QRADAR": 1, "NEVER": 1}
    f4 = c.get(f"{URL}?type_id=300").json()["facets"]
    assert sum(x["count"] for x in f4["type"]) == 6  # the type facet ignores type_id


def test_sql_is_constant_in_fleet_size(db_schema) -> None:
    async def setup(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        s.add(LogSourceGroup(instance_id=inst.id, qradar_id=10, name="G",
                             assignable=True, synced_at=CLOSED))
        for i in range(1, 506):
            ls = await make_log_source(s, inst, qradar_id=i, name=f"s-{i:04d}",
                                       type_id=i % 7, type_name=f"t{i % 7}",
                                       group_ids=[10], last_event_time=NOW)
            s.add(classification(ls, "OTHER"))
            s.add(WafSourceProfile(
                instance_id=inst.id, log_source_id=ls.id, enabled=True, url_property="u",
                method_property="m"))

    run(setup)
    c = Client(VIEWER)
    seen = {}
    for label, query in (
        ("5", "?limit=5"),
        ("500", "?limit=500"),
        ("filtered5", "?limit=5&class=OTHER&profile=WAF&eps=NO_BASELINE&flow=FLOWING&q=s-"),
        ("filtered500", "?limit=500&class=OTHER&profile=WAF&eps=NO_BASELINE&flow=FLOWING&q=s-"),
    ):
        resp = c.get(f"{URL}{query}")
        assert resp.status_code == 200, resp.text
        seen[label] = c.statements
    assert seen["5"] == seen["500"] and seen["filtered5"] == seen["filtered500"], seen
    assert seen["500"] <= 12, seen


def _bulk(client, ids, cls, reviewed=False):  # type: ignore[no-untyped-def]
    return client.post(f"{URL}/classification", json={
        "log_source_ids": ids, "class": cls, "mark_reviewed": reviewed})


def test_bulk_permissions_and_bounds(db_schema) -> None:
    ids = _fleet(db_schema)
    one = [ids["a-waf"]]
    assert _bulk(Client(NOTHING), one, "DNS").status_code == 403
    assert _bulk(Client(VIEWER), one, "DNS").status_code == 403
    editor = Client(EDITOR)
    assert _bulk(editor, [str(uuid.uuid4()) for _ in range(501)], "DNS").status_code == 422
    assert _bulk(editor, [], "DNS").status_code == 422
    assert _bulk(editor, one, "NOT_A_CLASS").status_code == 422
    assert editor.post(f"{URL}/classification", json={"log_source_ids": one}).status_code == 422
    resp = _bulk(editor, [*one, MISSING_ID, str(uuid.uuid4())], "DNS")
    assert resp.status_code == 404 and "2" in resp.json()["detail"]
    assert MISSING_ID not in resp.text
    assert audit_rows("fleet.classification_bulk") == []
    assert audit_rows("log_source.classification_override") == []


def test_bulk_other_instance_is_refused(db_schema) -> None:
    ids = _fleet(db_schema)

    async def other(s):  # type: ignore[no-untyped-def]
        inst = await make_instance(s)
        return str((await make_log_source(s, inst, qradar_id=1, name="elsewhere")).id)

    foreign = run(other)
    resp = _bulk(Client(EDITOR), [ids["a-waf"], foreign], "DNS")
    assert resp.status_code == 422
    rows = Client(VIEWER).get(f"{URL}?class=DNS").json()
    assert rows["total"] == 0


def test_bulk_sets_clears_reviews_and_audits(db_schema) -> None:
    ids = _fleet(db_schema)
    editor = Client(EDITOR)
    pair = [ids["b-ips"], ids["d-never"]]
    resp = _bulk(editor, [*pair, pair[0]], "DNS")  # a repeated id counts once
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["updated"] == 2
    got = {r["name"]: r for r in body["items"]}
    assert set(got) == {"b-ips", "d-never"}
    for r in got.values():
        assert r["source_class"] == "DNS" and r["basis"] == "OVERRIDE"
        assert r["override_class"] == "DNS" and r["override_by"] == "editor"
        assert r["override_at"] is not None
    assert got["b-ips"]["review_status"] == "NEW"  # classifying does not review
    assert body["items"][0]["waf_profile"] is None and "flow" in body["items"][0]
    assert len(audit_rows("log_source.classification_override")) == 2
    summary = audit_rows("fleet.classification_bulk")
    assert len(summary) == 1 and summary[0].detail["updated"] == 2
    assert summary[0].detail["reviewed"] == 0 and summary[0].object_id is None
    assert audit_rows("log_source.review") == []

    reviewed = _bulk(editor, pair, "DNS", reviewed=True).json()
    got = {r["name"]: r for r in reviewed["items"]}
    assert got["b-ips"]["review_status"] == "REVIEWED"
    assert got["b-ips"]["reviewed_by"] == "editor"
    assert len(audit_rows("log_source.review")) == 1  # only the one that was NEW
    assert audit_rows("fleet.classification_bulk")[-1].detail["reviewed"] == 1

    cleared = _bulk(editor, pair, None).json()
    for r in cleared["items"]:
        assert r["override_class"] is None and r["override_by"] is None
        assert r["basis"] != "OVERRIDE"
    last = audit_rows("log_source.classification_override")[-1]
    assert last.detail["to"] is None and last.detail["from"] == "DNS"
    # Sources outside the request did not move.
    assert Client(VIEWER).get(f"{URL}?class=WAF").json()["total"] == 1


def test_csv_export_filters_formula_guard_and_cap(db_schema) -> None:
    _fleet(db_schema)
    assert Client(NOTHING).get(f"{URL}.csv").status_code == 403
    c = Client(VIEWER)
    resp = c.get(f"{URL}.csv?flow=NEVER")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert "x-export-truncated" not in resp.headers
    lines = resp.text.split("\r\n")
    assert lines[0].startswith("id,qradar_id,name,type_name,identifier,class,")
    assert len(lines) == 3 and lines[2] == ""
    assert "'=cmd|calc" in lines[1]  # the formula guard
    full = c.get(f"{URL}.csv?sort=name").text.split("\r\n")
    assert len(full) == 8
    assert "Z," in full[1] and "2026-10-13T10:05:00Z" in full[1]  # UTC stamp


def test_csv_cap_marks_truncation(db_schema, monkeypatch) -> None:
    _fleet(db_schema)
    from app.core.config import get_settings

    monkeypatch.setenv("API_MAX_EXPORT_ROWS", "2")
    get_settings.cache_clear()
    try:
        resp = Client(VIEWER).get(f"{URL}.csv")
        assert resp.headers["x-export-truncated"] == "true"
        assert len(resp.text.split("\r\n")) == 4  # header, two rows, trailing
    finally:
        monkeypatch.delenv("API_MAX_EXPORT_ROWS")
        get_settings.cache_clear()
