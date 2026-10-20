"""The catch-all history backfill on a real database (HB-4).

The operator's path: the collector's tick, the scope's lifecycle, the three
guards of §B.4.4, the per-source floors, the prepend and the statement count.
"""

from __future__ import annotations

import math
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import event, func, select, text

from app.collectors.catchall_collector import (
    BOOTSTRAPPED_KEY,
    COLLECTOR_NAME,
    CatchallCollector,
)
from app.core.config import get_settings
from app.history.state import HistoryState
from app.models.alert import Alert
from app.models.catchall import CatchallBucket, CatchallSender
from app.models.enums import CatchallReviewStatus, SearchFamily
from app.models.history_backfill import HistoryBackfillScope
from app.models.monitoring import CollectionWatermark
from app.models.search_cadence import SearchCadenceSetting
from app.providers.base import ProviderError
from app.providers.mock import MockQRadarProvider
from app.repositories.history_backfill import HistoryBackfillRepository
from app.services.locks import collector_lock_key
from tests.integration.factories import FakeClock, make_instance, make_log_source

pytestmark = pytest.mark.integration

BUCKET = 300
NOW = datetime(2026, 10, 13, 12, 10, tzinfo=UTC)
#: The live run at NOW closes at 12:00, so its first-run window is 11:05 .. 12:00.
FIRST = datetime(2026, 10, 13, 11, 5, tzinfo=UTC)
LAST = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
SRC = 1001
#: The history window: seven days, with a one-hour margin before it.
DAYS = 7
MARGIN_HOURS = 1
#: `T0`: the start the reading aims at, the anchor less seven days.
T0 = FIRST - timedelta(days=DAYS)


@pytest.fixture
async def pin_cadence(db_session):  # type: ignore[no-untyped-def]
    db_session.add(
        SearchCadenceSetting(
            family=SearchFamily.CATCHALL_SENDERS.value, cadence_seconds=3600, updated_at=NOW
        )
    )
    await db_session.commit()


def row(sender: str | None, events: int = 10, source: int = SRC) -> dict:
    return {"logsourceid": source, "sender": sender, "events": events, "records": 1}


def provider(script, *, now=NOW) -> MockQRadarProvider:  # type: ignore[no-untyped-def]
    """A mock whose senders are `script(bucket_start)`; the searches are kept."""
    mock = MockQRadarProvider(seed=1, now=now)
    mock.catchall_script = lambda key: script(datetime.fromtimestamp(key * BUCKET, tz=UTC))
    return mock


def always(*rows) -> list[dict]:  # type: ignore[no-untyped-def]
    """The same rows in every bucket, live and in the past."""

    def script(start: datetime) -> list[dict]:
        return [dict(r) for r in rows]

    return script


def after(moment: datetime, *rows) -> list[dict]:  # type: ignore[no-untyped-def]
    """The same rows from `moment` on, and nothing before it."""

    def script(start: datetime) -> list[dict]:
        return [dict(r) for r in rows] if start >= moment else []

    return script


def split(before: list[dict], after_: list[dict]) -> list[dict]:  # type: ignore[no-untyped-def]
    """Different rows in the past and in the live window."""

    def script(start: datetime) -> list[dict]:
        return [dict(r) for r in (before if start < FIRST else after_)]

    return script


def collector(session, prov, *, now=NOW, clock=None, **overrides) -> CatchallCollector:  # type: ignore[no-untyped-def]
    settings = get_settings().model_copy(
        update={
            "search_cadence_max_searches_per_tick": 50,
            "history_backfill_days": DAYS,
            "history_backfill_floor_margin_hours": MARGIN_HOURS,
            **overrides,
        }
    )
    return CatchallCollector(
        session, prov, settings=settings, clock=clock or (lambda: now)
    )


async def setup(session, *, sources: int = 1):  # type: ignore[no-untyped-def]
    inst = await make_instance(session)
    made = [
        await make_log_source(
            session, inst, qradar_id=SRC + index, name=f"catch-{index}", type_id=67
        )
        for index in range(sources)
    ]
    return inst, made


async def decide(session, instance, *, answer: str = "YES", days: int = DAYS) -> None:  # type: ignore[no-untyped-def]
    await HistoryBackfillRepository(session).record_decision(
        instance.id,
        answer=answer,
        days=days if answer == "YES" else None,
        source="UI",
        decided_by="tester",
        at=NOW,
    )


