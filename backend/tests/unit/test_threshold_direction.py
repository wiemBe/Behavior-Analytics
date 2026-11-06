"""The direction knob's detector exclusion (SOC-17b, design §3.1).

``run_detectors`` is the one place the engine and the replay decide which
detectors judge a bucket, so these cases pin that a spike-only source skips only
the drop detector, a drop-only source skips only the spike detector, and
``both`` (or a malformed stored value) skips nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly.detectors import BaselineCell, DetectionContext, MetricPoint
from app.anomaly.engine import run_detectors
from app.anomaly.thresholds import Thresholds
from app.models.enums import AnomalyType, BucketCompleteness
from app.providers.base import LogSourceTelemetry

START = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
BUCKET = 300
TELEMETRY = frozenset({LogSourceTelemetry.EVENT_VOLUME})


def _ctx(direction: str) -> DetectionContext:
    point = MetricPoint(
        interval_start=START,
        interval_end=START + timedelta(seconds=BUCKET),
        completeness=BucketCompleteness.COMPLETE,
        event_count=int(3.0 * BUCKET),
        average_eps=3.0,
        peak_eps=3.0,
        last_event_at=START,
        event_delay_seconds=None,
        unknown_event_count=0,
        stored_event_count=int(3.0 * BUCKET),
        parsed_username_ratio=None,
        parsed_source_ip_ratio=None,
        distinct_qid_count=0,
        distinct_username_count=0,
        distinct_source_ip_count=0,
        collection_error_count=0,
        payload_signature=None,
    )
    cells = {
        "average_eps": BaselineCell(
            median=50.0, mad=2.0, p05=45.0, p95=55.0,
            sample_count=30, is_reliable=True, completeness=1.0, baseline_version=1,
        ),
        "event_count": BaselineCell(
            median=15000.0, mad=500.0, p05=14000.0, p95=16000.0,
            sample_count=30, is_reliable=True, completeness=1.0, baseline_version=1,
        ),
    }
    return DetectionContext(
        point=point,
        baselines=cells,
        thresholds=Thresholds(direction=direction),
        expected_interval_seconds=float(BUCKET),
    )


def _types(results) -> set[AnomalyType]:  # type: ignore[no-untyped-def]
    return {evidence.anomaly_type for evidence in results}


def test_both_runs_both_volume_directions() -> None:
    results, unsupported = run_detectors(_ctx("both"), TELEMETRY)
    assert {AnomalyType.VOLUME_SPIKE, AnomalyType.VOLUME_DROP} <= _types(results)
    assert "VOLUME_SPIKE" not in unsupported and "VOLUME_DROP" not in unsupported


def test_spike_only_skips_the_drop_detector_and_nothing_else() -> None:
    results, unsupported = run_detectors(_ctx("spike"), TELEMETRY)
    assert AnomalyType.VOLUME_DROP not in _types(results)
    assert AnomalyType.VOLUME_SPIKE in _types(results)
    assert unsupported["VOLUME_DROP"] == "direction is spike-only"
    assert "VOLUME_SPIKE" not in unsupported


def test_drop_only_skips_the_spike_detector_and_nothing_else() -> None:
    results, unsupported = run_detectors(_ctx("drop"), TELEMETRY)
    assert AnomalyType.VOLUME_SPIKE not in _types(results)
    assert AnomalyType.VOLUME_DROP in _types(results)
    assert unsupported["VOLUME_SPIKE"] == "direction is drop-only"
    assert "VOLUME_DROP" not in unsupported


@pytest.mark.parametrize("direction", ["sideways", "", None, 1])
def test_a_malformed_stored_direction_filters_nothing(direction: object) -> None:
    """A row written before validation existed keeps today's behaviour."""
    results, unsupported = run_detectors(_ctx(direction), TELEMETRY)  # type: ignore[arg-type]
    assert {AnomalyType.VOLUME_SPIKE, AnomalyType.VOLUME_DROP} <= _types(results)
    assert "VOLUME_SPIKE" not in unsupported and "VOLUME_DROP" not in unsupported
