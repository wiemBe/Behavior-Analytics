"""GET /searches/{id}/metric-keys and /latest-groups over stored aggregates.

Both reads are one SELECT. An unknown search is 404. A search that exists but
has nothing stored is 200 and says which emptiness it is. DB-gated.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import httpx
import pytest
from sqlalchemy import event

from app.core.database import get_session
from app.main import create_app
from app.models.enums import ExecutionStatus
from app.models.search import SearchExecution, SearchResultMetric
from tests.integration.factories import make_search, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _client(session) -> httpx.AsyncClient:
    """In-process client sharing the test's session and event loop."""

    async def _session():
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


async def _execution(
    session,
    search,
    *,
    run_key: str,
    started_at: datetime | None,
    rows: list[tuple[str, float, dict | None]] | None = None,
    status: ExecutionStatus = ExecutionStatus.COMPLETED,
    truncated: bool = False,
    created_at: datetime | None = None,
) -> SearchExecution:
    """One execution, plus the aggregate rows it stored (none, when omitted)."""
    execution = SearchExecution(
        search_id=search.id,
        run_key=run_key,
        query_version=1,
        trigger="SCHEDULED",
        status=status,
        started_at=started_at,
        completed_at=started_at,
        truncated=truncated,
        result_count=None if rows is None else len(rows),
    )
    if created_at is not None:
        execution.created_at = created_at
    session.add(execution)
    await session.flush()
    bucket = started_at or utc()
    for metric_key, value, dimensions in rows or []:
        session.add(
            SearchResultMetric(
                execution_id=execution.id,
                search_id=search.id,
                bucket_start=bucket,
                metric_key=metric_key,
                value=value,
                dimensions=dimensions or {},
            )
        )
    await session.flush()
    return execution


