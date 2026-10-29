"""Execute a stored, approved ScheduledSearch against a provider's Ariel API.

Responsibilities and the guarantees they satisfy:

  * Idempotency: one SearchExecution per (search_id, run_key). Retries reuse the
    row, so retries never create duplicate execution records (also enforced by a
    DB unique constraint).
  * Never executes frontend-supplied AQL: only the stored query string of a
    persisted search is run, and it is re-validated defensively here too.
  * Enforces timeout, max time range and max rows, and holds one cross-process
    Ariel permit for the whole search lifecycle (a refusal is a CAPACITY failure).
  * Polls Ariel rather than using QRadar's blocking wait, cancelling on timeout.
  * Retries retryable failures with exponential backoff + jitter, up to a cap.
  * Records QRadar search id, lifecycle state, duration, typed failure reason and
    retry count.
  * Stores aggregated result metrics only — never raw events.

Clock and sleep are injected so lifecycle/timeout/retry tests are deterministic.
"""

from __future__ import annotations

import random
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.i18n.messages import MessageRef
from app.models.enums import ExecutionErrorType, ExecutionStatus
from app.models.search import ScheduledSearch, SearchExecution, SearchResultMetric
from app.providers.base import ProviderCapability, QRadarProvider
from app.providers.dto import ArielSearchStatusDTO
from app.services.aql_validator import validate_aql
from app.services.ariel_errors import (
    ArielSearchFailed,
    ArielTimeout,
    ResultParsingError,
    classify,
    failure_ref,
    is_retryable,
)
from app.services.ariel_permit import (
    ArielPermitBudget,
    ArielPermitUnavailable,
    ariel_background_priority,
)
from app.services.backoff import backoff_delay

Clock = Callable[[], datetime]
Sleep = Callable[[float], Awaitable[None]]


def _utcnow() -> datetime:
    return datetime.now(UTC)


