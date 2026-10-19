"""Pure property-watch behavioural detectors (design §G.8 and §G.9.3).

Measurement scope always comes from ``PW_DETECTORS`` through
``pw_input_scope``. The functions here consume that decision; they never turn a
lower bound into permission on their own.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from app.anomaly import statistics as rstats
from app.anomaly.capabilities import pw_input_scope
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.models.enums import (
    DetectorSignal,
    PwAnomalyType,
    PwBucketMeasurement,
    PwWatchStatus,
    RobustScoreStatus,
    Severity,
    WafMeasurement,
)
from app.property_watch.baseline import PwBaselineCell
from app.property_watch.facts import PwBucketFact, PwReading, PwReadingKind
from app.property_watch.inputs import PwObservation
from app.waf.inputs import HistoryFacts
from app.waf.measurement import ScopeDecision, WafVerdictScope

EVIDENCE_VERSION = 1


@dataclass(frozen=True)
class PwThresholds:
    deviation_z: float = 3.5
    mad_floor_ratio: float = 0.1
    min_coverage: float = 0.9
    baseline_min_samples: int = 8
    spike_ratio: float = 2.0
    min_absolute_delta_events: float = 50.0
    min_bucket_events: float = 20.0
    drop_min_median: float = 20.0
    drop_ratio: float = 0.5
    disappear_min_median: float = 5.0
    disappear_buckets: int = 6
    value_active_days: int = 7
    new_value_history_days: int = 7
    new_value_window_buckets: int = 12
    new_value_min_events: int = 20
    new_value_min_buckets: int = 2
    admission_min_events: int = 3
    baseline_min_visits: int = 2


@dataclass(frozen=True)
class PwReadingPoint:
    bucket: PwBucketFact
    reading: PwReading


@dataclass(frozen=True)
class PwComponentFact:
    value: str
    tracked_before: bool
    measurement: WafMeasurement | None
    seen_below_admission: bool = False

    @property
    def known(self) -> bool:
        return self.tracked_before and self.measurement is not None


@dataclass(frozen=True)
class PwNoveltyFacts:
    first_seen_at: datetime
    history: HistoryFacts
    history_start: datetime
    history_end: datetime
    history_buckets: int
    appearance_bucket: PwBucketFact
    events_since_first_seen: int
    buckets_with_traffic: int
    materiality_measurement: WafMeasurement
    watch_first_collected_at: datetime | None
    pinned: bool = False
    membership_changed: bool = False
    property_count: int = 1
    components: tuple[PwComponentFact, ...] = ()
    established_at_hand_off: datetime | None = None


@dataclass
class PwDetectionContext:
    watch_status: PwWatchStatus | None
    bucket: PwBucketFact | None
    reading: PwReading
    baseline: PwBaselineCell | None = None
    thresholds: PwThresholds = field(default_factory=PwThresholds)
    last_seen_at: datetime | None = None
    recent: tuple[PwReadingPoint, ...] = ()
    novelty: PwNoveltyFacts | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PwVerdict:
    detector_type: PwAnomalyType
    evidence: AnomalyEvidence[PwAnomalyType]

    @property
    def signal(self) -> DetectorSignal:
        return self.evidence.signal

    @property
    def is_anomalous(self) -> bool:
        return self.evidence.is_anomalous


def _component_measurement(novelty: PwNoveltyFacts | None) -> WafMeasurement | None:
    if novelty is None or len(novelty.components) != 2:
        return None
    if not all(component.known for component in novelty.components):
        return None
    if any(component.measurement is WafMeasurement.LOWER_BOUND for component in novelty.components):
        return WafMeasurement.LOWER_BOUND
    return WafMeasurement.EXACT


class _Guard:
    def __init__(self, ctx: PwDetectionContext, detector_type: PwAnomalyType) -> None:
        self.ctx = ctx
        self.detector_type = detector_type
        self.bucket = ctx.bucket
        self.thresholds = ctx.thresholds
        novelty = ctx.novelty
        self.observation = PwObservation(
            bucket=ctx.bucket,
            reading=ctx.reading,
            history=novelty.history if novelty is not None else None,
            component_history=_component_measurement(novelty),
            materiality=(novelty.materiality_measurement if novelty is not None else None),
        )
        self.decision: ScopeDecision = pw_input_scope(detector_type, self.observation)

    def verdict(self, evidence: AnomalyEvidence[PwAnomalyType]) -> PwVerdict:
        return PwVerdict(self.detector_type, evidence)

    def _interval(self) -> tuple[datetime, datetime]:
        if self.bucket is None:
            start = datetime.min.replace(tzinfo=UTC)
            return start, start
        return (
            self.bucket.bucket_start,
            self.bucket.bucket_start + timedelta(seconds=self.bucket.bucket_seconds),
        )

    def insufficient(self, reason: str, *, sample_count: int = 0) -> PwVerdict:
        start, end = self._interval()
        return self.verdict(
            insufficient(
                self.detector_type,
                interval_start=start,
                interval_end=end,
                reason=reason,
                sample_count=sample_count,
            )
        )

    def healthy(
        self, reason: str, *, observed: float | None = None, expected: float | None = None
    ) -> PwVerdict:
        start, end = self._interval()
        return self.verdict(
            healthy(
                self.detector_type,
                interval_start=start,
                interval_end=end,
                observed=observed,
                expected=expected,
                reason=reason,
            )
        )

    def not_proven(self, reason: str, *, observed: float, expected: float) -> PwVerdict:
        if self.decision.scope is WafVerdictScope.INCREASE_ONLY:
            return self.insufficient(
                f"{reason}; {self.decision.reason or 'an input is a lower bound'}, so a "
                "normal verdict cannot be concluded"
            )
        return self.healthy(reason, observed=observed, expected=expected)

    def watch(self) -> PwVerdict | None:
        blocked = {
            PwWatchStatus.NOT_ENABLED,
            PwWatchStatus.DISABLED,
            PwWatchStatus.NOT_APPLICABLE,
            PwWatchStatus.SCOPE_STALE,
            PwWatchStatus.SCOPE_EMPTY,
            PwWatchStatus.SCOPE_TOO_LARGE,
            PwWatchStatus.STALE,
        }
        if self.ctx.watch_status is None:
            return self.insufficient("the watch has never established a collection status")
        if self.ctx.watch_status in blocked:
            return self.insufficient(
                f"the watch is {self.ctx.watch_status.value}: no value verdict is possible"
            )
        return None

    def bucket_guard(self) -> PwVerdict | None:
        if self.bucket is None or self.ctx.reading.kind is PwReadingKind.NO_BUCKET:
            return self.insufficient("the bucket is missing: no count was measured")
        if self.bucket.values_measurement is PwBucketMeasurement.NOT_COLLECTED:
            return self.insufficient("the bucket was NOT_COLLECTED: its count is not zero")
        if self.bucket.scope_changed:
            return self.insufficient("membership changed during this bucket")
        if self.bucket.property_not_parsed:
            return self.insufficient(
                "the property parsed on none of this bucket's events: NOT_APPLICABLE"
            )
        return None

    def cell(self) -> PwBaselineCell | PwVerdict:
        cell = self.ctx.baseline
        if cell is None:
            return self.insufficient("no value-events baseline for this weekday/hour cell yet")
        if cell.watch_version != self.bucket.watch_version:  # type: ignore[union-attr]
            return self.insufficient(
                f"the baseline uses watch version {cell.watch_version}, not the bucket's "
                f"{self.bucket.watch_version}"  # type: ignore[union-attr]
            )
        if (
            not cell.is_reliable
            and cell.sample_count >= self.thresholds.baseline_min_samples
        ):
            visits = self.thresholds.baseline_min_visits
            return self.insufficient(
                f"the value-events baseline has {cell.sample_count} sample(s), but they come "
                "from too few separate weekly visits of this weekday and hour: a baseline "
                f"needs the same hour from at least {visits} different "
                f"week{'' if visits == 1 else 's'}",
                sample_count=cell.sample_count,
            )
        if not cell.is_reliable or cell.sample_count < self.thresholds.baseline_min_samples:
            return self.insufficient(
                f"the value-events baseline has {cell.sample_count} sample(s), below the "
                f"minimum of {self.thresholds.baseline_min_samples}",
                sample_count=cell.sample_count,
            )
        return cell

    def reading(self) -> float | PwVerdict:
        if self.ctx.reading.events is None or self.ctx.reading.measurement is None:
            return self.insufficient("the value's count is unknown for this bucket, not zero")
        if self.decision.scope is WafVerdictScope.NONE:
            return self.insufficient(
                self.decision.reason or "the declared inputs do not support a verdict"
            )
        return float(self.ctx.reading.events)

    def baseline_preconditions(self) -> tuple[PwBaselineCell, float] | PwVerdict:
        if (early := self.watch()) is not None:
            return early
        if (early := self.bucket_guard()) is not None:
            return early
        cell = self.cell()
        if isinstance(cell, PwVerdict):
            return cell
        observed = self.reading()
        if isinstance(observed, PwVerdict):
            return observed
        return cell, observed

    def coverage(self) -> PwVerdict | None:
        assert self.bucket is not None
        coverage = self.bucket.coverage
        if not coverage.reaches(self.thresholds.min_coverage):
            seen = "unknown" if coverage.ratio is None else f"{coverage.ratio:.0%}"
            return self.insufficient(
                f"property coverage was {seen}, below the "
                f"{self.thresholds.min_coverage:.0%} floor"
            )
        return None


def _cell_facts(cell: PwBaselineCell, observed: float, extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "evidence_version": EVIDENCE_VERSION,
        "metric": cell.metric_name,
        "observed_events": observed,
        "median": cell.median,
        "p05": cell.p05,
        "p95": cell.p95,
        "samples": cell.sample_count,
        "watch_version": cell.watch_version,
        "metric_semantics": cell.metric_semantics,
        **extra,
    }


def _expected_band(cell: PwBaselineCell, mad_floor_ratio: float) -> tuple[float, float]:
    spread = max(abs(cell.median) * mad_floor_ratio, 1e-9)
    return cell.p05 if cell.p05 is not None else cell.median - spread, (
        cell.p95 if cell.p95 is not None else cell.median + spread
    )


def _confidence(
    cell: PwBaselineCell, magnitude: float, thresholds: PwThresholds, status: RobustScoreStatus
) -> float:
    reliability = min(1.0, cell.sample_count / 30.0)
    strength = min(1.0, abs(magnitude) / (2 * thresholds.deviation_z))
    value = (0.4 * reliability + 0.6 * strength) * min(1.0, cell.completeness + 0.5)
    if status is not RobustScoreStatus.OK:
        value = min(value, 0.7)
    return round(value, 3)


def _anomalous(
    guard: _Guard,
    cell: PwBaselineCell,
    *,
    observed: float,
    score: float | None,
    magnitude: float,
    status: RobustScoreStatus,
    reason: str,
    facts: dict[str, Any],
    severity: Severity = Severity.MEDIUM,
    interval_start: datetime | None = None,
) -> PwVerdict:
    assert guard.bucket is not None
    start = interval_start or guard.bucket.bucket_start
    end = guard.bucket.bucket_start + timedelta(seconds=guard.bucket.bucket_seconds)
    return guard.verdict(
        AnomalyEvidence(
            anomaly_type=guard.detector_type,
            signal=DetectorSignal.ANOMALOUS,
            severity=severity,
            observed_value=observed,
            expected_value=cell.median,
            baseline_low=cell.p05,
            baseline_high=cell.p95,
            deviation_score=score,
            threshold=guard.thresholds.deviation_z,
            sample_count=cell.sample_count,
            interval_start=start,
            interval_end=end,
            confidence=_confidence(cell, magnitude, guard.thresholds, status),
            reason=reason,
            extra=facts,
        )
    )


def detect_value_spike(ctx: PwDetectionContext) -> PwVerdict:
    guard = _Guard(ctx, PwAnomalyType.PW_VALUE_SPIKE)
    ready = guard.baseline_preconditions()
    if isinstance(ready, PwVerdict):
        return ready
    cell, observed = ready
    thresholds = ctx.thresholds
    expected = cell.median
    delta = observed - expected
    facts = _cell_facts(cell, observed, ctx.extra)
    if observed < thresholds.min_bucket_events:
        return guard.not_proven(
            f"{observed:.0f} events is below the {thresholds.min_bucket_events:.0f} minimum",
            observed=observed,
            expected=expected,
        )
    ratio = rstats.ratio(observed, expected)
    facts["ratio"] = ratio
    if ratio is not None and ratio < thresholds.spike_ratio:
        return guard.not_proven(
            f"observed/expected ratio {ratio:.2f} has not reached {thresholds.spike_ratio:.2f}",
            observed=observed,
            expected=expected,
        )
    if delta < thresholds.min_absolute_delta_events:
        return guard.not_proven(
            f"increase of {delta:.0f} events is below the "
            f"{thresholds.min_absolute_delta_events:.0f} minimum absolute delta",
            observed=observed,
            expected=expected,
        )
    score, status = rstats.robust_z_with_status(
        observed, cell.median, cell.mad, mad_floor_ratio=thresholds.mad_floor_ratio
    )
    facts.update(robust_z=round(score, 3), robust_score_status=status.value)
    if status is RobustScoreStatus.OK:
        if score < thresholds.deviation_z:
            return guard.not_proven(
                f"robust z={score:.2f} has not reached +{thresholds.deviation_z}",
                observed=observed,
                expected=expected,
            )
        basis = f"robust z={score:.2f} (threshold +{thresholds.deviation_z})"
        magnitude = score
    else:
        _, upper = _expected_band(cell, thresholds.mad_floor_ratio)
        facts["fallback_bound"] = upper
        if observed <= upper:
            return guard.not_proven(
                f"robust score is unavailable (MAD=0) and the value is within p95 {upper:.2f}",
                observed=observed,
                expected=expected,
            )
        basis = f"robust score is unavailable (MAD=0); the value is above p95 {upper:.2f}"
        magnitude = thresholds.deviation_z
    bound = "at least " if guard.decision.scope is WafVerdictScope.INCREASE_ONLY else ""
    return _anomalous(
        guard,
        cell,
        observed=observed,
        score=score,
        magnitude=magnitude,
        status=status,
        severity=Severity.HIGH if ratio is not None and ratio >= 4.0 else Severity.MEDIUM,
        reason=(
            f"{bound}{observed:.0f} events is above the median {expected:.1f} "
            f"(+{delta:.0f}, {ratio:.2f}x); {basis}"
            if ratio is not None
            else f"{bound}{observed:.0f} events rose from a zero median; {basis}"
        ),
        facts=facts,
    )


def detect_value_drop(ctx: PwDetectionContext) -> PwVerdict:
    guard = _Guard(ctx, PwAnomalyType.PW_VALUE_DROP)
    ready = guard.baseline_preconditions()
    if isinstance(ready, PwVerdict):
        return ready
    cell, observed = ready
    thresholds = ctx.thresholds
    if (early := guard.coverage()) is not None:
        return early
    expected = cell.median
    if expected < thresholds.drop_min_median:
        return guard.not_proven(
            f"baseline median {expected:.1f} is below the "
            f"{thresholds.drop_min_median:.1f} traffic floor",
            observed=observed,
            expected=expected,
        )
    ratio = observed / expected
    if ratio > thresholds.drop_ratio:
        return guard.not_proven(
            f"observed/expected ratio {ratio:.2f} is above the "
            f"{thresholds.drop_ratio:.2f} drop line",
            observed=observed,
            expected=expected,
        )
    delta = expected - observed
    if delta < thresholds.min_absolute_delta_events:
        return guard.not_proven(
            f"fall of {delta:.0f} events is below the "
            f"{thresholds.min_absolute_delta_events:.0f} minimum absolute delta",
            observed=observed,
            expected=expected,
        )
    score, status = rstats.robust_z_with_status(
        observed, cell.median, cell.mad, mad_floor_ratio=thresholds.mad_floor_ratio
    )
    facts = _cell_facts(cell, observed, ctx.extra)
    facts.update(ratio=ratio, robust_z=round(score, 3), robust_score_status=status.value)
    if status is RobustScoreStatus.OK:
        if score > -thresholds.deviation_z:
            return guard.not_proven(
                f"robust z={score:.2f} has not reached -{thresholds.deviation_z}",
                observed=observed,
                expected=expected,
            )
        basis = f"robust z={score:.2f} (threshold -{thresholds.deviation_z})"
        magnitude = abs(score)
    else:
        lower, _ = _expected_band(cell, thresholds.mad_floor_ratio)
        facts["fallback_bound"] = lower
        if observed >= lower:
            return guard.not_proven(
                f"robust score is unavailable (MAD=0) and the value is within p05 {lower:.2f}",
                observed=observed,
                expected=expected,
            )
        basis = f"robust score is unavailable (MAD=0); the value is below p05 {lower:.2f}"
        magnitude = thresholds.deviation_z
    return _anomalous(
        guard,
        cell,
        observed=observed,
        score=score,
        magnitude=magnitude,
        status=status,
        reason=(
            f"{observed:.0f} exact events is below the median {expected:.1f} "
            f"(-{delta:.0f}, {ratio:.2f}x); {basis}"
        ),
        facts=facts,
    )


def detect_value_disappeared(ctx: PwDetectionContext) -> PwVerdict:
    guard = _Guard(ctx, PwAnomalyType.PW_VALUE_DISAPPEARED)
    ready = guard.baseline_preconditions()
    if isinstance(ready, PwVerdict):
        return ready
    cell, observed = ready
    thresholds = ctx.thresholds
    if (early := guard.coverage()) is not None:
        return early
    assert ctx.bucket is not None
    active_after = ctx.bucket.bucket_start - timedelta(days=thresholds.value_active_days)
    if ctx.last_seen_at is None:
        return guard.insufficient("when the value was last observed is not known")
    if ctx.last_seen_at < active_after:
        return guard.not_proven(
            f"the value was not observed within the last {thresholds.value_active_days} days",
            observed=observed,
            expected=cell.median,
        )
    if cell.median < thresholds.disappear_min_median:
        return guard.not_proven(
            f"baseline median {cell.median:.1f} is below the "
            f"{thresholds.disappear_min_median:.1f} disappearance floor",
            observed=observed,
            expected=cell.median,
        )
    if observed > 0:
        return guard.not_proven(
            f"the value carried {observed:.0f} exact events in this bucket",
            observed=observed,
            expected=cell.median,
        )

    points = {point.bucket.bucket_start: point for point in ctx.recent}
    points.setdefault(ctx.bucket.bucket_start, PwReadingPoint(ctx.bucket, ctx.reading))
    expected_start = ctx.bucket.bucket_start
    streak = 0
    first = expected_start
    for _ in range(thresholds.disappear_buckets):
        point = points.get(expected_start)
        if point is None:
            return guard.insufficient(
                f"the bucket at {expected_start.isoformat()} inside the disappearance window "
                "was not supplied: its count is not zero"
            )
        if point.bucket.scope_changed:
            return guard.insufficient("membership changed inside the disappearance window")
        if point.reading.events is None or point.reading.measurement is not WafMeasurement.EXACT:
            return guard.insufficient(
                f"the value count at {expected_start.isoformat()} is unknown, not zero"
            )
        if not point.bucket.coverage.reaches(thresholds.min_coverage):
            return guard.insufficient(
                f"property coverage at {expected_start.isoformat()} was below the floor"
            )
        if point.reading.events != 0:
            break
        streak += 1
        first = expected_start
        expected_start -= timedelta(seconds=ctx.bucket.bucket_seconds)
    if streak < thresholds.disappear_buckets:
        return guard.not_proven(
            f"the value has {streak} consecutive measured-zero bucket(s), below the "
            f"required {thresholds.disappear_buckets}",
            observed=0.0,
            expected=cell.median,
        )
    facts = _cell_facts(cell, 0.0, ctx.extra)
    facts["consecutive_measured_zero_buckets"] = streak
    return _anomalous(
        guard,
        cell,
        observed=0.0,
        score=None,
        magnitude=thresholds.deviation_z,
        status=RobustScoreStatus.OK,
        reason=(
            f"the value had zero parsed events for {streak} consecutive exact buckets; "
            f"the seasonal median is {cell.median:.1f} events"
        ),
        facts=facts,
        interval_start=first,
    )


def _novelty_common(
    guard: _Guard, *, require_declared_scope: bool = True
) -> PwNoveltyFacts | PwVerdict:
    if (early := guard.watch()) is not None:
        return early
    if (early := guard.bucket_guard()) is not None:
        return early
    novelty = guard.ctx.novelty
    if novelty is None:
        return guard.insufficient("the value's first appearance was not established")
    if novelty.established_at_hand_off is not None:
        return guard.healthy(
            f"established value: first seen {novelty.first_seen_at.isoformat()} in the "
            "history read before the watch went live at "
            f"{novelty.established_at_hand_off.isoformat()}"
        )
    assert guard.bucket is not None
    thresholds = guard.thresholds
    window_end = novelty.first_seen_at + timedelta(
        seconds=thresholds.new_value_window_buckets * guard.bucket.bucket_seconds
    )
    if guard.bucket.bucket_start >= window_end:
        return guard.healthy(
            f"established value: first admitted {novelty.first_seen_at.isoformat()}, outside "
            f"the {thresholds.new_value_window_buckets}-bucket novelty window"
        )
    if novelty.pinned:
        return guard.healthy("pinned values are operator-named and are never classified as new")
    if novelty.watch_first_collected_at is None:
        return guard.insufficient("learning: the watch's first collected bucket is unknown")
    learned_at = novelty.watch_first_collected_at + timedelta(
        days=thresholds.new_value_history_days
    )
    if novelty.first_seen_at < learned_at:
        elapsed = max(0, (guard.bucket.bucket_start - novelty.watch_first_collected_at).days)
        return guard.insufficient(
            f"learning: day {min(elapsed + 1, thresholds.new_value_history_days)} of "
            f"{thresholds.new_value_history_days}"
        )
    if novelty.membership_changed:
        return guard.insufficient("membership changed inside the first-appearance grace window")
    if not novelty.history.complete:
        return guard.insufficient(
            "novelty cannot be established: "
            + (novelty.history.reason or guard.decision.reason or "the history is incomplete")
        )
    if require_declared_scope and guard.decision.scope is WafVerdictScope.NONE:
        return guard.insufficient(
            guard.decision.reason or "the declared novelty inputs do not support a verdict"
        )
    appearance = novelty.appearance_bucket
    if appearance.admission_truncated or appearance.rejected_over_quota > 0:
        return guard.insufficient(
            "the first-appearance bucket had truncated admission: the value appeared in a "
            "flood and is not individually new"
        )
    return novelty


def _materiality(
    guard: _Guard, novelty: PwNoveltyFacts, *, claim: str, classification: str
) -> PwVerdict:
    thresholds = guard.thresholds
    events = novelty.events_since_first_seen
    buckets = novelty.buckets_with_traffic
    lower_bound = novelty.materiality_measurement is WafMeasurement.LOWER_BOUND
    if events < thresholds.new_value_min_events or buckets < thresholds.new_value_min_buckets:
        reason = (
            f"{events} event(s) in {buckets} bucket(s) since first appearance has not reached "
            f"{thresholds.new_value_min_events} events in "
            f"{thresholds.new_value_min_buckets} buckets"
        )
        if lower_bound:
            return guard.insufficient(f"{reason}; the materiality counts are lower bounds")
        return guard.healthy(reason, observed=float(events), expected=0.0)
    assert guard.bucket is not None
    bound = "at least " if lower_bound else ""
    facts = {
        "evidence_version": EVIDENCE_VERSION,
        "classification": classification,
        "history_start": novelty.history_start.isoformat(),
        "history_end": novelty.history_end.isoformat(),
        "history_buckets_verified": novelty.history_buckets,
        "events_since_first_seen": events,
        "buckets_with_traffic": buckets,
        "measurement": novelty.materiality_measurement.value,
        "claim": claim,
        **guard.ctx.extra,
    }
    return guard.verdict(
        AnomalyEvidence(
            anomaly_type=guard.detector_type,
            signal=DetectorSignal.ANOMALOUS,
            severity=Severity.MEDIUM,
            observed_value=float(events),
            expected_value=0.0,
            baseline_low=None,
            baseline_high=None,
            deviation_score=None,
            threshold=float(thresholds.new_value_min_events),
            sample_count=novelty.history_buckets,
            interval_start=guard.bucket.bucket_start,
            interval_end=guard.bucket.bucket_start
            + timedelta(seconds=guard.bucket.bucket_seconds),
            confidence=0.8,
            reason=(
                f"{bound}{events} events across {buckets} buckets since first appearance; "
                f"{claim}"
            ),
            extra=facts,
        )
    )


def detect_new_value(ctx: PwDetectionContext) -> PwVerdict:
    guard = _Guard(ctx, PwAnomalyType.PW_NEW_VALUE)
    ready = _novelty_common(guard)
    if isinstance(ready, PwVerdict):
        return ready
    novelty = ready
    if (
        novelty.property_count == 2
        and len(novelty.components) == 2
        and all(component.known for component in novelty.components)
    ):
        return guard.healthy(
            "both tuple components were tracked before: this is classified only as a "
            "new combination"
        )
    claim = (
        f"the value stayed below {ctx.thresholds.admission_min_events} events in every bucket "
        "of the verified history"
    )
    return _materiality(guard, novelty, claim=claim, classification="PW_NEW_VALUE")


def detect_new_combination(ctx: PwDetectionContext) -> PwVerdict:
    guard = _Guard(ctx, PwAnomalyType.PW_NEW_COMBINATION)
    if (early := guard.watch()) is not None:
        return early
    if (early := guard.bucket_guard()) is not None:
        return early
    novelty = ctx.novelty
    if novelty is not None and novelty.property_count != 2:
        return guard.insufficient("a one-property watch has nothing to combine")
    ready = _novelty_common(guard, require_declared_scope=False)
    if isinstance(ready, PwVerdict):
        return ready
    novelty = ready
    if len(novelty.components) != 2:
        return guard.insufficient("the tuple's two component histories were not supplied")
    unknown = [component for component in novelty.components if not component.known]
    if unknown:
        below = any(component.seen_below_admission for component in unknown)
        suffix = " (seen only below the admission threshold)" if below else ""
        return guard.healthy(
            "at least one component was not previously tracked"
            f"{suffix}: this is classified only as a new value"
        )
    if guard.decision.scope is WafVerdictScope.NONE:
        return guard.insufficient(
            guard.decision.reason or "the declared combination inputs do not support a verdict"
        )
    claim = "both components were tracked before, but never together in the verified history"
    return _materiality(
        guard, novelty, claim=claim, classification="PW_NEW_COMBINATION"
    )


PW_DETECTOR_FUNCTIONS = {
    PwAnomalyType.PW_VALUE_SPIKE: detect_value_spike,
    PwAnomalyType.PW_VALUE_DROP: detect_value_drop,
    PwAnomalyType.PW_VALUE_DISAPPEARED: detect_value_disappeared,
    PwAnomalyType.PW_NEW_VALUE: detect_new_value,
    PwAnomalyType.PW_NEW_COMBINATION: detect_new_combination,
}
