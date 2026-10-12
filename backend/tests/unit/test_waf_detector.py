"""WAF_REQUEST_RATE_SPIKE guard matrix.

Each guard is exercised passing and failing independently, because the whole
value of a conjunctive detector is that any single guard can veto. A test that
only checks the happy path would pass against a detector with every guard
deleted.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.enums import (
    BucketCompleteness,
    DetectorSignal,
    RobustScoreStatus,
    Severity,
    WafAnomalyType,
)
from app.waf.detector import (
    WafBaselineCell,
    WafDetectionContext,
    WafPoint,
    WafThresholds,
    detect_request_rate_spike,
)

START = datetime(2026, 8, 3, 19, 25, tzinfo=UTC)
END = START + timedelta(seconds=300)


def point(
    *,
    requests: int = 500,
    completeness: BucketCompleteness = BucketCompleteness.COMPLETE,
    coverage: float = 1.0,
    clients: int | None = 30,
) -> WafPoint:
    return WafPoint(
        bucket_start=START,
        bucket_end=END,
        completeness=completeness,
        request_count=requests,
        endpoint_coverage_ratio=coverage,
        distinct_client_count=clients,
    )


def cell(
    *,
    median: float = 100.0,
    mad: float = 10.0,
    reliable: bool = True,
    samples: int = 20,
    p05: float | None = 80.0,
    p95: float | None = 130.0,
) -> WafBaselineCell:
    return WafBaselineCell(
        median=median,
        mad=mad,
        p05=p05,
        p95=p95,
        sample_count=samples,
        is_reliable=reliable,
        completeness=1.0,
    )


def ctx(**overrides) -> WafDetectionContext:
    base = {
        "point": point(),
        "baseline": cell(),
        "thresholds": WafThresholds(),
        "mad_floor_ratio": 0.1,
        "min_coverage": 0.9,
    }
    base.update(overrides)
    return WafDetectionContext(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(lambda: ctx(point=point(requests=900)), id="anomalous"),
        pytest.param(lambda: ctx(point=point(requests=110)), id="healthy"),
        pytest.param(
            lambda: ctx(point=point(completeness=BucketCompleteness.PARTIAL)),
            id="insufficient",
        ),
    ],
)
def test_evidence_carries_the_waf_detector_type(context) -> None:
    """`waf_anomaly.details` persists `evidence.to_dict()`.

    It used to record VOLUME_SPIKE there, because the evidence object could only
    hold a Phase A type. Migration 0006 corrects rows written that way.
    """
    evidence = detect_request_rate_spike(context()).evidence
    assert evidence.anomaly_type is WafAnomalyType.WAF_REQUEST_RATE_SPIKE
    assert evidence.to_dict()["anomaly_type"] == "WAF_REQUEST_RATE_SPIKE"


class TestFiresWhenEveryGuardPasses:
    def test_a_material_spike_fires(self) -> None:
        v = detect_request_rate_spike(ctx())
        assert v.is_anomalous
        assert v.detector_type is WafAnomalyType.WAF_REQUEST_RATE_SPIKE
        assert v.evidence.observed_value == 500.0
        assert v.evidence.expected_value == 100.0
        assert v.evidence.extra["ratio"] == pytest.approx(5.0)
        assert v.evidence.extra["absolute_delta_requests"] == pytest.approx(400.0)
        assert v.evidence.extra["robust_score_status"] == RobustScoreStatus.OK.value

    def test_a_large_spike_is_high_severity(self) -> None:
        v = detect_request_rate_spike(ctx())
        assert v.evidence.severity is Severity.HIGH

    def test_the_reason_states_observed_expected_and_basis(self) -> None:
        reason = detect_request_rate_spike(ctx()).evidence.reason
        assert "500" in reason and "100" in reason
        assert "robust z" in reason
        # Section 8.3: the engine reports what changed, never why.
        for forbidden in ("caused by", "root cause", "this is an attack"):
            assert forbidden not in reason.lower()


class TestBucketAndCoverageGuards:
    @pytest.mark.parametrize(
        "completeness", [BucketCompleteness.PARTIAL, BucketCompleteness.MISSING]
    )
    def test_an_incomplete_bucket_yields_insufficient_data(
        self, completeness: BucketCompleteness
    ) -> None:
        """A partially collected interval looks exactly like a change in volume."""
        v = detect_request_rate_spike(ctx(point=point(completeness=completeness)))
        assert v.signal is DetectorSignal.UNKNOWN
        assert v.evidence.insufficient_data is True

    def test_low_endpoint_coverage_yields_insufficient_data_not_healthy(self) -> None:
        """The finding that blocks a verdict must not read as "endpoint is fine"."""
        v = detect_request_rate_spike(ctx(point=point(coverage=0.5)))
        assert v.signal is DetectorSignal.UNKNOWN
        assert v.evidence.insufficient_data is True
        assert "parseable endpoint" in v.evidence.reason

    def test_coverage_exactly_at_the_floor_is_allowed(self) -> None:
        v = detect_request_rate_spike(ctx(point=point(coverage=0.9)))
        assert v.is_anomalous


class TestBaselineGuards:
    def test_no_baseline_yields_insufficient_data(self) -> None:
        v = detect_request_rate_spike(ctx(baseline=None))
        assert v.evidence.insufficient_data is True

    def test_an_unreliable_baseline_yields_insufficient_data(self) -> None:
        """Never a misleading NORMAL: "cannot say" and "is fine" differ."""
        v = detect_request_rate_spike(
            ctx(baseline=cell(reliable=False, samples=3))
        )
        assert v.signal is DetectorSignal.UNKNOWN
        assert v.evidence.insufficient_data is True
        assert v.evidence.sample_count == 3


class TestVolumeRatioAndDeltaGuards:
    def test_below_the_minimum_volume_is_healthy_not_anomalous(self) -> None:
        """The endpoint-scale version of the 0.2-to-0.4-EPS false positive."""
        v = detect_request_rate_spike(
            ctx(point=point(requests=8), baseline=cell(median=2.0, mad=0.5))
        )
        assert v.signal is DetectorSignal.HEALTHY
        assert "below the" in v.evidence.reason

    def test_ratio_below_the_threshold_does_not_fire(self) -> None:
        v = detect_request_rate_spike(
            ctx(point=point(requests=150), baseline=cell(median=100.0, mad=1.0))
        )
        assert v.signal is DetectorSignal.HEALTHY
        assert "ratio" in v.evidence.reason

    def test_absolute_delta_below_the_threshold_does_not_fire(self) -> None:
        """A clean 3x ratio on a change of no operational significance."""
        v = detect_request_rate_spike(
            ctx(
                point=point(requests=60),
                baseline=cell(median=20.0, mad=1.0, p05=18.0, p95=22.0),
                thresholds=WafThresholds(min_bucket_requests=20.0),
            )
        )
        assert v.signal is DetectorSignal.HEALTHY
        assert "absolute delta" in v.evidence.reason

    def test_a_spike_from_a_zero_baseline_has_no_ratio_but_still_fires(self) -> None:
        """No ratio exists; the absolute-delta guard carries the verdict alone."""
        v = detect_request_rate_spike(
            ctx(point=point(requests=500), baseline=cell(median=0.0, mad=0.0,
                                                        p05=0.0, p95=0.0))
        )
        assert v.is_anomalous
        assert v.evidence.extra["ratio"] is None
        assert v.evidence.extra["ratio_basis"] == "expected_zero"


class TestDegenerateMadFallback:
    """MAD = 0 whenever more than half the samples are identical.

    Very common for a steady endpoint. The score is then an artefact of the
    floored scale, so it must not be trusted -- in either direction. Treating it
    as evidence of normality is the dangerous error, because it lets a
    rock-steady baseline silently suppress a real, material deviation.
    """

    def test_outside_the_expected_band_still_fires(self) -> None:
        v = detect_request_rate_spike(
            ctx(baseline=cell(mad=0.0, median=100.0, p05=95.0, p95=105.0))
        )
        assert v.is_anomalous
        assert (
            v.evidence.extra["robust_score_status"]
            == RobustScoreStatus.DEGENERATE.value
        )
        assert "MAD=0" in v.evidence.reason

    def test_inside_the_expected_band_does_not_fire(self) -> None:
        v = detect_request_rate_spike(
            ctx(
                point=point(requests=300),
                baseline=cell(mad=0.0, median=100.0, p05=10.0, p95=400.0),
            )
        )
        assert v.signal is DetectorSignal.HEALTHY

    def test_confidence_is_capped_when_the_score_is_degenerate(self) -> None:
        """The number an operator sees must say the evidence is weaker."""
        v = detect_request_rate_spike(
            ctx(baseline=cell(mad=0.0, median=100.0, p05=95.0, p95=105.0))
        )
        assert v.evidence.confidence <= 0.7

    def test_a_missing_percentile_falls_back_to_a_band_not_the_median(self) -> None:
        """Falling back to the median would make any difference "outside"."""
        v = detect_request_rate_spike(
            ctx(
                point=point(requests=101),
                baseline=cell(mad=0.0, median=100.0, p05=None, p95=None),
                thresholds=WafThresholds(
                    min_bucket_requests=1.0,
                    spike_ratio=1.001,
                    min_absolute_delta_requests=0.0,
                ),
            )
        )
        # 101 is inside median +/- 10% (90..110), so it must not fire.
        assert v.signal is DetectorSignal.HEALTHY


class TestNoDropDetectorExists:
    def test_a_collapse_in_requests_is_not_reported_as_an_anomaly(self) -> None:
        """Endpoint silence is normal; Phase A NO_EVENTS covers source silence.

        Asserted rather than assumed, so adding a drop detector later has to be
        a deliberate decision that updates this test.
        """
        v = detect_request_rate_spike(
            ctx(point=point(requests=0), baseline=cell(median=500.0, mad=20.0))
        )
        assert not v.is_anomalous
