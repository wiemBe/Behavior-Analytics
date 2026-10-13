"""The split driver: time before sources, the base case, failure, budget, equivalence."""

from __future__ import annotations

import logging
import random
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from app.ips.collection_plan import IpsSourceGroup
from app.ips.keyed_search import (
    IpsCellOutcome,
    IpsCellProvenance,
    IpsSearchBudget,
    run_dimension,
)
from app.models.enums import IpsCellState as S
from app.models.enums import IpsDimension as D
from app.providers.base import ProviderError, ProviderQueryRejectedError
from app.providers.dto import IpsKeyedResult, IpsKeyedRow
from app.providers.mock import MockIpsSearch, MockQRadarProvider

BUCKET = 300
START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)


def end_of(buckets: int) -> datetime:
    return START + timedelta(seconds=BUCKET * buckets)


def group(*ids: int, dimension: D = D.SOURCE_IP) -> IpsSourceGroup:
    fields = {
        D.TOTALS: (),
        D.SOURCE_IP: (("SOURCE_IP", "NORMALIZED", "sourceip"),),
    }[dimension]
    return IpsSourceGroup(fields=fields, source_ids=tuple(sorted(ids)))


def sip_row(source: int, index: int, ip: str = "203.0.113.5", events: int = 10) -> IpsKeyedRow:
    return IpsKeyedRow(
        qradar_log_source_id=source,
        bucket_start=START + timedelta(seconds=BUCKET * index),
        events=events,
        records=1,
        source_ip=ip,
    )


class Scripted(MockQRadarProvider):
    """A provider whose answer is a function of the search, to craft exact scenarios."""

    def __init__(self, answer: Any) -> None:
        super().__init__(now=START)
        self.answer = answer

    async def get_ips_keyed(self, dimension: D, **kw: Any) -> IpsKeyedResult:  # type: ignore[override]
        search = MockIpsSearch(
            dimension,
            tuple(sorted(kw["qradar_log_source_ids"])),
            kw["window_start"],
            kw["window_end"],
        )
        self.ips_searches.append(search)
        outcome = self.answer(search)
        if isinstance(outcome, Exception):
            raise outcome
        rows, truncated = outcome
        return IpsKeyedResult(
            dimension=dimension,
            qradar_log_source_ids=search.qradar_log_source_ids,
            window_start=search.window_start,
            window_end=search.window_end,
            bucket_seconds=kw["bucket_seconds"],
            rows=tuple(rows),
            truncated=truncated,
        )


def buckets_of(search: MockIpsSearch) -> int:
    return int((search.window_end - search.window_start).total_seconds()) // BUCKET


async def run(
    provider: MockQRadarProvider,
    g: IpsSourceGroup,
    buckets: int,
    *,
    dimension: D = D.SOURCE_IP,
    max_rows: int = 1000,
    budget: int = 100,
) -> tuple[list[IpsCellOutcome], IpsSearchBudget]:
    spent = IpsSearchBudget(budget)
    cells = await run_dimension(
        provider,
        dimension,
        g,
        (START, end_of(buckets)),
        BUCKET,
        max_rows=max_rows,
        budget=spent,
    )
    return cells, spent


