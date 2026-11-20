"""The operator path: permissions, transactions, stored evidence and offline commands."""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import event, func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.ariel_search import ArielSearchRun
from app.models.feature_switch import FeatureSwitch
from app.models.identity import AuditLog
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.monitoring import QRadarAccessResult
from app.models.search_cadence import SearchCadenceSetting
from app.models.waf import WafSourceProfile
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services import feature_management as writes
from app.services.feature_overview import overview, prerequisites
from app.services.feature_switches import (
    ALERTING_REQUIRES,
    FEATURE_KEYS,
    ResolvedFeatures,
    ResolvedSwitch,
)

pytestmark = pytest.mark.integration
URL = "/api/v1/admin/features"


@pytest_asyncio.fixture
async def api(db_schema):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app()
    base = Settings(_env_file=None).model_copy(update=dict.fromkeys(FEATURE_KEYS, False))
    principal = Principal("operator", permissions=frozenset({"admin:*"}))

    async def sessions():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app.dependency_overrides[get_session] = sessions
    app.dependency_overrides[get_settings] = lambda: base
    app.dependency_overrides[get_principal] = lambda: principal
    limiter.reset()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app), base_url="http://test"
    ) as client:
        yield client, app, base
    limiter.reset()
    await engine.dispose()


async def test_roundtrip_and_audit(api, db_session):
    client, _, _ = api
    key = "geo_collection_enabled"
    for _ in range(2):
        assert (await client.put(f"{URL}/{key}", json={"enabled": True})).status_code == 204
    body = (await client.get(URL)).json()
    assert body["runtime_switches_active"] is True
    assert body["worker_switches_active"] is True
    geo = next(r for r in body["families"] if r["family"] == "geo")
    assert geo["switches"][key]["source"] == "db"
    assert geo["switches"][key]["updated_by"] == "operator"
    assert geo["switches"][key]["updated_at"]
    logs = (await db_session.scalars(select(AuditLog))).all()
    assert len(logs) == 1
    assert logs[0].action == "feature_switch.update"
    assert logs[0].actor == "operator"
    assert logs[0].object_type == "feature_switch"
    assert logs[0].object_id == key
    assert logs[0].source_ip == "127.0.0.1"
    assert logs[0].detail == {"old": False, "new": True, "source": "env"}
    for _ in range(2):
        assert (await client.delete(f"{URL}/{key}")).status_code == 204
    db_session.expire_all()
    logs = (await db_session.scalars(select(AuditLog).order_by(AuditLog.created_at))).all()
    assert len(logs) == 2
    assert logs[1].action == "feature_switch.reset"
    assert logs[1].detail == {"old": True, "new": False, "source": "db"}
    assert await db_session.get(FeatureSwitch, key) is None


@pytest.mark.parametrize("key,required", ALERTING_REQUIRES.items())
async def test_dependencies_and_inert(api, db_session, key, required):
    client, _, _ = api
    response = await client.put(f"{URL}/{key}", json={"enabled": True})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "feature_switch_dependency"
    assert (await client.put(f"{URL}/{required}", json={"enabled": True})).status_code == 204
    assert (await client.put(f"{URL}/{key}", json={"enabled": True})).status_code == 204
    assert (await client.put(f"{URL}/{required}", json={"enabled": False})).status_code == 204
    rows = (await client.get(URL)).json()["families"]
    switch = next(r["switches"][key] for r in rows if key in r["switches"])
    assert switch["enabled"] and switch["inert"]
    # Enabling a fallback value through reset is subject to the same guards.
    _, _, base = api
    setattr(base, key, True)
    assert (await client.delete(f"{URL}/{key}")).status_code == 422


@pytest.mark.parametrize("key,updates,field", [
    ("waf_collection_enabled", {"waf_retention_enabled": True, "waf_metric_retention_days": 1},
     "WAF_METRIC_RETENTION_DAYS"),
    ("ips_collection_enabled", {"ips_retention_enabled": True, "ips_metric_retention_days": 1},
     "IPS_METRIC_RETENTION_DAYS"),
    ("rule_match_collection_enabled", {"rule_match_repair_enabled": True,
                                      "rule_match_repair_max_age_seconds": 1},
     "RULE_MATCH_REPAIR_MAX_AGE_SECONDS"),
    ("waf_collection_enabled", {"waf_new_endpoint_history_days": 9999},
     "WAF_NEW_ENDPOINT_HISTORY_DAYS"),
    ("pw_collection_enabled", {"pw_max_rows_per_search": 9999999}, "PW_MAX_ROWS_PER_SEARCH"),
])
async def test_settings_validation(api, db_session, key, updates, field):
    client, app, base = api
    app.dependency_overrides[get_settings] = lambda: base.model_copy(update=updates)
    response = await client.put(f"{URL}/{key}", json={"enabled": True})
    assert response.status_code == 422, response.text
    assert response.json()["detail"]["code"] == "feature_switch_settings_invalid"
    assert field in response.text
    assert await db_session.scalar(select(func.count()).select_from(FeatureSwitch)) == 0
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 0


