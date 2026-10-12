"""The explanation task claims cadence before touching a QRadar provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.models.enums import SearchFamily
from app.workers import tasks


class _Session:
    def __init__(self) -> None:
        self.commits = 0

    async def commit(self) -> None:
        self.commits += 1


class _Context:
    def __init__(self, session: _Session) -> None:
        self.session = session

    async def __aenter__(self) -> _Session:
        return self.session

    async def __aexit__(self, *args: object) -> None:
        return None


class _Maker:
    def __init__(self) -> None:
        self.sessions: list[_Session] = []

    def __call__(self) -> _Context:
        session = _Session()
        self.sessions.append(session)
        return _Context(session)


@pytest.mark.asyncio
async def test_not_due_returns_before_any_provider_is_built(monkeypatch) -> None:
    maker = _Maker()
    for_each = AsyncMock()
    provider = Mock(side_effect=AssertionError("provider must not be built"))

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.ANOMALY_EXPLANATION
            return None

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(explanation_enabled=True))
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)
    monkeypatch.setattr(tasks, "build_provider_for_instance", provider)

    assert await tasks.collect_anomaly_explanations() == {
        "status": "not_due",
        "family": "ANOMALY_EXPLANATION",
    }
    for_each.assert_not_awaited()
    provider.assert_not_called()
    assert len(maker.sessions) == 1 and maker.sessions[0].commits == 1


@pytest.mark.asyncio
async def test_due_run_executes_and_records_honest_unknown_counts(monkeypatch) -> None:
    maker = _Maker()
    result = {
        "instances": 1,
        "results": [{"instance": "lab", "status": "ok", "explained": 2}],
    }
    for_each = AsyncMock(return_value=result)
    recorded: list[dict] = []

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.ANOMALY_EXPLANATION
            return object()

        async def record_run(self, family: SearchFamily, **values: object) -> None:
            assert family == SearchFamily.ANOMALY_EXPLANATION
            recorded.append(values)

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(explanation_enabled=True))
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)

    assert await tasks.collect_anomaly_explanations("lab") == result
    for_each.assert_awaited_once()
    assert recorded == [
        {"outcome": "ok", "searches": None, "buckets": None, "splits": None}
    ]
    assert [session.commits for session in maker.sessions] == [1, 1]
