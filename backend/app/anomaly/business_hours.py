"""Whether a log source is expected to be sending events in a bucket.

The bucket start is stored in UTC. The window is the source's
`timezone_name` local time, not the UTC hour. Suppression is the dangerous
direction: a business-hours-only source that is judged inactive does not raise
`NO_EVENTS` and its off-hours buckets never enter a baseline, so a configuration
that cannot be trusted counts as expected-active.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.models.log_source import LogSource

logger = logging.getLogger("app.anomaly.business_hours")

#: Sources already warned in this process. One line per source, not per bucket.
_warned_sources: set[uuid.UUID] = set()


class InvalidBusinessDays(ValueError):
    """`business_days` is empty or not a list of ISO weekdays 1-7."""


class InvalidBusinessHours(ValueError):
    """`business_hours_start` or `business_hours_end` is not an hour 0-23."""


def is_expected_active(log_source: LogSource, when: datetime) -> bool:
    """Whether silence at `when` would be an outage for this source.

    `when` is a bucket start, stored in UTC. The window is evaluated in
    `log_source.timezone_name`: the local hour and the local ISO weekday of
    that instant. `business_hours_end` is exclusive, so 09-18 is 09:00-17:59.
    A window with `start == end` covers the whole local day. A window with
    `start > end` wraps past midnight (22-6 is 22:00-05:59) and belongs to the
    local day it starts on: 05:00 Tuesday is Monday's window, and it is
    admitted only when Monday is a business day.

    A source that is not `business_hours_only` is always expected-active. An
    unknown zone name, an empty or out-of-range `business_days`, or a start or
    end outside 0-23 cannot be trusted and also counts as expected-active. The
    first such source logs one warning in the process (the failure class and
    the source id); later buckets do not repeat it.
    """
    if not log_source.business_hours_only:
        return True

    zone, zone_error = _load_zone(log_source.timezone_name)
    if zone is None:
        _warn_once(log_source, zone_error or ZoneInfoNotFoundError.__name__)
        return True

    days = _trusted_days(log_source.business_days)
    if days is None:
        _warn_once(log_source, InvalidBusinessDays.__name__)
        return True

    start = _hour(log_source.business_hours_start)
    end = _hour(log_source.business_hours_end)
    if start is None or end is None:
        _warn_once(log_source, InvalidBusinessHours.__name__)
        return True

    local = _as_utc(when).astimezone(zone)
    return _window_covers(local, start, end, days)


def _warn_once(log_source: LogSource, error_class: str) -> None:
    source_id = log_source.id
    if source_id in _warned_sources:
        return
    _warned_sources.add(source_id)
    logger.warning(
        "%s: business-hours configuration for log source %s cannot be trusted; "
        "treating the source as expected active",
        error_class,
        source_id,
    )


def _load_zone(name: object) -> tuple[ZoneInfo | None, str | None]:
    if not isinstance(name, str):
        return None, ZoneInfoNotFoundError.__name__
    try:
        return ZoneInfo(name), None
    except ZoneInfoNotFoundError:
        return None, ZoneInfoNotFoundError.__name__
    except ValueError:
        return None, ValueError.__name__


def _trusted_days(value: object) -> set[int] | None:
    if not isinstance(value, list) or len(value) == 0:
        return None
    days: set[int] = set()
    for day in value:
        if type(day) is not int or not 1 <= day <= 7:
            return None
        days.add(day)
    return days


def _hour(value: object) -> int | None:
    if type(value) is not int or not 0 <= value <= 23:
        return None
    return value


def _as_utc(when: datetime) -> datetime:
    if when.tzinfo is None:
        return when.replace(tzinfo=UTC)
    return when.astimezone(UTC)


def _window_covers(local: datetime, start: int, end: int, days: set[int]) -> bool:
    """Whether `local` falls inside the window.

    A wrapped window belongs to the day it starts on. The hour after midnight
    is admitted by the previous local weekday, not by the weekday of `local`.
    """
    hour = local.hour
    if start == end:
        return local.isoweekday() in days
    if start < end:
        return local.isoweekday() in days and start <= hour < end
    if hour >= start:
        return local.isoweekday() in days
    if hour < end:
        previous = local - timedelta(days=1)
        return previous.isoweekday() in days
    return False