async def test_no_row_no_audit_and_strict_payload(api, db_session):
    client, _, _ = api
    assert (await client.delete(f"{URL}/geo_collection_enabled")).status_code == 204
    response = await client.put(f"{URL}/geo_collection_enabled", json={"enabled": False})
    assert response.status_code == 204
    for payload in ({"enabled": "false"}, {"enabled": 0}, {}, {"enabled": True, "extra": True}):
        assert (await client.put(f"{URL}/geo_collection_enabled", json=payload)).status_code == 422
    for method in ("PUT", "DELETE"):
        response = await client.request(method, f"{URL}/unknown", **(
            {"json": {"enabled": True}} if method == "PUT" else {}))
        assert response.status_code == 404
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 0
    assert await db_session.scalar(select(func.count()).select_from(FeatureSwitch)) == 0


async def test_permissions_and_access_redaction(api):
    client, app, _ = api
    app.dependency_overrides[get_principal] = lambda: Principal("none")
    denied = await client.get(URL)
    assert denied.status_code == 403 and "read:features" in denied.text
    app.dependency_overrides[get_principal] = lambda: Principal(
        "reader", permissions=frozenset({"read:*"}))
    rows = (await client.get(URL)).json()["families"]
    assert all(r["access"] is None for r in rows)
    for method in ("PUT", "DELETE"):
        denied = await client.request(method, f"{URL}/geo_collection_enabled", **(
            {"json": {"enabled": True}} if method == "PUT" else {}))
        assert denied.status_code == 403 and "admin:features" in denied.text
    app.dependency_overrides.pop(get_principal)
    app.dependency_overrides[get_settings] = lambda: Settings(_env_file=None, auth_provider="oidc")
    assert (await client.get(URL)).status_code == 401


async def test_resolver_failure(api, monkeypatch):
    client, _, _ = api

    async def failed(*args):
        return ResolvedFeatures(dict.fromkeys(FEATURE_KEYS, ResolvedSwitch(False, "unreadable")),
                                reason="feature_switch_unreadable")

    monkeypatch.setattr("app.services.feature_overview.resolve", failed)
    monkeypatch.setattr(writes, "resolve", failed)
    body = (await client.get(URL)).json()
    assert body["reason"] == "feature_switch_unreadable"
    assert all(not switch["enabled"] for r in body["families"] for switch in r["switches"].values())
    for method in ("PUT", "DELETE"):
        response = await client.request(method, f"{URL}/geo_collection_enabled", **(
            {"json": {"enabled": True}} if method == "PUT" else {}))
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "feature_switch_unreadable"


@pytest.mark.parametrize("method", ["PUT", "DELETE"])
async def test_rate_limit(api, monkeypatch, method):
    client, _, _ = api
    settings = get_settings().model_copy(update={"rate_limit_default": "1/minute"})
    monkeypatch.setattr("app.core.rate_limit.get_settings", lambda: settings)
    kwargs = {"json": {"enabled": False}} if method == "PUT" else {}
    first = await client.request(method, f"{URL}/geo_collection_enabled", **kwargs)
    assert first.status_code == 204
    second = await client.request(method, f"{URL}/geo_collection_enabled", **kwargs)
    assert second.status_code == 429


