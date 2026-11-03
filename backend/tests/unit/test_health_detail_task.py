"""The QRadar health task: cadence gate, family switch, beat, settings, doctor wiring (HLT-2)."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.cli import doctor as doctor_module
from app.core.config import Settings
from app.models.enums import SearchCadence, SearchFamily, default_search_cadence
from app.services.search_cadence import family_enabled
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.unit.test_search_cadence_task import _Maker

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="


class TestRegistration:
    def test_the_beat_ticks_every_minute_and_retention_runs_daily(self) -> None:
        entry = celery_app.conf.beat_schedule["collect-qradar-health"]
        assert entry == {"task": "collect_qradar_health", "schedule": 60.0}
        retention = celery_app.conf.beat_schedule["apply-qradar-health-retention"]
        assert retention["task"] == "apply_qradar_health_retention"
        assert retention["schedule"] == 86400.0

    def test_the_tasks_are_registered(self) -> None:
        assert "collect_qradar_health" in celery_app.tasks
        assert "apply_qradar_health_retention" in celery_app.tasks

    def test_the_family_defaults_to_fifteen_minutes_and_has_a_switch(self) -> None:
        assert default_search_cadence(SearchFamily.QRADAR_HEALTH) is SearchCadence.FIFTEEN_MINUTES
        assert SearchFamily.QRADAR_HEALTH.value in doctor_module._FAMILY_SWITCHES
        assert (
            doctor_module._FAMILY_SWITCHES["QRADAR_HEALTH"] == "qradar_health_collection_enabled"
        )

    def test_the_family_switch_turns_the_family_off(self) -> None:
        assert family_enabled(SearchFamily.QRADAR_HEALTH, Settings(encryption_key=KEY)) is True
        off = Settings(encryption_key=KEY, qradar_health_collection_enabled=False)
        assert family_enabled(SearchFamily.QRADAR_HEALTH, off) is False

    def test_the_retention_deleter_is_in_the_doctor_catalog(self) -> None:
        tables = {
            table.table
            for deleter in doctor_module.BATCH_DELETERS
            if deleter.task == "apply_qradar_health_retention"
            for table in deleter.tables
        }
        assert tables == {
            "qradar_health_run",
            "qradar_health_metric_bucket",
            "qradar_health_notice_bucket",
            "qradar_health_rest_sample",
        }


class TestGate:
    @pytest.mark.asyncio
    async def test_a_tick_that_is_not_due_builds_no_provider_and_sends_no_search(
        self, monkeypatch
    ) -> None:
        maker = _Maker()
        for_each = AsyncMock()

        class Cadence:
            def __init__(self, *args: object) -> None:
                pass

            async def claim_slot(self, family: SearchFamily):  # type: ignore[no-untyped-def]
                assert family == SearchFamily.QRADAR_HEALTH
                return None

            async def record_run(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("a tick that is not due records no run")

        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
        monkeypatch.setattr(
            tasks, "get_settings", lambda: Mock(qradar_health_collection_enabled=True)
        )
        monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
        monkeypatch.setattr(tasks, "_for_each_instance", for_each)
        monkeypatch.setattr(
            tasks, "build_provider_for_instance", Mock(side_effect=AssertionError("no provider"))
        )

        assert await tasks.collect_qradar_health() == {
            "status": "not_due",
            "family": "QRADAR_HEALTH",
        }
        for_each.assert_not_called()

    @pytest.mark.asyncio
    async def test_the_family_off_is_not_scheduled_and_touches_no_session(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            tasks, "get_settings", lambda: Mock(qradar_health_collection_enabled=False)
        )
        monkeypatch.setattr(
            tasks, "get_sessionmaker", Mock(side_effect=AssertionError("no session"))
        )
        monkeypatch.setattr(
            tasks, "_for_each_instance", AsyncMock(side_effect=AssertionError("not scheduled"))
        )
        assert await tasks.collect_qradar_health() == {"status": "disabled"}


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings(encryption_key=KEY)
        assert s.qradar_health_collection_enabled is True
        assert s.qradar_health_disk_warn_percent == 85
        assert s.qradar_health_disk_fail_percent == 95
        assert s.qradar_health_cre_queue_warn == 10_000
        assert s.qradar_health_retention_days == 35
        assert "/store" in s.qradar_health_disk_mounts

    def test_the_fail_share_must_exceed_the_warn_share(self) -> None:
        with pytest.raises(ValidationError):
            Settings(
                encryption_key=KEY,
                qradar_health_disk_warn_percent=90,
                qradar_health_disk_fail_percent=90,
            )

    @pytest.mark.parametrize("mount", ["store", "/store;rm", "/a b", "", "/x'y", "../etc"])
    def test_a_mount_that_is_not_a_plain_absolute_path_is_refused(self, mount: str) -> None:
        with pytest.raises(ValidationError):
            Settings(encryption_key=KEY, qradar_health_disk_mounts=[mount])

    def test_mounts_are_trimmed_and_deduplicated_in_order(self) -> None:
        s = Settings(encryption_key=KEY, qradar_health_disk_mounts=[" /data ", "/", "/data"])
        assert s.qradar_health_disk_mounts == ["/data", "/"]
