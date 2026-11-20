"""Shared flag access and SQL predicates for the D-52 source-pause policy (P0).

Everything here is pure: the rollout flag and the two predicates the design
names for the pause policy. Nothing calls them yet — S0 registers the switch and
lands these definitions; the later P slices adopt them at their operator entry
points. Off stays legacy behavior, so a caller adds these predicates only after
resolving the switch.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ColumnElement, DateTime, cast, func, literal

from app.core.config import Settings

#: An open pause has no end: half-open overlap uses positive infinity for it.
_INFINITY = cast(literal("infinity"), DateTime(timezone=True))


def source_pause_enabled(settings: Settings) -> bool:
    """The single D-52 rollout flag, read from the resolved effective settings."""
    return settings.qradar_source_pause_enabled


def active_source(enabled: ColumnElement[bool]) -> ColumnElement[bool]:
    """The stored QRadar flag guard, placed beside a caller's monitoring filter."""
    return enabled.is_(True)


def pause_overlaps_bucket(
    bucket_start: ColumnElement[datetime],
    bucket_end: ColumnElement[datetime],
    starts_at: ColumnElement[datetime],
    ends_at: ColumnElement[datetime],
) -> ColumnElement[bool]:
    """Half-open overlap of a pause span and a bucket.

    A bucket overlaps when it starts before the pause ends (an open pause runs
    to infinity) and ends after the pause starts. Exact boundary touches do not
    overlap.
    """
    return (bucket_start < func.coalesce(ends_at, _INFINITY)) & (bucket_end > starts_at)
