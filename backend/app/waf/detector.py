"""WAF_REQUEST_RATE_SPIKE — the Phase B request-rate detector.

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

**Lower bounds.** When a bucket's counts are lower bounds (grouping truncated or
coverage below its floor) and the engine grants `INCREASE_ONLY` scope, the
detector runs its guards on the lower bound. Every guard on the way to an
anomalous verdict -- minimum volume, ratio, absolute delta, robust z, the
degenerate-MAD band -- is monotonically non-decreasing in the observed count,
so a lower bound that clears all of them proves the true count does too. Any
exit that would have been a normal verdict becomes INSUFFICIENT_DATA instead:
the hidden events may be exactly what makes it abnormal. See section 29.2.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.anomaly import statistics as rstats
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    BucketCompleteness,
    DetectorSignal,
    RobustScoreStatus,
    Severity,
    WafAnomalyType,
    WafMeasurement,
)
from app.waf.measurement import WafVerdictScope, default_measurement

#: A sentence as a detector holds it: a registry reference, or the English text of a sentence
#: that has no code (a caller's own wording, or a scope reason this build does not know).
Reason = MessageRef | str

_SCOPE_CODES = (
    "waf.scope.unstated",
    "waf.scope.lower_bound_increase",
    "waf.scope.needs_exact",
)
#: The scope decision's English (`app.waf.measurement.verdict_scope`) to its code. The registry
#: template is the English, so a reason that is not one of these stays plain text.
_SCOPE_BY_TEXT: dict[str, MessageRef] = {
    render(MessageRef(code)): MessageRef(code) for code in _SCOPE_CODES
}


def scope_reason_ref(reason: str | None) -> Reason | None:
    """The scope reason as a reference when it is one of the scope decision's sentences."""
    if reason is None:
        return None
    return _SCOPE_BY_TEXT.get(reason, reason)


def semantics_message(bucket: str | None, baseline: str | None) -> MessageRef | None:
    """`app.waf.semantics.semantics_incompatibility` as a reference (None when comparable)."""
    if bucket is None:
        return MessageRef("waf.semantics.bucket_unidentified")
    if baseline is None:
        return MessageRef("waf.semantics.baseline_unidentified")
    if bucket != baseline:
        return MessageRef("waf.semantics.mismatch", {"baseline": baseline, "bucket": bucket})
    return None


def split_reason(reason: Reason) -> tuple[str, MessageRef | None]:
    """The English text of a sentence and, when it has one, its reference."""
    if isinstance(reason, MessageRef):
        return render(reason), reason
    return reason, None


def _paired(text: str, message: MessageRef | None) -> Reason:
    """A stored text with its reference, kept only while the reference renders that text."""
    if message is not None and render(message) == text:
        return message
    return text


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
    #: What `request_count` claims. None: derive from completeness, which makes
    #: only a COMPLETE point exact and never assumes a lower bound.
    measurement: WafMeasurement | None = None

    @property
    def effective_measurement(self) -> WafMeasurement | None:
        if self.measurement is not None:
            return self.measurement
        return default_measurement(self.completeness)


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
    #: Ratio metrics: pooled numerator and denominator over the cell's buckets.
    numerator_sum: float | None = None
    denominator_sum: float | None = None
    metric_semantics: str | None = None


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
    #: Set by the engine when a baseline cell exists but was built under a
    #: different metric-semantics identity than the bucket. The detector then
    #: refuses a verdict rather than comparing incomparable numbers.
    baseline_incompatibility: str | None = None
    #: The same sentence as a registry reference, when the engine has one.
    baseline_incompatibility_message: MessageRef | None = None
    #: What the engine, from the detector's declared measurement requirement
    #: (app.anomaly.capabilities), allows this verdict to conclude. None means
    #: no grant: FULL for an exact count and NONE otherwise -- lower-bound
    #: reasoning is never assumed.
    verdict_scope: WafVerdictScope | None = None
    #: Why the scope is narrower than FULL, for the verdict's reason text.
    scope_reason: str | None = None
    #: Phase B.2 detectors read the whole observation (breakdown counts and
    #: their measurement) rather than the request-only `point`.
    observation: Any = None
    #: Every baseline cell loaded for the endpoint, by metric name, so a
    #: detector can put its verdict in context (a concentration verdict states
    #: whether volume also changed).
    baselines: dict[str, WafBaselineCell] = field(default_factory=dict)
    behavior: Any = None
    novelty: Any = None

    @property
    def effective_scope(self) -> WafVerdictScope:
        if self.verdict_scope is not None:
            return self.verdict_scope
        exact = self.point.effective_measurement is WafMeasurement.EXACT
        return WafVerdictScope.FULL if exact else WafVerdictScope.NONE


