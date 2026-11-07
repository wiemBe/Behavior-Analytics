"""Replay stored Phase A buckets through the production detectors (SOC-17b, design §3.3).

The tuning page asks one question: *if these thresholds had been in force over
the last 7 or 28 days of stored data, how many incidents would this source have
opened?* This module answers it without writing anything and without asking
QRadar.

It never re-implements detection. The per-bucket context is built with
``app.anomaly.engine.metric_point`` / ``baseline_cell``, the verdicts come from
``app.anomaly.engine.run_detectors`` (the same detector functions, the same
capability gate and the same NO_EVENTS demotion), and the lifecycle moves come
from ``app.anomaly.lifecycle.advance_counters`` / ``next_state``. Those three
helpers were extracted from the engine for exactly this reason: the engine and
the replay cannot drift apart.

Cost is four statements whatever the fleet size (design §3.5, AGENTS.md rule 9):
the source plus its outage policy and class, the metric rows, the baseline
cells, and the incidents whose spans intersect the window. Nothing runs per
bucket or per detector.

What the replay is not (design §3.4): it is not the number of alerts the engine
raised, and it is not a forecast. It folds stored buckets from a clean NORMAL
state and reports incidents it would open. The recorded history is a third,
separate number.
"""

from __future__ import annotations

import uuid
from bisect import bisect_right
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from math import ceil
from typing import Any

from sqlalchemy import select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.baseline import BASELINE_METRICS
from app.anomaly.business_hours import is_expected_active
from app.anomaly.capabilities import bucket_telemetry
from app.anomaly.detectors import BaselineCell, DetectionContext, MetricPoint
from app.anomaly.engine import baseline_cell, metric_point, run_detectors
from app.anomaly.lifecycle import LifecycleInput, advance_counters, next_state
from app.anomaly.seasonal import in_maintenance
from app.anomaly.thresholds import (
    ThresholdResolver,
    Thresholds,
    direction_excluded,
)
from app.core.config import Settings, get_settings
from app.custom_rules.preview import bucket_grid, latest_closed_bucket
from app.fleet.outage_policy import resolve as resolve_outage
from app.history.origin import is_live_clause
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Criticality,
    OutagePolicyMode,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)
from app.models.outage import OutageAlertPolicy
from app.providers.base import LogSourceTelemetry

#: The two windows the tuning page offers.
WINDOW_DAYS: tuple[int, ...] = (7, 28)
DEFAULT_WINDOW_DAYS = 28
#: A window below this many covered days cannot state a count (design §3.5).
MIN_COVERED_DAYS = 7
#: More than this share of unjudged buckets makes the count unstatable.
MAX_NOT_JUDGED_FRACTION = 0.10
#: The engine's NO_EVENTS streak depth and payload-signature history.
PRECEDING_BUCKETS = 12
SIGNATURE_BUCKETS = 6
#: The replay refuses a grid larger than this; 28 daily-specced windows at 300 s
#: are 8 064 buckets, and LAB_MODE's 60 s is 40 320, both below it.
MAX_BUCKETS = 50_000

REASON_NOT_MONITORED = "noise.preview.not_monitored"
REASON_INSUFFICIENT_COVERAGE = "noise.preview.insufficient_coverage"
REASON_NOT_JUDGED = "noise.preview.not_judged"
REASON_MAINTENANCE = "noise.preview.maintenance"
REASON_TOO_LARGE = "noise.preview.too_large"
REASON_DIRECTION_DISABLED = "noise.preview.direction_disabled"
REASON_NOT_REPLAYABLE = "noise.preview.not_replayable"
REASON_PARTIAL = "noise.preview.partial"
REASON_MAINTENANCE_HISTORY = "noise.preview.maintenance_history"


