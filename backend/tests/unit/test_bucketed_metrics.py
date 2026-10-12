"""Pure fold coverage for keyed Phase A metric searches."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.collectors.bucketed_metrics import fold_log_source_metric_buckets
from app.core.config import Settings
from app.providers.base import ProviderError

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
