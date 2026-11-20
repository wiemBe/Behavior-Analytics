"""Property discovery and durable background snapshots.

Editor discovery uses database snapshots and never gates validation.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.property_watch.reference import PW_NORMALIZED_FIELD_ALLOWLIST
from app.providers.base import QRadarProvider
from app.providers.dto import CustomPropertyDTO
from app.schemas.property_catalog import PropertyCatalogItemOut, PropertyCatalogOut
from app.schemas.property_watch import PropertyOptionOut, PropertyOptionsOut

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.property_catalog import PropertyCatalogState

logger = logging.getLogger(__name__)

_UNAVAILABLE = (
    "{error}: the property list could not be read from QRadar. Property names can still be typed."
)


@dataclass(frozen=True)
class TypedCatalogItem:
    name: str
    property_type: str | None
    name_allowed: bool
    #: The requested types that define this property, sorted. Empty: none does.
    defined_on_type_ids: tuple[int, ...]


@dataclass(frozen=True)
class TypedCatalog:
    status: str
    fetched_at: datetime | None
    properties: list[TypedCatalogItem]
    detail: str | None


class PropertyCatalogService:
    def __init__(
        self,
        *,
        ttl_seconds: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._ttl = timedelta(seconds=ttl_seconds)
        self._clock = clock
        self._cache: dict[object, tuple[datetime, list[CustomPropertyDTO]]] = {}
        self._locks: dict[object, asyncio.Lock] = {}

    def _fresh(self, instance_id: object) -> tuple[datetime, list[CustomPropertyDTO]] | None:
        entry = self._cache.get(instance_id)
        if entry is not None and self._clock() - entry[0] < self._ttl:
            return entry
        return None

    async def _load(
        self,
        instance_id: object,
        provider_factory: Callable[[], QRadarProvider],
        refresh: bool,
    ) -> tuple[tuple[datetime, list[CustomPropertyDTO]] | None, str | None]:
        """The cached entry, or `(None, error class name)` when the console read failed.

        Every failure degrades; none leaks. Only the class name travels on.
        """
        lock = self._locks.setdefault(instance_id, asyncio.Lock())
        async with lock:
            entry = None if refresh else self._fresh(instance_id)
            if entry is None:
                try:
                    entry = await self._fetch(instance_id, provider_factory)
                except Exception as exc:  # every failure degrades; none leaks
                    logger.warning(
                        "property catalog unavailable",
                        extra={"instance_id": str(instance_id), "error_class": type(exc).__name__},
                    )
                    return None, type(exc).__name__
        return entry, None

    async def catalog(
        self,
        *,
        instance_id: object,
        provider_factory: Callable[[], QRadarProvider],
        source_type_id: int | None,
        refresh: bool = False,
    ) -> PropertyCatalogOut:
        """The catalog for one log source's type (`None`: type unknown).

        The provider is built, and closed, only on a cache miss.
        """
        entry, error = await self._load(instance_id, provider_factory, refresh)
        if entry is None:
            return PropertyCatalogOut(
                status="UNAVAILABLE",
                filtered_by_source_type=False,
                properties=[],
                detail=_UNAVAILABLE.format(error=error),
            )
        fetched_at, properties = entry
        return PropertyCatalogOut(
            status="AVAILABLE",
            fetched_at=fetched_at,
            filtered_by_source_type=source_type_id is not None,
            properties=_for_source_type(properties, source_type_id),
        )

    async def catalog_for_types(
        self,
        *,
        instance_id: object,
        provider_factory: Callable[[], QRadarProvider],
        source_type_ids: Sequence[int],
        refresh: bool = False,
    ) -> TypedCatalog:
        """The catalog filtered by many log-source types (a group or a type scope).

        Every property comes back with the scope's types that define it; a property none of
        them defines is still listed with an empty set, because the catalog is a suggestion
        and never a gate. Same cache, same fixed failure sentence as `catalog`.
        """
        entry, error = await self._load(instance_id, provider_factory, refresh)
        if entry is None:
            return TypedCatalog(
                status="UNAVAILABLE",
                fetched_at=None,
                properties=[],
                detail=_UNAVAILABLE.format(error=error),
            )
        fetched_at, properties = entry
        return TypedCatalog(
            status="AVAILABLE",
            fetched_at=fetched_at,
            properties=_for_source_types(properties, source_type_ids),
            detail=None,
        )

    async def _fetch(
        self, instance_id: object, provider_factory: Callable[[], QRadarProvider]
    ) -> tuple[datetime, list[CustomPropertyDTO]]:
        provider = provider_factory()
        try:
            properties = await provider.list_custom_properties()
        finally:
            await provider.aclose()
        entry = (self._clock(), properties)
        self._cache[instance_id] = entry
        return entry


def _for_source_type(
    properties: list[CustomPropertyDTO], source_type_id: int | None
) -> list[PropertyCatalogItemOut]:
    items = [
        PropertyCatalogItemOut(
            name=p.name,
            property_type=p.property_type,
            applies_to_source_type=(
                None if source_type_id is None or not p.applicability_complete
                else source_type_id in p.log_source_type_ids
            ),
            name_allowed=p.name_allowed,
        )
        for p in properties
    ]
    items.sort(key=lambda i: (i.applies_to_source_type is not True, i.name.lower(), i.name))
    return items


def _for_source_types(
    properties: list[CustomPropertyDTO], source_type_ids: Sequence[int]
) -> list[TypedCatalogItem]:
    wanted = sorted(set(source_type_ids))
    items = [
        TypedCatalogItem(
            name=p.name,
            property_type=p.property_type,
            name_allowed=p.name_allowed,
            defined_on_type_ids=tuple(t for t in wanted if t in p.log_source_type_ids),
        )
        for p in properties
    ]
    items.sort(key=lambda i: (not i.defined_on_type_ids, i.name.lower(), i.name))
    return items


@dataclass(frozen=True)
class CatalogNameCheck:
    status: str
    as_of: datetime | None
    present: dict[str, bool | None]

    @property
    def readable(self) -> bool:
        return self.status in {"COMPLETE", "STALE", "REFRESHING"} and self.as_of is not None


async def stored_property_names(
    session: AsyncSession, *, instance_id: uuid.UUID, names: Sequence[str],
    now: datetime, refresh_seconds: int,
) -> CatalogNameCheck:
    """Check exact names and snapshot state in one MVCC-consistent statement.

    FIX-72 retains snapshots without age expiry. STALE means a refresh interval
    has elapsed, not that the names expired. Failed/partial refreshes cannot
    authorize searches, even when they retained an older complete list.
    """
    from sqlalchemy import and_, select

    from app.models.property_catalog import PropertyCatalogEntry as Entry
    from app.models.property_catalog import PropertyCatalogState as State

    rows = (await session.execute(select(State, Entry.name).outerjoin(Entry, and_(
        Entry.instance_id == State.instance_id, Entry.name.in_(names),
    )).where(State.instance_id == instance_id).execution_options(populate_existing=True))).all()
    state = rows[0][0] if rows else None
    as_of = state.as_of if state else None
    status = state.status if state else "NEVER"
    readable = bool(
        as_of and state and state.applicability_complete and not state.failure
        and status in {"COMPLETE", "REFRESHING"}
    )
    if (readable and status == "COMPLETE" and as_of is not None
            and (now - as_of).total_seconds() >= refresh_seconds):
        status = "STALE"
    elif not readable and status in {"COMPLETE", "REFRESHING"}:
        status = "NEVER" if as_of is None else "PARTIAL"
    found = {row[1] for row in rows if row[1] is not None}
    return CatalogNameCheck(status, as_of, {
        name: (name in found if readable else None) for name in names
    })


async def stored_catalog(
    session: AsyncSession, *, instance_id: uuid.UUID, source_type_id: int | None,
    q: str = "", limit: int = 50, refresh: bool = False,
) -> PropertyCatalogOut:
    """Editor reads are database-only, including empty and failed states."""
    from sqlalchemy import case, func, or_, select, true
    from sqlalchemy.orm import aliased

    from app.models.property_catalog import PropertyCatalogEntry as Entry
    from app.models.property_catalog import PropertyCatalogState as State

    if refresh:
        await queue_catalog_refresh(session, instance_id)
    query = select(Entry).where(Entry.instance_id == instance_id)
    if q:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(
            Entry.name.ilike(needle, escape="\\"), Entry.identifier.ilike(needle, escape="\\")
        ))
    if source_type_id is not None:
        query = query.order_by(case((
            State.applicability_complete.is_(True) & Entry.type_ids.contains([source_type_id]), 0
        ), else_=1))
    page = query.order_by(
        func.lower(Entry.name), Entry.name, Entry.qradar_id
    ).limit(min(200, max(1, limit))).correlate(State).lateral()
    item = aliased(Entry, page)
    # One statement gives state and rows the same MVCC snapshot during a swap.
    result = (await session.execute(select(State, item).outerjoin(
        item, true()
    ).where(State.instance_id == instance_id).execution_options(populate_existing=True))).all()
    state = result[0][0] if result else None
    rows = [row[1] for row in result if row[1] is not None]
    known = state is not None and state.applicability_complete and source_type_id is not None
    return PropertyCatalogOut(
        status="AVAILABLE" if state and state.as_of else "UNAVAILABLE",
        fetched_at=state.as_of if state else None,
        as_of=state.as_of if state else None,
        search_limited=True,
        catalog_status=state.status if state else "NEVER",
        applicability_complete=bool(state and state.applicability_complete),
        filtered_by_source_type=bool(known),
        properties=[PropertyCatalogItemOut(
            name=row.name, property_type=row.property_type, name_allowed=row.name_allowed,
            applies_to_source_type=(source_type_id in row.type_ids if known else None),
        ) for row in rows],
        last_started_at=state.last_started_at if state else None,
        last_completed_at=state.last_completed_at if state else None,
        property_count=state.property_count if state else None,
        expression_count=state.expression_count if state else None,
        duration_seconds=state.duration_seconds if state else None,
        failure=state.failure if state else None,
        refresh_seconds=get_settings().qradar_property_catalog_refresh_seconds,
    )


async def stored_watch_options(
    session: AsyncSession, *, instance_id: uuid.UUID, source_type_ids: Sequence[int],
    q: str = "", limit: int = 200, refresh: bool = False,
) -> PropertyOptionsOut:
    """One bounded snapshot statement; applicability is unknown after an incomplete walk."""
    from sqlalchemy import case, func, or_, select, true
    from sqlalchemy.orm import aliased

    from app.models.property_catalog import PropertyCatalogEntry as Entry
    from app.models.property_catalog import PropertyCatalogState as State

    if refresh:
        await queue_catalog_refresh(session, instance_id)
    wanted = sorted(set(source_type_ids))
    limit = min(200, max(1, limit))
    query = select(Entry).where(Entry.instance_id == instance_id)
    if q:
        needle = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        query = query.where(or_(
            Entry.name.ilike(needle, escape="\\"), Entry.identifier.ilike(needle, escape="\\")
        ))
    page = query.order_by(case((
        State.applicability_complete.is_(True) & Entry.type_ids.overlap(wanted), 0
    ), else_=1), func.lower(Entry.name), Entry.name, Entry.qradar_id).limit(limit).correlate(
        State
    ).lateral()
    item = aliased(Entry, page)
    result = (await session.execute(select(State, item).outerjoin(item, true()).where(
        State.instance_id == instance_id
    ).execution_options(populate_existing=True))).all()
    state = result[0][0] if result else None
    rows = [row[1] for row in result if row[1] is not None]
    known = bool(state and state.applicability_complete)
    catalog_status = state.status if state else "NEVER"
    if (state and state.as_of and catalog_status == "COMPLETE"
            and (datetime.now(UTC) - state.as_of).total_seconds()
            >= get_settings().qradar_property_catalog_refresh_seconds):
        catalog_status = "STALE"
    return PropertyOptionsOut(
        status="AVAILABLE" if state and state.as_of else "UNAVAILABLE",
        fetched_at=state.as_of if state else None, catalog_status=catalog_status,
        applicability_complete=known, limit_reached=len(rows) == limit,
        scope_type_ids=wanted, normalized_fields=list(PW_NORMALIZED_FIELD_ALLOWLIST),
        properties=[PropertyOptionOut(
            name=row.name, property_type=row.property_type, name_allowed=row.name_allowed,
            defined_on_type_ids=[t for t in wanted if t in row.type_ids] if known else [],
            covers_scope=known and bool(wanted) and set(wanted) <= set(row.type_ids),
        ) for row in rows],
    )


async def queue_catalog_refresh(
    session: AsyncSession, instance_id: uuid.UUID
) -> PropertyCatalogState:
    """Claim a bounded queue lease across API workers before publishing."""
    from sqlalchemy import or_, update
    from sqlalchemy.dialects.postgresql import insert

    from app.models.property_catalog import PropertyCatalogState as State
    from app.workers.tasks import refresh_property_catalog

    now = datetime.now(UTC)
    await session.execute(insert(State).values(
        instance_id=instance_id, status="NEVER", applicability_complete=False,
    ).on_conflict_do_nothing())
    cutoff = now - timedelta(
        seconds=get_settings().qradar_property_catalog_walk_timeout_seconds + 60
    )
    claimed = await session.scalar(update(State).where(
        State.instance_id == instance_id,
        or_(State.status != "REFRESHING", State.last_started_at < cutoff),
    ).values(status="REFRESHING", last_started_at=now).returning(State.instance_id))
    await session.commit()
    if claimed is not None:
        try:
            refresh_property_catalog.delay(str(instance_id))
        except Exception:
            await session.execute(update(State).where(State.instance_id == instance_id).values(
                status="FAILED", failure={
                    "kind": "upstream", "endpoint": "queue", "failure_class": "QueueUnavailable",
                },
            ))
            await session.commit()
    state = await session.get(State, instance_id, populate_existing=True)
    assert state is not None
    return state


async def refresh_catalog_snapshot(
    session: AsyncSession, instance_id: uuid.UUID,
    provider_factory: Callable[[], QRadarProvider],
) -> bool:
    """One locked refresh. Atomically publish complete snapshots only.

    On the first refresh only, complete names may seed an unknown-applicability
    list. A later failed walk never changes an existing snapshot.
    """
    import time

    from billiard.exceptions import SoftTimeLimitExceeded
    from sqlalchemy import delete, insert
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from app.models.property_catalog import PropertyCatalogEntry as Entry
    from app.models.property_catalog import PropertyCatalogState as State
    from app.providers.dto import CatalogFailureDTO, PropertyCatalogWalkDTO
    from app.services.locks import CollectorAdvisoryLock

    settings = get_settings()
    lock = CollectorAdvisoryLock(session, settings, instance_id, "property_catalog")
    async with lock as acquired:
        if not acquired:
            return False
        started = time.monotonic()
        await session.execute(pg_insert(State).values(
            instance_id=instance_id, status="NEVER", applicability_complete=False,
        ).on_conflict_do_nothing())
        state = await session.get(State, instance_id, populate_existing=True)
        assert state is not None
        state.status = "REFRESHING"
        state.last_started_at = datetime.now(UTC)
        await session.commit()
        provider = None
        result = PropertyCatalogWalkDTO(names_complete=False, applicability_complete=False)
        try:
            async with asyncio.timeout(settings.qradar_property_catalog_walk_timeout_seconds + 5):
                provider = provider_factory()
                result = await provider.read_property_catalog()
        except SoftTimeLimitExceeded:
            raise
        except Exception as exc:
            result.failure = CatalogFailureDTO(
                kind="timeout" if isinstance(exc, TimeoutError) else "upstream",
                endpoint="setup" if provider is None else "regex_properties",
                failure_class=type(exc).__name__, elapsed_seconds=time.monotonic() - started,
            )
        finally:
            if provider is not None:
                try:
                    async with asyncio.timeout(5):
                        await provider.aclose()
                except SoftTimeLimitExceeded:
                    raise
                except Exception:
                    # Close is cleanup, not a failed data walk. Retain the safe diagnostic.
                    logger.warning("property catalog provider close failed")
        complete = result.names_complete and result.applicability_complete
        now = datetime.now(UTC)
        if complete or (result.names_complete and state.as_of is None):
            await session.execute(delete(Entry).where(Entry.instance_id == instance_id))
            values = [{
                "instance_id": instance_id, "qradar_id": p.qradar_id, "identifier": p.identifier,
                "name": p.name, "property_type": p.property_type,
                "use_for_rule_engine": p.use_for_rule_engine, "auto_discovered": p.auto_discovered,
                "name_allowed": p.name_allowed,
                "type_ids": list(p.log_source_type_ids) if complete else [],
            } for p in result.properties]
            # Fixed batches bound asyncpg bind parameters at 12k+ properties.
            for offset in range(0, len(values), 1000):
                await session.execute(insert(Entry), values[offset:offset + 1000])
            state.as_of = now
            state.applicability_complete = complete
            state.property_count = len(values)
        state.status = "COMPLETE" if complete else "PARTIAL" if (
            result.names_complete or (result.failure and result.failure.page_start > 0)
        ) else "FAILED"
        state.last_completed_at = now
        state.expression_count = result.expression_count
        state.duration_seconds = time.monotonic() - started
        state.failure = result.failure.model_dump() if result.failure else None
        await session.commit()
        return True
