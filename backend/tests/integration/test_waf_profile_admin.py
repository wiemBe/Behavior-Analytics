"""WAF profile administration API and asynchronous validation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.enums import (
    ClassificationReviewStatus,
    SourceClass,
    WafProfileValidationStatus,
)
from app.models.fleet import LogSourceClassification
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.providers.dto import LogSourceDTO
from app.providers.mock import MockQRadarProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.waf_profiles import WafProfileService
from app.waf.profile import WafFieldMapping
from app.workers import tasks

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def settings() -> Settings:
    return get_settings().model_copy(
        update={
            "waf_collection_enabled": True,
            "waf_validation_pending_timeout_seconds": 900,
        }
    )


def test_validation_pending_timeout_default_and_floor() -> None:
    assert Settings().waf_validation_pending_timeout_seconds == 900
    assert Settings().waf_validation_lock_retry_delay_seconds == 30
    assert Settings().waf_validation_lock_max_retries == 20
    with pytest.raises(ValueError, match="greater than or equal to 60"):
        Settings(waf_validation_pending_timeout_seconds=59)
    with pytest.raises(ValueError, match="greater than or equal to 5"):
        Settings(waf_validation_lock_retry_delay_seconds=4)
    with pytest.raises(ValueError, match="less than or equal to 300"):
        Settings(waf_validation_lock_retry_delay_seconds=301)
    with pytest.raises(ValueError, match="greater than or equal to 0"):
        Settings(waf_validation_lock_max_retries=-1)
    with pytest.raises(ValueError, match="less than or equal to 100"):
        Settings(waf_validation_lock_max_retries=101)


def assert_conflict_detail(
    response,  # type: ignore[no-untyped-def]
    *,
    current_version: int | None,
    updated_by: str | None,
    pending: bool,
) -> None:
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert set(detail) == {"message", "current_version", "updated_by", "pending_since"}
    assert isinstance(detail["message"], str)
    assert detail["current_version"] == current_version
    assert detail["updated_by"] == updated_by
    assert (detail["pending_since"] is not None) is pending


async def seed_source(
    session, *, name: str = "WAF primary", qradar_id: int = 1001
) -> tuple[QRadarInstance, LogSource]:  # type: ignore[no-untyped-def]
    instance = QRadarInstance(
        name=f"instance-{uuid.uuid4()}",
        console_host="qradar.example.test",
        provider_kind="mock",
    )
    source = LogSource(
        instance=instance,
        qradar_id=qradar_id,
        name=name,
        type_id=212,
        type_name="Custom WAF",
        monitoring_enabled=True,
    )
    session.add_all([instance, source])
    await session.flush()
    return instance, source


def profile_body(
    *, expected: int | None = None, url: str = "URL", label: str | None = "Edge WAF"
) -> dict:
    return {
        "expected_profile_version": expected,
        "product_label": label,
        "mapping": {
            "URL": url,
            "METHOD": "HTTP_Method",
            "VIRTUAL_HOST": None,
            "ACTION": "WAF_Action",
            "RESPONSE_CODE": "WAF_ResponseCode",
            "RULE_ID": None,
            "USER_AGENT": None,
        },
        "action_value_map": {"BLOCKED": ["DENY"], "ALLOWED": ["ALLOW"]},
    }


def client(db_url: str, principal: Principal, cfg: Settings | None = None) -> TestClient:
    # TestClient may create a fresh event loop per request. NullPool prevents an
    # asyncpg connection created on one loop from being reused on the next.
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_settings] = lambda: cfg or settings()
    return TestClient(app)


def admin_client(db_url: str) -> TestClient:
    return client(
        db_url,
        Principal(subject="profile-admin", permissions=frozenset({"admin:waf"})),
    )


def test_profile_routes_require_admin_waf() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings().model_copy(
        update={"auth_provider": "oidc"}
    )
    with TestClient(app) as unauthenticated:
        response = unauthenticated.get(f"/api/v1/waf/log-sources/{uuid.uuid4()}/profile")
    assert response.status_code == 401

    viewer = client(
        "postgresql+asyncpg://unused:unused@localhost/unused",
        Principal(subject="viewer", permissions=frozenset({"read:*"})),
    )
    assert viewer.get(f"/api/v1/waf/log-sources/{uuid.uuid4()}/profile").status_code == 403


async def test_mapping_schema_defaults_and_unknown_resources(db_session, db_schema) -> None:
    cfg = settings().model_copy(update={"waf_virtual_host_property": ""})
    api = client(
        db_schema,
        Principal(subject="admin", permissions=frozenset({"admin:waf"})),
        cfg,
    )
    schema = api.get("/api/v1/waf/mapping-schema")
    assert schema.status_code == 200
    body = schema.json()
    assert body["domain"] == "WAF"
    assert body["property_name_pattern"] == "^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$"
    assert body["min_coverage_ratio"] == cfg.waf_validation_min_ratio
    fields = {field["key"]: field for field in body["fields"]}
    assert fields["URL"]["label"] == "URL path"
    assert fields["URL"]["default"] == "URL"
    assert fields["VIRTUAL_HOST"]["default"] is None
    assert body["value_maps"] == [{"field": "ACTION", "outcomes": ["BLOCKED", "ALLOWED"]}]

    unknown = uuid.uuid4()
    assert api.get(f"/api/v1/waf/log-sources/{unknown}/profile").status_code == 404
    assert (
        api.put(
            f"/api/v1/waf/log-sources/{unknown}/profile",
            json=profile_body(),
        ).status_code
        == 404
    )
    _, source = await seed_source(db_session)
    await db_session.commit()
    assert api.get(f"/api/v1/waf/log-sources/{source.id}/profile").status_code == 404
    assert (
        api.post(f"/api/v1/waf/log-sources/{source.id}/profile/validate").status_code
        == 404
    )


async def test_put_validation_guards_create_update_and_audit(
    db_session, db_schema, monkeypatch
) -> None:
    _, source = await seed_source(db_session)
    await db_session.commit()
    enqueued: list[str] = []
    monkeypatch.setattr(tasks.validate_waf_profile_task, "delay", enqueued.append)
    api = admin_client(db_schema)
    path = f"/api/v1/waf/log-sources/{source.id}/profile"

    missing = api.put(path, json=profile_body(expected=1))
    assert_conflict_detail(
        missing, current_version=None, updated_by=None, pending=False
    )

    invalid_bodies = []
    bad_name = profile_body()
    bad_name["mapping"]["URL"] = 'URL"; DROP'
    invalid_bodies.append((bad_name, "invalid QRadar property name"))
    overlap = profile_body()
    overlap["action_value_map"]["ALLOWED"] = ["deny"]
    invalid_bodies.append((overlap, "both BLOCKED and ALLOWED"))
    no_action = profile_body()
    no_action["mapping"]["ACTION"] = None
    invalid_bodies.append((no_action, "needs an action property"))
    for body, message in invalid_bodies:
        response = api.put(path, json=body)
        assert response.status_code == 422
        assert set(response.json()["detail"]) == {"message"}
        assert message in response.json()["detail"]["message"]

    body_validation = api.put(path, json={})
    assert body_validation.status_code == 422
    assert isinstance(body_validation.json()["detail"], list)

    created = api.put(path, json=profile_body())
    assert created.status_code == 201, created.text
    assert created.json()["validation_pending"] is True
    assert created.json()["validation_requested_by"] == "profile-admin"
    assert enqueued == [created.json()["profile_id"]]

    duplicate = api.put(path, json=profile_body())
    assert_conflict_detail(
        duplicate, current_version=1, updated_by="profile-admin", pending=True
    )
    assert "current version is 1" in duplicate.json()["detail"]["message"]
    stale = api.put(path, json=profile_body(expected=2, url="Request URL"))
    assert_conflict_detail(
        stale, current_version=1, updated_by="profile-admin", pending=True
    )
    assert "current version is 1" in stale.json()["detail"]["message"]

    await db_session.rollback()
    db_session.expire_all()
    audits_before = int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)
    unchanged = api.put(path, json=profile_body(expected=1))
    assert unchanged.status_code == 200
    assert unchanged.json()["profile_version"] == 1
    await db_session.rollback()
    db_session.expire_all()
    audits_after = int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)
    assert audits_after == audits_before
    assert len(enqueued) == 1

    relabelled = api.put(path, json=profile_body(expected=1, label="Renamed product"))
    assert relabelled.status_code == 200
    assert relabelled.json()["profile_version"] == 1
    assert len(enqueued) == 1
    await db_session.rollback()
    db_session.expire_all()
    actor_actions = list(
        (
            await db_session.scalars(
                select(AuditLog.action).where(AuditLog.actor == "profile-admin")
            )
        ).all()
    )
    assert actor_actions.count("waf.profile.created") == 1
    assert actor_actions.count("waf.profile.validation_requested") == 1
    assert actor_actions.count("waf.profile.relabelled") == 1


async def test_mapping_change_resets_validation_and_pauses_enabled_source(
    db_session, db_schema, monkeypatch
) -> None:
    _, source = await seed_source(db_session)
    service = WafProfileService(db_session, settings=settings(), clock=lambda: NOW)
    profile, _ = await service.upsert(
        log_source=source,
        mapping=WafFieldMapping(
            url="URL",
            method="HTTP_Method",
            action="WAF_Action",
            response_code="WAF_ResponseCode",
            blocked_actions=("DENY",),
            allowed_actions=("ALLOW",),
        ),
        actor="seed",
        product_label="Edge WAF",
    )
    profile.enabled = True
    profile.validation_status = WafProfileValidationStatus.VALID
    profile.validated_profile_version = 1
    profile.validated_at = NOW
    profile.validation_report = {"profile_version": 1}
    await db_session.commit()

    enqueued: list[str] = []
    monkeypatch.setattr(tasks.validate_waf_profile_task, "delay", enqueued.append)
    changed = admin_client(db_schema).put(
        f"/api/v1/waf/log-sources/{source.id}/profile",
        json=profile_body(expected=1, url="Request URL"),
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["profile_version"] == 2
    assert body["validation_status"] == "UNVALIDATED"
    assert body["validation"] is None
    assert body["validation_pending"] is True
    assert body["collection_eligible"] is False
    assert "profile version 2 has not been validated" in body["ineligible_reason"]
    assert len(enqueued) == 1

    statuses = admin_client(db_schema).get("/api/v1/waf/sources")
    assert statuses.status_code == 200
    status_body = statuses.json()[0]
    assert status_body["validation_pending"] is True
    assert status_body["validation_requested_at"] is not None
    assert status_body["validation_requested_by"] == "profile-admin"

    await db_session.rollback()
    db_session.expire_all()
    actions = list(
        (
            await db_session.scalars(
                select(AuditLog.action).where(AuditLog.actor == "profile-admin")
            )
        ).all()
    )
    assert actions == ["waf.profile.mapping_changed", "waf.profile.validation_requested"]


async def test_validate_pending_timeout_and_enable_disable(
    db_session, db_schema, monkeypatch
) -> None:
    _, source = await seed_source(db_session)
    service = WafProfileService(db_session, settings=settings(), clock=lambda: NOW)
    profile, _ = await service.upsert(
        log_source=source,
        mapping=WafFieldMapping(url="URL", method="HTTP_Method"),
        actor="seed",
    )
    # Pending relative to the API's real clock: fixed timestamps expire after the 900 s timeout.
    profile.validation_requested_at = datetime.now(UTC)
    profile.validation_requested_by = "seed"
    profile_id = profile.id
    await db_session.commit()

    enqueued: list[str] = []
    monkeypatch.setattr(tasks.validate_waf_profile_task, "delay", enqueued.append)
    api = admin_client(db_schema)
    base = f"/api/v1/waf/log-sources/{source.id}/profile"
    pending = api.post(f"{base}/validate")
    assert_conflict_detail(pending, current_version=1, updated_by="seed", pending=True)
    assert "already running" in pending.json()["detail"]["message"]
    refused = api.post(f"{base}/enable")
    assert_conflict_detail(refused, current_version=1, updated_by="seed", pending=True)
    assert "validated" in refused.json()["detail"]["message"]
    assert api.post(f"{base}/disable").status_code == 200

    await db_session.rollback()
    db_session.expire_all()
    current = await db_session.get(WafSourceProfile, profile_id)
    assert current is not None
    current.validation_requested_at = datetime.now(UTC) - timedelta(seconds=901)
    await db_session.commit()
    accepted = api.post(f"{base}/validate")
    assert accepted.status_code == 202, accepted.text
    assert accepted.json()["validation_pending"] is True
    assert enqueued == [str(profile_id)]


async def test_served_report_strips_queries(db_session, db_schema) -> None:
    _, source = await seed_source(db_session)
    profile = WafSourceProfile(
        instance_id=source.instance_id,
        log_source_id=source.id,
        url_property="URL",
        method_property="HTTP_Method",
        validation_status=WafProfileValidationStatus.VALID,
        validated_profile_version=1,
        validated_at=NOW,
        validation_report={
            "status": "VALID",
            "reasons": [],
            "total_events": 10,
            "window_start": (NOW - timedelta(minutes=15)).isoformat(),
            "window_end": NOW.isoformat(),
            "profile_version": 1,
            "total_query": "SELECT secret provenance",
            "fields": [
                {
                    "field": "URL",
                    "property_name": "URL",
                    "status": "AVAILABLE",
                    "used_by_collection": True,
                    "values_truncated": False,
                    "queries": ["SELECT URL"],
                }
            ],
        },
    )
    db_session.add(profile)
    await db_session.commit()
    response = admin_client(db_schema).get(
        f"/api/v1/waf/log-sources/{source.id}/profile"
    )
    assert response.status_code == 200, response.text
    validation = response.json()["validation"]
    assert "total_query" not in validation
    assert "queries" not in validation["fields"][0]
    assert "SELECT" not in response.text


async def test_candidates_literal_filters_order_and_limit(db_session, db_schema) -> None:
    instance, profiled = await seed_source(db_session, name="Already configured")
    names = ["Zulu", "Alpha%WAF", "AlphaXWAF", "Under_score", "UnderXscore", "Beta"]
    sources = []
    for offset, name in enumerate(names, start=10):
        source = LogSource(
            instance_id=instance.id,
            qradar_id=offset,
            name=name,
            monitoring_enabled=True,
        )
        db_session.add(source)
        sources.append(source)
    await db_session.flush()
    db_session.add(
        LogSourceClassification(
            log_source_id=sources[0].id,
            source_class=SourceClass.WAF,
            basis="TYPE",
            conflicts=[],
            classified_at=NOW,
            review_status=ClassificationReviewStatus.NEW,
        )
    )
    db_session.add(
        WafSourceProfile(
            instance_id=instance.id,
            log_source_id=profiled.id,
            url_property="URL",
            method_property="HTTP_Method",
        )
    )
    await db_session.commit()
    api = admin_client(db_schema)
    base = f"/api/v1/waf/candidates?instance_id={instance.id}"
    ordered = api.get(base)
    assert ordered.status_code == 200
    assert ordered.json()[0]["name"] == "Zulu"
    assert {item["instance_id"] for item in ordered.json()} == {str(instance.id)}
    assert "Already configured" not in [item["name"] for item in ordered.json()]
    assert [item["name"] for item in api.get(f"{base}&q=%25").json()] == ["Alpha%WAF"]
    assert [item["name"] for item in api.get(f"{base}&q=_").json()] == ["Under_score"]
    assert len(api.get(f"{base}&limit=2").json()) == 2
    assert api.get(f"{base}&limit=201").status_code == 422


async def test_candidates_without_instance_id_resolves_the_default(
    db_session, db_schema
) -> None:
    instance, profiled = await seed_source(db_session, name="Already configured")
    names = ["Zulu", "AlphaWAF", "Beta"]
    sources = []
    for offset, name in enumerate(names, start=30):
        source = LogSource(
            instance_id=instance.id,
            qradar_id=offset,
            name=name,
            monitoring_enabled=True,
        )
        db_session.add(source)
        sources.append(source)
    await db_session.flush()
    db_session.add(
        LogSourceClassification(
            log_source_id=sources[0].id,
            source_class=SourceClass.WAF,
            basis="TYPE",
            conflicts=[],
            classified_at=NOW,
            review_status=ClassificationReviewStatus.NEW,
        )
    )
    db_session.add(
        WafSourceProfile(
            instance_id=instance.id,
            log_source_id=profiled.id,
            url_property="URL",
            method_property="HTTP_Method",
        )
    )
    await db_session.commit()
    api = admin_client(db_schema)
    explicit = api.get(f"/api/v1/waf/candidates?instance_id={instance.id}")
    assert explicit.status_code == 200
    implicit = api.get("/api/v1/waf/candidates")
    assert implicit.status_code == 200, implicit.text
    assert implicit.json() == explicit.json()
    # WAF-classified first, then by name: the default instance resolves to the
    # same rows in the same order as the explicit id.
    assert [item["name"] for item in implicit.json()] == ["Zulu", "AlphaWAF", "Beta"]
    assert {item["instance_id"] for item in implicit.json()} == {str(instance.id)}


async def test_candidates_without_instance_id_and_no_instance_is_404(db_schema) -> None:
    api = admin_client(db_schema)
    response = api.get("/api/v1/waf/candidates")
    assert response.status_code == 404
    assert response.json()["detail"] == "no instance registered"


async def test_history_is_newest_first_and_capped(db_session, db_schema) -> None:
    _, source = await seed_source(db_session)
    profile = WafSourceProfile(
        instance_id=source.instance_id,
        log_source_id=source.id,
        url_property="URL",
        method_property="HTTP_Method",
    )
    db_session.add(profile)
    await db_session.flush()
    for index in range(55):
        db_session.add(
            AuditLog(
                created_at=NOW + timedelta(seconds=index),
                actor=f"actor-{index}",
                action="waf.profile.relabelled",
                object_type="waf_source_profile",
                object_id=str(profile.id),
                outcome="SUCCESS",
                detail={"profile_version": index + 1},
            )
        )
    await db_session.commit()
    response = admin_client(db_schema).get(
        f"/api/v1/waf/log-sources/{source.id}/profile/history"
    )
    assert response.status_code == 200, response.text
    history = response.json()
    assert len(history) == 50
    assert history[0]["actor"] == "actor-54"
    assert history[-1]["actor"] == "actor-5"


class ValidationProvider(MockQRadarProvider):
    def __init__(self, hook=None, error: Exception | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(now=NOW)
        self.hook = hook
        self.error = error
        self.closed = False

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        return LogSourceDTO(qradar_id=qradar_id, name="WAF")

    async def probe_waf_properties(self, **kwargs):  # type: ignore[no-untyped-def, override]
        if self.hook is not None:
            await self.hook()
        if self.error is not None:
            raise self.error
        return await super().probe_waf_properties(**kwargs)

    async def aclose(self) -> None:
        self.closed = True


async def pending_profile(db_session) -> WafSourceProfile:  # type: ignore[no-untyped-def]
    _, source = await seed_source(db_session)
    service = WafProfileService(db_session, settings=settings(), clock=lambda: NOW)
    profile, _ = await service.upsert(
        log_source=source,
        mapping=WafFieldMapping(url="URL", method="HTTP_Method"),
        actor="seed",
    )
    await service.request_validation(profile, actor="requester", enqueue=lambda _: None)
    return profile


async def test_worker_success_and_exception(db_session, db_schema, monkeypatch) -> None:
    profile = await pending_profile(db_session)
    profile_id = profile.id
    maker = async_sessionmaker(
        create_async_engine(db_schema, poolclass=NullPool), expire_on_commit=False
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    provider = ValidationProvider()
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *args, **kwargs: provider)
    result = await tasks.validate_waf_profile(str(profile_id), enqueue=lambda *_: None)
    assert result["status"] == "ok"
    assert provider.closed
    await db_session.rollback()
    db_session.expire_all()
    stored = await db_session.get(WafSourceProfile, profile_id)
    assert stored is not None
    assert stored.validation_status == WafProfileValidationStatus.VALID
    assert stored.validated_profile_version == 1
    assert stored.validation_requested_at is None
    assert stored.validation_report["profile_version"] == 1

    stored.validation_status = WafProfileValidationStatus.INVALID
    stored.validation_report = {"old": True}
    stored.validation_requested_at = NOW
    stored.validation_requested_by = "requester"
    await db_session.commit()
    failed_provider = ValidationProvider(error=RuntimeError("must not be persisted"))
    monkeypatch.setattr(
        tasks, "build_provider_for_instance", lambda *args, **kwargs: failed_provider
    )
    failed = await tasks.validate_waf_profile(str(profile_id), enqueue=lambda *_: None)
    assert failed == {
        "status": "failed",
        "profile_id": str(profile_id),
        "error_class": "RuntimeError",
    }
    assert failed_provider.closed
    await db_session.rollback()
    db_session.expire_all()
    unchanged = await db_session.get(WafSourceProfile, profile_id)
    assert unchanged is not None
    assert unchanged.validation_status == WafProfileValidationStatus.INVALID
    assert unchanged.validation_report == {"old": True}
    assert unchanged.validation_requested_at is None
    failure_audit = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.object_id == str(profile_id), AuditLog.outcome == "FAILURE")
        .order_by(AuditLog.created_at.desc())
    )
    assert failure_audit is not None
    assert failure_audit.actor == "requester"
    assert failure_audit.detail["error_class"] == "RuntimeError"
    assert "must not be persisted" not in failure_audit.detail.values()


async def test_worker_discards_stale_version_and_requeues(
    db_session, db_schema, monkeypatch
) -> None:
    profile = await pending_profile(db_session)
    profile_id = profile.id
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)

    async def change_mapping() -> None:
        async with maker() as other:
            current = await other.get(WafSourceProfile, profile_id)
            assert current is not None
            current.url_property = "Request URL"
            current.profile_version = 2
            current.validation_status = WafProfileValidationStatus.UNVALIDATED
            current.validation_report = {}
            current.validation_requested_at = NOW + timedelta(seconds=1)
            current.validation_requested_by = "newer-admin"
            await other.commit()

    provider = ValidationProvider(hook=change_mapping)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *args, **kwargs: provider)
    enqueued: list[tuple[str, int, int]] = []

    def enqueue(profile_id: str, retry_count: int, countdown: int) -> None:
        enqueued.append((profile_id, retry_count, countdown))

    result = await tasks.validate_waf_profile(str(profile_id), enqueue=enqueue)
    assert result["status"] == "stale_requeued"
    assert result["validated_version"] == 1
    assert result["current_version"] == 2
    assert enqueued == [(str(profile_id), 0, 0)]
    assert provider.closed
    await db_session.rollback()
    db_session.expire_all()
    current = await db_session.get(WafSourceProfile, profile_id)
    assert current is not None
    assert current.profile_version == 2
    assert current.validation_status == WafProfileValidationStatus.UNVALIDATED
    assert current.validation_report == {}
    assert current.validation_requested_at is not None
    await engine.dispose()


async def test_worker_requeues_when_collection_holds_source_lock(
    db_session, db_schema, monkeypatch
) -> None:
    profile = await pending_profile(db_session)
    profile_id = profile.id
    maker = async_sessionmaker(
        create_async_engine(db_schema, poolclass=NullPool), expire_on_commit=False
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    lock_names: list[str] = []

    class HeldLock:
        def __init__(self, _session, _settings, _instance_id, collector="") -> None:
            lock_names.append(collector)

        async def __aenter__(self) -> bool:
            return False

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(tasks, "CollectorAdvisoryLock", HeldLock)
    enqueued: list[tuple[str, int, int]] = []

    def enqueue(profile_id: str, retry_count: int, countdown: int) -> None:
        enqueued.append((profile_id, retry_count, countdown))

    result = await tasks.validate_waf_profile(str(profile_id), enqueue=enqueue)
    assert result == {
        "status": "locked_requeued",
        "profile_id": str(profile_id),
        "lock_retry_count": 1,
    }
    assert enqueued == [(str(profile_id), 1, 30)]
    assert lock_names == ["waf_pipeline:1001"]


async def test_worker_source_busy_preserves_current_valid_profile(
    db_session, db_schema, monkeypatch, caplog
) -> None:
    profile = await pending_profile(db_session)
    profile_id = profile.id
    profile.enabled = True
    profile.validation_status = WafProfileValidationStatus.VALID
    profile.validated_profile_version = profile.profile_version
    profile.validated_at = NOW
    profile.validation_report = {"old": True}
    previous_validation = (
        profile.validation_status,
        profile.validated_profile_version,
        profile.validated_at,
        dict(profile.validation_report),
    )
    await db_session.commit()
    maker = async_sessionmaker(
        create_async_engine(db_schema, poolclass=NullPool), expire_on_commit=False
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: settings().model_copy(update={"waf_validation_lock_max_retries": 2}),
    )

    class HeldLock:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        async def __aenter__(self) -> bool:
            return False

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(tasks, "CollectorAdvisoryLock", HeldLock)
    caplog.set_level("WARNING", logger="app.tasks")
    result = await tasks.validate_waf_profile(
        str(profile_id),
        lock_retry_count=2,
        enqueue=lambda *_: pytest.fail("an exhausted retry must not enqueue"),
    )
    assert result == {
        "status": "source_busy",
        "profile_id": str(profile_id),
        "lock_retry_count": 2,
    }
    assert "exhausted source-lock retries" in caplog.text

    await db_session.rollback()
    db_session.expire_all()
    stored = await db_session.get(WafSourceProfile, profile_id)
    assert stored is not None
    assert (
        stored.validation_status,
        stored.validated_profile_version,
        stored.validated_at,
        stored.validation_report,
    ) == previous_validation
    assert stored.validation_requested_at is None
    assert stored.validation_requested_by is None
    status_response = admin_client(db_schema).get("/api/v1/waf/sources")
    assert status_response.status_code == 200
    source_status = status_response.json()[0]
    assert source_status["validation_status"] == "VALID"
    assert source_status["collection_eligible"] is True
    audit = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.object_id == str(profile_id), AuditLog.outcome == "SOURCE_BUSY")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.actor == "requester"
    assert audit.detail == {
        "profile_version": 1,
        "status": "SOURCE_BUSY",
        "retry_count": 2,
    }


async def test_worker_source_busy_preserves_mapping_change_unvalidated_state(
    db_session, db_schema, monkeypatch
) -> None:
    _, source = await seed_source(db_session)
    service = WafProfileService(db_session, settings=settings(), clock=lambda: NOW)
    profile, _ = await service.upsert(
        log_source=source,
        mapping=WafFieldMapping(url="URL", method="HTTP_Method"),
        actor="seed",
    )
    profile.enabled = True
    profile.validation_status = WafProfileValidationStatus.VALID
    profile.validated_profile_version = profile.profile_version
    profile.validated_at = NOW
    profile.validation_report = {"old": True}
    await db_session.commit()

    profile, _ = await service.upsert(
        log_source=source,
        mapping=WafFieldMapping(url="Request URL", method="HTTP_Method"),
        actor="mapping-admin",
    )
    await service.request_validation(profile, actor="requester", enqueue=lambda _: None)
    profile_id = profile.id
    previous_validation = (
        profile.validation_status,
        profile.validated_profile_version,
        profile.validated_at,
        dict(profile.validation_report),
    )
    assert previous_validation[0] == WafProfileValidationStatus.UNVALIDATED

    maker = async_sessionmaker(
        create_async_engine(db_schema, poolclass=NullPool), expire_on_commit=False
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: settings().model_copy(update={"waf_validation_lock_max_retries": 2}),
    )

    class HeldLock:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        async def __aenter__(self) -> bool:
            return False

        async def __aexit__(self, *exc: object) -> None:
            return None

    monkeypatch.setattr(tasks, "CollectorAdvisoryLock", HeldLock)
    result = await tasks.validate_waf_profile(
        str(profile_id),
        lock_retry_count=2,
        enqueue=lambda *_: pytest.fail("an exhausted retry must not enqueue"),
    )
    assert result["status"] == "source_busy"

    await db_session.rollback()
    db_session.expire_all()
    stored = await db_session.get(WafSourceProfile, profile_id)
    assert stored is not None
    assert (
        stored.validation_status,
        stored.validated_profile_version,
        stored.validated_at,
        stored.validation_report,
    ) == previous_validation
    assert stored.validation_requested_at is None
    assert stored.validation_requested_by is None
    source_status = admin_client(db_schema).get("/api/v1/waf/sources").json()[0]
    assert source_status["validation_status"] == "UNVALIDATED"
    assert source_status["collection_eligible"] is False
    assert "profile version 2 has not been validated" in source_status["ineligible_reason"]
    audit = await db_session.scalar(
        select(AuditLog)
        .where(AuditLog.object_id == str(profile_id), AuditLog.outcome == "SOURCE_BUSY")
        .order_by(AuditLog.created_at.desc())
    )
    assert audit is not None
    assert audit.detail["profile_version"] == 2


async def test_worker_validates_normally_when_source_lock_frees_on_retry(
    db_session, db_schema, monkeypatch
) -> None:
    profile = await pending_profile(db_session)
    profile_id = profile.id
    maker = async_sessionmaker(
        create_async_engine(db_schema, poolclass=NullPool), expire_on_commit=False
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)

    class AvailableLock:
        def __init__(self, *_args, **_kwargs) -> None:  # type: ignore[no-untyped-def]
            pass

        async def __aenter__(self) -> bool:
            return True

        async def __aexit__(self, *exc: object) -> None:
            return None

    provider = ValidationProvider()
    monkeypatch.setattr(tasks, "CollectorAdvisoryLock", AvailableLock)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *args, **kwargs: provider)
    result = await tasks.validate_waf_profile(
        str(profile_id), lock_retry_count=7, enqueue=lambda *_: None
    )
    assert result["status"] == "ok"
    assert result["validation_status"] == "VALID"
    assert provider.closed

    await db_session.rollback()
    db_session.expire_all()
    stored = await db_session.get(WafSourceProfile, profile_id)
    assert stored is not None
    assert stored.validation_status == WafProfileValidationStatus.VALID
    assert stored.validation_requested_at is None


async def test_worker_ignores_missing_and_non_pending_profiles(
    db_session, db_schema, monkeypatch
) -> None:
    maker = async_sessionmaker(
        create_async_engine(db_schema, poolclass=NullPool), expire_on_commit=False
    )
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    missing_id = uuid.uuid4()
    assert await tasks.validate_waf_profile(str(missing_id), enqueue=lambda *_: None) == {
        "status": "not_found",
        "profile_id": str(missing_id),
    }

    _, source = await seed_source(db_session)
    profile = WafSourceProfile(
        instance_id=source.instance_id,
        log_source_id=source.id,
        url_property="URL",
        method_property="HTTP_Method",
    )
    db_session.add(profile)
    await db_session.commit()
    assert await tasks.validate_waf_profile(str(profile.id), enqueue=lambda *_: None) == {
        "status": "not_pending",
        "profile_id": str(profile.id),
    }
