"""Large history top-ups spend a bounded budget on frozen, disjoint units."""

import copy
import uuid
from datetime import timedelta
from types import SimpleNamespace

import pytest

from app.collectors.metric_collector import HistoryReport, MetricCollector
from app.history import phase_a
from app.history.lifecycle import DecisionView, ScopeRowView, scope_view
from app.history.planner import ScopeProgress
from app.history.progress_codec import ProgressCodecError, decode_progress, encode_progress
from app.history.source_floors import SourceFloors
from app.history.state import HistoryState
from app.history.topup import CHUNKS_KEY, CUTOFFS_KEY, TopupChunks
from app.providers.base import ProviderError
from app.services.ariel_permit import ArielPermitUnavailable
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    ScriptedMockProvider,
    settings_for,
)


class RecordingSession:
    def __init__(self):
        self.rows = {}
        self.attempts = 0

    async def execute(self, statement, rows):
        for row in rows:
            self.attempts += 1
            self.rows.setdefault((row["log_source_id"], row["bucket_start"]), row)


def resumed(*, buckets=6, width=2, count=8000):
    ids = list(range(1, count + 1))
    monitored = {q: uuid.UUID(int=q) for q in reversed(ids)}
    start = ANCHOR - buckets * STEP
    progress = ScopeProgress(target_start=start, started_at=FIRST_TICK,
                             floor=start, chunk_buckets=width, density_checked=True)
    adapter = {**SourceFloors({str(q): start - timedelta(hours=1) for q in ids}).encode(),
               phase_a.TOPUP_SOURCES_KEY: [str(q) for q in ids]}
    row = ScopeRowView(state=HistoryState.READING, anchor=ANCHOR, started_at=FIRST_TICK,
                       target_start=start, progress=encode_progress(progress, adapter))
    session = RecordingSession()
    provider = ScriptedMockProvider(ids)
    collector = MetricCollector(
        session, provider, settings=settings_for(), clock=lambda: FIRST_TICK,
    )

    async def sources(_):
        return monitored

    collector._monitored_sources = sources
    return collector, provider, session, monitored, row


async def step(collector, row, *, now=FIRST_TICK, live=0):
    report = HistoryReport()
    values = await collector._history_step(
        SimpleNamespace(id=uuid.UUID(int=123)), DecisionView("YES", 7), row,
        now=now, live_searches=live, cadence_seconds=300, report=report,
    )
    return scope_view(values), report


async def test_8000_sources_three_spans_complete_in_ten_ticks_without_duplicates():
    collector, provider, session, monitored, row = resumed()
    counts = []
    for tick in range(10):
        row, report = await step(collector, row)
        counts.append(report.searches)
        decoded = decode_progress(row.progress)
        chunks = decoded.adapter[CHUNKS_KEY]
        assert sum(c["read"] for c in chunks) == min((tick + 1) * 20, 192)
        assert row.state == (HistoryState.COMPLETE if tick == 9 else HistoryState.READING)
        assert [q for c in chunks for q in c["ids"]] == list(range(1, 8001))
    assert counts == [10] * 9 + [6]
    assert decoded.progress.searches == len(provider.calls) == 96
    assert decoded.progress.buckets_read == 6
    assert len(session.rows) == session.attempts == 48000
    assert all(len(f) == 250 for f in provider.source_filters)
    inverse = {v: k for k, v in monitored.items()}
    assert all(r["event_count"] == provider.sample(inverse[sid], at, 300).event_count
               for (sid, at), r in session.rows.items())


async def test_failed_chunk_keeps_span_unread_others_publish_and_next_tick_retries():
    collector, provider, session, _, row = resumed(buckets=2)
    provider.fail = lambda *_: len(provider.calls) == 1
    row, report = await step(collector, row)
    state = decode_progress(row.progress)
    assert isinstance(report.failure, ProviderError) and report.searches == 10
    assert state.adapter[CHUNKS_KEY][0]["read"] == 0
    assert state.adapter[CHUNKS_KEY][0]["error"] == "ProviderError"
    assert sum(c["read"] for c in state.adapter[CHUNKS_KEY]) == 18
    assert len(session.rows) == 4500 and row.state is HistoryState.READING
    provider.fail = None
    row, report = await step(collector, row)
    assert provider.source_filters[10] == frozenset(range(1, 251))
    assert report.failure is None
    assert decode_progress(row.progress).adapter[CHUNKS_KEY][0]["error"] is None


