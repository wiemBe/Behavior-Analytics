"""Batch-oriented IPS anomaly evaluation and lifecycle persistence.

For one collected source bucket the engine loads every active entity, its sparse
metric row, seasonal cells, detector states, live incidents and destination-port
novelty facts in a fixed number of statements. Pure detectors then judge plain
values in memory and the shared anomaly lifecycle folds their verdicts.
"""

from __future__ import annotations

import bisect
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.fingerprint import ips_anomaly_fingerprint
from app.alerts.service import AlertInput, AlertResolution, AlertService
from app.anomaly.capabilities import IPS_DETECTORS
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.lifecycle import LifecycleDecision, LifecycleInput, advance_counters, next_state
from app.core.config import Settings, get_settings
from app.i18n.messages import MessageRef, render
from app.ips.behavior import IPS_DETECTOR_FUNCTIONS, appearance_flood_ref, verify_port_history
from app.ips.detector import (
    IpsBaselineCell,
    IpsDetectionContext,
    IpsNoveltyFacts,
    IpsThresholds,
    IpsVerdict,
)
from app.ips.inputs import (
    IpsEntityFacts,
    IpsObservation,
    IpsSourceBucketFacts,
    ips_history_incomplete,
    observe_entity,
)
from app.models.enums import (
    AnomalyState,
    EvidenceStatus,
    IpsAnomalyType,
    IpsDimension,
    WafMeasurement,
)
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.ips import (
    IpsAnomaly,
    IpsAnomalyTransition,
    IpsDetectorState,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
)
from app.models.log_source import LogSource
from app.waf.detector import Reason
from app.waf.inputs import HistoryFacts

_ENTITY_TYPES = {
    IpsAnomalyType.IPS_PORT_RATE_SPIKE: IpsDimension.DESTINATION_PORT,
    IpsAnomalyType.IPS_DESTINATION_RATE_SPIKE: IpsDimension.DESTINATION_IP,
    IpsAnomalyType.IPS_SIGNATURE_BURST: IpsDimension.SIGNATURE,
    IpsAnomalyType.IPS_NEW_DESTINATION_PORT: IpsDimension.DESTINATION_PORT,
}
_SOURCE_TYPES = tuple(detector for detector in IpsAnomalyType if detector not in _ENTITY_TYPES)
#: Bound on ids per IN list, far below asyncpg's bind-parameter limit, so a
#: source at its active-entity ceiling still evaluates in chunked bulk loads.
_ID_CHUNK = 5000
_STATE_COLUMNS = (
    "consecutive_anomalous",
    "consecutive_healthy",
    "state",
    "last_interval_start",
    "active_anomaly_id",
    "open_anomaly_id",
)
_ANOMALY_COLUMNS = (
    "state",
    "severity",
    "opened_at",
    "anomaly_end",
    "resolved_at",
    "observed_value",
    "observed_measurement",
    "deviation_score",
    "deviation_ratio",
    "robust_z",
    "absolute_delta",
    "consecutive_buckets",
    "explanation",
    "explanation_i18n",
    "details",
    "suppressed",
    "suppression_reason",
    "suppression_reason_i18n",
)

#: The suppression reason a maintenance window writes (MSG-6b). The sentence is the WAF one
#: (the same English, no parameters), stored beside its `_i18n` form.
_MAINTENANCE = MessageRef("waf.suppression.maintenance_window")


@dataclass
class IpsEvaluationReport:
    subject_kind: str
    subject_id: uuid.UUID
    bucket_start: datetime
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    states: dict[str, str] = field(default_factory=dict)
    skipped_reason: str | None = None


@dataclass
class IpsBucketEvaluation:
    bucket_start: datetime
    subject_reports: list[IpsEvaluationReport] = field(default_factory=list)
    inferred_observations: int = 0

    @property
    def opened(self) -> int:
        return sum(len(report.opened) for report in self.subject_reports)

    @property
    def resolved(self) -> int:
        return sum(len(report.resolved) for report in self.subject_reports)


@dataclass(frozen=True)
class _Subject:
    kind: str
    subject_id: uuid.UUID
    entity: IpsEntity | None
    observation: IpsObservation


@dataclass(frozen=True)
class _AlertOpenOrUpdate:
    entity: IpsEntity | None
    source_id: uuid.UUID
    detector_type: IpsAnomalyType
    evidence: AnomalyEvidence
    anomaly_id: uuid.UUID | None


@dataclass(frozen=True)
class _AlertResolve:
    entity: IpsEntity | None
    source_id: uuid.UUID
    detector_type: IpsAnomalyType


type _AlertAction = _AlertOpenOrUpdate | _AlertResolve

#: The subject of an entity incident in words, for the alert title.
_DIMENSION_WORDS = {
    IpsDimension.DESTINATION_PORT: "destination port",
    IpsDimension.DESTINATION_IP: "destination IP",
    IpsDimension.SIGNATURE: "signature",
}


