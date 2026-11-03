"""The two parts of the QRadar health collection, without a database (HLT-2).

`_collect_metrics` and `_collect_notifications` use the provider and the settings only, so a
mock provider is enough: the property check, a failed or rejected search and a truncated one.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import Mock

import pytest

from app.collectors.qradar_health_collector import (
    QRadarHealthCollector,
    QRadarHealthReport,
    window_for,
)
from app.core.config import Settings
from app.i18n.messages import render
from app.providers.base import (
    ProviderError,
    ProviderQueryRejectedError,
    ProviderUnavailableError,
)
from app.providers.dto import CustomPropertyDTO
from app.providers.mock import MockHealthSearch, MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider

KEY = "sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A="
START = datetime(2026, 11, 3, 12, 0, tzinfo=UTC)
END = datetime(2026, 11, 3, 12, 15, tzinfo=UTC)


def collector(provider: MockQRadarProvider, **overrides: Any) -> QRadarHealthCollector:
    settings = Settings(encryption_key=KEY, **overrides)
    return QRadarHealthCollector(cast(Any, None), provider, settings=settings)


def report() -> QRadarHealthReport:
    import uuid

    return QRadarHealthReport(instance_id=uuid.uuid4())


class WithoutProperties(MockQRadarProvider):
    """A console whose catalog lacks some of the shipped Health Metrics properties."""

    def __init__(self, missing: set[str], **kwargs: Any) -> None:
        super().__init__(seed=1, **kwargs)
        self._missing = missing

    async def list_custom_properties(self, *, log_source_type_id: int | None = None):  # type: ignore[no-untyped-def]
        listed = await super().list_custom_properties(log_source_type_id=log_source_type_id)
        return [item for item in listed if item.name not in self._missing]


@pytest.mark.asyncio
class TestMetricsPart:
    async def test_a_whole_answer_is_ok_and_folded(self) -> None:
        provider = MockQRadarProvider(seed=1)
        outcome, folded = await collector(provider)._collect_metrics(START, END, report())
        assert outcome.state == "OK" and outcome.reason is None
        assert folded is not None and folded.dropped_mounts == 2  # overlay and /dev/shm
        assert {b.element for b in folded.buckets if b.metric == "DiskUsage"} == {
            "/", "/store", "/tmp",  # noqa: S108
        }
        assert [s.kind for s in provider.health_searches] == ["metrics"]
        search = provider.health_searches[0]
        assert (search.window_start, search.window_end) == (START, END)

    async def test_a_missing_property_sends_no_search_and_is_not_collected(self) -> None:
        provider = WithoutProperties({"Element", "Value"})
        rep = report()
        outcome, folded = await collector(provider)._collect_metrics(START, END, rep)
        assert outcome.state == "NOT_COLLECTED" and folded is None
        assert outcome.reason is not None
        assert outcome.reason.code == "qradar_health_detail.reason.property_missing"
        assert render(outcome.reason) == (
            "These custom properties do not exist on this console: Value, Element. "
            "No health metric search was sent."
        )
        assert provider.health_searches == []
        assert rep.searches_sent == 0

    async def test_an_unreadable_catalog_fails_closed_the_same_way(self) -> None:
        provider = MockQRadarProvider(seed=1)

        async def boom(*, log_source_type_id: int | None = None) -> list[CustomPropertyDTO]:
            raise ProviderUnavailableError("down")

        provider.list_custom_properties = boom  # type: ignore[method-assign]
        outcome, folded = await collector(provider)._collect_metrics(START, END, report())
        assert outcome.state == "NOT_COLLECTED" and folded is None
        assert outcome.reason is not None
        assert outcome.reason.code == "qradar_health_detail.reason.property_unreadable"
        assert provider.health_searches == []

    async def test_a_failed_search_is_failed_and_stores_nothing(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_fault = lambda _s: ProviderUnavailableError("secret-host.example")
        rep = report()
        outcome, folded = await collector(provider)._collect_metrics(START, END, rep)
        assert outcome.state == "FAILED" and folded is None
        assert outcome.reason is not None
        assert outcome.reason.code == "qradar_health_detail.reason.search_failed"
        assert "secret-host" not in render(outcome.reason)
        assert rep.searches_sent == 1

    async def test_a_rejected_search_is_failed_with_its_own_reason(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_fault = lambda _s: ProviderQueryRejectedError("bad query text")
        outcome, _ = await collector(provider)._collect_metrics(START, END, report())
        assert outcome.state == "FAILED"
        assert outcome.reason is not None
        assert outcome.reason.code == "qradar_health_detail.reason.search_rejected"

    async def test_a_truncated_answer_is_partial(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_force_truncated = lambda s: s.kind == "metrics"
        outcome, folded = await collector(provider)._collect_metrics(START, END, report())
        assert outcome.state == "PARTIAL" and outcome.measured
        assert folded is not None and folded.buckets
        assert outcome.reason is not None
        assert outcome.reason.code == "qradar_health_detail.reason.truncated"

    async def test_rows_over_the_cap_are_truncation(self) -> None:
        provider = MockQRadarProvider(seed=1)
        outcome, _folded = await collector(
            provider, qradar_health_max_metric_rows=100
        )._collect_metrics(START, END, report())
        assert outcome.state == "OK"  # 14 rows fit in 100
        provider.health_metric_script = lambda _s: [
            {"source": "Health Metrics-2 :: h", "metric": "SystemCPU", "element": str(n),
             "max_value": 1, "avg_value": 1, "samples": 1}
            for n in range(150)
        ]
        outcome, _folded = await collector(
            provider, qradar_health_max_metric_rows=100
        )._collect_metrics(START, END, report())
        assert outcome.state == "PARTIAL"

    async def test_an_answer_with_no_usable_row_is_not_collected_never_ok(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_metric_script = lambda _s: [
            {"source": "Health Metrics-2 :: h", "metric": "CREQueueSize", "element": None,
             "max_value": None, "avg_value": None, "samples": 4}
        ]
        outcome, folded = await collector(provider)._collect_metrics(START, END, report())
        assert outcome.state == "NOT_COLLECTED" and not outcome.measured
        assert folded is not None and folded.null_values == 1
        assert outcome.reason is not None
        assert outcome.reason.code == "qradar_health_detail.reason.no_events"

    async def test_a_null_value_never_reaches_a_stored_zero(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_metric_script = lambda _s: [
            {"source": "Health Metrics-2 :: h", "metric": "CREQueueSize", "element": None,
             "max_value": None, "avg_value": None, "samples": 4},
            {"source": "Health Metrics-2 :: h", "metric": "SystemCPU", "element": None,
             "max_value": 3, "avg_value": 2, "samples": 4},
        ]
        _outcome, folded = await collector(provider)._collect_metrics(START, END, report())
        assert folded is not None
        assert [(b.metric, b.max_value) for b in folded.buckets] == [("SystemCPU", 3.0)]


@pytest.mark.asyncio
class TestNotificationsPart:
    async def test_a_whole_answer_is_ok(self) -> None:
        provider = MockQRadarProvider(seed=1)
        rep = report()
        outcome, folded = await collector(provider)._collect_notifications(START, END, rep)
        assert outcome.state == "OK"
        assert folded is not None and len(folded.entries) == 2
        assert rep.searches_sent == 1

    async def test_it_runs_even_when_the_metric_properties_are_missing(self) -> None:
        provider = WithoutProperties({"Metric ID"})
        c = collector(provider)
        metrics, _ = await c._collect_metrics(START, END, report())
        notices, folded = await c._collect_notifications(START, END, report())
        assert metrics.state == "NOT_COLLECTED"
        assert notices.state == "OK" and folded is not None and folded.entries
        assert [s.kind for s in provider.health_searches] == ["notifications"]

    async def test_a_failed_search_is_failed(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_fault = lambda s: ProviderError("x") if s.kind == "notifications" else None
        outcome, folded = await collector(provider)._collect_notifications(START, END, report())
        assert outcome.state == "FAILED" and folded is None

    async def test_a_truncated_answer_is_partial(self) -> None:
        provider = MockQRadarProvider(seed=1)
        provider.health_force_truncated = lambda s: s.kind == "notifications"
        outcome, folded = await collector(provider)._collect_notifications(START, END, report())
        assert outcome.state == "PARTIAL" and folded is not None


class TestRunShape:
    def test_the_window_is_the_last_whole_cadence_before_the_slot(self) -> None:
        slot = datetime(2026, 11, 3, 12, 15, tzinfo=UTC)
        assert window_for(slot, 900) == (datetime(2026, 11, 3, 12, 0, tzinfo=UTC), slot)

    @pytest.mark.asyncio
    async def test_a_provider_that_cannot_run_ariel_is_unsupported_and_sends_nothing(self) -> None:
        mcp = Mock(spec=QRadarMCPProvider)
        mcp.supports.return_value = False
        c = QRadarHealthCollector(cast(Any, None), mcp, settings=Settings(encryption_key=KEY))
        instance = Mock(id="00000000-0000-0000-0000-000000000001")
        out = await c.collect(instance)
        assert out.unsupported and out.as_dict()["status"] == "unsupported"
        mcp.get_health_metrics.assert_not_called()

    def test_the_mock_records_the_window_it_was_asked_for(self) -> None:
        assert MockHealthSearch("metrics", START, END).kind == "metrics"
