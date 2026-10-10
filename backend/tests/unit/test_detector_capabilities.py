"""Detector capability registry: what each detector needs, and what supplies it.

These pin the operational truth the engine enforces and the API publishes. A
detector must not run on a bucket that never measured its input: the REST
provider's Ariel aggregate leaves `unknown_event_count` at its DTO default of 0,
and judging that zero reports a healthy unknown-event ratio nobody measured.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from app.anomaly.capabilities import (
    ARIEL_AGGREGATE_TELEMETRY,
    LOG_SOURCE_DETECTORS,
    SYNTHETIC_TELEMETRY,
    WAF_DETECTORS,
    DetectorSpec,
    DetectorStatus,
    all_detectors,
    bucket_telemetry,
    detector_status,
    unmet_requirement,
)
from app.anomaly.detectors import (
    DetectionContext,
    MetricPoint,
    detect_collection_error,
    detect_timestamp_delay,
    detect_unknown_event_spike,
)
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.thresholds import Thresholds
from app.models.enums import (
    AnomalyType,
    BucketCompleteness,
    DetectorSignal,
    WafAnomalyType,
)
from app.providers.base import LogSourceTelemetry as T
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider

EXPECTED_STATUS = {
    "NO_EVENTS": DetectorStatus.OPERATIONAL,
    "VOLUME_DROP": DetectorStatus.OPERATIONAL,
    "VOLUME_SPIKE": DetectorStatus.OPERATIONAL,
    "PARSING_DEGRADATION": DetectorStatus.SYNTHETIC_ONLY,
    "UNKNOWN_EVENT_SPIKE": DetectorStatus.SYNTHETIC_ONLY,
    "TIMESTAMP_DELAY": DetectorStatus.SYNTHETIC_ONLY,
    "REPEATED_PAYLOAD": DetectorStatus.SYNTHETIC_ONLY,
    "CARDINALITY_DROP": DetectorStatus.UNSUPPORTED,
    "COLLECTION_ERROR": DetectorStatus.UNSUPPORTED,
    "WAF_REQUEST_RATE_SPIKE": DetectorStatus.OPERATIONAL,
}


def test_provider_declarations_match_the_registry() -> None:
    """The legacy-row fallback duplicates the provider declarations; keep them equal."""
    assert QRadarRestProvider.log_source_telemetry == ARIEL_AGGREGATE_TELEMETRY
    assert MockQRadarProvider.log_source_telemetry == SYNTHETIC_TELEMETRY


def test_rest_provider_declares_the_aggregation_it_runs() -> None:
    assert QRadarRestProvider.event_count_aggregation == "SUM(eventcount)"


def test_every_named_detector_has_a_spec() -> None:
    assert set(LOG_SOURCE_DETECTORS) == set(AnomalyType)
    assert set(WAF_DETECTORS) == set(WafAnomalyType)


@pytest.mark.parametrize("spec", all_detectors(), ids=lambda s: s.detector_type)
def test_status_matrix(spec: DetectorSpec) -> None:
    assert detector_status(spec) is EXPECTED_STATUS[spec.detector_type]


def test_every_non_operational_detector_states_its_limitation() -> None:
    for spec in all_detectors():
        operational = detector_status(spec) is DetectorStatus.OPERATIONAL
        assert (spec.limitation is None) == operational, spec.detector_type


def test_scheduled_tasks_exist_in_the_beat_schedule() -> None:
    from app.workers.celery_app import celery_app

    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    for spec in all_detectors():
        if spec.scheduled_task is not None:
            assert spec.scheduled_task in scheduled, spec.detector_type


class TestBucketTelemetry:
    def test_declared_provenance_wins_over_the_collection_path(self) -> None:
        assert bucket_telemetry("mock", {"telemetry": ["EVENT_VOLUME"]}) == {T.EVENT_VOLUME}

    def test_unrecognized_declared_values_are_ignored(self) -> None:
        declared = {"telemetry": ["EVENT_VOLUME", "NOT_A_QUANTITY"]}
        assert bucket_telemetry("ariel_aggregate", declared) == {T.EVENT_VOLUME}

    def test_volume_is_always_credited(self) -> None:
        assert bucket_telemetry(None, {"telemetry": []}) == {T.EVENT_VOLUME}

    def test_legacy_rows_fall_back_to_what_their_path_measures(self) -> None:
        assert bucket_telemetry("ariel_aggregate", {}) == ARIEL_AGGREGATE_TELEMETRY
        assert bucket_telemetry("mock", None) == SYNTHETIC_TELEMETRY

    @pytest.mark.parametrize("source", [None, "", "unknown_path"])
    def test_an_unknown_path_is_credited_with_volume_alone(self, source: str | None) -> None:
        assert bucket_telemetry(source, {}) == {T.EVENT_VOLUME}


class TestUnmetRequirement:
    def test_a_live_ariel_bucket_runs_only_the_volume_detectors(self) -> None:
        runnable = {
            t for t in AnomalyType if unmet_requirement(t, ARIEL_AGGREGATE_TELEMETRY) is None
        }
        assert runnable == {
            AnomalyType.NO_EVENTS,
            AnomalyType.VOLUME_DROP,
            AnomalyType.VOLUME_SPIKE,
        }

    def test_a_synthetic_bucket_still_cannot_run_the_unsupported_detectors(self) -> None:
        blocked = {
            t for t in AnomalyType if unmet_requirement(t, SYNTHETIC_TELEMETRY) is not None
        }
        assert blocked == {AnomalyType.CARDINALITY_DROP, AnomalyType.COLLECTION_ERROR}

    def test_a_missing_baseline_is_named_before_missing_telemetry(self) -> None:
        reason = unmet_requirement(AnomalyType.CARDINALITY_DROP, SYNTHETIC_TELEMETRY)
        assert reason is not None
        assert "distinct_source_ip_count baseline" in reason

    def test_the_reason_names_the_missing_input(self) -> None:
        reason = unmet_requirement(AnomalyType.UNKNOWN_EVENT_SPIKE, ARIEL_AGGREGATE_TELEMETRY)
        assert reason is not None
        assert "UNKNOWN_EVENT_COUNT" in reason


def _rest_shaped_point() -> MetricPoint:
    """A bucket exactly as the REST provider fills it: volume and last event
    time measured, everything else left at its DTO default."""
    start = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)
    return MetricPoint(
        interval_start=start,
        interval_end=start + timedelta(minutes=5),
        completeness=BucketCompleteness.COMPLETE,
        event_count=7570,
        average_eps=7570 / 300,
        peak_eps=0.0,
        last_event_at=start + timedelta(minutes=4),
        event_delay_seconds=60.0,
        unknown_event_count=0,
        stored_event_count=7570,
        parsed_username_ratio=None,
        parsed_source_ip_ratio=None,
        distinct_qid_count=0,
        distinct_username_count=0,
        distinct_source_ip_count=0,
        collection_error_count=0,
        payload_signature=None,
    )


@pytest.mark.parametrize(
    ("detector", "anomaly_type"),
    [
        (detect_unknown_event_spike, AnomalyType.UNKNOWN_EVENT_SPIKE),
        (detect_timestamp_delay, AnomalyType.TIMESTAMP_DELAY),
        (detect_collection_error, AnomalyType.COLLECTION_ERROR),
    ],
)
def test_unmeasured_defaults_would_otherwise_be_judged_healthy(
    detector: Callable[[DetectionContext], AnomalyEvidence], anomaly_type: AnomalyType
) -> None:
    """The hazard the gate exists for, shown on the real detectors.

    Each of these returns HEALTHY on a live-shaped bucket, which would count
    towards recovery and render as a clean verdict -- on inputs that were never
    measured. The registry refuses to run them there.
    """
    ctx = DetectionContext(
        point=_rest_shaped_point(),
        baselines={},
        thresholds=Thresholds(),
        expected_interval_seconds=300.0,
    )
    assert detector(ctx).signal is DetectorSignal.HEALTHY
    assert unmet_requirement(anomaly_type, ARIEL_AGGREGATE_TELEMETRY) is not None
