"""Rule-match collection and rule health against a real database."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, update

from app.collectors.rule_match_collector import COLLECTOR_NAME, RuleMatchCollector
from app.core.config import get_settings
from app.models.enums import RuleHealthStatus
from app.models.monitoring import CollectionWatermark
from app.models.rule import (
    AnalyticsRule,
    RuleHealthSnapshot,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.providers.base import ProviderCapability, ProviderUnavailableError
from app.providers.dto import RuleMatchBatch, RuleMatchRow
from app.providers.mock import MockQRadarProvider
from app.services.rule_health import RuleHealthEvaluator
from tests.integration.factories import make_instance

pytestmark = pytest.mark.integration

#: 12:10 with a 300 s settle lag and 3,600 s windows: 11:00-12:00 is the newest
#: settled window.
NOW = datetime(2026, 10, 12, 12, 10, tzinfo=UTC)
HOUR = timedelta(hours=1)


@contextmanager
def recorded_statements(session, *, only: str | None = None):
    """The SQL statements executed on `session`'s engine inside the block."""
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):
        if only is None or only in statement:
            statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


class ScriptedProvider(MockQRadarProvider):
    """A mock whose rule matches are whatever the test says, and which counts calls."""

    def __init__(self, matches=None, *, fail: bool = False, fail_for=None) -> None:
        super().__init__(seed=1)
        self.matches = matches or {}
        self.fail = fail
        #: window start -> failures left; None fails that window every time.
        self.fail_for: dict[datetime, int | None] = dict(fail_for or {})
        self.calls: list[datetime] = []

    async def get_rule_matches(self, window_start, window_end, *, max_rows):
        self.calls.append(window_start)
        if window_start in self.fail_for:
            left = self.fail_for[window_start]
            if left is None or left > 0:
                if left is not None:
                    self.fail_for[window_start] = left - 1
                raise ProviderUnavailableError("QRadar is unreachable")
        if self.fail:
            raise ProviderUnavailableError("QRadar is unreachable")
        rows = self.matches(window_start) if callable(self.matches) else self.matches
        truncated = len(rows) >= max_rows
        return RuleMatchBatch(
            window_start=window_start,
            window_end=window_end,
            rows=[
                RuleMatchRow(qradar_rule_id=i, records=r, events=e)
                for i, (r, e) in list(rows.items())[:max_rows]
            ],
            truncated=truncated,
            row_cap=max_rows,
            query_version="rule_match/v1",
        )


async def add_rule(session, instance, qradar_id, *, rule_type="EVENT", bb=False, **kw):
    rule = AnalyticsRule(
        instance_id=instance.id,
        qradar_id=qradar_id,
        name=f"Rule {qradar_id}",
        enabled=True,
        is_building_block=bb,
        rule_type=rule_type,
        qradar_created_at=NOW - timedelta(days=90),
        first_seen_at=NOW - timedelta(days=90),
        **kw,
    )
    session.add(rule)
    await session.flush()
    return rule


def collector(session, provider, *, now=NOW, **overrides) -> RuleMatchCollector:
    settings = get_settings().model_copy(
        update={
            "rule_match_window_seconds": 3600,
            "rule_match_settle_lag_seconds": 300,
            "rule_match_max_windows_per_run": 6,
            "rule_match_max_rows": 100,
            **overrides,
        }
    )
    return RuleMatchCollector(session, provider, settings=settings, clock=lambda: now)


