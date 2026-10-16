"""The policy task against real TimescaleDB: jobs are created, reconciled, removed."""

from __future__ import annotations

from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.config import Settings
from app.services import timescale
from app.workers import tasks

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

TABLES = {
    "log_source_metric": "bucket_start",
    "search_result_metric": "bucket_start",
    "rule_metric": "bucket_start",
    "offense_snapshot": "captured_at",
}


@pytest.fixture
async def hypertables(db_session):  # type: ignore[no-untyped-def]
    ext = await db_session.scalar(text("SELECT 1 FROM pg_extension WHERE extname='timescaledb'"))
    if ext is None:
        # `reset_public_schema` in an earlier test module drops the extension with `public`.
        available = await db_session.scalar(
            text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
        )
        if available is None:
            pytest.skip("the database image does not offer the timescaledb extension")
        await db_session.execute(text("CREATE EXTENSION IF NOT EXISTS timescaledb"))
        await db_session.commit()
    for table, col in TABLES.items():
        await db_session.execute(
            text("SELECT create_hypertable(:t, :c, if_not_exists => TRUE, migrate_data => TRUE)")
            .bindparams(t=table, c=col)
        )
    await db_session.commit()
    return db_schema_url(db_session)


def db_schema_url(session) -> str:  # type: ignore[no-untyped-def]
    return session.bind.url.render_as_string(hide_password=False)


def make_settings(**kw):  # type: ignore[no-untyped-def]
    return Settings(_env_file=None, **kw)


@pytest.fixture
def run_task(monkeypatch, hypertables):  # type: ignore[no-untyped-def]
    engine = create_async_engine(hypertables)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_cm():
        async with maker() as s:
            yield s

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: session_cm)

    async def run(**kw):  # type: ignore[no-untyped-def]
        settings = make_settings(**kw)
        monkeypatch.setattr(tasks, "get_settings", lambda: settings)
        return await tasks.apply_timescale_policies()

    yield run


async def jobs(session):  # type: ignore[no-untyped-def]
    await session.rollback()
    rows = await session.execute(
        text(
            "SELECT hypertable_name, proc_name, config->>'drop_after', config->>'compress_after' "
            "FROM timescaledb_information.jobs "
            "WHERE proc_name IN ('policy_retention', 'policy_compression') "
            "ORDER BY 1, 2"
        )
    )
    return [tuple(r) for r in rows]


ALL_ON = dict(
    retention_enabled=True,
    retention_log_source_metric_days=90,
    retention_search_result_metric_days=90,
    retention_rule_metric_days=90,
    retention_offense_snapshot_days=180,
    compression_after_days=7,
)


def intervals(rows, proc, idx):  # type: ignore[no-untyped-def]
    return {r[0]: r[idx] for r in rows if r[1] == proc}


async def test_enabled_creates_a_retention_and_a_compression_job_per_table(
    db_session, run_task
) -> None:
    result = await run_task(**ALL_ON)
    assert result["failed"] == []
    rows = await jobs(db_session)
    assert set(intervals(rows, "policy_retention", 2)) == set(TABLES)
    assert set(intervals(rows, "policy_compression", 3)) == set(TABLES)
    assert intervals(rows, "policy_retention", 2)["offense_snapshot"] == "180 days"
    assert intervals(rows, "policy_compression", 3)["rule_metric"] == "7 days"


async def test_second_run_creates_no_duplicate_and_keeps_the_job_ids(
    db_session, run_task
) -> None:
    await run_task(**ALL_ON)
    first = (await db_session.execute(text(
            "SELECT job_id FROM timescaledb_information.jobs "
            "WHERE proc_name IN ('policy_retention', 'policy_compression') ORDER BY job_id"
        ))).all()
    await run_task(**ALL_ON)
    await db_session.rollback()
    second = (await db_session.execute(text(
            "SELECT job_id FROM timescaledb_information.jobs "
            "WHERE proc_name IN ('policy_retention', 'policy_compression') ORDER BY job_id"
        ))).all()
    assert len(first) == 8
    assert second == first


async def test_changed_retention_and_compression_replace_the_interval(
    db_session, run_task
) -> None:
    await run_task(**ALL_ON)
    await run_task(
        **{**ALL_ON, "retention_log_source_metric_days": 400, "compression_after_days": 14}
    )
    rows = await jobs(db_session)
    retention = intervals(rows, "policy_retention", 2)
    assert retention["log_source_metric"] == "400 days"
    assert retention["rule_metric"] == "90 days"
    assert set(intervals(rows, "policy_compression", 3).values()) == {"14 days"}
    assert len([r for r in rows if r[1] == "policy_retention"]) == 4


async def test_unsetting_one_table_and_compression_removes_only_those(
    db_session, run_task
) -> None:
    await run_task(**ALL_ON)
    await run_task(**{**ALL_ON, "retention_rule_metric_days": None, "compression_after_days": None})
    rows = await jobs(db_session)
    assert "rule_metric" not in intervals(rows, "policy_retention", 2)
    assert len(intervals(rows, "policy_retention", 2)) == 3
    assert [r for r in rows if r[1] == "policy_compression"] == []


