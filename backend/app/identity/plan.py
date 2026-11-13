"""Which identity sources share a search (design §3). Pure.

Sources with the same event-id field and the same username field share one
chunk, then the chunk is cut at ``max_sources_per_search``. A chunk is never
one source when the mapping matches, and a username is not a chunk key.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from uuid import UUID

from app.identity.dto import IdentitySearchKind
from app.identity.reference import IdentityFieldMapping, IdentityReferenceError
from app.models.enums import IDENTITY_EVENT_CATALOG, IdentityEventKind

_VOLUME_IDS = frozenset(
    event_id for event_id, kind in IDENTITY_EVENT_CATALOG if kind is IdentityEventKind.VOLUME
)
_RARE_IDS = frozenset(event_id for event_id, _kind in IDENTITY_EVENT_CATALOG) - _VOLUME_IDS


@dataclass(frozen=True)
class IdentitySourcePlan:
    """One eligible source inside a chunk."""

    qradar_id: int
    log_source_id: UUID
    enabled_event_ids: tuple[int, ...]


@dataclass(frozen=True)
class IdentityChunk:
    """Sources that share a mapping and fit in one search."""

    event_id_property: str
    username_property: str
    sources: tuple[IdentitySourcePlan, ...]

    @property
    def qradar_ids(self) -> tuple[int, ...]:
        return tuple(source.qradar_id for source in self.sources)

    def event_ids(self, search: IdentitySearchKind) -> tuple[int, ...]:
        """The union of enabled catalog ids this search asks about. Empty means skip it."""
        enabled: set[int] = set()
        for source in self.sources:
            enabled.update(source.enabled_event_ids)
        if search is IdentitySearchKind.VOLUME_NAMES:
            enabled &= _VOLUME_IDS
        elif search is IdentitySearchKind.RARE_NAMES:
            enabled &= _RARE_IDS
        return tuple(sorted(enabled))


def plan_chunks(
    sources: Iterable[tuple[int, UUID, IdentityFieldMapping]],
    *,
    max_sources_per_search: int,
) -> list[IdentityChunk]:
    """Group by mapping, then cut at the source cap. Order is deterministic."""
    if (
        isinstance(max_sources_per_search, bool)
        or not isinstance(max_sources_per_search, int)
        or max_sources_per_search < 1
    ):
        raise IdentityReferenceError("the log source cap must be a positive integer")
    pairs = list(sources)
    qradar_ids = [qradar_id for qradar_id, _source_id, _mapping in pairs]
    if len(set(qradar_ids)) != len(qradar_ids):
        raise IdentityReferenceError("a log source appears twice in the identity plan")
    grouped: dict[tuple[str, str], list[IdentitySourcePlan]] = {}
    for qradar_id, log_source_id, mapping in pairs:
        # Re-validate so a caller cannot pass a mapping that skipped the checks.
        checked = IdentityFieldMapping.from_parts(
            mapping.event_id_property,
            mapping.username_property,
            mapping.logon_type_property,
            mapping.enabled_event_ids,
        )
        key = (checked.event_id_property, checked.username_property)
        grouped.setdefault(key, []).append(
            IdentitySourcePlan(
                qradar_id=qradar_id,
                log_source_id=log_source_id,
                enabled_event_ids=checked.enabled_event_ids,
            )
        )
    chunks: list[IdentityChunk] = []
    for key in sorted(grouped):
        members = sorted(grouped[key], key=lambda source: source.qradar_id)
        for offset in range(0, len(members), max_sources_per_search):
            chunks.append(
                IdentityChunk(
                    event_id_property=key[0],
                    username_property=key[1],
                    sources=tuple(members[offset : offset + max_sources_per_search]),
                )
            )
    return chunks


def chunk_event_ids(chunk: IdentityChunk, search: IdentitySearchKind) -> Sequence[int]:
    """The ids ``search`` would put in one ``IN`` list for this chunk."""
    return chunk.event_ids(search)