async def test_membership_frozen_when_source_removed_and_another_added():
    collector, provider, session, monitored, row = resumed(buckets=2)
    row, _ = await step(collector, row)
    partition = copy.deepcopy(decode_progress(row.progress).adapter[CHUNKS_KEY])
    del monitored[3000]
    monitored[8001] = uuid.UUID(int=8001)
    for _ in range(3):
        row, _ = await step(collector, row)
        if row.state is not HistoryState.READING:
            break
    chunks = decode_progress(row.progress).adapter[CHUNKS_KEY]
    assert [c["ids"] for c in chunks] == [c["ids"] for c in partition]
    assert all(8001 not in chosen for chosen in provider.source_filters)
    assert len(session.rows) == 15998 and row.state is HistoryState.COMPLETE


async def test_permit_deferral_keeps_successful_units_and_counts_only_sent_searches():
    collector, provider, session, _, row = resumed(buckets=2)
    provider.fail = lambda *_: len(provider.calls) == 3
    provider.failure_type = lambda _: ArielPermitUnavailable(1, 3, 6)
    row, report = await step(collector, row)
    decoded = decode_progress(row.progress)
    assert report.searches == decoded.progress.searches == 2
    assert len(session.rows) == 1000 and report.failure is None
    assert row.reason == "deferred: no Ariel permit within 1 s"
    assert sum(c["read"] for c in decoded.adapter[CHUNKS_KEY]) == 4


@pytest.mark.parametrize("stop", ["budget", "deadline", "expired"])
async def test_no_search_when_budget_deadline_or_time_limit_ends(stop):
    collector, provider, session, _, row = resumed()
    now = FIRST_TICK
    live = 16 if stop == "budget" else 0
    if stop == "deadline":
        collector._clock = lambda: FIRST_TICK + timedelta(seconds=121)
    if stop == "expired":
        now += timedelta(hours=73)
    row, report = await step(collector, row, now=now, live=live)
    assert provider.calls == [] and session.rows == {} and report.searches == 0
    assert row.state is (HistoryState.PARTIAL if stop == "expired" else HistoryState.READING)


async def test_truncation_splits_only_the_affected_chunk_and_retains_prefix():
    collector, provider, session, _, row = resumed(buckets=2)
    provider.truncate_multi = True
    row, report = await step(collector, row, live=13)
    decoded = decode_progress(row.progress)
    assert report.searches == 3 and len(session.rows) == 500
    assert decoded.adapter[CHUNKS_KEY][0]["read"] == 2
    assert all(c["read"] == 0 for c in decoded.adapter[CHUNKS_KEY][1:])
    assert [call[2] for call in provider.calls] == [2, 1, 1]
    assert row.state is HistoryState.READING


@pytest.mark.parametrize("fault", ["fields", "duplicate", "negative", "membership", "time"])
def test_corrupt_chunk_state_fails_closed(fault):
    state = TopupChunks.load({}, ["1", "2"], ANCHOR - STEP, ANCHOR, STEP, 250)
    adapter = state.encode()
    if fault == "fields":
        state.chunks[0]["unknown"] = 1
    elif fault == "duplicate":
        state.chunks[0]["ids"] = [1, 1, 2]
    elif fault == "negative":
        state.chunks[0]["read"] = -1
    elif fault == "membership":
        state.chunks[0]["ids"] = [1, 3]
    else:
        adapter["topup_chunk_start"] = "bad"
    with pytest.raises(ProgressCodecError):
        TopupChunks.load(adapter, ["1", "2"], ANCHOR - STEP, ANCHOR, STEP, 250)


