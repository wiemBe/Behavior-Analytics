"""History top-up chunks through the collector command path and PostgreSQL."""

from datetime import timedelta

import pytest

from app.history.phase_a import TOPUP_SOURCES_KEY
from app.history.planner import ScopeProgress
from app.history.progress_codec import decode_progress, encode_progress
from app.history.source_floors import SourceFloors
from app.services.ariel_permit import ArielPermitUnavailable
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    ScriptedMockProvider,
    history_rows,
    make_console,
    say_yes,
    scope_of,
    settings_for,
    tick,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


@pytest.mark.parametrize("failure", [False, True, "permit"])
async def test_history_topup_step_counts_chunks_and_never_stores_a_subset(db_session, failure):
    qids = list(range(1, 252))
    console = await make_console(db_session, qids)
    await say_yes(db_session, console.instance)
    provider = ScriptedMockProvider(qids)
    settings = settings_for()
    await tick(db_session, console.instance, provider, settings, FIRST_TICK)
    scope = await scope_of(db_session, console.instance)
    assert scope is not None and scope.state == "READING"
    # Resume an already planned top-up with two buckets remaining. The tick still
    # runs collect(), its live pass, the real planner, settlement and persistence.
    start = ANCHOR - 2 * STEP
    scope.target_start = start
    scope.progress = encode_progress(
        ScopeProgress(target_start=start, started_at=scope.started_at, floor=start,
                      chunk_buckets=2, density_checked=True),
        {**SourceFloors({str(qid): start - timedelta(hours=1) for qid in qids}).encode(),
         TOPUP_SOURCES_KEY: [str(qid) for qid in qids]},
    )
    await db_session.flush()
    provider.fail = lambda first, end, count: failure and end <= ANCHOR and len(
        provider.source_filters[-1] or []
    ) == 1
    if failure == "permit":
        provider.failure_type = lambda _message: ArielPermitUnavailable(1, 3, 6)
    marked = len(provider.calls)
    report = await tick(db_session, console.instance, provider, settings, FIRST_TICK + STEP)
    scope = await scope_of(db_session, console.instance)
    assert report.history is not None and scope is not None
    assert report.history.searches == (1 if failure == "permit" else 2)
    progress = decode_progress(scope.progress).progress
    assert progress.searches == (0 if failure == "permit" else 2)
    calls = [filt for call, filt in zip(provider.calls[marked:], provider.source_filters[marked:],
                                      strict=True) if call[1] <= ANCHOR]
    assert list(map(len, calls)) == [250, 1]
    rows = await history_rows(db_session, console.instance)
    if failure == "permit":
        assert scope.state == "READING" and scope.last_error_class is None
        assert scope.reason == "deferred: no Ariel permit within 1 s"
        assert rows == [] and progress.buckets_read == 0 and progress.chunk_buckets == 2
        assert report.history.failure is None
    elif failure:
        assert scope.state != "COMPLETE" and scope.last_error_class == "ProviderError"
        assert rows == [] and progress.buckets_read == 0
        assert report.history.failure is not None
    else:
        assert scope.state == "COMPLETE"
        assert len(rows) == 502 and progress.buckets_read == 2
        id_map = {source.id: qid for qid, source in console.sources.items()}
        assert len({(row.log_source_id, row.bucket_start) for row in rows}) == 502
        assert all(row.event_count == provider.sample(
            id_map[row.log_source_id], row.bucket_start, 300,
        ).event_count for row in rows)