@dataclass
class WafSpikeVerdict:
    detector_type: WafAnomalyType
    evidence: AnomalyEvidence[WafAnomalyType]

    @property
    def signal(self) -> DetectorSignal:
        return self.evidence.signal

    @property
    def is_anomalous(self) -> bool:
        return self.evidence.is_anomalous


def _ref(name: str, **params: object) -> MessageRef:
    return MessageRef(f"waf.rate.{name}", params)


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
    # The evidence carries the real WAF type: it is persisted in
    # `waf_anomaly.details`, where a borrowed Phase A type would be false.
    at = dt = WafAnomalyType.WAF_REQUEST_RATE_SPIKE

    def verdict(ev: AnomalyEvidence[WafAnomalyType]) -> WafSpikeVerdict:
        return WafSpikeVerdict(detector_type=dt, evidence=ev)

    def no_verdict(reason: Reason, *, sample_count: int = 0) -> WafSpikeVerdict:
        text, message = split_reason(reason)
        return verdict(
            insufficient(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                reason=text, sample_count=sample_count, reason_message=message,
            )
        )

    scope = ctx.effective_scope
    lower_bound = scope is WafVerdictScope.INCREASE_ONLY

    # --- guard 1: the count must support a verdict ---------------------------
    # A whole observation supports any verdict. A lower bound supports only a
    # proven increase, and only when the engine granted that scope. Anything
    # else -- MISSING, or a partial count with no recorded semantics -- none.
    whole = p.completeness is BucketCompleteness.COMPLETE and scope is WafVerdictScope.FULL
    partial_bound = lower_bound and p.completeness is BucketCompleteness.PARTIAL
    if not (whole or partial_bound):
        reason: Reason = _ref("bucket_not_whole", completeness=p.completeness.value.lower())
        if p.completeness is BucketCompleteness.COMPLETE and ctx.scope_reason:
            reason = scope_reason_ref(ctx.scope_reason) or ctx.scope_reason
        return no_verdict(reason)

    def not_proven(reason: MessageRef, *, observed: float, expected: float) -> WafSpikeVerdict:
        """The normal exit of a guard: healthy for an exact count, no verdict for a bound."""
        if lower_bound:
            return no_verdict(_ref("not_proven_lower_bound", observed=observed, reason=reason))
        text, message = split_reason(reason)
        return verdict(
            healthy(
                at, interval_start=p.bucket_start, interval_end=p.bucket_end,
                observed=observed, expected=round(expected, 3), reason=text,
                reason_message=message,
            )
        )

    # --- guard 2: enough of the source's traffic carried an endpoint --------
    # A bucket where a third of the requests had no parseable path is not a
    # whole observation of any endpoint inside it, however complete the
    # collection was. For a lower bound this is already accounted for: the
    # unattributed events are exactly what the bound leaves out.
    if not lower_bound and p.endpoint_coverage_ratio < ctx.min_coverage:
        return no_verdict(_ref("coverage_low", covered=p.endpoint_coverage_ratio))

    # --- guard 3: a compatible, adequate baseline ---------------------------
    if ctx.baseline_incompatibility is not None:
        return no_verdict(
            _paired(ctx.baseline_incompatibility, ctx.baseline_incompatibility_message)
        )
    cell = ctx.baseline
    if cell is None:
        return no_verdict(_ref("no_baseline"))
    if not cell.is_reliable:
        return no_verdict(
            _ref("baseline_samples", samples=cell.sample_count), sample_count=cell.sample_count
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
        "request_count_measurement": (
            WafMeasurement.LOWER_BOUND.value if lower_bound else WafMeasurement.EXACT.value
        ),
        **ctx.extra,
    }

    # --- guard 4: minimum volume -------------------------------------------
    if observed < thr.min_bucket_requests:
        return not_proven(
            _ref("below_min_volume", observed=observed, minimum=thr.min_bucket_requests),
            observed=observed, expected=expected,
        )

    # --- guard 5: ratio -----------------------------------------------------
    ratio = rstats.ratio(observed, expected)
    facts["ratio"] = round(ratio, 4) if ratio is not None else None
    if ratio is None:
        # Expected is zero. A spike from nothing is real but has no ratio, so
        # the absolute-delta guard below carries it alone.
        facts["ratio_basis"] = "expected_zero"
    elif ratio < thr.spike_ratio:
        return not_proven(
            _ref("ratio_not_reached", ratio=ratio, threshold=thr.spike_ratio),
            observed=observed, expected=expected,
        )

    # --- guard 6: absolute delta -------------------------------------------
    if delta < thr.min_absolute_delta_requests:
        return not_proven(
            _ref("delta_below_min", delta=delta, minimum=thr.min_absolute_delta_requests),
            observed=observed, expected=expected,
        )

    # --- guard 7: robust score, or a deterministic fallback ----------------
    z, status = rstats.robust_z_with_status(
        observed, cell.median, cell.mad, mad_floor_ratio=ctx.mad_floor_ratio
    )
    facts["robust_score_status"] = status.value
    facts["robust_z"] = round(z, 3)

    if status is RobustScoreStatus.OK:
        if z < thr.deviation_z:
            return not_proven(
                _ref("z_not_reached", score=z, threshold=thr.deviation_z),
                observed=observed, expected=expected,
            )
        basis = MessageRef("waf.basis.z", {"score": z, "threshold": thr.deviation_z})
    else:
        # MAD is zero: every retained sample sat at the median, so the score is
        # an artefact of the floored scale rather than a measure of surprise.
        # We do NOT return healthy here -- that would let a perfectly steady
        # baseline suppress a real, material deviation that has already cleared
        # the volume, ratio and absolute-delta guards.
        lower, upper = _expected_band(cell, ctx.mad_floor_ratio)
        facts["fallback_bound"] = round(upper, 4)
        if observed <= upper:
            return not_proven(
                _ref("mad_within_band", lower=lower, upper=upper),
                observed=observed, expected=expected,
            )
        basis = MessageRef("waf.basis.mad_outside_band", {"lower": lower, "upper": upper})

    magnitude = abs(z) if status is RobustScoreStatus.OK else thr.deviation_z
    severity = _severity(ratio, magnitude, thr)
    ratio_text = (
        _ref("ratio_multiple", ratio=ratio) if ratio is not None else _ref("ratio_none")
    )
    confidence = _confidence(cell, magnitude, status)
    fired = (
        _ref(
            "fired_lower",
            observed=observed,
            scope=scope_reason_ref(ctx.scope_reason) or MessageRef("waf.scope.unattributed"),
            expected=expected,
            ratio_text=ratio_text,
            delta=delta,
            basis=basis,
        )
        if lower_bound
        else _ref(
            "fired", observed=observed, expected=expected, ratio_text=ratio_text, delta=delta,
            basis=basis,
        )
    )

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
            reason=render(fired),
            reason_message=fired,
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
