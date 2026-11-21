"""Shared D-52 flag access, SQL predicates and transaction-owned source locks.

Callers resolve the rollout switch before applying these predicates or locks;
with the switch off they retain legacy behavior.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import ColumnElement, DateTime, cast, event, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import QueryableAttribute, Session

from app.core.config import Settings
from app.models.log_source import LogSource

if TYPE_CHECKING:
    from app.anomaly.baseline import BaselineBuilder

#: An open pause has no end: half-open overlap uses positive infinity for it.
_INFINITY = cast(literal("infinity"), DateTime(timezone=True))


def source_pause_enabled(settings: Settings) -> bool:
    """The single D-52 rollout flag, read from the resolved effective settings."""
    return settings.qradar_source_pause_enabled


def active_source(enabled: ColumnElement[bool]) -> ColumnElement[bool]:
    """The stored QRadar flag guard, placed beside a caller's monitoring filter."""
    return enabled.is_(True)


def target_filters(
    settings: Settings | None,
    monitoring_enabled: QueryableAttribute[bool],
    enabled: QueryableAttribute[bool],
) -> list[ColumnElement[bool]]:
    """The shared collection-target WHERE terms: monitored, and (switch on) not QRadar-disabled.

    Off (or no settings) is exactly the legacy single `monitoring_enabled` term.
    """
    terms: list[ColumnElement[bool]] = [monitoring_enabled.is_(True)]
    if settings is not None and source_pause_enabled(settings):
        terms.append(active_source(enabled.expression))
    return terms


def pause_overlaps_bucket(
    bucket_start: ColumnElement[datetime],
    bucket_end: ColumnElement[datetime],
    starts_at: ColumnElement[datetime],
    ends_at: ColumnElement[datetime],
) -> ColumnElement[bool]:
    """Half-open overlap of a pause span and a bucket.

    A bucket overlaps when it starts before the pause ends (an open pause runs
    to infinity) and ends after the pause starts. Exact boundary touches do not
    overlap.
    """
    return (bucket_start < func.coalesce(ends_at, _INFINITY)) & (bucket_end > starts_at)


async def lock_sources(session: AsyncSession, ids: list[UUID]) -> list[LogSource]:
    """Lock at most 100 sources in UUID order, refreshing after any inventory wait.

    NO KEY UPDATE permits child-insert FK KEY SHARE locks while excluding
    inventory updates and other source-lock holders.
    Ownership follows the transaction: rollback invalidates it and a committed
    savepoint transfers it to its parent. Outer locks are reused inside savepoints.
    No helper commits; callers own the transaction and must do no network work.
    """
    ordered = sorted(set(ids))
    if len(ordered) > 100:
        raise ValueError("source lock batch exceeds 100 ids")
    if "source_pause_locks" not in session.info:
        session.info["source_pause_locks"] = {}

        def after_commit(sync_session: Session) -> None:
            # A released savepoint retains its row locks in the parent transaction.
            # A rollback leaves its owner inactive instead, so it is purged below.
            held = sync_session.info["source_pause_locks"]
            nested = sync_session.get_nested_transaction()
            if nested is None:
                held.clear()
            else:
                for key, (owner, source) in list(held.items()):
                    if owner is nested:
                        held[key] = (nested.parent, source)

        event.listen(session.sync_session, "after_commit", after_commit)
    held = session.info["source_pause_locks"]
    for key in ordered:
        entry = held.get(key)
        if entry is not None and not entry[0].is_active:
            del held[key]
    missing = [key for key in ordered if key not in held]
    if missing:
        rows = await session.scalars(
            select(LogSource)
            .where(LogSource.id.in_(missing))
            .order_by(LogSource.id)
            .with_for_update(key_share=True)
            .execution_options(populate_existing=True)
        )
        owner = (
            session.sync_session.get_nested_transaction() or session.sync_session.get_transaction()
        )
        assert owner is not None
        for source in rows:
            held[source.id] = (owner, source)
    return [held[key][1] for key in ordered if key in held]


async def rebuild_baselines_locked(
    session: AsyncSession,
    builder: BaselineBuilder,
    source_ids: Sequence[UUID],
    *,
    now: datetime,
    source_pause_policy: bool,
) -> int:
    """Rebuild under source locks, committing each ordered batch of at most 100.

    This entry owns the transaction; callers must have no pending collection or
    network work. Observations are read only after the refreshed source lock,
    so an inventory disable that wins the lock is visible before training.
    """
    ordered = sorted(set(source_ids))
    cells = 0
    for offset in range(0, len(ordered), 100):
        sources = await lock_sources(session, ordered[offset : offset + 100])
        if source_pause_policy:
            sources = [source for source in sources if source.enabled is True]
        cells += await builder.rebuild_many(sources, now=now)
        await session.commit()
    return cells
