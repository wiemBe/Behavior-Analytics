"""REST provider contract for keyed multi-bucket Phase A searches."""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.providers.qradar_rest import QRadarRestProvider

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=10)
BUCKET_MS = 300_000


def _provider(rows: list[dict], captured: list[str]) -> QRadarRestProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            captured.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "bucketed-1", "status": "WAIT"})
        if path.endswith("/ariel/searches/bucketed-1/results"):
            return httpx.Response(200, json={"events": rows})
        if path.endswith("/ariel/searches/bucketed-1"):
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://qradar.example.internal/api",
        headers={"SEC": "t", "Version": "20.0", "Accept": "application/json"},
    )
    return QRadarRestProvider(
        base_url="https://qradar.example.internal",
        sec_token="t",
        client=client,
        rng=random.Random(1),
        sleep=no_sleep,
    )


def _row(index: int) -> dict:
    at = START + timedelta(minutes=5 * (index % 2))
    return {
        "qradar_id": index + 1,
        "k": int(at.timestamp() * 1000) // BUCKET_MS,
        "event_count": 300,
        "last_event_time": int((at + timedelta(minutes=4)).timestamp() * 1000),
    }


@pytest.mark.asyncio
async def test_bucketed_aql_has_key_group_limit_and_both_window_bounds() -> None:
    captured: list[str] = []
    provider = _provider([_row(0), _row(1)], captured)

    batch = await provider.get_log_source_metrics_bucketed(
        START, END, bucket_seconds=300, max_rows=2
    )

    query = captured[0]
    start_ms = int(START.timestamp() * 1000)
    end_ms = int(END.timestamp() * 1000)
    assert f'starttime / {BUCKET_MS} AS "k"' in query
    assert f"GROUP BY logsourceid, starttime / {BUCKET_MS}" in query
    assert "LIMIT 3" in query
    assert f"WHERE starttime >= {start_ms} AND starttime < {end_ms}" in query
    assert f"START {start_ms} STOP {end_ms}" in query
    assert batch.rows_returned == 2
    assert batch.truncated is False
    await provider.aclose()


@pytest.mark.asyncio
async def test_bucketed_n_plus_one_rows_are_truncated() -> None:
    provider = _provider([_row(0), _row(1), _row(2)], [])

    batch = await provider.get_log_source_metrics_bucketed(
        START, END, bucket_seconds=300, max_rows=2
    )

    assert batch.rows_returned == 2
    assert batch.truncated is True
    await provider.aclose()


@pytest.mark.asyncio
async def test_bucketed_misaligned_window_is_rejected_before_io() -> None:
    provider = _provider([], [])

    with pytest.raises(ValueError, match="epoch-aligned whole buckets"):
        await provider.get_log_source_metrics_bucketed(
            START + timedelta(seconds=1), END, bucket_seconds=300, max_rows=2
        )
    await provider.aclose()
