"""SCHED-1: scheduled-search cycles never overlap and never hold a transaction across a search.

The properties come from running the task itself (`run_due_searches`) against PostgreSQL
with a stub executor that blocks on events, so every ordering below is forced, not timed.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import ClassVar

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts.fingerprint import search_threshold_fingerprint
from app.core.config import get_settings
from app.models.alert import Alert
from app.models.enums import ExecutionStatus
from app.models.search import ScheduledSearch, SearchExecution
from app.providers.mock import MockQRadarProvider
from app.services.ariel_permit import ArielPermitBudget
from app.services.locks import collector_lock_key
from app.services.search_executor import SearchExecutor
from app.workers import tasks
from tests.integration.factories import make_search

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_LOCK_KEY = collector_lock_key(tasks.SEARCH_SCHEDULER_LOCK_ID, "search_scheduler")
_ROW_LOCKS = """
    SELECT count(*) FROM pg_locks l JOIN pg_class c ON c.oid = l.relation
    WHERE c.relname = 'scheduled_search' AND l.granted
      AND l.mode IN ('RowShareLock', 'RowExclusiveLock') AND l.pid <> pg_backend_pid()
"""
_IDLE_IN_TXN = """
    SELECT count(*) FROM pg_stat_activity
    WHERE datname = current_database() AND state LIKE 'idle in transaction%'
      AND pid <> pg_backend_pid()
