"""Pure WAF history fold: oldest-first admission, retirement, and honest rows."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.history.waf import HistoryEndpoint, HistoryVolumeBucket, fold_volume_history
from app.models.enums import DetectorSignal, WafMeasurement, WafTelemetryStatus
from app.providers.dto import WafObservationBatch, WafRequestGroup
from app.waf.admission import AdmissionPolicy
from app.waf.behavior import NoveltyFacts, detect_new_endpoint_access
from app.waf.inputs import HistoryFacts

T0 = datetime(2026, 10, 1, tzinfo=UTC)
STEP = timedelta(minutes=5)
INSTANCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000001")
SOURCE_ID = uuid.UUID("00000000-0000-0000-0000-000000000002")
OLD_ID = uuid.UUID("00000000-0000-0000-0000-000000000003")
NEW_ID = uuid.UUID("00000000-0000-0000-0000-000000000004")


def volume(at: datetime, endpoint: str, requests: int) -> HistoryVolumeBucket:
    return HistoryVolumeBucket(
        WafObservationBatch(
            qradar_log_source_id=7,
            window_start=at,
            window_end=at + STEP,
            groups=[
                WafRequestGroup(
                    raw_url=endpoint,
                    raw_method="GET",
                    request_count=requests,
                    record_count=requests,
                )
            ],
            total_events=requests,
            query="STORED TEMPLATE",
            total_query="STORED TOTAL TEMPLATE",
        ),
        collected_at=T0 + timedelta(days=1),
    )


def old_endpoint() -> HistoryEndpoint:
    return HistoryEndpoint(
        id=OLD_ID,
        instance_id=INSTANCE_ID,
        log_source_id=SOURCE_ID,
        virtual_host=None,
        endpoint="/old",
        method="GET",
        first_seen_at=T0 - timedelta(days=10),
        last_seen_at=T0 - timedelta(days=8),
        active=True,
        retired_at=None,
        tracked_since=T0 - timedelta(days=10),
        tracked_until=None,
        baseline_rebuilt_at=None,
    )


def test_history_fold_retires_per_bucket_then_admits_in_the_next_bucket() -> None:
    folded = fold_volume_history(
        [volume(T0, "/new", 20), volume(T0 + STEP, "/new", 20)],
        [old_endpoint()],
        instance_id=INSTANCE_ID,
        log_source_id=SOURCE_ID,
        profile_version=9,
        metric_semantics="test-semantics",
        collection_source="ariel_history",
        floor=T0,
        policy=AdmissionPolicy(max_new_per_bucket=1, max_active=1, min_requests=3),
        min_coverage=0.9,
        active_days=7,
        maintenance=lambda _: False,
        id_factory=lambda: NEW_ID,
    )

    assert folded.endpoints_created == 1
    assert folded.endpoints_retired == 1
    assert folded.untracked_endpoints == 1
    assert [row["rejected_over_quota"] for row in folded.buckets] == [1, 0]
    assert [row["admission_truncated"] for row in folded.buckets] == [True, False]
    assert len(folded.metrics) == 1
    assert folded.metrics[0]["bucket_start"] == T0 + STEP
    assert folded.buckets[0]["origin"] == "HISTORY"
    assert folded.buckets[0]["profile_version"] == 9
    assert folded.buckets[0]["action_measurement"] == WafTelemetryStatus.NOT_COLLECTED
    assert folded.buckets[0]["query_provenance"]["history"] is True
    assert folded.buckets[0]["query_provenance"]["floor"] == T0.isoformat()


def test_history_fold_marks_low_coverage_as_a_lower_bound() -> None:
    bucket = volume(T0, "/partial", 10)
    incomplete = HistoryVolumeBucket(
        WafObservationBatch(
            qradar_log_source_id=7,
            window_start=T0,
            window_end=T0 + STEP,
            groups=bucket.batch.groups,
            total_events=100,
            query="STORED TEMPLATE",
            total_query="STORED TOTAL TEMPLATE",
        ),
        collected_at=T0 + timedelta(days=1),
    )
    folded = fold_volume_history(
        [incomplete],
        [],
        instance_id=INSTANCE_ID,
        log_source_id=SOURCE_ID,
        profile_version=1,
        metric_semantics="test",
        collection_source="ariel_history",
        floor=T0,
        policy=AdmissionPolicy(1, 10, 3),
        min_coverage=0.9,
        active_days=7,
        maintenance=lambda _: False,
        id_factory=lambda: NEW_ID,
    )

    assert folded.buckets[0]["endpoint_coverage_ratio"] == 0.1
    assert folded.buckets[0]["request_count_measurement"] == WafMeasurement.LOWER_BOUND.value
    assert folded.buckets[0]["completeness"] == "PARTIAL"


def test_history_fold_refuses_a_non_chronological_span() -> None:
    with pytest.raises(ValueError, match="oldest first"):
        fold_volume_history(
            [volume(T0 + STEP, "/new", 20), volume(T0, "/new", 20)],
            [],
            instance_id=INSTANCE_ID,
            log_source_id=SOURCE_ID,
            profile_version=1,
            metric_semantics="test",
            collection_source="ariel_history",
            floor=T0,
            policy=AdmissionPolicy(1, 10, 3),
            min_coverage=0.9,
            active_days=7,
            maintenance=lambda _: False,
            id_factory=lambda: NEW_ID,
        )


def test_pre_hand_off_endpoint_is_established_with_both_real_times() -> None:
    first_seen = T0 - STEP
    hand_off = T0
    from tests.unit.test_waf_behavior import new_ctx

    novelty = NoveltyFacts(
        first_seen_at=first_seen,
        window_end=T0 + timedelta(hours=1),
        within_window=False,
        history=HistoryFacts(False, "established at hand-off"),
        history_start=first_seen - timedelta(days=7),
        history_end=hand_off,
        history_buckets=0,
        appearance_flood=None,
        requests_since_first_seen=0,
        buckets_with_traffic=0,
        lower_bound=False,
    )
    verdict = detect_new_endpoint_access(new_ctx(novelty))

    assert verdict.signal is DetectorSignal.HEALTHY
    assert first_seen.isoformat() in verdict.evidence.reason
    assert hand_off.isoformat() in verdict.evidence.reason


def test_post_hand_off_first_appearance_can_still_be_new() -> None:
    from tests.unit.test_waf_behavior import new_ctx, novelty

    first_seen = T0 + STEP
    verdict = detect_new_endpoint_access(
        new_ctx(
            novelty(
                first_seen_at=first_seen,
                history_end=first_seen,
                history_start=first_seen - timedelta(days=7),
            )
        )
    )

    assert verdict.signal is DetectorSignal.ANOMALOUS
    assert first_seen.isoformat() in verdict.evidence.reason
