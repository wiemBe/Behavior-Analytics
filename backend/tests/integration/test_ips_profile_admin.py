"""IPS profile administration API and asynchronous validation worker."""

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
from app.ips.profile import IPS_PRESETS, NORMALIZED_FIELD_ALLOWLIST
from app.main import create_app
from app.models.enums import (
    ClassificationReviewStatus,
    IpsPreset,
    IpsProfileValidationStatus,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.identity import AuditLog
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.providers.dto import LogSourceDTO
from app.providers.mock import MockQRadarProvider
from app.schemas.property_catalog import PropertyCatalogItemOut, PropertyCatalogOut
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.ips_profiles import IpsProfileService
from app.services.property_catalog import get_property_catalog_service
from app.workers import tasks

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def settings() -> Settings:
    return get_settings().model_copy(
        update={"ips_validation_pending_timeout_seconds": 900}
    )


async def seed_source(
    session, *, name: str = "Cisco FTD primary", qradar_id: int = 413
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
        type_id=448,
        type_name="Cisco Firepower Threat Defense",
        monitoring_enabled=True,
    )
    session.add_all([instance, source])
    await session.flush()
    return instance, source


def profile_body(
    *,
    expected: int | None = None,
    destination_port: str = "destinationport",
    label: str | None = "Edge IPS",
    preset: str | None = "CISCO_FTD",
) -> dict:
    return {
        "expected_profile_version": expected,
        "product_label": label,
        "preset": preset,
        "mapping": {
            "SOURCE_IP": {"kind": "NORMALIZED", "name": "sourceip"},
            "DESTINATION_IP": {"kind": "NORMALIZED", "name": "destinationip"},
            "DESTINATION_PORT": {"kind": "NORMALIZED", "name": destination_port},
            "PROTOCOL": {"kind": "NORMALIZED", "name": "protocolid"},
            "SIGNATURE": {"kind": "NORMALIZED", "name": "qid"},
            "ACTION": None,
            "SEVERITY": {"kind": "NORMALIZED", "name": "severity"},
        },
        "action_value_map": {"BLOCKED": [], "ALLOWED": []},
    }


def client(db_url: str, principal: Principal, cfg: Settings | None = None) -> TestClient:
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
        Principal(subject="ips-admin", permissions=frozenset({"admin:ips"})),
    )


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


def test_profile_routes_require_admin_ips() -> None:
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: settings().model_copy(
        update={"auth_provider": "oidc"}
    )
    with TestClient(app) as unauthenticated:
        response = unauthenticated.get(f"/api/v1/ips/log-sources/{uuid.uuid4()}/profile")
    assert response.status_code == 401
    for permissions in ({"read:*"}, {"admin:waf"}):
        api = client(
            "postgresql+asyncpg://unused:unused@localhost/unused",
            Principal(subject="wrong-role", permissions=frozenset(permissions)),
        )
        assert api.get(f"/api/v1/ips/log-sources/{uuid.uuid4()}/profile").status_code == 403


