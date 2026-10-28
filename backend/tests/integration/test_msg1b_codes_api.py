"""MSG-1b: the authoring reasons and the remaining 409s carry message codes on the wire.

Each assertion reads the JSON a client receives: the English sentence as before, and the code
beside it (`reason_codes` parallel to `reasons`, `detail_code` / `detail_params`, or `code` /
`params` inside a conflict body).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select

from app.fleet.service import ensure_seed_rules
from app.models.fleet import ClassificationRule
from app.workers import tasks
from tests.integration import test_ips_profile_admin as ips_admin
from tests.integration import test_waf_profile_admin as waf_admin
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_custom_rules_api import (
    PREFIX,
    _client,
    _principal,
    _rule_body,
    _search_body,
)
from tests.integration.test_fleet_api import (
    ADMIN,
    RULES,
    Client,
    _instance_with_sources,
    _rule,
    run,
)

pytestmark = pytest.mark.integration


# --- custom rules ------------------------------------------------------------------------------


async def test_a_422_lists_each_reason_with_its_code(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=811, name="reason-src")
    await db_session.commit()
    client = _client(db_schema, _principal("admin"))
    body = _rule_body(
        source.id,
        "coded-rule",
        metric="it's",
        operator="NE",
        threshold_value=-1,
        sustain_buckets=0,
        recovery_buckets=1,
    )
    response = client.post(PREFIX, json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reasons"] == [
        "metric \"it's\" is not in the custom-rule catalog",
        "threshold_value must be at least 0",
        "operator 'NE' is not one of GT, GE, LT, LE, EQ",
        "sustain_buckets must be at least 1",
    ]
    assert detail["message"] == "; ".join(detail["reasons"])
    assert detail["reason_codes"] == [
        {"code": "ruledraft.metric_unknown", "params": {"metric": "\"it's\""}},
        {"code": "ruledraft.threshold_negative", "params": {}},
        {"code": "ruledraft.operator_unknown", "params": {"operator": "'NE'"}},
        {"code": "ruledraft.sustain_min", "params": {}},
    ]
    preview = client.post(f"{PREFIX}/preview", json={k: v for k, v in body.items() if k != "name"})
    assert preview.status_code == 422
    assert preview.json()["detail"]["reason_codes"] == detail["reason_codes"]


async def test_a_missing_subject_reason_is_coded(db_session, db_schema: str) -> None:
    await db_session.commit()
    missing = uuid.uuid4()
    client = _client(db_schema, _principal("detection_engineer"))
    response = client.post(
        PREFIX,
        json={
            "name": "gone-endpoint",
            "subject_kind": "WAF_ENDPOINT",
            "waf_endpoint_id": str(missing),
            "metric": "block_ratio",
            "operator": "GT",
            "threshold_value": 0.5,
            "sustain_buckets": 1,
            "recovery_buckets": 1,
            "enabled": False,
        },
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reasons"] == [f"waf endpoint {missing} does not exist"]
    assert detail["reason_codes"] == [
        {"code": "ruledraft.waf_endpoint_missing", "params": {"id": str(missing)}}
    ]


async def test_the_enabled_cap_409_is_a_coded_refusal(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=812, name="cap-src-b")
    await db_session.commit()
    client = _client(db_schema, _principal("admin"), custom_rules_max_enabled=1)
    assert client.post(PREFIX, json=_rule_body(source.id, "cap-a", enabled=True)).status_code == 201
    second = client.post(PREFIX, json=_rule_body(source.id, "cap-b", enabled=True))
    assert second.status_code == 409, second.text
    body = second.json()
    assert body["detail"] == "at most 1 rules may be enabled at once (1 already are)"
    assert body["detail_code"] == "ruledraft.enabled_cap"
    assert body["detail_params"] == {"max_enabled": 1, "enabled_count": 1}


async def test_a_null_field_patch_is_a_coded_422(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=813, name="null-src")
    await db_session.commit()
    client = _client(db_schema, _principal("admin"))
    created = client.post(PREFIX, json=_rule_body(source.id, "null-rule"))
    assert created.status_code == 201, created.text
    response = client.patch(
        f"{PREFIX}/{created.json()['id']}",
        json={"expected_updated_at": created.json()["updated_at"], "metric": None},
    )
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reasons"] == ["metric must not be null"]
    assert detail["reason_codes"] == [
        {"code": "ruledraft.null_field", "params": {"field": "metric"}}
    ]


# --- searches ----------------------------------------------------------------------------------


def test_an_unsafe_query_422_carries_a_code_per_reason(db_schema: str) -> None:
    client = _client(db_schema, _principal("admin"))
    body = _search_body("unsafe")
    body["aql_query"] = "DELETE FROM events LIMIT 99999"
    response = client.post("/api/v1/searches", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reasons"] == [
        "query must be a single SELECT statement",
        "disallowed keyword(s): DELETE",
        "query must include a bounded time window (LAST N ..., or START/STOP)",
        "LIMIT 99999 exceeds the maximum 10000 rows",
    ]
    assert detail["message"] == "unsafe AQL: " + "; ".join(detail["reasons"])
    assert [item["code"] for item in detail["reason_codes"]] == [
        "search.aql.not_select",
        "search.aql.disallowed_keywords",
        "search.aql.no_time_window",
        "search.aql.limit_exceeded",
    ]
    assert detail["reason_codes"][3]["params"] == {"limit": 99999, "max_rows": 10000}


def test_a_threshold_that_can_never_be_reached_is_coded(db_schema: str) -> None:
    client = _client(db_schema, _principal("admin"))
    body = _search_body("unreachable")
    body.update({"threshold_operator": "GT", "threshold_value": 500, "max_result_rows": 100})
    response = client.post("/api/v1/searches", json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reason_codes"] == [
        {
            "code": "search.threshold.unreachable",
            "params": {"operator": "GT", "threshold": "500", "max_result_rows": 100},
        }
    ]
    assert detail["reasons"][0].startswith("threshold GT 500 can never be reached")


def test_the_enabled_search_cap_409_is_coded(db_schema: str) -> None:
    client = _client(db_schema, _principal("admin"), scheduled_searches_max_enabled=1)
    assert client.post("/api/v1/searches", json=_search_body("cap-one")).status_code == 201
    second = client.post("/api/v1/searches", json=_search_body("cap-two"))
    assert second.status_code == 409, second.text
    assert second.json() == {
        "detail": "the limit is 1 enabled searches",
        "detail_code": "search.enabled_cap",
        "detail_params": {"limit": 1},
    }
    off = client.post("/api/v1/searches", json={**_search_body("cap-off"), "enabled": False})
    assert off.status_code == 201, off.text
    enabling = client.patch(f"/api/v1/searches/{off.json()['id']}", json={"enabled": True})
    assert enabling.status_code == 409
    assert enabling.json()["detail_code"] == "search.enabled_cap"


# --- classification rules ----------------------------------------------------------------------


def test_the_classification_rule_409s_are_coded(db_schema: str) -> None:
    instance_id, _ = _instance_with_sources()
    admin = Client(ADMIN)
    assert admin.post(RULES, json=_rule(instance_id=instance_id)).status_code == 201
    duplicate = admin.post(RULES, json=_rule(instance_id=instance_id))
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "detail": "a rule with this instance, kind and pattern already exists",
        "detail_code": "classification.rule.duplicate",
        "detail_params": {},
    }

    async def setup(s):  # type: ignore[no-untyped-def]
        await ensure_seed_rules(s)
        return str(
            await s.scalar(
                select(ClassificationRule.id).where(ClassificationRule.built_in.is_(True)).limit(1)
            )
        )

    built_in = run(setup)
    edit = admin.patch(f"{RULES}/{built_in}", json={"pattern": "x"})
    assert edit.status_code == 409
    assert edit.json()["detail"] == "a built-in rule can only be enabled or disabled"
    assert edit.json()["detail_code"] == "classification.rule.built_in_edit"
    delete = admin.delete(f"{RULES}/{built_in}")
    assert delete.status_code == 409
    assert delete.json() == {
        "detail": "a built-in rule cannot be deleted; disable it instead",
        "detail_code": "classification.rule.built_in_delete",
        "detail_params": {},
    }


# --- WAF and IPS profiles ----------------------------------------------------------------------


async def test_waf_profile_conflicts_and_input_refusals_are_coded(
    db_session, db_schema: str, monkeypatch
) -> None:
    _, source = await waf_admin.seed_source(db_session, qradar_id=2101)
    await db_session.commit()
    monkeypatch.setattr(tasks.validate_waf_profile_task, "delay", lambda _id: None)
    api = waf_admin.admin_client(db_schema)
    path = f"/api/v1/waf/log-sources/{source.id}/profile"

    missing = api.put(path, json=waf_admin.profile_body(expected=3))
    assert missing.status_code == 409
    detail = missing.json()["detail"]
    assert detail["code"] == "validation.profile.conflict.missing"
    assert detail["params"] == {}
    assert detail["message"] == (
        "the profile does not exist; create it with expected_profile_version null"
    )

    bad_name = waf_admin.profile_body()
    bad_name["mapping"]["URL"] = "it's;bad"
    refused = api.put(path, json=bad_name)
    assert refused.status_code == 422
    assert refused.json()["detail"]["code"] == "validation.waf.input.property_name"
    assert refused.json()["detail"]["params"] == {"name": "\"it's;bad\""}

    overlap = waf_admin.profile_body()
    overlap["action_value_map"]["ALLOWED"] = ["deny"]
    refused = api.put(path, json=overlap)
    assert refused.json()["detail"]["code"] == "validation.waf.input.action_overlap"
    assert refused.json()["detail"]["params"] == {"values": "['deny']"}

    assert api.put(path, json=waf_admin.profile_body()).status_code == 201
    stale = api.put(path, json=waf_admin.profile_body(expected=9, url="Request URL"))
    assert stale.status_code == 409
    detail = stale.json()["detail"]
    assert detail["code"] == "validation.profile.conflict.version"
    assert detail["params"] == {"current": 1}
    assert detail["message"] == "profile version conflict; current version is 1"
    assert detail["current_version"] == 1 and detail["updated_by"] == "profile-admin"

    running = api.post(f"{path}/validate")
    assert running.status_code == 409
    detail = running.json()["detail"]
    assert detail["code"] == "validation.profile.conflict.running"
    assert detail["params"]["profile_version"] == 1
    assert detail["message"].startswith("validation of version 1 is already running since ")
    assert detail["message"].endswith(detail["params"]["since"])
    assert datetime.fromisoformat(detail["params"]["since"]).tzinfo is not None


async def test_waf_enable_of_an_unvalidated_profile_is_coded(
    db_session, db_schema: str, monkeypatch
) -> None:
    _, source = await waf_admin.seed_source(db_session, qradar_id=2102)
    await db_session.commit()
    monkeypatch.setattr(tasks.validate_waf_profile_task, "delay", lambda _id: None)
    api = waf_admin.admin_client(db_schema)
    path = f"/api/v1/waf/log-sources/{source.id}/profile"
    assert api.put(path, json=waf_admin.profile_body()).status_code == 201
    enable = api.post(f"{path}/enable")
    assert enable.status_code == 409
    detail = enable.json()["detail"]
    assert detail["code"] == "validation.profile.conflict.not_validated"
    assert detail["params"] == {"profile_version": 1, "validation_status": "UNVALIDATED"}
    assert detail["message"] == (
        "profile version 1 has not validated (status UNVALIDATED); run validation first"
    )


async def test_ips_profile_conflicts_and_input_refusals_are_coded(
    db_session, db_schema: str, monkeypatch
) -> None:
    _, source = await ips_admin.seed_source(db_session, qradar_id=2201)
    await db_session.commit()
    monkeypatch.setattr(tasks.validate_ips_profile_task, "delay", lambda _id: None)
    api = ips_admin.admin_client(db_schema)
    path = f"/api/v1/ips/log-sources/{source.id}/profile"

    missing = api.put(path, json=ips_admin.profile_body(expected=2))
    assert missing.json()["detail"]["code"] == "validation.profile.conflict.missing"

    invalid = ips_admin.profile_body()
    invalid["mapping"]["SOURCE_IP"] = {"kind": "NORMALIZED", "name": "username"}
    refused = api.put(path, json=invalid)
    assert refused.status_code == 422
    assert refused.json()["detail"] == {
        "message": "normalized field is not in the IPS allowlist",
        "code": "validation.ips.input.normalized_field",
        "params": {},
    }

    without_action = ips_admin.profile_body()
    without_action["action_value_map"] = {"BLOCKED": ["DROP"], "ALLOWED": []}
    refused = api.put(path, json=without_action)
    assert refused.json()["detail"]["code"] == "validation.ips.input.action_needs_field"

    assert api.put(path, json=ips_admin.profile_body()).status_code == 201
    stale = api.put(path, json=ips_admin.profile_body(expected=9, destination_port="x"))
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "validation.profile.conflict.version"
    assert stale.json()["detail"]["params"] == {"current": 1}
    running = api.post(f"{path}/validate")
    assert running.json()["detail"]["code"] == "validation.profile.conflict.running"
    enable = api.post(f"{path}/enable")
    assert enable.json()["detail"]["code"] == "validation.profile.conflict.not_validated"
    assert enable.json()["detail"]["params"]["validation_status"] == "UNVALIDATED"


