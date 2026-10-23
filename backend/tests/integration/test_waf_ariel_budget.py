"""Phase B.1.5: the global WAF Ariel budget, across connections and workers.

Each budget or holder below uses its own engine, so every permit lives on its
own PostgreSQL connection -- exactly the isolation separate Celery worker
processes have.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine

from app.models.monitoring import CollectionWatermark
from app.models.waf import WafSourceBucket
from app.services.locks import collector_lock_key
from app.services.waf_ariel_budget import WafArielBudget, WafArielPermit
from app.services.waf_pipeline import run_waf_source, scheduling_record
from app.services.waf_status import waf_ariel_budget_status, waf_source_statuses
from tests.integration.factories import make_instance
from tests.integration.test_waf_productionization import (
    STEP,
    T0,
    StubWafProvider,
    batch,
    cfg,
    count,
    run_at,
    source_with_profile,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def budget_settings(**overrides):  # type: ignore[no-untyped-def]
    return cfg(
        waf_ariel_max_concurrent_sources=overrides.pop("limit", 1),
        waf_ariel_permit_wait_seconds=overrides.pop("wait", 0.2),
        waf_ariel_permit_poll_seconds=0.05,
        **overrides,
    )


def assigned_database_url() -> str:
    """The database this suite was given, to derive its server's neighbour from."""
    value = os.environ.get("TEST_DATABASE_URL")
    if not value:
        pytest.skip("TEST_DATABASE_URL not set")
    return value


@asynccontextmanager
async def holder(url: str, namespace: int, slot: int) -> AsyncIterator[AsyncConnection]:
    """A foreign process holding one permit slot."""
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            got = await conn.scalar(
                text("SELECT pg_try_advisory_lock(:ns, :s)").bindparams(ns=namespace, s=slot)
            )
            assert got
            yield conn
    finally:
        await engine.dispose()


