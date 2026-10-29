"""Phase A history, the pure core of one step (HISTORY-BACKFILL §B.3.2, §B.4.1, §B.6.2; HB-3).

The collector (`MetricCollector`) reads the console, runs the searches and writes; this
module decides what the rows are. Given the provider's bucketed samples for the settled
spans, the monitored sources and the per-source floors, it returns the `log_source_metric`
row mappings. No database, no provider, no clock, no settings import.

What it keeps from the live writer, column for column: `sample_columns` and `zero_columns`
are the ones the live `_write_bucket` uses too, so a history row of a bucket is the row the
live path would have written for the same samples. Three things differ and are the marker:
`collection_source` (`ariel_history`, or `mock_history` for the mock provider),
`query_provenance` (`history: true` and the source's `floor`) and `watermark_at` (none).

Floor of the read, in one sentence. The core `ScopeProgress.floor` means "where the read
starts" for this family: Phase A proves each source's floor from the read itself
(`SourceFloors`, `F_s = max(T0, first_event_bucket_s + m)`), so the first span is the floor
proof and the read starts at `T0 - m` (§B.6.2 step 1). The per-source floors live in the
codec's `adapter`; the core never sees them. A source is written only at or after its own
`F_s` and is zero-filled only from there on (§B.3.2 rule 3), never before.

Monitored sources only. The live writer stores a row for every source it can map, monitored
or not, but zero-fills only monitored ones. History reads the past for learning, and only a
monitored source is baselined, so an unmonitored source's history would be storage without a
reader (the same argument as the live zero-fill, `_zero_filled_rows`).

Top-up ("Read history now", a `RESTART`). The restart keeps the previous floors until the
new reading's first step. That step plans a fresh window from the anchor and searches only
the monitored sources that have no floor yet, one `logsourceid IN (...)` on the same
aggregate, and folds only those sources onto the floors already proved. A source that
already has a floor is not read again. Rows still insert with `ON CONFLICT DO NOTHING`.
A source that joined after the anchor gets history only before the anchor, from its own
floor; the span between the anchor and its first live bucket stays without rows (a missing
row is "no claim", never a zero). There is no per-source anchor on purpose.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any

from app.history.origin import HistoryOrigin, marker_values
from app.history.planner import (
    ChunkResult,
    PlannerSettings,
    SearchStatus,
    Span,
    row_fit_buckets,
    searches_for,
    window_buckets,
)
from app.history.source_floors import SourceFloors, finish_with_sources_coded
from app.history.state import HistoryFamily, HistoryTrigger
from app.i18n.messages import MessageRef, render
from app.models.enums import BucketCompleteness
from app.providers.dto import LogSourceMetricSample

NO_EVENT_MESSAGE = MessageRef("history.phase_a.no_event")
NO_MONITORED_MESSAGE = MessageRef("history.phase_a.no_monitored")
NO_EVENT_REASON = render(NO_EVENT_MESSAGE)
NO_MONITORED_REASON = render(NO_MONITORED_MESSAGE)
REBUILD_PENDING_KEY = "rebuild_pending"
#: Source keys a restarted reading may search: the ones that had no floor. Absent on a
#: reading that is not a top-up, which searches every monitored source. Not a codec field.
TOPUP_SOURCES_KEY = "topup_sources"


@dataclass(frozen=True)
class CompletedBucket:
    """One bucket a search completed: its samples and how they were found."""

    samples: list[LogSourceMetricSample]
    search_window_start: datetime
    search_window_end: datetime
    search_bucket_count: int
    split_depth: int
    duration_ms: int
    collected_at: datetime


# --------------------------------------------------------------------------- shared columns


def source_key(qradar_id: int) -> str:
    """The key of a source in `SourceFloors` and in `rebuild_pending`: its QRadar id."""
    return str(qradar_id)


def sample_columns(sample: LogSourceMetricSample) -> dict[str, Any]:
    """The columns a measured sample fills, exactly as the live writer stores them."""
    return {
        "bucket_seconds": sample.bucket_seconds,
        "event_count": sample.event_count,
        "average_eps": sample.average_eps,
        "peak_eps": sample.peak_eps,
        "last_event_at": sample.last_event_at,
        "event_delay_seconds": sample.event_delay_seconds,
        "unknown_event_count": sample.unknown_event_count,
        "stored_event_count": sample.stored_event_count,
        "parsed_username_ratio": sample.parsed_username_ratio,
        "parsed_source_ip_ratio": sample.parsed_source_ip_ratio,
        "distinct_qid_count": sample.distinct_qid_count,
        "distinct_username_count": sample.distinct_username_count,
        "distinct_source_ip_count": sample.distinct_source_ip_count,
        "collection_error_count": sample.collection_error_count,
        "payload_signature": sample.payload_signature,
    }


def zero_columns(bucket_seconds: int) -> dict[str, Any]:
    """The columns of a zero-filled bucket: a measured silence, not an assumption."""
    return {
        "bucket_seconds": bucket_seconds,
        "event_count": 0,
        "average_eps": 0.0,
        "peak_eps": 0.0,
        "last_event_at": None,
        "event_delay_seconds": None,
        "unknown_event_count": 0,
        "stored_event_count": 0,
        "parsed_username_ratio": None,
        "parsed_source_ip_ratio": None,
        "distinct_qid_count": 0,
        "distinct_username_count": 0,
        "distinct_source_ip_count": 0,
        "collection_error_count": 0,
        "payload_signature": None,
    }


def bucket_provenance(
    *,
    collector: str,
    start: datetime,
    end: datetime,
    provider: str,
    sample_count: int,
    aggregation: str | None,
    telemetry: Iterable[str],
    completed: CompletedBucket | None,
) -> dict[str, Any]:
    """Non-secret evidence of how a bucket's rows were produced (live and history alike)."""
    provenance: dict[str, Any] = {
        "collector": collector,
        "window_start": start.isoformat(),
        "window_end": end.isoformat(),
        "provider": provider,
        "sample_count": sample_count,
        "event_count_aggregation": aggregation,
        "telemetry": sorted(telemetry),
    }
    if completed is not None:
        provenance.update(
            {
                "search_window_start": completed.search_window_start.isoformat(),
                "search_window_end": completed.search_window_end.isoformat(),
                "search_bucket_count": completed.search_bucket_count,
                "bucket_key": f"starttime / {int((end - start).total_seconds()) * 1000}",
                "split_depth": completed.split_depth,
            }
        )
    return provenance


