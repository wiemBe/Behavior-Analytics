"""The split driver of keyed IPS searches (design D.5, SEARCH-CADENCE §SC.5).

One call runs one dimension for one group of sources over a window of whole
buckets and returns an outcome for every (source, bucket) cell of it:

* a whole search gives every cell `COMPLETE`; a source with no row has a measured
  zero, and that is the only way a zero is made;
* a truncated search keeps nothing. Time is split first (two halves of whole
  buckets, left first), then sources; at one source and one bucket the top rows
  are exact and the tail is missing, so the cell is `LOWER_BOUND`;
* a failed search marks every cell `FAILED` and is not split (retrying a failing
  appliance multiplies the load); a rejected one marks them `REJECTED` and its
  text is dropped, only the class is logged (§D.8);
* when the tick's `IpsSearchBudget` is spent, every cell not yet searched is
  `NOT_COLLECTED`, never zero.

No database and no clock: T-050b persists the outcomes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.collectors.bucketed_metrics import validate_bucketed_metric_window
from app.ips.collection_plan import IpsSourceGroup
from app.models.enums import IpsCellState, IpsDimension
from app.providers.base import ProviderError, ProviderQueryRejectedError, QRadarProvider
from app.providers.dto import IpsKeyedResult, IpsKeyedRow

logger = logging.getLogger("app.ips.keyed_search")


class IpsSearchBudget:
    """The searches one tick may send, shared by every dimension it runs."""

    def __init__(self, max_searches: int) -> None:
        if max_searches < 0:
            raise ValueError("the search budget cannot be negative")
        self.max_searches = max_searches
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.max_searches - self.used

    def try_take(self) -> bool:
        if self.used >= self.max_searches:
            return False
        self.used += 1
        return True


@dataclass(frozen=True)
class IpsCellProvenance:
    """The search that decided a cell (SEARCH-CADENCE §SC.5 rule 5)."""

    search_window_start: datetime
    search_window_end: datetime
    search_bucket_count: int
    search_source_count: int
    split_depth: int
    #: The literal bucket-key expression.
    bucket_key: str


@dataclass(frozen=True)
class IpsCellOutcome:
    """One (source, bucket) cell of one dimension.

    `rows` holds the cell's keyed rows: empty for a measured zero (`COMPLETE`),
    the kept top for `LOWER_BOUND`, and always empty for the three states that
    carry no measurement. `provenance` is None only for `NOT_COLLECTED`, where no
    search ran.
    """

    dimension: IpsDimension
    qradar_log_source_id: int
    bucket_start: datetime
    state: IpsCellState
    rows: tuple[IpsKeyedRow, ...] = ()
    provenance: IpsCellProvenance | None = None

    def __post_init__(self) -> None:
        if self.rows and not self.measured:
            raise ValueError("a cell without a measurement cannot hold rows")

    @property
    def measured(self) -> bool:
        return self.state in (IpsCellState.COMPLETE, IpsCellState.LOWER_BOUND)


async def run_dimension(
    provider: QRadarProvider,
    dimension: IpsDimension,
    group: IpsSourceGroup,
    window: tuple[datetime, datetime],
    bucket_seconds: int,
    *,
    max_rows: int,
    budget: IpsSearchBudget,
) -> list[IpsCellOutcome]:
    """Every (source, bucket) cell of `group` over `window`, ordered by source then bucket."""
    start, end = validate_bucketed_metric_window(window[0], window[1], bucket_seconds)
    if not group.source_ids:
        raise ValueError("an IPS source group cannot be empty")
    bucket_count = int((end - start).total_seconds()) // bucket_seconds
    bucket_key = f"starttime / {bucket_seconds * 1000}"
    fields = group.provider_fields()
    cells: dict[tuple[int, datetime], IpsCellOutcome] = {}

    def emit(
        sources: tuple[int, ...],
        first: int,
        count: int,
        state: IpsCellState,
        depth: int,
        rows: tuple[IpsKeyedRow, ...] = (),
        *,
        searched: bool = True,
    ) -> None:
        window_start = start + timedelta(seconds=bucket_seconds * first)
        window_end = window_start + timedelta(seconds=bucket_seconds * count)
        provenance = (
            IpsCellProvenance(
                search_window_start=window_start,
                search_window_end=window_end,
                search_bucket_count=count,
                search_source_count=len(sources),
                split_depth=depth,
                bucket_key=bucket_key,
            )
            if searched
            else None
        )
        by_cell: dict[tuple[int, datetime], list[IpsKeyedRow]] = {}
        for row in rows:
            by_cell.setdefault((row.qradar_log_source_id, row.bucket_start), []).append(row)
        for source_id in sources:
            for index in range(first, first + count):
                bucket_start = start + timedelta(seconds=bucket_seconds * index)
                cells[(source_id, bucket_start)] = IpsCellOutcome(
                    dimension=dimension,
                    qradar_log_source_id=source_id,
                    bucket_start=bucket_start,
                    state=state,
                    rows=tuple(by_cell.get((source_id, bucket_start), ())),
                    provenance=provenance,
                )

    def contained(
        result: IpsKeyedResult, sources: tuple[int, ...], first: int, count: int
    ) -> bool:
        """The provider's answer is about this search: nothing stray, nothing over the cap."""
        lo = start + timedelta(seconds=bucket_seconds * first)
        hi = lo + timedelta(seconds=bucket_seconds * count)
        return len(result.rows) <= max_rows and all(
            row.qradar_log_source_id in sources and lo <= row.bucket_start < hi
            for row in result.rows
        )

    async def search(sources: tuple[int, ...], first: int, count: int, depth: int) -> None:
        if not budget.try_take():
            emit(sources, first, count, IpsCellState.NOT_COLLECTED, depth, searched=False)
            return
        window_start = start + timedelta(seconds=bucket_seconds * first)
        try:
            result = await provider.get_ips_keyed(
                dimension,
                qradar_log_source_ids=sources,
                fields=fields,
                window_start=window_start,
                window_end=window_start + timedelta(seconds=bucket_seconds * count),
                bucket_seconds=bucket_seconds,
                max_rows=max_rows,
            )
        except ProviderQueryRejectedError as exc:
            # The text may quote the query or the appliance: class only (§D.8).
            logger.warning(
                "IPS %s search rejected (%s); %d sources, %d buckets",
                dimension.value,
                type(exc).__name__,
                len(sources),
                count,
            )
            emit(sources, first, count, IpsCellState.REJECTED, depth)
            return
        except ProviderError as exc:
            logger.warning(
                "IPS %s search failed (%s); %d sources, %d buckets",
                dimension.value,
                type(exc).__name__,
                len(sources),
                count,
            )
            emit(sources, first, count, IpsCellState.FAILED, depth)
            return
        if not contained(result, sources, first, count):
            logger.warning("IPS %s search returned rows outside its scope", dimension.value)
            emit(sources, first, count, IpsCellState.FAILED, depth)
            return
        if not result.truncated:
            emit(sources, first, count, IpsCellState.COMPLETE, depth, result.rows)
            return
        if count > 1:
            left = count // 2
            await search(sources, first, left, depth + 1)
            await search(sources, first + left, count - left, depth + 1)
        elif len(sources) > 1:
            half = len(sources) // 2
            await search(sources[:half], first, count, depth + 1)
            await search(sources[half:], first, count, depth + 1)
        else:
            emit(sources, first, count, IpsCellState.LOWER_BOUND, depth, result.rows)

    await search(tuple(sorted(group.source_ids)), 0, bucket_count, 0)
    return [cells[key] for key in sorted(cells)]