async def scope(session, instance) -> HistoryBackfillScope | None:  # type: ignore[no-untyped-def]
    """The console's scope row, read back (the session caches the row it loaded)."""
    rows = await HistoryBackfillRepository(session).scopes(instance.id, "CATCHALL")
    return rows.get("")


async def buckets(session, origin: str | None = None):  # type: ignore[no-untyped-def]
    statement = select(CatchallBucket)
    if origin is not None:
        statement = statement.where(CatchallBucket.origin == origin)
    return (await session.scalars(statement)).all()


async def senders(session) -> dict[str, CatchallSender]:  # type: ignore[no-untyped-def]
    rows = (
        await session.scalars(
            select(CatchallSender).execution_options(populate_existing=True)
        )
    ).all()
    return {r.identifier: r for r in rows}


async def watermark(session, inst) -> CollectionWatermark | None:  # type: ignore[no-untyped-def]
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == inst.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )


@contextmanager
def counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []
    engine = session.bind.sync_engine

    def record(conn, cursor, statement, *args):  # type: ignore[no-untyped-def]
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


# --------------------------------------------------------------------------- the lifecycle


@pytest.mark.usefixtures("pin_cadence")
class TestLifecycle:
    async def test_without_a_decision_nothing_is_read(self, db_session) -> None:
        inst, _ = await setup(db_session)
        prov = provider(always(row("fw-01", 50)))

        report = await collector(db_session, prov).collect(inst)

        assert report.history_scope_state is None
        assert await scope(db_session, inst) is None
        assert await buckets(db_session, "HISTORY") == []

    async def test_a_no_answer_reads_nothing(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst, answer="NO")
        prov = provider(always(row("fw-01", 50)))

        report = await collector(db_session, prov).collect(inst)

        assert report.history_scope_state is None
        assert await scope(db_session, inst) is None
        assert await buckets(db_session, "HISTORY") == []

    async def test_a_yes_waits_for_the_first_live_bucket_and_then_reads(
        self, db_session
    ) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        broken = provider(always(row("fw-01", 50)))
        broken.catchall_fault = lambda s: ProviderError("QRadar is unreachable")

        first = await collector(db_session, broken).collect(inst)

        # No live bucket, so the scope waits for its anchor.
        assert first.history_scope_state is HistoryState.WAITING
        waiting = await scope(db_session, inst)
        assert waiting is not None and waiting.state == "WAITING"
        assert waiting.progress == {}

        working = provider(always(row("fw-01", 50)))
        second = await collector(db_session, working).collect(inst)

        # The live bucket exists: the scope starts reading (the hand-off is a tick).
        assert second.history_scope_state is HistoryState.READING
        reading = await scope(db_session, inst)
        assert reading is not None and reading.state == "READING"
        assert reading.anchor == FIRST
        assert reading.target_start is None and reading.progress == {}

        third = await collector(db_session, working).collect(inst)

        # The first step reads the window and writes the history buckets.
        assert third.history_scope_state is HistoryState.READING
        assert third.history_buckets_written > 0
        history = await buckets(db_session, "HISTORY")
        assert history and all(b.origin == "HISTORY" for b in history)
        assert min(b.bucket_start for b in history) >= T0

    async def test_a_reading_that_finishes_is_complete(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))

        for _ in range(6):
            report = await collector(db_session, prov).collect(inst)
            if report.history_scope_state in (
                HistoryState.COMPLETE,
                HistoryState.PARTIAL,
                HistoryState.UNAVAILABLE,
            ):
                break

        assert report.history_scope_state is HistoryState.COMPLETE
        finished = await scope(db_session, inst)
        assert finished is not None and finished.state == "COMPLETE"
        assert finished.finished_at is not None
        assert finished.through is not None and finished.through < FIRST
        assert finished.target_start == T0
        history = await buckets(db_session, "HISTORY")
        assert history and max(b.bucket_start for b in history) < FIRST

    async def test_the_switch_off_ends_a_reading_partial(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))
        await collector(db_session, prov).collect(inst)
        assert (await scope(db_session, inst)).state == "READING"

        report = await collector(
            db_session, prov, history_backfill_enabled=False
        ).collect(inst)

        assert report.history_scope_state is HistoryState.PARTIAL
        stopped = await scope(db_session, inst)
        assert stopped is not None and stopped.state == "PARTIAL"
        assert stopped.reason and "switched off" in stopped.reason

    async def test_a_stop_request_ends_the_reading_partial(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))
        await collector(db_session, prov).collect(inst)
        await HistoryBackfillRepository(db_session).request_stop(
            inst.id, by="the operator", at=NOW + timedelta(hours=1)
        )

        report = await collector(db_session, prov).collect(inst)

        assert report.history_scope_state is HistoryState.PARTIAL
        stopped = await scope(db_session, inst)
        assert stopped is not None and stopped.state == "PARTIAL"
        assert stopped.reason and "the operator" in stopped.reason

    async def test_a_start_request_after_the_reading_restarts_it(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))
        for _ in range(6):
            report = await collector(db_session, prov).collect(inst)
            if report.history_scope_state is HistoryState.COMPLETE:
                break
        assert report.history_scope_state is HistoryState.COMPLETE
        finished = await scope(db_session, inst)
        assert finished is not None and finished.finished_at is not None

        await HistoryBackfillRepository(db_session).request_start(
            inst.id, by="the operator", at=finished.finished_at + timedelta(hours=1)
        )
        again = await collector(db_session, prov).collect(inst)

        assert again.history_scope_state is HistoryState.READING
        restarted = await scope(db_session, inst)
        assert restarted is not None and restarted.state == "READING"
        # A restart reads a fresh window from the same anchor.
        assert restarted.anchor == FIRST
        assert restarted.target_start is None and restarted.progress == {}
        assert restarted.finished_at is None

    async def test_a_budget_of_zero_reads_nothing(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))

        report = await collector(
            db_session, prov, search_cadence_max_searches_per_tick=1
        ).collect(inst)

        assert report.history_scope_state is HistoryState.READING
        assert report.history_searches == 0
        assert await buckets(db_session, "HISTORY") == []
        reading = await scope(db_session, inst)
        assert reading is not None and reading.state == "READING"
        assert reading.progress == {}

    async def test_the_time_budget_stops_the_reading(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))
        clock = FakeClock(NOW, step_seconds=30)

        report = await collector(
            db_session, prov, clock=clock, history_backfill_seconds_per_tick=10
        ).collect(inst)

        assert report.history_scope_state is HistoryState.READING
        assert report.history_searches == 0
        assert await buckets(db_session, "HISTORY") == []

    async def test_the_lock_not_acquired_skips_the_run(self, db_session, db_schema) -> None:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        settings = get_settings()
        engine = create_async_engine(db_schema, future=True)
        key = collector_lock_key(inst.id, COLLECTOR_NAME)
        try:
            async with async_sessionmaker(engine, future=True)() as other:
                await other.execute(
                    text("SELECT pg_try_advisory_lock(:ns, :key)").bindparams(
                        ns=settings.collection_advisory_lock_namespace, key=key
                    )
                )
                await other.commit()
                prov = provider(always(row("fw-01", 50)))

                report = await collector(db_session, prov).collect(inst)

                assert report.skipped_locked
                assert report.history_scope_state is None
                assert await scope(db_session, inst) is None
        finally:
            await engine.dispose()

    async def test_a_provider_failure_mid_step_writes_nothing(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))
        await collector(db_session, prov).collect(inst)
        assert (await scope(db_session, inst)).state == "READING"

        failing = provider(always(row("fw-01", 50)))
        # The live window still works; the history window (before the anchor) fails.
        failing.catchall_fault = lambda s: (
            ProviderError("QRadar is unreachable") if s.window_start < FIRST else None
        )

        report = await collector(db_session, failing).collect(inst)

        assert report.history_scope_state is HistoryState.READING
        assert await buckets(db_session, "HISTORY") == []
        reading = await scope(db_session, inst)
        assert reading is not None and reading.state == "READING"
        assert reading.through is None


