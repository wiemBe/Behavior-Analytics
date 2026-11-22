"""``GET /api/v1/healthz`` through the real app, against the test database.

The unit suite pins the status mapping, timeouts, cache and body shape with
replaceable checks. These integration tests prove the route is mounted on the
real app and that the database and migration components answer correctly
against a real schema. Redis, Celery and beat are replaceable here: they are
external services, and the unit suite owns their guard tests.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.api.routes import healthz
from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.core.version import migration_head
from app.main import create_app

pytestmark = pytest.mark.integration


def _async_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL not set")
    return url


def _sync_url() -> str:
    url = _async_url()
    if "+asyncpg" not in url:
        pytest.fail("TEST_DATABASE_URL must use the asyncpg driver")
    return url.replace("+asyncpg", "+psycopg", 1)


def _exec(sql: str, params: dict[str, str] | None = None) -> None:
    engine = create_engine(_sync_url(), future=True)
    try:
        with engine.begin() as conn:
            conn.execute(text(sql), params or {})
    finally:
        engine.dispose()


def _drop_alembic_version() -> None:
    """The schema fixture does not own ``alembic_version``; remove our own copy."""
    engine = create_engine(_sync_url(), future=True)
    try:
        with engine.begin() as conn:
            kind = conn.execute(
                text(
                    "SELECT c.relkind FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' AND c.relname = 'alembic_version'"
                )
            ).scalar()
            if kind == "v":
                conn.execute(text("DROP VIEW alembic_version"))
            elif kind == "r":
                conn.execute(text("DROP TABLE alembic_version"))
    finally:
        engine.dispose()


@pytest.fixture
def stamped(db_schema: str) -> Iterator[str]:
    del db_schema
    _drop_alembic_version()
    try:
        yield _async_url()
    finally:
        _drop_alembic_version()


@pytest.fixture(autouse=True)
def _reset() -> Iterator[None]:
    healthz.invalidate_healthz_cache()
    limiter.reset()
    yield
    healthz.invalidate_healthz_cache()
    limiter.reset()


@contextmanager
def _client() -> Iterator[TestClient]:
    engine = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override() -> Iterator[object]:  # type: ignore[misc]
        async with maker() as session:
            yield session

    app = create_app()
    app.dependency_overrides[get_session] = _override
    # Lifespan replaces the root handlers and installs a redaction filter. Put
    # them back so a later test in the same session sees its own configuration.
    root = logging.getLogger()
    httpx_logger = logging.getLogger("httpx")
    httpcore_logger = logging.getLogger("httpcore")
    saved_handlers = list(root.handlers)
    saved_level = root.level
    saved_filters = list(root.filters)
    saved_httpx_level = httpx_logger.level
    saved_httpcore_level = httpcore_logger.level
    try:
        with TestClient(app) as client:
            yield client
    finally:
        root.handlers.clear()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.filters[:] = saved_filters
        root.setLevel(saved_level)
        httpx_logger.setLevel(saved_httpx_level)
        httpcore_logger.setLevel(saved_httpcore_level)
        engine.sync_engine.dispose()


def _stamp_head() -> None:
    head = migration_head()
    assert head
    _exec("CREATE TABLE alembic_version (version_num VARCHAR(128) NOT NULL)")
    _exec("INSERT INTO alembic_version (version_num) VALUES (:rev)", {"rev": head})


def _external_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    async def ok() -> bool:
        return True

    for name in ("redis", "workers", "walker", "beat"):
        monkeypatch.setattr(healthz, f"_check_{name}", ok)


def test_healthz_is_open_and_ok_against_a_current_schema(
    stamped: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del stamped
    _stamp_head()
    _external_ok(monkeypatch)
    with _client() as client:
        response = client.get("/api/v1/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "components": dict.fromkeys(healthz.COMPONENTS, "ok"),
    }


def test_one_external_failure_degrades_the_public_answer(
    stamped: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    del stamped
    _stamp_head()
    _external_ok(monkeypatch)

    async def down() -> bool:
        return False

    monkeypatch.setattr(healthz, "_check_redis", down)
    with _client() as client:
        response = client.get("/api/v1/healthz")

    assert response.status_code == 503
    assert response.json() == {
        "status": "degraded",
        "components": {
            name: "fail" if name == "redis" else "ok" for name in healthz.COMPONENTS
        },
    }


def test_healthz_is_not_rate_limited(stamped: str, monkeypatch: pytest.MonkeyPatch) -> None:
    del stamped
    _stamp_head()
    _external_ok(monkeypatch)
    monkeypatch.setattr(get_settings(), "rate_limit_default", "1/minute")
    with _client() as client:
        statuses = [client.get("/api/v1/healthz").status_code for _ in range(4)]

    assert statuses == [200, 200, 200, 200]
