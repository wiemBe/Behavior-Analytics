"""JDBC-1 X2a: ABSENT presentation in the JDBC contract, switch-gated (D-94/D-101).

With the switch off every JDBC statement and every legacy field value stays as at base;
only the new nullable `absent_since` field is added to the response shape. DB-gated; run
one file at a time per database.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import jdbc as jdbc_routes
from app.core.config import get_settings
from app.core.database import get_session
from app.jdbc import queries
from app.jdbc.flow import flow_state, target_bucket
from app.jdbc.queries import JdbcFilters, TargetWindow
from app.main import create_app
from app.models.feature_switch import FeatureSwitch
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig
from app.models.log_source import LogSource, LogSourceMetric
from app.models.log_source_pause import REASON_ABSENT, LogSourcePause
from app.repositories.jdbc import JdbcQueryError, JdbcRepository
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.feature_switches import invalidate_effective_settings

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="needs TEST_DATABASE_URL"),
]

VIEWER = Principal(subject="viewer", permissions=frozenset({"read:*"}))
NOW = datetime(2026, 11, 20, 12, 3, tzinfo=UTC)
WIDTH = CADENCE = 300
START, END = target_bucket(NOW, cadence_seconds=CADENCE, width_seconds=WIDTH, anchor=0)
ABSENT_START = NOW - timedelta(hours=2)
ABSENT_END = NOW - timedelta(minutes=30)
OBS_START = START - timedelta(seconds=3 * WIDTH)
OBS_AT = OBS_START + timedelta(seconds=30)
CFG_AT = NOW - timedelta(hours=3)
TIMING: dict[str, Any] = {
    "now": NOW, "cadence_seconds": CADENCE, "width_seconds": WIDTH, "anchor": 0,
    "inventory_cadence_seconds": 3600, "truncated_attempt": False,
}

#: Compiled text captured from the unmodified base commit (9bf26bc0) before any edit.
#: The default arguments are the switch-off path: no ABSENT term, no `absent_since`.
LEGACY_JDBC_SQL = {
    "count_default": "2e778ceb75f9e2216ccd13f4da73133b6cf4846966252fc21aad192dbf6aba45",
    "count_flow": "38a83b70828042ca448b4366ef66dc5319bd16b684bd2af12f0a6fe6c038e895",
    "page_default": "5888700dd529705a4e3608ea4a13400e29676910133e5ca71f623b767136e233",
    "page_flow": "765ee3e57e530ad012e04aebe0a7e3abbedf504da5837f72fbd73c32be9dbe9e",
    "last_obs": "61f5d3501beb9546529a77c8e706d2b6cc580a58d4d384eccf80bf99bea66b71",
    "run_state": "3ee5e4101ae7f2f0d26e1b1cd2cfd171017a4c0766dba44e42dc20ca73712880",
}
SID = uuid.UUID("11111111-1111-1111-1111-111111111111")


@pytest.fixture(autouse=True)
def _effective_settings_cache() -> Any:
    """The resolved-settings cache is process-wide; leave it invalidated for other tests."""
    invalidate_effective_settings()
    yield
    invalidate_effective_settings()


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _utc(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _metric(source: uuid.UUID, start: datetime, count: int, *, at: datetime) -> dict[str, Any]:
    return {
        "log_source_id": source, "bucket_start": start, "bucket_seconds": WIDTH,
        "event_count": count, "average_eps": count / WIDTH, "peak_eps": 0.0,
        "unknown_event_count": 0, "stored_event_count": 0, "distinct_qid_count": 0,
        "distinct_username_count": 0, "distinct_source_ip_count": 0,
        "collection_error_count": 0, "completeness": "COMPLETE",
        "collection_source": "ariel_aggregate", "query_provenance": {}, "collected_at": at,
    }


async def _seed(
    session: AsyncSession, *, absent_enabled: bool = True, absent_closed: bool = False,
    with_absent: bool = True,
) -> dict[str, str]:
    inst = QRadarInstance(name=f"q-{uuid.uuid4()}", console_host="qradar.lab.local")
    session.add(inst)
    await session.flush()
    present_on = LogSource(
        instance_id=inst.id, qradar_id=1, name="present-on-src", protocol_type_id=8,
        enabled=True, monitoring_enabled=True)
    present_off = LogSource(
        instance_id=inst.id, qradar_id=2, name="present-off-src", protocol_type_id=8,
        enabled=False, monitoring_enabled=True)
    absent = LogSource(
        instance_id=inst.id, qradar_id=3, name="absent-src", protocol_type_id=8,
        enabled=absent_enabled, monitoring_enabled=True)
    session.add_all([present_on, present_off, absent])
    await session.flush()
    # The absent source keeps its own dated stored snapshot, not a cleared config.
    session.add(JdbcSourceConfig(
        log_source_id=absent.id, protocol_type_id=8, database_type="Postgres",
        database_host="absent.lab.local", database_port=5432,
        field_states={"database_type": {"code": "SET", "reason": None}},
        allowlist_version=1, config_state="COMPLETE", observed_at=CFG_AT,
        last_success_at=CFG_AT))
    # A complete target row would otherwise say RECEIVING: absence must outrank it.
    rows = [_metric(present_on.id, START, 9, at=NOW), _metric(absent.id, START, 9, at=NOW),
            _metric(absent.id, OBS_START, 7, at=OBS_AT)]
    await session.execute(insert(LogSourceMetric), rows)
    if with_absent:
        session.add(LogSourcePause(
            log_source_id=absent.id, reason=REASON_ABSENT, starts_at=ABSENT_START,
            ends_at=ABSENT_END if absent_closed else None))
    await session.commit()
    return {"present_on": str(present_on.id), "present_off": str(present_off.id),
            "absent": str(absent.id)}


async def _seed_for_client(**kwargs: Any) -> dict[str, str]:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return await _seed(session, **kwargs)
    finally:
        await engine.dispose()


async def _switches(**rows: bool) -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add_all(FeatureSwitch(key=key, enabled=value, updated_at=NOW, updated_by="test")
                            for key, value in rows.items())
            await session.commit()
    finally:
        await engine.dispose()


async def _open_absent(source_id: str) -> None:
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add(LogSourcePause(
                log_source_id=uuid.UUID(source_id), reason=REASON_ABSENT,
                starts_at=ABSENT_START, ends_at=None))
            await session.commit()
    finally:
        await engine.dispose()


async def _fixed_timing(session: Any, settings: Any) -> dict[str, Any]:
    """A fixed target window makes the seeded complete bucket deterministic."""
    return dict(TIMING)


def _client(
    monkeypatch: pytest.MonkeyPatch, counter: list[str] | None = None,
) -> TestClient:
    monkeypatch.setattr(jdbc_routes, "_timing", _fixed_timing)
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    if counter is not None:
        event.listen(engine.sync_engine, "before_cursor_execute",
                     lambda c, cur, stmt, *a: counter.append(stmt))

    async def _session() -> Any:
        async with maker() as session:
            yield session
            await session.commit()

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: VIEWER
    invalidate_effective_settings()
    return TestClient(app, raise_server_exceptions=False)


def _row(page: dict[str, Any], source_id: str) -> dict[str, Any]:
    return next(item for item in page["items"] if item["id"] == source_id)


def test_default_omit_include_absent_and_historical_detail(
    db_schema: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _run(_seed_for_client())
    _run(_switches(qradar_source_pause_enabled=True))
    client = _client(monkeypatch)

    default = client.get("/api/v1/log-sources/jdbc").json()
    assert default["total"] == 2
    assert {item["id"] for item in default["items"]} == {ids["present_on"], ids["present_off"]}

    full = client.get("/api/v1/log-sources/jdbc?include_absent=true").json()
    assert full["total"] == 3
    absent = _row(full, ids["absent"])
    assert absent["flow"]["state"] == "ABSENT_FROM_INVENTORY"
    assert absent["volume"]["measurement"] == "NOT_COLLECTED"
    assert absent["volume"]["event_count"] is None
    assert absent["volume"]["average_eps"] is None
    assert _utc(absent["qradar"]["absent_since"]) == ABSENT_START
    assert _row(full, ids["present_on"])["qradar"]["absent_since"] is None
    assert _row(full, ids["present_off"])["qradar"]["absent_since"] is None
    # The stored disabled source keeps its legacy label; absence does not touch it.
    assert _row(full, ids["present_off"])["flow"]["state"] == "DISABLED_IN_QRADAR"

    detail = client.get(f"/api/v1/log-sources/{ids['absent']}/jdbc")
    assert detail.status_code == 200
    body = detail.json()
    assert body["flow"]["state"] == "ABSENT_FROM_INVENTORY"
    assert body["volume"]["event_count"] is None
    assert _utc(body["qradar"]["absent_since"]) == ABSENT_START
    seen = body["volume"]["last_observation"]
    assert seen is not None and _utc(seen["start"]) == START
    assert _utc(seen["observed_at"]) == NOW and seen["event_count"] == 9
    assert _utc(body["config"]["observed_at"]) == CFG_AT
    assert _utc(body["config"]["last_success_at"]) == CFG_AT
    assert body["config"]["safe_fields"]["database_host"] == "absent.lab.local"


def test_absent_precedence_over_stored_disabled(
    db_schema: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _run(_seed_for_client(absent_enabled=False))
    _run(_switches(qradar_source_pause_enabled=True))
    client = _client(monkeypatch)
    full = client.get("/api/v1/log-sources/jdbc?include_absent=true").json()
    absent = _row(full, ids["absent"])
    assert absent["qradar"]["enabled"] is False
    assert absent["flow"]["state"] == "ABSENT_FROM_INVENTORY"
    assert absent["volume"]["measurement"] == "NOT_COLLECTED"
    assert absent["volume"]["event_count"] is None
    detail = client.get(f"/api/v1/log-sources/{ids['absent']}/jdbc")
    assert detail.status_code == 200
    assert detail.json()["flow"]["state"] == "ABSENT_FROM_INVENTORY"


def test_closed_absent_span_is_present_again(
    db_schema: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _run(_seed_for_client(absent_closed=True))
    _run(_switches(qradar_source_pause_enabled=True))
    client = _client(monkeypatch)
    page = client.get("/api/v1/log-sources/jdbc").json()
    assert page["total"] == 3
    absent = _row(page, ids["absent"])
    assert absent["flow"]["state"] == "RECEIVING"  # the complete target row is judged again
    assert absent["volume"]["event_count"] == 9
    assert absent["qradar"]["absent_since"] is None
    assert client.get(f"/api/v1/log-sources/{ids['absent']}/jdbc").status_code == 200


def test_switch_off_ignores_absent_with_legacy_sql_and_state(
    db_schema: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _run(_seed_for_client(with_absent=False))
    _run(_switches(qradar_source_pause_enabled=False))
    seen: list[str] = []
    client = _client(monkeypatch, counter=seen)

    before = client.get("/api/v1/log-sources/jdbc").json()
    before_sql = list(seen)
    seen.clear()
    _run(_open_absent(ids["absent"]))
    invalidate_effective_settings()  # both requests resolve the same switches the same way
    after = client.get("/api/v1/log-sources/jdbc").json()
    assert seen == before_sql  # no ABSENT term, no extra statement, byte-for-byte SQL
    assert after == before
    absent = _row(after, ids["absent"])
    assert absent["flow"]["state"] == "RECEIVING"  # the ABSENT span is ignored entirely
    assert absent["volume"]["event_count"] == 9
    assert absent["qradar"]["absent_since"] is None
    detail = client.get(f"/api/v1/log-sources/{ids['absent']}/jdbc")
    assert detail.status_code == 200
    assert detail.json()["flow"]["state"] == "RECEIVING"
    assert detail.json()["qradar"]["absent_since"] is None
    # The new parameter is accepted and ignored when off. The new filter value is accepted
    # too, but with no ABSENT branch in the legacy CASE it can only match nothing.
    assert client.get("/api/v1/log-sources/jdbc?include_absent=true").json() == after
    assert client.get(
        "/api/v1/log-sources/jdbc?flow_state=ABSENT_FROM_INVENTORY").json()["total"] == 0


def test_switch_off_compiled_sql_matches_base_commit() -> None:
    """The six compiled statements are byte-identical to the unmodified base commit."""
    window = TargetWindow(START, END)
    cases = {
        "count_default": queries.count_query(window, JdbcFilters()),
        "count_flow": queries.count_query(window, JdbcFilters(flow_state="RECEIVING")),
        "page_default": queries.page_query(window, JdbcFilters(), limit=100, offset=0),
        "page_flow": queries.page_query(
            window, JdbcFilters(flow_state="RECEIVING"), limit=100, offset=0),
        "last_obs": queries.last_observation_query([SID], since=START - timedelta(hours=24)),
        "run_state": queries.run_state_query(SID),
    }
    assert {name: sha256(str(stmt.compile(compile_kwargs={"literal_binds": True})).encode())
            .hexdigest() for name, stmt in cases.items()} == LEGACY_JDBC_SQL


def test_absent_flow_state_filter_and_include_absent_validation(
    db_schema: str, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ids = _run(_seed_for_client())
    _run(_switches(qradar_source_pause_enabled=True))
    client = _client(monkeypatch)
    # With the default omission an ABSENT-only filter is contradictory: nothing matches.
    assert client.get("/api/v1/log-sources/jdbc?flow_state=ABSENT_FROM_INVENTORY").json()[
        "total"] == 0
    full = client.get(
        "/api/v1/log-sources/jdbc?flow_state=ABSENT_FROM_INVENTORY&include_absent=true").json()
    assert full["total"] == 1 and _row(full, ids["absent"])["flow"][
        "state"] == "ABSENT_FROM_INVENTORY"
    assert client.get("/api/v1/log-sources/jdbc?include_absent=maybe").status_code == 422
    repo = JdbcRepository(db_session, get_settings())
    with pytest.raises(JdbcQueryError, match="include_absent_invalid"):
        repo.bounds(include_absent="yes")  # type: ignore[arg-type]


async def test_statement_counts_with_an_absent_row(db_session: AsyncSession) -> None:
    ids = await _seed(db_session)
    settings = get_settings().model_copy(update={"qradar_source_pause_enabled": True})
    repo = JdbcRepository(db_session, settings)

    async def counted(call: Any) -> tuple[Any, list[str]]:
        seen: list[str] = []
        connection = await db_session.connection()

        def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
            seen.append(statement)

        event.listen(connection.sync_connection, "before_cursor_execute", record)
        try:
            result = await call
        finally:
            event.remove(connection.sync_connection, "before_cursor_execute", record)
        assert all(s.lstrip().upper().startswith("SELECT") for s in seen)
        return result, seen

    page, seen = await counted(repo.list(limit=1000, include_absent=True, **TIMING))
    assert len(seen) == 4 and page.total == 3
    absent_row = next(i for i in page.items if str(i.id) == ids["absent"])
    assert absent_row.flow.state == "ABSENT_FROM_INVENTORY"
    empty, seen = await counted(repo.list(offset=4, **TIMING))
    assert len(seen) == 3 and empty.items == []
    detail, seen = await counted(repo.detail(uuid.UUID(ids["absent"]), **TIMING))
    assert detail is not None and len(seen) <= 4
    assert detail.flow.state == "ABSENT_FROM_INVENTORY"


def test_absent_precedence_in_pure_projection() -> None:
    old = flow_state(enabled=False, monitored=True, target=(START, END), absent=True, count=9)
    assert (old.state, old.measurement, old.count) == (
        "ABSENT_FROM_INVENTORY", "NOT_COLLECTED", None)
    # Without absence the same facts are the legacy disabled judgment.
    legacy = flow_state(enabled=False, monitored=True, target=(START, END), count=9)
    assert (legacy.state, legacy.measurement, legacy.count) == (
        "DISABLED_IN_QRADAR", "NOT_COLLECTED", None)