# --------------------------------------------------------------------------- the fold


@dataclass(frozen=True)
class StepFold:
    """What a settled step stores: the rows, and the floors the step proved."""

    rows: list[dict[str, Any]]
    floors: SourceFloors
    zero_filled: int


def fold_step(
    buckets: Mapping[datetime, CompletedBucket],
    *,
    monitored: Mapping[int, uuid.UUID],
    floors: SourceFloors,
    read_start: datetime,
    target_start: datetime,
    settings: PlannerSettings,
    collector: str,
    provider: str,
    aggregation: str | None,
    telemetry: Iterable[str],
    mock: bool,
) -> StepFold:
    """Turn the buckets of the settled spans into `log_source_metric` row mappings.

    `buckets` holds only buckets whose search completed (the settled prefix); anything else
    is not passed in, so nothing is invented for it. Two passes: the floors first (the first
    bucket in which a monitored source has a row, over this step and the earlier ones), then
    the rows, so a source whose first event lies in this step is already known when its own
    buckets are written. A row of source `s` exists only at or after `F_s`; a monitored source
    with a floor and no sample in a bucket at or after it gets the live zero-fill row.
    """
    seconds = settings.bucket_seconds
    margin = settings.margin
    starts = sorted(buckets)
    floors = floors.observe(
        (
            (source_key(sample.qradar_id), start)
            for start in starts
            for sample in buckets[start].samples
            if sample.qradar_id in monitored
        ),
        read_start=read_start,
        bucket_seconds=seconds,
    )
    floor_of: dict[int, datetime] = {}
    for qradar_id in monitored:
        floor = floors.floor(source_key(qradar_id), target_start=target_start, margin=margin)
        if floor is not None:
            floor_of[qradar_id] = floor

    marker = marker_values(HistoryFamily.PHASE_A, HistoryOrigin.HISTORY, mock=mock)
    rows: list[dict[str, Any]] = []
    zero_filled = 0
    for start in starts:
        completed = buckets[start]
        end = start + settings.bucket
        base = bucket_provenance(
            collector=collector,
            start=start,
            end=end,
            provider=provider,
            sample_count=len(completed.samples),
            aggregation=aggregation,
            telemetry=telemetry,
            completed=completed,
        )
        reported: set[int] = set()
        for sample in completed.samples:
            floor = floor_of.get(sample.qradar_id)
            if floor is None or start < floor:
                continue
            reported.add(sample.qradar_id)
            rows.append(
                _row(
                    monitored[sample.qradar_id],
                    start,
                    completed,
                    marker,
                    {**base, "history": True, "floor": floor.isoformat()},
                    sample_columns(sample),
                )
            )
        for qradar_id, floor in floor_of.items():
            if start < floor or qradar_id in reported:
                continue
            zero_filled += 1
            rows.append(
                _row(
                    monitored[qradar_id],
                    start,
                    completed,
                    marker,
                    {**base, "zero_filled": True, "history": True, "floor": floor.isoformat()},
                    zero_columns(seconds),
                )
            )
    return StepFold(rows=rows, floors=floors, zero_filled=zero_filled)


