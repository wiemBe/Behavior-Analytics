"""The bounded repair of recent FAILED rule-match windows (RM-2), against a real database.

Every test drives `RuleMatchCollector.collect` — the path the beat task takes — except
the one that pins the selector's statement count, which needs the helper directly.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from app.collectors.rule_match_collector import COLLECTOR_NAME
from app.models.monitoring import CollectionWatermark
from app.models.rule import RuleMatchMetric, RuleMatchWindow, RuleMatchWindowStatus
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import (
    HOUR,
    NOW,
    W10,
    W11,
    add_rule,
    collector,
    evaluator,
    fill_windows,
    latest,
    recorded_statements,
    set_watermark,
    windows,
)
from tests.integration.test_rule_match_windowed_collection import KeyedProvider

pytestmark = pytest.mark.integration

REPAIR_ON = {"rule_match_collection_enabled": True}
W9 = W10 - HOUR
W8 = W10 - 2 * HOUR
W6 = W10 - 4 * HOUR


async def failed_window_row(
    session,
    inst,
    start: datetime,
    *,
    error: str | None = "ProviderError: simulated outage",
    attempts: int = 3,
    origin: str = "LIVE",
) -> None:
    session.add(
        RuleMatchWindow(
            instance_id=inst.id,
            window_start=start,
            window_seconds=3600,
            origin=origin,
            status=RuleMatchWindowStatus.FAILED,
            rows_returned=0,
            row_cap=100,
            unknown_ids=0,
            query_version="rule_match/v1",
            collected_at=start + HOUR,
            attempts=attempts,
            last_error=error,
        )
    )
    await session.flush()


async def by_start(session, inst) -> dict[datetime, RuleMatchWindow]:
    return {w.window_start: w for w in await windows(session, inst)}


async def nth_window(session, inst, offset: int) -> RuleMatchWindow:
    return await session.scalar(
        select(RuleMatchWindow)
        .where(RuleMatchWindow.instance_id == inst.id)
        .order_by(RuleMatchWindow.window_start)
        .offset(offset)
        .limit(1)
    )


class TestTheRepairItself:
    @pytest.mark.asyncio
    async def test_a_failed_window_inside_the_age_bound_is_re_read_and_heals(
        self, db_session
    ) -> None:
        """End to end: repair, then coverage complete, then NEVER_OBSERVED again."""
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst)
        victim = await nth_window(db_session, inst, 7)
        victim.status = RuleMatchWindowStatus.FAILED
        victim.last_error = "ProviderError: simulated outage"
        victim.attempts = 3
        await set_watermark(db_session, inst, W11)

        # Before: the gap fails the span closed, and the reason names the window.
        await evaluator(db_session).evaluate_instance(inst)
        before = await latest(db_session, rule)
        assert before.status == "INSUFFICIENT_DATA"
        assert before.reason is not None
        assert "1 window failed to collect (latest error class: ProviderError)" in before.reason
        assert victim.window_start.strftime("%Y-%m-%d %H:%M") in before.reason
        assert "stops holding the last affected window" in before.reason

        report = await collector(db_session, KeyedProvider({}), **REPAIR_ON).collect(inst)

        assert report.windows_repaired == 1
        assert report.repair_searches == 1
        assert report.windows_attempted == 0  # the live pass had nothing
        rows = await by_start(db_session, inst)
        assert rows[victim.window_start].status == RuleMatchWindowStatus.COMPLETE
        assert rows[victim.window_start].last_error is None
        assert rows[victim.window_start].attempts == 4  # the repair search counts
        # The watermark never moves for a repair.
        wm = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == COLLECTOR_NAME
            )
        )
        assert wm.watermark_at == W11

        await evaluator(db_session).evaluate_instance(inst)
        await db_session.refresh(before)
        after = before
        assert after.status == "NEVER_OBSERVED"

    @pytest.mark.asyncio
    async def test_a_repaired_window_that_comes_back_truncated_stays_truncated(
        self, db_session
    ) -> None:
        """Honest, per RM-1 open point 2: narrowing a truncated window is out of scope."""
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await failed_window_row(db_session, inst, W10)
        await set_watermark(db_session, inst, W11)
        many = {100 + j: (50 - j, 60 - j) for j in range(14)}
        provider = KeyedProvider(lambda s: many)

        report = await collector(
            db_session, provider, rule_match_max_rows=10, **REPAIR_ON
        ).collect(inst)

        assert report.windows_repaired == 1
        rows = await by_start(db_session, inst)
        assert rows[W10].status == RuleMatchWindowStatus.TRUNCATED
        assert rows[W10].rows_returned == 10

    @pytest.mark.asyncio
    async def test_a_repair_never_overwrites_a_complete_or_truncated_row(
        self, db_session, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The forged race: the selector picked the windows, another writer finished them."""
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        done = RuleMatchWindow(
            instance_id=inst.id,
            window_start=W10,
            window_seconds=3600,
            origin="LIVE",
            status=RuleMatchWindowStatus.COMPLETE,
            rows_returned=0,
            row_cap=100,
            unknown_ids=0,
            query_version="rule_match/v1",
            collected_at=W10 + HOUR,
            attempts=1,
        )
        partial = RuleMatchWindow(
            instance_id=inst.id,
            window_start=W11,
            window_seconds=3600,
            origin="LIVE",
            status=RuleMatchWindowStatus.TRUNCATED,
            rows_returned=9,
            row_cap=100,
            unknown_ids=0,
            query_version="rule_match/v1",
            collected_at=W11 + HOUR,
            attempts=1,
        )
        db_session.add_all([done, partial])
        db_session.add_all(
            [
                RuleMatchMetric(
                    rule_id=rule.id,
                    bucket_start=W10,
                    bucket_seconds=3600,
                    match_records=2,
                    matched_events=3,
                ),
                RuleMatchMetric(
                    rule_id=rule.id,
                    bucket_start=W11,
                    bucket_seconds=3600,
                    match_records=4,
                    matched_events=5,
                ),
            ]
        )
        await set_watermark(db_session, inst, W11)

        run = collector(db_session, KeyedProvider({100: (7, 7)}), **REPAIR_ON)

        async def forged_starts(_instance_id, _now):
            return [W10, W11]

        monkeypatch.setattr(run, "_repair_starts", forged_starts)
        report = await run.collect(inst)

        rows = await by_start(db_session, inst)
        assert report.windows_repaired == 0
        assert rows[W10].status == RuleMatchWindowStatus.COMPLETE
        assert rows[W10].rows_returned == 0
        assert rows[W10].last_error is None
        assert rows[W11].status == RuleMatchWindowStatus.TRUNCATED
        assert rows[W11].rows_returned == 9
        metrics = {
            row.bucket_start: (row.match_records, row.matched_events)
            for row in (await db_session.scalars(select(RuleMatchMetric))).all()
        }
        assert metrics == {W10: (2, 3), W11: (4, 5)}


