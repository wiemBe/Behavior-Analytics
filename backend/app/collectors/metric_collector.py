"""Log-source metric collection.

Guarantees:
  * Bounded batches — sources processed in configurable chunks.
  * No overlapping runs for the same instance: a PostgreSQL advisory lock keyed
    on (instance, collector) is held for the duration; a second worker skips.
  * Idempotent — metrics are upserted on (log_source_id, bucket_start), so
    re-collecting an interval overwrites rather than duplicates.
  * Explicit UTC interval boundaries — intervals are floored to the configured
    interval width in UTC.
  * Watermark + lag persisted per (instance, collector).
  * Bounded backfill — at most one cadence plus
    `collection_max_backfill_intervals` is filled per tick.

After the live pass, under the same lock, the collector may also take one bounded step of
the Phase A history reading (HISTORY-BACKFILL §B.4.1, §B.6; `app.history.phase_a`). Live
goes first and keeps its transaction: the step runs in a savepoint, so a fault in it never
takes the live buckets with it. It spends only the searches and seconds the live pass left,
writes `collection_source = 'ariel_history'` rows with `ON CONFLICT DO NOTHING`, and never
touches the collection watermark.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.baseline import BaselineBuilder
from app.core.config import Settings, get_settings
from app.history import phase_a
from app.history.families import fit_family_window, history_search_budget
from app.history.lifecycle import (
    ActionKind,
    ConsoleMode,
    DecisionView,
    LifecycleError,
    ScopeAction,
    ScopeRowView,
    bounded_reason,
    console_mode,
    decode_scope,
    new_scope_row,
    scope_action,
    transition_row,
)
from app.history.origin import is_live_clause
from app.history.phase_a import CompletedBucket
from app.history.planner import (
    PlannerSettings,
    ScopeProgress,
    Span,
    StepAction,
    StepBudget,
    fit_window,
    new_progress,
    plan_step,
    settle_step,
)
from app.history.progress_codec import encode_progress
from app.history.source_floors import SourceFloors
from app.history.state import HistoryFamily, HistoryState, HistoryTrigger, next_state
from app.models.enums import BucketCompleteness, SearchFamily
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceMetric
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import LogSourceMetricSample
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService

COLLECTOR_NAME = "log_source_metric"
HISTORY_FAMILY = HistoryFamily.PHASE_A
HISTORY_SCOPE_KEY = ""  # Phase A has one console-wide scope

logger = logging.getLogger(__name__)


@dataclass
class HistoryReport:
    """What the Phase A history step of one tick did (counts and words, never values)."""

    state: str | None = None
    searches: int = 0
    buckets: int = 0
    rows: int = 0
    rebuilt: int = 0
    error: str | None = None


@dataclass
class CollectionReport:
    instance_id: uuid.UUID
    intervals_collected: int
    samples_written: int
    skipped_locked: bool = False
    watermark_at: datetime | None = None
    lag_seconds: int | None = None
    searches_sent: int = 0
    splits: int = 0
    partial: bool = False
    history: HistoryReport | None = None

    @property
    def buckets_collected(self) -> int:
        return self.intervals_collected


@dataclass
class _WindowSearch:
    """What `_search_window` found: the buckets it completed and how it stopped."""

    completed: dict[datetime, CompletedBucket] = field(default_factory=dict)
    searches: int = 0
    splits: int = 0
    capped: bool = False
    failure: ProviderError | None = None
    failed: Span | None = None


def floor_to_interval(dt: datetime, interval_seconds: int) -> datetime:
    """Floor a UTC datetime to the interval grid. Boundaries are explicit and
    deterministic so every worker agrees on interval identity."""
    dt = dt.astimezone(UTC)
    epoch = int(dt.timestamp())
    floored = epoch - (epoch % interval_seconds)
    return datetime.fromtimestamp(floored, tz=UTC)


class MetricCollector:
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

    @property
    def _collection_source(self) -> str:
        """Which subsystem produced a bucket. QRadar exposes no per-log-source
        metric endpoint, so live volume is always a bounded Ariel aggregate."""
        name = type(self.provider).__name__.lower()
        return "mock" if "mock" in name else "ariel_aggregate"

    async def collect(self, instance: QRadarInstance) -> CollectionReport:
        interval = self.settings.collection_interval_seconds
        now = self._clock()
        # The most recent *completed* interval is the one before the current.
        current_boundary = floor_to_interval(now, interval)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                return CollectionReport(instance.id, 0, 0, skipped_locked=True)

            watermark = await self._get_or_create_watermark(instance)
            cadence = await SearchCadenceService(self.session, self.settings).get(
                SearchFamily.PHASE_A_VOLUME
            )
            cadence_buckets = max(1, cadence.cadence_seconds // interval)
            max_intervals = cadence_buckets + self.settings.collection_max_backfill_intervals
            intervals = self._intervals_to_collect(
                watermark, current_boundary, interval, max_intervals=max_intervals
            )

            completed, searches_sent, splits, search_partial = (
                await self._collect_bucketed_window(intervals, interval)
            )

            total_samples = 0
            buckets_collected = 0
            last_done: datetime | None = watermark.watermark_at
            for start in intervals:
                bucket = completed.get(start)
                if bucket is None:
                    break
                end = start + timedelta(seconds=interval)
                written = await self._write_bucket(
                    instance,
                    start,
                    end,
                    bucket.samples,
                    watermark_at=last_done,
                    duration_ms=bucket.duration_ms,
                    collected_at=bucket.collected_at,
                    search_window_start=bucket.search_window_start,
                    search_window_end=bucket.search_window_end,
                    search_bucket_count=bucket.search_bucket_count,
                    split_depth=bucket.split_depth,
                )
                total_samples += written
                buckets_collected += 1
                # The watermark only advances past an interval that completed.
                # A partial failure raises out of this loop, leaving the
                # watermark on the last whole interval so the next run retries
                # rather than skipping the gap.
                last_done = start

            watermark.watermark_at = last_done
            watermark.last_run_at = now
            watermark.intervals_collected += buckets_collected
            if last_done is not None:
                watermark.lag_seconds = int((now - (last_done + timedelta(seconds=interval)))
                                            .total_seconds())
            watermark.consecutive_failures = 0
            watermark.last_error = None
            await self.session.flush()

            history = await self._history_phase(
                instance,
                now=now,
                live_searches=searches_sent,
                cadence_seconds=cadence.cadence_seconds,
            )

            return CollectionReport(
                instance_id=instance.id,
                intervals_collected=buckets_collected,
                samples_written=total_samples,
                watermark_at=watermark.watermark_at,
                lag_seconds=watermark.lag_seconds,
                searches_sent=searches_sent,
                splits=splits,
                partial=search_partial or buckets_collected < len(intervals),
                history=history,
            )

    def _intervals_to_collect(
        self,
        watermark: CollectionWatermark,
        current_boundary: datetime,
        interval: int,
        *,
        max_intervals: int,
    ) -> list[datetime]:
        """Intervals before the boundary, resuming at a bounded cadence window."""
        if watermark.watermark_at is None:
            # First run: just the most recent completed interval.
            start = current_boundary - timedelta(seconds=interval)
            return [start] if start < current_boundary else []

        first = watermark.watermark_at + timedelta(seconds=interval)
        out: list[datetime] = []
        cursor = first
        while cursor < current_boundary and len(out) < max_intervals:
            out.append(cursor)
            cursor += timedelta(seconds=interval)
        return out

    async def _collect_bucketed_window(
        self, intervals: list[datetime], interval: int
    ) -> tuple[dict[datetime, CompletedBucket], int, int, bool]:
        """The live pass: search the cadence window, retaining the complete buckets."""
        if not intervals:
            return {}, 0, 0, False
        found = await self._search_window(
            intervals[0],
            len(intervals),
            interval,
            max_searches=self.settings.search_cadence_max_searches_per_tick,
        )
        # Complete buckets before the failure are kept; with none, nothing
        # advanced and the error surfaces so the run is recorded as failed.
        if found.failure is not None and intervals[0] not in found.completed:
            raise found.failure
        return (
            found.completed,
            found.searches,
            found.splits,
            found.capped or found.failure is not None,
        )

    async def _search_window(
        self,
        start: datetime,
        bucket_count: int,
        interval: int,
        *,
        max_searches: int,
        deadline: datetime | None = None,
    ) -> _WindowSearch:
        """Search `bucket_count` buckets from `start`, recursively splitting truncation.

        The one routine of the live pass and of a history step (they differ only in the
        search cap and the start and end they pass, never in how a search is run). The
        completed buckets are always a prefix of the window: the search goes left first and
        stops at the first failure, the cap, or `deadline`. A lone bucket that fails (or is
        still truncated) writes nothing, for history as for live.
        """
        found = _WindowSearch()

        async def search(first: datetime, count: int, split_depth: int) -> None:
            if found.failure is not None:
                return
            if found.searches >= max_searches or (
                deadline is not None and self._clock() >= deadline
            ):
                found.capped = True
                return
            end = first + timedelta(seconds=interval * count)
            found.searches += 1
            started = self._clock()
            if count == 1:
                try:
                    samples = await self.provider.get_log_source_metrics(first, end)
                except ProviderError as exc:
                    found.failure, found.failed = exc, Span(first, end)
                    return
                collected_at = self._clock()
                found.completed[first] = CompletedBucket(
                    samples=samples,
                    search_window_start=first,
                    search_window_end=end,
                    search_bucket_count=1,
                    split_depth=split_depth,
                    duration_ms=int((collected_at - started).total_seconds() * 1000),
                    collected_at=collected_at,
                )
                return
            try:
                batch = await self.provider.get_log_source_metrics_bucketed(
                    first,
                    end,
                    bucket_seconds=interval,
                    max_rows=self.settings.ariel_multi_bucket_max_rows,
                )
            except ProviderError as exc:
                found.failure, found.failed = exc, Span(first, end)
                return
            collected_at = self._clock()
            duration_ms = int((collected_at - started).total_seconds() * 1000)
            if batch.truncated:
                found.splits += 1
                left_count = count // 2
                await search(first, left_count, split_depth + 1)
                if not found.capped and found.failure is None:
                    await search(
                        first + timedelta(seconds=interval * left_count),
                        count - left_count,
                        split_depth + 1,
                    )
                return

            for index in range(count):
                bucket_start = first + timedelta(seconds=interval * index)
                found.completed[bucket_start] = CompletedBucket(
                    samples=batch.buckets.get(bucket_start, []),
                    search_window_start=first,
                    search_window_end=end,
                    search_bucket_count=count,
                    split_depth=split_depth,
                    duration_ms=duration_ms,
                    collected_at=collected_at,
                )

        await search(start, bucket_count, 0)
        return found

    async def _collect_interval(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        *,
        watermark_at: datetime | None = None,
    ) -> int:
        started = self._clock()
        samples = await self.provider.get_log_source_metrics(start, end)
        collected_at = self._clock()
        duration_ms = int((collected_at - started).total_seconds() * 1000)
        return await self._write_bucket(
            instance,
            start,
            end,
            samples,
            watermark_at=watermark_at,
            duration_ms=duration_ms,
            collected_at=collected_at,
        )

    async def _write_bucket(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        samples: list[LogSourceMetricSample],
        *,
        watermark_at: datetime | None,
        duration_ms: int,
        collected_at: datetime,
        search_window_start: datetime | None = None,
        search_window_end: datetime | None = None,
        search_bucket_count: int | None = None,
        split_depth: int | None = None,
    ) -> int:
        """Persist one complete bucket through the existing upsert and zero-fill."""

        # Map qradar_id -> internal LogSource id for this instance.
        id_map = await self._log_source_id_map(instance.id)

        # Non-secret evidence of how these rows were produced. Never the token,
        # never response headers — only the shape of the request. What `event_count`
        # counts, and which columns were really measured, are recorded too: without
        # them a reader cannot tell events (SUM(eventcount)) from Ariel records
        # (COUNT(*)), or a measured zero from a DTO default. Rows written before
        # these keys existed carry neither. A history row is built by the same helper.
        completed = (
            CompletedBucket(
                samples=samples,
                search_window_start=search_window_start,
                search_window_end=search_window_end,
                search_bucket_count=search_bucket_count or 0,
                split_depth=split_depth or 0,
                duration_ms=duration_ms,
                collected_at=collected_at,
            )
            if search_window_start is not None and search_window_end is not None
            else None
        )
        provenance = phase_a.bucket_provenance(
            collector=COLLECTOR_NAME,
            start=start,
            end=end,
            provider=type(self.provider).__name__,
            sample_count=len(samples),
            aggregation=self.provider.event_count_aggregation,
            telemetry=(t.value for t in self.provider.log_source_telemetry),
            completed=completed,
        )

        rows = []
        for s in samples:
            ls_id = id_map.get(s.qradar_id)
            if ls_id is None:
                continue  # source not yet in inventory; a sync will add it
            rows.append(
                {
                    "log_source_id": ls_id,
                    "bucket_start": start,
                    "completeness": BucketCompleteness.COMPLETE,
                    "collection_source": self._collection_source,
                    "query_provenance": provenance,
                    "collection_duration_ms": duration_ms,
                    "collected_at": collected_at,
                    "watermark_at": watermark_at,
                    **phase_a.sample_columns(s),
                }
            )

        rows.extend(
            await self._zero_filled_rows(
                instance,
                start,
                end,
                reported={s.qradar_id for s in samples},
                id_map=id_map,
                provenance=provenance,
                duration_ms=duration_ms,
                collected_at=collected_at,
                watermark_at=watermark_at,
            )
        )
        if not rows:
            return 0

        # Idempotent upsert on the composite PK. Re-collecting overwrites.
        stmt = pg_insert(LogSourceMetric).values(rows)
        update_cols = {c: stmt.excluded[c] for c in rows[0] if c not in
                       ("log_source_id", "bucket_start")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["log_source_id", "bucket_start"], set_=update_cols
        )
        await self.session.execute(stmt)
        return len(rows)

    async def _zero_filled_rows(
        self,
        instance: QRadarInstance,
        start: datetime,
        end: datetime,
        *,
        reported: set[int],
        id_map: dict[int, uuid.UUID],
        provenance: dict,
        duration_ms: int,
        collected_at: datetime,
        watermark_at: datetime | None,
    ) -> list[dict]:
        """Explicit zero buckets for monitored sources QRadar did not report.

        A source that emits nothing is simply absent from the Ariel
        `GROUP BY logsourceid` result, so without this the interval leaves no
        row at all. Silence would then be indistinguishable from a collection
        gap: the anomaly engine evaluates the newest row it can find, so it
        would re-judge the last busy bucket forever and never observe the
        silence. `detect_no_events` requires a COMPLETE bucket with a zero
        count, and `_preceding_empty_buckets` counts consecutive complete zero
        buckets — both were written expecting these rows to exist.

        Only monitored sources are filled. An unmonitored source is never
        evaluated, so a row for it would be storage without a reader.

        The zero is an observation, not an assumption: the query for this
        window succeeded and returned no events for this source. That is why
        the row is COMPLETE. A failed or partial collection raises instead of
        reaching this point, so no zero row is ever written for a window we did
        not actually observe.
        """
        missing = [
            (qradar_id, ls_id)
            for qradar_id, ls_id in id_map.items()
            if qradar_id not in reported
        ]
        if not missing:
            return []

        monitored = set(
            (
                await self.session.scalars(
                    select(LogSource.id).where(
                        LogSource.instance_id == instance.id,
                        LogSource.monitoring_enabled.is_(True),
                    )
                )
            ).all()
        )

        bucket_seconds = int((end - start).total_seconds())
        # Marked so a zero that came from an empty result is never mistaken for
        # a zero that a provider actively reported.
        zero_provenance = {**provenance, "zero_filled": True}

        return [
            {
                "log_source_id": ls_id,
                "bucket_start": start,
                "completeness": BucketCompleteness.COMPLETE,
                "collection_source": self._collection_source,
                "query_provenance": zero_provenance,
                "collection_duration_ms": duration_ms,
                "collected_at": collected_at,
                "watermark_at": watermark_at,
                **phase_a.zero_columns(bucket_seconds),
            }
            for _, ls_id in missing
            if ls_id in monitored
        ]

    # ------------------------------------------------------------ history
    async def _history_phase(
        self,
        instance: QRadarInstance,
        *,
        now: datetime,
        live_searches: int,
        cadence_seconds: int,
    ) -> HistoryReport:
        """One tick of Phase A history, after the live pass, in a savepoint.

        Live goes first: whatever fails here rolls back to the savepoint and is logged by
        class only, so the live buckets and the watermark of this tick are never lost to
        the history reading. The reading itself fails closed: an unexpected state raises,
        nothing is written, and the next tick looks again.
        """
        try:
            async with self.session.begin_nested():
                return await self._history_tick(
                    instance,
                    now=now,
                    live_searches=live_searches,
                    cadence_seconds=cadence_seconds,
                )
        except Exception as exc:
            logger.warning(
                "history backfill step failed",
                extra={"instance": instance.name, "error_class": type(exc).__name__},
            )
            return HistoryReport(error=type(exc).__name__)

    async def _history_tick(
        self,
        instance: QRadarInstance,
        *,
        now: datetime,
        live_searches: int,
        cadence_seconds: int,
    ) -> HistoryReport:
        report = HistoryReport()
        repo = HistoryBackfillRepository(self.session)
        decision_row = (await repo.decisions([instance.id])).get(instance.id)
        decision = _decision_view(decision_row)
        mode = console_mode(decision)
        if decision is None or mode in (ConsoleMode.UNDECIDED, ConsoleMode.NO):
            return report  # undecided is not Yes (§B.12 row 15); nothing is created

        stored = (await repo.scopes(instance.id, HISTORY_FAMILY.value, [HISTORY_SCOPE_KEY])).get(
            HISTORY_SCOPE_KEY
        )
        row = _row_view(stored)
        switch_on = self.settings.history_backfill_enabled
        # The oldest live bucket is read only when the lifecycle can use it: a scope that
        # is created, wakes from WAITING or restarts. A reading scope never needs it.
        live_anchor = None
        if switch_on and mode is ConsoleMode.ACTIVE and _needs_anchor(row, decision):
            live_anchor = await self._oldest_live_bucket(instance.id)
        action = scope_action(
            family=HISTORY_FAMILY,
            mode=mode,
            decision=decision,
            row=row,
            has_live_data=live_anchor is not None,
            live_anchor=live_anchor,
            switch_on=switch_on,
        )

        values: dict[str, Any] | None = None
        if action.kind is ActionKind.CREATE:
            values = new_scope_row(
                action,
                instance_id=instance.id,
                family=HISTORY_FAMILY,
                scope_key=HISTORY_SCOPE_KEY,
                decision=decision,
                at=now,
            )
        elif action.kind is ActionKind.TRANSITION:
            assert row is not None
            values = transition_row(
                action,
                instance_id=instance.id,
                family=HISTORY_FAMILY,
                scope_key=HISTORY_SCOPE_KEY,
                row=_with_pending(row) if _ends_reading(action) else row,
                decision=decision,
                at=now,
            )
        elif action.kind is ActionKind.STEP:
            assert row is not None
            values = await self._history_step(
                instance,
                decision,
                row,
                now=now,
                live_searches=live_searches,
                cadence_seconds=cadence_seconds,
                report=report,
            )

        # The baseline rebuild of the sources a finished reading read (§B.8.5), in batches.
        current = _view_of(values) if values is not None else row
        if (
            switch_on
            and current is not None
            and current.state in (HistoryState.COMPLETE, HistoryState.PARTIAL)
        ):
            rebuilt_values = await self._rebuild_pending(instance, current, values, now, report)
            values = rebuilt_values if rebuilt_values is not None else values

        if values is not None:
            await repo.write_scopes([values])
            report.state = str(values["state"])
        elif row is not None:
            report.state = row.state.value
        return report

    async def _oldest_live_bucket(self, instance_id: uuid.UUID) -> datetime | None:
        """The anchor: the oldest LIVE bucket of the console's sources (one statement)."""
        return cast(
            datetime | None,
            await self.session.scalar(
                select(func.min(LogSourceMetric.bucket_start))
                .join(LogSource, LogSource.id == LogSourceMetric.log_source_id)
                .where(LogSource.instance_id == instance_id, is_live_clause(LogSourceMetric))
            ),
        )

    async def _monitored_sources(self, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
        """QRadar id -> internal id of the monitored sources: the id map and the set, once."""
        rows = await self.session.execute(
            select(LogSource.qradar_id, LogSource.id).where(
                LogSource.instance_id == instance_id,
                LogSource.monitoring_enabled.is_(True),
            )
        )
        return dict(rows.tuples().all())

    async def _history_step(
        self,
        instance: QRadarInstance,
        decision: DecisionView,
        row: ScopeRowView,
        *,
        now: datetime,
        live_searches: int,
        cadence_seconds: int,
        report: HistoryReport,
    ) -> dict[str, Any] | None:
        """One bounded step of a `READING` scope; the scope row to write, or None."""
        if row.anchor is None:
            raise LifecycleError("a reading scope has an anchor")
        anchor = row.anchor
        settings = self.settings
        monitored = await self._monitored_sources(instance.id)
        known = [phase_a.source_key(qradar_id) for qradar_id in monitored]
        ps = phase_a.planner_settings(
            settings, monitored=len(monitored), cadence_seconds=cadence_seconds
        )
        decoded = decode_scope(row)

        if decoded is None:
            # The first step plans the window (§B.6.5), from the anchor, fresh after a restart.
            planned = self._plan_window(decision, row, anchor, len(monitored), ps, now)
            if isinstance(planned, ScopeAction):
                return transition_row(
                    planned,
                    instance_id=instance.id,
                    family=HISTORY_FAMILY,
                    scope_key=HISTORY_SCOPE_KEY,
                    row=row,
                    decision=decision,
                    at=now,
                )
            progress, days_target, reason = planned
            row = replace(
                row,
                days_target=days_target,
                target_start=progress.target_start,
                progress=encode_progress(progress, SourceFloors().encode()),
                reason=reason,
            )
            floors = SourceFloors()
            first_step = True
            adapter: dict[str, Any] = SourceFloors().encode()
        else:
            progress, adapter = decoded.progress, decoded.adapter
            floors = SourceFloors.decode(adapter)
            first_step = False

        budget = history_search_budget(settings, used=live_searches)
        started = self._clock()
        deadline = now + timedelta(seconds=settings.history_backfill_seconds_per_tick)
        seconds_left = (deadline - started).total_seconds()
        plan = plan_step(
            progress,
            now=now,
            end=anchor,
            budget=StepBudget(budget, seconds_left),
            settings=ps,
        )
        if plan.action is StepAction.SCAN:  # pragma: no cover - the floor is set at planning
            raise LifecycleError("a Phase A step never scans: the read is its own floor proof")
        if plan.action is StepAction.WAIT:
            # Nothing was searched. Only the first step has something new to store: the plan.
            if not first_step:
                return None
            return self._step_values(
                instance.id, row, progress, adapter, floors, ps, row.reason or plan.reason
            )

        if plan.action is StepAction.FINISH:
            assert plan.trigger is not None
            return self._finish_values(
                instance, decision, row, progress, adapter, floors, known, ps, plan.trigger,
                plan.reason, now,
            )

        # READ: the chunks go through the live split routine, one search cap for the step.
        interval = settings.collection_interval_seconds
        completed: dict[datetime, CompletedBucket] = {}
        results = []
        spent = 0
        for chunk in plan.chunks:
            if spent >= budget:
                break
            found = await self._search_window(
                chunk.start,
                chunk.buckets(ps),
                interval,
                max_searches=budget - spent,
                deadline=deadline,
            )
            spent += found.searches
            completed.update(found.completed)
            results.extend(
                phase_a.chunk_results(
                    chunk, found.completed, settings=ps, searches=found.searches,
                    failed=found.failed,
                )
            )
            if len(found.completed) < chunk.buckets(ps):
                break  # a failure, the cap or the clock: nothing after it is searched
        report.searches = spent

        settlement = settle_step(progress, results, end=anchor, settings=ps)
        written = {
            start: completed[start]
            for span in settlement.write_spans
            for start in _span_starts(span, ps)
        }
        fold = phase_a.fold_step(
            written,
            monitored=monitored,
            floors=floors,
            read_start=progress.floor or progress.target_start,
            target_start=progress.target_start,
            settings=ps,
            collector=COLLECTOR_NAME,
            provider=type(self.provider).__name__,
            aggregation=self.provider.event_count_aggregation,
            telemetry=(t.value for t in self.provider.log_source_telemetry),
            mock=self._collection_source == "mock",
        )
        if fold.rows:
            # History never replaces a row: a live row, or an earlier history row, stays.
            table = cast(Table, LogSourceMetric.__table__)
            await self.session.execute(
                pg_insert(table).on_conflict_do_nothing(
                    index_elements=["log_source_id", "bucket_start"]
                ),
                fold.rows,
            )
        report.buckets = len(written)
        report.rows = len(fold.rows)

        if settlement.trigger is not None:
            return self._finish_values(
                instance, decision, row, settlement.progress, adapter, fold.floors, known, ps,
                settlement.trigger, settlement.reason, now,
            )
        return self._step_values(
            instance.id, row, settlement.progress, adapter, fold.floors, ps, settlement.reason
        )

    def _plan_window(
        self,
        decision: DecisionView,
        row: ScopeRowView,
        anchor: datetime,
        monitored: int,
        ps: PlannerSettings,
        now: datetime,
    ) -> tuple[ScopeProgress, int, str] | ScopeAction:
        """The first step's plan: the fitted window, `T0`, and the read start.

        The read starts at `T0 - m` and the first span is the floor proof (§B.6.2 step 1),
        so for this family the core's `ScopeProgress.floor` means "where the read starts",
        not a proved floor: Phase A proves one floor per source from the read itself
        (`SourceFloors`, kept in the codec's `adapter`), and the core never sees them. With
        the core floor at `T0 - m` the core never scans and never calls the window "cut";
        the per-source cut is judged by `phase_a.finish`.

        A refusal comes back as a ready `ScopeAction` (`WINDOW_TOO_SMALL` or no monitored
        source), moved only through `next_state`.
        """
        if monitored == 0:
            return _refusal(HistoryTrigger.NOTHING_TO_READ, phase_a.NO_MONITORED_REASON)
        fitted = fit_family_window(HISTORY_FAMILY, self.settings)
        if fitted.days is None:
            return _refusal(HistoryTrigger.WINDOW_TOO_SMALL, fitted.reason)
        # The operator's answer and the settings are both upper bounds of the window.
        requested = min(decision.days or fitted.days, fitted.days)
        fit = fit_window(
            requested,
            lambda days: phase_a.estimate_searches(days, monitored, ps),
            end=anchor,
            settings=ps,
            searches_per_tick=self.settings.history_backfill_searches_per_tick,
        )
        if fit.refused or fit.days_fitted is None or fit.target_start is None:
            return _refusal(HistoryTrigger.WINDOW_TOO_SMALL, fit.reason)
        reason = "; ".join(
            part for part in (fitted.reason, fit.reason if fit.reduced else None) if part
        ) or fit.reason
        progress = new_progress(fit.target_start, row.started_at or now, ps)
        progress = replace(progress, floor=fit.target_start - ps.margin, scan_cursor=None)
        return progress, fit.days_fitted, reason

    def _step_values(
        self,
        instance_id: uuid.UUID,
        row: ScopeRowView,
        progress: ScopeProgress,
        adapter: Mapping[str, Any],
        floors: SourceFloors,
        ps: PlannerSettings,
        reason: str,
    ) -> dict[str, Any]:
        """The scope row after a step that left the scope `READING`."""
        earliest = phase_a.earliest_floor(
            floors, floors.first_buckets, target_start=progress.target_start, settings=ps
        )
        return {
            **_scope_mapping(instance_id, row),
            "state": HistoryState.READING.value,
            "target_start": progress.target_start,
            "floor": earliest,
            "through": progress.through,
            "reason": bounded_reason(reason),
            "progress": encode_progress(progress, {**adapter, **floors.encode()}),
        }

    def _finish_values(
        self,
        instance: QRadarInstance,
        decision: DecisionView,
        row: ScopeRowView,
        progress: ScopeProgress,
        adapter: Mapping[str, Any],
        floors: SourceFloors,
        known: list[str],
        ps: PlannerSettings,
        trigger: HistoryTrigger,
        reason: str,
        now: datetime,
    ) -> dict[str, Any]:
        """The scope row of a reading that ends: the state comes from `next_state`."""
        trigger, reason = phase_a.finish(
            trigger, reason, floors, known=known, target_start=progress.target_start, settings=ps
        )
        change = next_state(HistoryState.READING, trigger)
        read = phase_a.read_sources(floors, known)
        pending = phase_a.merge_pending(phase_a.pending_of(adapter), read)
        stepped = self._step_values(
            instance.id,
            row,
            progress,
            {**adapter, phase_a.REBUILD_PENDING_KEY: pending},
            floors,
            ps,
            reason,
        )
        action = ScopeAction(ActionKind.TRANSITION, change.state, trigger, reason or change.reason)
        return transition_row(
            action,
            instance_id=instance.id,
            family=HISTORY_FAMILY,
            scope_key=HISTORY_SCOPE_KEY,
            row=_view_of(stepped),
            decision=decision,
            at=now,
        )

    async def _rebuild_pending(
        self,
        instance: QRadarInstance,
        current: ScopeRowView,
        values: dict[str, Any] | None,
        now: datetime,
        report: HistoryReport,
    ) -> dict[str, Any] | None:
        """Rebuild the baselines of the next batch of sources a finished reading read.

        At most `history_backfill_baseline_sources_per_tick` sources, and they leave the
        list in the same write as the rebuild. The statements are constant per source
        rebuilt (`BaselineBuilder.rebuild_for_source`), never per bucket of history.
        """
        decoded = decode_scope(current)
        if decoded is None:
            return None
        pending = phase_a.pending_of(decoded.adapter)
        if not pending:
            return None
        batch, rest = phase_a.next_rebuild_batch(
            pending, self.settings.history_backfill_baseline_sources_per_tick
        )
        sources = (
            await self.session.scalars(
                select(LogSource).where(
                    LogSource.instance_id == instance.id,
                    LogSource.qradar_id.in_([int(key) for key in batch]),
                )
            )
        ).all()
        builder = BaselineBuilder(self.session, settings=self.settings)
        for source in sources:
            await builder.rebuild_for_source(source, now=now)
        report.rebuilt = len(sources)
        adapter = {**decoded.adapter, phase_a.REBUILD_PENDING_KEY: rest}
        base = values if values is not None else _scope_mapping(instance.id, current)
        return {**base, "progress": encode_progress(decoded.progress, adapter)}

    async def _log_source_id_map(self, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
        rows = await self.session.execute(
            select(LogSource.qradar_id, LogSource.id).where(
                LogSource.instance_id == instance_id
            )
        )
        return {row[0]: row[1] for row in rows.all()}

    async def _get_or_create_watermark(self, instance: QRadarInstance) -> CollectionWatermark:
        wm = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if wm is None:
            wm = CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME)
            self.session.add(wm)
            await self.session.flush()
        return wm


# ------------------------------------------------------------------ history helpers
def _decision_view(row: HistoryBackfillDecision | None) -> DecisionView | None:
    if row is None:
        return None
    return DecisionView(
        answer=row.answer,
        days=row.days,
        start_requested_at=row.start_requested_at,
        start_requested_by=row.start_requested_by,
        stop_requested_at=row.stop_requested_at,
        stop_requested_by=row.stop_requested_by,
    )


_VIEW_FIELDS = (
    "days_requested",
    "days_target",
    "target_start",
    "floor",
    "through",
    "anchor",
    "started_at",
    "finished_at",
    "reason",
    "progress",
)


def _row_view(row: HistoryBackfillScope | None) -> ScopeRowView | None:
    if row is None:
        return None
    return ScopeRowView(
        state=HistoryState(row.state), **{name: getattr(row, name) for name in _VIEW_FIELDS}
    )


def _view_of(values: Mapping[str, Any]) -> ScopeRowView:
    """The lifecycle's view of a scope row mapping that is about to be written."""
    return ScopeRowView(
        state=HistoryState(values["state"]), **{name: values[name] for name in _VIEW_FIELDS}
    )


def _scope_mapping(instance_id: uuid.UUID, row: ScopeRowView) -> dict[str, Any]:
    """A scope row's full column mapping, for the updates no lifecycle helper covers.

    `new_scope_row` and `transition_row` build every create and every state change. A step
    that leaves the scope `READING` only moves `through`, `floor`, `reason` and `progress`,
    which the lifecycle has no helper for; it starts from the row as it is.
    """
    return {
        "instance_id": instance_id,
        "family": HISTORY_FAMILY.value,
        "scope_key": HISTORY_SCOPE_KEY,
        "state": row.state.value,
        **{name: getattr(row, name) for name in _VIEW_FIELDS},
    }


def _needs_anchor(row: ScopeRowView | None, decision: DecisionView) -> bool:
    """True when `scope_action` can use the oldest live bucket (create, wake, restart)."""
    if row is None or row.state is HistoryState.WAITING:
        return True
    if row.state in (HistoryState.COMPLETE, HistoryState.PARTIAL, HistoryState.UNAVAILABLE):
        started = decision.start_requested_at
        return started is not None and row.finished_at is not None and started > row.finished_at
    return False


def _ends_reading(action: ScopeAction) -> bool:
    return action.trigger in (HistoryTrigger.SWITCHED_OFF, HistoryTrigger.STOPPED_BY_OPERATOR)


def _with_pending(row: ScopeRowView) -> ScopeRowView:
    """The row with the sources it read already listed for a baseline rebuild.

    A reading ended by the switch or an operator keeps the rows it wrote, and they are
    learning evidence like any other: the sources it reached are rebuilt too.
    """
    decoded = decode_scope(row)
    if decoded is None:
        return row
    floors = SourceFloors.decode(decoded.adapter)
    pending = phase_a.merge_pending(
        phase_a.pending_of(decoded.adapter), phase_a.read_sources(floors, floors.first_buckets)
    )
    adapter = {**decoded.adapter, phase_a.REBUILD_PENDING_KEY: pending}
    return replace(row, progress=encode_progress(decoded.progress, adapter))


def _refusal(trigger: HistoryTrigger, reason: str | None) -> ScopeAction:
    """A reading that ends before it starts (`UNAVAILABLE`), through `next_state`."""
    change = next_state(HistoryState.READING, trigger)
    return ScopeAction(ActionKind.TRANSITION, change.state, trigger, reason or change.reason)


def _span_starts(span: Span, settings: PlannerSettings) -> list[datetime]:
    return [span.start + index * settings.bucket for index in range(span.buckets(settings))]