# --------------------------------------------------------------------------- the guards


@pytest.mark.usefixtures("pin_cadence")
class TestGuards:
    async def test_a_history_sender_is_reviewed_by_history(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        # "past-host" only ever sent in the past: the history creates it.
        prov = provider(split([row("past-host", 50)], [row("fw-01", 50)]))

        for _ in range(3):
            await collector(db_session, prov).collect(inst)

        found = await senders(db_session)
        assert "past-host" in found
        assert found["past-host"].review_status == CatchallReviewStatus.REVIEWED.value
        assert found["past-host"].reviewed_by == "history"
        assert found["past-host"].reviewed_at is not None
        # The live sender keeps its own review state.
        assert found["fw-01"].reviewed_by == "bootstrap"

    async def test_a_history_step_raises_no_alert(self, db_session) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))

        report = await collector(
            db_session, prov, catchall_new_sender_alerts_enabled=True
        ).collect(inst)
        await collector(
            db_session, prov, catchall_new_sender_alerts_enabled=True
        ).collect(inst)

        assert report.alerts_raised == 0
        assert await db_session.scalar(select(func.count()).select_from(Alert)) == 0

    async def test_a_live_sender_first_seen_while_reading_is_reviewed(
        self, db_session
    ) -> None:
        inst, _ = await setup(db_session)
        prov = provider(always(row("fw-01", 50)))
        # The first live run ends the bootstrap; the sender is NEW.
        await collector(db_session, prov).collect(inst)
        await decide(db_session, inst)

        newcomer = provider(always(row("fw-01", 50), row("newcomer", 9)))
        later = NOW + timedelta(hours=1)
        report = await collector(
            db_session, newcomer, now=later, catchall_new_sender_alerts_enabled=True
        ).collect(inst)

        assert report.history_scope_state is HistoryState.READING
        found = await senders(db_session)
        assert found["newcomer"].review_status == CatchallReviewStatus.REVIEWED.value
        assert found["newcomer"].reviewed_by == "bootstrap"
        assert report.alerts_raised == 0

    async def test_after_the_reading_a_new_live_sender_is_new_again(
        self, db_session
    ) -> None:
        inst, _ = await setup(db_session)
        prov = provider(always(row("fw-01", 50)))
        await collector(db_session, prov).collect(inst)
        await decide(db_session, inst)
        for _ in range(6):
            report = await collector(db_session, prov).collect(inst)
            if report.history_scope_state is HistoryState.COMPLETE:
                break
        assert report.history_scope_state is HistoryState.COMPLETE

        newcomer = provider(always(row("fw-01", 50), row("newcomer", 9)))
        later = NOW + timedelta(hours=1)
        report = await collector(
            db_session, newcomer, now=later, catchall_new_sender_alerts_enabled=True
        ).collect(inst)

        found = await senders(db_session)
        assert found["newcomer"].review_status == CatchallReviewStatus.NEW.value
        assert found["newcomer"].reviewed_by is None
        assert report.alerts_raised == 1
        assert await db_session.scalar(select(func.count()).select_from(Alert)) == 1

    async def test_the_bootstrap_key_waits_for_the_reading_to_finish(self, db_session) -> None:
        inst, _ = await setup(db_session)
        prov = provider(always(row("fw-01", 50)))
        await collector(db_session, prov).collect(inst)
        assert (await watermark(db_session, inst)).collection_metadata[BOOTSTRAPPED_KEY] is True

        await decide(db_session, inst)
        # The scope is created and starts reading: the live path is a bootstrap
        # run again, so the key waits for the hand-off.
        await collector(db_session, prov).collect(inst)
        assert (await watermark(db_session, inst)).collection_metadata[BOOTSTRAPPED_KEY] is False

        for _ in range(6):
            report = await collector(db_session, prov).collect(inst)
            if report.history_scope_state is HistoryState.COMPLETE:
                break
        assert report.history_scope_state is HistoryState.COMPLETE
        assert (await watermark(db_session, inst)).collection_metadata[BOOTSTRAPPED_KEY] is True


