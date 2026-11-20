"""JDBC run/lease lifecycle for one console (design §8 Track A).

Request, claim, walk outside every lock, finish only the current attempt: A, B
and C are separate transactions; only fixed codes and counts leave here. No
upstream value, name or exception text reaches state, results or logs.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID, uuid4

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import Table, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.sql import Insert, Select

from app.jdbc.dto import JdbcConfigWalk, JdbcWalkState
from app.models.instance import QRadarInstance
from app.models.jdbc import JdbcSyncState
from app.providers.base import QRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.services import jdbc_sync
from app.services.jdbc_sync import JdbcApplySummary

logger = logging.getLogger("app.jdbc.jobs")
SYNC = cast(Table, JdbcSyncState.__table__)

STATE_QUEUED = "QUEUED"
STATE_RUNNING = "RUNNING"
STATE_FAILED = "FAILED"
STATE_UNSUPPORTED = "UNSUPPORTED"

FAILURE_PROVIDER_UNSUPPORTED = "provider_unsupported"
FAILURE_WORKER_LOST = "worker_lost"
FAILURE_SOFT_TIMEOUT = "soft_timeout"
FAILURE_INTERNAL_ERROR = "internal_error"

_NULLABLE = (
    "queued_at", "started_at", "finished_at", "last_complete_at", "lease_expires_at",
    "attempt_id", "response_bytes", "failed_page", "failure_code", "fallback_resume_id",
)
_MAKER = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class JdbcRunResult:
    """Fixed state/codes and counts only; never an upstream value."""

    state: str
    failure_code: str | None = None
    pages: int = 0
    rows: int = 0
    failures: int = 0
    elapsed_ms: int = 0
    failed_page: int | None = None
    apply: JdbcApplySummary | None = None


def _counts(walk: JdbcConfigWalk) -> tuple[int, int, int, int, int | None, str | None]:
    return (
        walk.pages_read,
        walk.rows_read,
        walk.pages_failed + sum(walk.rejected_rows.values()),
        int(walk.elapsed_seconds * 1000),
        walk.failed_page,
        walk.failure_code.value if walk.failure_code else None,
    )


def _emit(result: JdbcRunResult) -> JdbcRunResult:
    """The one structured log line per run: fixed codes and counts only."""
    extra = {
        "state": result.state, "failure_code": result.failure_code,
        "pages": result.pages, "rows": result.rows, "failures": result.failures,
        "elapsed_ms": result.elapsed_ms, "failed_page": result.failed_page,
    }
    if result.apply:
        extra.update(
            written=result.apply.written, unmatched=result.apply.unmatched,
            cleared=result.apply.cleared,
        )
    logger.info("jdbc collection run", extra=extra)
    return result


def _ensure_row(instance_id: UUID) -> Insert:
    """Every column is set on insert: the table has no server-side defaults."""
    values: dict[str, Any] = {
        "instance_id": instance_id, "state": "IDLE", "pages": 0,
        "rows": 0, "failures": 0, "elapsed_ms": 0,
    }
    values.update(dict.fromkeys(_NULLABLE))
    return pg_insert(SYNC).values(**values).on_conflict_do_nothing(index_elements=["instance_id"])


def _locked(instance_id: UUID) -> Select[Any]:
    return (
        select(SYNC.c.state, SYNC.c.lease_expires_at)
        .where(SYNC.c.instance_id == instance_id)
        .with_for_update()
    )


def _live(state: str, lease: datetime | None, now: datetime) -> bool:
    return state == STATE_RUNNING and lease is not None and lease > now


async def request_jdbc_run(session: AsyncSession, instance_id: UUID, *, now: datetime) -> bool:
    """Queue one pass unless a queued request or a live lease already owns it."""
    await session.execute(_ensure_row(instance_id))
    row = (await session.execute(_locked(instance_id))).one()
    if row.state == STATE_QUEUED or _live(row.state, row.lease_expires_at, now):
        return False
    await session.execute(
        update(SYNC)
        .where(SYNC.c.instance_id == instance_id)
        .values(state=STATE_QUEUED, queued_at=now)
    )
    return True


async def request_and_enqueue_jdbc_run(
    session: AsyncSession,
    instance_id: UUID,
    *,
    now: datetime,
    enqueue: Callable[[UUID], None],
) -> bool:
    """Queue one pass and dispatch its task; the manual refresh (J1n) uses this too.

    Returns whether the row was newly queued. A live lease or an existing queued
    request coalesces: no task is sent. The caller guards the enqueue against a
    broker failure; the QUEUED row still commits, and the next beat tick claims
    it, because ``claim_jdbc_run`` accepts QUEUED.
    """
    if not await request_jdbc_run(session, instance_id, now=now):
        return False
    enqueue(instance_id)
    return True


async def claim_jdbc_run(
    session: AsyncSession, instance_id: UUID, *, now: datetime, lease_seconds: float
) -> UUID | None:
    """Claim one attempt under a fresh lease, reaping an expired worker first."""
    await session.execute(_ensure_row(instance_id))
    row = (await session.execute(_locked(instance_id))).one()
    if _live(row.state, row.lease_expires_at, now):
        return None
    if row.state == STATE_RUNNING:
        # A hard-killed worker left an expired lease: record its lost attempt
        # before the retry claim, in this same transaction.
        await session.execute(
            update(SYNC)
            .where(SYNC.c.instance_id == instance_id)
            .values(state=STATE_FAILED, failure_code=FAILURE_WORKER_LOST, finished_at=now)
        )
    attempt_id = uuid4()
    await session.execute(
        update(SYNC)
        .where(SYNC.c.instance_id == instance_id)
        .values(
            state=STATE_RUNNING, attempt_id=attempt_id, started_at=now, finished_at=None,
            pages=0, rows=0, failures=0, elapsed_ms=0,
            response_bytes=None, failed_page=None, failure_code=None,
            lease_expires_at=now + timedelta(seconds=lease_seconds),
        )
    )
    return attempt_id


async def _still_current(session: AsyncSession, instance_id: UUID, attempt_id: UUID) -> bool:
    """Lock the row before B's DB work and compare the attempt; never spans the walk."""
    stmt = select(SYNC.c.attempt_id).where(SYNC.c.instance_id == instance_id).with_for_update()
    return await session.scalar(stmt) == attempt_id