async def test_incomplete_cutoff_stays_behind_every_unread_chunk():
    collector, _, _, _, row = resumed(buckets=2)
    row, _ = await step(collector, row)
    decoded = decode_progress(row.progress)
    assert decoded.progress.through is None
    assert decoded.adapter[CUTOFFS_KEY][FIRST_TICK.isoformat()] == (ANCHOR - 2 * STEP).isoformat()
    row, _ = await step(collector, row)
    row, _ = await step(collector, row)
    assert row.state is HistoryState.READING
    row, _ = await step(collector, row)
    assert row.state is HistoryState.COMPLETE
    assert decode_progress(row.progress).adapter[CUTOFFS_KEY][FIRST_TICK.isoformat()] == (
        ANCHOR.isoformat()
    )


async def test_last_failed_chunk_prevents_complete_until_its_retry():
    collector, provider, session, _, row = resumed(buckets=1, count=2750)
    row, _ = await step(collector, row)
    provider.fail = lambda *_: True
    row, report = await step(collector, row)
    chunks = decode_progress(row.progress).adapter[CHUNKS_KEY]
    assert report.searches == 1 and row.state is HistoryState.READING
    assert [c["read"] for c in chunks] == [1] * 10 + [0]
    assert chunks[-1]["error"] == "ProviderError" and len(session.rows) == 2500
    provider.fail = None
    row, report = await step(collector, row)
    assert row.state is HistoryState.COMPLETE and report.searches == 1
    assert len(session.rows) == session.attempts == 2750


async def test_split_prefix_survives_failure_and_retry_never_reinserts_it():
    collector, provider, session, _, row = resumed(buckets=2, count=2750)
    provider.truncate_multi = True
    provider.fail = lambda *_: len(provider.calls) == 3
    row, report = await step(collector, row)
    chunks = decode_progress(row.progress).adapter[CHUNKS_KEY]
    assert chunks[0]["read"] == 1 and chunks[0]["error"] == "ProviderError"
    assert report.searches == 10 and len(session.rows) == 1250
    provider.fail = None
    calls = len(provider.calls)
    row, _ = await step(collector, row)
    assert provider.source_filters[calls] == frozenset(range(1, 251))
    assert provider.calls[calls][0] == ANCHOR - STEP
    for _ in range(5):
        row, _ = await step(collector, row)
        if row.state is not HistoryState.READING:
            break
    assert row.state is HistoryState.COMPLETE
    assert len(session.rows) == session.attempts == 5500


@pytest.mark.parametrize("field,value", [
    ("ids", []), ("ids", "1"), ("ids", [0]), ("ids", [True]),
    ("ids", ["1"]), ("ids", {1: None}), ("ids", list(range(1, 252))),
    ("read", True), ("read", "0"), ("read", 2), ("error", {}),
])
def test_invalid_chunk_values_are_rejected(field, value):
    state = TopupChunks.load({}, ["1"], ANCHOR - STEP, ANCHOR, STEP, 250)
    state.chunks[0][field] = value
    keys = list(map(str, value)) if field == "ids" and isinstance(value, list) else ["1"]
    with pytest.raises(ProgressCodecError):
        TopupChunks.load(state.encode(), keys, ANCHOR - STEP, ANCHOR, STEP, 250)


@pytest.mark.parametrize("chunks", [None, {}, [], [None], 1])
def test_invalid_partition_shapes_are_rejected(chunks):
    with pytest.raises(ProgressCodecError):
        TopupChunks.load(
            {CHUNKS_KEY: chunks, "topup_chunk_start": (ANCHOR - STEP).isoformat()},
            [], ANCHOR - STEP, ANCHOR, STEP, 250,
        )


@pytest.mark.parametrize("offset", [STEP, timedelta(seconds=1), -timedelta(seconds=1)])
def test_non_bucket_chunk_window_is_rejected(offset):
    with pytest.raises(ProgressCodecError):
        TopupChunks.load({}, ["1"], ANCHOR + offset, ANCHOR, STEP, 250)