# --------------------------------------------------------------------------- the floors


@pytest.mark.usefixtures("pin_cadence")
class TestFloors:
    async def test_no_bucket_of_a_source_is_written_before_its_floor(self, db_session) -> None:
        inst, _sources = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))

        await collector(db_session, prov).collect(inst)
        await collector(db_session, prov).collect(inst)

        history = await buckets(db_session, "HISTORY")
        assert history
        assert min(b.bucket_start for b in history) >= T0
        assert all(b.bucket_start < FIRST for b in history)

    async def test_the_history_ledger_starts_with_the_active_senders(self, db_session) -> None:
        """A sender the console already tracks is not admitted again by the history."""
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))

        await collector(db_session, prov).collect(inst)
        await collector(db_session, prov).collect(inst)
        await collector(db_session, prov).collect(inst)

        live = await buckets(db_session, "LIVE")
        history = await buckets(db_session, "HISTORY")
        # The live run admitted the sender once; the history, which starts with
        # the active senders in its ledger, admits it never again.
        assert sum(b.admitted for b in live) == 1
        assert sum(b.admitted for b in history) == 0
        assert all(b.top_senders == [{"identifier": "fw-01", "events": 50}] for b in history)

    async def test_a_silent_source_makes_the_reading_partial(self, db_session) -> None:
        inst, _sources = await setup(db_session)
        quiet = await make_log_source(
            db_session, inst, qradar_id=2002, name="Quiet", type_id=67
        )
        await decide(db_session, inst)
        prov = provider(always(row("fw-01", 50)))

        for _ in range(6):
            report = await collector(db_session, prov).collect(inst)
            if report.history_scope_state in (
                HistoryState.COMPLETE,
                HistoryState.PARTIAL,
                HistoryState.UNAVAILABLE,
            ):
                break

        assert report.history_scope_state is HistoryState.PARTIAL
        finished = await scope(db_session, inst)
        assert finished is not None and finished.state == "PARTIAL"
        assert finished.reason and "1 source" in finished.reason
        # The quiet source has no floor, so it has no history row at all.
        owners = {b.catchall_log_source_id for b in await buckets(db_session, "HISTORY")}
        assert quiet.id not in owners

    async def test_no_event_at_all_is_unavailable_and_writes_nothing(
        self, db_session
    ) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        # The live window sends, the past does not: the history finds nothing.
        prov = provider(after(FIRST, row("fw-01", 50)))

        for _ in range(4):
            report = await collector(db_session, prov).collect(inst)
            if report.history_scope_state in (
                HistoryState.COMPLETE,
                HistoryState.PARTIAL,
                HistoryState.UNAVAILABLE,
            ):
                break

        assert report.history_scope_state is HistoryState.UNAVAILABLE
        assert await buckets(db_session, "HISTORY") == []
        finished = await scope(db_session, inst)
        assert finished is not None and finished.state == "UNAVAILABLE"
        assert finished.reason and "no catch-all source had an event" in finished.reason


