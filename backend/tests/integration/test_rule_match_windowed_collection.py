"""One keyed search per cadence tick, against a real database (T-037).

The equivalence test is the acceptance bar: the rows this path writes equal, column
for column, the rows one search per window writes.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.collectors.rule_match_collector import (
    COLLECTOR_NAME,
    RuleMatchCollector,
    _WindowOutcome,
)
from app.models.enums import SearchFamily
from app.models.monitoring import CollectionWatermark
from app.models.rule import AnalyticsRule, RuleMatchMetric, RuleMatchWindowStatus
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import ProviderError, ProviderUnavailableError
from app.providers.dto import RuleMatchBatch, RuleMatchRow, WindowedRuleMatches
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import parse_rule_match_rows
from app.rules.matches import fold_windowed_rows, window_starts
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import (
    HOUR,
    NOW,
    W10,
    W11,
    add_rule,
    collector,
    metric_count,
    recorded_statements,
    set_watermark,
    windows,
)

pytestmark = pytest.mark.integration

W3 = W11 - 8 * HOUR  # the watermark: 8 windows (04:00 .. 11:00) are then pending
STEP = 3600


class KeyedProvider(MockQRadarProvider):
    """Rule matches are whatever `matches(start)` says; every search is logged.

    `get_rule_matches_windowed` builds raw rows with a `k` and runs the real fold,
    so the collector is exercised against the real keyed contract.
    """

    def __init__(self, matches, *, fail_when=None) -> None:
        super().__init__(seed=1)
        self.matches = matches
        #: (start, end) -> bool; True raises a provider failure for that search.
        self.fail_when = fail_when or (lambda start, end: False)
        #: ("single" | "windowed", start, end), in call order.
        self.log: list[tuple[str, datetime, datetime]] = []

    def _rows(self, start: datetime) -> list[tuple[int, int, int]]:
        found = self.matches(start) if callable(self.matches) else self.matches
        ordered = sorted(found.items(), key=lambda kv: (-kv[1][0], kv[0]))
        return [(rid, r, e) for rid, (r, e) in ordered]

    async def get_rule_matches(self, window_start, window_end, *, max_rows):
        self.log.append(("single", window_start, window_end))
        if self.fail_when(window_start, window_end):
            raise ProviderUnavailableError("QRadar is unreachable")
        rows = self._rows(window_start)[:max_rows]
        return RuleMatchBatch(
            window_start=window_start,
            window_end=window_end,
            rows=[RuleMatchRow(qradar_rule_id=i, records=r, events=e) for i, r, e in rows],
            truncated=len(rows) >= max_rows,
            row_cap=max_rows,
            query_version="rule_match/v1",
        )

    async def get_rule_matches_windowed(
        self, window_start, window_end, *, window_seconds, max_rows
    ):
        self.log.append(("windowed", window_start, window_end))
        if self.fail_when(window_start, window_end):
            raise ProviderUnavailableError("QRadar is unreachable")
        key_ms = window_seconds * 1000
        raw = [
            {
                "rule_id": rid,
                "k": int(start.timestamp() * 1000) // key_ms,
                "records": r,
                "events": e,
            }
            for start in window_starts(window_start, window_end, window_seconds)
            for rid, r, e in self._rows(start)
        ]
        raw.sort(key=lambda row: -row["records"])
        raw = raw[: max_rows + 1]  # LIMIT n + 1
        return WindowedRuleMatches(
            window_start=window_start,
            window_end=window_end,
            window_seconds=window_seconds,
            batches=fold_windowed_rows(
                raw,
                window_start=window_start,
                window_end=window_end,
                window_seconds=window_seconds,
                row_cap=max_rows,
                parse_rows=parse_rule_match_rows,
                query_version="rule_match/v2",
                query="q",
            ),
            truncated=len(raw) > max_rows,
            row_cap=max_rows,
            ariel_searches=1,
        )

    def windowed_calls(self):
        return [(s, e) for kind, s, e in self.log if kind == "windowed"]


class PerWindowCollector(RuleMatchCollector):
    """The old path: one `get_rule_matches` per window, no multi-window search."""

    async def _search_windows(self, starts, window_seconds, rule_ids, report) -> AsyncIterator:
        for start in starts:
            report.searches_sent += 1
            try:
                fold, duration_ms = await self._fetch_window(start, window_seconds, rule_ids)
            except ProviderError as exc:
                yield _WindowOutcome(start, error=exc)
            else:
                yield _WindowOutcome(start, fold=fold, duration_ms=duration_ms)


def per_window_counts(start: datetime) -> dict[int, tuple[int, int]]:
    """Rows per window: 3, 0 (an empty window), 5, 2, 0, 4, 12, 1."""
    n = [3, 0, 5, 2, 0, 4, 12, 1][int((start - (W3 + timedelta(hours=1))).total_seconds()) // STEP]
    return {100 + j: (j + 1, 2 * (j + 1) + 1) for j in range(n)}


async def seed_rules(session, inst, count=14):
    for rid in range(100, 100 + count):
        await add_rule(session, inst, rid)


async def snapshot(session, inst) -> dict:
    """Every persisted column the two paths must agree on."""
    rows = await windows(session, inst)
    metrics = (
        await session.execute(
            select(
                AnalyticsRule.qradar_id,
                RuleMatchMetric.bucket_start,
                RuleMatchMetric.bucket_seconds,
                RuleMatchMetric.match_records,
                RuleMatchMetric.matched_events,
            )
            .join(AnalyticsRule, AnalyticsRule.id == RuleMatchMetric.rule_id)
            .where(AnalyticsRule.instance_id == inst.id)
            .order_by(RuleMatchMetric.bucket_start, AnalyticsRule.qradar_id)
        )
    ).all()
    wm = await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == inst.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )
    return {
        # Not compared, by design: collected_at, duration_ms and query_version.
        "windows": [
            (
                w.window_start, w.window_seconds, w.status, w.rows_returned, w.row_cap,
                w.unknown_ids, w.attempts, w.last_error,
            )
            for w in rows
        ],
        "metrics": [tuple(m) for m in metrics],
        "watermark": (
            wm.watermark_at, wm.intervals_collected, wm.consecutive_failures, wm.last_error,
            wm.lag_seconds,
        ),
    }


async def set_cadence(session, seconds: int) -> None:
    session.add(
        SearchCadenceSetting(
            family=SearchFamily.RULE_MATCH.value, cadence_seconds=seconds, updated_at=NOW
        )
    )
    await session.flush()


class TestEquivalence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("max_rows", [100, 10])
    async def test_the_keyed_path_writes_what_the_per_window_path_writes(
        self, db_session, max_rows
    ) -> None:
        """8 windows, one of them empty; at 10 rows a search truncates and splits,
        and the 12-row window is truncated even alone."""
        old_inst = await make_instance(db_session)
        new_inst = await make_instance(db_session)
        for inst in (old_inst, new_inst):
            await seed_rules(db_session, inst)
            await set_watermark(db_session, inst, W3)
        cfg = {"rule_match_max_windows_per_run": 8, "rule_match_max_rows": max_rows}
        old_provider = KeyedProvider(per_window_counts)
        new_provider = KeyedProvider(per_window_counts)

        old = await PerWindowCollector(
            db_session, old_provider,
            settings=collector(db_session, old_provider, **cfg).settings,
            clock=lambda: NOW,
        ).collect(old_inst)
        new = await collector(db_session, new_provider, **cfg).collect(new_inst)

        assert old.windows_attempted == new.windows_attempted == 8
        assert len(old_provider.log) == 8
        before, after = await snapshot(db_session, old_inst), await snapshot(db_session, new_inst)
        assert after == before
        statuses = [w[2] for w in after["windows"]]
        assert RuleMatchWindowStatus.COMPLETE in statuses
        assert any(w[3] == 0 for w in after["windows"])  # the empty window was recorded
        if max_rows == 100:
            assert [k for k, *_ in new_provider.log] == ["windowed"]
            assert new.searches_sent == 1 and new.splits == 0
        else:
            assert statuses.count(RuleMatchWindowStatus.TRUNCATED) == 1
            assert new.splits >= 1 and new.searches_sent < 2 * 8


class TestOneSearchPerTick:
    @pytest.mark.asyncio
    async def test_eight_windows_at_an_eight_hour_cadence_take_one_search(self, db_session) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W3)
        await set_cadence(db_session, 8 * STEP)
        provider = KeyedProvider(per_window_counts)

        report = await collector(db_session, provider).collect(inst)  # 8 + 6 - 1 = 13

        assert report.windows_attempted == report.windows_complete + report.windows_truncated == 8
        assert report.searches_sent == 1 and report.splits == 0
        assert [k for k, *_ in provider.log] == ["windowed"]
        wm = await db_session.scalar(select(CollectionWatermark))
        assert wm.watermark_at == W11  # no lag left behind

    @pytest.mark.asyncio
    async def test_the_cap_is_the_setting_at_a_one_hour_cadence(self, db_session) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W3)
        provider = KeyedProvider(per_window_counts)

        # No cadence row: 1 h by default, so 1 + 6 - 1 = 6 of the 8 pending windows.
        report = await collector(db_session, provider).collect(inst)

        assert report.windows_attempted == 6
        assert provider.windowed_calls() == [(W3 + HOUR, W3 + 7 * HOUR)]

    @pytest.mark.asyncio
    async def test_a_cadence_that_is_not_a_multiple_of_the_window_rounds_up(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await set_watermark(db_session, inst, W11 - 10 * HOUR)
        await set_cadence(db_session, 7200)
        provider = KeyedProvider({})

        report = await collector(
            db_session, provider, rule_match_window_seconds=3000, rule_match_max_windows_per_run=1
        ).collect(inst)

        # ceil(7200 / 3000) = 3 windows of cadence, + 1 - 1.
        assert report.windows_attempted == 3


class TestSplitting:
    @pytest.mark.asyncio
    async def test_a_truncated_search_keeps_nothing_and_its_halves_tile_the_span(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W3)
        provider = KeyedProvider(lambda s: {100 + j: (j + 1, j + 2) for j in range(3)})

        report = await collector(
            db_session, provider, rule_match_max_windows_per_run=8, rule_match_max_rows=10
        ).collect(inst)

        # 8 windows x 3 rows = 24 > 10 -> 4 x 3 = 12 > 10 -> 2 x 3 = 6 fits.
        calls = provider.windowed_calls()
        first = W3 + HOUR
        assert calls == [
            (first, first + 8 * HOUR),
            (first, first + 4 * HOUR),
            (first, first + 2 * HOUR),
            (first + 2 * HOUR, first + 4 * HOUR),
            (first + 4 * HOUR, first + 8 * HOUR),
            (first + 4 * HOUR, first + 6 * HOUR),
            (first + 6 * HOUR, first + 8 * HOUR),
        ]
        assert report.searches_sent == 7 and report.splits == 3
        assert report.searches_sent <= 2 * 8 - 1
        assert report.windows_complete == 8
        assert await metric_count(db_session) == 8 * 3
        rows = await windows(db_session, inst)
        assert all(w.status == RuleMatchWindowStatus.COMPLETE for w in rows)

    @pytest.mark.asyncio
    async def test_a_single_window_truncation_keeps_todays_semantics_and_never_splits(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst, count=14)
        await set_watermark(db_session, inst, W10)
        many = {100 + j: (50 - j, 60 - j) for j in range(14)}
        provider = KeyedProvider(lambda s: many)

        report = await collector(db_session, provider, rule_match_max_rows=10).collect(inst)

        assert provider.log == [("single", W11, W11 + HOUR)]  # one window: the base case
        (window,) = await windows(db_session, inst)
        assert window.status == RuleMatchWindowStatus.TRUNCATED
        assert window.row_cap == 10 and window.rows_returned == 10
        assert await metric_count(db_session) == 10  # the top rows are kept
        assert report.windows_truncated == 1 and report.splits == 0

    @pytest.mark.asyncio
    async def test_a_window_that_stays_truncated_after_splitting_is_not_split_further(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W11 - 3 * HOUR)  # W10, W11 pending
        many = {100 + j: (50 - j, 60 - j) for j in range(12)}
        provider = KeyedProvider(lambda s: many if s == W11 else {100: (1, 1)})

        report = await collector(db_session, provider, rule_match_max_rows=10).collect(inst)

        by_start = {w.window_start: w for w in await windows(db_session, inst)}
        assert by_start[W11].status == RuleMatchWindowStatus.TRUNCATED
        assert by_start[W10].status == RuleMatchWindowStatus.COMPLETE
        assert [k for k, s, _ in provider.log if s == W11].count("single") == 1
        assert report.searches_sent <= 2 * 3 - 1


class TestFailureAttribution:
    @pytest.mark.asyncio
    async def test_a_failed_multi_window_search_charges_no_window_that_a_split_answers(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W3)
        # Only spans longer than one window fail: singles answer.
        provider = KeyedProvider(
            per_window_counts, fail_when=lambda s, e: (e - s) > HOUR
        )

        report = await collector(
            db_session, provider, rule_match_max_windows_per_run=8
        ).collect(inst)

        rows = await windows(db_session, inst)
        assert len(rows) == 8
        assert all(w.status != RuleMatchWindowStatus.FAILED and w.attempts == 1 for w in rows)
        assert all(w.last_error is None for w in rows)
        assert report.windows_failed == 0 and report.partial_failure is False
        assert report.splits >= 1
        wm = await db_session.scalar(select(CollectionWatermark))
        assert wm.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_a_provider_that_is_down_charges_only_the_first_window_and_stops(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W3)
        provider = KeyedProvider(per_window_counts, fail_when=lambda s, e: True)

        report = await collector(
            db_session, provider, rule_match_max_windows_per_run=8
        ).collect(inst)

        # 8 -> 4 -> 2 -> 1 windows: the halves are tried, then the run stops, as
        # the per-window loop stops at its first failure.
        assert [(e - s) // HOUR for _, s, e in provider.log] == [8, 4, 2, 1]
        assert report.searches_sent == 4 and report.splits == 3
        (failed,) = await windows(db_session, inst)
        assert failed.window_start == W3 + HOUR
        assert failed.status == RuleMatchWindowStatus.FAILED and failed.attempts == 1
        assert await metric_count(db_session) == 0
        wm = await db_session.scalar(select(CollectionWatermark))
        assert wm.watermark_at == W3 and wm.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_a_window_that_keeps_failing_is_skipped_after_its_attempts(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W10 - HOUR)
        provider = KeyedProvider(
            lambda s: {100: (1, 1)}, fail_when=lambda s, e: s <= W10 < e
        )

        for expected in (1, 2):
            await collector(db_session, provider).collect(inst)
            wm = await db_session.scalar(select(CollectionWatermark))
            assert wm.watermark_at == W10 - HOUR  # retried, not skipped
            by_start = {w.window_start: w for w in await windows(db_session, inst)}
            assert by_start[W10].attempts == expected
            assert W11 not in by_start  # the run stopped at W10
        report = await collector(db_session, provider).collect(inst)

        by_start = {w.window_start: w for w in await windows(db_session, inst)}
        assert by_start[W10].status == RuleMatchWindowStatus.FAILED
        assert by_start[W10].attempts == 3
        assert by_start[W11].status == RuleMatchWindowStatus.COMPLETE
        assert report.windows_complete == 1 and report.windows_failed == 1
        wm = await db_session.scalar(select(CollectionWatermark))
        assert wm.watermark_at == W11
        assert await metric_count(db_session) == 1

    @pytest.mark.asyncio
    async def test_a_failed_retry_never_downgrades_a_complete_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        await set_watermark(db_session, inst, W3)
        await collector(
            db_session, KeyedProvider(per_window_counts), rule_match_max_windows_per_run=8
        ).collect(inst)
        before = await snapshot(db_session, inst)
        wm = await db_session.scalar(select(CollectionWatermark))
        wm.watermark_at = W3
        await db_session.flush()

        await collector(
            db_session,
            KeyedProvider(per_window_counts, fail_when=lambda s, e: True),
            rule_match_max_windows_per_run=8,
        ).collect(inst)

        after = await snapshot(db_session, inst)
        assert after["windows"][0][2] == RuleMatchWindowStatus.COMPLETE
        assert after["metrics"] == before["metrics"]
        assert [w[:3] for w in after["windows"]] == [w[:3] for w in before["windows"]]


class TestLiveness:
    @pytest.mark.asyncio
    async def test_a_far_behind_watermark_is_skipped_in_bulk_and_the_rest_is_one_search(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await seed_rules(db_session, inst)
        horizon = NOW.replace(hour=12, minute=0) - timedelta(days=1)
        await set_watermark(db_session, inst, horizon - 11 * HOUR)
        provider = KeyedProvider({100: (1, 1)})

        report = await collector(db_session, provider).collect(inst)

        rows = await windows(db_session, inst)
        skipped = [w for w in rows if w.window_start < horizon]
        assert len(skipped) == report.windows_skipped == 10
        assert all(
            w.status == RuleMatchWindowStatus.FAILED and w.attempts == 0 for w in skipped
        )
        # Nothing was searched for the skipped span; the 6 that follow are one search.
        assert provider.windowed_calls() == [(horizon, horizon + 6 * HOUR)]
        assert report.windows_complete == 6 and report.searches_sent == 1

    @pytest.mark.asyncio
    async def test_recollecting_is_idempotent_and_statements_do_not_grow_with_rules(
        self, db_session
    ) -> None:
        counts: dict[int, int] = {}
        for rules in (3, 300):
            inst = await make_instance(db_session)
            await seed_rules(db_session, inst, count=rules)
            await set_watermark(db_session, inst, W3)
            provider = KeyedProvider(lambda s, n=rules: dict.fromkeys(range(100, 100 + n), (1, 2)))
            with recorded_statements(db_session) as statements:
                await collector(
                    db_session, provider, rule_match_max_windows_per_run=8, rule_match_max_rows=5000
                ).collect(inst)
            counts[rules] = len(statements)

            first = await snapshot(db_session, inst)
            wm = await db_session.scalar(
                select(CollectionWatermark).where(CollectionWatermark.instance_id == inst.id)
            )
            wm.watermark_at = W3
            await db_session.flush()
            await collector(
                db_session, provider, rule_match_max_windows_per_run=8, rule_match_max_rows=5000
            ).collect(inst)
            second = await snapshot(db_session, inst)
            assert second["metrics"] == first["metrics"]
            assert len(second["windows"]) == 8

        assert counts[3] == counts[300], counts
