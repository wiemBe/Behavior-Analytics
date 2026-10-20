"""The AQL of a property watch's three searches (design §G.4 scope filter, §G.6 searches).

Pure: text in, text out, no I/O. The providers call `validate_query_inputs` and
`build_query`; nothing else in the application writes a property-watch query, and the
browser never sends one (AGENTS.md rule 4).

**Nothing operator-typed reaches the text except through a validated, quoted position.**
A property name is a `PwFieldReference` (an exact allowlist member, or a name that matches
`PROPERTY_NAME_PATTERN`) and is double-quoted. A pinned value passes
`validate_pinned_value` and is single-quoted: it cannot hold a quote, a backslash or a
control character, so there is nothing to escape. Scope ids are integers. Both are
re-checked here, whoever the caller is. A reference or a value that fails is refused, never
escaped, and its text is not echoed.

A pinned value is restricted with `IN`, never with OR-ed equality (PW-0 L4). `IN` cannot
express a tuple, so a combination watch gets `IN (...) AND IN (...)` over the distinct
components of its pinned tuples and the fold keeps only the pinned tuples.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.models.enums import PwScopeKind
from app.property_watch.reference import (
    PW_MAX_PROPERTIES,
    PwFieldReference,
    PwReferenceError,
    validate_pinned_value,
)
from app.providers.dto import PwSearchKind


@dataclass(frozen=True)
class PwQuerySpec:
    """The validated inputs of one search. Build it with `validate_query_inputs`."""

    search: PwSearchKind
    scope_kind: PwScopeKind
    #: Sorted and de-duplicated. One id for a source or a type, one chunk for a group.
    scope_ids: tuple[int, ...]
    properties: tuple[PwFieldReference, ...]
    #: One tuple per pinned value, one string per property. Empty unless `search` is PINNED.
    pinned: tuple[tuple[str, ...], ...]


def _scope_ids(kind: PwScopeKind, ids: Sequence[int], *, max_sources: int) -> tuple[int, ...]:
    values = list(ids)
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in values):
        raise ValueError("property-watch scope ids must be non-negative integers")
    unique = tuple(sorted(set(values)))
    if not unique:
        raise ValueError("a property-watch search needs at least one scope id")
    if kind is PwScopeKind.LOG_SOURCE_GROUP:
        if len(unique) > max_sources:
            raise ValueError(f"a property-watch search covers at most {max_sources} log sources")
    elif len(unique) != 1:
        raise ValueError("a source or type scope names exactly one id")
    return unique


def validate_query_inputs(
    search: PwSearchKind,
    *,
    scope_kind: PwScopeKind,
    scope_ids: Sequence[int],
    properties: Sequence[tuple[str, str]],
    pinned: Sequence[Sequence[str]] = (),
    max_sources: int,
) -> PwQuerySpec:
    """Every input re-checked. `ValueError` (`PwReferenceError` included) when one fails.

    `properties` are `(kind, name)` pairs, the IPS shape. TOTALS carries no property
    condition, so it ignores them after they validate; GROUPED and PINNED need one or two.
    """
    kind_checked = PwScopeKind(scope_kind)
    ids = _scope_ids(kind_checked, scope_ids, max_sources=max_sources)
    if isinstance(properties, str | bytes) or len(properties) > PW_MAX_PROPERTIES:
        raise PwReferenceError("a watch names one or two properties")
    references: list[PwFieldReference] = []
    for item in properties:
        try:
            kind, name = item
        except (TypeError, ValueError) as exc:
            raise PwReferenceError("a property reference is a (kind, name) pair") from exc
        references.append(PwFieldReference.from_dict({"kind": str(kind), "name": name}))
    if len(set(references)) != len(references):
        raise PwReferenceError("a watch cannot name the same property twice")
    if search is not PwSearchKind.TOTALS and not references:
        raise PwReferenceError("a grouped or pinned search needs a property")
    tuples: list[tuple[str, ...]] = []
    for entry in pinned:
        if isinstance(entry, str | bytes) or len(entry) != len(references):
            raise PwReferenceError("each pinned value needs one string per property")
        tuples.append(tuple(validate_pinned_value(component) for component in entry))
    if search is PwSearchKind.PINNED:
        if not tuples:
            raise PwReferenceError("a pinned search needs at least one pinned value")
    elif tuples:
        raise PwReferenceError("only a pinned search takes pinned values")
    return PwQuerySpec(
        search=search,
        scope_kind=kind_checked,
        scope_ids=ids,
        properties=tuple(references),
        pinned=tuple(dict.fromkeys(tuples)),
    )


def scope_condition(kind: PwScopeKind, ids: Sequence[int]) -> str:
    """The §G.4 filter. `ids` are validated integers (`validate_query_inputs`)."""
    values = ", ".join(str(int(i)) for i in ids)
    if kind is PwScopeKind.LOG_SOURCE_TYPE:
        return f"devicetype = {values}"
    return f"logsourceid IN ({values})"


def pinned_components(spec: PwQuerySpec) -> tuple[tuple[str, ...], ...]:
    """The distinct pinned components of each property, sorted: what the `IN` lists hold."""
    return tuple(
        tuple(sorted({entry[position] for entry in spec.pinned}))
        for position in range(len(spec.properties))
    )


def pinned_rows_per_bucket(spec: PwQuerySpec) -> int:
    """The most rows one bucket of the pinned search can return: the product of the
    distinct components, since `IN` x `IN` admits every combination of them."""
    product = 1
    for components in pinned_components(spec):
        product *= len(components)
    return product


def build_query(
    spec: PwQuerySpec, *, start_ms: int, end_ms: int, bucket_ms: int, limit: int
) -> str:
    """The §G.6 query text up to its `LIMIT`; the REST provider appends `START`/`STOP`.

    `limit` is `cap + 1`: the console hides a cut, so the caller detects truncation by
    receiving more than `cap` rows.
    """
    for number in (start_ms, end_ms, bucket_ms, limit):
        if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
            raise ValueError("a property-watch query takes positive integer bounds")
    key = f"starttime / {bucket_ms}"
    where = (
        f"WHERE {scope_condition(spec.scope_kind, spec.scope_ids)} "
        f"AND starttime >= {start_ms} AND starttime < {end_ms}"
    )
    if spec.search is PwSearchKind.TOTALS:
        return (
            f'SELECT {key} AS "k", SUM(eventcount) AS "events" FROM events {where} '  # noqa: S608
            f"GROUP BY {key} ORDER BY SUM(eventcount) DESC LIMIT {limit}"
        )
    expressions = [f'"{reference.name}"' for reference in spec.properties]
    select = [f'{expression} AS "p{index}"' for index, expression in enumerate(expressions)]
    for expression in expressions:
        where += f" AND {expression} IS NOT NULL"
    if spec.search is PwSearchKind.PINNED:
        for expression, components in zip(expressions, pinned_components(spec), strict=True):
            literals = ",".join(f"'{component}'" for component in components)
            where += f" AND {expression} IN ({literals})"
    columns = [*select, f'{key} AS "k"', 'SUM(eventcount) AS "events"']
    group_by = ", ".join([*expressions, key])
    return (
        f"SELECT {', '.join(columns)} FROM events {where} "  # noqa: S608
        f"GROUP BY {group_by} ORDER BY SUM(eventcount) DESC LIMIT {limit}"
    )