async def _selects(session, call) -> list[str]:
    """SELECT statements issued while ``call`` runs. Inserts from the fixture do not count."""
    statements: list[str] = []

    def _capture(
        conn: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        statements.append(statement)

    connection = await session.connection()
    sync_engine = connection.sync_connection.engine
    event.listen(sync_engine, "before_cursor_execute", _capture)
    try:
        await call()
    finally:
        event.remove(sync_engine, "before_cursor_execute", _capture)
    return [sql for sql in statements if sql.lstrip().lower().startswith("select")]


async def test_metric_keys_shape_orders_total_first_then_latest_bucket(db_session) -> None:
    search = await make_search(db_session, name="keys-order")
    await _execution(
        db_session, search, run_key="early", started_at=utc(h=8),
        rows=[("total", 3.0, {}), ("alpha", 1.0, {"sourceip": "10.0.0.1"})],
    )
    await _execution(
        db_session, search, run_key="late", started_at=utc(h=12),
        rows=[
            ("alpha", 2.0, {"sourceip": "10.0.0.1"}),
            ("zebra", 4.0, {"sourceip": "10.0.0.2"}),
        ],
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/metric-keys")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["search_id"] == str(search.id)
    assert body["total"] == 3
    assert body["count"] == 3
    assert [row["metric_key"] for row in body["keys"]] == ["total", "alpha", "zebra"]
    by_key = {row["metric_key"]: row for row in body["keys"]}
    assert by_key["total"]["point_count"] == 1
    assert by_key["alpha"]["point_count"] == 2
    assert by_key["zebra"]["point_count"] == 1
    assert by_key["total"]["last_bucket_start"].startswith("2026-07-15T08:00:00")
    assert by_key["alpha"]["last_bucket_start"].startswith("2026-07-15T12:00:00")
    assert by_key["zebra"]["last_bucket_start"].startswith("2026-07-15T12:00:00")


async def test_metric_keys_limit_keeps_total_and_the_pre_limit_count(db_session) -> None:
    search = await make_search(db_session, name="keys-limit")
    await _execution(
        db_session, search, run_key="r", started_at=utc(h=9),
        rows=[("total", 1.0, {})],
    )
    await _execution(
        db_session, search, run_key="newer", started_at=utc(h=11),
        rows=[("newer-key", 2.0, {}), ("older-looking", 3.0, {})],
    )
    await _execution(
        db_session, search, run_key="mid", started_at=utc(h=10),
        rows=[("mid-key", 4.0, {})],
    )

    async with _client(db_session) as client:
        resp = await client.get(
            f"/api/v1/searches/{search.id}/metric-keys", params={"limit": 2}
        )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 4
    assert body["count"] == 2
    assert [row["metric_key"] for row in body["keys"]] == ["total", "newer-key"]


async def test_metric_keys_of_a_search_with_no_rows_is_empty(db_session) -> None:
    search = await make_search(db_session, name="keys-empty")

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/metric-keys")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["keys"] == []
    assert body["total"] == 0
    assert body["count"] == 0
    assert body["search_id"] == str(search.id)


async def test_metric_keys_unknown_search_is_404(db_session) -> None:
    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{uuid.uuid4()}/metric-keys")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "search not found"


async def test_metric_keys_limit_bounds_are_rejected(db_session) -> None:
    search = await make_search(db_session, name="keys-bounds")

    async with _client(db_session) as client:
        zero = await client.get(
            f"/api/v1/searches/{search.id}/metric-keys", params={"limit": 0}
        )
        over = await client.get(
            f"/api/v1/searches/{search.id}/metric-keys", params={"limit": 1001}
        )

    assert zero.status_code == 422
    assert over.status_code == 422


async def test_metric_keys_exclude_another_search(db_session) -> None:
    mine = await make_search(db_session, name="keys-mine")
    theirs = await make_search(db_session, name="keys-theirs")
    await _execution(
        db_session, mine, run_key="r", started_at=utc(h=10), rows=[("mine", 1.0, {})]
    )
    await _execution(
        db_session, theirs, run_key="r", started_at=utc(h=10), rows=[("theirs", 9.0, {})]
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{mine.id}/metric-keys")

    assert [row["metric_key"] for row in resp.json()["keys"]] == ["mine"]


async def test_latest_groups_shape_orders_by_value_then_key(db_session) -> None:
    search = await make_search(db_session, name="groups-order")
    execution = await _execution(
        db_session, search, run_key="r", started_at=utc(h=10),
        rows=[
            ("total", 10.0, {}),
            ("host-b", 40.0, {"sourceip": "10.1.0.2"}),
            ("host-a", 40.0, {"sourceip": "10.1.0.1"}),
        ],
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["search_id"] == str(search.id)
    assert body["execution_id"] == str(execution.id)
    assert body["started_at"].startswith("2026-07-15T10:00:00")
    assert body["status"] == "COMPLETED"
    assert body["truncated"] is False
    assert body["values_are_lower_bounds"] is False
    assert body["empty"] is None
    assert body["total"] == 3
    assert [(row["metric_key"], row["value"]) for row in body["rows"]] == [
        ("host-a", 40.0),
        ("host-b", 40.0),
        ("total", 10.0),
    ]
    assert body["rows"][0]["dimensions"] == {"sourceip": "10.1.0.1"}
    assert body["rows"][2]["dimensions"] == {}


async def test_latest_groups_uses_the_latest_execution_that_stored_rows(db_session) -> None:
    search = await make_search(db_session, name="groups-latest")
    await _execution(
        db_session, search, run_key="old", started_at=utc(h=9),
        rows=[("old-key", 1.0, {})],
    )
    await _execution(
        db_session, search, run_key="empty-later", started_at=utc(h=12), rows=None,
    )
    kept = await _execution(
        db_session, search, run_key="kept", started_at=utc(h=11),
        rows=[("kept-key", 7.0, {"username": "ada"})],
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    body = resp.json()
    assert body["execution_id"] == str(kept.id)
    assert body["empty"] is None
    assert [row["metric_key"] for row in body["rows"]] == ["kept-key"]
    assert body["rows"][0]["dimensions"] == {"username": "ada"}


async def test_latest_groups_breaks_ties_by_created_at_then_id(db_session) -> None:
    """``now()`` is the transaction start, so a tie has to be set by the test."""
    search = await make_search(db_session, name="groups-tie")
    same = utc(h=10)
    await _execution(
        db_session, search, run_key="earlier-row", started_at=same,
        created_at=utc(h=1), rows=[("earlier", 1.0, {})],
    )
    later = await _execution(
        db_session, search, run_key="later-row", started_at=same,
        created_at=utc(h=2), rows=[("later", 2.0, {})],
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    assert resp.json()["execution_id"] == str(later.id)


async def test_latest_groups_prefers_a_started_run_over_a_null_start(db_session) -> None:
    search = await make_search(db_session, name="groups-null-start")
    await _execution(
        db_session, search, run_key="unstarted", started_at=None,
        rows=[("orphan", 9.0, {})],
    )
    started = await _execution(
        db_session, search, run_key="started", started_at=utc(h=10),
        rows=[("measured", 1.0, {})],
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    assert resp.json()["execution_id"] == str(started.id)


async def test_latest_groups_limit_keeps_the_highest_and_the_full_total(db_session) -> None:
    search = await make_search(db_session, name="groups-limit")
    await _execution(
        db_session, search, run_key="r", started_at=utc(h=10),
        rows=[("low", 1.0, {}), ("mid", 5.0, {}), ("high", 9.0, {})],
    )

    async with _client(db_session) as client:
        resp = await client.get(
            f"/api/v1/searches/{search.id}/latest-groups", params={"limit": 1}
        )

    body = resp.json()
    assert body["total"] == 3
    assert [(row["metric_key"], row["value"]) for row in body["rows"]] == [("high", 9.0)]


async def test_latest_groups_truncated_rows_are_lower_bounds(db_session) -> None:
    search = await make_search(db_session, name="groups-truncated")
    await _execution(
        db_session, search, run_key="r", started_at=utc(h=10), truncated=True,
        rows=[("host-a", 1000.0, {"sourceip": "10.0.0.8"})],
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    body = resp.json()
    assert body["truncated"] is True
    assert body["values_are_lower_bounds"] is True
    assert body["rows"][0]["value"] == 1000.0
    assert body["empty"] is None


async def test_latest_groups_with_no_execution_names_that(db_session) -> None:
    search = await make_search(db_session, name="groups-never")

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["empty"] == "no_execution"
    assert body["rows"] == []
    assert body["execution_id"] is None
    assert body["total"] == 0
    assert body["truncated"] is False
    assert body["values_are_lower_bounds"] is False


async def test_latest_groups_with_executions_but_no_rows_names_that(db_session) -> None:
    search = await make_search(db_session, name="groups-empty-runs")
    await _execution(
        db_session, search, run_key="failed", started_at=utc(h=10), rows=None,
        status=ExecutionStatus.FAILED,
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{search.id}/latest-groups")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["empty"] == "no_rows"
    assert body["rows"] == []
    assert body["execution_id"] is None


async def test_latest_groups_unknown_search_is_404(db_session) -> None:
    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{uuid.uuid4()}/latest-groups")

    assert resp.status_code == 404
    assert resp.json()["detail"] == "search not found"


async def test_latest_groups_limit_bounds_are_rejected(db_session) -> None:
    search = await make_search(db_session, name="groups-bounds")

    async with _client(db_session) as client:
        zero = await client.get(
            f"/api/v1/searches/{search.id}/latest-groups", params={"limit": 0}
        )
        over = await client.get(
            f"/api/v1/searches/{search.id}/latest-groups", params={"limit": 501}
        )

    assert zero.status_code == 422
    assert over.status_code == 422


async def test_latest_groups_exclude_another_search(db_session) -> None:
    mine = await make_search(db_session, name="groups-mine")
    theirs = await make_search(db_session, name="groups-theirs")
    await _execution(
        db_session, mine, run_key="r", started_at=utc(h=10), rows=[("mine", 1.0, {})]
    )
    await _execution(
        db_session, theirs, run_key="r", started_at=utc(h=12), rows=[("theirs", 99.0, {})]
    )

    async with _client(db_session) as client:
        resp = await client.get(f"/api/v1/searches/{mine.id}/latest-groups")

    assert [row["metric_key"] for row in resp.json()["rows"]] == ["mine"]


async def test_each_read_is_one_select_at_either_size(db_session) -> None:
    small = await make_search(db_session, name="groups-n1-small")
    large = await make_search(db_session, name="groups-n1-large")
    await _execution(
        db_session, small, run_key="r", started_at=utc(h=10),
        rows=[("total", 1.0, {}), ("a", 2.0, {})],
    )
    await _execution(
        db_session, large, run_key="r", started_at=utc(h=10),
        rows=[(f"k{i}", float(i), {}) for i in range(30)],
    )

    async with _client(db_session) as client:
        async def keys_small() -> None:
            resp = await client.get(f"/api/v1/searches/{small.id}/metric-keys")
            assert resp.status_code == 200

        async def keys_large() -> None:
            resp = await client.get(f"/api/v1/searches/{large.id}/metric-keys")
            assert resp.json()["total"] == 30

        async def groups_small() -> None:
            resp = await client.get(f"/api/v1/searches/{small.id}/latest-groups")
            assert resp.json()["total"] == 2

        async def groups_large() -> None:
            resp = await client.get(f"/api/v1/searches/{large.id}/latest-groups")
            assert resp.json()["total"] == 30

        small_keys = await _selects(db_session, keys_small)
        large_keys = await _selects(db_session, keys_large)
        small_groups = await _selects(db_session, groups_small)
        large_groups = await _selects(db_session, groups_large)

    assert small_keys == [small_keys[0]], small_keys
    assert len(large_keys) == len(small_keys)
    assert small_groups == [small_groups[0]], small_groups
    assert len(large_groups) == len(small_groups)
