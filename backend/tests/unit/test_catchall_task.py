"""The catch-all task claims its cadence slot first; settings; beat; defaults (D-9)."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.enums import SearchFamily
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.unit.test_search_cadence_task import _Maker


def _settings() -> Mock:
    return Mock(catchall_collection_enabled=True)


class TestRegistration:
    def test_the_beat_ticks_every_minute_and_retention_runs_daily(self) -> None:
        entry = celery_app.conf.beat_schedule["collect-catchall-senders"]
        assert entry == {"task": "collect_catchall_senders", "schedule": 60.0}
        retention = celery_app.conf.beat_schedule["apply-catchall-retention"]
        assert retention["task"] == "apply_catchall_retention"
        assert retention["schedule"] == 86400.0

    def test_the_tasks_are_registered(self) -> None:
        assert "collect_catchall_senders" in celery_app.tasks
        assert "apply_catchall_retention" in celery_app.tasks


class TestGate:
    @pytest.mark.asyncio
    async def test_not_due_builds_no_provider_and_sends_no_search(self, monkeypatch) -> None:
        maker = _Maker()
        for_each = AsyncMock()
        provider = Mock(side_effect=AssertionError("provider must not be built"))

        class Cadence:
            def __init__(self, *args: object) -> None:
                pass

            async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
                assert family == SearchFamily.CATCHALL_SENDERS
                return None

            async def record_run(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("a tick that is not due records no run")

        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
        monkeypatch.setattr(tasks, "get_settings", _settings)
        monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
        monkeypatch.setattr(tasks, "_for_each_instance", for_each)
        monkeypatch.setattr(tasks, "build_provider_for_instance", provider)

        assert await tasks.collect_catchall_senders() == {
            "status": "not_due",
            "family": "CATCHALL_SENDERS",
        }
        for_each.assert_not_awaited()
        provider.assert_not_called()
        assert len(maker.sessions) == 1 and maker.sessions[0].commits == 1

    @pytest.mark.asyncio
    async def test_a_disabled_collector_does_not_even_claim_a_slot(self, monkeypatch) -> None:
        def boom():
            raise AssertionError("a disabled collector must not open a session")

        monkeypatch.setattr(tasks, "get_sessionmaker", boom)
        monkeypatch.setattr(
            tasks, "get_settings", lambda: Mock(catchall_collection_enabled=False)
        )
        assert await tasks.collect_catchall_senders() == {"status": "disabled"}


def _item(**over: object) -> dict:
    return {
        "instance": "lab",
        "status": "ok",
        "buckets_complete": 7,
        "buckets_lower_bound": 1,
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
            assert family == SearchFamily.CATCHALL_SENDERS
            recorded.append(values)

    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", _settings)
    monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
    monkeypatch.setattr(tasks, "_for_each_instance", AsyncMock(return_value=result))
    await tasks.collect_catchall_senders()
    return recorded


class TestRecordedRun:
    @pytest.mark.asyncio
    async def test_a_due_run_records_the_real_counts(self, monkeypatch) -> None:
        result = {
            "instances": 2,
            "results": [
                _item(),
                _item(instance="b", buckets_complete=2, searches_sent=1, splits=0),
            ],
        }
        recorded = await _run_due(monkeypatch, result)
        assert recorded == [{"outcome": "ok", "searches": 5, "buckets": 11, "splits": 1}]

    @pytest.mark.asyncio
    async def test_a_failed_instance_makes_the_outcome_failed(self, monkeypatch) -> None:
        recorded = await _run_due(
            monkeypatch, {"instances": 1, "results": [_item(status="failed")]}
        )
        assert recorded[0]["outcome"] == "failed" and recorded[0]["searches"] == 4

    @pytest.mark.asyncio
    async def test_an_instance_that_raised_makes_the_counts_unknown(self, monkeypatch) -> None:
        raised = {"instance": "b", "status": "failed", "error_class": "RuntimeError"}
        recorded = await _run_due(monkeypatch, {"instances": 2, "results": [_item(), raised]})
        assert recorded == [
            {"outcome": "failed", "searches": None, "buckets": None, "splits": None}
        ]


class TestSettings:
    def test_defaults_match_the_design(self) -> None:
        s = Settings()
        assert s.catchall_type_ids == [67, 39, 212, 367, 4000]
        assert s.catchall_max_new_senders_per_bucket == 20
        assert s.catchall_max_active_senders_per_source == 2000
        assert s.catchall_min_admission_events == 3
        assert s.catchall_top_senders == 20
        assert s.catchall_retention_days >= 2

    def test_new_sender_alerts_are_off_by_default_d9(self) -> None:
        assert Settings().catchall_new_sender_alerts_enabled is False

    @pytest.mark.parametrize(
        "ids", [[], [0], [-1], [100_001], list(range(1, 34))]
    )
    def test_type_ids_are_bounded(self, ids: list[int]) -> None:
        with pytest.raises(ValidationError):
            Settings(catchall_type_ids=ids)

    def test_type_ids_must_be_integers(self) -> None:
        with pytest.raises(ValidationError):
            Settings(catchall_type_ids=["67) OR 1=1"])

    def test_the_top_list_is_capped_at_50(self) -> None:
        assert Settings(catchall_top_senders=50).catchall_top_senders == 50
        with pytest.raises(ValidationError):
            Settings(catchall_top_senders=51)
        with pytest.raises(ValidationError):
            Settings(catchall_top_senders=0)