def _row(
    log_source_id: uuid.UUID,
    start: datetime,
    completed: CompletedBucket,
    marker: Mapping[str, str],
    provenance: dict[str, Any],
    columns: dict[str, Any],
) -> dict[str, Any]:
    return {
        "log_source_id": log_source_id,
        "bucket_start": start,
        "completeness": BucketCompleteness.COMPLETE,
        **marker,
        "query_provenance": provenance,
        "collection_duration_ms": completed.duration_ms,
        "collected_at": completed.collected_at,
        "watermark_at": None,
        **columns,
    }


# --------------------------------------------------------------------------- chunk results


def chunk_stats(buckets: Iterable[CompletedBucket]) -> tuple[int, int]:
    """`(rows, non_silent_buckets)` of the completed buckets of a chunk (Ariel rows)."""
    rows = non_silent = 0
    for bucket in buckets:
        rows += len(bucket.samples)
        non_silent += 1 if bucket.samples else 0
    return rows, non_silent


def chunk_results(
    chunk: Span,
    completed: Mapping[datetime, CompletedBucket],
    *,
    settings: PlannerSettings,
    searches: int,
    failed: Span | None,
) -> list[ChunkResult]:
    """The results one planned chunk gives `settle_step`, after the split routine ran.

    The routine searches left to right, so the completed buckets are a prefix of the chunk.
    The prefix is `COMPLETE`; what follows is `FAILED` over the search that failed, or
    `NOT_RUN` when the cap or the time ran out. A one-bucket search that failed or stayed
    truncated is `FAILED` for Phase A (the live path writes nothing for it either), never a
    lower bound. The chunk's `searches` are charged to its first result.
    """
    bucket = settings.bucket
    count = chunk.buckets(settings)
    prefix = 0
    while prefix < count and chunk.start + prefix * bucket in completed:
        prefix += 1
    results: list[ChunkResult] = []
    spent = searches
    if prefix:
        span = Span(chunk.start, chunk.start + prefix * bucket)
        rows, non_silent = chunk_stats(
            completed[chunk.start + index * bucket] for index in range(prefix)
        )
        results.append(
            ChunkResult(
                span, SearchStatus.COMPLETE, rows=rows, non_silent_buckets=non_silent,
                searches=spent,
            )
        )
        spent = 0
    if prefix < count:
        rest_start = chunk.start + prefix * bucket
        if failed is not None:
            if failed.start != rest_start:
                raise ValueError("the failed search does not follow the completed prefix")
            results.append(ChunkResult(failed, SearchStatus.FAILED, searches=spent))
        else:
            results.append(
                ChunkResult(Span(rest_start, chunk.end), SearchStatus.NOT_RUN, searches=spent)
            )
    return results


# --------------------------------------------------------------------------- the window


