"""JDBC-1 J1m2b: the repository lists and details JDBC sources in a fixed number of SELECTs.

Gated on TEST_DATABASE_URL (the shared async schema fixture, one file at a time per database).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.jdbc.flow import target_bucket
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.repositories.jdbc import JdbcQueryError, JdbcRepository

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 12, 3, tzinfo=UTC)
WIDTH = CADENCE = 300
START, END = target_bucket(NOW, cadence_seconds=CADENCE, width_seconds=WIDTH, anchor=0)
TIMING: dict[str, Any] = {
    "now": NOW, "cadence_seconds": CADENCE, "width_seconds": WIDTH, "anchor": 0,
    "inventory_cadence_seconds": 3600,
}


def metric(source: uuid.UUID, start: datetime = START, **over: Any) -> dict[str, Any]:
    row: dict[str, Any] = {
        "log_source_id": source, "bucket_start": start, "bucket_seconds": WIDTH,
        "event_count": 9, "average_eps": 0.03, "peak_eps": 0.0, "unknown_event_count": 0,
        "stored_event_count": 0, "distinct_qid_count": 0, "distinct_username_count": 0,
        "distinct_source_ip_count": 0, "collection_error_count": 0,
        "completeness": "COMPLETE", "collection_source": "ariel_aggregate",
        "query_provenance": {}, "collected_at": NOW,
    }
    row.update(over)
    return row


async def console(session: AsyncSession) -> uuid.UUID:
    instance = QRadarInstance(name=f"q-{uuid.uuid4()}", console_host="qradar.lab.local")
    session.add(instance)
    await session.flush()
    return instance.id


async def sources(
    session: AsyncSession, instance: uuid.UUID, count: int, *, first: int = 1, **fields: Any,
) -> list[uuid.UUID]:
    rows = [
        LogSource(
            instance_id=instance, qradar_id=first + n,
            name=fields.get("name") or f"src-{first + n:04d}",
            protocol_type_id=fields.get("protocol", 8),
            enabled=fields.get("enabled", True), monitoring_enabled=True,
        )
        for n in range(count)
    ]
    session.add_all(rows)
    await session.flush()
    return [r.id for r in rows]


class Counter:
    """Records every statement sent on the session's connection."""

    def __init__(self, connection: Any) -> None:
        self.connection, self.seen = connection, []

    def _record(self, conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        self.seen.append(statement)

    def __enter__(self) -> Counter:
        event.listen(self.connection.sync_connection, "before_cursor_execute", self._record)
        return self

    def __exit__(self, *exc: Any) -> None:
        event.remove(self.connection.sync_connection, "before_cursor_execute", self._record)


async def counted(session: AsyncSession, call: Any) -> tuple[Any, list[str]]:
    with Counter(await session.connection()) as counter:
        result = await call
    assert all(s.lstrip().upper().startswith("SELECT") for s in counter.seen)
    return result, counter.seen


@pytest.mark.parametrize("size", [1, 100, 800])
async def test_four_data_selects_1_100_800_matches(db_session: AsyncSession, size: int) -> None:
    instance = await console(db_session)
    ids = await sources(db_session, instance, size)
    await db_session.execute(insert(LogSourceMetric), [metric(i) for i in ids])
    repo = JdbcRepository(db_session)

    result, seen = await counted(db_session, repo.list(limit=1000, **TIMING))
    assert len(seen) == 4
    assert result.total == size and len(result.items) == min(size, repo.settings.api_max_page_size)

    # A flow filter and a small page cost the same four.
    _, seen = await counted(db_session, repo.list(limit=5, flow_state="RECEIVING", **TIMING))
    assert len(seen) == 4

    # An empty page has no ids to look up, so the last-observation read is skipped.
    empty, seen = await counted(db_session, repo.list(offset=size + 10, **TIMING))
    assert (len(seen), empty.items, empty.total) == (3, [], size)

    # Detail is one source with the same ceiling.
    found, seen = await counted(db_session, repo.detail(ids[0], **TIMING))
    assert found is not None and len(seen) <= 4
    assert (found.flow.state, found.volume.event_count) == ("RECEIVING", 9)


async def test_stable_page_q_wildcards_and_filters(db_session: AsyncSession) -> None:
    one, two = await console(db_session), await console(db_session)
    same = await sources(db_session, one, 7, name="same")  # equal names, UUID breaks the tie
    pct = (await sources(db_session, one, 1, first=20, name="audit_100%_db"))[0]
    await sources(db_session, one, 1, first=21, name="auditX100Y_db")
    slash = (await sources(db_session, one, 1, first=22, name="back\\slash"))[0]
    off = (await sources(db_session, one, 1, first=23, name="off", enabled=False))[0]
    other = (await sources(db_session, two, 1, first=30, name="elsewhere", protocol=20))[0]
    await sources(db_session, one, 1, first=40, name="not-jdbc", protocol=1)
    await db_session.execute(insert(LogSourceMetric), [metric(i) for i in same[:3]])
    repo = JdbcRepository(db_session)

    async def listed(**kw: Any) -> Any:
        return await repo.list(**TIMING, **kw)

    walked: list[uuid.UUID] = []
    for offset in range(0, 12, 3):
        walked += [i.id for i in (await listed(limit=3, offset=offset)).items]
    assert len(walked) == len(set(walked)) == 12  # no overlap, no skip; protocol 1 absent
    assert walked == [i.id for i in (await listed(limit=100)).items]
    assert [i.id for i in (await listed(q="  _100%_ ")).items] == [pct]  # literal % and _
    assert [i.id for i in (await listed(q="\\")).items] == [slash]
    assert [i.id for i in (await listed(enabled=False)).items] == [off]
    assert (await listed(enabled=True)).total == 11
    assert [i.id for i in (await listed(protocol=20)).items] == [other]
    assert [i.id for i in (await listed(instance_id=two)).items] == [other]
    assert (await listed(protocol=20, instance_id=one)).total == 0  # combined

    # The flow filter narrows total and page together.
    receiving = await listed(flow_state="RECEIVING", limit=2)
    assert (receiving.total, len(receiving.items)) == (3, 2)
    both = await listed(flow_state="RECEIVING", q="same", enabled=True, limit=2, offset=2)
    assert (both.total, len(both.items), both.items[0].flow.state) == (3, 1, "RECEIVING")
    assert (await listed(flow_state="DISABLED_IN_QRADAR")).total == 1
    assert (await listed(flow_state="NOT_MEASURED")).total == 8


async def test_partial_inventory_total_qualified(db_session: AsyncSession) -> None:
    instance = await console(db_session)
    ids = await sources(db_session, instance, 3)
    unverified = (await sources(db_session, instance, 1, first=9, protocol=20))[0]
    await db_session.execute(
        insert(LogSourceMetric), [metric(ids[0]), metric(ids[1], event_count=0)])
    repo = JdbcRepository(db_session)

    fresh = await repo.list(**TIMING)
    assert fresh.snapshot.state == "NOT_COLLECTED" and not fresh.snapshot.complete

    old = NOW - timedelta(days=5)
    mark = CollectionWatermark(
        instance_id=instance, collector="log_source_inventory", watermark_at=old,
        last_run_at=NOW, collection_metadata={"inventory_pass": {"status": "PARTIAL"}})
    db_session.add(mark)
    await db_session.flush()
    result = await repo.list(**TIMING)
    # Every stored row is listed and no flow state changes because the inventory is old.
    assert result.total == 4 and len(result.items) == 4
    states = {i.id: i.flow.state for i in result.items}
    assert states[ids[0]] == "RECEIVING" and states[ids[1]] == "NO_ATTRIBUTED_EVENTS"
    assert states[ids[2]] == states[unverified] == "NOT_MEASURED"
    snap = result.snapshot
    assert (snap.state, snap.stale, snap.complete, snap.last_complete_at) == (
        "PARTIAL", True, False, old)
    row = result.items[0].qradar
    assert (row.inventory_state, row.inventory_stale, row.reason_code) == (
        "PARTIAL", True, "inventory_stale")
    assert row.inventory_last_complete_at == old  # the pass clock, never LogSource.updated_at

    # A fresh COMPLETE pass makes the snapshot complete; the rows themselves are unchanged.
    mark.watermark_at = NOW
    mark.collection_metadata = {"inventory_pass": {"status": "COMPLETE"}}
    await db_session.flush()
    again = await repo.list(**TIMING)
    assert again.snapshot.complete and not again.snapshot.stale
    assert {i.id: i.flow.state for i in again.items} == states


async def test_detail_same_bound(db_session: AsyncSession) -> None:
    instance = await console(db_session)
    [verified] = await sources(db_session, instance, 1)
    [unverified] = await sources(db_session, instance, 1, first=2, protocol=20)
    [plain] = await sources(db_session, instance, 1, first=3, protocol=1)
    db_session.add(JdbcSourceConfig(
        log_source_id=verified, protocol_type_id=8, database_type="Postgres", field_states={
            "database_type": {"code": "SET", "reason": None}},
        allowlist_version=1, config_state="COMPLETE", observed_at=NOW, last_success_at=NOW))
    await db_session.flush()
    repo = JdbcRepository(db_session)

    row, seen = await counted(db_session, repo.detail(verified, **TIMING))
    assert len(seen) <= 4 and row.protocol_name == "JDBC"
    assert row.config.safe_fields is not None
    assert row.config.safe_fields.database_type == "Postgres"
    assert row.config.state.value == "COMPLETE"
    assert row.volume.event_count is None and row.volume.last_observation is None

    row, seen = await counted(db_session, repo.detail(unverified, **TIMING))
    assert len(seen) <= 4 and row is not None
    assert row.config.support == "UNVERIFIED_PROTOCOL" and row.config.safe_fields is None

    for missing in (plain, uuid.uuid4()):  # non-JDBC protocol, unknown id
        row, seen = await counted(db_session, repo.detail(missing, **TIMING))
        assert row is None and len(seen) == 1


@pytest.mark.parametrize("kw", [
    {"offset": -1}, {"q": "x" * 201}, {"q": " " + "x" * 201}, {"protocol": 1},
    {"protocol": 999}, {"flow_state": "NOPE"}, {"enabled": "yes"}, {"instance_id": "not-a-uuid"},
    {"limit": "10"},
])
async def test_bounds_rejected_without_sql(db_session: AsyncSession, kw: dict[str, Any]) -> None:
    repo = JdbcRepository(db_session)
    with Counter(await db_session.connection()) as counter, pytest.raises(JdbcQueryError):
        await repo.list(**TIMING, **kw)
    assert counter.seen == []


async def test_limit_default_and_clamp_and_q_bound(db_session: AsyncSession) -> None:
    repo = JdbcRepository(db_session)
    maximum = repo.settings.api_max_page_size
    assert repo.bounds()[:2] == (min(100, maximum), 0)
    assert repo.bounds(limit=0)[0] == 1
    assert repo.bounds(limit=-5)[0] == 1
    assert repo.bounds(limit=maximum + 1)[0] == maximum
    assert repo.bounds(q="x" * 200)[2].q == "x" * 200
    assert repo.bounds(q="   ")[2].q is None