async def test_stored_evidence(api, db_session):
    client, app, _ = api
    console = QRadarInstance(name="fixture", console_host="fixture.invalid", enabled=True)
    db_session.add(console)
    await db_session.flush()
    stamp = datetime.now(UTC)
    db_session.add_all([
        SearchCadenceSetting(family="QRADAR_HEALTH", cadence_seconds=900, updated_at=stamp,
                             last_run_at=stamp, last_run_outcome="ok", last_run_searches=2),
        QRadarAccessResult(instance_id=console.id, entry_id="ariel_searches", status="ok",
                           checked_at=stamp, syncs_started=[]),
        QRadarAccessResult(instance_id=console.id, entry_id="system_about", status="forbidden",
                           checked_at=stamp, syncs_started=[]),
    ])
    for caller, age, outcome in [("QRADAR_HEALTH", 5, "COMPLETED"),
                                  ("QRADAR_HEALTH", 20, "COMPLETED"),
                                  ("QRADAR_HEALTH", -5, "COMPLETED"),
                                  ("QRADAR_HEALTH", 10, "REFUSED"),
                                  ("QRADAR_HEALTH", 70, "COMPLETED"),
                                  ("WAF_COLLECTION", 5, "COMPLETED")]:
        db_session.add(ArielSearchRun(instance_id=console.id, caller=caller,
                                     submitted_at=stamp - timedelta(minutes=age),
                                     polls=0, outcome=outcome))
    await db_session.commit()
    rows = {r["family"]: r for r in (await client.get(URL)).json()["families"]}
    health = rows["qradar_health"]
    assert health["estimate"]["upper_bound_searches_per_hour"] == 8
    assert health["estimate"]["count"] == 1
    assert health["measured_last_hour"] == 2
    assert health["last_run"]["searches"] == 2
    statuses = {e["id"]: e["status"] for e in health["access"][0]["entries"]}
    assert statuses["ariel_searches"] == "ok"
    assert statuses["system_about"] == "forbidden"
    assert statuses["managed_servers"] is None
    assert rows["waf"]["measured_last_hour"] == 1
    assert rows["geo"]["measured_last_hour"] is None
    assert rows["geo"]["estimate"]["upper_bound_searches_per_hour"] is None
    assert rows["geo"]["estimate"]["reason"] == "NOT_COLLECTED"
    # Scoped administrators cannot read the token-access column (design section 3.7).
    app.dependency_overrides[get_principal] = lambda: Principal(
        "wafadmin", permissions=frozenset({"read:features", "admin:waf"}))
    assert (await client.get(URL)).json()["families"][0]["access"] is None


