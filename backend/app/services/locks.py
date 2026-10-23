"""PostgreSQL advisory locks for background collection.

Every collector that writes per-instance state takes one of these for the
duration of a run. The lock is *non-blocking*: a second worker that finds the
lock held skips the run instead of queueing behind it. Queueing would be worse
than skipping — the work is periodic, so a delayed run just does the next tick's
work twice, while a queue of blocked workers ties up the pool.

The lock key combines the QRadar instance with the collector name, so the
offense collector and the metric collector never contend with each other: they
touch different tables and have no reason to serialize.

A PostgreSQL session-level advisory lock belongs to the *physical connection*,
and a pooled session hands its connection back at every commit. A lock taken on
the caller's session would therefore follow that connection into the pool: the
next statement of the run may run on a different connection, the unlock returns
false, and the lock survives the run on whichever connection it sat on. This
class takes the lock on a dedicated AUTOCOMMIT connection of its own, checked
out from the session's engine for exactly the scope of the ``async with``, so
commits, rollbacks and connection changes on the caller's session cannot
separate the lock from its release.
"""

from __future__ import annotations

import hashlib
import logging
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
)

from app.core.config import Settings

logger = logging.getLogger(__name__)


def collector_lock_key(instance_id: uuid.UUID, collector: str = "") -> int:
    """Stable 31-bit advisory-lock key for one (instance, collector) pair.

    `collector=""` reproduces the historical instance-only key, so an existing
    deployment's held locks keep their meaning across this change.
    """
    key = instance_id.int & 0x7FFFFFFF
    if collector:
        digest = hashlib.sha256(collector.encode()).digest()[:4]
        key ^= int.from_bytes(digest, "big") & 0x7FFFFFFF
    return key & 0x7FFFFFFF


class CollectorAdvisoryLock:
    """Session-level advisory lock scoped to (instance, collector).

    The lock is held on a dedicated connection of the caller's engine, not on
    the caller's own session connection, and is released on that same
    connection on every exit path (normal, exception, cancellation). A release
    that fails or reports the lock as not held invalidates the connection, so
    a connection that may still hold the lock is closed rather than pooled.
    """

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        instance_id: uuid.UUID,
        collector: str = "",
    ) -> None:
        self.session = session
        self._namespace = settings.collection_advisory_lock_namespace
        self._key = collector_lock_key(instance_id, collector)
        self._acquired = False
        self._lock_conn: AsyncConnection | None = None

    def _engine(self) -> AsyncEngine | None:
        bind = self.session.bind
        engine = bind.engine if isinstance(bind, AsyncConnection) else bind
        return engine if isinstance(engine, AsyncEngine) else None

    async def __aenter__(self) -> bool:
        engine = self._engine()
        # SQLite (unit tests) and unbound sessions have no advisory locks;
        # treat as always-acquired, holding no connection.
        if engine is None or engine.dialect.name != "postgresql":
            self._acquired = True
            return True
        conn = await engine.connect()
        try:
            await conn.execution_options(isolation_level="AUTOCOMMIT")
            acquired = await conn.scalar(
                text("SELECT pg_try_advisory_lock(:ns, :key)").bindparams(
                    ns=self._namespace, key=self._key
                )
            )
        except BaseException:
            await conn.close()
            raise
        if acquired is not True:
            # Not acquired: nothing is held, so the connection goes straight back.
            await conn.close()
            self._acquired = False
            return False
        self._lock_conn = conn
        self._acquired = True
        return True

    async def __aexit__(self, *exc: object) -> None:
        conn, self._lock_conn = self._lock_conn, None
        if conn is None:
            return
        try:
            released = await conn.scalar(
                text("SELECT pg_advisory_unlock(:ns, :key)").bindparams(
                    ns=self._namespace, key=self._key
                )
            )
            if released is not True:
                # The statement ran but the lock was not ours to release: the
                # connection's lock state is not what we think it is, so it
                # must not go back to the pool. Invalidation closes it, and
                # PostgreSQL frees whatever it still holds.
                await conn.invalidate()
                logger.warning(
                    "collector advisory lock reported not held on release",
                    extra={"namespace": self._namespace, "key": self._key},
                )
        except BaseException:
            # A failed or cancelled release cannot be followed by pool reuse:
            # rollback does not release session advisory locks. Invalidation
            # closes the physical connection, which makes PostgreSQL free them.
            await conn.invalidate()
            raise
        finally:
            await conn.close()
