"""WAF anomaly evaluation engine, batch-oriented.

For one collected bucket of one source it loads, in a fixed number of queries,
everything every registered detector needs for every endpoint the bucket
observed -- explicit rows, reconstructed zeros and lower bounds, baseline cells,
detector states and live incidents -- then runs the pure detectors and the pure
lifecycle in memory, and writes the outcome in one flush. The statement count
per bucket does not grow with the number of endpoints or of detectors
(tests/integration/test_waf_scale.py pins that).

Which endpoints a bucket judges (app/waf/observations.py):

  * every endpoint with a row in the bucket;
  * in a sparse bucket, additionally every endpoint *tracked* at that bucket
    that has no row -- a measured zero when the bucket's counts are exact, an
    unknown lower bound of zero otherwise. Judging the zeros is what lets an
    incident on an endpoint that went silent recover.

`app.anomaly.lifecycle.next_state` is reused **unchanged**: it is pure decision
logic over counters and two hysteresis integers with no log-source concept in
it, so Phase B gets Phase A's proven transition rules -- including the two that
were fixed live (an UNKNOWN bucket advances nothing; an active incident is not
downgraded because one bucket was unreadable) -- without a second lifecycle
system to keep in sync.

Adding a detector means adding a pure function to `WAF_DETECTOR_FUNCTIONS` and
a spec to `app.anomaly.capabilities.WAF_DETECTORS`; the loaders already fetch
each spec's baseline metrics for every endpoint in one query.
"""

from __future__ import annotations

import bisect
import uuid
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import overload

from sqlalchemy import and_, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.fingerprint import waf_anomaly_fingerprint
from app.alerts.service import AlertInput, AlertResolution, AlertService
from app.anomaly.capabilities import WAF_DETECTORS, waf_input_scope, waf_verdict_scope
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.lifecycle import (
    LifecycleDecision,
    LifecycleInput,
    advance_counters,
    next_state,
)
from app.anomaly.seasonal import in_maintenance
from app.core.config import Settings, get_settings
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyState,
    DetectorSignal,
    EvidenceStatus,
    WafAnomalyType,
    WafBreakdown,
    WafMeasurement,
)
from app.models.fleet import LogSourceClassification
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.waf import (
    WafAnomaly,
    WafAnomalyTransition,
    WafDetectorState,
    WafEndpoint,
    WafEndpointBaseline,
    WafEndpointMetric,
    WafSourceBucket,
)
from app.waf.behavior import (
    NoveltyFacts,
    WafBehaviorThresholds,
    detect_block_ratio_spike,
    detect_client_concentration_spike,
    detect_client_error_ratio_spike,
    detect_error_ratio_spike,
    detect_new_endpoint_access,
)
from app.waf.detector import (
    WafBaselineCell,
    WafDetectionContext,
    WafPoint,
    WafSpikeVerdict,
    WafThresholds,
    detect_request_rate_spike,
)
from app.waf.inputs import HistoryFacts
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.novelty import semantics_normalizer_version, verify_endpoint_history
from app.waf.observations import BucketFacts, EndpointObservation, RowFacts, observe
from app.waf.semantics import semantics_incompatibility

#: The pure detector behind each WAF anomaly type. Every entry must have a spec
#: in `app.anomaly.capabilities.WAF_DETECTORS` (unit-tested).
WAF_DETECTOR_FUNCTIONS: dict[WafAnomalyType, Callable[[WafDetectionContext], WafSpikeVerdict]] = {
    WafAnomalyType.WAF_REQUEST_RATE_SPIKE: detect_request_rate_spike,
    WafAnomalyType.WAF_BLOCK_RATIO_SPIKE: detect_block_ratio_spike,
    WafAnomalyType.WAF_ERROR_RATIO_SPIKE: detect_error_ratio_spike,
    WafAnomalyType.WAF_CLIENT_ERROR_RATIO_SPIKE: detect_client_error_ratio_spike,
    WafAnomalyType.WAF_CLIENT_CONCENTRATION_SPIKE: detect_client_concentration_spike,
    WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS: detect_new_endpoint_access,
}

#: Bound on ids per IN list, far below asyncpg's bind-parameter limit.
_ID_CHUNK = 5000

#: Every column evaluation may change on a detector state, and on an incident.
#: The unit of work batches UPDATEs into one executemany only across
#: *consecutive* rows that change the same columns; states that moved and states
#: that only advanced a counter interleave, and fragment into one UPDATE per row.
#: Marking the full set on every modified row makes them one batch.
_STATE_COLUMNS = (
    "consecutive_anomalous", "consecutive_healthy", "state", "last_interval_start",
    "active_anomaly_id", "open_anomaly_id",
)
_ANOMALY_COLUMNS = (
    "state", "severity", "opened_at", "anomaly_end", "resolved_at", "observed_value",
    "observed_measurement", "deviation_score", "deviation_ratio", "robust_z", "absolute_delta",
    "consecutive_buckets", "explanation", "details", "suppressed", "suppression_reason",
)


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
    #: The bucket fell in its source's maintenance window: an anomalous verdict
    #: was demoted to UNKNOWN and no incident could open.
    in_maintenance: bool = False
    skipped_reason: str | None = None


