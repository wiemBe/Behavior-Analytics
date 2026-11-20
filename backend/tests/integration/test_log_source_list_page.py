"""`GET /log-sources` pages (SCALE-2g): bounded size, `q`, `ids`, exact total, constant cost."""

from __future__ import annotations

import os
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy import event, insert

from app.core.database import get_session
from app.main import create_app
from app.models.enums import AnomalyState, AnomalyType, Criticality, Severity
from app.models.log_source import LogSource, LogSourceAnomaly
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.factories import make_instance, make_log_source

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the log-source list API tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
NOTHING = Principal(subject="nobody", permissions=frozenset())
URL = "/api/v1/log-sources"


def _client(session, principal: Principal = READER):  # type: ignore[no-untyped-def]
    async def _session():  # type: ignore[no-untyped-def]
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


@contextmanager
def _counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def _bulk(session, instance, count: int, *, start: int = 10_000) -> None:
    """Insert `count` sources in one statement (names `bulk-00000`, ...)."""
    await session.execute(
        insert(LogSource),
        [
            {
                "instance_id": instance.id,
                "qradar_id": start + n,
                "name": f"bulk-{n:05d}",
                "criticality": Criticality.MEDIUM,
            }
            for n in range(count)
        ],
    )
    await session.commit()


async def _open_anomaly(session, source: LogSource) -> None:
    now = datetime(2026, 11, 1, tzinfo=UTC)
    session.add(
        LogSourceAnomaly(
            log_source_id=source.id,
            anomaly_type=AnomalyType.VOLUME_DROP,
            severity=Severity.MEDIUM,
            state=AnomalyState.OPEN,
            detected_at=now,
            opened_at=now,
            observed_value=1.0,
            expected_value=5.0,
        )
    )
    await session.flush()


async def test_the_default_page_is_200_and_the_envelope_says_so(db_session) -> None:
    instance = await make_instance(db_session)
    await _bulk(db_session, instance, 230)
    async with _client(db_session) as client:
        body = (await client.get(URL)).json()
    assert len(body["items"]) == 200
    assert (body["total"], body["limit"], body["offset"]) == (230, 200, 0)
    assert body["items"][0]["name"] == "bulk-00000"


async def test_offset_walks_to_the_second_page_and_the_total_stays_exact(db_session) -> None:
    instance = await make_instance(db_session)
    await _bulk(db_session, instance, 230)
    async with _client(db_session) as client:
        body = (await client.get(URL, params={"offset": 200})).json()
    assert body["items"][0]["name"] == "bulk-00200"
    assert len(body["items"]) == 30
    assert body["total"] == 230


async def test_a_limit_above_the_maximum_is_clamped_and_reported(db_session) -> None:
    instance = await make_instance(db_session)
    await _bulk(db_session, instance, 230)
    async with _client(db_session) as client:
        body = (await client.get(URL, params={"limit": 1000})).json()
        small = (await client.get(URL, params={"limit": 5})).json()
        refused = await client.get(URL, params={"limit": 0})
    assert body["limit"] == 200 and len(body["items"]) == 200
    assert small["limit"] == 5 and len(small["items"]) == 5
    assert refused.status_code == 422


