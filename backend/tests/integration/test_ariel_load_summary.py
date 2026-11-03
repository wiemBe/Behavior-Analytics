"""ARL-4: the /ariel-load window summary folds in SQL, not in Python.

``load_summary`` aggregates the window in two statements whatever the window
and the number of callers. These tests fold the same rows the old way — every
row into Python, through ``summarize_runs`` — and demand the same document:
the same keys in the same order, the same types, the same numbers, including
the NULL semantics (a sum is null, never 0, when nothing reported it) and the
half-to-even percentile ties.
"""

from __future__ import annotations

import json
import os
import random
import re
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.models.ariel_search import OUTCOMES, ArielSearchRun
from app.services import ariel_search_log as ariel_log

pytestmark = pytest.mark.integration

TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")

#: A window end inside an hour, so window edges and hour edges never coincide.
NOW = datetime(2026, 11, 2, 15, 37, 4, 263000, tzinfo=UTC)
#: Far past every seeded row: the empty window.
EMPTY_NOW = NOW + timedelta(days=45)
#: An hour boundary inside the smallest seeded window.
HOUR_EDGE = datetime(2026, 11, 2, 14, 0, tzinfo=UTC)
MAX_WINDOW_SECONDS = 720 * 3600
SEEDS = (11, 29, 173)
#: ``load_summary`` sends this many statements for every non-empty window.
STATEMENT_COUNT = 2

ALL_COLUMNS = set(ArielSearchRun.__table__.columns.keys())
_TABLE_REF = re.compile(r"ariel_search_run\.([a-z0-9_]+)")


def _row(
    *,
    at: datetime,
    caller: str,
    outcome: str,
    wall_ms: int | None,
    execution: int | None,
    records: int | None,
    data: int | None,
) -> ArielSearchRun:
    """One load row. The nullable measured columns are the caller's to set: a
    ``None`` and a measured ``0`` are different facts, and both belong here."""
    running = outcome == ariel_log.OUTCOME_IN_FLIGHT
    return ArielSearchRun(
        instance_id=None,
        caller=caller,
        submitted_at=at,
        # A submit row has measured nothing yet; every column it carries is null.
        finished_at=None if running else at + timedelta(milliseconds=1),
        wall_ms=None if running else wall_ms,
        qradar_execution_ms=None if running else execution,
        data_total_size=None if running else data,
        record_count=None if running else records,
        polls=0 if running else 1,
        outcome=outcome,
    )


def _random_rows(rng: random.Random) -> list[ArielSearchRun]:
    """Seeded rows across the whole 720 h window: several callers, every
    outcome, NULLs sprinkled through every measured column."""
    callers = ariel_log.CALLERS[:6]
    rows: list[ArielSearchRun] = []
    for _ in range(120):
        rows.append(
            _row(
                at=NOW - timedelta(seconds=rng.randrange(2, MAX_WINDOW_SECONDS)),
                caller=rng.choice(callers),
                outcome=rng.choice(OUTCOMES),
                wall_ms=None if rng.random() < 0.3 else rng.randrange(0, 5000),
                execution=None if rng.random() < 0.3 else rng.randrange(0, 2**20),
                records=None if rng.random() < 0.3 else rng.randrange(0, 5000),
                data=None if rng.random() < 0.3 else rng.randrange(0, 2**20),
            )
        )
    return rows


