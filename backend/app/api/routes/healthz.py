"""The public whole-app health answer: ``GET /api/v1/healthz``.

For monitoring, not for orchestration: container liveness stays on
``/health/live`` so a slow dependency cannot restart-loop the process. Every
component reduces to ``ok``, ``fail`` or ``unknown``; no host, revision, error
text, count or worker name is ever part of the body.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal

import redis.asyncio as redis
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.core.version import migration_head
from app.workers.celery_app import (
    BEAT_HEARTBEAT_KEY,
    BEAT_HEARTBEAT_TTL_SECONDS,
    QRADAR_WALKS_QUEUE,
    celery_app,
)

logger = logging.getLogger("app.healthz")

router = APIRouter(prefix="/healthz", tags=["health"])

ComponentStatus = Literal["ok", "fail", "unknown"]

#: The exact field set of the public body. ``api`` is the process answering.
COMPONENTS: tuple[str, ...] = (
    "api",
    "database",
    "migrations",
    "redis",
    "workers",
    "walker",
    "beat",
)

#: A Celery inspect with no ``limit`` waits its whole timeout for replies even
#: when every worker has already answered. It must sit clearly below the
#: per-check budget: the worker thread starts later and still has to open the
#: broker connection, so equal budgets make a healthy system report ``workers``
#: and ``walker`` as ``unknown`` on every uncached call.
INSPECT_TIMEOUT_SECONDS = 1.0
#: Each check is bounded on its own, so a broadcast that never answers becomes
#: ``unknown`` instead of holding the request open. All seven run at once.
CHECK_TIMEOUT_SECONDS = 2.0
TOTAL_TIMEOUT_SECONDS = 3.0
#: A monitor polling every few seconds must not fan out Celery broadcasts: one
#: answer is shared by every caller for this long.
CACHE_SECONDS = 10.0


async def _check_database(session: AsyncSession) -> bool:
    await session.execute(text("SELECT 1"))
    return True


async def _check_migrations(session: AsyncSession) -> bool:
    expected = migration_head()
    if expected is None:
        return False
    result = await session.execute(text("SELECT version_num FROM alembic_version"))
    rows = result.all()
    return len(rows) == 1 and rows[0][0] == expected


async def _check_redis() -> bool:
    client = redis.from_url(get_settings().redis_url)
    try:
        return bool(await client.ping())
    finally:
        await client.aclose()


async def _check_workers() -> bool:
    def ping() -> dict[str, object] | None:
        return celery_app.control.inspect(timeout=INSPECT_TIMEOUT_SECONDS).ping()

    return bool(await asyncio.to_thread(ping))


async def _check_walker() -> bool:
    def active_queues() -> dict[str, list[dict[str, object]]] | None:
        return celery_app.control.inspect(timeout=INSPECT_TIMEOUT_SECONDS).active_queues()

    queues = await asyncio.to_thread(active_queues)
    return any(
        queue.get("name") == QRADAR_WALKS_QUEUE
        for entries in (queues or {}).values()
        for queue in entries
    )


async def _check_beat() -> bool:
    client = redis.from_url(get_settings().redis_url, decode_responses=True)
    try:
        value = await client.get(BEAT_HEARTBEAT_KEY)
        if not value:
            return False
        timestamp = value.decode() if isinstance(value, bytes) else str(value)
        at = datetime.fromisoformat(timestamp)
        if at.tzinfo is None:
            return False
        age = (datetime.now(UTC) - at.astimezone(UTC)).total_seconds()
        return 0 <= age < BEAT_HEARTBEAT_TTL_SECONDS
    finally:
        await client.aclose()


async def _bounded(name: str, check: Callable[[], Awaitable[bool]]) -> ComponentStatus:
    """One component: ``ok``, ``fail``, or ``unknown`` when the budget expires."""
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            return "ok" if await check() else "fail"
    except TimeoutError:
        logger.warning("healthz check %s did not answer within the budget", name)
        return "unknown"
    except Exception:
        # Log only the component name: a check's exception can carry a host or
        # a credential, and this endpoint is public. Nothing goes in the body.
        logger.warning("healthz check %s failed", name)
        return "fail"


async def _compute(session: AsyncSession) -> dict[str, object]:
    # Both database checks share the request's one AsyncSession, which cannot
    # run two statements at once. Serialize that pair; the external checks stay
    # concurrent with each other and with the database checks.
    db_lock = asyncio.Lock()

    async def database() -> bool:
        async with db_lock:
            return await _check_database(session)

    async def migrations() -> bool:
        async with db_lock:
            return await _check_migrations(session)

    checks: list[tuple[str, Callable[[], Awaitable[bool]]]] = [
        ("database", database),
        ("migrations", migrations),
        ("redis", _check_redis),
        ("workers", _check_workers),
        ("walker", _check_walker),
        ("beat", _check_beat),
    ]

    async def gather_all() -> dict[str, ComponentStatus]:
        results = await asyncio.gather(*(_bounded(name, check) for name, check in checks))
        return dict(zip((name for name, _ in checks), results, strict=True))

    try:
        async with asyncio.timeout(TOTAL_TIMEOUT_SECONDS):
            component_status = await gather_all()
    except TimeoutError:
        logger.warning("healthz did not finish inside the total budget")
        component_status = {name: "unknown" for name, _ in checks}
    components: dict[str, ComponentStatus] = {"api": "ok", **component_status}
    status = "ok" if all(value == "ok" for value in components.values()) else "degraded"
    return {"status": status, "components": components}


class HealthzCache:
    """One answer per ``ttl`` seconds, however many monitors poll.

    Follows ``feature_switches.EffectiveSettingsCache``: a monotonic deadline,
    a generation counter so an invalidation during a check cannot leave a stale
    entry, and an explicit ``invalidate`` for tests and state changes.
    """

    def __init__(
        self, ttl: float = CACHE_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ttl = ttl
        self.clock = clock
        self._entry: tuple[float, dict[str, object]] | None = None
        self._generation = 0

    @property
    def generation(self) -> int:
        return self._generation

    def invalidate(self) -> None:
        self._generation += 1
        self._entry = None

    def get(self) -> dict[str, object] | None:
        entry = self._entry
        if entry is not None and self.clock() < entry[0]:
            return entry[1]
        return None

    def set(self, body: dict[str, object], generation: int) -> None:
        if generation == self._generation:
            self._entry = (self.clock() + self.ttl, body)


healthz_cache = HealthzCache()

#: Single-flight: callers arriving on a cold or expired cache share one check
#: run instead of each broadcasting to Celery. Rebuilt with the cache on
#: invalidation so a new event loop never reuses a lock bound to an old one.
_check_lock = asyncio.Lock()


def invalidate_healthz_cache() -> None:
    """Forget the cached answer, so the next request runs the checks again."""
    global _check_lock
    healthz_cache.invalidate()
    _check_lock = asyncio.Lock()


@router.get("")
@limiter.exempt
async def healthz(session: SessionDep) -> JSONResponse:
    """Whole-app health for monitoring. Public, bounded and cached for 10 s.

    The body carries only ``status`` and seven ``ok``/``fail``/``unknown``
    components. Container liveness keeps its own probe on ``/health/live``.
    """
    body = healthz_cache.get()
    if body is None:
        async with _check_lock:
            # A caller that waited for the lock may find the answer already
            # there; only the first one actually runs the checks.
            body = healthz_cache.get()
            if body is None:
                generation = healthz_cache.generation
                body = await _compute(session)
                healthz_cache.set(body, generation)
    status_code = 200 if body["status"] == "ok" else 503
    return JSONResponse(status_code=status_code, content=body)
