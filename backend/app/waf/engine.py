"""WAF anomaly evaluation engine.

For one endpoint and one bucket it builds a detection context, runs the spike
detector, applies hysteresis, and persists the lifecycle move plus its audit row.

`app.anomaly.lifecycle.next_state` is reused **unchanged**: it is pure decision
logic over counters and two hysteresis integers with no log-source concept in
it, so Phase B gets Phase A's proven transition rules -- including the two that
were fixed live (an UNKNOWN bucket advances nothing; an active incident is not
downgraded because one bucket was unreadable) -- for free and without a second
lifecycle system to keep in sync.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.lifecycle import LifecycleDecision, LifecycleInput, next_state
from app.core.config import Settings, get_settings
from app.models.enums import (
    AnomalyState,
    BucketCompleteness,
    DetectorSignal,
    EvidenceStatus,
    WafAnomalyType,
)
from app.models.waf import (
    WafAnomaly,
    WafAnomalyTransition,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
)
from app.waf.baseline import WAF_BASELINE_METRIC
from app.waf.detector import (
    WafBaselineCell,
    WafDetectionContext,
    WafPoint,
    WafThresholds,
    detect_request_rate_spike,
)
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION


@dataclass
class WafEvaluationReport:
    waf_endpoint_id: uuid.UUID
    bucket_start: datetime | None
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    anomalous: list[str] = field(default_factory=list)
    candidates: list[str] = field(default_factory=list)
    recovering: list[str] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    skipped_reason: str | None = None


class WafAnomalyEngine:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _thresholds(self) -> WafThresholds:
        s = self.settings
        return WafThresholds(
            deviation_z=s.anomaly_deviation_threshold,
            spike_ratio=s.waf_spike_ratio,
            min_absolute_delta_requests=s.waf_min_absolute_delta_requests,
            min_bucket_requests=s.waf_min_bucket_requests,
            open_after=s.anomaly_open_after_intervals,
            resolve_after=s.anomaly_resolve_after_intervals,
        )

    async def evaluate_interval(
        self, endpoint: WafEndpoint, metric: WafEndpointMetric
    ) -> WafEvaluationReport:
        report = WafEvaluationReport(endpoint.id, metric.bucket_start)

        cell = await self._baseline_cell(
            endpoint.id, metric.bucket_start.isoweekday(), metric.bucket_start.hour
        )
        ctx = WafDetectionContext(
            point=WafPoint(
                bucket_start=metric.bucket_start,
                bucket_end=metric.bucket_end,
                # Coerced, not passed through: String-typed enum columns come
                # back from the database as plain str and the detector compares
                # identity.
                completeness=BucketCompleteness(metric.completeness),
                request_count=metric.request_count,
                endpoint_coverage_ratio=metric.endpoint_coverage_ratio,
                distinct_client_count=metric.distinct_client_count,
            ),
            baseline=cell,
            thresholds=self._thresholds(),
            mad_floor_ratio=self.settings.baseline_mad_floor_ratio,
            min_coverage=self.settings.waf_min_endpoint_coverage,
            extra={"normalizer_version": metric.normalizer_version},
        )

        verdict = detect_request_rate_spike(ctx)
        if verdict.is_anomalous:
            report.anomalous.append(verdict.detector_type.value)
        await self._apply_hysteresis(endpoint, metric, verdict.detector_type,
                                     verdict.evidence, report)
        await self.session.flush()
        return report

    async def _baseline_cell(
        self, endpoint_id: uuid.UUID, weekday: int, hour: int
    ) -> WafBaselineCell | None:
        row = await self.session.scalar(
            select(WafEndpointBaseline).where(
                WafEndpointBaseline.waf_endpoint_id == endpoint_id,
                WafEndpointBaseline.metric_name == WAF_BASELINE_METRIC,
                WafEndpointBaseline.weekday == weekday,
                WafEndpointBaseline.hour == hour,
                WafEndpointBaseline.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
        )
        if row is None:
            return None
        return WafBaselineCell(
            median=row.median,
            mad=row.mad,
            p05=row.p05,
            p95=row.p95,
            sample_count=row.sample_count,
            is_reliable=row.is_reliable,
            completeness=row.completeness,
            baseline_version=row.baseline_version,
        )

    async def _apply_hysteresis(
        self,
        endpoint: WafEndpoint,
        metric: WafEndpointMetric,
        detector_type: WafAnomalyType,
        evidence: AnomalyEvidence,
        report: WafEvaluationReport,
    ) -> None:
        state = await self._get_state(endpoint.id, detector_type)

        # Idempotency: this bucket already folded into the counters.
        if (
            state.last_interval_start is not None
            and state.last_interval_start >= metric.bucket_start
        ):
            return

        thr = self._thresholds()
        signal = evidence.signal

        if signal is DetectorSignal.ANOMALOUS:
            state.consecutive_anomalous += 1
            state.consecutive_healthy = 0
        elif signal is DetectorSignal.HEALTHY:
            state.consecutive_healthy += 1
            state.consecutive_anomalous = 0
        # UNKNOWN: counters untouched. Missing or unreadable data must neither
        # confirm an anomaly nor count towards recovery.

        state.last_interval_start = metric.bucket_start

        decision = next_state(
            LifecycleInput(
                state=AnomalyState(state.state),
                signal=signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=thr.open_after,
                resolve_after=thr.resolve_after,
                insufficient_data=evidence.insufficient_data,
            )
        )
        await self._apply_transition(
            endpoint, metric, detector_type, evidence, state, decision, report
        )

    async def _apply_transition(
        self,
        endpoint: WafEndpoint,
        metric: WafEndpointMetric,
        detector_type: WafAnomalyType,
        evidence: AnomalyEvidence,
        state: WafDetectorState,
        decision: LifecycleDecision,
        report: WafEvaluationReport,
    ) -> None:
        previous = AnomalyState(state.state)
        target = decision.state
        report.states[detector_type.value] = target.value

        if target is previous:
            if previous is AnomalyState.OPEN:
                await self._touch(state, metric, evidence)
            return

        state.state = target

        if target is AnomalyState.CANDIDATE:
            created = await self._create_incident(
                endpoint, metric, detector_type, evidence
            )
            state.active_anomaly_id = created.id
            await self._record_transition(
                created, previous, target, metric, decision.reason, evidence
            )
            report.candidates.append(detector_type.value)
            return

        anomaly = await self._active(state)

        if target is AnomalyState.OPEN:
            # A confirmation threshold of 1 means the incident opens on the same
            # bucket that created it, so there may be nothing to promote yet.
            incident = anomaly or await self._create_incident(
                endpoint, metric, detector_type, evidence
            )
            incident.state = AnomalyState.OPEN
            incident.opened_at = self._clock()
            incident.anomaly_end = None
            incident.severity = evidence.severity
            state.active_anomaly_id = incident.id
            state.open_anomaly_id = incident.id
            await self._touch(state, metric, evidence)
            await self._record_transition(
                incident, previous, target, metric, decision.reason, evidence
            )
            report.opened.append(detector_type.value)
            return

        if anomaly is not None:
            await self._record_transition(
                anomaly, previous, target, metric, decision.reason, evidence
            )

        if target is AnomalyState.RECOVERING:
            if anomaly is not None:
                anomaly.state = AnomalyState.RECOVERING
                # The abnormal telemetry ended with the last abnormal bucket,
                # not with the normal one that revealed the recovery.
                anomaly.anomaly_end = metric.bucket_start
            report.recovering.append(detector_type.value)
            return

        if target is AnomalyState.RESOLVED:
            if anomaly is not None and anomaly.resolved_at is None:
                anomaly.state = AnomalyState.RESOLVED
                anomaly.resolved_at = self._clock()
            state.open_anomaly_id = None
            # Clearing the active incident is what makes a later recurrence open
            # a new one rather than reviving this closed incident.
            state.active_anomaly_id = None
            report.resolved.append(detector_type.value)
            return

        # NORMAL or INSUFFICIENT_DATA: no incident is live. A CANDIDATE that
        # never opened is closed out as a non-incident rather than left dangling.
        if anomaly is not None and previous is AnomalyState.CANDIDATE:
            anomaly.state = target
            anomaly.anomaly_end = metric.bucket_start
        state.active_anomaly_id = None
        state.open_anomaly_id = None

    async def _create_incident(
        self,
        endpoint: WafEndpoint,
        metric: WafEndpointMetric,
        detector_type: WafAnomalyType,
        evidence: AnomalyEvidence,
    ) -> WafAnomaly:
        facts = evidence.extra or {}
        anomaly = WafAnomaly(
            waf_endpoint_id=endpoint.id,
            detector_type=detector_type,
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
            absolute_delta=_as_float(facts.get("absolute_delta_requests")),
            consecutive_buckets=1,
            baseline_version=_as_int(facts.get("baseline_version")),
            policy_version=self.settings.anomaly_policy_version,
            normalizer_version=metric.normalizer_version,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            explanation=evidence.reason,
            details=evidence.to_dict(),
        )
        self.session.add(anomaly)
        await self.session.flush()
        return anomaly

    async def _touch(
        self,
        state: WafDetectorState,
        metric: WafEndpointMetric,
        evidence: AnomalyEvidence,
    ) -> None:
        """Refresh the live incident from the current bucket.

        Tracks the *worst* reading, not the latest: an operator asking "how bad
        did this get?" must not be shown the tail of a recovery.
        """
        anomaly = await self._active(state)
        if anomaly is None:
            return
        anomaly.consecutive_buckets = max(
            anomaly.consecutive_buckets, state.consecutive_anomalous
        )
        if not evidence.is_anomalous:
            return
        anomaly.anomaly_end = metric.bucket_start + timedelta(
            seconds=metric.bucket_seconds
        )
        observed = evidence.observed_value
        if observed is not None and (
            anomaly.observed_value is None or observed > anomaly.observed_value
        ):
            facts = evidence.extra or {}
            anomaly.observed_value = observed
            anomaly.deviation_score = evidence.deviation_score
            anomaly.deviation_ratio = _as_float(facts.get("ratio"))
            anomaly.robust_z = _as_float(facts.get("robust_z"))
            anomaly.absolute_delta = _as_float(facts.get("absolute_delta_requests"))
            anomaly.explanation = evidence.reason
            anomaly.details = evidence.to_dict()

    async def _active(self, state: WafDetectorState) -> WafAnomaly | None:
        if state.active_anomaly_id is None:
            return None
        return await self.session.get(WafAnomaly, state.active_anomaly_id)

    async def _record_transition(
        self,
        anomaly: WafAnomaly,
        from_state: AnomalyState,
        to_state: AnomalyState,
        metric: WafEndpointMetric,
        reason: str,
        evidence: AnomalyEvidence,
    ) -> None:
        self.session.add(
            WafAnomalyTransition(
                anomaly_id=anomaly.id,
                from_state=from_state,
                to_state=to_state,
                occurred_at=self._clock(),
                bucket_start=metric.bucket_start,
                reason=reason,
                actor="waf-anomaly-engine",
                observed_value=evidence.observed_value,
                expected_value=evidence.expected_value,
            )
        )

    async def _get_state(
        self, endpoint_id: uuid.UUID, detector_type: WafAnomalyType
    ) -> WafDetectorState:
        state = await self.session.scalar(
            select(WafDetectorState).where(
                WafDetectorState.waf_endpoint_id == endpoint_id,
                WafDetectorState.detector_type == detector_type,
            )
        )
        if state is None:
            state = WafDetectorState(
                waf_endpoint_id=endpoint_id, detector_type=detector_type
            )
            self.session.add(state)
            await self.session.flush()
        return state


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None