# --------------------------------------------------------------------------- the prepend


@pytest.mark.usefixtures("pin_cadence")
class TestPrepend:
    async def test_a_sender_created_new_live_keeps_new_and_gets_the_earlier_time(
        self, db_session
    ) -> None:
        inst, _ = await setup(db_session)
        # The first live run ends the bootstrap.
        await collector(db_session, provider(always(row("fw-01", 50)))).collect(inst)
        newcomer = provider(always(row("fw-01", 50), row("live-new", 9)))
        later = NOW + timedelta(hours=1)
        await collector(db_session, newcomer, now=later).collect(inst)
        before = (await senders(db_session))["live-new"]
        assert before.review_status == CatchallReviewStatus.NEW.value
        seen_live = before.first_seen_at

        await decide(db_session, inst)
        # The history reads a past in which the sender was already sending.
        past = provider(always(row("fw-01", 50), row("live-new", 9)))
        for _ in range(3):
            await collector(db_session, past, now=later).collect(inst)

        after_ = (await senders(db_session))["live-new"]
        assert after_.review_status == CatchallReviewStatus.NEW.value
        assert after_.reviewed_by is None
        assert after_.first_seen_at < seen_live
        assert after_.first_seen_at >= T0

    async def test_an_inactive_sender_is_not_reactivated_by_history(self, db_session) -> None:
        inst, _ = await setup(db_session)

        def script(start: datetime) -> list[dict]:
            # "fw-01" sends in the past and in the first live window, then stops.
            if start < LAST:
                return [row("fw-01", 50)]
            return [row("other", 9)]

        prov = provider(script)
        await collector(db_session, prov).collect(inst)
        # Eight days later the sender is retired: it was not seen for eight days.
        await collector(db_session, prov, now=NOW + timedelta(days=8)).collect(inst)
        retired = (await senders(db_session))["fw-01"]
        assert retired.active is False

        await decide(db_session, inst)
        later = NOW + timedelta(days=8)
        # The scope is created, then the first step reads the past the sender
        # was sending in.
        await collector(db_session, prov, now=later).collect(inst)
        await collector(db_session, prov, now=later).collect(inst)

        still = (await senders(db_session))["fw-01"]
        assert still.active is False
        assert still.retired_at is not None

    async def test_the_history_does_not_move_a_last_seen_back(self, db_session) -> None:
        inst, _ = await setup(db_session)
        prov = provider(always(row("fw-01", 50)))
        await collector(db_session, prov).collect(inst)
        await decide(db_session, inst)
        later = NOW + timedelta(hours=1)
        for _ in range(3):
            await collector(db_session, prov, now=later).collect(inst)

        found = (await senders(db_session))["fw-01"]
        # The live sighting is the newest one; the history is older and keeps it.
        assert found.last_seen_at > FIRST


