"""Pure custom-rule evaluation (design §F.5).

The fold decides sustain, recovery and honesty from readings the loaders already
measured. Verdict scope comes from the catalog entry, never from a special case
in here (AGENTS.md rule 8). Nothing in this module touches the database, and
nothing it returns carries a raw event value (rule 3).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.custom_rules.alerts import CustomRuleAlertEvent
from app.custom_rules.catalog import CUSTOM_RULE_METRICS, MeasurementRule, catalog_entry
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    CustomRuleMetric,
    WafMeasurement,
)
from app.waf.measurement import (
    WafMeasurementRequirement,
    WafVerdictScope,
    verdict_scope,
)


class RuleStatus(StrEnum):
    """The rule's last evaluation outcome. Each value fits ``String(16)``.

    ``INSUFFICIENT_DATA`` is a bucket outcome, counted on the breach, and is
    not a rule status: it does not fit the column, and it is not one of these.
    ``NO_VERDICT`` is a bucket that was judged and proved nothing. ``STALE``
    is the whole kind: collection has stopped, so no bucket was judged.
    """

    NOT_ENABLED = "NOT_ENABLED"
    SUBJECT_RETIRED = "SUBJECT_RETIRED"
    DISABLED = "DISABLED"
    NOT_COLLECTED = "NOT_COLLECTED"
    OPEN = "OPEN"
    RECOVERING = "RECOVERING"
    OK = "OK"
    NO_VERDICT = "NO_VERDICT"
    STALE = "STALE"


#: A family's run is due ``cadence + settle`` after the previous one, and the
#: scheduler that claims the slot ticks every 60 s. That is the catalog's
#: lateness tolerance (``LATE_TOLERANCE_SECONDS`` in the collection widget).
COLLECTION_LATE_TOLERANCE_SECONDS = 60


_REQUIREMENT = {
    MeasurementRule.EXACT_ONLY: WafMeasurementRequirement.EXACT,
    MeasurementRule.LOWER_BOUND_PROVES_INCREASE: (
        WafMeasurementRequirement.LOWER_BOUND_PROVES_INCREASE
    ),
}

_INCREASE_OPERATORS = frozenset({"GT", "GE"})


def latest_closed_bucket(now: datetime, interval_seconds: int) -> datetime:
    """Start of the latest bucket that has fully closed by ``now``.

    Integer epoch arithmetic, UTC. At an exact boundary the bucket that starts
    then is in progress, so the result is one interval earlier: 12:00 with a
    300 s interval closes 11:55, and 12:07 closes 12:00.
    """
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    epoch = int(now.timestamp())
    closed = (epoch // interval_seconds) * interval_seconds - interval_seconds
    return datetime.fromtimestamp(closed, tz=UTC)


def stale_bound_seconds(cadence_seconds: int, settle_seconds: int, bucket_seconds: int) -> int:
    """How far ``closed`` may sit past the watermark before the kind is stale.

    A family searches once per cadence (D-7, up to 8 h). The slot is not due
    until ``cadence + settle`` after the previous one, and the claiming beat
    can be one minute late. ``closed`` and the watermark are bucket starts, so
    the bound also includes one bucket: the documented detection delay is
    ``cadence + bucket + settle`` (design F.1.3). A lag of exactly this many
    seconds is still a collection that can legitimately be in flight. One
    second more means nothing new has been stored, and a verdict would be
    silence.
    """
    if cadence_seconds <= 0 or bucket_seconds <= 0 or settle_seconds < 0:
        raise ValueError("cadence and bucket must be positive and settle must be >= 0")
    return (
        cadence_seconds
        + bucket_seconds
        + settle_seconds
        + COLLECTION_LATE_TOLERANCE_SECONDS
    )


def collection_silence(
    *,
    watermark: datetime,
    closed: datetime,
    now: datetime,
    bound_seconds: int,
) -> str | None:
    """The stale reason when ``closed`` lags the watermark by more than the bound.

    ``None`` means the gap is still inside the time collection can take, and
    the rule's previous status stands. The lag is ``closed - watermark``,
    both bucket starts. The age in the reason is the wall-clock age of that
    watermark, which is the last bucket the kind was judged through.
    """
    for moment in (watermark, closed, now):
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
    lag = int((closed - watermark).total_seconds())
    if lag <= bound_seconds:
        return None
    message = collection_silence_message(
        watermark=watermark,
        closed=closed,
        now=now,
        bound_seconds=bound_seconds,
    )
    return render(message) if message is not None else None


def collection_silence_message(
    *,
    watermark: datetime,
    closed: datetime,
    now: datetime,
    bound_seconds: int,
) -> MessageRef | None:
    """Coded stale reason; the string API above remains for existing callers."""
    for moment in (watermark, closed, now):
        if moment.tzinfo is None or moment.utcoffset() is None:
            raise ValueError("timestamps must be timezone-aware")
    if int((closed - watermark).total_seconds()) <= bound_seconds:
        return None
    code = (
        "custom_rule.status.stale.second"
        if watermark.second or watermark.microsecond
        else "custom_rule.status.stale.minute"
    )
    return MessageRef(
        code,
        {
            "bucket": watermark,
            "age": max(0, int((now - watermark).total_seconds())),
        },
    )


def _stale_reason(last_bucket: datetime, now: datetime) -> str:
    stamp = _bucket_stamp(last_bucket)
    age = _format_age(int((now - last_bucket).total_seconds()))
    return f"no bucket collected for this kind since {stamp} ({age})"


def _bucket_stamp(moment: datetime) -> str:
    moment = moment.astimezone(UTC)
    if moment.second or moment.microsecond:
        return moment.strftime("%Y-%m-%d %H:%M:%S UTC")
    return moment.strftime("%Y-%m-%d %H:%M UTC")


def _format_age(seconds: int) -> str:
    total = max(0, seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours} h")
    if minutes:
        parts.append(f"{minutes} min")
    if secs or not parts:
        parts.append(f"{secs} s")
    return " ".join(parts)


def walk_buckets(
    observed: list[datetime] | tuple[datetime, ...],
    *,
    watermark: datetime,
    interval_seconds: int,
    limit: int,
    closed: datetime,
) -> list[datetime]:
    """Bucket starts to judge: observed points after the watermark, up to ``closed``.

    Aligned holes between two observed starts are inserted as unmeasured grid
    points, so a streak cannot skip a gap. The walk stops at ``limit`` even
    mid-gap; the next run continues from the new watermark. It does not invent
    an empty grid up to ``now`` — a bucket nobody has collected is not judged.
    """
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be positive")
    if limit <= 0:
        return []
    points = sorted({point for point in observed if watermark < point <= closed})
    if not points:
        return []
    grid: list[datetime] = []
    cursor = watermark
    for point in points:
        delta = round((point - cursor).total_seconds())
        if delta > interval_seconds and delta % interval_seconds == 0:
            steps = delta // interval_seconds
            for step in range(1, steps):
                gap = cursor + timedelta(seconds=step * interval_seconds)
                grid.append(gap)
                if len(grid) >= limit:
                    return grid
        grid.append(point)
        if len(grid) >= limit:
            return grid
        cursor = point
    return grid


@dataclass(frozen=True)
class BucketFacts:
    """One subject in one bucket, as the bulk loader projected it.

    ``present`` is false when the loader had no row. A missing row is not a
    zero: the fold treats it as unmeasured.
    """

    bucket_start: datetime
    present: bool = False
    in_maintenance: bool = False
    event_count: int | None = None
    average_eps: float | None = None
    completeness: str | None = None
    class_events: int | None = None
    class_seconds: int | None = None
    complete_sources: int | None = None
    expected_sources: int | None = None
    request_count: int | None = None
    request_count_measurement: str | None = None
    blocked_count: int | None = None
    action_events: int | None = None
    unmapped_action_count: int | None = None
    action_measurement: str | None = None
    server_error_count: int | None = None
    client_error_count: int | None = None
    response_code_events: int | None = None
    unclassified_response_count: int | None = None
    response_code_measurement: str | None = None
    events: int | None = None
    dimension_measurement: str | None = None
    total_events: int | None = None
    blocked_events: int | None = None
    allowed_events: int | None = None
    unmapped_events: int | None = None


@dataclass(frozen=True)
class InterpretedReading:
    """What one bucket proves for one metric. ``value`` is None when unmeasured."""

    bucket_start: datetime
    value: float | None
    #: ``EXACT``, ``LOWER_BOUND``, or None when the bucket supports no count.
    measurement: str | None
    in_maintenance: bool
    reason: str | None
    reason_message: MessageRef | None = None


def interpret(metric: CustomRuleMetric, facts: BucketFacts) -> InterpretedReading:
    """The reading ``metric`` takes from ``facts``. Scope is applied later, by the fold."""
    reading = _INTERPRETERS[metric](facts)
    if facts.in_maintenance:
        # The collector's decision wins over a live flag the engine may also OR in.
        return InterpretedReading(
            bucket_start=reading.bucket_start,
            value=reading.value,
            measurement=reading.measurement,
            in_maintenance=True,
            reason=render(MessageRef("custom_rule.status.maintenance")),
            reason_message=MessageRef("custom_rule.status.maintenance"),
        )
    return reading


@dataclass
class BreachState:
    """The live breach the fold mutates. A copy, never the caller's object."""

    id: uuid.UUID
    state: str
    first_bucket_start: datetime
    last_bucket_start: datetime
    opened_at: datetime
    recovering_at: datetime | None
    resolved_at: datetime | None
    worst_value: float | None
    worst_measurement: str | None
    last_value: float | None
    last_measurement: str | None
    unmeasured_buckets: int
    subject_label: str
    details: dict[str, object]
    is_new: bool = False


