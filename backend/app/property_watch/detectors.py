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
from app.i18n.messages import MessageRef, render
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
from app.property_watch.history import PwHistoryFacts
from app.property_watch.inputs import PwObservation
from app.waf.inputs import HistoryFacts
from app.waf.measurement import ScopeDecision, WafVerdictScope

EVIDENCE_VERSION = 1

#: A sentence as a detector holds it: a registry reference, or the English text of a sentence
#: that has no code (the scope decision's own wording, shared with WAF and IPS).
Reason = MessageRef | str


def _ref(name: str, **params: object) -> MessageRef:
    return MessageRef(f"pw.detector.{name}", params)


def _split(reason: Reason) -> tuple[str, MessageRef | None]:
    if isinstance(reason, MessageRef):
        return render(reason), reason
    return reason, None


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
    #: Of `history_buckets`, those read from QRadar's past, and the floor the reading proved.
    history_buckets_from_history: int = 0
    history_floor: datetime | None = None


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

    def insufficient(self, reason: Reason, *, sample_count: int = 0) -> PwVerdict:
        start, end = self._interval()
        text, message = _split(reason)
        return self.verdict(
            insufficient(
                self.detector_type,
                interval_start=start,
                interval_end=end,
                reason=text,
                sample_count=sample_count,
                reason_message=message,
            )
        )

    def healthy(
        self, reason: Reason, *, observed: float | None = None, expected: float | None = None
    ) -> PwVerdict:
        start, end = self._interval()
        text, message = _split(reason)
        return self.verdict(
            healthy(
                self.detector_type,
                interval_start=start,
                interval_end=end,
                observed=observed,
                expected=expected,
                reason=text,
                reason_message=message,
            )
        )

    def not_proven(self, reason: MessageRef, *, observed: float, expected: float) -> PwVerdict:
        if self.decision.scope is WafVerdictScope.INCREASE_ONLY:
            return self.insufficient(
                _ref(
                    "not_proven_lower_bound",
                    reason=reason,
                    scope=self.decision.reason or "an input is a lower bound",
                )
            )
        return self.healthy(reason, observed=observed, expected=expected)

    def scope_reason(self, fallback: str) -> Reason:
        """The scope decision's wording (uncoded: shared with WAF and IPS), else `fallback`."""
        return self.decision.reason or _ref(fallback)

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
            return self.insufficient(_ref("no_status"))
        if self.ctx.watch_status in blocked:
            return self.insufficient(_ref("watch_blocked", status=self.ctx.watch_status.value))
        return None

    def bucket_guard(self) -> PwVerdict | None:
        if self.bucket is None or self.ctx.reading.kind is PwReadingKind.NO_BUCKET:
            return self.insufficient(_ref("bucket_missing"))
        if self.bucket.values_measurement is PwBucketMeasurement.NOT_COLLECTED:
            return self.insufficient(_ref("bucket_not_collected"))
        if self.bucket.scope_changed:
            return self.insufficient(_ref("bucket_membership"))
        if self.bucket.property_not_parsed:
            return self.insufficient(_ref("bucket_not_parsed"))
        return None

    def cell(self) -> PwBaselineCell | PwVerdict:
        cell = self.ctx.baseline
        if cell is None:
            return self.insufficient(_ref("no_baseline"))
        if cell.watch_version != self.bucket.watch_version:  # type: ignore[union-attr]
            return self.insufficient(
                _ref(
                    "baseline_version",
                    baseline=cell.watch_version,
                    bucket=self.bucket.watch_version,  # type: ignore[union-attr]
                )
            )
        if (
            not cell.is_reliable
            and cell.sample_count >= self.thresholds.baseline_min_samples
        ):
            visits = self.thresholds.baseline_min_visits
            return self.insufficient(
                _ref("baseline_few_visits_one", samples=cell.sample_count)
                if visits == 1
                else _ref("baseline_few_visits", samples=cell.sample_count, visits=visits),
                sample_count=cell.sample_count,
            )
        if not cell.is_reliable or cell.sample_count < self.thresholds.baseline_min_samples:
            return self.insufficient(
                _ref(
                    "baseline_samples",
                    samples=cell.sample_count,
                    minimum=self.thresholds.baseline_min_samples,
                ),
                sample_count=cell.sample_count,
            )
        return cell

    def reading(self) -> float | PwVerdict:
        if self.ctx.reading.events is None or self.ctx.reading.measurement is None:
            return self.insufficient(_ref("count_unknown"))
        if self.decision.scope is WafVerdictScope.NONE:
            return self.insufficient(self.scope_reason("inputs_unsupported"))
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
            if coverage.ratio is None:
                return self.insufficient(
                    _ref("coverage_unknown", floor=self.thresholds.min_coverage)
                )
            return self.insufficient(
                _ref(
                    "coverage_below",
                    seen=coverage.ratio,
                    floor=self.thresholds.min_coverage,
                )
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
    reason: MessageRef,
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
            reason=render(reason),
            extra=facts,
            reason_message=reason,
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
            _ref("spike_min_events", observed=observed, minimum=thresholds.min_bucket_events),
            observed=observed,
            expected=expected,
        )
    ratio = rstats.ratio(observed, expected)
    facts["ratio"] = ratio
    if ratio is not None and ratio < thresholds.spike_ratio:
        return guard.not_proven(
            _ref("ratio_not_reached", ratio=ratio, threshold=thresholds.spike_ratio),
            observed=observed,
            expected=expected,
        )
    if delta < thresholds.min_absolute_delta_events:
        return guard.not_proven(
            _ref(
                "spike_delta_below_min",
                delta=delta,
                minimum=thresholds.min_absolute_delta_events,
            ),
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
                _ref("z_not_reached_high", score=score, threshold=thresholds.deviation_z),
                observed=observed,
                expected=expected,
            )
        basis = _ref("basis_z_high", score=score, threshold=thresholds.deviation_z)
        magnitude = score
    else:
        _, upper = _expected_band(cell, thresholds.mad_floor_ratio)
        facts["fallback_bound"] = upper
        if observed <= upper:
            return guard.not_proven(
                _ref("mad_within_p95", bound=upper),
                observed=observed,
                expected=expected,
            )
        basis = _ref("basis_above_p95", bound=upper)
        magnitude = thresholds.deviation_z
    lower = guard.decision.scope is WafVerdictScope.INCREASE_ONLY
    if ratio is not None:
        fired = _ref(
            "spike_fired_lower" if lower else "spike_fired",
            observed=observed,
            median=expected,
            delta=delta,
            ratio=ratio,
            basis=basis,
        )
    else:
        fired = _ref(
            "spike_zero_median_lower" if lower else "spike_zero_median",
            observed=observed,
            basis=basis,
        )
    return _anomalous(
        guard,
        cell,
        observed=observed,
        score=score,
        magnitude=magnitude,
        status=status,
        severity=Severity.HIGH if ratio is not None and ratio >= 4.0 else Severity.MEDIUM,
        reason=fired,
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
            _ref("drop_median_floor", median=expected, floor=thresholds.drop_min_median),
            observed=observed,
            expected=expected,
        )
    ratio = observed / expected
    if ratio > thresholds.drop_ratio:
        return guard.not_proven(
            _ref("drop_ratio_above", ratio=ratio, line=thresholds.drop_ratio),
            observed=observed,
            expected=expected,
        )
    delta = expected - observed
    if delta < thresholds.min_absolute_delta_events:
        return guard.not_proven(
            _ref(
                "drop_delta_below_min",
                delta=delta,
                minimum=thresholds.min_absolute_delta_events,
            ),
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
                _ref("z_not_reached_low", score=score, threshold=thresholds.deviation_z),
                observed=observed,
                expected=expected,
            )
        basis = _ref("basis_z_low", score=score, threshold=thresholds.deviation_z)
        magnitude = abs(score)
    else:
        lower, _ = _expected_band(cell, thresholds.mad_floor_ratio)
        facts["fallback_bound"] = lower
        if observed >= lower:
            return guard.not_proven(
                _ref("mad_within_p05", bound=lower),
                observed=observed,
                expected=expected,
            )
        basis = _ref("basis_below_p05", bound=lower)
        magnitude = thresholds.deviation_z
    return _anomalous(
        guard,
        cell,
        observed=observed,
        score=score,
        magnitude=magnitude,
        status=status,
        reason=_ref(
            "drop_fired",
            observed=observed,
            median=expected,
            delta=delta,
            ratio=ratio,
            basis=basis,
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
        return guard.insufficient(_ref("last_seen_unknown"))
    if ctx.last_seen_at < active_after:
        return guard.not_proven(
            _ref("not_active", days=thresholds.value_active_days),
            observed=observed,
            expected=cell.median,
        )
    if cell.median < thresholds.disappear_min_median:
        return guard.not_proven(
            _ref(
                "disappear_median_floor",
                median=cell.median,
                floor=thresholds.disappear_min_median,
            ),
            observed=observed,
            expected=cell.median,
        )
    if observed > 0:
        return guard.not_proven(
            _ref("observed_positive", observed=observed),
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
            return guard.insufficient(_ref("window_bucket_missing", at=expected_start))
        if point.bucket.scope_changed:
            return guard.insufficient(_ref("window_membership"))
        if point.reading.events is None or point.reading.measurement is not WafMeasurement.EXACT:
            return guard.insufficient(_ref("window_count_unknown", at=expected_start))
        if not point.bucket.coverage.reaches(thresholds.min_coverage):
            return guard.insufficient(_ref("window_coverage", at=expected_start))
        if point.reading.events != 0:
            break
        streak += 1
        first = expected_start
        expected_start -= timedelta(seconds=ctx.bucket.bucket_seconds)
    if streak < thresholds.disappear_buckets:
        return guard.not_proven(
            _ref("streak_short", streak=streak, required=thresholds.disappear_buckets),
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
        reason=_ref("disappeared_fired", streak=streak, median=cell.median),
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
        return guard.insufficient(_ref("novelty_unestablished"))
    if novelty.established_at_hand_off is not None:
        return guard.healthy(
            _ref(
                "established_hand_off",
                first_seen=novelty.first_seen_at,
                hand_off=novelty.established_at_hand_off,
            )
        )
    assert guard.bucket is not None
    thresholds = guard.thresholds
    window_end = novelty.first_seen_at + timedelta(
        seconds=thresholds.new_value_window_buckets * guard.bucket.bucket_seconds
    )
    if guard.bucket.bucket_start >= window_end:
        return guard.healthy(
            _ref(
                "established_outside_window",
                first_seen=novelty.first_seen_at,
                buckets=thresholds.new_value_window_buckets,
            )
        )
    if novelty.pinned:
        return guard.healthy(_ref("pinned_never_new"))
    if novelty.watch_first_collected_at is None:
        return guard.insufficient(_ref("learning_unknown"))
    learned_at = novelty.watch_first_collected_at + timedelta(
        days=thresholds.new_value_history_days
    )
    if novelty.first_seen_at < learned_at:
        elapsed = max(0, (guard.bucket.bucket_start - novelty.watch_first_collected_at).days)
        return guard.insufficient(
            _ref(
                "learning_day",
                day=min(elapsed + 1, thresholds.new_value_history_days),
                days=thresholds.new_value_history_days,
            )
        )
    if novelty.membership_changed:
        return guard.insufficient(_ref("grace_membership"))
    if not novelty.history.complete:
        return guard.insufficient(
            _ref("history_incomplete", reason=_history_reason(guard, novelty))
        )
    if require_declared_scope and guard.decision.scope is WafVerdictScope.NONE:
        return guard.insufficient(guard.scope_reason("novelty_inputs_unsupported"))
    appearance = novelty.appearance_bucket
    if appearance.admission_truncated or appearance.rejected_over_quota > 0:
        return guard.insufficient(_ref("admission_truncated"))
    return novelty


def _history_reason(guard: _Guard, novelty: PwNoveltyFacts) -> MessageRef | str:
    """Why the verified history is incomplete: the verifier's reference, else its text."""
    history = novelty.history
    if isinstance(history, PwHistoryFacts) and history.message is not None:
        return history.message
    return (
        history.reason
        or guard.decision.reason
        or MessageRef("pw.history_check.incomplete")
    )


def _materiality(
    guard: _Guard, novelty: PwNoveltyFacts, *, claim: MessageRef, classification: str
) -> PwVerdict:
    thresholds = guard.thresholds
    events = novelty.events_since_first_seen
    buckets = novelty.buckets_with_traffic
    lower_bound = novelty.materiality_measurement is WafMeasurement.LOWER_BOUND
    if events < thresholds.new_value_min_events or buckets < thresholds.new_value_min_buckets:
        short = _ref(
            "materiality_short",
            events=events,
            buckets=buckets,
            min_events=thresholds.new_value_min_events,
            min_buckets=thresholds.new_value_min_buckets,
        )
        if lower_bound:
            return guard.insufficient(_ref("materiality_lower_bound", reason=short))
        return guard.healthy(short, observed=float(events), expected=0.0)
    assert guard.bucket is not None
    claim_wire = claim.to_wire()
    fired = _ref(
        "new_fired_lower" if lower_bound else "new_fired",
        events=events,
        buckets=buckets,
        claim=claim,
    )
    facts = {
        "evidence_version": EVIDENCE_VERSION,
        "classification": classification,
        "history_start": novelty.history_start.isoformat(),
        "history_end": novelty.history_end.isoformat(),
        "history_buckets_verified": novelty.history_buckets,
        "history_buckets_from_history": novelty.history_buckets_from_history,
        "history_floor": (
            novelty.history_floor.isoformat() if novelty.history_floor is not None else None
        ),
        "events_since_first_seen": events,
        "buckets_with_traffic": buckets,
        "measurement": novelty.materiality_measurement.value,
        "claim": render(claim),
        "claim_code": claim_wire["code"],
        "claim_params": claim_wire["params"],
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
            reason=render(fired),
            extra=facts,
            reason_message=fired,
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
        return guard.healthy(_ref("new_value_is_combination"))
    claim = _ref("claim_new_value", min_events=ctx.thresholds.admission_min_events)
    return _materiality(guard, novelty, claim=claim, classification="PW_NEW_VALUE")


def detect_new_combination(ctx: PwDetectionContext) -> PwVerdict:
    guard = _Guard(ctx, PwAnomalyType.PW_NEW_COMBINATION)
    if (early := guard.watch()) is not None:
        return early
    if (early := guard.bucket_guard()) is not None:
        return early
    novelty = ctx.novelty
    if novelty is not None and novelty.property_count != 2:
        return guard.insufficient(_ref("one_property"))
    ready = _novelty_common(guard, require_declared_scope=False)
    if isinstance(ready, PwVerdict):
        return ready
    novelty = ready
    if len(novelty.components) != 2:
        return guard.insufficient(_ref("components_missing"))
    unknown = [component for component in novelty.components if not component.known]
    if unknown:
        below = any(component.seen_below_admission for component in unknown)
        return guard.healthy(
            _ref("component_untracked_below_admission" if below else "component_untracked")
        )
    if guard.decision.scope is WafVerdictScope.NONE:
        return guard.insufficient(guard.scope_reason("combination_inputs_unsupported"))
    claim = _ref("claim_new_combination")
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