class TestSelection:
    @pytest.mark.asyncio
    async def test_skipped_old_history_and_burned_windows_are_never_selected(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        old = NOW - timedelta(hours=25)
        await failed_window_row(db_session, inst, old)  # older than the 24 h bound
        await failed_window_row(
            db_session, inst, W8, error="skipped: beyond the catch-up horizon", attempts=0
        )
        await failed_window_row(db_session, inst, W9, origin="HISTORY")
        await failed_window_row(db_session, inst, W10 - 7 * HOUR, attempts=6)  # ceiling
        await failed_window_row(db_session, inst, W10)  # inside every bound

        starts = await collector(db_session, KeyedProvider({}), **REPAIR_ON)._repair_starts(
            inst.id, NOW
        )

        assert starts == [W10]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "switch_off",
        [{"rule_match_repair_enabled": False}, {"rule_match_collection_enabled": False}],
    )
    async def test_both_switches_off_select_nothing(self, db_session, switch_off) -> None:
        inst = await make_instance(db_session)
        await failed_window_row(db_session, inst, W10)
        settings = {**REPAIR_ON, **switch_off}

        starts = await collector(db_session, KeyedProvider({}), **settings)._repair_starts(
            inst.id, NOW
        )
        assert starts == []

        # And the row is left alone by a full run with the repair off.
        await set_watermark(db_session, inst, W11)
        report = await collector(db_session, KeyedProvider({}), **settings).collect(inst)

        assert report.windows_repaired == 0
        rows = await by_start(db_session, inst)
        assert rows[W10].status == RuleMatchWindowStatus.FAILED

    @pytest.mark.asyncio
    async def test_the_selector_is_one_statement_at_any_size(self, db_session) -> None:
        counts: dict[int, int] = {}
        for broken in (2, 12):
            inst = await make_instance(db_session)
            for i in range(broken):
                await failed_window_row(db_session, inst, W10 - i * HOUR)
            with recorded_statements(db_session, only="rule_match_window") as statements:
                starts = await collector(
                    db_session, KeyedProvider({}), **REPAIR_ON
                )._repair_starts(inst.id, NOW)
            selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
            counts[broken] = len(selects)
            assert len(starts) == min(broken, 6)

        assert counts[2] == counts[12] == 1, counts


