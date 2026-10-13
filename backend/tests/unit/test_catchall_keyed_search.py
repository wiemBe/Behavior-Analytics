"""The catch-all split driver: time first, the base case, failure, budget, containment."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.catchall.keyed_search import CatchallBucketOutcome, run_catchall_search
from app.ips.keyed_search import IpsSearchBudget
from app.models.enums import IpsCellState as S
from app.providers.base import ProviderError, ProviderQueryRejectedError
from app.providers.dto import CatchallKeyedResult, CatchallSenderRow
from app.providers.mock import MockCatchallSearch, MockQRadarProvider

BUCKET = 300
START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
TYPES = (67, 39)


def end_of(buckets: int) -> datetime:
    return START + timedelta(seconds=BUCKET * buckets)


def crow(index: int, sender: str = "h", events: int = 10, source: int = 1) -> CatchallSenderRow:
    return CatchallSenderRow(
        qradar_log_source_id=source,
        sender=sender,
        bucket_start=START + timedelta(seconds=BUCKET * index),
        events=events,
        records=1,
    )


def buckets_of(search: MockCatchallSearch) -> int:
    return int((search.window_end - search.window_start).total_seconds()) // BUCKET


class Scripted(MockQRadarProvider):
    """A provider whose answer is a function of the search."""

    def __init__(self, answer: Any) -> None:
        super().__init__(now=START)
        self.answer = answer

    async def get_catchall_senders_keyed(  # type: ignore[override]
        self, **kw: Any
    ) -> CatchallKeyedResult:
        search = MockCatchallSearch(
            tuple(sorted(kw["devicetype_ids"])), kw["window_start"], kw["window_end"]
        )
        self.catchall_searches.append(search)
        outcome = self.answer(search)
        if isinstance(outcome, Exception):
            raise outcome
        rows, truncated = outcome
        return CatchallKeyedResult(
            device_type_ids=search.device_type_ids,
            window_start=search.window_start,
            window_end=search.window_end,
            bucket_seconds=kw["bucket_seconds"],
            rows=tuple(rows),
            truncated=truncated,
        )


async def run(
    provider: MockQRadarProvider, buckets: int, *, max_rows: int = 1000, budget: int = 100
) -> tuple[list[CatchallBucketOutcome], IpsSearchBudget]:
    spent = IpsSearchBudget(budget)
    outcomes = await run_catchall_search(
        provider, TYPES, (START, end_of(buckets)), BUCKET, max_rows=max_rows, budget=spent
    )
    return outcomes, spent


class TestSplit:
    async def test_a_whole_search_makes_every_bucket_complete_and_empty_ones_measured(
        self,
    ) -> None:
        provider = Scripted(lambda s: ([crow(1)], False))
        outcomes, spent = await run(provider, 3)
        assert spent.used == 1
        assert [o.state for o in outcomes] == [S.COMPLETE] * 3
        assert [len(o.rows) for o in outcomes] == [0, 1, 0]
        assert all(o.provenance and o.provenance.search_bucket_count == 3 for o in outcomes)
        assert outcomes[0].provenance.device_type_ids == (39, 67)  # type: ignore[union-attr]
        assert outcomes[0].provenance.bucket_key == "starttime / 300000"  # type: ignore[union-attr]

    async def test_time_is_split_in_whole_buckets_left_half_first(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.catchall_force_truncated = lambda s: buckets_of(s) > 1
        await run(provider, 5)
        spans = [(s.window_start, buckets_of(s)) for s in provider.catchall_searches]
        assert spans[:3] == [(START, 5), (START, 2), (START, 1)]
        assert (end_of(2), 3) in spans
        for start, count in spans:
            assert (start - START).total_seconds() % BUCKET == 0 and count >= 1

    async def test_one_bucket_truncated_is_a_lower_bound_with_the_top_kept(self) -> None:
        rows = [crow(0, f"h{i}", events=100 - i) for i in range(3)]
        provider = Scripted(lambda s: (rows, True))
        (outcome,), spent = await run(provider, 1, max_rows=3)
        assert outcome.state is S.LOWER_BOUND and outcome.measured
        assert outcome.rows == tuple(rows)
        assert spent.used == 1

    async def test_a_truncated_wide_search_keeps_nothing(self) -> None:
        wide = crow(0, "wide", events=999_999)

        def answer(s: MockCatchallSearch):
            if buckets_of(s) > 1:
                return [wide], True
            return [crow(int((s.window_start - START).total_seconds()) // BUCKET, events=7)], False

        outcomes, _ = await run(Scripted(answer), 2)
        assert {o.state for o in outcomes} == {S.COMPLETE}
        assert [r.events for o in outcomes for r in o.rows] == [7, 7]

    async def test_a_truncated_wide_result_is_not_kept_when_the_halves_fail(self) -> None:
        wide = crow(0, events=999_999)

        def answer(s: MockCatchallSearch):
            return ([wide], True) if buckets_of(s) > 1 else ProviderError("boom")

        outcomes, _ = await run(Scripted(answer), 2)
        assert [o.state for o in outcomes] == [S.FAILED, S.FAILED]
        assert all(o.rows == () for o in outcomes)

    async def test_buckets_come_back_once_each_oldest_first(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.catchall_force_truncated = lambda s: buckets_of(s) > 1
        outcomes, _ = await run(provider, 7)
        starts = [o.bucket_start for o in outcomes]
        assert starts == [end_of(i) for i in range(7)]
        assert outcomes[0].provenance.split_depth == 2  # type: ignore[union-attr]
        assert all(o.provenance.search_bucket_count == 1 for o in outcomes)  # type: ignore[union-attr]


class TestFailure:
    async def test_a_failure_marks_every_bucket_failed_and_is_not_split(self) -> None:
        provider = Scripted(lambda s: ProviderError("upstream timed out"))
        outcomes, spent = await run(provider, 4)
        assert len(provider.catchall_searches) == spent.used == 1
        assert {o.state for o in outcomes} == {S.FAILED}
        assert all(not o.measured and o.rows == () for o in outcomes)

    async def test_a_rejection_is_rejected_and_its_text_is_dropped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        secret = "SELECT secret FROM somewhere -- upstream said no"
        provider = Scripted(lambda s: ProviderQueryRejectedError(secret))
        with caplog.at_level(logging.DEBUG):
            outcomes, spent = await run(provider, 2)
        assert {o.state for o in outcomes} == {S.REJECTED}
        assert spent.used == 1
        assert secret not in caplog.text and "secret" not in caplog.text

    async def test_rows_outside_the_search_are_a_failure_not_data(self) -> None:
        provider = Scripted(lambda s: ([crow(9)], False))
        outcomes, _ = await run(provider, 2)
        assert {o.state for o in outcomes} == {S.FAILED}
        assert all(o.rows == () for o in outcomes)

    async def test_more_rows_than_the_cap_is_a_failure(self) -> None:
        provider = Scripted(lambda s: ([crow(0, f"h{i}") for i in range(4)], False))
        outcomes, _ = await run(provider, 1, max_rows=3)
        assert [o.state for o in outcomes] == [S.FAILED]

    def test_an_unmeasured_bucket_cannot_hold_rows(self) -> None:
        with pytest.raises(ValueError):
            CatchallBucketOutcome(START, S.FAILED, rows=(crow(0),))


class TestBudget:
    async def test_an_exhausted_budget_is_not_collected_never_zero(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.catchall_force_truncated = lambda s: buckets_of(s) > 1
        outcomes, spent = await run(provider, 4, budget=2)
        assert spent.used == 2
        # 4 -> (2 -> (1, 1)): the first search and the left half; the rest is out of budget.
        assert [o.state for o in outcomes] == [S.NOT_COLLECTED] * 4
        assert all(o.rows == () and o.provenance is None for o in outcomes)
        assert not any(o.measured for o in outcomes)

    async def test_a_zero_budget_sends_nothing(self) -> None:
        provider = Scripted(lambda s: AssertionError("must not search"))
        outcomes, spent = await run(provider, 2, budget=0)
        assert provider.catchall_searches == [] and spent.used == 0
        assert {o.state for o in outcomes} == {S.NOT_COLLECTED}

    async def test_the_left_half_survives_when_the_right_runs_out_of_budget(self) -> None:
        def answer(s: MockCatchallSearch):
            if buckets_of(s) > 1:
                return [], True
            return [crow(0, events=5)], False

        outcomes, spent = await run(Scripted(answer), 2, budget=2)
        assert spent.used == 2
        assert [o.state for o in outcomes] == [S.COMPLETE, S.NOT_COLLECTED]


class TestInput:
    async def test_it_needs_a_device_type(self) -> None:
        with pytest.raises(ValueError):
            await run_catchall_search(
                MockQRadarProvider(now=START),
                (),
                (START, end_of(1)),
                BUCKET,
                max_rows=10,
                budget=IpsSearchBudget(5),
            )

    async def test_an_unaligned_window_is_refused(self) -> None:
        with pytest.raises(ValueError):
            await run_catchall_search(
                MockQRadarProvider(now=START),
                TYPES,
                (START + timedelta(seconds=1), end_of(1)),
                BUCKET,
                max_rows=10,
                budget=IpsSearchBudget(5),
            )
