"""Custom-rule API: guards, validation, cap, version, delete, audit, preview.

Driven off the OpenAPI document so a new ``/custom-rules`` route cannot ship
without the router guard. Agreement with the evaluator is on stored buckets,
one hand-built case per measurement kind.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.alerts.fingerprint import custom_rule_breach_fingerprint
from app.api.routes.custom_rules import build_custom_rule_catalog
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.custom_rules.alerts import alerting_mode
from app.custom_rules.engine import CustomRuleEngine
from app.custom_rules.evaluate import latest_closed_bucket
from app.custom_rules.preview import replay
from app.custom_rules.validation import CustomRuleDraft, validate_rule_draft
from app.main import create_app
from app.models.alert import Alert
from app.models.custom_rule import CustomRule, CustomRuleBreach, CustomRuleWatermark
from app.models.enums import (
    AlertStatus,
    BucketCompleteness,
    ClassificationReviewStatus,
    CustomRuleMetric,
    CustomRuleSubjectKind,
    Severity,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.identity import AuditLog
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.seed import _ROLES
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_waf_scale import counting

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run custom-rule API integration tests",
    ),
]

NIL = "00000000-0000-0000-0000-000000000000"
PREFIX = "/api/v1/custom-rules"
AQL = "SELECT sourceip FROM events LAST 1 HOURS"
HOURLY = "0 * * * *"
NOBODY = Principal(subject="nobody", permissions=frozenset())


def _principal(role: str) -> Principal:
    return Principal(subject=role, permissions=frozenset(_ROLES[role]))


def _client(
    db_url: str,
    principal: Principal | None,
    **overrides: object,
) -> TestClient:
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    updates: dict[str, object] = {"collection_interval_seconds": 300}
    updates.update(overrides)
    settings = get_settings().model_copy(update=updates)
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def _anonymous(db_url: str) -> TestClient:
    """OIDC with no bearer. The session override keeps the request off the dev database."""

    async def _no_session():  # type: ignore[no-untyped-def]
        yield None

    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_session] = _no_session
    return TestClient(app)


def _operations(client: TestClient) -> list[tuple[str, str]]:
    schema = client.app.openapi()
    found: list[tuple[str, str]] = []
    for path, operations in schema["paths"].items():
        if not path.startswith(PREFIX):
            continue
        concrete = path.replace("{rule_id}", NIL)
        assert "{" not in concrete, path
        for method in operations:
            if method in ("get", "post", "patch", "put", "delete"):
                found.append((method.upper(), concrete))
    return found


def _rule_body(source_id: uuid.UUID, name: str, **overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "name": name,
        "subject_kind": "LOG_SOURCE",
        "log_source_id": str(source_id),
        "metric": "event_count",
        "operator": "GT",
        "threshold_value": 100,
        "sustain_buckets": 3,
        "recovery_buckets": 2,
        "severity": "HIGH",
        "enabled": True,
    }
    body.update(overrides)
    return body


def _draft_body(source_id: uuid.UUID, **overrides: object) -> dict[str, object]:
    body = _rule_body(source_id, "ignored")
    body.pop("name")
    body.update(overrides)
    return body


def _search_body(name: str) -> dict[str, object]:
    return {"name": name, "aql_query": AQL, "schedule_cron": HOURLY}


def _catalog_body(enabled_count: int, **overrides: object) -> dict[str, object]:
    """The catalog the route should return for these settings and this count."""
    updates: dict[str, object] = {"collection_interval_seconds": 300}
    updates.update(overrides)
    settings = get_settings().model_copy(update=updates)
    built = build_custom_rule_catalog(
        evaluation_enabled=settings.custom_rules_enabled,
        alerting_enabled=settings.custom_rules_alerting_enabled,
        max_enabled=settings.custom_rules_max_enabled,
        enabled_count=enabled_count,
    )
    return built.model_dump(mode="json")


def _settings(**overrides: object) -> Settings:
    values = get_settings().model_dump()
    values.update(custom_rules_enabled=True, collection_interval_seconds=300)
    values.update(overrides)
    return Settings(**values)


async def _audits(session, action: str) -> list[AuditLog]:  # type: ignore[no-untyped-def]
    session.expire_all()
    rows = await session.scalars(
        select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    )
    return list(rows.all())


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[tuple[str, datetime, datetime]] = []

    async def open_or_update(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append((event.action, event.first_bucket_start, event.last_bucket_start))

    async def resolve(self, event) -> None:  # type: ignore[no-untyped-def]
        self.events.append((event.action, event.first_bucket_start, event.last_bucket_start))


def test_every_custom_rule_route_without_a_token_is_401(db_schema: str) -> None:
    client = _anonymous(db_schema)
    operations = _operations(client)
    assert len(operations) >= 8
    for method, path in operations:
        response = client.request(method, path, json={})
        assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
        assert response.json()["detail"] == "missing bearer token"


def test_every_custom_rule_route_without_read_is_403(db_schema: str) -> None:
    client = _client(db_schema, NOBODY)
    operations = _operations(client)
    assert len(operations) >= 8
    for method, path in operations:
        response = client.request(method, path, json={})
        assert response.status_code == 403, (
            f"{method} {path} -> {response.status_code} {response.text}"
        )
        assert response.json()["detail"] == "missing required permission: read:custom_rules"


async def test_each_role_on_custom_rules_and_on_searches(db_session, db_schema: str) -> None:
    """Readers may preview. Only admin and detection_engineer may write.

    The admin grant is ``admin:*`` alone: the role does not list
    ``custom_rule:write``, and the write still succeeds.
    """
    assert "custom_rule:write" not in _ROLES["admin"]
    assert "custom_rule:write" in _ROLES["detection_engineer"]
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=701, name="role-src")
    await db_session.commit()

    admin = _client(db_schema, _principal("admin"))
    created = admin.post(PREFIX, json=_rule_body(source.id, "shared-rule", enabled=False))
    assert created.status_code == 201, created.text
    shared_id = created.json()["id"]
    token = created.json()["updated_at"]

    for role in ("admin", "analyst", "viewer", "detection_engineer"):
        client = _client(db_schema, _principal(role))
        assert client.get(PREFIX).status_code == 200
        assert client.get(f"{PREFIX}/{shared_id}").status_code == 200
        assert client.get(f"{PREFIX}/{shared_id}/breaches").status_code == 200
        assert client.get(f"{PREFIX}/{shared_id}/preview", params={"hours": 1}).status_code == 200
        draft = client.post(
            f"{PREFIX}/preview",
            params={"hours": 1},
            json=_draft_body(source.id, enabled=False),
        )
        assert draft.status_code == 200, f"{role} preview {draft.status_code} {draft.text}"

    forbidden = {"expected_updated_at": token, "description": "not allowed"}
    for role in ("analyst", "viewer"):
        client = _client(db_schema, _principal(role))
        denied = client.post(PREFIX, json=_rule_body(source.id, f"{role}-rule", enabled=False))
        assert denied.status_code == 403, denied.text
        assert denied.json()["detail"] == "missing required permission: custom_rule:write"
        patched = client.patch(f"{PREFIX}/{shared_id}", json=forbidden)
        assert patched.status_code == 403, patched.text
        assert patched.json()["detail"] == "missing required permission: custom_rule:write"
        assert client.delete(f"{PREFIX}/{shared_id}").status_code == 403

    for role in ("detection_engineer", "admin"):
        client = _client(db_schema, _principal(role))
        made = client.post(PREFIX, json=_rule_body(source.id, f"{role}-rule", enabled=False))
        assert made.status_code == 201, f"{role} {made.text}"
        edited = client.patch(
            f"{PREFIX}/{made.json()['id']}",
            json={"expected_updated_at": made.json()["updated_at"], "description": role},
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["description"] == role
        assert client.delete(f"{PREFIX}/{made.json()['id']}").status_code == 204

    engineer = _client(db_schema, _principal("detection_engineer"))
    assert engineer.get("/api/v1/outage-policy").status_code == 403
    assert admin.get("/api/v1/outage-policy").status_code == 200

    search = admin.post("/api/v1/searches", json=_search_body("role-search"))
    assert search.status_code == 201, search.text
    search_id = search.json()["id"]
    for role in ("analyst", "viewer"):
        client = _client(db_schema, _principal(role))
        posted = client.post("/api/v1/searches", json=_search_body(f"{role}-search"))
        assert posted.status_code == 403, posted.text
        assert posted.json()["detail"] == "missing required permission: search:write"
        changed = client.patch(f"/api/v1/searches/{search_id}", json={"description": role})
        assert changed.status_code == 403, changed.text
        assert changed.json()["detail"] == "missing required permission: search:write"
    for role in ("detection_engineer", "admin"):
        client = _client(db_schema, _principal(role))
        posted = client.post("/api/v1/searches", json=_search_body(f"{role}-search"))
        assert posted.status_code == 201, posted.text
        changed = client.patch(
            f"/api/v1/searches/{posted.json()['id']}", json={"description": role}
        )
        assert changed.status_code == 200, changed.text


async def test_create_422_lists_every_validator_reason(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=702, name="valid-src")
    await db_session.commit()
    body = _rule_body(
        source.id,
        "bad-rule",
        metric="nope",
        operator="NOPE",
        threshold_value=-5,
        sustain_buckets=0,
        recovery_buckets=0,
        enabled=False,
    )
    draft = CustomRuleDraft(
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        metric="nope",
        operator="NOPE",
        threshold_value=-5,
        sustain_buckets=0,
        recovery_buckets=0,
        log_source_id=source.id,
        enabled=False,
    )
    reasons = validate_rule_draft(draft, enabled_count=0, max_enabled=100)
    assert len(reasons) >= 4
    client = _client(db_schema, _principal("admin"))
    response = client.post(PREFIX, json=body)
    assert response.status_code == 422, response.text
    detail = response.json()["detail"]
    assert detail["reasons"] == reasons
    assert detail["message"] == "; ".join(reasons)
    preview = client.post(f"{PREFIX}/preview", json={k: v for k, v in body.items() if k != "name"})
    assert preview.status_code == 422, preview.text
    assert preview.json()["detail"]["reasons"] == reasons


def test_schema_errors_stay_a_list(db_schema: str) -> None:
    client = _client(db_schema, _principal("admin"))
    response = client.post(
        PREFIX,
        json={
            "name": "schema",
            "subject_kind": "NOPE",
            "metric": "event_count",
            "operator": "GT",
            "threshold_value": 1,
            "sustain_buckets": 1,
            "recovery_buckets": 1,
        },
    )
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


async def test_a_missing_subject_is_an_extra_reason(db_session, db_schema: str) -> None:
    await db_session.commit()
    missing = uuid.uuid4()
    draft = CustomRuleDraft(
        subject_kind=CustomRuleSubjectKind.WAF_ENDPOINT,
        metric=CustomRuleMetric.BLOCK_RATIO,
        operator="GT",
        threshold_value=0.0,
        sustain_buckets=1,
        recovery_buckets=1,
        waf_endpoint_id=missing,
        enabled=False,
    )
    reasons = validate_rule_draft(draft, enabled_count=0, max_enabled=100)
    reasons.append(f"waf endpoint {missing} does not exist")
    assert any("strictly between 0 and 1" in reason for reason in reasons)
    body = {
        "name": "missing-endpoint",
        "subject_kind": "WAF_ENDPOINT",
        "waf_endpoint_id": str(missing),
        "metric": "block_ratio",
        "operator": "GT",
        "threshold_value": 0,
        "sustain_buckets": 1,
        "recovery_buckets": 1,
        "enabled": False,
    }
    client = _client(db_schema, _principal("detection_engineer"))
    response = client.post(PREFIX, json=body)
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["reasons"] == reasons
    assert response.json()["detail"]["message"] == "; ".join(reasons)


async def test_the_enabled_cap_is_409_and_a_disabled_rule_is_not(
    db_session, db_schema: str
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=703, name="cap-src")
    await db_session.commit()
    client = _client(db_schema, _principal("admin"), custom_rules_max_enabled=1)
    first = client.post(PREFIX, json=_rule_body(source.id, "cap-one", enabled=True))
    assert first.status_code == 201, first.text
    second = client.post(PREFIX, json=_rule_body(source.id, "cap-two", enabled=True))
    assert second.status_code == 409, second.text
    assert isinstance(second.json()["detail"], str)
    assert "at most 1" in second.json()["detail"]
    assert "1 already" in second.json()["detail"]
    disabled = client.post(PREFIX, json=_rule_body(source.id, "cap-off", enabled=False))
    assert disabled.status_code == 201, disabled.text
    enabling = client.patch(
        f"{PREFIX}/{disabled.json()['id']}",
        json={"expected_updated_at": disabled.json()["updated_at"], "enabled": True},
    )
    assert enabling.status_code == 409, enabling.text
    assert "at most 1" in enabling.json()["detail"]
    # A preview does not take a slot, so an enabled draft is still a read.
    preview = client.post(
        f"{PREFIX}/preview",
        params={"hours": 1},
        json=_draft_body(source.id, enabled=True),
    )
    assert preview.status_code == 200, preview.text


async def test_a_stale_patch_is_409_and_an_empty_patch_does_not_move_the_token(
    db_session, db_schema: str
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=704, name="ver-src")
    await db_session.commit()
    client = _client(db_schema, _principal("detection_engineer"))
    created = client.post(PREFIX, json=_rule_body(source.id, "versioned", enabled=False))
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    original = created.json()["updated_at"]
    same = client.patch(f"{PREFIX}/{rule_id}", json={"expected_updated_at": original})
    assert same.status_code == 200, same.text
    assert same.json()["updated_at"] == original
    assert same.json()["name"] == "versioned"
    renamed = client.patch(
        f"{PREFIX}/{rule_id}",
        json={"expected_updated_at": original, "name": "renamed"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "renamed"
    current = renamed.json()["updated_at"]
    assert current != original
    conflict = client.patch(
        f"{PREFIX}/{rule_id}",
        json={"expected_updated_at": original, "name": "other"},
    )
    assert conflict.status_code == 409, conflict.text
    detail = conflict.json()["detail"]
    assert detail["message"] == (
        f"rule version conflict; current version is {detail['current_version']}"
    )
    assert datetime.fromisoformat(str(detail["current_version"])) == datetime.fromisoformat(
        current
    )
    assert detail["updated_by"] == "detection_engineer"
    assert client.get(f"{PREFIX}/{rule_id}").json()["name"] == "renamed"


async def test_delete_resolves_the_open_alert_then_removes_the_rule(
    db_session, db_schema: str
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=705, name="del-src")
    await db_session.commit()
    actor = Principal(subject="deleter", permissions=frozenset({"admin:*"}))
    client = _client(db_schema, actor)
    created = client.post(PREFIX, json=_rule_body(source.id, "doomed", enabled=False))
    assert created.status_code == 201, created.text
    rule_id = uuid.UUID(created.json()["id"])
    fingerprint = custom_rule_breach_fingerprint(rule_id)
    when = datetime.now(UTC)
    alert = Alert(
        dedup_key=fingerprint,
        fingerprint=fingerprint,
        title="doomed crossed its threshold",
        severity=Severity.HIGH,
        status=AlertStatus.OPEN,
        source_type="custom_rule",
        source_id=rule_id,
        context={"rule": "doomed"},
        opened_at=when,
        first_seen_at=when,
        occurrence_count=1,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )
    db_session.add(alert)
    await db_session.commit()
    alert_id = alert.id
    deleted = client.delete(f"{PREFIX}/{rule_id}")
    assert deleted.status_code == 204, deleted.text
    db_session.expire_all()
    stored = await db_session.get(Alert, alert_id)
    assert stored is not None
    assert stored.status == AlertStatus.RESOLVED
    assert stored.resolved_by == "deleter"
    assert stored.resolution_reason == "the rule was deleted"
    assert client.get(f"{PREFIX}/{rule_id}").status_code == 404


async def test_writes_are_audited_and_previews_are_not(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=706, name="audit-src")
    await db_session.commit()
    source_id = source.id
    client = _client(db_schema, _principal("detection_engineer"))
    created = client.post(
        PREFIX,
        json=_rule_body(source.id, "audited", threshold_value=100, enabled=True),
    )
    assert created.status_code == 201, created.text
    rule_id = created.json()["id"]
    created_rows = await _audits(db_session, "custom_rule.create")
    assert len(created_rows) == 1
    assert created_rows[0].actor == "detection_engineer"
    assert created_rows[0].object_type == "custom_rule"
    assert created_rows[0].object_id == rule_id
    assert created_rows[0].detail["before"] is None
    after = created_rows[0].detail["after"]
    assert after["name"] == "audited"
    assert after["metric"] == "event_count"
    assert after["operator"] == "GT"
    assert after["threshold_value"] == 100
    assert after["sustain_buckets"] == 3
    assert after["recovery_buckets"] == 2
    assert after["severity"] == "HIGH"
    assert after["enabled"] is True
    assert after["subject_kind"] == "LOG_SOURCE"

    token = created.json()["updated_at"]
    updated = client.patch(
        f"{PREFIX}/{rule_id}",
        json={"expected_updated_at": token, "threshold_value": 50, "description": "tighter"},
    )
    assert updated.status_code == 200, updated.text
    update_rows = await _audits(db_session, "custom_rule.update")
    assert len(update_rows) == 1
    assert update_rows[0].actor == "detection_engineer"
    assert update_rows[0].detail["before"]["threshold_value"] == 100
    assert update_rows[0].detail["after"]["threshold_value"] == 50
    assert update_rows[0].detail["after"]["description"] == "tighter"

    token = updated.json()["updated_at"]
    disabled = client.patch(
        f"{PREFIX}/{rule_id}",
        json={"expected_updated_at": token, "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    disable_rows = await _audits(db_session, "custom_rule.disable")
    assert len(disable_rows) == 1
    assert disable_rows[0].detail["before"]["enabled"] is True
    assert disable_rows[0].detail["after"]["enabled"] is False

    token = disabled.json()["updated_at"]
    enabled = client.patch(
        f"{PREFIX}/{rule_id}",
        json={"expected_updated_at": token, "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    enable_rows = await _audits(db_session, "custom_rule.enable")
    assert len(enable_rows) == 1
    assert enable_rows[0].detail["before"]["enabled"] is False
    assert enable_rows[0].detail["after"]["enabled"] is True
    assert await _audits(db_session, "custom_rule.update") == update_rows

    before_preview = len(await _audits(db_session, "custom_rule.create"))
    assert client.get(f"{PREFIX}/{rule_id}/preview", params={"hours": 1}).status_code == 200
    preview = client.post(
        f"{PREFIX}/preview",
        params={"hours": 1},
        json=_draft_body(source_id, enabled=False),
    )
    assert preview.status_code == 200, preview.text
    assert len(await _audits(db_session, "custom_rule.create")) == before_preview
    assert await _audits(db_session, "custom_rule.preview") == []

    token = enabled.json()["updated_at"]
    assert client.delete(f"{PREFIX}/{rule_id}").status_code == 204
    delete_rows = await _audits(db_session, "custom_rule.delete")
    assert len(delete_rows) == 1
    assert delete_rows[0].actor == "detection_engineer"
    assert delete_rows[0].detail["before"]["name"] == "audited"
    assert delete_rows[0].detail["after"] is None


async def test_create_reports_shadow_mode_and_a_null_evaluation(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=707, name="shadow-src")
    await db_session.commit()
    client = _client(db_schema, _principal("admin"))
    created = client.post(PREFIX, json=_rule_body(source.id, "fresh", enabled=False))
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["alerting_mode"] == "shadow"
    assert body["last_status"] is None
    assert body["last_evaluated_at"] is None
    assert body["active_breach"] is None
    assert body["threshold_unit"] == "events/bucket"
    assert body["consecutive_breaching"] == 0
    assert body["consecutive_under"] == 0
    listed = client.get(PREFIX)
    assert listed.status_code == 200
    match = [row for row in listed.json() if row["id"] == body["id"]]
    assert len(match) == 1
    assert match[0]["alerting_mode"] == "shadow"
    paging = _client(
        db_schema, _principal("admin"), custom_rules_alerting_enabled=True
    )
    again = paging.get(f"{PREFIX}/{body['id']}")
    assert again.status_code == 200
    assert again.json()["alerting_mode"] == "alerting"


async def test_a_dangling_active_breach_is_null(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=708, name="dangle-src")
    rule = CustomRule(
        name="dangling",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        metric=CustomRuleMetric.EVENT_COUNT.value,
        operator="GT",
        threshold_value=10,
        sustain_buckets=1,
        recovery_buckets=1,
        severity=Severity.LOW,
        enabled=False,
        consecutive_breaching=0,
        consecutive_under=0,
        active_breach_id=uuid.uuid4(),
        created_at=utc(),
        updated_at=utc(),
    )
    db_session.add(rule)
    await db_session.commit()
    client = _client(db_schema, _principal("viewer"))
    response = client.get(f"{PREFIX}/{rule.id}")
    assert response.status_code == 200, response.text
    assert response.json()["active_breach"] is None
    assert response.json()["last_status"] is None


def test_hours_outside_1_to_168_is_422(db_schema: str) -> None:
    client = _client(db_schema, _principal("viewer"))
    missing = f"{PREFIX}/{NIL}/preview"
    assert client.get(missing, params={"hours": 0}).status_code == 422
    assert client.get(missing, params={"hours": 169}).status_code == 422
    # The rule is absent, which is 404 once the query itself is legal.
    assert client.get(missing, params={"hours": 1}).status_code == 404
    assert client.get(missing, params={"hours": 168}).status_code == 404


async def _plant_exact(session, source, when: datetime, events: int, **extra: object) -> None:
    await add_metric(
        session,
        source,
        when,
        event_count=events,
        average_eps=1.0,
        bucket_seconds=300,
        **extra,
    )


async def test_preview_agrees_with_the_engine_on_an_exact_count(db_session) -> None:
    """Two highs open on the second. Two lows resolve on the second. One tick."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=711, name="exact-src")
    for minute, events in ((0, 400), (5, 400), (10, 1), (15, 1)):
        await _plant_exact(
            db_session, source, utc(h=10, mi=minute), events
        )
    rule = CustomRule(
        name="exact-rule",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        metric=CustomRuleMetric.EVENT_COUNT.value,
        operator="GT",
        threshold_value=100,
        sustain_buckets=2,
        recovery_buckets=2,
        severity=Severity.HIGH,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    db_session.add(rule)
    db_session.add(
        CustomRuleWatermark(
            subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
            evaluated_through=utc(h=9, mi=55),
            updated_at=utc(h=9, mi=55),
        )
    )
    await db_session.flush()
    now = utc(h=10, mi=20)
    sink = _RecordingSink()
    await CustomRuleEngine(db_session, _settings(), sink=sink, clock=lambda: now).run()
    result = await replay(
        db_session,
        metric=CustomRuleMetric.EVENT_COUNT,
        operator="GT",
        threshold_value=100,
        sustain_buckets=2,
        recovery_buckets=2,
        severity="HIGH",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        source_class=None,
        waf_endpoint_id=None,
        ips_entity_id=None,
        hours=2,
        interval_seconds=300,
        now=now,
        name=rule.name,
    )
    opens = [event for event in sink.events if event[0] == "open"]
    resolves = [event for event in sink.events if event[0] == "resolve"]
    assert len(opens) == 1 and len(resolves) == 1
    assert result.fired_count == 1
    assert result.breach_intervals[0].start == opens[0][1] == utc(h=10, mi=5)
    assert result.breach_intervals[0].end == resolves[0][2] == utc(h=10, mi=15)
    stored = (await db_session.scalars(select(CustomRuleBreach))).one()
    assert stored.state == "RESOLVED"
    assert stored.first_bucket_start == result.breach_intervals[0].start
    assert stored.last_bucket_start == result.breach_intervals[0].end
    by_minute = {bucket.bucket_start: bucket for bucket in result.buckets}
    assert by_minute[utc(h=10, mi=0)].measurement == "EXACT"
    assert by_minute[utc(h=10, mi=0)].value == 400
    assert by_minute[utc(h=10, mi=0)].completeness == "COMPLETE"


async def test_preview_agrees_with_the_engine_on_a_lower_bound(db_session) -> None:
    """One COMPLETE source of two is a lower bound. A disabled sibling does not count.

    A later lower bound below the line does not resolve.
    """
    instance = await make_instance(db_session)
    hot = await make_log_source(db_session, instance, qradar_id=721, name="hot")
    cold = await make_log_source(db_session, instance, qradar_id=722, name="cold")
    quiet = await make_log_source(
        db_session, instance, qradar_id=723, name="quiet", enabled=False
    )
    when = utc()
    for source in (hot, cold, quiet):
        db_session.add(
            LogSourceClassification(
                log_source_id=source.id,
                source_class=SourceClass.LINUX.value,
                basis="NAME_REGEX",
                conflicts=[],
                classified_at=when,
                review_status=ClassificationReviewStatus.REVIEWED.value,
            )
        )
    await add_metric(
        db_session, hot, utc(h=10, mi=5), event_count=300, bucket_seconds=300, average_eps=1.0
    )
    await add_metric(
        db_session, hot, utc(h=10, mi=10), event_count=0, bucket_seconds=300, average_eps=0.0
    )
    await add_metric(
        db_session,
        quiet,
        utc(h=10, mi=5),
        event_count=300_000,
        bucket_seconds=300,
        average_eps=1000.0,
    )
    rule = CustomRule(
        name="linux-eps",
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        source_class=SourceClass.LINUX.value,
        metric=CustomRuleMetric.EPS.value,
        operator="GT",
        threshold_value=0.5,
        sustain_buckets=1,
        recovery_buckets=1,
        severity=Severity.MEDIUM,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    db_session.add(rule)
    db_session.add(
        CustomRuleWatermark(
            subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
            evaluated_through=utc(h=10, mi=0),
            updated_at=utc(h=10, mi=0),
        )
    )
    await db_session.flush()
    now = utc(h=10, mi=15)
    sink = _RecordingSink()
    await CustomRuleEngine(db_session, _settings(), sink=sink, clock=lambda: now).run()
    result = await replay(
        db_session,
        metric=CustomRuleMetric.EPS,
        operator="GT",
        threshold_value=0.5,
        sustain_buckets=1,
        recovery_buckets=1,
        severity="MEDIUM",
        subject_kind=CustomRuleSubjectKind.FLEET_CLASS,
        log_source_id=None,
        source_class=SourceClass.LINUX.value,
        waf_endpoint_id=None,
        ips_entity_id=None,
        hours=2,
        interval_seconds=300,
        now=now,
        name=rule.name,
    )
    assert [event[0] for event in sink.events] == ["open"]
    assert result.fired_count == 1
    assert result.breach_intervals[0].start == utc(h=10, mi=5)
    assert result.breach_intervals[0].end is None
    by_minute = {bucket.bucket_start: bucket for bucket in result.buckets}
    assert by_minute[utc(h=10, mi=5)].value == 1.0
    assert by_minute[utc(h=10, mi=5)].measurement == "LOWER_BOUND"
    assert by_minute[utc(h=10, mi=5)].completeness == "PARTIAL"
    assert by_minute[utc(h=10, mi=10)].measurement == "LOWER_BOUND"
    assert by_minute[utc(h=10, mi=10)].value == 0.0
    stored = (await db_session.scalars(select(CustomRuleBreach))).one()
    assert stored.worst_value == 1.0
    assert stored.worst_measurement == "LOWER_BOUND"
    assert stored.first_bucket_start == result.breach_intervals[0].start
    assert rule.last_status == "OPEN"


async def test_preview_agrees_with_the_engine_when_a_bucket_is_not_measured(
    db_session,
) -> None:
    """A PARTIAL bucket and a missing bucket pause. Neither one resets a counter."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=731, name="gap-src")
    await _plant_exact(db_session, source, utc(h=10, mi=0), 400)
    await _plant_exact(
        db_session,
        source,
        utc(h=10, mi=5),
        400,
        completeness=BucketCompleteness.PARTIAL,
    )
    await _plant_exact(db_session, source, utc(h=10, mi=10), 400)
    await _plant_exact(db_session, source, utc(h=10, mi=15), 1)
    await _plant_exact(db_session, source, utc(h=10, mi=25), 1)
    rule = CustomRule(
        name="gap-rule",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        metric=CustomRuleMetric.EVENT_COUNT.value,
        operator="GT",
        threshold_value=100,
        sustain_buckets=2,
        recovery_buckets=2,
        severity=Severity.HIGH,
        enabled=True,
        consecutive_breaching=0,
        consecutive_under=0,
    )
    db_session.add(rule)
    db_session.add(
        CustomRuleWatermark(
            subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
            evaluated_through=utc(h=9, mi=55),
            updated_at=utc(h=9, mi=55),
        )
    )
    await db_session.flush()
    now = utc(h=10, mi=30)
    sink = _RecordingSink()
    await CustomRuleEngine(db_session, _settings(), sink=sink, clock=lambda: now).run()
    breaches_before = await db_session.scalar(select(func.count()).select_from(CustomRuleBreach))
    result = await replay(
        db_session,
        metric=CustomRuleMetric.EVENT_COUNT,
        operator="GT",
        threshold_value=100,
        sustain_buckets=2,
        recovery_buckets=2,
        severity="HIGH",
        subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
        log_source_id=source.id,
        source_class=None,
        waf_endpoint_id=None,
        ips_entity_id=None,
        hours=2,
        interval_seconds=300,
        now=now,
        name=rule.name,
    )
    breaches_after = await db_session.scalar(select(func.count()).select_from(CustomRuleBreach))
    assert breaches_before == breaches_after
    opens = [event for event in sink.events if event[0] == "open"]
    resolves = [event for event in sink.events if event[0] == "resolve"]
    assert result.fired_count == len(opens) == 1
    assert result.breach_intervals[0].start == opens[0][1] == utc(h=10, mi=10)
    assert result.breach_intervals[0].end == resolves[0][2] == utc(h=10, mi=25)
    by_minute = {bucket.bucket_start: bucket for bucket in result.buckets}
    assert by_minute[utc(h=10, mi=5)].measurement == "NOT_MEASURED"
    assert by_minute[utc(h=10, mi=5)].completeness == "PARTIAL"
    assert by_minute[utc(h=10, mi=5)].value is None
    assert by_minute[utc(h=10, mi=20)].measurement == "NOT_MEASURED"
    assert by_minute[utc(h=10, mi=20)].completeness is None
    stored = (await db_session.scalars(select(CustomRuleBreach))).one()
    assert stored.first_bucket_start == utc(h=10, mi=10)
    assert stored.last_bucket_start == utc(h=10, mi=25)


async def test_preview_statement_count_does_not_grow_with_the_window(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=741, name="count-src")
    await add_metric(db_session, source, utc(h=10, mi=5), event_count=10, bucket_seconds=300)
    await db_session.flush()
    source_id = source.id
    now = utc(h=10, mi=20)
    await db_session.execute(select(1))

    async def once(hours: int) -> list[str]:
        db_session.expire_all()
        with counting(db_session) as statements:
            await replay(
                db_session,
                metric=CustomRuleMetric.EVENT_COUNT,
                operator="GT",
                threshold_value=1000,
                sustain_buckets=3,
                recovery_buckets=2,
                severity="LOW",
                subject_kind=CustomRuleSubjectKind.LOG_SOURCE,
                log_source_id=source_id,
                source_class=None,
                waf_endpoint_id=None,
                ips_entity_id=None,
                hours=hours,
                interval_seconds=300,
                now=now,
            )
        return list(statements)

    short = await once(1)
    long = await once(168)
    assert len(short) == len(long)
    assert sum("log_source_metric" in statement for statement in short) == 1
    assert sum("log_source_metric" in statement for statement in long) == 1
    for statement in (*short, *long):
        verb = statement.lstrip().split(None, 1)[0].upper()
        assert verb not in {"INSERT", "UPDATE", "DELETE"}


async def test_http_preview_sees_a_stored_exact_bucket(db_session, db_schema: str) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=751, name="http-src")
    closed = latest_closed_bucket(datetime.now(UTC), 300)
    start = closed - timedelta(seconds=600)
    await add_metric(
        db_session, source, start, event_count=400, bucket_seconds=300, average_eps=1.0
    )
    await db_session.commit()
    writer = _client(db_schema, _principal("admin"))
    created = writer.post(PREFIX, json=_rule_body(source.id, "http-rule", enabled=False))
    assert created.status_code == 201, created.text
    audits_before = len(await _audits(db_session, "custom_rule.create"))
    client = _client(db_schema, _principal("analyst"))
    response = client.get(f"{PREFIX}/{created.json()['id']}/preview", params={"hours": 6})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unit"] == "events/bucket"
    assert body["operator"] == "GT"
    assert body["threshold_value"] == 100
    found = [
        bucket
        for bucket in body["buckets"]
        if datetime.fromisoformat(bucket["bucket_start"]) == start
    ]
    assert len(found) == 1
    assert found[0]["value"] == 400
    assert found[0]["measurement"] == "EXACT"
    assert found[0]["completeness"] == "COMPLETE"
    assert len(await _audits(db_session, "custom_rule.create")) == audits_before
    rules = await db_session.scalar(select(func.count()).select_from(CustomRule))
    assert rules == 1


def test_catalog_is_on_the_openapi_guard_walk(db_schema: str) -> None:
    """401 and 403 are the OpenAPI sweep. Catalog has to be on that walk."""
    client = _anonymous(db_schema)
    assert ("GET", f"{PREFIX}/catalog") in _operations(client)


def test_viewer_catalog_returns_the_enforced_values(db_schema: str) -> None:
    client = _client(db_schema, _principal("viewer"))
    response = client.get(f"{PREFIX}/catalog")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body == _catalog_body(0)
    settings = get_settings()
    assert body["evaluation_enabled"] is settings.custom_rules_enabled
    assert body["alerting_mode"] == alerting_mode(settings.custom_rules_alerting_enabled)
    assert body["max_enabled"] == settings.custom_rules_max_enabled
    assert body["enabled_count"] == 0
    assert body["preview_hours_min"] == 1
    assert body["preview_hours_max"] == 168
    for metric in body["metrics"]:
        assert "reads" not in metric
        assert "bulk_loader" not in metric

    switched = _client(
        db_schema,
        _principal("viewer"),
        custom_rules_enabled=not settings.custom_rules_enabled,
        custom_rules_alerting_enabled=not settings.custom_rules_alerting_enabled,
        custom_rules_max_enabled=40,
    )
    again = switched.get(f"{PREFIX}/catalog")
    assert again.status_code == 200, again.text
    switched_body = again.json()
    assert switched_body == _catalog_body(
        0,
        custom_rules_enabled=not settings.custom_rules_enabled,
        custom_rules_alerting_enabled=not settings.custom_rules_alerting_enabled,
        custom_rules_max_enabled=40,
    )
    assert switched_body["evaluation_enabled"] is not settings.custom_rules_enabled
    assert switched_body["max_enabled"] == 40


async def test_catalog_enabled_count_counts_enabled_rules_only(
    db_session, db_schema: str
) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=761, name="catalog-src")
    await db_session.commit()
    writer = _client(db_schema, _principal("admin"))
    reader = _client(db_schema, _principal("viewer"))
    assert reader.get(f"{PREFIX}/catalog").json()["enabled_count"] == 0
    disabled = writer.post(PREFIX, json=_rule_body(source.id, "catalog-off", enabled=False))
    assert disabled.status_code == 201, disabled.text
    assert reader.get(f"{PREFIX}/catalog").json()["enabled_count"] == 0
    enabled = writer.post(PREFIX, json=_rule_body(source.id, "catalog-on", enabled=True))
    assert enabled.status_code == 201, enabled.text
    counted = reader.get(f"{PREFIX}/catalog")
    assert counted.status_code == 200, counted.text
    assert counted.json()["enabled_count"] == 1
    assert counted.json() == _catalog_body(1)


def test_catalog_is_not_shadowed_by_rule_id(db_schema: str) -> None:
    client = _client(db_schema, _principal("viewer"))
    catalog = client.get(f"{PREFIX}/catalog")
    assert catalog.status_code == 200, catalog.text
    assert isinstance(catalog.json()["metrics"], list)
    assert client.get(f"{PREFIX}/not-a-rule").status_code == 422
    # A UUID still reaches the rule route. Absence is 404, not the catalog.
    assert client.get(f"{PREFIX}/{NIL}").status_code == 404
