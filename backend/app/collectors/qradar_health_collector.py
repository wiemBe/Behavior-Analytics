"""QRadar health in detail (D-42): two aggregating Ariel searches per console per tick.

Each run takes the last cadence window of the claimed slot and asks QRadar twice, both
aggregated in Ariel and neither selecting a payload: the shipped Health Metrics log sources
(disk, system, pipeline) and QRadar's own System Notification events. The answers are folded
(`app.health_detail.fold`) to bounded aggregates and stored for trends.

Guarantees:
  * Read-only, and only on a provider that can run Ariel searches (never MCP).
  * The metric search is sent only when the console has the custom properties it reads
    (`Metric ID`, `Value`, `Element`), checked once per run; without them the metric part is
    NOT_COLLECTED with a coded reason and the notification part still runs.
  * A part that was not measured stores no bucket, so its absence is never a zero. Its state
    (OK, PARTIAL, NOT_COLLECTED, FAILED) and coded reason go on the run row.
  * A constant number of searches and statements per run, whatever the number of hosts:
    two searches, one delete per bucket table and one chunked bulk insert per table.
  * Idempotent: a window is replaced as a whole, so a metric that left the answer leaves no
    stale row. No overlapping runs for one instance: the advisory lock is keyed on
    (instance, collector).
  * The REST half (one sample per console per run) is written by the health service through
    a sink, after the searches, and never fails the run.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.health_detail import spec
from app.health_detail.fold import (
    MetricFold,
    NotificationFold,
    fold_metrics,
    fold_notifications,
)
from app.health_detail.rows import HealthMetricsResult, HealthNotificationsResult
from app.i18n.messages import MessageRef, render
from app.models.enums import SearchFamily
from app.models.instance import QRadarInstance
from app.models.qradar_health import (
    QRadarHealthMetricBucket,
    QRadarHealthNotificationBucket,
    QRadarHealthRun,
)
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderCapability,
    ProviderError,
    ProviderQueryRejectedError,
    QRadarProvider,
)
from app.schemas.qradar_health import QRadarHealth
from app.services.locks import CollectorAdvisoryLock
from app.services.qradar_health import HealthReadCache, read_qradar_health
from app.services.qradar_health_sample import make_sample_sink
from app.services.search_cadence import SearchCadenceService, slot_start

logger = logging.getLogger("app.collectors.qradar_health")

COLLECTOR_NAME = "qradar_health"
_REASON = "qradar_health_detail.reason."
#: Rows per bulk statement: at most 9 columns, far below asyncpg's 32767 parameters.
_ROW_CHUNK = 3000


@dataclass(frozen=True)
class PartOutcome:
    """What one part (metrics or notifications) of a window established."""

    state: str
    reason: MessageRef | None = None

    @property
    def measured(self) -> bool:
        return self.state in ("OK", "PARTIAL")


@dataclass
class QRadarHealthReport:
    instance_id: uuid.UUID
    metrics_state: str = "NOT_COLLECTED"
    notifications_state: str = "NOT_COLLECTED"
    #: Ariel searches sent, failed ones included.
    searches_sent: int = 0
    #: 1 when the window was stored whole (every part OK), else 0.
    buckets_complete: int = 0
    #: 1 when the window was stored and a part is PARTIAL.
    buckets_lower_bound: int = 0
    splits: int = 0
    rows_written: int = 0
    unparseable_hosts: int = 0
    rest_sample_written: bool = False
    skipped_locked: bool = False
    unsupported: bool = False
    partial_failure: bool = False
    errors: list[str] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def error(self) -> str | None:
        return "; ".join(self.errors) if self.errors else None

    def as_dict(self) -> dict:
        return {
            "status": (
                "skipped_locked"
                if self.skipped_locked
                else "unsupported"
                if self.unsupported
                else "failed"
                if self.partial_failure
                else "ok"
            ),
            "metrics_state": self.metrics_state,
            "notifications_state": self.notifications_state,
            "buckets_complete": self.buckets_complete,
            "buckets_lower_bound": self.buckets_lower_bound,
            "buckets_failed": 0,
            "buckets_not_collected": 0,
            "searches_sent": self.searches_sent,
            "splits": self.splits,
            "rows_written": self.rows_written,
            "rest_sample_written": self.rest_sample_written,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


def window_for(slot: datetime, cadence_seconds: int) -> tuple[datetime, datetime]:
    """The last whole cadence window before the claimed slot: ``[slot - cadence, slot)``."""
    return slot - timedelta(seconds=cadence_seconds), slot


class QRadarHealthCollector:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        health_provider_factory: Callable[[QRadarInstance], QRadarProvider] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._health_provider_factory = health_provider_factory

    async def collect(
        self, instance: QRadarInstance, *, slot: datetime | None = None
    ) -> QRadarHealthReport:
        started = self._clock()
        settings = self.settings
        report = QRadarHealthReport(instance_id=instance.id)

        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            # Nothing was attempted, so nothing is recorded.
            report.unsupported = True
            report.errors.append("provider cannot run Ariel searches")
            return report

        async with CollectorAdvisoryLock(
            self.session, settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            cadence = await SearchCadenceService(self.session, settings, self._clock).get(
                SearchFamily.QRADAR_HEALTH
            )
            cadence_seconds = cadence.cadence_seconds
            claimed = slot or slot_start(
                started, cadence_seconds, settings.search_cadence_settle_seconds
            )
            window_start, window_end = window_for(claimed, cadence_seconds)

            metrics, metric_fold = await self._collect_metrics(window_start, window_end, report)
            notifications, notice_fold = await self._collect_notifications(
                window_start, window_end, report
            )
            report.metrics_state = metrics.state
            report.notifications_state = notifications.state
            for label, part in (("metrics", metrics), ("notifications", notifications)):
                if part.state in ("FAILED", "NOT_COLLECTED"):
                    report.partial_failure = True
                    report.errors.append(f"{label}: {render(part.reason) if part.reason else ''}")
            report.unparseable_hosts = (
                (metric_fold.unparseable_hosts if metric_fold else 0)
                + (notice_fold.unparseable_hosts if notice_fold else 0)
            )

            await self._write(
                instance,
                (window_start, window_end),
                cadence_seconds,
                started,
                (metrics, metric_fold),
                (notifications, notice_fold),
                report,
            )
            if metrics.state == "OK" and notifications.state == "OK":
                report.buckets_complete = 1
            elif metrics.measured or notifications.measured:
                report.buckets_lower_bound = 1
            await self.session.flush()
            await self._store_rest_sample(instance, claimed, report)

        report.duration_ms = int((self._clock() - started).total_seconds() * 1000)
        logger.info(
            "qradar health collection complete",
            extra={
                "instance_id": str(instance.id),
                "metrics_state": report.metrics_state,
                "notifications_state": report.notifications_state,
                "rows_written": report.rows_written,
                "unparseable_hosts": report.unparseable_hosts,
                "duration_ms": report.duration_ms,
            },
        )
        return report

    # ---------------------------------------------------------------- metrics
    async def _collect_metrics(
        self, start: datetime, end: datetime, report: QRadarHealthReport
    ) -> tuple[PartOutcome, MetricFold | None]:
        missing = await self._missing_properties()
        if isinstance(missing, MessageRef):
            return PartOutcome("NOT_COLLECTED", missing), None
        if missing:
            return (
                PartOutcome(
                    "NOT_COLLECTED",
                    MessageRef(_REASON + "property_missing", {"names": ", ".join(missing)}),
                ),
                None,
            )
        report.searches_sent += 1
        try:
            result: HealthMetricsResult = await self.provider.get_health_metrics(
                start, end, max_rows=self.settings.qradar_health_max_metric_rows
            )
        except ProviderQueryRejectedError as exc:
            # The text may quote the query or the appliance: class only.
            logger.warning("qradar health metric search rejected (%s)", type(exc).__name__)
            return PartOutcome("FAILED", MessageRef(_REASON + "search_rejected")), None
        except ProviderError as exc:
            logger.warning("qradar health metric search failed (%s)", type(exc).__name__)
            return PartOutcome("FAILED", MessageRef(_REASON + "search_failed")), None
        folded = fold_metrics(result.rows, mounts=self.settings.qradar_health_disk_mounts)
        if not folded.buckets:
            return PartOutcome("NOT_COLLECTED", MessageRef(_REASON + "no_events")), folded
        if result.truncated:
            return PartOutcome("PARTIAL", MessageRef(_REASON + "truncated")), folded
        return PartOutcome("OK"), folded

    async def _missing_properties(self) -> list[str] | MessageRef:
        """The required custom properties the console lacks, or why that is unknown.

        One custom-property read per run. A missing capability and a failed read are the
        same closed failure: no search is sent. The exception text is not logged.
        """
        try:
            if not self.provider.supports(ProviderCapability.CUSTOM_PROPERTY_CATALOG):
                raise CapabilityNotSupportedError(
                    f"{type(self.provider).__name__} does not support the custom-property catalog"
                )
            listed = await self.provider.list_custom_properties()
        except Exception as exc:
            logger.warning("qradar health property catalog unreadable (%s)", type(exc).__name__)
            return MessageRef(_REASON + "property_unreadable")
        names = {item.name for item in listed}
        return [name for name in spec.REQUIRED_PROPERTIES if name not in names]

    # ---------------------------------------------------------- notifications
    async def _collect_notifications(
        self, start: datetime, end: datetime, report: QRadarHealthReport
    ) -> tuple[PartOutcome, NotificationFold | None]:
        report.searches_sent += 1
        try:
            result: HealthNotificationsResult = await self.provider.get_health_notifications(
                start, end, max_rows=self.settings.qradar_health_max_notification_rows
            )
        except ProviderQueryRejectedError as exc:
            logger.warning("qradar health notification search rejected (%s)", type(exc).__name__)
            return PartOutcome("FAILED", MessageRef(_REASON + "search_rejected")), None
        except ProviderError as exc:
            logger.warning("qradar health notification search failed (%s)", type(exc).__name__)
            return PartOutcome("FAILED", MessageRef(_REASON + "search_failed")), None
        folded = fold_notifications(result.rows)
        if result.truncated:
            return PartOutcome("PARTIAL", MessageRef(_REASON + "truncated")), folded
        return PartOutcome("OK"), folded

    # ----------------------------------------------------------------- writes
    async def _write(
        self,
        instance: QRadarInstance,
        window: tuple[datetime, datetime],
        cadence_seconds: int,
        collected_at: datetime,
        metrics: tuple[PartOutcome, MetricFold | None],
        notifications: tuple[PartOutcome, NotificationFold | None],
        report: QRadarHealthReport,
    ) -> None:
        """Replace the window as a whole: one delete and one bulk insert per table."""
        start, end = window
        metric_part, metric_fold = metrics
        notice_part, notice_fold = notifications
        # Replace both parts, so a part that was not measured this time leaves no stale rows
        # behind a run row that says it was not measured.
        for model in (QRadarHealthMetricBucket, QRadarHealthNotificationBucket):
            await self.session.execute(
                delete(model).where(
                    model.instance_id == instance.id, model.bucket_start == start
                )
            )

        metric_rows: list[dict] = []
        if metric_part.measured and metric_fold is not None:
            metric_rows = [
                {
                    "instance_id": instance.id,
                    "bucket_start": start,
                    "bucket_end": end,
                    "host": fact.host,
                    "metric": fact.metric,
                    "element": fact.element,
                    "max_value": fact.max_value,
                    "avg_value": fact.avg_value,
                    "samples": fact.samples,
                }
                for fact in metric_fold.buckets
            ]
        notice_rows: list[dict] = []
        if notice_part.measured and notice_fold is not None:
            notice_rows = [
                {
                    "instance_id": instance.id,
                    "bucket_start": start,
                    "bucket_end": end,
                    "kind": "GROUP",
                    "host": fact.host,
                    "qid_name": fact.qid_name,
                    "events": fact.events,
                    "groups": 1,
                    "max_severity": fact.max_severity,
                    "last_seen": fact.last_seen,
                }
                for fact in notice_fold.entries
            ]
            if notice_fold.remainder_groups > 0:
                notice_rows.append(
                    {
                        "instance_id": instance.id,
                        "bucket_start": start,
                        "bucket_end": end,
                        "kind": "REMAINDER",
                        "host": "",
                        "qid_name": "",
                        "events": notice_fold.remainder_events,
                        "groups": notice_fold.remainder_groups,
                        "max_severity": None,
                        "last_seen": None,
                    }
                )
        for model, rows in (
            (QRadarHealthMetricBucket, metric_rows),
            (QRadarHealthNotificationBucket, notice_rows),
        ):
            for offset in range(0, len(rows), _ROW_CHUNK):
                chunk = rows[offset : offset + _ROW_CHUNK]
                await self.session.execute(pg_insert(model).values(chunk))
        report.rows_written = len(metric_rows) + len(notice_rows)

        values = {
            "window_end": end,
            "cadence_seconds": cadence_seconds,
            "collected_at": collected_at,
            "query_version": spec.QUERY_VERSION,
            "metrics_state": metric_part.state,
            "metrics_reason_code": metric_part.reason.code if metric_part.reason else None,
            "metrics_reason_params": (
                {k: str(v) for k, v in metric_part.reason.params.items()}
                if metric_part.reason
                else None
            ),
            "notifications_state": notice_part.state,
            "notifications_reason_code": notice_part.reason.code if notice_part.reason else None,
            "notifications_reason_params": (
                {k: str(v) for k, v in notice_part.reason.params.items()}
                if notice_part.reason
                else None
            ),
            "unparseable_hosts": report.unparseable_hosts,
            "dropped_mounts": metric_fold.dropped_mounts if metric_fold else 0,
            "dropped_metrics": metric_fold.dropped_metrics if metric_fold else 0,
            "null_values": metric_fold.null_values if metric_fold else 0,
            "invalid_values": metric_fold.invalid_values if metric_fold else 0,
            "dropped_events": notice_fold.dropped_events if notice_fold else 0,
        }
        statement = pg_insert(QRadarHealthRun).values(
            instance_id=instance.id, window_start=start, **values
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["instance_id", "window_start"], set_=values
            )
        )

    async def _store_rest_sample(
        self, instance: QRadarInstance, claimed: datetime, report: QRadarHealthReport
    ) -> None:
        """One REST sample for this run, read by the 30 s health service. Never fails the run."""
        store = make_sample_sink(self.session, instance.id, slot_start=claimed, clock=self._clock)

        async def sink(health: QRadarHealth) -> None:
            await store(health)
            report.rest_sample_written = True

        try:
            await read_qradar_health(
                instance,
                settings=self.settings,
                provider_factory=self._health_provider_factory,
                cache=HealthReadCache(),
                sample_sink=sink,
            )
        except Exception as exc:
            logger.warning("qradar health REST sample failed (%s)", type(exc).__name__)
