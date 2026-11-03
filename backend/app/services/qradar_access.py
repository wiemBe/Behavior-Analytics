"""Which QRadar APIs the token can reach, and what to restart when it can reach more (ACC-1).

`check_console_access` sends one cheapest read per entry of the access catalog
(`app/providers/access_catalog.py`) to one console, classifies each answer by its status code
alone, stores the latest result per (console, entry), and starts the syncs of every entry that
went from not reachable to reachable.

Read-only (repository rule 1): the probes are GETs with `Range: items=0-0` (or the endpoint's own
minimal form), plus one stored Ariel search that is cancelled unread. The Ariel probe goes through
the REST provider's permit pool like every other search (rule 2). A response body is never
decoded, stored or logged (rule 5): `QRadarRestProvider.probe_access` returns the status code and
drops the rest, and an exception is reduced to its class and status code here.

Fail closed (rule 8): an entry whose answer was not obtained is `unreachable` or `error`, never
`ok`. The one exception is an Ariel permit that did not come: nothing was sent, so nothing is
learned and the stored row is left as it was (reported as `deferred`).

Nothing is started for an entry that stays refused. A first check (no stored row) starts nothing
either: nothing was known to be blocked, and the collectors already run on their own cadence.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.models.monitoring import QRadarAccessResult, QRadarAccessRun
from app.providers.access_catalog import (
    ACCESS_CATALOG,
    CATALOG_BY_ID,
    SYNC_HEALTH,
    SYNC_LOG_SOURCES,
    SYNC_NAMES,
    SYNC_OFFENSES,
    SYNC_RULES,
    SYNC_VOLUME,
    AccessEntry,
)
from app.providers.base import CapabilityNotSupportedError, ProviderError, ProviderUnavailableError
from app.schemas.qradar_access import AccessEntryOut, ConsoleAccessOut, CoveredCallOut
from app.services.ariel_permit import ArielPermitUnavailable
from app.services.locks import CollectorAdvisoryLock

logger = logging.getLogger("app.services.qradar_access")

STATUS_OK = "ok"
STATUS_FORBIDDEN = "forbidden"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_NOT_FOUND = "not_found"
STATUS_UNREACHABLE = "unreachable"
STATUS_ERROR = "error"

#: The advisory-lock name of one console's check, so a beat run and a button press do not both
#: start the syncs.
LOCK_NAME = "qradar_access"
#: A run that started this long ago and never finished is treated as not running: its worker
#: died. Longer than the slowest check (about a minute plus the Ariel permit wait).
ACCESS_RUN_STALE_SECONDS = 600
#: One probe is given this long. The provider's own client timeout is a minute.
PROBE_TIMEOUT_SECONDS = 15.0
#: The Ariel probe also waits for a permit (`ARIEL_PERMIT_WAIT_SECONDS`) and may cancel; it is
#: given this much beyond that wait.
ARIEL_PROBE_EXTRA_SECONDS = 90.0
#: Probes in flight at once on one console.
PROBE_CONCURRENCY = 4
#: The entry probed first: when the console does not answer it, the rest are not attempted.
FIRST_ENTRY_ID = "system_about"


class AccessProbeProvider(Protocol):
    """The two reads the check needs; `QRadarRestProvider` has both."""

    async def probe_access(self, path: str, *, range_header: str | None = None) -> int: ...

    async def probe_ariel_access(self) -> int: ...


class SyncDispatcher(Protocol):
    """Starts one named sync for one console. True when it was started."""

    def start(self, sync: str, instance: QRadarInstance) -> bool: ...


@dataclass(frozen=True)
class ProbeOutcome:
    status: str
    http_status: int | None


@dataclass
class ConsoleCheck:
    """What one check of one console did."""

    instance_name: str
    #: False when another check of this console held the lock; nothing was sent.
    ran: bool = True
    checked_at: datetime | None = None
    #: Entry id -> outcome stored by this check.
    outcomes: dict[str, ProbeOutcome] = field(default_factory=dict)
    #: Entry ids whose answer was not obtained (an Ariel permit that did not come).
    deferred: list[str] = field(default_factory=list)
    #: Sync names this check started, in catalog order.
    syncs_started: list[str] = field(default_factory=list)


def classify_status(code: int) -> str:
    """The status class of an HTTP status code."""
    if 200 <= code < 300:
        return STATUS_OK
    if code == 401:
        return STATUS_UNAUTHORIZED
    if code == 403:
        return STATUS_FORBIDDEN
    if code == 404:
        return STATUS_NOT_FOUND
    return STATUS_ERROR


def classify_exception(exc: BaseException) -> ProbeOutcome | None:
    """The outcome of a probe that raised; None when nothing was learned (no permit).

    From the exception type and its status code alone: its message is never read.
    """
    if isinstance(exc, ArielPermitUnavailable):
        return None
    if isinstance(exc, TimeoutError):
        return ProbeOutcome(STATUS_UNREACHABLE, None)
    if isinstance(exc, ProviderError):
        code = exc.status_code
        if code is not None:
            return ProbeOutcome(classify_status(code), code)
        if isinstance(exc, ProviderUnavailableError):
            return ProbeOutcome(STATUS_UNREACHABLE, None)
    return ProbeOutcome(STATUS_ERROR, None)


async def _probe(
    provider: AccessProbeProvider, entry: AccessEntry, *, ariel_timeout: float
) -> ProbeOutcome | None:
    """One entry, one answer. Never raises for a refusal or a failure."""
    try:
        if entry.probe == "ariel":
            code = await asyncio.wait_for(provider.probe_ariel_access(), timeout=ariel_timeout)
        else:
            code = await asyncio.wait_for(
                provider.probe_access(entry.path, range_header=entry.range_header),
                timeout=PROBE_TIMEOUT_SECONDS,
            )
    except CapabilityNotSupportedError:
        return None
    except Exception as exc:
        outcome = classify_exception(exc)
        if outcome is None or outcome.status in (STATUS_UNREACHABLE, STATUS_ERROR):
            # The class only: an upstream message can carry request detail.
            logger.info(
                "access probe did not get an answer",
                extra={"entry": entry.id, "error_class": type(exc).__name__},
            )
        return outcome
    return ProbeOutcome(classify_status(code), code)


async def probe_entries(
    provider: AccessProbeProvider, *, ariel_timeout: float = PROBE_TIMEOUT_SECONDS + 90.0
) -> dict[str, ProbeOutcome | None]:
    """Probe every catalog entry of one console.

    The system entry goes first; a console that does not answer it is not asked the rest, which
    would only wait out one timeout per entry. Those entries are `unreachable`, not probed.
    """
    first = CATALOG_BY_ID[FIRST_ENTRY_ID]
    head = await _probe(provider, first, ariel_timeout=ariel_timeout)
    results: dict[str, ProbeOutcome | None] = {first.id: head}
    rest = [entry for entry in ACCESS_CATALOG if entry.id != first.id]
    if head is not None and head.status == STATUS_UNREACHABLE:
        for entry in rest:
            results[entry.id] = ProbeOutcome(STATUS_UNREACHABLE, None)
        return results
    gate = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def limited(entry: AccessEntry) -> tuple[str, ProbeOutcome | None]:
        async with gate:
            return entry.id, await _probe(provider, entry, ariel_timeout=ariel_timeout)

    for entry_id, outcome in await asyncio.gather(*(limited(entry) for entry in rest)):
        results[entry_id] = outcome
    return results


def syncs_to_start(
    previous: dict[str, str], outcomes: dict[str, ProbeOutcome | None]
) -> dict[str, list[str]]:
    """Entry id -> the syncs it asks for: only entries that were stored not `ok` and now are.

    An entry with no stored row, one that stays `ok`, one that stays refused and one that was
    not answered ask for nothing.
    """
    wanted: dict[str, list[str]] = {}
    for entry in ACCESS_CATALOG:
        outcome = outcomes.get(entry.id)
        before = previous.get(entry.id)
        if (
            outcome is not None
            and outcome.status == STATUS_OK
            and before is not None
            and before != STATUS_OK
        ):
            wanted[entry.id] = list(entry.syncs)
    return wanted


class CeleryDispatcher:
    """Starts the syncs through the worker queue, or busts the health cache in this process.

    A broker that refuses the queue is reported as not started, never raised: the beat tasks run
    on their own cadence and catch up.
    """

    def start(self, sync: str, instance: QRadarInstance) -> bool:
        if sync == SYNC_HEALTH:
            from app.services.qradar_health import HEALTH_CACHE

            # Per process: the API's copy expires within QRADAR_HEALTH_CACHE_TTL_SECONDS when
            # the worker ran the check.
            HEALTH_CACHE.clear()
            return True
        from app.workers import tasks

        queue = {
            SYNC_LOG_SOURCES: tasks.sync_log_sources_task,
            SYNC_RULES: tasks.sync_rule_inventory_task,
            SYNC_OFFENSES: tasks.collect_offenses_task,
            SYNC_VOLUME: tasks.collect_metrics_task,
        }.get(sync)
        if queue is None:
            return False
        try:
            queue.apply_async(args=[instance.name])
        except Exception as exc:
            logger.warning(
                "sync was not queued after access returned",
                extra={"sync": sync, "error_class": type(exc).__name__},
            )
            return False
        return True


async def check_console_access(
    session: AsyncSession,
    instance: QRadarInstance,
    provider: AccessProbeProvider,
    *,
    settings: Settings | None = None,
    dispatcher: SyncDispatcher | None = None,
    now: Callable[[], datetime] | None = None,
    probe: Callable[[AccessProbeProvider], Awaitable[dict[str, ProbeOutcome | None]]]
    | None = None,
) -> ConsoleCheck:
    """Check one console, store the results and start the syncs. The caller commits."""
    settings = settings or get_settings()
    dispatcher = dispatcher or CeleryDispatcher()
    report = ConsoleCheck(instance_name=instance.name)
    async with CollectorAdvisoryLock(session, settings, instance.id, LOCK_NAME) as acquired:
        if not acquired:
            report.ran = False
            return report
        if probe is not None:
            answers = await probe(provider)
        else:
            answers = await probe_entries(
                provider,
                ariel_timeout=settings.ariel_permit_wait_seconds + ARIEL_PROBE_EXTRA_SECONDS,
            )
        checked_at = (now or (lambda: datetime.now(UTC)))()
        report.checked_at = checked_at

        rows = {
            row.entry_id: row
            for row in (
                await session.scalars(
                    select(QRadarAccessResult).where(QRadarAccessResult.instance_id == instance.id)
                )
            ).all()
        }
        wanted = syncs_to_start({entry_id: row.status for entry_id, row in rows.items()}, answers)
        started: dict[str, bool] = {}
        for sync in SYNC_NAMES:
            if any(sync in names for names in wanted.values()):
                started[sync] = dispatcher.start(sync, instance)
        report.syncs_started = [sync for sync in SYNC_NAMES if started.get(sync)]

        for entry in ACCESS_CATALOG:
            outcome = answers.get(entry.id)
            if outcome is None:
                report.deferred.append(entry.id)
                continue
            report.outcomes[entry.id] = outcome
            mine = [sync for sync in wanted.get(entry.id, []) if started.get(sync)]
            row = rows.get(entry.id)
            if row is None:
                session.add(
                    QRadarAccessResult(
                        instance_id=instance.id,
                        entry_id=entry.id,
                        status=outcome.status,
                        http_status=outcome.http_status,
                        checked_at=checked_at,
                        previous_status=None,
                        syncs_started=mine,
                    )
                )
            else:
                row.previous_status = row.status
                row.status = outcome.status
                row.http_status = outcome.http_status
                row.checked_at = checked_at
                row.syncs_started = mine
        await session.flush()
    return report


def _is_running(run: QRadarAccessRun | None, now: datetime) -> bool:
    """A run is running until it finishes, unless it started more than the bound ago."""
    if run is None:
        return False
    if now - run.started_at > timedelta(seconds=ACCESS_RUN_STALE_SECONDS):
        return False
    return run.finished_at is None or run.finished_at < run.started_at


async def claim_run(
    session: AsyncSession, instance_id: uuid.UUID, *, now: datetime | None = None
) -> bool:
    """Mark one console's check as started; False when one is already running.

    One atomic statement (insert, or update only when the stored run is finished or stale), and it
    commits at once so that the API sees the run while it works. Beat, start-up and "check now"
    all claim here, so two of them never probe the same console together.
    """
    moment = now or datetime.now(UTC)
    stale_before = moment - timedelta(seconds=ACCESS_RUN_STALE_SECONDS)
    insert = pg_insert(QRadarAccessRun).values(
        instance_id=instance_id, started_at=moment, finished_at=None
    )
    statement = insert.on_conflict_do_update(
        index_elements=[QRadarAccessRun.instance_id],
        set_={"started_at": moment},
        where=or_(
            QRadarAccessRun.finished_at >= QRadarAccessRun.started_at,
            QRadarAccessRun.started_at < stale_before,
        ),
    ).returning(QRadarAccessRun.instance_id)
    claimed = (await session.execute(statement)).first() is not None
    await session.commit()
    return claimed


async def finish_run(
    session: AsyncSession, instance_id: uuid.UUID, *, now: datetime | None = None
) -> None:
    """Mark one console's check as finished (also after a failure), and commit."""
    moment = now or datetime.now(UTC)
    row = await session.get(QRadarAccessRun, instance_id)
    if row is not None:
        row.finished_at = moment
    await session.commit()


