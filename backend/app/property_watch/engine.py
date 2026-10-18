"""Bulk property-watch evaluation and anomaly lifecycle persistence.

All watches of one instance are evaluated together.  The repository loads each
dependency once, pure detectors judge plain facts in memory, and the result is
written once per lifecycle table.  No query is issued per watch, value or tuple.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fingerprint import compute_fingerprint
from app.anomaly.evidence import AnomalyEvidence
from app.anomaly.lifecycle import LifecycleInput, advance_counters, next_state
from app.core.config import Settings, get_settings
from app.models.enums import (
    AnomalyState,
    EvidenceStatus,
    PwAnomalyType,
    PwBucketMeasurement,
    PwPinnedMeasurement,
    PwWatchStatus,
    WafMeasurement,
)
from app.models.instance import QRadarInstance
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwBucket,
    PwDetectorState,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.property_watch.baseline import PwBaselineCell
from app.property_watch.detectors import (
    PW_DETECTOR_FUNCTIONS,
    PwComponentFact,
    PwDetectionContext,
    PwNoveltyFacts,
    PwReadingPoint,
    PwThresholds,
)
from app.property_watch.facts import PwBucketFact, PwReading, PwReadingKind, reading_for
from app.property_watch.history import verify_value_history
from app.repositories.property_watch import PropertyWatchRepository
from app.waf.inputs import HistoryFacts


@dataclass
class PwEvaluationReport:
    instance_id: uuid.UUID
    buckets_evaluated: int = 0
    values_evaluated: int = 0
    opened: int = 0
    resolved: int = 0
    version_resolved: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "buckets_evaluated": self.buckets_evaluated,
            "values_evaluated": self.values_evaluated,
            "opened": self.opened,
            "resolved": self.resolved,
            "version_resolved": self.version_resolved,
        }


@dataclass
class _State:
    id: uuid.UUID
    watch_id: uuid.UUID
    value_id: uuid.UUID
    detector: PwAnomalyType
    value_version: int
    consecutive_anomalous: int = 0
    consecutive_healthy: int = 0
    state: AnomalyState = AnomalyState.INSUFFICIENT_DATA
    last_interval_start: datetime | None = None
    active_anomaly_id: uuid.UUID | None = None
    open_anomaly_id: uuid.UUID | None = None
    dirty: bool = False


@dataclass
class _Incident:
    row: dict[str, object]
    dirty: bool = False

    @property
    def id(self) -> uuid.UUID:
        value = self.row["id"]
        assert isinstance(value, uuid.UUID)
        return value


@dataclass(frozen=True)
class _History:
    """Novelty facts that depend only on a value's (watch, version, first appearance)."""

    facts: HistoryFacts
    start: datetime
    buckets: int
    appearance: PwBucketFact
    first_collected: datetime | None
    membership_changed: bool


@dataclass
class _Writes:
    transitions: list[dict[str, object]] = field(default_factory=list)
    watches: dict[uuid.UUID, dict[str, object]] = field(default_factory=dict)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _bucket_fact(row: PwBucket) -> PwBucketFact:
    return PwBucketFact(
        bucket_start=_aware(row.bucket_start) or row.bucket_start,
        bucket_seconds=row.bucket_seconds,
        watch_version=row.watch_version,
        totals_complete=row.totals_complete,
        values_measurement=PwBucketMeasurement(str(row.values_measurement)),
        pinned_measurement=PwPinnedMeasurement(str(row.pinned_measurement)),
        total_events=row.total_events,
        parsed_events=row.parsed_events,
        untracked_events=row.untracked_events,
        observed_values=row.observed_values,
        tracked_values_observed=row.tracked_values_observed,
        admitted_values=row.admitted_values,
        below_admission_threshold=row.below_admission_threshold,
        rejected_over_quota=row.rejected_over_quota,
        admission_truncated=row.admission_truncated,
        scope_member_count=row.scope_member_count,
        scope_fingerprint=row.scope_fingerprint,
        scope_changed=row.scope_changed,
        in_maintenance=row.in_maintenance,
    )


def _tracked_at(value: PwValue, at: datetime) -> bool:
    start, end = _aware(value.tracked_since), _aware(value.tracked_until)
    return start is not None and start <= at and (end is None or at <= end)


def _fingerprint(instance_id: uuid.UUID, watch_id: uuid.UUID, value_key: str, detector: str) -> str:
    natural_key = json.dumps(
        [str(instance_id), str(watch_id), value_key], separators=(",", ":")
    )
    return compute_fingerprint(
        source_type="pw_value",
        source_id=natural_key,
        condition="pw_anomaly",
        subtype=detector,
    )


