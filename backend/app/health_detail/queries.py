"""The two stored AQL searches of the QRadar health family (D-42 section 3).

Both aggregate in Ariel (`GROUP BY`), over one half-open time window, and select no payload.
The text is built here from constants and validated integers only; no caller text enters it.
`assert_stored_query` is the validation every search passes through before it is sent.
"""

from __future__ import annotations

import re

from app.core.patterns import PROPERTY_NAME_PATTERN
from app.health_detail.spec import (
    EXCLUDED_QID_NAME,
    LOG_SOURCE_TYPE_METRICS,
    LOG_SOURCE_TYPE_NOTIFICATIONS,
    METRIC_ID_PATTERN,
    METRIC_IDS,
    PROPERTY_ELEMENT,
    PROPERTY_METRIC_ID,
    PROPERTY_VALUE,
)

_FORBIDDEN = (
    re.compile(r"select\s+\*", re.IGNORECASE),
    re.compile(r"\bpayload\b", re.IGNORECASE),
    re.compile(r"\butf8\s*\(", re.IGNORECASE),
    re.compile(r"\brawpayload\b", re.IGNORECASE),
)


class StoredQueryError(ValueError):
    """A health search failed its own validation; it is never sent."""


def _quote_property(name: str) -> str:
    """A custom-property name, double-quoted, only after the shared allowlist."""
    if PROPERTY_NAME_PATTERN.fullmatch(name) is None:
        raise StoredQueryError("unsafe QRadar property name")
    return f'"{name}"'


def _window(start_ms: int, end_ms: int) -> None:
    if isinstance(start_ms, bool) or isinstance(end_ms, bool):
        raise StoredQueryError("the window bounds are integers")
    if not isinstance(start_ms, int) or not isinstance(end_ms, int) or start_ms >= end_ms:
        raise StoredQueryError("the window must be a non-empty half-open interval")


def metrics_query(*, start_ms: int, end_ms: int, limit: int) -> str:
    """Per host, metric and element: max, average and sample count over the window."""
    _window(start_ms, end_ms)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise StoredQueryError("the row limit must be a positive integer")
    metric_id = _quote_property(PROPERTY_METRIC_ID)
    value = _quote_property(PROPERTY_VALUE)
    element = _quote_property(PROPERTY_ELEMENT)
    ids = ", ".join(f"'{item}'" for item in METRIC_IDS)
    aql = (
        'SELECT LOGSOURCENAME(logsourceid) AS "source", '  # noqa: S608
        f'{metric_id} AS "metric", {element} AS "element", '
        f'MAX({value}) AS "max_value", AVG({value}) AS "avg_value", COUNT(*) AS "samples" '
        f"FROM events WHERE LOGSOURCETYPENAME(devicetype) = '{LOG_SOURCE_TYPE_METRICS}' "
        f"AND {metric_id} IN ({ids}) "
        f"AND starttime >= {start_ms} AND starttime < {end_ms} "
        f"GROUP BY LOGSOURCENAME(logsourceid), {metric_id}, {element} "
        f"ORDER BY COUNT(*) DESC LIMIT {limit}"
    )
    return assert_stored_query(aql)


def notifications_query(*, start_ms: int, end_ms: int, limit: int) -> str:
    """Per host and QID name: the count, the highest severity and the newest time.

    `Information Message` is left out in the query itself. No payload is selected: the raw
    notification text can carry addresses and our own AQL errors.
    """
    _window(start_ms, end_ms)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise StoredQueryError("the row limit must be a positive integer")
    aql = (
        'SELECT LOGSOURCENAME(logsourceid) AS "source", QIDNAME(qid) AS "qid_name", '  # noqa: S608
        'MAX(severity) AS "severity", SUM(eventcount) AS "events", '
        'MAX(starttime) AS "last_seen" '
        f"FROM events WHERE LOGSOURCETYPENAME(devicetype) = '{LOG_SOURCE_TYPE_NOTIFICATIONS}' "
        f"AND QIDNAME(qid) <> '{EXCLUDED_QID_NAME}' "
        f"AND starttime >= {start_ms} AND starttime < {end_ms} "
        "GROUP BY LOGSOURCENAME(logsourceid), QIDNAME(qid) "
        f"ORDER BY SUM(eventcount) DESC LIMIT {limit}"
    )
    return assert_stored_query(aql)


def assert_stored_query(aql: str) -> str:
    """Refuse a health search that selects a payload or everything, or names an odd metric."""
    for pattern in _FORBIDDEN:
        if pattern.search(aql):
            raise StoredQueryError("a health search selects a payload or every column")
    if not aql.startswith("SELECT ") or "GROUP BY" not in aql or " LIMIT " not in aql:
        raise StoredQueryError("a health search is an aggregate with a row limit")
    if "IN (" in aql:
        listed = aql.split("IN (", 1)[1].split(")", 1)[0]
        for item in (part.strip().strip("'") for part in listed.split(",")):
            if METRIC_ID_PATTERN.fullmatch(item) is None or item not in METRIC_IDS:
                raise StoredQueryError("a health search names a metric outside the fixed list")
    return aql
