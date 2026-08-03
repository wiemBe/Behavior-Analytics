"""WAF_REQUEST_RATE_SPIKE — the Phase B MVP detector.

A pure function over a context, exactly like the Phase A detectors: no I/O and
no ORM, so every guard is unit-testable against exact inputs.

The guard structure is deliberately identical to Phase A's `_volume`, including
the degenerate-MAD fallback, and reuses `app.anomaly.statistics` and
`app.anomaly.evidence` unchanged. Phase A's mathematics is not touched, and the
thresholds are Phase B's own settings so tuning one cannot move the other.

Only the spike direction is implemented. Drop is deliberately absent: an
endpoint receiving no requests is normal, Phase A's NO_EVENTS already detects
whole-source silence, and raising a second alert for the same physical condition
would be noise. See docs/PHASE-B-WAF-BEHAVIOR.md sections 10.4 and 11.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.anomaly import statistics as rstats
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.models.enums import (
    AnomalyType,
    BucketCompleteness,
    DetectorSignal,
    RobustScoreStatus,
    Severity,
    WafAnomalyType,
)

#: The `AnomalyEvidence` value object is typed on the Phase A `AnomalyType`.
#: Rather than widen that enum -- which is persisted in Phase A rows and alert
#: fingerprints -- Phase B borrows VOLUME_SPIKE as the evidence-level shape and
#: carries its real detector type separately, in `WafSpikeVerdict.detector_type`.
#: Nothing persists the borrowed value: `waf_anomaly.detector_type` is written
#: from the WafAnomalyType below.
_EVIDENCE_TYPE = AnomalyType.VOLUME_SPIKE


@dataclass(frozen=True)
class WafThresholds:
    """Phase B guards. Separate from Phase A's so neither can move the other."""

    deviation_z: float = 3.5
    spike_ratio: float = 2.0
    min_absolute_delta_requests: float = 50.0
    min_bucket_requests: float = 20.0
    open_after: int = 2
    resolve_after: int = 3


@dataclass
class WafPoint:
    """One endpoint's observation of one bucket."""

    bucket_start: datetime
    bucket_end: datetime
    completeness: BucketCompleteness
    request_count: int
    endpoint_coverage_ratio: float
    distinct_client_count: int | None = None


@dataclass
class WafBaselineCell:
    median: float
    mad: float
    p05: float | None
    p95: float | None
    sample_count: int
    is_reliable: bool
    completeness: float = 0.0
    baseline_version: int = 1


@dataclass
class WafDetectionContext:
    point: WafPoint
    baseline: WafBaselineCell | None
    thresholds: WafThresholds
    mad_floor_ratio: float = 0.1
    #: Below this the bucket is not a whole observation of the endpoint even if
    #: the collector marked it COMPLETE. Carried here so the detector states its
    #: own precondition rather than trusting an upstream flag.
    min_coverage: float = 0.9
    extra: dict = field(default_factory=dict)


@dataclass
class WafSpikeVerdict:
    detector_type: WafAnomalyType
    evidence: AnomalyEvidence

    @property
    def signal(self) -> DetectorSignal:
        return self.evidence.signal

    @property
    def is_anomalous(self) -> bool:
        return self.evidence.is_anomalous


def _expected_band(cell: WafBaselineCell, mad_floor_ratio: float) -> tuple[float, float]:
    """Displayed expected range, with a safe fallback when percentiles are absent.

    Falling back to the median itself would make any nonzero difference "outside
    the band", so the fallback is a symmetric spread around it.
    """
    spread = max(abs(cell.median) * mad_floor_ratio, 1e-9)
    lower = cell.p05 if cell.p05 is not None else cell.median - spread
    upper = cell.p95 if cell.p95 is not None else cell.median + spread
    return lower, upper