async def windows(session, instance):
    return list(
        (
            await session.scalars(
                select(RuleMatchWindow)
                .where(RuleMatchWindow.instance_id == instance.id)
                .order_by(RuleMatchWindow.window_start)
                # The skip is a raw bulk statement: read what is in the database,
                # not what the identity map remembers.
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def metric_count(session) -> int:
    return int(await session.scalar(select(func.count()).select_from(RuleMatchMetric)))


class TestCollector:
    @pytest.mark.asyncio
    async def test_complete_window_writes_sparse_rows_and_a_window_record(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        bb = await add_rule(db_session, inst, 200, bb=True)
        await add_rule(db_session, inst, 300)  # never matches: gets no row
        provider = ScriptedProvider({100: (3, 9), 200: (1, 1), 999: (5, 5)})

        report = await collector(db_session, provider).collect(inst)

        (window,) = await windows(db_session, inst)
        assert window.window_start == NOW.replace(hour=11, minute=0)
        assert window.status == RuleMatchWindowStatus.COMPLETE
        assert (window.rows_returned, window.unknown_ids, window.row_cap) == (3, 1, 100)
        assert window.query_version == "rule_match/v1"
        assert window.window_seconds == 3600
        assert window.origin == "LIVE"
        metrics = {
            m.rule_id: (m.match_records, m.matched_events, m.bucket_seconds)
            for m in (await db_session.scalars(select(RuleMatchMetric))).all()
        }
        assert metrics == {rule.id: (3, 9, 3600), bb.id: (1, 1, 3600)}
        assert (report.windows_complete, report.rows_written, report.unknown_ids) == (1, 2, 1)
        wm = await db_session.scalar(
            select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
        )
        assert wm.watermark_at == window.window_start

    @pytest.mark.asyncio
    async def test_truncated_window_is_marked_and_keeps_its_rows(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await add_rule(db_session, inst, 101)
        provider = ScriptedProvider({100: (3, 9), 101: (2, 2)})

        report = await collector(db_session, provider, rule_match_max_rows=2).collect(inst)

        (window,) = await windows(db_session, inst)
        assert window.status == RuleMatchWindowStatus.TRUNCATED
        assert report.windows_truncated == 1
        assert report.rows_written == 2

    @pytest.mark.asyncio
    async def test_provider_failure_writes_a_failed_window_and_no_rows(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)

        report = await collector(db_session, ScriptedProvider(fail=True)).collect(inst)

        (window,) = await windows(db_session, inst)
        assert window.status == RuleMatchWindowStatus.FAILED
        assert window.rows_returned == 0
        assert await metric_count(db_session) == 0
        assert report.partial_failure is True
        assert report.windows_failed == 1
        wm = await db_session.scalar(
            select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
        )
        assert wm.watermark_at is None  # the next run retries the same window
        assert wm.consecutive_failures == 1

    @pytest.mark.asyncio
    async def test_a_failed_window_is_retried_and_then_completes(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await collector(db_session, ScriptedProvider(fail=True)).collect(inst)

        await collector(db_session, ScriptedProvider({100: (1, 1)})).collect(inst)

        (window,) = await windows(db_session, inst)
        assert window.status == RuleMatchWindowStatus.COMPLETE
        assert await metric_count(db_session) == 1

    @pytest.mark.asyncio
    async def test_a_live_upsert_keeps_the_origin_of_an_existing_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await collector(db_session, ScriptedProvider({100: (1, 1)})).collect(inst)
        await db_session.execute(update(RuleMatchWindow).values(origin="HISTORY"))
        wm = await db_session.scalar(
            select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
        )
        wm.watermark_at = None
        await db_session.flush()

        await collector(db_session, ScriptedProvider({100: (2, 2)})).collect(inst)

        (window,) = await windows(db_session, inst)
        assert window.origin == "HISTORY"
        assert window.status == RuleMatchWindowStatus.COMPLETE

    @pytest.mark.asyncio
    async def test_a_failed_retry_never_downgrades_a_complete_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await collector(db_session, ScriptedProvider({100: (1, 1)})).collect(inst)
        wm = await db_session.scalar(
            select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
        )
        wm.watermark_at = None
        await db_session.flush()

        await collector(db_session, ScriptedProvider(fail=True)).collect(inst)

        (window,) = await windows(db_session, inst)
        assert window.status == RuleMatchWindowStatus.COMPLETE
        assert await metric_count(db_session) == 1

    @pytest.mark.asyncio
    async def test_recollecting_a_window_is_idempotent(self, db_session) -> None:
        inst = await make_instance(db_session)
        a = await add_rule(db_session, inst, 100)
        b = await add_rule(db_session, inst, 101)
        await collector(db_session, ScriptedProvider({100: (3, 9), 101: (2, 2)})).collect(inst)
        wm = await db_session.scalar(
            select(CollectionWatermark).where(CollectionWatermark.collector == COLLECTOR_NAME)
        )
        wm.watermark_at = None  # the same window is collected again
        await db_session.flush()

        await collector(db_session, ScriptedProvider({100: (4, 12)})).collect(inst)

        assert len(await windows(db_session, inst)) == 1
        metrics = {
            m.rule_id: m.matched_events
            for m in (await db_session.scalars(select(RuleMatchMetric))).all()
        }
        # Rule 101 matched the first time and not the second: no stale row.
        assert metrics == {a.id: 12}
        assert b.id not in metrics

    @pytest.mark.asyncio
    async def test_catch_up_is_bounded_per_run_and_resumes(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        db_session.add(
            CollectionWatermark(
                instance_id=inst.id,
                collector=COLLECTOR_NAME,
                watermark_at=NOW.replace(hour=11, minute=0) - 10 * HOUR,
            )
        )
        await db_session.flush()
        provider = ScriptedProvider({100: (1, 1)})
        cfg = {"rule_match_max_windows_per_run": 3}

        first = await collector(db_session, provider, **cfg).collect(inst)
        assert first.windows_attempted == 3
        assert len(provider.calls) == 3
        assert provider.calls == sorted(provider.calls)  # oldest first
        assert provider.calls[0] == NOW.replace(hour=11, minute=0) - 9 * HOUR

        second = await collector(db_session, provider, **cfg).collect(inst)
        assert second.windows_attempted == 3
        assert len(await windows(db_session, inst)) == 6

    @pytest.mark.asyncio
    async def test_nothing_is_collected_before_the_window_has_settled(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = ScriptedProvider({})

        # 12:02 is inside the settle lag of the 11:00-12:00 window.
        report = await collector(
            db_session, provider, now=NOW.replace(minute=2)
        ).collect(inst)

        assert report.windows_attempted == 1  # 10:00-11:00, the newest settled one
        assert provider.calls == [NOW.replace(hour=10, minute=0)]

    @pytest.mark.asyncio
    async def test_a_provider_without_ariel_records_nothing(self, db_session) -> None:
        inst = await make_instance(db_session)
        provider = ScriptedProvider({})
        provider.capabilities = frozenset(
            provider.capabilities - {ProviderCapability.AQL_EXECUTION}
        )

        report = await collector(db_session, provider).collect(inst)

        assert report.unsupported is True
        assert provider.calls == []
        assert await windows(db_session, inst) == []

    @pytest.mark.asyncio
    async def test_statement_count_is_constant_in_the_number_of_matched_rules(
        self, db_session
    ) -> None:
        counts: dict[int, int] = {}
        for matched in (50, 1000):
            inst = await make_instance(db_session)
            db_session.add_all(
                AnalyticsRule(
                    instance_id=inst.id, qradar_id=i, name=f"r{i}", enabled=True,
                    is_building_block=False, rule_type="EVENT",
                )
                for i in range(matched)
            )
            await db_session.flush()
            provider = ScriptedProvider(dict.fromkeys(range(matched), (1, 2)))
            with recorded_statements(db_session) as statements:
                await collector(db_session, provider, rule_match_max_rows=5000).collect(inst)
            counts[matched] = len(statements)
            assert await metric_count(db_session) == matched

            wipe = await db_session.scalars(select(RuleMatchMetric))
            for row in wipe.all():
                await db_session.delete(row)
            await db_session.flush()

        assert counts[50] == counts[1000], counts


W10 = NOW.replace(hour=10, minute=0)
W11 = NOW.replace(hour=11, minute=0)


async def set_watermark(session, inst, at: datetime) -> None:
    session.add(CollectionWatermark(instance_id=inst.id, collector=COLLECTOR_NAME,
                                    watermark_at=at))
    await session.flush()


class TestLiveness:
    @pytest.mark.asyncio
    async def test_a_window_that_keeps_failing_is_skipped_after_its_attempts(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await set_watermark(db_session, inst, W10 - HOUR)
        provider = ScriptedProvider({100: (1, 1)}, fail_for={W10: None})

        for _ in range(2):  # two attempts: retried, watermark held back
            await collector(db_session, provider).collect(inst)
            wm = await db_session.scalar(select(CollectionWatermark))
            assert wm.watermark_at == W10 - HOUR
        report = await collector(db_session, provider).collect(inst)  # the third

        by_start = {w.window_start: w for w in await windows(db_session, inst)}
        assert by_start[W10].status == RuleMatchWindowStatus.FAILED
        assert by_start[W10].attempts == 3
        assert by_start[W10].last_error
        # The next window collected in the same run.
        assert by_start[W11].status == RuleMatchWindowStatus.COMPLETE
        assert report.windows_complete == 1
        wm = await db_session.scalar(select(CollectionWatermark))
        assert wm.watermark_at == W11
        assert await metric_count(db_session) == 1  # only the 11:00 window's row

    @pytest.mark.asyncio
    async def test_a_transient_failure_is_not_skipped(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        await set_watermark(db_session, inst, W10 - HOUR)
        provider = ScriptedProvider({100: (1, 1)}, fail_for={W10: 1})

        await collector(db_session, provider).collect(inst)
        await collector(db_session, provider).collect(inst)

        by_start = {w.window_start: w for w in await windows(db_session, inst)}
        assert by_start[W10].status == RuleMatchWindowStatus.COMPLETE
        # Split-first: the failed multi-window search charged no window, and W10
        # was then collected by its first single-window search.
        assert by_start[W10].attempts == 1
        assert by_start[W10].last_error is None
        assert await metric_count(db_session) == 2

    @pytest.mark.asyncio
    async def test_attempts_setting_of_one_skips_on_the_first_failure(self, db_session) -> None:
        inst = await make_instance(db_session)
        await set_watermark(db_session, inst, W10 - HOUR)
        provider = ScriptedProvider({}, fail_for={W10: None})

        await collector(db_session, provider, rule_match_max_window_attempts=1).collect(inst)

        wm = await db_session.scalar(select(CollectionWatermark))
        assert wm.watermark_at == W11

    @pytest.mark.asyncio
    async def test_a_far_behind_watermark_jumps_to_the_horizon(self, db_session) -> None:
        inst = await make_instance(db_session)
        await add_rule(db_session, inst, 100)
        horizon = NOW.replace(hour=12, minute=0) - timedelta(days=1)
        await set_watermark(db_session, inst, horizon - 11 * HOUR)  # 10 windows to skip
        provider = ScriptedProvider({100: (1, 1)})

        report = await collector(db_session, provider).collect(inst)

        rows = await windows(db_session, inst)
        skipped = [w for w in rows if w.window_start < horizon]
        assert len(skipped) == report.windows_skipped == 10
        assert all(
            w.status == RuleMatchWindowStatus.FAILED
            and w.attempts == 0
            and w.last_error == "skipped: beyond the catch-up horizon"
            for w in skipped
        )
        # The skipped-window records are a raw INSERT: they state their origin too.
        assert {w.origin for w in skipped} == {"LIVE"}
        assert skipped[0].window_start == horizon - 10 * HOUR
        assert skipped[-1].window_start == horizon - HOUR
        # Nothing was searched for them, and nothing is written as zero.
        assert provider.calls[0] == horizon
        assert all(c >= horizon for c in provider.calls)
        # Collection resumed from the horizon.
        assert report.windows_complete == 6

    @pytest.mark.asyncio
    async def test_the_bulk_skip_is_constant_in_the_number_of_skipped_windows(
        self, db_session
    ) -> None:
        horizon = NOW.replace(hour=12, minute=0) - timedelta(days=1)
        counts: dict[int, int] = {}
        for skipped in (10, 500):
            inst = await make_instance(db_session)
            await set_watermark(db_session, inst, horizon - (skipped + 1) * HOUR)
            with recorded_statements(db_session) as statements:
                report = await collector(db_session, ScriptedProvider({})).collect(inst)
            counts[skipped] = len(statements)
            assert report.windows_skipped == skipped
            assert len(await windows(db_session, inst)) == skipped + 6
            wm = await db_session.scalar(
                select(CollectionWatermark).where(CollectionWatermark.instance_id == inst.id)
            )
            assert wm.watermark_at == horizon + 5 * HOUR

        assert counts[10] == counts[500], counts

    @pytest.mark.asyncio
    async def test_skipping_never_overwrites_a_collected_window(self, db_session) -> None:
        inst = await make_instance(db_session)
        horizon = NOW.replace(hour=12, minute=0) - timedelta(days=1)
        early = horizon - 3 * HOUR + timedelta(minutes=5)
        await collector(db_session, ScriptedProvider({}), now=early).collect(inst)
        done = (await windows(db_session, inst))[0]
        assert done.window_start < horizon  # inside the range the skip will cover
        wm = await db_session.scalar(select(CollectionWatermark))
        wm.watermark_at = done.window_start - 3 * HOUR
        await db_session.flush()

        await collector(db_session, ScriptedProvider({})).collect(inst)

        again = {w.window_start: w for w in await windows(db_session, inst)}
        assert again[done.window_start].status == RuleMatchWindowStatus.COMPLETE


# ---------------------------------------------------------------- rule health
EVAL_NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)


def evaluator(session, **cfg) -> RuleHealthEvaluator:
    settings = get_settings().model_copy(
        update={
            "rule_health_window_days": 1,
            "rule_match_window_seconds": 3600,
            "rule_match_settle_lag_seconds": 300,
            "rule_health_flap_threshold": 1,
            **cfg,
        }
    )
    return RuleHealthEvaluator(session, settings=settings, clock=lambda: EVAL_NOW)


async def fill_windows(session, instance, *, overrides: dict[int, str] | None = None):
    """Every window of the 24 h span ending at 11:00 (23 of them), COMPLETE unless told."""
    overrides = overrides or {}
    first = EVAL_NOW - timedelta(days=1)
    for i in range(23):
        session.add(
            RuleMatchWindow(
                instance_id=instance.id,
                window_start=first + i * HOUR,
                window_seconds=3600,
                origin="LIVE",
                status=overrides.get(i, RuleMatchWindowStatus.COMPLETE),
                rows_returned=0,
                row_cap=100,
                unknown_ids=0,
                query_version="rule_match/v1",
                collected_at=EVAL_NOW,
                attempts=1,
            )
        )
    await session.flush()


async def latest(session, rule) -> RuleHealthSnapshot:
    return await session.scalar(
        select(RuleHealthSnapshot)
        .where(RuleHealthSnapshot.rule_id == rule.id)
        .order_by(RuleHealthSnapshot.evaluated_at.desc())
        .limit(1)
    )


class TestRuleHealth:
    @pytest.mark.asyncio
    async def test_event_rule_absent_from_complete_windows_is_never_observed(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst)

        await evaluator(db_session).evaluate_instance(inst)

        snap = await latest(db_session, rule)
        assert snap.status == RuleHealthStatus.NEVER_OBSERVED
        assert snap.logic_version == 2
        assert snap.evidence["match_windows"]["complete"] == 23

    @pytest.mark.asyncio
    @pytest.mark.parametrize("bad", ["TRUNCATED", "FAILED"])
    async def test_one_bad_window_makes_the_same_rule_insufficient_data(
        self, db_session, bad
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst, overrides={7: bad})

        await evaluator(db_session).evaluate_instance(inst)

        assert (await latest(db_session, rule)).status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_a_skipped_window_in_the_span_makes_event_rules_insufficient_data(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst)
        victim = await db_session.scalar(
            select(RuleMatchWindow).order_by(RuleMatchWindow.window_start).offset(5).limit(1)
        )
        victim.status = RuleMatchWindowStatus.FAILED
        victim.attempts = 0
        victim.last_error = "skipped: beyond the catch-up horizon"
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)

        assert (await latest(db_session, rule)).status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_a_missing_window_makes_it_insufficient_data(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)
        await fill_windows(db_session, inst)
        victim = await db_session.scalar(
            select(RuleMatchWindow).order_by(RuleMatchWindow.window_start).offset(3).limit(1)
        )
        await db_session.delete(victim)
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)

        assert (await latest(db_session, rule)).status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_common_rule_absence_is_not_never_observed(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100, rule_type="COMMON")
        await fill_windows(db_session, inst)

        await evaluator(db_session).evaluate_instance(inst)

        status = (await latest(db_session, rule)).status
        assert status != RuleHealthStatus.NEVER_OBSERVED
        assert status == RuleHealthStatus.INSUFFICIENT_DATA

    @pytest.mark.asyncio
    async def test_common_rule_with_matches_is_healthy_by_lower_bound(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100, rule_type="COMMON")
        await fill_windows(db_session, inst)
        db_session.add(
            RuleMatchMetric(
                rule_id=rule.id, bucket_start=EVAL_NOW - 3 * HOUR, bucket_seconds=3600,
                match_records=4, matched_events=9,
            )
        )
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)

        snap = await latest(db_session, rule)
        assert snap.status == RuleHealthStatus.HEALTHY
        assert snap.trigger_count == 9
        assert snap.evidence["metric_kind"] == "measured_lower_bound"

    @pytest.mark.asyncio
    async def test_flow_rule_is_unchanged_whatever_the_windows_say(self, db_session) -> None:
        inst = await make_instance(db_session)
        without = await add_rule(db_session, inst, 100, rule_type="FLOW")
        await evaluator(db_session).evaluate_instance(inst)
        before = await latest(db_session, without)

        await fill_windows(db_session, inst)
        db_session.add(
            RuleMatchMetric(
                rule_id=without.id, bucket_start=EVAL_NOW - 3 * HOUR, bucket_seconds=3600,
                match_records=4, matched_events=9999,
            )
        )
        await db_session.flush()
        await evaluator(db_session).evaluate_instance(inst)
        after = await latest(db_session, without)

        assert before.status == after.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert (before.trigger_count, before.reason) == (after.trigger_count, after.reason)

    @pytest.mark.asyncio
    async def test_event_rule_above_its_expected_rate_is_noisy(self, db_session) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100, expected_daily_firings=10.0)
        await fill_windows(db_session, inst)
        db_session.add(
            RuleMatchMetric(
                rule_id=rule.id, bucket_start=EVAL_NOW - 3 * HOUR, bucket_seconds=3600,
                match_records=40, matched_events=500,
            )
        )
        await db_session.flush()

        await evaluator(db_session).evaluate_instance(inst)

        snap = await latest(db_session, rule)
        assert snap.status == RuleHealthStatus.NOISY
        assert snap.trigger_count == 500

    @pytest.mark.asyncio
    async def test_without_any_match_windows_event_rules_are_judged_as_before(
        self, db_session
    ) -> None:
        inst = await make_instance(db_session)
        rule = await add_rule(db_session, inst, 100)

        await evaluator(db_session).evaluate_instance(inst)

        snap = await latest(db_session, rule)
        assert snap.status == RuleHealthStatus.INSUFFICIENT_DATA
        assert "match_coverage" not in snap.evidence

    @pytest.mark.asyncio
    async def test_match_queries_are_constant_in_the_number_of_rules(self, db_session) -> None:
        seen: dict[int, int] = {}
        for n in (3, 30):
            inst = await make_instance(db_session)
            for i in range(n):
                await add_rule(db_session, inst, 1000 + i)
            await fill_windows(db_session, inst)
            with recorded_statements(db_session, only="rule_match_") as statements:
                await evaluator(db_session).evaluate_instance(inst)
            seen[n] = len(statements)

        assert seen[3] == seen[30]
