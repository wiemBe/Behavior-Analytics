"""LOCK-2: the IPS fleet's per-source locks are held on a connection of their own.

`_IpsFleetLocks` takes every per-source IPS pipeline lock in one
`pg_try_advisory_lock` statement over a `VALUES` list and releases them with
one `unnest` statement — two statements for the whole fleet, whatever its
size. Those locks used to run on the caller's session, and a PostgreSQL
session-level advisory lock belongs to the physical connection: a commit
inside the scope hands that connection back to the pool and separates the
locks from their release. Every test here drives the fleet scope exactly as
`IpsCollector` runs it — a session, work, a commit inside the scope, more
statements after — and observes the locks from a second, independent
connection.

The "parked" connection is how a single-coroutine test forces the production
hazard: the pool hands out the connection the commit just returned, exactly
as a concurrent collector on another worker would take it, so the caller's
next statement lands on a different one. Releasing it to the pool does not
free the locks: a pooled connection stays physically open, and the pool's
reset-on-return rollback does not release session advisory locks.
"""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Iterable, Sequence
from contextlib import asynccontextmanager

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.collectors.ips_collector import _IpsFleetLocks
from app.core.config import get_settings
from app.services.ips_pipeline import ips_pipeline_lock_key
from app.services.locks import collector_lock_key

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_INSTANCE = uuid.UUID("3f1d8c2e-9a47-4c6b-b1e0-2d5a7f9c8e11")


def _fleet_keys(sources: Sequence[int]) -> dict[int, int]:
    """The advisory-lock key of every source's IPS pipeline lock."""
    return {
        source: collector_lock_key(_INSTANCE, ips_pipeline_lock_key(source))
        for source in sources
    }