def detect_request_rate_spike(ctx: WafDetectionContext) -> WafSpikeVerdict:
    """Conjunctive spike verdict. Every guard must pass for the detector to fire.

        complete bucket
        AND adequate endpoint coverage
        AND adequate baseline
        AND observed volume above the endpoint minimum
        AND ratio guard satisfied
        AND absolute-delta guard satisfied
        AND ( robust z-score threshold satisfied
              OR robust score is degenerate (MAD = 0) and the deterministic
                 expected-bound fallback is satisfied )

    Guards are conjunctive on purpose: adding one can only make the detector
    more conservative. The ratio and absolute-delta pair specifically kills the
    endpoint-scale version of Phase A's classic false positive, where an
    endpoint serving 2 requests per bucket serves 4 and produces a clean 2x
    ratio on a change of no operational significance.
    """
    p = ctx.point
    thr = ctx.thresholds
    at = _EVIDENCE_TYPE
    dt = WafAnomalyType.WAF_REQUEST_RATE_SPIKE

    def verdict(ev: AnomalyEvidence) -> WafSpikeVerdict:
        return WafSpikeVerdict(detector_type=dt, evidence=ev)

    # --- guard 1: the bucket must be a real, whole observation --------------
    if p.completeness is not BucketCompleteness.COMPLETE:
        return verdict(
            insufficient(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                reason=(
                    f"bucket is {p.completeness.value.lower()}, not a whole observation"
                ),
            )
        )

    # --- guard 2: enough of the source's traffic carried an endpoint --------
    # A bucket where a third of the requests had no parseable path is not a
    # whole observation of any endpoint inside it, however complete the
    # collection was.
    if p.endpoint_coverage_ratio < ctx.min_coverage:
        return verdict(
            insufficient(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                reason=(
                    f"only {p.endpoint_coverage_ratio:.0%} of this source's events "
                    "carried a parseable endpoint in this bucket"
                ),
            )
        )

    # --- guard 3: adequate baseline ----------------------------------------
    cell = ctx.baseline
    if cell is None:
        return verdict(
            insufficient(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                reason="no request-rate baseline for this weekday/hour cell yet",
            )
        )
    if not cell.is_reliable:
        return verdict(
            insufficient(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                reason=(
                    f"baseline has {cell.sample_count} sample(s), below the minimum "
                    "required for a defensible verdict"
                ),
                sample_count=cell.sample_count,
            )
        )

    observed = float(p.request_count)
    expected = cell.median
    delta = observed - expected

    facts: dict[str, object] = {
        "observed_requests": round(observed, 1),
        "expected_requests": round(expected, 1),
        "absolute_delta_requests": round(delta, 1),
        "endpoint_coverage_ratio": round(p.endpoint_coverage_ratio, 4),
        "baseline_completeness": round(cell.completeness, 3),
        "baseline_version": cell.baseline_version,
        "distinct_client_count": p.distinct_client_count,
        **ctx.extra,
    }

    # --- guard 4: minimum volume -------------------------------------------
    if observed < thr.min_bucket_requests:
        return verdict(
            healthy(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                observed=observed, expected=round(expected, 3),
                reason=(
                    f"{observed:.0f} requests/bucket is below the "
                    f"{thr.min_bucket_requests:.0f} minimum for a defensible verdict"
                ),
            )
        )

    # --- guard 5: ratio -----------------------------------------------------
    ratio = rstats.ratio(observed, expected)
    facts["ratio"] = round(ratio, 4) if ratio is not None else None
    if ratio is None:
        # Expected is zero. A spike from nothing is real but has no ratio, so
        # the absolute-delta guard below carries it alone.
        facts["ratio_basis"] = "expected_zero"
    elif ratio < thr.spike_ratio:
        return verdict(
            healthy(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                observed=observed, expected=round(expected, 3),
                reason=(
                    f"observed/expected ratio {ratio:.2f} has not reached "
                    f"{thr.spike_ratio:.2f}"
                ),
            )
        )

    # --- guard 6: absolute delta -------------------------------------------
    if delta < thr.min_absolute_delta_requests:
        return verdict(
            healthy(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                observed=observed, expected=round(expected, 3),
                reason=(
                    f"increase of {delta:.0f} requests/bucket is below the "
                    f"{thr.min_absolute_delta_requests:.0f} minimum absolute delta"
                ),
            )
        )

    # --- guard 7: robust score, or a deterministic fallback ----------------
    z, status = rstats.robust_z_with_status(
        observed, cell.median, cell.mad, mad_floor_ratio=ctx.mad_floor_ratio
    )
    facts["robust_score_status"] = status.value
    facts["robust_z"] = round(z, 3)

    if status is RobustScoreStatus.OK:
        if z < thr.deviation_z:
            return verdict(
                healthy(
                    at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                    observed=observed, expected=round(expected, 3),
                    reason=(
                        f"robust z={z:.2f} has not reached the "
                        f"+{thr.deviation_z} threshold"
                    ),
                )
            )
        basis = f"robust z={z:.2f} (threshold +{thr.deviation_z})"
    else:
        # MAD is zero: every retained sample sat at the median, so the score is
        # an artefact of the floored scale rather than a measure of surprise.
        # We do NOT return healthy here -- that would let a perfectly steady
        # baseline suppress a real, material deviation that has already cleared
        # the volume, ratio and absolute-delta guards.
        lower, upper = _expected_band(cell, ctx.mad_floor_ratio)
        facts["fallback_bound"] = round(upper, 4)
        if observed <= upper:
            return verdict(
                healthy(
                    at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                    observed=observed, expected=round(expected, 3),
                    reason=(
                        "robust score unavailable (MAD=0) and observed value is "
                        f"inside the expected band [{lower:.2f}, {upper:.2f}]"
                    ),
                )
            )
        basis = (
            "robust score unavailable (MAD=0); observed value is outside the "
            f"expected band [{lower:.2f}, {upper:.2f}]"
        )

    magnitude = abs(z) if status is RobustScoreStatus.OK else thr.deviation_z
    severity = _severity(ratio, magnitude, thr)
    ratio_text = f"{ratio:.2f}x expected" if ratio is not None else "expected no traffic"
    confidence = _confidence(cell, magnitude, status)

    return verdict(
        AnomalyEvidence(
            anomaly_type=at,
            signal=DetectorSignal.ANOMALOUS,
            severity=severity,
            observed_value=observed,
            expected_value=round(expected, 3),
            baseline_low=cell.p05,
            baseline_high=cell.p95,
            deviation_score=round(z, 3),
            threshold=thr.deviation_z,
            sample_count=cell.sample_count,
            interval_start=p.bucket_start,
            interval_end=p.bucket_end,
            confidence=confidence,
            reason=(
                f"{observed:.0f} requests is above the baseline median "
                f"{expected:.1f} ({ratio_text}, +{delta:.0f} requests/bucket); {basis}"
            ),
            extra=facts,
        )
    )


def _severity(ratio: float | None, magnitude: float, thr: WafThresholds) -> Severity:
    if ratio is not None and ratio / thr.spike_ratio >= 2.0:
        return Severity.HIGH
    if magnitude >= thr.deviation_z * 1.5:
        return Severity.HIGH
    return Severity.MEDIUM


def _confidence(
    cell: WafBaselineCell, magnitude: float, status: RobustScoreStatus
) -> float:
    """Blend baseline strength with deviation magnitude.

    A degenerate robust score is capped: the verdict rests on the deterministic
    fallback, which is sound but weaker evidence than a measured z-score, and
    the number an operator sees should say so.
    """
    reliability = min(1.0, cell.sample_count / 30.0)
    strength = min(1.0, magnitude / 6.0)
    base = 0.4 * reliability + 0.6 * strength
    value = base * min(1.0, cell.completeness + 0.5)
    if status is not RobustScoreStatus.OK:
        value = min(value, 0.7)
    return round(value, 3)
