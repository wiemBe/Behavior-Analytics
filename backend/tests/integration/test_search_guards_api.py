"""Enabled-search cap, cron floor, authoring 422 shape, audit split, and auth.

DB-gated. The cap in these tests is two enabled searches. Creates use the
hourly preset except where the floor itself is what the test is about.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.identity import AuditLog
from app.schemas.search import unreachable_threshold_reason
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.aql_validator import AQLValidationError, validate_aql
from app.services.scheduled_search import ScheduledSearchService
from tests.integration.factories import make_search

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run search guard integration tests",
    ),
]

AQL = "SELECT sourceip FROM events LAST 1 HOURS"
BAD_AQL = "DELETE FROM events"
HOURLY = "0 * * * *"
EVERY_MINUTE = "* * * * *"
ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())


def _client(
    db_url: str,
    principal: Principal | None = ADMIN,
    *,
    max_enabled: int = 25,
    auth_provider: str | None = None,
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

    updates: dict[str, object] = {"scheduled_searches_max_enabled": max_enabled}
    if auth_provider is not None:
        updates["auth_provider"] = auth_provider
    settings = get_settings().model_copy(update=updates)
    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def _payload(name: str, **overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": name,
        "aql_query": AQL,
        "schedule_cron": HOURLY,
    }
    base.update(overrides)
    return base


def _aql_reasons() -> list[str]:
    with pytest.raises(AQLValidationError) as exc:
        validate_aql(BAD_AQL)
    return list(exc.value.reasons)


async def _audits(session, action: str, object_id: str | None = None) -> list[AuditLog]:
    session.expire_all()
    stmt = select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.created_at)
    if object_id is not None:
        stmt = stmt.where(AuditLog.object_id == object_id)
    return list((await session.scalars(stmt)).all())


# ------------------------------------------------------------------------ cap
def test_create_past_the_cap_is_409(db_schema: str) -> None:
    client = _client(db_schema, max_enabled=2)
    assert client.post("/api/v1/searches", json=_payload("cap-a")).status_code == 201
    assert client.post("/api/v1/searches", json=_payload("cap-b")).status_code == 201
    refused = client.post("/api/v1/searches", json=_payload("cap-c"))
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "the limit is 2 enabled searches"
    names = {row["name"] for row in client.get("/api/v1/searches").json()}
    assert names == {"cap-a", "cap-b"}


async def test_enable_past_the_cap_is_409(db_session, db_schema: str) -> None:
    client = _client(db_schema, max_enabled=1)
    created = client.post("/api/v1/searches", json=_payload("cap-on")).json()
    parked = client.post(
        "/api/v1/searches", json=_payload("cap-off", enabled=False)
    )
    assert parked.status_code == 201, parked.text
    refused = client.patch(
        f"/api/v1/searches/{parked.json()['id']}", json={"enabled": True}
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["detail"] == "the limit is 1 enabled searches"
    row = client.get(f"/api/v1/searches/{parked.json()['id']}").json()
    assert row["enabled"] is False
    assert await _audits(db_session, "search.enable", parked.json()["id"]) == []
    assert await _audits(db_session, "search.update", parked.json()["id"]) == []
    # The enabled row that filled the cap is untouched.
    assert client.get(f"/api/v1/searches/{created['id']}").json()["enabled"] is True


def test_disabled_search_can_be_created_over_the_cap(db_schema: str) -> None:
    client = _client(db_schema, max_enabled=1)
    assert client.post("/api/v1/searches", json=_payload("full")).status_code == 201
    created = client.post(
        "/api/v1/searches", json=_payload("parked", enabled=False)
    )
    assert created.status_code == 201, created.text
    assert created.json()["enabled"] is False


def test_reenabling_an_enabled_search_at_the_cap_is_allowed(db_schema: str) -> None:
    client = _client(db_schema, max_enabled=1)
    created = client.post("/api/v1/searches", json=_payload("stay")).json()
    resp = client.patch(f"/api/v1/searches/{created['id']}", json={"enabled": True})
    assert resp.status_code == 200, resp.text
    assert resp.json()["enabled"] is True


async def test_enabled_count_is_one_query_and_ignores_disabled(db_session) -> None:
    await make_search(db_session, name="counted", schedule_cron=HOURLY, enabled=True)
    await make_search(
        db_session, name="ignored", schedule_cron=EVERY_MINUTE, enabled=False
    )
    await db_session.commit()
    statements: list[str] = []

    def _capture(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    connection = await db_session.connection()
    sync_engine = connection.sync_connection.engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        counted = await ScheduledSearchService(db_session).count_enabled()
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)
    selects = [sql for sql in statements if sql.lstrip().lower().startswith("select")]
    assert counted == 1
    assert selects == [selects[0]]
    assert "scheduled_search" in selects[0].lower()


# ---------------------------------------------------------------------- cron
def test_five_minute_preset_is_accepted(db_schema: str) -> None:
    resp = _client(db_schema).post(
        "/api/v1/searches", json=_payload("five", schedule_cron="*/5 * * * *")
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["schedule_cron"] == "*/5 * * * *"


def test_every_minute_cron_is_refused_on_create(db_schema: str) -> None:
    client = _client(db_schema)
    resp = client.post(
        "/api/v1/searches", json=_payload("fast", schedule_cron=EVERY_MINUTE)
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert isinstance(detail, str)
    assert "60" in detail
    assert "300" in detail
    assert client.get("/api/v1/searches").json() == []


def test_changed_cron_below_the_floor_is_refused(db_schema: str) -> None:
    client = _client(db_schema)
    created = client.post("/api/v1/searches", json=_payload("pace")).json()
    resp = client.patch(
        f"/api/v1/searches/{created['id']}", json={"schedule_cron": EVERY_MINUTE}
    )
    assert resp.status_code == 422, resp.text
    assert isinstance(resp.json()["detail"], str)
    row = client.get(f"/api/v1/searches/{created['id']}").json()
    assert row["schedule_cron"] == HOURLY


async def test_unchanged_legacy_cron_is_kept(db_session, db_schema: str) -> None:
    search = await make_search(
        db_session, name="legacy", schedule_cron=EVERY_MINUTE, enabled=True
    )
    await db_session.commit()
    client = _client(db_schema)
    resp = client.patch(f"/api/v1/searches/{search.id}", json={"description": "kept"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["schedule_cron"] == EVERY_MINUTE
    assert resp.json()["description"] == "kept"


async def test_resending_the_same_cron_is_kept(db_session, db_schema: str) -> None:
    search = await make_search(
        db_session, name="legacy-same", schedule_cron=EVERY_MINUTE, enabled=True
    )
    await db_session.commit()
    resp = _client(db_schema).patch(
        f"/api/v1/searches/{search.id}", json={"schedule_cron": EVERY_MINUTE}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["schedule_cron"] == EVERY_MINUTE


async def test_timezone_only_change_does_not_recheck(db_session, db_schema: str) -> None:
    search = await make_search(
        db_session, name="legacy-tz", schedule_cron=EVERY_MINUTE, enabled=True
    )
    await db_session.commit()
    resp = _client(db_schema).patch(
        f"/api/v1/searches/{search.id}",
        json={"schedule_timezone": "America/New_York"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["schedule_cron"] == EVERY_MINUTE
    assert body["schedule_timezone"] == "America/New_York"


def test_invalid_cron_is_422(db_schema: str) -> None:
    resp = _client(db_schema).post(
        "/api/v1/searches",
        json=_payload("bogus", schedule_cron=HOURLY, schedule_timezone="Not/AZone"),
    )
    assert resp.status_code == 422, resp.text
    assert "not a valid cron expression" in resp.json()["detail"]


async def test_legacy_cron_can_change_to_a_legal_one(db_session, db_schema: str) -> None:
    search = await make_search(
        db_session, name="legacy-up", schedule_cron=EVERY_MINUTE, enabled=True
    )
    await db_session.commit()
    client = _client(db_schema)
    moved = client.patch(
        f"/api/v1/searches/{search.id}", json={"schedule_cron": HOURLY}
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["schedule_cron"] == HOURLY
    back = client.patch(
        f"/api/v1/searches/{search.id}", json={"schedule_cron": EVERY_MINUTE}
    )
    assert back.status_code == 422, back.text
    assert client.get(f"/api/v1/searches/{search.id}").json()["schedule_cron"] == HOURLY


# ---------------------------------------------------------------------- 422
def test_create_422_lists_aql_reasons(db_schema: str) -> None:
    reasons = _aql_reasons()
    resp = _client(db_schema).post(
        "/api/v1/searches", json=_payload("bad-aql", aql_query=BAD_AQL)
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reasons"] == reasons
    assert detail["message"] == "unsafe AQL: " + "; ".join(reasons)


def test_create_422_lists_the_threshold_reason(db_schema: str) -> None:
    sentence = unreachable_threshold_reason("GT", 20000, 100)
    assert sentence is not None
    resp = _client(db_schema).post(
        "/api/v1/searches",
        json=_payload(
            "bad-thr", threshold_operator="GT", threshold_value=20000, max_result_rows=100
        ),
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reasons"] == [sentence]
    assert detail["message"] == sentence


def test_create_422_lists_aql_and_threshold_reasons(db_schema: str) -> None:
    reasons = _aql_reasons()
    sentence = unreachable_threshold_reason("GT", 101, 100)
    assert sentence is not None
    resp = _client(db_schema).post(
        "/api/v1/searches",
        json=_payload(
            "both",
            aql_query=BAD_AQL,
            threshold_operator="GT",
            threshold_value=101,
            max_result_rows=100,
        ),
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reasons"] == [*reasons, sentence]
    assert detail["message"] == "unsafe AQL: " + "; ".join(reasons) + "; " + sentence
    assert not detail["reasons"][-1].startswith("unsafe AQL:")


def test_update_422_lists_the_effective_threshold_reason(db_schema: str) -> None:
    client = _client(db_schema)
    created = client.post(
        "/api/v1/searches",
        json=_payload("eff", threshold_operator="GT", threshold_value=100, max_result_rows=100),
    ).json()
    sentence = unreachable_threshold_reason("GT", 100, 99)
    resp = client.patch(f"/api/v1/searches/{created['id']}", json={"max_result_rows": 99})
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reasons"] == [sentence]
    assert detail["message"] == sentence
    assert client.get(f"/api/v1/searches/{created['id']}").json()["max_result_rows"] == 100


def test_update_422_lists_aql_and_threshold_reasons(db_schema: str) -> None:
    client = _client(db_schema)
    created = client.post(
        "/api/v1/searches",
        json=_payload("mix", threshold_operator="GT", threshold_value=100, max_result_rows=100),
    ).json()
    reasons = _aql_reasons()
    sentence = unreachable_threshold_reason("GT", 100, 99)
    assert sentence is not None
    resp = client.patch(
        f"/api/v1/searches/{created['id']}",
        json={"aql_query": BAD_AQL, "max_result_rows": 99},
    )
    assert resp.status_code == 422, resp.text
    detail = resp.json()["detail"]
    assert detail["reasons"] == [*reasons, sentence]
    row = client.get(f"/api/v1/searches/{created['id']}").json()
    assert row["aql_query"] == AQL
    assert row["max_result_rows"] == 100


def test_missing_search_with_unsafe_aql_is_422(db_schema: str) -> None:
    missing = uuid.uuid4()
    resp = _client(db_schema).patch(
        f"/api/v1/searches/{missing}", json={"aql_query": BAD_AQL}
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["reasons"] == _aql_reasons()


def test_schema_errors_stay_a_list(db_schema: str) -> None:
    client = _client(db_schema)
    missing = client.post(
        "/api/v1/searches",
        json={"aql_query": AQL, "schedule_cron": HOURLY},
    )
    assert missing.status_code == 422, missing.text
    assert isinstance(missing.json()["detail"], list)
    assert any("name" in err["loc"] for err in missing.json()["detail"])
    typed = client.post(
        "/api/v1/searches",
        json=_payload("typed", threshold_value="high"),
    )
    assert typed.status_code == 422, typed.text
    assert isinstance(typed.json()["detail"], list)
    assert any("threshold_value" in err["loc"] for err in typed.json()["detail"])


def test_reader_authoring_failure_is_422_not_403(db_schema: str) -> None:
    client = _client(db_schema, VIEWER)
    bad_aql = client.post("/api/v1/searches", json=_payload("reader-aql", aql_query=BAD_AQL))
    assert bad_aql.status_code == 422, bad_aql.text
    assert isinstance(bad_aql.json()["detail"], dict)
    unreachable = client.post(
        "/api/v1/searches",
        json=_payload(
            "reader-thr",
            threshold_operator="GT",
            threshold_value=101,
            max_result_rows=100,
        ),
    )
    assert unreachable.status_code == 422, unreachable.text
    assert "can never be reached" in unreachable.text


def test_reader_threshold_patch_is_still_403(db_schema: str) -> None:
    admin = _client(db_schema)
    created = admin.post(
        "/api/v1/searches",
        json=_payload(
            "reader-patch",
            threshold_value=100,
            threshold_operator="GT",
            max_result_rows=100,
        ),
    ).json()
    resp = _client(db_schema, VIEWER).patch(
        f"/api/v1/searches/{created['id']}", json={"max_result_rows": 99}
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "missing required permission: search:write"
    assert admin.get(f"/api/v1/searches/{created['id']}").json()["max_result_rows"] == 100


def test_reader_fast_cron_is_still_403(db_schema: str) -> None:
    resp = _client(db_schema, VIEWER).post(
        "/api/v1/searches", json=_payload("reader-fast", schedule_cron=EVERY_MINUTE)
    )
    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"] == "missing required permission: search:write"


# ---------------------------------------------------------------------- auth
def test_touched_routes_without_a_token_are_401(db_schema: str) -> None:
    client = _client(db_schema, principal=None, auth_provider="oidc")
    missing = str(uuid.uuid4())
    created = client.post("/api/v1/searches", json=_payload("no-token"))
    updated = client.patch(f"/api/v1/searches/{missing}", json={"description": "x"})
    assert created.status_code == 401, created.text
    assert updated.status_code == 401, updated.text
    assert created.json()["detail"] == "missing bearer token"
    assert updated.json()["detail"] == "missing bearer token"


def test_touched_routes_without_read_are_403(db_schema: str) -> None:
    client = _client(db_schema, NOBODY)
    missing = str(uuid.uuid4())
    created = client.post("/api/v1/searches", json=_payload("nobody"))
    updated = client.patch(f"/api/v1/searches/{missing}", json={"description": "x"})
    assert created.status_code == 403, created.text
    assert updated.status_code == 403, updated.text
    assert created.json()["detail"] == "missing required permission: read:searches"
    assert updated.json()["detail"] == "missing required permission: read:searches"


def test_reader_cannot_write_searches(db_schema: str) -> None:
    client = _client(db_schema, VIEWER)
    missing = str(uuid.uuid4())
    created = client.post("/api/v1/searches", json=_payload("viewer"))
    updated = client.patch(f"/api/v1/searches/{missing}", json={"description": "x"})
    assert created.status_code == 403, created.text
    assert updated.status_code == 403, updated.text
    assert created.json()["detail"] == "missing required permission: search:write"
    assert updated.json()["detail"] == "missing required permission: search:write"


# --------------------------------------------------------------------- audit
async def test_enable_and_disable_are_audited_apart_from_update(
    db_session, db_schema: str
) -> None:
    client = _client(db_schema)
    created = client.post("/api/v1/searches", json=_payload("audited")).json()
    search_id = created["id"]
    creates = await _audits(db_session, "search.create", search_id)
    assert len(creates) == 1
    assert creates[0].actor == "admin"
    assert creates[0].object_type == "scheduled_search"
    assert creates[0].detail == {"name": "audited"}

    described = client.patch(f"/api/v1/searches/{search_id}", json={"description": "note"})
    assert described.status_code == 200, described.text
    updates = await _audits(db_session, "search.update", search_id)
    assert len(updates) == 1
    assert updates[0].actor == "admin"
    assert updates[0].detail == {"version_before": 1, "version_after": 1}

    disabled = client.patch(f"/api/v1/searches/{search_id}", json={"enabled": False})
    assert disabled.status_code == 200, disabled.text
    disables = await _audits(db_session, "search.disable", search_id)
    assert len(disables) == 1
    assert disables[0].actor == "admin"
    assert disables[0].detail == {"before": True, "after": False}

    noted = client.patch(
        f"/api/v1/searches/{search_id}",
        json={"enabled": False, "change_note": "still just the flag"},
    )
    assert noted.status_code == 200, noted.text
    disables = await _audits(db_session, "search.disable", search_id)
    assert len(disables) == 2
    assert disables[1].detail == {"before": False, "after": False}

    mixed = client.patch(
        f"/api/v1/searches/{search_id}",
        json={"enabled": True, "description": "back, with a note"},
    )
    assert mixed.status_code == 200, mixed.text
    assert mixed.json()["enabled"] is True
    updates = await _audits(db_session, "search.update", search_id)
    assert len(updates) == 2
    assert updates[1].detail == {"version_before": 1, "version_after": 1}
    assert await _audits(db_session, "search.enable", search_id) == []

    enabled = client.patch(f"/api/v1/searches/{search_id}", json={"enabled": True})
    assert enabled.status_code == 200, enabled.text
    enables = await _audits(db_session, "search.enable", search_id)
    assert len(enables) == 1
    assert enables[0].actor == "admin"
    assert enables[0].detail == {"before": True, "after": True}