async def _finish(
    session: AsyncSession, instance_id: UUID, attempt_id: UUID, walk: JdbcConfigWalk, now: datetime
) -> None:
    pages, rows, failures, elapsed_ms, failed_page, code = _counts(walk)
    values: dict[str, Any] = {
        "state": walk.state.value, "pages": pages, "rows": rows, "failures": failures,
        "elapsed_ms": elapsed_ms, "failed_page": failed_page, "failure_code": code,
        "finished_at": now, "lease_expires_at": None,
    }
    if walk.state is JdbcWalkState.COMPLETE:
        values["last_complete_at"] = now
    await session.execute(
        update(SYNC)
        .where(SYNC.c.instance_id == instance_id, SYNC.c.attempt_id == attempt_id)
        .values(**values)
    )


async def _commit_state(
    maker: _MAKER, instance_id: UUID, now: datetime, *, state: str, code: str,
    attempt_id: UUID | None = None,
) -> None:
    """A fresh, committed transaction; fixed code only, never exception text."""
    where = SYNC.c.instance_id == instance_id
    if attempt_id is not None:
        where = where & (SYNC.c.attempt_id == attempt_id)
    values: dict[str, Any] = {
        "state": state, "failure_code": code, "finished_at": now, "lease_expires_at": None,
    }
    async with maker() as session:
        if attempt_id is None:
            await session.execute(_ensure_row(instance_id))
        await session.execute(update(SYNC).where(where).values(**values))
        await session.commit()


def _supports_walk(provider: QRadarProvider) -> bool:
    return isinstance(provider, QRadarRestProvider) and hasattr(provider, "walk_jdbc_configs")


async def run_jdbc_collection(
    sessionmaker: _MAKER,
    instance: QRadarInstance,
    provider: QRadarProvider,
    *,
    clock: Callable[[], datetime],
    lease_seconds: float,
    walk_kwargs: Mapping[str, Any],
) -> JdbcRunResult:
    """Walk one console outside any lock, then finish only the current attempt."""
    instance_id = instance.id
    if not _supports_walk(provider):
        await _commit_state(
            sessionmaker, instance_id, clock(), state=STATE_UNSUPPORTED,
            code=FAILURE_PROVIDER_UNSUPPORTED,
        )
        return _emit(
            JdbcRunResult(state=STATE_UNSUPPORTED, failure_code=FAILURE_PROVIDER_UNSUPPORTED)
        )

    async with sessionmaker() as claim_session:
        attempt_id = await claim_jdbc_run(
            claim_session, instance_id, now=clock(), lease_seconds=lease_seconds
        )
        await claim_session.commit()
    if attempt_id is None:
        return _emit(JdbcRunResult(state="coalesced"))

    try:
        walk = await provider.walk_jdbc_configs(**walk_kwargs)
        now = clock()
        async with sessionmaker() as apply_session:
            if not await _still_current(apply_session, instance_id, attempt_id):
                await apply_session.rollback()
                return _emit(JdbcRunResult(state="superseded"))
            summary = await jdbc_sync.apply_jdbc_walk(apply_session, instance_id, walk, now=now)
            await _finish(apply_session, instance_id, attempt_id, walk, now)
            await apply_session.commit()
    except Exception as exc:
        code = (
            FAILURE_SOFT_TIMEOUT
            if isinstance(exc, SoftTimeLimitExceeded)
            else FAILURE_INTERNAL_ERROR
        )
        await _commit_state(
            sessionmaker, instance_id, clock(), state=STATE_FAILED, code=code, attempt_id=attempt_id
        )
        _emit(JdbcRunResult(state=STATE_FAILED, failure_code=code))
        raise

    pages, rows, failures, elapsed_ms, failed_page, walk_code = _counts(walk)
    return _emit(
        JdbcRunResult(
            state=walk.state.value, failure_code=walk_code, pages=pages, rows=rows,
            failures=failures, elapsed_ms=elapsed_ms, failed_page=failed_page, apply=summary,
        )
    )
