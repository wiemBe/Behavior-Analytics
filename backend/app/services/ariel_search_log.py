"""Record, buffer and summarise Ariel search load. No AQL, no result rows, no raw values.

A note lands only while a task, a search execution, or a preview has opened a
buffer. With no buffer the note is dropped: unit tests call the provider
directly, and the write target is not theirs to choose.

Two kinds of write, both on a connection of their own so a rolled-back
collection transaction cannot take them:

* Per search, so the current load can be read while a search runs: one INSERT
  at submit (outcome ``IN_FLIGHT``), a poll-count UPDATE on every fifth status
  read, and one UPDATE at the terminal state. Each is bounded by the Ariel
  permit cap, which limits how many searches run at once.
* Per run: the buffer flushes once, as one upsert. It inserts what never had a
  submit row (the mock provider, a permit refusal) and closes any row whose
  immediate UPDATE failed.

The reaper (``reap_abandoned``) is a third, maintenance write: one UPDATE
closes the rows whose worker died before the terminal write as ``ABANDONED``,
measured columns left null, and a terminal write that arrives after the reaper
overwrites it with the truth.

A failed write is counted and logged without the exception text. It never
raises into the collection. ``worker_engine`` supplies the connection for all
of these; the test suite replaces that one function, and only that function,
to point them elsewhere. A lookup that raises is a failed write at every one
of these sites, never an exception into the search.

Percentiles (wall and execution) are over COMPLETED rows only: they answer how
long a finished search takes. Sums are the load itself and cover every
terminal row, a TIMEOUT's partial execution time included.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections import defaultdict
from collections.abc import AsyncIterator, Callable, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from contextvars import ContextVar, Token
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import wraps
from typing import TYPE_CHECKING, Any, Literal

from sqlalchemy import BigInteger, ColumnElement, Row, Select, case, func, insert, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.statistics import percentile
from app.i18n.messages import MessageRef, render
from app.models.ariel_search import CALLERS, OUTCOMES, ArielSearchRun

if TYPE_CHECKING:
    # Not imported at run time: the provider modules import this one to declare
    # their callers, and their package would import them back while this module
    # is still half-built (``python -m app.cli.ariel_load`` imports this first).
    from app.providers.dto import ArielSearchStatusDTO
    from app.services.ariel_permit import ArielPermitBudget

logger = logging.getLogger("app.ariel_search_log")

CALLER_LOG_SOURCE_VOLUME = "LOG_SOURCE_VOLUME"
CALLER_ANOMALY_EXPLANATION = "ANOMALY_EXPLANATION"
CALLER_RULE_MATCH = "RULE_MATCH"
CALLER_WAF_COLLECTION = "WAF_COLLECTION"
CALLER_IPS_COLLECTION = "IPS_COLLECTION"
CALLER_PROPERTY_WATCH = "PROPERTY_WATCH"
CALLER_CATCHALL_SENDERS = "CATCHALL_SENDERS"
CALLER_GEO_COLLECTION = "GEO_COLLECTION"
CALLER_QRADAR_HEALTH = "QRADAR_HEALTH"
CALLER_HISTORY = "HISTORY"
CALLER_PREVIEW = "PREVIEW"
CALLER_SCHEDULED_SEARCH = "SCHEDULED_SEARCH"
CALLER_MANUAL_RUN = "MANUAL_RUN"
#: `doctor --aql`: the operator's one-off test of every family's query (AQL-1).
CALLER_DOCTOR_AQL = "DOCTOR_AQL"
#: The access check's one probe search per console per check (ACC-1, ACC-3).
CALLER_ACCESS_CHECK = "ACCESS_CHECK"

OUTCOME_COMPLETED = "COMPLETED"
OUTCOME_ERROR = "ERROR"
OUTCOME_CANCELED = "CANCELED"
OUTCOME_TIMEOUT = "TIMEOUT"
OUTCOME_REFUSED = "REFUSED"
OUTCOME_PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
OUTCOME_IN_FLIGHT = "IN_FLIGHT"
OUTCOME_ABANDONED = "ABANDONED"

#: Terminal outcomes that are not COMPLETED: the "failed" band of a window.
#: ABANDONED is here too: a search whose worker died is a failure of the
#: platform's own pipeline, not a load that went away.
TERMINAL_FAILED = (
    OUTCOME_ERROR,
    OUTCOME_CANCELED,
    OUTCOME_TIMEOUT,
    OUTCOME_PROVIDER_UNAVAILABLE,
    OUTCOME_ABANDONED,
)

#: The REST provider polls a search at most this many times, once a second,
#: and then closes the row itself (TIMEOUT). ``qradar_rest`` loops over this
#: same constant, so the two cannot drift.
POLL_LIMIT = 120

#: Round-trip slack on top of the longest a search can legitimately run (create,
#: cancel, the last status read).
STALE_SLACK_SECONDS = 30


def stale_after_seconds() -> int:
    """How old an ``IN_FLIGHT`` row can be and still be running.

    The longest wait the application allows a search: the REST provider's poll
    budget, or ``ARIEL_MAX_TIMEOUT_SECONDS`` for a scheduled or manual search,
    whichever is larger, plus a little slack. A row older than that cannot be
    running: its worker died before the terminal write. ``load_current``
    reports those separately.
    """
    longest = POLL_LIMIT
    with suppress(Exception):
        from app.core.config import get_settings

        longest = max(longest, int(get_settings().ariel_max_timeout_seconds))
    return longest + STALE_SLACK_SECONDS


#: The reaper's cadence when the setting cannot be read, and the margin it
#: adds on top of ``stale_after_seconds()``. One scan interval, so a terminal
#: write that is late by less than a full scan still wins the race.
ABANDON_SCAN_DEFAULT_SECONDS = 600


def _abandon_margin_seconds() -> int:
    with suppress(Exception):
        from app.core.config import get_settings

        return int(get_settings().ariel_search_abandon_scan_seconds)
    return ABANDON_SCAN_DEFAULT_SECONDS


def abandon_cutoff(now: datetime) -> datetime:
    """The moment before which an ``IN_FLIGHT`` row is reaped as ``ABANDONED``.

    The row must be older than the longest wait the application allows a
    search — ``stale_after_seconds()``, round trips included — so a search
    that is still running is never reaped, plus one scan interval of margin:
    a terminal write held up by a busy worker or a queued beat task then
    still finds its row ``IN_FLIGHT`` and closes it with the real outcome.
    """
    return now - timedelta(seconds=stale_after_seconds() + _abandon_margin_seconds())


#: Nothing in the current read scans further back than this.
CURRENT_READ_WINDOW_SECONDS = 3600

#: The minute windows of the current load read, ascending.
MINUTES_WINDOWS: tuple[int, ...] = (5, 15, 60)

MAX_HOURS = 30 * 24
DEFAULT_HOURS = 24
EMPTY_CODE = "ariel_load.window.empty"
HOURS_CODE = "ariel_load.hours.invalid"
_WALL_MS_MAX = 2_147_483_647

_write_failures = 0

_buffer: ContextVar[list[ArielSearchObservation] | None] = ContextVar(
    "ariel_search_run_buffer", default=None
)
_family: ContextVar[str | None] = ContextVar("ariel_search_family", default=None)
_range: ContextVar[int | None] = ContextVar("ariel_search_range_seconds", default=None)
_override: ContextVar[str | None] = ContextVar("ariel_caller_override", default=None)

Clock = Callable[[], datetime]


@dataclass(frozen=True)
class ArielSearchObservation:
    """One search, ready to insert. No query text and no row payload."""

    id: uuid.UUID
    instance_id: uuid.UUID | None
    caller: str
    scheduled_search_id: uuid.UUID | None
    query_version: int | None
    submitted_at: datetime
    #: Null only on the submit-time row: the search has not ended, and a zero
    #: would read as a measured duration.
    finished_at: datetime | None
    wall_ms: int | None
    qradar_execution_ms: int | None
    qradar_record_count: int | None
    processed_record_count: int | None
    data_file_count: int | None
    data_total_size: int | None
    compressed_data_file_count: int | None
    compressed_data_total_size: int | None
    index_file_count: int | None
    index_total_size: int | None
    record_count: int | None
    range_seconds: int | None
    polls: int
    outcome: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "instance_id": self.instance_id,
            "caller": self.caller,
            "scheduled_search_id": self.scheduled_search_id,
            "query_version": self.query_version,
            "submitted_at": self.submitted_at,
            "finished_at": self.finished_at,
            "wall_ms": self.wall_ms,
            "qradar_execution_ms": self.qradar_execution_ms,
            "qradar_record_count": self.qradar_record_count,
            "processed_record_count": self.processed_record_count,
            "data_file_count": self.data_file_count,
            "data_total_size": self.data_total_size,
            "compressed_data_file_count": self.compressed_data_file_count,
            "compressed_data_total_size": self.compressed_data_total_size,
            "index_file_count": self.index_file_count,
            "index_total_size": self.index_total_size,
            "record_count": self.record_count,
            "range_seconds": self.range_seconds,
            "polls": self.polls,
            "outcome": self.outcome,
        }


def write_failure_count() -> int:
    return _write_failures


def reset_write_failures() -> None:
    global _write_failures
    _write_failures = 0


def effective_caller(family: str) -> str:
    """HISTORY and PREVIEW win over the method's family label."""
    override = _override.get()
    if override in CALLERS:
        return override
    return family


