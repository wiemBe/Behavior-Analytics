"""JDBC-1 J1m3: the two read routes. Stored state only; no provider, queue or QRadar call.

DB-gated; run one file at a time per database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.feature_switch import FeatureSwitch
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig
from app.models.log_source import LogSource
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.feature_switches import invalidate_effective_settings, resolve

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="needs TEST_DATABASE_URL"),
]

VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
NOTHING = Principal(subject="nobody", permissions=frozenset())
CANARY = "CANARY-s3cr3t-9f2a"
FIXED_404 = "No JDBC block for this log source."
ROW_KEYS = {"id", "qradar_id", "name", "protocol_type_id", "protocol_name", "qradar", "config",
            "volume", "flow", "monitoring"}
PAGE_KEYS = {"items", "total", "limit", "offset", "snapshot"}
SNAPSHOT_KEYS = {"complete", "state", "stale", "last_complete_at", "consoles"}
CONFIG_KEYS = {"support", "state", "failure_code", "observed_at", "last_success_at", "stale",
               "safe_fields", "field_states"}
SAFE_KEYS = {
    "database_type", "database_host", "tls_hostname", "database_port", "database_name",
    "schema_name", "table_name", "select_list", "compare_field", "polling_interval_seconds",
    "start_time_local", "prepared_statements", "predefined_query", "eps_throttle",
    "source_label", "use_ssl", "allow_untrusted", "advanced_options"}


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


async def _seed() -> dict[str, str]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            inst = QRadarInstance(name=f"q-{uuid.uuid4()}", console_host="qradar.lab.local")
            s.add(inst)
            await s.flush()
            jdbc = LogSource(
                instance_id=inst.id, qradar_id=1, name="jdbc-src", protocol_type_id=8,
                enabled=True, description=CANARY, owner_email=f"{CANARY}@x.test",
                identifier=CANARY, maintenance_reason=CANARY)
            other = LogSource(
                instance_id=inst.id, qradar_id=2, name="syslog-src", protocol_type_id=1)
            unverified = LogSource(
                instance_id=inst.id, qradar_id=3, name="odbc-src", protocol_type_id=20)
            s.add_all([jdbc, other, unverified])
            await s.flush()
            s.add(JdbcSourceConfig(
                log_source_id=jdbc.id, protocol_type_id=8, database_type="Postgres",
                database_host="db.lab.local", database_port=1433, database_name="events",
                select_list="id,msg", compare_field="id", field_states={}, allowlist_version=1,
                config_state="COMPLETE", observed_at=datetime.now(UTC)))
            await s.commit()
            return {"jdbc": str(jdbc.id), "other": str(other.id), "odbc": str(unverified.id)}
    finally:
        await engine.dispose()


async def _switches(**rows: bool) -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            s.add_all(FeatureSwitch(key=k, enabled=v, updated_at=datetime.now(UTC),
                                    updated_by="test") for k, v in rows.items())
            await s.commit()
    finally:
        await engine.dispose()


async def _add_disabled_source() -> str:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            inst = await s.scalar(select(QRadarInstance).limit(1))
            assert inst is not None
            off = LogSource(instance_id=inst.id, qradar_id=9, name="off-src",
                            protocol_type_id=8, enabled=False)
            s.add(off)
            await s.commit()
            return str(off.id)
    finally:
        await engine.dispose()


def _client(
    principal: Principal | None = VIEWER, counter: list[str] | None = None,
    **settings: Any,
) -> TestClient:
    # NullPool: every request runs in its own event loop, so no connection may be reused.
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    if counter is not None:
        event.listen(engine.sync_engine, "before_cursor_execute",
                     lambda c, cur, stmt, *a: counter.append(stmt))

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as s:
            yield s
            await s.commit()

    app = create_app()
    app.dependency_overrides[get_session] = _session
    if principal is not None:
        app.dependency_overrides[get_principal] = lambda: principal
    if settings:
        override = get_settings().model_copy(update=settings)
        app.dependency_overrides[get_settings] = lambda: override
    invalidate_effective_settings()
    return TestClient(app, raise_server_exceptions=False)


def test_401_403_404_static_route_order(db_schema: str) -> None:
    ids = _run(_seed())
    for path in ("/api/v1/log-sources/jdbc", f"/api/v1/log-sources/{ids['jdbc']}/jdbc"):
        assert _client(None, auth_provider="oidc").get(path).status_code == 401
        denied = _client(NOTHING).get(path)
        assert denied.status_code == 403 and CANARY not in denied.text
    client = _client()
    listed = client.get("/api/v1/log-sources/jdbc")
    assert listed.status_code == 200 and set(listed.json()) == PAGE_KEYS  # not read as an id
    assert listed.json()["total"] == 2  # protocols 8 and 20; the syslog source is not listed
    assert client.get(f"/api/v1/log-sources/{ids['jdbc']}/jdbc").status_code == 200
    for missing in (ids["other"], str(uuid.uuid4())):
        resp = client.get(f"/api/v1/log-sources/{missing}/jdbc")
        assert (resp.status_code, resp.json()["detail"]) == (404, FIXED_404)


def test_no_provider_or_queue_on_get(db_schema: str, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.providers.factory as factory
    from app.workers.celery_app import celery_app

    ids = _run(_seed())

    def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("a GET reached a provider or the queue")

    for name in ("build_provider", "build_provider_for_instance", "get_provider"):
        monkeypatch.setattr(factory, name, boom)
    monkeypatch.setattr(celery_app, "send_task", boom)
    monkeypatch.setattr(celery_app.__class__, "send_task", boom)
    client = _client()
    assert client.get("/api/v1/log-sources/jdbc").status_code == 200
    assert client.get(f"/api/v1/log-sources/{ids['jdbc']}/jdbc").status_code == 200


def test_safe_explicit_response_canaries_absent(db_schema: str) -> None:
    ids = _run(_seed())
    client = _client()
    page = client.get("/api/v1/log-sources/jdbc")
    detail = client.get(f"/api/v1/log-sources/{ids['jdbc']}/jdbc")
    assert CANARY not in page.text and CANARY not in detail.text
    body = detail.json()
    assert set(body) == ROW_KEYS and set(body["config"]) == CONFIG_KEYS
    assert set(body["config"]["safe_fields"]) == SAFE_KEYS
    assert body["config"]["safe_fields"]["database_host"] == "db.lab.local"
    assert set(page.json()["snapshot"]) == SNAPSHOT_KEYS
    assert all(set(item) == ROW_KEYS for item in page.json()["items"])
    unverified = client.get(f"/api/v1/log-sources/{ids['odbc']}/jdbc").json()
    assert unverified["config"]["safe_fields"] is None  # never {}


def test_d52_off_view_still_works(db_schema: str) -> None:
    ids = _run(_seed())
    # The DB rows win over a true environment value: both switches resolve to off.
    _run(_switches(qradar_source_pause_enabled=False, jdbc_collection_enabled=False))
    client = _client(qradar_source_pause_enabled=True, jdbc_collection_enabled=True)
    page = client.get("/api/v1/log-sources/jdbc").json()
    assert page["total"] == 2 and page["snapshot"]["complete"] is False
    assert page["snapshot"]["state"] == "NOT_COLLECTED"
    states = {i["id"]: i for i in page["items"]}
    assert states[ids["jdbc"]]["config"]["state"] == "COMPLETE"  # what is stored is shown
    assert states[ids["odbc"]]["config"]["support"] == "UNVERIFIED_PROTOCOL"
    assert {i["volume"]["measurement"] for i in states.values()} == {"NOT_COLLECTED"}
    assert client.get(f"/api/v1/log-sources/{ids['jdbc']}/jdbc").status_code == 200


def test_d52_on_still_lists_stored_disabled_source(db_schema: str) -> None:
    _run(_seed())
    _run(_switches(qradar_source_pause_enabled=True, jdbc_collection_enabled=False))
    off_id = _run(_add_disabled_source())
    client = _client()
    page = client.get("/api/v1/log-sources/jdbc")
    assert page.status_code == 200 and page.json()["total"] == 3
    row = next(i for i in page.json()["items"] if i["id"] == off_id)
    assert row["qradar"]["enabled"] is False
    assert row["flow"]["state"] == "DISABLED_IN_QRADAR"
    assert client.get(f"/api/v1/log-sources/{off_id}/jdbc").status_code == 200


@pytest.mark.parametrize("query", [
    "limit=abc", "limit=", "offset=-1", "offset=x", "q=" + "a" * 201, "protocol=1",
    "protocol=9999", "enabled=maybe", "instance_id=not-a-uuid", "flow_state=BOGUS",
])
def test_bounds_422_without_sql(db_schema: str, query: str) -> None:
    seen: list[str] = []
    client = _client(counter=seen)
    seen.clear()
    resp = client.get(f"/api/v1/log-sources/jdbc?{query}")
    assert resp.status_code == 422
    assert not any("log_source" in s or "search_cadence" in s for s in seen)
    assert "SELECT" not in resp.text.upper().replace("DETAIL", "")


def test_bounds_fixed_code_for_repository_errors(db_schema: str) -> None:
    resp = _client().get("/api/v1/log-sources/jdbc?protocol=1")
    assert resp.status_code == 422 and resp.json()["detail"] == "protocol_unknown"
    resp = _client().get("/api/v1/log-sources/jdbc?flow_state=" + CANARY)
    assert resp.json()["detail"] == "flow_state_unknown" and CANARY not in resp.text


async def _corrupt_config(value: str) -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as s:
            await s.execute(update(JdbcSourceConfig.__table__).values(database_type=value))
            await s.commit()
    finally:
        await engine.dispose()


def test_bad_stored_value_still_answers_200(db_schema: str) -> None:
    ids = _run(_seed())
    bad_value = "BADVALUE-SHOULD-NOT-APPEAR"
    _run(_corrupt_config(bad_value))
    client = _client()
    listed = client.get("/api/v1/log-sources/jdbc")
    assert listed.status_code == 200
    assert listed.json()["total"] == 2
    bad = next(i for i in listed.json()["items"] if i["id"] == ids["jdbc"])
    assert bad["config"]["safe_fields"]["database_type"] is None
    assert bad["config"]["field_states"]["database_type"] == {
        "code": "UNAVAILABLE", "reason": "unsupported_value"}
    assert (bad["config"]["state"], bad["config"]["stale"]) == ("PARTIAL", True)
    detail = client.get(f"/api/v1/log-sources/{ids['jdbc']}/jdbc")
    assert detail.status_code == 200
    assert detail.json()["config"] == bad["config"]
    assert bad_value not in listed.text and bad_value not in detail.text


def test_statement_budget_list_and_detail(db_schema: str) -> None:
    ids = _run(_seed())

    async def resolve_cost() -> int:
        engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True)
        seen: list[str] = []
        event.listen(engine.sync_engine, "before_cursor_execute",
                     lambda c, cur, stmt, *a: seen.append(stmt))
        try:
            async with AsyncSession(engine) as s:
                await resolve(s, get_settings())
        finally:
            await engine.dispose()
        return len(seen)

    switches = _run(resolve_cost())
    seen: list[str] = []
    client = _client(counter=seen)
    seen.clear()
    assert client.get("/api/v1/log-sources/jdbc").status_code == 200
    # four data SELECTs + one cadence read + the effective-settings resolution
    assert len(seen) == 4 + 1 + switches
    invalidate_effective_settings()
    seen.clear()
    assert client.get(f"/api/v1/log-sources/{ids['jdbc']}/jdbc").status_code == 200
    assert len(seen) == 3 + 1 + switches  # detail: three data SELECTs on this data
