"""Collect identity buckets for one QRadar instance.

One watermark for the fleet, one lock, and one search per mapping chunk.
Counts that come back cut are split down to a single bucket. A failed search
is stored as NOT_COLLECTED and is not split. The watermark moves only through
the contiguous prefix where every chunk's counts search ran. A name-search
failure does not hold it. Unattempted buckets are not written as zeros.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.identity.fold import (
    CountRow,
    CountSearch,
    IdentityBucketFact,
    IdentityFold,
    NameRow,
    NameSearch,
    SeriesKey,
    fold_tick,
)
from app.identity.plan import IdentityChunk, plan_chunks
from app.identity.reference import IdentityFieldMapping, IdentityReferenceError
from app.identity.search import (
    IdentityBucketCounts,
    IdentityChunkResult,
    IdentityNameOutcome,
    IdentitySearchBudget,
    run_chunk,
)
from app.identity.writer import write_tick
from app.models.enums import (
    FIRST_RUN_LOOKBACK_BUCKETS,
    IDENTITY_CATALOG_VERSION,
    IdentityProfileValidationStatus,
    SearchFamily,
)
from app.models.identity_detection import IdentitySourceProfile
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import ProviderCapability, QRadarProvider
from app.services.locks import CollectorAdvisoryLock
from app.services.search_cadence import SearchCadenceService

logger = logging.getLogger("app.identity.collector")

COLLECTOR_NAME = "identity_collection"


@dataclass
class IdentityCollectionReport:
    """What one instance's tick did. ``as_dict`` is what the task records."""

    status: str
    searches_sent: int = 0
    splits: int = 0
    buckets_written: int = 0
    watermark_at: datetime | None = None
    skipped_locked: bool = False

    def as_dict(self) -> dict[str, Any]:
        stamp = None if self.watermark_at is None else self.watermark_at.isoformat()
        return {
            "status": self.status,
            "searches_sent": self.searches_sent,
            "splits": self.splits,
            "buckets_written": self.buckets_written,
            "watermark_at": stamp,
            "skipped_locked": self.skipped_locked,
        }


def floor_to_bucket(moment: datetime, bucket_seconds: int) -> datetime:
    """Epoch-aligned bucket start at or before ``moment``. Copied from catch-all."""
    epoch = int(moment.astimezone(UTC).timestamp())
    return datetime.fromtimestamp(epoch - epoch % bucket_seconds, tz=UTC)


