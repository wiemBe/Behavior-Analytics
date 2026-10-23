"""LOCK-1: a collector's advisory lock is held on a connection of its own.

A PostgreSQL session-level advisory lock belongs to the physical connection,
and a pooled session returns its connection at every commit. A lock taken on
the caller's session can therefore end up held by whatever connection the pool
hands out next, and the run's unlock runs on a connection that does not hold
it. Every test here drives `CollectorAdvisoryLock` exactly as the collectors
run it — a session, work, a commit inside the scope, more statements after —
and observes the lock from a second, independent connection.

The "parked" connection is how a single-coroutine test forces the production
hazard: the pool hands out the connection the commit just returned, exactly as
a concurrent collector on another worker would take it, so the caller's next
statement lands on a different one.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.services.locks import CollectorAdvisoryLock, collector_lock_key

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_INSTANCE = uuid.UUID("0116f6ca-6b74-4d8e-b0b3-8f1cf3ec9d41")
_COLLECTOR = "offense_snapshot"
_KEY = collector_lock_key(_INSTANCE, _COLLECTOR)


async def _held_locks(url: str) -> int:
    """Count this test's advisory lock from a connection of its own."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            count = await conn.scalar(
                text(
                    "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory'"
                    " AND granted AND objsubid = 2 AND classid = :ns"
                    " AND objid = :key AND database = (SELECT oid FROM pg_database"
                    " WHERE datname = current_database())"
                ).bindparams(
                    ns=get_settings().collection_advisory_lock_namespace, key=_KEY
                )
            )
            return int(count or 0)
    finally:
        await engine.dispose()


@asynccontextmanager
async def _parks_the_returned_connection(engine) -> AsyncIterator[None]:
    """Hold the connection the caller's commit just gave back to the pool.

    It is the only idle pooled connection at that moment, so the pool — LIFO or
    FIFO — hands it out here, and the caller's next statement must take another
    one. Kept open until the caller asks: closing it would free any lock that
    leaked onto it and hide the very failure these tests pin.
    """
    conn = await engine.connect()
    try:
        yield
    finally:
        await conn.close()


async def test_a_commit_inside_the_scope_still_leaves_the_lock_released(db_schema):
    """The caller's own commits must not separate the lock from its release."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            async with CollectorAdvisoryLock(
                session, get_settings(), _INSTANCE, _COLLECTOR
            ) as acquired:
                assert acquired is True
                assert await _held_locks(db_schema) == 1
                # the collector commits inside the lock's scope, then keeps
                # working on whatever connection the pool provides next
                await session.commit()
                async with _parks_the_returned_connection(engine):
                    await session.execute(text("SELECT 1"))
                    await session.commit()
                    await session.execute(text("SELECT 1"))
        assert await _held_locks(db_schema) == 0, "the lock outlived its scope"
        # free again: the very next run of the same collector acquires
        async with maker() as session, CollectorAdvisoryLock(
            session, get_settings(), _INSTANCE, _COLLECTOR
        ) as again:
            assert again is True
        assert await _held_locks(db_schema) == 0
    finally:
        await engine.dispose()


async def test_an_exception_inside_the_scope_still_releases_the_lock(db_schema):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    parked: AsyncConnection | None = None
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            with pytest.raises(RuntimeError, match="collector failed"):
                async with CollectorAdvisoryLock(
                    session, get_settings(), _INSTANCE, _COLLECTOR
                ) as acquired:
                    assert acquired is True
                    await session.commit()
                    parked = await engine.connect()
                    await session.execute(text("SELECT 1"))
                    raise RuntimeError("collector failed")
            # only now may the parked connection go: closing it earlier would
            # free a lock that leaked onto it and hide the failure
            await parked.close()
            parked = None
        assert await _held_locks(db_schema) == 0, "the lock outlived a failed run"
        async with maker() as session, CollectorAdvisoryLock(
            session, get_settings(), _INSTANCE, _COLLECTOR
        ) as again:
            assert again is True
    finally:
        if parked is not None:
            await parked.close()
        await engine.dispose()


async def test_cancellation_inside_the_scope_still_releases_the_lock(db_schema):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    parked: AsyncConnection | None = None
    entered = asyncio.Event()
    try:

        async def collector_run() -> None:
            nonlocal parked
            async with maker() as session:
                await session.execute(text("SELECT 1"))
                async with CollectorAdvisoryLock(
                    session, get_settings(), _INSTANCE, _COLLECTOR
                ):
                    await session.commit()
                    parked = await engine.connect()
                    await session.execute(text("SELECT 1"))
                    entered.set()
                    await asyncio.Event().wait()  # cancelled here

        task = asyncio.create_task(collector_run())
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 10)
        assert parked is not None
        await parked.close()
        parked = None
        assert await _held_locks(db_schema) == 0, "the lock outlived a cancelled run"
        async with maker() as session, CollectorAdvisoryLock(
            session, get_settings(), _INSTANCE, _COLLECTOR
        ) as again:
            assert again is True
    finally:
        if parked is not None:
            await parked.close()
        await engine.dispose()


async def test_a_refused_lock_keeps_no_connection(db_schema):
    """`pg_try_advisory_lock` returning false must return the connection at once."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as holder, CollectorAdvisoryLock(
            holder, get_settings(), _INSTANCE, _COLLECTOR
        ) as held:
            assert held is True
            async with maker() as session:
                # warm the pool: one execute-and-commit leaves exactly one
                # idle connection, so the counts below have a fixed base
                await session.execute(text("SELECT 1"))
                await session.commit()
                before = engine.pool.checkedin()
                async with CollectorAdvisoryLock(
                    session, get_settings(), _INSTANCE, _COLLECTOR
                ) as refused:
                    assert refused is False
                assert engine.pool.checkedin() == before, (
                    "the refused lock kept a connection out of the pool"
                )
    finally:
        await engine.dispose()


class _DeadUnlockConn:
    """Delegates everything but `scalar`: the unlock statement cannot run.

    The failure is injected on the lock's own connection, so the probe that
    reads `pg_locks` afterwards is untouched.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        self.invalidated = False

    async def scalar(self, statement: object, *args: object, **kwargs: object) -> None:
        raise OSError("connection lost")

    async def invalidate(self) -> None:
        self.invalidated = True
        await self._conn.invalidate()

    async def close(self) -> None:
        await self._conn.close()


async def test_a_failing_unlock_invalidates_the_connection(db_schema):
    """A release that cannot run must close the connection, not pool it."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            lock = CollectorAdvisoryLock(
                session, get_settings(), _INSTANCE, _COLLECTOR
            )
            with pytest.raises(OSError, match="connection lost"):
                async with lock as acquired:
                    assert acquired is True
                    assert lock._lock_conn is not None
                    dead = _DeadUnlockConn(lock._lock_conn)
                    lock._lock_conn = dead
            # the lock's connection was invalidated and closed: only the
            # session's own connection is still out, and the lock the dead
            # connection held died with it
            assert dead.invalidated, "the unreleased connection was not invalidated"
            assert engine.pool.checkedout() == 1
            assert await _held_locks(db_schema) == 0
        async with maker() as session, CollectorAdvisoryLock(
            session, get_settings(), _INSTANCE, _COLLECTOR
        ) as again:
            assert again is True
    finally:
        await engine.dispose()
