"""Property-watch engine wiring and lifecycle rules."""

from __future__ import annotations

import uuid
from dataclasses import fields
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.core.config import Settings
from app.models.enums import (
    DetectorSignal,
    PwAnomalyType,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    Severity,
    WafMeasurement,
)
from app.models.instance import QRadarInstance
from app.models.property_watch import PropertyWatch, PwValue
from app.property_watch.detectors import PwThresholds
from app.property_watch.engine import (
    PwAnomalyEngine,
    PwEvaluationReport,
    _State,
    _Writes,
)
from app.property_watch.facts import PwBucketFact

AT = datetime(2026, 10, 18, 10, tzinfo=UTC)


def settings(**overrides: object) -> Settings:
    values = Settings(_env_file=None).model_dump()
    values.update(overrides)
    return Settings(**values)


def engine(**overrides: object) -> PwAnomalyEngine:
    return PwAnomalyEngine(
        cast(AsyncSession, object()), settings=settings(**overrides), clock=lambda: AT
    )


def test_every_threshold_field_is_wired_from_settings() -> None:
    configured = settings(
        anomaly_deviation_threshold=4.1,
        baseline_mad_floor_ratio=0.12,
        pw_min_coverage=0.81,
        baseline_min_samples=11,
        pw_spike_ratio=2.7,
        pw_min_absolute_delta_events=71,
        pw_min_bucket_events=31,
        pw_drop_min_median=23,
        pw_drop_ratio=0.41,
        pw_disappear_min_median=9,
        pw_disappear_buckets=8,
        pw_value_active_days=13,
        pw_new_value_history_days=9,
        pw_new_value_window_buckets=17,
        pw_new_value_min_events=29,
        pw_new_value_min_buckets=4,
        pw_min_admission_events=6,
    )
    thresholds = PwAnomalyEngine(cast(AsyncSession, object()), settings=configured)._thresholds()
    expected = {
        "deviation_z": configured.anomaly_deviation_threshold,
        "mad_floor_ratio": configured.baseline_mad_floor_ratio,
        "min_coverage": configured.pw_min_coverage,
        "baseline_min_samples": configured.baseline_min_samples,
        "spike_ratio": configured.pw_spike_ratio,
        "min_absolute_delta_events": configured.pw_min_absolute_delta_events,
        "min_bucket_events": configured.pw_min_bucket_events,
        "drop_min_median": configured.pw_drop_min_median,
        "drop_ratio": configured.pw_drop_ratio,
        "disappear_min_median": configured.pw_disappear_min_median,
        "disappear_buckets": configured.pw_disappear_buckets,
        "value_active_days": configured.pw_value_active_days,
        "new_value_history_days": configured.pw_new_value_history_days,
        "new_value_window_buckets": configured.pw_new_value_window_buckets,
        "new_value_min_events": configured.pw_new_value_min_events,
        "new_value_min_buckets": configured.pw_new_value_min_buckets,
        "admission_min_events": configured.pw_min_admission_events,
    }
    assert {field.name for field in fields(PwThresholds)} == set(expected)
    assert {name: getattr(thresholds, name) for name in expected} == expected


def _subjects() -> tuple[QRadarInstance, PropertyWatch, PwValue, PwBucketFact]:
    instance = QRadarInstance(
        id=uuid.uuid4(), name="i", console_host="qradar.example", provider_kind="mock"
    )
    watch = PropertyWatch(
        id=uuid.uuid4(),
        instance_id=instance.id,
        name="w",
        scope_kind="LOG_SOURCE_TYPE",
        scope_type_id=1,
        properties=[{"kind": "NORMALIZED", "name": "username"}],
        pinned_values=[],
        detectors=[PwAnomalyType.PW_VALUE_SPIKE.value],
        severity=Severity.HIGH,
        watch_version=1,
        enabled=True,
    )
    value = PwValue(
        id=uuid.uuid4(),
        watch_id=watch.id,
        watch_version=1,
        value_key="alice",
        component_0="alice",
        pinned=False,
        active=True,
        first_seen_at=AT - timedelta(days=20),
        last_seen_at=AT,
        tracked_since=AT - timedelta(days=20),
    )
    bucket = PwBucketFact(
        bucket_start=AT,
        bucket_seconds=300,
        watch_version=1,
        totals_complete=True,
        values_measurement=PwBucketMeasurement.EXACT,
        pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
        total_events=500,
        parsed_events=500,
    )
    return instance, watch, value, bucket