class IdentityCollector:
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

    async def collect(self, instance: QRadarInstance) -> IdentityCollectionReport:
        if not self.provider.supports(ProviderCapability.AQL_EXECUTION):
            return IdentityCollectionReport(status="unsupported")
        async with CollectorAdvisoryLock(
            self.session, self.settings, instance.id, COLLECTOR_NAME
        ) as acquired:
            if not acquired:
                return IdentityCollectionReport(status="skipped_locked", skipped_locked=True)
            return await self._collect_locked(instance)

    async def _collect_locked(self, instance: QRadarInstance) -> IdentityCollectionReport:
        started = self._clock()
        settings = self.settings
        bucket_seconds = settings.collection_interval_seconds
        watermark = await self._watermark(instance)
        eligible = await self._eligible(instance)
        if not eligible:
            self._stamp(watermark, started, bucket_seconds)
            await self.session.flush()
            return IdentityCollectionReport(status="idle", watermark_at=watermark.watermark_at)

        cadence = await SearchCadenceService(self.session, settings, self._clock).get(
            SearchFamily.IDENTITY_COLLECTION
        )
        buckets = _due_buckets(
            watermark.watermark_at,
            floor_to_bucket(
                started - timedelta(seconds=settings.search_cadence_settle_seconds),
                bucket_seconds,
            ),
            bucket_seconds,
            first_run_buckets=FIRST_RUN_LOOKBACK_BUCKETS,
            max_buckets=max(1, cadence.cadence_seconds // bucket_seconds)
            + settings.collection_max_backfill_intervals,
        )
        if not buckets:
            self._stamp(watermark, started, bucket_seconds)
            await self.session.flush()
            return IdentityCollectionReport(
                status="caught_up", watermark_at=watermark.watermark_at
            )

        chunks = plan_chunks(
            eligible, max_sources_per_search=settings.identity_max_sources_per_search
        )
        window = (buckets[0], buckets[-1] + timedelta(seconds=bucket_seconds))
        budget = IdentitySearchBudget(settings.search_cadence_max_searches_per_tick)
        results: list[IdentityChunkResult] = []
        for chunk in chunks:
            results.append(
                await run_chunk(
                    self.provider,
                    chunk,
                    window,
                    bucket_seconds,
                    max_rows=settings.ariel_multi_bucket_max_rows,
                    budget=budget,
                    max_sources=settings.identity_max_sources_per_search,
                )
            )
        facts = _fold_attempted(chunks, results, buckets)
        searches = sum(result.searches for result in results)
        splits = sum(result.splits for result in results)
        if facts:
            await write_tick(
                self.session,
                IdentityFold(catalog_version=IDENTITY_CATALOG_VERSION, buckets=tuple(facts)),
                collected_at=started,
                bucket_seconds=bucket_seconds,
                provenance={
                    "family": SearchFamily.IDENTITY_COLLECTION.value,
                    "catalog_version": IDENTITY_CATALOG_VERSION,
                    "searches": searches,
                    "splits": splits,
                },
            )
        advanced = _advance(watermark, chunks, results, buckets)
        if _counts_failed(chunks, results):
            watermark.consecutive_failures += 1
            watermark.last_error = "ProviderError"
        else:
            watermark.consecutive_failures = 0
            watermark.last_error = None
        watermark.intervals_collected += advanced
        watermark.collection_metadata = {
            "family": SearchFamily.IDENTITY_COLLECTION.value,
            "catalog_version": IDENTITY_CATALOG_VERSION,
            "searches": searches,
            "splits": splits,
            "bucket_seconds": bucket_seconds,
        }
        self._stamp(watermark, started, bucket_seconds)
        names_ran = all(result.rare.ran and result.volume.ran for result in results)
        status = "ok" if advanced == len(buckets) and names_ran else "partial"
        await self.session.flush()
        logger.info(
            "identity collection %s: %d searches, %d buckets",
            status,
            searches,
            len(facts),
        )
        return IdentityCollectionReport(
            status=status,
            searches_sent=searches,
            splits=splits,
            buckets_written=len(facts),
            watermark_at=watermark.watermark_at,
        )

    async def _watermark(self, instance: QRadarInstance) -> CollectionWatermark:
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

    async def _eligible(
        self, instance: QRadarInstance
    ) -> list[tuple[int, UUID, IdentityFieldMapping]]:
        rows = (
            await self.session.execute(
                select(IdentitySourceProfile, LogSource)
                .join(LogSource, LogSource.id == IdentitySourceProfile.log_source_id)
                .where(
                    IdentitySourceProfile.instance_id == instance.id,
                    IdentitySourceProfile.enabled.is_(True),
                    IdentitySourceProfile.validation_status
                    == IdentityProfileValidationStatus.VALID.value,
                    IdentitySourceProfile.validated_profile_version
                    == IdentitySourceProfile.profile_version,
                    LogSource.enabled.is_(True),
                )
            )
        ).all()
        eligible: list[tuple[int, UUID, IdentityFieldMapping]] = []
        for profile, source in rows:
            try:
                mapping = IdentityFieldMapping.from_profile(profile)
            except IdentityReferenceError:
                logger.warning("identity profile skipped; its mapping is not collectable")
                continue
            eligible.append((source.qradar_id, source.id, mapping))
        return eligible

    @staticmethod
    def _stamp(watermark: CollectionWatermark, started: datetime, bucket_seconds: int) -> None:
        watermark.last_run_at = started
        if watermark.watermark_at is None:
            return
        end_of_last = watermark.watermark_at + timedelta(seconds=bucket_seconds)
        watermark.lag_seconds = max(0, int((started - end_of_last).total_seconds()))


def _due_buckets(
    watermark_at: datetime | None,
    boundary: datetime,
    bucket_seconds: int,
    *,
    first_run_buckets: int,
    max_buckets: int,
) -> list[datetime]:
    """Settled bucket starts after the watermark, oldest first, at most ``max_buckets``."""
    step = timedelta(seconds=bucket_seconds)
    if watermark_at is None:
        first = boundary - step * first_run_buckets
        limit = first_run_buckets
    else:
        last = watermark_at if watermark_at.tzinfo else watermark_at.replace(tzinfo=UTC)
        first = last.astimezone(UTC) + step
        limit = max_buckets
    out: list[datetime] = []
    cursor = first
    while cursor + step <= boundary and len(out) < limit:
        out.append(cursor)
        cursor += step
    return out


def _source_ids(chunk: IdentityChunk) -> dict[int, UUID]:
    return {source.qradar_id: source.log_source_id for source in chunk.sources}


def _measured(cell: IdentityBucketCounts, chunk: IdentityChunk) -> bool:
    """True when the counts search ran and every row belongs to the chunk."""
    if not cell.ran:
        return False
    known = set(_source_ids(chunk))
    return all(row.qradar_log_source_id in known for row in cell.rows)


def _counts_failed(chunks: Sequence[IdentityChunk], results: Sequence[IdentityChunkResult]) -> bool:
    for chunk, result in zip(chunks, results, strict=True):
        for cell in result.counts.values():
            if cell.attempted and not _measured(cell, chunk):
                return True
    return False


def _advance(
    watermark: CollectionWatermark,
    chunks: Sequence[IdentityChunk],
    results: Sequence[IdentityChunkResult],
    buckets: Sequence[datetime],
) -> int:
    """Move the watermark through the prefix every chunk measured. Return how many."""
    advanced = 0
    for bucket in buckets:
        if not all(
            _measured(result.counts[bucket], chunk)
            for chunk, result in zip(chunks, results, strict=True)
        ):
            break
        watermark.watermark_at = bucket
        advanced += 1
    return advanced


def _fold_attempted(
    chunks: Sequence[IdentityChunk],
    results: Sequence[IdentityChunkResult],
    buckets: Sequence[datetime],
) -> list[IdentityBucketFact]:
    """Fold each attempted (chunk, bucket). An unattempted cell writes nothing."""
    facts: list[IdentityBucketFact] = []
    for bucket in buckets:
        for chunk, result in zip(chunks, results, strict=True):
            cell = result.counts[bucket]
            if not cell.attempted:
                continue
            facts.extend(
                fold_tick(
                    _scope(chunk, bucket),
                    _count_search(cell, chunk),
                    _name_search(result.rare, bucket, chunk),
                    _name_search(result.volume, bucket, chunk),
                ).buckets
            )
    return facts


def _scope(chunk: IdentityChunk, bucket: datetime) -> tuple[SeriesKey, ...]:
    return tuple(
        SeriesKey(source.log_source_id, event_id, bucket)
        for source in chunk.sources
        for event_id in source.enabled_event_ids
    )


def _count_search(cell: IdentityBucketCounts, chunk: IdentityChunk) -> CountSearch:
    if not _measured(cell, chunk):
        return CountSearch(ran=False, truncated=False)
    sources = _source_ids(chunk)
    rows = tuple(
        CountRow(
            sources[row.qradar_log_source_id],
            row.event_id,
            row.bucket_start,
            row.events,
        )
        for row in cell.rows
    )
    return CountSearch(ran=True, truncated=cell.truncated, rows=rows)


def _name_search(
    outcome: IdentityNameOutcome, bucket: datetime, chunk: IdentityChunk
) -> NameSearch:
    if not outcome.ran:
        return NameSearch(ran=False, truncated=False)
    sources = _source_ids(chunk)
    rows: list[NameRow] = []
    for row in outcome.rows:
        if row.bucket_start != bucket:
            continue
        source_id = sources.get(row.qradar_log_source_id)
        if source_id is None or row.username is None:
            return NameSearch(ran=False, truncated=False)
        rows.append(NameRow(source_id, row.event_id, row.bucket_start, row.username, row.events))
    return NameSearch(ran=True, truncated=outcome.truncated, rows=tuple(rows))
