"""The IPS search plan: which sources share which keyed search (design D.5). Pure.

Sources whose profiles name the same references for what a dimension reads form
one group, so a fleet of all-normalized sources is one search per dimension and a
custom property makes its own group. A group over `ips_max_sources_per_search`
is chunked, deterministically.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.ips.profile import IpsFieldMapping
from app.models.enums import IpsDimension, IpsLogicalField
from app.providers.base import IPS_DIMENSION_FIELDS


@dataclass(frozen=True)
class IpsSourceGroup:
    """Sources that share the exact references one dimension reads.

    `fields` is sorted by logical field and holds `(logical, kind, name)`.
    """

    fields: tuple[tuple[str, str, str], ...]
    source_ids: tuple[int, ...]

    def provider_fields(self) -> dict[str, tuple[str, str]]:
        """The `fields` argument of `QRadarProvider.get_ips_keyed`."""
        return {logical: (kind, name) for logical, kind, name in self.fields}


@dataclass(frozen=True)
class IpsPlannedSearch:
    dimension: IpsDimension
    group: IpsSourceGroup


def dimension_references(
    dimension: IpsDimension, mapping: IpsFieldMapping
) -> tuple[tuple[str, str, str], ...] | None:
    """The references `dimension` reads for a mapping, or None when it does not apply.

    None is for an ACTION search of a source without an ACTION field: that source
    has no block ratio, which is not a zero.
    """
    required, optional = IPS_DIMENSION_FIELDS[dimension]
    out: list[tuple[str, str, str]] = []
    for name in (*required, *optional):
        reference = mapping.reference_for(IpsLogicalField(name))
        if reference is None:
            if name in required:
                return None
            continue
        out.append((name, reference.kind.value, reference.name))
    return tuple(sorted(out))


def plan_searches(
    sources: Iterable[tuple[int, IpsFieldMapping]], *, max_sources_per_search: int
) -> list[IpsPlannedSearch]:
    """Groups per dimension for enabled, VALID sources.

    Order is deterministic: dimension order, then the sorted reference tuple, then
    the source id (chunks are consecutive runs of sorted ids).
    """
    if max_sources_per_search < 1:
        raise ValueError("max_sources_per_search must be positive")
    pairs = list(sources)
    ids = [source_id for source_id, _ in pairs]
    if len(set(ids)) != len(ids):
        raise ValueError("a log source appears twice in the IPS plan")
    planned: list[IpsPlannedSearch] = []
    for dimension in IpsDimension:
        by_references: dict[tuple[tuple[str, str, str], ...], list[int]] = {}
        for source_id, mapping in pairs:
            references = dimension_references(dimension, mapping)
            if references is None:
                continue
            by_references.setdefault(references, []).append(source_id)
        for references in sorted(by_references):
            members = sorted(by_references[references])
            for offset in range(0, len(members), max_sources_per_search):
                planned.append(
                    IpsPlannedSearch(
                        dimension,
                        IpsSourceGroup(
                            fields=references,
                            source_ids=tuple(members[offset : offset + max_sources_per_search]),
                        ),
                    )
                )
    return planned
