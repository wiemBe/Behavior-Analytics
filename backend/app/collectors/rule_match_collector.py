"""Measured rule matches (Phase E.1a): keyed `creEventList` searches over windows.

A run collects every settled window since its watermark with one keyed Ariel
search (`starttime / window_ms`), splitting it in halves while it is truncated.
A single window is searched by the plain per-window query.

Guarantees:
  * Read-only: aggregate searches over ``events``, nothing kept but per-rule
    counts.
  * No overlapping runs for one instance: the advisory lock is keyed on
    (instance, collector); a second worker skips.
  * Window-aligned, with a settle lag: a window is collected only once it ended
    `rule_match_settle_lag_seconds` ago.
  * Watermark-driven, with bounded catch-up: at most
    `rule_match_max_windows_per_run` windows a run when the cadence is no longer
    than a window, plus the extra windows of one cadence beyond that, oldest first.
  * Equivalent to the per-window path: the rows and window records written are
    the ones one search per window would write (only `collected_at`,
    `duration_ms` and the query version of a multi-window search differ). A
    truncated search keeps nothing; its halves are searched instead.
  * A constant number of statements per window, whatever the number of rules
    that matched: one SQL deletion, one bulk upsert, one window upsert.
  * Idempotent: re-collecting a window replaces its rows rather than adding to
    them.
  * Every attempted window leaves a `rule_match_window` row. A provider failure
    writes a FAILED window and *no* rows: a failed search must never read as
    "nothing matched".
"""

from __future__ import annotations