def _fixed_rows() -> list[ArielSearchRun]:
    """The edge cases randomness will not place on demand."""
    rows: list[ArielSearchRun] = []
    # Every outcome at least once, every measured column NULL. The per-outcome
    # counts, the unknown counts and the null-vs-zero sums all get rows to pin.
    for index, outcome in enumerate(OUTCOMES):
        rows.append(
            _row(
                at=NOW - timedelta(hours=5, minutes=index),
                caller=ariel_log.CALLER_GEO_COLLECTION,
                outcome=outcome,
                wall_ms=None,
                execution=None,
                records=None,
                data=None,
            )
        )
    # Window edges: on the bound is inside, the microsecond after it is inside
    # the next window, and the end bound itself is outside.
    for at, caller in (
        (NOW - timedelta(hours=720), ariel_log.CALLER_LOG_SOURCE_VOLUME),
        (NOW - timedelta(hours=24), ariel_log.CALLER_WAF_COLLECTION),
        (NOW - timedelta(hours=1), ariel_log.CALLER_RULE_MATCH),
    ):
        rows.extend(
            _row(
                at=at + timedelta(microseconds=offset),
                caller=caller,
                outcome=ariel_log.OUTCOME_COMPLETED,
                wall_ms=25 + offset,
                execution=250 + offset,
                records=2 + offset,
                data=2500 + offset,
            )
            for offset in (0, 1)
        )
    rows.append(
        _row(
            # One microsecond before the widest window: no window sees it.
            at=NOW - timedelta(hours=720, microseconds=1),
            caller=ariel_log.CALLER_LOG_SOURCE_VOLUME,
            outcome=ariel_log.OUTCOME_COMPLETED,
            wall_ms=99,
            execution=990,
            records=9,
            data=9900,
        )
    )
    rows.append(
        _row(
            # Exactly at the end bound: outside every window.
            at=NOW,
            caller=ariel_log.CALLER_RULE_MATCH,
            outcome=ariel_log.OUTCOME_COMPLETED,
            wall_ms=98,
            execution=980,
            records=8,
            data=9800,
        )
    )
    rows.append(
        _row(
            # In bounds but never summarised: the current read owns it.
            at=NOW - timedelta(hours=1),
            caller=ariel_log.CALLER_RULE_MATCH,
            outcome=ariel_log.OUTCOME_IN_FLIGHT,
            wall_ms=None,
            execution=None,
            records=None,
            data=None,
        )
    )
    # Hour edges: the microsecond before, the boundary itself, the microsecond
    # after — three different hour buckets.
    rows.extend(
        _row(
            at=HOUR_EDGE + timedelta(microseconds=offset),
            caller=ariel_log.CALLER_HISTORY,
            outcome=ariel_log.OUTCOME_COMPLETED,
            wall_ms=50 + offset,
            execution=None,
            records=None,
            data=None,
        )
        for offset in (-1, 0, 1)
    )
    # Percentile ties: two completed walls 10 and 11 make the p50 exactly 10.5,
    # and eleven walls 1..11 make the p95 exactly 10.5. Python rounds half to
    # even (10); PostgreSQL's round would take both away from zero (11).
    rows.extend(
        _row(
            at=NOW - timedelta(minutes=90),
            caller=ariel_log.CALLER_QRADAR_HEALTH,
            outcome=ariel_log.OUTCOME_COMPLETED,
            wall_ms=wall,
            execution=execution,
            records=None,
            data=None,
        )
        for wall, execution in ((10, 100), (11, 101))
    )
    rows.extend(
        _row(
            at=NOW - timedelta(hours=3, minutes=wall),
            caller=ariel_log.CALLER_SCHEDULED_SEARCH,
            outcome=ariel_log.OUTCOME_COMPLETED,
            wall_ms=wall,
            execution=None,
            records=None,
            data=None,
        )
        for wall in range(1, 12)
    )
    # A caller with a single COMPLETED row: n = 1 percentiles.
    rows.append(
        _row(
            at=NOW - timedelta(minutes=5),
            caller=ariel_log.CALLER_PREVIEW,
            outcome=ariel_log.OUTCOME_COMPLETED,
            wall_ms=7,
            execution=42,
            records=0,
            data=0,
        )
    )
    # A caller with no COMPLETED row: null percentiles, a real wall sum.
    rows.extend(
        _row(
            at=NOW - timedelta(minutes=12),
            caller=ariel_log.CALLER_CATCHALL_SENDERS,
            outcome=outcome,
            wall_ms=wall,
            execution=execution,
            records=None,
            data=None,
        )
        for outcome, wall, execution in (
            (ariel_log.OUTCOME_REFUSED, None, None),
            (ariel_log.OUTCOME_ERROR, 3, 77),
        )
    )
    return rows


async def _seed(url: str, rows: list[ArielSearchRun]) -> None:
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            session.add_all(rows)
            await session.commit()
    finally:
        await engine.dispose()


async def _old_fold(session: AsyncSession, *, hours: int, now: datetime) -> dict[str, Any]:
    """The old read: every terminal row of the window, folded in Python."""
    start = now - timedelta(hours=hours)
    found = (
        await session.scalars(
            select(ArielSearchRun).where(
                ArielSearchRun.submitted_at >= start,
                ArielSearchRun.submitted_at < now,
                ArielSearchRun.outcome != ariel_log.OUTCOME_IN_FLIGHT,
            )
        )
    ).all()
    return ariel_log.summarize_runs(found, start=start, end=now, hours=hours)


async def _load_with_statements(
    url: str, *, hours: int, now: datetime
) -> tuple[dict[str, Any], list[str]]:
    """``load_summary`` on its own engine, with every statement it sent."""
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    statements: list[str] = []
    event.listen(
        engine.sync_engine, "before_cursor_execute", lambda *args: statements.append(args[2])
    )
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            summary = await ariel_log.load_summary(session, hours=hours, now=now)
    finally:
        await engine.dispose()
    return summary, statements