def _anomalous() -> AnomalyEvidence[PwAnomalyType]:
    return AnomalyEvidence(
        anomaly_type=PwAnomalyType.PW_VALUE_SPIKE,
        signal=DetectorSignal.ANOMALOUS,
        severity=Severity.HIGH,
        observed_value=500,
        expected_value=100,
        baseline_low=80,
        baseline_high=120,
        deviation_score=8,
        threshold=3.5,
        sample_count=10,
        interval_start=AT,
        interval_end=AT + timedelta(minutes=5),
        confidence=0.9,
        reason="spike",
        extra={"observed_measurement": WafMeasurement.EXACT.value},
    )


def test_hysteresis_holds_unknown_and_confirms_then_recovers() -> None:
    instance, watch, value, first = _subjects()
    subject_state = _State(
        uuid.uuid4(), watch.id, value.id, PwAnomalyType.PW_VALUE_SPIKE, 1
    )
    incidents = {}
    writes = _Writes()
    report = PwEvaluationReport(instance.id)
    runner = engine(anomaly_open_after_intervals=2, anomaly_resolve_after_intervals=2)

    runner._advance(
        instance, watch, value, first, subject_state, _anomalous(), incidents, writes, report
    )
    assert subject_state.state.value == "CANDIDATE"
    assert len(incidents) == 1

    unknown = insufficient(
        PwAnomalyType.PW_VALUE_SPIKE,
        interval_start=AT + timedelta(minutes=5),
        interval_end=AT + timedelta(minutes=10),
        reason="unknown count",
    )
    second = PwBucketFact(
        **{**first.__dict__, "bucket_start": AT + timedelta(minutes=5)}
    )
    runner._advance(
        instance, watch, value, second, subject_state, unknown, incidents, writes, report
    )
    assert subject_state.state.value == "CANDIDATE"
    assert subject_state.consecutive_anomalous == 1

    third = PwBucketFact(
        **{**first.__dict__, "bucket_start": AT + timedelta(minutes=10)}
    )
    runner._advance(
        instance, watch, value, third, subject_state, _anomalous(), incidents, writes, report
    )
    assert subject_state.state.value == "OPEN"
    assert report.opened == 1

    normal = healthy(
        PwAnomalyType.PW_VALUE_SPIKE,
        interval_start=AT + timedelta(minutes=15),
        interval_end=AT + timedelta(minutes=20),
    )
    fourth = PwBucketFact(
        **{**first.__dict__, "bucket_start": AT + timedelta(minutes=15)}
    )
    fifth = PwBucketFact(
        **{**first.__dict__, "bucket_start": AT + timedelta(minutes=20)}
    )
    runner._advance(
        instance, watch, value, fourth, subject_state, normal, incidents, writes, report
    )
    assert subject_state.state.value == "RECOVERING"
    runner._advance(
        instance, watch, value, fifth, subject_state, normal, incidents, writes, report
    )
    assert subject_state.state.value == "RESOLVED"
    assert report.resolved == 1
    assert [row["to_state"] for row in writes.transitions] == [
        "CANDIDATE",
        "OPEN",
        "RECOVERING",
        "RESOLVED",
    ]


def _step(base: PwBucketFact, minutes: int) -> PwBucketFact:
    return PwBucketFact(**{**base.__dict__, "bucket_start": AT + timedelta(minutes=minutes)})


def _feed(runner, subjects, evidence, minutes):  # type: ignore[no-untyped-def]
    instance, watch, value, first, state, incidents, writes, report = subjects
    runner._advance(
        instance, watch, value, _step(first, minutes), state, evidence, incidents, writes, report
    )


def _rig(**overrides: object):  # type: ignore[no-untyped-def]
    instance, watch, value, first = _subjects()
    state = _State(uuid.uuid4(), watch.id, value.id, PwAnomalyType.PW_VALUE_SPIKE, 1)
    incidents: dict = {}
    writes = _Writes()
    report = PwEvaluationReport(instance.id)
    runner = engine(**overrides)
    return runner, (instance, watch, value, first, state, incidents, writes, report)


def _unknown(minutes: int) -> AnomalyEvidence[PwAnomalyType]:
    return insufficient(
        PwAnomalyType.PW_VALUE_SPIKE,
        interval_start=AT + timedelta(minutes=minutes),
        interval_end=AT + timedelta(minutes=minutes + 5),
        reason="unknown",
    )


def _healthy(minutes: int) -> AnomalyEvidence[PwAnomalyType]:
    return healthy(
        PwAnomalyType.PW_VALUE_SPIKE,
        interval_start=AT + timedelta(minutes=minutes),
        interval_end=AT + timedelta(minutes=minutes + 5),
    )


def test_an_unjudgeable_bucket_never_makes_a_value_normal() -> None:
    runner, subjects = _rig()
    _feed(runner, subjects, _unknown(0), 0)
    state = subjects[4]
    assert state.state.value == "INSUFFICIENT_DATA" and not subjects[5]
    _feed(runner, subjects, _healthy(5), 5)
    assert state.state.value == "NORMAL"
    _feed(runner, subjects, _unknown(10), 10)
    assert state.state.value == "INSUFFICIENT_DATA"
    assert state.consecutive_healthy == 1  # the unknown neither counted nor reset it