async def test_prerequisites_constant_statements(db_session):
    console = QRadarInstance(name="counts", console_host="fixture.invalid", enabled=True)
    db_session.add(console)
    await db_session.flush()
    statements = []
    engine = db_session.bind.sync_engine

    def record(*args):
        statements.append(args[2])

    async def add_profile(i):
        source = LogSource(instance_id=console.id, qradar_id=i, name=f"source {i}")
        db_session.add(source)
        await db_session.flush()
        db_session.add(WafSourceProfile(instance_id=console.id, log_source_id=source.id,
                                       enabled=True, profile_version=1, validation_status="VALID",
                                       url_property="URL", method_property="METHOD",
                                       validated_profile_version=1))
        db_session.add(IpsSourceProfile(instance_id=console.id, log_source_id=source.id,
                                       enabled=True, validation_status="VALID",
                                       validated_profile_version=1))
        db_session.add(IdentitySourceProfile(instance_id=console.id, log_source_id=source.id,
                                            enabled=True, validation_status="VALID",
                                            event_id_property="EventID",
                                            username_property="username",
                                            validated_profile_version=1))
        await db_session.flush()

    await add_profile(1)
    event.listen(engine, "before_cursor_execute", record)
    try:
        counts = await prerequisites(db_session)
        for family in ("waf", "ips", "identity"):
            assert counts[family] == {"enabled": 1, "validated": 1, "total": 1}
        first = len(statements)
        for i in range(2, 51):
            await add_profile(i)
        statements.clear()
        counts = await prerequisites(db_session)
        for family in ("waf", "ips", "identity"):
            assert counts[family] == {"enabled": 50, "validated": 50, "total": 50}
        assert len(statements) == first == 5
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_overview_constant_statements(db_session):
    console = QRadarInstance(name="counts", console_host="fixture.invalid", enabled=True)
    db_session.add(console)
    await db_session.flush()
    statements = []
    engine = db_session.bind.sync_engine

    def record(*args):
        statements.append(args[2])

    async def add_profile(i):
        source = LogSource(instance_id=console.id, qradar_id=i, name=f"source {i}")
        db_session.add(source)
        await db_session.flush()
        db_session.add(WafSourceProfile(instance_id=console.id, log_source_id=source.id,
                                       enabled=True, profile_version=1, validation_status="VALID",
                                       url_property="URL", method_property="METHOD",
                                       validated_profile_version=1))
        db_session.add(IpsSourceProfile(instance_id=console.id, log_source_id=source.id,
                                       enabled=True, validation_status="VALID",
                                       validated_profile_version=1))
        db_session.add(IdentitySourceProfile(instance_id=console.id, log_source_id=source.id,
                                             enabled=True, validation_status="VALID",
                                             event_id_property="EventID",
                                             username_property="username",
                                             validated_profile_version=1))
        await db_session.flush()

    await add_profile(1)
    base = Settings(_env_file=None).model_copy(update=dict.fromkeys(FEATURE_KEYS, False))
    principal = Principal("counts", permissions=frozenset({"admin:*"}))
    event.listen(engine, "before_cursor_execute", record)
    try:
        body = await overview(db_session, base, principal)
        counts = next(r["prerequisites"] for r in body["families"] if r["family"] == "waf")
        assert counts == {"enabled": 1, "validated": 1, "total": 1}
        first = len(statements)
        for i in range(2, 51):
            await add_profile(i)
        statements.clear()
        body = await overview(db_session, base, principal)
        counts = next(r["prerequisites"] for r in body["families"] if r["family"] == "waf")
        assert counts == {"enabled": 50, "validated": 50, "total": 50}
        # The whole GET path, not just prerequisites(), stays at a constant statement count.
        assert len(statements) == first
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_cli_commands(db_schema, db_session, tmp_path):
    database = make_url(db_schema)
    env = {**os.environ, "FEATURE_SWITCHES_DB": "true",
           "POSTGRES_HOST": database.host, "POSTGRES_PORT": str(database.port or 5432),
           "POSTGRES_USER": database.username, "POSTGRES_PASSWORD": database.password or "",
           "POSTGRES_DB": database.database}
    for key in FEATURE_KEYS:
        env[key.upper()] = "false"
    env["TMPDIR"] = str(tmp_path)

    def command(*args, code=0):
        result = subprocess.run(  # noqa: S603 — fixed module and test-owned arguments
            [sys.executable, "-m", "app.cli.features", *args],
                                env=env, capture_output=True, text=True, check=False)
        assert result.returncode == code, result.stdout + result.stderr
        return result.stdout

    assert "geo_collection_enabled=off" in command("show")
    command("set", "geo_collection_enabled", "on")
    assert "geo_collection_enabled=on" in command("show")
    lines = command("env").splitlines()
    assert len(lines) == len(FEATURE_KEYS)
    assert dict(line.split("=", 1) for line in lines)["GEO_COLLECTION_ENABLED"] == "true"
    command("apply", "ips_collection_enabled=on", "wrong=on", code=2)
    command("apply", "ips_collection_enabled=on", "geo_collection_enabled=oops", code=2)
    assert "ips_collection_enabled=off" in command("show")
    command("apply", "ips_collection_enabled=on", "ips_alerting_enabled=on")
    command("set", "unknown", "on", code=2)
    command("set", "geo_collection_enabled", "maybe", code=2)
    command("set", "waf_alerting_enabled", "on", code=2)
    command("reset", "geo_collection_enabled")
    command("reset", "geo_collection_enabled")
    command("show", "extra", code=2)
    audits = (await db_session.scalars(select(AuditLog))).all()
    assert len(audits) == 4
    assert all(row.actor == "cli" for row in audits)
    bad_env = {**env, "POSTGRES_PORT": "1", "POSTGRES_HOST": "127.0.0.1"}
    for args in [("show",), ("env",), ("set", "geo_collection_enabled", "on"),
                 ("reset", "geo_collection_enabled"), ("apply", "geo_collection_enabled=on")]:
        result = subprocess.run(  # noqa: S603 — fixed module and test-owned arguments
            [sys.executable, "-m", "app.cli.features", *args],
                                env=bad_env, capture_output=True, text=True, check=False)
        assert result.returncode == 1
        assert "invalid:invalid" not in result.stderr


async def test_missing_table_fails_closed(api, db_session):
    from sqlalchemy import text

    client, _, _ = api
    await db_session.execute(text("DROP TABLE feature_switch"))
    await db_session.commit()
    response = await client.get(URL)
    assert response.status_code == 200
    body = response.json()
    assert body["reason"] == "feature_switch_unreadable"
    assert all(not switch["enabled"] for row in body["families"]
               for switch in row["switches"].values())
    for method in ("PUT", "DELETE"):
        response = await client.request(method, f"{URL}/geo_collection_enabled", **(
            {"json": {"enabled": True}} if method == "PUT" else {}))
        assert response.status_code == 503
    assert await db_session.scalar(select(func.count()).select_from(AuditLog)) == 0
