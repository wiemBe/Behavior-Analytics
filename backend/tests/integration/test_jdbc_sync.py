"""JDBC-1 J1c2: applying a walk writes safe snapshots, counts and honesty states.

Gated on TEST_DATABASE_URL. Walk results are built through the real J1a
projector and J1b result type; no hand-made config dicts reach the apply.
Each test uses the shared async schema fixture, so it must not run beside
another integration suite on the same database.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import Insert

from app.jdbc.dto import (
    ConfigState,
    FieldStateCode,
    FieldStateReason,
    JdbcConfigWalk,
    JdbcWalkState,
)
from app.jdbc.parameters import project_jdbc_config
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig
from app.models.log_source import LogSource
from app.services import jdbc_sync
from app.services.jdbc_sync import apply_jdbc_walk

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=1)
OLDER = NOW - timedelta(days=2)

# Upstream name -> (parameter id, safe member), design §2 / JDBC-0.report.md.
FIELDS = {
    "identifier": (8000, "source_label"),
    "databaseType": (8005, "database_type"),
    "databaseName": (8010, "database_name"),
    "schema": (8017, "schema_name"),
    "databaseServerHostname": (8020, "database_host"),
    "databasePort": (8030, "database_port"),
    "queryList": (8080, "predefined_query"),
    "eventTable": (8090, "table_name"),
    "eventQuery": (8100, "select_list"),
    "compareField": (8110, "compare_field"),
    "usePreparedStatements": (8120, "prepared_statements"),
    "startTime": (8130, "start_time_local"),
    "pollingInterval": (8140, "polling_interval_seconds"),
    "epsThrottle": (8150, "eps_throttle"),
    "useSSL": (8210, "use_ssl"),
    "MSSQLServerHostname": (8280, "tls_hostname"),
    "enableAdvancedOptions": (8290, "advanced_options"),
    "allowUntrusted": (8320, "allow_untrusted"),
}
VALID = {
    "identifier": "labjdbc-flowing",
    "databaseType": "Postgres",
    "databaseName": "app_audit",
    "schema": "public",
    "databaseServerHostname": "db.example.com",
    "databasePort": "5432",
    "queryList": "none",
    "eventTable": "app_audit_flowing",
    "eventQuery": "id,event_time",
    "compareField": "id",
    "usePreparedStatements": "true",
    "startTime": "2026-11-20 10:30",
    "pollingInterval": "30",
    "epsThrottle": "20000",
    "useSSL": "false",
    "MSSQLServerHostname": "tls.example.com",
    "enableAdvancedOptions": "false",
    "allowUntrusted": "false",
}


def full_array(overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    values = dict(VALID)
    if overrides:
        values.update(overrides)
    return [
        {"id": FIELDS[name][0], "name": name, "value": values[name]} for name in FIELDS
    ]


def dto(
    qradar_id: int,
    *,
    overrides: dict[str, Any] | None = None,
    remove: tuple[str, ...] = (),
    protocol: int = 8,
    params: object = "array",
) -> Any:
    array: object = full_array(overrides)
    if params == "missing":
        array = None
    elif isinstance(array, list):
        array = [element for element in array if element["name"] not in remove]
    return project_jdbc_config(
        qradar_id=qradar_id, protocol_type_id=protocol, protocol_parameters=array
    )


def walk(*configs: Any, complete: bool = True) -> JdbcConfigWalk:
    state = JdbcWalkState.COMPLETE if complete else JdbcWalkState.PARTIAL
    return JdbcConfigWalk(configs=list(configs), state=state)


async def seed(session: AsyncSession, sources: list[tuple[int, int]]) -> uuid.UUID:
    """Create a console and its (qradar_id, protocol_type_id) inventory."""
    instance = QRadarInstance(
        name=f"jdbc-apply-{uuid.uuid4()}", console_host="qradar.lab.local"
    )
    session.add(instance)
    await session.flush()
    for qradar_id, protocol in sources:
        session.add(
            LogSource(
                instance_id=instance.id,
                qradar_id=qradar_id,
                name=f"src-{qradar_id}",
                protocol_type_id=protocol,
            )
        )
    await session.flush()
    return instance.id


async def ids(session: AsyncSession, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
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
    )
    values.update(overrides)
    session.add(JdbcSourceConfig(**values))


async def configs(session: AsyncSession, instance_id: uuid.UUID) -> dict[int, dict[str, Any]]:
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


def capture(session: AsyncSession) -> tuple[list[str], Callable[[], None]]:
    bind = session.get_bind()
    engine = getattr(bind, "sync_engine", bind)
    statements: list[str] = []

    def before(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: bool
    ) -> None:
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", before)
    return statements, lambda: event.remove(engine, "before_cursor_execute", before)


async def test_replace_clears_removed_and_invalid_fields(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    lsid = (await ids(db_session, instance_id))[100]
    put(
        db_session,
        lsid,
        database_host="old.example.com",
        schema_name="public",
        table_name="old_table",
        database_port=5432,
    )
    await db_session.flush()

    changed = dto(
        100,
        overrides={
            "databaseServerHostname": "new.example.com",
            "eventTable": "",
            "databasePort": "70000",
        },
        remove=("schema",),
    )
    summary = await apply_jdbc_walk(db_session, instance_id, walk(changed), now=NOW)
    row = (await configs(db_session, instance_id))[100]

    assert summary.written == 1
    assert row["config_state"] == ConfigState.PARTIAL.value
    assert row["failure_code"] is None
    assert row["database_host"] == "new.example.com"
    assert row["field_states"]["database_host"]["code"] == FieldStateCode.SET.value
    assert row["schema_name"] is None
    assert row["field_states"]["schema_name"]["code"] == "REMOVED"
    assert row["field_states"]["schema_name"]["reason"] is None
    assert row["table_name"] is None
    assert row["field_states"]["table_name"]["code"] == FieldStateCode.UNSET.value
    assert row["database_port"] is None
    assert row["field_states"]["database_port"]["code"] == FieldStateCode.UNAVAILABLE.value
    reason = row["field_states"]["database_port"]["reason"]
    assert reason == FieldStateReason.UNSUPPORTED_VALUE.value
    assert row["observed_at"] == NOW
    assert row["last_success_at"] == OLD  # PARTIAL does not advance it


async def test_bad_array_keeps_old_snapshot_stale(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    lsid = (await ids(db_session, instance_id))[100]
    put(db_session, lsid, database_host="good.example.com", database_port=5432)
    await db_session.flush()

    bad = dto(100, params="missing")
    assert bad.config_state is ConfigState.NOT_COLLECTED
    statements, stop = capture(db_session)
    try:
        summary = await apply_jdbc_walk(db_session, instance_id, walk(bad), now=NOW)
    finally:
        stop()
    row = (await configs(db_session, instance_id))[100]

    # A stale read must not issue a snapshot-replacing update: the ON CONFLICT
    # clause touches only the read state, never a value column.
    updates = [s.split("DO UPDATE SET", 1)[1] for s in statements if "DO UPDATE SET" in s]
    assert len(updates) == 1
    assert "database_host" not in updates[0]

    assert summary.written == 1
    assert row["database_host"] == "good.example.com"
    assert row["database_port"] == 5432
    assert row["observed_at"] == OLD
    assert row["last_success_at"] == OLD
    assert row["config_state"] == ConfigState.NOT_COLLECTED.value
    assert row["failure_code"] == jdbc_sync.FAILURE_PARAMETERS_UNAVAILABLE


async def test_not_collected_without_previous_inserts_nulls(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    await apply_jdbc_walk(db_session, instance_id, walk(dto(100, params="missing")), now=NOW)
    row = (await configs(db_session, instance_id))[100]

    assert row["config_state"] == ConfigState.NOT_COLLECTED.value
    assert row["failure_code"] == jdbc_sync.FAILURE_PARAMETERS_UNAVAILABLE
    assert row["observed_at"] == NOW
    assert row["last_success_at"] is None
    assert row["field_states"] == {}
    assert all(row[member] is None for member in jdbc_sync.SAFE_MEMBERS)


async def test_partial_keeps_unseen_last_success(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8), (200, 8)])
    by_qid = await ids(db_session, instance_id)
    put(db_session, by_qid[100], last_success_at=OLD)
    put(
        db_session,
        by_qid[200],
        last_success_at=OLDER,
        observed_at=OLDER,
        field_states={"database_host": {"code": "SET", "reason": None}},
    )
    await db_session.flush()

    seen = dto(100, remove=("schema",))
    summary = await apply_jdbc_walk(db_session, instance_id, walk(seen, complete=False), now=NOW)
    after = await configs(db_session, instance_id)

    assert summary.marked_absent == 0
    assert after[100]["observed_at"] == NOW
    assert after[100]["last_success_at"] == OLD  # seen but PARTIAL
    assert after[200]["config_state"] == ConfigState.COMPLETE.value
    assert after[200]["observed_at"] == OLDER
    assert after[200]["last_success_at"] == OLDER  # unseen and walk not complete


async def test_unmatched_changed_protocol_not_written(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8), (200, 9), (300, None)])
    by_qid = await ids(db_session, instance_id)
    put(db_session, by_qid[200])
    put(db_session, by_qid[300])
    await db_session.flush()

    statements, stop = capture(db_session)
    try:
        summary = await apply_jdbc_walk(
            db_session,
            instance_id,
            walk(dto(999), dto(200), dto(300)),
            now=NOW,
        )
    finally:
        stop()
    after = await configs(db_session, instance_id)
    inventory_count = (
        await db_session.execute(
            select(LogSource).where(LogSource.instance_id == instance_id)
        )
    ).all()

    assert summary.unmatched == 1
    assert summary.skipped_protocol == 2  # a non-8 id and a null id
    assert summary.cleared == 2
    assert 999 not in after and 200 not in after and 300 not in after and 100 not in after
    assert len(inventory_count) == 3  # never create or delete inventory rows
    assert any(
        statement.lstrip().upper().startswith("DELETE")
        and "protocol_type_id" in statement
        for statement in statements
    )


async def test_protocol_change_between_read_and_write_is_cleared(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A protocol leaving 8 after the inventory read is caught by the write-time DELETE."""
    instance_id = await seed(db_session, [(100, 8)])
    lsid = (await ids(db_session, instance_id))[100]
    put(db_session, lsid, database_host="stale.example.com")
    await db_session.flush()

    real_execute = db_session.execute
    flipped = False

    async def execute(statement: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal flipped
        if not flipped and isinstance(statement, Insert):
            # The inventory was already read as protocol 8. Flip it to a
            # non-JDBC protocol before the first write lands, as a concurrent
            # inventory sync would, then let the write proceed on the stale read.
            flipped = True
            await real_execute(
                update(LogSource).where(LogSource.id == lsid).values(protocol_type_id=9)
            )
        return await real_execute(statement, *args, **kwargs)

    monkeypatch.setattr(db_session, "execute", execute)
    summary = await apply_jdbc_walk(db_session, instance_id, walk(dto(100)), now=NOW)

    assert flipped is True  # the race actually fired
    assert summary.written == 1  # Python wrote it from the stale inventory read
    assert summary.cleared == 1  # the post-write recheck removed the row it wrote
    assert 100 not in await configs(db_session, instance_id)


async def test_complete_parameter_omission_not_deletion(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8), (200, 8)])
    by_qid = await ids(db_session, instance_id)
    put(db_session, by_qid[100])
    put(db_session, by_qid[200], database_host="omitted.example.com")
    await db_session.flush()

    summary = await apply_jdbc_walk(db_session, instance_id, walk(dto(100)), now=NOW)
    after = await configs(db_session, instance_id)

    assert summary.marked_absent == 1
    assert set(after) == {100, 200}  # omission is not deletion
    assert after[200]["config_state"] == ConfigState.NOT_COLLECTED.value
    assert after[200]["failure_code"] == jdbc_sync.FAILURE_ABSENT
    assert after[200]["database_host"] == "omitted.example.com"
    assert after[200]["last_success_at"] == OLD
    assert after[100]["config_state"] == ConfigState.COMPLETE.value
    assert after[100]["last_success_at"] == NOW


