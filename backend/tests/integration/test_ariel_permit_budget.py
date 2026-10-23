"""PostgreSQL contract tests for the platform-wide Ariel permit budget.

Each budget or raw holder uses its own engine, so every permit lives on its own
connection -- the same isolation separate workers and API processes have.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)

import app.services.ariel_permit as permit_module
from app.core.config import Settings
from app.services.ariel_permit import ArielPermitBudget, ariel_background_priority
from app.services.waf_ariel_budget import WafArielBudget

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def permit_settings(**overrides: object) -> Settings:
    values = Settings(_env_file=None).model_dump()
    values.update(
        ariel_max_concurrent_searches=overrides.pop("instance_limit", 2),
        ariel_global_max_concurrent_searches=overrides.pop("global_limit", 4),
        ariel_permit_reserved_live_slots=overrides.pop("reserved", 1),
        ariel_permit_wait_seconds=overrides.pop("wait", 0.15),
        ariel_permit_poll_seconds=overrides.pop("poll", 0.025),
    )
    values.update(overrides)
    return Settings(_env_file=None, **values)


def assigned_database_url() -> str:
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value


async def take_lock(conn: AsyncConnection, namespace: int, slot: int) -> None:
    got = await conn.scalar(
        text("SELECT pg_try_advisory_lock(:ns, :slot)").bindparams(ns=namespace, slot=slot)
    )
    assert got


async def lock_count(
    engine: AsyncEngine,
    namespace: int,
    *,
    slot: int | None = None,
) -> int:
    params = {"ns": namespace}
    if slot is None:
        statement = text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND granted AND objsubid = 2 AND classid = :ns "
            "AND database = (SELECT oid FROM pg_database "
            "WHERE datname = current_database())"
        )
    else:
        params["slot"] = slot
        statement = text(
            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
            "AND granted AND objsubid = 2 AND classid = :ns AND objid = :slot "
            "AND database = (SELECT oid FROM pg_database "
            "WHERE datname = current_database())"
        )
    async with engine.connect() as conn:
        count = await conn.scalar(statement.bindparams(**params))
    return int(count or 0)


@asynccontextmanager
async def raw_holder(
    url: str, locks: Iterable[tuple[int, int]]
) -> AsyncIterator[AsyncConnection]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            for namespace, slot in locks:
                await take_lock(conn, namespace, slot)
            yield conn
    finally:
        await engine.dispose()


async def dispose_all(engines: Iterable[AsyncEngine]) -> None:
    for engine in engines:
        await engine.dispose()


class TestPermitPools:
    async def test_global_cap_excludes_a_third_connection(self, db_schema: str) -> None:
        settings = permit_settings(global_limit=2, reserved=0, wait=0)
        engines = [create_async_engine(db_schema) for _ in range(3)]
        budgets = [ArielPermitBudget(e, settings, instance_id=None) for e in engines]
        try:
            async with contextlib.AsyncExitStack() as stack:
                first = await stack.enter_async_context(budgets[0].permit())
                second = await stack.enter_async_context(budgets[1].permit())
                assert first is not None and second is not None
                assert {first.global_slot, second.global_slot} == {0, 1}
                async with budgets[2].permit() as third:
                    assert third is None
                async with async_sessionmaker(engines[2])() as session:
                    counts = await budgets[2].active_permits(session)
                    assert counts is not None
                    assert (counts.global_held, counts.global_cap) == (2, 2)
                    assert counts.instance_held == 0
            async with budgets[2].permit() as released:
                assert released is not None
        finally:
            await dispose_all(engines)

    async def test_console_cap_binds_while_global_slots_remain(
        self, db_schema: str
    ) -> None:
        settings = permit_settings(instance_limit=1, global_limit=3, reserved=0, wait=0)
        instance_id = uuid.UUID(int=101)
        engines = [create_async_engine(db_schema) for _ in range(3)]
        same = [ArielPermitBudget(e, settings, instance_id=instance_id) for e in engines[:2]]
        other = ArielPermitBudget(engines[2], settings, instance_id=uuid.UUID(int=102))
        try:
            async with same[0].permit() as first:
                assert first is not None
                async with same[1].permit() as blocked:
                    assert blocked is None
                async with other.permit() as admitted:
                    assert admitted is not None
                async with async_sessionmaker(engines[1])() as session:
                    counts = await same[1].active_permits(session)
                    assert counts is not None
                    assert counts.global_held == 1 < counts.global_cap
                    assert counts.instance_held == counts.instance_cap == 1
        finally:
            await dispose_all(engines)

    async def test_two_consoles_get_disjoint_contiguous_ranges(self, db_schema: str) -> None:
        settings = permit_settings(instance_limit=2, global_limit=4, reserved=0, wait=0)
        ids = (uuid.UUID(int=201), uuid.UUID(int=202))
        engines = [create_async_engine(db_schema) for _ in range(4)]
        budgets = [
            ArielPermitBudget(engine, settings, instance_id=ids[index // 2])
            for index, engine in enumerate(engines)
        ]
        try:
            async with contextlib.AsyncExitStack() as stack:
                permits = [await stack.enter_async_context(budget.permit()) for budget in budgets]
                assert all(permit is not None for permit in permits)
                first_slots = {permit.instance_slot for permit in permits[:2] if permit is not None}
                second_slots = {
                    permit.instance_slot for permit in permits[2:] if permit is not None
                }
                assert len(first_slots) == len(second_slots) == 2
                assert first_slots.isdisjoint(second_slots)
                assert max(first_slots) - min(first_slots) == 1
                assert max(second_slots) - min(second_slots) == 1
        finally:
            await dispose_all(engines)

    async def test_forced_range_collision_shares_one_console_pool(
        self, db_schema: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(permit_module, "_instance_slot_base", lambda _id, _limit: 700)
        settings = permit_settings(instance_limit=2, global_limit=5, reserved=0, wait=0)
        engines = [create_async_engine(db_schema) for _ in range(3)]
        budgets = [
            ArielPermitBudget(engine, settings, instance_id=uuid.UUID(int=index + 301))
            for index, engine in enumerate(engines)
        ]
        try:
            async with contextlib.AsyncExitStack() as stack:
                first = await stack.enter_async_context(budgets[0].permit())
                second = await stack.enter_async_context(budgets[1].permit())
                assert first is not None and second is not None
                assert {first.instance_slot, second.instance_slot} == {700, 701}
                async with budgets[2].permit() as third:
                    assert third is None
                async with async_sessionmaker(engines[2])() as session:
                    counts = await budgets[2].active_permits(session)
                    assert counts is not None
                    assert counts.instance_held == counts.instance_cap == 2
                    assert counts.global_held == 2 < counts.global_cap
        finally:
            await dispose_all(engines)

    async def test_background_excludes_reserved_slots_and_live_uses_every_slot(
        self, db_schema: str
    ) -> None:
        settings = permit_settings(global_limit=4, reserved=2, wait=0)
        engines = [create_async_engine(db_schema) for _ in range(5)]
        budgets = [ArielPermitBudget(engine, settings, instance_id=None) for engine in engines]
        try:
            async with contextlib.AsyncExitStack() as stack:
                with ariel_background_priority():
                    first = await stack.enter_async_context(budgets[0].permit())
                    second = await stack.enter_async_context(budgets[1].permit())
                    blocked = await stack.enter_async_context(budgets[2].permit())
                assert first is not None and second is not None
                assert {first.global_slot, second.global_slot} == {2, 3}
                assert blocked is None

            async with contextlib.AsyncExitStack() as stack:
                live = [
                    await stack.enter_async_context(budget.permit()) for budget in budgets[:4]
                ]
                assert all(permit is not None for permit in live)
                assert {permit.global_slot for permit in live if permit is not None} == {
                    0,
                    1,
                    2,
                    3,
                }
        finally:
            await dispose_all(engines)

    async def test_timeout_releases_instance_half_acquired_before_global(
        self, db_schema: str
    ) -> None:
        settings = permit_settings(instance_limit=1, global_limit=1, reserved=0, wait=0.12)
        instance_id = uuid.UUID(int=401)
        engine = create_async_engine(db_schema)
        budget = ArielPermitBudget(engine, settings, instance_id=instance_id)
        assert budget._instance_base is not None
        try:
            async with raw_holder(
                db_schema,
                [(settings.ariel_permit_lock_namespace, 0)],
            ):
                result: list[object | None] = []

                async def wait_for_permit() -> None:
                    async with budget.permit() as permit:
                        result.append(permit)

                task = asyncio.create_task(wait_for_permit())
                async with engine.connect() as observer:
                    for _ in range(40):
                        held = await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                                "AND granted AND objsubid = 2 AND classid = :ns "
                                "AND objid = :slot AND database = (SELECT oid FROM pg_database "
                                "WHERE datname = current_database())"
                            ).bindparams(
                                ns=settings.ariel_permit_instance_lock_namespace,
                                slot=budget._instance_base,
                            )
                        )
                        if int(held or 0) == 1:
                            break
                        await asyncio.sleep(0.005)
                    assert int(held or 0) == 1
                await task
                assert result == [None]
                async with async_sessionmaker(engine)() as session:
                    counts = await budget.active_permits(session)
                    assert counts is not None
                    assert counts.instance_held == 0
        finally:
            await engine.dispose()

    async def test_cancelling_waiter_clears_console_lock_before_pool_reuse(
        self, db_schema: str
    ) -> None:
        settings = permit_settings(
            instance_limit=1,
            global_limit=1,
            reserved=0,
            wait=30,
            poll=10,
        )
        instance_id = uuid.UUID(int=451)
        sleeping = asyncio.Event()

        async def marked_sleep(delay: float) -> None:
            sleeping.set()
            await asyncio.sleep(delay)

        # A single pooled connection makes its reuse after cancellation certain.
        engine = create_async_engine(db_schema, pool_size=1, max_overflow=0)
        observer = create_async_engine(db_schema)
        budget = ArielPermitBudget(
            engine,
            settings,
            instance_id=instance_id,
            sleep=marked_sleep,
        )
        assert budget._instance_base is not None
        try:
            async with raw_holder(
                db_schema,
                [(settings.ariel_permit_lock_namespace, 0)],
            ):
                async def wait_for_permit() -> None:
                    async with budget.permit():
                        pytest.fail("global capacity was already full")

                task = asyncio.create_task(wait_for_permit())
                await asyncio.wait_for(sleeping.wait(), timeout=2)
                assert (
                    await lock_count(
                        observer,
                        settings.ariel_permit_instance_lock_namespace,
                        slot=budget._instance_base,
                    )
                    == 1
                )
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
                assert (
                    await lock_count(
                        observer,
                        settings.ariel_permit_instance_lock_namespace,
                        slot=budget._instance_base,
                    )
                    == 0
                )

            # The same one-connection pool must not carry the cancelled
            # waiter's console lock into the next permit.
            async with budget.permit() as permit:
                assert permit is not None
                assert permit.instance_slot == budget._instance_base
        finally:
            await observer.dispose()
            await engine.dispose()

    async def test_body_exception_clears_both_locks_before_pool_reuse(
        self, db_schema: str
    ) -> None:
        settings = permit_settings(instance_limit=1, global_limit=1, reserved=0, wait=0)
        engine = create_async_engine(db_schema, pool_size=1, max_overflow=0)
        observer = create_async_engine(db_schema)
        budget = ArielPermitBudget(engine, settings, instance_id=uuid.UUID(int=452))
        try:
            with pytest.raises(RuntimeError, match="body failed"):
                async with budget.permit() as permit:
                    assert permit is not None
                    raise RuntimeError("body failed")

            assert await lock_count(observer, settings.ariel_permit_lock_namespace) == 0
            assert (
                await lock_count(observer, settings.ariel_permit_instance_lock_namespace) == 0
            )
            async with budget.permit() as reused:
                assert reused is not None
        finally:
            await observer.dispose()
            await engine.dispose()

    async def test_failed_unlock_invalidates_connection_and_frees_locks(
        self,
        db_schema: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = permit_settings(instance_limit=1, global_limit=1, reserved=0, wait=0)
        engine = create_async_engine(db_schema, pool_size=1, max_overflow=0)
        observer = create_async_engine(db_schema)
        budget = ArielPermitBudget(engine, settings, instance_id=uuid.UUID(int=453))
        invalidations: list[BaseException | None] = []

        def record_invalidation(_connection, _record, error):  # type: ignore[no-untyped-def]
            invalidations.append(error)

        event.listen(engine.sync_engine.pool, "invalidate", record_invalidation)
        original_execute = AsyncConnection.execute

        async def fail_unlock(self, statement, *args, **kwargs):  # type: ignore[no-untyped-def]
            if str(statement) == "SELECT pg_advisory_unlock_all()":
                raise RuntimeError("scripted unlock failure")
            return await original_execute(self, statement, *args, **kwargs)

        monkeypatch.setattr(AsyncConnection, "execute", fail_unlock)
        try:
            with pytest.raises(RuntimeError, match="scripted unlock failure"):
                async with budget.permit() as permit:
                    assert permit is not None
            assert len(invalidations) == 1
            assert await lock_count(observer, settings.ariel_permit_lock_namespace) == 0
            assert (
                await lock_count(observer, settings.ariel_permit_instance_lock_namespace) == 0
            )
        finally:
            await observer.dispose()
            await engine.dispose()

    async def test_invalidating_holder_connection_frees_both_slots(
        self, db_schema: str
    ) -> None:
        settings = permit_settings(instance_limit=1, global_limit=1, reserved=0, wait=0)
        instance_id = uuid.UUID(int=501)
        holder_engine = create_async_engine(db_schema)
        contender_engine = create_async_engine(db_schema)
        budget = ArielPermitBudget(contender_engine, settings, instance_id=instance_id)
        assert budget._instance_base is not None
        conn = await holder_engine.connect()
        try:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            await take_lock(
                conn,
                settings.ariel_permit_instance_lock_namespace,
                budget._instance_base,
            )
            await take_lock(conn, settings.ariel_permit_lock_namespace, 0)
            async with budget.permit() as blocked:
                assert blocked is None

            # Model a worker crash: invalidate closes the physical connection
            # without issuing either advisory unlock.
            await conn.invalidate()
            await conn.close()
            async with budget.permit() as freed:
                assert freed is not None
                assert freed.instance_slot == budget._instance_base
                assert freed.global_slot == 0
        finally:
            if not conn.closed:
                await conn.close()
            await holder_engine.dispose()
            await contender_engine.dispose()

    async def test_active_counts_exclude_another_database(self, db_schema: str) -> None:
        settings = permit_settings(
            instance_limit=1,
            global_limit=2,
            reserved=0,
            ariel_permit_lock_namespace=14713,
            ariel_permit_instance_lock_namespace=14714,
        )
        instance_id = uuid.UUID(int=601)
        local_engine = create_async_engine(db_schema)
        observer_engine = create_async_engine(db_schema)
        budget = ArielPermitBudget(local_engine, settings, instance_id=instance_id)
        all_instances = ArielPermitBudget(observer_engine, settings, instance_id=None)
        assert budget._instance_base is not None
        assigned = make_url(assigned_database_url())
        elsewhere = create_async_engine(assigned.set(database="postgres"))
        try:
            async with (
                budget.permit() as local,
                elsewhere.connect() as other,
            ):
                assert local is not None
                await other.execution_options(isolation_level="AUTOCOMMIT")
                await take_lock(other, settings.ariel_permit_lock_namespace, 0)
                await take_lock(
                    other,
                    settings.ariel_permit_instance_lock_namespace,
                    budget._instance_base,
                )
                async with async_sessionmaker(observer_engine)() as session:
                    counts = await budget.active_permits(session)
                    all_counts = await all_instances.active_permits(session)
                    assert counts is not None and all_counts is not None
                    assert (counts.global_held, counts.instance_held) == (1, 1)
                    assert (all_counts.global_held, all_counts.instance_held) == (1, 1)
        finally:
            await elsewhere.dispose()
            await local_engine.dispose()
            await observer_engine.dispose()

    async def test_all_four_lock_namespaces_are_independent(self, db_schema: str) -> None:
        settings = permit_settings(instance_limit=1, global_limit=1, reserved=0, wait=0)
        settings = settings.model_copy(
            update={
                "waf_ariel_max_concurrent_sources": 1,
                "waf_ariel_permit_wait_seconds": 0.0,
            }
        )
        waf_engine = create_async_engine(db_schema)
        ariel_engine = create_async_engine(db_schema)
        ariel = ArielPermitBudget(ariel_engine, settings, instance_id=uuid.UUID(int=701))
        waf = WafArielBudget(waf_engine, settings)
        try:
            async with (
                raw_holder(db_schema, [(settings.collection_advisory_lock_namespace, 0)]),
                waf.permit() as waf_permit,
                ariel.permit() as ariel_permit,
            ):
                assert waf_permit is not None
                assert ariel_permit is not None
                assert len(
                    {
                        settings.collection_advisory_lock_namespace,
                        settings.waf_ariel_permit_lock_namespace,
                        settings.ariel_permit_lock_namespace,
                        settings.ariel_permit_instance_lock_namespace,
                    }
                ) == 4
        finally:
            await waf_engine.dispose()
            await ariel_engine.dispose()

