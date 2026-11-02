"""The current-load document with QRadar's own health beside it (ARL-3 on ARL-1).

``load_current`` (the load the platform puts on QRadar now) keeps its shape. This module adds
the ``qradar`` part: for each enabled console, ``read_qradar_health`` (reachability, managed
hosts, EPS, license, the Ariel search list) merged with what the platform's own searches saw
(``ariel_search_run``).

``qradar`` is a list, one entry per enabled console in creation order, and each entry names its
console (``instance_name``, ``instance_id``). A JSON object keyed by name would take whatever
characters an operator typed into a console name as keys, would have no order, and would change
shape between one console and several.

Cost. The database side is fixed: one query for the enabled consoles and one grouped query over
the last hour of ``ariel_search_run`` for all of them, however many consoles there are. The
QRadar side is bounded by the health cache (``QRADAR_HEALTH_CACHE_TTL_SECONDS``): a poll inside
the TTL sends QRadar nothing. The connection is released before QRadar is asked, so a slow
console never holds a pooled database connection.

A console whose health cannot be read never fails the endpoint: its fields carry their states.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.ariel_search import ArielSearchRun
from app.models.instance import QRadarInstance
from app.services import ariel_search_log as log
from app.services.qradar_health import (
    ARIEL_RECENT_WINDOW_MINUTES,
    ArielRunsSnapshot,
    read_qradar_health,
    unreadable_health,
)

logger = logging.getLogger("app.services.ariel_load_current")


def _part_unavailable(what: str) -> dict[str, Any]:
    return {"available": False, "reason": f"{what} could not be read"}


async def _enabled_consoles(session: AsyncSession) -> list[QRadarInstance]:
    rows = (
        await session.scalars(
            select(QRadarInstance)
            .where(QRadarInstance.enabled.is_(True))
            .order_by(QRadarInstance.created_at, QRadarInstance.name)
        )
    ).all()
    return list(rows)


async def read_ariel_runs(
    session: AsyncSession, console_ids: list[uuid.UUID], *, as_of: datetime
) -> dict[uuid.UUID, ArielRunsSnapshot]:
    """The platform's own search record for each console, in one grouped query.

    Over the last 15 minutes: searches sent to QRadar (a REFUSED row never reached it), how
    many found Ariel unavailable, the newest finish of a COMPLETED search and of an
    unavailable one. Beside them, the searches still in flight that are not stale (a stale
    row's worker died; the load view reports those apart). A console with no row gets a
    snapshot of zeros: the query ran and found none.
    """
    if not console_ids:
        return {}
    window_start = as_of - timedelta(minutes=ARIEL_RECENT_WINDOW_MINUTES)
    alive_since = as_of - timedelta(seconds=log.stale_after_seconds())
    in_window = ArielSearchRun.submitted_at >= window_start
    read_bound = as_of - timedelta(seconds=log.CURRENT_READ_WINDOW_SECONDS)
    reached = in_window & (ArielSearchRun.outcome != log.OUTCOME_REFUSED)
    unavailable = in_window & (ArielSearchRun.outcome == log.OUTCOME_PROVIDER_UNAVAILABLE)
    completed = in_window & (ArielSearchRun.outcome == log.OUTCOME_COMPLETED)
    rows = (
        await session.execute(
            select(
                ArielSearchRun.instance_id,
                func.count().filter(reached),
                func.count().filter(unavailable),
                func.max(ArielSearchRun.finished_at).filter(completed),
                func.max(
                    func.coalesce(ArielSearchRun.finished_at, ArielSearchRun.submitted_at)
                ).filter(unavailable),
                func.count().filter(
                    (ArielSearchRun.outcome == log.OUTCOME_IN_FLIGHT)
                    & (ArielSearchRun.submitted_at >= alive_since)
                ),
            )
            .where(
                ArielSearchRun.instance_id.in_(console_ids),
                ArielSearchRun.submitted_at >= read_bound,
            )
            .group_by(ArielSearchRun.instance_id)
        )
    ).all()
    found = {
        row[0]: ArielRunsSnapshot(
            window_minutes=ARIEL_RECENT_WINDOW_MINUTES,
            searches=row[1],
            unavailable=row[2],
            last_success_at=row[3],
            last_unavailable_at=row[4],
            in_flight=row[5],
        )
        for row in rows
    }
    empty = ArielRunsSnapshot(
        window_minutes=ARIEL_RECENT_WINDOW_MINUTES, searches=0, unavailable=0, in_flight=0
    )
    return {console_id: found.get(console_id, empty) for console_id in console_ids}


async def _one_console(
    instance: QRadarInstance,
    runs: ArielRunsSnapshot | None,
    *,
    runs_unreadable: bool,
    settings: Settings,
) -> dict[str, Any]:
    try:
        health = await read_qradar_health(
            instance,
            settings=settings,
            ariel_runs=runs,
            ariel_runs_unreadable=runs_unreadable,
        )
    except Exception:  # a health read never fails the endpoint
        logger.warning("qradar health read raised", exc_info=False)
        health = unreadable_health(
            instance,
            at=datetime.now(UTC),
            ttl=settings.qradar_health_cache_ttl_seconds,
        )
    return health.model_dump(mode="json")


async def load_qradar(session: AsyncSession, *, as_of: datetime) -> dict[str, Any]:
    """The ``qradar`` part: one entry per enabled console.

    Reads the database (two statements), then lets go of the connection before asking QRadar.
    """
    settings = get_settings()
    try:
        consoles = await _enabled_consoles(session)
    except Exception:
        return _part_unavailable("the registered consoles")
    runs: dict[uuid.UUID, ArielRunsSnapshot] | None
    try:
        runs = await read_ariel_runs(session, [c.id for c in consoles], as_of=as_of)
    except Exception:
        runs = None
    # Detach what the health reads need, then end this read-only transaction: the QRadar
    # reads below can take seconds and must not hold a pooled connection while they wait.
    for console in consoles:
        if console in session:
            session.expunge(console)
    await session.rollback()
    entries = await asyncio.gather(
        *(
            _one_console(
                console,
                runs.get(console.id) if runs is not None else None,
                runs_unreadable=runs is None,
                settings=settings,
            )
            for console in consoles
        )
    )
    return {"available": True, "consoles": list(entries)}


async def load_current_with_health(
    session: AsyncSession, *, now: datetime | None = None
) -> dict[str, Any]:
    """``load_current`` plus the ``qradar`` part; the existing parts keep their shape.

    Five database statements in all, whatever the number of consoles: the three of
    ``load_current``, the console list and the grouped search record.
    """
    as_of = now or datetime.now(UTC)
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    current = await log.load_current(session, now=as_of)
    current["qradar"] = await load_qradar(session, as_of=as_of)
    return current
