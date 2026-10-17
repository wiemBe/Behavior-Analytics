"""The property-watch split driver: time first, the base case, failure, budget, chunks."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.models.enums import PwScopeKind as K
from app.property_watch.keyed_search import (
    PwBucketOutcome,
    PwCellState,
    PwSearchBudget,
    PwSearchPlan,
    combine_chunks,
    run_search,
    search_row_cap,
)
from app.providers.base import ProviderError, ProviderQueryRejectedError
from app.providers.dto import PwKeyedResult, PwKeyedRow
from app.providers.dto import PwSearchKind as S
from app.providers.mock import MockQRadarProvider

BUCKET = 300
START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
USER = ("NORMALIZED", "username")
ACTION = ("CUSTOM_PROPERTY", "Action")


def end_of(buckets: int) -> datetime:
    return START + timedelta(seconds=BUCKET * buckets)


def at(index: int) -> datetime:
    return START + timedelta(seconds=BUCKET * index)


def prow(index: int, value: str = "alice", events: int = 10, *more: str) -> PwKeyedRow:
    return PwKeyedRow(bucket_start=at(index), events=events, values=(value, *more))


def trow(index: int, events: int = 100) -> PwKeyedRow:
    return PwKeyedRow(bucket_start=at(index), events=events)


@dataclass(frozen=True)
class Asked:
    search: S
    first: int
    count: int
    max_rows: int
    scope_ids: tuple[int, ...]


class Scripted(MockQRadarProvider):
    """A provider whose answer is a function of the search, to craft exact scenarios."""

    def __init__(self, answer: Callable[[Asked], Any]) -> None:
        super().__init__(now=START)
        self.answer = answer
        self.asked: list[Asked] = []

    async def get_pw_keyed(self, search: S, **kw: Any) -> PwKeyedResult:  # type: ignore[override]
        first = int((kw["window_start"] - START).total_seconds()) // BUCKET
        count = int((kw["window_end"] - kw["window_start"]).total_seconds()) // BUCKET
        asked = Asked(search, first, count, kw["max_rows"], tuple(kw["scope_ids"]))
        self.asked.append(asked)
        outcome = self.answer(asked)
        if isinstance(outcome, Exception):
            raise outcome
        rows, truncated = outcome
        return PwKeyedResult(
            search=search,
            scope_kind=kw["scope_kind"],
            scope_ids=asked.scope_ids,
            window_start=kw["window_start"],
            window_end=kw["window_end"],
            bucket_seconds=kw["bucket_seconds"],
            rows=tuple(rows),
            truncated=truncated,
            query=f"Q[{first}+{count}]",
        )


def plan(search: S = S.GROUPED, **over: Any) -> PwSearchPlan:
    fields: dict[str, Any] = {
        "search": search,
        "scope_kind": K.LOG_SOURCE,
        "scope_ids": (412,),
        "properties": () if search is S.TOTALS else (USER,),
    }
    return PwSearchPlan(**{**fields, **over})


async def run(
    provider: Scripted,
    buckets: int,
    *,
    search: S = S.GROUPED,
    budget: PwSearchBudget | None = None,
    watch: int | None = None,
    max_rows: int = 1000,
    **over: Any,
) -> list[PwBucketOutcome]:
    return await run_search(
        provider,
        plan(search, **over),
        (START, end_of(buckets)),
        BUCKET,
        max_rows=max_rows,
        max_sources=50,
        budget=budget or PwSearchBudget(100),
        watch=watch,
    )


def states(cells: list[PwBucketOutcome]) -> list[PwCellState]:
    return [c.state for c in cells]


class TestWholeSearch:
    async def test_every_bucket_is_complete_and_gets_its_rows(self) -> None:
        provider = Scripted(lambda a: ([prow(0), prow(2, "bob", 4)], False))
        cells = await run(provider, 3)
        assert states(cells) == [PwCellState.COMPLETE] * 3
        assert [len(c.rows) for c in cells] == [1, 0, 1]
        assert [c.bucket_start for c in cells] == [at(0), at(1), at(2)]
        assert len(provider.asked) == 1

    async def test_a_bucket_without_rows_is_a_measured_zero_not_a_gap(self) -> None:
        cells = await run(Scripted(lambda a: ([], False)), 2)
        assert states(cells) == [PwCellState.COMPLETE, PwCellState.COMPLETE]
        assert all(c.rows == () for c in cells)

    async def test_provenance_names_the_search_and_its_query(self) -> None:
        cells = await run(Scripted(lambda a: ([], False)), 4)
        prov = cells[0].provenance
        assert prov is not None
        assert (prov.search_bucket_count, prov.split_depth, prov.query) == (4, 0, "Q[0+4]")
        assert (prov.search_window_start, prov.search_window_end) == (at(0), at(4))

    async def test_a_rows_cell_must_be_measured(self) -> None:
        with pytest.raises(ValueError, match="without a measurement"):
            PwBucketOutcome(S.GROUPED, at(0), PwCellState.FAILED, (prow(0),))


class TestSplit:
    async def test_a_truncated_multi_bucket_search_keeps_nothing_and_splits_by_time(self) -> None:
        def answer(a: Asked) -> Any:
            # The whole window and the left half are cut; the right half is whole.
            if a.count == 4:
                return [prow(0), prow(1), prow(2), prow(3)], True
            if a.count == 2 and a.first == 0:
                return [prow(0), prow(1)], True
            if a.first < 2:
                return [prow(a.first)], True
            return [prow(2, "right")], False

        provider = Scripted(answer)
        cells = await run(provider, 4)
        # Left first, depth first: the whole window, then the left half, then its halves.
        assert [(a.first, a.count) for a in provider.asked] == [
            (0, 4),
            (0, 2),
            (0, 1),
            (1, 1),
            (2, 2),
        ]
        # The cut rows of the multi-bucket searches are gone; only base-case rows remain.
        assert states(cells) == [
            PwCellState.LOWER_BOUND,
            PwCellState.LOWER_BOUND,
            PwCellState.COMPLETE,
            PwCellState.COMPLETE,
        ]
        assert [c.rows[0].values[0] if c.rows else None for c in cells] == [
            "alice",
            "alice",
            "right",
            None,
        ]
        assert [len(c.rows) for c in cells] == [1, 1, 1, 0]

    async def test_at_one_bucket_a_truncated_search_is_a_lower_bound_with_its_rows(self) -> None:
        provider = Scripted(lambda a: ([prow(0, "a", 9), prow(0, "b", 8)], True))
        (cell,) = await run(provider, 1)
        assert cell.state is PwCellState.LOWER_BOUND
        assert [r.values for r in cell.rows] == [("a",), ("b",)]
        assert len(provider.asked) == 1

    async def test_an_odd_window_halves_left_smaller(self) -> None:
        provider = Scripted(lambda a: ([], a.count > 1))
        await run(provider, 3)
        assert [(a.first, a.count) for a in provider.asked] == [
            (0, 3),
            (0, 1),
            (1, 2),
            (1, 1),
            (2, 1),
        ]

    async def test_split_depth_is_in_the_provenance(self) -> None:
        provider = Scripted(lambda a: ([], a.count > 2))
        cells = await run(provider, 4)
        assert [c.provenance.split_depth for c in cells if c.provenance] == [1, 1, 1, 1]

    async def test_a_split_that_hits_the_budget_leaves_the_rest_not_collected(self) -> None:
        provider = Scripted(lambda a: ([prow(a.first)], True))
        budget = PwSearchBudget(3)  # whole window, left half, its left bucket; then nothing
        cells = await run(provider, 4, budget=budget)
        assert states(cells) == [
            PwCellState.LOWER_BOUND,
            PwCellState.NOT_COLLECTED,
            PwCellState.NOT_COLLECTED,
            PwCellState.NOT_COLLECTED,
        ]
        assert all(c.rows == () and c.provenance is None for c in cells[1:])
        assert budget.used == 3


class TestFailureAndBudget:
    async def test_a_failed_search_is_not_split_and_leaves_every_bucket_failed(self) -> None:
        provider = Scripted(lambda a: ProviderError("boom"))
        cells = await run(provider, 4)
        assert states(cells) == [PwCellState.FAILED] * 4
        assert len(provider.asked) == 1
        assert all(c.rows == () for c in cells)

    async def test_a_rejection_is_failed_and_only_its_class_is_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        provider = Scripted(lambda a: ProviderQueryRejectedError("SECRET query text"))
        with caplog.at_level(logging.WARNING, logger="app.property_watch.keyed_search"):
            cells = await run(provider, 2)
        assert states(cells) == [PwCellState.FAILED] * 2
        assert "ProviderQueryRejectedError" in caplog.text
        assert "SECRET" not in caplog.text

    async def test_a_spent_budget_leaves_not_collected_never_zero(self) -> None:
        provider = Scripted(lambda a: ([], False))
        cells = await run(provider, 3, budget=PwSearchBudget(0))
        assert states(cells) == [PwCellState.NOT_COLLECTED] * 3
        assert provider.asked == []
        assert all(c.rows == () and c.provenance is None and not c.measured for c in cells)

    async def test_the_budget_is_shared_across_calls(self) -> None:
        provider = Scripted(lambda a: ([], False))
        budget = PwSearchBudget(2)
        for kind in (S.GROUPED, S.TOTALS, S.GROUPED):
            await run(provider, 1, search=kind, budget=budget)
        assert budget.used == 2 and budget.remaining == 0
        assert [a.search for a in provider.asked] == [S.GROUPED, S.TOTALS]

    async def test_the_per_watch_cap_leaves_the_rest_of_that_watch_not_collected(self) -> None:
        provider = Scripted(lambda a: ([], a.count > 1))
        budget = PwSearchBudget(100, max_per_watch=3)
        cells = await run(provider, 8, budget=budget, watch=1)
        assert budget.used_by(1) == 3
        assert PwCellState.NOT_COLLECTED in states(cells)
        # Another watch still has its own share of the tick.
        other = await run(Scripted(lambda a: ([], False)), 1, budget=budget, watch=2)
        assert states(other) == [PwCellState.COMPLETE]

    def test_budget_arithmetic(self) -> None:
        budget = PwSearchBudget(2, max_per_watch=1)
        assert budget.try_take(1) and not budget.try_take(1)
        assert budget.try_take(2) and not budget.try_take(3)
        assert not budget.try_take()
        assert (budget.used, budget.remaining) == (2, 0)
        with pytest.raises(ValueError):
            PwSearchBudget(-1)
        with pytest.raises(ValueError):
            PwSearchBudget(1, max_per_watch=-1)


class TestContainment:
    async def test_a_row_outside_the_searched_window_fails_the_search(self) -> None:
        cells = await run(Scripted(lambda a: ([prow(5)], False)), 2)
        assert states(cells) == [PwCellState.FAILED] * 2

    async def test_more_rows_than_the_cap_fails_the_search(self) -> None:
        cells = await run(Scripted(lambda a: ([prow(0)] * 3, False)), 1, max_rows=2)
        assert states(cells) == [PwCellState.FAILED]

    async def test_a_row_with_the_wrong_number_of_values_fails_the_search(self) -> None:
        cells = await run(Scripted(lambda a: ([prow(0, "a", 1, "b")], False)), 1)
        assert states(cells) == [PwCellState.FAILED]

    async def test_a_bad_plan_is_refused_before_any_search(self) -> None:
        provider = Scripted(lambda a: ([], False))
        budget = PwSearchBudget(5)
        with pytest.raises(ValueError):
            await run(provider, 1, budget=budget, properties=(("CUSTOM_PROPERTY", "a'b"),))
        with pytest.raises(ValueError, match="at most 50"):
            await run(
                provider, 1, budget=budget, scope_kind=K.LOG_SOURCE_GROUP,
                scope_ids=tuple(range(51)),
            )
        assert provider.asked == [] and budget.used == 0

    async def test_an_unaligned_window_is_refused(self) -> None:
        with pytest.raises(ValueError, match="epoch-aligned"):
            await run_search(
                Scripted(lambda a: ([], False)),
                plan(),
                (START, START + timedelta(seconds=7)),
                BUCKET,
                max_rows=10,
                max_sources=50,
                budget=PwSearchBudget(5),
            )


class TestRowCaps:
    async def test_grouped_totals_and_pinned_each_get_their_cap(self) -> None:
        provider = Scripted(lambda a: ([], False))
        await run(provider, 12, max_rows=5000)
        await run(provider, 12, search=S.TOTALS, max_rows=5000)
        await run(
            provider,
            12,
            search=S.PINNED,
            properties=(USER, ACTION),
            pinned=(("a", "x"), ("b", "x"), ("a", "y")),
            max_rows=5000,
        )
        assert [a.max_rows for a in provider.asked] == [5000, 12, 4 * 12]

    async def test_the_pinned_cap_never_exceeds_the_grouped_cap(self) -> None:
        provider = Scripted(lambda a: ([], False))
        pins = tuple((f"u{i}",) for i in range(10))
        await run(provider, 12, search=S.PINNED, pinned=pins, max_rows=50)
        assert provider.asked[0].max_rows == 50

    async def test_the_cap_is_per_search_so_a_half_window_gets_half_the_totals_cap(self) -> None:
        provider = Scripted(lambda a: ([], a.count > 6))
        await run(provider, 12, search=S.TOTALS)
        assert [a.max_rows for a in provider.asked] == [12, 6, 6]

    def test_search_row_cap_directly(self) -> None:
        from app.property_watch.aql import validate_query_inputs

        spec = validate_query_inputs(
            S.PINNED,
            scope_kind=K.LOG_SOURCE,
            scope_ids=[1],
            properties=[USER],
            pinned=[("a",), ("b",)],
            max_sources=50,
        )
        assert search_row_cap(spec, 3, 5000) == 6


class TestChunks:
    def cells(self, state: PwCellState, rows: tuple[PwKeyedRow, ...] = ()) -> list[PwBucketOutcome]:
        return [PwBucketOutcome(S.GROUPED, at(0), state, rows if state in (
            PwCellState.COMPLETE, PwCellState.LOWER_BOUND) else ())]

    def test_exact_only_when_every_chunk_was_whole(self) -> None:
        (cell,) = combine_chunks(
            [
                self.cells(PwCellState.COMPLETE, (prow(0, "a", 3),)),
                self.cells(PwCellState.COMPLETE, (prow(0, "a", 4),)),
            ]
        )
        assert cell.state is PwCellState.COMPLETE
        assert [r.events for r in cell.rows] == [3, 4]

    def test_a_cut_chunk_makes_the_bucket_a_lower_bound_and_keeps_the_rows(self) -> None:
        (cell,) = combine_chunks(
            [
                self.cells(PwCellState.COMPLETE, (prow(0, "a", 3),)),
                self.cells(PwCellState.LOWER_BOUND, (prow(0, "a", 4),)),
            ]
        )
        assert cell.state is PwCellState.LOWER_BOUND
        assert len(cell.rows) == 2

    @pytest.mark.parametrize("bad", [PwCellState.FAILED, PwCellState.NOT_COLLECTED])
    def test_a_chunk_without_a_measurement_makes_the_bucket_unmeasured(
        self, bad: PwCellState
    ) -> None:
        (cell,) = combine_chunks(
            [self.cells(PwCellState.COMPLETE, (prow(0, "a", 3),)), self.cells(bad)]
        )
        assert cell.state is bad and cell.rows == ()

    def test_failed_outranks_not_collected(self) -> None:
        (cell,) = combine_chunks(
            [self.cells(PwCellState.NOT_COLLECTED), self.cells(PwCellState.FAILED)]
        )
        assert cell.state is PwCellState.FAILED

    def test_misaligned_chunks_are_refused(self) -> None:
        other = [PwBucketOutcome(S.GROUPED, at(1), PwCellState.COMPLETE)]
        with pytest.raises(ValueError, match="same buckets"):
            combine_chunks([self.cells(PwCellState.COMPLETE), other])
        with pytest.raises(ValueError):
            combine_chunks([])

    async def test_chunks_of_a_group_run_and_combine_end_to_end(self) -> None:
        def answer(a: Asked) -> Any:
            if a.scope_ids == (1, 2):
                return [prow(0, "a", 3)], False
            return [prow(a.first, "a", 4)], True  # the second chunk is cut down to one bucket

        provider = Scripted(answer)
        budget = PwSearchBudget(100)
        chunks = [
            await run(
                provider, 2, budget=budget, scope_kind=K.LOG_SOURCE_GROUP, scope_ids=ids
            )
            for ids in ((1, 2), (3, 4))
        ]
        first, second = combine_chunks(chunks)
        assert (first.state, second.state) == (PwCellState.LOWER_BOUND, PwCellState.LOWER_BOUND)
        assert sorted(r.events for r in first.rows) == [3, 4]
        assert [r.events for r in second.rows] == [4]
