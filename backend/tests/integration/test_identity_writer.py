"""The identity writer issues one bulk statement per table, however many sources."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import event, func, select

from app.identity.fold import (
    CountRow,
    CountSearch,
    IdentityFold,
    NameRow,
    NameSearch,
    SeriesKey,
    fold_tick,
)
from app.identity.writer import write_tick
from app.models.identity_detection import IdentityTopUsername
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

WHEN = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
COLLECTED = datetime(2026, 11, 13, 12, 1, tzinfo=UTC)


@contextmanager
def counting(session: object) -> Iterator[list[str]]:
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine  # type: ignore[attr-defined]
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _inserts(statements: list[str], table: str) -> int:
    return sum(
        f"INSERT INTO {table}" in sql or f'INSERT INTO "{table}"' in sql for sql in statements
    )


def _deletes(statements: list[str]) -> int:
    return sum(
        "DELETE FROM identity_top_username" in sql or 'DELETE FROM "identity_top_username"' in sql
        for sql in statements
    )


def _tick(source_id: UUID) -> IdentityFold:
    scope = (
        SeriesKey(source_id, 1102, WHEN),
        SeriesKey(source_id, 4625, WHEN),
    )
    return fold_tick(
        scope,
        CountSearch(
            True,
            False,
            (
                CountRow(source_id, 1102, WHEN, 1),
                CountRow(source_id, 4625, WHEN, 5),
            ),
        ),
        NameSearch(True, False, (NameRow(source_id, 1102, WHEN, "auditor", 1),)),
        NameSearch(
            True,
            False,
            (
                NameRow(source_id, 4625, WHEN, "one-off", 2),
                NameRow(source_id, 4625, WHEN, "repeated", 3),
            ),
        ),
    )


async def _names(session, source_id: UUID) -> set[str]:  # type: ignore[no-untyped-def]
    rows = (
        await session.scalars(
            select(IdentityTopUsername.username).where(
                IdentityTopUsername.log_source_id == source_id
            )
        )
    ).all()
    return set(rows)


async def test_statement_count_is_constant_for_one_and_fifty_sources(db_session) -> None:
    instance = await make_instance(db_session)
    one = await make_log_source(db_session, instance, qradar_id=4100, name="dc-one")
    many = [
        await make_log_source(db_session, instance, qradar_id=4200 + index, name=f"dc-{index}")
        for index in range(50)
    ]

    with counting(db_session) as one_sql:
        await write_tick(db_session, _tick(one.id), collected_at=COLLECTED)
    with counting(db_session) as many_sql:
        await write_tick(
            db_session,
            _merged([_tick(source.id) for source in many]),
            collected_at=COLLECTED,
        )

    assert len(one_sql) == len(many_sql)
    assert len(one_sql) > 0
    for table in ("identity_bucket", "identity_event_count", "identity_top_username"):
        assert _inserts(one_sql, table) == 1
        assert _inserts(many_sql, table) == 1
    assert _deletes(one_sql) == 1
    assert _deletes(many_sql) == 1

    assert await _names(db_session, one.id) == {"auditor", "repeated"}
    assert "one-off" not in await _names(db_session, one.id)
    stored = await db_session.scalar(
        select(func.count())
        .select_from(IdentityTopUsername)
        .where(IdentityTopUsername.log_source_id.in_([source.id for source in many]))
    )
    assert stored == 100


def _merged(folds: list[IdentityFold]) -> IdentityFold:
    """One tick across sources: the writer is given one fold, not one fold per source."""
    buckets = tuple(bucket for item in folds for bucket in item.buckets)
    return IdentityFold(catalog_version=folds[0].catalog_version, buckets=buckets)


async def test_rewrite_drops_a_name_and_an_unrun_search_keeps_one(db_session) -> None:
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance, qradar_id=4300, name="dc-rewrite")
    await write_tick(db_session, _tick(source.id), collected_at=COLLECTED)
    assert await _names(db_session, source.id) == {"auditor", "repeated"}

    kept = fold_tick(
        (SeriesKey(source.id, 1102, WHEN), SeriesKey(source.id, 4625, WHEN)),
        CountSearch(
            True,
            False,
            (CountRow(source.id, 1102, WHEN, 1), CountRow(source.id, 4625, WHEN, 5)),
        ),
        NameSearch(False, False, ()),
        NameSearch(False, False, ()),
    )
    await write_tick(db_session, kept, collected_at=COLLECTED)
    assert await _names(db_session, source.id) == {"auditor", "repeated"}

    cleared = fold_tick(
        (SeriesKey(source.id, 1102, WHEN),),
        CountSearch(True, False, (CountRow(source.id, 1102, WHEN, 1),)),
        NameSearch(True, False, ()),
        NameSearch(False, False, ()),
    )
    await write_tick(db_session, cleared, collected_at=COLLECTED)
    assert await _names(db_session, source.id) == {"repeated"}
