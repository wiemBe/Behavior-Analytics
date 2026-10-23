"""GET /api/v1/searches/ariel-budget through the real app. Counts only."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.main import create_app
from app.schemas.ariel import ArielBudgetOut
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run search integration tests",
    ),
]

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
WAF_ONLY = Principal(subject="waf", permissions=frozenset({"admin:waf"}))
KEYS = {
    "enforced",
    "global_cap",
    "instance_cap",
    "reserved_live_slots",
    "global_held",
    "instance_held",
    "permit_wait_seconds",
}


def budget_settings():  # type: ignore[no-untyped-def]
    return get_settings().model_copy(
        update={
            "ariel_global_max_concurrent_searches": 2,
            "ariel_max_concurrent_searches": 3,
            "ariel_permit_reserved_live_slots": 1,
            "ariel_permit_wait_seconds": 30.0,
            "ariel_permit_lock_namespace": 24715,
            "ariel_permit_instance_lock_namespace": 24716,
        }
    )


def _app(engine, settings, principal):  # type: ignore[no-untyped-def]
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_settings] = lambda: settings
    app.dependency_overrides[get_principal] = lambda: principal
    return app


def _assert_counts_only(body: dict[str, object]) -> None:
    assert set(body) == KEYS
    assert set(ArielBudgetOut.model_fields) == KEYS
    assert all(not isinstance(value, str) for value in body.values())


async def test_ariel_budget_with_locks_held_without_locks_and_not_enforced(
    db_schema: str,
) -> None:
    settings = budget_settings()
    engine = create_async_engine(db_schema, poolclass=NullPool)
    holder = create_async_engine(db_schema)
    try:
        with TestClient(_app(engine, settings, ADMIN)) as client:
            empty = client.get("/api/v1/searches/ariel-budget")
        assert empty.status_code == 200
        assert empty.json() == {
            "enforced": True,
            "global_cap": 2,
            "instance_cap": 3,
            "reserved_live_slots": 1,
            "global_held": 0,
            "instance_held": 0,
            "permit_wait_seconds": 30.0,
        }
        _assert_counts_only(empty.json())
        assert "SELECT" not in empty.text
        assert "pg_locks" not in empty.text

        conn = await holder.connect()
        try:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            for slot in range(settings.ariel_global_max_concurrent_searches):
                got = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:ns, :slot)").bindparams(
                        ns=settings.ariel_permit_lock_namespace, slot=slot
                    )
                )
                assert got is True
            with TestClient(_app(engine, settings, ADMIN)) as client:
                full = client.get("/api/v1/searches/ariel-budget")
        finally:
            await conn.invalidate()
        assert full.status_code == 200
        assert full.json() == {
            "enforced": True,
            "global_cap": 2,
            "instance_cap": 3,
            "reserved_live_slots": 1,
            "global_held": 2,
            "instance_held": 0,
            "permit_wait_seconds": 30.0,
        }
        _assert_counts_only(full.json())

        original = engine.dialect.name
        engine.dialect.name = "sqlite"
        try:
            with TestClient(_app(engine, settings, ADMIN)) as client:
                quiet = client.get("/api/v1/searches/ariel-budget")
        finally:
            engine.dialect.name = original
        assert quiet.status_code == 200
        assert quiet.json() == {
            "enforced": False,
            "global_cap": 2,
            "instance_cap": 3,
            "reserved_live_slots": 1,
            "global_held": None,
            "instance_held": None,
            "permit_wait_seconds": 30.0,
        }
        _assert_counts_only(quiet.json())
    finally:
        await holder.dispose()
        await engine.dispose()


@pytest.mark.parametrize(
    "principal",
    [READER, WAF_ONLY],
)
async def test_ariel_budget_refuses_a_caller_without_both_permissions(
    db_schema: str, principal: Principal
) -> None:
    engine = create_async_engine(db_schema, poolclass=NullPool)
    try:
        with TestClient(_app(engine, budget_settings(), principal)) as client:
            response = client.get("/api/v1/searches/ariel-budget")
        assert response.status_code == 403
    finally:
        await engine.dispose()
