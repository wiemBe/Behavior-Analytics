"""The split driver of keyed property-watch searches (design §G.6, §G.14.1).

One call runs one search kind for one scope chunk over a window of whole buckets and
returns an outcome for every bucket of it:

* a whole search gives every bucket `COMPLETE`; a bucket the result does not mention has a
  measured zero, and that is the only way a zero is made (`facts.count_from_search`);
* a truncated search keeps nothing. Time is split (two halves of whole buckets, left
  first); there is no per-source split, because no search groups by source. At one bucket
  the top rows are exact and the tail is missing, so the bucket is `LOWER_BOUND` and keeps
  them. **A bucket is never written from a truncated multi-bucket search** (AGENTS.md
  rule 8);
* a failed search marks every bucket `FAILED` and is not split (retrying a failing
  appliance multiplies the load); its text is dropped, only the class is logged;
* when the tick's `PwSearchBudget` (or the watch's share of it) is spent, every bucket not
  yet searched is `NOT_COLLECTED`, never zero.

`combine_chunks` adds the chunks of a large group back together: the result is `COMPLETE`
only when every chunk was whole, `LOWER_BOUND` when any was cut at one bucket, and
unmeasured when any chunk was not measured, because a scope missing a chunk is not the
scope.

No database and no clock: PW-2b persists the outcomes.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from app.collectors.bucketed_metrics import validate_bucketed_metric_window
from app.models.enums import PwScopeKind
from app.property_watch.aql import (
    PwQuerySpec,
    pinned_rows_per_bucket,
    validate_query_inputs,
)
from app.providers.base import ProviderError, QRadarProvider
from app.providers.dto import PwKeyedResult, PwKeyedRow, PwSearchKind

logger = logging.getLogger("app.property_watch.keyed_search")


class PwCellState(StrEnum):
    """What one search established about one bucket."""

    #: The search was whole. No row for a key is a measured zero.
    COMPLETE = "COMPLETE"
    #: Cut at one bucket: the rows are exact, the tail is missing.
    LOWER_BOUND = "LOWER_BOUND"
    #: The search failed. Nothing is known, and nothing is zero.
    FAILED = "FAILED"
    #: No search ran: the budget was spent.
    NOT_COLLECTED = "NOT_COLLECTED"


class PwSearchBudget:
    """The searches one tick may send, shared by every watch it runs (design §G.14.1).

    `max_per_watch` is `pw_max_searches_per_watch_per_tick`: one persistently truncated
    watch cannot spend the whole tick. Callers that do not name a watch only draw on the
    tick's total.
    """

    def __init__(self, max_searches: int, *, max_per_watch: int | None = None) -> None:
        if max_searches < 0:
            raise ValueError("the search budget cannot be negative")
        if max_per_watch is not None and max_per_watch < 0:
            raise ValueError("the per-watch search budget cannot be negative")
        self.max_searches = max_searches
        self.max_per_watch = max_per_watch
        self.used = 0
        self._by_watch: dict[int, int] = {}

    @property
    def remaining(self) -> int:
        return self.max_searches - self.used

    def used_by(self, watch: int) -> int:
        return self._by_watch.get(watch, 0)

    def try_take(self, watch: int | None = None) -> bool:
        if self.used >= self.max_searches:
            return False
        if (
            watch is not None
            and self.max_per_watch is not None
            and self.used_by(watch) >= self.max_per_watch
        ):
            return False
        self.used += 1
        if watch is not None:
            self._by_watch[watch] = self.used_by(watch) + 1
        return True


@dataclass(frozen=True)
class PwSearchPlan:
    """One search kind over one scope chunk: the inputs of `get_pw_keyed`, minus the window."""

    search: PwSearchKind
    scope_kind: PwScopeKind
    scope_ids: tuple[int, ...]
    #: `(kind, name)` pairs; ignored by TOTALS.
    properties: tuple[tuple[str, str], ...] = ()
    pinned: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class PwSearchProvenance:
    """The search that decided a bucket (SEARCH-CADENCE §SC.5 rule 5)."""

    search_window_start: datetime
    search_window_end: datetime
    search_bucket_count: int
    split_depth: int
    #: The generated query, for `pw_bucket.query_provenance`.
    query: str


@dataclass(frozen=True)
class PwBucketOutcome:
    """One bucket of one search kind.

    `rows` holds the bucket's keyed rows: empty for a measured zero (`COMPLETE`), the kept
    top for `LOWER_BOUND`, and always empty for the two states that carry no measurement.
    `provenance` is None only for `NOT_COLLECTED`, where no search ran.
    """

    search: PwSearchKind
    bucket_start: datetime
    state: PwCellState
    rows: tuple[PwKeyedRow, ...] = ()
    provenance: PwSearchProvenance | None = None

    def __post_init__(self) -> None:
        if self.rows and not self.measured:
            raise ValueError("a bucket without a measurement cannot hold rows")

    @property
    def measured(self) -> bool:
        return self.state in (PwCellState.COMPLETE, PwCellState.LOWER_BOUND)


def search_row_cap(spec: PwQuerySpec, buckets: int, max_rows: int) -> int:
    """The row cap of a search over `buckets` buckets (design §G.6).

    GROUPED: `max_rows` (`pw_max_rows_per_search`), for the whole result and not per
    bucket. TOTALS: one row per bucket. PINNED: the most rows its `IN` lists admit per
    bucket, times the buckets, and never more than `max_rows`.
    """
    if spec.search is PwSearchKind.TOTALS:
        return buckets
    if spec.search is PwSearchKind.PINNED:
        return min(pinned_rows_per_bucket(spec) * buckets, max_rows)
    return max_rows


async def run_search(
    provider: QRadarProvider,
    plan: PwSearchPlan,
    window: tuple[datetime, datetime],
    bucket_seconds: int,
    *,
    max_rows: int,
    budget: PwSearchBudget,
    max_sources: int,
    watch: int | None = None,
) -> list[PwBucketOutcome]:
    """Every bucket of `plan` over `window`, ordered by bucket.

    `max_rows` is the grouped search's cap; the totals and pinned caps follow from the
    window and the pins (`search_row_cap`). `watch` names the watch for the per-watch
    share of the budget. `max_sources` is `pw_max_sources_per_search`: the plan is refused
    before any search when a chunk is larger (the provider enforces its own as well).
    """
    start, end = validate_bucketed_metric_window(window[0], window[1], bucket_seconds)
    if max_rows < 1:
        raise ValueError("the row cap is at least 1")
    spec = validate_query_inputs(
        plan.search,
        scope_kind=plan.scope_kind,
        scope_ids=plan.scope_ids,
        properties=plan.properties,
        pinned=plan.pinned,
        max_sources=max_sources,
    )
    property_count = 0 if spec.search is PwSearchKind.TOTALS else len(spec.properties)
    bucket_count = int((end - start).total_seconds()) // bucket_seconds
    cells: dict[datetime, PwBucketOutcome] = {}

    def at(index: int) -> datetime:
        return start + timedelta(seconds=bucket_seconds * index)

    def emit(
        first: int,
        count: int,
        state: PwCellState,
        depth: int,
        rows: tuple[PwKeyedRow, ...] = (),
        query: str = "",
        *,
        searched: bool = True,
    ) -> None:
        provenance = (
            PwSearchProvenance(
                search_window_start=at(first),
                search_window_end=at(first + count),
                search_bucket_count=count,
                split_depth=depth,
                query=query,
            )
            if searched
            else None
        )
        by_bucket: dict[datetime, list[PwKeyedRow]] = {}
        for row in rows:
            by_bucket.setdefault(row.bucket_start, []).append(row)
        for index in range(first, first + count):
            cells[at(index)] = PwBucketOutcome(
                search=spec.search,
                bucket_start=at(index),
                state=state,
                rows=tuple(by_bucket.get(at(index), ())),
                provenance=provenance,
            )

    def contained(result: PwKeyedResult, first: int, count: int, cap: int) -> bool:
        """The provider's answer is about this search: nothing stray, nothing over the cap."""
        lo, hi = at(first), at(first + count)
        return len(result.rows) <= cap and all(
            lo <= row.bucket_start < hi and len(row.values) == property_count
            for row in result.rows
        )

    async def search(first: int, count: int, depth: int) -> None:
        if not budget.try_take(watch):
            emit(first, count, PwCellState.NOT_COLLECTED, depth, searched=False)
            return
        cap = search_row_cap(spec, count, max_rows)
        try:
            result = await provider.get_pw_keyed(
                spec.search,
                scope_kind=spec.scope_kind,
                scope_ids=spec.scope_ids,
                properties=[(r.kind.value, r.name) for r in spec.properties],
                pinned=spec.pinned,
                window_start=at(first),
                window_end=at(first + count),
                bucket_seconds=bucket_seconds,
                max_rows=cap,
            )
        except ProviderError as exc:
            # The text may quote the query or the appliance: class only (§D.8).
            logger.warning(
                "property-watch %s search failed (%s); %d buckets",
                spec.search.value,
                type(exc).__name__,
                count,
            )
            emit(first, count, PwCellState.FAILED, depth)
            return
        if not contained(result, first, count, cap):
            logger.warning(
                "property-watch %s search returned rows outside its window", spec.search.value
            )
            emit(first, count, PwCellState.FAILED, depth)
            return
        if not result.truncated:
            emit(first, count, PwCellState.COMPLETE, depth, result.rows, result.query)
            return
        if count > 1:
            left = count // 2
            await search(first, left, depth + 1)
            await search(first + left, count - left, depth + 1)
        else:
            emit(first, count, PwCellState.LOWER_BOUND, depth, result.rows, result.query)

    await search(0, bucket_count, 0)
    return [cells[key] for key in sorted(cells)]


