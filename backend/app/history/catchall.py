"""The catch-all family's history adapter (HISTORY-BACKFILL §B.3.2, §B.4.4, §B.6.2; HB-4).

Pure: no database, no provider, no clock, no settings import. The collector calls it.

What this module owns:

* the window of a reading (`T0 = anchor - days`) and the read start `T0 - m`, the
  margin that proves the floor (§B.3.2);
* the fold of a read: the same `fold_source_bucket` the live path uses, with the
  ledger seeded with the currently active senders (§B.4.4 "Order-dependent
  admission") and the per-source floor gate (nothing of a source is written
  before its `F_s`, and a source is zero-filled only from `F_s` on, §B.3.2
  rules 1 and 3);
* the bucket row of one source and bucket (`bucket_row`), which the live path
  uses too, so both write the same columns;
* the planner's chunk results of a read: a search truncated at one bucket is a
  `LOWER_BOUND` bucket, as live;
* the sender rows a history step creates: `REVIEWED`, `reviewed_by = 'history'`
  (Guard 1, §B.4.4) and active only when the sender was seen at or after
  `anchor - catchall_sender_active_days` (§B.4.4 "Retirement").

The per-source floors live in the codec's `adapter` object (`SourceFloors`); the
core progress's `floor` field means "where the read starts" for this family
(the orchestrator's decision, 2026-10-19), because the floor is proved per
source and not once for the console.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from app.catchall.fold import CatchallBucketFact, SenderLedger, fold_source_bucket
from app.catchall.keyed_search import CatchallBucketOutcome
from app.history.families import history_search_budget
from app.history.lifecycle import ScopeRowView, bounded_reason
from app.history.origin import HistoryOrigin, marker_values
from app.history.planner import (
    DEFAULT_MIN_WINDOW_DAYS,
    ChunkResult,
    PlannerSettings,
    ScopeProgress,
    SearchStatus,
    Span,
    StepBudget,
    WindowFit,
    fit_window,
    new_progress,
    searches_for,
)
from app.history.source_floors import SourceFloors, finish_with_sources
from app.history.state import HistoryFamily, HistoryTrigger
from app.models.enums import CatchallMeasurement, CatchallReviewStatus, IpsCellState
from app.providers.dto import CatchallSenderRow
from app.waf.admission import AdmissionPolicy

#: A sender a history step creates carries this `reviewed_by` (§B.4.4).
HISTORY_REVIEWED_BY = "history"

#: One search never covers more than a week (`validate_bucketed_metric_window`).
MAX_SPAN_HOURS = 168
#: A search aims at 80 % of the family's row cap (§B.8.2).
ROW_TARGET_SHARE = 0.8
#: The width of the first search of a reading, before a density is known: one day.
INITIAL_CHUNK_BUCKETS = 288
#: The narrowest search: one bucket, whose result is a lower bound when truncated.
MIN_CHUNK_BUCKETS = 1
#: How many unreadable spans a scope records before it gives up (§B.6.3).
MAX_GAPS = 20



@dataclass(frozen=True)
class CatchallSource:
    """One catch-all log source of the console: the row's key and the search's id."""

    id: uuid.UUID
    qradar_id: int

    @property
    def key(self) -> str:
        """The key the per-source floors are stored under (the QRadar log-source id)."""
        return str(self.qradar_id)


@dataclass
class SenderTouch:
    """One tracked sender seen in a read, with the buckets it was seen in."""

    first_seen_at: datetime
    last_seen_at: datetime


@dataclass
class CatchallHistoryFold:
    """The state one history step carries from one chunk to the next."""

    #: One ledger per catch-all source, seeded with the currently active senders.
    ledgers: dict[uuid.UUID, SenderLedger] = field(default_factory=dict)
    #: The proved floor of every source that had an event (the codec's adapter).
    floors: SourceFloors = field(default_factory=SourceFloors)


@dataclass(frozen=True)
class CatchallStep:
    """What one history step knows about the window it reads."""

    #: `T0`, the start the reading aims at.
    target_start: datetime
    #: `m`, the floor margin (§B.3.2).
    margin: timedelta
    #: `T0 - m`, where the read starts: the first span is the floor proof.
    read_start: datetime
    bucket_seconds: int
    #: How many senders a bucket's top list holds.
    top_n: int


@dataclass(frozen=True)
class CatchallChunk:
    """What one chunk of the history read established."""

    #: The `catchall_bucket` rows to write (origin `HISTORY`).
    rows: tuple[dict[str, Any], ...]
    #: The senders the chunk saw, per bucket: only the written buckets count.
    touches: Mapping[datetime, Mapping[tuple[uuid.UUID, str], SenderTouch]]
    #: The planner's results of the chunk, in time order.
    results: tuple[ChunkResult, ...]


def reading_progress(
    target_start: datetime, now: datetime, settings: PlannerSettings
) -> ScopeProgress:
    """The progress of a reading that has not searched yet.

    For this family the core progress's `floor` means "where the read starts"
    (`T0 - m`): the floor is proved per source, from the read itself, and lives
    in the codec's `adapter` object.
    """
    progress = new_progress(target_start, now, settings)
    return replace(progress, floor=target_start - settings.margin)


def catchall_planner_settings(settings: Any, *, cadence_seconds: int) -> PlannerSettings:
    """The shared planner's numbers for the catch-all family (§B.6.1, §B.8.2).

    The settings are read by attribute name, so this module imports no settings
    class and stays pure.
    """
    return PlannerSettings(
        bucket_seconds=settings.collection_interval_seconds,
        floor_margin_hours=settings.history_backfill_floor_margin_hours,
        max_span_hours=MAX_SPAN_HOURS,
        row_cap=settings.catchall_max_rows_per_search,
        row_target_share=ROW_TARGET_SHARE,
        initial_chunk_buckets=INITIAL_CHUNK_BUCKETS,
        min_chunk_buckets=MIN_CHUNK_BUCKETS,
        max_failures=settings.history_backfill_max_failures,
        max_lower_bound_share=settings.history_backfill_max_lower_bound_share,
        max_hours=settings.history_backfill_max_hours,
        max_gaps=MAX_GAPS,
        searches_per_tick=settings.history_backfill_searches_per_tick,
        cadence_seconds=cadence_seconds,
    )


def catchall_history_budget(
    settings: Any, *, used: int, seconds_since_tick_start: float
) -> StepBudget:
    """The history sub-budget of a tick (§B.6.1): what the live pass left over.

    Live collection goes first: the history spends only the searches the live
    pass did not use, and only the seconds the tick has left.
    """
    return StepBudget(
        searches=history_search_budget(settings, used=used),
        seconds_left=settings.history_backfill_seconds_per_tick - seconds_since_tick_start,
    )


def catchall_window_fit(
    progress: ScopeProgress,
    *,
    anchor: datetime,
    planner: PlannerSettings,
    settings: Any,
) -> WindowFit | None:
    """The window that fits the tick budget, or None before a density is known.

    §B.6.5: the estimate of §B.8 is computed from the density the read
    observed. Without an observation there is nothing to fit, and the whole
    asked window is read.
    """
    density = progress.rows_per_bucket
    if density is None:
        return None
    days = max(0, round((anchor - progress.target_start).total_seconds() / 86400))
    if days < DEFAULT_MIN_WINDOW_DAYS:
        return None
    return fit_window(
        days,
        lambda span_days: searches_for(
            span_days * (86400 // planner.bucket_seconds), density, planner
        ),
        end=anchor,
        settings=planner,
        searches_per_tick=settings.history_backfill_searches_per_tick,
    )


def catchall_finish(
    trigger: HistoryTrigger,
    reason: str,
    floors: SourceFloors,
    *,
    known: Sequence[str],
    target_start: datetime,
    margin: timedelta,
    bucket: timedelta,
) -> tuple[HistoryTrigger, str]:
    """The trigger and the reason a finished reading ends with (§B.3.4, §B.4.4).

    No catch-all source had an event in the window: the reading is unavailable
    and nothing is written. Otherwise a source that is cut (Ariel holds less
    than asked) or silent (no event at all) turns `COMPLETE` into `PARTIAL`,
    with the counts and never a source name.
    """
    if trigger not in (HistoryTrigger.FINISHED_COMPLETE, HistoryTrigger.FINISHED_PARTIAL):
        return trigger, reason
    if not floors.first_buckets:
        return (
            HistoryTrigger.NOTHING_TO_READ,
            "no catch-all source had an event in the window; nothing is written",
        )
    if trigger is HistoryTrigger.FINISHED_PARTIAL:
        return trigger, reason
    return finish_with_sources(
        trigger,
        reason,
        floors,
        known=known,
        target_start=target_start,
        margin=margin,
        bucket=bucket,
    )


def bucket_row(
    source_id: uuid.UUID,
    outcome: CatchallBucketOutcome,
    fact: CatchallBucketFact,
    *,
    bucket_seconds: int,
    now: datetime,
    origin: HistoryOrigin,
) -> dict[str, Any]:
    """The `catchall_bucket` row of one source and bucket.

    The live path and the history path write the same columns; only the marker
    (`origin`) and the collection columns (when it was written) differ.
    """
    assert outcome.provenance is not None
    return {
        "catchall_log_source_id": source_id,
        "bucket_start": outcome.bucket_start,
        "bucket_seconds": bucket_seconds,
        **marker_values(HistoryFamily.CATCHALL, origin),
        "events": fact.events,
        "records": fact.records,
        "distinct_senders": fact.distinct_senders,
        "distinct_senders_lower_bound": fact.distinct_senders_lower_bound,
        "measurement": fact.measurement.value,
        "top_senders": [
            {"identifier": identifier, "events": events} for identifier, events in fact.top_senders
        ],
        "admitted": fact.admitted,
        "below_admission_threshold": fact.below_admission_threshold,
        "rejected_over_quota": fact.rejected_over_quota,
        "untracked_senders": fact.untracked_senders,
        "untracked_events": fact.untracked_events,
        "query_provenance": outcome.provenance.as_json(),
        "collected_at": now,
    }


def _runs(
    outcomes: Sequence[CatchallBucketOutcome],
) -> list[tuple[IpsCellState, list[CatchallBucketOutcome]]]:
    """The outcomes of a span, grouped into contiguous runs of the same state."""
    runs: list[tuple[IpsCellState, list[CatchallBucketOutcome]]] = []
    for outcome in outcomes:
        if runs and runs[-1][0] is outcome.state:
            runs[-1][1].append(outcome)
        else:
            runs.append((outcome.state, [outcome]))
    return runs


def chunk_results(
    outcomes: Sequence[CatchallBucketOutcome], *, bucket_seconds: int, searches: int
) -> list[ChunkResult]:
    """The planner's chunk results of one read span.

    The buckets of a span tile it. A measured run is one result; a search
    truncated at one bucket is a `TRUNCATED` result of one bucket, which the
    planner writes as a lower bound; a failed or rejected run blocks the span
    (the planner halves the width and, at the minimum width, records a gap);
    a run the budget did not reach is `NOT_RUN` and proves nothing.

    `searches` is what the span really cost, its own truncation splits included;
    it is counted once, on the first result.
    """
    width = timedelta(seconds=bucket_seconds)
    results: list[ChunkResult] = []
    for state, buckets in _runs(outcomes):
        span = Span(buckets[0].bucket_start, buckets[-1].bucket_start + width)
        if state is IpsCellState.COMPLETE:
            results.append(
                ChunkResult(
                    span,
                    SearchStatus.COMPLETE,
                    rows=sum(len(bucket.rows) for bucket in buckets),
                    non_silent_buckets=sum(1 for bucket in buckets if bucket.rows),
                    searches=0,
                )
            )
        elif state is IpsCellState.LOWER_BOUND:
            for bucket in buckets:
                results.append(
                    ChunkResult(
                        Span(bucket.bucket_start, bucket.bucket_start + width),
                        SearchStatus.TRUNCATED,
                        rows=len(bucket.rows),
                        non_silent_buckets=1 if bucket.rows else 0,
                        searches=0,
                    )
                )
        elif state in (IpsCellState.FAILED, IpsCellState.REJECTED):
            results.append(ChunkResult(span, SearchStatus.FAILED, searches=0))
        else:
            results.append(ChunkResult(span, SearchStatus.NOT_RUN, searches=0))
    if results:
        results[0] = replace(results[0], searches=searches)
    return results


def fold_chunk(
    outcomes: Sequence[CatchallBucketOutcome],
    *,
    sources: Sequence[CatchallSource],
    fold: CatchallHistoryFold,
    policy: AdmissionPolicy,
    step: CatchallStep,
    now: datetime,
    searches: int = 1,
) -> CatchallChunk:
    """Fold one read span into the bucket rows it establishes.

    The span is folded oldest first, with the family's own pure fold and
    admission, so the ledger and the per-bucket quota see the buckets in time
    order (§B.4.4 "Order-dependent admission"). Two rules are the history's
    own: nothing of a source is written before its floor, and a source is
    zero-filled only from its floor on (§B.3.2 rules 1 and 3).
    """
    by_qradar = {source.qradar_id: source for source in sources}
    ordered = sorted(sources, key=lambda source: source.qradar_id)
    rows: list[dict[str, Any]] = []
    touches: dict[datetime, dict[tuple[uuid.UUID, str], SenderTouch]] = {}
    for outcome in outcomes:
        if not outcome.measured:
            continue
        by_source: dict[int, list[CatchallSenderRow]] = {}
        for row in outcome.rows:
            source = by_qradar.get(row.qradar_log_source_id)
            if source is None:
                # A row of a source the inventory does not know is not stored.
                continue
            by_source.setdefault(source.qradar_id, []).append(row)
        # The floor proof: the first bucket in which the source has a row.
        fold.floors = fold.floors.observe(
            [(by_qradar[qradar_id].key, outcome.bucket_start) for qradar_id in by_source],
            read_start=step.read_start,
            bucket_seconds=step.bucket_seconds,
        )
        measurement = (
            CatchallMeasurement.LOWER_BOUND
            if outcome.state is IpsCellState.LOWER_BOUND
            else CatchallMeasurement.EXACT
        )
        for source in ordered:
            source_rows = by_source.get(source.qradar_id, [])
            if not source_rows and measurement is CatchallMeasurement.LOWER_BOUND:
                # Its rows may be in the missing tail: unknown, not zero.
                continue
            if not fold.floors.writable(
                source.key,
                outcome.bucket_start,
                target_start=step.target_start,
                margin=step.margin,
            ):
                # Nothing of a source is written before its floor (§B.3.2 rule 1).
                continue
            ledger = fold.ledgers.setdefault(source.id, SenderLedger())
            fact = fold_source_bucket(
                source_rows,
                ledger,
                measurement=measurement,
                policy=policy,
                top_n=step.top_n,
            )
            rows.append(
                bucket_row(
                    source.id,
                    outcome,
                    fact,
                    bucket_seconds=step.bucket_seconds,
                    now=now,
                    origin=HistoryOrigin.HISTORY,
                )
            )
            for identifier in fact.tracked_events:
                bucket_touches = touches.setdefault(outcome.bucket_start, {})
                touch = bucket_touches.get((source.id, identifier))
                if touch is None:
                    bucket_touches[(source.id, identifier)] = SenderTouch(
                        outcome.bucket_start, outcome.bucket_start
                    )
                else:
                    touch.last_seen_at = max(touch.last_seen_at, outcome.bucket_start)
                    touch.first_seen_at = min(touch.first_seen_at, outcome.bucket_start)
    return CatchallChunk(
        rows=tuple(rows),
        touches=touches,
        results=tuple(
            chunk_results(outcomes, bucket_seconds=step.bucket_seconds, searches=searches)
        ),
    )


def merge_touches(
    chunks: Iterable[Mapping[datetime, Mapping[tuple[uuid.UUID, str], SenderTouch]]],
    *,
    buckets: Collection[datetime] | None = None,
) -> dict[tuple[uuid.UUID, str], SenderTouch]:
    """The touches of the given buckets, earliest first and latest last.

    `buckets` limits the merge to the buckets that are written: a sender seen
    only in a bucket the planner did not write is not a sender the history knows.
    """
    merged: dict[tuple[uuid.UUID, str], SenderTouch] = {}
    for chunk in chunks:
        for bucket, touches in chunk.items():
            if buckets is not None and bucket not in buckets:
                continue
            for key, touch in touches.items():
                known = merged.get(key)
                if known is None:
                    merged[key] = SenderTouch(touch.first_seen_at, touch.last_seen_at)
                else:
                    known.first_seen_at = min(known.first_seen_at, touch.first_seen_at)
                    known.last_seen_at = max(known.last_seen_at, touch.last_seen_at)
    return merged


def sender_rows(
    touches: Mapping[tuple[uuid.UUID, str], SenderTouch],
    *,
    instance_id: uuid.UUID,
    anchor: datetime,
    active_days: int,
    now: datetime,
) -> list[dict[str, Any]]:
    """The `catchall_sender` rows a history step writes for the senders it touched.

    A sender a history step creates is `REVIEWED` and `reviewed_by = 'history'`
    (Guard 1, §B.4.4): it was known before the Yes, so it is not a new sender
    for the analyst to review. It is active only when it was seen at or after
    `anchor - catchall_sender_active_days`, so a long-gone history sender does
    not hold a ceiling slot (§B.4.4 "Retirement").
    """
    cutoff = anchor - timedelta(days=active_days)
    rows: list[dict[str, Any]] = []
    for (source_id, identifier), touch in sorted(touches.items()):
        active = touch.last_seen_at >= cutoff
        rows.append(
            {
                "instance_id": instance_id,
                "catchall_log_source_id": source_id,
                "identifier": identifier,
                "first_seen_at": touch.first_seen_at,
                "last_seen_at": touch.last_seen_at,
                "active": active,
                # A sender that is written inactive was last seen then: that is
                # when it stopped, and the retention task deletes it by this.
                "retired_at": None if active else touch.last_seen_at,
                "review_status": CatchallReviewStatus.REVIEWED.value,
                "reviewed_by": HISTORY_REVIEWED_BY,
                "reviewed_at": now,
            }
        )
    return rows


def scope_row(
    view: ScopeRowView,
    *,
    instance_id: Any,
    family: HistoryFamily,
    scope_key: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """The full column mapping of a scope row, from a view of it.

    The lifecycle's `new_scope_row` and `transition_row` build the rows a
    scope's *state change* writes. A step that only advances the reading
    changes no state, so it needs this third builder; it writes the same
    columns, and the repository refuses a row that lacks one.
    """
    return {
        "instance_id": instance_id,
        "family": family.value,
        "scope_key": scope_key,
        "state": view.state.value,
        "days_requested": view.days_requested,
        "days_target": view.days_target,
        "target_start": view.target_start,
        "floor": view.floor,
        "through": view.through,
        "anchor": view.anchor,
        "started_at": view.started_at,
        "finished_at": view.finished_at,
        "reason": bounded_reason(reason if reason is not None else (view.reason or "")),
        "progress": dict(view.progress) if view.progress else {},
    }