@dataclass(frozen=True)
class FoldInput:
    """One rule and the buckets the engine wants judged, oldest first or not."""

    rule_id: uuid.UUID
    rule_name: str
    severity: str
    metric: CustomRuleMetric
    operator: str
    threshold_value: float
    sustain_buckets: int
    recovery_buckets: int
    consecutive_breaching: int
    consecutive_under: int
    last_evaluated_bucket_start: datetime | None
    previous_status: str | None
    previous_reason: str | None
    subject_kind: str
    subject_label: str
    platform_enabled: bool
    enabled: bool
    retired: bool
    retired_reason: str | None
    family_collected: bool
    family_reason: str | None
    breach: BreachState | None
    readings: tuple[InterpretedReading, ...]
    now: datetime
    #: Set when the kind's collection has stopped. Readings are not judged.
    collection_stale_reason: str | None = None
    previous_reason_message: MessageRef | None = None
    retired_reason_message: MessageRef | None = None
    family_reason_message: MessageRef | None = None
    collection_stale_reason_message: MessageRef | None = None


@dataclass(frozen=True)
class FoldOutput:
    """The rule's new hysteresis, the breach to keep, and the alert-seam events."""

    status: str | None
    status_reason: str | None
    status_reason_message: MessageRef | None
    consecutive_breaching: int
    consecutive_under: int
    last_evaluated_bucket_start: datetime | None
    #: The breach still live after this run. None when there isn't one.
    breach: BreachState | None
    #: Set when this run resolved a breach. The same object the engine inserts or updates.
    resolved: BreachState | None
    events: tuple[CustomRuleAlertEvent, ...]
    judged: int
    #: Idle run: the engine must not overwrite a previous status with a fake one.
    preserve_status: bool


