"""A cross-process budget on every Ariel search lifecycle.

Process-local semaphores cannot bound searches issued by separate Celery
workers, API processes and command-line runs. This module makes the configured
per-console and global ceilings real with PostgreSQL session advisory locks.
One permit holds an optional console slot and one global slot on a **dedicated
connection** for the whole search lifecycle.

  * *Cross-worker.* Every process connected to this database sees the same lock
    table.
  * *Crash-safe, no leaked permits.* Session advisory locks belong to their
    connection. PostgreSQL frees both slots when a dead worker's connection
    ends; there is no TTL or heartbeat to tune.
  * *Independent of the caller's transaction.* The permit uses one
    ``AUTOCOMMIT`` connection that does nothing else, so an aborted application
    transaction cannot prevent release.
  * *Bounded wait.* Acquisition uses ``pg_try_advisory_lock`` and polls for at
    most ``ARIEL_PERMIT_WAIT_SECONDS``. It never queues behind a lock. If the
    instance half was acquired but the global pool stays full, that half is
    released before refusal.
  * *Cancellation-safe cleanup.* Every exit asks PostgreSQL to release every
    advisory lock on the dedicated connection. If that cleanup fails or is
    interrupted, the connection is invalidated so the pool cannot reuse it.
  * *Live-first.* Background work cannot take the reserved low-numbered global
    slots. Live collection, the default, may take every global slot.
  * *Observable.* ``active_permits`` counts only locks held in this database;
    another installation sharing the PostgreSQL server is not included.

On a non-PostgreSQL bind (the SQLite unit suite) the budget is unbounded, as
``CollectorAdvisoryLock`` and ``WafArielBudget`` are.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, AsyncSession

from app.core.config import Settings
from app.i18n.messages import MessageRef, render
from app.providers.base import ProviderUnavailableError

_ArielPriority = Literal["live", "background"]
_ARIEL_PRIORITY: ContextVar[_ArielPriority] = ContextVar("ariel_priority", default="live")
_MAX_ADVISORY_SLOT = 2**31 - 1
_WAIT_LOG_SECONDS = 1.0
ARIEL_HISTORY_DEFERRED_REASON = "deferred: no Ariel permit within {waited_seconds:g} s"
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ArielPermit:
    #: The global slot held, or None when the budget is not enforced.
    global_slot: int | None
    #: The console slot held, or None for a global-only or unenforced permit.
    instance_slot: int | None
    waited_seconds: float


@dataclass(frozen=True)
class ArielPermitCounts:
    """Held permits and configured limits for this budget's scope.

    ``instance_held`` is the count for this budget's console when it has an
    ``instance_id``. A global-only budget reports all console-pool locks in the
    current database instead.
    """

    global_held: int
    instance_held: int
    global_cap: int
    instance_cap: int
    reserved_live_slots: int


class ArielPermitUnavailable(ProviderUnavailableError):
    """No Ariel capacity became available within the configured wait."""

    def __init__(self, waited_seconds: float, instance_cap: int, global_cap: int) -> None:
        self.waited_seconds = waited_seconds
        self.instance_cap = instance_cap
        self.global_cap = global_cap
        #: The sentence as a message code: a run refused for capacity stores it beside its text.
        self.ref = MessageRef(
            "search.run.capacity",
            {"waited": waited_seconds, "instance_cap": instance_cap, "global_cap": global_cap},
        )
        super().__init__(render(self.ref))


def ariel_history_deferred_reason(exc: ArielPermitUnavailable) -> str:
    """The single operator-facing reason used by every yielding history step."""

    return ARIEL_HISTORY_DEFERRED_REASON.format(waited_seconds=exc.waited_seconds)


@contextmanager
def ariel_background_priority() -> Iterator[None]:
    """Mark Ariel permit requests in this task-local scope as background work."""

    token = _ARIEL_PRIORITY.set("background")
    try:
        yield
    finally:
        _ARIEL_PRIORITY.reset(token)


def _instance_slot_base(instance_id: uuid.UUID, limit: int) -> int:
    """Map a console UUID to one bounded, contiguous advisory-lock range."""

    ranges = (_MAX_ADVISORY_SLOT + 1) // limit
    digest = hashlib.sha256(instance_id.bytes).digest()
    value = int.from_bytes(digest[:8], byteorder="big")
    return (value % ranges) * limit


class ArielPermitBudget:
    def __init__(
        self,
        engine: AsyncEngine | None,
        settings: Settings,
        *,
        instance_id: uuid.UUID | None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._engine = engine
        self.instance_id = instance_id
        self.instance_limit = settings.ariel_max_concurrent_searches
        self.global_limit = settings.ariel_global_max_concurrent_searches
        self.global_namespace = settings.ariel_permit_lock_namespace
        self.instance_namespace = settings.ariel_permit_instance_lock_namespace
        self.wait_seconds = settings.ariel_permit_wait_seconds
        self.poll_seconds = settings.ariel_permit_poll_seconds
        self.reserved_live_slots = settings.ariel_permit_reserved_live_slots
        self._sleep = sleep
        self._monotonic = monotonic

    @property
    def _instance_base(self) -> int | None:
        """The console's slot range start, derived on first use.

        Lazy so that building a provider never hashes the id: only a search that is
        about to hold a permit needs the range.
        """
        if self.instance_id is None:
            return None
        return _instance_slot_base(self.instance_id, self.instance_limit)

    @classmethod
    def for_session(
        cls,
        session: AsyncSession,
        settings: Settings,
        *,
        instance_id: uuid.UUID | None,
    ) -> ArielPermitBudget:
        bind = session.bind
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        return cls(
            engine if isinstance(engine, AsyncEngine) else None,
            settings,
            instance_id=instance_id,
        )

    @property
    def enforced(self) -> bool:
        return self._engine is not None and self._engine.dialect.name == "postgresql"

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[ArielPermit | None]:
        """Hold the applicable slots for the block, or yield None.

        None means capacity did not become available within the bounded wait:
        the caller must not issue its search and must not interpret the refusal
        as an empty result.
        """
        if not self.enforced:
            yield ArielPermit(global_slot=None, instance_slot=None, waited_seconds=0.0)
            return
        assert self._engine is not None
        started = self._monotonic()
        async with self._engine.connect() as conn:
            try:
                await conn.execution_options(isolation_level="AUTOCOMMIT")
                slots = await self._acquire(conn, started)
                waited = self._monotonic() - started
                if slots is None:
                    # The permit does not know which family asked. The collection
                    # watermark and the history reason already name it.
                    logger.warning(
                        "Ariel permit refused",
                        extra={
                            "priority": _ARIEL_PRIORITY.get(),
                            "waited_seconds": waited,
                            "instance_cap": self.instance_limit,
                            "global_cap": self.global_limit,
                        },
                    )
                    yield None
                    return
                instance_slot, global_slot = slots
                if waited >= _WAIT_LOG_SECONDS:
                    # The permit does not know which family asked. The collection
                    # watermark and the history reason already name it.
                    logger.info(
                        "Ariel permit granted after a wait",
                        extra={
                            "waited_seconds": waited,
                            "priority": _ARIEL_PRIORITY.get(),
                            "global_slot": global_slot,
                            "instance_slot": instance_slot,
                        },
                    )
                yield ArielPermit(
                    global_slot=global_slot,
                    instance_slot=instance_slot,
                    waited_seconds=waited,
                )
            finally:
                await self._release_all(conn)

    async def _acquire(
        self, conn: AsyncConnection, started: float
    ) -> tuple[int | None, int] | None:
        instance_slot: int | None = None
        while True:
            if self._instance_base is not None and instance_slot is None:
                instance_slot = await self._try_pool(
                    conn,
                    namespace=self.instance_namespace,
                    first_slot=self._instance_base,
                    count=self.instance_limit,
                )
            instance_ready = self._instance_base is None or instance_slot is not None
            if instance_ready:
                first_global = (
                    self.reserved_live_slots if _ARIEL_PRIORITY.get() == "background" else 0
                )
                global_slot = await self._try_pool(
                    conn,
                    namespace=self.global_namespace,
                    first_slot=first_global,
                    count=self.global_limit - first_global,
                )
                if global_slot is not None:
                    return instance_slot, global_slot
            if self._monotonic() - started + self.poll_seconds > self.wait_seconds:
                return None
            await self._sleep(self.poll_seconds)

    async def _try_pool(
        self,
        conn: AsyncConnection,
        *,
        namespace: int,
        first_slot: int,
        count: int,
    ) -> int | None:
        # A random starting slot spreads contention instead of every worker
        # hammering the first eligible slot. The order does not affect safety.
        offset = random.randrange(count)  # noqa: S311 - not security relevant
        for i in range(count):
            slot = first_slot + (offset + i) % count
            got = await conn.scalar(
                text("SELECT pg_try_advisory_lock(:ns, :slot)").bindparams(
                    ns=namespace, slot=slot
                )
            )
            if got:
                return slot
        return None

    @staticmethod
    async def _release_all(conn: AsyncConnection) -> None:
        """Clear this connection's locks or make the connection unreusable."""

        try:
            await conn.execute(text("SELECT pg_advisory_unlock_all()"))
        except BaseException:
            # A failed or cancelled unlock cannot be followed by pool reuse:
            # rollback does not release session advisory locks. Invalidation
            # closes the physical connection, which makes PostgreSQL free them.
            await conn.invalidate()
            raise

    async def active_permits(self, session: AsyncSession) -> ArielPermitCounts | None:
        """Return held slots in this database, or None when not enforced.

        Global locks are restricted to the configured pool. Console locks are
        restricted to this budget's derived range when it has an instance id;
        a global-only budget counts all console locks in the namespace. Locks
        in another database on the same server are excluded.
        """
        if not self.enforced:
            return None
        params: dict[str, int] = {
            "global_ns": self.global_namespace,
            "global_limit": self.global_limit,
            "instance_ns": self.instance_namespace,
        }
        if self._instance_base is None:
            statement = text(
                "SELECT "
                "count(*) FILTER (WHERE classid = :global_ns "
                "AND objid < :global_limit) AS global_held, "
                "count(*) FILTER (WHERE classid = :instance_ns) AS instance_held "
                "FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND objsubid = 2 AND database = (SELECT oid FROM pg_database "
                "WHERE datname = current_database())"
            )
        else:
            params.update(
                instance_base=self._instance_base,
                instance_end=self._instance_base + self.instance_limit,
            )
            statement = text(
                "SELECT "
                "count(*) FILTER (WHERE classid = :global_ns "
                "AND objid < :global_limit) AS global_held, "
                "count(*) FILTER (WHERE classid = :instance_ns "
                "AND objid >= :instance_base AND objid < :instance_end) AS instance_held "
                "FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND objsubid = 2 AND database = (SELECT oid FROM pg_database "
                "WHERE datname = current_database())"
            )
        row = (await session.execute(statement.bindparams(**params))).one()
        return ArielPermitCounts(
            global_held=int(row.global_held or 0),
            instance_held=int(row.instance_held or 0),
            global_cap=self.global_limit,
            instance_cap=self.instance_limit,
            reserved_live_slots=self.reserved_live_slots,
        )
