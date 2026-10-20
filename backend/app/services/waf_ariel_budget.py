"""A global, cross-worker budget on WAF Ariel work.

Each WAF source runs in its own Celery task under its own per-source lock, which
isolates sources from each other but does not bound how many of them search
Ariel at once: an estate of forty sources dispatched in one beat could start
forty searches together. This module caps that at
`WAF_ARIEL_MAX_CONCURRENT_SOURCES`, across every worker process and host.

**Mechanism.** A counting semaphore built from the primitive the collectors
already rely on: PostgreSQL session advisory locks. The budget has `limit` slots,
slot `i` being the advisory lock `(WAF_ARIEL_PERMIT_LOCK_NAMESPACE, i)`. A permit
is one slot held with `pg_try_advisory_lock` on a **dedicated connection** that
does nothing else.

  * *Cross-worker.* Every worker sees the same lock table.
  * *Crash-safe, no leaked permits.* A session advisory lock belongs to its
    connection. If the worker dies -- killed by a Celery time limit, OOM, a lost
    host -- the server ends the backend and the slot is free. There is no TTL to
    tune and no heartbeat to miss. (A worker that is alive but stuck keeps its
    slot until its Ariel poll bound or its task time limit ends it; a host that
    vanishes without closing TCP holds it until the server's TCP keepalive
    notices.)
  * *Independent of the run's transaction.* The pipeline's own session may roll
    back or be aborted by a database error; the permit connection is not part of
    that transaction, so release never depends on it.
  * *Bounded wait.* Acquisition polls every `WAF_ARIEL_PERMIT_POLL_SECONDS` for
    at most `WAF_ARIEL_PERMIT_WAIT_SECONDS`. It never queues behind a lock.
  * *Observable.* Held slots are visible in `pg_locks`; `active_permits` counts
    them in this database. Advisory locks are scoped to their database, and
    `pg_locks` lists every database on the server, so the count is restricted
    to the current database: a permit held by another installation that shares
    the server is not this installation's.

On a non-PostgreSQL bind (the SQLite unit suite) the budget is unbounded, as
`CollectorAdvisoryLock` is.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core.config import Settings


@dataclass(frozen=True)
class WafArielPermit:
    #: The slot held, or None when the budget is not enforced on this backend.
    slot: int | None
    waited_seconds: float


class WafArielBudget:
    def __init__(
        self,
        engine: AsyncEngine | None,
        settings: Settings,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self.limit = settings.waf_ariel_max_concurrent_sources
        self.namespace = settings.waf_ariel_permit_lock_namespace
        self.wait_seconds = settings.waf_ariel_permit_wait_seconds
        self.poll_seconds = settings.waf_ariel_permit_poll_seconds
        self._sleep = sleep
        self._monotonic = monotonic

    @classmethod
    def for_session(cls, session: AsyncSession, settings: Settings) -> WafArielBudget:
        bind = session.bind
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        return cls(engine if isinstance(engine, AsyncEngine) else None, settings)

    @property
    def enforced(self) -> bool:
        return self._engine is not None and self._engine.dialect.name == "postgresql"

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[WafArielPermit | None]:
        """Hold one slot for the duration of the block, or yield None.

        None means no slot freed up within the wait: the caller must not issue
        its searches, and must not treat that as an empty result.
        """
        if not self.enforced:
            yield WafArielPermit(slot=None, waited_seconds=0.0)
            return
        assert self._engine is not None
        started = self._monotonic()
        async with self._engine.connect() as conn:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            slot = await self._acquire(conn, started)
            if slot is None:
                yield None
                return
            try:
                yield WafArielPermit(slot=slot, waited_seconds=self._monotonic() - started)
            finally:
                # Closing the connection would release the slot too; unlocking
                # explicitly returns it at once, before the pool reuses the
                # connection.
                await conn.execute(
                    text("SELECT pg_advisory_unlock(:ns, :slot)").bindparams(
                        ns=self.namespace, slot=slot
                    )
                )

    async def _acquire(self, conn: AsyncConnection, started: float) -> int | None:
        while True:
            # A random starting slot spreads contention instead of every worker
            # hammering slot 0 first. The order does not affect correctness.
            offset = random.randrange(self.limit)  # noqa: S311 - not security relevant
            for i in range(self.limit):
                slot = (offset + i) % self.limit
                got = await conn.scalar(
                    text("SELECT pg_try_advisory_lock(:ns, :slot)").bindparams(
                        ns=self.namespace, slot=slot
                    )
                )
                if got:
                    return slot
            if self._monotonic() - started + self.poll_seconds > self.wait_seconds:
                return None
            await self._sleep(self.poll_seconds)

    async def active_permits(self, session: AsyncSession) -> int | None:
        """Slots currently held in this database. None when not enforced.

        Counts slots below the *configured* limit only; a slot above it would
        belong to a worker still running with a larger, older setting. Permits
        in another database on the same server are not counted: the advisory
        locks behind them are scoped to that database.
        """
        if not self.enforced:
            return None
        count = await session.scalar(
            text(
                "SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND objsubid = 2 AND classid = :ns AND objid < :limit "
                "AND database = (SELECT oid FROM pg_database "
                "WHERE datname = current_database())"
            ).bindparams(ns=self.namespace, limit=self.limit)
        )
        return int(count or 0)
