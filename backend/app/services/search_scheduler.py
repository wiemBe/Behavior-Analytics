"""Cron-driven dispatch of stored scheduled searches.

The executor (app/services/search_executor.py) knows how to run one search; this
module decides *which* searches are due and *when* they run again. Kept separate
so the scheduling policy is testable without an Ariel lifecycle.

Policy decisions worth stating explicitly:

  * **Cron is parsed with APScheduler's CronTrigger**, already a pinned
    dependency, rather than a hand-rolled parser. `next_run_at` is persisted so
    the schedule survives a worker restart and two workers agree on tick
    identity.
  * **First sight seeds, it does not run.** A search whose `next_run_at` is
    NULL gets its next fire computed and is *not* executed this cycle, so
    deploying N searches never fires N searches at once.
  * **Missed ticks coalesce.** If the worker was down, an overdue search runs
    once and is then scheduled from *now*, not from the missed instant. Firing
    every backlogged tick would stampede QRadar with searches whose results are
    already stale, which is worse than the gap it would fill.
  * **Overlap is refused.** A search with a run still in flight is skipped
    rather than queued; combined with the run-key unique constraint this makes
    a double-run structurally impossible.
  * **A cycle is bounded** by `search_max_dispatch_per_cycle`.
  * **A bad cron expression never breaks the cycle.** It is reported and
    skipped, leaving the other searches to run.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from typing import Any

from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.search_alerts import SearchAlertEvaluator
from app.core.config import Settings, get_settings
from app.models.enums import ExecutionErrorType, ExecutionStatus
from app.models.search import ScheduledSearch, SearchExecution
from app.services.scheduled_search import ScheduledSearchService
from app.services.search_executor import SearchExecutor

logger = logging.getLogger("app.search_scheduler")


class InvalidCronExpression(ValueError):
    """The stored schedule_cron/schedule_timezone cannot be parsed."""


class CronFloorError(ValueError):
    """A new or changed cron fires too often, or cannot be scheduled at all."""


# Five minutes. The editor's fastest preset, and the floor for any new cron
# (design F.8.2). An existing expression that this request does not change is
# not rechecked.
CRON_FLOOR_SECONDS = 300
# A five-field cron repeats within a year, so 366 days from `now` includes
# every day-of-year at least once. A short gap that exists only on a Feb 29
# falling outside this window is not seen. The horizon stays 366 days: that is
# the bound this floor already had.
CRON_FLOOR_HORIZON = timedelta(days=366)
# Most gaps a legal schedule can have in that horizon: one every
# CRON_FLOOR_SECONDS. A denser schedule fails on an earlier gap, so this cap
# is reached only by a schedule that has already met the floor for the whole
# window. 366 * 24 * 60 * 60 / 300 = 105_408.
CRON_FLOOR_MAX_FIRES = 366 * 24 * 60 * 60 // CRON_FLOOR_SECONDS


@dataclass
class SchedulerReport:
    considered: int = 0
    dispatched: list[str] = field(default_factory=list)
    seeded: list[str] = field(default_factory=list)
    skipped_running: list[str] = field(default_factory=list)
    invalid_cron: list[str] = field(default_factory=list)
    deferred_capacity: list[str] = field(default_factory=list)


def next_fire_time(
    cron: str, timezone: str, *, after: datetime | None, now: datetime
) -> datetime:
    """Next scheduled instant for `cron`, strictly after `after`.

    With `after=None` this is the first fire at or after `now` — used to seed a
    search the scheduler has not seen before.
    """
    try:
        trigger = CronTrigger.from_crontab(cron, timezone=timezone)
    except Exception as exc:  # APScheduler raises bare ValueError/KeyError here
        raise InvalidCronExpression(f"{cron!r} ({timezone}): {exc}") from exc

    fire = trigger.get_next_fire_time(after, now)
    if fire is None:
        raise InvalidCronExpression(f"{cron!r} ({timezone}) has no future fire time")
    return fire.astimezone(UTC)


@dataclass(frozen=True)
class CronFloorCheck:
    """One bounded check of successive `CronTrigger` fires.

    `fires_checked` is the number of gaps examined. It is never more than
    `max_fires`. `min_gap_seconds` is the shortest absolute gap seen, in UTC
    seconds, or None when no gap was examined.
    """

    accepted: bool
    reason: str | None
    fires_checked: int
    min_gap_seconds: float | None


def _gap_seconds_text(gap: float) -> str:
    if gap == int(gap):
        return str(int(gap))
    return f"{gap:.3f}".rstrip("0").rstrip(".")


def check_cron_floor(
    cron: str,
    timezone: str = "UTC",
    *,
    min_seconds: int = CRON_FLOOR_SECONDS,
    now: datetime | None = None,
    horizon: timedelta = CRON_FLOOR_HORIZON,
    max_fires: int | None = None,
) -> CronFloorCheck:
    """Refuse a cron whose successive fires are closer than `min_seconds`.

    Gaps are absolute UTC seconds, from the scheduler's `CronTrigger` (the
    same parser as `next_fire_time`). The floor is about load on QRadar, which
    is real time: a DST fall-back makes wall-clock subtraction zero or
    negative for a schedule that is still five minutes apart in UTC.

    The check stops at `horizon` (default 366 days; see `CRON_FLOOR_HORIZON`)
    or after `max_fires` gaps (default `CRON_FLOOR_MAX_FIRES`, one gap per
    `min_seconds` across that horizon), whichever comes first. A schedule that
    meets the floor has at most that many gaps in the window, so the cap does
    not hide a short gap inside the horizon. A short gap that starts after the
    horizon is not looked at: that is the bound.

    Inside one UTC offset the wall-clock pattern maps linearly onto real time,
    so those gaps come from the hour and minute sets (at most 1,440 values)
    and from the last fire of an allowed day to the first fire of the next
    one. A UTC-offset transition is not linear. Fires on that day, and the
    gap that crosses it, are taken from `CronTrigger` itself. Day-of-month and
    day-of-week use the trigger's own fields, including its Monday-is-0
    numbering and the requirement that both match.
    """
    if min_seconds <= 0:
        raise ValueError("min_seconds must be positive")
    if horizon <= timedelta(0):
        raise ValueError("horizon must be positive")
    default_limit = max(1, int(horizon.total_seconds()) // min_seconds)
    limit = default_limit if max_fires is None else max_fires
    if limit < 1:
        raise ValueError("max_fires must be positive")
    if now is None:
        now = datetime.now(UTC)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    else:
        now = now.astimezone(UTC)

    try:
        trigger = CronTrigger.from_crontab(cron, timezone=timezone)
    except Exception as exc:  # APScheduler raises ValueError; zoneinfo raises its own
        return CronFloorCheck(
            accepted=False,
            reason=(
                f"schedule_cron {cron!r} ({timezone}) is not a valid cron expression: {exc}"
            ),
            fires_checked=0,
            min_gap_seconds=None,
        )

    end = now + horizon
    previous = trigger.get_next_fire_time(None, now)
    if previous is None:
        return CronFloorCheck(
            accepted=False,
            reason=f"schedule_cron {cron!r} ({timezone}) has no future fire time",
            fires_checked=0,
            min_gap_seconds=None,
        )

    previous_utc = _as_utc(previous)
    if previous_utc >= end:
        return CronFloorCheck(accepted=True, reason=None, fires_checked=0, min_gap_seconds=None)

    fast = _CronFloorScan(
        trigger, cron, timezone, end, limit, min_seconds, previous_utc, previous
    ).run()
    if fast is not None:
        return fast
    return _walk_cron_floor(
        trigger, cron, timezone, end, limit, min_seconds, previous, previous_utc
    )


def _floor_result(
    *,
    accepted: bool,
    cron: str,
    timezone: str,
    min_seconds: int,
    checked: int,
    shortest: float | None,
    prev_utc: datetime | None = None,
    nxt_utc: datetime | None = None,
) -> CronFloorCheck:
    if accepted or prev_utc is None or nxt_utc is None or shortest is None:
        return CronFloorCheck(
            accepted=True, reason=None, fires_checked=checked, min_gap_seconds=shortest
        )
    gap = (nxt_utc - prev_utc).total_seconds()
    return CronFloorCheck(
        accepted=False,
        reason=(
            f"schedule_cron {cron!r} ({timezone}) fires {_gap_seconds_text(gap)} "
            f"seconds apart ({prev_utc.isoformat()} then {nxt_utc.isoformat()}); "
            f"a search must fire at least {min_seconds} seconds apart"
        ),
        fires_checked=checked,
        min_gap_seconds=shortest,
    )


def _walk_cron_floor(
    trigger: CronTrigger,
    cron: str,
    timezone: str,
    end: datetime,
    limit: int,
    min_seconds: int,
    previous: datetime,
    previous_utc: datetime,
) -> CronFloorCheck:
    """Fire-by-fire walk. Fallback for a schedule the linear scan cannot represent.

    A five-field crontab fires on whole minutes, so the scan covers it. This
    remains for a trigger whose seconds are not 0, and for a stable day whose
    UTC instant does not match the linear map.
    """
    checked = 0
    shortest: float | None = None
    while checked < limit:
        nxt = trigger.get_next_fire_time(previous, previous)
        if nxt is None:
            return _floor_result(
                accepted=True, cron=cron, timezone=timezone, min_seconds=min_seconds,
                checked=checked, shortest=shortest,
            )
        nxt_utc = _as_utc(nxt)
        gap = (nxt_utc - previous_utc).total_seconds()
        checked += 1
        if shortest is None or gap < shortest:
            shortest = gap
        if gap < min_seconds:
            return _floor_result(
                accepted=False, cron=cron, timezone=timezone, min_seconds=min_seconds,
                checked=checked, shortest=shortest, prev_utc=previous_utc, nxt_utc=nxt_utc,
            )
        if nxt_utc >= end:
            return _floor_result(
                accepted=True, cron=cron, timezone=timezone, min_seconds=min_seconds,
                checked=checked, shortest=shortest,
            )
        previous = nxt
        previous_utc = nxt_utc
    return _floor_result(
        accepted=True, cron=cron, timezone=timezone, min_seconds=min_seconds,
        checked=checked, shortest=shortest,
    )


def _wall_minutes(trigger: CronTrigger) -> list[int] | None:
    """Sorted minute-of-day values the hour and minute fields allow.

    None when a fire is not on second 0. The linear map is minute resolution;
    anything finer is walked instead.
    """
    fields = {field.name: field for field in trigger.fields}
    sample = datetime(2001, 1, 1)
    seconds = [
        second
        for second in range(60)
        if fields["second"].get_next_value(sample.replace(second=second)) == second
    ]
    if seconds != [0]:
        return None
    minutes: list[int] = []
    for hour in range(24):
        hour_at = sample.replace(hour=hour)
        if fields["hour"].get_next_value(hour_at) != hour:
            continue
        for minute in range(60):
            minute_at = hour_at.replace(minute=minute)
            if fields["minute"].get_next_value(minute_at) == minute:
                minutes.append(hour * 60 + minute)
    return minutes


def _day_allowed(fields: dict[str, Any], day: date) -> bool:
    """True when this calendar day matches the trigger's date fields.

    `CronTrigger` requires every field, so a restricted day-of-month and a
    restricted day-of-week must both match. Naive wall time: the fields do not
    read the clock or the offset.
    """
    wall = datetime(day.year, day.month, day.day)
    for name in ("year", "month", "day", "week", "day_of_week"):
        field = fields[name]
        if field.get_next_value(wall) != field.get_value(wall):
            return False
    return True


def _day_is_stable(tz: tzinfo, day: date) -> bool:
    """True when this local calendar day is one uninterrupted UTC offset.

    A spring-forward shortens the day and a fall-back lengthens it. Sampling
    the offset, and requiring the next local midnight to be exactly one UTC
    day later, catches both. The linear map is valid only on a stable day.
    """
    start = datetime(day.year, day.month, day.day, tzinfo=tz)
    noon = datetime(day.year, day.month, day.day, 12, tzinfo=tz)
    late = datetime(day.year, day.month, day.day, 23, tzinfo=tz)
    start_off = start.utcoffset()
    if start_off is None or noon.utcoffset() != start_off or late.utcoffset() != start_off:
        return False
    nxt = day + timedelta(days=1)
    end_local = datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
    return end_local.astimezone(UTC) - start.astimezone(UTC) == timedelta(days=1)


class _FloorCursor:
    """Where the scan is, and how many gaps it has already accepted."""

    __slots__ = ("checked", "day", "index", "prev_utc", "shortest")

    def __init__(self, prev_utc: datetime, day: date, index: int) -> None:
        self.prev_utc = prev_utc
        self.day = day
        self.index = index
        self.checked = 0
        self.shortest: float | None = None

    def note(self, gap: float) -> None:
        self.checked += 1
        if self.shortest is None or gap < self.shortest:
            self.shortest = gap


class _CronFloorScan:
    """Gaps of one cron over the horizon, without a fire-by-fire walk.

    `run` returns None when the schedule is not a whole-minute pattern or a
    stable day does not match the linear map. The caller walks those with
    `CronTrigger`.
    """

    def __init__(
        self,
        trigger: CronTrigger,
        cron: str,
        timezone: str,
        end: datetime,
        limit: int,
        min_seconds: int,
        first_utc: datetime,
        first_fire: datetime,
    ) -> None:
        self._trigger = trigger
        self._cron = cron
        self._timezone = timezone
        self._end = end
        self._limit = limit
        self._min_seconds = min_seconds
        self._first_utc = first_utc
        # The trigger's own datetime, fold included. The first step of a walk
        # that starts on this fire must use it: passing the UTC instant back
        # in drops the fold and CronTrigger can return that same instant.
        self._seed: datetime | None = first_fire
        self._tz: tzinfo = trigger.timezone
        self._fields: dict[str, Any] = {field.name: field for field in trigger.fields}
        self._minutes: list[int] = []
        self._intra: list[float] = []
        self._suffix_min: list[float] = []
        self._index_of: dict[int, int] = {}
        self._stable: dict[date, bool] = {}
        self._allowed: dict[date, bool] = {}
        self._midnight: dict[date, datetime] = {}
        self._offset: dict[date, timedelta | None] = {}
        self._cursor = _FloorCursor(first_utc, first_utc.date(), 0)

    def run(self) -> CronFloorCheck | None:
        minutes = _wall_minutes(self._trigger)
        if not minutes:
            return None
        self._minutes = minutes
        self._index_of = {minute: index for index, minute in enumerate(minutes)}
        self._intra = [
            float(minutes[index] - minutes[index - 1]) * 60.0
            for index in range(1, len(minutes))
        ]
        self._suffix_min = self._suffix_minima(self._intra)

        local = self._first_utc.astimezone(self._tz)
        minute = local.hour * 60 + local.minute
        index = self._index_of.get(minute)
        if index is None or local.second != 0 or local.microsecond != 0:
            return None
        if self._is_stable(local.date()) and self._fire_utc(local.date(), index) != self._first_utc:
            # The offset is constant and the instant still disagrees. Do not
            # trust the map for the rest of the horizon.
            return None

        search_until = self._end.astimezone(self._tz).date() + timedelta(days=1)
        self._cursor = _FloorCursor(self._first_utc, local.date(), index)
        walking = not self._is_stable(local.date())
        while True:
            if walking:
                landed = self._walk_until_stable()
                if isinstance(landed, CronFloorCheck):
                    return landed
                walking = False
                continue
            consumed = self._consume_stable_day()
            if isinstance(consumed, CronFloorCheck):
                return consumed
            if consumed == "fallback":
                return None
            nxt_day = self._next_allowed(self._cursor.day, search_until)
            if nxt_day is None:
                return self._one_trigger_step()
            # An offset change on a day that does not itself fire can still
            # make CronTrigger skip the next matching date. Cross analytically
            # only when every day in between shares one offset.
            if not self._cross_is_linear(self._cursor.day, nxt_day):
                walking = True
                continue
            nxt_utc = self._fire_utc(nxt_day, 0)
            decision = self._decide(nxt_utc)
            if decision is not None:
                return decision
            self._cursor.prev_utc = nxt_utc
            self._cursor.day = nxt_day
            self._cursor.index = 0

    def _suffix_minima(self, gaps: list[float]) -> list[float]:
        if not gaps:
            return []
        suffix = [0.0] * len(gaps)
        suffix[-1] = gaps[-1]
        for index in range(len(gaps) - 2, -1, -1):
            suffix[index] = min(gaps[index], suffix[index + 1])
        return suffix

    def _is_stable(self, day: date) -> bool:
        cached = self._stable.get(day)
        if cached is None:
            cached = _day_is_stable(self._tz, day)
            self._stable[day] = cached
        return cached

    def _day_offset(self, day: date) -> timedelta | None:
        cached = self._offset.get(day)
        if day not in self._offset:
            noon = datetime(day.year, day.month, day.day, 12, tzinfo=self._tz)
            cached = noon.utcoffset()
            self._offset[day] = cached
        return cached

    def _cross_is_linear(self, start: date, end: date) -> bool:
        """True when the gap from `start` to `end` is one constant UTC offset."""
        if not self._is_stable(start) or not self._is_stable(end):
            return False
        offset = self._day_offset(start)
        if offset is None or self._day_offset(end) != offset:
            return False
        day = start + timedelta(days=1)
        while day < end:
            if not self._is_stable(day) or self._day_offset(day) != offset:
                return False
            day += timedelta(days=1)
        return True

    def _is_allowed(self, day: date) -> bool:
        cached = self._allowed.get(day)
        if cached is None:
            cached = _day_allowed(self._fields, day)
            self._allowed[day] = cached
        return cached

    def _midnight_utc(self, day: date) -> datetime:
        cached = self._midnight.get(day)
        if cached is None:
            cached = datetime(day.year, day.month, day.day, tzinfo=self._tz).astimezone(UTC)
            self._midnight[day] = cached
        return cached

    def _fire_utc(self, day: date, index: int) -> datetime:
        return self._midnight_utc(day) + timedelta(minutes=self._minutes[index])

    def _next_allowed(self, after: date, until: date) -> date | None:
        day = after + timedelta(days=1)
        while day <= until:
            if self._is_allowed(day):
                return day
            day += timedelta(days=1)
        return None

    def _decide(self, nxt_utc: datetime) -> CronFloorCheck | None:
        """Count the gap ending at `nxt_utc`. None means the scan continues."""
        prev_utc = self._cursor.prev_utc
        gap = (nxt_utc - prev_utc).total_seconds()
        self._cursor.note(gap)
        if gap < self._min_seconds:
            return self._fail(prev_utc, nxt_utc)
        if nxt_utc >= self._end or self._cursor.checked >= self._limit:
            return self._accept()
        return None

    def _accept(self) -> CronFloorCheck:
        return _floor_result(
            accepted=True, cron=self._cron, timezone=self._timezone,
            min_seconds=self._min_seconds, checked=self._cursor.checked,
            shortest=self._cursor.shortest,
        )

    def _fail(self, prev_utc: datetime, nxt_utc: datetime) -> CronFloorCheck:
        return _floor_result(
            accepted=False, cron=self._cron, timezone=self._timezone,
            min_seconds=self._min_seconds, checked=self._cursor.checked,
            shortest=self._cursor.shortest, prev_utc=prev_utc, nxt_utc=nxt_utc,
        )

    def _note_many(self, gaps: list[float]) -> None:
        if not gaps:
            return
        smallest = min(gaps)
        if self._cursor.shortest is None or smallest < self._cursor.shortest:
            self._cursor.shortest = smallest
        self._cursor.checked += len(gaps)

    def _consume_stable_day(self) -> CronFloorCheck | str | None:
        """Count the remaining gaps on the cursor's day.

        Returns a finished check, ``"fallback"`` when the linear instant
        disagrees, or None when the cursor now sits on the day's last fire.
        """
        day = self._cursor.day
        start = self._cursor.index
        if self._fire_utc(day, start) != self._cursor.prev_utc:
            return "fallback"
        remaining = self._intra[start:]
        if not remaining:
            return None

        short_at: int | None = None
        if self._suffix_min[start] < self._min_seconds:
            short_at = next(
                index for index, gap in enumerate(remaining) if gap < self._min_seconds
            )
        reach = self._first_gap_reaching_end(day, start, len(remaining))
        room = self._limit - self._cursor.checked
        if room <= 0:
            return self._accept()
        # The earliest stop wins. On the same gap a short interval is reported
        # before the horizon and the cap, matching the fire-by-fire walk.
        last = len(remaining) - 1
        candidates: list[tuple[int, str]] = [(last, "day"), (room - 1, "limit")]
        if short_at is not None:
            candidates.append((short_at, "short"))
        if reach is not None:
            candidates.append((reach, "end"))
        priority = {"short": 0, "end": 1, "limit": 2, "day": 3}
        stop, kind = min(candidates, key=lambda item: (item[0], priority[item[1]]))
        self._note_many(remaining[: stop + 1])
        if kind == "short":
            prev_utc = self._fire_utc(day, start + stop)
            nxt_utc = self._fire_utc(day, start + stop + 1)
            return self._fail(prev_utc, nxt_utc)
        if kind != "day":
            return self._accept()

        self._cursor.index = len(self._minutes) - 1
        self._cursor.prev_utc = self._fire_utc(day, self._cursor.index)
        return None

    def _first_gap_reaching_end(self, day: date, start: int, count: int) -> int | None:
        """Index of the first remaining gap whose later fire is at or past the horizon."""
        base = self._midnight_utc(day)
        lo = 0
        hi = count - 1
        found: int | None = None
        while lo <= hi:
            mid = (lo + hi) // 2
            stamp = base + timedelta(minutes=self._minutes[start + 1 + mid])
            if stamp >= self._end:
                found = mid
                hi = mid - 1
            else:
                lo = mid + 1
        return found

    def _walk_until_stable(self) -> CronFloorCheck | None:
        """Trigger fires across an offset change, then hand back a stable instant.

        None means the cursor is on a stable fire and the linear scan can
        resume. The trigger datetime is kept after the first step so a folded
        hour is not collapsed back to one instant.
        """
        # Use the trigger's folded datetime only while the cursor is still the
        # first fire. A later walk starts from an unambiguous stable instant.
        prev_arg: datetime = self._cursor.prev_utc
        if self._seed is not None and self._cursor.prev_utc == self._first_utc:
            prev_arg = self._seed
        self._seed = None
        while True:
            nxt = self._trigger.get_next_fire_time(prev_arg, prev_arg)
            if nxt is None:
                return self._accept()
            nxt_utc = _as_utc(nxt)
            decision = self._decide(nxt_utc)
            if decision is not None:
                return decision
            local = nxt.astimezone(self._tz)
            minute = local.hour * 60 + local.minute
            index = self._index_of.get(minute)
            self._cursor.prev_utc = nxt_utc
            prev_arg = nxt
            if (
                index is None
                or local.second != 0
                or local.microsecond != 0
                or not self._is_stable(local.date())
            ):
                continue
            if self._fire_utc(local.date(), index) != nxt_utc:
                continue
            self._cursor.day = local.date()
            self._cursor.index = index
            return None

    def _one_trigger_step(self) -> CronFloorCheck | None:
        """The next allowed day is outside the horizon. Count that one gap.

        If the trigger's next fire is still inside the horizon, the day scan
        stopped short. None sends the caller back to the fire-by-fire walk.
        """
        nxt = self._trigger.get_next_fire_time(self._cursor.prev_utc, self._cursor.prev_utc)
        if nxt is None:
            return self._accept()
        nxt_utc = _as_utc(nxt)
        if nxt_utc < self._end:
            # The next fire is still inside the window. The day scan stopped
            # short of it, so the fire-by-fire walk has to count from the start.
            return None
        return self._decide(nxt_utc)


class SearchScheduler:
    def __init__(
        self,
        session: AsyncSession,
        executor: SearchExecutor,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        alert_evaluator: SearchAlertEvaluator | None = None,
    ) -> None:
        self.session = session
        self.executor = executor
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.alerts = alert_evaluator

    async def run_due(self, *, now: datetime | None = None) -> SchedulerReport:
        """One cycle. No transaction stays open across an Ariel search.

        Per due search: claim it (row lock, due and in-flight re-check, move
        `next_run_at`, PENDING row) and commit; run the search with no row lock
        held; record the result and commit. The caller must not run two cycles at
        once (`run_due_searches` holds a PostgreSQL advisory lock for that).
        """
        now = now or self._clock()
        report = SchedulerReport()

        candidates = [
            (row.id, row.name)
            for row in (
                await self.session.execute(
                    select(ScheduledSearch.id, ScheduledSearch.name)
                    .where(ScheduledSearch.enabled.is_(True))
                    .order_by(ScheduledSearch.next_run_at.nulls_first())
                )
            ).all()
        ]
        await self.session.commit()  # the listing's read transaction ends here
        report.considered = len(candidates)

        for index, (search_id, name) in enumerate(candidates):
            if len(report.dispatched) >= self.settings.search_max_dispatch_per_cycle:
                break
            if await self._process(search_id, name, now, report):
                # Only the searches this cycle would still have dispatched: the
                # slots left after the ones already sent, skipping a run that is
                # already in flight and a cron the cycle would not dispatch.
                remaining = self.settings.search_max_dispatch_per_cycle - len(
                    report.dispatched
                )
                deferred: list[str] = []
                for candidate_id, _candidate_name in candidates[index + 1 :]:
                    if len(deferred) >= remaining:
                        break
                    candidate = await self.session.get(
                        ScheduledSearch, candidate_id, populate_existing=True
                    )
                    if candidate is None:
                        continue
                    if (
                        candidate.next_run_at is None
                        or _as_utc(candidate.next_run_at) > now
                    ):
                        continue
                    try:
                        next_fire_time(
                            candidate.schedule_cron,
                            candidate.schedule_timezone,
                            after=None,
                            now=now,
                        )
                    except InvalidCronExpression:
                        continue
                    if await self._has_run_in_flight(candidate, now):
                        continue
                    deferred.append(candidate.name)
                report.deferred_capacity.extend(deferred)
                if report.deferred_capacity:
                    logger.warning(
                        "Ariel capacity refused a scheduled search; deferred %d due searches",
                        len(report.deferred_capacity),
                        extra={"deferred_count": len(report.deferred_capacity)},
                    )
                break

        await self.session.commit()
        return report

    async def _process(
        self, search_id: uuid.UUID, name: str, now: datetime, report: SchedulerReport
    ) -> bool:
        """Claim, run and record one search. True when Ariel capacity refused it."""
        claim = await self._claim(search_id, name, now, report)
        if claim is None:
            return False
        search, scheduled_for, run_key = claim
        try:
            execution = await self.executor.execute(
                search, run_key=run_key, instance_key="default", trigger="SCHEDULED"
            )
        except Exception:
            # The claim is committed; what the executor left stays as it is.
            await self.session.rollback()
            raise
        return await self._record(search_id, name, execution, scheduled_for, report)

    async def _claim(
        self, search_id: uuid.UUID, name: str, now: datetime, report: SchedulerReport
    ) -> tuple[ScheduledSearch, datetime, str] | None:
        """Take one search in a short transaction and commit the claim.

        The row lock (`SKIP LOCKED`), the due and in-flight re-checks, the
        `next_run_at` move and the PENDING execution row commit together, so a
        later reader sees either the whole claim or none of it.
        """
        try:
            claim = await self._claim_locked(search_id, name, now, report)
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        return claim

    async def _claim_locked(
        self, search_id: uuid.UUID, name: str, now: datetime, report: SchedulerReport
    ) -> tuple[ScheduledSearch, datetime, str] | None:
        search = await self.session.scalar(
            select(ScheduledSearch)
            .where(ScheduledSearch.id == search_id)
            .with_for_update(skip_locked=True)
            .execution_options(populate_existing=True)
        )
        if search is None:
            # Locked by someone else, or deleted since the listing.
            exists = await self.session.scalar(
                select(ScheduledSearch.id).where(ScheduledSearch.id == search_id)
            )
            if exists is not None:
                report.skipped_running.append(name)
            return None
        if not search.enabled:
            return None

        try:
            # Unseen search: compute the next fire and wait for it.
            if search.next_run_at is None:
                search.next_run_at = next_fire_time(
                    search.schedule_cron, search.schedule_timezone, after=None, now=now
                )
                report.seeded.append(search.name)
                return None

            if _as_utc(search.next_run_at) > now:
                return None

            if await self._has_run_in_flight(search, now):
                report.skipped_running.append(search.name)
                return None

            scheduled_for = _as_utc(search.next_run_at)
            # Reschedule from `now`, not from the missed tick, so a backlog
            # coalesces into one run instead of replaying every tick.
            search.next_run_at = next_fire_time(
                search.schedule_cron, search.schedule_timezone, after=now, now=now
            )
        except InvalidCronExpression as exc:
            # Leave next_run_at as-is so fixing the expression resumes the
            # schedule; a broken cron must not take the whole cycle down.
            logger.warning(
                "invalid cron for scheduled search",
                extra={"search": search.name, "error": str(exc)},
            )
            report.invalid_cron.append(search.name)
            return None

        run_key = ScheduledSearchService.scheduled_run_key(search, scheduled_for)
        existing = await self.session.scalar(
            select(SearchExecution.id).where(
                SearchExecution.search_id == search.id, SearchExecution.run_key == run_key
            )
        )
        if existing is None:
            self.session.add(
                SearchExecution(
                    search_id=search.id,
                    run_key=run_key,
                    trigger="SCHEDULED",
                    query_version=search.query_version,
                    status=ExecutionStatus.PENDING,
                )
            )
        return search, scheduled_for, run_key

    async def _record(
        self,
        search_id: uuid.UUID,
        name: str,
        execution: SearchExecution,
        scheduled_for: datetime,
        report: SchedulerReport,
    ) -> bool:
        """Write the result of one sent search and commit it on its own.

        A failure here is logged and rolled back for this search only: the
        searches already committed stay committed.
        """
        capacity = execution.error_type == ExecutionErrorType.CAPACITY.value
        report.dispatched.append(name)
        try:
            search = await self.session.get(
                ScheduledSearch, search_id, populate_existing=True
            )
            if search is not None:
                search.last_run_at = scheduled_for
                if execution.status == ExecutionStatus.COMPLETED:
                    search.consecutive_failures = 0
                else:
                    search.consecutive_failures += 1
                await self.session.flush()
                if self.alerts is not None:
                    await self.alerts.evaluate(search, execution)
            await self.session.commit()
        except Exception:
            logger.exception(
                "could not record the result of a scheduled search",
                extra={"search": name},
            )
            await self.session.rollback()
        return capacity

    async def _has_run_in_flight(self, search: ScheduledSearch, now: datetime) -> bool:
        """True if a previous run is still going.

        Bounded by age: a worker killed mid-execution leaves a row stuck in
        RUNNING forever, and treating that as live would silently retire the
        search. Past twice its own timeout a run is presumed dead and no longer
        blocks the schedule — the run-key unique constraint still prevents the
        replacement run from duplicating a row.
        """
        stale_before = now - timedelta(seconds=search.timeout_seconds * 2)
        candidates = await self.session.scalars(
            select(SearchExecution).where(
                SearchExecution.search_id == search.id,
                SearchExecution.status.in_(
                    [ExecutionStatus.PENDING, ExecutionStatus.RUNNING]
                ),
            )
        )
        for execution in candidates:
            started = execution.started_at or execution.created_at
            if started is None or _as_utc(started) > stale_before:
                return True
        return False


def _as_utc(value: datetime) -> datetime:
    """Postgres returns tz-aware values; SQLite hands back naive ones. Compare
    everything in UTC so the two backends agree on whether a tick is due."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