async def test_q_matches_name_or_identifier_case_insensitively_and_escapes_wildcards(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await make_log_source(db_session, instance, qradar_id=1, name="Perimeter Firewall")
    await make_log_source(
        db_session, instance, qradar_id=2, name="Core", identifier="fw-edge-01"
    )
    await make_log_source(db_session, instance, qradar_id=3, name="100% sure")
    await make_log_source(db_session, instance, qradar_id=4, name="Other")
    await db_session.commit()
    async with _client(db_session) as client:
        by_name = (await client.get(URL, params={"q": "FIREWALL"})).json()
        by_identifier = (await client.get(URL, params={"q": "edge-01"})).json()
        percent = (await client.get(URL, params={"q": "%"})).json()
        none = (await client.get(URL, params={"q": "no-such"})).json()
    assert [r["name"] for r in by_name["items"]] == ["Perimeter Firewall"]
    assert [r["name"] for r in by_identifier["items"]] == ["Core"]
    assert [r["name"] for r in percent["items"]] == ["100% sure"]
    assert percent["total"] == 1
    assert none == {"items": [], "total": 0, "limit": 200, "offset": 0}


async def test_ids_returns_exactly_those_sources_and_unknown_ids_are_absent(db_session) -> None:
    instance = await make_instance(db_session)
    a = await make_log_source(db_session, instance, qradar_id=1, name="A")
    b = await make_log_source(db_session, instance, qradar_id=2, name="B")
    await make_log_source(db_session, instance, qradar_id=3, name="C")
    await db_session.commit()
    async with _client(db_session) as client:
        body = (
            await client.get(URL, params={"ids": [str(b.id), str(a.id), str(uuid.uuid4())]})
        ).json()
    assert [r["name"] for r in body["items"]] == ["A", "B"]
    assert body["total"] == 2


async def test_more_than_200_ids_is_refused_naming_the_field(db_session) -> None:
    async with _client(db_session) as client:
        ok = await client.get(URL, params={"ids": [str(uuid.uuid4()) for _ in range(200)]})
        refused = await client.get(URL, params={"ids": [str(uuid.uuid4()) for _ in range(201)]})
    assert ok.status_code == 200
    assert refused.status_code == 422
    assert "ids" in refused.json()["detail"][0]["loc"]


async def test_a_malformed_id_is_refused_naming_the_field(db_session) -> None:
    async with _client(db_session) as client:
        refused = await client.get(URL, params={"ids": "not-a-uuid"})
    assert refused.status_code == 422
    assert "ids" in refused.json()["detail"][0]["loc"]


async def test_open_anomaly_counts_are_those_of_the_returned_page(db_session) -> None:
    instance = await make_instance(db_session)
    a = await make_log_source(db_session, instance, qradar_id=1, name="A")
    b = await make_log_source(db_session, instance, qradar_id=2, name="B")
    await _open_anomaly(db_session, a)
    await _open_anomaly(db_session, a)
    await _open_anomaly(db_session, b)
    await db_session.commit()
    async with _client(db_session) as client:
        body = (await client.get(URL, params={"ids": str(a.id)})).json()
        both = (await client.get(URL)).json()
    assert [(r["name"], r["open_anomaly_count"]) for r in body["items"]] == [("A", 2)]
    assert [(r["name"], r["open_anomaly_count"]) for r in both["items"]] == [("A", 2), ("B", 1)]


async def test_monitored_only_still_filters_and_counts(db_session) -> None:
    instance = await make_instance(db_session)
    await make_log_source(db_session, instance, qradar_id=1, name="On", monitoring_enabled=True)
    await make_log_source(db_session, instance, qradar_id=2, name="Off", monitoring_enabled=False)
    await db_session.commit()
    async with _client(db_session) as client:
        body = (await client.get(URL, params={"monitored_only": "true"})).json()
    assert [r["name"] for r in body["items"]] == ["On"]
    assert body["total"] == 1


async def test_the_statement_count_is_the_same_for_ten_and_for_eight_thousand_sources(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    await _bulk(db_session, instance, 10)
    async with _client(db_session) as client:
        await client.get(URL)  # warm lazy imports and the statement cache
        with _counting(db_session) as small:
            small_body = (await client.get(URL)).json()
        await _bulk(db_session, instance, 7_990, start=50_000)
        with _counting(db_session) as large:
            large_body = (await client.get(URL)).json()
        with _counting(db_session) as searched:
            await client.get(URL, params={"q": "bulk-0000", "offset": 3})
    assert small_body["total"] == 10 and large_body["total"] == 8_000
    assert len(large_body["items"]) == 200
    assert len(small) == len(large) == len(searched)
    assert len(large) <= 4


async def test_the_read_permission_is_unchanged(db_session) -> None:
    async with _client(db_session, NOTHING) as client:
        assert (await client.get(URL)).status_code == 403
        assert (await client.get(URL, params={"ids": str(uuid.uuid4())})).status_code == 403