def fold_rule(spec: FoldInput) -> FoldOutput:
    """Sustain, recovery and the §F.5 honesty rules for one rule.

    Re-processing a bucket at or before ``last_evaluated_bucket_start`` is a
    no-op. An unmeasured bucket, and a maintenance bucket, pause both counters:
    they do not open, refresh or resolve (§F.5 rule 1, §F.6).
    """
    return _Fold(spec).run()


class _Fold:
    def __init__(self, spec: FoldInput) -> None:
        self.spec = spec
        self.breach = _copy_breach(spec.breach)
        self.resolved: BreachState | None = None
        self.events: list[CustomRuleAlertEvent] = []
        self.breaching = spec.consecutive_breaching
        self.under = spec.consecutive_under
        self.last_evaluated = spec.last_evaluated_bucket_start
        self.judged = 0
        self.last_kind: str | None = None
        self.last_reason: str | None = None
        self.last_reason_message: MessageRef | None = None
        self._requirement = WafMeasurementRequirement.EXACT
        self._unit = ""

    def run(self) -> FoldOutput:
        stopped = self._halt()
        if stopped is not None:
            return stopped
        if self.spec.collection_stale_reason is not None:
            # No bucket was judged, so neither counter moves and a live breach
            # cannot resolve. Exact unders are the only way out (§F.5 rules 1
            # and 3); a stopped collector is not an under.
            return self._output(
                RuleStatus.STALE.value,
                self.spec.collection_stale_reason,
                self.spec.collection_stale_reason_message,
                False,
            )
        entry = catalog_entry(self.spec.metric)
        self._requirement = _REQUIREMENT[entry.measurement]
        self._unit = entry.unit
        for reading in sorted(self.spec.readings, key=lambda item: item.bucket_start):
            self._consume(reading)
        return self._finish()

    def _halt(self) -> FoldOutput | None:
        # A rule that is not allowed to judge keeps its counters and its breach.
        # Readings passed in are ignored, including ones that would recover.
        spec = self.spec
        if not spec.platform_enabled:
            return self._coded_output(
                RuleStatus.NOT_ENABLED.value, MessageRef("custom_rule.status.platform_off")
            )
        if not spec.enabled:
            return self._coded_output(
                RuleStatus.DISABLED.value, MessageRef("custom_rule.status.disabled")
            )
        if spec.retired:
            message = spec.retired_reason_message or MessageRef(
                "custom_rule.status.subject_retired"
            )
            return self._output(
                RuleStatus.SUBJECT_RETIRED.value,
                spec.retired_reason or render(message),
                message,
                False,
            )
        if not spec.family_collected:
            message = spec.family_reason_message or MessageRef(
                "custom_rule.status.family_not_collected"
            )
            return self._output(
                RuleStatus.NOT_COLLECTED.value,
                spec.family_reason or render(message),
                message,
                False,
            )
        return None

    def _consume(self, reading: InterpretedReading) -> None:
        if self.last_evaluated is not None and reading.bucket_start <= self.last_evaluated:
            return
        self.last_evaluated = reading.bucket_start
        self.judged += 1
        if reading.in_maintenance:
            # §F.5 rule 1, applied to maintenance by §F.6: pause both counters.
            # Do not open, refresh or resolve, and do not count it as unmeasured.
            self.last_kind = "maintenance"
            message = reading.reason_message or MessageRef("custom_rule.status.maintenance")
            self.last_reason = reading.reason or render(message)
            self.last_reason_message = message
            return
        if (
            reading.value is None
            or reading.measurement is None
            or not math.isfinite(reading.value)
        ):
            message = reading.reason_message or MessageRef(
                "custom_rule.status.bucket_not_measured"
            )
            self._unmeasured(reading.reason or render(message), message)
            return
        if not math.isfinite(self.spec.threshold_value):
            # A non-finite threshold compares true against nothing. That must
            # not be read as "under" and used to resolve the breach.
            message = MessageRef("custom_rule.status.threshold_not_finite")
            self._unmeasured(render(message), message)
            return
        measured = _waf_measurement(reading.measurement)
        decision = verdict_scope(self._requirement, measured)
        direction = _breaches(self.spec.operator, reading.value, self.spec.threshold_value)
        if direction is None:
            message = MessageRef(
                "custom_rule.status.operator_unknown",
                {"operator": repr(self.spec.operator)},
            )
            self._unmeasured(render(message), message)
            return
        if decision.scope is WafVerdictScope.NONE:
            if measured is None:
                message = MessageRef("custom_rule.reading.count_semantics_unknown")
            elif self._requirement is WafMeasurementRequirement.EXACT:
                message = MessageRef("custom_rule.reading.exact_required")
            else:
                message = MessageRef("custom_rule.status.no_verdict")
            self._unmeasured(decision.reason or render(message), message)
            return
        if decision.scope is WafVerdictScope.INCREASE_ONLY and (
            self.spec.operator not in _INCREASE_OPERATORS or not direction
        ):
            # A lower bound proves "above" only. It never proves under, and a
            # bound that does not itself cross the line proves nothing.
            if self.spec.operator in _INCREASE_OPERATORS:
                message = MessageRef("custom_rule.status.lower_bound_below_threshold")
            else:
                message = MessageRef("custom_rule.status.lower_bound_cannot_prove_under")
            self._unmeasured(render(message), message)
            return
        if direction:
            self._on_breach(reading)
        else:
            self._on_under(reading)

    def _unmeasured(self, reason: str, reason_message: MessageRef | None = None) -> None:
        # §F.5 rule 1: advance neither counter. Count the bucket only while a
        # breach is already open — an unmeasured reading never opens one.
        if self.breach is not None:
            self.breach.unmeasured_buckets += 1
        self.last_kind = "insufficient"
        self.last_reason = reason
        self.last_reason_message = reason_message

    def _on_breach(self, reading: InterpretedReading) -> None:
        self.breaching += 1
        self.under = 0
        self.last_kind = "breach"
        self.last_reason = None
        self.last_reason_message = None
        if self.breach is None:
            if self.breaching >= self.spec.sustain_buckets:
                opened = self._open(reading)
                self.breach = opened
                self.events.append(self._event("open", opened, reading, None))
            return
        self.breach.state = AnomalyState.OPEN.value
        self.breach.recovering_at = None
        _note_worst(self.spec.operator, self.breach, reading)
        # A later breaching bucket refreshes. The opening bucket emitted ``open``
        # and returns above, so an open is never also a refresh.
        self.events.append(self._event("refresh", self.breach, reading, None))

    def _on_under(self, reading: InterpretedReading) -> None:
        self.breaching = 0
        self.under += 1
        self.last_kind = "under"
        self.last_reason = None
        self.last_reason_message = None
        if self.breach is None:
            return
        _note_last(self.breach, reading)
        if self.under >= self.spec.recovery_buckets:
            self.breach.state = AnomalyState.RESOLVED.value
            self.breach.resolved_at = self.spec.now
            self.events.append(
                self._event(
                    "resolve",
                    self.breach,
                    reading,
                    render(MessageRef("alert.custom_rule.recovered")),
                    MessageRef("alert.custom_rule.recovered"),
                )
            )
            self.resolved = self.breach
            self.breach = None
            self.breaching = 0
            self.under = 0
            return
        # Entering recovery is not an alert. The breach stays the observation.
        if self.breach.state != AnomalyState.RECOVERING.value:
            self.breach.state = AnomalyState.RECOVERING.value
        if self.breach.recovering_at is None:
            self.breach.recovering_at = self.spec.now

    def _open(self, reading: InterpretedReading) -> BreachState:
        # The opening bucket is the one on which sustain was met, not an
        # estimated start of the streak. The design stores no streak-start column.
        label = self.spec.subject_label[:255]
        assert reading.value is not None
        return BreachState(
            id=uuid.uuid4(),
            state=AnomalyState.OPEN.value,
            first_bucket_start=reading.bucket_start,
            last_bucket_start=reading.bucket_start,
            opened_at=self.spec.now,
            recovering_at=None,
            resolved_at=None,
            worst_value=reading.value,
            worst_measurement=reading.measurement,
            last_value=reading.value,
            last_measurement=reading.measurement,
            unmeasured_buckets=0,
            subject_label=label,
            details={
                "metric": self.spec.metric.value,
                "unit": self._unit,
                "operator": self.spec.operator,
                "threshold": float(self.spec.threshold_value),
                "subject_kind": self.spec.subject_kind,
                "subject_label": label,
            },
            is_new=True,
        )

    def _event(
        self,
        action: str,
        breach: BreachState,
        reading: InterpretedReading,
        reason: str | None,
        reason_message: MessageRef | None = None,
    ) -> CustomRuleAlertEvent:
        return CustomRuleAlertEvent(
            action=action,
            rule_id=self.spec.rule_id,
            breach_id=breach.id,
            rule_name=self.spec.rule_name,
            severity=self.spec.severity,
            subject_kind=self.spec.subject_kind,
            subject_label=breach.subject_label,
            metric=self.spec.metric.value,
            unit=self._unit,
            operator=self.spec.operator,
            threshold_value=float(self.spec.threshold_value),
            observed_value=reading.value,
            measurement=reading.measurement,
            first_bucket_start=breach.first_bucket_start,
            last_bucket_start=breach.last_bucket_start,
            unmeasured_buckets=breach.unmeasured_buckets,
            reason=reason,
            reason_message=reason_message,
        )

    def _finish(self) -> FoldOutput:
        if self.judged == 0:
            return self._output(
                self.spec.previous_status,
                self.spec.previous_reason,
                self.spec.previous_reason_message,
                True,
            )
        if self.breach is not None:
            reason = (
                self.last_reason
                if self.last_kind in ("insufficient", "maintenance")
                else None
            )
            message = (
                self.last_reason_message
                if self.last_kind in ("insufficient", "maintenance")
                else None
            )
            return self._output(self.breach.state, reason, message, False)
        if self.last_kind in ("insufficient", "maintenance"):
            return self._output(
                RuleStatus.NO_VERDICT.value,
                self.last_reason,
                self.last_reason_message,
                False,
            )
        return self._output(RuleStatus.OK.value, None, None, False)

    def _coded_output(self, status: str, message: MessageRef) -> FoldOutput:
        return self._output(status, render(message), message, False)

    def _output(
        self,
        status: str | None,
        reason: str | None,
        reason_message: MessageRef | None,
        preserve: bool,
    ) -> FoldOutput:
        return FoldOutput(
            status=status,
            status_reason=reason,
            status_reason_message=reason_message,
            consecutive_breaching=self.breaching,
            consecutive_under=self.under,
            last_evaluated_bucket_start=self.last_evaluated,
            breach=self.breach,
            resolved=self.resolved,
            events=tuple(self.events),
            judged=self.judged,
            preserve_status=preserve,
        )