@dataclass
class WafBucketEvaluation:
    bucket_start: datetime
    endpoint_reports: list[WafEvaluationReport] = field(default_factory=list)
    #: Endpoints judged from a reconstructed zero or lower bound, not a row.
    inferred_observations: int = 0

    @property
    def opened(self) -> int:
        return sum(len(r.opened) for r in self.endpoint_reports)

    @property
    def resolved(self) -> int:
        return sum(len(r.resolved) for r in self.endpoint_reports)


@dataclass
class _Subject:
    endpoint: WafEndpoint
    observation: EndpointObservation


@dataclass(frozen=True)
class _AlertOpenOrUpdate:
    endpoint: WafEndpoint
    detector_type: WafAnomalyType
    evidence: AnomalyEvidence
    anomaly_id: uuid.UUID | None


@dataclass(frozen=True)
class _AlertResolve:
    endpoint: WafEndpoint
    detector_type: WafAnomalyType


type _AlertAction = _AlertOpenOrUpdate | _AlertResolve


def _chunks(ids: Sequence[uuid.UUID]) -> Iterable[Sequence[uuid.UUID]]:
    for i in range(0, len(ids), _ID_CHUNK):
        yield ids[i : i + _ID_CHUNK]


class WafAnomalyEngine:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        detectors: dict[
            WafAnomalyType, Callable[[WafDetectionContext], WafSpikeVerdict]
        ] | None = None,
        enqueuer: AlertEnqueuer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.detectors = detectors if detectors is not None else WAF_DETECTOR_FUNCTIONS
        self.alerts = AlertService(session, clock=self._clock)
        self._enqueuer = enqueuer

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

    def _behavior_thresholds(self) -> WafBehaviorThresholds:
        s = self.settings
        return WafBehaviorThresholds(
            deviation_z=s.anomaly_deviation_threshold,
            min_coverage=s.waf_min_endpoint_coverage,
            mad_floor_ratio=s.baseline_mad_floor_ratio,
            ratio_min_denominator=s.waf_ratio_min_denominator,
            ratio_min_numerator=s.waf_ratio_min_numerator,
            ratio_min_increase=s.waf_ratio_min_increase,
            ratio_min_multiple=s.waf_ratio_min_multiple,
            concentration_min_endpoint_requests=s.waf_concentration_min_endpoint_requests,
            concentration_min_client_requests=s.waf_concentration_min_client_requests,
            concentration_min_increase=s.waf_concentration_min_increase,
            new_endpoint_min_requests=s.waf_new_endpoint_min_requests,
            new_endpoint_min_buckets=s.waf_new_endpoint_min_buckets,
            new_endpoint_window_buckets=s.waf_new_endpoint_window_buckets,
            new_endpoint_history_days=s.waf_new_endpoint_history_days,
            admission_min_requests=s.waf_min_admission_requests,
        )

    def _enabled_types(self) -> list[WafAnomalyType]:
        enabled: list[WafAnomalyType] = []
        for detector_type in self.detectors:
            spec = WAF_DETECTORS.get(detector_type)
            if spec is None:
                continue
            if spec.enabling_setting is not None and not getattr(
                self.settings, spec.enabling_setting
            ):
                continue
            enabled.append(detector_type)
        return enabled

    # ------------------------------------------------------------ entry points
    async def evaluate_bucket(
        self, log_source: LogSource, bucket: WafSourceBucket
    ) -> WafBucketEvaluation:
        """Judge every endpoint one collected bucket observed. Constant queries."""
        facts = BucketFacts.of(bucket)
        subjects = await self._load_subjects(log_source, facts)
        result = WafBucketEvaluation(bucket.bucket_start)
        result.inferred_observations = sum(1 for s in subjects if s.observation.inferred)
        result.endpoint_reports = await self._evaluate(log_source, subjects)
        return result

    async def evaluate_interval(
        self,
        endpoint: WafEndpoint,
        metric: WafEndpointMetric,
        *,
        source_bucket: WafSourceBucket | None = None,
        log_source: LogSource | None = None,
    ) -> WafEvaluationReport:
        """Judge one stored row. The batch path, for a batch of one."""
        if log_source is None:
            log_source = await self.session.get(LogSource, endpoint.log_source_id)
        if source_bucket is None:
            source_bucket = await self.session.get(
                WafSourceBucket, (endpoint.log_source_id, metric.bucket_start)
            )
        observation = observe(
            RowFacts.of(metric),
            BucketFacts.of(source_bucket) if source_bucket is not None else None,
            tracked=True,
            normalizer_version=endpoint.normalizer_version,
        )
        assert observation is not None
        reports = await self._evaluate(log_source, [_Subject(endpoint, observation)])
        return reports[0]

    # ------------------------------------------------------------------ loading
    async def _load_subjects(self, log_source: LogSource, facts: BucketFacts) -> list[_Subject]:
        """Rows in the bucket, plus tracked endpoints without one. One query."""
        tracked = and_(
            WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            WafEndpoint.tracked_since <= facts.bucket_start,
            or_(
                WafEndpoint.tracked_until.is_(None),
                WafEndpoint.tracked_until >= facts.bucket_start,
            ),
        )
        query = (
            select(WafEndpoint, WafEndpointMetric)
            .outerjoin(
                WafEndpointMetric,
                and_(
                    WafEndpointMetric.waf_endpoint_id == WafEndpoint.id,
                    WafEndpointMetric.bucket_start == facts.bucket_start,
                ),
            )
            .where(WafEndpoint.log_source_id == log_source.id)
            .order_by(WafEndpoint.id)
        )
        if facts.sparse:
            query = query.where(or_(WafEndpointMetric.waf_endpoint_id.is_not(None), tracked))
        else:
            query = query.where(WafEndpointMetric.waf_endpoint_id.is_not(None))
        subjects: list[_Subject] = []
        for endpoint, metric in (await self.session.execute(query)).all():
            observation = observe(
                RowFacts.of(metric) if metric is not None else None,
                facts,
                tracked=True,
                normalizer_version=endpoint.normalizer_version,
            )
            if observation is not None:
                subjects.append(_Subject(endpoint, observation))
        return subjects

    async def _load_cells(
        self, ids: Sequence[uuid.UUID], metrics: set[str], weekday: int, hour: int
    ) -> dict[tuple[uuid.UUID, str], WafEndpointBaseline]:
        out: dict[tuple[uuid.UUID, str], WafEndpointBaseline] = {}
        for chunk in _chunks(ids):
            rows = await self.session.scalars(
                select(WafEndpointBaseline).where(
                    WafEndpointBaseline.waf_endpoint_id.in_(chunk),
                    WafEndpointBaseline.metric_name.in_(metrics),
                    WafEndpointBaseline.weekday == weekday,
                    WafEndpointBaseline.hour == hour,
                    WafEndpointBaseline.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
                )
            )
            for row in rows:
                out[(row.waf_endpoint_id, row.metric_name)] = row
        return out

    async def _load_states(
        self, ids: Sequence[uuid.UUID], types: Iterable[WafAnomalyType]
    ) -> dict[tuple[uuid.UUID, str], WafDetectorState]:
        wanted = [t.value for t in types]
        out: dict[tuple[uuid.UUID, str], WafDetectorState] = {}
        for chunk in _chunks(ids):
            rows = await self.session.scalars(
                select(WafDetectorState).where(
                    WafDetectorState.waf_endpoint_id.in_(chunk),
                    WafDetectorState.detector_type.in_(wanted),
                )
            )
            for row in rows:
                out[(row.waf_endpoint_id, str(row.detector_type))] = row
        return out

    async def _ensure_states(
        self,
        ids: Sequence[uuid.UUID],
        types: Sequence[WafAnomalyType],
        states: dict[tuple[uuid.UUID, str], WafDetectorState],
    ) -> None:
        """Create every missing detector state in one statement, at any batch size."""
        missing = [
            (uuid.uuid4(), endpoint_id, detector_type.value)
            for endpoint_id in ids
            for detector_type in types
            if (endpoint_id, detector_type.value) not in states
        ]
        if not missing:
            return
        statement = text(
            """
            INSERT INTO waf_detector_state (
                id, waf_endpoint_id, detector_type, consecutive_anomalous,
                consecutive_healthy, state
            )
            SELECT pending.id, pending.waf_endpoint_id, pending.detector_type, 0, 0, 'NORMAL'
            FROM unnest(
                CAST(:state_ids AS uuid[]),
                CAST(:endpoint_ids AS uuid[]),
                CAST(:detector_types AS text[])
            ) AS pending(id, waf_endpoint_id, detector_type)
            ON CONFLICT ON CONSTRAINT uq_waf_detector_state DO NOTHING
            RETURNING waf_detector_state.*
            """
        )
        query = select(WafDetectorState).from_statement(statement)
        created = await self.session.scalars(
            query,
            {
                "state_ids": [item[0] for item in missing],
                "endpoint_ids": [item[1] for item in missing],
                "detector_types": [item[2] for item in missing],
            },
        )
        for state in created:
            states[(state.waf_endpoint_id, str(state.detector_type))] = state

    async def _load_anomalies(self, ids: set[uuid.UUID]) -> dict[uuid.UUID, WafAnomaly]:
        out: dict[uuid.UUID, WafAnomaly] = {}
        ordered = sorted(ids)
        for chunk in _chunks(ordered):
            for row in await self.session.scalars(
                select(WafAnomaly).where(WafAnomaly.id.in_(chunk))
            ):
                out[row.id] = row
        return out

    async def _load_novelty_traffic(
        self, subjects: Sequence[_Subject]
    ) -> dict[uuid.UUID, tuple[int, int, bool]]:
        """Post-appearance requests, buckets with traffic and lower-bound-ness per endpoint.

        One statement per id chunk. The counts are a lower bound when any source
        bucket from first appearance through the judged bucket counted only a lower
        bound, or was never collected: an uncollected bucket's traffic is unknown.
        """
        out: dict[uuid.UUID, tuple[int, int, bool]] = {}
        by_id = {subject.endpoint.id: subject for subject in subjects}
        statement = text(
            """
            SELECT
                pending.endpoint_id,
                COALESCE(traffic.requests, 0)::bigint,
                COALESCE(traffic.traffic_buckets, 0)::integer,
                COALESCE(recorded.bucket_count, 0)::integer,
                COALESCE(recorded.lower_bound, false)
            FROM unnest(
                CAST(:endpoint_ids AS uuid[]),
                CAST(:first_seen_ats AS timestamptz[]),
                CAST(:judged_ats AS timestamptz[])
            ) AS pending(endpoint_id, first_seen_at, judged_at)
            LEFT JOIN LATERAL (
                SELECT SUM(metric.request_count) AS requests,
                       COUNT(*) FILTER (WHERE metric.request_count > 0) AS traffic_buckets
                FROM waf_endpoint_metric AS metric
                WHERE metric.waf_endpoint_id = pending.endpoint_id
                  AND metric.bucket_start >= pending.first_seen_at
                  AND metric.bucket_start <= pending.judged_at
            ) AS traffic ON true
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS bucket_count,
                       BOOL_OR(source_bucket.request_count_measurement = 'LOWER_BOUND')
                           AS lower_bound
                FROM waf_source_bucket AS source_bucket
                WHERE source_bucket.log_source_id = :log_source_id
                  AND source_bucket.bucket_start >= pending.first_seen_at
                  AND source_bucket.bucket_start <= pending.judged_at
            ) AS recorded ON true
            """
        )
        for chunk in _chunks(sorted(by_id)):
            rows = await self.session.execute(
                statement,
                {
                    "endpoint_ids": list(chunk),
                    "first_seen_ats": [by_id[item].endpoint.first_seen_at for item in chunk],
                    "judged_ats": [by_id[item].observation.bucket_start for item in chunk],
                    "log_source_id": by_id[chunk[0]].endpoint.log_source_id,
                },
            )
            for endpoint_id, requests, traffic_buckets, recorded, lower_bound in rows:
                subject = by_id[endpoint_id]
                width = timedelta(seconds=subject.observation.bucket_seconds)
                expected = (
                    subject.observation.bucket_start - subject.endpoint.first_seen_at
                ) // width + 1
                out[endpoint_id] = (
                    int(requests),
                    int(traffic_buckets),
                    bool(lower_bound) or recorded < expected,
                )
        return out

    async def _load_novelty(
        self, subjects: Sequence[_Subject], thresholds: WafBehaviorThresholds
    ) -> dict[uuid.UUID, NoveltyFacts]:
        """Build novelty facts in bulk. Established endpoints cost no I/O at all."""
        history_delta = timedelta(days=thresholds.new_endpoint_history_days)
        result: dict[uuid.UUID, NoveltyFacts] = {}
        in_window: list[_Subject] = []
        window_ends: dict[uuid.UUID, datetime] = {}
        for subject in subjects:
            endpoint, obs = subject.endpoint, subject.observation
            window_end = endpoint.first_seen_at + timedelta(
                seconds=thresholds.new_endpoint_window_buckets * obs.bucket_seconds
            )
            window_ends[endpoint.id] = window_end
            if obs.bucket_start >= window_end:
                result[endpoint.id] = NoveltyFacts(
                    first_seen_at=endpoint.first_seen_at,
                    window_end=window_end,
                    within_window=False,
                    history=HistoryFacts(
                        complete=False, reason="history was not loaded for an established endpoint"
                    ),
                    history_start=endpoint.first_seen_at - history_delta,
                    history_end=endpoint.first_seen_at,
                    history_buckets=0,
                    appearance_flood=None,
                    requests_since_first_seen=0,
                    buckets_with_traffic=0,
                    lower_bound=False,
                )
            else:
                in_window.append(subject)
        if not in_window:
            return result

        earliest = min(item.endpoint.first_seen_at for item in in_window) - history_delta
        latest = max(item.endpoint.first_seen_at for item in in_window)
        rows = await self.session.scalars(
            select(WafSourceBucket)
            .where(
                WafSourceBucket.log_source_id == in_window[0].endpoint.log_source_id,
                WafSourceBucket.bucket_start >= earliest,
                WafSourceBucket.bucket_start <= latest,
            )
            .order_by(WafSourceBucket.bucket_start)
        )
        buckets = [BucketFacts.of(row) for row in rows]
        starts = [bucket.bucket_start for bucket in buckets]
        by_start = {bucket.bucket_start: bucket for bucket in buckets}
        traffic = await self._load_novelty_traffic(in_window)

        # Endpoints first seen in the same bucket under one normalizer share a history.
        verified: dict[tuple[datetime, int], tuple[HistoryFacts, int, str | None]] = {}
        for subject in in_window:
            endpoint = subject.endpoint
            first_seen = endpoint.first_seen_at
            key = (first_seen, endpoint.normalizer_version)
            if key not in verified:
                verified[key] = self._verify_history(
                    first_seen, endpoint.normalizer_version, history_delta,
                    buckets, starts, by_start,
                )
            history, history_buckets, flood = verified[key]
            requests, traffic_buckets, lower_bound = traffic[endpoint.id]
            result[endpoint.id] = NoveltyFacts(
                first_seen_at=first_seen,
                window_end=window_ends[endpoint.id],
                within_window=True,
                history=history,
                history_start=first_seen - history_delta,
                history_end=first_seen,
                history_buckets=history_buckets,
                appearance_flood=flood,
                requests_since_first_seen=requests,
                buckets_with_traffic=traffic_buckets,
                lower_bound=lower_bound,
            )
        return result

    @staticmethod
    def _verify_history(
        first_seen: datetime,
        normalizer_version: int,
        history_delta: timedelta,
        buckets: Sequence[BucketFacts],
        starts: Sequence[datetime],
        by_start: dict[datetime, BucketFacts],
    ) -> tuple[HistoryFacts, int, str | None]:
        """History facts, verified bucket count and appearance flood for one first sighting."""
        history_start = first_seen - history_delta
        history_rows = buckets[
            bisect.bisect_left(starts, history_start) : bisect.bisect_left(starts, first_seen)
        ]
        appearance = by_start.get(first_seen)
        if appearance is None:
            reason = "the first-appearance bucket was never collected"
        elif appearance.profile_version is None:
            reason = "the first-appearance bucket carries no profile version"
        elif semantics_normalizer_version(appearance.metric_semantics) != normalizer_version:
            reason = (
                "the first-appearance bucket is incompatible with endpoint normalizer "
                f"version {normalizer_version}"
            )
        else:
            history = verify_endpoint_history(
                history_rows,
                history_start=history_start,
                history_end=first_seen,
                bucket_seconds=appearance.bucket_seconds,
                profile_version=appearance.profile_version,
                normalizer_version=normalizer_version,
            )
            flood: str | None = None
            if appearance.groups_truncated:
                flood = "endpoint discovery was truncated in the first-appearance bucket"
            elif appearance.admission_truncated:
                flood = "endpoint admission was truncated in the first-appearance bucket"
            return history, len(history_rows), flood
        return HistoryFacts(complete=False, reason=reason), len(history_rows), None

    # --------------------------------------------------------------- evaluation
    async def _evaluate(
        self, log_source: LogSource | None, subjects: list[_Subject]
    ) -> list[WafEvaluationReport]:
        if not subjects:
            return []
        types = self._enabled_types()
        metrics = {m for t in types for m in WAF_DETECTORS[t].baseline_metrics}
        ids = [s.endpoint.id for s in subjects]
        by_hour: dict[tuple[int, int], list[uuid.UUID]] = {}
        for s in subjects:
            key = (s.observation.bucket_start.isoweekday(), s.observation.bucket_start.hour)
            by_hour.setdefault(key, []).append(s.endpoint.id)
        cells: dict[tuple[uuid.UUID, str], WafEndpointBaseline] = {}
        if metrics:
            for (weekday, hour), hour_ids in by_hour.items():
                cells.update(await self._load_cells(hour_ids, metrics, weekday, hour))
        states = await self._load_states(ids, types) if types else {}
        await self._ensure_states(ids, types, states)
        still_missing = [
            (endpoint_id, detector_type)
            for endpoint_id in ids
            for detector_type in types
            if (endpoint_id, detector_type.value) not in states
        ]
        if still_missing:
            states.update(
                await self._load_states(
                    sorted({item[0] for item in still_missing}),
                    sorted({item[1] for item in still_missing}, key=lambda item: item.value),
                )
            )
        for endpoint_id, detector_type in still_missing:
            if (endpoint_id, detector_type.value) not in states:
                raise RuntimeError(
                    f"detector state is missing for endpoint {endpoint_id} and "
                    f"detector {detector_type.value} after creation and reload"
                )
        anomalies = await self._load_anomalies(
            {
                aid
                for st in states.values()
                for aid in (st.active_anomaly_id, st.open_anomaly_id)
                if aid is not None
            }
        )

        thresholds = self._thresholds()
        behavior = self._behavior_thresholds()
        novelty = (
            await self._load_novelty(subjects, behavior)
            if WafAnomalyType.WAF_NEW_ENDPOINT_ACCESS in types
            else {}
        )
        reports: list[WafEvaluationReport] = []
        modified: list[WafDetectorState] = []
        alert_actions: list[_AlertAction] = []
        for subject in subjects:
            endpoint, obs = subject.endpoint, subject.observation
            report = WafEvaluationReport(endpoint.id, obs.bucket_start)
            # The shared maintenance rule, as decided when the bucket was
            # collected. Re-deciding against the current flag would let a later
            # change to the flag -- which carries no start time -- rewrite history
            # in both directions. Only a bucket with no record uses the rule now.
            if obs.in_maintenance is not None:
                report.in_maintenance = obs.in_maintenance
            else:
                report.in_maintenance = log_source is not None and in_maintenance(
                    obs.bucket_start,
                    maintenance_mode=log_source.maintenance_mode,
                    maintenance_until=log_source.maintenance_until,
                )
            baselines = {
                metric: self._cell(row)
                for metric in metrics
                if (row := cells.get((endpoint.id, metric))) is not None
            }
            for detector_type in types:
                spec = WAF_DETECTORS[detector_type]
                metric_name = next(iter(sorted(spec.baseline_metrics)), None)
                row = cells.get((endpoint.id, metric_name)) if metric_name is not None else None
                verdict = self.detectors[detector_type](
                    self._context(
                        obs, row, detector_type, thresholds, behavior, baselines,
                        endpoint=endpoint,
                        novelty=novelty.get(endpoint.id),
                    )
                )
                if verdict.is_anomalous:
                    report.anomalous.append(verdict.detector_type.value)
                state = states.get((endpoint.id, detector_type.value))
                if state is None:
                    raise RuntimeError(
                        f"detector state is missing for endpoint {endpoint.id} and "
                        f"detector {detector_type.value}"
                    )
                if self._apply_hysteresis(
                    endpoint, obs, detector_type, verdict.evidence, state, anomalies,
                    report, thresholds, alert_actions,
                ):
                    modified.append(state)
            reports.append(report)
        self._uniform_updates(modified, anomalies.values())
        if alert_actions:
            await self._apply_alert_actions(log_source, alert_actions)
        else:
            await self.session.flush()
        return reports

    def _uniform_updates(
        self, states: Iterable[WafDetectorState], anomalies: Iterable[WafAnomaly]
    ) -> None:
        """Make every modified row update the same columns, so each table is one batch."""
        persistent_states = [s for s in states if s not in self.session.new]
        for state in persistent_states:
            for column in _STATE_COLUMNS:
                flag_modified(state, column)
        dirty = self.session.dirty
        for anomaly in anomalies:
            if anomaly in dirty:
                for column in _ANOMALY_COLUMNS:
                    flag_modified(anomaly, column)

    def _context(
        self,
        obs: EndpointObservation,
        row: WafEndpointBaseline | None,
        detector_type: WafAnomalyType,
        thresholds: WafThresholds,
        behavior: WafBehaviorThresholds,
        baselines: dict[str, WafBaselineCell],
        *,
        endpoint: WafEndpoint,
        novelty: NoveltyFacts | None,
    ) -> WafDetectionContext:
        spec = WAF_DETECTORS[detector_type]
        bucket_semantics = obs.metric_semantics
        if spec.breakdowns == frozenset({WafBreakdown.ACTION}):
            bucket_semantics = obs.breakdowns.action_semantics
        elif spec.breakdowns == frozenset({WafBreakdown.RESPONSE_CODE}):
            bucket_semantics = obs.breakdowns.response_code_semantics
        elif spec.breakdowns == frozenset({WafBreakdown.SOURCE_IP}):
            bucket_semantics = obs.breakdowns.client_semantics
        incompatibility: str | None = None
        if bucket_semantics is None:
            incompatibility = semantics_incompatibility(None, None)
        elif row is not None:
            incompatibility = semantics_incompatibility(bucket_semantics, row.metric_semantics)
        if detector_type is WafAnomalyType.WAF_REQUEST_RATE_SPIKE or not spec.inputs:
            scope = waf_verdict_scope(detector_type, obs.measurement)
        else:
            scope = waf_input_scope(
                detector_type,
                obs,
                novelty.history if novelty is not None else None,
            )
        return WafDetectionContext(
            point=WafPoint(
                bucket_start=obs.bucket_start,
                bucket_end=obs.bucket_end,
                completeness=obs.completeness,
                request_count=obs.request_count,
                endpoint_coverage_ratio=obs.endpoint_coverage_ratio,
                distinct_client_count=obs.distinct_client_count,
                measurement=obs.measurement,
            ),
            baseline=self._cell(row),
            thresholds=thresholds,
            mad_floor_ratio=self.settings.baseline_mad_floor_ratio,
            min_coverage=self.settings.waf_min_endpoint_coverage,
            extra={
                "normalizer_version": obs.normalizer_version,
                "metric_semantics": obs.metric_semantics,
                "entity": {
                    "virtual_host": endpoint.virtual_host,
                    "normalized_endpoint": endpoint.normalized_endpoint,
                    "http_method": endpoint.http_method,
                },
            },
            baseline_incompatibility=incompatibility,
            verdict_scope=scope.scope,
            scope_reason=scope.reason,
            observation=obs,
            baselines=baselines,
            behavior=behavior,
            novelty=novelty,
        )

    @overload
    @staticmethod
    def _cell(row: WafEndpointBaseline) -> WafBaselineCell: ...

    @overload
    @staticmethod
    def _cell(row: None) -> None: ...

    @staticmethod
    def _cell(row: WafEndpointBaseline | None) -> WafBaselineCell | None:
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
            numerator_sum=row.numerator_sum,
            denominator_sum=row.denominator_sum,
            metric_semantics=row.metric_semantics,
        )

    def _apply_hysteresis(
        self,
        endpoint: WafEndpoint,
        obs: EndpointObservation,
        detector_type: WafAnomalyType,
        evidence: AnomalyEvidence,
        state: WafDetectorState,
        anomalies: dict[uuid.UUID, WafAnomaly],
        report: WafEvaluationReport,
        thr: WafThresholds,
        alert_actions: list[_AlertAction],
    ) -> bool:
        """Fold one verdict into the state. False when the bucket was already folded."""
        # Idempotency: this bucket already folded into the counters.
        if state.last_interval_start is not None and state.last_interval_start >= obs.bucket_start:
            return False

        signal = evidence.signal
        # Maintenance, exactly as Phase A: an anomalous verdict is demoted to
        # UNKNOWN, so it neither advances confirmation nor opens an incident,
        # while a healthy one still lets an existing incident recover.
        if report.in_maintenance and signal is DetectorSignal.ANOMALOUS:
            signal = DetectorSignal.UNKNOWN

        state.consecutive_anomalous, state.consecutive_healthy = advance_counters(
            signal, state.consecutive_anomalous, state.consecutive_healthy
        )
        state.last_interval_start = obs.bucket_start

        decision = next_state(
            LifecycleInput(
                state=AnomalyState(state.state),
                signal=signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=thr.open_after,
                resolve_after=thr.resolve_after,
                insufficient_data=evidence.insufficient_data,
                suppressed=(
                    report.in_maintenance
                    and AnomalyState(state.state) not in ACTIVE_ANOMALY_STATES
                ),
            )
        )
        self._apply_transition(
            endpoint,
            obs,
            detector_type,
            evidence,
            state,
            decision,
            anomalies,
            report,
            alert_actions,
        )
        return True

    def _apply_transition(
        self,
        endpoint: WafEndpoint,
        obs: EndpointObservation,
        detector_type: WafAnomalyType,
        evidence: AnomalyEvidence,
        state: WafDetectorState,
        decision: LifecycleDecision,
        anomalies: dict[uuid.UUID, WafAnomaly],
        report: WafEvaluationReport,
        alert_actions: list[_AlertAction],
    ) -> None:
        previous = AnomalyState(state.state)
        target = decision.state
        report.states[detector_type.value] = target.value

        if target is previous:
            if previous is AnomalyState.OPEN:
                self._touch(state, obs, evidence, anomalies)
                if (
                    self.settings.waf_alerting_enabled
                    and evidence.is_anomalous
                    and not report.in_maintenance
                ):
                    alert_actions.append(
                        _AlertOpenOrUpdate(
                            endpoint, detector_type, evidence, state.open_anomaly_id
                        )
                    )
            return

        state.state = target

        if target is AnomalyState.CANDIDATE:
            created = self._create_incident(endpoint, obs, detector_type, evidence, anomalies)
            state.active_anomaly_id = created.id
            self._record_transition(created, previous, target, obs, decision.reason, evidence)
            report.candidates.append(detector_type.value)
            return

        anomaly = self._active(state, anomalies)

        if target is AnomalyState.OPEN:
            # A confirmation threshold of 1 means the incident opens on the same
            # bucket that created it, so there may be nothing to promote yet.
            incident = anomaly or self._create_incident(
                endpoint, obs, detector_type, evidence, anomalies
            )
            incident.state = AnomalyState.OPEN
            incident.opened_at = self._clock()
            incident.anomaly_end = None
            incident.severity = evidence.severity
            state.active_anomaly_id = incident.id
            state.open_anomaly_id = incident.id
            self._touch(state, obs, evidence, anomalies)
            self._record_transition(incident, previous, target, obs, decision.reason, evidence)
            report.opened.append(detector_type.value)
            if self.settings.waf_alerting_enabled and not report.in_maintenance:
                alert_actions.append(
                    _AlertOpenOrUpdate(endpoint, detector_type, evidence, incident.id)
                )
            return

        if anomaly is not None:
            self._record_transition(anomaly, previous, target, obs, decision.reason, evidence)

        if target is AnomalyState.RECOVERING:
            if anomaly is not None:
                anomaly.state = AnomalyState.RECOVERING
                # The abnormal telemetry ended with the last abnormal bucket,
                # not with the normal one that revealed the recovery.
                anomaly.anomaly_end = obs.bucket_start
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
            if self.settings.waf_alerting_enabled:
                alert_actions.append(_AlertResolve(endpoint, detector_type))
            return

        if target is AnomalyState.SUPPRESSED:
            if anomaly is not None:
                anomaly.state = AnomalyState.SUPPRESSED
                anomaly.suppressed = True
                anomaly.suppression_reason = anomaly.suppression_reason or "maintenance window"
            return

        # NORMAL or INSUFFICIENT_DATA: no incident is live. A CANDIDATE that
        # never opened is closed out as a non-incident rather than left dangling.
        if anomaly is not None and previous is AnomalyState.CANDIDATE:
            anomaly.state = target
            anomaly.anomaly_end = obs.bucket_start
        state.active_anomaly_id = None
        state.open_anomaly_id = None

    async def _apply_alert_actions(
        self, log_source: LogSource | None, actions: Sequence[_AlertAction]
    ) -> None:
        """Apply the synchronous fold's alert side effects in one async batch."""
        if log_source is None:
            await self.session.flush()
            return

        opens = [item for item in actions if isinstance(item, _AlertOpenOrUpdate)]
        instance_name: str | None = None
        source_class: str | None = None
        if opens:
            # One round trip for everything the alert body needs beyond the log
            # source itself: the instance name and the Phase C class that class
            # routing matches on. The class row is optional, hence the outer
            # join -- a source with no classification simply carries none.
            row = (
                await self.session.execute(
                    select(QRadarInstance.name, LogSourceClassification.source_class)
                    .outerjoin(
                        LogSourceClassification,
                        LogSourceClassification.log_source_id == log_source.id,
                    )
                    .where(QRadarInstance.id == log_source.instance_id)
                )
            ).one_or_none()
            if row is not None:
                instance_name, source_class = row

        open_inputs = [
            self._alert_input(log_source, instance_name, item, source_class)
            for item in opens
        ]
        resolves = [
            AlertResolution(
                fingerprint=self._fingerprint(item.endpoint, item.detector_type),
                actor="waf-anomaly-engine",
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
        log_source: LogSource,
        instance_name: str | None,
        action: _AlertOpenOrUpdate,
        source_class: str | None = None,
    ) -> AlertInput:
        endpoint = action.endpoint
        detector_type = action.detector_type
        evidence = action.evidence
        context = {
            "log_source": log_source.name,
            "owner": log_source.owner,
            "criticality": str(log_source.criticality),
            "instance": instance_name,
            "virtual_host": endpoint.virtual_host,
            "endpoint": endpoint.normalized_endpoint,
            "method": endpoint.http_method,
            "detector_type": detector_type.value,
        }
        # Added only when known, and never part of the fingerprint: a route
        # scoped to a class matches, and an unclassified source cannot slip
        # into it.
        if source_class is not None:
            context["source_class"] = str(source_class)
        return AlertInput(
            fingerprint=self._fingerprint(endpoint, detector_type),
            title=(
                f"{detector_type.value} on {log_source.name}: "
                f"{endpoint.http_method} {endpoint.normalized_endpoint}"
            ),
            severity=evidence.severity,
            source_type="waf_endpoint",
            source_id=endpoint.id,
            description=evidence.reason,
            evidence=evidence.to_dict(),
            source_anomaly_ids=(
                [str(action.anomaly_id)] if action.anomaly_id is not None else []
            ),
            context=context,
        )

    @staticmethod
    def _fingerprint(endpoint: WafEndpoint, detector_type: WafAnomalyType) -> str:
        return waf_anomaly_fingerprint(
            instance_id=endpoint.instance_id,
            log_source_id=endpoint.log_source_id,
            virtual_host=endpoint.virtual_host,
            normalized_endpoint=endpoint.normalized_endpoint,
            http_method=endpoint.http_method,
            normalizer_version=endpoint.normalizer_version,
            detector_type=detector_type.value,
        )

    def _create_incident(
        self,
        endpoint: WafEndpoint,
        obs: EndpointObservation,
        detector_type: WafAnomalyType,
        evidence: AnomalyEvidence,
        anomalies: dict[uuid.UUID, WafAnomaly],
    ) -> WafAnomaly:
        facts = evidence.extra or {}
        anomaly = WafAnomaly(
            id=uuid.uuid4(),
            waf_endpoint_id=endpoint.id,
            detector_type=detector_type,
            severity=evidence.severity,
            state=AnomalyState.CANDIDATE,
            detected_at=obs.bucket_start,
            anomaly_start=obs.bucket_start,
            observed_value=evidence.observed_value,
            observed_measurement=_measurement(evidence),
            expected_value=evidence.expected_value,
            deviation_score=evidence.deviation_score,
            confidence=evidence.confidence,
            deviation_ratio=_as_float(facts.get("ratio")),
            robust_z=_as_float(facts.get("robust_z")),
            absolute_delta=_absolute_delta(facts),
            consecutive_buckets=1,
            baseline_version=_baseline_version(facts),
            policy_version=self.settings.anomaly_policy_version,
            normalizer_version=obs.normalizer_version,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            explanation=evidence.reason,
            details=evidence.to_dict(),
            suppressed=False,
        )
        self.session.add(anomaly)
        anomalies[anomaly.id] = anomaly
        return anomaly

    def _touch(
        self,
        state: WafDetectorState,
        obs: EndpointObservation,
        evidence: AnomalyEvidence,
        anomalies: dict[uuid.UUID, WafAnomaly],
    ) -> None:
        """Refresh the live incident from the current bucket.

        Tracks the *worst* reading, not the latest: an operator asking "how bad
        did this get?" must not be shown the tail of a recovery. A lower bound
        replaces the reading only when it is larger -- and then the incident
        says the reading is a lower bound.
        """
        anomaly = self._active(state, anomalies)
        if anomaly is None:
            return
        anomaly.consecutive_buckets = max(anomaly.consecutive_buckets, state.consecutive_anomalous)
        if not evidence.is_anomalous:
            return
        anomaly.anomaly_end = obs.bucket_start + timedelta(seconds=obs.bucket_seconds)
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
            anomaly.absolute_delta = _absolute_delta(facts)
            anomaly.explanation = evidence.reason
            anomaly.details = evidence.to_dict()

    @staticmethod
    def _active(
        state: WafDetectorState, anomalies: dict[uuid.UUID, WafAnomaly]
    ) -> WafAnomaly | None:
        if state.active_anomaly_id is None:
            return None
        return anomalies.get(state.active_anomaly_id)

    def _record_transition(
        self,
        anomaly: WafAnomaly,
        from_state: AnomalyState,
        to_state: AnomalyState,
        obs: EndpointObservation,
        reason: str,
        evidence: AnomalyEvidence,
    ) -> None:
        self.session.add(
            WafAnomalyTransition(
                id=uuid.uuid4(),
                anomaly_id=anomaly.id,
                from_state=from_state,
                to_state=to_state,
                occurred_at=self._clock(),
                bucket_start=obs.bucket_start,
                reason=reason,
                actor="waf-anomaly-engine",
                observed_value=evidence.observed_value,
                observed_measurement=_measurement(evidence),
                expected_value=evidence.expected_value,
            )
        )


def _measurement(evidence: AnomalyEvidence) -> WafMeasurement:
    facts = evidence.extra or {}
    value = facts.get("observed_measurement", facts.get("request_count_measurement"))
    return WafMeasurement.LOWER_BOUND if value == WafMeasurement.LOWER_BOUND else (
        WafMeasurement.EXACT
    )


def _absolute_delta(facts: dict) -> float | None:
    return _as_float(facts.get("absolute_delta_requests", facts.get("absolute_delta")))


def _baseline_version(facts: dict) -> int | None:
    expected = facts.get("expected")
    nested = expected.get("baseline_version") if isinstance(expected, dict) else None
    return _as_int(facts.get("baseline_version", nested))


def _as_float(value: object) -> float | None:
    return float(value) if isinstance(value, int | float) else None


def _as_int(value: object) -> int | None:
    return int(value) if isinstance(value, int | float) else None

