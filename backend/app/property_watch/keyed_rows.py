"""Row parsing for keyed property-watch searches. Pure: no I/O, no clock.

Shared by the REST provider and the mock, so a mock run exercises the very guards and the
very row parsing production does (SEARCH-CADENCE §SC.5 rule 6). Nothing here keeps or
judges a raw value: the rows go to the pure fold in `app.property_watch.fold`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from app.collectors.bucketed_metrics import _integer_bucket_key, validate_bucketed_metric_window
from app.property_watch.facts import PwFactError, coerce_count
from app.providers.base import ProviderError
from app.providers.dto import PwKeyedRow


def _raw_value(value: Any) -> str | None:
    """The property value as text, untouched. NULL, and anything that is not text or a
    plain number, is None: the fold counts it as unparsed."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return None


def parse_pw_keyed_rows(
    rows: Sequence[dict[str, Any]],
    *,
    property_count: int,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_rows: int,
) -> tuple[tuple[PwKeyedRow, ...], bool]:
    """Keyed aggregate rows to validated `PwKeyedRow`s, and whether they were cut.

    ``len(rows) > max_rows`` is truncation; the first `max_rows` rows are kept (the search
    is ordered by volume). A key outside the window, a non-integer key and a count that is
    not an integer-valued, non-negative number (`coerce_count`: `12.0` is accepted, `12.5`,
    `-1`, NaN, a string and a boolean are not) fail the whole search with a ProviderError
    that quotes nothing upstream. A search that fails is unmeasured, never zero.
    """
    start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
    first_key = int(start.timestamp()) // bucket_seconds
    last_key = int(end.timestamp()) // bucket_seconds  # exclusive
    parsed: list[PwKeyedRow] = []
    for row in rows:
        key = _integer_bucket_key(row.get("k"))
        if key is None:
            raise ProviderError("QRadar Ariel property-watch row had a non-integer bucket key")
        if not first_key <= key < last_key:
            raise ProviderError(
                "QRadar Ariel property-watch row had a bucket key outside the window"
            )
        try:
            events = coerce_count(row.get("events"))
        except PwFactError as exc:
            raise ProviderError("QRadar Ariel property-watch row had a malformed count") from exc
        parsed.append(
            PwKeyedRow(
                bucket_start=datetime.fromtimestamp(key * bucket_seconds, tz=start.tzinfo),
                events=events,
                values=tuple(_raw_value(row.get(f"p{i}")) for i in range(property_count)),
            )
        )
    truncated = len(parsed) > max_rows
    return tuple(parsed[:max_rows]), truncated
