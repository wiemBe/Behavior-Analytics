"""The split driver of the catch-all senders search (design C.8, SEARCH-CADENCE §SC.5).

One call runs one search over a window of whole buckets and returns an outcome
for every bucket of it. It mirrors `app.ips.keyed_search` and shares its budget
and cell states:

* a whole search gives every bucket `COMPLETE`; a catch-all source with no row
  in it has a measured zero;
* a truncated search keeps nothing. Time is split first (two halves of whole
  buckets, left first); at one bucket the top rows are exact and the tail is
  missing, so the bucket is `LOWER_BOUND`;
* a failed search marks every bucket `FAILED` and is not split (retrying a
  failing appliance multiplies the load); a rejected one marks them `REJECTED`
  and its text is dropped, only the class is logged;
* when the tick's budget is spent, every bucket not yet searched is
  `NOT_COLLECTED`, never zero.

There is no source split: the query is over device types, not log sources.

No database and no clock: the collector persists the outcomes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.collectors.bucketed_metrics import validate_bucketed_metric_window
from app.ips.keyed_search import IpsSearchBudget
from app.models.enums import IpsCellState
from app.providers.base import ProviderError, ProviderQueryRejectedError, QRadarProvider
from app.providers.dto import CatchallKeyedResult, CatchallSenderRow

logger = logging.getLogger("app.catchall.keyed_search")

#: The literal bucket-key expression's shape; the width is in milliseconds.
BUCKET_KEY_TEMPLATE = "starttime / {bucket_ms}"


@dataclass(frozen=True)
class CatchallProvenance:
    """The search that decided a bucket (SEARCH-CADENCE §SC.5 rule 5)."""

    search_window_start: datetime
    search_window_end: datetime
    search_bucket_count: int
    split_depth: int
    device_type_ids: tuple[int, ...]
    #: The literal bucket-key expression.
    bucket_key: str

    def as_json(self) -> dict:
        return {
            "search_window_start": self.search_window_start.isoformat(),
            "search_window_end": self.search_window_end.isoformat(),
            "search_bucket_count": self.search_bucket_count,
            "split_depth": self.split_depth,
            "device_type_ids": list(self.device_type_ids),
            "bucket_key": self.bucket_key,
        }


@dataclass(frozen=True)
class CatchallBucketOutcome:
    """One bucket of the catch-all search.

    `rows` holds every sender row of the bucket: empty for a measured zero
    (`COMPLETE`), the kept top for `LOWER_BOUND`, and always empty for the three
    states that carry no measurement. `provenance` is None only for
    `NOT_COLLECTED`, where no search ran.
    """

    bucket_start: datetime
    state: IpsCellState
    rows: tuple[CatchallSenderRow, ...] = ()
    provenance: CatchallProvenance | None = None

    def __post_init__(self) -> None:
        if self.rows and not self.measured:
            raise ValueError("a bucket without a measurement cannot hold rows")

    @property
    def measured(self) -> bool:
        return self.state in (IpsCellState.COMPLETE, IpsCellState.LOWER_BOUND)


async def run_catchall_search(
    provider: QRadarProvider,
    device_type_ids: Sequence[int],
    window: tuple[datetime, datetime],
    bucket_seconds: int,
    *,
    max_rows: int,
    budget: IpsSearchBudget,
) -> list[CatchallBucketOutcome]:
    """Every bucket of `window`, oldest first."""
    start, end = validate_bucketed_metric_window(window[0], window[1], bucket_seconds)
    if not device_type_ids:
        raise ValueError("a catch-all search needs at least one device type")
    types = tuple(sorted(set(device_type_ids)))
    bucket_count = int((end - start).total_seconds()) // bucket_seconds
    bucket_key = BUCKET_KEY_TEMPLATE.format(bucket_ms=bucket_seconds * 1000)
    buckets: dict[datetime, CatchallBucketOutcome] = {}

    def emit(
        first: int,
        count: int,
        state: IpsCellState,
        depth: int,
        rows: tuple[CatchallSenderRow, ...] = (),
        *,
        searched: bool = True,
    ) -> None:
        window_start = start + timedelta(seconds=bucket_seconds * first)
        window_end = window_start + timedelta(seconds=bucket_seconds * count)
        provenance = (
            CatchallProvenance(
                search_window_start=window_start,
                search_window_end=window_end,
                search_bucket_count=count,
                split_depth=depth,
                device_type_ids=types,
                bucket_key=bucket_key,
            )
            if searched
            else None
        )
        by_bucket: dict[datetime, list[CatchallSenderRow]] = {}
        for row in rows:
            by_bucket.setdefault(row.bucket_start, []).append(row)
        for index in range(first, first + count):
            bucket_start = start + timedelta(seconds=bucket_seconds * index)
            buckets[bucket_start] = CatchallBucketOutcome(
                bucket_start=bucket_start,
                state=state,
                rows=tuple(by_bucket.get(bucket_start, ())),
                provenance=provenance,
            )

    def contained(result: CatchallKeyedResult, first: int, count: int) -> bool:
        """The provider's answer is about this search: nothing stray, nothing over the cap."""
        lo = start + timedelta(seconds=bucket_seconds * first)
        hi = lo + timedelta(seconds=bucket_seconds * count)
        return len(result.rows) <= max_rows and all(
            lo <= row.bucket_start < hi for row in result.rows
        )

    async def search(first: int, count: int, depth: int) -> None:
        if not budget.try_take():
            emit(first, count, IpsCellState.NOT_COLLECTED, depth, searched=False)
            return
        window_start = start + timedelta(seconds=bucket_seconds * first)
        try:
            result = await provider.get_catchall_senders_keyed(
                devicetype_ids=types,
                window_start=window_start,
                window_end=window_start + timedelta(seconds=bucket_seconds * count),
                bucket_seconds=bucket_seconds,
                max_rows=max_rows,
            )
        except ProviderQueryRejectedError as exc:
            # The text may quote the query or the appliance: class only.
            logger.warning(
                "catch-all search rejected (%s); %d buckets", type(exc).__name__, count
            )
            emit(first, count, IpsCellState.REJECTED, depth)
            return
        except ProviderError as exc:
            logger.warning("catch-all search failed (%s); %d buckets", type(exc).__name__, count)
            emit(first, count, IpsCellState.FAILED, depth)
            return
        if not contained(result, first, count):
            logger.warning("catch-all search returned rows outside its scope")
            emit(first, count, IpsCellState.FAILED, depth)
            return
        if not result.truncated:
            emit(first, count, IpsCellState.COMPLETE, depth, result.rows)
            return
        if count > 1:
            left = count // 2
            await search(first, left, depth + 1)
            await search(first + left, count - left, depth + 1)
        else:
            emit(first, count, IpsCellState.LOWER_BOUND, depth, result.rows)

    await search(0, bucket_count, 0)
    return [buckets[key] for key in sorted(buckets)]