def caller_for_trigger(trigger: str) -> str | None:
    if trigger == "SCHEDULED":
        return CALLER_SCHEDULED_SEARCH
    if trigger == "MANUAL":
        return CALLER_MANUAL_RUN
    return None


def outcome_for_exception(exc: BaseException) -> str:
    # Imported here for the reason given at the top of the module.
    from app.providers.base import ProviderError, ProviderUnavailableError
    from app.services.ariel_errors import ArielTimeout
    from app.services.ariel_permit import ArielPermitUnavailable

    if isinstance(exc, ArielPermitUnavailable):
        return OUTCOME_REFUSED
    if isinstance(exc, ArielTimeout):
        return OUTCOME_TIMEOUT
    if isinstance(exc, ProviderUnavailableError):
        if "timed out" in str(exc).lower():
            return OUTCOME_TIMEOUT
        return OUTCOME_PROVIDER_UNAVAILABLE
    if isinstance(exc, ProviderError):
        return OUTCOME_ERROR
    return OUTCOME_ERROR


def outcome_of_status(status: ArielSearchStatusDTO | None) -> str:
    if status is None:
        return OUTCOME_ERROR
    if status.status == "CANCELED":
        return OUTCOME_CANCELED
    if status.status == "COMPLETED":
        return OUTCOME_COMPLETED
    return OUTCOME_ERROR


def span_from_call(args: tuple[object, ...], kwargs: Mapping[str, object]) -> int | None:
    """The window the call already carries, in seconds. Not parsed from AQL."""
    start = kwargs.get("window_start", kwargs.get("start"))
    end = kwargs.get("window_end", kwargs.get("end"))
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        found = [item for item in args if isinstance(item, datetime)]
        if len(found) >= 2:
            start, end = found[0], found[1]
    if not isinstance(start, datetime) or not isinstance(end, datetime):
        return None
    try:
        seconds = int((end - start).total_seconds())
    except (TypeError, ValueError, OverflowError):
        return None
    if seconds <= 0:
        return None
    return seconds


def load_family() -> str | None:
    family = _family.get()
    return family if family in CALLERS else None


def load_range() -> int | None:
    return _range.get()


@contextmanager
def load_scope(family: str, range_seconds: int | None) -> Iterator[None]:
    if family not in CALLERS:
        raise ValueError("unknown ariel caller")
    family_token = _family.set(family)
    range_token = _range.set(range_seconds)
    try:
        yield
    finally:
        _family.reset(family_token)
        _range.reset(range_token)


