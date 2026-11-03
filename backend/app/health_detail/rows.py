"""The answers of the two health searches, parsed defensively (D-42 section 3).

Shared by the REST provider and the mock, so both parse QRadar's rows the same way. A row
that is not a mapping, or a count that is not a non-negative integer, fails the whole search
with a `ProviderError` that quotes nothing. A value that is missing, not a number, or not
finite becomes None: it is a missing measurement, never a zero.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.providers.base import ProviderError
from app.providers.dto import ProviderDTO
from app.providers.normalize import as_int, parse_qradar_time

#: Text columns are capped to what the stored columns hold.
_SOURCE_LIMIT = 512
_NAME_LIMIT = 255


class HealthMetricRow(ProviderDTO):
    """One (log source, metric, element) group of the metric search."""

    source: str | None = None
    metric: str | None = None
    element: str | None = None
    max_value: float | None = None
    avg_value: float | None = None
    #: `COUNT(*)`: events behind the group.
    samples: int


class HealthNotificationRow(ProviderDTO):
    """One (log source, QID name) group of the notification search."""

    source: str | None = None
    qid_name: str | None = None
    max_severity: int | None = None
    #: `SUM(eventcount)`.
    events: int
    last_seen: datetime | None = None


class HealthMetricsResult(ProviderDTO):
    window_start: datetime
    window_end: datetime
    rows: tuple[HealthMetricRow, ...] = ()
    #: More than `max_rows` groups existed: `rows` is a part of them.
    truncated: bool = False
    #: The query text, for provenance. It holds no payload and no caller text.
    query: str = ""


class HealthNotificationsResult(ProviderDTO):
    window_start: datetime
    window_end: datetime
    rows: tuple[HealthNotificationRow, ...] = ()
    truncated: bool = False
    query: str = ""


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    return cleaned[:limit] if cleaned else None


def _number(value: Any) -> float | None:
    """A finite number, or None. Booleans, text that is not a number and NaN are None."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.strip())
        except ValueError:
            return None
    else:
        return None
    return number if math.isfinite(number) else None


def _count(value: Any, what: str) -> int:
    count = as_int(value)
    if count is None or count < 0:
        raise ProviderError(f"QRadar Ariel health {what} row had a malformed count")
    return count


def parse_health_metric_rows(
    rows: Sequence[Any], *, max_rows: int
) -> tuple[tuple[HealthMetricRow, ...], bool]:
    """Metric rows to validated rows, and whether the search was cut at `max_rows`."""
    parsed: list[HealthMetricRow] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError("QRadar Ariel health metric row was not an object")
        parsed.append(
            HealthMetricRow(
                source=_text(row.get("source"), _SOURCE_LIMIT),
                metric=_text(row.get("metric"), _NAME_LIMIT),
                element=_text(row.get("element"), _NAME_LIMIT),
                max_value=_number(row.get("max_value")),
                avg_value=_number(row.get("avg_value")),
                samples=_count(row.get("samples"), "metric"),
            )
        )
    return tuple(parsed[:max_rows]), len(parsed) > max_rows


def parse_health_notification_rows(
    rows: Sequence[Any], *, max_rows: int
) -> tuple[tuple[HealthNotificationRow, ...], bool]:
    """Notification rows to validated rows, and whether the search was cut at `max_rows`."""
    parsed: list[HealthNotificationRow] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ProviderError("QRadar Ariel health notification row was not an object")
        severity = as_int(row.get("severity"))
        parsed.append(
            HealthNotificationRow(
                source=_text(row.get("source"), _SOURCE_LIMIT),
                qid_name=_text(row.get("qid_name"), _NAME_LIMIT),
                max_severity=severity if severity is not None and 0 <= severity <= 10 else None,
                events=_count(row.get("events"), "notification"),
                last_seen=parse_qradar_time(row.get("last_seen")),
            )
        )
    return tuple(parsed[:max_rows]), len(parsed) > max_rows