"""


class _Closable:
    async def aclose(self) -> None:
        return None


class StubExecutor:
    """Stands for SearchExecutor; `hook(search, session)` runs where the Ariel search would."""

    instances: ClassVar[list[StubExecutor]] = []

    def __init__(self, session, provider, permit, *, settings=None, commit_progress=False):
        self.session = session
        self.commit_progress = commit_progress
        self.calls: list[str] = []
        type(self).instances.append(self)

    hook = None  # set per test: async (executor, search) -> None

    async def execute(self, search, *, run_key, instance_key, trigger="SCHEDULED",
                      triggered_by=None):
        self.calls.append(search.name)
        if type(self).hook is not None:
            await type(self).hook(self, search)
        execution = await self.session.scalar(
            select(SearchExecution).where(
                SearchExecution.search_id == search.id, SearchExecution.run_key == run_key
            )
        )
        assert execution is not None, "the claim must have created the PENDING row"
        execution.status = ExecutionStatus.COMPLETED
        execution.completed_at = datetime.now(UTC)
        execution.result_count = 5
        execution.threshold_breached = search.threshold_value is not None
        await self.session.flush()
        return execution


@pytest.fixture
async def env(db_schema, monkeypatch):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    StubExecutor.instances = []
    StubExecutor.hook = None
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "build_provider", lambda: _Closable())
    monkeypatch.setattr(tasks, "SearchExecutor", StubExecutor)
    try:
        yield maker
    finally:
        StubExecutor.hook = None
        await engine.dispose()


async def _seed(maker, n: int, **kwargs) -> None:
    now = datetime.now(UTC)
    async with maker() as session:
        for i in range(n):
            await make_search(
                session, name=f"s{i}", next_run_at=now - timedelta(minutes=10 - i), **kwargs
            )
        await session.commit()


async def _scalar(maker, sql: str, **params):
    async with maker() as session:
        return await session.scalar(text(sql), params)


async def _lock_held(maker) -> int:
    return await _scalar(
        maker,
        "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND objsubid = 2"
        " AND classid = :ns AND objid = :key",
        ns=get_settings().collection_advisory_lock_namespace,
        key=_LOCK_KEY,
    )


async def test_a_second_overlapping_run_of_the_task_skips_and_sends_nothing(env) -> None:
    await _seed(env, 1)
    entered, release = asyncio.Event(), asyncio.Event()

    async def hook(executor, search):
        entered.set()
        await release.wait()

    StubExecutor.hook = hook
    first = asyncio.create_task(tasks.run_due_searches())
    await asyncio.wait_for(entered.wait(), 10)
    assert await _lock_held(env) == 1

    second = await asyncio.wait_for(tasks.run_due_searches(), 10)

    assert second == {"status": "skipped_locked"}
    assert [e.calls for e in StubExecutor.instances] == [["s0"]], "the second sent nothing"
    assert await _lock_held(env) == 1, "the skipper did not free the first cycle's lock"
    release.set()
    result = await asyncio.wait_for(first, 10)
    assert result["dispatched"] == 1
    async with env() as session:
        execution = await session.scalar(select(SearchExecution))
        assert execution.status == ExecutionStatus.COMPLETED
    assert await _lock_held(env) == 0


async def test_a_crash_on_the_third_search_keeps_the_first_two_committed(env) -> None:
    await _seed(env, 3, threshold_value=1.0)
    before = datetime.now(UTC)

    async def hook(executor, search):
        if search.name == "s2":
            raise RuntimeError("worker died")

    StubExecutor.hook = hook
    with pytest.raises(RuntimeError, match="worker died"):
        await tasks.run_due_searches()

    async with env() as session:
        rows = {
            s.name: s for s in (await session.scalars(select(ScheduledSearch))).all()
        }
        executions = {
            s.name: (
                await session.scalars(
                    select(SearchExecution).where(SearchExecution.search_id == s.id)
                )
            ).all()
            for s in rows.values()
        }
        for name in ("s0", "s1"):
            assert [e.status for e in executions[name]] == [ExecutionStatus.COMPLETED]
            assert rows[name].last_run_at is not None
            assert rows[name].consecutive_failures == 0
            alert = await session.scalar(
                select(Alert).where(
                    Alert.fingerprint == search_threshold_fingerprint(rows[name].id)
                )
            )
            assert alert is not None, f"the alert of {name} was committed"
        # The third: the claim committed (next_run_at moved, PENDING row), and the stub
        # raised before touching the row, so it is left PENDING, as the real executor
        # leaves a row when an exception escapes it. last_run_at stays unset.
        assert [e.status for e in executions["s2"]] == [ExecutionStatus.PENDING]
        assert rows["s2"].last_run_at is None
        for s in rows.values():
            assert s.next_run_at > before, f"{s.name} next_run_at moved"
    assert await _lock_held(env) == 0


async def test_no_transaction_and_no_row_lock_while_the_search_runs(env) -> None:
    await _seed(env, 2)
    seen: list[tuple[bool, int, int, int]] = []

    async def hook(executor, search):
        seen.append((
            executor.session.in_transaction(),
            await _scalar(env, _ROW_LOCKS),
            await _scalar(env, _IDLE_IN_TXN),
            # The claim is visible to the API while the search runs.
            await _scalar(
                env, "SELECT count(*) FROM search_execution WHERE status = 'PENDING'"
            ),
        ))

    StubExecutor.hook = hook
    await tasks.run_due_searches()

    assert len(seen) == 2
    for in_txn, row_locks, idle, pending in seen:
        assert in_txn is False
        assert row_locks == 0
        assert idle == 0
        assert pending >= 1


async def _bulk_round(maker) -> int:
    async with maker() as session:
        await session.execute(text("DELETE FROM search_execution"))
        await session.execute(text("DELETE FROM scheduled_search"))
        await session.commit()
    await _seed(maker, 3)
    entered, release = asyncio.Event(), asyncio.Event()
    first = True

    async def hook(executor, search):
        nonlocal first
        if first:
            first = False
            entered.set()
            await release.wait()

    async def bulk():
        async with maker() as session:
            await session.execute(
                text("UPDATE scheduled_search SET next_run_at = now() - interval '1 minute'"
                     " WHERE enabled")
            )
            await session.commit()

    StubExecutor.hook = hook
    cycle = asyncio.create_task(tasks.run_due_searches())
    await asyncio.wait_for(entered.wait(), 10)
    await asyncio.wait_for(bulk(), 10)  # a row lock held by the cycle would block here
    release.set()
    return (await asyncio.wait_for(cycle, 10))["considered"]


async def test_bulk_update_during_a_search_never_deadlocks(env) -> None:
    """AP-8 D-1: a bulk UPDATE of scheduled_search issued mid-cycle, ten rounds."""
    assert [await _bulk_round(env) for _ in range(10)] == [3] * 10


async def test_the_lock_is_released_on_every_exit_path(env) -> None:
    await _seed(env, 1)

    # success
    await tasks.run_due_searches()
    assert await _lock_held(env) == 0

    # exception
    async def boom(executor, search):
        raise RuntimeError("boom")

    async with env() as session:
        await session.execute(
            text("UPDATE scheduled_search SET next_run_at = now() - interval '1 minute'")
        )
        await session.commit()
    StubExecutor.hook = boom
    with pytest.raises(RuntimeError, match="boom"):
        await tasks.run_due_searches()
    assert await _lock_held(env) == 0

    # skipped_locked: another connection holds the key; the skipper leaves it alone
    holder_engine = create_async_engine(env.kw["bind"].url, future=True)
    try:
        async with holder_engine.connect() as holder:
            await holder.execute(
                text("SELECT pg_advisory_lock(:ns, :key)").bindparams(
                    ns=get_settings().collection_advisory_lock_namespace, key=_LOCK_KEY
                )
            )
            assert await tasks.run_due_searches() == {"status": "skipped_locked"}
            assert await _lock_held(env) == 1
            await holder.execute(text("SELECT pg_advisory_unlock_all()"))
            await holder.commit()
    finally:
        await holder_engine.dispose()
    assert await _lock_held(env) == 0


async def test_one_failed_result_commit_does_not_undo_earlier_searches(
    env, caplog, monkeypatch
) -> None:
    await _seed(env, 2)
    from app.alerts.search_alerts import SearchAlertEvaluator

    original = SearchAlertEvaluator.evaluate

    async def evaluate(self, search, execution):
        if search.name == "s1":
            raise RuntimeError("evaluator failed")
        return await original(self, search, execution)

    monkeypatch.setattr(SearchAlertEvaluator, "evaluate", evaluate)
    result = await tasks.run_due_searches()

    assert result["dispatched"] == 2
    assert any("could not record the result" in r.message for r in caplog.records)
    async with env() as session:
        done = {
            s.name: s.last_run_at
            for s in (await session.scalars(select(ScheduledSearch))).all()
        }
        assert done["s0"] is not None, "the first search's result stayed committed"
        assert done["s1"] is None
        assert await session.scalar(select(func.count()).select_from(SearchExecution)) == 2


# ----------------------------------------------------------- executor commit points
async def test_commit_progress_makes_the_run_visible_and_holds_no_transaction(db_schema) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    seen: dict[str, object] = {}

    class Probe(MockQRadarProvider):
        async def get_ariel_search_status(self, search_id):
            if "polled" not in seen:
                seen["polled"] = True
                seen["in_txn"] = session.in_transaction()
                seen["visible"] = await _scalar(
                    maker,
                    "SELECT count(*) FROM search_execution"
                    " WHERE status = 'RUNNING' AND ariel_search_id IS NOT NULL",
                )
            return await super().get_ariel_search_status(search_id)

    async def _sleep(_s: float) -> None:
        return None

    try:
        async with maker() as session:
            search = await make_search(session, name="vis")
            await session.commit()
            executor = SearchExecutor(
                session, Probe(seed=1), ArielPermitBudget.for_session(
                    session, get_settings(), instance_id=None),
                sleep=_sleep, commit_progress=True,
            )
            await executor.execute(search, run_key="k1", instance_key="default")
        assert seen == {"polled": True, "in_txn": False, "visible": 1}
    finally:
        await engine.dispose()


async def test_without_commit_progress_the_callers_transaction_stays_open(db_schema) -> None:
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    seen: dict[str, object] = {}

    class Probe(MockQRadarProvider):
        async def get_ariel_search_status(self, search_id):
            seen.setdefault("in_txn", session.in_transaction())
            return await super().get_ariel_search_status(search_id)

    async def _sleep(_s: float) -> None:
        return None

    try:
        async with maker() as session:
            search = await make_search(session, name="manual")
            executor = SearchExecutor(
                session, Probe(seed=1), ArielPermitBudget.for_session(
                    session, get_settings(), instance_id=None),
                sleep=_sleep,
            )
            await executor.execute(search, run_key="k1", instance_key="default")
        assert seen == {"in_txn": True}
    finally:
        await engine.dispose()


async def test_each_result_is_committed_by_its_own_record_step(env) -> None:
    """The record step commits before the cycle's closing commit does: if that last
    commit fails, the search that was sent is still recorded."""
    from app.services.search_scheduler import SearchScheduler

    await _seed(env, 1)
    StubExecutor.hook = None
    async with env() as session:
        executor = StubExecutor(session, None, None)
        real_commit = session.commit
        calls = 0

        async def commit() -> None:
            nonlocal calls
            calls += 1
            # listing, claim, record, then the cycle's closing commit (4th) fails
            if calls == 4:
                raise RuntimeError("closing commit failed")
            await real_commit()

        session.commit = commit  # type: ignore[method-assign]
        with pytest.raises(RuntimeError, match="closing commit failed"):
            await SearchScheduler(session, executor).run_due()  # type: ignore[arg-type]
    assert calls == 4
    async with env() as session:
        assert (await session.scalar(select(ScheduledSearch))).last_run_at is not None