async def test_schema_presets_unknown_resources_and_property_catalog(
    db_session, db_schema
) -> None:
    api = admin_client(db_schema)
    response = api.get("/api/v1/ips/mapping-schema")
    assert response.status_code == 200
    body = response.json()
    assert body["domain"] == "IPS"
    assert body["property_name_pattern"] == "^[A-Za-z0-9_][A-Za-z0-9_ -]{0,63}$"
    assert body["min_coverage_ratio"] == settings().ips_validation_min_ratio
    assert {preset["key"] for preset in body["presets"]} == {
        "CISCO_FTD",
        "PALO_ALTO_THREAT",
    }
    for preset in body["presets"]:
        assert preset["mapping"]["ACTION"] is None
        assert preset["mapping"]["SIGNATURE"] == {"kind": "NORMALIZED", "name": "qid"}
    fields = {field["key"]: field for field in body["fields"]}
    assert fields["SOURCE_IP"]["required"] is True
    assert fields["PROTOCOL"]["required"] is False
    assert fields["SOURCE_IP"]["normalized_options"] == list(
        NORMALIZED_FIELD_ALLOWLIST
    )

    unknown = uuid.uuid4()
    assert api.get(f"/api/v1/ips/log-sources/{unknown}/profile").status_code == 404
    _, source = await seed_source(db_session)
    await db_session.commit()
    assert api.get(f"/api/v1/ips/log-sources/{source.id}/profile").status_code == 404

    class Catalog:
        async def catalog(self, **_kwargs):  # type: ignore[no-untyped-def]
            return PropertyCatalogOut(
                status="AVAILABLE",
                filtered_by_source_type=True,
                properties=[
                    PropertyCatalogItemOut(
                        name="IPS_Action",
                        property_type="string",
                        applies_to_source_type=True,
                        name_allowed=True,
                    )
                ],
            )

    api.app.dependency_overrides[get_property_catalog_service] = lambda: Catalog()
    catalog = api.get(f"/api/v1/ips/log-sources/{source.id}/property-catalog")
    assert catalog.status_code == 200
    assert catalog.json()["properties"][0]["name"] == "IPS_Action"


async def test_put_guards_create_update_sources_and_audit(
    db_session, db_schema, monkeypatch
) -> None:
    _, source = await seed_source(db_session)
    await db_session.commit()
    enqueued: list[str] = []
    monkeypatch.setattr(tasks.validate_ips_profile_task, "delay", enqueued.append)
    api = admin_client(db_schema)
    path = f"/api/v1/ips/log-sources/{source.id}/profile"

    missing = api.put(path, json=profile_body(expected=1))
    assert_conflict_detail(missing, current_version=None, updated_by=None, pending=False)

    invalid = profile_body()
    invalid["mapping"]["SOURCE_IP"] = {"kind": "NORMALIZED", "name": "username"}
    response = api.put(path, json=invalid)
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"message"}
    assert "allowlist" in response.json()["detail"]["message"]
    assert "username" not in response.json()["detail"]["message"]

    unsafe = profile_body()
    unsafe["mapping"]["ACTION"] = {"kind": "CUSTOM_PROPERTY", "name": 'x"; DROP'}
    response = api.put(path, json=unsafe)
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"message"}
    assert 'x"; DROP' not in response.json()["detail"]["message"]

    missing_required = profile_body()
    del missing_required["mapping"]["SIGNATURE"]
    assert api.put(path, json=missing_required).status_code == 422

    overlap = profile_body()
    overlap["mapping"]["ACTION"] = {"kind": "CUSTOM_PROPERTY", "name": "IPS_Action"}
    overlap["action_value_map"] = {"BLOCKED": ["DROP"], "ALLOWED": ["drop"]}
    response = api.put(path, json=overlap)
    assert response.status_code == 422
    assert set(response.json()["detail"]) == {"message"}

    without_action = profile_body()
    without_action["action_value_map"] = {"BLOCKED": ["DROP"], "ALLOWED": []}
    response = api.put(path, json=without_action)
    assert response.status_code == 422
    assert "needs an ACTION" in response.json()["detail"]["message"]

    created = api.put(path, json=profile_body())
    assert created.status_code == 201, created.text
    assert created.json()["enabled"] is False
    assert created.json()["validation_status"] == "UNVALIDATED"
    assert created.json()["preset"] == "CISCO_FTD"
    assert created.json()["validation_pending"] is True
    assert enqueued == [created.json()["profile_id"]]

    stale = api.put(path, json=profile_body(expected=2, destination_port="sourceport"))
    assert_conflict_detail(stale, current_version=1, updated_by="ips-admin", pending=True)

    await db_session.rollback()
    db_session.expire_all()
    before = int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)
    unchanged = api.put(path, json=profile_body(expected=1))
    assert unchanged.status_code == 200
    await db_session.rollback()
    db_session.expire_all()
    after = int(await db_session.scalar(select(func.count()).select_from(AuditLog)) or 0)
    assert after == before
    assert len(enqueued) == 1

    changed = api.put(
        path, json=profile_body(expected=1, destination_port="sourceport")
    )
    assert changed.status_code == 200
    assert changed.json()["profile_version"] == 2
    assert changed.json()["validation_status"] == "UNVALIDATED"
    assert changed.json()["validation_pending"] is True
    assert len(enqueued) == 2

    sources = api.get("/api/v1/ips/sources")
    assert sources.status_code == 200
    assert sources.json()[0]["validation_pending"] is True
    assert sources.json()[0]["validation_requested_by"] == "ips-admin"
    await db_session.rollback()
    db_session.expire_all()
    actor_actions = list(
        (
            await db_session.scalars(
                select(AuditLog.action).where(AuditLog.actor == "ips-admin")
            )
        ).all()
    )
    assert actor_actions.count("ips.profile.created") == 1
    assert actor_actions.count("ips.profile.mapping_changed") == 1
    assert actor_actions.count("ips.profile.validation_requested") == 2


