"""History topup source chunks use real REST AQL and publish only whole buckets."""

import math
import re
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.collectors.metric_collector import HISTORY_TOPUP_SOURCE_CHUNK_SIZE, MetricCollector
from app.core.config import Settings
from app.history import phase_a
from app.history.planner import PlannerSettings, SearchStatus, Span
from app.providers.qradar_rest import QRadarRestProvider

START = datetime(2026, 10, 12, 10, tzinfo=UTC)
STEP = timedelta(minutes=5)
IDS = list(range(2**63 - 2000, 2**63))


def rest_fixture(*, fail_at=None, truncate_at=None, advance=None):
    queries = []
    urls = []
    responses = {}

    def handler(request):
        if request.method == "POST":
            query = request.url.params["query_expression"]
            queries.append(query)
            urls.append(str(request.url))
            if len(queries) == fail_at:
                return httpx.Response(503)
            selected = [int(qid) for qid in re.search(r"IN \(([^)]+)\)", query)[1].split(",")]
            bounds = re.search(r"starttime >= (\d+) AND starttime < (\d+)", query)
            first, end = [int(v) for v in bounds.groups()]
            keyed = 'AS "k"' in query
            rows = []
            for ms in range(first, end, 300000):
                for qid in selected:
                    # Include a silent source and an empty bucket in every chunk.
                    if qid % 13 == 0 or ms == int((START + STEP).timestamp() * 1000):
                        continue
                    row = {"qradar_id": qid, "event_count": qid % 31 + 1,
                           "last_event_time": ms + 290000}
                    if keyed:
                        row["k"] = ms // 300000
                    rows.append(row)
            if len(queries) == truncate_at:
                rows *= 10  # provider's cap detects truncation, never a complete prefix
            sid = str(len(queries))
            responses[sid] = rows
            if advance is not None:
                advance()
            return httpx.Response(201, json={"search_id": sid, "status": "WAIT"})
        if request.url.path.endswith("/results"):
            sid = request.url.path.split("/")[-2]
            return httpx.Response(200, json={"events": responses[sid]})
        return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})

    client = httpx.AsyncClient(base_url="https://qradar.example/api",
                               transport=httpx.MockTransport(handler))
    provider = QRadarRestProvider(base_url="https://qradar.example", sec_token="test",
                                  client=client, max_retries=0)
    return provider, queries, urls


def collector(provider, *, clock=lambda: START, **settings):
    return MetricCollector(None, provider, settings=Settings(_env_file=None, **settings),
                           clock=clock)


@pytest.mark.parametrize("buckets", [1, 2, 3])
async def test_2000_ids_merge_like_one_search_without_lost_or_duplicated_buckets(buckets):
    provider, queries, urls = rest_fixture()
    try:
        found = await collector(provider)._search_window(
            START, buckets, 300, max_searches=64, qradar_ids=[*IDS, *IDS[:10]],
        )
        chunk_queries = queries.copy()
        chunk_urls = urls.copy()
        end = START + buckets * STEP
        if buckets == 1:
            expected = {START: await provider.get_log_source_metrics(START, end, qradar_ids=IDS)}
        else:
            expected = (await provider.get_log_source_metrics_bucketed(
                START, end, bucket_seconds=300, max_rows=10000, qradar_ids=IDS,
            )).buckets
        expected_searches = math.ceil(2000 / HISTORY_TOPUP_SOURCE_CHUNK_SIZE)
        assert found.searches == len(chunk_queries) == expected_searches
        assert max(map(len, chunk_queries)) < 6000
        assert max(len(url.encode()) for url in chunk_urls) < 8192
        assert found.splits == 0 and not found.capped and found.failure is None
        for index in range(buckets):
            at = START + index * STEP
            actual = found.completed[at].samples
            assert sorted(actual, key=lambda s: s.qradar_id) == sorted(
                expected.get(at, []), key=lambda s: s.qradar_id,
            )
            assert len({s.qradar_id for s in actual}) == len(actual)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("buckets", [1, 3])