def _copy_breach(breach: BreachState | None) -> BreachState | None:
    if breach is None:
        return None
    return BreachState(
        id=breach.id,
        state=breach.state,
        first_bucket_start=breach.first_bucket_start,
        last_bucket_start=breach.last_bucket_start,
        opened_at=breach.opened_at,
        recovering_at=breach.recovering_at,
        resolved_at=breach.resolved_at,
        worst_value=breach.worst_value,
        worst_measurement=breach.worst_measurement,
        last_value=breach.last_value,
        last_measurement=breach.last_measurement,
        unmeasured_buckets=breach.unmeasured_buckets,
        subject_label=breach.subject_label,
        details=dict(breach.details),
        is_new=breach.is_new,
    )


def _waf_measurement(value: str | None) -> WafMeasurement | None:
    """Map a reading's label onto the enum ``verdict_scope`` compares with ``is``.

    ``COMPLETE`` is a Phase A completeness value that means the count is exact.
    Any other label is unknown and supports no verdict — it is not passed to
    the enum constructor.
    """
    if value in (WafMeasurement.EXACT.value, BucketCompleteness.COMPLETE.value):
        return WafMeasurement.EXACT
    if value == WafMeasurement.LOWER_BOUND.value:
        return WafMeasurement.LOWER_BOUND
    return None