class ReplayState(StrEnum):
    """Whether the stored window can state a count at all."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    TOO_LARGE = "TOO_LARGE"


@dataclass(frozen=True)
class ReplayReason:
    """A stable code, its raw parameters and the English sentence they render to."""

    code: str
    params: dict[str, object]
    text: str

    def to_wire(self) -> dict[str, object]:
        return {"code": self.code, "params": dict(self.params), "text": self.text}


def reason(code: str, **params: object) -> ReplayReason:
    """Build a reason by rendering its registered sentence, never by hand."""
    ref = MessageRef(code, params)
    return ReplayReason(code=code, params=dict(params), text=render(ref))


@dataclass(frozen=True)
class WindowSpan:
    """The requested window and the part of it that stored rows can cover."""

    days: int
    first_bucket: datetime | None
    last_bucket: datetime | None
    covered_days: int


@dataclass(frozen=True)
class BaselineInfo:
    """The current baseline snapshot the replay judged against (design §1.5)."""

    computed_at: datetime | None
    baseline_version: int | None
    lookback_days: int


@dataclass(frozen=True)
class DetectorCount:
    """One detector's count, or null when the run's direction excluded it.

    ``excluded_by_direction`` is true exactly when ``incidents`` is None: the
    detector did not run, so the row must not read as a measured zero.
    """

    anomaly_type: AnomalyType
    incidents: int | None
    excluded_by_direction: bool = False


@dataclass(frozen=True)
class ReplayCounts:
    """Incidents the replay would open, in total and per detector.

    ``total_incidents`` sums only the detectors that ran; a row with
    ``excluded_by_direction`` carries no count and adds nothing.
    """

    total_incidents: int
    by_detector: tuple[DetectorCount, ...]


@dataclass(frozen=True)
class RecordedCounts:
    """What the real engine recorded over the same window, as a separate number."""

    incidents: int
    opened: int
    suppressed: int


@dataclass(frozen=True)
class NotReplayable:
    """A detector the window cannot replay, and why."""

    anomaly_type: AnomalyType
    reason: ReplayReason


@dataclass(frozen=True)
class ReplayResult:
    state: ReplayState
    available: bool
    window: WindowSpan
    baseline: BaselineInfo
    current: ReplayCounts | None
    proposed: ReplayCounts | None
    recorded: RecordedCounts | None
    not_replayable: tuple[NotReplayable, ...]
    not_judged_buckets: int | None
    maintenance_buckets: int | None
    warnings: tuple[ReplayReason, ...]
    reason: ReplayReason | None
    #: Criticality both resolver calls used for the proposed fold. When the
    #: caller omitted one, this is the source's stored value. Never written.
    effective_criticality: Criticality
    #: Same flag as ``NoiseCurrentOut.lab_mode``: LAB_MODE overrides the
    #: criticality hysteresis, so criticality has no detection effect (§3.2).
    lab_mode: bool


@dataclass(frozen=True)
class BucketInput:
    """Everything one stored bucket contributes to a context, thresholds aside.

    Building this once and constructing the context per threshold set keeps the
    current run (M) and the proposed run (N) on exactly the same reconstructed
    inputs, which is what makes them comparable (design §3.3 step 6).
    """

    start: datetime
    point: MetricPoint
    cells: Mapping[str, BaselineCell]
    expected_interval_seconds: float
    recent_signatures: tuple[str, ...]
    is_expected_active: bool
    preceding_empty_buckets: int
    telemetry: frozenset[LogSourceTelemetry]
    complete: bool


def detection_context(bucket: BucketInput, thresholds: Thresholds) -> DetectionContext:
    """The exact context the engine would build for this bucket."""
    return DetectionContext(
        point=bucket.point,
        baselines=dict(bucket.cells),
        thresholds=thresholds,
        expected_interval_seconds=bucket.expected_interval_seconds,
        recent_signatures=list(bucket.recent_signatures),
        is_expected_active=bucket.is_expected_active,
        preceding_empty_buckets=bucket.preceding_empty_buckets,
    )


def bucket_judged(bucket: BucketInput) -> bool:
    """Whether the volume knobs can reach a verdict for this bucket.

    A bucket is judged when it has a complete metric row and a reliable cell for
    both baseline metrics the replayable detectors read. A missing or partial
    row, or a still-learning cell, is "not judged" -- never a smaller count
    (design §3.5).
    """
    if not bucket.complete:
        return False
    for metric in ("average_eps", "event_count"):
        cell = bucket.cells.get(metric)
        if cell is None or not cell.is_reliable:
            return False
    return True


def fold_buckets(
    buckets: Sequence[BucketInput],
    *,
    thresholds: Thresholds,
    by_type: Mapping[AnomalyType, Thresholds],
) -> tuple[
    dict[AnomalyType, int], dict[AnomalyType, int], dict[AnomalyType, str]
]:
    """Fold the buckets through the production lifecycle, from a clean NORMAL.

    Returns the number of OPEN transitions per detector, how many buckets each
    detector actually judged, and the reason each unjudged detector was skipped.
    """
    counts: dict[AnomalyType, int] = {}
    judged: dict[AnomalyType, int] = {}
    skipped: dict[AnomalyType, str] = {}
    state = dict.fromkeys(AnomalyType, AnomalyState.NORMAL)
    anomalous = dict.fromkeys(AnomalyType, 0)
    healthy = dict.fromkeys(AnomalyType, 0)

    for bucket in buckets:
        ctx = detection_context(bucket, thresholds)
        evidences, unsupported = run_detectors(ctx, bucket.telemetry)
        for kind, why in unsupported.items():
            skipped.setdefault(AnomalyType(kind), why)
        for evidence in evidences:
            kind = evidence.anomaly_type
            judged[kind] = judged.get(kind, 0) + 1
            current = state[kind]
            consecutive_anomalous, consecutive_healthy = advance_counters(
                evidence.signal, anomalous[kind], healthy[kind]
            )
            decision = next_state(
                LifecycleInput(
                    state=current,
                    signal=evidence.signal,
                    consecutive_anomalous=consecutive_anomalous,
                    consecutive_healthy=consecutive_healthy,
                    open_after=by_type[kind].open_after,
                    resolve_after=by_type[kind].resolve_after,
                    insufficient_data=evidence.insufficient_data,
                    # The preview refuses a source in maintenance, so no bucket
                    # here is suppressed by today's open-ended window.
                    suppressed=False,
                )
            )
            if decision.state is AnomalyState.OPEN and current is not AnomalyState.OPEN:
                counts[kind] = counts.get(kind, 0) + 1
            state[kind] = decision.state
            anomalous[kind] = consecutive_anomalous
            healthy[kind] = consecutive_healthy
    return counts, judged, skipped


def _as_counts(
    counts: Mapping[AnomalyType, int],
    judged: Mapping[AnomalyType, int],
    *,
    direction: object,
) -> ReplayCounts:
    """Per detector, for every detector that ran, including a real zero.

    A detector the run's ``direction`` excludes did not run (``run_detectors``
    drops it), so it is listed with null incidents and the flag, never as a
    measured ``0``. The total sums the detectors that ran.
    """
    excluded = direction_excluded(direction)
    kinds = [
        kind for kind in AnomalyType if judged.get(kind, 0) > 0 or kind in excluded
    ]
    by_detector = tuple(
        DetectorCount(
            anomaly_type=kind,
            incidents=None if kind in excluded else counts.get(kind, 0),
            excluded_by_direction=kind in excluded,
        )
        for kind in kinds
    )
    return ReplayCounts(
        total_incidents=sum(counts.values()),
        by_detector=by_detector,
    )


def _covered_days(starts: Sequence[datetime], interval_seconds: int, window_days: int) -> int:
    """Whole days from the first stored bucket through the last one's end."""
    if not starts:
        return 0
    span = starts[-1] + timedelta(seconds=interval_seconds) - starts[0]
    return min(window_days, max(0, ceil(span.total_seconds() / 86_400)))


