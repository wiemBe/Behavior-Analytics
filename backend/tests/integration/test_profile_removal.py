"""Removing a WAF or IPS source profile (UIQ-9).

The profile row is the only thing deleted: the log source, its class and every collected
aggregate stay. Removal must work whatever the state of the log source (disabled in
QRadar) or of the profile (disabled, INCONCLUSIVE).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.models.enums import (
    IpsProfileValidationStatus,
    WafProfileValidationStatus,
)
from app.models.identity import AuditLog
from app.models.ips import IpsSourceBucket, IpsSourceProfile
from app.models.log_source import LogSource
from app.models.waf import WafSourceBucket, WafSourceProfile
from app.security.rbac import Principal
from app.services.waf_pipeline import eligible_profile_ids
from app.workers import tasks
from tests.integration import test_ips_profile_admin as ips_t
from tests.integration import test_waf_profile_admin as waf_t
from tests.integration.test_entity_timeline import _ips_bucket, _waf_bucket

pytestmark = pytest.mark.integration

KINDS = ("waf", "ips")


def _client(kind: str, db_url: str, *, permissions: frozenset[str] | None = None):  # type: ignore[no-untyped-def]
    perms = permissions or frozenset({f"admin:{kind}"})
    mod = waf_t if kind == "waf" else ips_t
    return mod.client(db_url, Principal(subject=f"{kind}-admin", permissions=perms))


async def _seed(kind: str, session, *, source_enabled: bool = True) -> LogSource:  # type: ignore[no-untyped-def]
    mod = waf_t if kind == "waf" else ips_t
    _, source = await mod.seed_source(session)
    source.enabled = source_enabled
    await session.commit()
    return source


def _put(kind: str, api, source: LogSource, monkeypatch) -> dict:  # type: ignore[no-untyped-def]
    task = tasks.validate_waf_profile_task if kind == "waf" else tasks.validate_ips_profile_task
    monkeypatch.setattr(task, "delay", lambda *_a, **_k: None)
    body = waf_t.profile_body() if kind == "waf" else ips_t.profile_body()
    response = api.put(f"/api/v1/{kind}/log-sources/{source.id}/profile", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def _path(kind: str, source_id: uuid.UUID) -> str:
    return f"/api/v1/{kind}/log-sources/{source_id}/profile"


@pytest.mark.parametrize("kind", KINDS)
def test_delete_requires_the_family_permission(kind: str) -> None:
    other = "ips" if kind == "waf" else "waf"
    for permissions in ({"read:*"}, {f"admin:{other}"}):
        api = _client(
            kind,
            "postgresql+asyncpg://unused:unused@localhost/unused",
            permissions=frozenset(permissions),
        )
        assert api.delete(_path(kind, uuid.uuid4())).status_code == 403


@pytest.mark.parametrize("kind", KINDS)
def test_delete_unauthenticated_is_401(kind: str) -> None:
    from fastapi.testclient import TestClient

    from app.core.config import get_settings
    from app.main import create_app

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "oidc"}
    )
    with TestClient(app) as anonymous:
        assert anonymous.delete(_path(kind, uuid.uuid4())).status_code == 401


@pytest.mark.parametrize("kind", KINDS)
async def test_delete_404_for_unknown_source_and_for_source_without_profile(
    kind: str, db_session, db_schema
) -> None:
    api = _client(kind, db_schema)
    assert api.delete(_path(kind, uuid.uuid4())).status_code == 404
    source = await _seed(kind, db_session)
    assert api.delete(_path(kind, source.id)).status_code == 404


@pytest.mark.parametrize("kind", KINDS)
@pytest.mark.parametrize("source_enabled", [True, False])
@pytest.mark.parametrize(
    ("profile_enabled", "status"),
    [(True, "VALID"), (False, "VALID"), (False, "INCONCLUSIVE"), (False, "UNVALIDATED")],
)
async def test_delete_works_in_every_state_and_keeps_history(
    kind: str,
    source_enabled: bool,
    profile_enabled: bool,
    status: str,
    db_session,
    db_schema,
    monkeypatch,
) -> None:
    source = await _seed(kind, db_session, source_enabled=source_enabled)
    source_id, qradar_id = source.id, source.qradar_id
    api = _client(kind, db_schema)
    created = _put(kind, api, source, monkeypatch)
    profile_id = uuid.UUID(created["profile_id"])
    model = WafSourceProfile if kind == "waf" else IpsSourceProfile
    enum = WafProfileValidationStatus if kind == "waf" else IpsProfileValidationStatus
    profile = await db_session.get(model, profile_id)
    profile.enabled = profile_enabled
    profile.validation_status = enum(status)
    bucket = _waf_bucket(source_id) if kind == "waf" else _ips_bucket(source_id)
    db_session.add(bucket)
    await db_session.commit()
    bucket_model = WafSourceBucket if kind == "waf" else IpsSourceBucket

    response = api.delete(_path(kind, source_id))
    assert response.status_code == 204
    assert response.content == b""

    db_session.expire_all()
    assert await db_session.get(model, profile_id) is None
    # The log source and the collected aggregate stay.
    assert await db_session.get(LogSource, source_id) is not None
    assert (
        await db_session.scalar(
            select(func.count()).select_from(bucket_model).where(
                bucket_model.log_source_id == source_id
            )
        )
        == 1
    )
    assert api.get(_path(kind, source_id)).status_code == 404
    assert api.delete(_path(kind, source_id)).status_code == 404
    listing = api.get("/api/v1/waf/sources" if kind == "waf" else "/api/v1/ips/sources")
    assert listing.status_code == 200
    assert listing.json() == []

    audit = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.action == f"{kind}.profile.removed")
        )
    ).all()
    assert len(audit) == 1
    assert audit[0].actor == f"{kind}-admin"
    assert audit[0].object_id == str(profile_id)
    # The audit writer redacts UUID-shaped values (as it does on `profile.created`), so the
    # source is identified by its QRadar id.
    assert audit[0].detail["qradar_log_source_id"] == qradar_id
    assert audit[0].detail["profile_version"] == 1
    assert audit[0].detail["enabled"] is profile_enabled
    assert audit[0].detail["validation_status"] == status


@pytest.mark.parametrize("kind", KINDS)
async def test_removed_source_is_a_candidate_again_and_can_be_re_added(
    kind: str, db_session, db_schema, monkeypatch
) -> None:
    source = await _seed(kind, db_session, source_enabled=False)
    api = _client(kind, db_schema)
    source_id, name = source.id, source.name
    first = _put(kind, api, source, monkeypatch)

    def candidate_ids() -> set[str]:
        response = api.get(f"/api/v1/{kind}/candidates", params={"q": name})
        assert response.status_code == 200
        return {item["log_source_id"] for item in response.json()}

    assert str(source_id) not in candidate_ids()
    assert api.delete(_path(kind, source_id)).status_code == 204
    assert str(source_id) in candidate_ids()

    second = _put(kind, api, source, monkeypatch)
    assert second["profile_id"] != first["profile_id"]
    assert second["profile_version"] == 1
    assert second["enabled"] is False
    assert str(source_id) not in candidate_ids()


async def test_waf_dispatch_skips_a_removed_profile(
    db_session, db_schema, monkeypatch
) -> None:
    source = await _seed("waf", db_session)
    api = _client("waf", db_schema)
    created = _put("waf", api, source, monkeypatch)
    profile = await db_session.get(WafSourceProfile, uuid.UUID(created["profile_id"]))
    profile.enabled = True
    profile.validation_status = WafProfileValidationStatus.VALID
    profile.validated_profile_version = profile.profile_version
    profile.validated_at = datetime.now(UTC) - timedelta(minutes=1)
    await db_session.commit()
    cfg = waf_t.settings()

    ids, _ = await eligible_profile_ids(db_session, cfg)
    assert ids == [profile.id]

    assert api.delete(_path("waf", source.id)).status_code == 204
    db_session.expire_all()
    ids, skipped = await eligible_profile_ids(db_session, cfg)
    assert ids == []
    assert skipped == {}


@pytest.mark.parametrize("kind", KINDS)
async def test_class_route_reports_override_or_rule(kind: str, db_session, db_schema) -> None:
    from app.models.enums import ClassificationReviewStatus, SourceClass
    from app.models.fleet import LogSourceClassification

    source = await _seed(kind, db_session)
    api = _client(kind, db_schema)
    url = f"/api/v1/{kind}/log-sources/{source.id}/class"
    assert api.get(url).json() == {"source_class": None, "basis": None, "override_class": None}

    klass = SourceClass.WAF if kind == "waf" else SourceClass.IPS
    row = LogSourceClassification(
        log_source_id=source.id,
        source_class=klass,
        basis="OVERRIDE",
        override_class=klass,
        classified_at=datetime.now(UTC),
        review_status=ClassificationReviewStatus.REVIEWED,
    )
    db_session.add(row)
    await db_session.commit()
    assert api.get(url).json() == {
        "source_class": klass.value,
        "basis": "OVERRIDE",
        "override_class": klass.value,
    }
    row.basis = "RULE"
    row.override_class = None
    await db_session.commit()
    assert api.get(url).json()["override_class"] is None
    assert api.get(f"/api/v1/{kind}/log-sources/{uuid.uuid4()}/class").status_code == 404
