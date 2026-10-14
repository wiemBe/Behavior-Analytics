"""Validation and row parsing for the catch-all senders search. Pure: no I/O, no clock.

Shared by the REST provider and the mock, so a mock run exercises the very guards
and the very row parsing production does (SEARCH-CADENCE §SC.5 rule 6). The sender
text is device-controlled: it is carried raw to the pure fold in
`app.catchall.fold`, which sanitizes it and decides whether it is a sender at all.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any

from app.collectors.bucketed_metrics import _integer_bucket_key, validate_bucketed_metric_window
from app.providers.base import ProviderError
from app.providers.dto import CatchallSenderRow
from app.providers.normalize import as_int

#: Device type ids are small positive integers in QRadar.
DEVICE_TYPE_ID_MAX = 100_000
MAX_DEVICE_TYPES = 32


def validate_catchall_type_ids(ids: Iterable[int]) -> tuple[int, ...]:
    """The sorted, de-duplicated device types of a search, or a ValueError.

    They are the only caller-supplied text that reaches the query, and only as
    integers.
    """
    values = list(ids)
    if any(isinstance(v, bool) or not isinstance(v, int) for v in values):
        raise ValueError("catch-all device type ids must be integers")
    unique = tuple(sorted(set(values)))
    if not unique:
        raise ValueError("a catch-all search needs at least one device type")
    if len(unique) > MAX_DEVICE_TYPES:
        raise ValueError(f"a catch-all search covers at most {MAX_DEVICE_TYPES} device types")
    if unique[0] < 1 or unique[-1] > DEVICE_TYPE_ID_MAX:
        raise ValueError(f"catch-all device type ids run from 1 to {DEVICE_TYPE_ID_MAX}")
    return unique


def _text(value: Any) -> str | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value
    return None


def parse_catchall_rows(
    rows: Sequence[dict[str, Any]],
    *,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_rows: int,
) -> tuple[tuple[CatchallSenderRow, ...], bool]:
    """Keyed aggregate rows to validated `CatchallSenderRow`s, and whether they were cut.

    ``len(rows) > max_rows`` is truncation; the first `max_rows` rows are kept
    (the search is ordered by volume). Any key outside the window, any
    non-integer key, any non-integer log source and any malformed volume fails
    the whole search with a ProviderError that quotes nothing upstream. The
    sender text is not judged here.
    """
    start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
    first_key = int(start.timestamp()) // bucket_seconds
    last_key = int(end.timestamp()) // bucket_seconds  # exclusive
    parsed: list[CatchallSenderRow] = []
    for row in rows:
        key = _integer_bucket_key(row.get("k"))
        if key is None:
            raise ProviderError("QRadar Ariel catch-all row had a non-integer bucket key")
        if not first_key <= key < last_key:
            raise ProviderError("QRadar Ariel catch-all row had a bucket key outside the window")
        source = as_int(row.get("logsourceid"))
        if source is None:
            raise ProviderError("QRadar Ariel catch-all row had no log source")
        events = as_int(row.get("events"))
        records = as_int(row.get("records"))
        if events is None or records is None or events < 0 or records < 0:
            raise ProviderError("QRadar Ariel catch-all row had a malformed volume")
        parsed.append(
            CatchallSenderRow(
                qradar_log_source_id=source,
                sender=_text(row.get("sender")),
                bucket_start=datetime.fromtimestamp(key * bucket_seconds, tz=start.tzinfo),
                events=events,
                records=records,
            )
        )
    truncated = len(parsed) > max_rows
    return tuple(parsed[:max_rows]), truncated
