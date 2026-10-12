"""`collect_rule_matches` claims the RULE_MATCH slot before touching a provider."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from app.models.enums import SearchFamily
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.unit.test_search_cadence_task import _Maker


def _settings() -> Mock:
    return Mock(rule_match_collection_enabled=True)


def test_the_beat_ticks_every_minute() -> None:
    entry = celery_app.conf.beat_schedule["collect-rule-matches"]

    assert entry["task"] == "collect_rule_matches"
    assert entry["schedule"] == 60.0


@pytest.mark.asyncio
async def test_not_due_builds_no_provider_and_sends_no_search(monkeypatch) -> None:
    maker = _Maker()
    for_each = AsyncMock()
    provider = Mock(side_effect=AssertionError("provider must not be built"))

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            assert family == SearchFamily.RULE_MATCH
            return None

        async def record_run(self, *args: object, **kwargs: object) -> None:
            raise AssertionError("a tick that is not due records no run")

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", _settings)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)
    monkeypatch.setattr(tasks, "build_provider_for_instance", provider)

    assert await tasks.collect_rule_matches() == {"status": "not_due", "family": "RULE_MATCH"}
    for_each.assert_not_awaited()
    provider.assert_not_called()
    assert len(maker.sessions) == 1 and maker.sessions[0].commits == 1


@pytest.mark.asyncio
async def test_disabled_collection_does_not_even_claim_a_slot(monkeypatch) -> None:
    def boom():
        raise AssertionError("a disabled collector must not open a session")

    monkeypatch.setattr(tasks, "get_sessionmaker", boom)
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(rule_match_collection_enabled=False))

    assert await tasks.collect_rule_matches() == {"status": "disabled"}


def _item(**over: object) -> dict:
    return {
        "instance": "lab",
        "status": "ok",
        "windows_complete": 7,
        "windows_truncated": 1,
        "searches_sent": 4,
        "splits": 1,
        **over,
    }


async def _run_due(monkeypatch, result: dict) -> list[dict]:
    maker = _Maker()
    recorded: list[dict] = []

    class Cadence:
        def __init__(self, *args: object) -> None:
            pass

        async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
            return object()

        async def record_run(self, family: SearchFamily, **values: object) -> None:
            assert family == SearchFamily.RULE_MATCH
            recorded.append(values)

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", _settings)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", AsyncMock(return_value=result))
    await tasks.collect_rule_matches()
    return recorded


@pytest.mark.asyncio
async def test_due_run_records_the_real_counts(monkeypatch) -> None:
    result = {
        "instances": 2,
        "results": [_item(), _item(instance="b", windows_complete=2, searches_sent=1, splits=0)],
    }

    recorded = await _run_due(monkeypatch, result)

    assert recorded == [{"outcome": "ok", "searches": 5, "buckets": 11, "splits": 1}]


@pytest.mark.asyncio
async def test_a_failed_window_makes_the_outcome_failed(monkeypatch) -> None:
    recorded = await _run_due(
        monkeypatch, {"instances": 1, "results": [_item(status="failed")]}
    )

    assert recorded[0]["outcome"] == "failed"
    assert recorded[0]["searches"] == 4


@pytest.mark.asyncio
async def test_an_instance_that_raised_makes_the_counts_unknown_not_smaller(
    monkeypatch,
) -> None:
    raised = {"instance": "b", "status": "failed", "error_class": "RuntimeError"}

    recorded = await _run_due(
        monkeypatch, {"instances": 2, "results": [_item(), raised]}
    )

    assert recorded == [{"outcome": "failed", "searches": None, "buckets": None, "splits": None}]


@pytest.mark.asyncio
async def test_no_instance_records_unknown_counts(monkeypatch) -> None:
    recorded = await _run_due(
        monkeypatch, {"status": "no-instance", "instances": 0, "results": []}
    )

    assert recorded == [
        {"outcome": "no-instance", "searches": None, "buckets": None, "splits": None}
    ]
