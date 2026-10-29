"""The nine anomaly detectors. Pure functions over a DetectionContext.

Each returns an AnomalyEvidence with a HEALTHY / ANOMALOUS / UNKNOWN signal and
full structured evidence. No I/O, no ORM — the engine assembles the context and
persists the outcome. This keeps every detector exhaustively unit-testable
against exact inputs.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from app.anomaly import statistics as rstats
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient, unknown
from app.anomaly.thresholds import Thresholds
from app.core.config import get_settings
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    AnomalyType,
    BucketCompleteness,
    DetectorSignal,
    RobustScoreStatus,
    Severity,
)


@dataclass
class MetricPoint:
    interval_start: datetime
    interval_end: datetime
    completeness: BucketCompleteness
    event_count: int
    average_eps: float
    peak_eps: float
    last_event_at: datetime | None
    event_delay_seconds: float | None
    unknown_event_count: int
    stored_event_count: int
    parsed_username_ratio: float | None
    parsed_source_ip_ratio: float | None
    distinct_qid_count: int
    distinct_username_count: int
    distinct_source_ip_count: int
    collection_error_count: int
    payload_signature: str | None


@dataclass
class BaselineCell:
    median: float
    mad: float
    p05: float | None
    p95: float | None
    sample_count: int
    is_reliable: bool
    completeness: float = 0.0
    baseline_version: int = 1


@dataclass
class DetectionContext:
    point: MetricPoint
    baselines: dict[str, BaselineCell]
    thresholds: Thresholds
    expected_interval_seconds: float
    # payload_signature of preceding intervals, oldest→newest, excluding current
    recent_signatures: list[str] = field(default_factory=list)
    # whether the source is expected to be actively reporting in this interval
    # (business hours / schedule) — off-hours silence is not NO_EVENTS
    is_expected_active: bool = True
    # consecutive preceding buckets that were also empty, for the silence grace
    # period. Excludes the current bucket.
    preceding_empty_buckets: int = 0

    @property
    def mad_floor_ratio(self) -> float:
        return get_settings().baseline_mad_floor_ratio

    @property
    def bucket_seconds(self) -> float:
        """Width of the observed bucket. Converts an EPS baseline into the event
        counts the absolute-delta and minimum-volume guards are expressed in."""
        width = (self.point.interval_end - self.point.interval_start).total_seconds()
        return width if width > 0 else self.expected_interval_seconds


def _ref(name: str, **params: object) -> MessageRef:
    """A Phase A detector sentence (`anomaly.detector.<name>`), by its registry reference.

    The sentence is the registry's English template; a detector passes the reference to the
    evidence helpers, which render the stored text from it, or renders it itself for a verdict
    it builds directly.
    """
    return MessageRef(f"anomaly.detector.{name}", params)


def _confidence(cell: BaselineCell | None, magnitude: float) -> float:
    """Confidence blends baseline reliability with deviation magnitude."""
    if cell is None:
        return 0.5
    reliability = min(1.0, cell.sample_count / 30.0)
    strength = min(1.0, magnitude / 6.0)
    return round(0.4 * reliability + 0.6 * strength, 3)


# --------------------------------------------------------------------------- 1
def detect_no_events(ctx: DetectionContext) -> AnomalyEvidence:
    """Silence, judged against what this source normally does at this time.

    A source that is normally inactive in this seasonal cell must never raise
    NO_EVENTS — the overnight silence of a business-hours application is the
    expected observation, not an outage.
    """
    p = ctx.point
    at = AnomalyType.NO_EVENTS
    cell = ctx.baselines.get("event_count")
    expected = cell.median if cell else None

    if p.event_count > 0:
        return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       observed=float(p.event_count), expected=expected,
                       reason_message=_ref("within_range"))

    # --- the bucket is empty; decide whether that is a finding --------------
    # A bucket we did not fully observe is not evidence of silence. Reporting
    # NO_EVENTS on a failed collection would make every collector outage look
    # like a simultaneous outage of every monitored source.
    if p.completeness is not BucketCompleteness.COMPLETE:
        return insufficient(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            reason_message=_ref(
                "no_events_bucket_incomplete", completeness=p.completeness.value
            ),
        )

    # Schedule: off-hours for a business-hours-only source.
    if not ctx.is_expected_active:
        return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       observed=0.0, expected=expected,
                       reason_message=_ref("no_events_not_expected"))

    # Seasonal pattern: this weekday/hour cell is normally empty.
    if cell is not None and cell.is_reliable and cell.median <= 0:
        return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       observed=0.0, expected=0.0,
                       reason_message=_ref("no_events_normally_inactive"))

    # Without a baseline we cannot distinguish a new quiet source from a broken
    # one. Say so rather than guessing in either direction.
    if cell is None or not cell.is_reliable:
        return insufficient(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            reason_message=_ref("no_events_no_baseline"),
            sample_count=cell.sample_count if cell else 0,
        )

    # Grace period: tolerate short gaps from a bursty-but-healthy source.
    grace = ctx.thresholds.silence_grace_buckets
    if ctx.preceding_empty_buckets < grace:
        return healthy(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            observed=0.0, expected=expected,
            reason_message=_ref("no_events_grace", grace=grace),
        )

    empty_streak = ctx.preceding_empty_buckets + 1
    fired = _ref("no_events_streak", streak=empty_streak, expected=expected)
    return AnomalyEvidence(
        anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.HIGH,
        observed_value=0.0, expected_value=expected,
        baseline_low=cell.p05, baseline_high=cell.p95,
        deviation_score=None, threshold=0.0, sample_count=cell.sample_count,
        interval_start=p.interval_start, interval_end=p.interval_end,
        confidence=round(min(1.0, 0.6 + 0.1 * empty_streak), 3),
        reason=render(fired),
        reason_message=fired,
        extra={
            "empty_buckets": empty_streak,
            "grace_buckets": grace,
            "last_event_at": p.last_event_at.isoformat() if p.last_event_at else None,
            "baseline_version": cell.baseline_version,
        },
    )


# --------------------------------------------------------------------------- 2
def detect_volume_drop(ctx: DetectionContext) -> AnomalyEvidence:
    return _volume(ctx, AnomalyType.VOLUME_DROP, direction=-1)


# --------------------------------------------------------------------------- 3
def detect_volume_spike(ctx: DetectionContext) -> AnomalyEvidence:
    return _volume(ctx, AnomalyType.VOLUME_SPIKE, direction=+1)


def _volume(ctx: DetectionContext, at: AnomalyType, *, direction: int) -> AnomalyEvidence:
    """Conjunctive volume verdict. Every guard must pass for the detector to fire.

        adequate baseline
        AND complete metric bucket
        AND volume above the source minimum
        AND ratio guard satisfied
        AND absolute-delta guard satisfied
        AND ( robust z-score threshold satisfied
              OR robust score is degenerate (MAD = 0) and the deterministic
                 expected-bound fallback is satisfied )

    Guards are conjunctive on purpose: adding one can only make the detector
    more conservative. The ratio and absolute-delta pair specifically kills the
    classic false positive where 0.2 EPS becomes 0.4 EPS — a clean 2x ratio and
    a large robust z-score on a change of no operational significance.
    """
    p = ctx.point
    thr = ctx.thresholds

    # --- guard 1: the bucket must be a real, whole observation --------------
    # A partially collected interval looks exactly like a volume drop. Judging
    # one would turn a collection outage into a fleet-wide false alarm.
    if p.completeness is not BucketCompleteness.COMPLETE:
        return insufficient(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            reason_message=_ref("volume_bucket_incomplete", completeness=p.completeness.value),
        )

    # --- guard 2: adequate baseline ----------------------------------------
    cell = ctx.baselines.get("average_eps")
    if cell is None:
        return insufficient(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            reason_message=_ref("volume_no_baseline"),
        )
    if not cell.is_reliable:
        return insufficient(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            reason_message=_ref("volume_baseline_samples", samples=cell.sample_count),
            sample_count=cell.sample_count,
        )

    observed_eps = p.average_eps
    expected_eps = cell.median
    seconds = ctx.bucket_seconds
    observed_events = observed_eps * seconds
    expected_events = expected_eps * seconds
    # Signed, in events per bucket. Positive means more traffic than expected.
    delta_events = observed_events - expected_events

    band = _ExpectedBand(cell)
    facts: dict[str, object] = {
        "observed_eps": round(observed_eps, 4),
        "expected_eps": round(expected_eps, 4),
        "observed_events": round(observed_events, 1),
        "expected_events": round(expected_events, 1),
        "absolute_delta_events": round(delta_events, 1),
        "bucket_seconds": seconds,
        "baseline_completeness": round(cell.completeness, 3),
        "baseline_version": cell.baseline_version,
    }

    # --- guard 3: minimum volume -------------------------------------------
    # For a spike the observed side must be material; for a drop it is the
    # expected side, because the observed side is by definition small.
    subject_events = observed_events if direction > 0 else expected_events
    if subject_events < thr.min_bucket_events:
        return healthy(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            observed=round(observed_eps, 3), expected=round(expected_eps, 3),
            reason_message=_ref(
                "volume_min_events", events=subject_events, minimum=thr.min_bucket_events
            ),
        )

    # --- guard 4: ratio -----------------------------------------------------
    observed_ratio = rstats.ratio(observed_eps, expected_eps)
    facts["ratio"] = round(observed_ratio, 4) if observed_ratio is not None else None
    if observed_ratio is None:
        # Expected is zero. A drop below nothing is not a thing; a spike from
        # nothing is real but has no ratio, so the absolute-delta guard below
        # carries it alone.
        if direction < 0:
            return healthy(
                at, interval_start=p.interval_start, interval_end=p.interval_end,
                observed=round(observed_eps, 3), expected=0.0,
                reason_message=_ref("volume_drop_undefined"),
            )
        facts["ratio_basis"] = "expected_zero"
    else:
        ratio_ok = (
            observed_ratio >= thr.spike_ratio if direction > 0
            else observed_ratio <= thr.drop_ratio
        )
        if not ratio_ok:
            limit = thr.spike_ratio if direction > 0 else thr.drop_ratio
            return healthy(
                at, interval_start=p.interval_start, interval_end=p.interval_end,
                observed=round(observed_eps, 3), expected=round(expected_eps, 3),
                reason_message=_ref(
                    "volume_ratio_not_reached" if direction > 0 else "volume_ratio_not_fallen",
                    ratio=observed_ratio,
                    limit=limit,
                ),
            )

    # --- guard 5: absolute delta -------------------------------------------
    directional_delta = delta_events if direction > 0 else -delta_events
    if directional_delta < thr.min_absolute_delta_events:
        return healthy(
            at, interval_start=p.interval_start, interval_end=p.interval_end,
            observed=round(observed_eps, 3), expected=round(expected_eps, 3),
            reason_message=_ref(
                "volume_delta_below_min",
                delta=directional_delta,
                minimum=thr.min_absolute_delta_events,
            ),
        )

    # --- guard 6: robust score, or a deterministic fallback ----------------
    z, score_status = rstats.robust_z_with_status(
        observed_eps, cell.median, cell.mad, mad_floor_ratio=ctx.mad_floor_ratio
    )
    facts["robust_score_status"] = score_status.value
    facts["robust_z"] = round(z, 3)

    if score_status is RobustScoreStatus.OK:
        score_ok = (z >= thr.deviation_z) if direction > 0 else (z <= -thr.deviation_z)
        if not score_ok:
            return healthy(
                at, interval_start=p.interval_start, interval_end=p.interval_end,
                observed=round(observed_eps, 3), expected=round(expected_eps, 3),
                reason_message=_ref(
                    "volume_z_not_reached", score=z, threshold=thr.deviation_z
                ),
            )
        basis = _ref("basis_z", score=z, threshold=thr.deviation_z)
    else:
        # MAD is zero: every retained sample sat at the median, so the score is
        # an artefact of the floored scale rather than a measure of surprise.
        # Crucially we do NOT return healthy here — that would let a perfectly
        # steady baseline suppress a real, material deviation that has already
        # cleared the volume, ratio and absolute-delta guards. Fall back to the
        # deterministic expected band instead.
        bound = band.upper if direction > 0 else band.lower
        outside = (observed_eps > bound) if direction > 0 else (observed_eps < bound)
        facts["fallback_bound"] = round(bound, 4)
        if not outside:
            return healthy(
                at, interval_start=p.interval_start, interval_end=p.interval_end,
                observed=round(observed_eps, 3), expected=round(expected_eps, 3),
                reason_message=_ref(
                    "volume_mad_inside_band", lower=band.lower, upper=band.upper
                ),
            )
        basis = _ref("basis_mad_outside", lower=band.lower, upper=band.upper)

    # --- every guard passed -------------------------------------------------
    magnitude = abs(z) if score_status is RobustScoreStatus.OK else thr.deviation_z
    severity = _volume_severity(observed_ratio, magnitude, thr, direction)
    ratio_text = (
        _ref("ratio_times_expected", ratio=observed_ratio)
        if observed_ratio is not None
        else _ref("ratio_no_traffic")
    )
    fired = _ref(
        "volume_above" if direction > 0 else "volume_below",
        observed=observed_eps,
        expected=expected_eps,
        ratio_text=ratio_text,
        delta=directional_delta,
        basis=basis,
    )
    return AnomalyEvidence(
        anomaly_type=at,
        signal=DetectorSignal.ANOMALOUS,
        severity=severity,
        observed_value=round(observed_eps, 3),
        expected_value=round(expected_eps, 3),
        baseline_low=cell.p05,
        baseline_high=cell.p95,
        deviation_score=round(z, 3),
        threshold=thr.deviation_z,
        sample_count=cell.sample_count,
        interval_start=p.interval_start,
        interval_end=p.interval_end,
        confidence=_volume_confidence(cell, magnitude, score_status),
        reason=render(fired),
        reason_message=fired,
        extra=facts,
    )


@dataclass
class _ExpectedBand:
    """The displayed expected range, with safe fallbacks when percentiles are
    missing. Used as the deterministic test when the robust score is degenerate."""

    lower: float
    upper: float

    def __init__(self, cell: BaselineCell) -> None:
        # Percentiles are the honest bounds when present. Without them, fall
        # back to a symmetric band around the median rather than to the median
        # itself, which would make any nonzero difference "outside the band".
        spread = max(abs(cell.median) * get_settings().baseline_mad_floor_ratio, 1e-9)
        self.lower = cell.p05 if cell.p05 is not None else cell.median - spread
        self.upper = cell.p95 if cell.p95 is not None else cell.median + spread


def _volume_severity(
    observed_ratio: float | None, magnitude: float, thr: Thresholds, direction: int
) -> Severity:
    """Severity from how far past the guards the deviation went."""
    if observed_ratio is not None:
        # How many times past the configured ratio the source actually went.
        excess = (
            observed_ratio / thr.spike_ratio
            if direction > 0
            else (thr.drop_ratio / observed_ratio if observed_ratio > 0 else float("inf"))
        )
        if excess >= 2.0:
            return Severity.HIGH
    if magnitude >= thr.deviation_z * 1.5:
        return Severity.HIGH
    return Severity.MEDIUM


def _volume_confidence(
    cell: BaselineCell, magnitude: float, score_status: RobustScoreStatus
) -> float:
    """Confidence blends baseline strength with deviation magnitude.

    A degenerate robust score is capped: the verdict rests on the deterministic
    fallback, which is sound but weaker evidence than a measured z-score, and
    the number an operator sees should say so.
    """
    base = _confidence(cell, magnitude)
    quality = min(1.0, cell.completeness + 0.5)
    value = base * quality
    if score_status is not RobustScoreStatus.OK:
        value = min(value, 0.7)
    return round(value, 3)


# --------------------------------------------------------------------------- 4
def detect_parsing_degradation(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.PARSING_DEGRADATION
    ratios = [r for r in (p.parsed_username_ratio, p.parsed_source_ip_ratio) if r is not None]
    if not ratios:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason_message=_ref("parsing_no_data"))
    worst = min(ratios)
    floor = ctx.thresholds.min_parsed_ratio
    if worst < floor:
        fired = _ref("parsing_below_floor", ratio=worst, floor=floor)
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=round(worst, 3), expected_value=floor,
            baseline_low=floor, baseline_high=1.0, deviation_score=round(floor - worst, 3),
            threshold=floor, sample_count=p.event_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=round(min(1.0, (floor - worst) / max(floor, 0.01)), 3),
            reason=render(fired),
            reason_message=fired,
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(worst, 3), expected=floor,
                   reason_message=_ref("within_range"))


# --------------------------------------------------------------------------- 5
def detect_unknown_event_spike(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.UNKNOWN_EVENT_SPIKE
    if p.event_count <= 0:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason_message=_ref("unknown_no_events"))
    ratio = p.unknown_event_count / p.event_count
    ceiling = ctx.thresholds.max_unknown_ratio
    if ratio > ceiling:
        fired = _ref("unknown_above_ceiling", ratio=ratio, ceiling=ceiling)
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=round(ratio, 3), expected_value=ceiling,
            baseline_low=0.0, baseline_high=ceiling, deviation_score=round(ratio - ceiling, 3),
            threshold=ceiling, sample_count=p.event_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=round(min(1.0, (ratio - ceiling) / max(ceiling, 0.01)), 3),
            reason=render(fired),
            reason_message=fired,
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(ratio, 3), expected=ceiling,
                   reason_message=_ref("within_range"))


# --------------------------------------------------------------------------- 6
def detect_timestamp_delay(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.TIMESTAMP_DELAY
    if p.event_delay_seconds is None:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason_message=_ref("delay_no_measurement"))
    limit = ctx.thresholds.delay_interval_multiple * ctx.expected_interval_seconds
    if p.event_delay_seconds > limit:
        fired = _ref("delay_exceeds", delay=p.event_delay_seconds, limit=limit)
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=round(p.event_delay_seconds, 1), expected_value=round(limit, 1),
            baseline_low=0.0, baseline_high=limit,
            deviation_score=round(p.event_delay_seconds / max(limit, 1), 2),
            threshold=round(limit, 1), sample_count=1,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=0.8,
            reason=render(fired),
            reason_message=fired,
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=round(p.event_delay_seconds, 1), expected=round(limit, 1),
                   reason_message=_ref("within_range"))


# --------------------------------------------------------------------------- 7
def detect_cardinality_drop(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.CARDINALITY_DROP
    cell = ctx.baselines.get("distinct_source_ip_count")
    if cell is None or not cell.is_reliable or cell.median <= 0:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason_message=_ref("cardinality_no_baseline"))
    frac = ctx.thresholds.cardinality_drop_fraction
    expected_floor = cell.median * frac
    if p.distinct_source_ip_count < expected_floor:
        fired = _ref(
            "cardinality_dropped",
            count=p.distinct_source_ip_count,
            fraction=frac,
            median=cell.median,
        )
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=float(p.distinct_source_ip_count), expected_value=round(cell.median, 1),
            baseline_low=round(expected_floor, 1), baseline_high=cell.p95,
            deviation_score=round(1 - p.distinct_source_ip_count / max(cell.median, 1), 3),
            threshold=round(expected_floor, 1), sample_count=cell.sample_count,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=_confidence(cell, 4.0),
            reason=render(fired),
            reason_message=fired,
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(p.distinct_source_ip_count), expected=round(cell.median, 1),
                   reason_message=_ref("within_range"))


# --------------------------------------------------------------------------- 8
def detect_collection_error(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.COLLECTION_ERROR
    if p.collection_error_count > ctx.thresholds.collection_error_threshold:
        fired = _ref("collection_errors", count=p.collection_error_count)
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.HIGH,
            observed_value=float(p.collection_error_count),
            expected_value=float(ctx.thresholds.collection_error_threshold),
            baseline_low=0.0, baseline_high=float(ctx.thresholds.collection_error_threshold),
            deviation_score=float(p.collection_error_count), threshold=float(
                ctx.thresholds.collection_error_threshold),
            sample_count=1, interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=0.95,
            reason=render(fired),
            reason_message=fired,
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(p.collection_error_count), expected=0.0,
                   reason_message=_ref("within_range"))


# --------------------------------------------------------------------------- 9
def detect_repeated_payload(ctx: DetectionContext) -> AnomalyEvidence:
    p = ctx.point
    at = AnomalyType.REPEATED_PAYLOAD
    if p.payload_signature is None:
        return unknown(at, interval_start=p.interval_start, interval_end=p.interval_end,
                       reason_message=_ref("payload_no_signature"))
    need = ctx.thresholds.repeated_payload_intervals
    # Count trailing consecutive intervals (including current) with same signature.
    streak = 1
    for sig in reversed(ctx.recent_signatures):
        if sig == p.payload_signature:
            streak += 1
        else:
            break
    if streak >= need:
        fired = _ref("payload_repeated", streak=streak)
        return AnomalyEvidence(
            anomaly_type=at, signal=DetectorSignal.ANOMALOUS, severity=Severity.MEDIUM,
            observed_value=float(streak), expected_value=float(need - 1),
            baseline_low=0.0, baseline_high=float(need - 1), deviation_score=float(streak),
            threshold=float(need), sample_count=streak,
            interval_start=p.interval_start, interval_end=p.interval_end,
            confidence=round(min(1.0, streak / (need * 2)), 3),
            reason=render(fired),
            reason_message=fired,
        )
    return healthy(at, interval_start=p.interval_start, interval_end=p.interval_end,
                   observed=float(streak), expected=float(need),
                   reason_message=_ref("within_range"))


# Canonical order. NO_EVENTS first: when it fires the engine suppresses the
# volume/parsing/cardinality detectors, which a zero-event interval would
# trivially and redundantly trip.
DETECTORS: list[Callable[[DetectionContext], AnomalyEvidence]] = [
    detect_no_events,
    detect_collection_error,
    detect_volume_drop,
    detect_volume_spike,
    detect_parsing_degradation,
    detect_unknown_event_spike,
    detect_timestamp_delay,
    detect_cardinality_drop,
    detect_repeated_payload,
]

# When NO_EVENTS fires, these are meaningless for the same interval.
SUPPRESSED_BY_NO_EVENTS = {
    AnomalyType.VOLUME_DROP,
    AnomalyType.PARSING_DEGRADATION,
    AnomalyType.UNKNOWN_EVENT_SPIKE,
    AnomalyType.CARDINALITY_DROP,
    AnomalyType.REPEATED_PAYLOAD,
}
