"""JDBC-1 J1m2a: the stored-query projection agrees with `flow.flow_state` and stays bounded.

Gated on TEST_DATABASE_URL (the shared async schema fixture, so one file at a time per
database). Rows are inserted by Core, so every column the guard looks at is set explicitly.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import insert, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from app.jdbc import queries
from app.jdbc.flow import flow_state, observation_stale, target_bucket
from app.jdbc.queries import JdbcFilters, TargetWindow
from app.models.enums import BucketCompleteness
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSourceConfig, JdbcSyncState
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 20, 12, 3, tzinfo=UTC)
WIDTH, CADENCE = 300, 300
WINDOW = TargetWindow(*target_bucket(
    NOW, cadence_seconds=CADENCE, width_seconds=WIDTH, anchor=0
))
TRUNCATED_WINDOW = TargetWindow(WINDOW.start, WINDOW.end, truncated_attempt=True)
NO_FILTER = JdbcFilters()


def metric(source: uuid.UUID, start: datetime, **over: Any) -> dict[str, Any]:
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


async def source(
    session: AsyncSession, instance: uuid.UUID, number: int, *, protocol: int = 8,
    enabled: bool = True, monitored: bool = True, name: str | None = None,
) -> uuid.UUID:
    row = LogSource(
        instance_id=instance, qradar_id=number, name=name or f"src-{number:04d}",
        protocol_type_id=protocol, enabled=enabled, monitoring_enabled=monitored,
    )
    session.add(row)
    await session.flush()
    return row.id


async def page(
    session: AsyncSession, window: TargetWindow = WINDOW, filters: JdbcFilters = NO_FILTER,
    *, limit: int = 100, offset: int = 0,
) -> list[Any]:
    result = await session.execute(queries.page_query(window, filters, limit=limit, offset=offset))
    return list(result.all())


# The §4 table, plus each way a stored row can fail to be a complete live target.
# (enabled, row overrides or None, truncated attempt known)
CASES: dict[str, tuple[bool, dict[str, Any] | None, bool]] = {
    "disabled_wins_over_live_positive": (False, {}, False),
    "disabled_wins_over_truncated": (False, None, True),
    "complete_live_positive": (True, {}, False),
    "complete_live_zero": (True, {"event_count": 0}, False),
    "null_collection_source_is_live": (True, {"collection_source": None}, False),
    "partial_live": (True, {"completeness": "PARTIAL"}, False),
    "fleet_empty_flag": (
        True, {"completeness": "PARTIAL", "query_provenance": {"fleet_empty": True},
               "event_count": 0}, False),
    "fleet_empty_flag_on_complete_row": (
        True, {"query_provenance": {"fleet_empty": True}, "event_count": 0}, False),
    "truncated_attempt_row_absent": (True, None, True),
    "absent_row": (True, None, False),
    "missing_completeness": (True, {"completeness": "MISSING"}, False),
    "unknown_completeness": (True, {"completeness": "WEIRD"}, False),
    "history_complete": (True, {"collection_source": "ariel_history"}, False),
    "mock_history_partial": (
        True, {"collection_source": "mock_history", "completeness": "PARTIAL"}, False),
    "negative_count": (True, {"event_count": -1}, False),
    "unmonitored_source_with_row": (True, {}, False),
    "truncated_attempt_but_row_present": (True, {}, True),
    "other_width_is_not_the_target": (True, {"bucket_seconds": 60}, False),
}


@pytest.mark.parametrize("name", list(CASES))
async def test_sql_case_matches_python_flow_state(db_session: AsyncSession, name: str) -> None:
    enabled, over, truncated = CASES[name]
    instance = await console(db_session)
    sid = await source(
        db_session, instance, 1, enabled=enabled, monitored="unmonitored" not in name
    )
    if over is not None:
        await db_session.execute(insert(LogSourceMetric), [metric(sid, WINDOW.start, **over)])
    window = TRUNCATED_WINDOW if truncated else WINDOW
    [row] = await page(db_session, window)

    # The window's width is part of the target; a row of another width is no row at all.
    present = over is not None and over.get("bucket_seconds", WIDTH) == WIDTH
    facts: dict[str, Any] = {}
    if present:
        assert over is not None
        stored = metric(sid, WINDOW.start, **over)
        try:
            completeness = BucketCompleteness(stored["completeness"])
        except ValueError:
            completeness = None  # type: ignore[assignment]
        facts = {
            "completeness": completeness, "count": stored["event_count"],
            "origin": "history" if stored["collection_source"] in (
                "ariel_history", "mock_history") else "live",
            "fleet_empty": stored["query_provenance"].get("fleet_empty") is True,
        }
    # The flow contract: a truncated attempt is passed only when the target row is absent.
    expected = flow_state(
        enabled=enabled, target=(WINDOW.start, WINDOW.end), monitored=True, row_present=present,
        truncated_attempt=truncated and not present, **facts,
    )
    assert (row.flow_state, row.measurement, row.event_count) == (
        expected.state, expected.measurement, expected.count
    )
    assert row.target_present is present
    if expected.count is None:
        assert row.average_eps is None  # a rate is exact-only, never a zero


async def test_target_live_bucket_and_last_observation_separate(db_session: AsyncSession) -> None:
    instance = await console(db_session)
    sid = await source(db_session, instance, 1)
    old = WINDOW.start - timedelta(hours=2)
    await db_session.execute(
        insert(LogSourceMetric), [metric(sid, old, event_count=500, collected_at=old)])
    [row] = await page(db_session)
    # An older positive bucket never fills the missing target.
    assert (row.flow_state, row.measurement, row.event_count) == (
        "NOT_MEASURED", "NOT_COLLECTED", None
    )
    assert row.target_present is False

    since = queries.last_observation_since(WINDOW.end, cadence_seconds=CADENCE, width_seconds=WIDTH)
    rows = (await db_session.execute(queries.last_observation_query([sid], since=since))).all()
    [last] = [queries.to_last_observation(r) for r in rows]
    assert (last.start, last.count, last.origin) == (old, 500, "live")
    assert observation_stale(last.observed_at, NOW, cadence_seconds=CADENCE, width_seconds=WIDTH)

    # Outside the bounded window nothing is returned, so an old row is not dug up.
    lone = await source(db_session, instance, 2)
    far = WINDOW.end - timedelta(days=3)
    await db_session.execute(insert(LogSourceMetric), [metric(lone, far, event_count=7)])
    rows = (await db_session.execute(
        queries.last_observation_query([sid, lone], since=since))).all()
    assert [(r.log_source_id, r.bucket_start) for r in rows] == [(sid, old)]

    # The target arrives later: it is both the target and the newest observation.
    await db_session.execute(insert(LogSourceMetric), [metric(sid, WINDOW.start, event_count=3)])
    [row] = [r for r in await page(db_session) if r.id == sid]
    assert (row.flow_state, row.event_count) == ("RECEIVING", 3)
    rows = (await db_session.execute(queries.last_observation_query([sid], since=since))).all()
    assert [r.bucket_start for r in rows] == [WINDOW.start]  # one row per source


async def test_last_observation_never_reports_a_partial_or_history_count(
    db_session: AsyncSession,
) -> None:
    instance = await console(db_session)
    partial = await source(db_session, instance, 1)
    history = await source(db_session, instance, 2)
    await db_session.execute(insert(LogSourceMetric), [
        metric(partial, WINDOW.start, completeness="PARTIAL", event_count=4),
        metric(history, WINDOW.start, collection_source="ariel_history", event_count=4),
    ])
    since = WINDOW.end - timedelta(hours=24)
    rows = {
        r.log_source_id: queries.to_last_observation(r)
        for r in (await db_session.execute(
            queries.last_observation_query([partial, history], since=since))).all()
    }
    assert (rows[partial].count, rows[partial].origin) == (None, "live")
    assert (rows[history].count, rows[history].origin) == (4, "history")


async def test_flow_filter_before_page_and_count(db_session: AsyncSession) -> None:
    instance = await console(db_session)
    wanted: list[uuid.UUID] = []
    for number in range(1, 8):
        sid = await source(db_session, instance, number)
        if number in (1, 3, 4, 6, 7):  # RECEIVING; 2 and 5 have no target row at all
            wanted.append(sid)
            await db_session.execute(insert(LogSourceMetric), [metric(sid, WINDOW.start)])
    await source(db_session, instance, 8, enabled=False)
    filters = JdbcFilters(flow_state="RECEIVING")

    total = await db_session.scalar(queries.count_query(WINDOW, filters))
    assert total == 5
    first = await page(db_session, filters=filters, limit=2, offset=0)
    second = await page(db_session, filters=filters, limit=2, offset=2)
    third = await page(db_session, filters=filters, limit=2, offset=4)
    assert [len(first), len(second), len(third)] == [2, 2, 1]  # no row dropped after paging
    got = [r.id for r in (*first, *second, *third)]
    assert got == wanted and {r.flow_state for r in (*first, *second, *third)} == {"RECEIVING"}
    # The unfiltered total and the other state agree with the same projection.
    assert await db_session.scalar(queries.count_query(WINDOW, NO_FILTER)) == 8
    assert await db_session.scalar(
        queries.count_query(WINDOW, JdbcFilters(flow_state="NOT_MEASURED"))) == 2
    assert await db_session.scalar(
        queries.count_query(WINDOW, JdbcFilters(flow_state="DISABLED_IN_QRADAR"))) == 1


async def test_filters_name_escape_enabled_protocol_instance(db_session: AsyncSession) -> None:
    one, two = await console(db_session), await console(db_session)
    pct = await source(db_session, one, 1, name="audit_100%_db")
    await source(db_session, one, 2, name="auditX100Y_db")
    await source(db_session, one, 3, name="plain", protocol=20)
    await source(db_session, one, 4, name="off", enabled=False)
    other = await source(db_session, two, 5, name="elsewhere")

    async def ids(filters: JdbcFilters) -> list[uuid.UUID]:
        total = await db_session.scalar(queries.count_query(WINDOW, filters))
        rows = await page(db_session, filters=filters)
        assert total == len(rows)
        return [r.id for r in rows]

    assert await ids(JdbcFilters(q="_100%_")) == [pct]  # `_` and `%` are literal
    assert len(await ids(JdbcFilters(enabled=False))) == 1
    assert len(await ids(JdbcFilters(enabled=True))) == 4
    assert len(await ids(JdbcFilters(protocol=20))) == 1
    assert await ids(JdbcFilters(instance_id=two)) == [other]
    assert await ids(JdbcFilters(protocol=1)) == []  # a non-JDBC protocol is never listed


async def test_disabled_unsupported_stale_axes(db_session: AsyncSession) -> None:
    instance = await console(db_session)
    disabled = await source(db_session, instance, 1, enabled=False)
    verified = await source(db_session, instance, 2)
    unverified = await source(db_session, instance, 3, protocol=20)
    await source(db_session, instance, 4, protocol=1, name="not-jdbc")
    await db_session.execute(insert(LogSourceMetric), [
        metric(disabled, WINDOW.start), metric(verified, WINDOW.start),
        metric(unverified, WINDOW.start, event_count=0),
    ])
    await db_session.execute(insert(JdbcSourceConfig), [{
        "log_source_id": verified, "protocol_type_id": 8, "database_type": "Postgres",
        "field_states": {}, "allowlist_version": 1, "config_state": "COMPLETE",
        "observed_at": NOW,
    }])
    # The inventory is five days stale and the last pass failed; the sync row is its own axis.
    stale = NOW - timedelta(days=5)
    db_session.add(CollectionWatermark(
        instance_id=instance, collector="log_source_inventory", watermark_at=stale,
        last_run_at=NOW, collection_metadata={"inventory_pass": {"status": "FAILED"}},
    ))
    db_session.add(JdbcSyncState(instance_id=instance, state="PARTIAL", last_complete_at=stale))
    await db_session.flush()

    rows = {r.id: r for r in await page(db_session)}
    assert len(rows) == 3  # protocol 1 is not a JDBC-like source
    assert rows[disabled].flow_state == "DISABLED_IN_QRADAR"
    assert (rows[disabled].measurement, rows[disabled].event_count) == ("NOT_COLLECTED", None)
    assert rows[verified].flow_state == "RECEIVING" and rows[verified].config_present is True
    assert rows[verified].cfg_database_type == "Postgres"
    assert rows[unverified].config_present is False  # null config, not an empty one
    assert rows[unverified].cfg_database_type is None and rows[unverified].cfg_field_states is None
    assert rows[unverified].flow_state == "NO_ATTRIBUTED_EVENTS"

    [state] = (await db_session.execute(queries.run_state_query(instance))).all()
    assert state.inventory_last_complete_at == stale and state.sync_state == "PARTIAL"
    assert queries.inventory_attempt_state(state.inventory_pass) == "FAILED"
    assert queries.inventory_attempt_state(None) == "NOT_COLLECTED"
    assert queries.inventory_attempt_state({"status": "RUNNING"}) == "NOT_COLLECTED"
    # Statement 4 is one SELECT over every console, and a console with no state still appears.
    other = await console(db_session)
    states = (await db_session.execute(queries.run_state_query())).all()
    assert {s.instance_id for s in states} == {instance, other}
    assert [s.sync_state for s in states if s.instance_id == other] == [None]


async def test_four_statements_for_a_page(db_session: AsyncSession) -> None:
    instance = await console(db_session)
    ids = [await source(db_session, instance, n) for n in range(1, 4)]
    await db_session.execute(insert(LogSourceMetric), [metric(i, WINDOW.start) for i in ids])
    seen: list[str] = []
    connection = await db_session.connection()

    from sqlalchemy import event

    def record(conn: Any, cursor: Any, statement: str, *rest: Any) -> None:
        seen.append(statement)

    event.listen(connection.sync_connection, "before_cursor_execute", record)
    try:
        filters = JdbcFilters(flow_state="RECEIVING")
        await db_session.scalar(queries.count_query(WINDOW, filters))
        rows = await page(db_session, filters=filters)
        since = WINDOW.end - timedelta(hours=24)
        await db_session.execute(queries.last_observation_query([r.id for r in rows], since=since))
        await db_session.execute(queries.run_state_query(instance))
    finally:
        event.remove(connection.sync_connection, "before_cursor_execute", record)
    assert len(seen) == 4
    assert all(s.lstrip().upper().startswith("SELECT") for s in seen)


def _walk(plan: Any, found: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found.append(plan)
    for child in plan.get("Plans", []):
        _walk(child, found)
    return found


async def _explain(session: AsyncSession, statement: Any) -> list[dict[str, Any]]:
    sql = str(statement.compile(
        dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
    ))
    explain = "EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql
    raw = (await session.execute(text(explain))).scalar_one()
    plan = raw if isinstance(raw, list) else json.loads(raw)
    return _walk(plan[0]["Plan"], [])


async def test_exact_bucket_plan_at_8k(db_session: AsyncSession) -> None:
    """8,000 JDBC sources, a few buckets each: the target join is a composite-key probe."""
    instance = await console(db_session)
    ids = [uuid.uuid4() for _ in range(8000)]
    await db_session.execute(insert(LogSource), [
        {"id": sid, "instance_id": instance, "qradar_id": n, "name": f"jdbc-{n:05d}",
         "protocol_type_id": 8, "enabled": True, "monitoring_enabled": True,
         "criticality": "MEDIUM", "business_hours_only": False, "business_hours_start": 8,
         "business_hours_end": 18, "business_days": [1, 2, 3, 4, 5], "timezone_name": "UTC",
         "custom_thresholds": {}, "first_seen_at": NOW, "created_at": NOW, "updated_at": NOW}
        for n, sid in enumerate(ids)
    ])
    await db_session.execute(insert(LogSourceMetric), [
        metric(sid, WINDOW.start - timedelta(seconds=WIDTH * back))
        for sid in ids for back in range(0, 4)
    ])
    await db_session.execute(insert(JdbcSourceConfig), [
        {"log_source_id": sid, "protocol_type_id": 8, "field_states": {},
         "allowlist_version": 1, "config_state": "COMPLETE", "observed_at": NOW}
        for sid in ids
    ])
    await db_session.execute(text("ANALYZE log_source_metric"))
    await db_session.execute(text("ANALYZE log_source"))
    await db_session.execute(text("ANALYZE jdbc_source_config"))

    flow = JdbcFilters(flow_state="RECEIVING")
    statements = {
        "page": queries.page_query(WINDOW, NO_FILTER, limit=100, offset=0),
        "flow page": queries.page_query(WINDOW, flow, limit=100, offset=0),
        "flow count": queries.count_query(WINDOW, flow),
        "last observation": queries.last_observation_query(
            ids[:100], since=WINDOW.end - timedelta(hours=24)),
    }
    exact = "bucket_start = '2026-11-20 11:55:00+00'"  # the literal rendering of WINDOW.start
    for label, statement in statements.items():
        nodes = [n for n in await _explain(db_session, statement)
                 if n.get("Relation Name") == "log_source_metric"]
        assert nodes, label
        for node in nodes:
            cond = node.get("Index Cond", "") + node.get("Filter", "")
            if label == "last observation":  # a bounded, indexed read; never a table scan
                assert node["Node Type"] != "Seq Scan", (label, node["Node Type"])
            elif label == "page":
                # The limited page probes the composite key per source.
                assert "Index" in node["Node Type"], (label, node["Node Type"])
                assert "log_source_id" in cond and exact in cond, (label, cond)
            else:
                # A flow filter needs every source's target row: the planner may hash-join, but
                # the metric side is still pinned to the one exact bucket, never to history.
                assert exact in cond and "bucket_seconds = 300" in cond, (label, cond)

    rows = await page(db_session, filters=flow, limit=100)
    assert len(rows) == 100 and {r.event_count for r in rows} == {9}
    assert await db_session.scalar(queries.count_query(WINDOW, flow)) == 8000
