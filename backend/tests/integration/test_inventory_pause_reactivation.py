"""D-102: reconcile retained QRadar-disabled spans through the actual inventory sync."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models.log_source import LogSource
from app.models.log_source_pause import LogSourcePause
from app.services.source_pause_spans import open_pause
from tests.integration.test_inventory_source_pauses import (
    NOW,
    PREVIOUS,
    REASON,
    _edge_statements,
    _record,
    _seed,
    _sync,
)
from tests.integration.test_inventory_source_pauses import (
    rig as _inventory_rig,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]
rig = _inventory_rig


async def _snapshot(maker):
    async with maker() as session:
        flags = list(await session.execute(select(LogSource.id, LogSource.enabled).order_by(
            LogSource.id,
        )))
        spans = list(await session.execute(select(
            LogSourcePause.log_source_id, LogSourcePause.reason,
            LogSourcePause.starts_at, LogSourcePause.ends_at,
        ).order_by(LogSourcePause.log_source_id, LogSourcePause.starts_at)))
        return flags, spans


@pytest.mark.parametrize("seconds,rounded", [(0, 0), (1, 300)])
async def test_rollback_reactivation_closes_retained_span(rig, seconds, rounded):
    engine, maker = rig
    instance_id, [source_id] = await _seed(maker)
    async with maker() as session:
        await _sync(session, instance_id, [(1, False)])
        await session.commit()
    flags, [opened] = await _snapshot(maker)
    assert flags == [(source_id, False)]
    assert opened.log_source_id == source_id
    assert opened.starts_at == PREVIOUS and opened.ends_at is None

    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, True)], pause=False,
                        now=NOW + timedelta(minutes=5))
            await session.commit()
    assert _edge_statements(operations) == []
    flags, spans = await _snapshot(maker)
    assert flags == [(source_id, True)] and spans == [opened]

    observed = NOW.replace(second=0) + timedelta(minutes=10, seconds=seconds)
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, True)], now=observed)
            await session.commit()
    flags, [closed] = await _snapshot(maker)
    assert flags == [(source_id, True)]
    assert closed[:3] == opened[:3]
    assert closed.ends_at == observed.replace(second=0) + timedelta(seconds=rounded)
    assert sum(sql.startswith("UPDATE log_source_pause") for sql, *_ in operations) == 1


async def test_plain_enabled_has_no_pause_writes(rig):
    engine, maker = rig
    instance_id, [source_id] = await _seed(maker)
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, True)])
            await session.commit()
    edge_sql = _edge_statements(operations)
    # The shared reconcile read establishes absence; no helper writes for this source.
    assert len(edge_sql) == 1 and edge_sql[0].startswith("SELECT log_source_pause")
    assert await _snapshot(maker) == ([(source_id, True)], [])


@pytest.mark.parametrize("stored", [False, True])
async def test_unknown_leaves_open_span_untouched(rig, stored):
    engine, maker = rig
    instance_id, [source_id] = await _seed(maker, (stored,))
    async with maker() as session:
        await open_pause(session, [source_id], reason=REASON, starts_at=PREVIOUS, now=NOW)
        await session.commit()
    before = await _snapshot(maker)
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, None)])
            await session.commit()
    assert _edge_statements(operations) == []
    assert await _snapshot(maker) == before


async def test_mixed_batch_one_bulk_read_and_idempotent(rig):
    engine, maker = rig
    instance_id, ids = await _seed(maker, (False, True, True, True, False))
    async with maker() as session:
        await open_pause(session, [*ids[:2], ids[4]], reason=REASON, starts_at=PREVIOUS, now=NOW)
        await session.commit()
    # Duplicate explicit true must still close each source just once, from the final DTO.
    # The unchanged disabled control makes the legacy reconcile read run in this batch too.
    values = [(1, True), (2, False), (2, True), (3, True), (4, False), (5, False)]
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, values)
            await session.commit()
    reads = [op for op in operations if op[0].startswith("SELECT log_source_pause")]
    assert len(reads) == 1
    assert sum(sql.startswith("SELECT ") for sql, *_ in operations) == 6
    sql, params, _, _ = reads[0]
    assert "= ANY (" in sql and len(params) == 2
    assert list(params[0]) == ids
    pause_updates = [op for op in operations if op[0].startswith("UPDATE log_source_pause")]
    assert len(pause_updates) == 1
    _, params, positions, _ = pause_updates[0]
    assert list(params[positions.index("ids")]) == ids[:2]
    assert sum(sql.startswith("INSERT INTO log_source_pause") for sql, *_ in operations) == 1
    source_updates = [i for i, (sql, *_) in enumerate(operations)
                      if sql.startswith("UPDATE log_source SET")]
    first_edge = next(i for i, (sql, *_) in enumerate(operations)
                      if sql.startswith("INSERT INTO log_source_pause"))
    assert source_updates and max(source_updates) < first_edge
    flags, spans = await _snapshot(maker)
    assert flags == list(zip(ids, (True, True, True, False, False), strict=True))
    assert [s.log_source_id for s in spans] == [ids[0], ids[1], ids[3], ids[4]]
    assert [s.starts_at for s in spans] == [PREVIOUS] * 4
    end = NOW.replace(second=0) + timedelta(minutes=5)
    assert [s.ends_at for s in spans] == [end, end, None, None]

    async with maker() as session:
        with _record(engine) as repeated:
            await _sync(session, instance_id, values, now=NOW + timedelta(hours=1))
            await session.commit()
    edge_sql = _edge_statements(repeated)
    assert len(edge_sql) == 1 and edge_sql[0].startswith("SELECT log_source_pause")
    assert sum(sql.startswith("SELECT ") for sql, *_ in repeated) == 5
    assert await _snapshot(maker) == (flags, spans)


async def test_final_unknown_and_closed_history_do_not_close(rig):
    engine, maker = rig
    instance_id, ids = await _seed(maker, (True, True))
    async with maker() as session:
        await open_pause(session, [ids[0]], reason=REASON, starts_at=PREVIOUS, now=NOW)
        session.add(LogSourcePause(
            log_source_id=ids[1], reason=REASON, starts_at=PREVIOUS,
            ends_at=PREVIOUS + timedelta(minutes=5),
        ))
        await session.commit()
    before = await _snapshot(maker)
    async with maker() as session:
        with _record(engine) as operations:
            await _sync(session, instance_id, [(1, True), (1, None), (2, True)])
            await session.commit()
    [(sql, params, _, _)] = [op for op in operations
                            if op[0].startswith("SELECT log_source_pause")]
    assert list(params[0]) == [ids[1]] and params[1] == REASON
    assert _edge_statements(operations) == [sql]
    assert await _snapshot(maker) == before
