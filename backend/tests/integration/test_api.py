"""API-level tests.

The liveness test needs no database and always runs. The overview/inventory
tests drive the real app against Postgres via a dependency override, and skip
without TEST_DATABASE_URL.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


def test_liveness_needs_no_dependencies() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/api/v1/health/live")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_security_headers_present() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/api/v1/health/live")
    assert resp.headers["x-frame-options"] == "DENY"
    assert resp.headers["x-content-type-options"] == "nosniff"


@pytest.mark.integration
def test_inventory_and_overview_flow(db_session) -> None:
    """Full flow: sync inventory, list it, read the overview.

    Uses the same engine as db_session by overriding get_session to bind to the
    test database.
    """
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.database import get_session

    url = os.environ["TEST_DATABASE_URL"]
    engine = create_async_engine(url, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _override

    with TestClient(app) as client:
        synced = client.post("/api/v1/log-sources/sync")
        assert synced.status_code == 200
        assert synced.json()["created"] > 0

        listing = client.get("/api/v1/log-sources")
        assert listing.status_code == 200
        assert len(listing.json()) == synced.json()["log_sources_seen"]

        overview = client.get("/api/v1/overview")
        assert overview.status_code == 200
        body = overview.json()
        assert body["log_sources"]["total_log_sources"] > 0


def _overview_client() -> TestClient:
    """A client whose request sessions bind to the test database."""
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.core.database import get_session

    # NullPool: TestClient runs each request on its own event loop and a
    # pooled asyncpg connection is pinned to its opener.
    engine = create_async_engine(
        os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _override
    return TestClient(app)


def _seed_instance(**kwargs) -> None:
    """Add one QRadarInstance row to the test database."""
    import asyncio
    import os
    import uuid

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.pool import NullPool

    from app.models.instance import QRadarInstance

    async def _go() -> None:
        engine = create_async_engine(
            os.environ["TEST_DATABASE_URL"], future=True, poolclass=NullPool
        )
        try:
            maker = async_sessionmaker(engine, expire_on_commit=False)
            async with maker() as session:
                session.add(
                    QRadarInstance(
                        name=f"inst-{uuid.uuid4().hex[:8]}",
                        console_host="mock",
                        provider_kind="mock",
                        **kwargs,
                    )
                )
                await session.commit()
        finally:
            await engine.dispose()

    asyncio.run(_go())


@pytest.mark.integration
def test_overview_carries_instance_last_health_check_at(db_session) -> None:
    """A checked instance says when its status was last checked."""
    from datetime import UTC, datetime

    checked_at = datetime(2026, 10, 14, 9, 30, tzinfo=UTC)
    _seed_instance(last_health_check_at=checked_at)

    body = _overview_client().get("/api/v1/overview").json()

    reported = body["instance_last_health_check_at"]
    assert reported is not None
    parsed = datetime.fromisoformat(reported)
    assert parsed.tzinfo is not None, "the timestamp must keep its timezone"
    assert parsed == checked_at


@pytest.mark.integration
def test_overview_instance_never_checked_is_null(db_session) -> None:
    _seed_instance()

    body = _overview_client().get("/api/v1/overview").json()

    assert body["instance_last_health_check_at"] is None


@pytest.mark.integration
def test_overview_without_instance_is_unknown_and_null(db_session) -> None:
    body = _overview_client().get("/api/v1/overview").json()

    assert body["instance_status"] == "UNKNOWN"
    assert body["instance_last_health_check_at"] is None


@pytest.mark.integration
def test_overview_never_exposes_instance_last_error(db_session) -> None:
    """`last_error` may hold a provider message; the status age is all
    the top bar needs, so the key must not appear at all."""
    _seed_instance(last_error="connection refused")

    body = _overview_client().get("/api/v1/overview").json()

    assert "last_error" not in body
