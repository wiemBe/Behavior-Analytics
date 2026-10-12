"""The Phase A task gates QRadar access and reports measured cadence costs."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.models.enums import SearchFamily
from app.workers import tasks
from app.workers.celery_app import celery_app


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


def test_phase_a_collection_and_evaluation_tick_every_minute() -> None:
    schedule = celery_app.conf.beat_schedule
    assert schedule["collect-metrics"]["schedule"] == 60.0
    assert schedule["evaluate-anomalies"]["schedule"] == 60.0


@pytest.mark.asyncio
async def test_phase_a_not_due_returns_before_provider_construction(monkeypatch) -> None:
    maker = _Maker()
    for_each = AsyncMock()
    provider = Mock(side_effect=AssertionError("provider must not be built"))

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.PHASE_A_VOLUME
            return None

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)
    monkeypatch.setattr(tasks, "build_provider_for_instance", provider)

    assert await tasks.collect_metrics() == {
        "status": "not_due",
        "family": "PHASE_A_VOLUME",
    }
    for_each.assert_not_awaited()
    provider.assert_not_called()
    assert len(maker.sessions) == 1 and maker.sessions[0].commits == 1


@pytest.mark.asyncio
async def test_phase_a_due_records_real_search_bucket_and_split_counts(monkeypatch) -> None:
    maker = _Maker()
    result = {
        "instances": 2,
        "results": [
            {
                "instance": "one",
                "status": "ok",
                "searches_sent": 1,
                "buckets_collected": 12,
                "splits": 0,
            },
            {
                "instance": "two",
                "status": "partial",
                "searches_sent": 5,
                "buckets_collected": 7,
                "splits": 2,
            },
        ],
    }
    for_each = AsyncMock(return_value=result)
    recorded: list[dict] = []

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.PHASE_A_VOLUME
            return object()

        async def record_run(self, family: SearchFamily, **values: object) -> None:
            assert family == SearchFamily.PHASE_A_VOLUME
            recorded.append(values)

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)

    assert await tasks.collect_metrics("lab") == result
    assert recorded == [
        {"outcome": "partial", "searches": 6, "buckets": 19, "splits": 2}
    ]
    assert [session.commits for session in maker.sessions] == [1, 1]


@pytest.mark.asyncio
async def test_phase_a_failed_run_records_unknown_counts_as_none(monkeypatch) -> None:
    maker = _Maker()
    result = {
        "instances": 1,
        "results": [{"instance": "one", "status": "failed", "error_class": "ProviderError"}],
    }
    recorded: list[dict] = []

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, _family: SearchFamily):  # type: ignore[no-untyped-def]
            return object()

        async def record_run(self, _family: SearchFamily, **values: object) -> None:
            recorded.append(values)

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", AsyncMock(return_value=result))

    assert await tasks.collect_metrics() == result
    assert recorded == [
        {"outcome": "failed", "searches": None, "buckets": None, "splits": None}
    ]


@pytest.mark.asyncio
async def test_phase_a_crashed_run_records_failed_with_unknown_counts(monkeypatch) -> None:
    maker = _Maker()
    recorded: list[dict] = []

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, _family: SearchFamily):  # type: ignore[no-untyped-def]
            return object()

        async def record_run(self, _family: SearchFamily, **values: object) -> None:
            recorded.append(values)

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", AsyncMock(side_effect=RuntimeError("x")))

    with pytest.raises(RuntimeError):
        await tasks.collect_metrics()
    assert recorded == [
        {"outcome": "failed", "searches": None, "buckets": None, "splits": None}
    ]
