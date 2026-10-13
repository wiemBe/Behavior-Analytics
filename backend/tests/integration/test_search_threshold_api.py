"""API surface for the threshold reachability rule (T-047).

Create refuses a never-breachable threshold with a 422; update refuses a patch
whose *effective* values make the threshold unreachable, writing nothing. DB-
gated: both routes persist, so they need a real schema.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run API threshold integration tests",
    ),
]

AQL = "SELECT sourceip FROM events LAST 1 HOURS"


def _client() -> TestClient:
    url = os.environ["TEST_DATABASE_URL"]
    # NullPool: TestClient runs each request on a fresh event loop, and a pooled
    # asyncpg connection is bound to the loop that opened it.
    engine = create_async_engine(url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="admin", permissions=frozenset({"admin:*"})
    )
    return TestClient(app)


def _create_payload(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "thr",
        "aql_query": AQL,
        "schedule_cron": "*/5 * * * *",
        "max_result_rows": 100,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------- create
@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        # GT at the cap is reachable (a whole run of exactly 100 breaches it).
        ({"threshold_value": 100, "threshold_operator": "GT"}, 201),
        ({"threshold_value": 100.5, "threshold_operator": "GT"}, 201),
        # GT at cap + 1 can never be observed as breached.
        ({"threshold_value": 101, "threshold_operator": "GT"}, 422),
        # GE at cap + 1 is proven by a truncated run.
        ({"threshold_value": 101, "threshold_operator": "GE"}, 201),
        # GE above cap + 1 cannot.
        ({"threshold_value": 101.5, "threshold_operator": "GE"}, 422),
        # A null threshold is reachable with any cap.
        ({"threshold_value": None}, 201),
        # LT with a huge threshold is unaffected.
        ({"threshold_value": 1_000_000, "threshold_operator": "LT"}, 201),
    ],
)
def test_create_refuses_never_breachable_threshold(
    db_schema, overrides: dict, expected: int
) -> None:
    client = _client()
    resp = client.post("/api/v1/searches", json=_create_payload(**overrides))
    assert resp.status_code == expected, resp.text
    if expected == 422:
        assert "can never be reached" in resp.text


def test_create_gt_refusal_message_says_what_to_change(db_schema) -> None:
    client = _client()
    resp = client.post(
        "/api/v1/searches",
        json=_create_payload(threshold_value=20_000, threshold_operator="GT"),
    )
    assert resp.status_code == 422
    assert (
        "threshold GT 20000 can never be reached: a run counts at most "
        "max_result_rows (100) rows" in resp.text
    )


def test_create_refused_search_is_not_persisted(db_schema) -> None:
    client = _client()
    resp = client.post(
        "/api/v1/searches",
        json=_create_payload(threshold_value=101, threshold_operator="GT"),
    )
    assert resp.status_code == 422
    body = client.get("/api/v1/searches").json()
    assert body == []  # nothing reached the database


# ---------------------------------------------------------------------- update
def test_update_lowering_max_result_rows_under_existing_threshold_is_refused(db_schema) -> None:
    client = _client()
    created = client.post(
        "/api/v1/searches",
        json=_create_payload(threshold_value=100, threshold_operator="GT"),
    ).json()
    # Lowering the cap to 99 makes GT 100 unreachable (boundary = 100).
    resp = client.patch(f"/api/v1/searches/{created['id']}", json={"max_result_rows": 99})
    assert resp.status_code == 422, resp.text
    assert "can never be reached" in resp.text
    # Nothing of the patch was written: the row is exactly as created.
    row = client.get(f"/api/v1/searches/{created['id']}").json()
    assert row["max_result_rows"] == 100
    assert row["threshold_value"] == 100.0
    assert row["threshold_operator"] == "GT"


def test_update_raising_threshold_alone_is_refused(db_schema) -> None:
    client = _client()
    created = client.post(
        "/api/v1/searches",
        json=_create_payload(threshold_value=100, threshold_operator="GT"),
    ).json()
    resp = client.patch(f"/api/v1/searches/{created['id']}", json={"threshold_value": 101})
    assert resp.status_code == 422, resp.text
    row = client.get(f"/api/v1/searches/{created['id']}").json()
    assert row["threshold_value"] == 100.0


def test_update_changing_operator_to_gt_at_boundary_is_refused(db_schema) -> None:
    client = _client()
    # GE at cap + 1 is reachable; GT at the same value is not.
    created = client.post(
        "/api/v1/searches",
        json=_create_payload(
            threshold_value=101, threshold_operator="GE", max_result_rows=100
        ),
    ).json()
    resp = client.patch(f"/api/v1/searches/{created['id']}", json={"threshold_operator": "GT"})
    assert resp.status_code == 422, resp.text
    row = client.get(f"/api/v1/searches/{created['id']}").json()
    assert row["threshold_operator"] == "GE"


def test_update_reachable_patch_succeeds(db_schema) -> None:
    client = _client()
    created = client.post(
        "/api/v1/searches",
        json=_create_payload(threshold_value=100, threshold_operator="GT"),
    ).json()
    resp = client.patch(
        f"/api/v1/searches/{created['id']}", json={"threshold_value": 99}
    )
    assert resp.status_code == 200, resp.text
    row = resp.json()
    assert row["threshold_value"] == 99.0
    assert row["threshold_operator"] == "GT"


def test_update_patch_that_clears_threshold_is_reachable(db_schema) -> None:
    client = _client()
    created = client.post(
        "/api/v1/searches",
        json=_create_payload(threshold_value=100, threshold_operator="GT"),
    ).json()
    resp = client.patch(f"/api/v1/searches/{created['id']}", json={"threshold_value": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["threshold_value"] is None