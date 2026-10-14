"""The geo task claims its cadence slot first; settings; beat; defaults (D-10)."""

from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.models.enums import SearchFamily
from app.security.rbac import Principal
from app.workers import tasks
from app.workers.celery_app import celery_app
from tests.unit.test_search_cadence_task import _Maker


class TestRegistration:
    def test_the_beat_ticks_every_minute_and_retention_runs_daily(self) -> None:
        entry = celery_app.conf.beat_schedule["collect-geo-countries"]
        assert entry == {"task": "collect_geo_countries", "schedule": 60.0}
        retention = celery_app.conf.beat_schedule["apply-geo-retention"]
        assert retention["task"] == "apply_geo_retention"
        assert retention["schedule"] == 86400.0

    def test_the_tasks_are_registered(self) -> None:
        assert "collect_geo_countries" in celery_app.tasks
        assert "apply_geo_retention" in celery_app.tasks

    def test_the_family_is_last_in_the_enum(self) -> None:
        assert list(SearchFamily)[-1] is SearchFamily.GEO_COLLECTION
        assert [f.value for f in SearchFamily][-2:] == ["CATCHALL_SENDERS", "GEO_COLLECTION"]


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
                assert family == SearchFamily.GEO_COLLECTION
                return None

            async def record_run(self, *args: object, **kwargs: object) -> None:
                raise AssertionError("a tick that is not due records no run")

        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
        monkeypatch.setattr(tasks, "get_settings", lambda: Mock(geo_collection_enabled=True))
        monkeypatch.setattr("app.services.search_cadence.SearchCadenceService", Cadence)
        monkeypatch.setattr(tasks, "_for_each_instance", for_each)
        monkeypatch.setattr(tasks, "build_provider_for_instance", provider)

        assert await tasks.collect_geo_countries() == {
            "status": "not_due",
            "family": "GEO_COLLECTION",
        }
        for_each.assert_not_called()

    @pytest.mark.asyncio
    async def test_disabled_does_nothing(self, monkeypatch) -> None:
        monkeypatch.setattr(tasks, "get_settings", lambda: Mock(geo_collection_enabled=False))
        monkeypatch.setattr(
            tasks, "get_sessionmaker", Mock(side_effect=AssertionError("no session"))
        )
        assert await tasks.collect_geo_countries() == {"status": "disabled"}


class TestSettings:
    def test_defaults(self) -> None:
        s = Settings()
        assert s.geo_collection_enabled is True
        assert s.geo_directions == ["SOURCE", "DESTINATION"]
        assert s.geo_max_rows_per_search == 5000
        assert s.geo_max_countries_per_bucket == 64
        assert s.geo_retention_days == 35

    @pytest.mark.parametrize("value", [[], ["SOURCE", "SOURCE", "DESTINATION"], ["sourceip"]])
    def test_directions_are_validated(self, value: list[str]) -> None:
        with pytest.raises(ValidationError):
            Settings(geo_directions=value)

    def test_directions_are_deduplicated_in_order(self) -> None:
        assert Settings(geo_directions=["DESTINATION", "DESTINATION"]).geo_directions == [
            "DESTINATION"
        ]


class TestPermission:
    def test_read_geo_is_satisfied_by_the_read_wildcard_only(self) -> None:
        from app.security.rbac import PERM_GEO_READ

        assert PERM_GEO_READ == "read:geo"
        assert Principal("a", permissions=frozenset({"read:*"})).has(PERM_GEO_READ)
        assert Principal("a", permissions=frozenset({"read:geo"})).has(PERM_GEO_READ)
        assert not Principal("a", permissions=frozenset({"read:fleet"})).has(PERM_GEO_READ)
        assert not Principal("a", permissions=frozenset()).has(PERM_GEO_READ)