class TestSplit:
    async def test_time_is_split_before_sources(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.ips_force_truncated = lambda s: buckets_of(s) > 1 or len(
            s.qradar_log_source_ids
        ) > 1
        await run(provider, group(412, 413), 2)
        shape = [
            (buckets_of(s), s.qradar_log_source_ids, s.window_start)
            for s in provider.ips_searches
        ]
        assert shape == [
            (2, (412, 413), START),
            (1, (412, 413), START),  # the left half keeps both sources
            (1, (412,), START),
            (1, (413,), START),
            (1, (412, 413), end_of(1)),
            (1, (412,), end_of(1)),
            (1, (413,), end_of(1)),
        ]

    async def test_halves_are_whole_buckets_with_the_left_half_first(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.ips_force_truncated = lambda s: buckets_of(s) > 1
        await run(provider, group(412), 5)
        spans = [(s.window_start, buckets_of(s)) for s in provider.ips_searches]
        assert spans[:3] == [(START, 5), (START, 2), (START, 1)]
        assert (end_of(2), 3) in spans
        for start, count in spans:
            assert (start - START).total_seconds() % BUCKET == 0 and count >= 1

    async def test_one_source_one_bucket_is_a_lower_bound_with_the_top_kept(self) -> None:
        rows = [sip_row(412, 0, f"203.0.113.{i}", events=100 - i) for i in range(1, 4)]
        provider = Scripted(lambda s: (rows, True))
        cells, spent = await run(provider, group(412), 1, max_rows=3)
        (cell,) = cells
        assert cell.state is S.LOWER_BOUND
        assert cell.rows == tuple(rows)
        assert spent.used == 1

    async def test_a_truncated_wide_search_keeps_nothing(self) -> None:
        wide = sip_row(412, 0, "198.51.100.99", events=999_999)

        def answer(s: MockIpsSearch):
            if len(s.qradar_log_source_ids) > 1 or buckets_of(s) > 1:
                return [wide], True
            return [sip_row(s.qradar_log_source_ids[0], 0, events=7)], False

        provider = Scripted(answer)
        cells, _ = await run(provider, group(412, 413), 1)
        assert all(r.events == 7 for c in cells for r in c.rows)
        assert wide not in [r for c in cells for r in c.rows]
        assert {c.state for c in cells} == {S.COMPLETE}

    async def test_a_truncated_multi_bucket_result_is_not_written_even_when_the_split_fails(
        self,
    ) -> None:
        wide = sip_row(412, 0, events=999_999)

        def answer(s: MockIpsSearch):
            if buckets_of(s) > 1:
                return [wide], True
            return ProviderError("boom")

        cells, _ = await run(Scripted(answer), group(412), 2)
        assert [c.state for c in cells] == [S.FAILED, S.FAILED]
        assert all(c.rows == () for c in cells)

    async def test_split_results_cover_every_cell_once_in_order(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.ips_force_truncated = lambda s: buckets_of(s) > 1 or len(
            s.qradar_log_source_ids
        ) > 1
        cells, _ = await run(provider, group(412, 413, 414), 3)
        keys = [(c.qradar_log_source_id, c.bucket_start) for c in cells]
        assert keys == sorted(keys)
        assert len(keys) == len(set(keys)) == 9


class TestFailure:
    async def test_a_failure_marks_every_cell_failed_and_is_not_split(self) -> None:
        provider = Scripted(lambda s: ProviderError("upstream timed out"))
        cells, spent = await run(provider, group(412, 413), 4)
        assert len(provider.ips_searches) == spent.used == 1
        assert len(cells) == 8
        assert {c.state for c in cells} == {S.FAILED}
        assert all(c.rows == () for c in cells)
        assert all(c.provenance and c.provenance.search_bucket_count == 4 for c in cells)

    async def test_a_rejection_marks_every_cell_rejected_and_keeps_no_text(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        secret = "SELECT secret FROM somewhere -- upstream said no"
        provider = Scripted(lambda s: ProviderQueryRejectedError(secret))
        with caplog.at_level(logging.DEBUG):
            cells, _ = await run(provider, group(412, 413), 2)
        assert len(provider.ips_searches) == 1
        assert {c.state for c in cells} == {S.REJECTED}
        assert secret not in repr(cells)
        assert secret not in caplog.text and "somewhere" not in caplog.text
        assert "ProviderQueryRejectedError" in caplog.text

    async def test_a_failure_after_a_truncation_fails_only_the_failing_half(self) -> None:
        def answer(s: MockIpsSearch):
            if buckets_of(s) > 1:
                return [], True
            if s.window_start == START:
                return ProviderError("boom")
            return [sip_row(412, 1)], False

        cells, _ = await run(Scripted(answer), group(412), 2)
        assert [c.state for c in cells] == [S.FAILED, S.COMPLETE]
        assert cells[1].rows == (sip_row(412, 1),)

    async def test_rows_outside_the_search_scope_fail_closed(self) -> None:
        provider = Scripted(lambda s: ([sip_row(999, 0)], False))
        cells, _ = await run(provider, group(412), 1)
        assert [c.state for c in cells] == [S.FAILED]
        assert cells[0].rows == ()

    async def test_more_rows_than_the_cap_fail_closed(self) -> None:
        provider = Scripted(lambda s: ([sip_row(412, 0)] * 3, False))
        cells, _ = await run(provider, group(412), 1, max_rows=2)
        assert [c.state for c in cells] == [S.FAILED]


class TestBudget:
    async def test_an_exhausted_budget_is_not_collected_never_zero(self) -> None:
        provider = MockQRadarProvider(now=START)
        cells, spent = await run(provider, group(412), 3, budget=0)
        assert provider.ips_searches == []
        assert {c.state for c in cells} == {S.NOT_COLLECTED}
        assert all(c.rows == () and c.provenance is None for c in cells)
        assert spent.used == 0

    async def test_the_budget_stops_the_split_mid_way(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.ips_force_truncated = lambda s: buckets_of(s) > 1
        cells, spent = await run(provider, group(412), 4, budget=2)
        # 4 buckets truncated, left 2 buckets truncated, then the budget is gone.
        assert len(provider.ips_searches) == 2 and spent.used == 2 and spent.remaining == 0
        assert {c.state for c in cells} == {S.NOT_COLLECTED}

    async def test_a_partial_budget_collects_what_it_reached(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.ips_force_truncated = lambda s: buckets_of(s) > 1
        cells, _ = await run(provider, group(412), 2, budget=3)
        # 2-bucket search truncates; the two single buckets fit in the budget.
        assert {c.state for c in cells} == {S.COMPLETE}
        cells, _ = await run(provider, group(412), 2, budget=2)
        assert [c.state for c in cells] == [S.COMPLETE, S.NOT_COLLECTED]

    async def test_the_budget_is_shared_across_dimensions(self) -> None:
        provider = MockQRadarProvider(now=START)
        shared = IpsSearchBudget(1)
        first = await run_dimension(
            provider, D.TOTALS, group(412, dimension=D.TOTALS), (START, end_of(1)), BUCKET,
            max_rows=1000, budget=shared,
        )
        second = await run_dimension(
            provider, D.SOURCE_IP, group(412), (START, end_of(1)), BUCKET,
            max_rows=1000, budget=shared,
        )
        assert {c.state for c in first} == {S.COMPLETE}
        assert {c.state for c in second} == {S.NOT_COLLECTED}

    async def test_failures_and_rejections_spend_the_budget(self) -> None:
        provider = Scripted(lambda s: ProviderError("x"))
        _, spent = await run(provider, group(412), 1, budget=5)
        assert spent.used == 1

    def test_a_negative_budget_is_refused(self) -> None:
        with pytest.raises(ValueError):
            IpsSearchBudget(-1)


class TestZeroAndProvenance:
    async def test_a_silent_source_in_a_complete_search_is_a_measured_zero(self) -> None:
        provider = Scripted(lambda s: ([sip_row(412, 0)], False))
        cells, _ = await run(provider, group(412, 413), 2)
        by_key = {(c.qradar_log_source_id, c.bucket_start): c for c in cells}
        silent = by_key[(413, START)]
        assert silent.state is S.COMPLETE and silent.rows == ()
        assert by_key[(412, START)].rows == (sip_row(412, 0),)
        assert by_key[(412, end_of(1))].state is S.COMPLETE

    async def test_only_a_measured_cell_may_hold_rows(self) -> None:
        for state in (S.FAILED, S.REJECTED, S.NOT_COLLECTED):
            with pytest.raises(ValueError, match="measurement"):
                IpsCellOutcome(D.SOURCE_IP, 412, START, state, rows=(sip_row(412, 0),))
        assert IpsCellOutcome(D.SOURCE_IP, 412, START, S.COMPLETE).rows == ()

    async def test_provenance_records_the_deciding_search(self) -> None:
        provider = MockQRadarProvider(now=START)
        provider.ips_force_truncated = lambda s: buckets_of(s) > 1 or len(
            s.qradar_log_source_ids
        ) > 1
        cells, _ = await run(provider, group(412, 413), 2)
        first = cells[0]  # (412, bucket 0): 2 buckets -> 1 bucket both sources -> 1 source
        assert first.provenance == IpsCellProvenance(
            search_window_start=START,
            search_window_end=end_of(1),
            search_bucket_count=1,
            search_source_count=1,
            split_depth=2,
            bucket_key=f"starttime / {BUCKET * 1000}",
        )

    async def test_an_unsplit_search_has_depth_zero_and_the_whole_window(self) -> None:
        cells, _ = await run(MockQRadarProvider(now=START), group(412, 413), 3)
        for c in cells:
            assert c.provenance == IpsCellProvenance(
                START, end_of(3), 3, 2, 0, "starttime / 300000"
            )


class TestInputs:
    async def test_an_unaligned_window_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="epoch-aligned"):
            await run_dimension(
                MockQRadarProvider(now=START), D.SOURCE_IP, group(412),
                (START + timedelta(seconds=1), end_of(1)), BUCKET,
                max_rows=10, budget=IpsSearchBudget(5),
            )

    async def test_an_empty_group_is_a_value_error(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            await run(MockQRadarProvider(now=START), IpsSourceGroup(fields=(), source_ids=()), 1)


class TestEquivalence:
    """SC.5 rule 4: the keyed path with splits equals every (source, bucket) run alone."""

    @pytest.mark.parametrize("dimension", list(D))
    async def test_split_equals_alone(self, dimension: D) -> None:
        rng = random.Random(4242 + list(D).index(dimension))
        fields = {
            D.TOTALS: (),
            D.DESTINATION_PORT: (
                ("DESTINATION_PORT", "NORMALIZED", "destinationport"),
                ("PROTOCOL", "NORMALIZED", "protocolid"),
            ),
            D.DESTINATION_IP: (("DESTINATION_IP", "NORMALIZED", "destinationip"),),
            D.SIGNATURE: (("SIGNATURE", "NORMALIZED", "qid"),),
            D.SOURCE_IP: (("SOURCE_IP", "NORMALIZED", "sourceip"),),
            D.PORT_FANOUT: (
                ("DESTINATION_PORT", "NORMALIZED", "destinationport"),
                ("SOURCE_IP", "NORMALIZED", "sourceip"),
            ),
            D.ACTION: (("ACTION", "CUSTOM_PROPERTY", "IPS_Action"),),
        }[dimension]
        seen: set[S] = set()
        deepest = 0
        for trial in range(12):
            ids = rng.sample(range(400, 430), rng.randint(1, 5))
            buckets = rng.randint(1, 6)
            first = rng.randint(0, 50)
            window = (
                START + timedelta(seconds=BUCKET * first),
                START + timedelta(seconds=BUCKET * (first + buckets)),
            )
            cap = rng.choice([2, 3, 5, 8, 1000])
            forced = rng.random() < 0.5
            g = IpsSourceGroup(fields=fields, source_ids=tuple(sorted(ids)))
            provider = MockQRadarProvider(seed=trial, now=START)
            if forced:
                # Wide searches read as truncated too, so splitting always happens.
                provider.ips_force_truncated = lambda s: buckets_of(s) > 1 or len(
                    s.qradar_log_source_ids
                ) > 1
            split = await run_dimension(
                provider, dimension, g, window, BUCKET,
                max_rows=cap, budget=IpsSearchBudget(10_000),
            )
            alone: list[IpsCellOutcome] = []
            for source in g.source_ids:
                for index in range(buckets):
                    start = window[0] + timedelta(seconds=BUCKET * index)
                    alone += await run_dimension(
                        MockQRadarProvider(seed=trial, now=START), dimension,
                        IpsSourceGroup(fields=fields, source_ids=(source,)),
                        (start, start + timedelta(seconds=BUCKET)), BUCKET,
                        max_rows=cap, budget=IpsSearchBudget(10),
                    )
            strip = lambda cs: [  # noqa: E731
                (c.dimension, c.qradar_log_source_id, c.bucket_start, c.state, c.rows)
                for c in sorted(cs, key=lambda c: (c.qradar_log_source_id, c.bucket_start))
            ]
            assert strip(split) == strip(alone), (dimension, trial, ids, buckets, cap, forced)
            assert len(split) == len(ids) * buckets
            seen |= {c.state for c in split}
            deepest = max(deepest, *(c.provenance.split_depth for c in split if c.provenance))
        # The comparison is not vacuous: splitting happened, and so did the base case.
        assert deepest > 0
        if dimension is not D.TOTALS:
            assert S.LOWER_BOUND in seen
        assert S.COMPLETE in seen
