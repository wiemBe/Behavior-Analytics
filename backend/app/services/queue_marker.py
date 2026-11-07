"""The per-user queue marker (SOC-4a): read it, and move it forward under a compare-and-set.

The marker is the caller's own row, keyed by principal subject. The only write is `advance`,
and it writes this application's own table only. `GET /queue` calls `get_marker` and nothing
else here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException
from app.models.queue_marker import UserQueueMarker

#: How far ahead of the server clock an `as_of` may be before it is refused.
MAX_SKEW = timedelta(minutes=2)


@dataclass(frozen=True)
class MarkerState:
    marked_at: datetime
    previous_marked_at: datetime | None


@dataclass(frozen=True)
class AdvanceResult:
    state: MarkerState
    advanced: bool


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _wire(state: MarkerState | None) -> dict[str, object]:
    return {
        "marker": {
            "marked_at": _iso(state.marked_at) if state else None,
            "previous_marked_at": _iso(state.previous_marked_at) if state else None,
        }
    }


async def get_marker(session: AsyncSession, subject: str) -> datetime | None:
    """The caller's stored marker, or None when they have never advanced it."""
    return await session.scalar(
        select(UserQueueMarker.marked_at).where(UserQueueMarker.owner_subject == subject)
    )


async def _current(session: AsyncSession, subject: str) -> MarkerState | None:
    row = (
        await session.execute(
            select(UserQueueMarker.marked_at, UserQueueMarker.previous_marked_at).where(
                UserQueueMarker.owner_subject == subject
            )
        )
    ).first()
    return None if row is None else MarkerState(row[0], row[1])


def _changed(current: MarkerState | None) -> CodedHTTPException:
    return CodedHTTPException(409, MessageRef("queue.marker.changed"), fields=_wire(current))


async def advance(
    session: AsyncSession,
    subject: str,
    *,
    as_of: datetime,
    expected: datetime | None,
    all_sources_ok: bool,
    now: datetime | None = None,
) -> AdvanceResult:
    """Move the caller's marker to `as_of` when `expected` is still the stored value.

    Refusals write nothing: a source that failed (409), an `as_of` beyond the clock skew (422)
    and a stale `expected` (409, carrying the current marker). An `as_of` not after the stored
    marker is a no-op that returns the stored value, so two tabs opening in sequence cannot
    move it back. The row is locked while it is compared, so of two concurrent requests with
    the same `expected` exactly one wins.
    """
    if not all_sources_ok:
        current = await _current(session, subject)
        raise CodedHTTPException(
            409, MessageRef("queue.marker.source_failed"), fields=_wire(current)
        )
    clock = now or datetime.now(UTC)
    as_of = _utc(as_of)
    if as_of > clock + MAX_SKEW:
        raise CodedHTTPException(422, MessageRef("queue.marker.future"))
    expected = None if expected is None else _utc(expected)

    row = (
        await session.execute(
            select(UserQueueMarker.marked_at, UserQueueMarker.previous_marked_at)
            .where(UserQueueMarker.owner_subject == subject)
            .with_for_update()
        )
    ).first()
    if row is None:
        if expected is not None:
            raise _changed(None)
        inserted = await session.execute(
            pg_insert(UserQueueMarker)
            .values(owner_subject=subject, marked_at=as_of)
            .on_conflict_do_nothing(index_elements=[UserQueueMarker.owner_subject])
            .returning(UserQueueMarker.owner_subject)
        )
        if inserted.first() is None:
            # A concurrent first write won between our read and our insert.
            raise _changed(await _current(session, subject))
        return AdvanceResult(MarkerState(as_of, None), advanced=True)

    current = MarkerState(row[0], row[1])
    if expected != current.marked_at:
        raise _changed(current)
    if as_of <= current.marked_at:
        return AdvanceResult(current, advanced=False)
    await session.execute(
        update(UserQueueMarker)
        .where(UserQueueMarker.owner_subject == subject)
        .values(marked_at=as_of, previous_marked_at=current.marked_at)
    )
    return AdvanceResult(MarkerState(as_of, current.marked_at), advanced=True)
