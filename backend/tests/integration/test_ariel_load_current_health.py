"""ARL-3 round 1: QRadar's health in `/ariel-load/current`, against real rows."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import ProviderKind
from app.core.database import get_session
from app.main import create_app
from app.models.ariel_search import ArielSearchRun
from app.models.instance import QRadarInstance
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services.ariel_load_current import load_current_with_health, read_ariel_runs
from app.services.ariel_search_log import stale_after_seconds
from app.services.qradar_health import HEALTH_CACHE, REASONS

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))


@pytest.fixture(autouse=True)
def _fresh_cache() -> None:
    HEALTH_CACHE.clear()


def _console(name: str, *, kind: ProviderKind = ProviderKind.MOCK, **kw: object) -> QRadarInstance:
    return QRadarInstance(
        id=uuid.uuid4(),
        name=name,
        console_host=f"https://{name}.example.internal",
        api_version="20.0",
        sec_token="integration-token-never-in-a-response",
        verify_ssl=True,
        provider_kind=kind.value,
        **kw,
    )


def _run(
    instance_id: uuid.UUID | None,
    *,
    outcome: str,
    submitted: datetime,
    finished: datetime | None = None,
) -> ArielSearchRun:
    terminal = outcome != "IN_FLIGHT"
    return ArielSearchRun(
        instance_id=instance_id,
        caller="WAF_COLLECTION",
        submitted_at=submitted,
        finished_at=(finished or submitted + timedelta(seconds=1)) if terminal else None,
        wall_ms=1000 if terminal else None,
        polls=1,
        outcome=outcome,
    )


async def _seed(url: str, rows: list[object]) -> None:
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add_all(rows)
            await session.commit()
    finally:
        await engine.dispose()


async def test_the_search_record_is_one_grouped_read_per_console(db_schema: str) -> None:
    now = datetime.now(UTC)
    lab, east, quiet = _console("lab"), _console("east"), _console("quiet")
    alive = now - timedelta(seconds=30)
    lost = now - timedelta(seconds=stale_after_seconds() + 60)
    await _seed(
        db_schema,
        [
            lab,
            east,
            quiet,
            # lab: 3 reached QRadar in the window, one of which found Ariel down; a refusal that
            # never reached it; an old row outside the window; one active and one lost search.
            _run(lab.id, outcome="COMPLETED", submitted=now - timedelta(minutes=5)),
            _run(lab.id, outcome="COMPLETED", submitted=now - timedelta(minutes=2)),
            _run(
                lab.id,
                outcome="PROVIDER_UNAVAILABLE",
                submitted=now - timedelta(minutes=3),
                finished=now - timedelta(minutes=3),
            ),
            _run(lab.id, outcome="REFUSED", submitted=now - timedelta(minutes=1)),
            _run(lab.id, outcome="COMPLETED", submitted=now - timedelta(minutes=40)),
            _run(lab.id, outcome="IN_FLIGHT", submitted=alive),
            _run(lab.id, outcome="IN_FLIGHT", submitted=lost),
            # east: only failures; another console's rows never leak in.
            _run(east.id, outcome="PROVIDER_UNAVAILABLE", submitted=now - timedelta(minutes=1)),
            # no console at all (a mock-run or refused row)
            _run(None, outcome="COMPLETED", submitted=now - timedelta(minutes=1)),
        ],
    )
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    statements: list[str] = []
    event.listen(
        engine.sync_engine, "before_cursor_execute", lambda *a: statements.append(a[2])
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            runs = await read_ariel_runs(session, [lab.id, east.id, quiet.id], as_of=now)
    finally:
        await engine.dispose()
    assert len(statements) == 1, "one statement for all consoles"

    lab_runs = runs[lab.id]
    assert (lab_runs.window_minutes, lab_runs.searches, lab_runs.unavailable) == (15, 4, 1)
    assert lab_runs.in_flight == 1, "the lost search is not counted as in flight"
    assert lab_runs.last_success_at is not None
    assert abs((lab_runs.last_success_at - (now - timedelta(minutes=2) + timedelta(seconds=1)))
               .total_seconds()) < 1
    assert lab_runs.last_unavailable_at is not None
    east_runs = runs[east.id]
    assert (east_runs.searches, east_runs.unavailable, east_runs.last_success_at) == (1, 1, None)
    # A console with no row: the query ran and found none, so zeros are the measurement.
    quiet_runs = runs[quiet.id]
    assert (quiet_runs.searches, quiet_runs.unavailable, quiet_runs.in_flight) == (0, 0, 0)
    assert await _no_rows_for_empty_input(db_schema) == {}


async def _no_rows_for_empty_input(url: str) -> dict[uuid.UUID, object]:
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            return dict(await read_ariel_runs(session, [], as_of=datetime.now(UTC)))
    finally:
        await engine.dispose()


async def test_the_database_cost_of_current_is_fixed_however_many_consoles(
    db_schema: str,
) -> None:
    async def count(consoles: int) -> int:
        HEALTH_CACHE.clear()
        engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
        statements: list[str] = []
        event.listen(
            engine.sync_engine, "before_cursor_execute", lambda *a: statements.append(a[2])
        )
        try:
            async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                body = await load_current_with_health(session)
        finally:
            await engine.dispose()
        assert len(body["qradar"]["consoles"]) == consoles
        return len(statements)

    # Three of load_current (in flight, last hour, permits) and the console list; with no
    # console there is nothing to ask the search record about.
    assert await count(0) == 4
    await _seed(db_schema, [_console("c0")])
    # With consoles: plus one grouped read of the search record, for all of them.
    assert await count(1) == 5
    await _seed(db_schema, [_console("c1"), _console("c2")])
    assert await count(3) == 5


def _client(principal: Principal) -> TestClient:
    assert TEST_DB_URL is not None
    engine = create_async_engine(TEST_DB_URL, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session() -> AsyncIterator[AsyncSession]:
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


async def test_the_route_returns_a_qradar_section_beside_the_existing_parts(
    db_schema: str,
) -> None:
    now = datetime.now(UTC)
    lab = _console("lab")
    off = _console("off", enabled=False)
    await _seed(
        db_schema,
        [
            lab,
            off,
            _run(lab.id, outcome="COMPLETED", submitted=now - timedelta(minutes=2)),
            _run(lab.id, outcome="IN_FLIGHT", submitted=now - timedelta(seconds=10)),
        ],
    )
    assert _client(READER).get("/api/v1/admin/ariel-load/current").status_code == 403
    response = _client(ADMIN).get("/api/v1/admin/ariel-load/current")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"as_of", "in_flight", "windows", "slots", "qradar"}
    assert body["in_flight"]["available"] is True and len(body["in_flight"]["active"]) == 1
    assert body["qradar"]["available"] is True
    [entry] = body["qradar"]["consoles"]
    assert entry["instance_name"] == "lab", "a disabled console is not read"
    assert entry["reachable"]["value"] is True
    assert entry["servers"]["state"] == "OK"
    assert entry["ariel"]["list_probe"]["value"] == {"held": 4, "truncated": False}
    assert entry["ariel"]["ours_in_flight"]["value"] == 1
    assert entry["ariel"]["recent"]["value"]["searches"] == 2
    assert entry["ariel"]["available"]["value"] is True
    assert "integration-token-never-in-a-response" not in response.text

    # The second poll comes from the cache and says so; the search record is still fresh.
    again = _client(ADMIN).get("/api/v1/admin/ariel-load/current").json()
    assert again["qradar"]["consoles"][0]["cached"] is True


async def test_an_unreachable_or_unconfigured_console_never_fails_the_endpoint(
    db_schema: str,
) -> None:
    def rest(name: str, *, verify_ssl: bool, created: datetime) -> QRadarInstance:
        return QRadarInstance(
            id=uuid.uuid4(),
            name=name,
            console_host="https://127.0.0.1:9",
            api_version="20.0",
            sec_token="integration-token-never-in-a-response",
            verify_ssl=verify_ssl,
            provider_kind=ProviderKind.REST.value,
            created_at=created,
        )

    now = datetime.now(UTC)
    await _seed(
        db_schema,
        [
            rest("gone", verify_ssl=True, created=now - timedelta(days=2)),
            rest("no-tls", verify_ssl=False, created=now - timedelta(days=1)),
        ],
    )
    response = _client(ADMIN).get("/api/v1/admin/ariel-load/current")
    assert response.status_code == 200
    gone, no_tls = response.json()["qradar"]["consoles"]
    assert gone["instance_name"] == "gone"
    assert gone["reachable"]["value"] is False
    assert gone["reachable"]["reason"] == REASONS["unreachable"]
    assert gone["servers"]["reason_code"] == "console_unreachable"
    assert gone["ariel"]["list_probe"]["value"] is None
    # A console the platform cannot even build a provider for is not "unreachable".
    assert no_tls["reachable"]["state"] == "NOT_COLLECTED"
    assert no_tls["reachable"]["reason_code"] == "provider_not_configured"
    assert "integration-token-never-in-a-response" not in response.text
