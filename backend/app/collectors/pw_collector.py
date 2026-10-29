"""Property-watch collection with bounded admission and bulk persistence (design §G.4-§G.7).

One run plans every due watch of an instance together, under one `PwSearchBudget`. Per
watch it resolves the scope from the inventory read once for all watches, runs the grouped,
totals and (when values are pinned) pinned searches per scope chunk, folds each collected
bucket, applies admission and persists. Raw values live only inside the pure fold and
admission; what is written is counts, the bounded provenance and the value texts that
were admitted or pinned (AGENTS.md rule 3).

The watermark is `property_watch.collected_through`. It advances only past a contiguous
run of buckets that completed -- the grouped and totals searches (and the pinned search,
when values are pinned) all measured. The first bucket that did not complete holds it; when
a search failed, that bucket is stored `NOT_COLLECTED` so the gap is visible and a failed
search is never a zero. A bucket that was only not searched (the budget was spent) is
simply left for the next tick. **The first run seeds the watermark at the latest closed
bucket and replays nothing** (docs/SEARCH-CADENCE.md §SC.6).

The statements of a run are constant in the number of watches, values and buckets: the
reads are bulk (watches, groups, sources, values, scope history) and each table gets one
write (design §G.14.2, AGENTS.md rule 9). Evaluation and training are PW-3b's.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.anomaly.seasonal import in_maintenance
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.history.origin import HistoryOrigin, is_live_clause, marker_values
from app.history.planner import (
    ChunkResult,
    PlannerSettings,
    ScanOutcome,
    ScanSpanResult,
    ScopeProgress,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    interpret_scan,
)
from app.history.progress_codec import ProgressCodecError, decode_progress, encode_progress
from app.history.state import HistoryFamily, HistoryTrigger
from app.i18n.messages import MessageRef, render
from app.models.enums import (
    PwBucketMeasurement,
    PwHistoryState,
    PwPinnedMeasurement,
    PwScopeKind,
    PwWatchStatus,
    SearchFamily,
)
from app.models.instance import QRadarInstance
from app.models.property_watch import PropertyWatch, PwBucket, PwValue
from app.property_watch.admission import (
    AdmittedBucket,
    PwAdmissionPolicy,
    TrackedValue,
    admit_bucket,
    components_of,
    retire_other_versions,
    retire_stale,
    sync_pins,
)
from app.property_watch.aql import pinned_rows_per_bucket, validate_query_inputs
from app.property_watch.bootstrap import (
    UNREADABLE_PROGRESS,
    WAITING_FOR_BUDGET,
    BootstrapStateError,
    apply_trigger,
    begin_reading,
    handoff_columns,
    history_budget_sentence,
    history_columns,
    history_step_searches,
    plan_pw_step,
    pw_history_budget,
    pw_planner_settings,
    read_progress,
    restart_columns,
    settle_pw_step,
    start_progress,
)
from app.property_watch.facts import PwBucketFact
from app.property_watch.fold import component_limit, fold_bucket, pinned_value_keys
from app.property_watch.keyed_search import (
    PwBucketOutcome,
    PwCellState,
    PwSearchBudget,
    PwSearchPlan,
    combine_chunks,
    run_search,
)
from app.property_watch.reference import validate_pinned_values, validate_properties
from app.property_watch.scope import (
    ResolvedScope,
    ScopeRecord,
    SourceRow,
    resolve_scope,
    scope_changed_flags,
)
from app.providers.base import QRadarProvider
from app.providers.dto import PwSearchKind
from app.repositories.property_watch import PropertyWatchRepository
from app.services.ariel_permit import (
    ArielPermitUnavailable,
    ariel_background_priority,
    ariel_history_deferred_reason,
)
from app.services.search_cadence import SearchCadenceService

logger = logging.getLogger("app.collectors.pw_collector")

#: The statuses collection itself sets, and so the only ones it clears again.
_COLLECTION_STATUSES = frozenset(
    {
        PwWatchStatus.SCOPE_EMPTY.value,
        PwWatchStatus.SCOPE_STALE.value,
        PwWatchStatus.SCOPE_TOO_LARGE.value,
        PwWatchStatus.NOT_APPLICABLE.value,
        PwWatchStatus.STALE.value,
        # Set by the history reading, which collection owns, and cleared at the hand-off.
        PwWatchStatus.READING_HISTORY.value,
    }
)

#: `NOT_APPLICABLE` needs this many consecutive collected buckets on which the property
#: parsed on no event, counted across ticks, whatever the cadence.
_NOT_APPLICABLE_MIN_BUCKETS = 12
_NOT_APPLICABLE_MESSAGE = MessageRef("pw.status.not_applicable")
_NOT_APPLICABLE_REASON = render(_NOT_APPLICABLE_MESSAGE)


@dataclass
class PwWatchReport:
    watch_id: uuid.UUID
    #: `collected`, `held`, `scope`, `nothing_to_collect` or `failed`.
    status: str
    buckets_written: int = 0
    searches_sent: int = 0
    reason: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "watch_id": str(self.watch_id),
            "status": self.status,
            "buckets_written": self.buckets_written,
            "searches_sent": self.searches_sent,
            "reason": self.reason,
        }


@dataclass
class PwCollectionReport:
    instance_id: uuid.UUID
    status: str = "ok"
    watches_enabled: int = 0
    watches_due: int = 0
    #: Watches whose history was read (or waited to be read) in this tick.
    watches_reading: int = 0
    searches_sent: int = 0
    #: The part of `searches_sent` that history steps spent (live goes first).
    history_searches: int = 0
    buckets_written: int = 0
    metric_rows_written: int = 0
    values_written: int = 0
    values_admitted: int = 0
    values_retired: int = 0
    watermarks_advanced: int = 0
    watches: list[PwWatchReport] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "watches_enabled": self.watches_enabled,
            "watches_due": self.watches_due,
            "watches_reading": self.watches_reading,
            "searches_sent": self.searches_sent,
            "history_searches": self.history_searches,
            "buckets_written": self.buckets_written,
            "metric_rows_written": self.metric_rows_written,
            "values_written": self.values_written,
            "values_admitted": self.values_admitted,
            "values_retired": self.values_retired,
            "watermarks_advanced": self.watermarks_advanced,
            "results": [item.as_dict() for item in self.watches],
        }


@dataclass
class _Plan:
    watch: PropertyWatch
    index: int
    window: tuple[datetime, datetime]
    scope: ResolvedScope | None = None


@dataclass
class _Staged:
    admitted: int = 0
    retired: int = 0


@dataclass
class _Writes:
    """The rows one tick writes, gathered across watches before any statement is sent."""

    values: list[dict[str, Any]] = field(default_factory=list)
    buckets: list[dict[str, Any]] = field(default_factory=list)
    metrics: list[dict[str, Any]] = field(default_factory=list)
    watches: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class _Reading:
    """A watch whose history is read in this tick (PHASE-G §H.4.4)."""

    watch: PropertyWatch
    #: Where to continue; None until the reading has a progress (a restart clears it).
    progress: ScopeProgress | None
    #: Columns staged before the step: a begin or a version restart (§H.5.4).
    columns: dict[str, Any]
    #: The stored progress did not read back: the watch is handed off `PARTIAL`.
    unreadable: bool = False


class _StepBudget(PwSearchBudget):
    """What one history step may send: its share of the tick, and the tick's time budget.

    Every search it allows is also taken from the tick's `PwSearchBudget`, so live collection
    (which ran first) and the history steps never spend more than the one knob (§H.4.4).
    No new search starts once `pw_bootstrap_seconds_per_tick` have passed.
    """

    def __init__(
        self,
        parent: PwSearchBudget,
        limit: int,
        *,
        deadline: float,
        monotonic: Callable[[], float],
    ) -> None:
        super().__init__(max(0, limit))
        self._parent = parent
        self._deadline = deadline
        self._monotonic = monotonic

    def try_take(self, watch: int | None = None) -> bool:
        if self._monotonic() >= self._deadline or self.used >= self.max_searches:
            return False
        if not self._parent.try_take():
            return False
        self.used += 1
        return True

    def refund_refused_search(self) -> None:
        """A permit refusal spent neither the step nor the tick's search allowance."""

        if self.used < 1 or self._parent.used < 1:
            raise RuntimeError("cannot refund a search the history budget did not take")
        self.used -= 1
        self._parent.used -= 1


