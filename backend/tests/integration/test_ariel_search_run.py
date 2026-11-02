"""Migration 0046, Ariel load retention, and the admin summary against real rows."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.models.ariel_search import ArielSearchRun
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services import ariel_search_log as ariel_log
from app.services.ariel_search_retention import ArielSearchRunRetention
from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
TABLE = "ariel_search_run"
WHEN = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
WAF_ADMIN = Principal(subject="waf", permissions=frozenset({"admin:waf"}))


def _sync_url() -> str:
    if not TEST_DB_URL:
        pytest.skip("TEST_DATABASE_URL not set")
    return TEST_DB_URL.replace("+asyncpg", "+psycopg")


def _alembic_config(sync_url: str):  # type: ignore[no-untyped-def]
    from alembic.config import Config

    config = Config()
    config.set_main_option("script_location", "alembic")
    config.set_main_option("sqlalchemy.url", sync_url)
    return config


@pytest.fixture
def clean_db() -> str:
    sync_url = _sync_url()
    reset_public_schema(sync_url)
    return sync_url


def _row(
    *,
    submitted_at: datetime,
    caller: str,
    outcome: str,
    wall_ms: int,
    execution: int | None,
    records: int | None,
) -> ArielSearchRun:
    return ArielSearchRun(
        instance_id=uuid.uuid4(),
        caller=caller,
        submitted_at=submitted_at,
        finished_at=submitted_at + timedelta(milliseconds=wall_ms),
        wall_ms=wall_ms,
        polls=1,
        outcome=outcome,
        qradar_execution_ms=execution,
        record_count=records,
        data_total_size=execution,
    )


def test_0046_round_trips_and_checks_clean(clean_db: str) -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    config = _alembic_config(clean_db)
    script = ScriptDirectory.from_config(config)
    revision = script.get_revision("0046")
    assert revision is not None and revision.down_revision == "0045"
    engine = create_engine(clean_db, future=True)
    try:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0046"
        columns = {column["name"] for column in inspect(engine).get_columns(TABLE)}
        assert "caller" in columns
        assert "qradar_execution_ms" in columns
        assert "data_total_size" in columns
        assert "aql" not in columns
        assert "query_string" not in columns
        indexes = {index["name"]: index for index in inspect(engine).get_indexes(TABLE)}
        assert indexes["ix_ariel_search_run_instance_id_submitted_at"]["column_names"] == [
            "instance_id",
            "submitted_at",
        ]
        assert indexes["ix_ariel_search_run_submitted_at"]["column_names"] == ["submitted_at"]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO ariel_search_run "
                    "(id, caller, submitted_at, finished_at, wall_ms, polls, outcome) "
                    "VALUES (:id, 'LOG_SOURCE_VOLUME', :at, :at, 1, 0, 'COMPLETED')"
                ),
                {"id": uuid.uuid4(), "at": WHEN},
            )

        command.downgrade(config, "0045")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0045"
        assert not inspect(engine).has_table(TABLE)

        command.upgrade(config, "head")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0046"
        assert inspect(engine).has_table(TABLE)
        command.check(config)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_retention_deletes_only_rows_older_than_the_cutoff(db_session: AsyncSession) -> None:
    settings = get_settings()
    cutoff = WHEN - timedelta(days=settings.ariel_search_run_retention_days)
    old = _row(
        submitted_at=cutoff - timedelta(seconds=1),
        caller="LOG_SOURCE_VOLUME",
        outcome="COMPLETED",
        wall_ms=10,
        execution=5,
        records=1,
    )
    edge = _row(
        submitted_at=cutoff,
        caller="WAF_COLLECTION",
        outcome="TIMEOUT",
        wall_ms=20,
        execution=None,
        records=None,
    )
    fresh = _row(
        submitted_at=WHEN - timedelta(hours=1),
        caller="GEO_COLLECTION",
        outcome="ERROR",
        wall_ms=30,
        execution=0,
        records=0,
    )
    db_session.add_all([old, edge, fresh])
    await db_session.commit()

    report = await ArielSearchRunRetention(
        db_session, settings=settings, clock=lambda: WHEN
    ).run()
    assert report.deleted[TABLE] == 1
    assert report.incomplete == []
    left = set((await db_session.scalars(select(ArielSearchRun.id))).all())
    assert old.id not in left
    assert {edge.id, fresh.id} <= left


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


def _seed(url: str, rows: list[ArielSearchRun]) -> None:
    import asyncio

    async def insert() -> None:
        engine = create_async_engine(url, future=True, poolclass=NullPool)
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                session.add_all(rows)
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(insert())


def test_admin_summary_reads_real_rows_and_refuses_other_roles(db_schema: str) -> None:
    now = datetime.now(UTC)
    recent = now - timedelta(minutes=30)
    _seed(
        db_schema,
        [
            _row(
                submitted_at=recent,
                caller="LOG_SOURCE_VOLUME",
                outcome="COMPLETED",
                wall_ms=10,
                execution=None,
                records=4,
            ),
            _row(
                submitted_at=recent,
                caller="LOG_SOURCE_VOLUME",
                outcome="COMPLETED",
                wall_ms=30,
                execution=None,
                records=None,
            ),
            _row(
                submitted_at=recent,
                caller="WAF_COLLECTION",
                outcome="ERROR",
                wall_ms=40,
                execution=5,
                records=2,
            ),
            _row(
                submitted_at=recent,
                caller="WAF_COLLECTION",
                outcome="TIMEOUT",
                wall_ms=50,
                execution=None,
                records=1,
            ),
            _row(
                submitted_at=recent,
                caller="IPS_COLLECTION",
                outcome="COMPLETED",
                wall_ms=7,
                execution=0,
                records=0,
            ),
        ],
    )
    denied = _client(READER).get("/api/v1/admin/ariel-load", params={"hours": "24"})
    assert denied.status_code == 403
    assert denied.json()["detail_code"] == "auth.permission.missing"
    assert _client(WAF_ADMIN).get("/api/v1/admin/ariel-load").status_code == 403

    bad = _client(ADMIN).get("/api/v1/admin/ariel-load", params={"hours": "721"})
    assert bad.status_code == 400
    assert bad.json()["detail_code"] == "ariel_load.hours.invalid"

    response = _client(ADMIN).get("/api/v1/admin/ariel-load", params={"hours": "24"})
    assert response.status_code == 200
    body = response.json()
    assert body["empty"] is False
    by_name = {item["caller"]: item for item in body["callers"]}
    phase = by_name["LOG_SOURCE_VOLUME"]
    assert phase["searches"] == 2
    assert phase["wall_ms_p50"] == 20
    assert phase["qradar_execution_ms_sum"] is None
    assert phase["qradar_execution_ms_unknown"] == 2
    assert phase["records_returned_sum"] == 4
    assert phase["records_returned_unknown"] == 1
    waf = by_name["WAF_COLLECTION"]
    assert waf["qradar_execution_ms_sum"] == 5
    assert waf["qradar_execution_ms_unknown"] == 1
    assert waf["outcomes"] == {"ERROR": 1, "TIMEOUT": 1}
    ips = by_name["IPS_COLLECTION"]
    assert ips["qradar_execution_ms_sum"] == 0
    assert ips["qradar_execution_ms_unknown"] == 0
    assert any(
        point["searches"] == 0 and point["qradar_execution_ms_sum"] is None
        for point in body["series"]
    )
    assert "SELECT" not in response.text
    assert "aql" not in response.text


def test_admin_summary_says_when_the_window_has_no_rows(db_schema: str) -> None:
    _seed(
        db_schema,
        [
            _row(
                submitted_at=datetime.now(UTC) - timedelta(hours=48),
                caller="LOG_SOURCE_VOLUME",
                outcome="COMPLETED",
                wall_ms=10,
                execution=9,
                records=1,
            )
        ],
    )
    response = _client(ADMIN).get("/api/v1/admin/ariel-load", params={"hours": "1"})
    assert response.status_code == 200
    body = response.json()
    assert body["empty"] is True
    assert body["callers"] == []
    assert body["series"] == []
    assert body["message"] == "No Ariel search was recorded in this window."
    assert body["message_code"] == "ariel_load.window.empty"
    assert "qradar_execution_ms_sum" not in body


# ------------------------------------------------------- the operator's path
SLOT = datetime(2026, 10, 13, 10, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_a_celery_task_body_writes_its_searches_through_the_worker_flush(
    db_schema: str, ariel_load_engine: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The task runs as the worker runs it: ``_run`` on a loop of its own.

    The property-watch dispatch sends its searches through the mock provider.
    The mock holds no submit-time row, so the rows in the database can only
    have come from the buffer flush at the end of ``_run``.
    """
    from app.models.property_watch import PropertyWatch  # noqa: F401
    from app.workers import tasks
    from tests.integration.factories import make_instance
    from tests.integration.test_pw_collection import add_watch, cfg

    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: SLOT)
    monkeypatch.setattr(tasks, "get_settings", lambda: cfg())
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            await add_watch(session, instance)
            await session.commit()
        # Not awaited here: the shim runs it, on its own loop, in a worker thread.
        result = await asyncio.to_thread(tasks._run, tasks.dispatch_property_watch_collection())
        [outcome] = result["results"]
        assert outcome["status"] == "ok" and outcome["searches_sent"] == 2
        async with maker() as session:
            rows = (await session.scalars(select(ArielSearchRun))).all()
    finally:
        await engine.dispose()
    assert [(row.caller, row.outcome) for row in rows] == [("PROPERTY_WATCH", "COMPLETED")] * 2
    assert all(row.finished_at is not None and row.wall_ms is not None for row in rows)


