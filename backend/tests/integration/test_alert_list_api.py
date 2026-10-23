"""The alert worklist is filtered and paged without hiding older alerts."""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.core.database import get_session
from app.main import create_app
from app.models.alert import Alert
from app.models.enums import AlertStatus, Severity
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the alert list API tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))


def _client(session) -> httpx.AsyncClient:  # type: ignore[no-untyped-def]
    async def _session():  # type: ignore[no-untyped-def]
        yield session

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: READER
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    )


def _alert(
    title: str,
    opened_at: datetime,
    status: AlertStatus,
    *,
    alert_id: uuid.UUID | None = None,
) -> Alert:
    fingerprint = f"list:{uuid.uuid4()}"
    return Alert(
        id=alert_id,
        dedup_key=fingerprint,
        fingerprint=fingerprint,
        title=title,
        description=None,
        severity=Severity.HIGH,
        status=status,
        source_type="log_source",
        source_id=uuid.uuid4(),
        context={},
        opened_at=opened_at,
        first_seen_at=opened_at,
        occurrence_count=1,
        evidence_snapshot={},
        source_anomaly_ids=[],
    )


async def test_alert_list_returns_total_offset_filter_and_descending_order(
    db_session,
) -> None:
    base = datetime(2026, 10, 23, 12, tzinfo=UTC)
    rows = [
        _alert("resolved-newest", base, AlertStatus.RESOLVED),
        _alert("open-newer", base - timedelta(minutes=1), AlertStatus.OPEN),
        _alert("acknowledged", base - timedelta(minutes=2), AlertStatus.ACKNOWLEDGED),
        _alert("open-middle", base - timedelta(minutes=3), AlertStatus.OPEN),
        _alert("resolved-old", base - timedelta(minutes=4), AlertStatus.RESOLVED),
        _alert("open-oldest", base - timedelta(minutes=5), AlertStatus.OPEN),
    ]
    db_session.add_all(rows)
    await db_session.commit()

    async with _client(db_session) as client:
        first = await client.get("/api/v1/alerts", params={"limit": 2})
        second = await client.get(
            "/api/v1/alerts", params={"limit": 2, "offset": 2}
        )
        opened = await client.get(
            "/api/v1/alerts",
            params={"status": "OPEN", "limit": 2, "offset": 1},
        )

    assert first.status_code == 200, first.text
    assert first.json()["total"] == 6
    assert first.json()["limit"] == 2
    assert first.json()["offset"] == 0
    assert [item["title"] for item in first.json()["items"]] == [
        "resolved-newest",
        "open-newer",
    ]

    assert second.status_code == 200, second.text
    assert second.json()["total"] == 6
    assert second.json()["offset"] == 2
    assert [item["title"] for item in second.json()["items"]] == [
        "acknowledged",
        "open-middle",
    ]

    assert opened.status_code == 200, opened.text
    assert opened.json()["total"] == 3
    assert opened.json()["limit"] == 2
    assert opened.json()["offset"] == 1
    assert [item["title"] for item in opened.json()["items"]] == [
        "open-middle",
        "open-oldest",
    ]
    assert {item["status"] for item in opened.json()["items"]} == {"OPEN"}


async def test_alert_list_uses_stable_id_order_across_equal_timestamps(
    db_session,
) -> None:
    opened_at = datetime(2026, 10, 23, 12, tzinfo=UTC)
    insertion_order = [3, 1, 5, 2, 4]
    db_session.add_all(
        [
            _alert(
                f"same-tick-{value}",
                opened_at,
                AlertStatus.OPEN,
                alert_id=uuid.UUID(int=value),
            )
            for value in insertion_order
        ]
    )
    await db_session.commit()

    observed_orders: list[list[str]] = []
    async with _client(db_session) as client:
        for _ in range(2):
            observed: list[str] = []
            for offset in (0, 2, 4):
                response = await client.get(
                    "/api/v1/alerts", params={"limit": 2, "offset": offset}
                )
                assert response.status_code == 200, response.text
                assert response.json()["total"] == 5
                observed.extend(item["id"] for item in response.json()["items"])
            observed_orders.append(observed)

    expected = [str(uuid.UUID(int=value)) for value in (5, 4, 3, 2, 1)]
    assert observed_orders == [expected, expected]
    assert len(set(observed_orders[0])) == 5


@pytest.mark.parametrize(
    ("params", "expected_status"),
    [
        ({}, 200),
        ({"limit": 500}, 200),
        ({"limit": 0}, 422),
        ({"limit": 501}, 422),
        ({"offset": -1}, 422),
        ({"status": "NOT_A_STATUS"}, 422),
    ],
)
async def test_alert_list_enforces_defaults_and_bounds(
    db_session, params: dict[str, int | str], expected_status: int
) -> None:
    async with _client(db_session) as client:
        response = await client.get("/api/v1/alerts", params=params)

    assert response.status_code == expected_status, response.text
    if not params:
        assert response.json() == {"items": [], "total": 0, "limit": 100, "offset": 0}