def _breaches(operator: str, value: float, threshold: float) -> bool | None:
    """Whether ``value`` crosses ``threshold``. None when the operator is unknown.

    Equality is exact. There is no epsilon: a share that is not the threshold
    is not EQ.
    """
    if operator == "GT":
        return value > threshold
    if operator == "GE":
        return value >= threshold
    if operator == "LT":
        return value < threshold
    if operator == "LE":
        return value <= threshold
    if operator == "EQ":
        return value == threshold
    return None


def _note_worst(operator: str, breach: BreachState, reading: InterpretedReading) -> None:
    _note_last(breach, reading)
    candidate = reading.value
    current = breach.worst_value
    if candidate is None or current is None:
        if candidate is not None and current is None:
            breach.worst_value = candidate
            breach.worst_measurement = reading.measurement
        return
    # EQ keeps the first matching value. A later equal reading is not worse.
    increase = operator in _INCREASE_OPERATORS and candidate > current
    decrease = operator in ("LT", "LE") and candidate < current
    if increase or decrease:
        breach.worst_value = candidate
        breach.worst_measurement = reading.measurement


def _note_last(breach: BreachState, reading: InterpretedReading) -> None:
    breach.last_bucket_start = reading.bucket_start
    breach.last_value = reading.value
    breach.last_measurement = reading.measurement


