"""Readiness through the real app, against the test database.

``db_schema`` builds tables from model metadata. It does not create
``alembic_version``. These tests stamp that table themselves and call
``GET /api/v1/health/ready``. No existing integration test calls that route
(the session-auth sweep lists it as open and skips it).
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

from app.core.database import get_session
from app.core.version import migration_head
from app.main import create_app

pytestmark = pytest.mark.integration

_LEAK = "OPS20C_LEAK_TOKEN"


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


def _drop_alembic_objects() -> None:
    """Remove the probe table, view and function. Model metadata does not own them."""
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
            conn.execute(text("DROP FUNCTION IF EXISTS readiness_boom()"))
    finally:
        engine.dispose()


@pytest.fixture
def alembic_stamp(db_schema: str) -> Iterator[str]:
    del db_schema
    _drop_alembic_objects()
    try:
        yield _async_url()
    finally:
        _drop_alembic_objects()


@contextmanager
def _client() -> Iterator[TestClient]:
    engine = create_async_engine(_async_url(), future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _override
    # Lifespan calls configure_logging, which replaces the root handlers.
    # Put them back so a later test in the same session still sees its own.
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    try:
        with TestClient(app) as client:
            yield client
    finally:
        root.handlers.clear()
        for handler in saved_handlers:
            root.addHandler(handler)
        root.setLevel(saved_level)
        engine.sync_engine.dispose()


def _ready() -> tuple[int, str]:
    with _client() as client:
        response = client.get("/api/v1/health/ready")
    return response.status_code, response.text


def _assert_no_database_error(body: str) -> None:
    for needle in (
        _LEAK,
        "Traceback",
        "ProgrammingError",
        "UndefinedTable",
        "asyncpg",
        "psycopg",
        "does not exist",
        "sqlalchemy",
    ):
        assert needle not in body


def test_head_match_returns_200(alembic_stamp: str) -> None:
    del alembic_stamp
    head = migration_head()
    assert head
    _exec("CREATE TABLE alembic_version (version_num VARCHAR(128) NOT NULL)")
    _exec("INSERT INTO alembic_version (version_num) VALUES (:rev)", {"rev": head})
    with _client() as client:
        response = client.get("/api/v1/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "ok", "revision": head}


def test_behind_names_both_revisions(alembic_stamp: str) -> None:
    del alembic_stamp
    head = migration_head()
    assert head
    behind = "0000_not_head"
    assert behind != head
    _exec("CREATE TABLE alembic_version (version_num VARCHAR(128) NOT NULL)")
    _exec("INSERT INTO alembic_version (version_num) VALUES (:rev)", {"rev": behind})
    status, body = _ready()
    assert status == 503
    assert behind in body
    assert head in body
    _assert_no_database_error(body)


def test_missing_table_is_503_without_the_database_error(alembic_stamp: str) -> None:
    del alembic_stamp
    status, body = _ready()
    assert status == 503
    assert "database readiness check failed" in body
    _assert_no_database_error(body)


def test_query_error_is_503_without_the_database_error(alembic_stamp: str) -> None:
    del alembic_stamp
    _exec(
        "CREATE FUNCTION readiness_boom() RETURNS varchar LANGUAGE plpgsql AS $$ "
        "BEGIN RAISE EXCEPTION 'OPS20C_LEAK_TOKEN'; END $$"
    )
    _exec("CREATE VIEW alembic_version AS SELECT readiness_boom() AS version_num")
    status, body = _ready()
    assert status == 503
    assert "database readiness check failed" in body
    _assert_no_database_error(body)