def ariel_caller(label: str) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Mark a history or preview entry. The provider methods keep their family."""

    def deco(fn: Callable[..., Any]) -> Callable[..., Any]:
        @wraps(fn)
        async def wrapped(*args: Any, **kwargs: Any) -> Any:
            if label not in CALLERS:
                raise ValueError("unknown ariel caller")
            token = _override.set(label)
            try:
                return await fn(*args, **kwargs)
            finally:
                _override.reset(token)

        return wrapped

    return deco


def open_run_buffer() -> Token[list[ArielSearchObservation] | None] | None:
    """Open a buffer. A nested open reuses the outer list and does not flush."""
    if _buffer.get() is not None:
        return None
    return _buffer.set([])


def take_run_buffer(
    token: Token[list[ArielSearchObservation] | None] | None,
) -> list[ArielSearchObservation]:
    if token is None:
        return []
    rows = _buffer.get()
    _buffer.reset(token)
    return list(rows or [])


def note(observation: ArielSearchObservation) -> None:
    """Append one search. A closed buffer drops it; this never writes and never raises."""
    if observation.caller not in CALLERS or observation.outcome not in OUTCOMES:
        return
    buf = _buffer.get()
    if buf is None:
        return
    buf.append(observation)


WriteKind = Literal["submit", "flush", "lookup"]


def count_write_failure(count: int, *, what: WriteKind) -> None:
    """Count one failed write of ``count`` rows and log it, without the exception text.

    ``what`` names the write that failed: ``submit`` (the in-flight INSERT),
    ``flush`` (the run's upsert) or ``lookup`` (no bind or engine to flush on).
    """
    global _write_failures
    _write_failures += 1
    logger.warning("ariel search log write failed what=%s rows=%s", what, count)


def _default_worker_engine() -> Any:
    """The worker's own database. Production always writes here."""
    from app.core.database import get_engine

    return get_engine()


#: The one place the per-search writes (submit row, poll refresh) and the
#: worker buffer flush get their connection from. Production's default is the
#: process engine and the flush always runs; the test suite replaces this
#: single function so a test can point the writes at its own database, or at
#: nothing (``None``) so a unit test cannot write the developer's database.
worker_engine: Callable[[], Any] = _default_worker_engine

#: What ``_worker_engine_or_failed`` answers when the lookup itself raised. A
#: sentinel, not ``None``: ``None`` already means "drop on purpose, no count".
_LOOKUP_FAILED = object()


def _worker_engine_or_failed() -> Any:
    """``worker_engine()``, or ``_LOOKUP_FAILED`` when the lookup itself raised.

    Building the engine can raise (an unreadable URL, a pool that cannot be
    built). The module's contract is that recording a search never raises into
    the search, so every write path treats that as a failed write and keeps its
    own accounting: a submit row that could not be opened counts one, a poll
    refresh and a terminal close are swallowed like their failed statements, and
    the flush counts its rows. ``asyncio.CancelledError`` is a ``BaseException``
    and still propagates.
    """
    try:
        return worker_engine()
    except Exception:
        return _LOOKUP_FAILED


#: Columns the terminal write may set. Everything else on a row is fixed at
#: submit time (who asked, when, for what span), so the flush's upsert never
#: rewrites them.
_TERMINAL_COLUMNS: tuple[str, ...] = (
    "finished_at",
    "wall_ms",
    "qradar_execution_ms",
    "qradar_record_count",
    "processed_record_count",
    "data_file_count",
    "data_total_size",
    "compressed_data_file_count",
    "compressed_data_total_size",
    "index_file_count",
    "index_total_size",
    "record_count",
    "polls",
    "outcome",
)


async def _insert(bind: Any, rows: Sequence[ArielSearchObservation]) -> None:
    """Upsert the buffered rows as one statement.

    A row whose submit-time INSERT landed is closed by the conflict branch; a
    row that only ever existed in the buffer (the mock provider, a permit
    refusal) is inserted. Either way this is one statement per run.
    """
    if not rows:
        return
    statement = pg_insert(ArielSearchRun).values([row.as_dict() for row in rows])
    statement = statement.on_conflict_do_update(
        index_elements=[ArielSearchRun.id],
        set_={name: statement.excluded[name] for name in _TERMINAL_COLUMNS},
        # The truth wins here too: a buffered observation closes a row that is
        # still open or was reaped as ABANDONED, and never overwrites another
        # terminal outcome a worker already recorded.
        where=ArielSearchRun.outcome.in_((OUTCOME_IN_FLIGHT, OUTCOME_ABANDONED)),
    )
    try:
        async with bind.connect() as conn:
            await conn.execute(statement)
            await conn.commit()
    except Exception:
        count_write_failure(len(rows), what="flush")


@dataclass(frozen=True)
class Submitted:
    """The submit-time record of one search.

    ``written`` says whether the IN_FLIGHT row reached the database. When it
    did not (no buffer, no target, a failed write) the terminal note carries
    the whole row and the flush inserts it.
    """

    id: uuid.UUID
    submitted_at: datetime
    written: bool


async def _submit_in_flight(
    *,
    submitted_at: datetime,
    caller: str,
    instance_id: uuid.UUID | None,
    scheduled_search_id: uuid.UUID | None,
    query_version: int | None,
    range_seconds: int | None,
) -> Submitted:
    """Insert the row a read of the current load can see while the search runs.

    One INSERT per search on its own connection, committed before the search
    is created, with the submit-time facts only: no wall, no costs, outcome
    ``IN_FLIGHT``. With no buffer open, or nowhere to write, nothing is
    written; the id is still returned so the terminal note names one row.
    """
    row_id = uuid.uuid4()
    if _buffer.get() is None:
        return Submitted(row_id, submitted_at, False)
    engine = _worker_engine_or_failed()
    if engine is _LOOKUP_FAILED:
        count_write_failure(1, what="submit")
        return Submitted(row_id, submitted_at, False)
    if engine is None:
        return Submitted(row_id, submitted_at, False)
    span = range_seconds if range_seconds is not None else load_range()
    observation = _observation(
        observation_id=row_id,
        caller=caller,
        submitted_at=submitted_at,
        finished_at=None,
        outcome=OUTCOME_IN_FLIGHT,
        polls=0,
        instance_id=instance_id,
        scheduled_search_id=scheduled_search_id,
        query_version=query_version,
        range_seconds=span,
        status=None,
        record_count=None,
    )
    try:
        async with engine.connect() as conn:
            await conn.execute(insert(ArielSearchRun).values(observation.as_dict()))
            await conn.commit()
    except Exception:
        count_write_failure(1, what="submit")
        return Submitted(row_id, submitted_at, False)
    return Submitted(row_id, submitted_at, True)


async def note_submit(
    *,
    clock: Clock,
    caller: str | None,
    instance_id: uuid.UUID | None = None,
    scheduled_search_id: uuid.UUID | None = None,
    query_version: int | None = None,
    range_seconds: int | None = None,
) -> Submitted | None:
    """Record a search being submitted now, for a caller that owns its own
    lifecycle (the search executor). ``None`` when the caller has no label.

    A permit refusal happens before this and never leaves an in-flight row.
    """
    if caller not in CALLERS:
        return None
    return await _submit_in_flight(
        submitted_at=clock(),
        caller=effective_caller(caller),
        instance_id=instance_id,
        scheduled_search_id=scheduled_search_id,
        query_version=query_version,
        range_seconds=range_seconds,
    )


#: The poll count of an in-flight row is refreshed on every Nth status read,
#: so a long search costs a handful of small UPDATEs, not one per second.
POLL_REFRESH_EVERY = 5


async def refresh_polls(observation_id: uuid.UUID | None, polls: int) -> None:
    """Refresh the poll count of an in-flight row, best effort.

    The guarded ``WHERE outcome = 'IN_FLIGHT'`` keeps a late refresh from
    touching a closed row. The terminal write is authoritative for the final
    count, so a failure here is swallowed without a count.
    """
    if observation_id is None or polls % POLL_REFRESH_EVERY != 0:
        return
    engine = _worker_engine_or_failed()
    if engine is None or engine is _LOOKUP_FAILED or _buffer.get() is None:
        return
    try:
        async with engine.connect() as conn:
            await conn.execute(
                update(ArielSearchRun)
                .where(
                    ArielSearchRun.id == observation_id,
                    ArielSearchRun.outcome == OUTCOME_IN_FLIGHT,
                )
                .values(polls=max(0, polls))
            )
            await conn.commit()
    except Exception:
        return


async def _close_in_flight(observation: ArielSearchObservation) -> bool:
    """Close the submit-time row with the terminal facts, at the terminal state.

    One UPDATE per search, guarded by ``outcome IN ('IN_FLIGHT', 'ABANDONED')``:
    the real outcome is the truth, so it overwrites a row the reaper already
    closed, and it never touches a row another terminal write closed first.
    True only when that row was closed here; otherwise the caller buffers the
    observation and the run's one upsert writes it.
    """
    engine = _worker_engine_or_failed()
    if engine is None or engine is _LOOKUP_FAILED:
        return False
    values = {name: getattr(observation, name) for name in _TERMINAL_COLUMNS}
    try:
        async with engine.connect() as conn:
            result = await conn.execute(
                update(ArielSearchRun)
                .where(
                    ArielSearchRun.id == observation.id,
                    ArielSearchRun.outcome.in_((OUTCOME_IN_FLIGHT, OUTCOME_ABANDONED)),
                )
                .values(**values)
            )
            await conn.commit()
    except Exception:
        return False
    return bool(result.rowcount)


async def settle(observation: ArielSearchObservation | None, *, written: bool) -> None:
    """A search reached its terminal state: close its row, or buffer it.

    ``written`` is true when the IN_FLIGHT row exists. A cancelled task skips
    the awaited UPDATE and leaves the row to the buffer's flush.
    """
    if observation is None:
        return
    if written:
        try:
            if await _close_in_flight(observation):
                return
        except asyncio.CancelledError:
            note(observation)
            raise
    note(observation)


async def finish_session_buffer(
    token: Token[list[ArielSearchObservation] | None] | None,
    session: AsyncSession,
) -> None:
    """Flush on the session's bind, a separate connection from the caller's transaction."""
    rows = take_run_buffer(token)
    if token is None or not rows:
        return
    bind = session.bind
    if bind is None:
        count_write_failure(len(rows), what="lookup")
        return
    await _insert(bind, rows)


async def finish_worker_buffer(
    token: Token[list[ArielSearchObservation] | None] | None,
) -> None:
    """Flush the Celery task buffer. Empty means no statement.

    The connection comes from ``worker_engine``: production's own process
    engine, always — there is no test switch in this code. The suite's
    fixture points the function at a test engine or at nothing; ``None``
    drops the rows on purpose and without a failure count. A lookup that
    raises is a failed write of every row, exactly as a failed statement is.
    """
    rows = take_run_buffer(token)
    if token is None or not rows:
        return
    engine = _worker_engine_or_failed()
    if engine is _LOOKUP_FAILED:
        count_write_failure(len(rows), what="lookup")
        return
    if engine is None:
        return
    await _insert(engine, rows)


async def reap_abandoned(session: AsyncSession, *, now: datetime | None = None) -> int:
    """Close ``IN_FLIGHT`` rows whose worker died, as ``ABANDONED``.

    One UPDATE statement whatever the number of rows. Rows older than the
    retention window are retention's to delete, not the reaper's, so the
    statement has that lower bound on ``submitted_at``. Only ``outcome`` and
    ``finished_at`` change — every measured column stays NULL, because the
    search did run on QRadar but nobody measured it, and a zero would read as
    a measurement. The poll count the refreshes wrote stays. A worker that
    reaches its terminal state later still wins: both closers accept
    ``ABANDONED``, so the real outcome overwrites the guess.

    Returns how many rows closed. The count is logged, never row content.
    """
    from app.core.config import get_settings

    at = now or datetime.now(UTC)
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    oldest = at - timedelta(days=get_settings().ariel_search_run_retention_days)
    result = await session.execute(
        update(ArielSearchRun)
        .where(
            ArielSearchRun.outcome == OUTCOME_IN_FLIGHT,
            ArielSearchRun.submitted_at < abandon_cutoff(at),
            ArielSearchRun.submitted_at >= oldest,
        )
        .values(
            outcome=OUTCOME_ABANDONED,
            finished_at=at,
            wall_ms=None,
            qradar_execution_ms=None,
            qradar_record_count=None,
            processed_record_count=None,
            data_file_count=None,
            data_total_size=None,
            compressed_data_file_count=None,
            compressed_data_total_size=None,
            index_file_count=None,
            index_total_size=None,
            record_count=None,
        )
    )
    await session.commit()
    closed = int(getattr(result, "rowcount", 0) or 0)
    if closed:
        logger.info("ariel search reaper closed abandoned rows=%s", closed)
    return closed


class SearchAttempt:
    """The polls and the terminal status of one in-flight search."""

    def __init__(self) -> None:
        self.polls = 0
        self.status: ArielSearchStatusDTO | None = None
        self.record_count: int | None = None
        self.marked: str | None = None
        #: The submit-time row, if the search has one. The terminal note
        #: reuses its id so the close updates that row instead of inserting
        #: a second one.
        self.submitted: Submitted | None = None

    async def saw(self, status: ArielSearchStatusDTO) -> None:
        """Count one status poll and, now and then, refresh the in-flight row."""
        self.polls += 1
        self.status = status
        if self.submitted is not None and self.submitted.written:
            await refresh_polls(self.submitted.id, self.polls)

    def returned(self, count: int | None) -> None:
        self.record_count = count

    def mark(self, outcome: str) -> None:
        if outcome in OUTCOMES:
            self.marked = outcome


@asynccontextmanager
async def track_search(
    *,
    clock: Clock,
    instance_id: uuid.UUID | None,
    scheduled_search_id: uuid.UUID | None = None,
    query_version: int | None = None,
    caller: str | None = None,
    range_seconds: int | None = None,
) -> AsyncIterator[SearchAttempt]:
    """Record one search when the block leaves. A missing caller records nothing.

    The submit-time row is written before the block runs, so a read of the
    current load sees the search while it is still on the wire, and it is
    closed the moment the block leaves.
    """
    attempt = SearchAttempt()
    submitted = clock()
    family = caller if caller in CALLERS else load_family()
    if family is not None:
        attempt.submitted = await _submit_in_flight(
            submitted_at=submitted,
            caller=effective_caller(family),
            instance_id=instance_id,
            scheduled_search_id=scheduled_search_id,
            query_version=query_version,
            range_seconds=range_seconds,
        )
    try:
        yield attempt
    except BaseException as exc:
        await settle(
            _close_attempt(
                attempt,
                clock=clock,
                submitted=submitted,
                exc=exc,
                instance_id=instance_id,
                scheduled_search_id=scheduled_search_id,
                query_version=query_version,
                caller=caller,
                range_seconds=range_seconds,
            ),
            written=attempt.submitted is not None and attempt.submitted.written,
        )
        raise
    else:
        await settle(
            _close_attempt(
                attempt,
                clock=clock,
                submitted=submitted,
                exc=None,
                instance_id=instance_id,
                scheduled_search_id=scheduled_search_id,
                query_version=query_version,
                caller=caller,
                range_seconds=range_seconds,
            ),
            written=attempt.submitted is not None and attempt.submitted.written,
        )


def _close_attempt(
    attempt: SearchAttempt,
    *,
    clock: Clock,
    submitted: datetime,
    exc: BaseException | None,
    instance_id: uuid.UUID | None,
    scheduled_search_id: uuid.UUID | None,
    query_version: int | None,
    caller: str | None,
    range_seconds: int | None,
) -> ArielSearchObservation | None:
    family = caller if caller in CALLERS else load_family()
    if family is None:
        return None
    outcome = attempt.marked
    if outcome not in OUTCOMES:
        outcome = OUTCOME_COMPLETED if exc is None else outcome_for_exception(exc)
    span = range_seconds if range_seconds is not None else load_range()
    # A permit refusal has no query, so it has no range.
    if outcome == OUTCOME_REFUSED:
        span = None
    return _observation(
        observation_id=attempt.submitted.id if attempt.submitted is not None else None,
        caller=effective_caller(family),
        submitted_at=submitted,
        finished_at=clock(),
        outcome=outcome,
        polls=attempt.polls,
        instance_id=_uuid_or_none(instance_id),
        scheduled_search_id=_uuid_or_none(scheduled_search_id),
        query_version=_int_or_none(query_version),
        range_seconds=span if isinstance(span, int) and span > 0 else None,
        status=attempt.status,
        record_count=attempt.record_count,
    )


def record_refusal(owner: object, family: str | None) -> None:
    """A permit yielded nothing. Nothing was sent to QRadar."""
    if not isinstance(family, str) or family not in CALLERS:
        return
    clock = getattr(owner, "_clock", None)
    now = clock() if callable(clock) else datetime.now(UTC)
    permit = getattr(owner, "_permit", None)
    instance_id = getattr(permit, "instance_id", None) if permit is not None else None
    note(
        _observation(
            caller=effective_caller(family),
            submitted_at=now,
            finished_at=now,
            outcome=OUTCOME_REFUSED,
            polls=0,
            instance_id=_uuid_or_none(instance_id),
            scheduled_search_id=None,
            query_version=None,
            range_seconds=None,
            status=None,
            record_count=None,
        )
    )


def _finished(
    *,
    clock: Clock,
    submitted_at: datetime,
    outcome: str,
    polls: int,
    instance_id: uuid.UUID | None,
    scheduled_search_id: uuid.UUID | None,
    query_version: int | None,
    caller: str | None,
    range_seconds: int | None,
    status: ArielSearchStatusDTO | None,
    record_count: int | None,
    observation_id: uuid.UUID | None,
) -> ArielSearchObservation | None:
    family = caller if caller in CALLERS else load_family()
    if family is None or outcome not in OUTCOMES:
        return None
    span = range_seconds if range_seconds is not None else load_range()
    if outcome == OUTCOME_REFUSED:
        span = None
    return _observation(
        observation_id=observation_id,
        caller=effective_caller(family),
        submitted_at=submitted_at,
        finished_at=clock(),
        outcome=outcome,
        polls=polls,
        instance_id=_uuid_or_none(instance_id),
        scheduled_search_id=_uuid_or_none(scheduled_search_id),
        query_version=_int_or_none(query_version),
        range_seconds=span if isinstance(span, int) and span > 0 else None,
        status=status,
        record_count=record_count,
    )


def note_finished(
    *,
    clock: Clock,
    submitted_at: datetime,
    outcome: str,
    polls: int,
    instance_id: uuid.UUID | None = None,
    scheduled_search_id: uuid.UUID | None = None,
    query_version: int | None = None,
    caller: str | None = None,
    range_seconds: int | None = None,
    status: ArielSearchStatusDTO | None = None,
    record_count: int | None = None,
) -> None:
    """Record a search that never had a submit-time row (the mock provider, a
    refusal). It lands in the run buffer and is inserted by the flush."""
    observation = _finished(
        clock=clock,
        submitted_at=submitted_at,
        outcome=outcome,
        polls=polls,
        instance_id=instance_id,
        scheduled_search_id=scheduled_search_id,
        query_version=query_version,
        caller=caller,
        range_seconds=range_seconds,
        status=status,
        record_count=record_count,
        observation_id=None,
    )
    if observation is not None:
        note(observation)


async def settle_finished(
    *,
    submitted: Submitted | None,
    clock: Clock,
    outcome: str,
    polls: int,
    instance_id: uuid.UUID | None = None,
    scheduled_search_id: uuid.UUID | None = None,
    query_version: int | None = None,
    caller: str | None = None,
    range_seconds: int | None = None,
    status: ArielSearchStatusDTO | None = None,
    record_count: int | None = None,
    fallback_submitted_at: datetime | None = None,
) -> None:
    """Close a search whose lifecycle the caller (the search executor) owns.

    With a submit-time row the row is closed now; without one the observation
    goes to the buffer. ``fallback_submitted_at`` stamps a search that was
    refused before it had a submit record.
    """
    at = submitted.submitted_at if submitted is not None else fallback_submitted_at
    if at is None:
        return
    observation = _finished(
        clock=clock,
        submitted_at=at,
        outcome=outcome,
        polls=polls,
        instance_id=instance_id,
        scheduled_search_id=scheduled_search_id,
        query_version=query_version,
        caller=caller,
        range_seconds=range_seconds,
        status=status,
        record_count=record_count,
        observation_id=submitted.id if submitted is not None else None,
    )
    await settle(observation, written=submitted is not None and submitted.written)


def _observation(
    *,
    caller: str,
    submitted_at: datetime,
    finished_at: datetime | None,
    outcome: str,
    polls: int,
    instance_id: uuid.UUID | None,
    scheduled_search_id: uuid.UUID | None,
    query_version: int | None,
    range_seconds: int | None,
    status: ArielSearchStatusDTO | None,
    record_count: int | None,
    observation_id: uuid.UUID | None = None,
) -> ArielSearchObservation:
    costs = _costs(status)
    return ArielSearchObservation(
        id=observation_id if observation_id is not None else uuid.uuid4(),
        instance_id=instance_id,
        caller=caller,
        scheduled_search_id=scheduled_search_id,
        query_version=query_version,
        submitted_at=submitted_at,
        finished_at=finished_at,
        # No finish yet means no wall: a zero would read as a measurement.
        wall_ms=None if finished_at is None else _wall_ms(submitted_at, finished_at),
        polls=max(0, polls),
        outcome=outcome,
        record_count=record_count,
        range_seconds=range_seconds,
        **costs,
    )


def _costs(status: ArielSearchStatusDTO | None) -> dict[str, int | None]:
    if status is None:
        return {
            "qradar_execution_ms": None,
            "qradar_record_count": None,
            "processed_record_count": None,
            "data_file_count": None,
            "data_total_size": None,
            "compressed_data_file_count": None,
            "compressed_data_total_size": None,
            "index_file_count": None,
            "index_total_size": None,
        }
    return {
        "qradar_execution_ms": status.query_execution_time,
        "qradar_record_count": status.record_count,
        "processed_record_count": status.processed_record_count,
        "data_file_count": status.data_file_count,
        "data_total_size": status.data_total_size,
        "compressed_data_file_count": status.compressed_data_file_count,
        "compressed_data_total_size": status.compressed_data_total_size,
        "index_file_count": status.index_file_count,
        "index_total_size": status.index_total_size,
    }


def _wall_ms(submitted: datetime, finished: datetime) -> int:
    try:
        millis = int((finished - submitted).total_seconds() * 1000)
    except (TypeError, ValueError, OverflowError):
        return 0
    if millis < 0:
        return 0
    if millis > _WALL_MS_MAX:
        return _WALL_MS_MAX
    return millis


def _uuid_or_none(value: object) -> uuid.UUID | None:
    return value if isinstance(value, uuid.UUID) else None


def _int_or_none(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def parse_hours(raw: str | None) -> int:
    """A whole number of hours from 1 to 720. ``None`` is the default 24."""
    if raw is None or raw == "":
        return DEFAULT_HOURS
    if not raw.isascii() or not raw.isdigit() or len(raw) > 4:
        raise ValueError(render(MessageRef(HOURS_CODE)))
    hours = int(raw)
    if hours < 1 or hours > MAX_HOURS:
        raise ValueError(render(MessageRef(HOURS_CODE)))
    return hours


def empty_message() -> str:
    return render(MessageRef(EMPTY_CODE))


def _sum_known(values: Sequence[int | None]) -> tuple[int | None, int]:
    """Sum of present numbers, and how many were missing.

    A present 0 is included. When every value is missing the sum is null, not 0.
    """
    known = [item for item in values if item is not None]
    unknown = len(values) - len(known)
    if not known:
        return None, unknown
    return sum(known), unknown


def _percentile_ms(values: Sequence[int], q: float) -> int:
    return round(percentile([float(item) for item in values], q))


def _hour_start(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _hours_between(start: datetime, end: datetime) -> list[datetime]:
    first = _hour_start(start)
    last = _hour_start(end - timedelta(microseconds=1))
    cursor = first
    hours: list[datetime] = []
    while cursor <= last:
        hours.append(cursor)
        cursor += timedelta(hours=1)
    return hours


def summarize_runs(
    rows: Sequence[ArielSearchRun | ArielSearchObservation],
    *,
    start: datetime,
    end: datetime,
    hours: int,
) -> dict[str, Any]:
    """Per-caller summary of one window. An empty window says so and carries no zeros.

    Wall and execution percentiles are over COMPLETED rows only: they answer
    "how long does a search take", which only a finished one proves. Every sum
    is the load itself and covers every row given (`load_summary` hands over
    terminal rows only; a TIMEOUT's partial execution time is included).
    """
    window = {
        "hours": hours,
        "start": start.isoformat(),
        "end": end.isoformat(),
    }
    if not rows:
        return {
            "empty": True,
            "message": empty_message(),
            "message_code": EMPTY_CODE,
            **window,
            "callers": [],
            "series": [],
        }
    by_caller: dict[str, list[ArielSearchRun | ArielSearchObservation]] = defaultdict(list)
    for row in rows:
        by_caller[row.caller].append(row)
    callers = [_caller_summary(label, by_caller[label]) for label in sorted(by_caller)]
    return {
        "empty": False,
        **window,
        "callers": callers,
        "series": _series(rows, start, end),
    }


def _caller_summary(
    label: str, rows: Sequence[ArielSearchRun | ArielSearchObservation]
) -> dict[str, Any]:
    """One caller's load. Percentiles answer "how long does a search take",
    so they cover COMPLETED rows only — an ERROR or TIMEOUT wall is a failure
    time, not a duration the platform promises. The sums are the load itself:
    every row counts, a TIMEOUT's partial execution time included."""
    outcomes: dict[str, int] = defaultdict(int)
    for row in rows:
        outcomes[row.outcome] += 1
    completed_walls = [
        row.wall_ms for row in rows if row.outcome == OUTCOME_COMPLETED and row.wall_ms is not None
    ]
    walls = [row.wall_ms for row in rows if row.wall_ms is not None]
    completed_execution = [
        row.qradar_execution_ms
        for row in rows
        if row.outcome == OUTCOME_COMPLETED and row.qradar_execution_ms is not None
    ]
    execution_sum, execution_unknown = _sum_known([row.qradar_execution_ms for row in rows])
    records_sum, records_unknown = _sum_known([row.record_count for row in rows])
    data_sum, data_unknown = _sum_known([row.data_total_size for row in rows])
    return {
        "caller": label,
        "searches": len(rows),
        "outcomes": dict(sorted(outcomes.items())),
        "wall_ms_p50": _percentile_ms(completed_walls, 50) if completed_walls else None,
        "wall_ms_p95": _percentile_ms(completed_walls, 95) if completed_walls else None,
        "wall_ms_max": max(completed_walls) if completed_walls else None,
        "wall_ms_sum": sum(walls),
        "qradar_execution_ms_p50": (
            _percentile_ms(completed_execution, 50) if completed_execution else None
        ),
        "qradar_execution_ms_p95": (
            _percentile_ms(completed_execution, 95) if completed_execution else None
        ),
        "qradar_execution_ms_sum": execution_sum,
        "qradar_execution_ms_unknown": execution_unknown,
        "records_returned_sum": records_sum,
        "records_returned_unknown": records_unknown,
        "data_total_size_sum": data_sum,
        "data_total_size_unknown": data_unknown,
    }


def _series(
    rows: Sequence[ArielSearchRun | ArielSearchObservation],
    start: datetime,
    end: datetime,
) -> list[dict[str, Any]]:
    grouped: dict[datetime, list[ArielSearchRun | ArielSearchObservation]] = defaultdict(list)
    for row in rows:
        grouped[_hour_start(row.submitted_at)].append(row)
    series: list[dict[str, Any]] = []
    for hour in _hours_between(start, end):
        bucket = grouped.get(hour, [])
        execution_sum, execution_unknown = _sum_known([row.qradar_execution_ms for row in bucket])
        series.append(
            {
                "hour": hour.isoformat(),
                "searches": len(bucket),
                "qradar_execution_ms_sum": execution_sum,
                "qradar_execution_ms_unknown": execution_unknown,
            }
        )
    return series


# -------------------------------------------------------- the window summary
#: Outcomes the window summary counts per caller. ``IN_FLIGHT`` cannot appear:
#: every summary statement carries the terminal-rows predicate below.
_COUNTED_OUTCOMES: tuple[str, ...] = tuple(
    outcome for outcome in OUTCOMES if outcome != OUTCOME_IN_FLIGHT
)


def _terminal_rows_between(start: datetime, end: datetime) -> tuple[ColumnElement[bool], ...]:
    """The predicate every summary statement shares: terminal rows inside the
    window, the same half-open bounds as before."""
    return (
        ArielSearchRun.submitted_at >= start,
        ArielSearchRun.submitted_at < end,
        ArielSearchRun.outcome != OUTCOME_IN_FLIGHT,
    )


#: A COMPLETED-only value for the aggregates: ``percentile_cont`` and ``max``
#: skip NULL inputs, so every other row contributes nothing — exactly what the
#: list comprehensions in ``_caller_summary`` skip on the Python side.
_completed_wall = case(
    (ArielSearchRun.outcome == OUTCOME_COMPLETED, ArielSearchRun.wall_ms),
)
_completed_execution = case(
    (ArielSearchRun.outcome == OUTCOME_COMPLETED, ArielSearchRun.qradar_execution_ms),
)


def _caller_aggregates(start: datetime, end: datetime) -> Select:
    """One row per caller: counts, the COMPLETED-only percentiles, the sums.

    ``percentile_cont`` is linear interpolation at rank ``q/100·(n-1)`` — the
    definition of ``statistics.percentile`` — and it returns a float, which
    Python then rounds (``_rounded_ms``). ``sum`` over a bigint column is
    ``numeric`` in PostgreSQL; the cast keeps today's ``int``, never a
    ``Decimal``.
    """
    return (
        select(
            ArielSearchRun.caller.label("caller"),
            func.count().label("searches"),
            *(
                func.count()
                .filter(ArielSearchRun.outcome == outcome)
                .label(f"outcome_{outcome.lower()}")
                for outcome in _COUNTED_OUTCOMES
            ),
            func.percentile_cont(0.5).within_group(_completed_wall).label("wall_ms_p50"),
            func.percentile_cont(0.95).within_group(_completed_wall).label("wall_ms_p95"),
            func.max(_completed_wall).label("wall_ms_max"),
            # sum(walls): an int in the Python fold even when nothing was measured.
            func.coalesce(func.sum(ArielSearchRun.wall_ms), 0).label("wall_ms_sum"),
            func.percentile_cont(0.5)
            .within_group(_completed_execution)
            .label("qradar_execution_ms_p50"),
            func.percentile_cont(0.95)
            .within_group(_completed_execution)
            .label("qradar_execution_ms_p95"),
            func.sum(ArielSearchRun.qradar_execution_ms)
            .cast(BigInteger)
            .label("qradar_execution_ms_sum"),
            func.count()
            .filter(ArielSearchRun.qradar_execution_ms.is_(None))
            .label("qradar_execution_ms_unknown"),
            func.sum(ArielSearchRun.record_count)
            .cast(BigInteger)
            .label("records_returned_sum"),
            func.count()
            .filter(ArielSearchRun.record_count.is_(None))
            .label("records_returned_unknown"),
            func.sum(ArielSearchRun.data_total_size)
            .cast(BigInteger)
            .label("data_total_size_sum"),
            func.count()
            .filter(ArielSearchRun.data_total_size.is_(None))
            .label("data_total_size_unknown"),
        )
        .where(*_terminal_rows_between(start, end))
        .group_by(ArielSearchRun.caller)
    )


def _hour_aggregates(start: datetime, end: datetime) -> Select:
    """One row per hour that has a row: searches and the execution load.

    ``timezone('UTC', …)`` truncates in UTC whatever the session's zone is,
    the same hour ``_hour_start`` computes on the aware datetimes the ORM
    returns.
    """
    hour = func.date_trunc(
        "hour", func.timezone("UTC", ArielSearchRun.submitted_at)
    ).label("hour")
    return select(
        hour,
        func.count().label("searches"),
        func.sum(ArielSearchRun.qradar_execution_ms)
        .cast(BigInteger)
        .label("qradar_execution_ms_sum"),
        func.count()
        .filter(ArielSearchRun.qradar_execution_ms.is_(None))
        .label("qradar_execution_ms_unknown"),
    ).where(*_terminal_rows_between(start, end)).group_by(hour)


def _int_from_db(value: Any) -> int:
    """A count column as an ``int``, whatever the driver hands back."""
    return int(value)


def _int_or_none_from_db(value: Any) -> int | None:
    """A nullable bigint column as ``int | None`` — never a ``Decimal``."""
    return None if value is None else int(value)


def _rounded_ms(value: float | None) -> int | None:
    """Round a SQL percentile exactly as ``_percentile_ms`` does.

    Python rounds halves to even; PostgreSQL's ``round`` takes them away from
    zero, so a percentile that lands on ``.5`` is rounded here, never in SQL.
    """
    return None if value is None else round(value)


def _caller_summaries(rows: Sequence[Row[Any]]) -> list[dict[str, Any]]:
    """Per-caller dicts in the shape ``_caller_summary`` has always produced."""
    summaries: list[dict[str, Any]] = []
    for row in sorted(rows, key=lambda row: row.caller):
        counts = {
            outcome: _int_from_db(getattr(row, f"outcome_{outcome.lower()}"))
            for outcome in _COUNTED_OUTCOMES
        }
        summaries.append(
            {
                "caller": row.caller,
                "searches": _int_from_db(row.searches),
                # A zero count is not an outcome the caller had: the per-row
                # defaultdict only ever held outcomes that appeared.
                "outcomes": dict(
                    sorted((name, count) for name, count in counts.items() if count)
                ),
                "wall_ms_p50": _rounded_ms(row.wall_ms_p50),
                "wall_ms_p95": _rounded_ms(row.wall_ms_p95),
                "wall_ms_max": _int_or_none_from_db(row.wall_ms_max),
                "wall_ms_sum": _int_from_db(row.wall_ms_sum),
                "qradar_execution_ms_p50": _rounded_ms(row.qradar_execution_ms_p50),
                "qradar_execution_ms_p95": _rounded_ms(row.qradar_execution_ms_p95),
                "qradar_execution_ms_sum": _int_or_none_from_db(row.qradar_execution_ms_sum),
                "qradar_execution_ms_unknown": _int_from_db(row.qradar_execution_ms_unknown),
                "records_returned_sum": _int_or_none_from_db(row.records_returned_sum),
                "records_returned_unknown": _int_from_db(row.records_returned_unknown),
                "data_total_size_sum": _int_or_none_from_db(row.data_total_size_sum),
                "data_total_size_unknown": _int_from_db(row.data_total_size_unknown),
            }
        )
    return summaries


def _series_summaries(
    rows: Sequence[Row[Any]], start: datetime, end: datetime
) -> list[dict[str, Any]]:
    """Every hour of the window in order, as ``_series`` builds it: an hour
    with no row carries ``searches: 0`` and the empty-bucket sums — a null
    sum, no unknowns — which is what ``_sum_known([])`` has always returned."""
    found = {
        row.hour.replace(tzinfo=UTC): (
            _int_from_db(row.searches),
            _int_or_none_from_db(row.qradar_execution_ms_sum),
            _int_from_db(row.qradar_execution_ms_unknown),
        )
        for row in rows
    }
    series: list[dict[str, Any]] = []
    for hour in _hours_between(start, end):
        searches, execution_sum, execution_unknown = found.get(hour, (0, None, 0))
        series.append(
            {
                "hour": hour.isoformat(),
                "searches": searches,
                "qradar_execution_ms_sum": execution_sum,
                "qradar_execution_ms_unknown": execution_unknown,
            }
        )
    return series


async def load_summary(
    session: AsyncSession,
    *,
    hours: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Summarise a window. Only terminal rows: an ``IN_FLIGHT`` search has no
    measured load yet, and the current read owns reporting it.

    The database does the folding in two statements — one per-caller aggregate,
    one per-hour aggregate — whatever the window and whatever the number of
    callers; no row of the window is materialised in Python. ``summarize_runs``
    stays as the reference the equivalence tests fold the same rows against.
    """
    if hours < 1 or hours > MAX_HOURS:
        raise ValueError(render(MessageRef(HOURS_CODE)))
    end = now or datetime.now(UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    start = end - timedelta(hours=hours)
    caller_rows = (await session.execute(_caller_aggregates(start, end))).all()
    window = {"hours": hours, "start": start.isoformat(), "end": end.isoformat()}
    if not caller_rows:
        return {
            "empty": True,
            "message": empty_message(),
            "message_code": EMPTY_CODE,
            **window,
            "callers": [],
            "series": [],
        }
    hour_rows = (await session.execute(_hour_aggregates(start, end))).all()
    return {
        "empty": False,
        **window,
        "callers": _caller_summaries(caller_rows),
        "series": _series_summaries(hour_rows, start, end),
    }


# --------------------------------------------------------------- current load
def _utcnow() -> datetime:
    return datetime.now(UTC)


def _in_flight_entry(row: ArielSearchRun, *, as_of: datetime) -> dict[str, Any]:
    age = (as_of - row.submitted_at).total_seconds()
    return {
        "caller": row.caller,
        "instance_id": str(row.instance_id) if row.instance_id is not None else None,
        "submitted_at": row.submitted_at.isoformat(),
        "age_seconds": max(0, int(age)),
        "polls": row.polls,
        "range_seconds": row.range_seconds,
    }


def _part_unavailable(what: str) -> dict[str, Any]:
    """A part that could not be read: its own sentence, never a zero."""
    return {"available": False, "reason": f"{what} could not be read"}


async def _current_in_flight(session: AsyncSession, *, as_of: datetime) -> dict[str, Any]:
    bound = as_of - timedelta(seconds=CURRENT_READ_WINDOW_SECONDS)
    try:
        rows = (
            await session.scalars(
                select(ArielSearchRun)
                .where(
                    ArielSearchRun.outcome == OUTCOME_IN_FLIGHT,
                    ArielSearchRun.submitted_at >= bound,
                )
                .order_by(ArielSearchRun.submitted_at)
            )
        ).all()
    except Exception:
        return _part_unavailable("in-flight searches")
    active: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    limit = stale_after_seconds()
    for row in rows:
        entry = _in_flight_entry(row, as_of=as_of)
        if entry["age_seconds"] > limit:
            stale.append(entry)
        else:
            active.append(entry)
    return {
        "available": True,
        "active": active,
        "stale": stale,
        "stale_after_seconds": limit,
    }


def is_failed(caller: str, outcome: str) -> bool:
    """Whether a terminal outcome counts as failed for this caller.

    The access check cancels its probe search unread (ACC-3), so a CANCELED row of that caller
    is its designed end, not a failure. Every other outcome, and every other caller, follows
    ``TERMINAL_FAILED``.
    """
    if caller == CALLER_ACCESS_CHECK and outcome == OUTCOME_CANCELED:
        return False
    return outcome in TERMINAL_FAILED


def _window_summary(rows: Sequence[Any], *, cutoff: datetime) -> dict[str, Any]:
    """One minute window. Counts by submit time; percentiles over COMPLETED
    rows only; sums over every terminal row in the window, a TIMEOUT's partial
    execution time included. An IN_FLIGHT row is counted but not summed: it
    has no measurement yet."""
    bucket = [row for row in rows if row.submitted_at >= cutoff]
    completed = [row for row in bucket if row.outcome == OUTCOME_COMPLETED]
    failed = [row for row in bucket if is_failed(row.caller, row.outcome)]
    refused = [row for row in bucket if row.outcome == OUTCOME_REFUSED]
    in_flight = [row for row in bucket if row.outcome == OUTCOME_IN_FLIGHT]
    terminal = [row for row in bucket if row.outcome != OUTCOME_IN_FLIGHT]
    walls = [row.wall_ms for row in completed if row.wall_ms is not None]
    execution = [
        row.qradar_execution_ms for row in completed if row.qradar_execution_ms is not None
    ]
    wall_sum, _wall_unknown = _sum_known([row.wall_ms for row in terminal])
    execution_sum, execution_unknown = _sum_known([row.qradar_execution_ms for row in terminal])
    records_sum, records_unknown = _sum_known([row.record_count for row in terminal])
    return {
        "submitted": len(bucket),
        "completed": len(completed),
        "failed": len(failed),
        "refused": len(refused),
        "in_flight": len(in_flight),
        "wall_ms_sum": wall_sum,
        "wall_ms_p50": _percentile_ms(walls, 50) if walls else None,
        "wall_ms_p95": _percentile_ms(walls, 95) if walls else None,
        "qradar_execution_ms_sum": execution_sum,
        "qradar_execution_ms_unknown": execution_unknown,
        "qradar_execution_ms_p50": _percentile_ms(execution, 50) if execution else None,
        "qradar_execution_ms_p95": _percentile_ms(execution, 95) if execution else None,
        "records_returned_sum": records_sum,
        "records_returned_unknown": records_unknown,
    }


async def _current_windows(session: AsyncSession, *, as_of: datetime) -> dict[str, Any]:
    bound = as_of - timedelta(seconds=CURRENT_READ_WINDOW_SECONDS)
    try:
        rows = (
            await session.execute(
                select(
                    ArielSearchRun.submitted_at,
                    ArielSearchRun.caller,
                    ArielSearchRun.outcome,
                    ArielSearchRun.wall_ms,
                    ArielSearchRun.qradar_execution_ms,
                    ArielSearchRun.record_count,
                ).where(ArielSearchRun.submitted_at >= bound)
            )
        ).all()
    except Exception:
        return _part_unavailable("recent searches")
    windows = {
        f"minutes_{minutes}": _window_summary(rows, cutoff=as_of - timedelta(minutes=minutes))
        for minutes in MINUTES_WINDOWS
    }
    return {"available": True, **windows}


def _permit_budget_class() -> type[ArielPermitBudget]:
    """The permit budget, imported the way a cold process can import it.

    ``app.services.ariel_permit`` imports the provider package, whose REST
    module imports ``ariel_permit`` back: entered from ``ariel_permit`` that
    fails. ``python -m app.cli.ariel_load --current`` has not loaded the
    providers by now, so the package goes first.
    """
    import app.providers  # noqa: F401
    from app.services.ariel_permit import ArielPermitBudget

    return ArielPermitBudget


async def _current_slots(session: AsyncSession) -> dict[str, Any]:
    """The permit pool, read exactly as the doctor's ``ariel_permits`` check
    reads it (a global-only budget, so the console figure is every
    console-pool lock in the database). No new lock mechanism."""
    try:
        from app.core.config import get_settings

        budget = _permit_budget_class().for_session(session, get_settings(), instance_id=None)
        counts = await budget.active_permits(session)
    except Exception:
        return _part_unavailable("permit slots")
    if counts is None:
        return {"available": True, "enforced": False}
    return {
        "available": True,
        "enforced": True,
        "global_held": counts.global_held,
        "global_cap": counts.global_cap,
        "console_pool_locks": counts.instance_held,
        "console_cap": counts.instance_cap,
        # The slots the permit pool keeps back for foreground work.
        "reserved_slots": counts.reserved_live_slots,
    }


async def load_current(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """One poll of the load the platform is putting on QRadar at this moment.

    Three parts, each fail-closed on its own — a part that cannot be read
    says so with ``available: false`` and a reason, never zeros:

    - ``in_flight``: every submitted, not-yet-terminal search (caller,
      instance, age, polls so far, range), plus the ones older than the
      provider's own poll budget, reported separately as ``stale`` — their
      worker died before the terminal write.
    - ``windows``: the last 5, 15 and 60 minutes — searches submitted,
      completed, failed, refused, in flight, wall and execution percentiles
      (COMPLETED rows only), sums over every terminal row, records returned.
    - ``slots``: the Ariel permit pool now (held / cap, global and per
      console), the same read the doctor's ``ariel_permits`` check uses.

    Cost: one bounded query per part over at most the last hour (indexed on
    ``submitted_at``) plus the permit read, so the endpoint is cheap to poll.
    """
    as_of = now or _utcnow()
    if as_of.tzinfo is None:
        as_of = as_of.replace(tzinfo=UTC)
    return {
        "as_of": as_of.isoformat(),
        "in_flight": await _current_in_flight(session, as_of=as_of),
        "windows": await _current_windows(session, as_of=as_of),
        "slots": await _current_slots(session),
    }