async def test_500_row_chunks_no_n_plus_one(db_session: AsyncSession) -> None:
    qids = list(range(1, 1202))
    instance_id = await seed(db_session, [(qid, 8) for qid in qids])
    configs_in = [dto(qid) for qid in qids]

    statements, stop = capture(db_session)
    try:
        summary = await apply_jdbc_walk(db_session, instance_id, walk(*configs_in), now=NOW)
    finally:
        stop()

    assert summary.written == 1201
    assert summary.chunks == 3  # ceil(1201 / 500)
    # Two console-wide reads, three upserts and one instance-scoped recheck
    # DELETE. A per-source statement would make this four figures, not six.
    assert len(statements) == 6


CANARIES = {
    "password": "CANARY-PASSWORD-9f3a",
    "username": "CANARY-USERNAME-7c1b",
    "keyfile": "CANARY-KEYFILE-2e8d",
    "unknown": "CANARY-UNKNOWN-4a6f",
    "malformed": "CANARY-MALFORMED-1b5c",
    "connection": "jdbc:postgresql://db/app?user=svc&password=CANARY-CONNECTION-8d2e",
}


async def test_secret_canaries_not_stored(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    lsid = (await ids(db_session, instance_id))[100]
    put(db_session, lsid, database_host="good.example.com")
    await db_session.flush()

    array = full_array({"databaseName": CANARIES["connection"]})
    array.extend(
        [
            {"id": 8050, "name": "databasePassword", "value": CANARIES["password"]},
            {"id": 8040, "name": "databaseUsername", "value": CANARIES["username"]},
            {"id": 8052, "name": "privateKeyFileName", "value": CANARIES["keyfile"]},
            {"id": 99999, "name": "futureCredential", "value": CANARIES["unknown"]},
            CANARIES["malformed"],
        ]
    )
    projected = project_jdbc_config(
        qradar_id=100, protocol_type_id=8, protocol_parameters=array
    )
    await apply_jdbc_walk(db_session, instance_id, walk(projected), now=NOW)

    stored = (
        await db_session.execute(
            text("SELECT to_jsonb(c)::text FROM jdbc_source_config c")
        )
    ).scalar_one()
    for canary in CANARIES.values():
        assert canary not in stored
    row = (await configs(db_session, instance_id))[100]
    assert row["database_name"] is None  # connection-shaped canary rejected
    assert row["database_host"] == "db.example.com"  # safe projected value replaced the old one


async def test_complete_empty_walk_is_unproven(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    lsid = (await ids(db_session, instance_id))[100]
    put(db_session, lsid)
    await db_session.flush()

    summary = await apply_jdbc_walk(db_session, instance_id, walk(), now=NOW)
    after = (await configs(db_session, instance_id))[100]

    assert summary.empty_unproven is True
    assert summary.marked_absent == 0
    assert summary.written == 0
    assert after["config_state"] == ConfigState.COMPLETE.value  # nothing marked


async def test_empty_walk_without_jdbc_inventory_is_not_unproven(
    db_session: AsyncSession,
) -> None:
    instance_id = await seed(db_session, [(100, 9)])
    summary = await apply_jdbc_walk(db_session, instance_id, walk(), now=NOW)
    assert summary.empty_unproven is False
    assert await configs(db_session, instance_id) == {}


async def test_unverified_and_not_jdbc_not_stored(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8), (200, 8)])
    unverified = dto(100, protocol=20)
    not_jdbc = dto(200, protocol=99)
    assert unverified.config_state is ConfigState.UNVERIFIED_PROTOCOL
    assert not_jdbc.config_state is ConfigState.NOT_JDBC

    summary = await apply_jdbc_walk(db_session, instance_id, walk(unverified, not_jdbc), now=NOW)
    assert summary.not_stored_unverified == 2
    assert summary.written == 0
    assert await configs(db_session, instance_id) == {}


async def test_validate_field_states_called_on_replace(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    calls = 0
    original = jdbc_sync.validate_field_states

    def spy(value: dict[str, Any]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return original(value)

    monkeypatch.setattr(jdbc_sync, "validate_field_states", spy)
    await apply_jdbc_walk(db_session, instance_id, walk(dto(100)), now=NOW)
    assert calls == 1


async def test_apply_does_not_commit(db_session: AsyncSession) -> None:
    instance_id = await seed(db_session, [(100, 8)])
    await apply_jdbc_walk(db_session, instance_id, walk(dto(100)), now=NOW)

    assert db_session.in_transaction()
    assert (
        await db_session.execute(text("SELECT count(*) FROM jdbc_source_config"))
    ).scalar_one() == 1

    await db_session.rollback()
    assert (
        await db_session.execute(text("SELECT count(*) FROM jdbc_source_config"))
    ).scalar_one() == 0