class SearchExecutor:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        permit: ArielPermitBudget | None,
        *,
        settings: Settings | None = None,
        clock: Clock = _utcnow,
        sleep: Sleep | None = None,
        rng: random.Random | None = None,
        commit_progress: bool = False,
    ) -> None:
        self.session = session
        # True only for the scheduler's cycle: it commits the RUNNING row, the Ariel
        # search id and each retry mark, so no transaction stays open while a search
        # runs or backs off and the API can see the run. Everyone else (the manual
        # run route) keeps the caller's transaction and the executor only flushes.
        self.commit_progress = commit_progress
        self.provider = provider
        # The cross-process Ariel permit (docs/ARIEL-PERMIT.md §P.4). None means no
        # enforcement, as for the provider; production callers always pass one.
        self.permit = permit
        self.settings = settings or get_settings()
        self.clock = clock
        self._rng = rng or random.Random()  # noqa: S311 - jitter, not crypto
        if sleep is None:
            import asyncio

            sleep = asyncio.sleep
        self.sleep = sleep

    async def execute(
        self,
        search: ScheduledSearch,
        *,
        run_key: str,
        instance_key: str,
        trigger: str = "SCHEDULED",
        triggered_by: str | None = None,
    ) -> SearchExecution:
        execution = await self._get_or_create_execution(
            search, run_key=run_key, trigger=trigger, triggered_by=triggered_by
        )
        # Idempotent short-circuit: a completed run is never re-run.
        if execution.status == ExecutionStatus.COMPLETED:
            return execution

        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            return self._fail(
                execution,
                ExecutionErrorType.VALIDATION,
                "configured provider cannot execute AQL",
                ref=MessageRef("search.run.no_aql_execution"),
            )

        max_attempts = self.settings.ariel_max_retries + 1
        while True:
            execution.status = ExecutionStatus.RUNNING
            execution.started_at = execution.started_at or self.clock()
            execution.error_type = None
            execution.error_message = None
            execution.error_message_i18n = None
            await self.session.flush()
            await self._checkpoint()

            start = self.clock()
            try:
                results = await self._run_once(search, execution)
            except Exception as exc:
                error_type = classify(exc)
                # attempt number that just failed (1-based): retries so far + 1.
                attempt = execution.retry_count + 1
                if not is_retryable(error_type) or attempt >= max_attempts:
                    return self._fail(
                        execution, error_type, str(exc), final=True, ref=failure_ref(exc)
                    )

                # Retryable: record this retry and schedule the next attempt with
                # backoff + jitter.
                execution.retry_count = attempt
                delay = backoff_delay(
                    attempt,
                    base_seconds=self.settings.ariel_retry_base_seconds,
                    max_seconds=self.settings.ariel_retry_max_seconds,
                    rng=self._rng,
                )
                execution.next_retry_at = self._add_seconds(self.clock(), delay)
                execution.error_type = error_type
                execution.error_message = str(exc)
                execution.error_message_i18n = _wire(failure_ref(exc))
                await self.session.flush()
                await self._checkpoint()
                await self.sleep(delay)
                continue

            # Success.
            duration = (self.clock() - start).total_seconds()
            execution.duration_ms = int(duration * 1000)
            execution.completed_at = self.clock()
            execution.result_count = results["total_count"]
            execution.truncated = results["truncated"]
            execution.ariel_status = "COMPLETED"
            execution.status = ExecutionStatus.COMPLETED
            await self._store_metrics(search, execution, results)
            self._apply_threshold(
                search, execution, results["total_count"], truncated=results["truncated"]
            )
            await self.session.flush()
            return execution

    async def _checkpoint(self) -> None:
        if self.commit_progress:
            await self.session.commit()

    # ------------------------------------------------------------------ steps
    async def _run_once(self, search: ScheduledSearch, execution: SearchExecution) -> dict:
        # Defensive re-validation: only stored queries reach here, but validating
        # again guarantees a query that slipped past an older policy is caught.
        validate_aql(
            search.aql_query,
            max_time_range_hours=search.max_time_range_hours,
            max_result_rows=search.max_result_rows,
            settings=self.settings,
        )

        with ariel_background_priority():
            async with self._hold_permit():
                handle = await self.provider.create_ariel_search(search.aql_query)
                # The row's ariel_search_id is nullable (a search is created before
                # the id exists), but past this point the id is always set; thread
                # the non-optional value through rather than re-reading the column.
                search_id = handle.search_id
                execution.ariel_search_id = search_id
                execution.ariel_status = handle.status
                await self.session.flush()
                await self._checkpoint()

                status = await self._poll_until_terminal(search, execution, search_id)
                if status.status == "ERROR":
                    # Ariel's own error texts are free text; only the empty case is a fixed
                    # sentence, and it has a code.
                    raise ArielSearchFailed(
                        "; ".join(status.error_messages) or MessageRef("search.run.failed")
                    )

                try:
                    results = await self.provider.get_ariel_search_results(
                        handle.search_id, max_rows=search.max_result_rows
                    )
                except (ArielTimeout, ArielSearchFailed):
                    raise
                except Exception as exc:
                    raise ResultParsingError(
                        MessageRef("search.run.parse_failed", {"detail": str(exc)})
                    ) from exc

                return {
                    "columns": results.columns,
                    "rows": results.rows,
                    "total_count": results.total_count,
                    "truncated": results.truncated,
                }

    @asynccontextmanager
    async def _hold_permit(self) -> AsyncIterator[None]:
        """Hold one Ariel permit for the whole lifecycle: create, poll, results, cancel.

        A refusal raises before anything is sent to QRadar; `classify` names it
        CAPACITY and it is not retried.
        """
        if self.permit is None:
            yield
            return
        async with self.permit.permit() as held:
            if held is None:
                raise ArielPermitUnavailable(
                    self.permit.wait_seconds,
                    self.permit.instance_limit,
                    self.permit.global_limit,
                )
            yield

    async def _poll_until_terminal(
        self, search: ScheduledSearch, execution: SearchExecution, search_id: str
    ) -> ArielSearchStatusDTO:
        timeout = min(search.timeout_seconds, self.settings.ariel_max_timeout_seconds)
        poll = self.settings.ariel_poll_interval_seconds
        start = self.clock()
        while True:
            status = await self.provider.get_ariel_search_status(search_id)
            execution.ariel_status = status.status
            if status.is_terminal:
                return status
            elapsed = (self.clock() - start).total_seconds()
            if elapsed >= timeout:
                await self._safe_cancel(search_id)
                raise ArielTimeout(MessageRef("search.run.timeout", {"timeout": timeout}))
            await self.sleep(poll)

    async def _safe_cancel(self, search_id: str) -> None:
        # Best-effort cancel while already handling a timeout; failure here must
        # not mask the original error.
        import contextlib

        with contextlib.suppress(Exception):
            await self.provider.cancel_ariel_search(search_id)

    async def _store_metrics(
        self, search: ScheduledSearch, execution: SearchExecution, results: dict
    ) -> None:
        bucket = _floor_minute(execution.completed_at or self.clock())
        # Store the total only when it was measured. A truncated result's count
        # is a cap, not a measurement, and the execution row already says so.
        # Aggregated only — never raw events.
        if not results["truncated"]:
            self.session.add(
                SearchResultMetric(
                    execution_id=execution.id,
                    search_id=search.id,
                    bucket_start=bucket,
                    metric_key="total",
                    value=float(results["total_count"]),
                    dimensions={},
                )
            )
        # Grouped rows become per-dimension metrics (bounded by max_result_rows).
        for row in results["rows"]:
            key, value = _row_to_metric(row)
            if key is None:
                continue
            self.session.add(
                SearchResultMetric(
                    execution_id=execution.id,
                    search_id=search.id,
                    bucket_start=bucket,
                    metric_key=key[:255],
                    value=value,
                    dimensions=row,
                )
            )

    def _apply_threshold(
        self,
        search: ScheduledSearch,
        execution: SearchExecution,
        value: int,
        *,
        truncated: bool = False,
    ) -> None:
        if search.threshold_value is None:
            execution.threshold_breached = False
            return
        op = search.threshold_operator
        t = search.threshold_value
        if truncated:
            # `value` is a lower bound on the true count, and an exact one: the
            # provider capped total_count at max_result_rows and set truncated
            # only because more than max_result_rows rows existed, so the proven
            # bound is value + 1. Only "greater than" comparisons can be proven
            # from it; for LT, LE and EQ (and any unknown operator) there is no
            # verdict, recorded as not breached. The alert evaluator reads
            # `truncated` to keep that from resolving.
            bound = value + 1
            execution.threshold_breached = bool(
                {"GT": bound > t, "GE": bound >= t}.get(op, False)
            )
            return
        breached = {
            "GT": value > t,
            "GE": value >= t,
            "LT": value < t,
            "LE": value <= t,
            "EQ": value == t,
        }.get(op, value > t)
        execution.threshold_breached = bool(breached)

    # ------------------------------------------------------------- persistence
    async def _get_or_create_execution(
        self, search: ScheduledSearch, *, run_key: str, trigger: str, triggered_by: str | None
    ) -> SearchExecution:
        existing = await self.session.scalar(
            select(SearchExecution).where(
                SearchExecution.search_id == search.id,
                SearchExecution.run_key == run_key,
            )
        )
        if existing is not None:
            return existing
        execution = SearchExecution(
            search_id=search.id,
            run_key=run_key,
            trigger=trigger,
            triggered_by=triggered_by,
            query_version=search.query_version,
            status=ExecutionStatus.PENDING,
        )
        self.session.add(execution)
        await self.session.flush()
        return execution

    def _fail(
        self,
        execution: SearchExecution,
        error_type: ExecutionErrorType,
        message: str,
        *,
        final: bool = True,
        ref: MessageRef | None = None,
    ) -> SearchExecution:
        execution.error_type = error_type.value
        execution.error_message = message[:2000]
        # A coded sentence is never truncated: when the text had to be cut, the code would
        # describe more than the text says, so the row keeps the cut text and no code.
        execution.error_message_i18n = _wire(ref) if len(message) <= 2000 else None
        execution.completed_at = self.clock()
        if error_type == ExecutionErrorType.TIMEOUT:
            execution.status = ExecutionStatus.TIMEOUT
        elif error_type == ExecutionErrorType.CANCELLED:
            execution.status = ExecutionStatus.CANCELLED
        else:
            execution.status = ExecutionStatus.FAILED
        return execution

    def _add_seconds(self, when: datetime, seconds: float) -> datetime:
        from datetime import timedelta

        return when + timedelta(seconds=seconds)


def _wire(ref: MessageRef | None) -> dict[str, object] | None:
    return None if ref is None else ref.to_wire()


def execution_instance_key(execution: SearchExecution, search: ScheduledSearch) -> str:
    # Searches are not yet instance-scoped in the model, so every run is "default".
    # The permit callers therefore build a global-only budget (instance_id=None);
    # when searches get real instance ids, a per-console budget binds without
    # further change to the executor.
    return "default"


def _floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _row_to_metric(row: dict) -> tuple[str | None, float]:
    """Turn one aggregated result row into (metric_key, value).

    Convention: the first non-numeric column is the grouping label; a column
    named like a count supplies the value, else the row counts as 1.
    """
    if not row:
        return None, 0.0
    label = None
    value = 1.0
    for col, val in row.items():
        count_cols = {"count", "event_count", "total", "cnt"}
        if isinstance(val, (int, float)) and col.lower() in count_cols:
            value = float(val)
        elif label is None and isinstance(val, str):
            label = val
    if label is None:
        # Fall back to the first column's stringified value.
        first_col = next(iter(row))
        label = str(row[first_col])
    return label, value