def _chunks(ids: Sequence[uuid.UUID]) -> Iterable[Sequence[uuid.UUID]]:
    for i in range(0, len(ids), _ID_CHUNK):
        yield ids[i : i + _ID_CHUNK]


class IpsAnomalyEngine:
    """Evaluate every registered IPS detector without per-entity SQL."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        detectors: dict[
            IpsAnomalyType, Callable[[IpsDetectionContext], IpsVerdict]
        ] | None = None,
        enqueuer: AlertEnqueuer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.detectors = detectors if detectors is not None else IPS_DETECTOR_FUNCTIONS
        self.alerts = AlertService(session, clock=self._clock)
        self._enqueuer = enqueuer
        self.history_end: datetime | None = None

    def _thresholds(self) -> IpsThresholds:
        settings = self.settings
        return IpsThresholds(
            deviation_z=settings.anomaly_deviation_threshold,
            mad_floor_ratio=settings.baseline_mad_floor_ratio,
            min_coverage=settings.ips_min_coverage,
            baseline_min_samples=settings.baseline_min_samples,
            spike_ratio=settings.ips_spike_ratio,
            min_absolute_delta_events=settings.ips_min_absolute_delta_events,
            min_bucket_events=settings.ips_min_bucket_events,
            fanout_min_ports=settings.ips_fanout_min_ports,
            fanout_ratio=settings.ips_fanout_ratio,
            fanout_min_delta_ports=settings.ips_fanout_min_delta_ports,
            ratio_min_denominator=settings.ips_ratio_min_denominator,
            ratio_min_numerator=settings.ips_ratio_min_numerator,
            ratio_min_increase=settings.ips_ratio_min_increase,
            ratio_min_multiple=settings.ips_ratio_min_multiple,
            concentration_min_source_events=settings.ips_concentration_min_source_events,
            concentration_min_top1_events=settings.ips_concentration_min_top1_events,
            concentration_min_increase=settings.ips_concentration_min_increase,
            new_port_min_events=settings.ips_new_port_min_events,
            new_port_min_buckets=settings.ips_new_port_min_buckets,
            new_port_window_buckets=settings.ips_new_port_window_buckets,
            new_port_history_days=settings.ips_new_port_history_days,
            admission_min_events=settings.ips_min_admission_events,
            open_after=settings.anomaly_open_after_intervals,
            resolve_after=settings.anomaly_resolve_after_intervals,
        )

    async def evaluate_bucket(
        self, source: LogSource, bucket: IpsSourceBucket
    ) -> IpsBucketEvaluation:
        """Judge one source bucket, loading every subject dependency in bulk."""
        facts = IpsSourceBucketFacts.of(bucket)
        subjects = await self._load_subjects(source, facts)
        result = IpsBucketEvaluation(bucket.bucket_start)
        result.inferred_observations = sum(
            1
            for subject in subjects
            if subject.entity is not None
            and subject.observation.count is not None
            and subject.observation.count.events == 0
        )
        result.subject_reports = await self._evaluate(source, subjects)
        return result

    async def _load_subjects(
        self, source: LogSource, facts: IpsSourceBucketFacts
    ) -> list[_Subject]:
        dimensions = {dimension.value for dimension in _ENTITY_TYPES.values()}
        rows = (
            await self.session.execute(
                select(IpsEntity, IpsEntityMetric)
                .outerjoin(
                    IpsEntityMetric,
                    and_(
                        IpsEntityMetric.ips_entity_id == IpsEntity.id,
                        IpsEntityMetric.bucket_start == facts.bucket_start,
                    ),
                )
                .where(
                    IpsEntity.log_source_id == source.id,
                    IpsEntity.active.is_(True),
                    IpsEntity.dimension.in_(dimensions),
                )
                .order_by(IpsEntity.id)
            )
        ).all()
        subjects: list[_Subject] = []
        for entity, metric in rows:
            entity_facts = IpsEntityFacts(
                dimension=IpsDimension(str(entity.dimension)),
                value_key=entity.value_key,
                display_name=entity.display_name,
                first_seen_at=entity.first_seen_at,
                active=entity.active,
                last_seen_at=entity.last_seen_at,
                normalizer_version=entity.normalizer_version,
            )
            subjects.append(
                _Subject(
                    "IPS_ENTITY",
                    entity.id,
                    entity,
                    observe_entity(
                        entity_facts,
                        facts,
                        metric.events if metric is not None else None,
                        min_coverage=self.settings.ips_min_coverage,
                    ),
                )
            )
        subjects.append(_Subject("IPS_SOURCE", source.id, None, IpsObservation(bucket=facts)))
        return subjects

    async def _evaluate(
        self, source: LogSource, subjects: Sequence[_Subject]
    ) -> list[IpsEvaluationReport]:
        bucket = subjects[0].observation.bucket
        entity_ids = [subject.subject_id for subject in subjects if subject.entity is not None]
        entity_metrics = {
            metric
            for detector_type in self.detectors
            if detector_type in _ENTITY_TYPES
            for metric in IPS_DETECTORS[detector_type].baseline_metrics
        }
        source_metrics = {
            metric
            for detector_type in self.detectors
            if detector_type in _SOURCE_TYPES
            for metric in IPS_DETECTORS[detector_type].baseline_metrics
        }
        weekday, hour = bucket.bucket_start.isoweekday(), bucket.bucket_start.hour
        entity_cells = await self._load_entity_cells(entity_ids, entity_metrics, weekday, hour)
        source_cells = await self._load_source_cells(source.id, source_metrics, weekday, hour)
        wanted = self._wanted_state_keys(subjects)
        states = await self._load_states(source.id, entity_ids)
        await self._ensure_states(wanted, states)
        if set(wanted) - set(states):
            states.update(await self._load_states(source.id, entity_ids))
        if set(wanted) - set(states):
            raise RuntimeError("IPS detector state is missing after bulk creation and reload")
        anomaly_ids = {
            anomaly_id
            for state in states.values()
            for anomaly_id in (state.active_anomaly_id, state.open_anomaly_id)
            if anomaly_id is not None
        }
        anomalies = await self._load_anomalies(anomaly_ids)
        thresholds = self._thresholds()
        novelty = (
            await self._load_novelty(subjects, thresholds)
            if IpsAnomalyType.IPS_NEW_DESTINATION_PORT in self.detectors
            else {}
        )

        reports: list[IpsEvaluationReport] = []
        modified: list[IpsDetectorState] = []
        alert_actions: list[_AlertAction] = []
        for subject in subjects:
            report = IpsEvaluationReport(
                subject.kind, subject.subject_id, subject.observation.bucket.bucket_start
            )
            # A recorded maintenance bucket is outside the lifecycle entirely.
            if bucket.in_maintenance:
                report.skipped_reason = "source was in maintenance"
                reports.append(report)
                continue
            for detector_type in self._types_for(subject):
                metric = next(iter(sorted(IPS_DETECTORS[detector_type].baseline_metrics)), None)
                row = None
                if metric is not None:
                    row = (
                        entity_cells.get((subject.subject_id, metric))
                        if subject.entity is not None
                        else source_cells.get(metric)
                    )
                verdict = self.detectors[detector_type](
                    IpsDetectionContext(
                        observation=subject.observation,
                        baseline=self._cell(row),
                        thresholds=thresholds,
                        novelty=novelty.get(subject.subject_id),
                        extra={"log_source_id": str(source.id)},
                    )
                )
                if (
                    detector_type is IpsAnomalyType.IPS_NEW_DESTINATION_PORT
                    and subject.entity is not None
                    and self.history_end is not None
                    and subject.entity.first_seen_at < self.history_end
                ):
                    established = MessageRef(
                        "ips.novelty.established_hand_off",
                        {
                            "first_seen": subject.entity.first_seen_at,
                            "hand_off": self.history_end,
                        },
                    )
                    verdict.evidence.reason = render(established)
                    verdict.evidence.reason_message = established
                state = states[(subject.kind, subject.subject_id, detector_type.value)]
                if self._apply_hysteresis(
                    source,
                    subject,
                    detector_type,
                    verdict.evidence,
                    state,
                    anomalies,
                    report,
                    thresholds,
                    alert_actions,
                ):
                    modified.append(state)
            reports.append(report)
        self._uniform_updates(modified, anomalies.values())
        if alert_actions:
            await self._apply_alert_actions(source, alert_actions)
        else:
            await self.session.flush()
        return reports

    def _types_for(self, subject: _Subject) -> list[IpsAnomalyType]:
        if subject.entity is None:
            return [detector for detector in self.detectors if detector in _SOURCE_TYPES]
        dimension = IpsDimension(str(subject.entity.dimension))
        return [detector for detector in self.detectors if _ENTITY_TYPES.get(detector) is dimension]

    async def _load_entity_cells(
        self, ids: Sequence[uuid.UUID], metrics: set[str], weekday: int, hour: int
    ) -> dict[tuple[uuid.UUID, str], IpsEntityBaseline]:
        if not ids or not metrics:
            return {}
        out: dict[tuple[uuid.UUID, str], IpsEntityBaseline] = {}
        for chunk in _chunks(ids):
            rows = await self.session.scalars(
                select(IpsEntityBaseline).where(
                    IpsEntityBaseline.ips_entity_id.in_(chunk),
                    IpsEntityBaseline.metric_name.in_(metrics),
                    IpsEntityBaseline.weekday == weekday,
                    IpsEntityBaseline.hour == hour,
                )
            )
            for row in rows:
                out[(row.ips_entity_id, row.metric_name)] = row
        return out

    async def _load_source_cells(
        self, source_id: uuid.UUID, metrics: set[str], weekday: int, hour: int
    ) -> dict[str, IpsSourceBaseline]:
        if not metrics:
            return {}
        rows = await self.session.scalars(
            select(IpsSourceBaseline).where(
                IpsSourceBaseline.log_source_id == source_id,
                IpsSourceBaseline.metric_name.in_(metrics),
                IpsSourceBaseline.weekday == weekday,
                IpsSourceBaseline.hour == hour,
            )
        )
        return {row.metric_name: row for row in rows}

    def _wanted_state_keys(
        self, subjects: Sequence[_Subject]
    ) -> list[tuple[str, uuid.UUID, str]]:
        return [
            (subject.kind, subject.subject_id, detector.value)
            for subject in subjects
            for detector in self._types_for(subject)
        ]

    async def _load_states(
        self, source_id: uuid.UUID, entity_ids: Sequence[uuid.UUID]
    ) -> dict[tuple[str, uuid.UUID, str], IpsDetectorState]:
        result: dict[tuple[str, uuid.UUID, str], IpsDetectorState] = {}
        entity_chunks = list(_chunks(entity_ids)) if entity_ids else []
        for entity_chunk in entity_chunks or [()]:
            conditions = [
                and_(
                    IpsDetectorState.subject_kind == "IPS_SOURCE",
                    IpsDetectorState.log_source_id == source_id,
                )
            ]
            if entity_chunk:
                conditions.append(
                    and_(
                        IpsDetectorState.subject_kind == "IPS_ENTITY",
                        IpsDetectorState.ips_entity_id.in_(entity_chunk),
                    )
                )
            rows = await self.session.scalars(
                select(IpsDetectorState).where(or_(*conditions))
            )
            for row in rows:
                subject_id = (
                    row.ips_entity_id if row.ips_entity_id is not None else row.log_source_id
                )
                if subject_id is not None:
                    result[(row.subject_kind, subject_id, str(row.detector_type))] = row
        return result

    async def _ensure_states(
        self,
        wanted: Sequence[tuple[str, uuid.UUID, str]],
        states: dict[tuple[str, uuid.UUID, str], IpsDetectorState],
    ) -> None:
        missing = [item for item in wanted if item not in states]
        if not missing:
            return
        statement = text(
            """
            INSERT INTO ips_detector_state (
                id, subject_kind, ips_entity_id, log_source_id, detector_type,
                consecutive_anomalous, consecutive_healthy, state
            )
            SELECT pending.id, pending.subject_kind,
                   CASE WHEN pending.subject_kind = 'IPS_ENTITY' THEN pending.subject_id END,
                   CASE WHEN pending.subject_kind = 'IPS_SOURCE' THEN pending.subject_id END,
                   pending.detector_type, 0, 0, 'NORMAL'
            FROM unnest(
                CAST(:ids AS uuid[]), CAST(:subject_kinds AS text[]),
                CAST(:subject_ids AS uuid[]), CAST(:detector_types AS text[])
            ) AS pending(id, subject_kind, subject_id, detector_type)
            ON CONFLICT DO NOTHING
            RETURNING ips_detector_state.*
            """
        )
        created = await self.session.scalars(
            select(IpsDetectorState).from_statement(statement),
            {
                "ids": [uuid.uuid4() for _ in missing],
                "subject_kinds": [item[0] for item in missing],
                "subject_ids": [item[1] for item in missing],
                "detector_types": [item[2] for item in missing],
            },
        )
        for state in created:
            subject_id = (
                state.ips_entity_id if state.ips_entity_id is not None else state.log_source_id
            )
            if subject_id is not None:
                states[(state.subject_kind, subject_id, str(state.detector_type))] = state

    async def _load_anomalies(
        self, ids: set[uuid.UUID]
    ) -> dict[uuid.UUID, IpsAnomaly]:
        if not ids:
            return {}
        out: dict[uuid.UUID, IpsAnomaly] = {}
        for chunk in _chunks(sorted(ids)):
            for row in await self.session.scalars(
                select(IpsAnomaly).where(IpsAnomaly.id.in_(chunk))
            ):
                out[row.id] = row
        return out

    async def _load_novelty(
        self, subjects: Sequence[_Subject], thresholds: IpsThresholds
    ) -> dict[uuid.UUID, IpsNoveltyFacts]:
        ports = [
            subject
            for subject in subjects
            if subject.entity is not None
            and IpsDimension(str(subject.entity.dimension)) is IpsDimension.DESTINATION_PORT
        ]
        result: dict[uuid.UUID, IpsNoveltyFacts] = {}
        pending: list[_Subject] = []
        history_delta = timedelta(days=thresholds.new_port_history_days)
        for subject in ports:
            assert subject.entity is not None
            bucket = subject.observation.bucket
            if (
                self.history_end is not None
                and subject.entity.first_seen_at < self.history_end
            ):
                result[subject.entity.id] = IpsNoveltyFacts(
                    first_seen_at=subject.entity.first_seen_at,
                    window_end=self.history_end,
                    within_window=False,
                    readmitted=False,
                    history=replace(
                        ips_history_incomplete(
                            "ips.history_check.before_hand_off", hand_off=self.history_end
                        ),
                        complete=True,
                    ),
                    history_start=subject.entity.first_seen_at,
                    history_end=self.history_end,
                    history_buckets=0,
                    appearance_flood=None,
                    events_since_first_seen=0,
                    buckets_with_traffic=0,
                    lower_bound=False,
                )
                continue
            window_end = subject.entity.first_seen_at + timedelta(
                seconds=thresholds.new_port_window_buckets * bucket.bucket_seconds
            )
            if bucket.bucket_start >= window_end:
                result[subject.entity.id] = IpsNoveltyFacts(
                    first_seen_at=subject.entity.first_seen_at,
                    window_end=window_end,
                    within_window=False,
                    readmitted=False,
                    history=ips_history_incomplete("ips.history_check.not_loaded"),
                    history_start=subject.entity.first_seen_at - history_delta,
                    history_end=subject.entity.first_seen_at,
                    history_buckets=0,
                    appearance_flood=None,
                    events_since_first_seen=0,
                    buckets_with_traffic=0,
                    lower_bound=False,
                )
            else:
                pending.append(subject)
        if not pending:
            return result

        entities = [subject.entity for subject in pending if subject.entity is not None]
        earliest = min(entity.first_seen_at for entity in entities) - history_delta
        judged_at = pending[0].observation.bucket.bucket_start
        source_id = entities[0].log_source_id
        source_rows = list(
            (
                await self.session.scalars(
                    select(IpsSourceBucket)
                    .where(
                        IpsSourceBucket.log_source_id == source_id,
                        IpsSourceBucket.bucket_start >= earliest,
                        IpsSourceBucket.bucket_start <= judged_at,
                    )
                    .order_by(IpsSourceBucket.bucket_start)
                )
            ).all()
        )
        facts = [IpsSourceBucketFacts.of(row) for row in source_rows]
        starts = [row.bucket_start for row in facts]
        by_start = {row.bucket_start: row for row in facts}
        traffic: dict[uuid.UUID, tuple[int, int]] = {}
        for offset in range(0, len(entities), _ID_CHUNK):
            chunk = entities[offset : offset + _ID_CHUNK]
            traffic_rows = await self.session.execute(
                select(
                    IpsEntityMetric.ips_entity_id,
                    func.coalesce(func.sum(IpsEntityMetric.events), 0).label("events"),
                    func.count().filter(IpsEntityMetric.events > 0).label("traffic_buckets"),
                )
                .where(
                    IpsEntityMetric.ips_entity_id.in_([entity.id for entity in chunk]),
                    IpsEntityMetric.bucket_start >= min(
                        entity.first_seen_at for entity in chunk
                    ),
                    IpsEntityMetric.bucket_start <= judged_at,
                )
                .group_by(IpsEntityMetric.ips_entity_id)
            )
            for row in traffic_rows:
                traffic[row.ips_entity_id] = (int(row.events), int(row.traffic_buckets))
        verified: dict[tuple[datetime, int], tuple[HistoryFacts, int, Reason | None]] = {}
        for subject in pending:
            entity = subject.entity
            assert entity is not None
            first_seen = entity.first_seen_at
            key = (first_seen, entity.normalizer_version)
            if key not in verified:
                left = bisect.bisect_left(starts, first_seen - history_delta)
                right = bisect.bisect_left(starts, first_seen)
                history_rows = facts[left:right]
                appearance = by_start.get(first_seen)
                history: HistoryFacts
                flood: Reason | None
                if appearance is None:
                    history = ips_history_incomplete("waf.history_check.appearance_never_collected")
                    flood = None
                else:
                    history = verify_port_history(
                        history_rows,
                        history_start=first_seen - history_delta,
                        history_end=first_seen,
                        bucket_seconds=appearance.bucket_seconds,
                        profile_version=appearance.profile_version,
                        normalizer_version=entity.normalizer_version,
                        min_coverage=thresholds.min_coverage,
                    )
                    flood = appearance_flood_ref(appearance, min_coverage=thresholds.min_coverage)
                verified[key] = (history, len(history_rows), flood)
            history, history_buckets, flood = verified[key]
            bucket = subject.observation.bucket
            width = timedelta(seconds=bucket.bucket_seconds)
            expected = (judged_at - first_seen) // width + 1
            observed_rows = facts[
                bisect.bisect_left(starts, first_seen) : bisect.bisect_right(starts, judged_at)
            ]
            lower_bound = len(observed_rows) < expected or any(
                row.destination_port.waf_measurement is not WafMeasurement.EXACT
                for row in observed_rows
            )
            events, traffic_buckets = traffic.get(entity.id, (0, 0))
            result[entity.id] = IpsNoveltyFacts(
                first_seen_at=first_seen,
                window_end=first_seen
                + timedelta(
                    seconds=thresholds.new_port_window_buckets * bucket.bucket_seconds
                ),
                within_window=True,
                readmitted=False,
                history=history,
                history_start=first_seen - history_delta,
                history_end=first_seen,
                history_buckets=history_buckets,
                appearance_flood=flood,
                events_since_first_seen=events,
                buckets_with_traffic=traffic_buckets,
                lower_bound=lower_bound,
            )
        return result

    @staticmethod
    def _cell(
        row: IpsEntityBaseline | IpsSourceBaseline | None,
    ) -> IpsBaselineCell | None:
        if row is None:
            return None
        return IpsBaselineCell(
            median=row.median,
            mad=row.mad,
            p05=row.p05,
            p95=row.p95,
            sample_count=row.sample_count,
            is_reliable=row.is_reliable,
            completeness=row.completeness,
            baseline_version=row.baseline_version,
            numerator_sum=getattr(row, "numerator_sum", None),
            denominator_sum=getattr(row, "denominator_sum", None),
            metric_semantics=row.metric_semantics,
            profile_version=row.profile_version,
            normalizer_version=row.normalizer_version,
        )

    def _apply_hysteresis(
        self,
        source: LogSource,
        subject: _Subject,
        detector_type: IpsAnomalyType,
        evidence: AnomalyEvidence,
        state: IpsDetectorState,
        anomalies: dict[uuid.UUID, IpsAnomaly],
        report: IpsEvaluationReport,
        thresholds: IpsThresholds,
        alert_actions: list[_AlertAction],
    ) -> bool:
        bucket = subject.observation.bucket
        if (
            state.last_interval_start is not None
            and state.last_interval_start >= bucket.bucket_start
        ):
            return False
        state.consecutive_anomalous, state.consecutive_healthy = advance_counters(
            evidence.signal, state.consecutive_anomalous, state.consecutive_healthy
        )
        state.last_interval_start = bucket.bucket_start
        decision = next_state(
            LifecycleInput(
                state=AnomalyState(state.state),
                signal=evidence.signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=thresholds.open_after,
                resolve_after=thresholds.resolve_after,
                insufficient_data=evidence.insufficient_data,
                suppressed=False,
            )
        )
        self._apply_transition(
            source, subject, detector_type, evidence, state, decision, anomalies, report,
            alert_actions,
        )
        return True

    def _apply_transition(
        self,
        source: LogSource,
        subject: _Subject,
        detector_type: IpsAnomalyType,
        evidence: AnomalyEvidence,
        state: IpsDetectorState,
        decision: LifecycleDecision,
        anomalies: dict[uuid.UUID, IpsAnomaly],
        report: IpsEvaluationReport,
        alert_actions: list[_AlertAction],
    ) -> None:
        previous = AnomalyState(state.state)
        target = decision.state
        report.states[detector_type.value] = target.value
        # An alert is raised only behind its own setting, and never for a bucket
        # recorded in maintenance (which the evaluation loop skips before this).
        alerting = (
            self.settings.ips_alerting_enabled and not subject.observation.bucket.in_maintenance
        )
        if target is previous:
            if previous is AnomalyState.OPEN:
                self._touch(state, subject.observation, evidence, anomalies)
                if alerting and evidence.is_anomalous:
                    alert_actions.append(
                        _AlertOpenOrUpdate(
                            subject.entity,
                            source.id,
                            detector_type,
                            evidence,
                            state.open_anomaly_id,
                        )
                    )
            return
        state.state = target
        if target is AnomalyState.CANDIDATE:
            incident = self._create_incident(
                source, subject, detector_type, evidence, anomalies
            )
            state.active_anomaly_id = incident.id
            self._record_transition(incident, previous, target, subject, decision.message, evidence)
            return
        anomaly = self._active(state, anomalies)
        if target is AnomalyState.OPEN:
            incident = anomaly or self._create_incident(
                source, subject, detector_type, evidence, anomalies
            )
            incident.state = AnomalyState.OPEN
            incident.opened_at = self._clock()
            incident.anomaly_end = None
            incident.severity = evidence.severity
            state.active_anomaly_id = incident.id
            state.open_anomaly_id = incident.id
            self._touch(state, subject.observation, evidence, anomalies)
            self._record_transition(incident, previous, target, subject, decision.message, evidence)
            report.opened.append(detector_type.value)
            if alerting:
                alert_actions.append(
                    _AlertOpenOrUpdate(
                        subject.entity, source.id, detector_type, evidence, incident.id
                    )
                )
            return
        if anomaly is not None:
            self._record_transition(anomaly, previous, target, subject, decision.message, evidence)
        if target is AnomalyState.RECOVERING:
            if anomaly is not None:
                anomaly.state = target
                anomaly.anomaly_end = subject.observation.bucket.bucket_start
            return
        if target is AnomalyState.RESOLVED:
            if anomaly is not None and anomaly.resolved_at is None:
                anomaly.state = target
                anomaly.resolved_at = self._clock()
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            report.resolved.append(detector_type.value)
            if self.settings.ips_alerting_enabled:
                alert_actions.append(_AlertResolve(subject.entity, source.id, detector_type))
            return
        if target is AnomalyState.SUPPRESSED:
            if anomaly is not None:
                anomaly.state = target
                anomaly.suppressed = True
                anomaly.suppression_reason = render(_MAINTENANCE)
                anomaly.suppression_reason_i18n = _MAINTENANCE.to_wire()
            return
        if anomaly is not None and previous is AnomalyState.CANDIDATE:
            anomaly.state = target
            anomaly.anomaly_end = subject.observation.bucket.bucket_start
        state.active_anomaly_id = None
        state.open_anomaly_id = None

    async def _apply_alert_actions(
        self, source: LogSource, actions: Sequence[_AlertAction]
    ) -> None:
        """Apply the synchronous fold's alert side effects in one async batch."""
        opens = [item for item in actions if isinstance(item, _AlertOpenOrUpdate)]
        instance_name: str | None = None
        source_class: str | None = None
        if opens:
            # One round trip for the instance name and the Phase C class that
            # class routing matches on; a source with no classification carries none.
            row = (
                await self.session.execute(
                    select(QRadarInstance.name, LogSourceClassification.source_class)
                    .outerjoin(
                        LogSourceClassification,
                        LogSourceClassification.log_source_id == source.id,
                    )
                    .where(QRadarInstance.id == source.instance_id)
                )
            ).one_or_none()
            if row is not None:
                instance_name, source_class = row
        open_inputs = [
            self._alert_input(source, instance_name, item, source_class) for item in opens
        ]
        resolves = [
            AlertResolution(
                fingerprint=self._fingerprint(
                    source.instance_id, item.source_id, item.entity, item.detector_type
                ),
                actor="ips-anomaly-engine",
                reason="condition recovered",
            )
            for item in actions
            if isinstance(item, _AlertResolve)
        ]
        opened_or_updated, resolved = await self.alerts.apply_bulk(open_inputs, resolves)
        if self._enqueuer is None:
            return
        for result in [*opened_or_updated, *resolved]:
            if result is not None and result.transition is not None:
                await self._enqueuer.enqueue(result.alert, result.transition)

    def _alert_input(
        self,
        source: LogSource,
        instance_name: str | None,
        action: _AlertOpenOrUpdate,
        source_class: str | None = None,
    ) -> AlertInput:
        entity = action.entity
        evidence = action.evidence
        detector_type = action.detector_type
        facts = evidence.extra or {}
        context: dict[str, object] = {
            "log_source": source.name,
            "owner": source.owner,
            "criticality": str(source.criticality),
            "instance": instance_name,
            "detector_type": detector_type.value,
        }
        title = f"{detector_type.value} on {source.name}"
        if entity is not None:
            # The stored identity of an admitted entity, never a raw event value.
            display = entity.display_name or entity.value_key
            dimension = IpsDimension(str(entity.dimension))
            context["dimension"] = dimension.value
            context["entity"] = display
            title = f"{title}: {_DIMENSION_WORDS[dimension]} {display}"
        for key, value in (
            ("observed", evidence.observed_value),
            ("expected", evidence.expected_value),
            ("ratio", _as_float(facts.get("ratio"))),
        ):
            if value is not None:
                context[key] = value
        # Added only when known, and never part of the fingerprint: a route
        # scoped to a class matches, and an unclassified source cannot slip in.
        if source_class is not None:
            context["source_class"] = str(source_class)
        return AlertInput(
            fingerprint=self._fingerprint(
                source.instance_id, action.source_id, entity, detector_type
            ),
            title=title,
            severity=evidence.severity,
            source_type="ips_entity" if entity is not None else "ips_source",
            source_id=entity.id if entity is not None else action.source_id,
            description=evidence.reason,
            evidence=evidence.to_dict(),
            source_anomaly_ids=(
                [str(action.anomaly_id)] if action.anomaly_id is not None else []
            ),
            context=context,
        )

    @staticmethod
    def _fingerprint(
        instance_id: uuid.UUID,
        log_source_id: uuid.UUID,
        entity: IpsEntity | None,
        detector_type: IpsAnomalyType,
    ) -> str:
        return ips_anomaly_fingerprint(
            instance_id=instance_id,
            log_source_id=log_source_id,
            detector_type=detector_type.value,
            dimension=str(entity.dimension) if entity is not None else None,
            value_key=entity.value_key if entity is not None else None,
        )

    def _create_incident(
        self,
        source: LogSource,
        subject: _Subject,
        detector_type: IpsAnomalyType,
        evidence: AnomalyEvidence,
        anomalies: dict[uuid.UUID, IpsAnomaly],
    ) -> IpsAnomaly:
        bucket = subject.observation.bucket
        facts = evidence.extra or {}
        entity = subject.entity
        anomaly = IpsAnomaly(
            id=uuid.uuid4(),
            subject_kind=subject.kind,
            ips_entity_id=entity.id if entity is not None else None,
            log_source_id=None if entity is not None else source.id,
            detector_type=detector_type,
            fingerprint=self._fingerprint(source.instance_id, source.id, entity, detector_type),
            severity=evidence.severity,
            state=AnomalyState.CANDIDATE,
            detected_at=bucket.bucket_start,
            anomaly_start=bucket.bucket_start,
            observed_value=evidence.observed_value,
            expected_value=evidence.expected_value,
            deviation_score=evidence.deviation_score,
            deviation_ratio=_as_float(facts.get("ratio")),
            robust_z=_as_float(facts.get("robust_z")),
            absolute_delta=_as_float(facts.get("absolute_delta")),
            confidence=evidence.confidence,
            consecutive_buckets=1,
            baseline_version=_baseline_version(facts),
            policy_version=self.settings.anomaly_policy_version,
            profile_version=bucket.profile_version,
            normalizer_version=bucket.normalizer_version,
            explanation=evidence.reason,
            explanation_i18n=evidence.reason_wire,
            details=evidence.to_dict(),
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            suppressed=False,
            observed_measurement=_measurement(evidence),
        )
        self.session.add(anomaly)
        anomalies[anomaly.id] = anomaly
        return anomaly

    def _touch(
        self,
        state: IpsDetectorState,
        observation: IpsObservation,
        evidence: AnomalyEvidence,
        anomalies: dict[uuid.UUID, IpsAnomaly],
    ) -> None:
        anomaly = self._active(state, anomalies)
        if anomaly is None:
            return
        anomaly.consecutive_buckets = max(anomaly.consecutive_buckets, state.consecutive_anomalous)
        if not evidence.is_anomalous:
            return
        anomaly.anomaly_end = observation.bucket.bucket_end
        observed = evidence.observed_value
        if observed is not None and (
            anomaly.observed_value is None or observed > anomaly.observed_value
        ):
            facts = evidence.extra or {}
            anomaly.observed_value = observed
            anomaly.observed_measurement = _measurement(evidence)
            anomaly.deviation_score = evidence.deviation_score
            anomaly.deviation_ratio = _as_float(facts.get("ratio"))
            anomaly.robust_z = _as_float(facts.get("robust_z"))
            anomaly.absolute_delta = _as_float(facts.get("absolute_delta"))
            anomaly.explanation = evidence.reason
            anomaly.explanation_i18n = evidence.reason_wire
            anomaly.details = evidence.to_dict()

    @staticmethod
    def _active(
        state: IpsDetectorState, anomalies: dict[uuid.UUID, IpsAnomaly]
    ) -> IpsAnomaly | None:
        return anomalies.get(state.active_anomaly_id) if state.active_anomaly_id else None

    def _record_transition(
        self,
        anomaly: IpsAnomaly,
        previous: AnomalyState,
        target: AnomalyState,
        subject: _Subject,
        message: MessageRef,
        evidence: AnomalyEvidence,
    ) -> None:
        self.session.add(
            IpsAnomalyTransition(
                id=uuid.uuid4(),
                ips_anomaly_id=anomaly.id,
                from_state=previous,
                to_state=target,
                occurred_at=self._clock(),
                bucket_start=subject.observation.bucket.bucket_start,
                reason=render(message),
                reason_i18n=message.to_wire(),
                actor="ips-anomaly-engine",
                observed_value=evidence.observed_value,
                expected_value=evidence.expected_value,
                observed_measurement=_measurement(evidence),
            )
        )

    def _uniform_updates(
        self, states: Iterable[IpsDetectorState], anomalies: Iterable[IpsAnomaly]
    ) -> None:
        for state in (item for item in states if item not in self.session.new):
            for column in _STATE_COLUMNS:
                flag_modified(state, column)
        dirty = self.session.dirty
        for anomaly in anomalies:
            if anomaly in dirty:
                for column in _ANOMALY_COLUMNS:
                    flag_modified(anomaly, column)


def _measurement(evidence: AnomalyEvidence) -> WafMeasurement:
    facts = evidence.extra or {}
    return (
        WafMeasurement.LOWER_BOUND
        if facts.get("observed_measurement") == WafMeasurement.LOWER_BOUND
        else WafMeasurement.EXACT
    )


def _baseline_version(facts: dict) -> int | None:
    expected = facts.get("expected")
    nested = expected.get("baseline_version") if isinstance(expected, dict) else None
    value = facts.get("baseline_version", nested)
    return int(value) if isinstance(value, int | float) else None


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None
