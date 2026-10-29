"""Turn scheduled-search outcomes into alerts.

Two independent alertable conditions per search, each with its own fingerprint
so they never deduplicate into one another (app/alerts/fingerprint.py):

  * ``search_threshold`` — a completed run whose aggregated result crossed the
    search's configured threshold. Recovers when a later run completes under
    the threshold, which resolves the open alert. A truncated run only holds a
    lower bound: it may open or keep a breach, but never resolves one.
  * ``search_failure`` — the search itself has failed to run
    ``search_failure_alert_after`` times consecutively. A scheduled detection
    that has quietly stopped executing produces no results and therefore no
    threshold breach, so without this it would fail silently — the exact blind
    spot this platform exists to close. Recovers on the next successful run.

Deduplication, occurrence counting and the OPEN/RESOLVED transitions are all
owned by AlertService; this module only decides *which* condition holds. The
enqueuer is optional and injected, mirroring AnomalyEngine, so unit tests can
drive the logic without a notification stack.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.dispatcher import AlertEnqueuer
from app.alerts.fingerprint import search_failure_fingerprint, search_threshold_fingerprint
from app.alerts.service import AlertInput, AlertResult, AlertService
from app.core.config import Settings, get_settings
from app.i18n.messages import MessageRef, render
from app.models.enums import ExecutionStatus, Severity
from app.models.search import ScheduledSearch, SearchExecution


class SearchAlertEvaluator:
    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        enqueuer: AlertEnqueuer | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.alerts = AlertService(session, clock=self._clock)
        # Optional object with `async enqueue(alert, transition)`; None in unit
        # tests, an enqueue-only NotificationDispatcher in the worker.
        self._enqueuer = enqueuer

    async def evaluate(
        self, search: ScheduledSearch, execution: SearchExecution
    ) -> list[AlertResult]:
        """Apply both conditions to one finished execution."""
        results: list[AlertResult] = []
        for result in (
            await self._evaluate_threshold(search, execution),
            await self._evaluate_failure(search, execution),
        ):
            if result is None:
                continue
            results.append(result)
            await self._notify(result)
        return results

    # ------------------------------------------------------------- threshold
    async def _evaluate_threshold(
        self, search: ScheduledSearch, execution: SearchExecution
    ) -> AlertResult | None:
        # Only a completed run carries a trustworthy result count. A failed run
        # says nothing about the threshold and must not resolve a breach.
        if execution.status != ExecutionStatus.COMPLETED:
            return None
        if search.threshold_value is None:
            return None

        fingerprint = search_threshold_fingerprint(search.id)
        if not execution.threshold_breached:
            # A truncated result is a lower bound: "not breached" is no verdict
            # on it, so it neither opens nor resolves anything.
            if execution.truncated:
                return None
            return await self.alerts.resolve_by_fingerprint(
                fingerprint,
                actor="search-scheduler",
                reason_message=MessageRef("alert.search.threshold.recovered"),
            )

        observed = float(execution.result_count or 0)
        title_message = MessageRef("alert.threshold.crossed", {"name": search.name})
        description_message = MessageRef(
            (
                "alert.search.threshold.lower_bound"
                if execution.truncated
                else "alert.search.threshold.exact"
            ),
            {
                "name": search.name,
                "observed": observed,
                "operator": search.threshold_operator,
                "threshold": search.threshold_value,
            },
        )
        return await self.alerts.open_or_update(
            AlertInput(
                fingerprint=fingerprint,
                title=render(title_message),
                title_message=title_message,
                severity=search.severity,
                source_type="scheduled_search",
                source_id=search.id,
                description=render(description_message),
                description_message=description_message,
                evidence={
                    "observed_value": observed,
                    "expected_value": search.threshold_value,
                    "operator": search.threshold_operator,
                    "execution_id": str(execution.id),
                    "query_version": execution.query_version,
                    "truncated": execution.truncated,
                },
                context={
                    "search": search.name,
                    "owner": search.owner,
                    "category": search.category,
                },
            )
        )

    # --------------------------------------------------------------- failure
    async def _evaluate_failure(
        self, search: ScheduledSearch, execution: SearchExecution
    ) -> AlertResult | None:
        fingerprint = search_failure_fingerprint(search.id)

        if execution.status == ExecutionStatus.COMPLETED:
            return await self.alerts.resolve_by_fingerprint(
                fingerprint,
                actor="search-scheduler",
                reason_message=MessageRef("alert.search.failure.recovered"),
            )

        if search.consecutive_failures < self.settings.search_failure_alert_after:
            return None

        title_message = MessageRef("alert.search.failure.title", {"name": search.name})
        description_message = MessageRef(
            "alert.search.failure.description",
            {
                "name": search.name,
                "failures": search.consecutive_failures,
                "error_type": str(execution.error_type or execution.status),
            },
        )
        return await self.alerts.open_or_update(
            AlertInput(
                fingerprint=fingerprint,
                # A detection that is not running is a control failure, not a
                # finding; severity is fixed rather than inherited from the
                # search's own finding severity.
                title=render(title_message),
                title_message=title_message,
                severity=Severity.HIGH,
                source_type="scheduled_search",
                source_id=search.id,
                description=render(description_message),
                description_message=description_message,
                evidence={
                    "observed_value": float(search.consecutive_failures),
                    "reason": execution.error_message,
                    "error_type": execution.error_type,
                    "status": str(execution.status),
                    "execution_id": str(execution.id),
                },
                context={
                    "search": search.name,
                    "owner": search.owner,
                    "category": search.category,
                },
            )
        )

    async def _notify(self, result: AlertResult) -> None:
        # A transition of None means the alert was only refreshed — that is the
        # dedup guarantee, and it must not produce a second notification.
        if self._enqueuer is not None and result.transition is not None:
            await self._enqueuer.enqueue(result.alert, result.transition)