def refused_entries(rows: list[QRadarAccessResult]) -> dict[str, list[QRadarAccessResult]]:
    """The stored rows that are not `ok`, grouped by status, in catalog order."""
    order = {entry.id: index for index, entry in enumerate(ACCESS_CATALOG)}
    grouped: dict[str, list[QRadarAccessResult]] = {}
    for row in sorted(rows, key=lambda item: order.get(item.entry_id, len(order))):
        if row.status != STATUS_OK:
            grouped.setdefault(row.status, []).append(row)
    return grouped


def console_view(
    instance: QRadarInstance,
    rows: list[QRadarAccessResult],
    run: QRadarAccessRun | None = None,
    now: datetime | None = None,
) -> ConsoleAccessOut:
    """One console's stored results, joined to the catalog, in catalog order."""
    by_entry = {row.entry_id: row for row in rows}
    applicable = instance.provider_kind == "rest"
    latest = max((row.checked_at for row in rows), default=None)
    entries: list[AccessEntryOut] = []
    started: set[str] = set()
    for entry in ACCESS_CATALOG:
        row = by_entry.get(entry.id)
        mine = list(row.syncs_started) if row is not None and row.checked_at == latest else []
        started.update(mine)
        entries.append(
            AccessEntryOut(
                id=entry.id,
                title=entry.title,
                method=entry.method,
                path=entry.path,
                feeds=entry.feeds,
                used_by=list(entry.used_by),
                status=row.status if row is not None else None,
                http_status=row.http_status if row is not None else None,
                checked_at=row.checked_at if row is not None else None,
                previous_status=row.previous_status if row is not None else None,
                syncs_started=mine,
                required_capability=entry.required_capability,
                capability_verified=entry.capability_verified,
                capability_hint=entry.capability_hint,
                sources=list(entry.sources),
                covers=[
                    CoveredCallOut(method=c.method, path=c.path, reason=c.reason)
                    for c in entry.covers
                ],
            )
        )
    return ConsoleAccessOut(
        instance_id=instance.id,
        instance_name=instance.name,
        applicable=applicable,
        last_checked_at=latest,
        check_running=_is_running(run, now or datetime.now(UTC)),
        last_check_started_at=run.started_at if run is not None else None,
        last_check_finished_at=run.finished_at if run is not None else None,
        entries=entries,
        syncs_started=[sync for sync in SYNC_NAMES if sync in started],
    )


async def read_access(
    session: AsyncSession, instances: list[QRadarInstance], *, now: datetime | None = None
) -> list[ConsoleAccessOut]:
    """Every given console's stored results and run state: two queries, whatever the number."""
    if not instances:
        return []
    rows = (
        await session.scalars(
            select(QRadarAccessResult).where(
                QRadarAccessResult.instance_id.in_([i.id for i in instances])
            )
        )
    ).all()
    runs = {
        run.instance_id: run
        for run in (
            await session.scalars(
                select(QRadarAccessRun).where(
                    QRadarAccessRun.instance_id.in_([i.id for i in instances])
                )
            )
        ).all()
    }
    grouped: dict[uuid.UUID, list[QRadarAccessResult]] = {}
    for row in rows:
        grouped.setdefault(row.instance_id, []).append(row)
    return [
        console_view(instance, grouped.get(instance.id, []), runs.get(instance.id), now)
        for instance in instances
    ]