def _merge_custom(current: Mapping[str, Any], proposed: Mapping[str, Any]) -> dict[str, Any]:
    """The proposed knobs on top of the stored ones, with a nested by_type replaced."""
    merged = dict(current)
    merged.update(proposed)
    return merged


class NoiseBacktest:
    """The four reads and the two folds behind ``POST /noise/sources/{id}/preview``."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.now = now or datetime.now(UTC)

    async def preview(
        self,
        log_source_id: uuid.UUID,
        *,
        proposed: Mapping[str, Any] | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
        criticality: Criticality | None = None,
    ) -> ReplayResult | None:
        """One source, one window, current versus proposed. ``None`` when unknown.

        ``criticality`` None resolves the proposed fold with the stored value,
        which is what a body that omits the field does. A value is passed to
        both resolver calls of that fold and is never written.
        """
        if window_days not in WINDOW_DAYS:
            raise ValueError("window_days must be 7 or 28")
        source_row = await self._source(log_source_id)
        if source_row is None:
            return None
        source, policies, source_class, latest_width = source_row
        # The proposed fold's criticality, decided once. The current fold keeps
        # the stored column. LAB_MODE makes the two folds agree either way.
        effective_criticality = source.criticality if criticality is None else criticality
        lab_mode = self.settings.lab_mode
        interval = int(
            source.expected_interval_seconds or latest_width or 300
        )
        if interval <= 0:
            interval = 300

        estimated = (window_days * 86_400) // interval + 1
        if estimated > MAX_BUCKETS:
            return ReplayResult(
                state=ReplayState.TOO_LARGE,
                available=False,
                window=WindowSpan(
                    days=window_days,
                    first_bucket=None,
                    last_bucket=latest_closed_bucket(self.now, interval),
                    covered_days=0,
                ),
                baseline=BaselineInfo(
                    computed_at=None,
                    baseline_version=None,
                    lookback_days=self.settings.baseline_lookback_days,
                ),
                current=None,
                proposed=None,
                recorded=None,
                not_replayable=(),
                not_judged_buckets=None,
                maintenance_buckets=None,
                warnings=(),
                reason=reason(REASON_TOO_LARGE, count=estimated, limit=MAX_BUCKETS),
                effective_criticality=effective_criticality,
                lab_mode=lab_mode,
            )

        grid = bucket_grid(self.now, window_days * 24, interval)
        window_start = grid[0]
        window_end = grid[-1] + timedelta(seconds=interval)
        metrics = await self._metrics(log_source_id, window_start, window_end, interval)
        cells = await self._cells(log_source_id)
        anomalies = await self._anomalies(log_source_id, window_start, window_end)
        buckets = self._bucket_inputs(source, metrics, cells, grid)
        recorded = _recorded(anomalies, window_start, window_end)
        maintenance_buckets = _maintenance_buckets(anomalies, grid, window_end)
        maintained = in_maintenance(
            self.now,
            maintenance_mode=source.maintenance_mode,
            maintenance_until=source.maintenance_until,
        )

        stored_starts = [bucket.start for bucket in buckets if bucket is not None]
        covered_days = _covered_days(stored_starts, interval, window_days)
        present = [bucket for bucket in buckets if bucket is not None]
        # Only the covered span can be "not judged": buckets the source never
        # reported are outside coverage, and the PARTIAL state names that span.
        # A missing row inside it is unjudged too -- never a smaller count.
        covered_grid = (
            [start for start in grid if stored_starts[0] <= start <= stored_starts[-1]]
            if stored_starts
            else []
        )
        not_judged = len(covered_grid) - sum(
            1 for bucket in present if bucket_judged(bucket)
        )
        baseline = BaselineInfo(
            computed_at=max(
                (cell.computed_at for cell in cells.values() if cell.computed_at),
                default=None,
            ),
            baseline_version=max(
                (
                    cell.baseline_version
                    for cell in cells.values()
                    if cell.baseline_version
                ),
                default=None,
            ),
            lookback_days=self.settings.baseline_lookback_days,
        )
        window = WindowSpan(
            days=window_days,
            first_bucket=stored_starts[0] if stored_starts else None,
            last_bucket=stored_starts[-1] if stored_starts else None,
            covered_days=covered_days,
        )
        warnings = _warnings(maintenance_buckets, len(covered_grid))

        insufficient = self._insufficient(
            source=source,
            maintained=maintained,
            covered_days=covered_days,
            window_days=window_days,
            not_judged=not_judged,
            total_buckets=len(covered_grid),
        )
        if insufficient is not None:
            return ReplayResult(
                state=ReplayState.INSUFFICIENT_DATA,
                available=False,
                window=window,
                baseline=baseline,
                current=None,
                proposed=None,
                recorded=recorded,
                not_replayable=(),
                not_judged_buckets=not_judged,
                maintenance_buckets=maintenance_buckets,
                warnings=warnings,
                reason=insufficient,
                effective_criticality=effective_criticality,
                lab_mode=lab_mode,
            )

        state = (
            ReplayState.COMPLETE
            if covered_days == window_days and not_judged == 0
            else ReplayState.PARTIAL
        )
        current_custom = dict(source.custom_thresholds or {})
        combined = _merge_custom(current_custom, proposed or {})
        resolver = ThresholdResolver(self.settings)
        # The engine resolves twice per run: once without a type for the
        # detectors, once per type for open_after/resolve_after. The current
        # run keeps the stored criticality. The proposed run uses the body's
        # value in both of those calls when one was given.
        outage_default = OutagePolicyMode(self.settings.outage_alert_default)
        current_thresholds, current_by_type = _thresholds(
            resolver,
            source,
            current_custom,
            policies,
            source_class,
            outage_default,
        )
        proposed_thresholds, proposed_by_type = _thresholds(
            resolver,
            source,
            combined,
            policies,
            source_class,
            outage_default,
            criticality=criticality,
        )
        current_counts, judged, skipped = fold_buckets(
            present, thresholds=current_thresholds, by_type=current_by_type
        )
        proposed_counts, proposed_judged, _ = fold_buckets(
            present, thresholds=proposed_thresholds, by_type=proposed_by_type
        )
        not_replayable = _not_replayable(current_thresholds, judged, skipped)
        return ReplayResult(
            state=state,
            available=True,
            window=window,
            baseline=baseline,
            current=_as_counts(
                current_counts, judged, direction=current_thresholds.direction
            ),
            proposed=_as_counts(
                proposed_counts,
                proposed_judged,
                direction=proposed_thresholds.direction,
            ),
            recorded=recorded,
            not_replayable=not_replayable,
            not_judged_buckets=not_judged,
            maintenance_buckets=maintenance_buckets,
            warnings=warnings,
            reason=(
                reason(REASON_PARTIAL, covered=covered_days, window=window_days)
                if state is ReplayState.PARTIAL and covered_days < window_days
                else None
            ),
            effective_criticality=effective_criticality,
            lab_mode=lab_mode,
        )

    def _insufficient(
        self,
        *,
        source: LogSource,
        maintained: bool,
        covered_days: int,
        window_days: int,
        not_judged: int,
        total_buckets: int,
    ) -> ReplayReason | None:
        if not source.monitoring_enabled:
            return reason(REASON_NOT_MONITORED)
        if maintained:
            return reason(REASON_MAINTENANCE)
        if covered_days < MIN_COVERED_DAYS:
            return reason(
                REASON_INSUFFICIENT_COVERAGE, covered=covered_days, window=window_days
            )
        if not_judged > MAX_NOT_JUDGED_FRACTION * total_buckets:
            return reason(REASON_NOT_JUDGED, count=not_judged, total=total_buckets)
        return None

    async def _source(
        self, log_source_id: uuid.UUID
    ) -> tuple[LogSource, list[OutageAlertPolicy], str | None, int | None] | None:
        """Statement 1: the source, its policy rows, its class and its bucket width."""
        latest_width = (
            select(LogSourceMetric.bucket_seconds)
            .where(
                LogSourceMetric.log_source_id == LogSource.id,
                is_live_clause(LogSourceMetric),
            )
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
            .correlate(LogSource)
            .scalar_subquery()
        )
        rows = (
            await self.session.execute(
                select(
                    LogSource,
                    OutageAlertPolicy,
                    LogSourceClassification.source_class,
                    latest_width.label("latest_bucket_seconds"),
                )
                .select_from(LogSource)
                .outerjoin(OutageAlertPolicy, true())
                .outerjoin(
                    LogSourceClassification,
                    LogSourceClassification.log_source_id == LogSource.id,
                )
                .where(LogSource.id == log_source_id)
            )
        ).all()
        if not rows:
            return None
        source = rows[0][0]
        policies = [row[1] for row in rows if row[1] is not None]
        source_class = rows[0][2]
        if source_class is not None and not isinstance(source_class, str):
            source_class = source_class.value
        return source, policies, source_class, rows[0].latest_bucket_seconds

    async def _metrics(
        self,
        log_source_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
        interval_seconds: int,
    ) -> list[LogSourceMetric]:
        """Statement 2: the window plus the streak/signature history before it."""
        after = window_start - timedelta(seconds=interval_seconds * PRECEDING_BUCKETS)
        return list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start >= after,
                        LogSourceMetric.bucket_start < window_end,
                        is_live_clause(LogSourceMetric),
                    )
                    .order_by(LogSourceMetric.bucket_start.asc())
                )
            ).all()
        )

    async def _cells(
        self, log_source_id: uuid.UUID
    ) -> dict[tuple[int, int, str], LogSourceBaseline]:
        """Statement 3: every baseline cell the replayable detectors read."""
        rows = (
            await self.session.scalars(
                select(LogSourceBaseline).where(
                    LogSourceBaseline.log_source_id == log_source_id,
                    LogSourceBaseline.metric_name.in_(BASELINE_METRICS),
                )
            )
        ).all()
        return {
            (row.weekday, row.hour, row.metric_name): row for row in rows
        }

    async def _anomalies(
        self,
        log_source_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
    ) -> list[LogSourceAnomaly]:
        """Statement 4: incidents whose spans intersect the window."""
        return list(
            (
                await self.session.scalars(
                    select(LogSourceAnomaly).where(
                        LogSourceAnomaly.log_source_id == log_source_id,
                        LogSourceAnomaly.detected_at < window_end,
                        (
                            LogSourceAnomaly.resolved_at.is_(None)
                            | (LogSourceAnomaly.resolved_at >= window_start)
                        ),
                    )
                )
            ).all()
        )

    def _bucket_inputs(
        self,
        source: LogSource,
        metrics: Sequence[LogSourceMetric],
        cells: Mapping[tuple[int, int, str], LogSourceBaseline],
        grid: Sequence[datetime],
    ) -> list[BucketInput | None]:
        """Build each grid bucket's inputs in one pass over the ordered rows."""
        starts = [row.bucket_start for row in metrics]
        cells_by_key: dict[tuple[int, int], dict[str, BaselineCell]] = {}
        for (weekday, hour, metric), cell_row in cells.items():
            cells_by_key.setdefault((weekday, hour), {})[metric] = baseline_cell(
                cell_row
            )
        buckets: list[BucketInput | None] = []
        previous_rows: deque[LogSourceMetric] = deque(maxlen=PRECEDING_BUCKETS)
        signatures: deque[str] = deque(maxlen=SIGNATURE_BUCKETS)
        cursor = 0
        for start in grid:
            while cursor < len(metrics) and starts[cursor] < start:
                row = metrics[cursor]
                previous_rows.append(row)
                if row.payload_signature is not None:
                    signatures.append(row.payload_signature)
                cursor += 1
            if cursor >= len(metrics) or starts[cursor] != start:
                buckets.append(None)
                continue
            row = metrics[cursor]
            streak = 0
            for previous in reversed(previous_rows):
                if not previous.is_complete or previous.event_count > 0:
                    break
                streak += 1
            point = metric_point(row)
            complete = point.completeness is BucketCompleteness.COMPLETE
            buckets.append(
                BucketInput(
                    start=start,
                    point=point,
                    cells=cells_by_key.get((start.isoweekday(), start.hour), {}),
                    expected_interval_seconds=float(
                        source.expected_interval_seconds or row.bucket_seconds or 300
                    ),
                    recent_signatures=tuple(signatures),
                    is_expected_active=is_expected_active(source, start),
                    preceding_empty_buckets=streak,
                    telemetry=bucket_telemetry(
                        row.collection_source, row.query_provenance
                    ),
                    complete=complete,
                )
            )
        return buckets


