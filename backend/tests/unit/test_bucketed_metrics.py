"""Pure fold coverage for keyed Phase A metric searches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.collectors.bucketed_metrics import fold_log_source_metric_buckets
from app.collectors.metric_collector import MetricCollector
from app.core.config import Settings
from app.providers.base import ProviderError
from app.providers.dto import LogSourceMetricBucketBatch

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=10)
BUCKET_MS = 300_000


def _key(at: datetime) -> int:
    return int(at.timestamp() * 1000) // BUCKET_MS


def test_fold_maps_keys_and_computes_each_buckets_derived_values() -> None:
    second = START + timedelta(minutes=5)
    rows = [
        {
            "qradar_id": 7,
            "k": _key(START),
            "event_count": 300,
            "last_event_time": int((START + timedelta(minutes=4, seconds=50)).timestamp() * 1000),
        },
        {
            "qradar_id": 7,
            "k": _key(second),
            "event_count": 600,
            "last_event_time": int((second + timedelta(minutes=4, seconds=40)).timestamp() * 1000),
        },
    ]

    buckets = fold_log_source_metric_buckets(
        rows, START, END, bucket_seconds=300
    )

    assert list(buckets) == [START, second]
    first_sample = buckets[START][0]
    second_sample = buckets[second][0]
    assert first_sample.average_eps == pytest.approx(1.0)
    assert first_sample.last_event_at == START + timedelta(minutes=4, seconds=50)
    assert first_sample.event_delay_seconds == pytest.approx(10.0)
    assert second_sample.average_eps == pytest.approx(2.0)
    assert second_sample.last_event_at == second + timedelta(minutes=4, seconds=40)
    assert second_sample.event_delay_seconds == pytest.approx(20.0)


def test_fold_rejects_bucket_key_outside_window() -> None:
    with pytest.raises(ProviderError, match="outside the window"):
        fold_log_source_metric_buckets(
            [{"k": _key(END), "qradar_id": 7, "event_count": 1}],
            START,
            END,
            bucket_seconds=300,
        )


def test_fold_rejects_non_integer_bucket_key() -> None:
    with pytest.raises(ProviderError, match="non-integer"):
        fold_log_source_metric_buckets(
            [{"k": _key(START) + 0.5, "qradar_id": 7, "event_count": 1}],
            START,
            END,
            bucket_seconds=300,
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("ariel_multi_bucket_max_rows", 0),
        ("ariel_multi_bucket_max_rows", 100_001),
        ("search_cadence_max_searches_per_tick", 0),
        ("search_cadence_max_searches_per_tick", 257),
        ("anomaly_max_buckets_per_evaluation", 0),
        ("anomaly_max_buckets_per_evaluation", 10_001),
    ],
)
def test_new_phase_a_limits_reject_values_outside_their_bounds(
    field: str, value: int
) -> None:
    with pytest.raises(ValidationError):
        Settings(encryption_key="x" * 44, **{field: value})


def test_new_phase_a_limits_have_the_binding_defaults() -> None:
    settings = Settings(encryption_key="x" * 44)
    assert settings.ariel_multi_bucket_max_rows == 5000
    assert settings.search_cadence_max_searches_per_tick == 16
    assert settings.anomaly_max_buckets_per_evaluation == 120


class PlannedVolumeProvider:
    def __init__(self, *, fail_at: int | None = None) -> None:
        self.calls: list[tuple[datetime, datetime, dict]] = []
        self.fail_at = fail_at

    async def get_log_source_metrics(self, first, end, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((first, end, kwargs))
        if len(self.calls) == self.fail_at:
            raise ProviderError("scripted failure")
        return []

    async def get_log_source_metrics_bucketed(self, first, end, **kwargs):  # type: ignore[no-untyped-def]
        self.calls.append((first, end, kwargs))
        return LogSourceMetricBucketBatch(buckets={}, rows_returned=0, truncated=False, aql="")


@pytest.mark.parametrize(
    ("sources", "ids", "widths"),
    [(8000, None, [1] * 12), (2000, None, [2] * 6), (900, None, [5, 5, 2]),
     (100, None, [12]), (0, None, [12]), (8000, [1, 2], [12])],
)
async def test_volume_span_is_planned_before_search(sources, ids, widths) -> None:
    provider = PlannedVolumeProvider()
    collector = MetricCollector(
        SimpleNamespace(), provider,  # type: ignore[arg-type]
        settings=Settings(encryption_key="x" * 44), clock=lambda: START,
    )
    found = await collector._search_window(
        START, 12, 300, max_searches=16, source_count=sources, qradar_ids=ids,
    )
    assert [int((end - first).total_seconds()) // 300 for first, end, _ in provider.calls] == widths
    assert len(found.completed) == 12
    assert found.searches == len(widths) and found.splits == 0
    assert not found.capped and found.failure is None
    if ids is not None:
        assert all(kwargs["qradar_ids"] == ids for _, _, kwargs in provider.calls)


@pytest.mark.parametrize("stop", ["budget", "deadline", "failure"])
async def test_planned_volume_span_stops_at_first_incomplete_bucket(stop: str) -> None:
    provider = PlannedVolumeProvider(fail_at=3 if stop == "failure" else None)
    collector = MetricCollector(
        SimpleNamespace(), provider,  # type: ignore[arg-type]
        settings=Settings(encryption_key="x" * 44), clock=lambda: START,
    )
    found = await collector._search_window(
        START, 12, 300, max_searches=2 if stop == "budget" else 16,
        source_count=8000, deadline=START if stop == "deadline" else None,
    )
    assert len(found.completed) == (0 if stop == "deadline" else 2)
    assert found.searches == len(provider.calls) == {
        "deadline": 0, "budget": 2, "failure": 3,
    }[stop]
    assert found.capped is (stop != "failure")
    assert (found.failure is not None) is (stop == "failure")