class _HistoryProvider:
    """Remember the typed capacity refusal a keyed-search driver renders as FAILED."""

    def __init__(self, provider: QRadarProvider) -> None:
        self.provider = provider
        self.deferred: ArielPermitUnavailable | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self.provider, name)

    async def get_pw_keyed(self, *args: Any, **kwargs: Any) -> Any:
        try:
            return await self.provider.get_pw_keyed(*args, **kwargs)
        except ArielPermitUnavailable as exc:
            self.deferred = exc
            raise


def _stored_version(watch: PropertyWatch) -> int | None:
    """The `watch_version` the stored progress was read for (its adapter object)."""
    try:
        adapter = decode_progress(watch.history_progress).adapter
    except ProgressCodecError:
        return None
    version = adapter.get("watch_version")
    return version if isinstance(version, int) and not isinstance(version, bool) else None


def _stamp(
    columns: dict[str, Any], progress: ScopeProgress | None, *, reason: str, version: int
) -> dict[str, Any]:
    """The progress JSON of `columns` with the watch version it was read for: the version
    restart (§H.5.4) compares it with the watch's, and only the collector writes it."""
    if progress is not None:
        columns["history_progress"] = encode_progress(
            progress, adapter={"reason": reason, "watch_version": version}
        )
    return columns


def _wide_status(outcomes: Sequence[PwBucketOutcome], searches: int) -> SearchStatus:
    """What a span-wide search (TOTALS, PINNED, a scan) established, for the planner.

    Whole only if every bucket is `COMPLETE`. A bucket cut at one bucket is a lower bound,
    which is no total; a search that was only partly sent is `TRUNCATED` (its searches are
    spent), one never sent is `NOT_RUN`.
    """
    states = {o.state for o in outcomes}
    if PwCellState.FAILED in states:
        return SearchStatus.FAILED
    if states == {PwCellState.COMPLETE}:
        return SearchStatus.COMPLETE
    if PwCellState.NOT_COLLECTED in states and searches == 0:
        return SearchStatus.NOT_RUN
    return SearchStatus.TRUNCATED


def _chunk_result(span: Span, outcomes: Sequence[PwBucketOutcome], searches: int) -> ChunkResult:
    """One GROUPED chunk for the planner, with the searches it really spent (splits included).

    `run_search` already split a cut chunk in time, so a chunk whose every bucket was
    measured is `COMPLETE`, with the buckets it could only keep as lower bounds counted. A
    failed bucket fails the chunk. A chunk the budget cut short is `TRUNCATED` (it keeps
    nothing; the width halves) when it spent searches, `NOT_RUN` when it spent none.
    """
    states = {o.state for o in outcomes}
    if PwCellState.FAILED in states:
        return ChunkResult(span, SearchStatus.FAILED, searches=searches)
    if PwCellState.NOT_COLLECTED in states:
        status = SearchStatus.TRUNCATED if searches else SearchStatus.NOT_RUN
        return ChunkResult(span, status, searches=searches)
    return ChunkResult(
        span,
        SearchStatus.COMPLETE,
        rows=sum(len(o.rows) for o in outcomes),
        non_silent_buckets=sum(1 for o in outcomes if o.rows),
        lower_bound_buckets=sum(1 for o in outcomes if o.state is PwCellState.LOWER_BOUND),
        searches=searches,
    )


def _set_reason(row: dict[str, Any], message: MessageRef | None) -> None:
    """The status sentence of a watch row and its message reference, written together.

    The text and the reference are one fact: a row never keeps the reference of an earlier
    sentence beside a new text, and `None` clears both.
    """
    row["last_status_reason"] = None if message is None else render(message)
    row["last_status_reason_i18n"] = None if message is None else message.to_wire()


def _set_scope_reason(row: dict[str, Any], scope: ResolvedScope) -> None:
    """The reason an unresolved scope gives, with its reference."""
    row["last_status_reason"] = scope.reason
    row["last_status_reason_i18n"] = None if scope.message is None else scope.message.to_wire()


def _days_message(
    progress: ScopeProgress, boundary: datetime, settings: PlannerSettings
) -> MessageRef:
    """The status sentence, "n of m days": from the floor (or the asked start), `through`
    and the boundary. Never an ETA (§H.5.1)."""
    start = progress.floor or progress.target_start
    total = max(0.0, (boundary - start).total_seconds() / 86400)
    done = 0.0
    if progress.through is not None:
        done = max(0.0, (progress.through + settings.bucket - start).total_seconds() / 86400)
    return MessageRef(
        "pw.status.reading_history", {"done": min(done, total), "total": total}
    )


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _tracked(row: PwValue, property_count: int) -> TrackedValue:
    return TrackedValue(
        id=row.id,
        value_key=row.value_key,
        components=components_of(row.value_key, property_count),
        pinned=row.pinned,
        active=row.active,
        first_seen_at=_aware(row.first_seen_at),
        last_seen_at=_aware(row.last_seen_at),
        tracked_since=_aware(row.tracked_since),
        tracked_until=_aware(row.tracked_until),
        retired_at=_aware(row.retired_at),
        truncated=row.truncated,
    )


def _value_row(watch_id: uuid.UUID, version: int, value: TrackedValue) -> dict[str, Any]:
    components = value.components
    return {
        "id": value.id,
        "watch_id": watch_id,
        "watch_version": version,
        "value_key": value.value_key,
        "component_0": components[0] if components else None,
        "component_1": components[1] if len(components) > 1 else None,
        "truncated": value.truncated,
        "pinned": value.pinned,
        "first_seen_at": value.first_seen_at,
        "last_seen_at": value.last_seen_at,
        "tracked_since": value.tracked_since,
        "tracked_until": value.tracked_until,
        "active": value.active,
        "retired_at": value.retired_at,
    }


def _provenance(outcome: PwBucketOutcome, chunks: int) -> dict[str, object] | None:
    p = outcome.provenance
    if p is None:
        return None
    return {
        "state": outcome.state.value,
        "search_window_start": p.search_window_start.isoformat(),
        "search_window_end": p.search_window_end.isoformat(),
        "search_bucket_count": p.search_bucket_count,
        "split_depth": p.split_depth,
        "chunks": chunks,
        "query": p.query,
    }