@pytest.mark.asyncio
async def test_a_search_is_in_flight_while_it_runs_and_closed_when_it_ends(
    db_schema: str, ariel_load_engine: object
) -> None:
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    statements: list[str] = []
    event.listen(
        engine.sync_engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    started = datetime.now(UTC) - timedelta(seconds=7)
    clock_ticks = iter([started, started + timedelta(seconds=7)])
    token = ariel_log.open_run_buffer()
    assert token is not None
    try:
        async with ariel_log.track_search(
            clock=lambda: next(clock_ticks),
            instance_id=None,
            caller=ariel_log.CALLER_GEO_COLLECTION,
            range_seconds=900,
        ):
            async with maker() as session:
                before = len(statements)
                current = await ariel_log.load_current(session)
                reads = len(statements) - before
            [open_row] = current["in_flight"]["active"]
            assert open_row["caller"] == "GEO_COLLECTION"
            assert open_row["range_seconds"] == 900 and open_row["polls"] == 0
            assert open_row["age_seconds"] >= 7
            assert current["windows"]["minutes_5"]["in_flight"] == 1
            assert current["windows"]["minutes_5"]["wall_ms_sum"] is None
            assert current["slots"]["available"] is True
            assert current["slots"]["global_held"] == 0
            # Bounded: in flight, the last hour, the permit read. A fixed few.
            assert reads == 3
        assert ariel_log.take_run_buffer(token) == []
        async with maker() as session:
            [row] = (await session.scalars(select(ArielSearchRun))).all()
            after = await ariel_log.load_current(session)
    finally:
        await engine.dispose()
    assert row.outcome == "COMPLETED" and row.wall_ms == 7000 and row.finished_at is not None
    assert after["in_flight"]["active"] == [] and after["in_flight"]["stale"] == []
    assert after["windows"]["minutes_5"]["completed"] == 1
    assert after["windows"]["minutes_5"]["wall_ms_sum"] == 7000


def test_admin_current_reads_real_rows_and_refuses_other_roles(db_schema: str) -> None:
    now = datetime.now(UTC)
    in_flight = _row(
        submitted_at=now - timedelta(seconds=20),
        caller="WAF_COLLECTION",
        outcome="IN_FLIGHT",
        wall_ms=0,
        execution=None,
        records=None,
    )
    in_flight.finished_at = None
    in_flight.wall_ms = None
    in_flight.polls = 3
    lost = _row(
        submitted_at=now - timedelta(minutes=30),
        caller="HISTORY",
        outcome="IN_FLIGHT",
        wall_ms=0,
        execution=None,
        records=None,
    )
    lost.finished_at = None
    lost.wall_ms = None
    _seed(
        db_schema,
        [
            in_flight,
            lost,
            _row(
                submitted_at=now - timedelta(minutes=2),
                caller="WAF_COLLECTION",
                outcome="COMPLETED",
                wall_ms=100,
                execution=40,
                records=3,
            ),
            _row(
                submitted_at=now - timedelta(minutes=2),
                caller="WAF_COLLECTION",
                outcome="TIMEOUT",
                wall_ms=900,
                execution=500,
                records=None,
            ),
            _row(
                submitted_at=now - timedelta(hours=3),
                caller="WAF_COLLECTION",
                outcome="COMPLETED",
                wall_ms=1,
                execution=1,
                records=1,
            ),
        ],
    )
    assert _client(READER).get("/api/v1/admin/ariel-load/current").status_code == 403
    assert _client(WAF_ADMIN).get("/api/v1/admin/ariel-load/current").status_code == 403
    response = _client(ADMIN).get("/api/v1/admin/ariel-load/current")
    assert response.status_code == 200
    body = response.json()
    assert datetime.fromisoformat(body["as_of"]).tzinfo is not None
    [active] = body["in_flight"]["active"]
    assert active["caller"] == "WAF_COLLECTION" and active["polls"] == 3
    [stale] = body["in_flight"]["stale"]
    assert stale["caller"] == "HISTORY" and stale["age_seconds"] >= 1800
    five = body["windows"]["minutes_5"]
    assert (five["submitted"], five["completed"], five["failed"], five["in_flight"]) == (3, 1, 1, 1)
    # The TIMEOUT adds to the sums but not to the percentiles.
    assert five["wall_ms_sum"] == 1000 and five["qradar_execution_ms_sum"] == 540
    assert five["wall_ms_p50"] == 100 and five["qradar_execution_ms_p50"] == 40
    assert body["windows"]["minutes_60"]["submitted"] == 4, "the 3 h old row is out of the read"
    assert body["slots"]["available"] is True
    assert "SELECT" not in response.text and "aql" not in response.text
