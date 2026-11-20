"""Edge-written QRadar-disabled spans (design §5): open, close and overlap helpers.

DB helpers on the caller's session; none commits. An empty batch issues no statement:
on a stale inventory or a `None` enabled the caller passes nothing, so no row results.
Spans are half-open `[starts_at, ends_at)`, `ends_at` NULL while open; datetimes aware.
"""
from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import DateTime, Exists, cast, exists, func, literal, literal_column, select, update
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models.log_source_pause import LogSourcePause as Pause

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_TZ = DateTime(timezone=True)
_NEVER = literal_column("'infinity'::timestamptz", _TZ)


class PauseSpan(NamedTuple):
    log_source_id: UUID
    reason: str
    starts_at: datetime
    ends_at: datetime | None


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("pause timestamps must be timezone-aware")
    return value


def disable_edge_start(
    previous_complete_watermark: datetime | None, first_seen_at: datetime, now: datetime
) -> datetime:
    """The previous complete watermark, else `first_seen_at` (the later of the two when
    the source was first seen after the watermark); never later than `now`."""
    start = _aware(first_seen_at)
    if previous_complete_watermark is not None:
        start = max(start, _aware(previous_complete_watermark))
    return min(start, _aware(now))


async def open_pause(
    session: AsyncSession,
    source_ids: Iterable[UUID],
    *,
    reason: str,
    starts_at: datetime,
    now: datetime,
) -> int:
    """Open one span per source in a single statement; idempotent (returns rows opened).

    `starts_at` is bounded to `now` and moved up to the source's latest closed `ends_at`
    (a re-disable at an unchanged watermark neither overlaps nor clashes); an open span stays.
    """
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return 0
    wanted = literal(_aware(starts_at), _TZ)
    sid = func.unnest(cast(ids, ARRAY(PgUUID(as_uuid=True)))).column_valued("id")
    closed = (
        select(func.max(Pause.ends_at))
        .where(Pause.log_source_id == sid, Pause.reason == reason)
        .scalar_subquery()
    )
    start = func.least(
        literal(_aware(now), _TZ), func.greatest(wanted, func.coalesce(closed, wanted))
    )
    statement = (
        pg_insert(Pause)
        .from_select(["log_source_id", "reason", "starts_at"], select(sid, literal(reason), start))
        .on_conflict_do_nothing(
            index_elements=[Pause.log_source_id, Pause.reason], index_where=Pause.ends_at.is_(None)
        )
        .returning(Pause.log_source_id)
    )
    return len((await session.execute(statement)).all())


async def close_pause(
    session: AsyncSession,
    source_ids: Iterable[UUID],
    *,
    reason: str,
    observed_at: datetime,
    bucket_seconds: int,
) -> int:
    """Close each open span in a single statement; idempotent (returns rows closed).

    `ends_at` is the observation rounded **up** to a bucket boundary (an exact boundary
    stays itself), at least one second after `starts_at`.
    """
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return 0
    step = timedelta(seconds=bucket_seconds)
    end = _EPOCH + -((_EPOCH - _aware(observed_at)) // step) * step
    statement = (
        update(Pause)
        .where(Pause.log_source_id.in_(ids), Pause.reason == reason, Pause.ends_at.is_(None))
        .values(ends_at=func.greatest(end, Pause.starts_at + timedelta(seconds=1)))
        .returning(Pause.log_source_id)
    )
    return len((await session.execute(statement)).all())


def bucket_overlaps_pause(
    source_id: ColumnElement[UUID],
    bucket_start: ColumnElement[datetime],
    bucket_end: ColumnElement[datetime],
) -> Exists:
    """`EXISTS` of a span overlapping the half-open bucket, correlated to the outer row.

    `bucket_start < coalesce(ends_at, 'infinity') AND bucket_end > starts_at`; every
    reason counts. Embed it in an existing SELECT: no query per bucket.
    """
    return exists().where(
        Pause.log_source_id == source_id,
        bucket_start < func.coalesce(Pause.ends_at, _NEVER),
        bucket_end > Pause.starts_at,
    )


async def overlapping_spans(
    session: AsyncSession,
    source_ids: Iterable[UUID],
    window_start: datetime,
    window_end: datetime,
) -> dict[UUID, list[PauseSpan]]:
    """One bulk read: each source's spans overlapping `[window_start, window_end)`."""
    ids = list(dict.fromkeys(source_ids))
    if not ids:
        return {}
    rows = await session.execute(
        select(Pause.log_source_id, Pause.reason, Pause.starts_at, Pause.ends_at)
        .where(
            Pause.log_source_id.in_(ids),
            _aware(window_start) < func.coalesce(Pause.ends_at, _NEVER),
            _aware(window_end) > Pause.starts_at,
        )
        .order_by(Pause.log_source_id, Pause.starts_at)
    )
    spans: dict[UUID, list[PauseSpan]] = {}
    for row in rows:
        spans.setdefault(row.log_source_id, []).append(PauseSpan(*row))
    return spans