class TestPermits:
    async def test_cancellation_during_acquisition_releases_before_pool_reuse(
        self, db_schema
    ) -> None:
        settings = budget_settings(limit=1, wait=10.0)
        acquired = asyncio.Event()
        never = asyncio.Event()
        engine = create_async_engine(db_schema, pool_size=1, max_overflow=0)

        class CancelAfterGrant(WafArielBudget):
            async def _acquire(self, conn, started):  # type: ignore[no-untyped-def, override]
                got = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:ns, 0)").bindparams(
                        ns=self.namespace
                    )
                )
                assert got
                acquired.set()
                await never.wait()
                return 0

        async def wait_for_permit() -> None:
            async with CancelAfterGrant(engine, settings).permit():
                raise AssertionError("the cancelled acquisition must not enter the body")

        task = asyncio.create_task(wait_for_permit())
        try:
            await acquired.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

            async with engine.connect() as conn:
                leaked = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                        "AND classid = :ns AND objid = 0 AND granted"
                    ).bindparams(ns=settings.waf_ariel_permit_lock_namespace)
                )
                assert leaked == 0

            # The same one-connection pool must not retain the session lock.
            async with WafArielBudget(engine, settings).permit() as reused:
                assert reused is not None and reused.slot == 0
        finally:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            await engine.dispose()

    async def test_permits_are_bounded_across_connections(self, db_schema) -> None:
        settings = budget_settings(limit=2)
        engines = [create_async_engine(db_schema) for _ in range(3)]
        budgets = [WafArielBudget(e, settings) for e in engines]
        try:
            async with contextlib.AsyncExitStack() as stack:
                first = await stack.enter_async_context(budgets[0].permit())
                second = await stack.enter_async_context(budgets[1].permit())
                assert first is not None and second is not None
                assert {first.slot, second.slot} == {0, 1}
                async with budgets[2].permit() as third:
                    assert third is None
                maker = async_sessionmaker(engines[2])
                async with maker() as session:
                    assert await budgets[2].active_permits(session) == 2
                    status = await waf_ariel_budget_status(session, settings=settings)
                    assert (status.enforced, status.active_permits,
                            status.max_concurrent_sources) == (True, 2, 2)
            # Released on exit.
            async with budgets[2].permit() as again:
                assert isinstance(again, WafArielPermit)
        finally:
            for e in engines:
                await e.dispose()

    async def test_a_crashed_holder_does_not_leak_its_permit(self, db_schema) -> None:
        settings = budget_settings(limit=1)
        engine = create_async_engine(db_schema)
        budget = WafArielBudget(engine, settings)
        try:
            ns = settings.waf_ariel_permit_lock_namespace
            async with holder(db_schema, ns, 0) as conn:
                pid = await conn.scalar(text("SELECT pg_backend_pid()"))
                async with budget.permit() as blocked:
                    assert blocked is None
                # The worker dies: the server ends its backend.
                async with engine.connect() as admin:
                    await admin.execute(text("SELECT pg_terminate_backend(:p)").bindparams(p=pid))
                    await admin.commit()
                await asyncio.sleep(0.1)
                async with budget.permit() as freed:
                    assert freed is not None and freed.slot == 0
        finally:
            await engine.dispose()

    async def test_a_permit_in_another_database_is_not_counted(
        self, db_session, db_schema
    ) -> None:
        """Advisory locks are per database; the count must be too."""
        settings = budget_settings(limit=3)
        ns = settings.waf_ariel_permit_lock_namespace
        assigned = make_url(assigned_database_url())
        assert assigned.database is not None
        # Another database on the same server: the server's maintenance
        # database, derived from the URL this suite was given.
        elsewhere = create_async_engine(assigned.set(database="postgres"))
        local = WafArielBudget.for_session(db_session, settings)
        try:
            async with (
                holder(db_schema, ns, 0),
                elsewhere.connect() as conn,
            ):
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                slot: int | None = None
                for candidate in range(settings.waf_ariel_max_concurrent_sources):
                    if await conn.scalar(
                        text("SELECT pg_try_advisory_lock(:ns, :s)").bindparams(
                            ns=ns, s=candidate
                        )
                    ):
                        slot = candidate
                        break
                assert slot is not None
                try:
                    # Both locks are held in the same namespace, below the
                    # limit: an unfiltered pg_locks count sees both.
                    both = await db_session.scalar(
                        text(
                            "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' "
                            "AND granted AND objsubid = 2 AND classid = :ns "
                            "AND objid < :limit"
                        ).bindparams(ns=ns, limit=settings.waf_ariel_max_concurrent_sources)
                    )
                    assert int(both or 0) >= 2
                    # Only this database's permit is the budget's.
                    assert await local.active_permits(db_session) == 1
                finally:
                    await conn.execute(
                        text("SELECT pg_advisory_unlock(:ns, :s)").bindparams(
                            ns=ns, s=slot
                        )
                    )
        finally:
            await elsewhere.dispose()

    async def test_a_dropped_connection_releases_its_permit(self, db_schema) -> None:
        settings = budget_settings(limit=1)
        engine = create_async_engine(db_schema)
        budget = WafArielBudget(engine, settings)
        try:
            async with (
                holder(db_schema, settings.waf_ariel_permit_lock_namespace, 0),
                budget.permit() as blocked,
            ):
                assert blocked is None
            # The holder's engine was disposed without ever unlocking.
            async with budget.permit() as freed:
                assert freed is not None
        finally:
            await engine.dispose()

    async def test_failed_unlock_invalidates_connection_and_frees_locks(
        self,
        db_schema: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from sqlalchemy import event

        settings = budget_settings(limit=1, wait=0)
        engine = create_async_engine(db_schema, pool_size=1, max_overflow=0)
        observer = create_async_engine(db_schema)
        budget = WafArielBudget(engine, settings)
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
            async with observer.connect() as conn:
                leaked = await conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_locks "
                        "WHERE locktype = 'advisory' AND granted AND objsubid = 2 "
                        "AND classid = :ns AND database = ("
                        "SELECT oid FROM pg_database WHERE datname = current_database())"
                    ).bindparams(ns=settings.waf_ariel_permit_lock_namespace)
                )
            assert int(leaked or 0) == 0
        finally:
            await observer.dispose()
            await engine.dispose()