def _thresholds(
    resolver: ThresholdResolver,
    source: LogSource,
    custom: Mapping[str, Any],
    policies: Sequence[OutageAlertPolicy],
    source_class: str | None,
    default_mode: OutagePolicyMode,
    criticality: Criticality | None = None,
) -> tuple[Thresholds, dict[AnomalyType, Thresholds]]:
    """The top-level thresholds (with the outage grace) and the per-type hysteresis.

    This is the resolver call the engine makes twice: once without a type for the
    detectors, once per type for `open_after`/`resolve_after` (design §1.3). The
    outage policy's grace override is scoped to the top-level set exactly as the
    engine scopes it. ``criticality`` None keeps the stored column; a value is
    used in both calls and is not written back onto the source.
    """
    outage = resolve_outage(
        policies,
        log_source_id=source.id,
        source_class=source_class,
        instance_id=source.instance_id,
        default=default_mode,
    )
    resolved = source.criticality if criticality is None else criticality
    thresholds = resolver.resolve(criticality=resolved, custom=dict(custom))
    if outage.silence_grace_buckets is not None:
        thresholds = replace(
            thresholds, silence_grace_buckets=outage.silence_grace_buckets
        )
    by_type = {
        kind: resolver.resolve(
            criticality=resolved, custom=dict(custom), anomaly_type=kind
        )
        for kind in AnomalyType
    }
    return thresholds, by_type