def test_one_abnormal_bucket_that_returns_to_normal_never_opens() -> None:
    runner, subjects = _rig()
    _feed(runner, subjects, _anomalous(), 0)
    _feed(runner, subjects, _healthy(5), 5)
    incidents, writes, report = subjects[5], subjects[6], subjects[7]
    assert subjects[4].state.value == "NORMAL" and report.opened == 0
    [incident] = incidents.values()
    assert incident.row["state"] == "NORMAL" and incident.row["opened_at"] is None
    assert [row["to_state"] for row in writes.transitions] == ["CANDIDATE", "NORMAL"]
    assert subjects[4].active_anomaly_id is None


def test_the_confirmation_count_is_the_setting() -> None:
    runner, subjects = _rig(anomaly_open_after_intervals=1)
    _feed(runner, subjects, _anomalous(), 0)
    assert subjects[4].state.value == "OPEN" and subjects[7].opened == 1
    runner, subjects = _rig(anomaly_open_after_intervals=3)
    for minutes in (0, 5):
        _feed(runner, subjects, _anomalous(), minutes)
    assert subjects[4].state.value == "CANDIDATE"
    _feed(runner, subjects, _anomalous(), 10)
    assert subjects[4].state.value == "OPEN"


def test_the_recovery_count_is_the_setting() -> None:
    runner, subjects = _rig(anomaly_open_after_intervals=1, anomaly_resolve_after_intervals=1)
    _feed(runner, subjects, _anomalous(), 0)
    _feed(runner, subjects, _healthy(5), 5)
    assert subjects[4].state.value == "RESOLVED" and subjects[7].resolved == 1


def test_a_bucket_judged_twice_changes_nothing() -> None:
    runner, subjects = _rig(anomaly_open_after_intervals=3)
    _feed(runner, subjects, _anomalous(), 0)
    _feed(runner, subjects, _anomalous(), 0)
    assert subjects[4].consecutive_anomalous == 1 and len(subjects[6].transitions) == 1


def test_a_relapse_reopens_the_same_incident() -> None:
    runner, subjects = _rig(anomaly_open_after_intervals=1)
    _feed(runner, subjects, _anomalous(), 0)
    _feed(runner, subjects, _healthy(5), 5)
    assert subjects[4].state.value == "RECOVERING"
    _feed(runner, subjects, _anomalous(), 10)
    assert subjects[4].state.value == "OPEN" and len(subjects[5]) == 1
    assert subjects[7].opened == 2


def test_an_incident_of_an_older_version_is_resolved_once_with_its_reason() -> None:
    runner, subjects = _rig(anomaly_open_after_intervals=1)
    _, watch, value, _, state, incidents, writes, report = subjects
    _feed(runner, subjects, _anomalous(), 0)
    watch.watch_version = 2
    states = {(value.id, state.detector): state}
    runner._resolve_old_versions([watch], states, incidents, writes, report)
    [incident] = incidents.values()
    assert incident.row["state"] == "RESOLVED" and state.state.value == "RESOLVED"
    assert writes.transitions[-1]["reason"] == "watch definition changed"
    assert report.version_resolved == 1 and state.active_anomaly_id is None
    state.dirty = False
    states = {(value.id, state.detector): state}
    runner._resolve_old_versions([watch], states, incidents, writes, report)
    assert report.version_resolved == 1 and not state.dirty


def test_the_status_the_detectors_see_is_never_invented() -> None:
    _, watch, _, _ = _subjects()
    assert PwAnomalyEngine._detection_status(watch).value == "OK"  # collection cleared its own
    watch.last_status = "SCOPE_STALE"
    assert PwAnomalyEngine._detection_status(watch).value == "SCOPE_STALE"
    watch.last_status = "SOMETHING_NEW"
    assert PwAnomalyEngine._detection_status(watch).value == "STALE"  # unknown fails closed


def test_a_combination_detector_on_a_one_property_watch_is_not_run() -> None:
    _, watch, _, _ = _subjects()
    watch.detectors = [PwAnomalyType.PW_NEW_COMBINATION.value, PwAnomalyType.PW_VALUE_DROP.value]
    assert PwAnomalyEngine._selected(watch) == (PwAnomalyType.PW_VALUE_DROP,)
    watch.properties = [{"kind": "NORMALIZED", "name": "a"}, {"kind": "NORMALIZED", "name": "b"}]
    assert PwAnomalyType.PW_NEW_COMBINATION in PwAnomalyEngine._selected(watch)
