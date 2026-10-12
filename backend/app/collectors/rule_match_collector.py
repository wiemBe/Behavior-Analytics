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
    that matched: one delete, one bulk upsert, one window upsert.
  * Idempotent: re-collecting a window replaces its rows rather than adding to
    them.
  * Every attempted window leaves a `rule_match_window` row. A provider failure
    writes a FAILED window and *no* rows: a failed search must never read as
    "nothing matched".
"""

from __future__ import annotations

import logging
import math
import uuid
from collections import deque
from collections.abc import AsyncGenerator, Callable
from contextlib import aclosing
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
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
    error: str | None = None
    duration_ms: int = 0
    watermark_at: datetime | None = None


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
            # between windows of one run in any way that matters here.
            rule_ids = await self._rule_id_map(instance.id) if starts else {}

            async with aclosing(
                self._search_windows(starts, window_seconds, rule_ids, report)
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
            },
        )
        return report

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
                " status, rows_returned, row_cap, unknown_ids, duration_ms, query_version, "
                " collected_at, attempts, last_error) "
                "SELECT CAST(:i AS uuid), gs, CAST(:w AS integer), 'FAILED', 0, "
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
        return dict(rows.tuples().all())

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
            k: stmt.excluded[k] for k in values if k not in ("instance_id", "window_start")
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
        return dict(rows.tuples().all())

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