@pytest.mark.parametrize("fail_at", [1, 4, 8])
async def test_a_failing_chunk_never_completes_a_subset(buckets, fail_at):
    provider, queries, _ = rest_fixture(fail_at=fail_at)
    try:
        found = await collector(provider)._search_window(
            START, buckets, 300, max_searches=64, qradar_ids=IDS,
        )
        assert found.completed == {} and found.failure is not None
        assert found.searches == len(queries) == fail_at
        results = phase_a.chunk_results(Span(START, START + buckets * STEP), found.completed,
                                       settings=PlannerSettings(), searches=found.searches,
                                       failed=found.failed)
        assert all(result.status is SearchStatus.FAILED for result in results)
    finally:
        await provider.aclose()


async def test_truncated_chunk_splits_and_never_publishes_poisoned_rows():
    provider, queries, _ = rest_fixture(truncate_at=2)
    try:
        found = await collector(provider, ariel_multi_bucket_max_rows=1000,
                                pw_max_rows_per_search=1000)._search_window(
            START, 3, 300, max_searches=64, qradar_ids=IDS,
        )
        assert found.splits == 1 and found.failure is None
        assert found.searches == len(queries) == 18
        assert len(found.completed) == 3
        assert all(s.event_count <= 31 for b in found.completed.values() for s in b.samples)
    finally:
        await provider.aclose()


@pytest.mark.parametrize("stop", ["budget", "deadline", "between_chunks", "empty"])
async def test_caps_do_not_publish_subsets_or_send_predictably_unusable_searches(stop):
    now = START

    def advance():
        nonlocal now
        now += STEP

    provider, queries, _ = rest_fixture(advance=advance if stop == "between_chunks" else None)
    try:
        call = collector(provider, clock=lambda: now)._search_window(
            START, 2, 300, max_searches=7 if stop == "budget" else 64,
            qradar_ids=[] if stop == "empty" else IDS,
            deadline=START if stop == "deadline" else START + STEP,
        )
        if stop == "empty":
            with pytest.raises(ValueError, match="empty"):
                await call
        else:
            found = await call
            assert found.capped and found.completed == {} and found.failure is None
        assert len(queries) == (1 if stop == "between_chunks" else 0)
    finally:
        await provider.aclose()


async def test_failure_in_next_time_span_retains_only_the_complete_prefix():
    provider, queries, _ = rest_fixture(fail_at=10)
    try:
        found = await collector(provider)._search_window(
            START, 21, 300, max_searches=64, qradar_ids=IDS,
        )
        assert found.searches == len(queries) == 10
        assert list(found.completed) == [START + index * STEP for index in range(20)]
        assert found.failed == Span(START + 20 * STEP, START + 21 * STEP)
        assert found.failure is not None
        assert all(len({s.qradar_id for s in bucket.samples}) == len(bucket.samples)
                   for bucket in found.completed.values())
    finally:
        await provider.aclose()


async def test_truncated_single_bucket_chunk_cannot_be_completed_from_other_chunks():
    provider, queries, _ = rest_fixture(truncate_at=2)
    provider._page_size = 10
    provider._max_pages = 100
    try:
        found = await collector(provider)._search_window(
            START, 1, 300, max_searches=64, qradar_ids=IDS,
        )
        assert found.completed == {} and found.failure is not None
        assert found.searches == len(queries) == 2 and found.splits == 0
    finally:
        await provider.aclose()


@pytest.mark.parametrize("refuse_at", [1, 2, 8])
async def test_permit_refusal_counts_only_chunks_actually_sent(refuse_at):
    from contextlib import asynccontextmanager

    from app.services.ariel_permit import ArielPermitUnavailable

    class Budget:
        wait_seconds = 1
        instance_limit = 3
        global_limit = 6
        enters = 0

        @asynccontextmanager
        async def permit(self):
            self.enters += 1
            yield object() if self.enters < refuse_at else None

    provider, queries, _ = rest_fixture()
    provider._permit = Budget()
    try:
        found = await collector(provider)._search_window(
            START, 2, 300, max_searches=64, qradar_ids=IDS,
        )
        assert isinstance(found.failure, ArielPermitUnavailable)
        assert found.completed == {}
        assert found.searches == len(queries) == refuse_at - 1
    finally:
        await provider.aclose()