async def test_turning_retention_off_removes_every_retention_job(db_session, run_task) -> None:
    await run_task(**ALL_ON)
    # The flag goes false; the per-table days stay set. The jobs must go.
    result = await run_task(**{**ALL_ON, "retention_enabled": False})
    assert result["failed"] == []
    rows = await jobs(db_session)
    assert [r for r in rows if r[1] == "policy_retention"] == []
    # Compression is not retention and keeps its own setting.
    assert len([r for r in rows if r[1] == "policy_compression"]) == 4


async def test_default_settings_create_no_policy(db_session, run_task) -> None:
    result = await run_task()
    assert result["failed"] == []
    assert await jobs(db_session) == []


@pytest.mark.parametrize("kind", ["retention", "compression"])
async def test_a_database_error_on_one_table_does_not_stop_the_others(
    db_session, run_task, monkeypatch, kind
) -> None:
    # A statement fails inside the table's transaction, after its first policy
    # statement succeeded. The savepoint must roll that back and leave the
    # transaction usable for the next table.
    original = getattr(timescale, f"_reconcile_{kind}")

    async def broken(session, table, days):  # type: ignore[no-untyped-def]
        out = await original(session, table, days)
        if table == "rule_metric":
            await session.execute(text("SELECT 1/0"))
        return out

    monkeypatch.setattr(timescale, f"_reconcile_{kind}", broken)
    result = await run_task(**ALL_ON)
    key = "rule_metric" if kind == "retention" else "_compression:rule_metric"
    assert result["failed"] == [key]
    assert result["outcome"][key] == "error:DBAPIError"
    rows = await jobs(db_session)
    retention = set(intervals(rows, "policy_retention", 2))
    compression = set(intervals(rows, "policy_compression", 3))
    if kind == "retention":
        assert retention == set(TABLES) - {"rule_metric"}
        assert compression == set(TABLES)
    else:
        assert retention == set(TABLES)
        assert compression == set(TABLES) - {"rule_metric"}


async def test_one_bad_table_is_isolated(db_session, run_task) -> None:
    # A retention below 1 day is a ValueError for that table alone.
    result = await run_task(**{**ALL_ON, "retention_rule_metric_days": 0})
    assert result["failed"] == ["rule_metric"]
    rows = await jobs(db_session)
    assert set(intervals(rows, "policy_retention", 2)) == set(TABLES) - {"rule_metric"}


async def _compress_old_offense_chunk(session) -> int:  # type: ignore[no-untyped-def]
    """Insert old offense snapshots and compress their chunk, as day 8 does in production."""
    from datetime import UTC, datetime, timedelta

    from tests.integration.factories import make_instance

    inst = await make_instance(session)
    old = datetime.now(UTC) - timedelta(days=60)
    for offense_id in (1, 2, 3):
        await session.execute(
            text(
                "INSERT INTO offense_snapshot "
                "(instance_id, qradar_offense_id, captured_at, is_assigned, categories, "
                "source_addresses, local_destination_addresses, usernames, "
                "log_source_ids, rule_ids) "
                "VALUES (:i, :o, :c, false, '[]', '[]', '[]', '[]', '[]', '[]')"
            ).bindparams(i=inst.id, o=offense_id, c=old)
        )
    await session.commit()
    chunks = (
        await session.execute(
            text("SELECT show_chunks('offense_snapshot', older_than => interval '30 days')")
        )
    ).all()
    assert chunks
    for (chunk,) in chunks:
        await session.execute(
            text("SELECT compress_chunk(CAST(:c AS regclass))").bindparams(c=chunk)
        )
    await session.commit()
    compressed = await session.scalar(
        text(
            "SELECT count(*) FROM timescaledb_information.chunks "
            "WHERE hypertable_name = 'offense_snapshot' AND is_compressed"
        )
    )
    assert compressed >= 1
    return await session.scalar(text("SELECT count(*) FROM offense_snapshot"))


@pytest.mark.parametrize("second_days", [7, 14])
async def test_rerun_with_a_compressed_chunk_is_safe(
    db_session, run_task, second_days
) -> None:
    # Production path from day 8: the next daily run re-issues the compress
    # setting on a hypertable that already holds compressed chunks.
    await run_task(**ALL_ON)
    rows_before = await _compress_old_offense_chunk(db_session)
    result = await run_task(**{**ALL_ON, "compression_after_days": second_days})
    assert result["failed"] == []
    rows = await jobs(db_session)
    compression = [r for r in rows if r[1] == "policy_compression" and r[0] == "offense_snapshot"]
    assert [r[3] for r in compression] == [f"{second_days} days"]
    assert len([r for r in rows if r[1] == "policy_compression"]) == 4
    assert await db_session.scalar(text("SELECT count(*) FROM offense_snapshot")) == rows_before
