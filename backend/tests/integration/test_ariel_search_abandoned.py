"""ARL-5: migration 0052, the reaper against real rows, and the truth winning over it."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.models.ariel_search import ArielSearchRun
from app.services import ariel_search_log as ariel_log
from app.workers.celery_app import celery_app
from tests.integration.schema_reset import reset_public_schema

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
NOW = datetime(2026, 11, 2, 15, 0, tzinfo=UTC)


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


def _open(submitted_at: datetime, *, outcome: str = "IN_FLIGHT") -> ArielSearchRun:
    closed = outcome != "IN_FLIGHT"
    return ArielSearchRun(
        id=uuid.uuid4(),
        caller="IPS_COLLECTION",
        submitted_at=submitted_at,
        finished_at=submitted_at + timedelta(seconds=4) if closed else None,
        wall_ms=4000 if closed else None,
        qradar_execution_ms=3000 if outcome == "COMPLETED" else None,
        record_count=9 if outcome == "COMPLETED" else None,
        polls=2,
        outcome=outcome,
    )


def _observation(row_id: uuid.UUID, outcome: str, submitted_at: datetime):  # type: ignore[no-untyped-def]
    return ariel_log.ArielSearchObservation(
        id=row_id,
        instance_id=None,
        caller="IPS_COLLECTION",
        scheduled_search_id=None,
        query_version=None,
        submitted_at=submitted_at,
        finished_at=submitted_at + timedelta(seconds=8),
        wall_ms=8000,
        qradar_execution_ms=6000,
        qradar_record_count=None,
        processed_record_count=None,
        data_file_count=None,
        data_total_size=None,
        compressed_data_file_count=None,
        compressed_data_total_size=None,
        index_file_count=None,
        index_total_size=None,
        record_count=5,
        range_seconds=None,
        polls=3,
        outcome=outcome,
    )


def test_0052_round_trips_with_an_abandoned_row_and_checks_clean() -> None:
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    from alembic import command

    sync_url = _sync_url()
    reset_public_schema(sync_url)
    config = _alembic_config(sync_url)
    revision = ScriptDirectory.from_config(config).get_revision("0052")
    assert revision is not None and revision.down_revision == "0051"
    engine = create_engine(sync_url, future=True)
    insert = text(
        "INSERT INTO ariel_search_run "
        "(id, caller, submitted_at, finished_at, polls, outcome) "
        "VALUES (:id, 'IPS_COLLECTION', :at, :done, 1, :outcome)"
    )
    try:
        command.upgrade(config, "0052")
        abandoned = uuid.uuid4()
        kept = uuid.uuid4()
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0052"
            connection.execute(
                insert, {"id": abandoned, "at": NOW, "done": NOW, "outcome": "ABANDONED"}
            )
            connection.execute(insert, {"id": kept, "at": NOW, "done": NOW, "outcome": "ERROR"})

        command.downgrade(config, "0051")
        with engine.begin() as connection:
            assert MigrationContext.configure(connection).get_current_revision() == "0051"
            rows = dict(
                connection.execute(
                    text("SELECT id, outcome FROM ariel_search_run")
                ).all()
            )
            finished = connection.execute(
                text("SELECT finished_at FROM ariel_search_run WHERE id = :id"), {"id": abandoned}
            ).scalar_one()
        # The state the row really had: no end was recorded.
        assert rows == {abandoned: "IN_FLIGHT", kept: "ERROR"}
        assert finished is None
        with pytest.raises(IntegrityError), engine.begin() as connection:
            connection.execute(
                insert, {"id": uuid.uuid4(), "at": NOW, "done": NOW, "outcome": "ABANDONED"}
            )

        command.upgrade(config, "0052")
        with engine.begin() as connection:
            connection.execute(
                insert, {"id": uuid.uuid4(), "at": NOW, "done": NOW, "outcome": "ABANDONED"}
            )
        command.upgrade(config, "head")
        command.check(config)
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_the_reaper_is_one_update_and_honours_its_cutoff_and_floor(
    db_session: AsyncSession,
) -> None:
    # Spelled out, not read back from the code under test: the longest wait plus one scan.
    bound = ariel_log.stale_after_seconds() + get_settings().ariel_search_abandon_scan_seconds
    cutoff = NOW - timedelta(seconds=bound)
    retention = timedelta(days=get_settings().ariel_search_run_retention_days)
    dead = [_open(cutoff - timedelta(seconds=1)), _open(cutoff - timedelta(hours=2))]
    running = _open(cutoff + timedelta(seconds=1))
    on_the_line = _open(cutoff)
    done = _open(cutoff - timedelta(hours=1), outcome="COMPLETED")
    retention_owned = _open(NOW - retention - timedelta(seconds=1))
    db_session.add_all([*dead, running, on_the_line, done, retention_owned])
    await db_session.commit()

    statements: list[str] = []
    bind = db_session.get_bind()
    event.listen(
        bind.sync_engine if hasattr(bind, "sync_engine") else bind,
        "before_cursor_execute",
        lambda *args: statements.append(args[2]),
    )
    assert await ariel_log.reap_abandoned(db_session, now=NOW) == 2
    updates = [sql for sql in statements if sql.lstrip().upper().startswith("UPDATE")]
    assert len(updates) == 1 and len(statements) == 1

    db_session.expire_all()
    by_id = {row.id: row for row in (await db_session.scalars(select(ArielSearchRun))).all()}
    for row in dead:
        reaped = by_id[row.id]
        assert reaped.outcome == "ABANDONED" and reaped.finished_at is not None
        for column in (
            "wall_ms",
            "qradar_execution_ms",
            "qradar_record_count",
            "processed_record_count",
            "data_file_count",
            "data_total_size",
            "compressed_data_file_count",
            "compressed_data_total_size",
            "index_file_count",
            "index_total_size",
            "record_count",
        ):
            assert getattr(reaped, column) is None, column
        assert reaped.polls == 2
    # One second younger than the bound is still running; the bound itself stays too.
    assert by_id[running.id].outcome == "IN_FLIGHT"
    assert by_id[on_the_line.id].outcome == "IN_FLIGHT"
    assert by_id[done.id].outcome == "COMPLETED" and by_id[done.id].wall_ms == 4000
    assert by_id[retention_owned.id].outcome == "IN_FLIGHT"


@pytest.mark.asyncio
async def test_a_worker_that_ends_after_the_reaper_records_the_real_outcome(
    db_schema: str, ariel_load_engine: object
) -> None:
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    started = NOW - timedelta(hours=3)
    ticks = iter([started, started + timedelta(seconds=9)])
    token = ariel_log.open_run_buffer()
    assert token is not None
    try:
        async with (
            ariel_log.track_search(
                clock=lambda: next(ticks),
                instance_id=None,
                caller=ariel_log.CALLER_GEO_COLLECTION,
            ),
            maker() as session,
        ):
            assert await ariel_log.reap_abandoned(session, now=NOW) == 1
            [row] = (await session.scalars(select(ArielSearchRun))).all()
            assert row.outcome == "ABANDONED" and row.wall_ms is None
        assert ariel_log.take_run_buffer(token) == []
        async with maker() as session:
            [row] = (await session.scalars(select(ArielSearchRun))).all()
    finally:
        await engine.dispose()
    assert row.outcome == "COMPLETED" and row.wall_ms == 9000 and row.finished_at is not None


@pytest.mark.asyncio
async def test_the_real_terminal_write_never_overwrites_another_terminal_outcome(
    db_schema: str, ariel_load_engine: object
) -> None:
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    timed_out = _open(NOW - timedelta(hours=1), outcome="TIMEOUT")
    abandoned = _open(NOW - timedelta(hours=2), outcome="ABANDONED")
    abandoned.wall_ms = None
    open_row = _open(NOW - timedelta(hours=3))
    try:
        async with maker() as session:
            session.add_all([timed_out, abandoned, open_row])
            await session.commit()
        for row in (timed_out, abandoned, open_row):
            observation = _observation(row.id, "COMPLETED", row.submitted_at)
            closed = await ariel_log._close_in_flight(observation)
            assert closed is (row is not timed_out)
            # The buffered upsert obeys the same rule.
            await ariel_log._insert(engine, [observation])
        async with maker() as session:
            by_id = {r.id: r for r in (await session.scalars(select(ArielSearchRun))).all()}
    finally:
        await engine.dispose()
    assert by_id[timed_out.id].outcome == "TIMEOUT" and by_id[timed_out.id].wall_ms == 4000
    assert by_id[abandoned.id].outcome == "COMPLETED" and by_id[abandoned.id].wall_ms == 8000
    assert by_id[open_row.id].outcome == "COMPLETED"


@pytest.mark.asyncio
async def test_load_summary_counts_abandoned_rows_without_a_measurement(
    db_session: AsyncSession,
) -> None:
    done = _open(NOW - timedelta(hours=2), outcome="COMPLETED")
    lost = _open(NOW - timedelta(hours=3), outcome="ABANDONED")
    lost.wall_ms = None
    db_session.add_all([done, lost, _open(NOW - timedelta(hours=1))])
    await db_session.commit()

    summary = await ariel_log.load_summary(db_session, hours=24, now=NOW)
    [caller] = summary["callers"]
    assert caller["searches"] == 2
    assert caller["outcomes"] == {"ABANDONED": 1, "COMPLETED": 1}
    assert caller["wall_ms_p50"] == 4000 and caller["wall_ms_sum"] == 4000
    assert caller["qradar_execution_ms_sum"] == 3000
    assert caller["qradar_execution_ms_unknown"] == 1
    assert caller["records_returned_unknown"] == 1


def test_the_beat_entry_exists() -> None:
    entry = celery_app.conf.beat_schedule["reap-abandoned-ariel-searches"]
    assert entry["task"] == "reap_abandoned_ariel_searches"
    assert entry["schedule"] == float(get_settings().ariel_search_abandon_scan_seconds)