def _recorded(
    anomalies: Sequence[LogSourceAnomaly],
    window_start: datetime,
    window_end: datetime,
) -> RecordedCounts:
    in_window = [
        incident
        for incident in anomalies
        if incident.detected_at >= window_start and incident.detected_at < window_end
    ]
    return RecordedCounts(
        incidents=len(in_window),
        opened=sum(1 for incident in in_window if incident.opened_at is not None),
        suppressed=sum(1 for incident in in_window if incident.suppressed),
    )


def _maintenance_buckets(
    anomalies: Sequence[LogSourceAnomaly],
    grid: Sequence[datetime],
    window_end: datetime,
) -> int:
    """Buckets whose recorded incident was suppressed by a maintenance window."""
    marks: set[datetime] = set()
    for incident in anomalies:
        if not incident.suppressed:
            continue
        start = incident.detected_at
        end = incident.resolved_at or incident.anomaly_end or window_end
        first = bisect_right(grid, start) - 1
        if first < 0:
            first = 0
        for bucket in grid[first:]:
            if bucket >= end:
                break
            marks.add(bucket)
    return len(marks)


def _warnings(maintenance_buckets: int, total_buckets: int) -> tuple[ReplayReason, ...]:
    if total_buckets and maintenance_buckets > MAX_NOT_JUDGED_FRACTION * total_buckets:
        return (reason(REASON_MAINTENANCE_HISTORY, count=maintenance_buckets),)
    return ()


def _not_replayable(
    thresholds: Thresholds,
    judged: Mapping[AnomalyType, int],
    skipped: Mapping[AnomalyType, str],
) -> tuple[NotReplayable, ...]:
    excluded = direction_excluded(thresholds.direction)
    found: list[NotReplayable] = []
    for kind in AnomalyType:
        if judged.get(kind, 0) > 0:
            continue
        if kind in excluded:
            found.append(
                NotReplayable(
                    anomaly_type=kind, reason=reason(REASON_DIRECTION_DISABLED)
                )
            )
            continue
        found.append(
            NotReplayable(
                anomaly_type=kind,
                reason=reason(
                    REASON_NOT_REPLAYABLE,
                    reason=skipped.get(kind, "no stored bucket could be judged"),
                ),
            )
        )
    return tuple(found)
