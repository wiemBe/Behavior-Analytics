"""Parse identity aggregate rows. Shared by the REST provider and the mock.

A bad key, a bad count, an unasked log source or an unasked event id fails the
whole search. The message does not quote the cell. More than ``max_rows`` rows
is truncation; the extra row is dropped and the rest are kept.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from app.collectors.bucketed_metrics import _integer_bucket_key
from app.identity.dto import IdentityKeyedRow
from app.providers.base import ProviderError


def coerce_events(value: object) -> int | None:
    """A non-negative whole event count, or None when the cell is not one."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):  # NaN
            return None
        return int(value) if value.is_integer() and value >= 0 else None
    return None


def parse_event_id(value: object) -> int | None:
    """A catalog-shaped event id: an integer, a numeric string, or ``WIN-<id>``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("WIN-") and text[4:].isascii() and text[4:].isdigit():
            return int(text[4:])
        if text.isascii() and text.isdigit():
            return int(text)
    return None


def parse_identity_rows(
    raw_rows: Sequence[dict[str, Any]],
    *,
    source_ids: Sequence[int],
    event_ids: Sequence[int],
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_rows: int,
    with_username: bool,
) -> tuple[tuple[IdentityKeyedRow, ...], bool]:
    """Parse one search. Raises ``ProviderError`` when a row is not in the asked set."""
    if isinstance(max_rows, bool) or not isinstance(max_rows, int) or max_rows < 1:
        raise ProviderError("identity row cap is not a positive integer")
    bad_width = (
        isinstance(bucket_seconds, bool)
        or not isinstance(bucket_seconds, int)
        or bucket_seconds < 1
    )
    if bad_width:
        raise ProviderError("identity bucket width is not a positive integer")
    asked_sources = set(source_ids)
    asked_events = set(event_ids)
    parsed: list[IdentityKeyedRow] = []
    for raw in raw_rows:
        source = _integer_bucket_key(raw.get("log_source_id"))
        event_id = parse_event_id(raw.get("event_id"))
        key = _integer_bucket_key(raw.get("k"))
        events = coerce_events(raw.get("events"))
        if source is None or source not in asked_sources:
            raise ProviderError("identity search returned a log source it was not asked for")
        if event_id is None or event_id not in asked_events:
            raise ProviderError("identity search returned an event id it was not asked for")
        if key is None or events is None:
            raise ProviderError(
                "identity search returned a key or count that is not a whole number"
            )
        bucket_start = datetime.fromtimestamp(key * bucket_seconds, tz=UTC)
        if bucket_start < window_start or bucket_start >= window_end:
            raise ProviderError("identity search returned a bucket outside its window")
        username: str | None = None
        if with_username:
            value = raw.get("username")
            if not isinstance(value, str):
                raise ProviderError("identity name search returned a row without a username")
            username = value
        parsed.append(
            IdentityKeyedRow(
                qradar_log_source_id=source,
                event_id=event_id,
                bucket_start=bucket_start,
                events=events,
                username=username,
            )
        )
    truncated = len(parsed) > max_rows
    kept = tuple(parsed[:max_rows])
    return kept, truncated
