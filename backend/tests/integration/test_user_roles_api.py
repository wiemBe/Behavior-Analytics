"""GET /api/v1/users/roles returns each role's name and permissions."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.enums import AuthSource
from app.models.identity import Role, User
from app.security.passwords import hash_password
from app.services.seed import _ROLES, ensure_default_roles

pytestmark = pytest.mark.integration

ORIGIN = "http://localhost:3000"
PASSWORD = "Correct-Horse-Battery-99!"


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def api(db_schema: str):
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "session", "public_origin": ORIGIN}
    )
    client = TestClient(app, headers={"Origin": ORIGIN})
    try:
        yield client, maker
    finally:
        client.close()
        engine.sync_engine.dispose()


async def test_roles_list_is_name_and_permissions_only(api) -> None:
    client, maker = api
    async with maker() as session:
        await ensure_default_roles(session)
        admin = await session.scalar(select(Role).where(Role.name == "admin"))
        assert admin is not None
        session.add(
            User(
                username="ada",
                auth_source=AuthSource.LOCAL.value,
                hashed_password=hash_password(PASSWORD),
                is_active=True,
                display_name="Ada",
                roles=[admin],
            )
        )
        await session.commit()

    signed = client.post("/api/v1/auth/login", json={"username": "ada", "password": PASSWORD})
    assert signed.status_code == 200, signed.text

    listed = client.get("/api/v1/users/roles")
    assert listed.status_code == 200, listed.text
    body = listed.json()
    # Names and permissions come from the seed, in name order. A new seeded
    # role is then expected, and the response still carries nothing else.
    assert [row["name"] for row in body] == sorted(_ROLES)
    for row in body:
        assert set(row) == {"name", "permissions"}
        assert isinstance(row["permissions"], list)
        assert all(isinstance(item, str) for item in row["permissions"])
        assert row["permissions"] == list(_ROLES[row["name"]])
    assert PASSWORD not in listed.text
    assert "hashed_password" not in listed.text