async def test_pending_enable_disable_report_history_and_candidates(
    db_session, db_schema, monkeypatch
) -> None:
    instance, source = await seed_source(db_session)
    profile = IpsSourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        preset=IpsPreset.CISCO_FTD,
        mapping=IPS_PRESETS[IpsPreset.CISCO_FTD].mapping_dict(),
        action_value_map={},
        validation_status=IpsProfileValidationStatus.UNVALIDATED,
        validation_requested_at=NOW,
        validation_requested_by="seed",
        validation_report={
            "status": "INCONCLUSIVE",
            "reasons": ["old"],
            "warnings": [],
            "total_events": None,
            "window_start": (NOW - timedelta(minutes=15)).isoformat(),
            "window_end": NOW.isoformat(),
            "profile_version": 1,
            "probe_truncated": False,
            "ariel_searches": 1,
            "queries": ["SELECT secret"],
            "fields": [
                {
                    "field": "SOURCE_IP",
                    "reference": {"kind": "NORMALIZED", "name": "sourceip"},
                    "status": "AVAILABLE",
                    "used_by_collection": True,
                    "values_truncated": False,
                    "queries": ["SELECT sourceip"],
                }
            ],
        },
    )
    db_session.add(profile)
    await db_session.flush()
    profile_id = profile.id
    source_id = source.id
    instance_id = instance.id
    for index in range(55):
        db_session.add(
            AuditLog(
                created_at=NOW + timedelta(seconds=index),
                actor=f"actor-{index}",
                action="ips.profile.relabelled",
                object_type="ips_source_profile",
                object_id=str(profile_id),
                outcome="SUCCESS",
                detail={"profile_version": index + 1},
            )
        )
    names = ["Zulu", "Alpha%IPS", "AlphaXIPS", "Under_score", "UnderXscore"]
    candidates: list[LogSource] = []
    for offset, name in enumerate(names, start=500):
        candidate = LogSource(
            instance_id=instance.id,
            qradar_id=offset,
            name=name,
            monitoring_enabled=True,
        )
        db_session.add(candidate)
        candidates.append(candidate)
    await db_session.flush()
    db_session.add(
        LogSourceClassification(
            log_source_id=candidates[0].id,
            source_class=SourceClass.IPS,
            basis="TYPE",
            conflicts=[],
            classified_at=NOW,
            review_status=ClassificationReviewStatus.NEW,
        )
    )
    await db_session.commit()

    api = admin_client(db_schema)
    base = f"/api/v1/ips/log-sources/{source_id}/profile"
    pending = api.post(f"{base}/validate")
    assert_conflict_detail(pending, current_version=1, updated_by=None, pending=True)
    refused = api.post(f"{base}/enable")
    assert_conflict_detail(refused, current_version=1, updated_by=None, pending=True)
    disabled = api.post(f"{base}/disable")
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False
    assert "SELECT" not in disabled.text

    await db_session.rollback()
    db_session.expire_all()
    current = await db_session.get(IpsSourceProfile, profile_id)
    assert current is not None
    current.validation_status = IpsProfileValidationStatus.VALID
    current.validated_profile_version = 1
    current.validation_requested_at = None
    await db_session.commit()
    enabled = api.post(f"{base}/enable")
    assert enabled.status_code == 200
    assert enabled.json()["enabled"] is True
    assert api.post(f"{base}/disable").status_code == 200

    history = api.get(f"{base}/history")
    assert history.status_code == 200
    assert len(history.json()) == 50
    assert history.json()[0]["actor"] == "actor-54"
    assert history.json()[-1]["actor"] == "actor-5"

    candidate_base = f"/api/v1/ips/candidates?instance_id={instance_id}"
    ordered = api.get(candidate_base).json()
    assert ordered[0]["name"] == "Zulu"
    assert {item["instance_id"] for item in ordered} == {str(instance_id)}
    assert "Cisco FTD primary" not in [item["name"] for item in ordered]
    assert [item["name"] for item in api.get(f"{candidate_base}&q=%25").json()] == [
        "Alpha%IPS"
    ]
    assert [item["name"] for item in api.get(f"{candidate_base}&q=_").json()] == [
        "Under_score"
    ]
    assert len(api.get(f"{candidate_base}&limit=2").json()) == 2
    assert api.get(f"{candidate_base}&limit=201").status_code == 422

    await db_session.rollback()
    db_session.expire_all()
    mutation_actors = set(
        (
            await db_session.scalars(
                select(AuditLog.actor).where(
                    AuditLog.object_id == str(profile_id),
                    AuditLog.action.in_({"ips.profile.enabled", "ips.profile.disabled"}),
                )
            )
        ).all()
    )
    assert mutation_actors == {"ips-admin"}