class TestBounds:
    @pytest.mark.asyncio
    async def test_at_most_n_per_run_oldest_first_one_search_per_group(
        self, db_session
    ) -> None:
        """Five contiguous FAILED windows, two a run: the pair is one keyed search."""
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        for i in range(5):
            await failed_window_row(db_session, inst, W6 + i * HOUR)
        await set_watermark(db_session, inst, W11)
        provider = KeyedProvider({})
        cfg = {**REPAIR_ON, "rule_match_repair_max_windows_per_run": 2}

        first = await collector(db_session, provider, **cfg).collect(inst)
        assert first.windows_repaired == 2
        assert provider.windowed_calls() == [(W6, W6 + 2 * HOUR)]
        repaired = sorted(
            w.window_start
            for w in await windows(db_session, inst)
            if w.status == RuleMatchWindowStatus.COMPLETE
        )
        assert repaired == [W6, W6 + HOUR]  # oldest first

        second = await collector(db_session, provider, **cfg).collect(inst)
        third = await collector(db_session, provider, **cfg).collect(inst)
        assert second.windows_repaired == 2
        assert third.windows_repaired == 1
        rows = await by_start(db_session, inst)
        assert rows[W10].status == RuleMatchWindowStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_repair_searches_count_in_the_tick_budget(self, db_session) -> None:
        """A low cap: the oldest group runs, the next defers, nothing goes over."""
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await failed_window_row(db_session, inst, W6)
        await failed_window_row(db_session, inst, W8)
        await set_watermark(db_session, inst, W10)
        provider = KeyedProvider({})

        first = await collector(
            db_session, provider, search_cadence_max_searches_per_tick=2, **REPAIR_ON
        ).collect(inst)

        # Live pass: W11 (one single-window search). Repair: the oldest window
        # (W6) fits the cap; W8 would be the third search, so it waits.
        assert first.windows_attempted == 1
        assert first.searches_sent == 2
        assert first.windows_repaired == 1
        assert first.history_reason is not None  # the history step still ran
        rows = await by_start(db_session, inst)
        assert rows[W6].status == RuleMatchWindowStatus.COMPLETE
        assert rows[W8].status == RuleMatchWindowStatus.FAILED

        second = await collector(
            db_session, provider, search_cadence_max_searches_per_tick=2, **REPAIR_ON
        ).collect(inst)
        assert second.searches_sent == 1  # only the W8 repair
        assert second.windows_repaired == 1

    @pytest.mark.asyncio
    async def test_a_failed_repair_is_counted_and_stops_at_the_ceiling(
        self, db_session
    ) -> None:
        """No endless loop: three repair attempts, then the window is left alone."""
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await failed_window_row(db_session, inst, W10)
        await set_watermark(db_session, inst, W11)
        provider = KeyedProvider({}, fail_when=lambda s, e: s <= W10 < e)

        for expected_attempts in (4, 5, 6):
            report = await collector(db_session, provider, **REPAIR_ON).collect(inst)
            assert report.repair_failed == 1
            assert report.windows_repaired == 0
            row = (await by_start(db_session, inst))[W10]
            assert row.status == RuleMatchWindowStatus.FAILED
            assert row.attempts == expected_attempts

        # The ceiling reached: the window is not selected, so nothing is sent.
        quiet = await collector(db_session, provider, **REPAIR_ON).collect(inst)
        assert quiet.repair_failed == 0
        assert quiet.repair_searches == 0
        assert (await by_start(db_session, inst))[W10].attempts == 6

    @pytest.mark.asyncio
    async def test_the_repair_is_skipped_when_the_live_pass_failed(self, db_session) -> None:
        """A provider that is down this tick must not burn the repair attempts."""
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await failed_window_row(db_session, inst, W9)  # behind the watermark
        await set_watermark(db_session, inst, W10 - HOUR)  # the live pass owes W10
        provider = KeyedProvider({}, fail_when=lambda s, e: True)

        report = await collector(db_session, provider, **REPAIR_ON).collect(inst)

        assert report.partial_failure is True
        assert report.repair_searches == 0
        rows = await by_start(db_session, inst)
        assert rows[W10].status == RuleMatchWindowStatus.FAILED  # the live failure
        assert rows[W9].attempts == 3  # untouched: the repair never ran


class TestReason:
    @pytest.mark.asyncio
    async def test_a_skipped_window_is_named_as_never_searched(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst)
        victim = await nth_window(db_session, inst, 5)
        victim.status = RuleMatchWindowStatus.FAILED
        victim.last_error = "skipped: beyond the catch-up horizon"
        victim.attempts = 0
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)

        snap = await latest(db_session, rule)
        assert snap.status == "INSUFFICIENT_DATA"
        assert snap.reason is not None
        assert "1 window was skipped beyond the catch-up horizon" in snap.reason
        assert "never searched" in snap.reason
        assert "last error" not in snap.reason
        assert "stops holding the last affected window" in snap.reason
        until = victim.window_start + timedelta(days=1)  # rule_health_window_days = 1
        assert until.strftime("%Y-%m-%d %H:%M") in snap.reason

    @pytest.mark.asyncio
    async def test_naming_the_windows_changes_the_sentence_never_the_verdict(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst)

        await evaluator(db_session).evaluate_instance(inst)
        healthy = await latest(db_session, rule)
        assert healthy.status == "NEVER_OBSERVED"

        victim = await nth_window(db_session, inst, 3)
        victim.status = RuleMatchWindowStatus.FAILED
        victim.last_error = "ProviderError: simulated outage"
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)
        await db_session.refresh(healthy)
        gap = healthy

        assert gap.status == "INSUFFICIENT_DATA"
        assert gap.confidence == pytest.approx(0.2)
        assert "incomplete" in (gap.reason or "")