def planner_settings(
    settings: Any, *, monitored: int, cadence_seconds: int
) -> PlannerSettings:
    """The planner's numbers for Phase A, from the application settings.

    The first chunk is sized for the monitored sources (one row per source per bucket), so a
    big fleet starts narrow instead of spending its first searches on truncation splits.
    """
    base = PlannerSettings(
        bucket_seconds=int(settings.collection_interval_seconds),
        floor_margin_hours=int(settings.history_backfill_floor_margin_hours),
        row_cap=int(settings.ariel_multi_bucket_max_rows),
        max_failures=int(settings.history_backfill_max_failures),
        max_lower_bound_share=float(settings.history_backfill_max_lower_bound_share),
        max_hours=int(settings.history_backfill_max_hours),
        searches_per_tick=int(settings.history_backfill_searches_per_tick),
        cadence_seconds=int(cadence_seconds),
    )
    fit = row_fit_buckets(float(max(monitored, 0)), base)
    return replace(base, initial_chunk_buckets=fit)


def estimate_searches(days: int, monitored: int, settings: PlannerSettings) -> int:
    """§B.8.2: `ceil(window buckets / min(2016, row target / monitored sources))`."""
    return searches_for(window_buckets(days, settings), float(max(monitored, 0)), settings)


# --------------------------------------------------------------------------- finishing


def earliest_floor(
    floors: SourceFloors,
    known: Collection[str],
    *,
    target_start: datetime,
    settings: PlannerSettings,
) -> datetime | None:
    """The earliest `F_s` of the monitored sources, for the scope row's `floor` column."""
    found = [
        floor
        for key in known
        if (floor := floors.floor(key, target_start=target_start, margin=settings.margin))
        is not None
    ]
    return min(found) if found else None


def finish(
    trigger: HistoryTrigger,
    reason: str,
    floors: SourceFloors,
    *,
    known: Collection[str],
    target_start: datetime,
    settings: PlannerSettings,
) -> tuple[HistoryTrigger, str]:
    """The finishing trigger of the Phase A scope, as its sentence only.

    Kept for the callers that only need the words; `finish_coded` is the same rule
    and also returns the sentence's wire reference.
    """
    trigger, reason, _ = finish_coded(
        trigger,
        reason,
        floors,
        known=known,
        target_start=target_start,
        settings=settings,
    )
    return trigger, reason


def finish_coded(
    trigger: HistoryTrigger,
    reason: str,
    floors: SourceFloors,
    *,
    known: Collection[str],
    target_start: datetime,
    settings: PlannerSettings,
    reason_message: MessageRef | dict[str, object] | None = None,
) -> tuple[HistoryTrigger, str, dict[str, object] | None]:
    """The finishing trigger of the Phase A scope, with its wire reference.

    A reading that reached its end with no event of any monitored source is `UNAVAILABLE`
    (nothing was written, §B.12 row 2); otherwise a cut or silent source turns `COMPLETE`
    into `PARTIAL` with the counts (`finish_with_sources_coded`). Any other trigger is
    unchanged.
    """
    if trigger is HistoryTrigger.FINISHED_COMPLETE and not any(
        key in floors.first_buckets for key in known
    ):
        return HistoryTrigger.NOTHING_TO_READ, NO_EVENT_REASON, NO_EVENT_MESSAGE.to_wire()
    return finish_with_sources_coded(
        trigger,
        reason,
        floors,
        known=known,
        target_start=target_start,
        margin=settings.margin,
        bucket=settings.bucket,
        reason_message=reason_message,
    )


def read_sources(floors: SourceFloors, known: Collection[str]) -> list[str]:
    """The monitored sources that were read (they have a floor), in a stable order."""
    return sorted(key for key in known if key in floors.first_buckets)


def merge_pending(existing: Sequence[str], read: Iterable[str]) -> list[str]:
    """`rebuild_pending` after a reading: the earlier list plus the sources just read."""
    return sorted(set(existing) | set(read))


def next_rebuild_batch(pending: Sequence[str], limit: int) -> tuple[list[str], list[str]]:
    """Split the pending list into this tick's batch and the rest (`limit` >= 1)."""
    if limit < 1:
        raise ValueError("the rebuild batch is at least one source")
    return list(pending[:limit]), list(pending[limit:])


def pending_of(adapter: Mapping[str, Any]) -> list[str]:
    """`rebuild_pending` from a decoded adapter; a wrong shape raises (fail closed)."""
    raw = adapter.get(REBUILD_PENDING_KEY, [])
    if not isinstance(raw, list) or not all(isinstance(item, str) and item for item in raw):
        raise ValueError("rebuild_pending must be a list of non-empty strings")
    return list(raw)