class GrantingBudget(WafArielBudget):
    """Grants `n` permits, then none: a deterministic capacity squeeze."""

    def __init__(self, n: int, settings) -> None:  # type: ignore[no-untyped-def]
        super().__init__(None, settings)
        self.remaining = n

    @asynccontextmanager
    async def permit(self):  # type: ignore[no-untyped-def, override]
        if self.remaining <= 0:
            yield None
            return
        self.remaining -= 1
        yield WafArielPermit(slot=0, waited_seconds=0.0)


class TestPipelineUnderBudget:
    async def test_exhausted_capacity_writes_nothing_and_holds_the_watermark(
        self, db_session, db_schema
    ) -> None:
        settings = budget_settings(limit=1)
        provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40)])])
        profile, source = await source_with_profile(db_session, provider, settings)
        async with holder(db_schema, settings.waf_ariel_permit_lock_namespace, 0):
            result = await run_at(db_session, profile, provider, settings, T0)
        assert result["status"] == "capacity_unavailable"
        assert "no global WAF Ariel permit" in result["reason"]
        assert provider.observation_calls == 0
        assert await count(db_session, WafSourceBucket) == 0
        wm = await db_session.scalar(
            select(CollectionWatermark)
            .where(CollectionWatermark.collector == f"waf_endpoint_metric:{source.qradar_id}")
            .execution_options(populate_existing=True)
        )
        assert wm.watermark_at is None and wm.consecutive_failures == 0
        (status,) = await waf_source_statuses(db_session, settings=settings, now=T0 + STEP * 2)
        assert status.previous_run_skipped and status.last_skip_reason == "global_capacity"

        # Capacity returns: the retry collects the same bucket.
        result = await run_at(db_session, profile, provider, settings, T0)
        assert result["status"] == "ok" and result["intervals_collected"] == 1
        assert await count(db_session, WafSourceBucket) == 1
        (status,) = await waf_source_statuses(db_session, settings=settings, now=T0 + STEP * 2)
        assert not status.previous_run_skipped
        assert status.last_collection_duration_ms is not None
        assert status.last_run_intervals_collected == 1

    async def test_a_tick_without_a_permit_collects_nothing(self, db_session) -> None:
        settings = budget_settings()
        starts = [T0 + STEP * i for i in range(5)]
        provider = StubWafProvider([batch(s, [("/portal/login", "GET", 40)]) for s in starts])
        profile, _source = await source_with_profile(db_session, provider, settings)
        profile_id = profile.id
        await run_at(db_session, profile_id, provider, settings, starts[0])
        calls_before = provider.observation_calls

        squeezed = await run_waf_source(
            db_session, profile_id, provider_factory=lambda _i: provider, settings=settings,
            clock=lambda: starts[4] + STEP + timedelta(seconds=5),
            ariel_budget=GrantingBudget(0, settings),
        )
        assert squeezed["status"] == "capacity_unavailable"
        assert squeezed["capacity_unavailable"] is True
        assert squeezed["intervals_collected"] == 0
        # Not one search was sent, the watermark did not move, no bucket was written.
        assert provider.observation_calls == calls_before
        assert squeezed["collection_watermark_at"] == starts[0].isoformat()
        buckets = set((await db_session.scalars(select(WafSourceBucket.bucket_start))).all())
        assert buckets == {starts[0]}

        resumed = await run_at(db_session, profile_id, provider, settings, starts[4])
        assert resumed["status"] == "ok" and resumed["intervals_collected"] == 4
        buckets = set((await db_session.scalars(select(WafSourceBucket.bucket_start))).all())
        assert buckets == set(starts)

    async def test_one_permit_collects_the_whole_window(self, db_session) -> None:
        settings = budget_settings()
        starts = [T0 + STEP * i for i in range(5)]
        provider = StubWafProvider([batch(s, [("/portal/login", "GET", 40)]) for s in starts])
        profile, _source = await source_with_profile(db_session, provider, settings)
        profile_id = profile.id
        await run_at(db_session, profile_id, provider, settings, starts[0])

        budget = GrantingBudget(1, settings)
        tick = await run_waf_source(
            db_session, profile_id, provider_factory=lambda _i: provider, settings=settings,
            clock=lambda: starts[4] + STEP + timedelta(seconds=5), ariel_budget=budget,
        )
        # Four buckets on exactly the one permit that was granted.
        assert tick["status"] == "ok" and tick["intervals_collected"] == 4
        assert budget.remaining == 0

    async def test_a_locked_source_spends_no_permit_and_records_the_skip(
        self, db_session, db_schema
    ) -> None:
        settings = budget_settings(limit=1)
        provider = StubWafProvider([batch(T0, [("/portal/login", "GET", 40)])])
        instance = await make_instance(db_session)
        profile, source = await source_with_profile(
            db_session, provider, settings, instance=instance
        )
        key = collector_lock_key(instance.id, f"waf_pipeline:{source.qradar_id}")
        async with holder(db_schema, settings.collection_advisory_lock_namespace, key):
            budget = WafArielBudget.for_session(db_session, settings)
            result = await run_at(db_session, profile, provider, settings, T0)
            assert result["status"] == "locked"
            assert provider.observation_calls == 0
            assert await budget.active_permits(db_session) == 0
        record = await db_session.scalar(
            select(CollectionWatermark).where(
                CollectionWatermark.collector == scheduling_record(source)
            )
        )
        assert record.collection_metadata["last_skip_reason"] == "source_locked"
        (status,) = await waf_source_statuses(db_session, settings=settings, now=T0 + STEP)
        assert status.previous_run_skipped and status.last_skip_reason == "source_locked"

        assert (await run_at(db_session, profile, provider, settings, T0))["status"] == "ok"
        (status,) = await waf_source_statuses(db_session, settings=settings, now=T0 + STEP)
        assert not status.previous_run_skipped

    async def test_concurrent_sources_never_exceed_the_global_limit(
        self, db_session, db_schema
    ) -> None:
        settings = budget_settings(limit=2, wait=10.0)
        in_flight = 0
        peak = 0

        class SlowProvider(StubWafProvider):
            async def get_waf_observations(self, **kw):  # type: ignore[no-untyped-def, override]
                nonlocal in_flight, peak
                in_flight += 1
                peak = max(peak, in_flight)
                try:
                    await asyncio.sleep(0.4)
                    return await super().get_waf_observations(**kw)
                finally:
                    in_flight -= 1

        instance = await make_instance(db_session)
        providers, profile_ids = [], []
        for qid in (312, 313, 314, 315):
            provider = SlowProvider(
                [batch(T0, [("/portal/login", "GET", 40)])],
                qradar_ids=frozenset({312, 313, 314, 315}),
            )
            profile, _source = await source_with_profile(
                db_session, provider, settings, qradar_id=qid, instance=instance
            )
            providers.append(provider)
            profile_ids.append(profile.id)

        async def worker(profile_id, provider):  # type: ignore[no-untyped-def]
            engine = create_async_engine(db_schema)
            try:
                async with async_sessionmaker(engine, expire_on_commit=False)() as session:
                    return await run_waf_source(
                        session, profile_id, provider_factory=lambda _i: provider,
                        settings=settings, clock=lambda: T0 + STEP + timedelta(seconds=5),
                    )
            finally:
                await engine.dispose()

        results = await asyncio.gather(
            *(worker(p, pr) for p, pr in zip(profile_ids, providers, strict=True))
        )
        assert [r["status"] for r in results] == ["ok"] * 4
        assert peak == 2
        assert await count(db_session, WafSourceBucket) == 4


async def test_the_budget_route_requires_admin_waf(db_schema) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.security.auth import get_principal
    from app.security.rbac import Principal

    app = create_app()
    app.dependency_overrides[get_principal] = lambda: Principal(
        subject="t", permissions=frozenset({"read:*"})
    )
    assert TestClient(app).get("/api/v1/waf/ariel-budget").status_code == 403
