"""Fleet-wide IPS collection with bounded admission and bulk persistence.

One run plans every eligible source together. Sources sharing a window and
field references share keyed searches; the tick-wide search budget is shared by
all dimensions and windows. Raw keyed rows are folded before this module builds
any database value.

A source bucket is written only when TOTALS is COMPLETE and every planned
dimension was searched. FAILED and REJECTED dimensions are persisted as states
with NULL facts and coverage; structurally unplanned dimensions are persisted
as NOT_COLLECTED. Both advance the watermark. A planned dimension that was not
searched is transient, so its bucket is not written and the watermark holds.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from copy import deepcopy
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Table, delete, exists, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.anomaly.seasonal import in_maintenance
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.history import (
    ActionKind,
    HistoryFamily,
    HistoryOrigin,
    HistoryState,
    HistoryTrigger,
    IpsGroup,
    bounded_reason,
    console_mode,
    decode_progress,
    encode_progress,
    fit_family_window,
    fit_window,
    history_search_budget,
    ips_group_searches,
    is_live_clause,
    marker_values,
    new_progress,
    new_scope_row,
    next_state,
    scope_action,
    transition_row,
)
from app.history.estimate import IPS_SOURCE_IPS_PER_SOURCE
from app.history.ips import (
    HistoryIpsBucket,
    HistoryIpsEntity,
    fold_ips_history,
    ips_planner_settings,
)
from app.history.lifecycle import decision_view, deferred_scope_row, scope_row, scope_view
from app.history.planner import (
    ChunkResult,
    PlannerSettings,
    ScanSpanResult,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    interpret_scan,
    plan_step,
    settle_step,
)
from app.ips.collection_plan import IpsSourceGroup, plan_searches
from app.ips.fold import (
    IpsActionFact,
    IpsEntityBucketFact,
    IpsEntityFact,
    IpsFanoutFact,
    IpsSourceIpFact,
    IpsTotalsFact,
    coverage,
    fold_outcome,
)
from app.ips.keyed_search import IpsCellOutcome, IpsSearchBudget, run_dimension
from app.ips.profile import IpsFieldMapping, IpsProfileError
from app.models.enums import (
    IpsCellState,
    IpsDimension,
    IpsMeasurement,
    IpsProfileValidationStatus,
    SearchFamily,
)
from app.models.history_backfill import HistoryBackfillDecision
from app.models.instance import QRadarInstance
from app.models.ips import (
    IPS_NORMALIZER_VERSION,
    IpsEntity,
    IpsEntityMetric,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError, QRadarProvider
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.ariel_permit import (
    ArielPermitUnavailable,
    ariel_background_priority,
    ariel_history_deferred_reason,
)
from app.services.ips_pipeline import ips_pipeline_lock_key
from app.services.locks import collector_lock_key
from app.services.search_cadence import SearchCadenceService
from app.waf.admission import AdmissionPolicy, decide_admission

COLLECTOR_NAME = "ips_collection"
logger = logging.getLogger("app.collectors.ips_collector")
_PRESERVE_ERROR = object()
ENTITY_DIMENSIONS = (
    IpsDimension.DESTINATION_PORT,
    IpsDimension.DESTINATION_IP,
    IpsDimension.SIGNATURE,
)


def watermark_collector(qradar_log_source_id: int) -> str:
    return f"{COLLECTOR_NAME}:{int(qradar_log_source_id)}"


@dataclass
class IpsCollectionReport:
    instance_id: uuid.UUID
    status: str = "ok"
    sources_planned: int = 0
    sources_collected: int = 0
    sources_locked: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    searches_sent: int = 0
    buckets_written: int = 0
    entity_rows_written: int = 0
    entities_admitted: int = 0
    entities_reactivated: int = 0
    entities_retired: int = 0
    watermarks_advanced: int = 0
    budget_blocked_windows: list[dict[str, object]] = field(default_factory=list)
    history_sources: int = 0
    history_searches: int = 0
    history_buckets_written: int = 0
    history_states: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sources_planned": self.sources_planned,
            "sources_collected": self.sources_collected,
            "sources_locked": self.sources_locked,
            "skipped": self.skipped,
            "searches_sent": self.searches_sent,
            "buckets_written": self.buckets_written,
            "entity_rows_written": self.entity_rows_written,
            "entities_admitted": self.entities_admitted,
            "entities_reactivated": self.entities_reactivated,
            "entities_retired": self.entities_retired,
            "watermarks_advanced": self.watermarks_advanced,
            "budget_blocked_windows": self.budget_blocked_windows,
            "history_sources": self.history_sources,
            "history_searches": self.history_searches,
            "history_buckets_written": self.history_buckets_written,
            "history_states": self.history_states,
        }


@dataclass(frozen=True)
class _Source:
    profile: IpsSourceProfile
    log_source: LogSource
    mapping: IpsFieldMapping
    window: tuple[datetime, datetime]


class _HistoryProvider:
    """Delegate IPS history reads and retain each caught source fault by class."""

    def __init__(self, provider: QRadarProvider) -> None:
        self.provider = provider
        self.errors: dict[int, ProviderError] = {}
        self.deferred: ArielPermitUnavailable | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    async def get_ips_keyed(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self.provider.get_ips_keyed(*args, **kwargs)
        except ArielPermitUnavailable as exc:
            self.deferred = exc
            raise
        except ProviderError as exc:
            for source_id in kwargs.get("qradar_log_source_ids", ()):
                self.errors.setdefault(int(source_id), exc)
            raise


class _LiveTotalsFailure:
    """Remember the class of a live TOTALS failure, then re-raise it.

    The keyed search turns the exception into a failed cell and does not
    return the class. A message is not stored: it can echo query text. History
    keeps its own provider and is not wrapped.
    """

    def __init__(self, provider: QRadarProvider) -> None:
        self.provider = provider
        self.totals_errors: dict[int, str] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    async def get_ips_keyed(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self.provider.get_ips_keyed(*args, **kwargs)
        except ProviderError as exc:
            dimension = kwargs.get("dimension", args[0] if args else None)
            if dimension is IpsDimension.TOTALS:
                # Class name only. The exception text can echo the query.
                for source_id in kwargs.get("qradar_log_source_ids", ()):
                    self.totals_errors.setdefault(int(source_id), type(exc).__name__)
            raise


@dataclass(frozen=True)
class _Candidate:
    fact: IpsEntityFact
    dimension: IpsDimension

    @property
    def virtual_host(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return self.fact.value_key

    @property
    def method(self) -> str:
        return self.dimension.value

    @property
    def request_count(self) -> int:
        return self.fact.events


@dataclass(frozen=True)
class _SearchResult:
    outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome]
    budget_blocked_reasons: dict[int, str]
    budget_blocked_windows: list[dict[str, object]]
    totals_errors: dict[int, str] = field(default_factory=dict)


@dataclass(frozen=True)
class _HistoryWrite:
    source: _Source
    floor: datetime
    buckets: tuple[HistoryIpsBucket, ...]


@dataclass
class _HistoryFaultContext:
    """The scope being processed and the scopes that started this tick reading."""

    reading_at_start: set[str] = field(default_factory=set)
    current_scope_key: str | None = None


class _TimedIpsSearchBudget(IpsSearchBudget):
    """An IPS budget that also refuses searches after the history time deadline."""

    def __init__(self, max_searches: int, deadline: float) -> None:
        super().__init__(max_searches)
        self.deadline = deadline

    def try_take(self) -> bool:
        if time.monotonic() >= self.deadline:
            return False
        return super().try_take()


def _scan_status(state: IpsCellState) -> SearchStatus:
    """How a cell's state reads as the result of one floor-scan span.

    A span that was not searched, failed or truncated proves nothing, so the scan is
    retried; only a whole, complete span may prove the floor.
    """
    if state is IpsCellState.COMPLETE:
        return SearchStatus.COMPLETE
    if state is IpsCellState.LOWER_BOUND:
        return SearchStatus.TRUNCATED
    if state is IpsCellState.NOT_COLLECTED:
        return SearchStatus.NOT_RUN
    return SearchStatus.FAILED


def _ips_search_estimator(
    group: IpsGroup, settings: PlannerSettings
) -> Callable[[int], int]:
    return lambda days: ips_group_searches(group, days, settings)


class _IpsFleetLocks:
    """Take every per-source IPS pipeline lock in two SQL statements total."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        instance_id: uuid.UUID,
        qradar_ids: Sequence[int],
    ) -> None:
        self.session = session
        self.settings = settings
        self.instance_id = instance_id
        self.qradar_ids = tuple(qradar_ids)
        self.acquired: set[int] = set()
        self._keys: dict[int, int] = {}

    async def __aenter__(self) -> set[int]:
        bind = self.session.bind
        if bind is None or bind.dialect.name != "postgresql":
            self.acquired = set(self.qradar_ids)
            return self.acquired
        self._keys = {
            source_id: collector_lock_key(
                self.instance_id, ips_pipeline_lock_key(source_id)
            )
            for source_id in self.qradar_ids
        }
        if not self._keys:
            return self.acquired
        values = ", ".join(
            f"(CAST(:source_{index} AS integer), CAST(:key_{index} AS integer))"
            for index in range(len(self._keys))
        )
        params: dict[str, int] = {"namespace": self.settings.collection_advisory_lock_namespace}
        for index, (source_id, key) in enumerate(self._keys.items()):
            params[f"source_{index}"] = source_id
            params[f"key_{index}"] = key
        lock_sql = (
            "SELECT v.source_id, pg_try_advisory_lock("  # noqa: S608
            "CAST(:namespace AS integer), v.lock_key) "
            f"FROM (VALUES {values}) AS v(source_id, lock_key)"
        )
        rows = await self.session.execute(text(lock_sql), params)
        self.acquired = {int(source_id) for source_id, held in rows if held}
        return self.acquired

    async def __aexit__(self, *exc: object) -> None:
        if not self.acquired or not self._keys:
            return
        keys = [self._keys[source_id] for source_id in sorted(self.acquired)]
        await self.session.execute(
            text(
                "SELECT pg_advisory_unlock(:namespace, lock_key) "
                "FROM unnest(CAST(:keys AS integer[])) AS lock_key"
            ),
            {"namespace": self.settings.collection_advisory_lock_namespace, "keys": keys},
        )


class IpsCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def collect(self, instance: QRadarInstance) -> IpsCollectionReport:
        report = IpsCollectionReport(instance.id)
        if not self.settings.ips_collection_enabled:
            report.status = "disabled"
            return report
        now = self._clock()
        interval = self.settings.collection_interval_seconds
        boundary = floor_to_interval(now, interval)
        rows = (
            await self.session.execute(
                select(IpsSourceProfile, LogSource, HistoryBackfillDecision)
                .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
                .outerjoin(
                    HistoryBackfillDecision,
                    HistoryBackfillDecision.instance_id == IpsSourceProfile.instance_id,
                )
                .where(IpsSourceProfile.instance_id == instance.id)
                .order_by(LogSource.qradar_id)
            )
        ).all()
        cadence = await SearchCadenceService(self.session, self.settings).get(
            SearchFamily.IPS_COLLECTION
        )
        max_buckets = max(1, cadence.cadence_seconds // interval)
        max_buckets += self.settings.collection_max_backfill_intervals
        watermark_rows = await self._load_watermarks(
            instance, [source for _, source, _decision in rows]
        )
        decision_row = rows[0][2] if rows else None

        candidates: list[_Source] = []
        skipped_rows: list[dict[str, object]] = []
        for profile, source, _decision in rows:
            reason = self._ineligibility(profile, source)
            mapping: IpsFieldMapping | None = None
            if reason is None:
                try:
                    mapping = IpsFieldMapping.from_profile(profile)
                except IpsProfileError:
                    reason = "invalid profile mapping"
            if reason is not None or mapping is None:
                report.skipped[reason or "invalid profile mapping"] = (
                    report.skipped.get(reason or "invalid profile mapping", 0) + 1
                )
                skipped_rows.append(
                    self._watermark_values(
                        instance,
                        source,
                        watermark_rows.get(source.qradar_id),
                        now=now,
                        skip_reason=reason or "invalid profile mapping",
                    )
                )
                continue
            watermark = watermark_rows.get(source.qradar_id)
            start = (
                watermark.watermark_at + timedelta(seconds=interval)
                if watermark is not None and watermark.watermark_at is not None
                else boundary - timedelta(seconds=interval)
            )
            end = min(boundary, start + timedelta(seconds=interval * max_buckets))
            candidates.append(_Source(profile, source, mapping, (start, end)))

        # The ordinary collection path must retain its established 12-statement
        # bound. The decision was folded into the fleet query above; without a
        # Yes, no scope or live-data query is useful, so history stops here.
        has_yes = decision_row is not None and decision_row.answer == "YES"
        lock_candidates = candidates if has_yes else [
            item for item in candidates if item.window[0] < item.window[1]
        ]
        report.sources_planned = len(lock_candidates)
        async with _IpsFleetLocks(
            self.session,
            self.settings,
            instance.id,
            [item.log_source.qradar_id for item in lock_candidates],
        ) as acquired:
            locked = [
                item for item in lock_candidates if item.log_source.qradar_id in acquired
            ]
            report.sources_locked = len(lock_candidates) - len(locked)
            if report.sources_locked:
                report.skipped["source locked"] = report.sources_locked

            scope_values: dict[int, dict[str, object]] = {}
            handoffs: dict[int, bool] = {}
            history_sources: list[_Source] = []
            history_fault = _HistoryFaultContext()
            live_sources = [item for item in locked if item.window[0] < item.window[1]]
            if has_yes and decision_row is not None:
                try:
                    async with self.session.begin_nested():
                        live_sources, history_sources, scope_values, handoffs = (
                            await self._apply_history_lifecycle(
                                instance,
                                locked,
                                decision_row,
                                now=now,
                                fault_context=history_fault,
                            )
                        )
                        if handoffs:
                            await self._write_handoff_watermarks(
                                instance,
                                {
                                    item.log_source.qradar_id: item.log_source
                                    for item in locked
                                },
                                handoffs,
                                boundary=boundary,
                                now=now,
                                interval=interval,
                            )
                            # A settled hand-off already owns the last bucket before this
                            # boundary. An empty hand-off deliberately lets the ordinary
                            # first-live window run now.
                            live_sources = [
                                replace(item, window=(boundary, boundary))
                                if handoffs.get(item.log_source.qradar_id)
                                else item
                                for item in live_sources
                            ]
                            handoffs = {}
                        if scope_values and not history_sources:
                            await HistoryBackfillRepository(self.session).write_scopes(
                                list(scope_values.values())
                            )
                            scope_values = {}
                except Exception as exc:
                    error_keys = await self._history_fault_scope_keys(
                        instance, history_fault
                    )
                    await HistoryBackfillRepository(self.session).write_step_errors(
                        instance.id,
                        HistoryFamily.IPS.value,
                        dict.fromkeys(error_keys, exc),
                        at=self._clock(),
                    )
                    logger.warning(
                        "IPS history lifecycle failed",
                        extra={
                            "instance_id": str(instance.id),
                            "scope_keys": error_keys,
                            "error_class": type(exc).__name__,
                        },
                    )
                    scope_values = {}
                    handoffs = {}
                    history_sources = []
                    # Sources that were already reading stay paused. Every other
                    # locked source takes its ordinary live window, as though the
                    # lifecycle had found no transition or hand-off to apply.
                    live_sources = [
                        item
                        for item in locked
                        if str(item.log_source.qradar_id)
                        not in history_fault.reading_at_start
                        and item.window[0] < item.window[1]
                    ]
                else:
                    live_sources = [
                        item for item in live_sources if item.window[0] < item.window[1]
                    ]

            budget = IpsSearchBudget(self.settings.search_cadence_max_searches_per_tick)
            search_result = await self._search(live_sources, interval, budget)
            report.searches_sent = budget.used
            report.budget_blocked_windows.extend(search_result.budget_blocked_windows)
            await self._persist(
                instance,
                live_sources,
                search_result.outcomes,
                watermark_rows,
                skipped_rows,
                now,
                interval,
                report,
                search_result.budget_blocked_reasons,
                search_result.totals_errors,
            )
            if history_sources:
                lifecycle_scope_values = scope_values
                run_scope_values = deepcopy(scope_values)
                report_before_history = (
                    report.history_sources,
                    report.history_searches,
                    report.history_buckets_written,
                    dict(report.history_states),
                )
                try:
                    async with self.session.begin_nested():
                        new_handoffs, history_errors = await self._run_history(
                            instance,
                            history_sources,
                            run_scope_values,
                            boundary=boundary,
                            now=now,
                            interval=interval,
                            cadence_seconds=cadence.cadence_seconds,
                            live_searches=budget.used,
                            report=report,
                            fault_context=history_fault,
                        )
                        await HistoryBackfillRepository(self.session).write_scopes(
                            list(run_scope_values.values())
                        )
                        if history_errors:
                            await HistoryBackfillRepository(self.session).write_step_errors(
                                instance.id,
                                HistoryFamily.IPS.value,
                                {
                                    str(source_id): exc
                                    for source_id, exc in history_errors.items()
                                },
                                at=self._clock(),
                            )
                        if new_handoffs:
                            await self._write_handoff_watermarks(
                                instance,
                                {
                                    item.log_source.qradar_id: item.log_source
                                    for item in locked
                                },
                                new_handoffs,
                                boundary=boundary,
                                now=now,
                                interval=interval,
                            )
                except ArielPermitUnavailable as exc:
                    (
                        report.history_sources,
                        report.history_searches,
                        report.history_buckets_written,
                        report.history_states,
                    ) = report_before_history
                    scope_keys = await self._history_fault_scope_keys(
                        instance, history_fault
                    )
                    repository = HistoryBackfillRepository(self.session)
                    stored = await repository.scopes(
                        instance.id, HistoryFamily.IPS.value, scope_keys
                    )
                    reason = ariel_history_deferred_reason(exc)
                    deferred_rows = [
                        deferred_scope_row(
                            scope,
                            instance_id=instance.id,
                            family=HistoryFamily.IPS,
                            scope_key=scope_key,
                            reason=reason,
                        )
                        for scope_key in scope_keys
                        if (scope := stored.get(scope_key)) is not None
                        and scope.state == HistoryState.READING.value
                    ]
                    if deferred_rows:
                        await repository.write_scopes(deferred_rows)
                except Exception as exc:
                    (
                        report.history_sources,
                        report.history_searches,
                        report.history_buckets_written,
                        report.history_states,
                    ) = report_before_history
                    await HistoryBackfillRepository(self.session).write_scopes(
                        list(lifecycle_scope_values.values())
                    )
                    error_keys = await self._history_fault_scope_keys(
                        instance, history_fault
                    )
                    await HistoryBackfillRepository(self.session).write_step_errors(
                        instance.id,
                        HistoryFamily.IPS.value,
                        dict.fromkeys(error_keys, exc),
                        at=self._clock(),
                    )
                    logger.warning(
                        "IPS history run failed",
                        extra={
                            "instance_id": str(instance.id),
                            "scope_keys": error_keys,
                            "error_class": type(exc).__name__,
                        },
                    )
        return report

    async def _history_fault_scope_keys(
        self, instance: QRadarInstance, context: _HistoryFaultContext
    ) -> tuple[str, ...]:
        """Resolve the scope keys to mark after a history savepoint rolled back."""
        if context.current_scope_key is not None:
            return (context.current_scope_key,)
        if not context.reading_at_start:
            scopes = await HistoryBackfillRepository(self.session).scopes(
                instance.id, HistoryFamily.IPS.value
            )
            context.reading_at_start = {
                key
                for key, row in scopes.items()
                if row.state == HistoryState.READING.value
            }
        return tuple(sorted(context.reading_at_start))

    def _ineligibility(self, profile: IpsSourceProfile, source: LogSource) -> str | None:
        if not profile.enabled:
            return "profile disabled"
        if not source.monitoring_enabled:
            return "monitoring disabled"
        if profile.validated_profile_version != profile.profile_version:
            return "current profile version is not validated"
        if profile.validation_status != IpsProfileValidationStatus.VALID:
            return f"profile validation is {profile.validation_status}"
        return None

    async def _load_watermarks(
        self, instance: QRadarInstance, sources: Sequence[LogSource]
    ) -> dict[int, CollectionWatermark]:
        collectors = [watermark_collector(source.qradar_id) for source in sources]
        if not collectors:
            return {}
        rows = (
            await self.session.scalars(
                select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == instance.id,
                    CollectionWatermark.collector.in_(collectors),
                )
            )
        ).all()
        by_name = {row.collector: row for row in rows}
        return {
            source.qradar_id: row
            for source in sources
            if (row := by_name.get(watermark_collector(source.qradar_id))) is not None
        }

    async def _apply_history_lifecycle(
        self,
        instance: QRadarInstance,
        sources: Sequence[_Source],
        decision_row: HistoryBackfillDecision,
        *,
        now: datetime,
        fault_context: _HistoryFaultContext,
    ) -> tuple[
        list[_Source],
        list[_Source],
        dict[int, dict[str, object]],
        dict[int, bool],
    ]:
        """Apply every eligible source's lifecycle before the live fleet plan.

        The reads are bulk and happen only for a Yes decision: one scope read and one
        live-source read, independent of fleet size. Decisions without a Yes stay on
        the pre-HB-7 statement shape in ``collect``.
        """
        if not sources:
            return [], [], {}, {}
        repository = HistoryBackfillRepository(self.session)
        scopes = await repository.scopes(instance.id, HistoryFamily.IPS.value)
        fault_context.reading_at_start = {
            key for key, row in scopes.items() if row.state == HistoryState.READING.value
        }
        # The lifecycle reads `has_live_data` only to decide whether a scope may be
        # created, so the live-bucket read happens only for a source that has no scope
        # row yet. It is one bulk statement whatever the fleet size, and it looks at
        # live buckets only: a source whose only rows are history is still eligible.
        live_ids: set[uuid.UUID] = set()
        fresh = [item for item in sources if str(item.log_source.qradar_id) not in scopes]
        if fresh:
            source_ids = [item.log_source.id for item in fresh]
            live_ids = set(
                (
                    await self.session.scalars(
                        select(IpsSourceBucket.log_source_id)
                        .where(
                            IpsSourceBucket.log_source_id.in_(source_ids),
                            is_live_clause(IpsSourceBucket),
                        )
                        .distinct()
                    )
                ).all()
            )
        decision = decision_view(decision_row)
        live: list[_Source] = []
        reading: list[_Source] = []
        values_by_source: dict[int, dict[str, object]] = {}
        handoffs: dict[int, bool] = {}
        mode = console_mode(decision)

        for item in sources:
            qradar_id = item.log_source.qradar_id
            key = str(qradar_id)
            fault_context.current_scope_key = key
            row = scopes.get(key)
            row_view = scope_view(row)
            action = scope_action(
                family=HistoryFamily.IPS,
                mode=mode,
                decision=decision,
                row=row_view,
                has_live_data=item.log_source.id in live_ids,
                live_anchor=None,
                switch_on=self.settings.history_backfill_enabled,
            )
            values: dict[str, object] | None = None
            if action.kind is ActionKind.CREATE:
                values = new_scope_row(
                    action,
                    instance_id=instance.id,
                    family=HistoryFamily.IPS,
                    scope_key=key,
                    decision=decision,
                    at=now,
                )
            elif action.kind is ActionKind.TRANSITION:
                assert row is not None and row_view is not None
                values = transition_row(
                    action,
                    instance_id=instance.id,
                    family=HistoryFamily.IPS,
                    scope_key=key,
                    row=row_view,
                    decision=decision,
                    at=now,
                )
                if row.state == HistoryState.READING.value:
                    handoffs[qradar_id] = row.through is not None
            elif row is not None:
                assert row_view is not None
                values = scope_row(
                    row_view,
                    instance_id=instance.id,
                    family=HistoryFamily.IPS,
                    scope_key=key,
                )

            # Act on the mapping just built, never on the identity-mapped row the
            # repository upsert will leave stale (HB-7's written-row decision).
            state = None if values is None else str(values["state"])
            if state == HistoryState.READING.value:
                assert values is not None
                reading.append(item)
                values_by_source[qradar_id] = values
            else:
                live.append(item)
                if action.kind in (ActionKind.CREATE, ActionKind.TRANSITION):
                    assert values is not None
                    values_by_source[qradar_id] = values
            fault_context.current_scope_key = None
        reading.sort(
            key=lambda item: (
                values_by_source[item.log_source.qradar_id].get("started_at") or now,
                item.log_source.qradar_id,
            )
        )
        return live, reading, values_by_source, handoffs

    async def _run_history(
        self,
        instance: QRadarInstance,
        sources: Sequence[_Source],
        scope_values: dict[int, dict[str, object]],
        *,
        boundary: datetime,
        now: datetime,
        interval: int,
        cadence_seconds: int,
        live_searches: int,
        report: IpsCollectionReport,
        fault_context: _HistoryFaultContext,
    ) -> tuple[dict[int, bool], dict[int, BaseException]]:
        """Advance independent source scopes under one oldest-first sub-budget.

        IPS history is deliberately per source: a shared keyed search returns roughly
        one source's rows times the group size, while coupling unrelated floors and
        failures. Sources share only this tick's search and time budgets.

        One chunk costs one search per planned dimension, so the planner is given the
        number of chunks the sub-budget can pay for, not the number of searches.
        """
        planner = ips_planner_settings(self.settings, cadence_seconds=cadence_seconds)
        remaining = history_search_budget(self.settings, live_searches)
        budget = _TimedIpsSearchBudget(
            remaining,
            time.monotonic() + float(self.settings.history_backfill_seconds_per_tick),
        )
        fair_share = max(1, remaining // max(1, len(sources)))
        starting = []
        for item in sources:
            stored = scope_values[item.log_source.qradar_id]["progress"]
            assert isinstance(stored, dict)
            if not stored:
                starting.append(item)
        fitted = fit_family_window(HistoryFamily.IPS, self.settings) if starting else None
        entity_counts = (
            await self._history_entity_counts(starting)
            if starting and fitted is not None and fitted.available
            else {}
        )
        writes: list[_HistoryWrite] = []
        handoffs: dict[int, bool] = {}
        history_provider = _HistoryProvider(self.provider)

        for item in sources:
            source_id = item.log_source.qradar_id
            fault_context.current_scope_key = str(source_id)
            values = scope_values[source_id]
            report.history_sources += 1
            stored = values["progress"]
            assert isinstance(stored, dict)
            fit_reason: str | None = None
            if stored:
                decoded = decode_progress(stored)
                progress = decoded.progress
                adapter = decoded.adapter
                stored_reason = adapter.get("fit_reason")
                fit_reason = stored_reason if isinstance(stored_reason, str) else None
            else:
                assert fitted is not None
                if not fitted.available or fitted.days is None:
                    change = next_state(HistoryState.READING, HistoryTrigger.WINDOW_TOO_SMALL)
                    values.update(
                        state=change.state.value,
                        anchor=boundary,
                        finished_at=now,
                        reason=bounded_reason(fitted.reason or change.reason),
                    )
                    handoffs[source_id] = False
                    report.history_states[str(source_id)] = change.state.value
                    fault_context.current_scope_key = None
                    continue
                counts = entity_counts.get(item.log_source.id, {})
                no_entities = not any(counts.values())
                group = IpsGroup(
                    sources=1,
                    ports=max(1, counts.get(IpsDimension.DESTINATION_PORT, 0)),
                    ips=max(1, counts.get(IpsDimension.DESTINATION_IP, 0)),
                    signatures=max(1, counts.get(IpsDimension.SIGNATURE, 0)),
                    source_ips=IPS_SOURCE_IPS_PER_SOURCE,
                )
                fit = fit_window(
                    fitted.days,
                    _ips_search_estimator(group, planner),
                    end=boundary,
                    settings=planner,
                    searches_per_tick=fair_share,
                )
                zero_reason = (
                    f"fitted with no admitted entity yet; the "
                    f"{self.settings.history_backfill_max_hours}-hour limit still bounds "
                    "the reading"
                    if no_entities
                    else None
                )
                fair_reason = (
                    f"fitted with a fair share of {fair_share} of the {remaining} shared "
                    "history searches per tick"
                )
                fit_reason = "; ".join(
                    part
                    for part in (
                        fitted.reason,
                        fit.reason if fit.reduced or fit.refused else None,
                        zero_reason,
                        fair_reason,
                    )
                    if part
                )
                if fit.refused or fit.days_fitted is None or fit.target_start is None:
                    change = next_state(HistoryState.READING, HistoryTrigger.WINDOW_TOO_SMALL)
                    values.update(
                        state=change.state.value,
                        anchor=boundary,
                        finished_at=now,
                        reason=bounded_reason(fit_reason),
                    )
                    handoffs[source_id] = False
                    report.history_states[str(source_id)] = change.state.value
                    fault_context.current_scope_key = None
                    continue
                started_at = values.get("started_at")
                assert isinstance(started_at, datetime)
                progress = new_progress(fit.target_start, started_at, planner)
                adapter = {"fit_reason": fit_reason}
                values.update(
                    days_target=fit.days_fitted,
                    target_start=fit.target_start,
                )

            dimensions = self._history_dimensions(item)
            chunk_budget = max(0, budget.remaining // max(1, len(dimensions)))
            plan = plan_step(
                progress,
                now=now,
                end=boundary,
                budget=StepBudget(
                    chunk_budget,
                    max(0.0, budget.deadline - time.monotonic()),
                ),
                settings=planner,
            )
            reason = plan.reason
            trigger = plan.trigger
            updated = progress
            if plan.action is StepAction.SCAN:
                assert plan.scan_span is not None
                status, totals = await self._history_scan(
                    item, plan.scan_span, interval, budget, history_provider
                )
                decision = interpret_scan(
                    progress,
                    [ScanSpanResult(plan.scan_span, status, totals)],
                    boundary,
                    planner,
                )
                updated = decision.progress
                reason = decision.reason
                trigger = decision.trigger
            elif plan.action is StepAction.READ:
                results: list[ChunkResult] = []
                completed: dict[Span, dict[tuple[int, datetime, IpsDimension], IpsCellOutcome]] = {}
                for chunk in plan.chunks:
                    outcome_map, result = await self._history_search_span(
                        item, chunk, interval, budget, history_provider
                    )
                    results.append(result)
                    if result.status is SearchStatus.COMPLETE:
                        completed[chunk] = outcome_map
                    else:
                        break
                if results:
                    settled = settle_step(progress, results, end=boundary, settings=planner)
                    updated = settled.progress
                    reason = settled.reason
                    trigger = settled.trigger
                    ready: list[HistoryIpsBucket] = []
                    for span in settled.write_spans:
                        ready.extend(self._history_buckets(item, (span,), completed[span]))
                    if ready:
                        assert updated.floor is not None
                        writes.append(_HistoryWrite(item, updated.floor, tuple(ready)))

            reason = "; ".join(part for part in (fit_reason, reason) if part)
            values.update(
                floor=updated.floor,
                through=updated.through,
                progress=encode_progress(updated, adapter=adapter),
                reason=bounded_reason(reason),
            )
            if trigger is not None:
                change = next_state(HistoryState.READING, trigger)
                values.update(
                    state=change.state.value,
                    anchor=boundary,
                    finished_at=now,
                )
                handoffs[source_id] = (
                    updated.through is not None
                    and change.state is not HistoryState.UNAVAILABLE
                )
            report.history_states[str(source_id)] = str(values["state"])
            fault_context.current_scope_key = None

        report.history_searches = budget.used
        if writes:
            await self._persist_history(instance, writes, now=now, interval=interval, report=report)
        errors: dict[int, BaseException] = {
            source_id: exc
            for source_id, exc in history_provider.errors.items()
            if str(scope_values[source_id]["state"]) == HistoryState.READING.value
        }
        return handoffs, errors

    async def _history_entity_counts(
        self, sources: Sequence[_Source]
    ) -> dict[uuid.UUID, dict[IpsDimension, int]]:
        """Active entity counts for every starting source in one bulk statement."""
        source_ids = [item.log_source.id for item in sources]
        rows = await self.session.execute(
            select(IpsEntity.log_source_id, IpsEntity.dimension, func.count(IpsEntity.id))
            .where(
                IpsEntity.log_source_id.in_(source_ids),
                IpsEntity.active.is_(True),
            )
            .group_by(IpsEntity.log_source_id, IpsEntity.dimension)
        )
        counts: dict[uuid.UUID, dict[IpsDimension, int]] = {
            source_id: {} for source_id in source_ids
        }
        for source_id, dimension, entities in rows:
            counts[source_id][IpsDimension(str(dimension))] = int(entities)
        return counts

    def _history_dimensions(self, source: _Source) -> tuple[IpsDimension, ...]:
        """The dimensions one source's keyed searches read, in the plan's order.

        A chunk of history costs one search per dimension, so this is also what
        converts the tick's search sub-budget into a number of chunks.
        """
        plan = plan_searches(
            [(source.log_source.qradar_id, source.mapping)],
            max_sources_per_search=self.settings.ips_max_sources_per_search,
        )
        return tuple(search.dimension for search in plan)

    async def _history_scan(
        self,
        source: _Source,
        span: Span,
        interval: int,
        budget: IpsSearchBudget,
        provider: _HistoryProvider,
    ) -> tuple[SearchStatus, tuple[tuple[datetime, int], ...]]:
        """The floor proof: the `TOTALS` dimension of one span, and nothing else.

        The proof comes from the read itself, so it costs the one search it proves
        the floor with (§B.3.2). A bucket before the floor is never written, so the
        scan writes nothing itself.
        """
        group = IpsSourceGroup(fields=(), source_ids=(source.log_source.qradar_id,))
        with ariel_background_priority():
            cells = await run_dimension(
                cast(QRadarProvider, provider),
                IpsDimension.TOTALS,
                group,
                (span.start, span.end),
                interval,
                max_rows=self.settings.ariel_multi_bucket_max_rows,
                budget=budget,
            )
        if provider.deferred is not None:
            raise provider.deferred
        totals: list[tuple[datetime, int]] = []
        status = SearchStatus.COMPLETE
        for cell in cells:
            if cell.state is IpsCellState.COMPLETE:
                fact = fold_outcome(
                    cell,
                    mapping=source.mapping,
                    max_entities=self.settings.ips_max_entities_per_bucket,
                )
                if isinstance(fact, IpsTotalsFact):
                    totals.append((cell.bucket_start, fact.events))
                continue
            status = _scan_status(cell.state)
            break
        return status, tuple(totals)

    async def _history_search_span(
        self,
        source: _Source,
        span: Span,
        interval: int,
        budget: IpsSearchBudget,
        provider: _HistoryProvider,
    ) -> tuple[
        dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
        ChunkResult,
    ]:
        before = budget.used
        with ariel_background_priority():
            searched = await self._search(
                [replace(source, window=(span.start, span.end))],
                interval,
                budget,
                provider=cast(QRadarProvider, provider),
            )
        if provider.deferred is not None:
            raise provider.deferred
        spent = budget.used - before
        result = self._history_chunk_result(source, span, searched.outcomes, searches=spent)
        return searched.outcomes, result

    def _history_chunk_result(
        self,
        source: _Source,
        span: Span,
        outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
        *,
        searches: int,
    ) -> ChunkResult:
        planned = self._history_dimensions(source)
        status = SearchStatus.COMPLETE
        rows = non_silent = lower_bound = 0
        cursor = span.start
        while cursor < span.end:
            cells = {
                dimension: outcomes.get((source.log_source.qradar_id, cursor, dimension))
                for dimension in planned
            }
            if any(
                cell is None or cell.state is IpsCellState.NOT_COLLECTED
                for cell in cells.values()
            ):
                status = SearchStatus.NOT_RUN
                break
            totals = cells.get(IpsDimension.TOTALS)
            if totals is None or totals.state is IpsCellState.FAILED:
                status = SearchStatus.FAILED
                break
            if totals.state is not IpsCellState.COMPLETE:
                status = SearchStatus.TRUNCATED
                break
            # The row cap binds one search, so the density of a bucket is its
            # busiest dimension, not the sum over the dimensions.
            busiest = max(
                (len(cell.rows) for cell in cells.values() if cell is not None),
                default=0,
            )
            rows += busiest
            non_silent += int(bool(busiest))
            lower_bound += int(
                any(
                    cell is not None and cell.state is IpsCellState.LOWER_BOUND
                    for cell in cells.values()
                )
            )
            cursor += timedelta(seconds=self.settings.collection_interval_seconds)
        return ChunkResult(
            span,
            status,
            rows=rows,
            non_silent_buckets=non_silent,
            lower_bound_buckets=lower_bound,
            searches=searches,
        )

    @staticmethod
    def _history_buckets(
        source: _Source,
        spans: Sequence[Span],
        outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
    ) -> tuple[HistoryIpsBucket, ...]:
        buckets: list[HistoryIpsBucket] = []
        for span in spans:
            starts = sorted(
                {
                    start
                    for qradar_id, start, _dimension in outcomes
                    if qradar_id == source.log_source.qradar_id
                    and span.start <= start < span.end
                }
            )
            for start in starts:
                cells = {
                    dimension: outcome
                    for (qradar_id, bucket, dimension), outcome in outcomes.items()
                    if qradar_id == source.log_source.qradar_id and bucket == start
                }
                totals = cells.get(IpsDimension.TOTALS)
                # Only a whole, complete bucket is written: a bucket whose totals did
                # not come back whole proves nothing.
                if totals is None or totals.state is not IpsCellState.COMPLETE:
                    continue
                buckets.append(HistoryIpsBucket(start, cells))
        return tuple(buckets)

    async def _persist_history(
        self,
        instance: QRadarInstance,
        writes: Sequence[_HistoryWrite],
        *,
        now: datetime,
        interval: int,
        report: IpsCollectionReport,
    ) -> None:
        """Fold the fleet, then executemany each table once (constant SQL cost)."""
        source_ids = [write.source.log_source.id for write in writes]
        stored = (
            await self.session.scalars(
                select(IpsEntity).where(IpsEntity.log_source_id.in_(source_ids))
            )
        ).all()
        by_source: dict[uuid.UUID, list[HistoryIpsEntity]] = defaultdict(list)
        for row in stored:
            by_source[row.log_source_id].append(
                HistoryIpsEntity(
                    row.id,
                    row.instance_id,
                    row.log_source_id,
                    IpsDimension(str(row.dimension)),
                    row.value_key,
                    row.display_name,
                    row.first_seen_at,
                    row.last_seen_at,
                    row.active,
                    row.retired_at,
                    row.normalizer_version,
                )
            )
        entity_rows: list[dict[str, object]] = []
        bucket_rows: list[dict[str, object]] = []
        metric_rows: list[dict[str, object]] = []
        for write in writes:
            source = write.source.log_source

            def maintenance(at: datetime, source: LogSource = source) -> bool:
                return in_maintenance(
                    at,
                    maintenance_mode=source.maintenance_mode,
                    maintenance_until=source.maintenance_until,
                )

            folded = fold_ips_history(
                write.buckets,
                by_source[source.id],
                instance_id=instance.id,
                log_source_id=source.id,
                mapping=write.source.mapping,
                profile_version=write.source.profile.profile_version,
                bucket_seconds=interval,
                floor=write.floor,
                collected_at=now,
                max_entities_per_bucket=self.settings.ips_max_entities_per_bucket,
                policy=AdmissionPolicy(
                    max_new_per_bucket=self.settings.ips_max_new_entities_per_bucket,
                    max_active=self.settings.ips_max_active_entities_per_source_dimension,
                    min_requests=self.settings.ips_min_admission_events,
                ),
                active_days=self.settings.ips_entity_active_days,
                maintenance=maintenance,
                id_factory=uuid.uuid4,
            )
            entity_rows.extend(folded.entities)
            bucket_rows.extend(folded.buckets)
            metric_rows.extend(folded.metrics)
            report.entities_admitted += folded.admitted
            report.entities_reactivated += folded.reactivated
            report.entities_retired += folded.retired

        if entity_rows:
            entity_insert = pg_insert(cast(Table, IpsEntity.__table__))
            await self.session.execute(
                entity_insert.on_conflict_do_update(
                    constraint="uq_ips_entity_natural_key",
                    set_={
                        "display_name": func.coalesce(
                            entity_insert.excluded.display_name, IpsEntity.display_name
                        ),
                        "first_seen_at": func.least(
                            entity_insert.excluded.first_seen_at, IpsEntity.first_seen_at
                        ),
                        "last_seen_at": func.greatest(
                            entity_insert.excluded.last_seen_at, IpsEntity.last_seen_at
                        ),
                        "active": entity_insert.excluded.active,
                        "retired_at": entity_insert.excluded.retired_at,
                        "normalizer_version": entity_insert.excluded.normalizer_version,
                    },
                ),
                entity_rows,
            )
        if bucket_rows:
            bucket_insert = pg_insert(cast(Table, IpsSourceBucket.__table__))
            await self.session.execute(
                bucket_insert.on_conflict_do_nothing(
                    index_elements=["log_source_id", "bucket_start"]
                ),
                bucket_rows,
            )
        if metric_rows:
            metric_insert = pg_insert(cast(Table, IpsEntityMetric.__table__))
            await self.session.execute(
                metric_insert.on_conflict_do_nothing(
                    index_elements=["ips_entity_id", "bucket_start"]
                ),
                metric_rows,
            )
        report.history_buckets_written += len(bucket_rows)

    async def _write_handoff_watermarks(
        self,
        instance: QRadarInstance,
        sources: dict[int, LogSource],
        handoffs: dict[int, bool],
        *,
        boundary: datetime,
        now: datetime,
        interval: int,
    ) -> None:
        """Seed both source watermarks in one executemany statement."""
        from app.services.ips_pipeline import evaluation_collector

        rows: list[dict[str, object]] = []
        for qradar_id, settled in handoffs.items():
            if qradar_id not in sources:
                continue
            watermark = boundary - timedelta(seconds=interval) if settled else None
            for collector in (
                watermark_collector(qradar_id),
                evaluation_collector(qradar_id),
            ):
                rows.append(
                    {
                        "id": uuid.uuid4(),
                        "instance_id": instance.id,
                        "collector": collector,
                        "watermark_at": watermark,
                        "last_run_at": now,
                        "lag_seconds": None,
                        "intervals_collected": 0,
                        "consecutive_failures": 0,
                        "last_error": None,
                        "collection_metadata": {
                            "history_handoff": True,
                            "history_end": boundary.isoformat() if settled else None,
                        },
                    }
                )
        if not rows:
            return
        insert = pg_insert(cast(Table, CollectionWatermark.__table__))
        await self.session.execute(
            insert.on_conflict_do_update(
                constraint="uq_collection_watermark_key",
                set_={
                    "watermark_at": insert.excluded.watermark_at,
                    "last_run_at": insert.excluded.last_run_at,
                    "lag_seconds": insert.excluded.lag_seconds,
                    "consecutive_failures": 0,
                    "last_error": None,
                    "collection_metadata": insert.excluded.collection_metadata,
                },
            ),
            rows,
        )

    async def _search(
        self,
        sources: Sequence[_Source],
        bucket_seconds: int,
        budget: IpsSearchBudget,
        *,
        provider: QRadarProvider | None = None,
    ) -> _SearchResult:
        search_provider = provider or self.provider
        totals_witness: _LiveTotalsFailure | None = None
        if not isinstance(search_provider, _HistoryProvider):
            totals_witness = _LiveTotalsFailure(search_provider)
            search_provider = cast(QRadarProvider, totals_witness)
        by_window: dict[tuple[datetime, datetime], list[_Source]] = defaultdict(list)
        for source in sources:
            by_window[source.window].append(source)
        out: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome] = {}
        blocked_reasons: dict[int, str] = {}
        blocked_windows: list[dict[str, object]] = []
        for window in sorted(by_window):
            members = by_window[window]
            mapping_by_id = {
                member.log_source.qradar_id: member.mapping for member in members
            }
            plan = plan_searches(
                mapping_by_id.items(),
                max_sources_per_search=self.settings.ips_max_sources_per_search,
            )
            searches_required = len(plan)
            if searches_required > budget.remaining:
                if searches_required > budget.max_searches:
                    reason = (
                        f"plan needs {searches_required} searches; per-tick budget is "
                        f"{budget.max_searches}"
                    )
                    logger.warning(
                        "IPS collection window cannot fit the per-tick search budget: %s; "
                        "raise search_cadence_max_searches_per_tick to at least %d",
                        reason,
                        searches_required,
                    )
                    blocked_reasons.update(
                        (member.log_source.qradar_id, reason) for member in members
                    )
                    blocked_windows.append(
                        {
                            "window_start": window[0].isoformat(),
                            "window_end": window[1].isoformat(),
                            "source_count": len(members),
                            "searches_required": searches_required,
                            "per_tick_budget": budget.max_searches,
                            "reason": reason,
                            "remedy": (
                                "raise search_cadence_max_searches_per_tick to at least "
                                f"{searches_required}"
                            ),
                        }
                    )
                continue
            for search in plan:
                cells = await run_dimension(
                    search_provider,
                    search.dimension,
                    search.group,
                    window,
                    bucket_seconds,
                    max_rows=self.settings.ariel_multi_bucket_max_rows,
                    budget=budget,
                )
                if (
                    isinstance(search_provider, _HistoryProvider)
                    and search_provider.deferred is not None
                ):
                    # The keyed search turns a permit refusal into a failed cell
                    # and would continue with the next dimension. Yield on the
                    # first refusal so the step does not spend another wait.
                    raise search_provider.deferred
                for cell in cells:
                    out[(cell.qradar_log_source_id, cell.bucket_start, cell.dimension)] = cell
        return _SearchResult(
            out,
            blocked_reasons,
            blocked_windows,
            totals_witness.totals_errors if totals_witness is not None else {},
        )

    async def _persist(
        self,
        instance: QRadarInstance,
        sources: Sequence[_Source],
        outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
        watermarks: dict[int, CollectionWatermark],
        skipped_rows: list[dict[str, object]],
        now: datetime,
        bucket_seconds: int,
        report: IpsCollectionReport,
        budget_blocked_reasons: dict[int, str],
        totals_errors: dict[int, str] | None = None,
    ) -> None:
        if totals_errors is None:
            totals_errors = {}
        source_uuids = [item.log_source.id for item in sources]
        existing_rows = (
            (
                await self.session.scalars(
                    select(IpsEntity).where(
                        IpsEntity.log_source_id.in_(source_uuids),
                    )
                )
            ).all()
            if source_uuids
            else []
        )
        entities = {
            (row.log_source_id, str(row.dimension), row.value_key): row for row in existing_rows
        }
        active_counts: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for row in existing_rows:
            if row.active:
                active_counts[(row.log_source_id, str(row.dimension))] += 1

        entity_upserts: dict[tuple[uuid.UUID, str, str], dict[str, object]] = {}
        metric_rows: list[dict[str, object]] = []
        bucket_rows: list[dict[str, object]] = []
        collected_pairs: list[tuple[uuid.UUID, datetime]] = []
        watermark_values = list(skipped_rows)

        for item in sources:
            source = item.log_source
            cursor = item.window[0]
            last_done = (
                watermarks[source.qradar_id].watermark_at
                if source.qradar_id in watermarks
                else None
            )
            collected = 0
            totals_failed = False
            totals_error: str | None = None
            while cursor < item.window[1]:
                if any(
                    outcome is not None
                    and outcome.state is IpsCellState.NOT_COLLECTED
                    and outcome.provenance is None
                    for dimension in IpsDimension
                    if (
                        outcome := outcomes.get((source.qradar_id, cursor, dimension))
                    )
                    is not None
                ):
                    break
                totals_outcome = outcomes.get(
                    (source.qradar_id, cursor, IpsDimension.TOTALS)
                )
                if totals_outcome is None or totals_outcome.state is not IpsCellState.COMPLETE:
                    # Class name only, captured beside the live search. A budget
                    # stop (NOT_COLLECTED) and a missing cell leave the marker.
                    if totals_outcome is not None and totals_outcome.state in (
                        IpsCellState.FAILED,
                        IpsCellState.REJECTED,
                    ):
                        totals_failed = True
                        totals_error = (
                            totals_errors.get(source.qradar_id) or totals_outcome.state.value
                        )
                    break
                totals = fold_outcome(
                    totals_outcome,
                    mapping=item.mapping,
                    max_entities=self.settings.ips_max_entities_per_bucket,
                )
                if not isinstance(totals, IpsTotalsFact):
                    break
                bucket_values, new_metrics = self._bucket_values(
                    instance,
                    item,
                    cursor,
                    bucket_seconds,
                    totals,
                    outcomes,
                    entities,
                    active_counts,
                    entity_upserts,
                    now,
                    report,
                )
                bucket_rows.append(bucket_values)
                metric_rows.extend(new_metrics)
                collected_pairs.append((source.id, cursor))
                last_done = cursor
                collected += 1
                cursor += timedelta(seconds=bucket_seconds)
            if collected:
                report.sources_collected += 1
                report.buckets_written += collected
                report.watermarks_advanced += 1
            prior = watermarks.get(source.qradar_id)
            # A complete window clears a previous totals failure. A totals
            # failure records the class and counts the streak. Anything else
            # (a budget stop, a partial window, a non-totals failure) keeps
            # the marker the row already has.
            window_done = collected > 0 and cursor >= item.window[1]
            if totals_failed:
                stored_error: str | None | object = totals_error
                stored_failures: int | None = (
                    (prior.consecutive_failures if prior is not None else 0) + 1
                )
            elif window_done:
                stored_error = None
                stored_failures = 0
            else:
                stored_error = _PRESERVE_ERROR
                stored_failures = None
            watermark_values.append(
                self._watermark_values(
                    instance,
                    source,
                    prior,
                    now=now,
                    watermark_at=last_done,
                    intervals_added=collected,
                    searches=report.searches_sent,
                    budget_blocked_reason=budget_blocked_reasons.get(source.qradar_id),
                    last_error=stored_error,
                    consecutive_failures=stored_failures,
                )
            )

        if collected_pairs:
            condition = tuple_(IpsEntity.log_source_id, IpsEntityMetric.bucket_start).in_(
                collected_pairs
            )
            await self.session.execute(
                delete(IpsEntityMetric)
                .where(
                    exists(
                        select(1).where(
                            IpsEntity.id == IpsEntityMetric.ips_entity_id,
                            condition,
                        )
                    )
                )
                .execution_options(synchronize_session=False)
            )
        if entity_upserts:
            values = list(entity_upserts.values())
            statement = pg_insert(cast(Table, IpsEntity.__table__))
            statement = statement.on_conflict_do_update(
                constraint="uq_ips_entity_natural_key",
                set_={
                    "display_name": func.coalesce(
                        statement.excluded.display_name, IpsEntity.display_name
                    ),
                    "last_seen_at": func.greatest(
                        statement.excluded.last_seen_at, IpsEntity.last_seen_at
                    ),
                    "active": True,
                    "retired_at": None,
                    "normalizer_version": statement.excluded.normalizer_version,
                },
            )
            await self.session.execute(statement, values)
        if metric_rows:
            statement = pg_insert(cast(Table, IpsEntityMetric.__table__))
            statement = statement.on_conflict_do_update(
                index_elements=["ips_entity_id", "bucket_start"],
                set_={
                    "events": statement.excluded.events,
                    "bucket_seconds": statement.excluded.bucket_seconds,
                },
            )
            await self.session.execute(statement, metric_rows)
            report.entity_rows_written = len(metric_rows)
        if bucket_rows:
            statement = pg_insert(cast(Table, IpsSourceBucket.__table__))
            statement = statement.on_conflict_do_update(
                index_elements=["log_source_id", "bucket_start"],
                set_={
                    key: statement.excluded[key]
                    for key in bucket_rows[0]
                    # A live write never changes the marker of an existing row.
                    if key not in ("log_source_id", "bucket_start", "origin")
                },
            )
            await self.session.execute(statement, bucket_rows)
        if watermark_values:
            statement = pg_insert(cast(Table, CollectionWatermark.__table__))
            statement = statement.on_conflict_do_update(
                constraint="uq_collection_watermark_key",
                set_={
                    "watermark_at": statement.excluded.watermark_at,
                    "last_run_at": statement.excluded.last_run_at,
                    "lag_seconds": statement.excluded.lag_seconds,
                    "intervals_collected": statement.excluded.intervals_collected,
                    "consecutive_failures": statement.excluded.consecutive_failures,
                    "last_error": statement.excluded.last_error,
                    "collection_metadata": statement.excluded.collection_metadata,
                },
            )
            await self.session.execute(statement, watermark_values)
        if source_uuids:
            cutoff = now - timedelta(days=self.settings.ips_entity_active_days)
            retired = await self.session.execute(
                update(IpsEntity)
                .where(
                    IpsEntity.log_source_id.in_(source_uuids),
                    IpsEntity.active.is_(True),
                    IpsEntity.last_seen_at < cutoff,
                )
                .values(active=False, retired_at=now)
                .returning(IpsEntity.id)
                .execution_options(synchronize_session=False)
            )
            report.entities_retired = len(retired.all())

    def _bucket_values(
        self,
        instance: QRadarInstance,
        item: _Source,
        bucket_start: datetime,
        bucket_seconds: int,
        totals: IpsTotalsFact,
        outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
        entities: dict[tuple[uuid.UUID, str, str], IpsEntity],
        active_counts: dict[tuple[uuid.UUID, str], int],
        entity_upserts: dict[tuple[uuid.UUID, str, str], dict[str, object]],
        now: datetime,
        report: IpsCollectionReport,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        source = item.log_source
        measurements: dict[IpsDimension, str] = {}
        facts: dict[IpsDimension, object | None] = {}
        provenance: dict[str, object] = {}
        for dimension in IpsDimension:
            outcome = outcomes.get((source.qradar_id, bucket_start, dimension))
            if outcome is None:
                if dimension is not IpsDimension.TOTALS:
                    measurements[dimension] = IpsCellState.NOT_COLLECTED.value
                facts[dimension] = None
                continue
            if dimension is not IpsDimension.TOTALS:
                measurements[dimension] = (
                    IpsMeasurement.EXACT.value
                    if outcome.state is IpsCellState.COMPLETE
                    else outcome.state.value
                )
            facts[dimension] = fold_outcome(
                outcome,
                mapping=item.mapping,
                max_entities=self.settings.ips_max_entities_per_bucket,
            )
            if outcome.provenance is not None:
                p = outcome.provenance
                provenance[dimension.value] = {
                    "search_window_start": p.search_window_start.isoformat(),
                    "search_window_end": p.search_window_end.isoformat(),
                    "search_bucket_count": p.search_bucket_count,
                    "search_source_count": p.search_source_count,
                    "split_depth": p.split_depth,
                    "bucket_key": p.bucket_key,
                }

        metrics: list[dict[str, object]] = []
        admitted: dict[str, int] = {}
        below: dict[str, int] = {}
        rejected: dict[str, int] = {}
        untracked: dict[str, int] = {}
        untracked_events: dict[str, int] = {}
        admission_truncated: dict[str, bool] = {}
        policy = AdmissionPolicy(
            max_new_per_bucket=self.settings.ips_max_new_entities_per_bucket,
            max_active=self.settings.ips_max_active_entities_per_source_dimension,
            min_requests=self.settings.ips_min_admission_events,
        )
        for dimension in ENTITY_DIMENSIONS:
            fact = facts.get(dimension)
            if not isinstance(fact, IpsEntityBucketFact):
                continue
            existing_active: list[tuple[IpsEntityFact, uuid.UUID]] = []
            unseen: list[_Candidate] = []
            for entity_fact in fact.entities:
                key = (source.id, dimension.value, entity_fact.value_key)
                current = entities.get(key)
                if current is not None and current.active:
                    existing_active.append((entity_fact, current.id))
                else:
                    unseen.append(_Candidate(entity_fact, dimension))
            decision = decide_admission(
                unseen,
                active_count=active_counts[(source.id, dimension.value)],
                policy=policy,
            )
            overflow_count = max(0, fact.entity_count - len(fact.entities))
            overflow_events = max(
                0,
                fact.dimension_events
                - fact.unattributed_events
                - sum(entity.events for entity in fact.entities),
            )
            admitted[dimension.value] = len(decision.admitted)
            below[dimension.value] = len(decision.below_threshold)
            rejected[dimension.value] = len(decision.over_quota)
            untracked[dimension.value] = len(decision.untracked) + overflow_count
            untracked_events[dimension.value] = decision.untracked_requests + overflow_events
            admission_truncated[dimension.value] = (
                fact.measurement is IpsMeasurement.LOWER_BOUND
                or decision.truncated
                or overflow_count > 0
            )
            for entity_fact, entity_id in existing_active:
                self._queue_entity(
                    instance, source, dimension, entity_fact, entity_id,
                    bucket_start, entity_upserts,
                )
                metrics.append(
                    self._metric_values(entity_id, bucket_start, bucket_seconds, entity_fact.events)
                )
            for candidate in decision.admitted:
                entity_fact = candidate.fact
                key = (source.id, dimension.value, entity_fact.value_key)
                current = entities.get(key)
                entity_id = current.id if current is not None else uuid.uuid4()
                if current is not None:
                    # The bulk upsert performs the database mutation. Marking
                    # these as committed only keeps later buckets in this run
                    # from re-admitting the same entity through an ORM UPDATE.
                    set_committed_value(current, "active", True)
                    set_committed_value(current, "retired_at", None)
                    report.entities_reactivated += 1
                else:
                    entities[key] = IpsEntity(
                        id=entity_id,
                        instance_id=instance.id,
                        log_source_id=source.id,
                        dimension=dimension,
                        value_key=entity_fact.value_key,
                        display_name=entity_fact.display_name,
                        first_seen_at=bucket_start,
                        last_seen_at=bucket_start,
                        active=True,
                        normalizer_version=IPS_NORMALIZER_VERSION,
                    )
                active_counts[(source.id, dimension.value)] += 1
                report.entities_admitted += 1
                self._queue_entity(
                    instance, source, dimension, entity_fact, entity_id,
                    bucket_start, entity_upserts,
                )
                metrics.append(
                    self._metric_values(entity_id, bucket_start, bucket_seconds, entity_fact.events)
                )

        def coverage_values(dimension: IpsDimension) -> tuple[float | None, bool | None]:
            fact = facts.get(dimension)
            coverage_fact = (
                fact
                if isinstance(fact, (IpsEntityBucketFact, IpsSourceIpFact, IpsActionFact))
                else None
            )
            measured = coverage(coverage_fact, totals)
            if measured is None:
                return None, None
            return min(1.0, max(0.0, measured.ratio)), measured.lower_bound

        source_fact = facts.get(IpsDimension.SOURCE_IP)
        fanout_fact = facts.get(IpsDimension.PORT_FANOUT)
        action_fact = facts.get(IpsDimension.ACTION)
        contributors: dict[str, object] = {}
        if isinstance(source_fact, IpsSourceIpFact):
            contributors[IpsDimension.SOURCE_IP.value] = [
                {"source_ip": address, "events": events}
                for address, events in source_fact.contributors
            ]
        if isinstance(fanout_fact, IpsFanoutFact):
            contributors[IpsDimension.PORT_FANOUT.value] = [
                {"source_ip": address, "distinct_ports": ports}
                for address, ports in fanout_fact.contributors
            ]
        row: dict[str, object] = {
            "log_source_id": source.id,
            "bucket_start": bucket_start,
            "bucket_seconds": bucket_seconds,
            **marker_values(HistoryFamily.IPS, HistoryOrigin.LIVE),
            "total_events": totals.events,
            "total_records": totals.records,
            "port_fanout_measurement": measurements[IpsDimension.PORT_FANOUT],
            "contributors": contributors,
            "admitted": admitted,
            "below_admission_threshold": below,
            "rejected_over_quota": rejected,
            "untracked_entities": untracked,
            "untracked_events": untracked_events,
            "admission_truncated": admission_truncated,
            "profile_version": item.profile.profile_version,
            "normalizer_version": IPS_NORMALIZER_VERSION,
            "query_provenance": provenance,
            "in_maintenance": in_maintenance(
                bucket_start,
                maintenance_mode=source.maintenance_mode,
                maintenance_until=source.maintenance_until,
            ),
            "collected_at": now,
            "source_events": (
                source_fact.source_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "distinct_sources": (
                source_fact.distinct_sources
                if isinstance(source_fact, IpsSourceIpFact)
                else None
            ),
            "distinct_sources_lower_bound": (
                source_fact.measurement is IpsMeasurement.LOWER_BOUND
                if isinstance(source_fact, IpsSourceIpFact)
                else None
            ),
            "top1_events": (
                source_fact.top1_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "top5_events": (
                source_fact.top5_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "max_distinct_ports": (
                fanout_fact.max_distinct_ports if isinstance(fanout_fact, IpsFanoutFact) else None
            ),
            "blocked_events": (
                action_fact.blocked_events if isinstance(action_fact, IpsActionFact) else None
            ),
            "allowed_events": (
                action_fact.allowed_events if isinstance(action_fact, IpsActionFact) else None
            ),
            "unmapped_events": (
                action_fact.unmapped_events if isinstance(action_fact, IpsActionFact) else None
            ),
        }
        for dimension, prefix in (
            (IpsDimension.DESTINATION_PORT, "destination_port"),
            (IpsDimension.DESTINATION_IP, "destination_ip"),
            (IpsDimension.SIGNATURE, "signature"),
            (IpsDimension.SOURCE_IP, "source_ip"),
            (IpsDimension.ACTION, "action"),
        ):
            ratio, lower = coverage_values(dimension)
            row[f"{prefix}_measurement"] = measurements[dimension]
            row[f"{prefix}_coverage_ratio"] = ratio
            row[f"{prefix}_coverage_lower_bound"] = lower
        return row, metrics

    @staticmethod
    def _metric_values(
        entity_id: uuid.UUID, bucket_start: datetime, bucket_seconds: int, events: int
    ) -> dict[str, object]:
        return {
            "ips_entity_id": entity_id,
            "bucket_start": bucket_start,
            "events": events,
            "bucket_seconds": bucket_seconds,
        }

    @staticmethod
    def _queue_entity(
        instance: QRadarInstance,
        source: LogSource,
        dimension: IpsDimension,
        fact: IpsEntityFact,
        entity_id: uuid.UUID,
        bucket_start: datetime,
        rows: dict[tuple[uuid.UUID, str, str], dict[str, object]],
    ) -> None:
        key = (source.id, dimension.value, fact.value_key)
        previous = rows.get(key)
        first_seen = previous["first_seen_at"] if previous is not None else bucket_start
        rows[key] = {
            "id": entity_id,
            "instance_id": instance.id,
            "log_source_id": source.id,
            "dimension": dimension.value,
            "value_key": fact.value_key,
            "display_name": fact.display_name,
            "first_seen_at": first_seen,
            "last_seen_at": bucket_start,
            "active": True,
            "retired_at": None,
            "normalizer_version": IPS_NORMALIZER_VERSION,
        }

    @staticmethod
    def _watermark_values(
        instance: QRadarInstance,
        source: LogSource,
        existing: CollectionWatermark | None,
        *,
        now: datetime,
        watermark_at: datetime | None = None,
        intervals_added: int = 0,
        searches: int = 0,
        skip_reason: str | None = None,
        budget_blocked_reason: str | None = None,
        last_error: str | None | object = _PRESERVE_ERROR,
        consecutive_failures: int | None = None,
    ) -> dict[str, object]:
        current = (
            watermark_at
            if watermark_at is not None
            else (existing.watermark_at if existing else None)
        )
        metadata = {
            "last_run_searches": searches,
            **({"last_skip_reason": skip_reason} if skip_reason is not None else {}),
            **(
                {"budget_blocked_reason": budget_blocked_reason}
                if budget_blocked_reason is not None
                else {}
            ),
        }
        lag = None
        if current is not None:
            lag = max(0, int((now - current).total_seconds()))
        if isinstance(last_error, str) or last_error is None:
            stored_error: str | None = last_error
        else:
            stored_error = existing.last_error if existing else None
        if consecutive_failures is None:
            stored_failures = existing.consecutive_failures if existing else 0
        else:
            stored_failures = consecutive_failures
        return {
            "id": existing.id if existing is not None else uuid.uuid4(),
            "instance_id": instance.id,
            "collector": watermark_collector(source.qradar_id),
            "watermark_at": current,
            "last_run_at": now,
            "lag_seconds": lag,
            "intervals_collected": (existing.intervals_collected if existing else 0)
            + intervals_added,
            "consecutive_failures": stored_failures,
            "last_error": stored_error,
            "collection_metadata": metadata,
        }