class ValidationProvider(MockQRadarProvider):
    def __init__(self, hook=None, error: Exception | None = None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(now=NOW)
        self.hook = hook
        self.error = error
        self.closed = False

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        return LogSourceDTO(qradar_id=qradar_id, name="IPS")

    async def probe_ips_fields(self, **kwargs):  # type: ignore[no-untyped-def, override]
        if self.hook is not None:
            await self.hook()
        if self.error is not None:
            raise self.error
        return await super().probe_ips_fields(**kwargs)

    async def aclose(self) -> None:
        self.closed = True


async def pending_profile(db_session) -> IpsSourceProfile:  # type: ignore[no-untyped-def]
    _, source = await seed_source(db_session)
    service = IpsProfileService(db_session, settings=settings(), clock=lambda: NOW)
    profile, _ = await service.upsert(
        log_source=source,
        mapping=IPS_PRESETS[IpsPreset.CISCO_FTD],
        actor="seed",
        preset=IpsPreset.CISCO_FTD,
    )
    await service.request_validation(profile, actor="requester", enqueue=lambda _: None)
    return profile


async def test_worker_success_exception_and_stale_race(
    db_session, db_schema, monkeypatch
) -> None:
    profile = await pending_profile(db_session)
    profile_id = profile.id
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)

    provider = ValidationProvider()
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *args, **kwargs: provider)
    result = await tasks.validate_ips_profile(str(profile_id), enqueue=lambda *_: None)
    assert result["status"] == "ok"
    assert result["validation_status"] == "VALID"
    assert provider.closed
    await db_session.rollback()
    db_session.expire_all()
    stored = await db_session.get(IpsSourceProfile, profile_id)
    assert stored is not None
    assert stored.validation_status == IpsProfileValidationStatus.VALID
    assert stored.validated_profile_version == 1
    assert stored.validation_requested_at is None
    success_audit = await db_session.scalar(
        select(AuditLog)
        .where(
            AuditLog.object_id == str(profile_id),
            AuditLog.action == "ips.profile.validated",
        )
        .order_by(AuditLog.created_at.desc())
    )
    assert success_audit is not None
    assert success_audit.actor == "requester"

    stored.validation_status = IpsProfileValidationStatus.INVALID
    stored.validation_report = {"old": True}
    stored.validation_requested_at = NOW
    stored.validation_requested_by = "requester"
    await db_session.commit()
    failed_provider = ValidationProvider(error=RuntimeError("do not persist this text"))
    monkeypatch.setattr(
        tasks, "build_provider_for_instance", lambda *args, **kwargs: failed_provider
    )
    failed = await tasks.validate_ips_profile(str(profile_id), enqueue=lambda *_: None)
    assert failed["status"] == "failed"
    assert failed["error_class"] == "RuntimeError"
    await db_session.rollback()
    db_session.expire_all()
    unchanged = await db_session.get(IpsSourceProfile, profile_id)
    assert unchanged is not None
    assert unchanged.validation_status == IpsProfileValidationStatus.INVALID
    assert unchanged.validation_report == {"old": True}
    assert unchanged.validation_requested_at is None

    unchanged.validation_requested_at = NOW
    unchanged.validation_requested_by = "requester"
    await db_session.commit()

    async def change_mapping() -> None:
        async with maker() as other:
            current = await other.get(IpsSourceProfile, profile_id)
            assert current is not None
            current.mapping = {
                **current.mapping,
                "DESTINATION_PORT": {"kind": "NORMALIZED", "name": "sourceport"},
            }
            current.profile_version = 2
            current.validation_status = IpsProfileValidationStatus.UNVALIDATED
            current.validation_report = {}
            current.validation_requested_at = NOW + timedelta(seconds=1)
            current.validation_requested_by = "newer-admin"
            await other.commit()

    stale_provider = ValidationProvider(hook=change_mapping)
    monkeypatch.setattr(
        tasks, "build_provider_for_instance", lambda *args, **kwargs: stale_provider
    )
    enqueued: list[tuple[str, int, int]] = []
    stale = await tasks.validate_ips_profile(
        str(profile_id),
        enqueue=lambda *args: enqueued.append(args),
    )
    assert stale["status"] == "stale_requeued"
    assert stale["validated_version"] == 1
    assert stale["current_version"] == 2
    assert enqueued == [(str(profile_id), 0, 0)]
    await engine.dispose()