def _absent(facts: BucketFacts) -> InterpretedReading:
    message = MessageRef("custom_rule.reading.subject_missing")
    return InterpretedReading(
        facts.bucket_start, None, None, False, render(message), message
    )


def _none(facts: BucketFacts, message: MessageRef) -> InterpretedReading:
    return InterpretedReading(
        facts.bucket_start, None, None, False, render(message), message
    )


def _measured(facts: BucketFacts, value: float, *, lower: bool) -> InterpretedReading:
    if not math.isfinite(value):
        return _none(facts, MessageRef("custom_rule.reading.not_finite"))
    label = WafMeasurement.LOWER_BOUND.value if lower else WafMeasurement.EXACT.value
    return InterpretedReading(facts.bucket_start, value, label, False, None)


def _phase_a(facts: BucketFacts, value: float | None) -> InterpretedReading:
    """Log-source counts: exact only from a COMPLETE row. No row is not zero."""
    if not facts.present:
        return _absent(facts)
    if facts.completeness == BucketCompleteness.PARTIAL.value:
        return _none(facts, MessageRef("custom_rule.reading.bucket_partial"))
    if facts.completeness != BucketCompleteness.COMPLETE.value or value is None:
        return _none(facts, MessageRef("custom_rule.reading.bucket_not_complete"))
    return _measured(facts, float(value), lower=False)


