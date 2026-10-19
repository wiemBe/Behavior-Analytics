"""The pure core of a Phase A history step (HISTORY-BACKFILL §B.3.2, §B.4.1; HB-3)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import Settings
from app.history import phase_a
from app.history.phase_a import CompletedBucket
from app.history.planner import (
    PlannerSettings,
    SearchStatus,
    Span,
    window_buckets,
)
from app.history.source_floors import SourceFloors
from app.history.state import HistoryTrigger
from app.providers.dto import LogSourceMetricSample

BUCKET = timedelta(minutes=5)
MARGIN = timedelta(hours=1)
SETTINGS = PlannerSettings(floor_margin_hours=1)
T0 = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
READ_START = T0 - MARGIN
NOW = datetime(2026, 9, 10, tzinfo=UTC)
IDS = {1: uuid.uuid4(), 2: uuid.uuid4(), 3: uuid.uuid4()}


def sample(qid: int, start: datetime, count: int = 100) -> LogSourceMetricSample:
    return LogSourceMetricSample(
        qradar_id=qid,
        bucket_start=start,
        bucket_seconds=300,
        event_count=count,
        average_eps=count / 300,
        stored_event_count=count,
    )


def completed(start: datetime, *qids: int) -> CompletedBucket:
    return CompletedBucket(
        samples=[sample(q, start) for q in qids],
        search_window_start=start,
        search_window_end=start + BUCKET,
        search_bucket_count=1,
        split_depth=0,
        duration_ms=5,
        collected_at=NOW,
    )


def fold(buckets, *, floors=None, monitored=None, mock=True):  # type: ignore[no-untyped-def]
    return phase_a.fold_step(
        buckets,
        monitored=IDS if monitored is None else monitored,
        floors=floors or SourceFloors(),
        read_start=READ_START,
        target_start=T0,
        settings=SETTINGS,
        collector="log_source_metric",
        provider="ScriptedProvider",
        aggregation="SUM(eventcount)",
        telemetry=["volume"],
        mock=mock,
    )


def test_a_row_exists_only_at_or_after_the_floor_of_its_source() -> None:
    # Source 1 has its first event in the read's first bucket: its floor is T0.
    buckets = {READ_START + i * BUCKET: completed(READ_START + i * BUCKET, 1) for i in range(30)}

    out = fold(buckets)

    starts = sorted(row["bucket_start"] for row in out.rows)
    assert starts[0] == T0  # the 12 margin buckets prove the floor and write nothing
    assert len(starts) == 30 - 12
    assert all(row["log_source_id"] == IDS[1] for row in out.rows)


def test_a_monitored_source_gets_its_zero_fill_only_from_its_own_floor() -> None:
    # Source 2 appears at T0 + 1 h: its floor is T0 + 2 h; before it, not even a zero.
    first_two = T0 + timedelta(hours=1)
    buckets = {}
    for i in range(60):
        start = READ_START + i * BUCKET
        who = (1, 2) if start >= first_two else (1,)
        buckets[start] = completed(start, *who)
    # And from T0 + 2 h + 1 bucket on, source 2 falls silent: it is zero-filled.
    silent_from = T0 + timedelta(hours=2) + BUCKET
    for start in list(buckets):
        if start >= silent_from:
            buckets[start] = completed(start, 1)

    out = fold(buckets)

    two = {r["bucket_start"]: r for r in out.rows if r["log_source_id"] == IDS[2]}
    assert min(two) == T0 + timedelta(hours=2)  # the floor, not the first event
    assert two[T0 + timedelta(hours=2)]["event_count"] == 100
    zero = two[silent_from]
    assert zero["event_count"] == 0 and zero["average_eps"] == 0.0
    assert zero["query_provenance"]["zero_filled"] is True
    assert zero["completeness"].value == "COMPLETE"
    assert zero["last_event_at"] is None and zero["payload_signature"] is None


def test_a_silent_monitored_source_has_no_row_and_no_floor() -> None:
    buckets = {READ_START + i * BUCKET: completed(READ_START + i * BUCKET, 1) for i in range(20)}

    out = fold(buckets)

    assert IDS[2] not in {r["log_source_id"] for r in out.rows}
    assert set(out.floors.first_buckets) == {"1"}
    assert out.zero_filled == 0


def test_an_unmonitored_source_is_never_stored_or_floored() -> None:
    buckets = {
        READ_START + i * BUCKET: completed(READ_START + i * BUCKET, 1, 99) for i in range(20)
    }

    out = fold(buckets)

    assert {r["log_source_id"] for r in out.rows} == {IDS[1]}
    assert "99" not in out.floors.first_buckets


def test_the_marker_and_provenance_say_history() -> None:
    buckets = {READ_START + i * BUCKET: completed(READ_START + i * BUCKET, 1) for i in range(14)}

    mock = fold(buckets, mock=True)
    real = fold(buckets, mock=False)

    assert {r["collection_source"] for r in mock.rows} == {"mock_history"}
    assert {r["collection_source"] for r in real.rows} == {"ariel_history"}
    row = real.rows[0]
    assert row["query_provenance"]["history"] is True
    assert row["query_provenance"]["floor"] == T0.isoformat()
    assert row["watermark_at"] is None
    assert row["query_provenance"]["collector"] == "log_source_metric"


def test_the_fold_keeps_the_floors_of_earlier_steps() -> None:
    earlier = SourceFloors({"1": READ_START})
    later = {T0 + i * BUCKET: completed(T0 + i * BUCKET, 1) for i in range(5)}

    out = fold(later, floors=earlier)

    assert len(out.rows) == 5  # floor T0 is known from the earlier step: no margin to skip


def test_chunk_results_complete_prefix_then_a_failed_search() -> None:
    chunk = Span(T0, T0 + 8 * BUCKET)
    done = {T0 + i * BUCKET: completed(T0 + i * BUCKET, 1, 2) for i in range(3)}
    failed = Span(T0 + 3 * BUCKET, T0 + 5 * BUCKET)

    results = phase_a.chunk_results(chunk, done, settings=SETTINGS, searches=4, failed=failed)

    assert [r.status for r in results] == [SearchStatus.COMPLETE, SearchStatus.FAILED]
    assert results[0].span == Span(T0, T0 + 3 * BUCKET)
    assert results[0].rows == 6 and results[0].non_silent_buckets == 3
    assert results[0].searches == 4 and results[1].searches == 0
    assert results[1].span == failed


def test_a_lone_failed_bucket_is_failed_never_a_lower_bound() -> None:
    chunk = Span(T0, T0 + 2 * BUCKET)
    failed = Span(T0, T0 + BUCKET)

    results = phase_a.chunk_results(chunk, {}, settings=SETTINGS, searches=2, failed=failed)

    assert [r.status for r in results] == [SearchStatus.FAILED]
    assert results[0].lower_bound_buckets == 0


def test_chunk_results_not_run_when_the_cap_or_clock_stopped_it() -> None:
    chunk = Span(T0, T0 + 4 * BUCKET)
    done = {T0: completed(T0, 1)}

    results = phase_a.chunk_results(chunk, done, settings=SETTINGS, searches=1, failed=None)

    assert [r.status for r in results] == [SearchStatus.COMPLETE, SearchStatus.NOT_RUN]
    assert results[1].span == Span(T0 + BUCKET, T0 + 4 * BUCKET)


def test_chunk_results_refuse_a_failure_that_skips_a_bucket() -> None:
    chunk = Span(T0, T0 + 4 * BUCKET)
    with pytest.raises(ValueError):
        phase_a.chunk_results(
            chunk, {T0: completed(T0, 1)}, settings=SETTINGS, searches=2,
            failed=Span(T0 + 2 * BUCKET, T0 + 3 * BUCKET),
        )


def test_a_reading_with_no_event_of_a_monitored_source_is_unavailable() -> None:
    trigger, reason = phase_a.finish(
        HistoryTrigger.FINISHED_COMPLETE,
        "reached the end",
        SourceFloors(),
        known=["1", "2"],
        target_start=T0,
        settings=SETTINGS,
    )

    assert trigger is HistoryTrigger.NOTHING_TO_READ
    assert "no event" in reason.lower() or "holds no event" in reason


def test_a_cut_and_a_silent_source_turn_complete_into_partial_with_counts() -> None:
    floors = SourceFloors({"1": READ_START, "2": T0 + timedelta(days=1)})

    trigger, reason = phase_a.finish(
        HistoryTrigger.FINISHED_COMPLETE,
        "reached the end",
        floors,
        known=["1", "2", "3"],
        target_start=T0,
        settings=SETTINGS,
    )

    assert trigger is HistoryTrigger.FINISHED_PARTIAL
    assert "1 of 3 sources" in reason and "1 source had no event" in reason
    assert "2" not in reason.replace("1 of 3", "").replace("1 source", "")  # counts, no names


def test_a_complete_reading_of_whole_sources_stays_complete() -> None:
    floors = SourceFloors({"1": READ_START, "2": READ_START + BUCKET})

    trigger, _ = phase_a.finish(
        HistoryTrigger.FINISHED_COMPLETE, "ok", floors, known=["1", "2"],
        target_start=T0, settings=SETTINGS,
    )

    assert trigger is HistoryTrigger.FINISHED_COMPLETE


def test_other_triggers_pass_through_finish_unchanged() -> None:
    trigger, reason = phase_a.finish(
        HistoryTrigger.TIME_LIMIT, "too long", SourceFloors(), known=["1"],
        target_start=T0, settings=SETTINGS,
    )
    assert (trigger, reason) == (HistoryTrigger.TIME_LIMIT, "too long")


def test_the_window_estimate_is_the_one_of_the_design() -> None:
    """§B.8.2: 42 / 209 / 2 088 searches for 20 / 100 / 1 000 sources over 28 days."""
    settings = Settings(encryption_key="x" * 44)
    for sources, expected in ((20, 42), (100, 209), (1000, 2088)):
        ps = phase_a.planner_settings(settings, monitored=sources, cadence_seconds=300)
        assert window_buckets(28, ps) == 8352
        assert phase_a.estimate_searches(28, sources, ps) == expected


def test_the_first_chunk_is_sized_for_the_monitored_sources() -> None:
    settings = Settings(encryption_key="x" * 44)
    few = phase_a.planner_settings(settings, monitored=1, cadence_seconds=300)
    many = phase_a.planner_settings(settings, monitored=1000, cadence_seconds=300)
    assert few.initial_chunk_buckets == 2016
    assert many.initial_chunk_buckets == 3  # floor(4000 / (1.25 x 1000))


def test_rebuild_batches_take_the_limit_and_keep_the_rest_in_order() -> None:
    pending = phase_a.merge_pending(["3", "1"], ["2", "3"])
    assert pending == ["1", "2", "3"]
    assert phase_a.next_rebuild_batch(pending, 2) == (["1", "2"], ["3"])
    assert phase_a.next_rebuild_batch(pending, 5) == (["1", "2", "3"], [])
    with pytest.raises(ValueError):
        phase_a.next_rebuild_batch(pending, 0)


def test_a_wrong_pending_shape_raises() -> None:
    assert phase_a.pending_of({}) == []
    assert phase_a.pending_of({"rebuild_pending": ["1"]}) == ["1"]
    for bad in ("1", [1], [""], None):
        with pytest.raises(ValueError):
            phase_a.pending_of({"rebuild_pending": bad})


@pytest.mark.asyncio
async def test_the_mock_provider_answers_an_old_window_deterministically() -> None:
    """History reads weeks into the past; the mock must give the same answer every time."""
    from app.providers.mock import MockQRadarProvider

    start = datetime(2026, 6, 1, 0, 0, tzinfo=UTC)  # far older than the mock's own clock
    end = start + 12 * BUCKET
    first = await MockQRadarProvider(seed=1337).get_log_source_metrics_bucketed(
        start, end, bucket_seconds=300, max_rows=5000
    )
    second = await MockQRadarProvider(seed=1337).get_log_source_metrics_bucketed(
        start, end, bucket_seconds=300, max_rows=5000
    )
    single = await MockQRadarProvider(seed=1337).get_log_source_metrics(start, start + BUCKET)

    assert first.truncated is False and len(first.buckets) == 12
    assert first.buckets == second.buckets
    assert first.buckets[start] == single  # a one-bucket search and a keyed one agree
