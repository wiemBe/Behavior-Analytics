"""Pure validation and folding for keyed Phase A metric searches."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from app.providers.base import ProviderError
from app.providers.dto import LogSourceMetricSample
from app.providers.normalize import as_int, parse_qradar_time


def validate_bucketed_metric_window(
    window_start: datetime,
    window_end: datetime,
    bucket_seconds: int,
) -> tuple[datetime, datetime]:
    """Normalize a bounded window and require epoch-aligned whole buckets."""
    if bucket_seconds <= 0:
        raise ValueError("metric bucket width must be positive")
    start = window_start if window_start.tzinfo else window_start.replace(tzinfo=UTC)
    end = window_end if window_end.tzinfo else window_end.replace(tzinfo=UTC)
    start, end = start.astimezone(UTC), end.astimezone(UTC)
    seconds = (end - start).total_seconds()
    if seconds <= 0:
        raise ValueError("metric window end must be after its start")
    if seconds > 168 * 3600:
        raise ValueError("metric window exceeds the 168-hour safety bound")
    if (
        start.microsecond
        or end.microsecond
        or int(start.timestamp()) % bucket_seconds
        or int(end.timestamp()) % bucket_seconds
        or not seconds.is_integer()
        or int(seconds) % bucket_seconds
    ):
        raise ValueError("metric window must contain epoch-aligned whole buckets")
    return start, end


def _integer_bucket_key(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isascii() and stripped.lstrip("-").isdigit():
            return int(stripped)
    return None


def fold_log_source_metric_buckets(
    rows: Sequence[dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    *,
    bucket_seconds: int,
) -> dict[datetime, list[LogSourceMetricSample]]:
    """Map keyed aggregate rows to validated UTC buckets without side effects."""
    start, end = validate_bucketed_metric_window(window_start, window_end, bucket_seconds)
    bucket_ms = bucket_seconds * 1000
    bucket_count = int((end - start).total_seconds()) // bucket_seconds
    buckets: dict[datetime, list[LogSourceMetricSample]] = {
        start + timedelta(seconds=bucket_seconds * index): []
        for index in range(bucket_count)
    }

    for row in rows:
        key = _integer_bucket_key(row.get("k"))
        if key is None:
            raise ProviderError("QRadar Ariel metric row had a non-integer bucket key")
        try:
            bucket_start = datetime.fromtimestamp((key * bucket_ms) / 1000, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise ProviderError(
                "QRadar Ariel metric row had a bucket key outside the window"
            ) from exc
        if bucket_start not in buckets:
            raise ProviderError("QRadar Ariel metric row had a bucket key outside the window")

        qradar_id = as_int(row.get("qradar_id"))
        count = as_int(row.get("event_count"))
        if qradar_id is None or count is None or count < 0:
            continue
        bucket_end = bucket_start + timedelta(seconds=bucket_seconds)
        last_event = parse_qradar_time(row.get("last_event_time"))
        delay = (
            max(0.0, (bucket_end - last_event).total_seconds())
            if last_event is not None
            else None
        )
        buckets[bucket_start].append(
            LogSourceMetricSample(
                qradar_id=qradar_id,
                bucket_start=bucket_start,
                bucket_seconds=bucket_seconds,
                event_count=count,
                average_eps=count / bucket_seconds,
                last_event_at=last_event,
                event_delay_seconds=delay,
                stored_event_count=count,
            )
        )
    return buckets
