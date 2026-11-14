"""Evaluate one identity collection tick and record incidents and alerts.

The judged bucket is the series already in memory. History is one bulk read
per table, not a query per source or per username. Writes are one statement
per table. The collector owns the commit; this engine only flushes through
the statements it executes.

SOC-13 analyst exclusions are not read. A known incident is kept out of the
seasonal band by the shared anomaly span.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.policy import AlertPolicyGate
from app.alerts.suppression import Evidence, SuppressionGate, record_hit
from app.anomaly.lifecycle import LifecycleInput, advance_counters, next_state
from app.anomaly.seasonal import (
    anomaly_exclusion_span,
    in_any_span,
    seasonal_key,
    summarize_cell,
)
from app.core.config import Settings, get_settings
from app.identity.detectors import (
    NOVELTY_EVENT_IDS,
    NOVELTY_HISTORY_DAYS,
    NameEvidence,
    NoveltyHistoryBucket,
    SpikeThresholds,
    detect_new_privileged_account,
    detect_rate_spike,
    detect_threshold,
    detectors_for,
    verify_novelty_history,
)
from app.identity.fold import IdentitySeries, StoredUsername
from app.models.alert import Alert
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    IDENTITY_BUCKET_SECONDS,
    AlertPolicyMode,
    AlertStatus,
    AnomalyFamily,
    AnomalyState,
    DetectorSignal,
    EvidenceStatus,
    IdentityAnomalyType,
    IdentityMeasurement,
)
from app.models.fleet import LogSourceClassification
from app.models.identity_anomaly import (
    IdentityAnomaly,
    IdentityAnomalyTransition,
    IdentityDetectorState,
)
from app.models.identity_detection import (
    IdentityBucket,
    IdentityEventCount,
    IdentitySourceProfile,
    IdentityTopUsername,
)
from app.models.log_source import LogSource

logger = logging.getLogger(__name__)

ACTOR = "identity-anomaly-engine"
SOURCE_TYPE = "identity_source"
_HELD = "Held by an alert suppression."

_TITLES = {
    IdentityAnomalyType.IDENTITY_AUDIT_LOG_CLEARED: "Audit log cleared",
    IdentityAnomalyType.IDENTITY_PRIVILEGED_GROUP_ADD: "Privileged group membership added",
    IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT: "New privileged account",
    IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE: "Failed logon spike",
    IdentityAnomalyType.IDENTITY_LOCKOUT_SPIKE: "Account lockout spike",
    IdentityAnomalyType.IDENTITY_ACCOUNT_CREATED: "Account created",
    IdentityAnomalyType.IDENTITY_ACCOUNT_DELETED: "Account deleted",
}

_Key = tuple[uuid.UUID, int, IdentityAnomalyType]


@dataclass
class IdentityEngineReport:
    incidents_opened: int = 0
    incidents_refreshed: int = 0
    incidents_resolved: int = 0
    alerts_opened: int = 0
    alerts_resolved: int = 0
    insufficient: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "incidents_opened": self.incidents_opened,
            "incidents_refreshed": self.incidents_refreshed,
            "incidents_resolved": self.incidents_resolved,
            "alerts_opened": self.alerts_opened,
            "alerts_resolved": self.alerts_resolved,
            "insufficient": self.insufficient,
        }


@dataclass
class _State:
    id: uuid.UUID
    log_source_id: uuid.UUID
    event_id: int
    detector: str
    consecutive_anomalous: int
    consecutive_healthy: int
    state: str
    last_interval_start: datetime | None
    active_anomaly_id: uuid.UUID | None
    open_anomaly_id: uuid.UUID | None
    is_new: bool


@dataclass
class _Incident:
    id: uuid.UUID
    log_source_id: uuid.UUID
    event_id: int
    detector: str
    fingerprint: str
    severity: str
    state: str
    detected_at: datetime
    opened_at: datetime | None
    anomaly_start: datetime | None
    anomaly_end: datetime | None
    resolved_at: datetime | None
    observed_value: float | None
    expected_value: float | None
    deviation_score: float | None
    deviation_ratio: float | None
    robust_z: float | None
    absolute_delta: float | None
    consecutive_buckets: int
    policy_version: int
    profile_version: int
    catalog_version: int
    explanation: str | None
    details: dict[str, Any]
    evidence_status: str
    suppressed: bool
    suppression_reason: str | None
    observed_measurement: str
    is_new: bool


@dataclass
class _Count:
    bucket_start: datetime
    event_count: int | None
    measurement: str
    names_measurement: str
    admission_truncated: bool


@dataclass(frozen=True)
class _AlertOpen:
    fingerprint: str
    source_id: uuid.UUID
    detector: str
    event_id: int
    severity: str
    title: str
    description: str
    evidence: dict[str, Any]
    context: dict[str, Any]
    anomaly_id: uuid.UUID
    bucket_start: datetime
    ratio: float | None


@dataclass(frozen=True)
class _AlertResolve:
    fingerprint: str
    reason: str


class _RatioEvidence:
    """Suppression evidence. ``extra`` is writable so it matches the protocol."""

    def __init__(self, ratio: float | None) -> None:
        self.extra: dict[str, Any] = {"ratio": ratio}


@dataclass
class _Batch:
    incidents: dict[uuid.UUID, _Incident] = field(default_factory=dict)
    by_key: dict[_Key, _Incident] = field(default_factory=dict)
    transitions: list[dict[str, Any]] = field(default_factory=list)
    states: dict[_Key, _State] = field(default_factory=dict)
    opens: list[_AlertOpen] = field(default_factory=list)
    resolves: list[_AlertResolve] = field(default_factory=list)
    counts: dict[tuple[uuid.UUID, int], list[_Count]] = field(default_factory=dict)
    catalogs: dict[tuple[uuid.UUID, datetime], tuple[int, int]] = field(default_factory=dict)
    names: dict[tuple[uuid.UUID, int, datetime], frozenset[str]] = field(default_factory=dict)
    spans: dict[_Key, list[tuple[datetime, datetime]]] = field(default_factory=dict)
    profiles: dict[uuid.UUID, int] = field(default_factory=dict)
    sources: dict[uuid.UUID, tuple[str, str | None]] = field(default_factory=dict)
    bucket_seconds: int = IDENTITY_BUCKET_SECONDS


def fingerprint(
    instance_id: uuid.UUID,
    log_source_id: uuid.UUID,
    event_id: int,
    detector: str,
) -> str:
    return f"identity:{instance_id}:{log_source_id}:{event_id}:{detector}"


def _name_evidence(usernames: Sequence[StoredUsername]) -> tuple[NameEvidence, ...]:
    return tuple(
        NameEvidence(rank=item.rank, username=item.username, count=item.count)
        for item in usernames
        if isinstance(item.username, str) and item.username
    )


def _username_payload(names: Sequence[NameEvidence]) -> list[dict[str, Any]]:
    return [{"rank": item.rank, "username": item.username, "count": item.count} for item in names]


def _details(
    event_id: int,
    verdict_measurement: str,
    names: Sequence[NameEvidence],
    ratio: float | None,
    robust_z: float | None,
    score_status: str | None,
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "measurement": verdict_measurement,
        "usernames": _username_payload(names),
        "ratio": ratio,
        "robust_z": robust_z,
        "score_status": score_status,
    }


class IdentityAnomalyEngine:
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
        self._policy = AlertPolicyGate(
            session, default=AlertPolicyMode(self.settings.alert_policy_default)
        )
        self._suppression = SuppressionGate(session)

    async def evaluate(
        self,
        *,
        instance_id: uuid.UUID,
        series: Sequence[IdentitySeries],
        bucket_seconds: int = IDENTITY_BUCKET_SECONDS,
    ) -> IdentityEngineReport:
        report = IdentityEngineReport()
        grouped = _group(series)
        if not grouped:
            return report
        batch = await self._load(grouped, bucket_seconds)
        thresholds = SpikeThresholds(
            deviation_z=self.settings.anomaly_deviation_threshold,
            mad_floor_ratio=self.settings.baseline_mad_floor_ratio,
        )
        for key in sorted(grouped, key=lambda item: (str(item[0]), item[1], item[2].value)):
            buckets = sorted(grouped[key], key=lambda item: item.bucket_start)
            for fact in buckets:
                self._judge(instance_id, key, fact, batch, thresholds, bucket_seconds, report)
        await self._persist(instance_id, batch, report)
        logger.info(
            "identity anomalies: opened %d, refreshed %d, resolved %d",
            report.incidents_opened,
            report.incidents_refreshed,
            report.incidents_resolved,
        )
        return report

    async def _load(self, grouped: dict[_Key, list[IdentitySeries]], bucket_seconds: int) -> _Batch:
        batch = _Batch()
        source_ids = list({key[0] for key in grouped})
        starts = [fact.bucket_start for facts in grouped.values() for fact in facts]
        earliest = min(starts)
        latest = max(starts)
        horizon = max(self.settings.baseline_lookback_days, NOVELTY_HISTORY_DAYS)
        lo = earliest - timedelta(days=horizon)
        counts = (
            await self.session.execute(
                select(
                    IdentityEventCount.log_source_id,
                    IdentityEventCount.event_id,
                    IdentityEventCount.bucket_start,
                    IdentityEventCount.event_count,
                    IdentityEventCount.measurement,
                    IdentityEventCount.names_measurement,
                    IdentityEventCount.admission_truncated,
                ).where(
                    IdentityEventCount.log_source_id.in_(source_ids),
                    IdentityEventCount.bucket_start >= lo,
                    IdentityEventCount.bucket_start <= latest,
                )
            )
        ).all()
        batch_counts: dict[tuple[uuid.UUID, int], list[_Count]] = defaultdict(list)
        for row in counts:
            batch_counts[(row.log_source_id, row.event_id)].append(
                _Count(
                    bucket_start=row.bucket_start,
                    event_count=None if row.event_count is None else int(row.event_count),
                    measurement=str(row.measurement),
                    names_measurement=str(row.names_measurement),
                    admission_truncated=bool(row.admission_truncated),
                )
            )
        catalogs = {
            (row.log_source_id, row.bucket_start): (
                int(row.catalog_version),
                int(row.bucket_seconds),
            )
            for row in (
                await self.session.execute(
                    select(
                        IdentityBucket.log_source_id,
                        IdentityBucket.bucket_start,
                        IdentityBucket.catalog_version,
                        IdentityBucket.bucket_seconds,
                    ).where(
                        IdentityBucket.log_source_id.in_(source_ids),
                        IdentityBucket.bucket_start >= lo,
                        IdentityBucket.bucket_start <= latest,
                    )
                )
            ).all()
        }
        name_lo = earliest - timedelta(days=NOVELTY_HISTORY_DAYS)
        names: dict[tuple[uuid.UUID, int, datetime], set[str]] = defaultdict(set)
        for name_row in (
            await self.session.execute(
                select(
                    IdentityTopUsername.log_source_id,
                    IdentityTopUsername.event_id,
                    IdentityTopUsername.bucket_start,
                    IdentityTopUsername.username,
                ).where(
                    IdentityTopUsername.log_source_id.in_(source_ids),
                    IdentityTopUsername.event_id.in_(tuple(NOVELTY_EVENT_IDS)),
                    IdentityTopUsername.bucket_start >= name_lo,
                    IdentityTopUsername.bucket_start <= latest,
                )
            )
        ).all():
            names[
                (name_row.log_source_id, name_row.event_id, name_row.bucket_start)
            ].add(str(name_row.username))
        active = tuple(state.value for state in ACTIVE_ANOMALY_STATES)
        anomaly_rows = (
            await self.session.execute(
                select(
                    IdentityAnomaly.id,
                    IdentityAnomaly.log_source_id,
                    IdentityAnomaly.event_id,
                    IdentityAnomaly.detector_type,
                    IdentityAnomaly.fingerprint,
                    IdentityAnomaly.severity,
                    IdentityAnomaly.state,
                    IdentityAnomaly.detected_at,
                    IdentityAnomaly.opened_at,
                    IdentityAnomaly.anomaly_start,
                    IdentityAnomaly.anomaly_end,
                    IdentityAnomaly.resolved_at,
                    IdentityAnomaly.observed_value,
                    IdentityAnomaly.expected_value,
                    IdentityAnomaly.deviation_score,
                    IdentityAnomaly.deviation_ratio,
                    IdentityAnomaly.robust_z,
                    IdentityAnomaly.absolute_delta,
                    IdentityAnomaly.consecutive_buckets,
                    IdentityAnomaly.policy_version,
                    IdentityAnomaly.profile_version,
                    IdentityAnomaly.catalog_version,
                    IdentityAnomaly.explanation,
                    IdentityAnomaly.details,
                    IdentityAnomaly.evidence_status,
                    IdentityAnomaly.suppressed,
                    IdentityAnomaly.suppression_reason,
                    IdentityAnomaly.observed_measurement,
                ).where(
                    IdentityAnomaly.log_source_id.in_(source_ids),
                    or_(
                        IdentityAnomaly.state.in_(active),
                        IdentityAnomaly.detected_at >= lo,
                    ),
                )
            )
        ).all()
        spans: dict[_Key, list[tuple[datetime, datetime]]] = defaultdict(list)
        for anomaly_row in anomaly_rows:
            incident = _incident_from_row(anomaly_row)
            batch.incidents[incident.id] = incident
            detector = IdentityAnomalyType(incident.detector)
            key = (incident.log_source_id, incident.event_id, detector)
            if incident.state in active:
                batch.by_key[key] = incident
            spans[key].append(
                anomaly_exclusion_span(
                    incident.detected_at,
                    incident.anomaly_end,
                    incident.resolved_at,
                    latest,
                )
            )
        for state_row in (
            await self.session.execute(
                select(
                    IdentityDetectorState.id,
                    IdentityDetectorState.log_source_id,
                    IdentityDetectorState.event_id,
                    IdentityDetectorState.detector_type,
                    IdentityDetectorState.consecutive_anomalous,
                    IdentityDetectorState.consecutive_healthy,
                    IdentityDetectorState.state,
                    IdentityDetectorState.last_interval_start,
                    IdentityDetectorState.active_anomaly_id,
                    IdentityDetectorState.open_anomaly_id,
                ).where(IdentityDetectorState.log_source_id.in_(source_ids))
            )
        ).all():
            detector = IdentityAnomalyType(state_row.detector_type)
            key = (state_row.log_source_id, state_row.event_id, detector)
            batch.states[key] = _State(
                id=state_row.id,
                log_source_id=state_row.log_source_id,
                event_id=state_row.event_id,
                detector=state_row.detector_type,
                consecutive_anomalous=int(state_row.consecutive_anomalous),
                consecutive_healthy=int(state_row.consecutive_healthy),
                state=str(state_row.state),
                last_interval_start=state_row.last_interval_start,
                active_anomaly_id=state_row.active_anomaly_id,
                open_anomaly_id=state_row.open_anomaly_id,
                is_new=False,
            )
        profiles = {
            row.log_source_id: int(row.profile_version)
            for row in (
                await self.session.execute(
                    select(
                        IdentitySourceProfile.log_source_id,
                        IdentitySourceProfile.profile_version,
                    ).where(IdentitySourceProfile.log_source_id.in_(source_ids))
                )
            ).all()
        }
        sources: dict[uuid.UUID, tuple[str, str | None]] = {}
        for source_row in (
            await self.session.execute(
                select(
                    LogSource.id,
                    LogSource.name,
                    LogSourceClassification.source_class,
                    LogSourceClassification.override_class,
                )
                .outerjoin(
                    LogSourceClassification,
                    LogSourceClassification.log_source_id == LogSource.id,
                )
                .where(LogSource.id.in_(source_ids))
            )
        ).all():
            chosen = source_row.override_class or source_row.source_class
            sources[source_row.id] = (
                str(source_row.name),
                None if chosen is None else str(chosen),
            )
        batch.counts = batch_counts
        batch.catalogs = catalogs
        batch.names = {key: frozenset(value) for key, value in names.items()}
        batch.spans = spans
        batch.profiles = profiles
        batch.sources = sources
        batch.bucket_seconds = bucket_seconds
        return batch

    def _judge(
        self,
        instance_id: uuid.UUID,
        key: _Key,
        fact: IdentitySeries,
        batch: _Batch,
        thresholds: SpikeThresholds,
        bucket_seconds: int,
        report: IdentityEngineReport,
    ) -> None:
        source_id, event_id, detector = key
        state = batch.states.get(key)
        if state is None:
            state = _State(
                id=uuid.uuid4(),
                log_source_id=source_id,
                event_id=event_id,
                detector=detector.value,
                consecutive_anomalous=0,
                consecutive_healthy=0,
                state=AnomalyState.NORMAL.value,
                last_interval_start=None,
                active_anomaly_id=None,
                open_anomaly_id=None,
                is_new=True,
            )
            batch.states[key] = state
        if (
            state.last_interval_start is not None
            and fact.bucket_start <= state.last_interval_start
        ):
            return
        names = _name_evidence(fact.usernames)
        verdict = self._verdict(key, fact, batch, thresholds, bucket_seconds, names)
        if verdict.insufficient:
            report.insufficient += 1
        previous = AnomalyState(state.state)
        anomalous, healthy = advance_counters(
            verdict.signal, state.consecutive_anomalous, state.consecutive_healthy
        )
        decision = next_state(
            LifecycleInput(
                state=previous,
                signal=verdict.signal,
                consecutive_anomalous=anomalous,
                consecutive_healthy=healthy,
                open_after=self.settings.identity_open_after_intervals,
                resolve_after=self.settings.identity_resolve_after_intervals,
                insufficient_data=verdict.insufficient,
            )
        )
        state.consecutive_anomalous = anomalous
        state.consecutive_healthy = healthy
        state.last_interval_start = fact.bucket_start
        target = decision.state
        incident = batch.by_key.get(key)
        if target is previous:
            if (
                previous is AnomalyState.OPEN
                and verdict.signal is DetectorSignal.ANOMALOUS
                and incident is not None
            ):
                self._touch(incident, fact, verdict, anomalous)
                self._queue_open(instance_id, fact, verdict, incident, batch, refresh=True)
                report.incidents_refreshed += 1
            return
        state.state = target.value
        if target in (AnomalyState.CANDIDATE, AnomalyState.OPEN) and incident is None:
            incident = self._new_incident(
                instance_id, fact, verdict, anomalous, target, batch
            )
            batch.incidents[incident.id] = incident
            batch.by_key[key] = incident
            state.active_anomaly_id = incident.id
        if incident is not None and target is not previous:
            incident.state = target.value
            batch.transitions.append(
                {
                    "id": uuid.uuid4(),
                    "identity_anomaly_id": incident.id,
                    "from_state": previous.value,
                    "to_state": target.value,
                    "occurred_at": self._clock(),
                    "bucket_start": fact.bucket_start,
                    "reason": decision.reason,
                    "reason_i18n": decision.message.to_wire(),
                    "actor": ACTOR,
                    "observed_value": verdict.observed,
                    "expected_value": verdict.expected,
                    "observed_measurement": verdict.measurement.value,
                }
            )
        if target is AnomalyState.OPEN and incident is not None:
            if incident.opened_at is None:
                incident.opened_at = self._clock()
            incident.anomaly_end = None
            state.open_anomaly_id = incident.id
            state.active_anomaly_id = incident.id
            self._touch(incident, fact, verdict, anomalous)
            opened = previous not in (AnomalyState.OPEN, AnomalyState.RECOVERING)
            if opened:
                report.incidents_opened += 1
            else:
                report.incidents_refreshed += 1
            self._queue_open(instance_id, fact, verdict, incident, batch, refresh=not opened)
            return
        if target is AnomalyState.RECOVERING and incident is not None:
            incident.anomaly_end = fact.bucket_start
            return
        if target is AnomalyState.RESOLVED:
            if incident is not None:
                incident.resolved_at = self._clock()
                if incident.anomaly_end is None:
                    incident.anomaly_end = fact.bucket_start
                incident.state = AnomalyState.RESOLVED.value
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            batch.by_key.pop(key, None)
            report.incidents_resolved += 1
            if self.settings.identity_alerting_enabled and previous in (
                AnomalyState.OPEN,
                AnomalyState.RECOVERING,
            ):
                batch.resolves.append(
                    _AlertResolve(
                        fingerprint=fingerprint(
                            instance_id, source_id, event_id, detector.value
                        ),
                        reason=decision.reason,
                    )
                )
            return
        if target is AnomalyState.NORMAL:
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            batch.by_key.pop(key, None)

    def _verdict(
        self,
        key: _Key,
        fact: IdentitySeries,
        batch: _Batch,
        thresholds: SpikeThresholds,
        bucket_seconds: int,
        names: tuple[NameEvidence, ...],
    ) -> Any:
        _source, event_id, detector = key
        if detector is IdentityAnomalyType.IDENTITY_NEW_PRIVILEGED_ACCOUNT:
            verified, known = self._novelty(key, fact, batch, bucket_seconds)
            return detect_new_privileged_account(
                event_id=event_id,
                measurement=fact.names_measurement,
                admission_truncated=fact.admission_truncated,
                history_verified=verified,
                known_names=known,
                usernames=names,
            )
        if detector in (
            IdentityAnomalyType.IDENTITY_FAILED_LOGON_SPIKE,
            IdentityAnomalyType.IDENTITY_LOCKOUT_SPIKE,
        ):
            cell, cold = self._band(key, fact.bucket_start, batch)
            return detect_rate_spike(
                detector,
                event_id=event_id,
                count=None if fact.count is None else float(fact.count),
                measurement=fact.measurement,
                cell=cell,
                cold=cold,
                thresholds=thresholds,
                usernames=names,
            )
        return detect_threshold(
            detector,
            event_id=event_id,
            count=None if fact.count is None else float(fact.count),
            measurement=fact.measurement,
            usernames=names,
        )

    def _band(self, key: _Key, judged: datetime, batch: _Batch) -> tuple[Any, bool]:
        rows: list[_Count] = batch.counts.get((key[0], key[1]), [])
        before = [
            row
            for row in rows
            if row.bucket_start < judged
            and row.measurement == IdentityMeasurement.EXACT.value
            and row.event_count is not None
        ]
        if not before:
            return None, True
        earliest = min(row.bucket_start for row in before)
        cold = earliest > judged - timedelta(days=self.settings.baseline_lookback_days)
        wanted = seasonal_key(judged)
        spans = batch.spans.get(key, [])
        samples = [
            float(row.event_count)
            for row in before
            if row.event_count is not None
            and seasonal_key(row.bucket_start) == wanted
            and not in_any_span(row.bucket_start, spans)
        ]
        if not samples:
            return None, cold
        return summarize_cell(samples, min_samples=self.settings.baseline_min_samples), cold

    def _novelty(
        self,
        key: _Key,
        fact: IdentitySeries,
        batch: _Batch,
        bucket_seconds: int,
    ) -> tuple[bool, frozenset[str]]:
        window_end = fact.bucket_start
        window_start = window_end - timedelta(days=NOVELTY_HISTORY_DAYS)
        rows: list[_Count] = batch.counts.get((key[0], key[1]), [])
        history: list[NoveltyHistoryBucket] = []
        for row in rows:
            if not (window_start <= row.bucket_start < window_end):
                continue
            catalog = batch.catalogs.get((key[0], row.bucket_start))
            version, width = catalog if catalog is not None else (-1, 0)
            history.append(
                NoveltyHistoryBucket(
                    bucket_start=row.bucket_start,
                    names_measurement=IdentityMeasurement(row.names_measurement),
                    admission_truncated=row.admission_truncated,
                    catalog_version=version,
                    bucket_seconds=width,
                    usernames=batch.names.get((key[0], key[1], row.bucket_start), frozenset()),
                )
            )
        return verify_novelty_history(
            history,
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            catalog_version=fact.catalog_version,
        )

    def _new_incident(
        self,
        instance_id: uuid.UUID,
        fact: IdentitySeries,
        verdict: Any,
        consecutive: int,
        target: AnomalyState,
        batch: _Batch,
    ) -> _Incident:
        now = self._clock()
        names_whole = (
            fact.names_measurement is IdentityMeasurement.EXACT and not fact.admission_truncated
        )
        return _Incident(
            id=uuid.uuid4(),
            log_source_id=fact.log_source_id,
            event_id=fact.event_id,
            detector=verdict.detector.value,
            fingerprint=fingerprint(
                instance_id, fact.log_source_id, fact.event_id, verdict.detector.value
            ),
            severity=verdict.severity.value,
            state=target.value,
            detected_at=fact.bucket_start,
            opened_at=now if target is AnomalyState.OPEN else None,
            anomaly_start=fact.bucket_start,
            anomaly_end=None,
            resolved_at=None,
            observed_value=verdict.observed,
            expected_value=verdict.expected,
            deviation_score=verdict.robust_z,
            deviation_ratio=verdict.ratio,
            robust_z=verdict.robust_z,
            absolute_delta=verdict.absolute_delta,
            consecutive_buckets=consecutive,
            policy_version=self.settings.anomaly_policy_version,
            profile_version=batch.profiles.get(fact.log_source_id, 1),
            catalog_version=fact.catalog_version,
            explanation=verdict.reason,
            details=_details(
                fact.event_id,
                verdict.measurement.value,
                verdict.usernames,
                verdict.ratio,
                verdict.robust_z,
                verdict.score_status,
            ),
            evidence_status=(
                EvidenceStatus.COMPLETE.value if names_whole else EvidenceStatus.PARTIAL.value
            ),
            suppressed=False,
            suppression_reason=None,
            observed_measurement=verdict.measurement.value,
            is_new=True,
        )

    def _touch(
        self, incident: _Incident, fact: IdentitySeries, verdict: Any, consecutive: int
    ) -> None:
        names_whole = (
            fact.names_measurement is IdentityMeasurement.EXACT and not fact.admission_truncated
        )
        incident.severity = verdict.severity.value
        incident.observed_value = verdict.observed
        incident.expected_value = verdict.expected
        incident.deviation_score = verdict.robust_z
        incident.deviation_ratio = verdict.ratio
        incident.robust_z = verdict.robust_z
        incident.absolute_delta = verdict.absolute_delta
        incident.consecutive_buckets = consecutive
        incident.explanation = verdict.reason
        incident.details = _details(
            fact.event_id,
            verdict.measurement.value,
            verdict.usernames,
            verdict.ratio,
            verdict.robust_z,
            verdict.score_status,
        )
        incident.evidence_status = (
            EvidenceStatus.COMPLETE.value if names_whole else EvidenceStatus.PARTIAL.value
        )
        incident.observed_measurement = verdict.measurement.value
        incident.suppressed = False
        incident.suppression_reason = None

    def _queue_open(
        self,
        instance_id: uuid.UUID,
        fact: IdentitySeries,
        verdict: Any,
        incident: _Incident,
        batch: _Batch,
        *,
        refresh: bool,
    ) -> None:
        if not self.settings.identity_alerting_enabled:
            return
        name, _source_class = batch.sources.get(fact.log_source_id, ("log source", None))
        payload = _username_payload(verdict.usernames)
        evidence = dict(incident.details)
        context = {
            "log_source_name": name,
            "event_id": fact.event_id,
            "detector": verdict.detector.value,
            "observed": verdict.observed,
            "expected": verdict.expected,
            "ratio": verdict.ratio,
            "usernames": payload,
        }
        batch.opens.append(
            _AlertOpen(
                fingerprint=fingerprint(
                    instance_id, fact.log_source_id, fact.event_id, verdict.detector.value
                ),
                source_id=fact.log_source_id,
                detector=verdict.detector.value,
                event_id=fact.event_id,
                severity=verdict.severity.value,
                title=f"{_TITLES[verdict.detector]} on {name} ({fact.event_id})",
                description=verdict.reason,
                evidence=evidence,
                context=context,
                anomaly_id=incident.id,
                bucket_start=fact.bucket_start,
                ratio=verdict.ratio,
            )
        )
        if refresh:
            return

    async def _persist(
        self, instance_id: uuid.UUID, batch: _Batch, report: IdentityEngineReport
    ) -> None:
        new_rows = [_incident_insert(item) for item in batch.incidents.values() if item.is_new]
        if new_rows:
            await self.session.execute(pg_insert(IdentityAnomaly).values(new_rows))
        existing = [item for item in batch.incidents.values() if not item.is_new]
        for item in existing:
            await self.session.execute(
                update(IdentityAnomaly)
                .where(IdentityAnomaly.id == item.id)
                .values(**_incident_update(item))
            )
        if batch.transitions:
            await self.session.execute(
                pg_insert(IdentityAnomalyTransition).values(batch.transitions)
            )
        if batch.states:
            await self._upsert_states(batch)
        if self.settings.identity_alerting_enabled:
            await self._alerts(instance_id, batch, report)

    async def _upsert_states(self, batch: _Batch) -> None:
        rows = [
            {
                "id": state.id,
                "log_source_id": state.log_source_id,
                "event_id": state.event_id,
                "detector_type": state.detector,
                "consecutive_anomalous": state.consecutive_anomalous,
                "consecutive_healthy": state.consecutive_healthy,
                "state": state.state,
                "last_interval_start": state.last_interval_start,
                "active_anomaly_id": state.active_anomaly_id,
                "open_anomaly_id": state.open_anomaly_id,
            }
            for state in batch.states.values()
        ]
        statement = pg_insert(IdentityDetectorState).values(rows)
        excluded = statement.excluded
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["log_source_id", "event_id", "detector_type"],
                set_={
                    "consecutive_anomalous": excluded.consecutive_anomalous,
                    "consecutive_healthy": excluded.consecutive_healthy,
                    "state": excluded.state,
                    "last_interval_start": excluded.last_interval_start,
                    "active_anomaly_id": excluded.active_anomaly_id,
                    "open_anomaly_id": excluded.open_anomaly_id,
                    "updated_at": func.now(),
                },
            )
        )

    async def _alerts(
        self, instance_id: uuid.UUID, batch: _Batch, report: IdentityEngineReport
    ) -> None:
        allowed: list[_AlertOpen] = []
        for item in batch.opens:
            _name, source_class = batch.sources.get(item.source_id, ("log source", None))
            mode = await self._policy.mode(
                instance_id=instance_id,
                family=AnomalyFamily.IDENTITY,
                source_id=item.source_id,
                source_class=source_class,
                detector=item.detector,
            )
            if mode is not AlertPolicyMode.ALERT:
                continue
            evidence: Evidence = _RatioEvidence(item.ratio)
            suppression, breakthrough = await self._suppression.stops(
                family=AnomalyFamily.IDENTITY,
                subject_id=item.source_id,
                detector=item.detector,
                at=self._clock(),
                evidence=evidence,
                criticality=None,
                settings=self.settings,
            )
            incident = batch.incidents.get(item.anomaly_id)
            if suppression is not None and not breakthrough:
                if incident is not None:
                    incident.suppressed = True
                    incident.suppression_reason = _HELD
                    await self.session.execute(
                        update(IdentityAnomaly)
                        .where(IdentityAnomaly.id == incident.id)
                        .values(suppressed=True, suppression_reason=_HELD, updated_at=func.now())
                    )
                await record_hit(
                    self.session,
                    suppression,
                    family=AnomalyFamily.IDENTITY,
                    anomaly_id=item.anomaly_id,
                    detector=item.detector,
                    bucket_start=item.bucket_start,
                    at=self._clock(),
                )
                continue
            allowed.append(item)
        if allowed:
            fingerprints = [item.fingerprint for item in allowed]
            existing = set(
                (
                    await self.session.scalars(
                        select(Alert.fingerprint).where(
                            Alert.fingerprint.in_(fingerprints),
                            Alert.status != AlertStatus.RESOLVED.value,
                        )
                    )
                ).all()
            )
            now = self._clock()
            fresh, repeats = _collapse_opens(allowed, existing)
            if fresh:
                await self.session.execute(
                    pg_insert(Alert).values(
                        [
                            {
                                "id": uuid.uuid4(),
                                "dedup_key": item.fingerprint,
                                "fingerprint": item.fingerprint,
                                "title": item.title,
                                "description": item.description,
                                "severity": item.severity,
                                "status": AlertStatus.OPEN.value,
                                "source_type": SOURCE_TYPE,
                                "source_id": item.source_id,
                                "context": item.context,
                                "opened_at": now,
                                "first_seen_at": now,
                                "last_seen_at": now,
                                "occurrence_count": count,
                                "evidence_snapshot": item.evidence,
                                "source_anomaly_ids": [str(item.anomaly_id)],
                            }
                            for item, count in fresh
                        ]
                    )
                )
                report.alerts_opened += len(fresh)
            for item, count in repeats:
                await self.session.execute(
                    update(Alert)
                    .where(
                        Alert.fingerprint == item.fingerprint,
                        Alert.status != AlertStatus.RESOLVED.value,
                    )
                    .values(
                        occurrence_count=Alert.occurrence_count + count,
                        last_seen_at=now,
                        evidence_snapshot=item.evidence,
                        context=item.context,
                        severity=item.severity,
                        description=item.description,
                        updated_at=func.now(),
                    )
                )
        if batch.resolves:
            now = self._clock()
            for closing in batch.resolves:
                result = await self.session.execute(
                    update(Alert)
                    .where(
                        Alert.fingerprint == closing.fingerprint,
                        Alert.status != AlertStatus.RESOLVED.value,
                    )
                    .values(
                        status=AlertStatus.RESOLVED.value,
                        resolved_at=now,
                        resolved_by=ACTOR,
                        resolution_reason=closing.reason,
                        updated_at=func.now(),
                    )
                )
                report.alerts_resolved += int(getattr(result, "rowcount", 0) or 0)


def _collapse_opens(
    allowed: Sequence[_AlertOpen], existing: set[str]
) -> tuple[list[tuple[_AlertOpen, int]], list[tuple[_AlertOpen, int]]]:
    """One open alert per fingerprint. The last fire in the tick is the evidence.

    A tick judges every due bucket. ``uq_alert_active_dedup`` allows one open
    row per fingerprint, so repeats of that fingerprint are a count.
    """
    latest: dict[str, _AlertOpen] = {}
    fires: dict[str, int] = {}
    for item in allowed:
        latest[item.fingerprint] = item
        fires[item.fingerprint] = fires.get(item.fingerprint, 0) + 1
    fresh: list[tuple[_AlertOpen, int]] = []
    repeats: list[tuple[_AlertOpen, int]] = []
    for fingerprint, item in latest.items():
        pair = (item, fires[fingerprint])
        if fingerprint in existing:
            repeats.append(pair)
        else:
            fresh.append(pair)
    return fresh, repeats


def _group(series: Sequence[IdentitySeries]) -> dict[_Key, list[IdentitySeries]]:
    grouped: dict[_Key, list[IdentitySeries]] = defaultdict(list)
    for fact in series:
        for detector in detectors_for(fact.event_id):
            grouped[(fact.log_source_id, fact.event_id, detector)].append(fact)
    return grouped


def _incident_from_row(row: Any) -> _Incident:
    details = row.details if isinstance(row.details, dict) else {}
    return _Incident(
        id=row.id,
        log_source_id=row.log_source_id,
        event_id=int(row.event_id),
        detector=str(row.detector_type),
        fingerprint=str(row.fingerprint),
        severity=str(row.severity),
        state=str(row.state),
        detected_at=row.detected_at,
        opened_at=row.opened_at,
        anomaly_start=row.anomaly_start,
        anomaly_end=row.anomaly_end,
        resolved_at=row.resolved_at,
        observed_value=row.observed_value,
        expected_value=row.expected_value,
        deviation_score=row.deviation_score,
        deviation_ratio=row.deviation_ratio,
        robust_z=row.robust_z,
        absolute_delta=row.absolute_delta,
        consecutive_buckets=int(row.consecutive_buckets),
        policy_version=int(row.policy_version),
        profile_version=int(row.profile_version),
        catalog_version=int(row.catalog_version),
        explanation=row.explanation,
        details=details,
        evidence_status=str(row.evidence_status),
        suppressed=bool(row.suppressed),
        suppression_reason=row.suppression_reason,
        observed_measurement=str(row.observed_measurement),
        is_new=False,
    )


def _incident_insert(item: _Incident) -> dict[str, Any]:
    return {
        "id": item.id,
        "log_source_id": item.log_source_id,
        "event_id": item.event_id,
        "detector_type": item.detector,
        "fingerprint": item.fingerprint,
        "severity": item.severity,
        "state": item.state,
        "detected_at": item.detected_at,
        "opened_at": item.opened_at,
        "anomaly_start": item.anomaly_start,
        "anomaly_end": item.anomaly_end,
        "resolved_at": item.resolved_at,
        "observed_value": item.observed_value,
        "expected_value": item.expected_value,
        "deviation_score": item.deviation_score,
        "deviation_ratio": item.deviation_ratio,
        "robust_z": item.robust_z,
        "absolute_delta": item.absolute_delta,
        "consecutive_buckets": item.consecutive_buckets,
        "policy_version": item.policy_version,
        "profile_version": item.profile_version,
        "catalog_version": item.catalog_version,
        "explanation": item.explanation,
        "details": item.details,
        "evidence_status": item.evidence_status,
        "suppressed": item.suppressed,
        "suppression_reason": item.suppression_reason,
        "observed_measurement": item.observed_measurement,
    }


def _incident_update(item: _Incident) -> dict[str, Any]:
    values = _incident_insert(item)
    values.pop("id")
    values["updated_at"] = func.now()
    return values