# --------------------------------------------------------------------------- the retirement


@pytest.mark.usefixtures("pin_cadence")
class TestRetirement:
    async def test_a_history_only_sender_of_long_ago_is_written_inactive(
        self, db_session
    ) -> None:
        inst, _ = await setup(db_session)
        await decide(db_session, inst)
        # "old-host" stops a day before the anchor: the history's rule (the
        # anchor less the active days) writes it inactive.
        old_until = FIRST - timedelta(days=1)

        def script(start: datetime) -> list[dict]:
            if start < old_until:
                return [row("old-host", 50)]
            if start < FIRST:
                return [row("new-host", 50)]
            return [row("fw-01", 50)]

        prov = provider(script)
        # The scope is created, then the first step reads the past.
        await collector(db_session, prov, catchall_sender_active_days=1).collect(inst)
        await collector(db_session, prov, catchall_sender_active_days=1).collect(inst)

        found = await senders(db_session)
        assert found["old-host"].active is False
        assert found["old-host"].retired_at is not None
        assert found["new-host"].active is True
        assert found["new-host"].retired_at is None


# --------------------------------------------------------------------------- the equivalence


@pytest.mark.usefixtures("pin_cadence")
class TestEquivalence:
    async def test_the_same_senders_fold_the_same_live_and_as_history(
        self, db_session
    ) -> None:
        """The same scripted senders, collected live and folded as history.

        The bucket rows are equal column for column, except the marker and the
        columns of the collection (when it was written and by which search); the
        sender set is equal too.
        """
        from app.catchall.keyed_search import run_catchall_search
        from app.history.catchall import (
            CatchallHistoryFold,
            CatchallSource,
            CatchallStep,
            fold_chunk,
        )
        from app.history.source_floors import SourceFloors
        from app.ips.keyed_search import IpsSearchBudget
        from app.waf.admission import AdmissionPolicy

        inst, sources = await setup(db_session)
        prov = provider(always(row("fw-01", 50), row("fw-02", 20)))
        settings = get_settings().model_copy(
            update={"history_backfill_days": DAYS, "history_backfill_floor_margin_hours": 1}
        )

        await collector(db_session, prov).collect(inst)

        live = (await db_session.scalars(select(CatchallBucket))).all()
        assert live

        # The same window, read again and folded the way history folds it.
        outcomes = await run_catchall_search(
            prov,
            settings.catchall_type_ids,
            (FIRST, LAST + timedelta(seconds=BUCKET)),
            BUCKET,
            max_rows=settings.catchall_max_rows_per_search,
            budget=IpsSearchBudget(10),
        )
        margin = timedelta(hours=1)
        step = CatchallStep(
            target_start=FIRST - timedelta(days=DAYS),
            margin=margin,
            read_start=FIRST - timedelta(days=DAYS) - margin,
            bucket_seconds=BUCKET,
            top_n=settings.catchall_top_senders,
        )
        folded = fold_chunk(
            outcomes,
            sources=[
                CatchallSource(id=source.id, qradar_id=source.qradar_id) for source in sources
            ],
            fold=CatchallHistoryFold(
                floors=SourceFloors({str(sources[0].qradar_id): FIRST - margin})
            ),
            policy=AdmissionPolicy(
                max_new_per_bucket=settings.catchall_max_new_senders_per_bucket,
                max_active=settings.catchall_max_active_senders_per_source,
                min_requests=settings.catchall_min_admission_events,
            ),
            step=step,
            now=NOW,
        )

        history = {row["bucket_start"]: row for row in folded.rows}
        assert len(history) == len(live)
        for stored in live:
            computed = history[stored.bucket_start]
            for column in (
                "bucket_seconds",
                "events",
                "records",
                "distinct_senders",
                "distinct_senders_lower_bound",
                "measurement",
                "top_senders",
                "admitted",
                "below_admission_threshold",
                "rejected_over_quota",
                "untracked_senders",
                "untracked_events",
            ):
                assert computed[column] == getattr(stored, column), column
            assert computed["origin"] == "HISTORY" and stored.origin == "LIVE"

        # The sender set is the same.
        live_senders = set(await senders(db_session))
        assert live_senders == {"fw-01", "fw-02"}
        touched = {
            identifier
            for bucket in folded.touches.values()
            for _source, identifier in bucket
        }
        assert touched == live_senders