def combine_chunks(chunks: Sequence[Sequence[PwBucketOutcome]]) -> list[PwBucketOutcome]:
    """The chunks of one search kind added back into one outcome per bucket.

    Exact only when every chunk was whole: any `LOWER_BOUND` chunk makes the bucket a
    lower bound, because the chunk's missing tail can only add events. A chunk that was
    `FAILED` or `NOT_COLLECTED` makes the bucket unmeasured and drops the rows of the
    others: a sum over part of the scope is not a statement about the scope. The rows are
    concatenated; the fold adds equal keys.
    """
    if not chunks:
        raise ValueError("combining needs at least one chunk")
    first = chunks[0]
    keys = [(outcome.search, outcome.bucket_start) for outcome in first]
    if any([(o.search, o.bucket_start) for o in chunk] != keys for chunk in chunks[1:]):
        raise ValueError("every chunk covers the same buckets of the same search")
    combined: list[PwBucketOutcome] = []
    for index, lead in enumerate(first):
        cells = [chunk[index] for chunk in chunks]
        states = {cell.state for cell in cells}
        if PwCellState.FAILED in states:
            state = PwCellState.FAILED
        elif PwCellState.NOT_COLLECTED in states:
            state = PwCellState.NOT_COLLECTED
        elif PwCellState.LOWER_BOUND in states:
            state = PwCellState.LOWER_BOUND
        else:
            state = PwCellState.COMPLETE
        measured = state in (PwCellState.COMPLETE, PwCellState.LOWER_BOUND)
        combined.append(
            PwBucketOutcome(
                search=lead.search,
                bucket_start=lead.bucket_start,
                state=state,
                rows=tuple(row for cell in cells for row in cell.rows) if measured else (),
                provenance=next((c.provenance for c in cells if c.provenance), None),
            )
        )
    return combined
