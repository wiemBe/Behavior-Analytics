"""Shared search and ordering for the WAF and IPS add-source pickers.

An operator knows the product, the QRadar id or the device address of a log
source as often as its name, and a console holds thousands of sources. One
text matches the name, the DSM type and the identifier (each a contains
match, wildcards escaped) and, when it is all digits, the QRadar id exactly.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, case, func, or_

from app.models.enums import SourceClass
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource

_MAX_QRADAR_ID = 2**31 - 1  # LogSource.qradar_id is a 32-bit integer


def search_clause(q: str) -> ColumnElement[bool]:
    """The ``WHERE`` condition for a search text (already stripped, non-empty)."""
    clauses = [
        LogSource.name.icontains(q, autoescape=True),
        LogSource.type_name.icontains(q, autoescape=True),
        LogSource.identifier.icontains(q, autoescape=True),
    ]
    qradar_id = _qradar_id(q)
    if qradar_id is not None:
        clauses.append(LogSource.qradar_id == qradar_id)
    return or_(*clauses)


def _qradar_id(q: str) -> int | None:
    if not (q.isascii() and q.isdigit()):
        return None
    value = int(q)
    return value if value <= _MAX_QRADAR_ID else None


def apply_candidate_search(
    query: Any,
    *,
    family: SourceClass,
    q: str | None,
    limit: int,
) -> Any:
    """Filter by the search text, order and cap the candidate query.

    Order: sources classed as the page's family, then an exact QRadar-id match,
    then name, then QRadar id.
    """
    text = (q or "").strip()
    order: list[Any] = [
        case((LogSourceClassification.source_class == family, 0), else_=1)
    ]
    qradar_id = _qradar_id(text) if text else None
    if qradar_id is not None:
        order.append(case((LogSource.qradar_id == qradar_id, 0), else_=1))
    order += [func.lower(LogSource.name), LogSource.qradar_id]
    if text:
        query = query.where(search_clause(text))
    return query.order_by(*order).limit(limit)
