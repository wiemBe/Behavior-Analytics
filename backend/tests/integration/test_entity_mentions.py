"""Database contract for normalized entity mention writes and retention deletes."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import event, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError

from app.models.entity import EntityMention, EntityMentionBackfill
from app.models.instance import QRadarInstance
from app.services.entity_mentions import (
    MAX_ENTITY_VALUE_LENGTH,
    MENTION_INSERT_CHUNK_SIZE,
    EntityMentionRow,
    delete_mentions_for,
    record_mentions,
)

pytestmark = pytest.mark.integration

OCCURRED = datetime(2026, 11, 8, 12, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def instance(db_session) -> QRadarInstance:  # type: ignore[no-untyped-def]
    row = QRadarInstance(
        name=f"entity-lab-{uuid.uuid4()}",
        console_host="entity-lab.local",
        provider_kind="mock",
    )
    db_session.add(row)
    await db_session.flush()
    return row


def _mention(
    instance_id: uuid.UUID,
    ref_id: str,
    *,
    ref_table: str = "waf_endpoint_metric",
    value: str = "192.0.2.10",
) -> EntityMentionRow:
    return EntityMentionRow(
        instance_id=instance_id,
        kind="IP",
        value_norm=value,
        role="SOURCE_IP",
        source="WAF_TOP",
        ref_table=ref_table,
        ref_id=ref_id,
        occurred_at=OCCURRED,
        occurred_end=OCCURRED + timedelta(minutes=15),
    )


@contextmanager
def _statements(session) -> Iterator[list[str]]:  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def before_cursor_execute(
        _conn, _cursor, statement, _parameters, _context, _executemany
    ) -> None:  # type: ignore[no-untyped-def]
        statements.append(str(statement))

    event.listen(engine, "before_cursor_execute", before_cursor_execute)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", before_cursor_execute)


async def _count(session) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(select(func.count()).select_from(EntityMention)) or 0)


async def test_batch_insert_is_one_statement_idempotent_and_deduplicated(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    first = _mention(instance.id, "parent-1")
    with _statements(db_session) as statements:
        await record_mentions(db_session, [first, first])
    assert len(statements) == 1
    assert "), (" not in statements[0]
    assert await _count(db_session) == 1

    with _statements(db_session) as statements:
        await record_mentions(db_session, [first])
    assert len(statements) == 1
    assert await _count(db_session) == 1


async def test_statement_count_is_constant_for_one_and_500_rows_and_zero_for_empty(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    with _statements(db_session) as empty_statements:
        await record_mentions(db_session, [])
    with _statements(db_session) as one_statement:
        await record_mentions(db_session, [_mention(instance.id, "single")])
    with _statements(db_session) as many_statements:
        await record_mentions(
            db_session,
            [_mention(instance.id, f"many-{number}") for number in range(500)],
        )

    assert len(empty_statements) == 0
    assert len(one_statement) == 1
    assert len(many_statements) == 1


async def test_insert_chunks_only_after_fixed_row_cap(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    at_cap = [
        _mention(instance.id, f"at-cap-{number}")
        for number in range(MENTION_INSERT_CHUNK_SIZE)
    ]
    over_cap = [
        _mention(instance.id, f"over-cap-{number}")
        for number in range(MENTION_INSERT_CHUNK_SIZE + 1)
    ]

    with _statements(db_session) as at_cap_statements:
        await record_mentions(db_session, at_cap)
    with _statements(db_session) as over_cap_statements:
        await record_mentions(db_session, over_cap)

    assert len(at_cap_statements) == 1
    assert len(over_cap_statements) == 2


async def test_overlong_value_is_skipped_without_a_statement(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    row = _mention(instance.id, "overlong", value="x" * (MAX_ENTITY_VALUE_LENGTH + 1))

    with _statements(db_session) as statements:
        await record_mentions(db_session, [row])

    assert statements == []
    assert await _count(db_session) == 0


@pytest.mark.parametrize(
    ("column", "unknown"),
    (("kind", "DEVICE"), ("role", "CLIENT"), ("source", "UNKNOWN")),
)
async def test_text_checks_reject_unknown_values(
    db_session, instance: QRadarInstance, column: str, unknown: str
) -> None:  # type: ignore[no-untyped-def]
    values = {
        "instance_id": instance.id,
        "kind": "IP",
        "value_norm": "192.0.2.20",
        "role": "SOURCE_IP",
        "source": "WAF_TOP",
        "ref_table": "waf_endpoint_metric",
        "ref_id": f"invalid-{column}",
        "occurred_at": OCCURRED,
        column: unknown,
    }

    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():
            await db_session.execute(pg_insert(EntityMention).values(values))


async def test_read_lookup_index_exists_by_name(db_session) -> None:  # type: ignore[no-untyped-def]
    index = await db_session.scalar(
        text("SELECT to_regclass('public.ix_entity_mention_lookup')")
    )
    assert index == "ix_entity_mention_lookup"


async def test_delete_removes_only_named_parent_rows_in_one_statement(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    await record_mentions(
        db_session,
        [
            _mention(instance.id, "parent-a"),
            _mention(instance.id, "parent-b"),
            _mention(instance.id, "parent-c"),
            _mention(instance.id, "parent-a", ref_table="ips_source_bucket"),
        ],
    )

    with _statements(db_session) as statements:
        await delete_mentions_for(
            db_session, "waf_endpoint_metric", ["parent-a", "parent-b", "parent-a"]
        )
    assert len(statements) == 1

    remaining = set(
        (await db_session.execute(select(EntityMention.ref_table, EntityMention.ref_id))).all()
    )
    assert remaining == {
        ("waf_endpoint_metric", "parent-c"),
        ("ips_source_bucket", "parent-a"),
    }


async def test_delete_empty_parent_batch_issues_no_statement(db_session) -> None:  # type: ignore[no-untyped-def]
    with _statements(db_session) as statements:
        await delete_mentions_for(db_session, "waf_endpoint_metric", [])
    assert statements == []


async def test_deleting_instance_cascades_mentions(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    await record_mentions(db_session, [_mention(instance.id, "cascade")])
    assert await _count(db_session) == 1

    await db_session.delete(instance)
    await db_session.flush()

    assert await _count(db_session) == 0


async def test_backfill_defaults(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    row = EntityMentionBackfill(instance_id=instance.id, source="WAF_TOP")
    db_session.add(row)
    await db_session.flush()

    assert row.status == "PENDING"
    assert row.rows_written == 0
    assert row.updated_at is not None


@pytest.mark.parametrize(
    ("column", "unknown", "constraint_name"),
    (
        ("source", "UNKNOWN", "ck_entity_mention_backfill_source_known"),
        ("status", "UNKNOWN", "ck_entity_mention_backfill_status_known"),
    ),
)
async def test_backfill_text_checks_reject_unknown_values(
    db_session, instance: QRadarInstance, column: str, unknown: str, constraint_name: str
) -> None:  # type: ignore[no-untyped-def]
    values = {
        "instance_id": instance.id,
        "source": "WAF_TOP",
        "status": "PENDING",
        "rows_written": 0,
        column: unknown,
    }

    with pytest.raises(IntegrityError, match=constraint_name) as exc_info:
        async with db_session.begin_nested():
            await db_session.execute(pg_insert(EntityMentionBackfill).values(values))
    assert constraint_name in str(exc_info.value)


async def test_backfill_rejects_negative_rows_written(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    constraint_name = "ck_entity_mention_backfill_rows_written_non_negative"
    with pytest.raises(IntegrityError, match=constraint_name) as exc_info:
        async with db_session.begin_nested():
            await db_session.execute(
                pg_insert(EntityMentionBackfill).values(
                    instance_id=instance.id,
                    source="WAF_TOP",
                    status="PENDING",
                    rows_written=-1,
                )
            )
    assert constraint_name in str(exc_info.value)


async def test_backfill_instance_source_pair_is_unique(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    db_session.add(EntityMentionBackfill(instance_id=instance.id, source="WAF_TOP"))
    await db_session.flush()

    constraint_name = "pk_entity_mention_backfill"
    with pytest.raises(IntegrityError, match=constraint_name) as exc_info:
        async with db_session.begin_nested():
            await db_session.execute(
                pg_insert(EntityMentionBackfill).values(
                    instance_id=instance.id,
                    source="WAF_TOP",
                    status="PENDING",
                    rows_written=0,
                )
            )
    assert constraint_name in str(exc_info.value)


async def test_deleting_instance_cascades_backfill_rows(
    db_session, instance: QRadarInstance
) -> None:  # type: ignore[no-untyped-def]
    db_session.add(EntityMentionBackfill(instance_id=instance.id, source="WAF_TOP"))
    await db_session.flush()

    await db_session.delete(instance)
    await db_session.flush()

    count = await db_session.scalar(select(func.count()).select_from(EntityMentionBackfill))
    assert count == 0