# --------------------------------------------------------------------------- the statements


@pytest.mark.usefixtures("pin_cadence")
class TestStatements:
    async def _history_statements(
        self, session, *, sources: int, budget: int
    ) -> tuple[int, int, int, int]:
        """The statements of one history step, and the rows it wrote."""
        inst, _ = await setup(session, sources=sources)
        prov = provider(always(*(row("fw-01", 50, source=SRC + i) for i in range(sources))))
        # The first tick collects the live buckets, the second creates the scope
        # and the third reads: every INSERT of the third is the history's.
        await collector(session, prov).collect(inst)
        await decide(session, inst)
        await collector(session, prov).collect(inst)
        with counting(session) as statements:
            report = await collector(
                session,
                prov,
                search_cadence_max_searches_per_tick=50,
                history_backfill_searches_per_tick=budget,
                # One search holds the whole span, so the statement count is
                # about the rows written and not about the truncation splits.
                catchall_max_rows_per_search=100_000,
            ).collect(inst)
        bucket_inserts = sum("INSERT INTO catchall_bucket" in s for s in statements)
        sender_inserts = sum("INSERT INTO catchall_sender" in s for s in statements)
        history = await buckets(session, "HISTORY")
        return (
            report.history_buckets_written,
            len(history),
            bucket_inserts,
            sender_inserts,
        )

    async def test_the_statements_follow_the_rows_and_not_the_sources(
        self, db_session
    ) -> None:
        """One source and fifty, a short span and a long one: the rows decide."""
        for sources in (1, 50):
            for budget in (1, 10):
                written, _stored, bucket_inserts, sender_inserts = (
                    await self._history_statements(
                        db_session, sources=sources, budget=budget
                    )
                )
                assert written > 0
                # The writers chunk by row count: 1 000 bucket rows, 2 000 sender rows.
                assert bucket_inserts == math.ceil(written / 1000)
                assert sender_inserts == 1

    async def test_fifty_sources_write_fifty_times_the_rows_and_the_same_rule_holds(
        self, db_session
    ) -> None:
        one_written, _one_stored, one_buckets, one_senders = await self._history_statements(
            db_session, sources=1, budget=1
        )
        many_written, _many_stored, many_buckets, many_senders = await self._history_statements(
            db_session, sources=50, budget=1
        )
        # Fifty times the rows, so the rows decide the statements: the writers
        # chunk by row count, never by source.
        assert many_written > one_written * 10
        assert one_buckets == math.ceil(one_written / 1000)
        assert many_buckets == math.ceil(many_written / 1000)
        assert many_senders == one_senders

    async def test_the_same_number_of_rows_costs_the_same_statements(
        self, db_session
    ) -> None:
        """Fifty senders in fifty sources and fifty senders in one: the same rows."""

        async def sender_statements(sources: int, per_source: int) -> tuple[int, int]:
            inst, _ = await setup(db_session, sources=sources)
            prov = provider(always(row("fw-01", 50)))
            await collector(db_session, prov).collect(inst)
            await decide(db_session, inst)
            if sources == 1:
                script = always(*(row(f"host-{i:02d}", 50) for i in range(per_source)))
            else:
                script = always(*(row("host-00", 50, source=SRC + i) for i in range(sources)))
            prov = provider(script)
            await collector(db_session, prov).collect(inst)
            with counting(db_session) as statements:
                await collector(db_session, prov).collect(inst)
            rows = await db_session.scalars(
                select(CatchallSender).where(CatchallSender.instance_id == inst.id)
            )
            return (
                sum("INSERT INTO catchall_sender" in s for s in statements),
                len(rows.all()),
            )

        many_statements, many_rows = await sender_statements(50, 1)
        one_statements, one_rows = await sender_statements(1, 50)
        assert many_rows == one_rows
        assert many_statements == one_statements == 1