def _event_count(facts: BucketFacts) -> InterpretedReading:
    return _phase_a(facts, None if facts.event_count is None else float(facts.event_count))


def _average_eps(facts: BucketFacts) -> InterpretedReading:
    return _phase_a(facts, facts.average_eps)


def _fleet(facts: BucketFacts) -> InterpretedReading:
    if not facts.present:
        return _absent(facts)
    expected = facts.expected_sources or 0
    complete = facts.complete_sources or 0
    seconds = facts.class_seconds or 0
    events = facts.class_events or 0
    if expected <= 0:
        return _none(facts, MessageRef("custom_rule.reading.class_no_enabled_source"))
    if complete <= 0 or seconds <= 0:
        return _none(facts, MessageRef("custom_rule.reading.class_no_complete_source"))
    # Missing sources can only add events, so a partial class is a lower bound
    # — including a lower bound of zero.
    return _measured(facts, events / seconds, lower=complete != expected)


def _request_count(facts: BucketFacts) -> InterpretedReading:
    if not facts.present:
        return _absent(facts)
    measurement = facts.request_count_measurement
    if measurement is None:
        return _none(
            facts, MessageRef("custom_rule.reading.request_count_semantics_missing")
        )
    if facts.request_count is None:
        return _none(
            facts, MessageRef("custom_rule.reading.request_count_semantics_missing")
        )
    if measurement == WafMeasurement.EXACT.value:
        return _measured(facts, float(facts.request_count), lower=False)
    if measurement == WafMeasurement.LOWER_BOUND.value:
        return _measured(facts, float(facts.request_count), lower=True)
    return _none(
        facts, MessageRef("custom_rule.reading.request_count_semantics_missing")
    )


def _ratio(
    facts: BucketFacts,
    numerator: int | None,
    denominator: int | None,
    *,
    measurement: str | None,
    remainder: int | None,
    collected_reason: MessageRef,
    lower_reason: MessageRef,
    zero_reason: MessageRef,
) -> InterpretedReading:
    """A share. The denominator must be exact and positive; a remainder lowers the numerator."""
    if not facts.present:
        return _absent(facts)
    if measurement in (None, "NOT_COLLECTED", "FAILED", "REJECTED"):
        return _none(facts, collected_reason)
    if measurement == WafMeasurement.LOWER_BOUND.value:
        return _none(facts, lower_reason)
    if measurement != WafMeasurement.EXACT.value:
        return _none(facts, collected_reason)
    if numerator is None:
        return _none(facts, MessageRef("custom_rule.reading.numerator_missing"))
    if denominator is None or denominator <= 0:
        return _none(facts, zero_reason)
    if remainder is None:
        return _none(facts, MessageRef("custom_rule.reading.unmapped_remainder_missing"))
    return _measured(facts, numerator / denominator, lower=remainder > 0)


def _block_ratio(facts: BucketFacts) -> InterpretedReading:
    return _ratio(
        facts,
        facts.blocked_count,
        facts.action_events,
        measurement=facts.action_measurement,
        remainder=facts.unmapped_action_count,
        collected_reason=MessageRef("custom_rule.reading.action_breakdown_missing"),
        lower_reason=MessageRef("custom_rule.reading.action_denominator_lower_bound"),
        zero_reason=MessageRef("custom_rule.reading.action_denominator_zero"),
    )


def _server_error_ratio(facts: BucketFacts) -> InterpretedReading:
    return _ratio(
        facts,
        facts.server_error_count,
        facts.response_code_events,
        measurement=facts.response_code_measurement,
        remainder=facts.unclassified_response_count,
        collected_reason=MessageRef("custom_rule.reading.response_breakdown_missing"),
        lower_reason=MessageRef("custom_rule.reading.response_denominator_lower_bound"),
        zero_reason=MessageRef("custom_rule.reading.response_denominator_zero"),
    )


