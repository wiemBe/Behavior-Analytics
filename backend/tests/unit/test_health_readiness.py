"""Health readiness endpoint: checks SELECT 1 + alembic_version matches image head."""
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import health


class FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


def make_session(rows, raises_on_select_1=None):
    session = AsyncMock()
    async def execute(sql):
        if raises_on_select_1 and "SELECT 1" in str(sql):
            raise raises_on_select_1
        # Return a FakeResult for SELECT version_num query.
        return FakeResult(rows)
    session.execute = execute
    return session


@pytest.mark.asyncio
async def test_head_match_returns_200():
    with patch.object(health, "migration_head", return_value="0032_foo"):
        session = make_session([["0032_foo"]])
        result = await health.ready(session)
        assert result == {"status": "ready", "database": "ok", "revision": "0032_foo"}


@pytest.mark.asyncio
async def test_behind_returns_503():
    with patch.object(health, "migration_head", return_value="0032_foo"):
        session = make_session([["0028_old"]])
        with pytest.raises(HTTPException) as exc:
            await health.ready(session)
        assert exc.value.status_code == 503
        assert "0028_old" in exc.value.detail
        assert "0032_foo" in exc.value.detail


@pytest.mark.asyncio
async def test_no_revision_row_returns_503():
    with patch.object(health, "migration_head", return_value="0032_foo"):
        session = make_session([])
        with pytest.raises(HTTPException) as exc:
            await health.ready(session)
        assert exc.value.status_code == 503
        assert "database revision missing" in exc.value.detail


@pytest.mark.asyncio
async def test_query_failing_returns_503_without_leak():
    with patch.object(health, "migration_head", return_value="0032_foo"):
        session = AsyncMock()
        async def execute(sql):
            raise RuntimeError("psycopg2.InternalError")
        session.execute = execute
        with pytest.raises(HTTPException) as exc:
            await health.ready(session)
        assert exc.value.status_code == 503
        assert "database readiness check failed" in exc.value.detail
        assert "InternalError" not in exc.value.detail
