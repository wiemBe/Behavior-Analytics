"""Rule silence and rule storm: pure detectors over measured rule matches (SOC-6, D-60).

No session, no clock, no I/O. The engine (a later slice) loads the window rows, the metric
rows and the inventory facts, builds the frozen contexts below, and gets back an
`AnomalyEvidence` in the shape every other family returns. Every sentence carries a
`rule.detector.*` reference from `app.i18n.messages`, rendered to the same English the
uncoded sentence printed before the catalog (SOC-6g).

What a verdict may rest on comes from the declared requirement in
`app.anomaly.capabilities.RULE_DETECTORS`, applied by `rule_verdict_scope` to the evidence
the rule's type gives (`match_evidence`). Neither function here grants itself more. Anything
that is not a measured fact -- a window that is missing, TRUNCATED, FAILED, of another width
or not yet settled, an inventory older than the window, a rule edited or flipped inside the
span -- ends in `INSUFFICIENT_DATA` or "not applicable" with a reason, never a zero and never
a healthy verdict.

Silence (design §3). The rule is an enabled EVENT rule (building blocks included, marked in
the explanation). The 7 days before the zero streak must be a fully COMPLETE span of the
current window width; the median per window must reach `min_median`, and at least
`min_presence` of the windows must have matched. Then a proven-zero window is ANOMALOUS and
the shared lifecycle opens on the `windows`-th one. An open incident does not re-test
stability: proven zeros hold it, proven counts are HEALTHY, anything unproven is held.

Storm (design §4). The baseline is the weekday x slot cell (`summarize_rule_cell`), reliable
only with `storm_min_samples` samples over `storm_min_visits` distinct ISO weeks. The guards
are conjunctive and each one can only refuse: window events floor, ratio (skipped when the
median is 0), absolute delta, robust z (or above p95 when the MAD is degenerate).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from app.anomaly import statistics as rstats
from app.anomaly.capabilities import rule_verdict_scope
from app.anomaly.evidence import AnomalyEvidence, healthy, insufficient
from app.anomaly.seasonal import CellSummary, summarize_cell
from app.i18n.messages import MessageRef, render
from app.models.enums import DetectorSignal, RobustScoreStatus, RuleAnomalyType, Severity
from app.models.rule import RuleMatchWindowStatus
from app.rules.matches import floor_to_window, match_evidence
from app.waf.measurement import WafVerdictScope

EVIDENCE_VERSION = 1

#: A sentence as a detector holds it: a registry reference, or uncoded English. Every sentence
#: this module writes is coded; the plain form stays for a shared sentence with no code.
Reason = MessageRef | str


def _ref(name: str, **params: object) -> MessageRef:
    return MessageRef(f"rule.detector.{name}", params)


def _split(reason: Reason) -> tuple[str, MessageRef | None]:
    if isinstance(reason, MessageRef):
        return render(reason), reason
    return reason, None


#: Silence: consecutive proven-zero windows before the incident opens (D-60: 3).
SILENCE_WINDOWS = 3
#: Silence: days of COMPLETE windows before the streak that define a stable rate.
SILENCE_BASELINE_DAYS = 7
#: Silence: share of baseline windows that must have matched (design §3.1: 0.8).
SILENCE_MIN_PRESENCE = 0.8
#: Silence: median `matched_events` per baseline window, zeros included (design §3.1: 1).
SILENCE_MIN_MEDIAN = 1.0
#: Storm: samples and distinct ISO weeks a cell needs (design §4.1: 8 and 4).
STORM_MIN_SAMPLES = 8
STORM_MIN_VISITS = 4
#: The English name of each detector, for a title that has no stored explanation to use.
DETECTOR_LABELS: dict[str, str] = {
    RuleAnomalyType.RULE_SILENCE.value: "Rule silence",
    RuleAnomalyType.RULE_STORM.value: "Rule storm",
}
#: Storm floors, the property-watch event floors (design §4.2: 20 events, 2.0x, 50 delta).
STORM_MIN_WINDOW_EVENTS = 20
STORM_RATIO = 2.0
STORM_MIN_ABSOLUTE_DELTA = 50.0
#: Storm: robust-z threshold (`anomaly_deviation_threshold`) and the MAD floor ratio.
STORM_DEVIATION_Z = 3.5
STORM_MAD_FLOOR_RATIO = 0.1
#: Storm: severity is HIGH from this ratio, otherwise MEDIUM (property-watch rule).
STORM_HIGH_RATIO = 4.0

_DAY_SECONDS = 86_400


@dataclass(frozen=True)
class RuleThresholds:
    silence_windows: int = SILENCE_WINDOWS
    silence_baseline_days: int = SILENCE_BASELINE_DAYS
    silence_min_presence: float = SILENCE_MIN_PRESENCE
    silence_min_median: float = SILENCE_MIN_MEDIAN
    storm_min_samples: int = STORM_MIN_SAMPLES
    storm_min_visits: int = STORM_MIN_VISITS
    storm_min_window_events: float = STORM_MIN_WINDOW_EVENTS
    storm_ratio: float = STORM_RATIO
    storm_min_absolute_delta: float = STORM_MIN_ABSOLUTE_DELTA
    deviation_z: float = STORM_DEVIATION_Z
    mad_floor_ratio: float = STORM_MAD_FLOOR_RATIO


@dataclass(frozen=True)
class RuleSubject:
    """The inventory facts about one rule, as the last inventory sync collected them."""

    qradar_id: int
    rule_type: str | None
    enabled: bool
    is_building_block: bool = False
    health_monitoring_enabled: bool = True
    first_seen_at: datetime | None = None
    qradar_created_at: datetime | None = None
    qradar_modified_at: datetime | None = None
    #: `observed_at` of every `rule_state_transition` that could touch the span.
    transitions: tuple[datetime, ...] = ()
    #: Stored explicit building-block dependencies (QRadar ids) that are disabled.
    disabled_dependencies: tuple[int, ...] = ()
    #: Stored explicit dependencies that the inventory does not hold.
    unobserved_dependencies: tuple[int, ...] = ()
    #: Count of stored explicit dependencies (0 is allowed: the payload listed none).
    dependency_count: int = 0


@dataclass(frozen=True)
class WindowFact:
    """One recorded window and this rule's metric row in it.

    A window with no `rule_match_window` row is simply absent from the series: a gap is
    never constructed here as a zero. `matched_events` is None when the rule has no metric
    row in the window.
    """

    start: datetime
    window_seconds: int
    status: RuleMatchWindowStatus | str
    matched_events: int | None = None
    origin: str = "LIVE"
    unknown_ids: int = 0


class _Kind(StrEnum):
    ZERO = "ZERO"  # COMPLETE, right width, no row: a proven zero
    COUNT = "COUNT"  # COMPLETE, right width, a row
    PARTIAL_COUNT = "PARTIAL_COUNT"  # TRUNCATED, right width, a row (positive evidence only)
    MISSING = "MISSING"
    WRONG_WIDTH = "WRONG_WIDTH"
    TRUNCATED = "TRUNCATED"
    FAILED = "FAILED"


def _utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _kind(fact: WindowFact | None, width: int) -> _Kind:
    if fact is None:
        return _Kind.MISSING
    if fact.window_seconds != width:
        return _Kind.WRONG_WIDTH
    if fact.status == RuleMatchWindowStatus.COMPLETE:
        return _Kind.ZERO if fact.matched_events is None else _Kind.COUNT
    if fact.status == RuleMatchWindowStatus.TRUNCATED:
        return _Kind.PARTIAL_COUNT if fact.matched_events is not None else _Kind.TRUNCATED
    # FAILED, and any status this code does not know: fail closed.
    return _Kind.FAILED


def _by_start(windows: Sequence[WindowFact]) -> dict[datetime, WindowFact]:
    return {_utc(w.start): w for w in windows}


def _skip(
    detector: RuleAnomalyType,
    reason: Reason,
    *,
    start: datetime,
    end: datetime,
    code: str,
    applicable: bool = True,
    **extra: Any,
) -> AnomalyEvidence[RuleAnomalyType]:
    """INSUFFICIENT_DATA with a machine-readable `skip` code.

    `applicable` False means the subject is not one this detector judges at all (type,
    disabled, monitoring off, dependency): the engine may resolve an open incident for
    `disabled` and `dependency_disabled`; every other skip holds it.
    """
    text, message = _split(reason)
    ev = insufficient(
        detector, interval_start=start, interval_end=end, reason=text, reason_message=message
    )
    ev.extra = {"skip": code, "applicable": applicable, "evidence_version": EVIDENCE_VERSION}
    ev.extra.update(extra)
    return ev


def _subject_gate(
    detector: RuleAnomalyType,
    subject: RuleSubject,
    *,
    start: datetime,
    end: datetime,
    inventory_watermark: datetime | None,
) -> AnomalyEvidence[RuleAnomalyType] | None:
    """The refusals shared by both detectors, in order; None when the subject may be judged."""
    evidence = match_evidence(subject.rule_type)
    decision = rule_verdict_scope(detector, evidence)
    if decision.scope is not WafVerdictScope.FULL:
        return _skip(
            detector,
            _ref("not_exact", rule_type=subject.rule_type or "unknown"),
            start=start,
            end=end,
            code="not_exact",
            applicable=False,
            match_evidence=evidence.value,
        )
    if not subject.enabled:
        if inventory_watermark is None:
            return _skip(
                detector,
                _ref("disabled_no_sync"),
                start=start,
                end=end,
                code="disabled",
                applicable=False,
            )
        return _skip(
            detector,
            _ref("disabled_with_sync", synced_at=_utc(inventory_watermark)),
            start=start,
            end=end,
            code="disabled",
            applicable=False,
        )
    if not subject.health_monitoring_enabled:
        return _skip(
            detector,
            _ref("monitoring_off"),
            start=start,
            end=end,
            code="monitoring_off",
            applicable=False,
        )
    if subject.disabled_dependencies:
        return _skip(
            detector,
            _ref("dependency_disabled", ids=list(subject.disabled_dependencies)),
            start=start,
            end=end,
            code="dependency_disabled",
            applicable=False,
        )
    if subject.unobserved_dependencies:
        return _skip(
            detector,
            _ref("dependency_unobserved", ids=list(subject.unobserved_dependencies)),
            start=start,
            end=end,
            code="dependency_unobserved",
        )
    if inventory_watermark is None or _utc(inventory_watermark) < end:
        return _skip(
            detector,
            _ref("stale_inventory"),
            start=start,
            end=end,
            code="stale_inventory",
        )
    return None


def _window_gate(
    detector: RuleAnomalyType,
    fact: WindowFact | None,
    width: int,
    *,
    start: datetime,
    end: datetime,
    settled_until: datetime,
) -> AnomalyEvidence[RuleAnomalyType] | None:
    """Refuse a judged window that is not settled or not usable at all."""
    if end > _utc(settled_until):
        return _skip(detector, _ref("not_settled"), start=start, end=end, code="not_settled")
    kind = _kind(fact, width)
    if kind is _Kind.MISSING:
        reason: MessageRef = _ref("window_missing")
    elif kind is _Kind.WRONG_WIDTH:
        reason = _ref("window_wrong_width", width=width)
    elif kind is _Kind.FAILED:
        reason = _ref("window_failed")
    else:
        return None
    return _skip(
        detector,
        reason,
        start=start,
        end=end,
        code=f"window_{kind.value.lower()}",
    )


@dataclass(frozen=True)
class BaselineTally:
    expected: int = 0
    complete: int = 0
    truncated: int = 0
    failed: int = 0
    missing: int = 0
    wrong_width: int = 0

    @property
    def is_complete(self) -> bool:
        return self.expected > 0 and self.complete == self.expected


def _grid(span_start: datetime, span_end: datetime, width: int) -> list[datetime]:
    """Aligned window starts with start >= span_start and end <= span_end."""
    step = timedelta(seconds=width)
    first = floor_to_window(span_start, width)
    if first < _utc(span_start):
        first += step
    out: list[datetime] = []
    while first + step <= _utc(span_end):
        out.append(first)
        first += step
    return out


def _in_half_open(when: datetime, spans: Sequence[tuple[datetime, datetime]]) -> bool:
    """Whether ``when`` falls in a half-open ``[start, end)`` span.

    Analyst exclusions use this. An incident span is closed on the end, which
    is what that span wants and what an analyst interval does not.
    """
    return any(start <= when < end for start, end in spans)


def _baseline(
    by_start: Mapping[datetime, WindowFact],
    span_start: datetime,
    span_end: datetime,
    width: int,
    analyst_spans: Sequence[tuple[datetime, datetime]] = (),
) -> tuple[BaselineTally, list[float], int]:
    """(tally, per-window matched_events of the COMPLETE windows, HISTORY window count).

    Only a span whose every grid window is COMPLETE of the current width is a baseline;
    the caller refuses otherwise, so the values never include a filled-in gap.
    A COMPLETE window inside an analyst span counts as missing (SOC-13h). The span
    is half-open, and it applies only when the window would otherwise count, so a
    truncated, failed or wrong-width window keeps that reason.
    """
    expected = complete = truncated = failed = missing = wrong = history = 0
    values: list[float] = []
    for start in _grid(span_start, span_end, width):
        expected += 1
        fact = by_start.get(start)
        kind = _kind(fact, width)
        if kind in (_Kind.ZERO, _Kind.COUNT):
            if _in_half_open(start, analyst_spans):
                missing += 1
                continue
            complete += 1
            values.append(float(fact.matched_events or 0) if fact else 0.0)
            history += 1 if fact is not None and fact.origin == "HISTORY" else 0
        elif kind in (_Kind.TRUNCATED, _Kind.PARTIAL_COUNT):
            truncated += 1
        elif kind is _Kind.MISSING:
            missing += 1
        elif kind is _Kind.WRONG_WIDTH:
            wrong += 1
        else:
            failed += 1
    return BaselineTally(expected, complete, truncated, failed, missing, wrong), values, history


@dataclass(frozen=True)
class SilenceContext:
    subject: RuleSubject
    #: Every recorded window the engine loaded, the baseline span and the streak included.
    windows: Sequence[WindowFact]
    window_seconds: int
    #: Start of the window under judgement.
    judged_start: datetime
    #: Windows ending after this are not settled (the collector's settle lag applied).
    settled_until: datetime
    #: `analytics_rule` inventory watermark: the last sync time.
    inventory_watermark: datetime | None
    incident_open: bool = False
    #: Start of the open incident's streak, so its explanation keeps the original baseline.
    incident_streak_start: datetime | None = None
    thresholds: RuleThresholds = field(default_factory=RuleThresholds)
    #: Half-open ``rule:silence`` spans (SOC-13h). Empty leaves the grid unchanged.
    analyst_spans: Sequence[tuple[datetime, datetime]] = ()


def detect_silence(ctx: SilenceContext) -> AnomalyEvidence[RuleAnomalyType]:
    """Judge one settled window of one rule for silence (design §3)."""
    det = RuleAnomalyType.RULE_SILENCE
    th = ctx.thresholds
    width = ctx.window_seconds
    step = timedelta(seconds=width)
    start = _utc(ctx.judged_start)
    end = start + step
    refused = _subject_gate(
        det, ctx.subject, start=start, end=end, inventory_watermark=ctx.inventory_watermark
    )
    if refused is not None:
        return refused
    by_start = _by_start(ctx.windows)
    fact = by_start.get(start)
    refused = _window_gate(det, fact, width, start=start, end=end, settled_until=ctx.settled_until)
    if refused is not None:
        return refused
    kind = _kind(fact, width)
    if kind is _Kind.TRUNCATED:
        # Silence never reads a TRUNCATED window: a rule cut off cannot be told from a
        # rule that matched zero.
        return _skip(det, _ref("silence_truncated"), start=start, end=end,
                     code="window_truncated")
    if kind in (_Kind.COUNT, _Kind.PARTIAL_COUNT):
        assert fact is not None and fact.matched_events is not None
        if kind is _Kind.PARTIAL_COUNT:
            # A row in a TRUNCATED window proves firing, but the window is not whole.
            return _skip(det, _ref("silence_truncated_with_row"), start=start, end=end,
                         code="window_truncated")
        count = fact.matched_events
        text, message = _split(_ref("matched_count", count=count))
        return healthy(
            det,
            interval_start=start,
            interval_end=end,
            observed=float(count),
            reason=text,
            reason_message=message,
        )

    # A proven zero. Count the run of proven zeros that ends here, up to the open threshold.
    run = 0
    cursor = start
    while run < th.silence_windows and _kind(by_start.get(cursor), width) is _Kind.ZERO:
        run += 1
        cursor -= step
    if ctx.incident_open and ctx.incident_streak_start is not None:
        streak_start = _utc(ctx.incident_streak_start)
    else:
        streak_start = start - (run - 1) * step
    span_start = streak_start - timedelta(days=th.silence_baseline_days)
    tally, values, history = _baseline(
        by_start, span_start, streak_start, width, ctx.analyst_spans
    )

    facts: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "unit": "matched_events",
        "window_seconds": width,
        "windows": th.silence_windows,
        "baseline_days": th.silence_baseline_days,
        "qradar_id": ctx.subject.qradar_id,
        "is_building_block": ctx.subject.is_building_block,
        "match_evidence": "EXACT",
        "history_windows": history,
        "unknown_ids": fact.unknown_ids if fact is not None else 0,
        "inventory_watermark": _utc(ctx.inventory_watermark).isoformat()
        if ctx.inventory_watermark
        else None,
        "dependencies": ctx.subject.dependency_count,
        "consecutive_buckets": run,
        "ratio_to_baseline": 0.0,
        "baseline_tally": tally.__dict__,
        "stability_retested": not ctx.incident_open,
    }

    if not ctx.incident_open:
        refused = _stability_gate(ctx, span_start, streak_start, end, tally, values, facts)
        if refused is not None:
            return refused

    median = rstats.median(values) if tally.is_complete else None
    presence = (sum(1 for v in values if v > 0) / len(values)) if tally.is_complete else None
    facts["median"] = median
    facts["nonzero_fraction"] = presence
    if median is not None and presence is not None:
        reason_ref = _ref(
            "silence_proven_zero",
            windows=run,
            width=width,
            days=th.silence_baseline_days,
            median=median,
            presence=presence * 100,
        )
    else:
        reason_ref = _ref("silence_proven_zero_open", windows=run, width=width)
    text, message = _split(reason_ref)
    return AnomalyEvidence(
        anomaly_type=det,
        signal=DetectorSignal.ANOMALOUS,
        severity=Severity.MEDIUM,
        observed_value=0.0,
        expected_value=median,
        baseline_low=rstats.percentile(values, 5) if tally.is_complete else None,
        baseline_high=rstats.percentile(values, 95) if tally.is_complete else None,
        deviation_score=None,
        threshold=float(th.silence_windows),
        sample_count=tally.complete,
        interval_start=streak_start,
        interval_end=end,
        confidence=round(presence, 3) if presence is not None else 0.5,
        reason=text,
        extra=facts,
        reason_message=message,
    )


def _stability_gate(
    ctx: SilenceContext,
    span_start: datetime,
    streak_start: datetime,
    end: datetime,
    tally: BaselineTally,
    values: list[float],
    facts: dict[str, Any],
) -> AnomalyEvidence[RuleAnomalyType] | None:
    det = RuleAnomalyType.RULE_SILENCE
    th = ctx.thresholds
    subject = ctx.subject
    kw: dict[str, Any] = {"start": streak_start, "end": end}
    held = [t for t in (subject.qradar_created_at, subject.first_seen_at) if t is not None]
    if not held or all(_utc(t) > span_start for t in held):
        return _skip(det, _ref("rule_too_new", days=th.silence_baseline_days),
                     code="rule_too_new", **kw)
    if any(span_start <= _utc(t) <= end for t in subject.transitions):
        return _skip(det, _ref("state_transition"), code="state_transition", **kw)
    modified = subject.qradar_modified_at
    if modified is not None and _utc(modified) >= span_start:
        return _skip(
            det,
            _ref("modified", modified_at=_utc(modified)),
            code="modified", modified_at=_utc(modified).isoformat(), **kw,
        )
    if not tally.is_complete:
        return _skip(
            det,
            _ref(
                "baseline_incomplete",
                days=th.silence_baseline_days,
                missing=tally.missing,
                truncated=tally.truncated,
                failed=tally.failed,
                wrong_width=tally.wrong_width,
                expected=tally.expected,
            ),
            code="baseline_incomplete", baseline_tally=tally.__dict__, **kw,
        )
    median = rstats.median(values)
    if median < th.silence_min_median:
        return _skip(det, _ref("median_below_floor", median=median,
                               minimum=th.silence_min_median),
                     code="median_below_floor", median=median, **kw)
    presence = sum(1 for v in values if v > 0) / len(values)
    if presence < th.silence_min_presence:
        return _skip(det, _ref("presence_below_gate", presence=presence * 100,
                               minimum=th.silence_min_presence * 100),
                     code="presence_below_gate", nonzero_fraction=presence, **kw)
    facts["median"] = median
    return None


# --- Storm -------------------------------------------------------------------------------


def slot_of(window_start: datetime, window_seconds: int) -> tuple[int, int] | None:
    """(ISO weekday, slot) of a UTC window; None when the width does not tile a UTC day.

    The slot is the window's index inside the UTC day; at 3,600 s it is the hour, the same
    key as `seasonal_key`. Any other width keys by index because `seasonal_key` buckets by
    clock hour and would merge or split such windows.
    """
    if window_seconds <= 0 or _DAY_SECONDS % window_seconds != 0:
        return None
    s = _utc(window_start)
    seconds = s.hour * 3600 + s.minute * 60 + s.second
    return s.isoweekday(), seconds // window_seconds


@dataclass(frozen=True)
class RuleCell:
    """One weekday x slot cell of one rule. `summary` is None when no sample exists."""

    weekday: int
    slot: int
    summary: CellSummary | None
    visits: int
    is_reliable: bool
    #: Of the samples, those that came from HISTORY windows (carried into the explanation).
    history_samples: int = 0


def summarize_rule_cell(
    weekday: int,
    slot: int,
    values: Sequence[float],
    distinct_weeks: int,
    *,
    min_samples: int = STORM_MIN_SAMPLES,
    min_visits: int = STORM_MIN_VISITS,
    history_samples: int = 0,
) -> RuleCell:
    """The cell for one (weekday, slot) from the samples the engine's query selected.

    The query has already excluded incident spans, windows that are not COMPLETE of the
    current width, and missing windows (never a stored zero). Reliable only with enough
    samples *and* enough distinct weeks: one noisy week must not fill a cell.
    """
    if not values:
        return RuleCell(weekday, slot, None, distinct_weeks, False, history_samples)
    summary = summarize_cell(values, min_samples=min_samples)
    return RuleCell(
        weekday,
        slot,
        summary,
        distinct_weeks,
        summary.is_reliable and distinct_weeks >= min_visits,
        history_samples,
    )


@dataclass(frozen=True)
class StormContext:
    subject: RuleSubject
    #: The judged window's record, or None when nothing was recorded for it.
    window: WindowFact | None
    window_seconds: int
    judged_start: datetime
    settled_until: datetime
    inventory_watermark: datetime | None
    cell: RuleCell | None
    thresholds: RuleThresholds = field(default_factory=RuleThresholds)


def detect_storm(ctx: StormContext) -> AnomalyEvidence[RuleAnomalyType]:
    """Judge one settled window of one rule for a storm (design §4)."""
    det = RuleAnomalyType.RULE_STORM
    th = ctx.thresholds
    width = ctx.window_seconds
    start = _utc(ctx.judged_start)
    end = start + timedelta(seconds=width)
    refused = _subject_gate(
        det, ctx.subject, start=start, end=end, inventory_watermark=ctx.inventory_watermark
    )
    if refused is not None:
        return refused
    key = slot_of(start, width)
    if key is None:
        return _skip(det, _ref("width_does_not_tile", width=width), start=start, end=end,
                     code="width_does_not_tile")
    refused = _window_gate(
        det, ctx.window, width, start=start, end=end, settled_until=ctx.settled_until
    )
    if refused is not None:
        return refused
    fact = ctx.window
    kind = _kind(fact, width)
    if kind is _Kind.TRUNCATED:
        return _skip(det, _ref("storm_truncated"), start=start, end=end,
                     code="window_truncated")
    observed = float(fact.matched_events or 0) if fact is not None else 0.0
    if kind is _Kind.ZERO:
        text, message = _split(_ref("storm_zero"))
        return healthy(
            det,
            interval_start=start,
            interval_end=end,
            observed=0.0,
            reason=text,
            reason_message=message,
        )
    cell = ctx.cell
    if cell is None or cell.summary is None or (cell.weekday, cell.slot) != key:
        return _skip(det, _ref("cell_missing"), start=start, end=end, code="cell_missing",
                     weekday=key[0], slot=key[1])
    summary = cell.summary
    base: dict[str, Any] = {
        "evidence_version": EVIDENCE_VERSION,
        "unit": "matched_events",
        "window_seconds": width,
        "weekday": cell.weekday,
        "slot": cell.slot,
        "hour": cell.slot if width == 3600 else None,
        "visits": cell.visits,
        "history_samples": cell.history_samples,
        "qradar_id": ctx.subject.qradar_id,
        "is_building_block": ctx.subject.is_building_block,
        "match_evidence": "EXACT",
        "truncated_window": kind is _Kind.PARTIAL_COUNT,
        "unknown_ids": fact.unknown_ids if fact is not None else 0,
    }
    if not cell.is_reliable:
        ev = _skip(
            det,
            _ref("cell_thin", samples=summary.sample_count, weeks=cell.visits,
                 min_samples=th.storm_min_samples, min_weeks=th.storm_min_visits),
            start=start, end=end, code="cell_thin", **base,
        )
        ev.sample_count = summary.sample_count
        return ev

    expected = summary.median
    delta = observed - expected

    def calm(reason: Reason, **more: Any) -> AnomalyEvidence[RuleAnomalyType]:
        text, message = _split(reason)
        ev = healthy(det, interval_start=start, interval_end=end, observed=observed,
                     expected=expected, reason=text, reason_message=message)
        ev.sample_count = summary.sample_count
        ev.extra = {**base, **more}
        return ev

    if observed < th.storm_min_window_events:
        return calm(
            _ref("below_event_floor", observed=observed, minimum=th.storm_min_window_events)
        )
    ratio = rstats.ratio(observed, expected)
    if ratio is not None and ratio < th.storm_ratio:
        return calm(_ref("ratio_below", ratio=ratio, threshold=th.storm_ratio), ratio=ratio)
    if delta < th.storm_min_absolute_delta:
        return calm(
            _ref("rise_below_delta", delta=delta, minimum=th.storm_min_absolute_delta),
            ratio=ratio,
        )
    score, status = rstats.robust_z_with_status(
        observed, summary.median, summary.mad, mad_floor_ratio=th.mad_floor_ratio
    )
    if status is RobustScoreStatus.OK:
        if score < th.deviation_z:
            return calm(_ref("z_below", score=score, threshold=th.deviation_z),
                        ratio=ratio, robust_z=round(score, 3))
        basis = _ref("basis_z", score=score, threshold=th.deviation_z)
        magnitude = score
    else:
        upper = max(summary.p95, expected)
        if observed <= upper:
            return calm(_ref("mad_within_p95", bound=upper), ratio=ratio)
        basis = _ref("basis_above_p95", bound=upper)
        magnitude = th.deviation_z
    extra = {
        **base,
        "robust_z": round(score, 3),
        "robust_score_status": status.value,
        "median": expected,
        "absolute_delta": delta,
        "deviation_ratio": ratio,
    }
    if ratio is not None:
        extra["ratio"] = ratio
        reason_ref = _ref(
            "storm_fired",
            observed=observed,
            expected=expected,
            ratio=ratio,
            delta=delta,
            basis=basis,
        )
    else:
        reason_ref = _ref("storm_fired_zero_median", observed=observed, basis=basis)
    text, message = _split(reason_ref)
    return AnomalyEvidence(
        anomaly_type=det,
        signal=DetectorSignal.ANOMALOUS,
        severity=Severity.HIGH
        if ratio is not None and ratio >= STORM_HIGH_RATIO
        else Severity.MEDIUM,
        observed_value=observed,
        expected_value=expected,
        baseline_low=summary.p05,
        baseline_high=summary.p95,
        deviation_score=magnitude,
        threshold=th.deviation_z,
        sample_count=summary.sample_count,
        interval_start=start,
        interval_end=end,
        confidence=min(1.0, summary.sample_count / (2 * th.storm_min_samples)),
        reason=text,
        extra=extra,
        reason_message=message,
    )