def _client_error_ratio(facts: BucketFacts) -> InterpretedReading:
    return _ratio(
        facts,
        facts.client_error_count,
        facts.response_code_events,
        measurement=facts.response_code_measurement,
        remainder=facts.unclassified_response_count,
        collected_reason=MessageRef("custom_rule.reading.response_breakdown_missing"),
        lower_reason=MessageRef("custom_rule.reading.response_denominator_lower_bound"),
        zero_reason=MessageRef("custom_rule.reading.response_denominator_zero"),
    )


def _entity_events(facts: BucketFacts) -> InterpretedReading:
    if not facts.present:
        return _absent(facts)
    measurement = facts.dimension_measurement
    if facts.events is None or measurement is None:
        return _none(facts, MessageRef("custom_rule.reading.dimension_missing"))
    if measurement == WafMeasurement.EXACT.value:
        return _measured(facts, float(facts.events), lower=False)
    if measurement == WafMeasurement.LOWER_BOUND.value:
        return _measured(facts, float(facts.events), lower=True)
    return _none(facts, MessageRef("custom_rule.reading.dimension_missing"))


def _total_events(facts: BucketFacts) -> InterpretedReading:
    """Totals are exact whenever the row exists. Absence is unmeasured, not zero."""
    if not facts.present or facts.total_events is None:
        return _absent(facts)
    return _measured(facts, float(facts.total_events), lower=False)


def _blocked_share(facts: BucketFacts) -> InterpretedReading:
    if not facts.present:
        return _absent(facts)
    blocked = facts.blocked_events
    allowed = facts.allowed_events
    unmapped = facts.unmapped_events
    if (
        facts.action_measurement in (None, "NOT_COLLECTED", "FAILED", "REJECTED")
    ):
        return _none(facts, MessageRef("custom_rule.reading.action_breakdown_missing"))
    if facts.action_measurement == WafMeasurement.LOWER_BOUND.value:
        return _none(
            facts, MessageRef("custom_rule.reading.action_denominator_lower_bound")
        )
    if facts.action_measurement != WafMeasurement.EXACT.value:
        return _none(facts, MessageRef("custom_rule.reading.action_breakdown_missing"))
    if blocked is None or allowed is None or unmapped is None:
        return _none(facts, MessageRef("custom_rule.reading.numerator_missing"))
    denominator = blocked + allowed + unmapped
    if denominator <= 0:
        return _none(facts, MessageRef("custom_rule.reading.action_denominator_zero"))
    return _measured(facts, blocked / denominator, lower=unmapped > 0)


_Interpreter = Callable[[BucketFacts], InterpretedReading]

_INTERPRETERS: dict[CustomRuleMetric, _Interpreter] = {
    CustomRuleMetric.EVENT_COUNT: _event_count,
    CustomRuleMetric.AVERAGE_EPS: _average_eps,
    CustomRuleMetric.EPS: _fleet,
    CustomRuleMetric.REQUEST_COUNT: _request_count,
    CustomRuleMetric.BLOCK_RATIO: _block_ratio,
    CustomRuleMetric.SERVER_ERROR_RATIO: _server_error_ratio,
    CustomRuleMetric.CLIENT_ERROR_RATIO: _client_error_ratio,
    CustomRuleMetric.EVENTS: _entity_events,
    CustomRuleMetric.TOTAL_EVENTS: _total_events,
    CustomRuleMetric.BLOCKED_SHARE: _blocked_share,
    # The loader decides the value's measurement and puts it where an IPS entity's
    # dimension measurement goes (design §G.10.2), so the reading is the same shape.
    CustomRuleMetric.PW_VALUE_EVENTS: _entity_events,
}

# A missing key is a programming error and should fail at import, not on the
# first tick that uses the metric.
if set(_INTERPRETERS) != set(CUSTOM_RULE_METRICS):
    _missing = sorted(
        metric.value for metric in set(CUSTOM_RULE_METRICS) - set(_INTERPRETERS)
    )
    raise RuntimeError(f"custom-rule interpreters do not match the catalog: {_missing}")


__all__ = [
    "BreachState",
    "BucketFacts",
    "FoldInput",
    "FoldOutput",
    "InterpretedReading",
    "RuleStatus",
    "fold_rule",
    "interpret",
    "latest_closed_bucket",
    "walk_buckets",
]
