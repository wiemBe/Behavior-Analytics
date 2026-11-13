"""The AQL of an identity tick's three searches (design §3).

Pure: text in, text out, no I/O. Providers call ``validate_query_inputs`` and
``build_query``. Nothing else writes an identity query, and the browser never
sends one (AGENTS.md rule 4).

A property name reaches the text only after ``identity.reference`` has accepted
it, and it is double-quoted. ``qid`` is the function ``QIDNAME(qid)`` with
``WIN-<id>`` literals built from catalog integers. Source ids and event ids are
integers. A value that fails is refused, never escaped, and its text is not
echoed.

``LIMIT`` is ``max_rows + 1``. The console hides a cut, so the caller treats
more than ``max_rows`` rows as truncation. One call is one search for every
source in the chunk and every catalog id in the set. Usernames are not inputs.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from app.identity.dto import IdentitySearchKind
from app.identity.reference import (
    IdentityReferenceError,
    event_id_expression,
    event_id_literal,
    username_expression,
    validate_event_id_property,
    validate_event_ids,
    validate_source_ids,
    validate_username_property,
)
from app.models.enums import IDENTITY_EVENT_CATALOG, IdentityEventKind

_VOLUME_IDS = frozenset(
    event_id for event_id, kind in IDENTITY_EVENT_CATALOG if kind is IdentityEventKind.VOLUME
)
_RARE_IDS = frozenset(event_id for event_id, _kind in IDENTITY_EVENT_CATALOG) - _VOLUME_IDS


@dataclass(frozen=True)
class IdentityQuerySpec:
    """Validated inputs of one search. Build it with ``validate_query_inputs``."""

    search: IdentitySearchKind
    source_ids: tuple[int, ...]
    event_id_property: str
    username_property: str
    event_ids: tuple[int, ...]


def _ids_for(search: IdentitySearchKind, enabled: Sequence[int]) -> tuple[int, ...]:
    chosen = set(enabled)
    if search is IdentitySearchKind.VOLUME_NAMES:
        chosen &= _VOLUME_IDS
    elif search is IdentitySearchKind.RARE_NAMES:
        chosen &= _RARE_IDS
    if not chosen:
        raise IdentityReferenceError("this identity search has no catalog event id")
    return tuple(sorted(chosen))


def validate_query_inputs(
    search: IdentitySearchKind,
    *,
    source_ids: Sequence[int],
    event_id_property: object,
    username_property: object,
    event_ids: Sequence[int],
    max_sources: int,
) -> IdentityQuerySpec:
    """Re-check every input. ``IdentityReferenceError`` when one fails.

    ``event_ids`` is the chunk's enabled catalog. The search keeps the subset
    it asks about. An empty subset is refused so the caller skips that search
    instead of sending ``IN ()``.
    """
    kind = IdentitySearchKind(search)
    return IdentityQuerySpec(
        search=kind,
        source_ids=validate_source_ids(source_ids, max_sources=max_sources),
        event_id_property=validate_event_id_property(event_id_property),
        username_property=validate_username_property(username_property),
        event_ids=_ids_for(kind, validate_event_ids(event_ids)),
    )


def _positive(name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise IdentityReferenceError(f"an identity query needs a positive {name}")
    return value


def build_query(
    spec: IdentityQuerySpec,
    *,
    start_ms: int,
    end_ms: int,
    bucket_ms: int,
    max_rows: int,
) -> str:
    """One search. ``LIMIT`` is ``max_rows + 1``. The provider appends ``START``/``STOP``."""
    start = _positive("start", start_ms)
    end = _positive("end", end_ms)
    bucket = _positive("bucket", bucket_ms)
    cap = _positive("row cap", max_rows)
    if end <= start:
        raise IdentityReferenceError("an identity query needs a positive window")
    event_expr = event_id_expression(spec.event_id_property)
    sources = ", ".join(str(source_id) for source_id in spec.source_ids)
    literals = ", ".join(
        event_id_literal(spec.event_id_property, event_id) for event_id in spec.event_ids
    )
    key = f"starttime / {bucket}"
    where = (
        f"WHERE logsourceid IN ({sources}) "
        f"AND starttime >= {start} AND starttime < {end} "
        f"AND {event_expr} IN ({literals})"
    )
    group = ["logsourceid", event_expr, key]
    columns = [
        'logsourceid AS "log_source_id"',
        f'{event_expr} AS "event_id"',
        f'{key} AS "k"',
        'SUM(eventcount) AS "events"',
    ]
    if spec.search is not IdentitySearchKind.COUNTS:
        user_expr = username_expression(spec.username_property)
        where += f" AND {user_expr} IS NOT NULL"
        group.insert(2, user_expr)
        columns.insert(2, f'{user_expr} AS "username"')
    return (
        f"SELECT {', '.join(columns)} FROM events {where} "  # noqa: S608
        f"GROUP BY {', '.join(group)} ORDER BY SUM(eventcount) DESC LIMIT {cap + 1}"
    )


def build_total_query(
    *,
    qradar_log_source_id: int,
    start_ms: int,
    end_ms: int,
) -> str:
    """The validation probe's total. ``LIMIT 2`` so a cut is visible."""
    source = validate_source_ids([qradar_log_source_id], max_sources=1)[0]
    start = _positive("start", start_ms)
    end = _positive("end", end_ms)
    return (
        'SELECT SUM(eventcount) AS "events" FROM events '  # noqa: S608
        f"WHERE logsourceid = {source} AND starttime >= {start} AND starttime < {end} "
        "LIMIT 2"
    )


def build_populated_query(
    *,
    qradar_log_source_id: int,
    event_id_property: object,
    username_property: object,
    field: str,
    start_ms: int,
    end_ms: int,
) -> str:
    """How often one mapped field is present. ``field`` is ``event_id`` or ``username``."""
    source = validate_source_ids([qradar_log_source_id], max_sources=1)[0]
    event_property = validate_event_id_property(event_id_property)
    username_property_checked = validate_username_property(username_property)
    start = _positive("start", start_ms)
    end = _positive("end", end_ms)
    if field == "event_id":
        expression = event_id_expression(event_property)
    elif field == "username":
        expression = username_expression(username_property_checked)
    else:
        raise IdentityReferenceError("the validation probe names event id or username")
    return (
        'SELECT SUM(eventcount) AS "events" FROM events '  # noqa: S608
        f"WHERE logsourceid = {source} AND starttime >= {start} AND starttime < {end} "
        f"AND {expression} IS NOT NULL LIMIT 2"
    )


def build_catalog_query(
    *,
    qradar_log_source_id: int,
    event_id_property: object,
    start_ms: int,
    end_ms: int,
) -> str:
    """Which catalog event ids occur. ``LIMIT`` is one past the catalog."""
    source = validate_source_ids([qradar_log_source_id], max_sources=1)[0]
    event_property = validate_event_id_property(event_id_property)
    start = _positive("start", start_ms)
    end = _positive("end", end_ms)
    expression = event_id_expression(event_property)
    literals = ", ".join(
        event_id_literal(event_property, event_id)
        for event_id, _kind in IDENTITY_EVENT_CATALOG
    )
    limit = len(IDENTITY_EVENT_CATALOG) + 1
    return (
        f'SELECT {expression} AS "event_id", SUM(eventcount) AS "events" FROM events '  # noqa: S608
        f"WHERE logsourceid = {source} AND starttime >= {start} AND starttime < {end} "
        f"AND {expression} IN ({literals}) "
        f"GROUP BY {expression} ORDER BY SUM(eventcount) DESC LIMIT {limit}"
    )
