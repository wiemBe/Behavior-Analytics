"""Run one chunk's three identity searches, and split a truncated counts search.

A whole counts search gives every asked key an exact count; a missing key is a
measured zero. A truncated counts search keeps nothing and is halved by time
until one bucket, which is kept as a floor (``TRUNCATED``). A failed search is
not split. A name search is one call for the window: truncation flags admission
and is not split. A spent budget leaves the cell unattempted, which the
collector does not store as zero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.collectors.bucketed_metrics import validate_bucketed_metric_window
from app.identity.dto import IdentityKeyedRow, IdentitySearchKind
from app.identity.plan import IdentityChunk
from app.identity.reference import IdentityReferenceError
from app.providers.base import ProviderError, QRadarProvider

logger = logging.getLogger("app.identity.search")


class IdentitySearchBudget:
    """The searches one tick may send, shared by every chunk."""

    def __init__(self, max_searches: int) -> None:
        if isinstance(max_searches, bool) or not isinstance(max_searches, int) or max_searches < 0:
            raise IdentityReferenceError("the search budget cannot be negative")
        self.max_searches = max_searches
        self.used = 0

    def try_take(self) -> bool:
        if self.used >= self.max_searches:
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class IdentityBucketCounts:
    """The counts search for one bucket of one chunk.

    ``attempted`` false is a budget skip: the collector writes no row.
    ``attempted`` true and ``ran`` false is a failed search: ``NOT_COLLECTED``.
    """

    bucket_start: datetime
    ran: bool
    truncated: bool
    attempted: bool
    rows: tuple[IdentityKeyedRow, ...] = ()


@dataclass(frozen=True)
class IdentityNameOutcome:
    """A rare-name or volume-name search for the whole window."""

    ran: bool
    truncated: bool
    rows: tuple[IdentityKeyedRow, ...] = ()


@dataclass
class IdentityChunkResult:
    """What one chunk's searches established. ``counts`` is keyed by bucket start."""

    counts: dict[datetime, IdentityBucketCounts]
    rare: IdentityNameOutcome
    volume: IdentityNameOutcome
    searches: int
    splits: int


async def run_chunk(
    provider: QRadarProvider,
    chunk: IdentityChunk,
    window: tuple[datetime, datetime],
    bucket_seconds: int,
    *,
    max_rows: int,
    budget: IdentitySearchBudget,
    max_sources: int,
) -> IdentityChunkResult:
    """The three searches for ``chunk`` over ``window``. Counts split; names do not."""
    if isinstance(max_sources, bool) or not isinstance(max_sources, int) or max_sources < 1:
        raise IdentityReferenceError("the log source cap must be a positive integer")
    if len(chunk.sources) > max_sources:
        raise IdentityReferenceError(
            f"an identity search covers at most {max_sources} log sources"
        )
    start, end = validate_bucketed_metric_window(window[0], window[1], bucket_seconds)
    bucket_count = int((end - start).total_seconds()) // bucket_seconds
    starts = [start + timedelta(seconds=bucket_seconds * index) for index in range(bucket_count)]
    counts = {
        bucket: IdentityBucketCounts(bucket, ran=False, truncated=False, attempted=False)
        for bucket in starts
    }
    tally = {"searches": 0, "splits": 0}
    sources = chunk.qradar_ids

    def fail(first: int, count: int) -> None:
        for index in range(first, first + count):
            bucket = starts[index]
            counts[bucket] = IdentityBucketCounts(
                bucket, ran=False, truncated=False, attempted=True
            )

    def keep(first: int, count: int, rows: tuple[IdentityKeyedRow, ...], truncated: bool) -> None:
        for index in range(first, first + count):
            bucket = starts[index]
            mine = tuple(row for row in rows if row.bucket_start == bucket)
            counts[bucket] = IdentityBucketCounts(
                bucket, ran=True, truncated=truncated, attempted=True, rows=mine
            )

    async def search_counts(first: int, count: int) -> None:
        if not budget.try_take():
            return
        tally["searches"] += 1
        window_start = starts[first]
        window_end = window_start + timedelta(seconds=bucket_seconds * count)
        try:
            result = await provider.get_identity_keyed(
                IdentitySearchKind.COUNTS,
                qradar_log_source_ids=sources,
                event_id_property=chunk.event_id_property,
                username_property=chunk.username_property,
                event_ids=chunk.event_ids(IdentitySearchKind.COUNTS),
                window_start=window_start,
                window_end=window_end,
                bucket_seconds=bucket_seconds,
                max_rows=max_rows,
            )
        except ProviderError as exc:
            logger.warning(
                "identity counts search failed (%s); %d sources, %d buckets",
                type(exc).__name__,
                len(sources),
                count,
            )
            fail(first, count)
            return
        outside = any(
            row.qradar_log_source_id not in sources
            or row.bucket_start < window_start
            or row.bucket_start >= window_end
            for row in result.rows
        )
        if outside:
            logger.warning("identity counts search returned rows outside its window")
            fail(first, count)
            return
        if result.truncated and count > 1:
            tally["splits"] += 1
            left = count // 2
            await search_counts(first, left)
            await search_counts(first + left, count - left)
            return
        keep(first, count, tuple(result.rows), result.truncated)

    async def search_names(kind: IdentitySearchKind) -> IdentityNameOutcome:
        event_ids = chunk.event_ids(kind)
        if not event_ids:
            return IdentityNameOutcome(ran=True, truncated=False)
        if not budget.try_take():
            return IdentityNameOutcome(ran=False, truncated=False)
        tally["searches"] += 1
        try:
            result = await provider.get_identity_keyed(
                kind,
                qradar_log_source_ids=sources,
                event_id_property=chunk.event_id_property,
                username_property=chunk.username_property,
                event_ids=event_ids,
                window_start=start,
                window_end=end,
                bucket_seconds=bucket_seconds,
                max_rows=max_rows,
            )
        except ProviderError as exc:
            logger.warning("identity %s search failed (%s)", kind.value, type(exc).__name__)
            return IdentityNameOutcome(ran=False, truncated=False)
        return IdentityNameOutcome(ran=True, truncated=result.truncated, rows=tuple(result.rows))

    if starts and chunk.event_ids(IdentitySearchKind.COUNTS):
        await search_counts(0, bucket_count)
    rare = await search_names(IdentitySearchKind.RARE_NAMES)
    volume = await search_names(IdentitySearchKind.VOLUME_NAMES)
    return IdentityChunkResult(
        counts=counts,
        rare=rare,
        volume=volume,
        searches=tally["searches"],
        splits=tally["splits"],
    )