_DETECTOR_OWNED = frozenset({PwWatchStatus.OK, PwWatchStatus.OPEN, PwWatchStatus.RECOVERING})
_NOVELTY = frozenset({PwAnomalyType.PW_NEW_VALUE, PwAnomalyType.PW_NEW_COMBINATION})


class PwAnomalyEngine:
    """Evaluate current-version values and reconcile old-version incidents."""

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
        self.repo = PropertyWatchRepository(session)

    def _thresholds(self) -> PwThresholds:
        s = self.settings
        return PwThresholds(
            deviation_z=s.anomaly_deviation_threshold,
            mad_floor_ratio=s.baseline_mad_floor_ratio,
            min_coverage=s.pw_min_coverage,
            baseline_min_samples=s.baseline_min_samples,
            spike_ratio=s.pw_spike_ratio,
            min_absolute_delta_events=s.pw_min_absolute_delta_events,
            min_bucket_events=s.pw_min_bucket_events,
            drop_min_median=s.pw_drop_min_median,
            drop_ratio=s.pw_drop_ratio,
            disappear_min_median=s.pw_disappear_min_median,
            disappear_buckets=s.pw_disappear_buckets,
            value_active_days=s.pw_value_active_days,
            new_value_history_days=s.pw_new_value_history_days,
            new_value_window_buckets=s.pw_new_value_window_buckets,
            new_value_min_events=s.pw_new_value_min_events,
            new_value_min_buckets=s.pw_new_value_min_buckets,
            admission_min_events=s.pw_min_admission_events,
        )

    async def evaluate(self, instance: QRadarInstance) -> PwEvaluationReport:
        report = PwEvaluationReport(instance.id)
        watches = await self.repo.enabled_watches(instance.id)
        if not watches:
            return report
        by_watch = {watch.id: watch for watch in watches}
        pending = await self.repo.pending_buckets(
            instance.id, limit=self.settings.pw_max_evaluation_buckets_per_run
        )
        versions = [(watch.id, watch.watch_version) for watch in watches]
        values = await self.repo.values(versions)
        current_values = [
            value
            for value in values
            if value.watch_id in by_watch
            and value.watch_version == by_watch[value.watch_id].watch_version
        ]
        state_rows = await self.repo.detector_states(list(by_watch))
        states = self._states(state_rows)
        self._ensure_states(watches, current_values, states)
        anomaly_ids = {
            anomaly_id
            for state in states.values()
            for anomaly_id in (state.active_anomaly_id, state.open_anomaly_id)
            if anomaly_id is not None
        }
        incidents = {
            row.id: _Incident(self._incident_row(row))
            for row in await self.repo.anomalies(anomaly_ids)
        }
        writes = _Writes()
        self._resolve_old_versions(watches, states, incidents, writes, report)

        if pending:
            pending_facts = [(watch, _bucket_fact(bucket)) for watch, bucket, _ in pending]
            first_collected = {
                (watch.id, watch.watch_version): _aware(first) or first
                for watch, _, first in pending
                if first is not None
            }
            earliest_by_watch: dict[uuid.UUID, datetime] = {}
            latest = pending_facts[0][1].bucket_start
            for watch, fact in pending_facts:
                earliest_by_watch[watch.id] = min(
                    fact.bucket_start, earliest_by_watch.get(watch.id, fact.bucket_start)
                )
                latest = max(latest, fact.bucket_start)
            candidates = self._novelty_candidates(by_watch, current_values, earliest_by_watch)
            interval = timedelta(seconds=self.settings.collection_interval_seconds)
            recent_start = min(earliest_by_watch.values()) - interval * (
                self.settings.pw_disappear_buckets
            )
            metric_start = min(
                [recent_start, *(_aware(v.first_seen_at) or recent_start for v in candidates)]
            )
            bucket_start = min(
                [
                    metric_start,
                    *(
                        (_aware(v.first_seen_at) or recent_start)
                        - timedelta(days=self.settings.pw_new_value_history_days)
                        for v in candidates
                    ),
                ]
            )
            history_rows = await self.repo.buckets_between(
                list(by_watch), start=bucket_start, end=latest
            )
            metrics = await self.repo.metrics_between(
                [value.id for value in current_values], start=metric_start, end=latest
            )
            cells = await self.repo.baseline_cells(
                [value.id for value in current_values],
                [
                    (fact.bucket_start.isoweekday(), fact.bucket_start.hour)
                    for _, fact in pending_facts
                ],
            )
            component_rows = await self._component_rows(candidates)
            self._evaluate_pending(
                instance,
                pending_facts,
                current_values,
                history_rows,
                metrics,
                cells,
                component_rows,
                {value.id for value in candidates},
                first_collected,
                states,
                incidents,
                writes,
                report,
            )
        await self._persist(states, incidents, writes)
        return report

    def _novelty_candidates(
        self,
        watches: Mapping[uuid.UUID, PropertyWatch],
        values: Sequence[PwValue],
        earliest_by_watch: Mapping[uuid.UUID, datetime],
    ) -> list[PwValue]:
        """Values whose first appearance still lies in the novelty window of a pending bucket.

        Only these need the long history; every other value is established and is judged by the
        baseline detectors alone. A watch that selected no novelty detector has none.
        """
        window = timedelta(
            seconds=self.settings.pw_new_value_window_buckets
            * self.settings.collection_interval_seconds
        )
        out = []
        for value in values:
            first = _aware(value.first_seen_at)
            earliest = earliest_by_watch.get(value.watch_id)
            if first is None or earliest is None or earliest >= first + window:
                continue
            if not set(self._selected(watches[value.watch_id])) & _NOVELTY:
                continue
            out.append(value)
        return out

    async def _component_rows(
        self, candidates: Sequence[PwValue]
    ) -> list[tuple[uuid.UUID, int, str, int, str, datetime]]:
        """The conditional component lookup: one statement, only when a tuple is in its window."""
        tuples = [value for value in candidates if value.component_1 is not None]
        if not tuples:
            return []
        components = {
            component
            for value in tuples
            for component in (value.component_0, value.component_1)
            if component is not None
        }
        start = min(
            (_aware(value.first_seen_at) or datetime.max.replace(tzinfo=UTC))
            - timedelta(days=self.settings.pw_new_value_history_days)
            for value in tuples
        )
        end = max(_aware(value.first_seen_at) or start for value in tuples)
        versions = sorted({(value.watch_id, value.watch_version) for value in tuples})
        return await self.repo.component_occurrences(versions, components, start=start, end=end)

    @staticmethod
    def _states(
        rows: Sequence[tuple[PwDetectorState, PwValue]],
    ) -> dict[tuple[uuid.UUID, PwAnomalyType], _State]:
        return {
            (state.pw_value_id, PwAnomalyType(str(state.detector_type))): _State(
                id=state.id,
                watch_id=state.watch_id,
                value_id=state.pw_value_id,
                detector=PwAnomalyType(str(state.detector_type)),
                value_version=value.watch_version,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                state=AnomalyState(str(state.state)),
                last_interval_start=_aware(state.last_interval_start),
                active_anomaly_id=state.active_anomaly_id,
                open_anomaly_id=state.open_anomaly_id,
            )
            for state, value in rows
        }

    @staticmethod
    def _selected(watch: PropertyWatch) -> tuple[PwAnomalyType, ...]:
        selected = []
        for raw in watch.detectors:
            try:
                detector = PwAnomalyType(str(raw))
            except ValueError:
                continue
            if detector is PwAnomalyType.PW_NEW_COMBINATION and len(watch.properties) != 2:
                continue
            if detector in PW_DETECTOR_FUNCTIONS:
                selected.append(detector)
        return tuple(selected)

    def _ensure_states(
        self,
        watches: Sequence[PropertyWatch],
        values: Sequence[PwValue],
        states: dict[tuple[uuid.UUID, PwAnomalyType], _State],
    ) -> None:
        watch_by_id = {watch.id: watch for watch in watches}
        for value in values:
            watch = watch_by_id[value.watch_id]
            for detector in self._selected(watch):
                key = (value.id, detector)
                if key not in states:
                    states[key] = _State(
                        id=uuid.uuid4(),
                        watch_id=watch.id,
                        value_id=value.id,
                        detector=detector,
                        value_version=value.watch_version,
                        dirty=True,
                    )

    def _resolve_old_versions(
        self,
        watches: Sequence[PropertyWatch],
        states: Mapping[tuple[uuid.UUID, PwAnomalyType], _State],
        incidents: Mapping[uuid.UUID, _Incident],
        writes: _Writes,
        report: PwEvaluationReport,
    ) -> None:
        versions = {watch.id: watch.watch_version for watch in watches}
        for state in states.values():
            if state.value_version == versions.get(state.watch_id):
                continue
            incident = incidents.get(state.active_anomaly_id) if state.active_anomaly_id else None
            if incident is not None:
                previous = state.state
                incident.row.update(
                    state=AnomalyState.RESOLVED.value,
                    resolved_at=self._clock(),
                    anomaly_end=state.last_interval_start,
                )
                incident.dirty = True
                writes.transitions.append(
                    self._transition(
                        incident.id,
                        previous,
                        AnomalyState.RESOLVED,
                        state.last_interval_start,
                        "watch definition changed",
                    )
                )
                report.resolved += 1
                report.version_resolved += 1
            if (
                state.state is not AnomalyState.RESOLVED
                or state.active_anomaly_id is not None
                or state.open_anomaly_id is not None
            ):
                state.dirty = True
            state.state = AnomalyState.RESOLVED
            state.consecutive_anomalous = 0
            state.consecutive_healthy = 0
            state.active_anomaly_id = None
            state.open_anomaly_id = None

    def _evaluate_pending(
        self,
        instance: QRadarInstance,
        pending: Sequence[tuple[PropertyWatch, PwBucketFact]],
        values: Sequence[PwValue],
        buckets: Sequence[PwBucket],
        metrics: Sequence[PwValueMetric],
        cells: Sequence[PwValueBaseline],
        component_rows: Sequence[tuple[uuid.UUID, int, str, int, str, datetime]],
        candidate_ids: set[uuid.UUID],
        first_collected: Mapping[tuple[uuid.UUID, int], datetime],
        states: dict[tuple[uuid.UUID, PwAnomalyType], _State],
        incidents: dict[uuid.UUID, _Incident],
        writes: _Writes,
        report: PwEvaluationReport,
    ) -> None:
        thresholds = self._thresholds()
        values_by_watch: dict[uuid.UUID, list[PwValue]] = {}
        for value in values:
            values_by_watch.setdefault(value.watch_id, []).append(value)
        buckets_by_watch: dict[uuid.UUID, dict[datetime, PwBucketFact]] = {}
        for row in buckets:
            fact = _bucket_fact(row)
            buckets_by_watch.setdefault(row.watch_id, {})[fact.bucket_start] = fact
        metric_map = {
            (row.pw_value_id, _aware(row.bucket_start) or row.bucket_start): row.events
            for row in metrics
        }
        cell_map = {
            (row.pw_value_id, row.weekday, row.hour): self._cell(row) for row in cells
        }
        component_first = self._component_first(component_rows)
        history_cache: dict[tuple[uuid.UUID, int, datetime], _History] = {}

        evaluated: dict[uuid.UUID, datetime] = {}
        for watch, bucket in pending:
            watch_values = values_by_watch.get(watch.id, ())
            history = buckets_by_watch.get(watch.id, {})
            status = self._detection_status(watch)
            selected = self._selected(watch)
            for value in watch_values:
                reading = self._reading(value, bucket, metric_map)
                if reading.kind is PwReadingKind.NOT_AN_OBSERVATION:
                    # The value was not tracked at this bucket: nothing was observed, so
                    # nothing is judged and no state moves (design §G.8.0 reading table).
                    continue
                recent = self._recent(value, bucket, history, metric_map, thresholds)
                novelty = (
                    self._novelty(
                        watch,
                        value,
                        bucket,
                        history,
                        metric_map,
                        component_first,
                        first_collected,
                        history_cache,
                        thresholds,
                    )
                    if value.id in candidate_ids
                    else self._established(watch, value, bucket)
                )
                extra = {
                    "watch_id": str(watch.id),
                    "value_id": str(value.id),
                    "observed_measurement": (
                        reading.measurement.value if reading.measurement is not None else None
                    ),
                }
                baseline = cell_map.get(
                    (value.id, bucket.bucket_start.isoweekday(), bucket.bucket_start.hour)
                )
                for detector in selected:
                    evidence = PW_DETECTOR_FUNCTIONS[detector](
                        PwDetectionContext(
                            watch_status=status,
                            bucket=bucket,
                            reading=reading,
                            baseline=baseline,
                            thresholds=thresholds,
                            last_seen_at=_aware(value.last_seen_at),
                            recent=recent,
                            novelty=novelty,
                            extra=extra,
                        )
                    ).evidence
                    self._advance(
                        instance,
                        watch,
                        value,
                        bucket,
                        states[(value.id, detector)],
                        evidence,
                        incidents,
                        writes,
                        report,
                    )
                report.values_evaluated += 1
            evaluated[watch.id] = bucket.bucket_start
            report.buckets_evaluated += 1

        watches = {watch.id: watch for watch, _ in pending}
        for watch_id, at in evaluated.items():
            watch = watches[watch_id]
            current = [
                state
                for state in states.values()
                if state.watch_id == watch_id and state.value_version == watch.watch_version
            ]
            current_status = self._detection_status(watch)
            if current_status not in _DETECTOR_OWNED and watch.last_status is not None:
                # A collection status (stale scope, not applicable, ...) is not the detector's
                # to overwrite; only the evaluation watermark moves.
                status_value, reason = str(watch.last_status), watch.last_status_reason
            elif any(state.state is AnomalyState.OPEN for state in current):
                status_value, reason = (
                    PwWatchStatus.OPEN.value,
                    "one or more value incidents are open",
                )
            elif any(state.state is AnomalyState.RECOVERING for state in current):
                status_value, reason = (
                    PwWatchStatus.RECOVERING.value,
                    "one or more value incidents are recovering",
                )
            else:
                status_value, reason = (
                    PwWatchStatus.OK.value,
                    "the latest collected bucket was evaluated",
                )
            writes.watches[watch_id] = {
                "watch_id": watch_id,
                "evaluated_through": at,
                "last_status": status_value,
                "last_status_reason": reason,
            }

    @staticmethod
    def _detection_status(watch: PropertyWatch) -> PwWatchStatus:
        """The watch status the detectors see.

        A successful collection clears its own statuses to NULL, so NULL here means the
        buckets being judged were collected whole: OK.  Any blocking status stays blocking.
        """
        if watch.last_status is None:
            return PwWatchStatus.OK
        try:
            return PwWatchStatus(str(watch.last_status))
        except ValueError:
            return PwWatchStatus.STALE

    @staticmethod
    def _cell(row: PwValueBaseline) -> PwBaselineCell:
        return PwBaselineCell(
            metric_name=row.metric_name,
            weekday=row.weekday,
            hour=row.hour,
            median=row.median,
            mad=row.mad,
            p05=row.p05 if row.p05 is not None else row.median,
            p95=row.p95 if row.p95 is not None else row.median,
            sample_count=row.sample_count,
            is_reliable=row.is_reliable,
            completeness=row.completeness,
            observations=list(row.observations),
            watch_version=row.watch_version,
            metric_semantics=row.metric_semantics or "",
            baseline_version=row.baseline_version,
        )

    @staticmethod
    def _reading(
        value: PwValue,
        bucket: PwBucketFact,
        metrics: Mapping[tuple[uuid.UUID, datetime], int],
    ) -> PwReading:
        return reading_for(
            bucket,
            pinned=value.pinned,
            tracked_at_bucket=_tracked_at(value, bucket.bucket_start),
            metric_events=metrics.get((value.id, bucket.bucket_start)),
        )

    def _recent(
        self,
        value: PwValue,
        current: PwBucketFact,
        buckets: Mapping[datetime, PwBucketFact],
        metrics: Mapping[tuple[uuid.UUID, datetime], int],
        thresholds: PwThresholds,
    ) -> tuple[PwReadingPoint, ...]:
        points = []
        for index in range(thresholds.disappear_buckets):
            at = current.bucket_start - timedelta(seconds=current.bucket_seconds * index)
            bucket = buckets.get(at)
            # A bucket stored under another watch version is never compared: it is left out,
            # and the detector reads the gap as "not supplied", never as a zero.
            if bucket is None or bucket.watch_version != value.watch_version:
                continue
            points.append(PwReadingPoint(bucket, self._reading(value, bucket, metrics)))
        return tuple(points)

    @staticmethod
    def _component_first(
        rows: Sequence[tuple[uuid.UUID, int, str, int, str, datetime]],
    ) -> dict[tuple[uuid.UUID, int, str, int], dict[WafMeasurement, datetime]]:
        out: dict[tuple[uuid.UUID, int, str, int], dict[WafMeasurement, datetime]] = {}
        for watch_id, version, component, position, measurement, first_at in rows:
            kind = (
                WafMeasurement.LOWER_BOUND
                if measurement == PwBucketMeasurement.LOWER_BOUND.value
                else WafMeasurement.EXACT
            )
            slot = out.setdefault((watch_id, version, component, position), {})
            at = _aware(first_at) or first_at
            slot[kind] = min(at, slot.get(kind, at))
        return out

    def _history(
        self,
        value: PwValue,
        first: datetime,
        current: PwBucketFact,
        buckets: Mapping[datetime, PwBucketFact],
        first_collected: Mapping[tuple[uuid.UUID, int], datetime],
        thresholds: PwThresholds,
    ) -> _History:
        """What depends only on (watch, version, first appearance): shared by its values."""
        history_start = first - timedelta(days=thresholds.new_value_history_days)
        step = timedelta(seconds=current.bucket_seconds)
        before: list[PwBucketFact] = []
        at = history_start
        while at < first:
            found = buckets.get(at)
            if found is not None:
                before.append(found)
            at += step
        facts = verify_value_history(
            before,
            history_start=history_start,
            history_end=first,
            bucket_seconds=current.bucket_seconds,
            watch_version=value.watch_version,
        )
        appearance = buckets.get(first)
        if appearance is None:
            appearance = current
            facts = HistoryFacts(
                complete=False, reason="the first-appearance bucket was never collected"
            )
        grace = timedelta(
            seconds=current.bucket_seconds * self.settings.pw_scope_change_grace_buckets
        )
        membership_changed = any(
            found.scope_changed and first - grace <= found.bucket_start <= first + grace
            for found in buckets.values()
        )
        return _History(
            facts=facts,
            start=history_start,
            buckets=len(before),
            appearance=appearance,
            first_collected=first_collected.get((value.watch_id, value.watch_version)),
            membership_changed=membership_changed,
        )

    @staticmethod
    def _established(
        watch: PropertyWatch, value: PwValue, current: PwBucketFact
    ) -> PwNoveltyFacts | None:
        """Novelty facts of a value whose novelty window ended before every pending bucket.

        The detectors leave at "established value" before reading anything else, so none of
        the history is loaded or claimed for it.
        """
        first = _aware(value.first_seen_at)
        if first is None:
            return None
        return PwNoveltyFacts(
            first_seen_at=first,
            history=HistoryFacts(complete=False, reason="outside the novelty window"),
            history_start=first,
            history_end=first,
            history_buckets=0,
            appearance_bucket=current,
            events_since_first_seen=0,
            buckets_with_traffic=0,
            materiality_measurement=WafMeasurement.LOWER_BOUND,
            watch_first_collected_at=None,
            pinned=value.pinned,
            property_count=len(watch.properties),
        )

    def _novelty(
        self,
        watch: PropertyWatch,
        value: PwValue,
        current: PwBucketFact,
        buckets: Mapping[datetime, PwBucketFact],
        metrics: Mapping[tuple[uuid.UUID, datetime], int],
        components: Mapping[
            tuple[uuid.UUID, int, str, int], Mapping[WafMeasurement, datetime]
        ],
        first_collected: Mapping[tuple[uuid.UUID, int], datetime],
        cache: dict[tuple[uuid.UUID, int, datetime], _History],
        thresholds: PwThresholds,
    ) -> PwNoveltyFacts | None:
        first = _aware(value.first_seen_at)
        if first is None:
            return None
        key = (watch.id, value.watch_version, first)
        if key not in cache:
            cache[key] = self._history(
                value, first, current, buckets, first_collected, thresholds
            )
        history = cache[key]
        step = timedelta(seconds=current.bucket_seconds)
        since: list[PwBucketFact] = []
        at = first
        while at <= current.bucket_start:
            found = buckets.get(at)
            if found is not None and found.watch_version == value.watch_version:
                since.append(found)
            at += step
        events = sum(metrics.get((value.id, bucket.bucket_start), 0) for bucket in since)
        traffic = sum(metrics.get((value.id, bucket.bucket_start), 0) > 0 for bucket in since)
        expected = (
            max(0, int((current.bucket_start - first).total_seconds()) // current.bucket_seconds)
            + 1
        )
        materiality = (
            WafMeasurement.EXACT
            if len(since) == expected
            and all(
                bucket.values_measurement is PwBucketMeasurement.EXACT
                or (value.pinned and bucket.pinned_measurement is PwPinnedMeasurement.EXACT)
                for bucket in since
            )
            else WafMeasurement.LOWER_BOUND
        )
        component_facts: list[PwComponentFact] = []
        for index, component in enumerate((value.component_0, value.component_1)):
            if component is None:
                continue
            seen = components.get((watch.id, value.watch_version, component, index), {})
            exact = seen.get(WafMeasurement.EXACT)
            lower = seen.get(WafMeasurement.LOWER_BOUND)
            exact_before = exact is not None and exact < first
            lower_before = lower is not None and lower < first
            measurement = (
                WafMeasurement.LOWER_BOUND
                if lower_before
                else WafMeasurement.EXACT if exact_before else None
            )
            component_facts.append(
                PwComponentFact(
                    value=component,
                    tracked_before=exact_before or lower_before,
                    measurement=measurement,
                )
            )
        return PwNoveltyFacts(
            first_seen_at=first,
            history=history.facts,
            history_start=history.start,
            history_end=first,
            history_buckets=history.buckets,
            appearance_bucket=history.appearance,
            events_since_first_seen=events,
            buckets_with_traffic=traffic,
            materiality_measurement=materiality,
            watch_first_collected_at=history.first_collected,
            pinned=value.pinned,
            membership_changed=history.membership_changed,
            property_count=len(watch.properties),
            components=tuple(component_facts),
        )

    def _advance(
        self,
        instance: QRadarInstance,
        watch: PropertyWatch,
        value: PwValue,
        bucket: PwBucketFact,
        state: _State,
        evidence: AnomalyEvidence[PwAnomalyType],
        incidents: dict[uuid.UUID, _Incident],
        writes: _Writes,
        report: PwEvaluationReport,
    ) -> None:
        if (
            state.last_interval_start is not None
            and state.last_interval_start >= bucket.bucket_start
        ):
            return
        previous = state.state
        state.consecutive_anomalous, state.consecutive_healthy = advance_counters(
            evidence.signal, state.consecutive_anomalous, state.consecutive_healthy
        )
        state.last_interval_start = bucket.bucket_start
        decision = next_state(
            LifecycleInput(
                state=previous,
                signal=evidence.signal,
                consecutive_anomalous=state.consecutive_anomalous,
                consecutive_healthy=state.consecutive_healthy,
                open_after=self.settings.anomaly_open_after_intervals,
                resolve_after=self.settings.anomaly_resolve_after_intervals,
                insufficient_data=evidence.insufficient_data,
            )
        )
        state.dirty = True
        target = decision.state
        incident = incidents.get(state.active_anomaly_id) if state.active_anomaly_id else None
        if target is previous:
            if incident is not None and previous is AnomalyState.OPEN and evidence.is_anomalous:
                self._touch(incident, state, bucket, evidence)
            return
        state.state = target
        if target in (AnomalyState.CANDIDATE, AnomalyState.OPEN) and incident is None:
            incident = self._new_incident(instance, watch, value, bucket, evidence)
            incidents[incident.id] = incident
            state.active_anomaly_id = incident.id
        if incident is not None:
            incident.row["state"] = target.value
            incident.dirty = True
            writes.transitions.append(
                self._transition(
                    incident.id,
                    previous,
                    target,
                    bucket.bucket_start,
                    decision.reason,
                    evidence,
                )
            )
        if target is AnomalyState.OPEN:
            assert incident is not None
            incident.row.update(
                opened_at=self._clock(), anomaly_end=None, severity=str(watch.severity)
            )
            state.open_anomaly_id = incident.id
            self._touch(incident, state, bucket, evidence)
            report.opened += 1
        elif target is AnomalyState.RECOVERING:
            if incident is not None:
                incident.row["anomaly_end"] = bucket.bucket_start
        elif target is AnomalyState.RESOLVED:
            if incident is not None:
                incident.row["resolved_at"] = self._clock()
            state.active_anomaly_id = None
            state.open_anomaly_id = None
            report.resolved += 1
        elif target is AnomalyState.NORMAL:
            if incident is not None:
                incident.row["anomaly_end"] = bucket.bucket_start
            state.active_anomaly_id = None
            state.open_anomaly_id = None

    def _new_incident(
        self,
        instance: QRadarInstance,
        watch: PropertyWatch,
        value: PwValue,
        bucket: PwBucketFact,
        evidence: AnomalyEvidence[PwAnomalyType],
    ) -> _Incident:
        incident_id = uuid.uuid4()
        facts = evidence.extra or {}
        row: dict[str, object] = {
            "id": incident_id,
            "watch_id": watch.id,
            "pw_value_id": value.id,
            "value_key": value.value_key,
            "detector_type": evidence.anomaly_type.value,
            "fingerprint": _fingerprint(
                instance.id, watch.id, value.value_key, evidence.anomaly_type.value
            ),
            "severity": str(watch.severity),
            "state": AnomalyState.CANDIDATE.value,
            "detected_at": bucket.bucket_start,
            "opened_at": None,
            "anomaly_start": bucket.bucket_start,
            "anomaly_end": bucket.bucket_start + timedelta(seconds=bucket.bucket_seconds),
            "resolved_at": None,
            "observed_value": evidence.observed_value,
            "expected_value": evidence.expected_value,
            "deviation_score": evidence.deviation_score,
            "deviation_ratio": facts.get("ratio"),
            "robust_z": facts.get("robust_z"),
            "absolute_delta": facts.get("absolute_delta"),
            "confidence": evidence.confidence,
            "consecutive_buckets": 1,
            "baseline_version": facts.get("baseline_version"),
            "policy_version": self.settings.anomaly_policy_version,
            "watch_version": value.watch_version,
            "explanation": evidence.reason,
            "details": evidence.to_dict(),
            "evidence_status": EvidenceStatus.NOT_REQUESTED.value,
            "suppressed": False,
            "suppression_reason": None,
            "observed_measurement": self._measurement(evidence).value,
        }
        return _Incident(row, dirty=True)

    def _touch(
        self,
        incident: _Incident,
        state: _State,
        bucket: PwBucketFact,
        evidence: AnomalyEvidence[PwAnomalyType],
    ) -> None:
        old_count = incident.row.get("consecutive_buckets")
        prior = old_count if isinstance(old_count, int) else 0
        incident.row["consecutive_buckets"] = max(
            prior, state.consecutive_anomalous
        )
        if evidence.is_anomalous:
            incident.row.update(
                anomaly_end=bucket.bucket_start + timedelta(seconds=bucket.bucket_seconds),
                observed_value=evidence.observed_value,
                expected_value=evidence.expected_value,
                deviation_score=evidence.deviation_score,
                confidence=evidence.confidence,
                explanation=evidence.reason,
                details=evidence.to_dict(),
                observed_measurement=self._measurement(evidence).value,
            )
        incident.dirty = True

    async def _persist(
        self,
        states: Mapping[tuple[uuid.UUID, PwAnomalyType], _State],
        incidents: Mapping[uuid.UUID, _Incident],
        writes: _Writes,
    ) -> None:
        await self.repo.write_detector_states(
            [self._state_row(state) for state in states.values() if state.dirty]
        )
        await self.repo.write_anomalies(
            [incident.row for incident in incidents.values() if incident.dirty]
        )
        await self.repo.write_transitions(writes.transitions)
        await self.repo.update_evaluation_watches(list(writes.watches.values()))

    @staticmethod
    def _state_row(state: _State) -> dict[str, object]:
        return {
            "id": state.id,
            "watch_id": state.watch_id,
            "pw_value_id": state.value_id,
            "detector_type": state.detector.value,
            "consecutive_anomalous": state.consecutive_anomalous,
            "consecutive_healthy": state.consecutive_healthy,
            "state": state.state.value,
            "last_interval_start": state.last_interval_start,
            "active_anomaly_id": state.active_anomaly_id,
            "open_anomaly_id": state.open_anomaly_id,
        }

    @staticmethod
    def _incident_row(row: PwAnomaly) -> dict[str, object]:
        # The audit timestamps are the database's: a new row gets its defaults and a changed
        # row has `updated_at` set by the upsert, so one statement can carry both kinds.
        return {
            column.name: getattr(row, column.name)
            for column in PwAnomaly.__table__.columns
            if column.name not in {"created_at", "updated_at"}
        }

    def _transition(
        self,
        anomaly_id: uuid.UUID,
        previous: AnomalyState,
        target: AnomalyState,
        bucket_start: datetime | None,
        reason: str,
        evidence: AnomalyEvidence[PwAnomalyType] | None = None,
    ) -> dict[str, object]:
        return {
            "id": uuid.uuid4(),
            "pw_anomaly_id": anomaly_id,
            "from_state": previous.value,
            "to_state": target.value,
            "occurred_at": self._clock(),
            "bucket_start": bucket_start,
            "reason": reason,
            "actor": "pw-engine",
            "observed_value": evidence.observed_value if evidence else None,
            "expected_value": evidence.expected_value if evidence else None,
            "observed_measurement": self._measurement(evidence).value,
        }

    @staticmethod
    def _measurement(evidence: AnomalyEvidence[PwAnomalyType] | None) -> WafMeasurement:
        if evidence is not None and (evidence.extra or {}).get("observed_measurement") == (
            WafMeasurement.LOWER_BOUND.value
        ):
            return WafMeasurement.LOWER_BOUND
        return WafMeasurement.EXACT

