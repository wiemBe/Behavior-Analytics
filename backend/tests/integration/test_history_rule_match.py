"""The rule-match history adapter in the collector, against a real database (HB-5).

The acceptance bar of `docs/tasks/HB-5.md`: the history rows equal the live
ones; the floor proof; the console-total scan; the operator's every exit path;
and a constant statement count.
"""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta
from unittest import mock

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine

from app.collectors.rule_match_collector import COLLECTOR_NAME, RuleMatchCollector
from app.history.progress_codec import ProgressCodecError
from app.models.enums import HistoryDecisionAnswer, HistoryDecisionSource, RuleHealthStatus
from app.models.history_backfill import HistoryBackfillDecision, HistoryBackfillScope
from app.models.monitoring import CollectionWatermark
from app.models.rule import (
    AnalyticsRule,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.providers.base import ConsoleEventTotals, ProviderUnavailableError
from app.rules.matches import CoverageState, assess_coverage
from app.services.ariel_permit import _ARIEL_PRIORITY, ArielPermitUnavailable
from app.services.locks import collector_lock_key
from tests.integration.factories import make_instance
from tests.integration.history_reason_support import assert_coded
from tests.integration.test_rule_match_collection import (
    HOUR,
    NOW,
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

#: The pinned statement count of one collector tick: 16, including HB-19's
#: savepoint pair around the console-wide history step.
#: The pre-HB-19 count was measured on 2026-10-19 over every leg below, the
#: large steps of `TestLargeStep` included. A step costs the same statements
#: whatever it reads and writes (AGENTS.md §2 rule 9): the scan and chunk
#: searches, the scope's own queries, and one executemany INSERT per table
#: however many rows the step writes.
HISTORY_STATEMENTS_PER_TICK = 16

#: The oldest live window of the history tests: 11:00, one hour before EVAL_NOW.
ANCHOR = NOW.replace(hour=11, minute=0)
#: The rule-match window read is at least a week (DEFAULT_MIN_WINDOW_DAYS).
DAYS = 7
TARGET_START = ANCHOR - timedelta(days=DAYS)

#: Seven whole hours just before the anchor: 04:00 .. 10:00.
H0 = ANCHOR - 7 * HOUR
HISTORY_WINDOWS = [H0 + i * HOUR for i in range(7)]

#: Two known rules match in three of the seven windows; one unknown id shows up.
MATCHES = {
    H0: {100: (3, 9)},
    H0 + 2 * HOUR: {101: (1, 1)},
    H0 + 6 * HOUR: {100: (2, 4), 900: (5, 5)},
}

#: 100 rules that matched (1 record, 1 event) in every one of the 168 hourly
#: windows of the requested week: 16,800 metric rows for one step.
DENSE_RULES = {100 + j: (1, 1) for j in range(100)}


def by_window(start, table=None):
    return (table or MATCHES).get(start, {})


HISTORY_SETTINGS = {
    "history_backfill_enabled": True,
    "history_backfill_days": DAYS,
    "history_backfill_max_hours": 72,
    "history_backfill_searches_per_tick": 10,
    "rule_health_window_days": DAYS,
    "rule_match_max_windows_per_run": 7,
    "rule_match_max_rows": 5000,
}


class HistoryProvider(KeyedProvider):
    """A keyed provider that also answers the console-total floor scan.

    `first_event` is the only bucket the console scan reports events in (the
    mock provider reports every bucket); None means the console holds no event,
    which is the `UNAVAILABLE` case. `fail_scan` makes the scan search fail;
    `KeyedProvider.fail_when` makes the keyed read fail.
    """

    def __init__(self, matches, *, first_event=None, fail_scan=False, fail_when=None) -> None:
        super().__init__(matches, fail_when=fail_when)
        self.first_event = first_event
        self.fail_scan = fail_scan
        self.scan_calls: list[tuple[datetime, datetime]] = []

    async def get_console_event_totals(self, window_start, window_end, *, bucket_seconds):
        self.scan_calls.append((window_start, window_end))
        if self.fail_scan:
            raise ProviderUnavailableError("QRadar is unreachable")
        totals: dict[datetime, int] = {}
        if (
            self.first_event is not None
            and window_start <= self.first_event < window_end
        ):
            totals[self.first_event] = 9
        return ConsoleEventTotals(
            window_start=window_start,
            window_end=window_end,
            bucket_seconds=bucket_seconds,
            totals=totals,
            query_version="console_event_totals/v1",
        )


def history_collector(session, provider, *, now=NOW, **overrides) -> RuleMatchCollector:
    merged = {**HISTORY_SETTINGS, **overrides}
    return collector(session, provider, now=now, **merged)


async def seed_live_window(
    session,
    instance,
    start: datetime,
    *,
    status: RuleMatchWindowStatus = RuleMatchWindowStatus.COMPLETE,
    attempts: int = 1,
    last_error: str | None = None,
) -> RuleMatchWindow:
    row = RuleMatchWindow(
        instance_id=instance.id,
        window_start=start,
        window_seconds=3600,
        origin="LIVE",
        status=status,
        rows_returned=0,
        row_cap=5000,
        unknown_ids=0,
        query_version="rule_match/v1",
        collected_at=NOW,
        attempts=attempts,
        last_error=last_error,
    )
    session.add(row)
    await session.flush()
    return row


async def seed_decision(session, instance, **overrides) -> None:
    values = {
        "answer": HistoryDecisionAnswer.YES.value,
        "days": DAYS,
        "decided_at": NOW,
        "decided_by": "tester",
        "source": HistoryDecisionSource.INSTALLER.value,
    }
    values.update(overrides)
    session.add(HistoryBackfillDecision(instance_id=instance.id, **values))
    await session.flush()


async def history_windows(session, instance):
    return [w for w in await windows(session, instance) if w.origin == "HISTORY"]


async def scope_row(session, instance) -> HistoryBackfillScope | None:
    return await session.scalar(
        select(HistoryBackfillScope)
        .where(
            HistoryBackfillScope.instance_id == instance.id,
            HistoryBackfillScope.family == "RULE_MATCH",
            HistoryBackfillScope.scope_key == "",
        )
        .execution_options(populate_existing=True)
    )


async def window_facts(session, instance, *, origin: str | None = None) -> dict:
    rows = await windows(session, instance)
    if origin is not None:
        rows = [w for w in rows if w.origin == origin]
    return {
        w.window_start: (
            w.window_seconds,
            str(w.status),
            w.rows_returned,
            w.row_cap,
            w.unknown_ids,
            w.attempts,
            w.last_error,
        )
        for w in rows
    }


async def metric_facts(session, instance) -> list:
    rows = (
        await session.execute(
            select(
                AnalyticsRule.qradar_id,
                RuleMatchMetric.bucket_start,
                RuleMatchMetric.bucket_seconds,
                RuleMatchMetric.match_records,
                RuleMatchMetric.matched_events,
            )
            .join(AnalyticsRule, AnalyticsRule.id == RuleMatchMetric.rule_id)
            .where(AnalyticsRule.instance_id == instance.id)
            .order_by(RuleMatchMetric.bucket_start, AnalyticsRule.qradar_id)
        )
    ).all()
    return [tuple(row) for row in rows]


async def seed_anchor_and_decision(session, instance) -> None:
    """A live window at the anchor and a YES: the common history-test setup."""
    await seed_live_window(session, instance, ANCHOR)
    await set_watermark(session, instance, ANCHOR)
    await seed_decision(session, instance)


class TestEquivalence:
    @pytest.mark.asyncio
    async def test_history_writes_what_the_live_pass_wrote(self, db_session) -> None:
        live = await make_instance(db_session)
        history = await make_instance(db_session)
        for instance in (live, history):
            await add_rule(db_session, instance, 100)
            await add_rule(db_session, instance, 101)

        # The live path collects 04:00 .. 10:00 with one keyed search.
        await set_watermark(db_session, live, H0 - HOUR)
        live_provider = KeyedProvider(by_window)
        live_report = await collector(
            db_session, live_provider, rule_match_max_windows_per_run=7, rule_match_max_rows=5000
        ).collect(live)

        # The history path reads the same span from its anchor at 11:00.
        await seed_anchor_and_decision(db_session, history)
        history_provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        history_report = await history_collector(db_session, history_provider).collect(history)

        assert live_report.windows_attempted == 7
        assert history_report.history_windows_written == 7
        assert history_report.history_searches >= 1
        # The floor was cut later than the asked start, so the reading is PARTIAL.
        assert history_report.history_state == "PARTIAL"
        assert "later than the asked start" in history_report.history_reason

        live_facts = await window_facts(db_session, live)
        assert set(live_facts) == set(HISTORY_WINDOWS)
        history_facts = await window_facts(db_session, history, origin="HISTORY")
        assert history_facts == live_facts
        assert await metric_facts(db_session, history) == await metric_facts(db_session, live)
        # The anchor is untouched by history.
        anchor = (await window_facts(db_session, history))[ANCHOR]
        assert anchor[1] == "COMPLETE"

    @pytest.mark.asyncio
    async def test_nothing_is_written_before_the_floor(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await add_rule(db_session, instance, 101)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        await history_collector(db_session, provider).collect(instance)

        starts = [w.window_start for w in await history_windows(db_session, instance)]
        assert min(starts) == H0
        # The scan's span begins a day before the first event; nothing of it is written.
        assert provider.scan_calls[0][0] == TARGET_START - 24 * HOUR
        assert all(start >= TARGET_START for start in starts)


class TestFloorProof:
    @pytest.mark.asyncio
    async def test_a_console_with_no_event_is_unavailable(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=None)

        first = await history_collector(db_session, provider).collect(instance)
        assert first.history_state == "READING"  # the first scan proved nothing yet
        second = await history_collector(db_session, provider).collect(instance)

        assert second.history_state == "UNAVAILABLE"
        assert await history_windows(db_session, instance) == []
        scope = await scope_row(db_session, instance)
        assert scope.state == "UNAVAILABLE"
        assert_coded(scope)

    @pytest.mark.asyncio
    async def test_a_scan_failure_is_retried_then_unavailable(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, fail_scan=True)

        for _ in range(2):
            report = await history_collector(db_session, provider).collect(instance)
            assert report.history_state == "READING"
        third = await history_collector(db_session, provider).collect(instance)

        assert third.history_state == "UNAVAILABLE"
        assert await history_windows(db_session, instance) == []


class TestNeverOverLive:
    @pytest.mark.asyncio
    async def test_the_failed_anchor_is_never_replaced(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await add_rule(db_session, instance, 101)
        anchor = await seed_live_window(
            db_session,
            instance,
            ANCHOR,
            status=RuleMatchWindowStatus.FAILED,
            attempts=0,
            last_error="skipped: beyond the catch-up horizon",
        )
        await set_watermark(db_session, instance, ANCHOR)
        await seed_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        await history_collector(db_session, provider).collect(instance)

        stored = next(w for w in await windows(db_session, instance) if w.window_start == ANCHOR)
        # `rule_match_window` has no surrogate key: the window start is the row.
        assert stored.window_start == anchor.window_start
        assert stored.origin == "LIVE"
        assert stored.status == RuleMatchWindowStatus.FAILED
        assert stored.attempts == 0
        assert stored.last_error == "skipped: beyond the catch-up horizon"
        assert ANCHOR not in {w.window_start for w in await history_windows(db_session, instance)}

    @pytest.mark.asyncio
    async def test_re_reading_leaves_the_stored_history_rows_alone(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await add_rule(db_session, instance, 101)
        await seed_anchor_and_decision(db_session, instance)
        first = await history_collector(
            db_session, HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        ).collect(instance)
        before = await window_facts(db_session, instance, origin="HISTORY")
        assert first.history_windows_written == 7

        # An operator asks to read history now: the restart re-reads the very same
        # span with different counts and must not touch the stored rows.
        decision = await db_session.scalar(
            select(HistoryBackfillDecision).where(
                HistoryBackfillDecision.instance_id == instance.id
            )
        )
        decision.start_requested_at = NOW + HOUR
        decision.start_requested_by = "bob"
        await db_session.flush()
        changes = dict(MATCHES)
        changes[H0] = {100: (99, 99)}
        second = await history_collector(
            db_session, HistoryProvider(by_window_of(changes), first_event=H0 - 24 * HOUR)
        ).collect(instance)

        assert second.history_windows_written == 7  # the same span read again
        assert await window_facts(db_session, instance, origin="HISTORY") == before
        metrics = await metric_facts(db_session, instance)
        assert metrics[0][3:5] == (3, 9)  # the first read's H0 row


def by_window_of(table):
    def lookup(start):
        return table.get(start, {})

    return lookup


class TestOperatorPaths:
    @pytest.mark.asyncio
    async def test_no_decision_reads_nothing(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_live_window(db_session, instance, ANCHOR)
        await set_watermark(db_session, instance, ANCHOR)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        report = await history_collector(db_session, provider).collect(instance)

        assert report.history_reason == "the console has no Yes answer"
        assert report.history_searches == 0
        assert provider.scan_calls == []
        assert await scope_row(db_session, instance) is None

    @pytest.mark.asyncio
    async def test_a_no_decision_reads_nothing(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_live_window(db_session, instance, ANCHOR)
        await set_watermark(db_session, instance, ANCHOR)
        await seed_decision(
            db_session,
            instance,
            answer=HistoryDecisionAnswer.NO.value,
            days=None,
        )
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        report = await history_collector(db_session, provider).collect(instance)

        assert report.history_reason == "the console has no Yes answer"
        assert provider.scan_calls == []
        assert await scope_row(db_session, instance) is None

    @pytest.mark.asyncio
    async def test_yes_before_the_anchor_waits_then_reads(self, db_session) -> None:
        instance = await make_instance(db_session)
        # The watermark is at the settled boundary, so the live pass collects nothing.
        await set_watermark(db_session, instance, ANCHOR)
        await seed_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=None)

        waiting = await history_collector(db_session, provider).collect(instance)

        assert waiting.history_state == "WAITING"
        assert (await scope_row(db_session, instance)).state == "WAITING"

        later = NOW.replace(hour=13, minute=10)
        reading = await history_collector(db_session, provider, now=later).collect(instance)

        assert reading.history_state == "READING"
        assert (await scope_row(db_session, instance)).state == "READING"
        # The anchor appeared: 12:00 was collected live.
        assert any(w.window_start == datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
                   for w in await windows(db_session, instance))

    @pytest.mark.asyncio
    async def test_a_live_pass_that_used_the_whole_cap_waits(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await set_watermark(db_session, instance, ANCHOR - HOUR)
        await seed_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        report = await history_collector(
            db_session, provider, search_cadence_max_searches_per_tick=1
        ).collect(instance)

        assert report.searches_sent == 1
        assert report.history_searches == 0
        assert report.history_state == "READING"
        assert provider.scan_calls == []  # the budget was spent before the scan

    @pytest.mark.asyncio
    async def test_the_time_budget_waits(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        # Patch the collector's own reference to `time`, not the stdlib module:
        # asyncio calls `time.monotonic` too, and a side-effect list shared with
        # the event loop would break every later test in the session.
        with mock.patch("app.collectors.rule_match_collector.time") as fake_time:
            fake_time.monotonic.side_effect = [0.0, 1e9]
            report = await history_collector(db_session, provider).collect(instance)

        assert report.history_searches == 0
        assert report.history_state == "READING"
        assert provider.scan_calls == []

    @pytest.mark.asyncio
    async def test_the_switch_off_creates_nothing(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        report = await history_collector(
            db_session, provider, history_backfill_enabled=False
        ).collect(instance)

        assert report.history_reason == "history reading is switched off"
        assert provider.scan_calls == []
        assert await scope_row(db_session, instance) is None

    @pytest.mark.asyncio
    async def test_a_stop_request_reads_nothing(self, db_session) -> None:
        instance = await make_instance(db_session)
        await seed_live_window(db_session, instance, ANCHOR)
        await set_watermark(db_session, instance, ANCHOR)
        await seed_decision(
            db_session,
            instance,
            stop_requested_at=NOW,
            stop_requested_by="alice",
        )
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)

        report = await history_collector(db_session, provider).collect(instance)

        assert report.history_reason == "an operator stopped history reading"
        assert provider.scan_calls == []
        assert await scope_row(db_session, instance) is None

    @pytest.mark.asyncio
    async def test_a_start_request_after_a_finished_reading_restarts_it(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await add_rule(db_session, instance, 101)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        await history_collector(db_session, provider).collect(instance)
        finished = await scope_row(db_session, instance)
        assert finished.state in ("COMPLETE", "PARTIAL")
        assert_coded(finished)
        scans_before = len(provider.scan_calls)

        decision = await db_session.scalar(
            select(HistoryBackfillDecision).where(
                HistoryBackfillDecision.instance_id == instance.id
            )
        )
        decision.start_requested_at = finished.finished_at + HOUR
        decision.start_requested_by = "bob"
        await db_session.flush()

        report = await history_collector(db_session, provider).collect(instance)

        assert len(provider.scan_calls) > scans_before  # a fresh scan: the restart
        assert report.history_state in ("COMPLETE", "PARTIAL")

    @pytest.mark.asyncio
    async def test_a_held_lock_skips_the_run(self, db_session, db_schema) -> None:
        instance = await make_instance(db_session)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        run = history_collector(db_session, provider)
        namespace = run.settings.collection_advisory_lock_namespace
        key = collector_lock_key(instance.id, COLLECTOR_NAME)

        holder = create_async_engine(db_schema, future=True)
        try:
            async with holder.connect() as conn:
                locked = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:ns, :key)").bindparams(
                        ns=namespace, key=key
                    )
                )
                assert locked
                report = await run.collect(instance)
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                        ns=namespace, key=key
                    )
                )
        finally:
            await holder.dispose()

        assert report.skipped_locked is True
        assert provider.scan_calls == []
        assert await scope_row(db_session, instance) is None

    @pytest.mark.asyncio
    async def test_a_keyed_read_failure_does_not_lose_the_live_pass(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(
            by_window, first_event=H0 - 24 * HOUR, fail_when=lambda start, end: True
        )

        report = await history_collector(db_session, provider).collect(instance)

        assert report.history_searches >= 1
        assert report.history_state == "READING"
        assert report.error is None
        assert report.partial_failure is False
        assert await history_windows(db_session, instance) == []

    @pytest.mark.asyncio
    async def test_a_raised_step_fault_is_visible_then_a_success_clears_it(
        self, db_session, monkeypatch
    ) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        await history_collector(
            db_session, provider, search_cadence_max_searches_per_tick=1
        ).collect(instance)
        reading = await scope_row(db_session, instance)
        assert reading is not None and reading.state == "READING"
        assert_coded(reading)
        live_before = len(await windows(db_session, instance))
        original = RuleMatchCollector._read_history

        async def broken(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            raise ProgressCodecError("SELECT sensitive_property FROM events")

        monkeypatch.setattr(RuleMatchCollector, "_read_history", broken)
        report = await history_collector(db_session, provider, now=NOW + HOUR).collect(instance)

        failed = await scope_row(db_session, instance)
        assert report.history_error == "ProgressCodecError"
        assert failed is not None and failed.state == "READING"
        assert failed.last_error_class == "ProgressCodecError"
        assert failed.last_error_at == NOW + HOUR
        assert len(await windows(db_session, instance)) > live_before

        monkeypatch.setattr(RuleMatchCollector, "_read_history", original)
        await history_collector(db_session, provider, now=NOW + 2 * HOUR).collect(instance)
        cleared = await scope_row(db_session, instance)
        assert cleared is not None
        assert cleared.last_error_class is None and cleared.last_error_at is None

    @pytest.mark.asyncio
    async def test_capacity_refusal_yields_without_halving_or_marking(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        await history_collector(
            db_session, provider, search_cadence_max_searches_per_tick=1
        ).collect(instance)
        before = await scope_row(db_session, instance)
        assert before is not None and before.state == "READING"
        before_progress = copy.deepcopy(before.progress)
        before_through = before.through
        before.last_error_class = "EarlierProviderError"
        before.last_error_at = NOW
        await db_session.commit()
        original = provider.get_rule_matches_windowed

        async def refuse_history(window_start, window_end, **kwargs):  # type: ignore[no-untyped-def]
            if window_end <= ANCHOR:
                assert _ARIEL_PRIORITY.get() == "background"
                raise ArielPermitUnavailable(7, 2, 4)
            return await original(window_start, window_end, **kwargs)

        provider.get_rule_matches_windowed = refuse_history  # type: ignore[method-assign]
        report = await history_collector(db_session, provider, now=NOW + HOUR).collect(instance)

        yielded = await scope_row(db_session, instance)
        assert report.history_state == "READING"
        assert yielded is not None and yielded.state == "READING"
        assert yielded.through == before_through and yielded.progress == before_progress
        assert yielded.last_error_class == "EarlierProviderError"
        assert yielded.last_error_at == NOW
        assert yielded.reason == "deferred: no Ariel permit within 7 s"
        assert yielded.reason_i18n is None
        assert await history_windows(db_session, instance) == []
        assert _ARIEL_PRIORITY.get() == "live"

    @pytest.mark.asyncio
    async def test_an_unexpected_step_fault_keeps_live_and_rolls_history_back(
        self, db_session, monkeypatch
    ) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        await history_collector(
            db_session, provider, search_cadence_max_searches_per_tick=1
        ).collect(instance)
        before = await scope_row(db_session, instance)
        assert before is not None and before.state == "READING"
        before_progress = dict(before.progress)
        before_through = before.through
        history_before = await history_windows(db_session, instance)
        live_before = len(
            [row for row in await windows(db_session, instance) if row.origin == "LIVE"]
        )
        watermark_before = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.instance_id == instance.id,
                CollectionWatermark.collector == COLLECTOR_NAME,
            )
        )
        assert watermark_before is not None
        old_watermark = watermark_before.watermark_at
        original = RuleMatchCollector._read_history

        async def write_then_crash(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await original(self, *args, **kwargs)
            raise RuntimeError("SELECT sensitive_property FROM events")

        monkeypatch.setattr(RuleMatchCollector, "_read_history", write_then_crash)
        failed_at = NOW + HOUR
        report = await history_collector(
            db_session, provider, now=failed_at
        ).collect(instance)

        failed = await scope_row(db_session, instance)
        assert report.history_error == "RuntimeError"
        assert failed is not None and failed.state == "READING"
        assert failed.progress == before_progress and failed.through == before_through
        assert failed.last_error_class == "RuntimeError"
        assert failed.last_error_at == failed_at
        assert await history_windows(db_session, instance) == history_before
        assert len(
            [row for row in await windows(db_session, instance) if row.origin == "LIVE"]
        ) > live_before
        await db_session.refresh(watermark_before)
        assert watermark_before.watermark_at > old_watermark  # type: ignore[operator]

        monkeypatch.setattr(RuleMatchCollector, "_read_history", original)
        await history_collector(db_session, provider, now=NOW + 2 * HOUR).collect(instance)
        cleared = await scope_row(db_session, instance)
        assert cleared is not None
        assert cleared.last_error_class is None and cleared.last_error_at is None

    @pytest.mark.asyncio
    async def test_a_fault_after_the_step_wrote_rows_reports_the_pre_step_counts(
        self, db_session, monkeypatch
    ) -> None:
        """A rolled-back step's written counts cannot stay in the tick's report."""
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await seed_anchor_and_decision(db_session, instance)
        provider = HistoryProvider(by_window, first_event=H0 - 24 * HOUR)
        await history_collector(
            db_session, provider, search_cadence_max_searches_per_tick=1
        ).collect(instance)
        before = await scope_row(db_session, instance)
        assert before is not None and before.state == "READING"
        history_before = await history_windows(db_session, instance)
        rows_original = RuleMatchCollector._write_history_rows
        written: list[tuple[int, int]] = []

        async def count_then_write(self, window_rows, metric_rows):  # type: ignore[no-untyped-def]
            await rows_original(self, window_rows, metric_rows)
            written.append((len(window_rows), len(metric_rows)))

        monkeypatch.setattr(RuleMatchCollector, "_write_history_rows", count_then_write)
        scope_original = RuleMatchCollector._write_history_scope

        async def write_then_crash(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            await scope_original(self, *args, **kwargs)
            raise RuntimeError("SELECT sensitive_property FROM events")

        monkeypatch.setattr(RuleMatchCollector, "_write_history_scope", write_then_crash)
        failed_at = NOW + HOUR

        report = await history_collector(db_session, provider, now=failed_at).collect(instance)

        assert written and written[0][0] > 0  # the step wrote rows first
        # Everything the step said about itself was said inside the savepoint, so
        # the report is back to its pre-step defaults: no state, no counts.
        assert report.history_state is None
        assert report.history_reason is None
        assert report.history_searches == 0
        assert report.history_windows_written == 0
        assert report.history_rows_written == 0
        assert report.history_error == "RuntimeError"
        failed = await scope_row(db_session, instance)
        assert failed is not None and failed.state == "READING"
        assert failed.last_error_class == "RuntimeError"
        assert failed.last_error_at == failed_at
        assert await history_windows(db_session, instance) == history_before


def full_read_matches(start: datetime) -> dict:
    """The history read of the whole asked window: every window COMPLETE, no rows.

    The keyed provider calls `matches(start)`, so this is the by-window lookup; an
    empty mapping reads as a whole, silent window.
    """
    return {}


class TestCoverage:
    @pytest.mark.asyncio
    async def test_a_full_read_covers_its_span(self, db_session) -> None:
        instance = await make_instance(db_session)
        await add_rule(db_session, instance, 100)
        await seed_anchor_and_decision(db_session, instance)
        # One event an hour before the asked start proves the whole span.
        provider = HistoryProvider(full_read_matches, first_event=TARGET_START - 24 * HOUR)

        report = await history_collector(db_session, provider).collect(instance)

        assert report.history_state == "COMPLETE"
        assert report.history_windows_written == DAYS * 24
        rows = await windows(db_session, instance)
        coverage = assess_coverage(
            [(w.window_start, w.window_seconds, w.status) for w in rows],
            evaluation_start=TARGET_START,
            now=ANCHOR,
            window_seconds=3600,
            settle_seconds=0,
            ever_collected=True,
        )
        assert coverage.state is CoverageState.COMPLETE
        assert coverage.expected == coverage.complete == DAYS * 24

    @pytest.mark.asyncio
    async def test_a_truncated_read_leaves_the_span_missing(self, db_session) -> None:
        instance = await make_instance(db_session)
        for qid in range(100, 103):
            await add_rule(db_session, instance, qid)
        await seed_anchor_and_decision(db_session, instance)
        # Three rules match in every window with a cap of 10: a multi-window chunk truncates.
        dense = lambda start: {100: (1, 1), 101: (1, 1), 102: (1, 1)}  # noqa: E731
        provider = HistoryProvider(dense, first_event=H0 - 24 * HOUR)

        report = await history_collector(
            db_session, provider, rule_match_max_rows=10
        ).collect(instance)

        assert report.history_windows_written == 0
        assert report.history_state == "READING"
        rows = await history_windows(db_session, instance)
        coverage = assess_coverage(
            [(w.window_start, w.window_seconds, w.status) for w in rows],
            evaluation_start=H0,
            now=ANCHOR,
            window_seconds=3600,
            settle_seconds=0,
            ever_collected=True,
        )
        assert coverage.state is CoverageState.INCOMPLETE
        assert coverage.missing == coverage.expected == 7

    @pytest.mark.asyncio
    async def test_rule_health_reads_a_history_window_like_a_live_one(self, db_session) -> None:
        live = await make_instance(db_session)
        history = await make_instance(db_session)
        live_rule = await add_rule(db_session, live, 100)
        history_rule = await add_rule(db_session, history, 100)
        await fill_windows(db_session, live, origin="LIVE")
        await fill_windows(db_session, history, origin="HISTORY")

        await evaluator(db_session).evaluate_instance(live)
        await evaluator(db_session).evaluate_instance(history)

        a = await latest(db_session, live_rule)
        b = await latest(db_session, history_rule)
        assert a.status == b.status
        assert a.trigger_count == b.trigger_count
        assert a.evidence == b.evidence
        assert a.evidence["match_windows"]["complete"] == 23

    @pytest.mark.asyncio
    async def test_a_common_rule_read_from_history_stays_a_lower_bound(self, db_session) -> None:
        instance = await make_instance(db_session)
        rule = await add_rule(db_session, instance, 100, rule_type="COMMON")
        await seed_anchor_and_decision(db_session, instance)

        def one_metric(start: datetime) -> dict:
            return {100: (2, 5)} if start == TARGET_START + 3 * HOUR else {}

        provider = HistoryProvider(one_metric, first_event=TARGET_START - 24 * HOUR)

        report = await history_collector(db_session, provider).collect(instance)

        assert report.history_state == "COMPLETE"
        await evaluator(db_session, rule_health_window_days=DAYS).evaluate_instance(instance)
        snapshot = await latest(db_session, rule)
        assert snapshot.status == RuleHealthStatus.HEALTHY
        assert snapshot.trigger_count == 5
        assert snapshot.evidence["metric_kind"] == "measured_lower_bound"
        assert snapshot.evidence["match_windows"]["complete"] == DAYS * 24 - 1


class TestLargeStep:
    """The Round-1 fix (2026-10-19): one insert statement per table, whatever the step.

    `_write_history_rows` used to build one multi-VALUES `.values(rows)` query per
    table, so a step bigger than asyncpg's bind ceiling (32,767) failed inside the
    collector's transaction and rolled the tick back. The tests here run the
    steps that crossed it: metric rows (5 columns) past 32,767 / 5 = 6,553.4 and
    window rows (13 columns) past 32,767 / 20 ≈ 1,638. Both must write, and the
    rows must be exactly the ones the fold produced -- not merely counted.
    """

    @pytest.mark.asyncio
    async def test_a_step_with_more_metric_rows_than_the_bind_limit_writes_them(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        for qid in range(100, 200):
            await add_rule(db_session, instance, qid)
        await seed_anchor_and_decision(db_session, instance)
        dense = DENSE_RULES
        matches: dict[datetime, dict[int, tuple[int, int]]] = {
            TARGET_START + i * HOUR: dense for i in range(168)
        }
        provider = HistoryProvider(by_window_of(matches), first_event=TARGET_START - 24 * HOUR)

        # The first two ticks' single chunk of the week truncates (16,800 then
        # 2 x 8,400 rows against the 5,000 cap); the third tick reads the whole
        # remaining week in four chunks of 42 windows and writes every row once.
        for _ in range(4):
            report = await history_collector(db_session, provider).collect(instance)
            if report.history_state in ("COMPLETE", "PARTIAL"):
                break
        assert report.history_state == "COMPLETE"
        # 168 windows x 100 rules = 16,800 rows of 5 columns = 84,000 binds as
        # one multi-VALUES query: past the ceiling, hence the executemany.
        assert report.history_windows_written == 168
        assert report.history_rows_written == 16_800

        expected_metrics = [
            (qid, start, 3600, 1, 1) for start in matches for qid in range(100, 200)
        ]
        assert await metric_facts(db_session, instance) == expected_metrics
        expected_windows = dict.fromkeys(
            matches, (3600, "COMPLETE", 100, 5000, 0, 1, None)
        )
        assert await window_facts(db_session, instance, origin="HISTORY") == expected_windows

    @pytest.mark.asyncio
    async def test_a_step_with_more_window_rows_than_the_bind_limit_writes_them(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        await seed_live_window(db_session, instance, ANCHOR)
        # The settled boundary of NOW (12:10) at a 60 s window: the live pass
        # collects nothing, and the tick's whole search budget goes to history.
        await set_watermark(db_session, instance, NOW.replace(hour=12, minute=5))
        await seed_decision(db_session, instance)
        provider = HistoryProvider(full_read_matches, first_event=TARGET_START - 24 * HOUR)

        report = await history_collector(
            db_session, provider, rule_match_window_seconds=60
        ).collect(instance)

        assert report.history_state == "COMPLETE"
        assert report.history_searches == 2  # the floor scan and the week's one chunk
        # [TARGET_START, ANCHOR) at 60 s = 10,080 windows of 13 columns = 131,040
        # binds as one multi-VALUES query: past the ceiling, hence the executemany.
        assert report.history_windows_written == 10_080
        assert report.history_rows_written == 0  # every window is silent

        expected = {
            TARGET_START + i * timedelta(seconds=60): (60, "COMPLETE", 0, 5000, 0, 1, None)
            for i in range(10_080)
        }
        assert await window_facts(db_session, instance, origin="HISTORY") == expected
        assert await metric_facts(db_session, instance) == []


class TestStatementCount:
    @pytest.mark.asyncio
    async def test_history_statements_are_constant_in_the_number_of_rules(
        self, db_session, capsys
    ) -> None:
        counts: dict[int, int] = {}
        for rules in (1, 100):
            instance = await make_instance(db_session)
            for i in range(rules):
                await add_rule(db_session, instance, 100 + i)
            await seed_anchor_and_decision(db_session, instance)
            floor = ANCHOR - 12 * HOUR
            matches = {
                floor + i * HOUR: {100 + j: (1, 1) for j in range(rules)} for i in range(12)
            }
            provider = HistoryProvider(
                lambda start, table=matches: table.get(start, {}),
                first_event=floor - 24 * HOUR,
            )
            with recorded_statements(db_session) as statements:
                report = await history_collector(
                    db_session, provider, rule_match_max_rows=5000
                ).collect(instance)
            assert report.history_windows_written == 12
            counts[rules] = len(statements)
            # HB-19 adds only SAVEPOINT / RELEASE SAVEPOINT to the old 14.
            assert counts[rules] == 16, counts

        # Large step of F1: 168 windows x 100 rules = 16,800 metric rows.
        instance_large = await make_instance(db_session)
        for qid in range(100, 200):
            await add_rule(db_session, instance_large, qid)
        await seed_anchor_and_decision(db_session, instance_large)
        floor_large = ANCHOR - 168 * HOUR
        dense_matches: dict[datetime, dict[int, tuple[int, int]]] = {
            floor_large + i * HOUR: {100 + j: (1, 1) for j in range(100)} for i in range(168)
        }
        with recorded_statements(db_session) as statements_large:
            await history_collector(
                db_session,
                HistoryProvider(
                    lambda start, table=dense_matches: table.get(start, {}),
                    first_event=floor_large - 24 * HOUR,
                ),
                rule_match_max_rows=5000,
            ).collect(instance_large)
        assert len(statements_large) == 14
        counts["large_168_100"] = len(statements_large)

        with capsys.disabled():
            print("\nrule-match collection+history statements per tick:", counts)
        assert counts[1] == counts[100] == 16
        assert counts.get("large_168_100") == 14

    @pytest.mark.asyncio
    async def test_history_statements_are_constant_in_the_number_of_windows(
        self, db_session, capsys
    ) -> None:
        # The third size is TestLargeStep's window step: a week of 60 s windows,
        # whose one step writes 10,080 window rows. It must cost the same
        # statements as the 12- and 168-windowhour spans.
        counts: dict[tuple[int, int], int] = {}
        for span_buckets, window_seconds in ((12, 3600), (168, 3600), (10_080, 60)):
            instance = await make_instance(db_session)
            await add_rule(db_session, instance, 100)
            await seed_live_window(db_session, instance, ANCHOR)
            if window_seconds == 3600:
                await set_watermark(db_session, instance, ANCHOR)
                span_start = ANCHOR - span_buckets * HOUR
            else:
                # The settled boundary of NOW: the live pass collects nothing.
                await set_watermark(db_session, instance, NOW.replace(hour=12, minute=5))
                span_start = ANCHOR - (span_buckets * window_seconds) // 3600 * HOUR
            await seed_decision(db_session, instance)
            if window_seconds == 3600:
                matches = {
                    span_start + i * HOUR: {100: (1, 1)} for i in range(span_buckets)
                }
            else:
                # 120 minutes with one rule in each: 120 metric rows, far under
                # the row cap, so the single chunk of the week does not truncate.
                matches = {
                    span_start + i * timedelta(seconds=window_seconds): {100: (1, 1)}
                    for i in range(120)
                }
            provider = HistoryProvider(
                by_window_of(matches), first_event=span_start - 24 * HOUR
            )
            with recorded_statements(db_session) as statements:
                report = await history_collector(
                    db_session,
                    provider,
                    rule_match_max_rows=5000,
                    rule_match_window_seconds=window_seconds,
                ).collect(instance)
            assert report.history_windows_written == span_buckets
            counts[(span_buckets, window_seconds)] = len(statements)
            # HB-19 adds only SAVEPOINT / RELEASE SAVEPOINT to the old 14.
            assert counts[(span_buckets, window_seconds)] == 16, counts

        with capsys.disabled():
            print("\nrule-match collection+history statements per tick:", counts)
        assert (
            counts[(12, 3600)] == counts[(168, 3600)] == counts[(10_080, 60)] == 16
        )
