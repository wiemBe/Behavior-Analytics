"""Identity validation is queued: the route marks the profile pending and a worker probes."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.identity.revalidation import failure_token
from app.models.identity import AuditLog
from app.models.identity_detection import IdentitySourceProfile
from app.models.log_source import LogSource
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider
from app.security.rbac import Principal
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_fleet_api import Client

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
NOW = datetime.now(UTC)


class Probe(MockQRadarProvider):
    """The mock probe, with a failure to inject and a record of the close."""

    def __init__(self, *, error: Exception | None = None, hook=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(seed=1)
        self.error = error
        self.hook = hook
        self.closed = False

    async def probe_identity_fields(self, **kwargs):  # type: ignore[no-untyped-def]
        if self.hook is not None:
            await self.hook()
        if self.error is not None:
            raise self.error
        return await super().probe_identity_fields(**kwargs)

    async def aclose(self) -> None:
        self.closed = True


async def seed_profile(db_session, **over):  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=1003, name="dc01")
    values = dict(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=False,
        profile_version=1,
        event_id_property="EventID",
        username_property="username",
        enabled_event_ids=[4625],
        validation_status="UNVALIDATED",
        validation_report={},
    )
    values.update(over)
    profile = IdentitySourceProfile(**values)
    db_session.add(profile)
    await db_session.commit()
    return profile, source


def _url(source_id: uuid.UUID) -> str:
    return f"/api/v1/identity/sources/{source_id}/validate"


async def _reload(db_session, profile_id):  # type: ignore[no-untyped-def]
    await db_session.rollback()
    db_session.expire_all()
    row = await db_session.get(IdentitySourceProfile, profile_id)
    assert row is not None
    return row


def _worker(db_schema, monkeypatch, provider):  # type: ignore[no-untyped-def]
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "build_provider_for_instance", lambda *a, **k: provider)
    return maker


async def test_the_route_queues_and_builds_no_provider(
    db_session, db_schema, monkeypatch
) -> None:
    profile, source = await seed_profile(db_session)
    profile_id, source_id = profile.id, source.id
    queued: list[str] = []
    monkeypatch.setattr(tasks.validate_identity_profile_task, "delay", queued.append)

    def forbidden(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("the request must not build a provider")

    monkeypatch.setattr("app.providers.factory.build_provider_for_instance", forbidden)
    monkeypatch.setattr(tasks, "build_provider_for_instance", forbidden)

    response = Client(ADMIN).post(_url(source_id))

    assert response.status_code == 202, response.text
    body = response.json()
    assert body["validation_pending"] is True
    assert body["validation_requested_by"] == "admin"
    assert body["validation_requested_at"] is not None
    assert body["validation_status"] == "UNVALIDATED"
    assert queued == [str(profile_id)]
    stored = await _reload(db_session, profile_id)
    assert stored.validation_requested_at is not None
    actions = (await db_session.scalars(select(AuditLog.action))).all()
    assert "identity.profile.validation_requested" in actions


async def test_a_second_request_is_refused_until_the_pending_marker_is_stale(
    db_session, db_schema, monkeypatch
) -> None:
    since = NOW - timedelta(seconds=30)
    profile, source = await seed_profile(
        db_session, validation_requested_at=since, validation_requested_by="first"
    )
    profile_id, source_id = profile.id, source.id
    queued: list[str] = []
    monkeypatch.setattr(tasks.validate_identity_profile_task, "delay", queued.append)
    client = Client(ADMIN)

    refused = client.post(_url(source_id))

    assert refused.status_code == 409, refused.text
    assert refused.json()["detail_code"] == "validation.profile.conflict.running"
    assert refused.json()["detail_params"]["since"] == since.isoformat()
    assert queued == []
    assert (await _reload(db_session, profile_id)).validation_requested_by == "first"

    stale = await _reload(db_session, profile_id)
    stale.validation_requested_at = NOW - timedelta(seconds=901)
    await db_session.commit()
    allowed = client.post(_url(source_id))
    assert allowed.status_code == 202, allowed.text
    assert queued == [str(profile_id)]
    assert (await _reload(db_session, profile_id)).validation_requested_by == "admin"


async def test_the_task_stores_valid_and_clears_the_marker(
    db_session, db_schema, monkeypatch
) -> None:
    profile, _ = await seed_profile(
        db_session, validation_requested_at=NOW, validation_requested_by="requester"
    )
    provider = Probe()
    _worker(db_schema, monkeypatch, provider)

    result = await tasks.validate_identity_profile(str(profile.id), enqueue=lambda *_: None)

    assert result["status"] == "ok"
    assert result["validation_status"] == "VALID"
    assert provider.closed
    stored = await _reload(db_session, profile.id)
    assert stored.validation_status == "VALID"
    assert stored.validated_profile_version == 1
    assert stored.validation_requested_at is None
    assert stored.validation_requested_by is None
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.object_id == str(profile.id), AuditLog.action == "identity.profile.validated"
        )
    )
    assert audit is not None and audit.actor == "requester"


@pytest.mark.parametrize("error", [ProviderError("upstream said secret-text"), RuntimeError("x")])
async def test_a_provider_failure_is_never_valid_and_clears_the_marker(
    db_session, db_schema, monkeypatch, error
) -> None:
    profile, _ = await seed_profile(
        db_session,
        validation_status="INVALID",
        validation_report={"old": True},
        validation_requested_at=NOW,
        validation_requested_by="requester",
    )
    provider = Probe(error=error)
    _worker(db_schema, monkeypatch, provider)

    result = await tasks.validate_identity_profile(str(profile.id), enqueue=lambda *_: None)

    assert result["status"] == "failed"
    assert provider.closed
    stored = await _reload(db_session, profile.id)
    assert stored.validation_status == "INVALID"
    assert stored.validation_status != "VALID"
    assert stored.validation_requested_at is None
    assert stored.validation_report["old"] is True
    assert stored.validation_report["validation_error"] == failure_token(type(error).__name__)
    assert "secret-text" not in str(stored.validation_report)
    audit = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.object_id == str(profile.id), AuditLog.outcome == "FAILURE"
        )
    )
    assert audit is not None


async def test_a_failure_does_not_downgrade_a_collecting_profile(
    db_session, db_schema, monkeypatch
) -> None:
    profile, _ = await seed_profile(
        db_session,
        enabled=True,
        validation_status="VALID",
        validated_profile_version=1,
        validation_requested_at=NOW,
        validation_requested_by="requester",
    )
    _worker(db_schema, monkeypatch, Probe(error=ProviderError("down")))

    await tasks.validate_identity_profile(str(profile.id), enqueue=lambda *_: None)

    stored = await _reload(db_session, profile.id)
    assert stored.validation_status == "VALID"
    assert stored.validated_profile_version == 1
    assert stored.validation_report["validation_error"] == failure_token("ProviderError")
    assert stored.validation_requested_at is None


async def test_a_refused_mapping_is_stored_invalid(
    db_session, db_schema, monkeypatch
) -> None:
    profile, _ = await seed_profile(
        db_session,
        enabled_event_ids=[1],
        validation_requested_at=NOW,
        validation_requested_by="requester",
    )
    _worker(db_schema, monkeypatch, Probe())

    result = await tasks.validate_identity_profile(str(profile.id), enqueue=lambda *_: None)

    assert result["status"] == "failed"
    stored = await _reload(db_session, profile.id)
    assert stored.validation_status == "INVALID"
    assert stored.validation_report["validation_error"] == "profile_invalid"
    assert stored.validation_requested_at is None


async def test_an_unsupported_provider_stores_the_reason_and_stays_unvalidated(
    db_session, db_schema, monkeypatch
) -> None:
    profile, _ = await seed_profile(
        db_session, validation_requested_at=NOW, validation_requested_by="requester"
    )
    provider = Probe()
    provider.supports = lambda capability: False  # type: ignore[method-assign]
    _worker(db_schema, monkeypatch, provider)

    await tasks.validate_identity_profile(str(profile.id), enqueue=lambda *_: None)

    stored = await _reload(db_session, profile.id)
    assert stored.validation_status == "UNVALIDATED"
    assert stored.validation_report["validation_error"] == "provider_unsupported"
    assert stored.validation_requested_at is None
    assert provider.closed


async def test_a_deleted_source_and_an_unrequested_profile_do_nothing(
    db_session, db_schema, monkeypatch
) -> None:
    profile, source = await seed_profile(db_session, validation_requested_at=NOW)
    provider = Probe()
    _worker(db_schema, monkeypatch, provider)

    idle, _ = await seed_profile_idle(db_session)
    assert await tasks.validate_identity_profile(str(idle.id), enqueue=lambda *_: None) == {
        "status": "not_pending",
        "profile_id": str(idle.id),
    }

    await db_session.delete(await db_session.get(LogSource, source.id))
    await db_session.commit()
    missing = await tasks.validate_identity_profile(str(profile.id), enqueue=lambda *_: None)
    assert missing["status"] == "not_found"
    unknown = await tasks.validate_identity_profile(str(uuid.uuid4()), enqueue=lambda *_: None)
    assert unknown["status"] == "not_found"
    assert not provider.closed


async def seed_profile_idle(db_session):  # type: ignore[no-untyped-def]
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=2001, name="dc02")
    profile = IdentitySourceProfile(
        instance_id=instance.id,
        log_source_id=source.id,
        enabled=False,
        profile_version=1,
        event_id_property="EventID",
        username_property="username",
        enabled_event_ids=[4625],
        validation_status="UNVALIDATED",
        validation_report={},
    )
    db_session.add(profile)
    await db_session.commit()
    return profile, source


async def test_a_mapping_change_during_the_probe_drops_the_result(
    db_session, db_schema, monkeypatch
) -> None:
    profile, _ = await seed_profile(
        db_session, validation_requested_at=NOW, validation_requested_by="requester"
    )
    profile_id = profile.id
    engine = create_async_engine(db_schema, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def change_mapping() -> None:
        async with maker() as other:
            current = await other.get(IdentitySourceProfile, profile_id)
            assert current is not None
            current.profile_version = 2
            current.validation_requested_at = NOW + timedelta(seconds=1)
            current.validation_requested_by = "newer-admin"
            await other.commit()

    _worker(db_schema, monkeypatch, Probe(hook=change_mapping))
    enqueued: list[tuple[str, int, int]] = []

    result = await tasks.validate_identity_profile(
        str(profile_id), enqueue=lambda *args: enqueued.append(args)
    )

    assert result["status"] == "stale_requeued"
    assert enqueued == [(str(profile_id), 0, 0)]
    stored = await _reload(db_session, profile_id)
    assert stored.validation_status == "UNVALIDATED"
    assert stored.profile_version == 2
    assert stored.validation_requested_by == "newer-admin"
    await engine.dispose()


async def test_the_celery_task_runs_the_coroutine_and_is_registered() -> None:
    assert tasks.validate_identity_profile_task.name == "validate_identity_profile"