def _scope_in_maintenance(scope: ResolvedScope, bucket_start: datetime) -> bool:
    """Source maintenance, or every resolved group member; never a type scope (§G.10.1)."""
    if scope.scope_kind is PwScopeKind.LOG_SOURCE_TYPE or not scope.members:
        return False
    return all(
        in_maintenance(
            bucket_start,
            maintenance_mode=source.maintenance_mode,
            maintenance_until=_aware(source.maintenance_until),
        )
        for source in scope.members
    )


def _bucket_row(
    watch_id: uuid.UUID,
    fact: PwBucketFact,
    *,
    provenance: dict[str, object],
    collected_at: datetime,
    origin: HistoryOrigin = HistoryOrigin.LIVE,
) -> dict[str, Any]:
    return {
        "watch_id": watch_id,
        "bucket_start": fact.bucket_start,
        "bucket_seconds": fact.bucket_seconds,
        "watch_version": fact.watch_version,
        **marker_values(HistoryFamily.PROPERTY_WATCH, origin),
        "total_events": fact.total_events,
        "parsed_events": fact.parsed_events,
        "unparsed_events": fact.unparsed_events,
        "untracked_events": fact.untracked_events,
        "values_measurement": fact.values_measurement.value,
        "pinned_measurement": fact.pinned_measurement.value,
        "totals_complete": fact.totals_complete,
        "observed_values": fact.observed_values,
        "tracked_values_observed": fact.tracked_values_observed,
        "admitted_values": fact.admitted_values,
        "below_admission_threshold": fact.below_admission_threshold,
        "rejected_over_quota": fact.rejected_over_quota,
        "admission_truncated": fact.admission_truncated,
        "scope_member_count": fact.scope_member_count,
        "scope_fingerprint": fact.scope_fingerprint,
        "scope_changed": fact.scope_changed,
        "in_maintenance": fact.in_maintenance,
        "query_provenance": provenance,
        "collected_at": collected_at,
    }


def not_collected_fact(
    *, bucket_start: datetime, bucket_seconds: int, watch_version: int, pinned: bool
) -> PwBucketFact:
    """A bucket a search failed to measure: counts unknown, never zero."""
    return PwBucketFact(
        bucket_start=bucket_start,
        bucket_seconds=bucket_seconds,
        watch_version=watch_version,
        totals_complete=False,
        values_measurement=PwBucketMeasurement.NOT_COLLECTED,
        pinned_measurement=(
            PwPinnedMeasurement.NOT_COLLECTED if pinned else PwPinnedMeasurement.NOT_REQUESTED
        ),
    )


def collected_prefix(
    grouped: Sequence[PwBucketOutcome],
    totals: Sequence[PwBucketOutcome],
    pinned: Sequence[PwBucketOutcome] | None,
) -> int:
    """How many leading buckets completed: the watermark may advance past exactly these.

    A bucket completes when the grouped search measured it (whole, or cut at one bucket and
    stored as a lower bound), the totals search was whole, and the pinned search, when one
    was asked, measured it. Anything else is transient and holds the watermark.
    """
    count = 0
    for index, group in enumerate(grouped):
        total = totals[index]
        pin = pinned[index] if pinned is not None else None
        if not group.measured or total.state is not PwCellState.COMPLETE:
            break
        if pin is not None and not pin.measured:
            break
        count += 1
    return count


class PwCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        #: Seconds since an arbitrary point, for the history time budget of a tick.
        self._monotonic = monotonic or time.monotonic
        self.repo = PropertyWatchRepository(session)

    async def collect(self, instance: QRadarInstance) -> PwCollectionReport:
        report = PwCollectionReport(instance.id)
        if not self.settings.pw_collection_enabled:
            report.status = "disabled"
            return report
        tick_started = self._monotonic()
        now = self._clock()
        interval = self.settings.collection_interval_seconds
        step = timedelta(seconds=interval)
        watches = await self.repo.enabled_watches(instance.id)
        report.watches_enabled = len(watches)
        if not watches:
            return report
        # The latest closed bucket: its end plus the settle time is not after `now`.
        boundary = floor_to_interval(
            now - timedelta(seconds=self.settings.search_cadence_settle_seconds), interval
        )
        cadence = await SearchCadenceService(self.session, self.settings).get(
            SearchFamily.PROPERTY_WATCH
        )
        max_buckets = (
            max(1, cadence.cadence_seconds // interval)
            + self.settings.collection_max_backfill_intervals
        )
        planner = pw_planner_settings(self.settings, cadence_seconds=cadence.cadence_seconds)
        # Which watches read their past, which are live: from the loaded rows, no statement.
        live_watches, readings, staged = self._classify(
            watches, now=now, boundary=boundary, planner=planner
        )
        plans: list[_Plan] = []
        for watch in live_watches:
            through = _aware(watch.collected_through)
            # The first run starts at the latest closed bucket: nothing before it is replayed.
            start = boundary - step if through is None else through + step
            end = min(boundary, start + step * max_buckets)
            if start < end:
                plans.append(_Plan(watch, len(plans), (start, end)))
            else:
                report.watches.append(PwWatchReport(watch.id, "nothing_to_collect"))
        report.watches_due = len(plans)
        report.watches_reading = len(readings)
        if not plans and not readings:
            return report

        # ---- the bulk reads: constant in the number of watches and values
        involved = [p.watch for p in plans] + [r.watch for r in readings]
        whole_inventory = any(w.scope_kind != PwScopeKind.LOG_SOURCE.value for w in involved)
        sources = await self.repo.sources(
            instance.id,
            source_ids={w.log_source_id for w in involved if w.log_source_id is not None},
            whole_inventory=whole_inventory,
        )
        groups = (
            await self.repo.groups(instance.id)
            if any(w.scope_kind == PwScopeKind.LOG_SOURCE_GROUP.value for w in involved)
            else {}
        )
        value_rows = await self.repo.values([(w.id, w.watch_version) for w in involved])
        grace = self.settings.pw_scope_change_grace_buckets
        # The newest stored bucket of each watch: the live watermark, or a reading's `through`.
        newest: dict[uuid.UUID, datetime] = {}
        for plan in plans:
            if (through := _aware(plan.watch.collected_through)) is not None:
                newest[plan.watch.id] = through
        for entry in readings:
            if entry.progress is not None and entry.progress.through is not None:
                newest[entry.watch.id] = entry.progress.through
        history = await self.repo.scope_history(
            list(newest),
            depth=grace + 1,
            since=(min(newest.values()) - step * (grace + 2)) if newest else now,
        )

        live_known = [
            t
            for p in plans
            if (t := _aware(p.watch.collected_through)) is not None
        ]
        unparsed_before = await self._unparsed_streaks(
            [p.watch.id for p in plans if p.watch.collected_through is not None],
            since=(
                (min(live_known) - step * (_NOT_APPLICABLE_MIN_BUCKETS + 2))
                if live_known
                else now
            ),
        )

        budget = PwSearchBudget(
            self.settings.pw_max_searches_per_tick,
            max_per_watch=self.settings.pw_max_searches_per_watch_per_tick,
        )
        rows_by_watch: dict[uuid.UUID, list[PwValue]] = {}
        for row in value_rows:
            rows_by_watch.setdefault(row.watch_id, []).append(row)
        writes = _Writes()
        # Live first (§H.4.4): the live watches spend what they spend, as they always did.
        for plan in plans:
            watch_report = await self._collect_watch(
                plan,
                now=now,
                sources=sources,
                groups=groups,
                value_rows=rows_by_watch.get(plan.watch.id, []),
                history=history.get(plan.watch.id, []),
                budget=budget,
                writes=writes,
                report=report,
                unparsed_before=unparsed_before.get(plan.watch.id, 0),
                extra_columns=staged.get(plan.watch.id),
            )
            report.watches.append(watch_report)
        live_searches = budget.used
        # Then the watches that read history, oldest first, on what live left over.
        readings.sort(
            key=lambda e: (
                e.progress.started_at
                if e.progress is not None
                else (_aware(e.watch.history_started_at) or now),
                e.watch.created_at,
                str(e.watch.id),
            )
        )
        for offset, entry in enumerate(readings):
            report.watches.append(
                await self._read_history(
                    entry,
                    index=len(plans) + offset,
                    now=now,
                    boundary=boundary,
                    planner=planner,
                    tick_started=tick_started,
                    sources=sources,
                    groups=groups,
                    value_rows=rows_by_watch.get(entry.watch.id, []),
                    history=history.get(entry.watch.id, []),
                    budget=budget,
                    writes=writes,
                    report=report,
                )
            )
        report.searches_sent = budget.used
        report.history_searches = budget.used - live_searches
        # A staged change (a begin that switched reading off) that no live row carried.
        written = {row["watch_id"] for row in writes.watches}
        for watch_id, columns in staged.items():
            if watch_id not in written:
                loaded = next(w for w in watches if w.id == watch_id)
                writes.watches.append(
                    {
                        "watch_id": watch_id,
                        "collected_through": loaded.collected_through,
                        "last_status": loaded.last_status,
                        "last_status_reason": loaded.last_status_reason,
                        "last_status_reason_i18n": loaded.last_status_reason_i18n,
                        "last_run_at": now,
                        **columns,
                    }
                )
        await self._persist(writes, report)
        by_id = {w.id: w for w in watches}
        for update in writes.watches:
            # The loaded rows follow the bulk UPDATE, so the pipeline step after this one
            # reads the watermark and status this tick wrote.
            loaded = by_id[update["watch_id"]]
            for column, value in update.items():
                if column != "watch_id":
                    set_committed_value(loaded, column, value)
        return report

    # ------------------------------------------------------------ which watches read
    def _classify(
        self,
        watches: Sequence[PropertyWatch],
        *,
        now: datetime,
        boundary: datetime,
        planner: PlannerSettings,
    ) -> tuple[list[PropertyWatch], list[_Reading], dict[uuid.UUID, dict[str, Any]]]:
        """Split the loaded watches into live ones and those that read history (§H.4.4).

        * a `NOT_REQUESTED` watch that asked for days and has no stored bucket of its version
          (both watermarks NULL, §H.5.5) begins reading, or, with the switch off, is
          `PARTIAL` and live from this tick;
        * a watch whose stored progress was read for another `watch_version` restarts
          (§H.5.4 row 1; the API only bumps the version, the collector owns `history_*`);
        * every `READING` watch is skipped by the live plan.
        """
        live: list[PropertyWatch] = []
        readings: list[_Reading] = []
        staged: dict[uuid.UUID, dict[str, Any]] = {}
        for watch in watches:
            state = PwHistoryState(watch.history_state)
            days = watch.history_days
            asked = days is not None and days >= 1
            version = watch.watch_version
            if state is PwHistoryState.NOT_REQUESTED:
                if (
                    asked
                    and days is not None
                    and watch.collected_through is None
                    and watch.evaluated_through is None
                ):
                    begin = begin_reading(
                        history_days=days,
                        boundary=boundary,
                        now=now,
                        enabled=self.settings.pw_bootstrap_enabled,
                        settings=planner,
                    )
                    columns = _stamp(
                        begin.columns, begin.progress, reason=begin.reason, version=version
                    )
                    if begin.state is PwHistoryState.READING:
                        readings.append(_Reading(watch, begin.progress, columns))
                        continue
                    staged[watch.id] = columns
                live.append(watch)
                continue
            if state is PwHistoryState.READING:
                try:
                    # The protocol declares plain attributes; the mapped columns satisfy it.
                    current = read_progress(cast(Any, watch))
                except BootstrapStateError:
                    readings.append(_Reading(watch, None, {}, unreadable=True))
                    continue
                if current is None:
                    # A restart cleared the progress: start it (never `begin_reading`).
                    readings.append(_Reading(watch, None, {}))
                    continue
                stored = _stored_version(watch)
                if stored is None:
                    readings.append(_Reading(watch, None, {}, unreadable=True))
                    continue
                if stored != version and (restart := restart_columns(state, history_days=days)):
                    readings.append(_Reading(watch, None, restart))
                    continue
                readings.append(_Reading(watch, current[0], {}))
                continue
            # COMPLETE, PARTIAL, UNAVAILABLE: live, unless the version moved on.
            stored = _stored_version(watch)
            if asked and stored is not None and stored != version:
                restart = restart_columns(state, history_days=days)
                if restart:
                    readings.append(_Reading(watch, None, restart))
                    continue
            live.append(watch)
        return live, readings, staged

    # -------------------------------------------------------------- one watch
    async def _collect_watch(
        self,
        plan: _Plan,
        *,
        now: datetime,
        sources: Sequence[SourceRow],
        groups: dict[int, Any],
        value_rows: Sequence[PwValue],
        history: Sequence[ScopeRecord],
        budget: PwSearchBudget,
        writes: _Writes,
        report: PwCollectionReport,
        unparsed_before: int,
        extra_columns: dict[str, Any] | None = None,
    ) -> PwWatchReport:
        watch = plan.watch
        settings = self.settings
        kind = PwScopeKind(watch.scope_kind)
        status_row = {
            "watch_id": watch.id,
            "collected_through": watch.collected_through,
            "last_status": watch.last_status,
            "last_status_reason": watch.last_status_reason,
            "last_status_reason_i18n": watch.last_status_reason_i18n,
            "last_run_at": now,
            # Staged by the history classification (a reading switched off): rides along.
            **(extra_columns or {}),
        }
        scope = resolve_scope(
            kind,
            log_source_id=watch.log_source_id,
            type_id=watch.scope_type_id,
            group_id=watch.scope_group_id,
            groups=groups,
            sources=sources,
            now=now,
            stale_after=timedelta(hours=settings.pw_scope_stale_after_hours),
            max_sources=settings.pw_max_sources_per_search,
            max_chunks=settings.pw_max_chunks_per_watch,
        )
        plan.scope = scope
        if not scope.resolved:
            # No bucket and no zero: the watch takes the reason and the next tick retries.
            status_row["last_status"] = scope.status.value if scope.status else None
            _set_scope_reason(status_row, scope)
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "scope", reason=scope.reason)

        try:
            references = validate_properties(watch.properties)
            pinned = validate_pinned_values(
                watch.pinned_values,
                property_count=len(references),
                max_pinned=settings.pw_max_pinned_values,
            )
            properties = tuple((r.kind.value, r.name) for r in references)
            used_before = budget.used
            grouped, totals, pinned_out = await self._search(
                plan, scope, properties, pinned, budget
            )
            searches_sent = budget.used - used_before
            count = collected_prefix(grouped, totals, pinned_out)
            staged = self._fold_and_stage(
                plan,
                scope,
                grouped=grouped,
                totals=totals,
                pinned_out=pinned_out,
                pinned=pinned,
                property_count=len(references),
                count=count,
                value_rows=value_rows,
                history=history,
                now=now,
                writes=writes,
                status_row=status_row,
                unparsed_before=unparsed_before,
            )
        except ValueError as exc:
            # A corrupt stored watch: the class only, never the text (it may hold a value).
            logger.warning(
                "property-watch collection refused a watch",
                extra={"watch_id": str(watch.id), "error_class": type(exc).__name__},
            )
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "failed", reason=type(exc).__name__)
        writes.watches.append(status_row)
        report.values_admitted += staged.admitted
        report.values_retired += staged.retired
        if count:
            report.watermarks_advanced += 1
        return PwWatchReport(
            watch.id,
            "collected" if count == len(grouped) else "held",
            buckets_written=count,
            searches_sent=searches_sent,
            reason=None if count == len(grouped) else "a bucket did not complete",
        )

    # ------------------------------------------------------------ one watch's history
    async def _read_history(
        self,
        entry: _Reading,
        *,
        index: int,
        now: datetime,
        boundary: datetime,
        planner: PlannerSettings,
        tick_started: float,
        sources: Sequence[SourceRow],
        groups: dict[int, Any],
        value_rows: Sequence[PwValue],
        history: Sequence[ScopeRecord],
        budget: PwSearchBudget,
        writes: _Writes,
        report: PwCollectionReport,
    ) -> PwWatchReport:
        """One history step of one watch (PHASE-G §H.4.2): at most one floor scan and one
        read, planned by PW-13b and run through the live search driver and fold.

        Nothing is written here: the rows go into the tick's `_Writes`, one statement per
        table for every watch (§H.5.6), so a crash anywhere leaves `history_through` where
        the stored buckets are. The watch's `history_*` columns, and on a hand-off the two
        watermarks, are in the same row as its status.
        """
        watch = entry.watch
        settings = self.settings
        version = watch.watch_version
        status_row: dict[str, Any] = {
            "watch_id": watch.id,
            "collected_through": watch.collected_through,
            "last_status": watch.last_status,
            "last_status_reason": watch.last_status_reason,
            "last_status_reason_i18n": watch.last_status_reason_i18n,
            "last_run_at": now,
            **entry.columns,
        }

        def finish(
            progress: ScopeProgress | None,
            trigger: HistoryTrigger | None,
            reason: str,
            *,
            outcome: str,
            staged: _Staged | None = None,
            written: int = 0,
        ) -> PwWatchReport:
            if trigger is not None:
                state, _ = apply_trigger(PwHistoryState.READING, trigger)
                columns = handoff_columns(
                    progress,
                    state=state,
                    reason=reason,
                    boundary=boundary,
                    now=now,
                    settings=planner,
                )
                _stamp(columns, progress, reason=reason, version=version)
                if status_row["last_status"] in _COLLECTION_STATUSES:
                    status_row["last_status"] = None
                    _set_reason(status_row, None)
            else:
                assert progress is not None
                columns = history_columns(progress, state=PwHistoryState.READING, reason=reason)
                _stamp(columns, progress, reason=reason, version=version)
                status_row["last_status"] = PwWatchStatus.READING_HISTORY.value
                _set_reason(status_row, _days_message(progress, boundary, planner))
            status_row.update(columns)
            writes.watches.append(status_row)
            if staged is not None:
                report.values_admitted += staged.admitted
                report.values_retired += staged.retired
            return PwWatchReport(
                watch.id, outcome, buckets_written=written, searches_sent=spent[0], reason=reason
            )

        spent = [0]
        history_provider = _HistoryProvider(self.provider)

        def unreadable() -> PwWatchReport:
            """The stored progress does not read back: hand off `PARTIAL`, from the column."""
            through = _aware(watch.history_through)
            target = _aware(watch.history_target_start)
            began = _aware(watch.history_started_at)
            rebuilt = (
                ScopeProgress(
                    target_start=target,
                    started_at=began,
                    floor=_aware(watch.history_floor),
                    through=through,
                )
                if target is not None and began is not None
                else None
            )
            columns = handoff_columns(
                rebuilt,
                state=PwHistoryState.PARTIAL,
                reason=UNREADABLE_PROGRESS,
                boundary=boundary,
                now=now,
                settings=planner,
            )
            if rebuilt is None and through is not None:
                last = boundary - planner.bucket
                columns.update(
                    collected_through=last,
                    evaluated_through=last,
                    history_end=boundary,
                    history_through=through,
                )
            _stamp(columns, rebuilt, reason=UNREADABLE_PROGRESS, version=version)
            if status_row["last_status"] in _COLLECTION_STATUSES:
                status_row["last_status"] = None
                _set_reason(status_row, None)
            status_row.update(columns)
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "handed_off", reason=UNREADABLE_PROGRESS)

        if entry.unreadable:
            return unreadable()
        try:
            progress = entry.progress or start_progress(
                history_days=watch.history_days or 0,
                boundary=boundary,
                now=now,
                settings=planner,
            )
        except BootstrapStateError:
            return unreadable()

        if not settings.pw_bootstrap_enabled:
            # The operator wants no more Ariel load (§H.5.4): hand off at once.
            _, reason = apply_trigger(PwHistoryState.READING, HistoryTrigger.SWITCHED_OFF)
            return finish(
                progress, HistoryTrigger.SWITCHED_OFF, reason, outcome="handed_off"
            )

        scope = resolve_scope(
            PwScopeKind(watch.scope_kind),
            log_source_id=watch.log_source_id,
            type_id=watch.scope_type_id,
            group_id=watch.scope_group_id,
            groups=groups,
            sources=sources,
            now=now,
            stale_after=timedelta(hours=settings.pw_scope_stale_after_hours),
            max_sources=settings.pw_max_sources_per_search,
            max_chunks=settings.pw_max_chunks_per_watch,
        )
        if not scope.resolved:
            # No step and no bucket (§H.6 row 7): `history_through` stays where it is.
            status_row["last_status"] = scope.status.value if scope.status else None
            _set_scope_reason(status_row, scope)
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "scope", reason=scope.reason)

        try:
            references = validate_properties(watch.properties)
            pinned = validate_pinned_values(
                watch.pinned_values,
                property_count=len(references),
                max_pinned=settings.pw_max_pinned_values,
            )
            properties = tuple((r.kind.value, r.name) for r in references)
            pinned_rows = 0
            if pinned:
                pinned_rows = pinned_rows_per_bucket(
                    validate_query_inputs(
                        PwSearchKind.PINNED,
                        scope_kind=scope.scope_kind,
                        scope_ids=scope.chunks[0],
                        properties=properties,
                        pinned=pinned,
                        max_sources=settings.pw_max_sources_per_search,
                    )
                )
            allowance = pw_history_budget(
                settings=settings,
                live_remaining=budget.remaining,
                seconds_since_tick_start=self._monotonic() - tick_started,
            )
            deadline = tick_started + settings.pw_bootstrap_seconds_per_tick
            step_budget = _StepBudget(
                budget, allowance.searches, deadline=deadline, monotonic=self._monotonic
            )
            chunks_per_search = max(1, len(scope.chunks))
            trigger: HistoryTrigger | None = None
            reason = ""
            waiting = False
            staged = _Staged()
            written = 0
            scan: ScanSpanResult | None = None
            scan_outcomes: list[PwBucketOutcome] = []
            # The smallest step of this watch (design §H.4.4): TOTALS, one GROUPED and, with
            # pins, PINNED. A tick gives a reading `min(setting, what live left over)`
            # searches; when the setting is the smaller of the two and cannot pay for one
            # step, the reading waits for a budget that never comes.
            needed = history_step_searches(bool(pinned))
            setting = settings.pw_bootstrap_searches_per_tick
            starved_by_setting = setting < needed and setting <= budget.remaining
            while True:
                if starved_by_setting:
                    # The setting is the limit: the reading waits and says why. It is not
                    # ended here; the time limit still hands it off `PARTIAL` (§H.4.4).
                    reason = history_budget_sentence(needed, setting=setting)
                    waiting = True
                    break
                plan = plan_pw_step(
                    progress,
                    now=now,
                    boundary=boundary,
                    # A search of the plan is one per scope chunk: the plan budgets in whole
                    # searches, so it gets the share that fits every chunk.
                    budget=StepBudget(
                        step_budget.remaining // chunks_per_search, deadline - self._monotonic()
                    ),
                    pinned_rows_per_bucket=pinned_rows,
                    enabled=settings.pw_bootstrap_enabled,
                    settings=planner,
                    scan=scan,
                )
                action = plan.step.action
                if action is StepAction.FINISH:
                    trigger, reason = plan.step.trigger, plan.step.reason
                    break
                if action is StepAction.WAIT:
                    reason = plan.step.reason
                    waiting = True
                    break
                if action is StepAction.SCAN:
                    assert plan.totals_span is not None
                    before = step_budget.used
                    outcomes = await self._run_kind(
                        PwSearchKind.TOTALS,
                        scope,
                        plan.totals_span,
                        (),
                        (),
                        step_budget,
                        provider=history_provider,
                    )
                    used = step_budget.used - before
                    result = ScanSpanResult(
                        plan.totals_span,
                        _wide_status(outcomes, used),
                        tuple(
                            (o.bucket_start, sum(row.events for row in o.rows))
                            for o in outcomes
                        )
                        if all(o.state is PwCellState.COMPLETE for o in outcomes)
                        else (),
                    )
                    if result.status is SearchStatus.NOT_RUN:
                        reason = WAITING_FOR_BUDGET
                        waiting = True
                        break
                    decision = interpret_scan(progress, [result], boundary, planner)
                    # `interpret_scan` counts one search per scan span; a scope of several
                    # chunks spent one per chunk.
                    progress = replace(
                        decision.progress,
                        searches=decision.progress.searches + max(0, used - 1),
                    )
                    reason = decision.reason
                    if decision.outcome is ScanOutcome.FLOOR_FOUND:
                        scan, scan_outcomes = result, outcomes
                        continue
                    if decision.outcome is ScanOutcome.CONTINUE:
                        continue
                    trigger = decision.trigger  # UNAVAILABLE; None on RETRY (hold, try later)
                    break
                # READ
                before = step_budget.used
                span = plan.step.span
                assert span is not None
                if plan.reuses_scan_totals:
                    totals_out = [
                        o for o in scan_outcomes if span.start <= o.bucket_start < span.end
                    ]
                    totals_status = SearchStatus.COMPLETE
                else:
                    assert plan.totals_span is not None
                    totals_out = await self._run_kind(
                        PwSearchKind.TOTALS,
                        scope,
                        plan.totals_span,
                        (),
                        (),
                        step_budget,
                        provider=history_provider,
                    )
                    totals_status = _wide_status(totals_out, step_budget.used - before)
                pinned_out: list[PwBucketOutcome] | None = None
                pinned_status: SearchStatus | None = None
                if pinned and totals_status is SearchStatus.COMPLETE:
                    assert plan.pinned_span is not None
                    mark = step_budget.used
                    pinned_out = await self._run_kind(
                        PwSearchKind.PINNED, scope, plan.pinned_span, properties, pinned,
                        step_budget, provider=history_provider,
                    )
                    pinned_status = _wide_status(pinned_out, step_budget.used - mark)
                elif pinned:
                    pinned_status = SearchStatus.NOT_RUN
                whole = totals_status is SearchStatus.COMPLETE and (
                    pinned_status is None or pinned_status is SearchStatus.COMPLETE
                )
                results: list[ChunkResult] = []
                grouped_by_chunk: list[list[PwBucketOutcome]] = []
                blocked = not whole
                for chunk in plan.grouped_chunks:
                    if blocked:
                        # Not sent: a span whose TOTALS or PINNED was not whole proves
                        # nothing, and a failed chunk ends the step (the width halves).
                        results.append(ChunkResult(chunk, SearchStatus.NOT_RUN, searches=0))
                        continue
                    mark = step_budget.used
                    outcomes = await self._run_kind(
                        PwSearchKind.GROUPED,
                        scope,
                        chunk,
                        properties,
                        (),
                        step_budget,
                        provider=history_provider,
                    )
                    result_chunk = _chunk_result(chunk, outcomes, step_budget.used - mark)
                    results.append(result_chunk)
                    if result_chunk.status is SearchStatus.COMPLETE:
                        grouped_by_chunk.append(outcomes)
                    else:
                        blocked = True
                settlement = settle_pw_step(
                    progress,
                    totals=totals_status,
                    pinned=pinned_status,
                    grouped=results,
                    totals_reused=plan.reuses_scan_totals,
                    end=boundary,
                    settings=planner,
                )
                # The record of what was spent is the budget's own count, exact for a scope
                # of several chunks too.
                progress = replace(
                    settlement.progress,
                    searches=progress.searches + (step_budget.used - before),
                )
                reason, trigger = settlement.reason, settlement.trigger
                prefix = grouped_by_chunk[: len(settlement.write_spans)]
                grouped_out = [o for chunk_out in prefix for o in chunk_out]
                if grouped_out:
                    totals_by = {o.bucket_start: o for o in totals_out}
                    pinned_by = {o.bucket_start: o for o in pinned_out or []}
                    assert progress.floor is not None
                    staged = self._fold_and_stage(
                        _Plan(watch, index, (grouped_out[0].bucket_start, boundary)),
                        scope,
                        grouped=grouped_out,
                        totals=[totals_by[o.bucket_start] for o in grouped_out],
                        pinned_out=(
                            [pinned_by[o.bucket_start] for o in grouped_out]
                            if pinned_out is not None
                            else None
                        ),
                        pinned=pinned,
                        property_count=len(references),
                        count=len(grouped_out),
                        value_rows=value_rows,
                        history=history,
                        now=now,
                        writes=writes,
                        status_row=status_row,
                        unparsed_before=0,
                        history_floor=progress.floor,
                    )
                    written = len(grouped_out)
                break
        except ArielPermitUnavailable as exc:
            return finish(
                entry.progress or progress,
                None,
                ariel_history_deferred_reason(exc),
                outcome="waiting",
            )
        except ValueError as exc:
            # A corrupt stored watch: the class only, never the text (it may hold a value).
            logger.warning(
                "property-watch history reading refused a watch",
                extra={"watch_id": str(watch.id), "error_class": type(exc).__name__},
            )
            writes.watches.append(status_row)
            return PwWatchReport(watch.id, "failed", reason=type(exc).__name__)
        spent[0] = step_budget.used
        if trigger is not None:
            outcome = "handed_off"
        elif waiting:
            outcome = "waiting"
        else:
            outcome = "history"
        return finish(
            progress, trigger, reason, outcome=outcome, staged=staged, written=written
        )

    async def _run_kind(
        self,
        kind: PwSearchKind,
        scope: ResolvedScope,
        span: Span,
        properties: tuple[tuple[str, str], ...],
        pinned: tuple[tuple[str, ...], ...],
        budget: PwSearchBudget,
        *,
        provider: _HistoryProvider | None = None,
    ) -> list[PwBucketOutcome]:
        """One search kind over `span`, for every chunk of the scope, combined per bucket."""
        settings = self.settings
        chunks: list[list[PwBucketOutcome]] = []
        for ids in scope.chunks:
            selected = self.provider if provider is None else provider
            plan = PwSearchPlan(
                search=kind,
                scope_kind=scope.scope_kind,
                scope_ids=ids,
                properties=properties if kind is not PwSearchKind.TOTALS else (),
                pinned=pinned if kind is PwSearchKind.PINNED else (),
            )
            if provider is None:
                outcomes = await run_search(
                    cast(QRadarProvider, selected),
                    plan,
                    (span.start, span.end),
                    settings.collection_interval_seconds,
                    max_rows=settings.pw_max_rows_per_search,
                    budget=budget,
                    max_sources=settings.pw_max_sources_per_search,
                )
            else:
                with ariel_background_priority():
                    outcomes = await run_search(
                        cast(QRadarProvider, selected),
                        plan,
                        (span.start, span.end),
                        settings.collection_interval_seconds,
                        max_rows=settings.pw_max_rows_per_search,
                        budget=budget,
                        max_sources=settings.pw_max_sources_per_search,
                    )
                if provider.deferred is not None:
                    if isinstance(budget, _StepBudget):
                        budget.refund_refused_search()
                    raise provider.deferred
            chunks.append(outcomes)
        return combine_chunks(chunks)

    async def _search(
        self,
        plan: _Plan,
        scope: ResolvedScope,
        properties: tuple[tuple[str, str], ...],
        pinned: tuple[tuple[str, ...], ...],
        budget: PwSearchBudget,
    ) -> tuple[list[PwBucketOutcome], list[PwBucketOutcome], list[PwBucketOutcome] | None]:
        settings = self.settings
        kinds = [PwSearchKind.GROUPED, PwSearchKind.TOTALS]
        if pinned:
            kinds.append(PwSearchKind.PINNED)
        combined: dict[PwSearchKind, list[PwBucketOutcome]] = {}
        for search in kinds:
            chunks: list[list[PwBucketOutcome]] = []
            for ids in scope.chunks:
                chunks.append(
                    await run_search(
                        self.provider,
                        PwSearchPlan(
                            search=search,
                            scope_kind=scope.scope_kind,
                            scope_ids=ids,
                            properties=properties if search is not PwSearchKind.TOTALS else (),
                            pinned=pinned if search is PwSearchKind.PINNED else (),
                        ),
                        plan.window,
                        settings.collection_interval_seconds,
                        max_rows=settings.pw_max_rows_per_search,
                        budget=budget,
                        max_sources=settings.pw_max_sources_per_search,
                        watch=plan.index,
                    )
                )
            combined[search] = combine_chunks(chunks)
        return (
            combined[PwSearchKind.GROUPED],
            combined[PwSearchKind.TOTALS],
            combined.get(PwSearchKind.PINNED),
        )

    def _fold_and_stage(
        self,
        plan: _Plan,
        scope: ResolvedScope,
        *,
        grouped: Sequence[PwBucketOutcome],
        totals: Sequence[PwBucketOutcome],
        pinned_out: Sequence[PwBucketOutcome] | None,
        pinned: Sequence[Sequence[str]],
        property_count: int,
        count: int,
        value_rows: Sequence[PwValue],
        history: Sequence[ScopeRecord],
        now: datetime,
        writes: _Writes,
        status_row: dict[str, Any],
        unparsed_before: int,
        history_floor: datetime | None = None,
    ) -> _Staged:
        """Fold, admit and stage the first `count` buckets.

        `history_floor` (the proved floor `F`) switches on the history reading, whose
        differences from live are exactly PHASE-G §H.2.5's table: `origin` HISTORY,
        `in_maintenance` False, `retire_stale` after every bucket, no `NOT_APPLICABLE`
        check, and the progress (not `collected_through`) is the history's watermark.
        """
        reading = history_floor is not None
        origin = HistoryOrigin.HISTORY if reading else HistoryOrigin.LIVE
        watch = plan.watch
        settings = self.settings
        interval = settings.collection_interval_seconds
        chunks = len(scope.chunks)
        version = watch.watch_version
        has_pins = bool(pinned)

        if count < len(grouped) and not reading:
            # The first bucket that did not complete. A failed search is written so the gap
            # is visible; one that was only not searched (the budget) is left for the next
            # tick. Either way the watermark holds here.
            held = count
            outcomes = [grouped[held], totals[held]] + (
                [pinned_out[held]] if pinned_out is not None else []
            )
            if any(o.state is PwCellState.FAILED for o in outcomes):
                fact = not_collected_fact(
                    bucket_start=grouped[held].bucket_start,
                    bucket_seconds=interval,
                    watch_version=version,
                    pinned=has_pins,
                )
                fact = replace(
                    fact,
                    scope_member_count=scope.member_count,
                    scope_fingerprint=scope.fingerprint,
                )
                writes.buckets.append(
                    _bucket_row(
                        watch.id,
                        fact,
                        provenance={
                            "grouped": _provenance(grouped[held], chunks),
                            "totals": _provenance(totals[held], chunks),
                            "pinned": (
                                _provenance(pinned_out[held], chunks)
                                if pinned_out is not None
                                else None
                            ),
                        },
                        collected_at=now,
                    )
                )
                if count == 0:
                    # Nothing newer than the last evaluated bucket exists: the watch must not
                    # keep reading OK. A later collected bucket clears this (STALE is one of
                    # collection's own statuses).
                    status_row["last_status"] = PwWatchStatus.STALE.value
                    _set_reason(
                        status_row,
                        MessageRef(
                            "pw.status.latest_search_failed",
                            {"bucket": grouped[held].bucket_start},
                        ),
                    )
        if count == 0:
            return _Staged()

        policy = PwAdmissionPolicy(
            min_events=settings.pw_min_admission_events,
            max_new_per_bucket=settings.pw_max_new_values_per_bucket,
            max_active=settings.pw_max_active_values_per_watch,
            active_days=settings.pw_value_active_days,
        )
        values: dict[str, TrackedValue] = {}
        other_versions: list[tuple[int, TrackedValue]] = []
        for row in value_rows:
            if row.watch_version == version:
                values[row.value_key] = _tracked(row, property_count)
            elif row.active:
                other_versions.append((row.watch_version, _tracked(row, property_count)))
        limit = component_limit(property_count, settings.pw_value_max_length)
        pins = pinned_value_keys(pinned, property_count, limit)
        first_bucket = grouped[0].bucket_start
        sync_pins(values, pins, first_bucket=first_bucket)
        retire_other_versions([v for _, v in other_versions], at=first_bucket)

        flags = scope_changed_flags(
            history, scope.fingerprint, count, settings.pw_scope_change_grace_buckets
        )
        facts: list[AdmittedBucket] = []
        collected_facts: list[PwBucketFact] = []
        retired_in_history: list[str] = []
        for index in range(count):
            bucket_start = grouped[index].bucket_start
            fact = fold_bucket(
                bucket_start=bucket_start,
                bucket_seconds=interval,
                watch_version=version,
                property_count=property_count,
                grouped=grouped[index],
                totals=totals[index],
                pinned_outcome=pinned_out[index] if pinned_out is not None else None,
                pinned=pinned,
                tracked={key for key, value in values.items() if value.active},
                max_values=settings.pw_max_values_per_bucket,
                value_max_length=settings.pw_value_max_length,
            )
            fact = replace(
                fact,
                scope_member_count=scope.member_count,
                scope_fingerprint=scope.fingerprint,
                scope_changed=flags[index],
                # History has no maintenance state: today's would be applied to the past.
                in_maintenance=(
                    False if reading else _scope_in_maintenance(scope, bucket_start)
                ),
            )
            admitted = admit_bucket(fact, values, policy, property_count=property_count)
            facts.append(admitted)
            collected_facts.append(admitted.fact)
            provenance: dict[str, object] = {
                "grouped": _provenance(grouped[index], chunks),
                "totals": _provenance(totals[index], chunks),
                "pinned": (
                    _provenance(pinned_out[index], chunks) if pinned_out is not None else None
                ),
            }
            if history_floor is not None:
                provenance |= {
                    "history": True,
                    "membership": "current",
                    "floor": history_floor.isoformat(),
                }
            writes.buckets.append(
                _bucket_row(
                    watch.id,
                    admitted.fact,
                    provenance=provenance,
                    collected_at=now,
                    origin=origin,
                )
            )
            if reading:
                # After every bucket, as bucket-by-bucket live collection would: the
                # ceiling and the tracking windows never see a chunk-wide reference (§H.7.3).
                retired_in_history.extend(
                    retire_stale(values, reference=bucket_start, policy=policy)
                )
        reference = grouped[count - 1].bucket_start
        retired = (
            retired_in_history
            if reading
            else retire_stale(values, reference=reference, policy=policy)
        )
        for admitted in facts:
            for key, events in admitted.metrics.items():
                writes.metrics.append(
                    {
                        "pw_value_id": values[key].id,
                        "bucket_start": admitted.fact.bucket_start,
                        "events": events,
                        "bucket_seconds": interval,
                    }
                )
        for value in values.values():
            if value.dirty:
                writes.values.append(_value_row(watch.id, version, value))
        for old_version, value in other_versions:
            if value.dirty:
                writes.values.append(_value_row(watch.id, old_version, value))
        if reading:
            # Nothing is judged: no watermark, no NOT_APPLICABLE; the progress moves instead.
            return _Staged(
                admitted=sum(f.fact.admitted_values for f in facts), retired=len(retired)
            )
        status_row["collected_through"] = reference
        status = status_row["last_status"]
        trailing = 0
        for collected in reversed(collected_facts):
            if not collected.property_not_parsed:
                break
            trailing += 1
        if trailing == count:
            trailing += unparsed_before
        if trailing >= _NOT_APPLICABLE_MIN_BUCKETS:
            status_row["last_status"] = PwWatchStatus.NOT_APPLICABLE.value
            _set_reason(status_row, _NOT_APPLICABLE_MESSAGE)
        elif status in _COLLECTION_STATUSES:
            status_row["last_status"] = None
            _set_reason(status_row, None)
        return _Staged(
            admitted=sum(f.fact.admitted_values for f in facts), retired=len(retired)
        )

    async def _unparsed_streaks(
        self, watch_ids: Sequence[uuid.UUID], *, since: datetime
    ) -> dict[uuid.UUID, int]:
        """Per watch, how many of its newest stored buckets, at or before the watermark, in a
        row hold events on which the property parsed on none (one statement for all watches).

        A bucket that measured nothing ends the run. Only the current version counts. The
        count is capped at what `NOT_APPLICABLE` needs, so the scan is bounded.
        """
        if not watch_ids:
            return {}
        depth = _NOT_APPLICABLE_MIN_BUCKETS - 1
        ranked = (
            select(
                PwBucket.watch_id.label("watch_id"),
                (
                    (PwBucket.values_measurement == PwBucketMeasurement.EXACT.value)
                    & (PwBucket.total_events > 0)
                    & (PwBucket.parsed_events == 0)
                ).label("unparsed"),
                func.row_number()
                .over(partition_by=PwBucket.watch_id, order_by=PwBucket.bucket_start.desc())
                .label("rank"),
            )
            .join(PropertyWatch, PropertyWatch.id == PwBucket.watch_id)
            .where(
                PwBucket.watch_id.in_(list(watch_ids)),
                PwBucket.watch_version == PropertyWatch.watch_version,
                is_live_clause(PwBucket),
                PwBucket.bucket_start <= PropertyWatch.collected_through,
                PwBucket.bucket_start >= since,
            )
            .subquery()
        )
        rows = await self.session.execute(
            select(ranked.c.watch_id, ranked.c.unparsed)
            .where(ranked.c.rank <= depth)
            .order_by(ranked.c.watch_id, ranked.c.rank)
        )
        streaks: dict[uuid.UUID, int] = {}
        ended: set[uuid.UUID] = set()
        for watch_id, unparsed in rows.all():
            if watch_id in ended:
                continue
            if unparsed:
                streaks[watch_id] = streaks.get(watch_id, 0) + 1
            else:
                ended.add(watch_id)
        return streaks

    async def _persist(self, writes: _Writes, report: PwCollectionReport) -> None:
        # Values first: the metric rows reference them.
        await self.repo.write_values(writes.values)
        await self.repo.write_buckets(writes.buckets)
        await self.repo.write_metrics(writes.metrics)
        await self.repo.update_watches(writes.watches)
        report.values_written = len(writes.values)
        report.buckets_written = sum(
            1
            for row in writes.buckets
            if row["values_measurement"] != PwBucketMeasurement.NOT_COLLECTED.value
        )
        report.metric_rows_written = len(writes.metrics)
