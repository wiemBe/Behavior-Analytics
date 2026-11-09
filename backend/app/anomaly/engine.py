"""Anomaly evaluation engine.

For one log source and one collection interval it:
  1. Builds a DetectionContext from the interval's metric, the matching
     (weekday, hour) baselines, and recent payload signatures.
  2. Runs every detector, suppressing the ones NO_EVENTS makes redundant.
  3. Applies hysteresis per (source, anomaly type) via LogSourceDetectorState:
     open only after N consecutive anomalous intervals, resolve only after M
     consecutive healthy ones. N and M come from the threshold resolver
     (per type and per criticality), so alerts do not flap.
  4. Persists LogSourceAnomaly rows and drives the alert lifecycle.

Evaluation is idempotent per interval: re-running the same interval is a no-op
because the detector state records the last interval processed.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fingerprint import anomaly_fingerprint, fleet_empty_fingerprint
from app.alerts.policy import AlertPolicyGate
from app.alerts.service import AlertInput, AlertService
from app.alerts.suppression import SuppressionGate, record_hit
from app.anomaly.business_hours import is_expected_active
from app.anomaly.capabilities import bucket_telemetry, unmet_requirement
from app.anomaly.detectors import (
    DETECTORS,
    SUPPRESSED_BY_NO_EVENTS,
    BaselineCell,
    DetectionContext,
    MetricPoint,
)
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.faults import FaultDecision, next_attempt
from app.anomaly.lifecycle import (
    LifecycleDecision,
    LifecycleInput,
    advance_counters,
    next_state,
)
from app.anomaly.seasonal import in_maintenance
from app.anomaly.thresholds import ThresholdResolver, direction_excluded
from app.core.config import Settings, get_settings
from app.fleet.outage_policy import ResolvedOutage, resolve
from app.history.origin import is_live_clause
from app.i18n.messages import MessageRef, render
from app.models.alert import Alert
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AlertPolicyMode,
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Criticality,
    DetectorSignal,
    EvidenceStatus,
    OutagePolicyMode,
    OutagePolicyScope,
    Severity,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.monitoring import AnomalyEvaluationWatermark
from app.models.outage import OutageAlertPolicy
from app.providers.base import LogSourceTelemetry
from app.repositories.anomaly_faults import AnomalyFaultRepository

if TYPE_CHECKING:
    # Annotations only: the engine depends on delivery structurally, never on
    # the dispatcher's module, so there is no need to import it at runtime.
    from app.alerts.dispatcher import AlertEnqueuer

logger = logging.getLogger(__name__)

#: Sentences this engine writes itself (the detectors' and the lifecycle's are in their modules).
MESSAGE_SUPPRESSED_BY_NO_EVENTS = MessageRef("anomaly.detector.suppressed_by_no_events")
MESSAGE_MAINTENANCE = MessageRef("anomaly.suppression.maintenance")
MESSAGE_RECOVERED = MessageRef("anomaly.resolution.recovered")

#: Detector types whose incidents warrant a bounded explanation package. A
#: silence has no volume to attribute, so there is nothing to explain.
EXPLAINABLE_TYPES = frozenset({AnomalyType.VOLUME_SPIKE, AnomalyType.VOLUME_DROP})


@dataclass
class EvaluationReport:
    log_source_id: uuid.UUID
    interval_start: datetime | None
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    anomalous: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    recovering: list[str] = field(default_factory=list)
    #: Final lifecycle state per detector type after this bucket.
    states: dict[str, str] = field(default_factory=dict)
    #: Detectors not run on this bucket, with the input they lacked. They get no
    #: verdict and no state change: a detector that was never given its input
    #: must not report the source as healthy.
    unsupported: dict[str, str] = field(default_factory=dict)
    #: Buckets whose evaluation raised during this tick.
    failed: int = 0
    #: ... of which the engine gave up on, so they stay "not judged".
    given_up: int = 0
    skipped_reason: str | None = None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None


def _is_worse(anomaly: LogSourceAnomaly, observed: float) -> bool:
    """Whether `observed` is a more extreme reading than the one on record.

    Direction matters: for a spike the peak is the worst case, for a drop and a
    silence it is the trough.
    """
    current = anomaly.observed_value
    if current is None:
        return True
    # `==`: anomaly_type is a String column, so this is a plain str on load.
    if anomaly.anomaly_type == AnomalyType.VOLUME_SPIKE:
        return observed > current
    return observed < current


# ------------------------------------------------------- pure context builders
# The replay (`app.noise.backtest`) must reconstruct the engine's per-bucket
# context and verdict exactly, never a copy of them. These three helpers are the
# extraction: the engine and the replay both call them, so a change in either
# path cannot make the two disagree.
def metric_point(metric: LogSourceMetric) -> MetricPoint:
    """The detector-facing point of one stored metric row."""
    return MetricPoint(
        interval_start=metric.bucket_start,
        interval_end=metric.bucket_start + timedelta(seconds=metric.bucket_seconds),
        # Coerced, not passed through: String-typed enum columns come back
        # from the database as plain str, and the detectors compare
        # identity. Normalizing here keeps that logic pure and safe.
        completeness=BucketCompleteness(metric.completeness),
        event_count=metric.event_count,
        average_eps=metric.average_eps,
        peak_eps=metric.peak_eps,
        last_event_at=metric.last_event_at,
        event_delay_seconds=metric.event_delay_seconds,
        unknown_event_count=metric.unknown_event_count,
        stored_event_count=metric.stored_event_count,
        parsed_username_ratio=metric.parsed_username_ratio,
        parsed_source_ip_ratio=metric.parsed_source_ip_ratio,
        distinct_qid_count=metric.distinct_qid_count,
        distinct_username_count=metric.distinct_username_count,
        distinct_source_ip_count=metric.distinct_source_ip_count,
        collection_error_count=metric.collection_error_count,
        payload_signature=metric.payload_signature,
        fleet_empty=fleet_empty_bucket(metric),
    )


def fleet_empty_bucket(metric: LogSourceMetric) -> bool:
    """Whether the collector found no monitored source in this bucket (D-62).

    Only a PARTIAL row carrying the provenance flag counts: the flag alone, on a COMPLETE
    row, is not how the collector writes it, so a stray key cannot silence a source.
    """
    provenance = metric.query_provenance
    return (
        BucketCompleteness(metric.completeness) is BucketCompleteness.PARTIAL
        and isinstance(provenance, dict)
        and provenance.get("fleet_empty") is True
    )


def baseline_cell(row: LogSourceBaseline) -> BaselineCell:
    """The detector-facing cell of one stored baseline row."""
    return BaselineCell(
        median=row.median, mad=row.mad, p05=row.p05, p95=row.p95,
        sample_count=row.sample_count, is_reliable=row.is_reliable,
        completeness=row.completeness, baseline_version=row.baseline_version,
    )


def run_detectors(
    ctx: DetectionContext, telemetry: frozenset[LogSourceTelemetry]
) -> tuple[list[AnomalyEvidence], dict[str, str]]:
    """Run the detectors this bucket can support, and report the ones it cannot.

    Detectors are pure, so each runs and its verdict is then kept only if the
    bucket measured every input it read. A dropped verdict never reaches
    hysteresis, so its state neither advances nor changes. The direction knob
    drops the excluded volume verdict for the same reason: the detector did not
    run, so it writes nothing that bucket.
    """
    excluded = direction_excluded(ctx.thresholds.direction)
    results: list[AnomalyEvidence] = []
    unsupported: dict[str, str] = {}
    for evidence in (d(ctx) for d in DETECTORS):
        reason = excluded.get(evidence.anomaly_type)
        if reason is None:
            reason = unmet_requirement(evidence.anomaly_type, telemetry)
        if reason is None:
            results.append(evidence)
        else:
            unsupported[evidence.anomaly_type.value] = reason
    no_events = next(
        (e for e in results if e.anomaly_type == AnomalyType.NO_EVENTS), None
    )
    if (no_events is not None and no_events.is_anomalous) or ctx.point.fleet_empty:
        # Demote the redundant detectors to UNKNOWN so their hysteresis
        # counters do not advance on a trivially-satisfied condition.
        for e in results:
            if e.anomaly_type in SUPPRESSED_BY_NO_EVENTS:
                e.signal = DetectorSignal.UNKNOWN
                # The text and the reference are replaced together: the demoted verdict
                # must not keep the detector's own code beside a different sentence.
                e.reason_message = MESSAGE_SUPPRESSED_BY_NO_EVENTS
                e.reason = render(e.reason_message)
    return results, unsupported


class AnomalyEngine:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        enqueuer: AlertEnqueuer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._resolver = ThresholdResolver(self.settings)
        self.alerts = AlertService(session, clock=self._clock)
        # Optional object with `async enqueue(alert, transition)`. When set,
        # alert transitions produce notification rows. None in unit tests.
        self._enqueuer = enqueuer
        self._source_class_cache: dict[uuid.UUID, str | None] = {}
        # Phase C.2 outage policy: the rows are loaded once per engine instance
        # (one statement for the whole run) and the per-source verdict is
        # memoised, so a refresh every bucket still costs one load (rule 9).
        self._policy_rows: list[OutageAlertPolicy] | None = None
        self._outage_cache: dict[uuid.UUID, ResolvedOutage] = {}
        # SOC-17c: the alert policy table, loaded once per engine run and
        # memoised per (source, detector). With no rows the default is ALERT, so
        # this changes nothing; the gate is only reached on an alert transition.
        self._alert_policy = AlertPolicyGate(
            session, default=AlertPolicyMode(self.settings.alert_policy_default)
        )
        # SOC-17d: the open suppression rows, loaded once per engine run. With
        # no rows the gate returns None after that one load, so this changes
        # nothing; `suppressed` and AnomalyState.SUPPRESSED stay maintenance's.
        self._suppression = SuppressionGate(session)
        # The class map outage resolution reads, loaded in one statement the
        # first time a CLASS policy row needs a source's class and reused for
        # the rest of the run. Distinct from `_source_class_cache`, which the
        # alert context fills one source at a time.
        self._class_map: dict[uuid.UUID, str] | None = None
        # Faults awaiting a retry. Loaded once per engine instance in one
        # statement (rule 9), so the success path can tell whether a source
        # needs its fault cleared without a query per bucket.
        self._faults = AnomalyFaultRepository(session)
        self._open_faults: set[uuid.UUID] | None = None
        # D-62: the open fleet-empty alert per QRadar instance, loaded once per engine run
        # (one statement whatever the fleet size, rule 9). It also remembers the newest bucket
        # judged, so N sources judging one empty bucket count it once.
        self._fleet_alerts: dict[uuid.UUID, Alert] | None = None

    async def evaluate_latest(self, log_source: LogSource) -> EvaluationReport:
        """Test-only: no collector calls this; tests pin that it reads live rows."""
        metric = await self.session.scalar(
            select(LogSourceMetric)
            .where(
                LogSourceMetric.log_source_id == log_source.id,
                is_live_clause(LogSourceMetric),
            )
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
        )
        if metric is None:
            return EvaluationReport(log_source.id, None, skipped_reason="no metrics")
        return await self.evaluate_interval(log_source, metric)

    async def evaluate_pending(self, log_source: LogSource) -> EvaluationReport:
        """Evaluate unseen buckets in order and advance only after each success."""
        if not log_source.monitoring_enabled:
            return EvaluationReport(
                log_source.id, None, skipped_reason="monitoring disabled"
            )

        watermark = await self.session.get(AnomalyEvaluationWatermark, log_source.id)
        if watermark is None:
            metric = await self.session.scalar(
                select(LogSourceMetric)
                .where(
                    LogSourceMetric.log_source_id == log_source.id,
                    is_live_clause(LogSourceMetric),
                )
                .order_by(LogSourceMetric.bucket_start.desc())
                .limit(1)
            )
            if metric is None:
                return EvaluationReport(log_source.id, None, skipped_reason="no metrics")
            try:
                async with self.session.begin_nested():
                    report = await self.evaluate_interval(log_source, metric)
                    self.session.add(
                        AnomalyEvaluationWatermark(
                            log_source_id=log_source.id,
                            evaluated_through=metric.bucket_start,
                            updated_at=self._clock(),
                        )
                    )
                    await self.session.flush()
            except Exception as exc:
                given_up = await self._record_failure(log_source, metric, exc)
                if given_up:
                    # Without a watermark every later tick would retry this
                    # bucket forever. Seed the mark at the bucket that was given
                    # up so newer buckets are evaluated on the next tick.
                    self.session.add(
                        AnomalyEvaluationWatermark(
                            log_source_id=log_source.id,
                            evaluated_through=metric.bucket_start,
                            updated_at=self._clock(),
                        )
                    )
                    await self.session.flush()
                return EvaluationReport(
                    log_source.id,
                    metric.bucket_start,
                    failed=1,
                    given_up=1 if given_up else 0,
                    skipped_reason="evaluation failed",
                )
            await self._clear_failure(log_source)
            return report

        metrics = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source.id,
                        LogSourceMetric.bucket_start > watermark.evaluated_through,
                        is_live_clause(LogSourceMetric),
                    )
                    .order_by(LogSourceMetric.bucket_start.asc())
                    .limit(self.settings.anomaly_max_buckets_per_evaluation)
                )
            ).all()
        )
        combined = EvaluationReport(log_source.id, None)
        if not metrics:
            combined.skipped_reason = "no pending metrics"
            return combined

        for metric in metrics:
            try:
                async with self.session.begin_nested():
                    report = await self.evaluate_interval(log_source, metric)
                    watermark.evaluated_through = metric.bucket_start
                    watermark.updated_at = self._clock()
                    await self.session.flush()
            except Exception as exc:
                combined.failed += 1
                if not await self._record_failure(log_source, metric, exc):
                    combined.skipped_reason = "evaluation failed"
                    break
                # Given up: the bucket stays "not judged" (nothing is written
                # for it), and the watermark moves past it so the next bucket in
                # this same tick is attempted.
                combined.given_up += 1
                watermark.evaluated_through = metric.bucket_start
                watermark.updated_at = self._clock()
                await self.session.flush()
                continue
            await self._clear_failure(log_source)
            combined.interval_start = report.interval_start
            combined.opened.extend(report.opened)
            combined.resolved.extend(report.resolved)
            combined.anomalous.extend(report.anomalous)
            combined.candidates.extend(report.candidates)
            combined.recovering.extend(report.recovering)
            combined.states = report.states
            combined.unsupported = report.unsupported
        return combined

    # ---------------------------------------------------------------- faults
    async def _open_fault_sources(self) -> set[uuid.UUID]:
        """Sources with a fault awaiting a retry, loaded once per engine run.

        One statement whatever the fleet size (rule 9). The recovery update is
        only issued for a source in this set, so a healthy bucket costs none.
        """
        if self._open_faults is None:
            self._open_faults = await self._faults.open_source_ids()
        return self._open_faults

    async def _record_failure(
        self, log_source: LogSource, metric: LogSourceMetric, exc: Exception
    ) -> bool:
        """Record one failed attempt; return whether the bucket was given up.

        Runs after the bucket's savepoint rolled back, so the fault row is the
        only trace of it. The log line names the bucket and the attempt count:
        one repeating bucket used to be indistinguishable from a new failure.

        The bookkeeping has its own savepoint. If it fails (the table is
        unavailable, the source was deleted and the FK refuses the upsert), the
        bucket is held exactly as it was before T-171 -- break, keep the
        watermark, report "evaluation failed" -- so one unrecordable fault
        cannot cost every other source of the tick its verdict.
        """
        now = self._clock()
        error_class = type(exc).__name__
        try:
            async with self.session.begin_nested():
                attempts = await self._faults.record_failure(
                    log_source.id, metric.bucket_start, error_class=error_class, at=now
                )
                given_up = (
                    next_attempt(attempts, self.settings.anomaly_max_bucket_attempts)
                    is FaultDecision.GIVE_UP
                )
                open_faults = await self._open_fault_sources()
                if given_up:
                    await self._faults.give_up(log_source.id, metric.bucket_start, at=now)
                    open_faults.discard(log_source.id)
                else:
                    open_faults.add(log_source.id)
        except Exception as bookkeeping:
            logger.warning(
                "anomaly fault bookkeeping failed",
                extra={
                    "log_source_id": str(log_source.id),
                    "bucket_start": metric.bucket_start.isoformat(),
                    "error_class": type(bookkeeping).__name__,
                    "evaluation_error_class": error_class,
                },
            )
            return False
        logger.warning(
            "anomaly evaluation failed",
            extra={
                "log_source_id": str(log_source.id),
                "bucket_start": metric.bucket_start.isoformat(),
                "attempts": attempts,
                "given_up": given_up,
                "error_class": error_class,
            },
        )
        return given_up

    async def _clear_failure(self, log_source: LogSource) -> None:
        """Close the fault this source was retrying, now that a bucket judged.

        Only a source the bulk load marked as open pays the update. A given-up
        row stays as the record of a bucket with no verdict until retention
        removes it.
        """
        open_faults = await self._open_fault_sources()
        if log_source.id not in open_faults:
            return
        await self._faults.resolve(log_source.id, at=self._clock())
        open_faults.discard(log_source.id)

    async def evaluate_interval(
        self, log_source: LogSource, metric: LogSourceMetric
    ) -> EvaluationReport:
        report = EvaluationReport(log_source.id, metric.bucket_start)

        if not log_source.monitoring_enabled:
            report.skipped_reason = "monitoring disabled"
            return report

        in_maintenance = self._in_maintenance(log_source, metric.bucket_start)

        ctx = await self._build_context(log_source, metric)
        telemetry = bucket_telemetry(metric.collection_source, metric.query_provenance)
        evidences = self._run_detectors(ctx, telemetry, report)

        for evidence in evidences:
            if evidence.is_anomalous:
                report.anomalous.append(evidence.anomaly_type.value)
            await self._apply_hysteresis(log_source, metric, evidence, in_maintenance, report)

        await self._fleet_step(log_source, metric, ctx, evidences, in_maintenance)
        await self.session.flush()
        return report

    # ----------------------------------------------------------- fleet empty
    async def _open_fleet_alerts(self) -> dict[uuid.UUID, Alert]:
        """Open fleet-empty alerts by instance id, loaded once per engine run."""
        if self._fleet_alerts is None:
            rows = await self.session.scalars(
                select(Alert).where(
                    Alert.source_type == "qradar_instance",
                    Alert.fingerprint.like("fleet_empty:%"),
                    Alert.status != AlertStatus.RESOLVED,
                )
            )
            self._fleet_alerts = {
                alert.source_id: alert for alert in rows if alert.source_id is not None
            }
        return self._fleet_alerts

    @staticmethod
    def _fleet_alert_bucket(alert: Alert) -> datetime | None:
        """The newest bucket the alert has judged, from its evidence."""
        raw = alert.evidence_snapshot.get("bucket_start")
        if not isinstance(raw, str):
            return None
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)

    async def _fleet_step(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        ctx: DetectionContext,
        evidences: list[AnomalyEvidence],
        in_maintenance: bool,
    ) -> None:
        """Open, refresh or resolve the instance's fleet-empty alert (D-62).

        A bucket the collector flagged opens or refreshes it when this source expects
        traffic (the detector says so; a source in maintenance never counts). A complete,
        unflagged bucket newer than the alert's last one resolves it. Each runs once per
        (instance, bucket), whatever the number of sources judging that bucket.
        """
        instance_id = log_source.instance_id
        bucket = metric.bucket_start
        if ctx.point.fleet_empty:
            expects = not in_maintenance and any(
                e.anomaly_type == AnomalyType.NO_EVENTS and e.extra.get("fleet_empty") is True
                for e in evidences
            )
            if not expects:
                return
            open_alerts = await self._open_fleet_alerts()
            existing = open_alerts.get(instance_id)
            if existing is not None:
                judged = self._fleet_alert_bucket(existing)
                if judged is not None and bucket <= judged:
                    return
            title = MessageRef("alert.fleet_empty.title", {"bucket": bucket.isoformat()})
            description = MessageRef(
                "alert.fleet_empty.description", {"bucket": bucket.isoformat()}
            )
            result = await self.alerts.open_or_update(
                AlertInput(
                    fingerprint=fleet_empty_fingerprint(instance_id),
                    title=render(title),
                    title_message=title,
                    description=render(description),
                    description_message=description,
                    severity=Severity.HIGH,
                    source_type="qradar_instance",
                    source_id=instance_id,
                    evidence={
                        "bucket_start": bucket.isoformat(),
                        "bucket_end": (
                            bucket + timedelta(seconds=metric.bucket_seconds)
                        ).isoformat(),
                    },
                )
            )
            open_alerts[instance_id] = result.alert
            if self._enqueuer is not None and result.transition is not None:
                await self._enqueuer.enqueue(result.alert, result.transition)
            return

        if metric.completeness != BucketCompleteness.COMPLETE:
            return
        open_alerts = await self._open_fleet_alerts()
        existing = open_alerts.get(instance_id)
        if existing is None:
            return
        judged = self._fleet_alert_bucket(existing)
        if judged is not None and bucket <= judged:
            return
        resolved = await self.alerts.resolve_by_fingerprint(
            fleet_empty_fingerprint(instance_id),
            actor="system:anomaly-engine",
            reason_message=MessageRef("alert.fleet_empty.recovered"),
        )
        open_alerts.pop(instance_id, None)
        if self._enqueuer is not None and resolved is not None and resolved.transition:
            await self._enqueuer.enqueue(resolved.alert, resolved.transition)

    # ------------------------------------------------------------- context
    async def _build_context(
        self, log_source: LogSource, metric: LogSourceMetric
    ) -> DetectionContext:
        weekday = metric.bucket_start.isoweekday()
        hour = metric.bucket_start.hour
        cells = await self._baseline_cells(log_source.id, weekday, hour)

        recent = await self._recent_signatures(log_source.id, metric.bucket_start)
        thresholds = self._resolver.resolve(
            criticality=log_source.criticality, custom=log_source.custom_thresholds
        )
        # A resolved grace overrides the configured one for this source's
        # NO_EVENTS evaluation. Only NO_EVENTS reads this field, so the override
        # is exactly scoped and every volume detector is untouched.
        outage = await self._outage(log_source)
        if outage.silence_grace_buckets is not None:
            thresholds = replace(
                thresholds, silence_grace_buckets=outage.silence_grace_buckets
            )
        expected_interval = float(
            log_source.expected_interval_seconds or metric.bucket_seconds or 300
        )

        point = metric_point(metric)
        return DetectionContext(
            point=point,
            baselines=cells,
            thresholds=thresholds,
            expected_interval_seconds=expected_interval,
            recent_signatures=recent,
            is_expected_active=self._is_expected_active(log_source, metric.bucket_start),
            preceding_empty_buckets=await self._preceding_empty_buckets(
                log_source.id, metric
            ),
        )

    async def _preceding_empty_buckets(
        self, log_source_id: uuid.UUID, metric: LogSourceMetric, limit: int = 12
    ) -> int:
        """Consecutive empty buckets immediately before this one.

        The streak counts live rows only (HISTORY-BACKFILL §B.4.1): a silence claim built
        partly from QRadar history would lean on an old event.

        Only complete buckets count towards the streak. A gap we failed to
        collect is not evidence that the source was silent, and letting it
        extend the streak would make a collector outage graduate into NO_EVENTS.
        The walk is therefore gap-aware: each row must end exactly where the
        bucket after it starts, or the streak stops there.
        """
        rows = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start < metric.bucket_start,
                        is_live_clause(LogSourceMetric),
                    )
                    .order_by(LogSourceMetric.bucket_start.desc())
                    .limit(limit)
                )
            ).all()
        )
        streak = 0
        expected_next = metric.bucket_start
        for row in rows:
            if row.bucket_start + timedelta(seconds=row.bucket_seconds) != expected_next:
                break
            if not row.is_complete or row.event_count > 0:
                break
            streak += 1
            expected_next = row.bucket_start
        return streak

    async def _baseline_cells(
        self, log_source_id: uuid.UUID, weekday: int, hour: int
    ) -> dict[str, BaselineCell]:
        rows = await self.session.scalars(
            select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == log_source_id,
                LogSourceBaseline.weekday == weekday,
                LogSourceBaseline.hour == hour,
            )
        )
        cells: dict[str, BaselineCell] = {}
        for b in rows:
            cells[b.metric_name] = baseline_cell(b)
        return cells

    async def _recent_signatures(
        self, log_source_id: uuid.UUID, before: datetime, limit: int = 6
    ) -> list[str]:
        rows = list(
            (
                await self.session.scalars(
                    select(LogSourceMetric.payload_signature)
                    .where(
                        LogSourceMetric.log_source_id == log_source_id,
                        LogSourceMetric.bucket_start < before,
                        LogSourceMetric.payload_signature.is_not(None),
                    )
                    .order_by(LogSourceMetric.bucket_start.desc())
                    .limit(limit)
                )
            ).all()
        )
        # The query excludes NULL signatures; the guard restates that guarantee
        # in the return type so it does not depend on the reader knowing the SQL.
        return [s for s in reversed(rows) if s is not None]  # oldest -> newest

    def _run_detectors(
        self,
        ctx: DetectionContext,
        telemetry: frozenset[LogSourceTelemetry],
        report: EvaluationReport,
    ) -> list[AnomalyEvidence]:
        """Run the detectors this bucket can support (see `run_detectors`)."""
        results, unsupported = run_detectors(ctx, telemetry)
        report.unsupported.update(unsupported)
        return results

    # ------------------------------------------------------------ hysteresis
    async def _apply_hysteresis(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        state = await self._get_state(log_source.id, evidence.anomaly_type)

        # Idempotency: this interval already folded into the counters.
        if state.last_interval_start is not None and \
                state.last_interval_start >= metric.bucket_start:
            return

        thresholds = self._resolver.resolve(
            criticality=log_source.criticality,
            custom=log_source.custom_thresholds,
            anomaly_type=evidence.anomaly_type,
        )

        signal = evidence.signal
        # In maintenance, do not advance the anomalous counter (never open), but
        # allow recovery to proceed so a pre-existing anomaly can clear.
        if in_maintenance and signal is DetectorSignal.ANOMALOUS:
            signal = DetectorSignal.UNKNOWN

        state.consecutive_anomalous, state.consecutive_healthy = advance_counters(
            signal, state.consecutive_anomalous, state.consecutive_healthy
        )

        state.last_interval_start = metric.bucket_start

        decision = next_state(
            LifecycleInput(
                state=AnomalyState(state.state),
                signal=signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=thresholds.open_after,
                resolve_after=thresholds.resolve_after,
                insufficient_data=evidence.insufficient_data,
                suppressed=(
                    in_maintenance
                    and AnomalyState(state.state) not in ACTIVE_ANOMALY_STATES
                ),
            )
        )
        await self._apply_transition(
            log_source, metric, evidence, state, decision, in_maintenance, report
        )
        await self.session.flush()

    async def _apply_transition(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        decision: LifecycleDecision,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        """Persist one lifecycle move, its audit row, and its side effects."""
        previous = AnomalyState(state.state)
        target = decision.state
        report.states[evidence.anomaly_type.value] = target.value

        if target is previous:
            # No move. An already-open incident still gets its evidence
            # refreshed so an operator can see how long it has persisted.
            if previous is AnomalyState.OPEN:
                await self._refresh(log_source, evidence, state, in_maintenance)
                await self._touch_anomaly(state, metric, evidence)
            return

        state.state = target

        if target is AnomalyState.CANDIDATE:
            created = await self._create_incident(
                log_source, metric, evidence, in_maintenance
            )
            state.active_anomaly_id = created.id
            await self._record_transition(
                created, previous, target, metric, decision.message, evidence
            )
            report.candidates.append(evidence.anomaly_type.value)
            return

        anomaly = await self._active_anomaly(state)

        if target is AnomalyState.OPEN:
            # Confirmation threshold of 1: the incident opens on the same bucket
            # that created it, so there is nothing to promote yet.
            incident = anomaly or await self._create_incident(
                log_source, metric, evidence, in_maintenance
            )
            state.active_anomaly_id = incident.id
            await self._promote_to_open(
                log_source, metric, evidence, state, incident, in_maintenance, report
            )
            await self._record_transition(
                incident, previous, target, metric, decision.message, evidence
            )
            return

        if anomaly is not None:
            await self._record_transition(
                anomaly, previous, target, metric, decision.message, evidence
            )

        if target is AnomalyState.RECOVERING:
            if anomaly is not None:
                anomaly.state = AnomalyState.RECOVERING
                # The abnormal telemetry ended with the last abnormal bucket,
                # not with the normal one that revealed the recovery.
                anomaly.anomaly_end = metric.bucket_start
            report.recovering.append(evidence.anomaly_type.value)
            return

        if target is AnomalyState.RESOLVED:
            await self._resolve(log_source, evidence, state, anomaly, report)
            return

        if target is AnomalyState.SUPPRESSED:
            if anomaly is not None:
                anomaly.state = AnomalyState.SUPPRESSED
                anomaly.suppressed = True
                if not anomaly.suppression_reason:
                    anomaly.suppression_reason = render(MESSAGE_MAINTENANCE)
                    anomaly.suppression_reason_i18n = MESSAGE_MAINTENANCE.to_wire()
            state.is_open = False
            return

        # NORMAL or INSUFFICIENT_DATA: no incident is live any more. A CANDIDATE
        # that never opened is closed out as a non-incident rather than left
        # dangling, so a later abnormal bucket starts a fresh one.
        if anomaly is not None and previous is AnomalyState.CANDIDATE:
            anomaly.state = target
            anomaly.anomaly_end = metric.bucket_start
        state.active_anomaly_id = None
        state.is_open = False

    async def _create_incident(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        in_maintenance: bool,
    ) -> LogSourceAnomaly:
        """Record a new incident in CANDIDATE. Not yet an alert.

        Persisting at candidate time rather than at open time means the first
        abnormal bucket is captured with its evidence even if the incident is
        never confirmed — which is exactly the data needed to tune away a noisy
        detector.
        """
        facts = evidence.extra or {}
        anomaly = LogSourceAnomaly(
            log_source_id=log_source.id,
            anomaly_type=evidence.anomaly_type,
            severity=evidence.severity,
            state=AnomalyState.CANDIDATE,
            detected_at=metric.bucket_start,
            anomaly_start=metric.bucket_start,
            observed_value=evidence.observed_value,
            expected_value=evidence.expected_value,
            deviation_score=evidence.deviation_score,
            confidence=evidence.confidence,
            deviation_ratio=_as_float(facts.get("ratio")),
            robust_z=_as_float(facts.get("robust_z")),
            absolute_delta=_as_float(facts.get("absolute_delta_events")),
            consecutive_buckets=1,
            baseline_version=_as_int(facts.get("baseline_version")),
            policy_version=self.settings.anomaly_policy_version,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            explanation=evidence.reason,
            explanation_i18n=evidence.reason_wire,
            details=evidence.to_dict(),
            suppressed=in_maintenance,
            suppression_reason=render(MESSAGE_MAINTENANCE) if in_maintenance else None,
            suppression_reason_i18n=MESSAGE_MAINTENANCE.to_wire() if in_maintenance else None,
        )
        self.session.add(anomaly)
        await self.session.flush()
        return anomaly

    async def _promote_to_open(
        self,
        log_source: LogSource,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        anomaly: LogSourceAnomaly,
        in_maintenance: bool,
        report: EvaluationReport,
    ) -> None:
        anomaly.state = AnomalyState.OPEN
        anomaly.opened_at = self._clock()
        anomaly.anomaly_end = None
        anomaly.severity = evidence.severity
        await self._touch_anomaly(state, metric, evidence)

        state.is_open = True
        state.open_anomaly_id = anomaly.id
        state.active_anomaly_id = anomaly.id
        report.opened.append(evidence.anomaly_type.value)

        # A confirmed volume anomaly is what the investigation exists for. Mark
        # the package pending here; the collector task fills it asynchronously
        # so detection never blocks on an Ariel round-trip.
        if (
            self.settings.explanation_enabled
            and evidence.anomaly_type in EXPLAINABLE_TYPES
            and not in_maintenance
        ):
            anomaly.evidence_status = EvidenceStatus.PENDING

        # Maintenance anomalies are recorded but never alerted.
        if in_maintenance:
            return
        # A RECORD_ONLY outage keeps the anomaly, its state, its transitions and
        # its explanation pending; only the alert and its notification are
        # suppressed. The fingerprint and every Phase A table are untouched.
        if (
            evidence.anomaly_type == AnomalyType.NO_EVENTS
            and (await self._outage(log_source)).mode is OutagePolicyMode.RECORD_ONLY
        ):
            return
        # Layer 2 of the two-layer verdict (design §4.4). A RECORD_ONLY or
        # DIGEST policy stops the alert and its notification; the incident, its
        # OPEN state and its transitions were already recorded above.
        if (
            await self._alert_mode(log_source, evidence.anomaly_type.value)
            is not AlertPolicyMode.ALERT
        ):
            return
        # SOC-17d layer 3: a time-boxed suppression stops the alert only after
        # the policy said ALERT (design §5.3). The incident above is untouched;
        # one hit row records what was held back. A break-through opens the
        # alert as today and leaves the suppression in force.
        suppression, breakthrough = await self._suppression.stops(
            family=AnomalyFamily.LOG_SOURCE,
            subject_id=log_source.id,
            detector=evidence.anomaly_type.value,
            at=self._clock(),
            evidence=evidence,
            criticality=Criticality(log_source.criticality),
            settings=self.settings,
        )
        if suppression is not None and not breakthrough:
            await record_hit(
                self.session,
                suppression,
                family=AnomalyFamily.LOG_SOURCE,
                anomaly_id=anomaly.id,
                detector=evidence.anomaly_type.value,
                bucket_start=metric.bucket_start,
                at=self._clock(),
            )
            return
        result = await self.alerts.open_or_update(
            self._alert_input(
                log_source,
                evidence,
                anomaly.id,
                await self._source_class(log_source.id),
            )
        )
        # Enqueue a notification only on a genuine OPEN transition (dedup: an
        # update to an already-open alert has transition=None).
        if self._enqueuer is not None and result.transition is not None:
            await self._enqueuer.enqueue(result.alert, result.transition)

    async def _touch_anomaly(
        self,
        state: LogSourceDetectorState,
        metric: LogSourceMetric,
        evidence: AnomalyEvidence,
    ) -> None:
        """Refresh the live incident's measurements from the current bucket."""
        anomaly = await self._active_anomaly(state)
        if anomaly is None:
            return
        facts = evidence.extra or {}
        anomaly.consecutive_buckets = max(
            anomaly.consecutive_buckets, state.consecutive_anomalous
        )
        # Track the worst observation seen, not merely the latest: an operator
        # asking "how bad did this get?" must not get the tail of a recovery.
        if evidence.is_anomalous:
            anomaly.anomaly_end = metric.bucket_start + timedelta(
                seconds=metric.bucket_seconds
            )
            observed = evidence.observed_value
            if observed is not None and _is_worse(anomaly, observed):
                anomaly.observed_value = observed
                anomaly.deviation_score = evidence.deviation_score
                anomaly.deviation_ratio = _as_float(facts.get("ratio"))
                anomaly.robust_z = _as_float(facts.get("robust_z"))
                anomaly.absolute_delta = _as_float(facts.get("absolute_delta_events"))
                anomaly.explanation = evidence.reason
                anomaly.explanation_i18n = evidence.reason_wire
                anomaly.details = evidence.to_dict()

    async def _active_anomaly(
        self, state: LogSourceDetectorState
    ) -> LogSourceAnomaly | None:
        if state.active_anomaly_id is None:
            return None
        return await self.session.get(LogSourceAnomaly, state.active_anomaly_id)

    async def _record_transition(
        self,
        anomaly: LogSourceAnomaly,
        from_state: AnomalyState,
        to_state: AnomalyState,
        metric: LogSourceMetric,
        message: MessageRef,
        evidence: AnomalyEvidence,
    ) -> None:
        self.session.add(
            AnomalyStateTransition(
                anomaly_id=anomaly.id,
                from_state=from_state,
                to_state=to_state,
                occurred_at=self._clock(),
                bucket_start=metric.bucket_start,
                reason=render(message),
                reason_i18n=message.to_wire(),
                actor="anomaly-engine",
                observed_value=evidence.observed_value,
                expected_value=evidence.expected_value,
            )
        )

    async def _source_class(self, log_source_id: uuid.UUID) -> str | None:
        """The source's Phase C class for alert routing; None when unclassified.

        Read per source and cached for the engine's lifetime: a refresh of an
        open condition happens every bucket, and a class changes only on an
        operator action or a sync, never within one evaluation pass.
        """
        if log_source_id not in self._source_class_cache:
            self._source_class_cache[log_source_id] = await self.session.scalar(
                select(LogSourceClassification.source_class).where(
                    LogSourceClassification.log_source_id == log_source_id
                )
            )
        return self._source_class_cache[log_source_id]

    async def _policies(self) -> list[OutageAlertPolicy]:
        """Every outage policy row, loaded once per engine instance.

        One statement for the whole run, whatever the fleet size (rule 9); the
        per-source verdict is folded in Python and memoised.
        """
        if self._policy_rows is None:
            self._policy_rows = list(
                (await self.session.scalars(select(OutageAlertPolicy))).all()
            )
        return self._policy_rows

    async def _classes(self) -> dict[uuid.UUID, str]:
        """Every classified source's class, loaded once per engine instance.

        Only a CLASS policy row makes the class relevant -- the caller checks
        for one before asking -- so a SOURCE-only or empty policy never pays
        for this. When it is needed, the whole map loads in one statement
        whatever the fleet size (rule 9), rather than a lookup per source.
        """
        if self._class_map is None:
            rows = (
                await self.session.execute(
                    select(
                        LogSourceClassification.log_source_id,
                        LogSourceClassification.source_class,
                    )
                )
            ).all()
            self._class_map = {
                log_source_id: str(source_class)
                for log_source_id, source_class in rows
            }
        return self._class_map

    async def _outage(self, log_source: LogSource) -> ResolvedOutage:
        """The resolved outage verdict for one source, memoised per engine run."""
        cached = self._outage_cache.get(log_source.id)
        if cached is None:
            policies = await self._policies()
            # A SOURCE row resolves without the class; only a CLASS row needs
            # it. No policy rows at all falls through to the default here with
            # the class never read, so the per-source grace override in
            # `_build_context` costs no class lookup per source (rule 9).
            source_class: str | None = None
            if any(
                row.scope_kind == OutagePolicyScope.CLASS.value for row in policies
            ):
                source_class = (await self._classes()).get(log_source.id)
            cached = resolve(
                policies,
                log_source_id=log_source.id,
                source_class=source_class,
                instance_id=log_source.instance_id,
                default=OutagePolicyMode(self.settings.outage_alert_default),
            )
            self._outage_cache[log_source.id] = cached
        return cached

    async def _alert_mode(
        self, log_source: LogSource, detector: str
    ) -> AlertPolicyMode:
        """The SOC-17c verdict for one source and detector, memoised per run.

        A CLASS row makes the verdict depend on the source's class. The class map
        loads once for the fleet, never once per source (rule 9); with no CLASS
        row at all it is never read, and the default answers.
        """
        source_class: str | None = None
        if await self._alert_policy.has_class_rows():
            source_class = (await self._classes()).get(log_source.id)
        return await self._alert_policy.mode(
            instance_id=log_source.instance_id,
            family=AnomalyFamily.LOG_SOURCE,
            source_id=log_source.id,
            source_class=source_class,
            detector=detector,
        )

    def _alert_input(
        self,
        log_source: LogSource,
        evidence: AnomalyEvidence,
        anomaly_id: uuid.UUID | None,
        source_class: str | None = None,
    ) -> AlertInput:
        context = {
            "log_source": log_source.name,
            "owner": log_source.owner,
            "criticality": str(log_source.criticality),
        }
        # Added only when known, and never part of the fingerprint.
        if source_class is not None:
            context["source_class"] = str(source_class)
        description = evidence.reason
        description_message = evidence.reason_message
        if evidence.anomaly_type is AnomalyType.NO_EVENTS:
            # NO_EVENTS only fires on a COMPLETE bucket, so the alert can say the
            # read succeeded and the source sent nothing. The generic enum title
            # cannot: an analyst reads it as the detector name, not as an
            # observation. Every other anomaly type keeps the generic title.
            title_message = MessageRef(
                "alert.anomaly.no_events.title", {"log_source": log_source.name}
            )
            specific = self._no_events_description(evidence)
            if specific is not None:
                description = render(specific)
                description_message = specific
        else:
            title_message = MessageRef(
                "alert.anomaly.title",
                {"anomaly_type": evidence.anomaly_type.value, "log_source": log_source.name},
            )
        return AlertInput(
            fingerprint=anomaly_fingerprint(log_source.id, evidence.anomaly_type.value),
            title=render(title_message),
            title_message=title_message,
            severity=evidence.severity,
            source_type="log_source",
            source_id=log_source.id,
            description=description,
            description_message=description_message,
            evidence=evidence.to_dict(),
            source_anomaly_ids=[str(anomaly_id)] if anomaly_id is not None else [],
            context=context,
        )

    def _no_events_description(self, evidence: AnomalyEvidence) -> MessageRef | None:
        """The NO_EVENTS alert sentence: the streak, and the last event when known.

        The detector's own sentence counts empty buckets; the alert adds that the
        buckets were complete observations, and, when the bucket carried one, the
        last event time. A detector evidence without the streak (or without the
        baseline expectation) leaves the generic reason in place rather than
        inventing a number.
        """
        streak = evidence.extra.get("empty_buckets")
        expected = evidence.expected_value
        if not isinstance(streak, int) or isinstance(streak, bool) or expected is None:
            return None
        last_event_at = evidence.extra.get("last_event_at")
        if isinstance(last_event_at, str) and last_event_at:
            return MessageRef(
                "alert.anomaly.no_events.description_last_event",
                {"streak": streak, "expected": expected, "last_event_at": last_event_at},
            )
        return MessageRef(
            "alert.anomaly.no_events.description",
            {"streak": streak, "expected": expected},
        )

    async def _refresh(
        self,
        log_source: LogSource,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        in_maintenance: bool,
    ) -> None:
        """A still-anomalous interval on an already-open condition.

        Re-asserting the alert bumps occurrence_count and refreshes the evidence,
        so an operator can see how many intervals the condition has persisted for
        rather than a count frozen at 1. open_or_update returns transition=None
        for an already-open alert, so this deliberately never notifies — that is
        the anti-noise guarantee, and it is why no enqueue happens here.
        """
        if in_maintenance:
            return
        if (
            evidence.anomaly_type == AnomalyType.NO_EVENTS
            and (await self._outage(log_source)).mode is OutagePolicyMode.RECORD_ONLY
        ):
            return
        if (
            await self._alert_mode(log_source, evidence.anomaly_type.value)
            is not AlertPolicyMode.ALERT
        ):
            return
        # SOC-17d layer 3, on the refresh path too: the suppression holds the
        # paging back while the incident stays recorded, and the first bucket
        # after `ends_at` re-enters this method and opens the alert normally
        # (design §5.3).
        suppression, breakthrough = await self._suppression.stops(
            family=AnomalyFamily.LOG_SOURCE,
            subject_id=log_source.id,
            detector=evidence.anomaly_type.value,
            at=self._clock(),
            evidence=evidence,
            criticality=Criticality(log_source.criticality),
            settings=self.settings,
        )
        if suppression is not None and not breakthrough:
            if state.open_anomaly_id is not None:
                await record_hit(
                    self.session,
                    suppression,
                    family=AnomalyFamily.LOG_SOURCE,
                    anomaly_id=state.open_anomaly_id,
                    detector=evidence.anomaly_type.value,
                    bucket_start=evidence.interval_start,
                    at=self._clock(),
                )
            return
        await self.alerts.open_or_update(
            self._alert_input(
                log_source,
                evidence,
                state.open_anomaly_id,
                await self._source_class(log_source.id),
            )
        )

    async def _resolve(
        self,
        log_source: LogSource,
        evidence: AnomalyEvidence,
        state: LogSourceDetectorState,
        anomaly: LogSourceAnomaly | None,
        report: EvaluationReport,
    ) -> None:
        if anomaly is not None and anomaly.resolved_at is None:
            anomaly.state = AnomalyState.RESOLVED
            anomaly.resolved_at = self._clock()
        state.is_open = False
        state.open_anomaly_id = None
        # Clearing the active incident is what makes a later recurrence open a
        # new one rather than reviving this closed incident.
        state.active_anomaly_id = None
        report.resolved.append(evidence.anomaly_type.value)
        result = await self.alerts.resolve_by_fingerprint(
            anomaly_fingerprint(log_source.id, evidence.anomaly_type.value),
            actor="anomaly-engine",
            reason_message=MESSAGE_RECOVERED,
        )
        if self._enqueuer is not None and result is not None and result.transition is not None:
            await self._enqueuer.enqueue(result.alert, result.transition)

    async def _get_state(
        self, log_source_id: uuid.UUID, anomaly_type: AnomalyType
    ) -> LogSourceDetectorState:
        state = await self.session.scalar(
            select(LogSourceDetectorState).where(
                LogSourceDetectorState.log_source_id == log_source_id,
                LogSourceDetectorState.anomaly_type == anomaly_type,
            )
        )
        if state is None:
            state = LogSourceDetectorState(
                log_source_id=log_source_id, anomaly_type=anomaly_type
            )
            self.session.add(state)
            await self.session.flush()
        return state

    # ---------------------------------------------------------------- helpers
    def _in_maintenance(self, log_source: LogSource, when: datetime) -> bool:
        return in_maintenance(
            when,
            maintenance_mode=log_source.maintenance_mode,
            maintenance_until=log_source.maintenance_until,
        )

    def _is_expected_active(self, log_source: LogSource, when: datetime) -> bool:
        """Local business-hours gate. See `app.anomaly.business_hours`."""
        return is_expected_active(log_source, when)
