"""Fold one keyed WAF search into per-bucket batches. Pure; no I/O.

A keyed search (docs/SEARCH-CADENCE.md section SC.5) groups by the raw endpoint
key *and* `starttime / bucket_ms`, so one result covers many buckets. `LIMIT`
caps that result as a whole, never each bucket (T-032), so the per-bucket
top-N that today's per-bucket query gets from `ORDER BY SUM(eventcount) DESC
LIMIT n` has to be redone here, in the fold.

**The cut.** A bucket keeps its `max_groups` largest groups by request count.
Ties are broken deterministically, by the raw key ascending (URL, method,
virtual host and, for a breakdown, the value; a missing part sorts as the
empty string). Today's query leaves ties to Ariel; any fixed order is as
correct, and a fixed one makes the keyed path reproducible.

**Truncation.** The caller keeps nothing from a search whose row budget was
exceeded, so this fold only ever sees a whole result. Within it, a bucket is
flagged truncated by the rule today's per-bucket query uses: it returned at
least `max_groups` groups. For volume that counts the groups with a usable
request count (rows without one are dropped, as there); for a breakdown it
counts every row of the bucket.

**Where raw values live.** URLs, methods, virtual hosts, action and
response-code values and source IPs exist here only as the fields of the
returned groups, exactly the transient batch today's per-bucket search hands the
collector. Nothing is persisted from this module.

**Failing closed.** A row whose `k` is missing, not an integer, or outside the
window means the appliance did not answer the question asked, so the whole
search fails rather than assigning the row to a bucket it may not belong to.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from app.collectors.bucketed_metrics import (
    _integer_bucket_key,
    validate_bucketed_metric_window,
)
from app.providers.base import ProviderError
from app.providers.dto import (
    WafBreakdownBatch,
    WafBreakdownGroup,
    WafObservationBatch,
    WafRequestGroup,
)
from app.providers.normalize import as_int, as_text

#: Sort keys of a row with no usable count: after every real key, whose first
#: element is a count negated and so never above 0.
_UNUSABLE_LAST = (1, "", "", "")
_UNUSABLE_LAST_BREAKDOWN = (1, "", "", "", "")


def observation_sort_key(group: WafRequestGroup) -> tuple[int, str, str, str]:
    """Request count descending, then the raw endpoint key ascending."""
    return (
        -group.request_count,
        group.raw_url or "",
        group.raw_method or "",
        group.raw_virtual_host or "",
    )


def breakdown_sort_key(group: WafBreakdownGroup) -> tuple[int, str, str, str, str]:
    """Request count descending, then the raw endpoint key and value ascending."""
    return (
        -group.request_count,
        group.raw_url or "",
        group.raw_method or "",
        group.raw_virtual_host or "",
        group.raw_value or "",
    )


def parse_group_row(row: dict[str, Any]) -> WafRequestGroup | None:
    """One volume row as a group, or None when it has no usable request count."""
    count = as_int(row.get("request_count"))
    if count is None or count < 0:
        return None
    return WafRequestGroup(
        raw_url=as_text(row.get("raw_url"), limit=2048),
        raw_method=as_text(row.get("raw_method"), limit=32),
        raw_virtual_host=as_text(row.get("raw_virtual_host"), limit=255),
        request_count=count,
        record_count=as_int(row.get("record_count")) or 0,
        distinct_client_count=as_int(row.get("distinct_clients")),
    )


def parse_breakdown_row(row: dict[str, Any]) -> WafBreakdownGroup | None:
    """One breakdown row as a group, or None when it has no usable request count."""
    count = as_int(row.get("request_count"))
    if count is None or count < 0:
        return None
    return WafBreakdownGroup(
        raw_url=as_text(row.get("raw_url"), limit=2048),
        raw_method=as_text(row.get("raw_method"), limit=32),
        raw_virtual_host=as_text(row.get("raw_virtual_host"), limit=255),
        raw_value=as_text(row.get("raw_value"), limit=255),
        request_count=count,
    )


def _bucket_starts(
    window_start: datetime, window_end: datetime, bucket_seconds: int
) -> tuple[list[datetime], int]:
    """The bucket starts of a validated window, and the first bucket key."""
    start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
    count = int((end - start).total_seconds()) // bucket_seconds
    starts = [start + timedelta(seconds=bucket_seconds * i) for i in range(count)]
    return starts, int(start.timestamp()) // bucket_seconds


def _bucket_index(row: dict[str, Any], first_key: int, count: int, *, what: str) -> int:
    key = _integer_bucket_key(row.get("k"))
    if key is None:
        raise ProviderError(f"QRadar Ariel WAF {what} row had a non-integer bucket key")
    index = key - first_key
    if not 0 <= index < count:
        raise ProviderError(f"QRadar Ariel WAF {what} row had a bucket key outside the window")
    return index


def fold_observation_buckets(
    group_rows: Sequence[dict[str, Any]],
    total_rows: Sequence[dict[str, Any]],
    *,
    qradar_log_source_id: int,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_groups: int,
    query: str | None = None,
    total_query: str | None = None,
) -> dict[datetime, WafObservationBatch]:
    """One `WafObservationBatch` per bucket from a whole keyed volume search.

    Every bucket of the window gets a batch. A bucket with no group row has no
    groups and, with no totals row, a total of 0: the same empty measurement the
    per-bucket search returns. Two totals rows for one bucket are not a
    measurement of anything, so they fail the search.
    """
    starts, first_key = _bucket_starts(window_start, window_end, bucket_seconds)
    grouped: list[list[dict[str, Any]]] = [[] for _ in starts]
    for row in group_rows:
        grouped[_bucket_index(row, first_key, len(starts), what="volume")].append(row)
    totals: dict[int, int] = {}
    for row in total_rows:
        index = _bucket_index(row, first_key, len(starts), what="total")
        if index in totals:
            raise ProviderError("QRadar Ariel WAF total returned two rows for one bucket")
        totals[index] = as_int(row.get("total_events")) or 0

    out: dict[datetime, WafObservationBatch] = {}
    for index, start in enumerate(starts):
        parsed = [parse_group_row(row) for row in grouped[index]]
        # Rows without a usable count sort last, so they are cut first, as
        # `ORDER BY SUM(eventcount) DESC` would leave them.
        ordered = sorted(
            parsed,
            key=lambda g: observation_sort_key(g) if g is not None else _UNUSABLE_LAST,
        )
        kept = [g for g in ordered[:max_groups] if g is not None]
        out[start] = WafObservationBatch(
            qradar_log_source_id=qradar_log_source_id,
            window_start=start,
            window_end=start + timedelta(seconds=bucket_seconds),
            groups=kept,
            total_events=totals.get(index, 0),
            truncated=len(kept) >= max_groups,
            query=query,
            total_query=total_query,
        )
    return out


def fold_breakdown_buckets(
    rows: Sequence[dict[str, Any]],
    *,
    breakdown: str,
    qradar_log_source_id: int,
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
    max_groups: int,
    query: str | None = None,
) -> dict[datetime, WafBreakdownBatch]:
    """One `WafBreakdownBatch` per bucket from a whole keyed breakdown search.

    A bucket is truncated when it holds `max_groups` or more rows, counting rows
    the parser then drops, as the per-bucket query does.
    """
    starts, first_key = _bucket_starts(window_start, window_end, bucket_seconds)
    grouped: list[list[dict[str, Any]]] = [[] for _ in starts]
    for row in rows:
        grouped[_bucket_index(row, first_key, len(starts), what="breakdown")].append(row)

    out: dict[datetime, WafBreakdownBatch] = {}
    for index, start in enumerate(starts):
        parsed: list[WafBreakdownGroup | None] = [parse_breakdown_row(r) for r in grouped[index]]
        ordered = sorted(
            parsed,
            key=lambda g: breakdown_sort_key(g) if g is not None else _UNUSABLE_LAST_BREAKDOWN,
        )
        cut = ordered[:max_groups]
        out[start] = WafBreakdownBatch(
            breakdown=breakdown,
            qradar_log_source_id=qradar_log_source_id,
            window_start=start,
            window_end=start + timedelta(seconds=bucket_seconds),
            groups=[g for g in cut if g is not None],
            truncated=len(cut) >= max_groups,
            query=query,
        )
    return out


__all__ = [
    "breakdown_sort_key",
    "fold_breakdown_buckets",
    "fold_observation_buckets",
    "observation_sort_key",
    "parse_breakdown_row",
    "parse_group_row",
]
