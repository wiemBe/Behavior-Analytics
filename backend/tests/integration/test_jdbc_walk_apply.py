"""FIX-97 / D-96 (a): a real walk's content-rejected row reaches apply.

The walk runs over MockTransport, not a hand-built DTO list, so the pass-through
of a NOT_COLLECTED config and the walk's COMPLETE state are both exercised.
Gated on TEST_DATABASE_URL; each test owns the shared schema fixture.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.jdbc.dto import ConfigState, JdbcConfigWalk, JdbcRowRejection, JdbcWalkState
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig
from app.models.log_source import LogSource
from app.providers.qradar_rest import QRadarRestProvider
from app.services import jdbc_sync
from app.services.jdbc_sync import apply_jdbc_walk

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=1)

BAD_QID = 99  # returns empty protocol_parameters, so the projector cannot collect it
GOOD_QIDS = tuple(range(99))  # its 99 neighbours on the same page
OMITTED_QID = 100  # in inventory but absent from the walk

#: The 18 allow-listed parameters with valid values, so neighbours project COMPLETE.
COMPLETE_PARAMETERS = [
    {"id": 8000, "name": "identifier", "value": "labjdbc-flowing"},
    {"id": 8005, "name": "databaseType", "value": "Postgres"},
    {"id": 8010, "name": "databaseName", "value": "app_audit"},
    {"id": 8017, "name": "schema", "value": "public"},
    {"id": 8020, "name": "databaseServerHostname", "value": "db.example.com"},
    {"id": 8030, "name": "databasePort", "value": "5432"},
    {"id": 8080, "name": "queryList", "value": "none"},
    {"id": 8090, "name": "eventTable", "value": "app_audit_flowing"},
    {"id": 8100, "name": "eventQuery", "value": "id,event_time"},
    {"id": 8110, "name": "compareField", "value": "id"},
    {"id": 8120, "name": "usePreparedStatements", "value": "true"},
    {"id": 8130, "name": "startTime", "value": "2026-11-20 10:30"},
    {"id": 8140, "name": "pollingInterval", "value": "30"},
    {"id": 8150, "name": "epsThrottle", "value": "20000"},
    {"id": 8210, "name": "useSSL", "value": "false"},
    {"id": 8280, "name": "MSSQLServerHostname", "value": "tls.example.com"},
    {"id": 8290, "name": "enableAdvancedOptions", "value": "false"},
    {"id": 8320, "name": "allowUntrusted", "value": "false"},
]


def upstream_row(qradar_id: int) -> dict[str, Any]:
    return {"id": qradar_id, "protocol_type_id": 8, "protocol_parameters": COMPLETE_PARAMETERS}


async def real_walk() -> JdbcConfigWalk:
    """One full page of 99 good rows plus one empty-array row, then a short terminator."""

    def handler(request: httpx.Request) -> httpx.Response:
        start, _ = map(int, request.headers["Range"].removeprefix("items=").split("-"))
        if start:
            return httpx.Response(200, json=[])
        body = [upstream_row(qid) for qid in GOOD_QIDS]
        body.append({"id": BAD_QID, "protocol_type_id": 8, "protocol_parameters": []})
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api"
    )
    walker = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="t", client=client
    )
    try:
        return await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()


async def seed(session: AsyncSession, qids: tuple[int, ...]) -> uuid.UUID:
    instance = QRadarInstance(
        name=f"jdbc-walk-apply-{uuid.uuid4()}", console_host="qradar.lab.local"
    )
    session.add(instance)
    await session.flush()
    for qid in qids:
        session.add(
            LogSource(
                instance_id=instance.id, qradar_id=qid, name=f"src-{qid}", protocol_type_id=8
            )
        )
    await session.flush()
    return instance.id


async def by_qid(session: AsyncSession, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
    rows = (
        await session.execute(
            select(LogSource.qradar_id, LogSource.id).where(
                LogSource.instance_id == instance_id
            )
        )
    ).all()
    return dict(rows)


def put(session: AsyncSession, lsid: uuid.UUID, **overrides: Any) -> None:
    values: dict[str, Any] = dict(
        log_source_id=lsid,
        protocol_type_id=8,
        field_states={},
        allowlist_version=1,
        config_state=ConfigState.COMPLETE.value,
        observed_at=OLD,
        last_success_at=OLD,
        database_host="good.example.com",
        database_port=5432,
        schema_name="public",
        table_name="app_audit_flowing",
    )
    values.update(overrides)
    session.add(JdbcSourceConfig(**values))


async def stored(session: AsyncSession, instance_id: uuid.UUID) -> dict[int, dict[str, Any]]:
    rows = (
        await session.execute(
            select(LogSource.qradar_id, JdbcSourceConfig)
            .join(JdbcSourceConfig, JdbcSourceConfig.log_source_id == LogSource.id)
            .where(LogSource.instance_id == instance_id)
            .execution_options(populate_existing=True)
        )
    ).all()
    columns = JdbcSourceConfig.__table__.columns
    return {
        qid: {column.name: getattr(row, column.name) for column in columns}
        for qid, row in rows
    }


async def test_walk_content_rejection_keeps_stored_values_and_refreshes_neighbours(
    db_session: AsyncSession,
) -> None:
    instance_id = await seed(db_session, (*GOOD_QIDS, BAD_QID))
    lsids = await by_qid(db_session, instance_id)
    for qid in GOOD_QIDS:
        put(db_session, lsids[qid])
    put(db_session, lsids[BAD_QID], database_host="last-good.example.com")
    await db_session.flush()

    result = await real_walk()
    assert result.state is JdbcWalkState.COMPLETE and result.complete
    assert result.rejected_rows == {JdbcRowRejection.PARAMETERS_UNAVAILABLE: 1}
    summary = await apply_jdbc_walk(db_session, instance_id, result, now=NOW)
    after = await stored(db_session, instance_id)

    assert summary.written == 100
    assert summary.marked_absent == 0
    bad = after[BAD_QID]
    assert bad["config_state"] == ConfigState.NOT_COLLECTED.value
    assert bad["failure_code"] == jdbc_sync.FAILURE_PARAMETERS_UNAVAILABLE
    assert bad["database_host"] == "last-good.example.com"
    assert bad["database_port"] == 5432
    assert bad["observed_at"] == OLD  # a stale read keeps its timestamps
    assert bad["last_success_at"] == OLD
    for qid in GOOD_QIDS:
        neighbour = after[qid]
        assert neighbour["config_state"] == ConfigState.COMPLETE.value
        assert neighbour["observed_at"] == NOW


async def test_walk_content_rejection_without_stored_row_writes_null(
    db_session: AsyncSession,
) -> None:
    instance_id = await seed(db_session, (*GOOD_QIDS, BAD_QID))
    result = await real_walk()

    await apply_jdbc_walk(db_session, instance_id, result, now=NOW)
    after = await stored(db_session, instance_id)

    bad = after[BAD_QID]
    assert bad["config_state"] == ConfigState.NOT_COLLECTED.value
    assert bad["failure_code"] == jdbc_sync.FAILURE_PARAMETERS_UNAVAILABLE
    assert bad["observed_at"] == NOW
    assert bad["last_success_at"] is None
    assert bad["field_states"] == {}
    assert all(bad[member] is None for member in jdbc_sync.SAFE_MEMBERS)


async def test_complete_walk_marks_omitted_absent_not_content_rejected(
    db_session: AsyncSession,
) -> None:
    instance_id = await seed(db_session, (*GOOD_QIDS, BAD_QID, OMITTED_QID))
    lsids = await by_qid(db_session, instance_id)
    put(db_session, lsids[BAD_QID])
    put(db_session, lsids[OMITTED_QID], database_host="absent.example.com")
    await db_session.flush()

    result = await real_walk()
    assert result.complete
    summary = await apply_jdbc_walk(db_session, instance_id, result, now=NOW)
    after = await stored(db_session, instance_id)

    assert summary.marked_absent == 1
    absent = after[OMITTED_QID]
    assert absent["config_state"] == ConfigState.NOT_COLLECTED.value
    assert absent["failure_code"] == jdbc_sync.FAILURE_ABSENT
    assert absent["database_host"] == "absent.example.com"
    assert absent["observed_at"] == OLD
    content = after[BAD_QID]
    assert content["config_state"] == ConfigState.NOT_COLLECTED.value
    assert content["failure_code"] == jdbc_sync.FAILURE_PARAMETERS_UNAVAILABLE
