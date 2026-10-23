"""The catch-all family's history adapter: the fold, the floors and the writes (HB-4)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.catchall.keyed_search import CatchallBucketOutcome, CatchallProvenance
from app.history.catchall import (
    HISTORY_REVIEWED_BY,
    CatchallHistoryFold,
    CatchallSource,
    CatchallStep,
    chunk_results,
    fold_chunk,
    merge_touches,
    reading_progress,
    sender_rows,
)
from app.history.origin import HistoryOrigin
from app.history.planner import PlannerSettings, SearchStatus
from app.models.enums import CatchallMeasurement, IpsCellState
from app.providers.dto import CatchallSenderRow
from app.waf.admission import AdmissionPolicy

BUCKET = 300
NOW = datetime(2026, 10, 13, 12, 10, tzinfo=UTC)
T0 = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
MARGIN = timedelta(hours=1)
SOURCE = uuid.UUID("11111111-1111-1111-1111-111111111111")
QRADAR_ID = 1001

PLANNER = PlannerSettings(
    bucket_seconds=BUCKET,
    floor_margin_hours=1,
    row_cap=2000,
    initial_chunk_buckets=12,
    min_chunk_buckets=1,
)
POLICY = AdmissionPolicy(max_new_per_bucket=20, max_active=2000, min_requests=3)


def step(target_start: datetime = T0) -> CatchallStep:
    return CatchallStep(
        target_start=target_start,
        margin=MARGIN,
        read_start=target_start - MARGIN,
        bucket_seconds=BUCKET,
        top_n=20,
    )


def provenance(start: datetime, count: int = 1) -> CatchallProvenance:
    return CatchallProvenance(
        search_window_start=start,
        search_window_end=start + timedelta(seconds=BUCKET * count),
        search_bucket_count=count,
        split_depth=0,
        device_type_ids=(67,),
        bucket_key="starttime / 300000",
    )


def row(
    sender: str, events: int = 10, at: datetime | None = None, source: int = QRADAR_ID
) -> CatchallSenderRow:
    return CatchallSenderRow(
        qradar_log_source_id=source,
        sender=sender,
        bucket_start=at or T0,
        events=events,
        records=1,
    )


def outcome(
    at: datetime,
    rows: tuple[CatchallSenderRow, ...] = (),
    state: IpsCellState = IpsCellState.COMPLETE,
    count: int = 1,
) -> CatchallBucketOutcome:
    return CatchallBucketOutcome(
        bucket_start=at,
        state=state,
        rows=rows,
        provenance=provenance(at, count),
    )


SOURCES = (CatchallSource(id=SOURCE, qradar_id=QRADAR_ID),)


# --------------------------------------------------------------------------- the row


def test_the_history_row_differs_from_the_live_one_only_in_the_marker() -> None:
    from app.catchall.fold import SenderLedger, fold_source_bucket
    from app.history.catchall import bucket_row

    fact = fold_source_bucket(
        [row("fw-01", 50)], SenderLedger(), measurement=CatchallMeasurement.EXACT, policy=POLICY,
        top_n=20,
    )
    at = T0
    live = bucket_row(
        SOURCE, outcome(at, (row("fw-01", 50),)), fact,
        bucket_seconds=BUCKET, now=NOW, origin=HistoryOrigin.LIVE,
    )
    history = bucket_row(
        SOURCE, outcome(at, (row("fw-01", 50),)), fact,
        bucket_seconds=BUCKET, now=NOW, origin=HistoryOrigin.HISTORY,
    )
    assert set(live) == set(history)
    assert {k: v for k, v in live.items() if k != "origin"} == {
        k: v for k, v in history.items() if k != "origin"
    }
    assert live["origin"] == "LIVE" and history["origin"] == "HISTORY"
    assert live["events"] == 50 and history["events"] == 50


# --------------------------------------------------------------------------- the floors


def test_nothing_of_a_source_is_written_before_its_floor() -> None:
    """The first event is at the read start, so the floor is one margin later."""
    fold = CatchallHistoryFold()
    first = T0 - MARGIN
    outcomes = [
        outcome(first + timedelta(seconds=BUCKET * index), (row("fw-01", 50),))
        for index in range(14)
    ]

    folded = fold_chunk(
        outcomes, sources=SOURCES, fold=fold, policy=POLICY, step=step(), now=NOW
    )

    # The margin bucket is before the floor: it is not written, and no zero is
    # made for it either.
    assert [row_["bucket_start"] for row_ in folded.rows] == [
        T0,
        T0 + timedelta(seconds=BUCKET),
    ]
    assert fold.floors.floor("1001", target_start=T0, margin=MARGIN) == T0


def test_a_source_that_starts_late_is_written_from_its_own_floor() -> None:
    """Each source has its own floor: the late one is written from its own."""
    fold = CatchallHistoryFold()
    late = CatchallSource(id=uuid.UUID("22222222-2222-2222-2222-222222222222"), qradar_id=2002)
    early_rows = (row("fw-01", 50),)
    late_rows = (row("fw-02", 50, source=2002),)
    outcomes = [
        outcome(T0 - MARGIN, early_rows),
        outcome(T0, early_rows),
        outcome(T0 + timedelta(seconds=BUCKET), early_rows),
        outcome(T0 + timedelta(seconds=2 * BUCKET), early_rows + late_rows),
    ]

    folded = fold_chunk(
        outcomes, sources=(*SOURCES, late), fold=fold, policy=POLICY, step=step(), now=NOW
    )

    floors = fold.floors
    assert floors.floor("1001", target_start=T0, margin=MARGIN) == T0
    # The late source's floor is one margin after its first event: past the window.
    assert floors.floor("2002", target_start=T0, margin=MARGIN) == T0 + timedelta(
        seconds=2 * BUCKET
    ) + MARGIN
    written = {r_["bucket_start"] for r_ in folded.rows}
    assert written == {T0, T0 + timedelta(seconds=BUCKET), T0 + timedelta(seconds=2 * BUCKET)}
    # Only the early source is written: the late one is not at its floor yet.
    assert {r_["catchall_log_source_id"] for r_ in folded.rows} == {SOURCE}


def test_a_source_with_no_event_at_all_is_written_nothing() -> None:
    """A quiet source gets no zero row before its floor, and none at all without one."""
    fold = CatchallHistoryFold()
    quiet = CatchallSource(id=uuid.UUID("22222222-2222-2222-2222-222222222222"), qradar_id=2002)
    outcomes = [
        outcome(T0 + timedelta(seconds=BUCKET * index), (row("fw-01", 50),)) for index in range(3)
    ]

    folded = fold_chunk(
        outcomes, sources=(quiet,), fold=fold, policy=POLICY, step=step(), now=NOW
    )

    assert folded.rows == ()
    assert fold.floors.first_buckets == {}


def test_a_quiet_source_is_zero_filled_from_its_floor_on() -> None:
    """A source with an event before the window is zero-filled in the window."""
    fold = CatchallHistoryFold()
    outcomes = [
        outcome(T0 - MARGIN, (row("fw-01", 50),)),
        outcome(T0, ()),
        outcome(T0 + timedelta(seconds=BUCKET), ()),
    ]

    folded = fold_chunk(
        outcomes, sources=SOURCES, fold=fold, policy=POLICY, step=step(), now=NOW
    )

    assert [(r_["bucket_start"], r_["events"]) for r_ in folded.rows] == [
        (T0, 0),
        (T0 + timedelta(seconds=BUCKET), 0),
    ]


# ----------------------------------------------------------------- the unmapped rows


def test_a_row_of_an_unknown_source_is_counted_and_not_stored() -> None:
    """The known row is folded; the row of an unknown source is only counted."""
    fold = CatchallHistoryFold()
    outcomes = [
        outcome(T0 - MARGIN, (row("fw-01", 50),)),
        outcome(T0, (row("fw-01", 50), row("ghost", 50, source=9999))),
    ]

    folded = fold_chunk(
        outcomes, sources=SOURCES, fold=fold, policy=POLICY, step=step(), now=NOW
    )

    assert folded.unmapped_rows == 1
    # Only the known row is folded: the unknown one's events are not in it.
    assert [(r_["bucket_start"], r_["events"]) for r_ in folded.rows] == [(T0, 50)]
    assert {r_["catchall_log_source_id"] for r_ in folded.rows} == {SOURCE}


# --------------------------------------------------------------------------- the chunk results


def test_a_truncated_single_bucket_is_a_lower_bound() -> None:
    results = chunk_results(
        [outcome(T0, (row("fw-01", 50),), IpsCellState.LOWER_BOUND)],
        bucket_seconds=BUCKET,
        searches=1,
    )

    assert len(results) == 1
    assert results[0].status is SearchStatus.TRUNCATED
    assert results[0].span.buckets(PLANNER) == 1


def test_a_failed_run_blocks_the_span_and_keeps_nothing() -> None:
    results = chunk_results(
        [
            outcome(T0, (row("fw-01", 50),)),
            outcome(T0 + timedelta(seconds=BUCKET), state=IpsCellState.FAILED),
        ],
        bucket_seconds=BUCKET,
        searches=2,
    )

    assert [r_.status for r_ in results] == [SearchStatus.COMPLETE, SearchStatus.FAILED]
    assert results[0].searches == 2 and results[1].searches == 0


def test_a_run_the_budget_did_not_reach_proves_nothing() -> None:
    results = chunk_results(
        [outcome(T0, state=IpsCellState.NOT_COLLECTED)],
        bucket_seconds=BUCKET,
        searches=0,
    )

    assert results[0].status is SearchStatus.NOT_RUN


# --------------------------------------------------------------------------- the senders


def test_a_history_sender_is_reviewed_by_history_and_active_by_the_anchor() -> None:
    from app.history.catchall import SenderTouch

    anchor = T0
    old = T0 - timedelta(days=9)
    recent = anchor - timedelta(days=1)
    touches = {
        (SOURCE, "old"): SenderTouch(old, old),
        (SOURCE, "recent"): SenderTouch(recent, recent),
    }

    rows = sender_rows(
        touches, instance_id=uuid.uuid4(), anchor=anchor, active_days=7, now=NOW
    )

    assert {r_["identifier"] for r_ in rows} == {"old", "recent"}
    for row_ in rows:
        assert row_["review_status"] == "REVIEWED"
        assert row_["reviewed_by"] == HISTORY_REVIEWED_BY
        assert row_["reviewed_at"] == NOW
    by_name = {r_["identifier"]: r_ for r_ in rows}
    assert by_name["old"]["active"] is False
    assert by_name["old"]["retired_at"] == old
    assert by_name["recent"]["active"] is True
    assert by_name["recent"]["retired_at"] is None


def test_the_touches_of_several_chunks_merge_to_the_first_and_the_last() -> None:
    from app.history.catchall import SenderTouch

    later = T0 + timedelta(seconds=BUCKET)
    first = {T0: {(SOURCE, "a"): SenderTouch(T0, T0)}}
    second = {later: {(SOURCE, "a"): SenderTouch(T0, later)}}

    merged = merge_touches([first, second])

    assert merged[(SOURCE, "a")].first_seen_at == T0
    assert merged[(SOURCE, "a")].last_seen_at == T0 + timedelta(seconds=BUCKET)


def test_only_the_written_buckets_count_as_touches() -> None:
    from app.history.catchall import SenderTouch

    written = {T0: {(SOURCE, "a"): SenderTouch(T0, T0)}}
    unwritten = {T0 + timedelta(seconds=BUCKET): {(SOURCE, "b"): SenderTouch(T0, T0)}}

    merged = merge_touches([written, unwritten], buckets={T0})

    assert set(merged) == {(SOURCE, "a")}


# --------------------------------------------------------------------------- the window


def test_the_read_starts_one_margin_before_the_asked_window() -> None:
    progress = reading_progress(T0, NOW, PLANNER)

    assert progress.target_start == T0
    assert progress.floor == T0 - MARGIN
    assert progress.through is None