async def test_worker_lock_countdown_exhaustion_and_terminal_guards(
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
    first = await tasks.validate_ips_profile(
        str(profile_id), enqueue=lambda *args: enqueued.append(args)
    )
    assert first == {
        "status": "locked_requeued",
        "profile_id": str(profile_id),
        "lock_retry_count": 1,
    }
    assert enqueued == [(str(profile_id), 1, 30)]
    assert lock_names == ["ips_pipeline:413"]

    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: settings().model_copy(update={"ips_validation_lock_max_retries": 1}),
    )
    exhausted = await tasks.validate_ips_profile(
        str(profile_id),
        lock_retry_count=1,
        enqueue=lambda *_: pytest.fail("exhaustion must not enqueue"),
    )
    assert exhausted == {
        "status": "source_busy",
        "profile_id": str(profile_id),
        "lock_retry_count": 1,
    }
    await db_session.rollback()
    db_session.expire_all()
    current = await db_session.get(IpsSourceProfile, profile_id)
    assert current is not None
    assert current.validation_status == IpsProfileValidationStatus.UNVALIDATED
    assert current.validation_requested_at is None
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.object_id == str(profile_id), AuditLog.outcome == "SOURCE_BUSY"
        )
    )
    assert audit is not None
    assert audit.actor == "requester"

    missing_id = uuid.uuid4()
    assert await tasks.validate_ips_profile(str(missing_id), enqueue=lambda *_: None) == {
        "status": "not_found",
        "profile_id": str(missing_id),
    }
    assert await tasks.validate_ips_profile(str(profile_id), enqueue=lambda *_: None) == {
        "status": "not_pending",
        "profile_id": str(profile_id),
    }
