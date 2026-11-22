"""Log-source inventory collection.

`InventorySyncService` already performs the upsert and is used by the seed path
and the API. This collector wraps it in the operational envelope the scheduled
collectors share: an advisory lock so two workers cannot sync one console
concurrently, a watermark carrying run time and failure count, and a report the
task layer can log.

A pass ends COMPLETE, PARTIAL or FAILED (FIX-74). The pages that arrived are upserted even when
a later page fails, and only a COMPLETE walk may conclude that a source is absent (nothing here
deletes a source, and a partial walk leaves every unseen source's mentions as they were). Only a
COMPLETE pass advances the watermark. The pass's own account (status, counts, pages, failure
kind, elapsed time) is kept in the watermark's metadata for the Fleet and access pages.

Unlike the offense collector there is no incremental window — QRadar's log
source endpoint has no usable "changed since" filter, so this is a full
inventory pass every run. The watermark therefore records *when we last
succeeded*, not a resume point; the sync is idempotent by (instance, qradar_id)
so a repeated full pass converges rather than duplicating.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.log_source_policy import source_pause_enabled
from app.models.instance import QRadarInstance
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderError, QRadarProvider, failure_kind
from app.schemas.fleet import FleetSyncPass
from app.services.inventory_sync import InventorySyncService
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.collectors.log_source")

COLLECTOR_NAME = "log_source_inventory"


#: How one pass ended. Stored in `collection_watermark.collection_metadata["inventory_pass"]`.
RUN_COMPLETE = "COMPLETE"
RUN_PARTIAL = "PARTIAL"
RUN_FAILED = "FAILED"
INVENTORY_RUN_KEY = "inventory_pass"


@dataclass
class LogSourceSyncReport:
    instance_id: uuid.UUID
    log_sources_seen: int = 0
    created: int = 0
    updated: int = 0
    skipped_locked: bool = False
    #: True when the pass FAILED outright (nothing was read). A PARTIAL pass is not this.
    partial_failure: bool = False
    error: str | None = None
    duration_ms: int = 0
    #: COMPLETE, PARTIAL or FAILED; None when the pass was skipped on the lock.
    status: str | None = None
    pages_done: int = 0
    pages_failed: int = 0
    failed_page: int | None = None
    failure_kind: str | None = None
    absence: dict[str, str | int] | None = None


def stored_run(mark: CollectionWatermark | None) -> dict | None:
    """The last finished pass kept on the watermark, or None when none was recorded."""
    if mark is None:
        return None
    run = (mark.collection_metadata or {}).get(INVENTORY_RUN_KEY)
    return run if isinstance(run, dict) and run.get("status") in (
        RUN_COMPLETE, RUN_PARTIAL, RUN_FAILED
    ) else None


def sync_pass(mark: CollectionWatermark | None) -> FleetSyncPass | None:
    run = stored_run(mark)
    if run is None:
        return None

    def number(key: str) -> int:
        value = run.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else 0

    def optional(key: str) -> int | None:
        value = run.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    finished = run.get("finished_at")
    try:
        finished_at = datetime.fromisoformat(finished) if isinstance(finished, str) else None
    except ValueError:
        finished_at = None
    kind = run.get("failure_kind")
    return FleetSyncPass(
        status=run["status"],
        finished_at=finished_at,
        elapsed_ms=optional("elapsed_ms"),
        seen=number("seen"),
        created=number("created"),
        updated=number("updated"),
        pages_done=number("pages_done"),
        pages_failed=number("pages_failed"),
        failed_page=optional("failed_page"),
        failure_kind=kind if isinstance(kind, str) else None,
    )


def _describe(kind: str | None, page: int | None) -> str:
    """One short line naming the failure; no upstream text ever reaches it."""
    what = (kind or "upstream").replace("_", " ")
    return f"{what} on page {page}" if page is not None else what


class LogSourceCollector:
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

    async def sync(self, instance: QRadarInstance) -> LogSourceSyncReport:
        started = self._clock()
        report = LogSourceSyncReport(instance_id=instance.id)

        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                report.skipped_locked = True
                return report

            watermark = await self._get_or_create_watermark(instance)

            try:
                service = InventorySyncService(
                    self.session,
                    self.provider,
                    settings=self.settings,
                    clock=self._clock,
                )
                result = await service.sync(
                    instance,
                    bootstrap_hint=watermark.watermark_at is None,
                    previous_complete_watermark=watermark.watermark_at,
                    previous_inventory_metadata=(watermark.collection_metadata or {})
                    if source_pause_enabled(self.settings) else None,
                )
            except ProviderError as exc:
                report.status = RUN_FAILED
                report.failure_kind = failure_kind(exc)
                report.error = _describe(report.failure_kind, None)
            else:
                report.log_sources_seen = result.log_sources_seen
                report.created = result.created
                report.updated = result.updated
                report.pages_done = result.pages_done
                report.pages_failed = result.pages_failed
                report.failed_page = result.failed_page
                report.failure_kind = result.failure_kind
                if result.complete:
                    report.status = RUN_COMPLETE
                elif result.log_sources_seen > 0:
                    report.status = RUN_PARTIAL
                    report.error = _describe(result.failure_kind or "truncated", result.failed_page)
                else:
                    # Nothing arrived: there is nothing to keep, and no zero is recorded as a
                    # count of sources.
                    report.status = RUN_FAILED
                    report.error = _describe(result.failure_kind or "truncated", result.failed_page)

            absence_metadata: dict[str, object] = {}
            if source_pause_enabled(self.settings):
                evaluation = service.absence
                if evaluation is None:
                    # The provider failed before any source walk; preserve all candidates.
                    previous = (watermark.collection_metadata or {}).get("absence_candidates", [])
                    report.absence = {
                        "state": "skipped", "reason": "failed_snapshot",
                        "candidates": len(previous) if isinstance(previous, list) else 0,
                        "confirmed": 0, "held_count": 0,
                    }
                else:
                    report.absence = evaluation.summary()
                    absence_metadata["absence_candidates"] = sorted(
                        str(source_id) for source_id in evaluation.candidates
                    )
                if evaluation is not None and evaluation.state != "skipped":
                    absence_metadata["absence_complete_rows"] = report.log_sources_seen
            finished = self._clock()
            report.duration_ms = int((finished - started).total_seconds() * 1000)
            watermark.last_run_at = started
            if report.status == RUN_COMPLETE:
                # Only a complete pass advances the watermark: it is the last time the whole
                # console was read.
                watermark.watermark_at = started
                watermark.intervals_collected += 1
                watermark.consecutive_failures = 0
                watermark.last_error = None
                watermark.lag_seconds = 0
            else:
                # The watermark is deliberately not advanced: a failed or partial pass must
                # not read downstream as a completed observation.
                watermark.consecutive_failures += 1
                watermark.last_error = (
                    f"{report.status.lower()}: {report.error}"[:512] if report.status else None
                )
            metadata = dict(watermark.collection_metadata or {})
            if (source_pause_enabled(self.settings) and service.absence is not None
                    and service.absence.confirmed):
                metadata.pop("absence_accept", None)
            watermark.collection_metadata = {
                **metadata,
                **absence_metadata,
                INVENTORY_RUN_KEY: {
                    "status": report.status,
                    "finished_at": finished.isoformat(),
                    "elapsed_ms": report.duration_ms,
                    "seen": report.log_sources_seen,
                    "created": report.created,
                    "updated": report.updated,
                    "pages_done": report.pages_done,
                    "pages_failed": report.pages_failed,
                    "failed_page": report.failed_page,
                    "failure_kind": report.failure_kind,
                    **({"absence": report.absence} if report.absence is not None else {}),
                },
            }
            await self.session.flush()

        if report.status == RUN_FAILED:
            report.partial_failure = True
            logger.warning(
                "log source inventory sync failed",
                extra={
                    "instance_id": str(instance.id),
                    "failure_kind": report.failure_kind,
                },
            )
            return report
        logger.log(
            logging.INFO if report.status == RUN_COMPLETE else logging.WARNING,
            f"log source inventory sync {report.status.lower() if report.status else ''}",
            extra={
                "instance_id": str(instance.id),
                "log_sources_seen": report.log_sources_seen,
                # Not "created"/"updated": LogRecord reserves `created` for its
                # own timestamp, and logging raises KeyError rather than
                # shadowing it. That only fires when the logger is actually
                # enabled at INFO, so it hides from tests that leave logging at
                # default level and shows up in production.
                "rows_created": report.created,
                "rows_updated": report.updated,
                "duration_ms": report.duration_ms,
                "failure_kind": report.failure_kind,
            },
        )
        return report

    async def _get_or_create_watermark(self, instance: QRadarInstance) -> CollectionWatermark:
        statement = select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
        if source_pause_enabled(self.settings):
            # The advisory lock ends before commit; the row lock protects candidate rotation.
            statement = statement.with_for_update().execution_options(populate_existing=True)
        watermark = await self.session.scalar(statement)
        if watermark is None:
            watermark = CollectionWatermark(
                instance_id=instance.id, collector=COLLECTOR_NAME
            )
            self.session.add(watermark)
            await self.session.flush()
        return watermark
