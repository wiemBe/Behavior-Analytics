"""The QRadar custom-property catalog, cached per instance.

The slim joined list (names and type ids, two requests, about 80 KB) is kept
per instance for a TTL and filtered in memory for each log source. The catalog
is a suggestion for the mapping editor, never a gate, so any failure becomes an
`UNAVAILABLE` answer with a fixed sentence; it is not cached, and no upstream
text, header, token or console address reaches the caller.

The cache is process-local: each API worker keeps its own, and a restart drops it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.config import get_settings
from app.providers.base import QRadarProvider
from app.providers.dto import CustomPropertyDTO
from app.schemas.property_catalog import PropertyCatalogItemOut, PropertyCatalogOut

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
                None if source_type_id is None else source_type_id in p.log_source_type_ids
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


_service: PropertyCatalogService | None = None


def get_property_catalog_service() -> PropertyCatalogService:
    """The process-wide instance, created on first use with the configured TTL."""
    global _service
    if _service is None:
        _service = PropertyCatalogService(
            ttl_seconds=get_settings().qradar_property_catalog_ttl_seconds
        )
    return _service