import logging
import math
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.history import (
    ActionKind,
    ConsoleMode,
    DecisionView,
    HistoryFamily,
    HistoryOrigin,
    HistoryState,
    HistoryTrigger,
    LifecycleError,
    ScopeAction,
    ScopeRowView,
    bounded_reason,
    console_mode,
    encode_progress,
    fit_family_window,
    history_search_budget,
    interpret_scan,
    is_live_clause,
    marker_values,
    new_progress,
    new_scope_row,
    next_state,
    scope_action,
    scope_progress,
    transition_row,
)
from app.history.planner import (
    PlannerInputError,
    ScanOutcome,
    ScopeProgress,
    SearchStatus,
    Span,
    StepAction,
    StepBudget,
    plan_step,
    settle_step,
)
from app.history.progress_codec import ProgressCodecError
from app.history.rule_match import (
    RULE_MATCH_SCOPE_KEY,
    ChunkFold,
    failed_chunk,
    failed_scan,
    fitted_window,
    fold_chunk,
    planner_settings,
    scan_result,
    scope_base,
    step_scope_row,
)
from app.models.enums import SearchFamily
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.models.rule import (
    AnalyticsRule,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.providers.base import ProviderCapability, ProviderError, QRadarProvider
from app.repositories.history_backfill import HistoryBackfillRepository
from app.rules.matches import (
    MatchFold,
    MatchRowToWrite,
    WindowRecord,
    failed_window,
    floor_to_window,
    fold_rule_matches,
)
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService

logger = logging.getLogger("app.collectors.rule_match")

COLLECTOR_NAME = "rule_match"
#: Used for a FAILED window, which has no batch to name the query version.
FAILED_QUERY_VERSION = "rule_match/v1"
SKIPPED_REASON = "skipped: beyond the catch-up horizon"


@dataclass
class _WindowOutcome:
    """What searching one window came to: a fold, or the failure that ended it."""

    start: datetime
    fold: MatchFold | None = None
    duration_ms: int | None = None
    error: ProviderError | None = None


@dataclass
class RuleMatchCollectionReport:
    instance_id: uuid.UUID
    windows_attempted: int = 0
    windows_complete: int = 0
    windows_truncated: int = 0
    windows_failed: int = 0
    windows_skipped: int = 0
    #: Ariel searches sent, failed ones included.
    searches_sent: int = 0
    #: Times a search was divided in halves (truncated or failed).
    splits: int = 0
    rows_written: int = 0
    unknown_ids: int = 0
    skipped_locked: bool = False
    unsupported: bool = False
    partial_failure: bool = False
    #: The history step's outcome, which never changes the live report's status.
    history_state: str | None = None
    history_reason: str | None = None
    history_searches: int = 0
    history_windows_written: int = 0
    history_rows_written: int = 0
    history_error: str | None = None
    error: str | None = None
    duration_ms: int = 0
    watermark_at: datetime | None = None


def _value(row: object, name: str) -> object:
    if isinstance(row, Mapping):
        return row[name]
    return getattr(row, name)


def _decision_view(row: object | None) -> DecisionView | None:
    """The lifecycle's view of a decision row, never the ORM object itself."""
    if row is None:
        return None
    return DecisionView(
        answer=str(_value(row, "answer")),
        days=_value(row, "days"),  # type: ignore[arg-type]
        start_requested_at=_value(row, "start_requested_at"),  # type: ignore[arg-type]
        start_requested_by=_value(row, "start_requested_by"),  # type: ignore[arg-type]
        stop_requested_at=_value(row, "stop_requested_at"),  # type: ignore[arg-type]
        stop_requested_by=_value(row, "stop_requested_by"),  # type: ignore[arg-type]
    )


def _scope_view(row: object | None) -> ScopeRowView | None:
    """The lifecycle's view of a scope row, whether stored or newly built."""
    if row is None:
        return None
    progress = _value(row, "progress")
    return ScopeRowView(
        state=HistoryState(str(_value(row, "state"))),
        days_requested=_value(row, "days_requested"),  # type: ignore[arg-type]
        days_target=_value(row, "days_target"),  # type: ignore[arg-type]
        target_start=_value(row, "target_start"),  # type: ignore[arg-type]
        floor=_value(row, "floor"),  # type: ignore[arg-type]
        through=_value(row, "through"),  # type: ignore[arg-type]
        anchor=_value(row, "anchor"),  # type: ignore[arg-type]
        started_at=_value(row, "started_at"),  # type: ignore[arg-type]
        finished_at=_value(row, "finished_at"),  # type: ignore[arg-type]
        reason=_value(row, "reason"),  # type: ignore[arg-type]
        progress=dict(progress) if isinstance(progress, Mapping) else {},
    )


class RuleMatchCollector:
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

    async def collect(self, instance: QRadarInstance) -> RuleMatchCollectionReport:
        started = self._clock()
        report = RuleMatchCollectionReport(instance_id=instance.id)

        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            # Nothing was attempted, so nothing is recorded: a FAILED window per
            # tick for a provider that can never answer would be noise.
            report.unsupported = True
            report.error = "provider cannot run Ariel searches"
            return report

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            window_seconds = self.settings.rule_match_window_seconds
            watermark = await self._get_or_create_watermark(instance)
            report.windows_skipped = await self._skip_beyond_horizon(
                instance.id, watermark, started, window_seconds
            )
            cadence = await SearchCadenceService(self.session, self.settings, self._clock).get(
                SearchFamily.RULE_MATCH
            )
            # One cadence of windows is the steady-state load of a tick. The
            # configured maximum is the per-run count when the cadence is no
            # longer than a window (cadence_windows == 1); a longer cadence adds
            # its extra windows on top (design SC.5.2).
            cadence_windows = math.ceil(cadence.cadence_seconds / window_seconds)
            starts = self._windows_to_collect(
                watermark,
                started,
                window_seconds,
                cadence_windows + self.settings.rule_match_max_windows_per_run - 1,
            )
            prior_attempts = await self._prior_attempts(instance.id, starts)
            # One lookup serves the whole run; the inventory does not change
            # between windows of one run in any way that matters here. The
            # history step reuses it, or loads it when the live pass had no
            # window to collect.
            rule_ids = await self._rule_id_map(instance.id) if starts else None

            async with aclosing(
                self._search_windows(starts, window_seconds, rule_ids or {}, report)
            ) as outcomes:
                async for outcome in outcomes:
                    start = outcome.start
                    report.windows_attempted += 1
                    if outcome.error is not None or outcome.fold is None:
                        exc = outcome.error
                        attempts = prior_attempts.get(start, 0) + 1
                        error = f"{type(exc).__name__}: {exc}"[:512]
                        failed = failed_window(
                            window_start=start,
                            window_seconds=window_seconds,
                            row_cap=self.settings.rule_match_max_rows,
                            query_version=FAILED_QUERY_VERSION,
                        )
                        await self._write_window(
                            instance.id, failed, duration_ms=None, attempts=attempts, error=error
                        )
                        report.windows_failed += 1
                        report.partial_failure = True
                        report.error = error
                        watermark.consecutive_failures += 1
                        watermark.last_error = error
                        logger.warning(
                            "rule match window failed",
                            extra={
                                "instance_id": str(instance.id),
                                "window_start": start.isoformat(),
                                "attempts": attempts,
                                "error_class": type(exc).__name__,
                            },
                        )
                        if attempts >= self.settings.rule_match_max_window_attempts:
                            # Out of attempts: the FAILED record stays (so rule
                            # health keeps reading this span as unmeasured) and the
                            # watermark moves on, so one unanswerable window cannot
                            # stall collection for good.
                            watermark.watermark_at = start
                            continue
                        # Otherwise stop: the provider is most likely down, and the
                        # watermark stays before this window so the next run retries.
                        break

                    await self._record_window(
                        instance.id,
                        outcome,
                        window_seconds,
                        prior_attempts.get(start, 0) + 1,
                        report,
                        watermark,
                    )

            watermark.last_run_at = started
            if watermark.watermark_at is not None:
                end_of_last = watermark.watermark_at + timedelta(seconds=window_seconds)
                watermark.lag_seconds = max(0, int((started - end_of_last).total_seconds()))
            watermark.collection_metadata = {
                "window_seconds": window_seconds,
                "row_cap": self.settings.rule_match_max_rows,
                "settle_lag_seconds": self.settings.rule_match_settle_lag_seconds,
            }
            report.watermark_at = watermark.watermark_at
            # History runs after the live pass, under the same lock and in the
            # same transaction, and never touches the live report's status.
            await self._history_step(
                instance,
                started,
                report,
                window_seconds=window_seconds,
                cadence_seconds=cadence.cadence_seconds,
                rule_ids=rule_ids,
            )
            await self.session.flush()

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "rule match collection complete",
            extra={
                "instance_id": str(instance.id),
                "windows_attempted": report.windows_attempted,
                "windows_complete": report.windows_complete,
                "windows_truncated": report.windows_truncated,
                "windows_failed": report.windows_failed,
                "rows_written": report.rows_written,
                "unknown_ids": report.unknown_ids,
                "duration_ms": report.duration_ms,
                "history_state": report.history_state,
                "history_searches": report.history_searches,
                "history_windows_written": report.history_windows_written,
            },
        )
        return report

    # ------------------------------------------------------------------ history

    async def _history_step(
        self,
        instance: QRadarInstance,
        now: datetime,
        report: RuleMatchCollectionReport,
        *,
        window_seconds: int,
        cadence_seconds: int,
        rule_ids: dict[int, uuid.UUID] | None,
    ) -> None:
        """Run one history step after the live pass, in the same lock and transaction.

        The console-wide scope follows the shared lifecycle: it waits for its
        first live window, then reads `[T0, anchor)` where the anchor is the
        oldest live window of the console. Nothing here can fail the live run:
        an unreadable scope row or a planner refusal is recorded on the report
        and the next tick retries from `through`.
        """
        repository = HistoryBackfillRepository(self.session)
        decision_row = (await repository.decisions([instance.id])).get(instance.id)
        decision = _decision_view(decision_row)
        mode = console_mode(decision)
        if decision is None or mode in (ConsoleMode.UNDECIDED, ConsoleMode.NO):
            report.history_reason = "the console has no Yes answer"
            return

        live_anchor = await self._oldest_live_window(instance.id)
        # The anchor is the oldest live window; align it down so a changed
        # window width can never make an already-recorded live window overlap
        # the span history writes.
        anchor = (
            None if live_anchor is None else floor_to_window(live_anchor, window_seconds)
        )
        scopes = await repository.scopes(
            instance.id, HistoryFamily.RULE_MATCH.value, [RULE_MATCH_SCOPE_KEY]
        )
        scope_row = scopes.get(RULE_MATCH_SCOPE_KEY)
        row = _scope_view(scope_row)
        action = scope_action(
            family=HistoryFamily.RULE_MATCH,
            mode=mode,
            decision=decision,
            row=row,
            has_live_data=anchor is not None,
            live_anchor=anchor,
            switch_on=bool(self.settings.history_backfill_enabled),
        )
        if row is not None:
            report.history_state = row.state.value
        report.history_reason = action.reason

        if action.kind is ActionKind.NONE:
            return
        if action.kind is ActionKind.CREATE:
            values = new_scope_row(
                action,
                instance_id=instance.id,
                family=HistoryFamily.RULE_MATCH,
                scope_key=RULE_MATCH_SCOPE_KEY,
                decision=decision,
                at=now,
            )
            await repository.write_scopes([values])
            row = _scope_view(values)
            report.history_state = action.state.value if action.state else None
            if action.state is not HistoryState.READING:
                return
        elif action.kind is ActionKind.TRANSITION:
            if row is None:  # pragma: no cover - scope_action cannot transition a missing row
                return
            values = transition_row(
                action,
                instance_id=instance.id,
                family=HistoryFamily.RULE_MATCH,
                scope_key=RULE_MATCH_SCOPE_KEY,
                row=row,
                decision=decision,
                at=now,
            )
            await repository.write_scopes([values])
            row = _scope_view(values)
            report.history_state = action.state.value if action.state else None
            report.history_reason = action.reason
            if action.state is not HistoryState.READING:
                return

        # `ActionKind.STEP`, or a create/transition that just put the scope in
        # READING: both plan a step against the live anchor.
        if row is None or anchor is None:  # pragma: no cover - READING implies both
            return
        try:
            await self._read_history(
                instance,
                row,
                decision,
                anchor,
                now,
                report,
                window_seconds=window_seconds,
                cadence_seconds=cadence_seconds,
                rule_ids=rule_ids,
            )
        except (PlannerInputError, LifecycleError, ProgressCodecError) as exc:
            # A scope row that cannot be read back is a bug or a manual edit,
            # not a reason to lose the live pass. Stop this step; the next tick
            # retries, and the operator sees the reason on the scope row.
            report.history_error = type(exc).__name__
            await repository.write_step_errors(
                instance.id,
                HistoryFamily.RULE_MATCH.value,
                {RULE_MATCH_SCOPE_KEY: exc},
                at=self._clock(),
            )
            logger.warning(
                "rule match history step failed",
                extra={
                    "instance_id": str(instance.id),
                    "error_class": type(exc).__name__,
                },
            )

    async def _read_history(
        self,
        instance: QRadarInstance,
        row: ScopeRowView,
        decision: DecisionView,
        anchor: datetime,
        now: datetime,
        report: RuleMatchCollectionReport,
        *,
        window_seconds: int,
        cadence_seconds: int,
        rule_ids: dict[int, uuid.UUID] | None,
    ) -> None:
        """One scan or read step: plan, search, settle and write the scope row."""
        settings = planner_settings(
            self.settings, window_seconds=window_seconds, cadence_seconds=cadence_seconds
        )
        begin = time.monotonic()
        limit_seconds = float(self.settings.history_backfill_seconds_per_tick)

        progress = scope_progress(row)
        days_target = row.days_target
        target_start = row.target_start
        started_at = row.started_at
        fit_reason: str | None = None

        if progress is None:
            # A fresh window (§B.6.5): the family's own lookback, reduced to
            # what the time budget allows, always ending at the anchor.
            family = fit_family_window(HistoryFamily.RULE_MATCH, self.settings)
            if not family.available or family.days is None:
                await self._finish_history(
                    instance,
                    row,
                    decision,
                    report,
                    state=next_state(HistoryState.READING, HistoryTrigger.WINDOW_TOO_SMALL).state,
                    trigger=HistoryTrigger.WINDOW_TOO_SMALL,
                    reason=family.reason or "history is unavailable for this console",
                    now=now,
                    progress=None,
                )
                return
            fitted = fitted_window(family.days, anchor=anchor, settings=settings)
            if fitted.refused or fitted.days_fitted is None or fitted.target_start is None:
                await self._finish_history(
                    instance,
                    row,
                    decision,
                    report,
                    state=next_state(HistoryState.READING, HistoryTrigger.WINDOW_TOO_SMALL).state,
                    trigger=HistoryTrigger.WINDOW_TOO_SMALL,
                    reason=fitted.reason,
                    now=now,
                    progress=None,
                )
                return
            days_target = fitted.days_fitted
            target_start = fitted.target_start
            started_at = started_at or now
            progress = new_progress(target_start, started_at, settings)
            fit_reason = fitted.reason

        # A scan may be followed by a read in the same tick, while budget and
        # time remain; a read ends the step.
        for _ in range(2):
            seconds_left = limit_seconds - (time.monotonic() - begin)
            budget = history_search_budget(
                self.settings, used=report.searches_sent + report.history_searches
            )
            plan = plan_step(
                progress,
                now=now,
                end=anchor,
                budget=StepBudget(searches=budget, seconds_left=seconds_left),
                settings=settings,
                enabled=bool(self.settings.history_backfill_enabled),
            )
            if plan.action is StepAction.WAIT:
                report.history_reason = self._with_fit(fit_reason, plan.reason)
                return
            if plan.action is StepAction.FINISH:
                trigger = plan.trigger or HistoryTrigger.FINISHED_PARTIAL
                await self._finish_history(
                    instance,
                    row,
                    decision,
                    report,
                    state=next_state(HistoryState.READING, trigger).state,
                    trigger=trigger,
                    reason=self._with_fit(fit_reason, plan.reason),
                    now=now,
                    progress=progress,
                    days_target=days_target,
                    target_start=target_start,
                    started_at=started_at,
                )
                return

            if plan.action is StepAction.SCAN:
                span = plan.scan_span
                if span is None:  # pragma: no cover - SCAN always names a span
                    return
                report.history_searches += 1
                try:
                    totals = await self.provider.get_console_event_totals(
                        span.start, span.end, bucket_seconds=window_seconds
                    )
                    result = scan_result(span, totals, settings)
                except ProviderError:
                    result = failed_scan(span)
                scan = interpret_scan(progress, [result], end=anchor, settings=settings)
                progress = scan.progress
                report.history_reason = self._with_fit(fit_reason, scan.reason)
                progress_json = encode_progress(progress)
                if scan.trigger is not None:
                    await self._finish_history(
                        instance,
                        row,
                        decision,
                        report,
                        state=next_state(HistoryState.READING, scan.trigger).state,
                        trigger=scan.trigger,
                        reason=report.history_reason,
                        now=now,
                        progress=progress,
                        days_target=days_target,
                        target_start=target_start,
                        started_at=started_at,
                    )
                    return
                await self._write_history_scope(
                    instance,
                    row,
                    decision,
                    state=HistoryState.READING,
                    trigger=None,
                    reason=report.history_reason,
                    now=now,
                    progress=progress,
                    days_target=days_target,
                    target_start=target_start,
                    started_at=started_at,
                )
                if scan.outcome is not ScanOutcome.FLOOR_FOUND:
                    report.history_state = HistoryState.READING.value
                    return
                refreshed = _scope_view(
                    {
                        **scope_base(
                            instance_id=instance.id,
                            family=HistoryFamily.RULE_MATCH,
                            scope_key=RULE_MATCH_SCOPE_KEY,
                            row=row,
                        ),
                        "state": HistoryState.READING.value,
                        "days_target": days_target,
                        "target_start": target_start,
                        "floor": progress.floor,
                        "through": progress.through,
                        "started_at": started_at,
                        "progress": progress_json,
                    }
                )
                if refreshed is None:  # pragma: no cover - the mapping is never None
                    return
                row = refreshed
                continue

            # READ: one keyed search per planned chunk, oldest first.
            results = []
            folds: dict[Span, ChunkFold] = {}
            for chunk in plan.chunks:
                if limit_seconds - (time.monotonic() - begin) <= 0:
                    break
                mapping = rule_ids
                if mapping is None:
                    mapping = await self._rule_id_map(instance.id)
                    rule_ids = mapping
                report.history_searches += 1
                begun = self._clock()
                try:
                    batch = await self.provider.get_rule_matches_windowed(
                        chunk.start,
                        chunk.end,
                        window_seconds=window_seconds,
                        max_rows=self.settings.rule_match_max_rows,
                    )
                    fold = fold_chunk(
                        batch,
                        mapping,
                        span=chunk,
                        settings=settings,
                        duration_ms=int((self._clock() - begun).total_seconds() * 1000),
                        collected_at=self._clock(),
                    )
                except ProviderError:
                    results.append(failed_chunk(chunk))
                    break
                folds[chunk] = fold
                results.append(fold.result)
                done = fold.result.status is SearchStatus.COMPLETE or (
                    fold.result.status is SearchStatus.TRUNCATED
                    and chunk.buckets(settings) == 1
                )
                if not done:
                    # A failed or truncated multi-window search keeps nothing:
                    # stop here, so no search is spent beyond the blocker.
                    break

            floor = progress.floor
            assert floor is not None
            settlement = settle_step(progress, results, end=anchor, settings=settings)
            window_rows: list[dict[str, Any]] = []
            metric_rows: list[dict[str, Any]] = []
            for span in settlement.write_spans:
                written = folds.get(span)
                if written is None:  # pragma: no cover - a written span was searched
                    continue
                window_rows.extend(written.window_rows(instance.id, floor=floor))
                metric_rows.extend(written.metric_rows(floor=floor))
            await self._write_history_rows(window_rows, metric_rows)
            report.history_windows_written += len(window_rows)
            report.history_rows_written += len(metric_rows)
            report.history_reason = self._with_fit(fit_reason, settlement.reason)

            if settlement.trigger is not None:
                await self._finish_history(
                    instance,
                    row,
                    decision,
                    report,
                    state=next_state(HistoryState.READING, settlement.trigger).state,
                    trigger=settlement.trigger,
                    reason=report.history_reason,
                    now=now,
                    progress=settlement.progress,
                    days_target=days_target,
                    target_start=target_start,
                    started_at=started_at,
                )
            else:
                await self._write_history_scope(
                    instance,
                    row,
                    decision,
                    state=HistoryState.READING,
                    trigger=None,
                    reason=report.history_reason,
                    now=now,
                    progress=settlement.progress,
                    days_target=days_target,
                    target_start=target_start,
                    started_at=started_at,
                )
                report.history_state = HistoryState.READING.value
            return

    async def _finish_history(
        self,
        instance: QRadarInstance,
        row: ScopeRowView,
        decision: DecisionView,
        report: RuleMatchCollectionReport,
        *,
        state: HistoryState,
        trigger: HistoryTrigger,
        reason: str,
        now: datetime,
        progress: ScopeProgress | None,
        days_target: int | None = None,
        target_start: datetime | None = None,
        started_at: datetime | None = None,
    ) -> None:
        """End the reading with a lifecycle state and its full scope row."""
        await self._write_history_scope(
            instance,
            row,
            decision,
            state=state,
            trigger=trigger,
            reason=reason,
            now=now,
            progress=progress,
            days_target=days_target,
            target_start=target_start,
            started_at=started_at,
        )
        report.history_state = state.value
        report.history_reason = reason

    async def _write_history_scope(
        self,
        instance: QRadarInstance,
        row: ScopeRowView,
        decision: DecisionView,
        *,
        state: HistoryState,
        trigger: HistoryTrigger | None,
        reason: str,
        now: datetime,
        progress: ScopeProgress | None,
        days_target: int | None,
        target_start: datetime | None,
        started_at: datetime | None,
    ) -> None:
        """Write the scope row: the lifecycle's columns, then the planner's fields."""
        if trigger is None:
            scope_values = scope_base(
                instance_id=instance.id,
                family=HistoryFamily.RULE_MATCH,
                scope_key=RULE_MATCH_SCOPE_KEY,
                row=row,
            )
        else:
            scope_values = transition_row(
                ScopeAction(
                    ActionKind.TRANSITION,
                    state,
                    trigger,
                    bounded_reason(reason),
                ),
                instance_id=instance.id,
                family=HistoryFamily.RULE_MATCH,
                scope_key=RULE_MATCH_SCOPE_KEY,
                row=row,
                decision=decision,
                at=now,
            )
        if progress is None:
            scope_values.update(state=state.value, reason=bounded_reason(reason))
        else:
            scope_values = step_scope_row(
                scope_values,
                state=state,
                days_target=days_target,
                target_start=target_start,
                floor=progress.floor,
                through=progress.through,
                started_at=started_at,
                finished_at=now if trigger is not None else None,
                reason=reason,
                progress=encode_progress(progress),
            )
        await HistoryBackfillRepository(self.session).write_scopes([scope_values])

    async def _write_history_rows(
        self,
        window_rows: list[dict[str, Any]],
        metric_rows: list[dict[str, Any]],
    ) -> None:
        """One `INSERT ... ON CONFLICT DO NOTHING` statement per table, whatever the span.

        The rows go through `executemany` (statement first, rows second, as
        `PwRepository.write_buckets` does): a multi-VALUES `.values(rows)` insert
        would bind one parameter per cell of every row of the step, and a large
        step (a week of windows times the rules that matched) exceeds asyncpg's
        32,767-parameter ceiling for one query. Every column is written exactly
        as before.

        There is deliberately no `DO UPDATE` for a skipped `FAILED` live window
        (`_skip_beyond_horizon`): those records are written only after the first
        live window, so a reading of `[T0, anchor)` never meets one. Repairing
        skipped windows after the anchor is a separate design (HB-5 decision,
        2026-10-19). `DO NOTHING` is also the whole live guard: a history insert
        can never replace a measured live window or one of its metric rows.
        """
        if window_rows:
            await self.session.execute(
                pg_insert(RuleMatchWindow).on_conflict_do_nothing(
                    index_elements=["instance_id", "window_start"]
                ),
                window_rows,
            )
        if metric_rows:
            await self.session.execute(
                pg_insert(RuleMatchMetric).on_conflict_do_nothing(
                    index_elements=["rule_id", "bucket_start"]
                ),
                metric_rows,
            )

    @staticmethod
    def _with_fit(fit_reason: str | None, step_reason: str) -> str:
        """The window's reason once, followed by this step's reason."""
        if fit_reason is None:
            return step_reason
        return f"{fit_reason}; {step_reason}"

    async def _oldest_live_window(self, instance_id: uuid.UUID) -> datetime | None:
        """The anchor: the oldest live `rule_match_window`, whatever its status."""
        return await self.session.scalar(
            select(RuleMatchWindow.window_start)
            .where(
                RuleMatchWindow.instance_id == instance_id,
                is_live_clause(RuleMatchWindow),
            )
            .order_by(RuleMatchWindow.window_start)
            .limit(1)
        )

    def _first_start(self, watermark: CollectionWatermark, window_seconds: int) -> datetime | None:
        """The window after the watermark, or None on a first run."""
        if watermark.watermark_at is None:
            return None
        step = timedelta(seconds=window_seconds)
        last = watermark.watermark_at
        last = last if last.tzinfo is not None else last.replace(tzinfo=UTC)
        first = floor_to_window(last + step, window_seconds)
        if first <= last:  # the window width changed under the watermark
            first += step
        return first

    async def _skip_beyond_horizon(
        self,
        instance_id: uuid.UUID,
        watermark: CollectionWatermark,
        now: datetime,
        window_seconds: int,
    ) -> int:
        """Record windows older than the catch-up horizon as FAILED, in one insert.

        They were never searched, so they are never COMPLETE and never empty:
        rule health reads any span holding one as unmeasured. The watermark then
        jumps to the horizon so collection resumes near real time.
        """
        first = self._first_start(watermark, window_seconds)
        if first is None:
            return 0
        step = timedelta(seconds=window_seconds)
        settled_until = floor_to_window(
            now - timedelta(seconds=self.settings.rule_match_settle_lag_seconds),
            window_seconds,
        )
        horizon = min(
            floor_to_window(
                settled_until - timedelta(seconds=self.settings.rule_match_max_catchup_seconds),
                window_seconds,
            ),
            settled_until - step,
        )
        if first >= horizon:
            return 0
        last_skipped = horizon - step
        await self.session.execute(
            text(
                "INSERT INTO rule_match_window (instance_id, window_start, window_seconds, "
                " origin, status, rows_returned, row_cap, unknown_ids, duration_ms, "
                " query_version, collected_at, attempts, last_error) "
                "SELECT CAST(:i AS uuid), gs, CAST(:w AS integer), CAST(:origin AS varchar), "
                " 'FAILED', 0, "
                " CAST(:cap AS integer), 0, NULL, :qv, CAST(:now AS timestamptz), 0, :err "
                "FROM generate_series(CAST(:first AS timestamptz), "
                " CAST(:last AS timestamptz), make_interval(secs => CAST(:w AS double precision))"
                ") AS gs "
                "ON CONFLICT (instance_id, window_start) DO NOTHING"
            ).bindparams(
                i=str(instance_id),
                w=window_seconds,
                cap=self.settings.rule_match_max_rows,
                qv=FAILED_QUERY_VERSION,
                now=self._clock(),
                err=SKIPPED_REASON,
                origin=marker_values(HistoryFamily.RULE_MATCH, HistoryOrigin.LIVE)["origin"],
                first=first,
                last=last_skipped,
            )
        )
        watermark.watermark_at = last_skipped
        return int((horizon - first) / step)

    async def _prior_attempts(
        self, instance_id: uuid.UUID, starts: list[datetime]
    ) -> dict[datetime, int]:
        """Attempts already spent on each FAILED window about to be collected."""
        if not starts:
            return {}
        rows = await self.session.execute(
            select(RuleMatchWindow.window_start, RuleMatchWindow.attempts).where(
                RuleMatchWindow.instance_id == instance_id,
                RuleMatchWindow.window_start.in_(starts),
                RuleMatchWindow.status == RuleMatchWindowStatus.FAILED,
            )
        )
        return dict(rows.all())

    def _windows_to_collect(
        self, watermark: CollectionWatermark, now: datetime, window_seconds: int, limit: int
    ) -> list[datetime]:
        """Settled window starts after the watermark, oldest first, at most `limit`."""
        settled_until = floor_to_window(
            now - timedelta(seconds=self.settings.rule_match_settle_lag_seconds),
            window_seconds,
        )
        step = timedelta(seconds=window_seconds)
        # First run: only the most recent settled window. Walking back through
        # history would be a backfill nobody asked for.
        first = self._first_start(watermark, window_seconds) or settled_until - step
        out: list[datetime] = []
        cursor = first
        while cursor + step <= settled_until and len(out) < limit:
            out.append(cursor)
            cursor += step
        return out

    async def _record_window(
        self,
        instance_id: uuid.UUID,
        outcome: _WindowOutcome,
        window_seconds: int,
        attempts: int,
        report: RuleMatchCollectionReport,
        watermark: CollectionWatermark,
    ) -> None:
        """Persist one answered window and move the watermark onto it."""
        fold = outcome.fold
        assert fold is not None
        report.rows_written += await self._write_rows(
            instance_id, outcome.start, window_seconds, fold.rows
        )
        await self._write_window(
            instance_id, fold.window, duration_ms=outcome.duration_ms, attempts=attempts
        )
        report.unknown_ids += fold.window.unknown_ids
        if fold.window.status == RuleMatchWindowStatus.COMPLETE:
            report.windows_complete += 1
        else:
            report.windows_truncated += 1
        watermark.watermark_at = outcome.start
        watermark.intervals_collected += 1
        watermark.consecutive_failures = 0
        watermark.last_error = None

    async def _search_windows(
        self,
        starts: list[datetime],
        window_seconds: int,
        rule_ids: dict[int, uuid.UUID],
        report: RuleMatchCollectionReport,
    ) -> AsyncGenerator[_WindowOutcome, None]:
        """Search `starts` (consecutive, oldest first), yielding one outcome per window.

        All the windows go in one keyed search. The search is divided into two
        halves of whole windows, and each half searched in turn, whenever it
        is *truncated* (a truncated search keeps nothing: `LIMIT` caps the
        result as a whole, so a window's rows may be missing) or *fails*. A
        single window is searched by the plain per-window query, with today's
        TRUNCATED semantics, and is never divided further.

        Failure attribution. A failed multi-window search charges no window:
        its windows may be answerable in halves, and a window must not be
        recorded FAILED, or spend one of its attempts, for a failure a split
        would have avoided. An attempt is spent only by a window whose own
        single-window search fails, and that is attributed exactly as the
        per-window loop does: the caller records it FAILED and either skips
        the window (out of attempts) or stops the run, leaving the windows
        after it unsearched. Windows are therefore never charged for searches
        that did not cover them alone.

        Bound: the halves form a binary tree over the windows and each node is
        searched at most once, and only after its parent was truncated or
        failed. For n windows that is at most 2n - 1 searches a run, so no
        separate cap is needed.
        """
        queue: deque[list[datetime]] = deque([starts] if starts else [])
        while queue:
            span = queue.popleft()
            report.searches_sent += 1
            if len(span) == 1:
                try:
                    fold, duration_ms = await self._fetch_window(
                        span[0], window_seconds, rule_ids
                    )
                except ProviderError as exc:
                    yield _WindowOutcome(span[0], error=exc)
                else:
                    yield _WindowOutcome(span[0], fold=fold, duration_ms=duration_ms)
                continue
            try:
                folds, duration_ms = await self._fetch_windows(span, window_seconds, rule_ids)
            except ProviderError:
                folds = None
            if folds is None:
                middle = len(span) // 2
                report.splits += 1
                queue.extendleft([span[middle:], span[:middle]])
                continue
            for start in span:
                yield _WindowOutcome(start, fold=folds[start], duration_ms=duration_ms)

    async def _fetch_windows(
        self, span: list[datetime], window_seconds: int, rule_ids: dict[int, uuid.UUID]
    ) -> tuple[dict[datetime, MatchFold] | None, int]:
        """One keyed search over `span`; None when it was truncated (nothing kept)."""
        step = timedelta(seconds=window_seconds)
        begun = self._clock()
        result = await self.provider.get_rule_matches_windowed(
            span[0],
            span[-1] + step,
            window_seconds=window_seconds,
            max_rows=self.settings.rule_match_max_rows,
        )
        duration_ms = int((self._clock() - begun).total_seconds() * 1000)
        if result.truncated:
            return None, duration_ms
        if set(result.batches) != set(span):
            # A provider that does not answer every window asked is as bad as
            # a malformed key: fail the search, so the halves are tried.
            raise ProviderError("windowed rule match search did not cover every window")
        return {
            start: fold_rule_matches(
                result.batches[start], rule_ids, window_start=start, window_seconds=window_seconds
            )
            for start in span
        }, duration_ms

    async def _fetch_window(
        self, start: datetime, window_seconds: int, rule_ids: dict[int, uuid.UUID]
    ) -> tuple[MatchFold, int]:
        end = start + timedelta(seconds=window_seconds)
        begun = self._clock()
        batch = await self.provider.get_rule_matches(
            start, end, max_rows=self.settings.rule_match_max_rows
        )
        duration_ms = int((self._clock() - begun).total_seconds() * 1000)
        fold = fold_rule_matches(
            batch, rule_ids, window_start=start, window_seconds=window_seconds
        )
        return fold, duration_ms

    async def _write_rows(
        self,
        instance_id: uuid.UUID,
        start: datetime,
        window_seconds: int,
        rows: list[MatchRowToWrite],
    ) -> int:
        """Replace this window's rows: one delete, one bulk upsert.

        The delete is what makes re-collection idempotent. Without it, a rule
        that matched in the first collection of a window but not the second
        would keep a stale count.
        """
        await self.session.execute(
            delete(RuleMatchMetric).where(
                RuleMatchMetric.bucket_start == start,
                RuleMatchMetric.rule_id.in_(
                    select(AnalyticsRule.id).where(AnalyticsRule.instance_id == instance_id)
                ),
            )
        )
        if not rows:
            return 0
        values = [
            {
                "rule_id": row.rule_id,
                "bucket_start": start,
                "bucket_seconds": window_seconds,
                "match_records": row.match_records,
                "matched_events": row.matched_events,
            }
            for row in rows
        ]
        stmt = pg_insert(RuleMatchMetric).values(values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["rule_id", "bucket_start"],
            set_={
                "bucket_seconds": stmt.excluded.bucket_seconds,
                "match_records": stmt.excluded.match_records,
                "matched_events": stmt.excluded.matched_events,
            },
        )
        await self.session.execute(stmt)
        return len(values)

    async def _write_window(
        self,
        instance_id: uuid.UUID,
        window: WindowRecord,
        *,
        duration_ms: int | None,
        attempts: int,
        error: str | None = None,
    ) -> None:
        values = {
            "instance_id": instance_id,
            "window_start": window.window_start,
            "window_seconds": window.window_seconds,
            **marker_values(HistoryFamily.RULE_MATCH, HistoryOrigin.LIVE),
            "status": window.status,
            "rows_returned": window.rows_returned,
            "row_cap": window.row_cap,
            "unknown_ids": window.unknown_ids,
            "duration_ms": duration_ms,
            "query_version": window.query_version,
            "collected_at": self._clock(),
            "attempts": attempts,
            "last_error": error,
        }
        stmt = pg_insert(RuleMatchWindow).values(values)
        update = {
            # A live write never changes the marker of an existing row.
            k: stmt.excluded[k]
            for k in values
            if k not in ("instance_id", "window_start", "origin")
        }
        stmt = stmt.on_conflict_do_update(
            index_elements=["instance_id", "window_start"],
            set_=update,
            # A failed retry never downgrades a window that was collected
            # whole: its rows are still there and still true.
            where=(
                RuleMatchWindow.status != RuleMatchWindowStatus.COMPLETE
                if window.status == RuleMatchWindowStatus.FAILED
                else None
            ),
        )
        await self.session.execute(stmt)

    async def _rule_id_map(self, instance_id: uuid.UUID) -> dict[int, uuid.UUID]:
        rows = await self.session.execute(
            select(AnalyticsRule.qradar_id, AnalyticsRule.id).where(
                AnalyticsRule.instance_id == instance_id
            )
        )
        return dict(rows.all())

    async def _get_or_create_watermark(
        self, instance: QRadarInstance
    ) -> CollectionWatermark:
        watermark = await self.session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        if watermark is None:
            watermark = CollectionWatermark(instance_id=instance.id, collector=COLLECTOR_NAME)
            self.session.add(watermark)
            await self.session.flush()
        return watermark