@pytest.mark.parametrize("seed", SEEDS)
async def test_sql_summary_equals_the_python_fold(db_schema: str, seed: int) -> None:
    await _seed(db_schema, _random_rows(random.Random(seed)) + _fixed_rows())
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as session:
            for hours in (1, 24, 720):
                sql_side = await ariel_log.load_summary(session, hours=hours, now=NOW)
                py_side = await _old_fold(session, hours=hours, now=NOW)
                assert sql_side == py_side
                # == ignores key order; the serialized document pins it, and
                # with it the int-not-float types and the null placements.
                assert json.dumps(sql_side) == json.dumps(py_side)
                assert sql_side["empty"] is False
                if hours == 24:
                    by_caller = {item["caller"]: item for item in sql_side["callers"]}
                    # The ties, pinned: half to even, not away from zero.
                    health = by_caller[ariel_log.CALLER_QRADAR_HEALTH]
                    assert health["wall_ms_p50"] == 10
                    assert health["qradar_execution_ms_p50"] == 100
                    assert by_caller[ariel_log.CALLER_SCHEDULED_SEARCH]["wall_ms_p95"] == 10
                    # n = 1 percentiles; measured zeros are zeros, not nulls.
                    preview = by_caller[ariel_log.CALLER_PREVIEW]
                    assert (preview["wall_ms_p50"], preview["wall_ms_p95"]) == (7, 7)
                    assert (preview["records_returned_sum"], preview["data_total_size_sum"]) == (
                        0,
                        0,
                    )
                    # No COMPLETED row: null percentiles, a real wall sum.
                    catchall = by_caller[ariel_log.CALLER_CATCHALL_SENDERS]
                    assert catchall["wall_ms_p50"] is None
                    assert catchall["wall_ms_max"] is None
                    assert catchall["wall_ms_sum"] == 3
                    assert catchall["qradar_execution_ms_sum"] == 77
            empty_sql = await ariel_log.load_summary(session, hours=24, now=EMPTY_NOW)
            empty_py = await _old_fold(session, hours=24, now=EMPTY_NOW)
            assert empty_sql == empty_py
            assert json.dumps(empty_sql) == json.dumps(empty_py)
            assert empty_sql["empty"] is True
            assert empty_sql["callers"] == [] and empty_sql["series"] == []
    finally:
        await engine.dispose()


async def test_two_statements_whatever_the_window_and_the_callers(db_schema: str) -> None:
    """The statement count depends on neither the window nor the callers.

    The table's ``caller_known`` check admits only the thirteen known callers,
    so the many-caller case uses all of them — the most the table allows.
    """
    await _seed(
        db_schema,
        [
            _row(
                at=NOW - timedelta(minutes=5),
                caller=ariel_log.CALLER_LOG_SOURCE_VOLUME,
                outcome=ariel_log.OUTCOME_COMPLETED,
                wall_ms=1,
                execution=1,
                records=1,
                data=1,
            )
        ],
    )
    one, statements_one = await _load_with_statements(db_schema, hours=1, now=NOW)
    assert one["empty"] is False

    rng = random.Random(7)
    await _seed(
        db_schema,
        [
            _row(
                at=NOW - timedelta(seconds=rng.randrange(3600, MAX_WINDOW_SECONDS - 3600)),
                caller=caller,
                outcome=(
                    ariel_log.OUTCOME_COMPLETED if index % 3 else ariel_log.OUTCOME_ERROR
                ),
                wall_ms=rng.randrange(0, 1000),
                execution=None if index % 4 == 0 else rng.randrange(0, 10**6),
                records=rng.randrange(0, 100),
                data=None if index % 5 == 0 else rng.randrange(0, 10**6),
            )
            for index, caller in enumerate(ariel_log.CALLERS)
            for _ in range(12)
        ],
    )
    many, statements_many = await _load_with_statements(db_schema, hours=720, now=NOW)
    assert many["empty"] is False
    assert len(many["callers"]) == len(ariel_log.CALLERS)
    assert len(statements_one) == len(statements_many) == STATEMENT_COUNT

    empty, statements_empty = await _load_with_statements(db_schema, hours=2, now=EMPTY_NOW)
    assert empty["empty"] is True
    assert len(statements_empty) <= STATEMENT_COUNT


async def test_no_statement_selects_the_row_entity(db_schema: str) -> None:
    """No statement of the summary selects every column of ``ariel_search_run``
    — the ORM entity's select does, and that is what must never come back."""
    await _seed(db_schema, _fixed_rows())
    _, statements = await _load_with_statements(db_schema, hours=24, now=NOW)
    _, empty_statements = await _load_with_statements(db_schema, hours=24, now=EMPTY_NOW)
    statements += empty_statements
    assert statements, "load_summary sent no statements to inspect"
    assert any("ariel_search_run" in sql for sql in statements)
    for sql in statements:
        referenced = set(_TABLE_REF.findall(sql))
        assert not referenced >= ALL_COLUMNS, sql
    # The guard itself bites: the old entity select names every column.
    from sqlalchemy.dialects import postgresql

    old = str(select(ArielSearchRun).compile(dialect=postgresql.dialect()))
    assert set(_TABLE_REF.findall(old)) >= ALL_COLUMNS