async def _held_fleet_keys(url: str, keys: Iterable[int]) -> set[int]:
    """Which of these fleet keys are held, from a connection of its own."""
    engine = create_async_engine(url, poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            rows = await conn.execute(
                text(
                    "SELECT objid FROM pg_locks WHERE locktype = 'advisory'"
                    " AND granted AND objsubid = 2 AND classid = :ns"
                    " AND objid = ANY(CAST(:keys AS integer[]))"
                    " AND database = (SELECT oid FROM pg_database"
                    " WHERE datname = current_database())"
                ).bindparams(
                    ns=get_settings().collection_advisory_lock_namespace,
                    keys=list(keys),
                )
            )
            return {int(objid) for objid, in rows}
    finally:
        await engine.dispose()


@asynccontextmanager
async def _parks_the_returned_connection(engine) -> AsyncIterator[None]:
    """Hold the connection the caller's commit just gave back to the pool.

    It is the only idle pooled connection at that moment, so the pool — LIFO or
    FIFO — hands it out here, and the caller's next statement must take another
    one. Kept checked out until the caller asks: closing it would free any lock
    that leaked onto it and hide the very failure these tests pin.
    """
    conn = await engine.connect()
    try:
        yield
    finally:
        await conn.close()


async def _holds(key: int, engine) -> AsyncConnection:
    """A connection of the test's own that holds one fleet lock."""
    conn = await engine.connect()
    await conn.execution_options(isolation_level="AUTOCOMMIT")
    assert await conn.scalar(
        text("SELECT pg_try_advisory_lock(:ns, :key)").bindparams(
            ns=get_settings().collection_advisory_lock_namespace, key=key
        )
    ) is True
    return conn


async def test_a_commit_inside_the_scope_still_leaves_every_fleet_lock_released(
    db_schema,
):
    """The caller's own commits must not separate a fleet lock from its release."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sources = [4101, 4102, 4103]
    keys = _fleet_keys(sources)
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            async with _IpsFleetLocks(
                session, get_settings(), _INSTANCE, sources
            ) as acquired:
                assert acquired == set(sources)
                assert await _held_fleet_keys(db_schema, keys.values()) == set(
                    keys.values()
                )
                # the collector commits inside the lock scope, then keeps
                # working on whatever connection the pool provides next
                await session.commit()
                async with _parks_the_returned_connection(engine):
                    await session.execute(text("SELECT 1"))
                    await session.commit()
                    await session.execute(text("SELECT 1"))
        assert await _held_fleet_keys(db_schema, keys.values()) == set(), (
            "a fleet lock outlived its scope"
        )
        # free again: the very next fleet run takes every source at once
        async with maker() as session, _IpsFleetLocks(
            session, get_settings(), _INSTANCE, sources
        ) as again:
            assert again == set(sources)
        assert await _held_fleet_keys(db_schema, keys.values()) == set()
    finally:
        await engine.dispose()


async def test_partial_acquisition_returns_and_releases_only_the_free_sources(
    db_schema,
):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sources = [4201, 4202, 4203]
    keys = _fleet_keys(sources)
    holder: AsyncConnection | None = None
    try:
        # another worker already holds the middle source's pipeline lock
        holder = await _holds(keys[4202], engine)
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            async with _IpsFleetLocks(
                session, get_settings(), _INSTANCE, sources
            ) as acquired:
                assert acquired == {4201, 4203}
                assert await _held_fleet_keys(db_schema, keys.values()) == set(
                    keys.values()
                )
            # exit released exactly the two it took; the holder keeps its own
            assert await _held_fleet_keys(db_schema, keys.values()) == {keys[4202]}
        assert await holder.scalar(
            text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                ns=get_settings().collection_advisory_lock_namespace, key=keys[4202]
            )
        ) is True
        await holder.close()
        holder = None
        # with the holder gone the whole fleet is lockable again
        async with maker() as session, _IpsFleetLocks(
            session, get_settings(), _INSTANCE, sources
        ) as again:
            assert again == set(sources)
    finally:
        if holder is not None:
            await holder.close()
        await engine.dispose()


async def test_an_exception_inside_the_scope_still_releases_every_fleet_lock(
    db_schema,
):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sources = [4301, 4302]
    keys = _fleet_keys(sources)
    parked: AsyncConnection | None = None
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            with pytest.raises(RuntimeError, match="collector failed"):
                async with _IpsFleetLocks(
                    session, get_settings(), _INSTANCE, sources
                ) as acquired:
                    assert acquired == set(sources)
                    await session.commit()
                    parked = await engine.connect()
                    await session.execute(text("SELECT 1"))
                    raise RuntimeError("collector failed")
            # only now may the parked connection go: closing it earlier would
            # free a lock that leaked onto it and hide the failure
            await parked.close()
            parked = None
        assert await _held_fleet_keys(db_schema, keys.values()) == set(), (
            "a fleet lock outlived a failed run"
        )
        async with maker() as session, _IpsFleetLocks(
            session, get_settings(), _INSTANCE, sources
        ) as again:
            assert again == set(sources)
    finally:
        if parked is not None:
            await parked.close()
        await engine.dispose()


async def test_cancellation_inside_the_scope_still_releases_every_fleet_lock(
    db_schema,
):
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sources = [4401, 4402]
    keys = _fleet_keys(sources)
    parked: AsyncConnection | None = None
    entered = asyncio.Event()
    try:

        async def collector_run() -> None:
            nonlocal parked
            async with maker() as session:
                await session.execute(text("SELECT 1"))
                async with _IpsFleetLocks(
                    session, get_settings(), _INSTANCE, sources
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
        assert await _held_fleet_keys(db_schema, keys.values()) == set(), (
            "a fleet lock outlived a cancelled run"
        )
        async with maker() as session, _IpsFleetLocks(
            session, get_settings(), _INSTANCE, sources
        ) as again:
            assert again == set(sources)
    finally:
        if parked is not None:
            await parked.close()
        await engine.dispose()


async def test_a_scope_that_acquires_nothing_keeps_no_connection(db_schema):
    """A fleet take that holds nothing must return its connection at once."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sources = [4501, 4502]
    keys = _fleet_keys(sources)
    holder: AsyncConnection | None = None
    try:
        # every source is already held elsewhere, so the fleet take gets nothing
        holder = await _holds(keys[4501], engine)
        second = await _holds(keys[4502], engine)
        async with maker() as session:
            # warm the pool: one execute-and-commit leaves exactly one idle
            # connection, so the checked-in count below has a fixed base
            await session.execute(text("SELECT 1"))
            await session.commit()
            before = engine.pool.checkedin()
            async with _IpsFleetLocks(
                session, get_settings(), _INSTANCE, sources
            ) as acquired:
                assert acquired == set()
            assert engine.pool.checkedin() == before, (
                "a fleet scope that acquired nothing kept a connection"
            )
        # releasing nothing must not touch the holders' locks
        assert await _held_fleet_keys(db_schema, keys.values()) == set(keys.values())
        await second.close()
    finally:
        if holder is not None:
            await holder.close()
        await engine.dispose()


class _DeadReleaseConn:
    """Delegates everything but `execute`: the release statement cannot run.

    The failure is injected on the lock's own connection, so the probe that
    reads `pg_locks` afterwards is untouched.
    """

    def __init__(self, conn: AsyncConnection) -> None:
        self._conn = conn
        self.invalidated = False

    async def execute(self, statement: object, *args: object, **kwargs: object) -> None:
        raise OSError("connection lost")

    async def invalidate(self) -> None:
        self.invalidated = True
        await self._conn.invalidate()

    async def close(self) -> None:
        await self._conn.close()


async def test_a_failing_release_invalidates_the_connection(db_schema):
    """A release that cannot run must close the connection, not pool it."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    sources = [4601, 4602, 4603]
    keys = _fleet_keys(sources)
    try:
        async with maker() as session:
            await session.execute(text("SELECT 1"))
            fleet = _IpsFleetLocks(session, get_settings(), _INSTANCE, sources)
            with pytest.raises(OSError, match="connection lost"):
                async with fleet as acquired:
                    assert acquired == set(sources)
                    assert fleet._lock_conn is not None
                    dead = _DeadReleaseConn(fleet._lock_conn)
                    fleet._lock_conn = dead
            # the lock's connection was invalidated and closed: only the
            # session's own connection is still out, and every lock the dead
            # connection held died with it
            assert dead.invalidated, "the unreleased connection was not invalidated"
            assert engine.pool.checkedout() == 1
            assert await _held_fleet_keys(db_schema, keys.values()) == set()
        async with maker() as session, _IpsFleetLocks(
            session, get_settings(), _INSTANCE, sources
        ) as again:
            assert again == set(sources)
    finally:
        await engine.dispose()


async def test_the_statement_count_stays_two_for_one_and_five_sources(db_schema):
    """The fleet costs one take statement and one release, whatever its size."""
    engine = create_async_engine(db_schema, future=True)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    statements: dict[int, list[str]] = defaultdict(list)
    # strong references keep every lock connection's id() stable, so the two
    # fleet sizes cannot read each other's statement lists
    lock_conns: list[object] = []

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def record(conn, cursor, statement, parameters, context, executemany):
        statements[id(conn)].append(statement)

    try:
        for sources in ([4701], [4801, 4802, 4803, 4804, 4805]):
            async with maker() as session:
                await session.execute(text("SELECT 1"))
                fleet = _IpsFleetLocks(session, get_settings(), _INSTANCE, sources)
                async with fleet as acquired:
                    assert acquired == set(sources)
                    assert fleet._lock_conn is not None
                    lock_conns.append(fleet._lock_conn.sync_connection)
                on_lock_conn = statements[id(lock_conns[-1])]
                takes = [s for s in on_lock_conn if "pg_try_advisory_lock" in s]
                releases = [s for s in on_lock_conn if "pg_advisory_unlock" in s]
                assert len(takes) == 1, on_lock_conn
                assert len(releases) == 1, on_lock_conn
    finally:
        await engine.dispose()
