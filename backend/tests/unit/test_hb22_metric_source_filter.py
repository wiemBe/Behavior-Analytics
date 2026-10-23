"""Phase A metric searches take an optional log-source filter (HB-22 D2a).

`None` is the whole console: the AQL stays the unfiltered aggregate. A top-up passes
the sources that have no floor, as one `logsourceid IN (...)` of integers only.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.providers.qradar_rest import QRadarRestProvider

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
END = START + timedelta(minutes=5)


def _provider(captured: list[str]) -> QRadarRestProvider:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            captured.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "metric-hb22", "status": "WAIT"})
        if path.endswith("/ariel/searches/metric-hb22/results"):
            return httpx.Response(200, json={"events": []})
        if path.endswith("/ariel/searches/metric-hb22"):
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
        sleep=no_sleep,
        ariel_stop_pad_seconds=0,
    )


@pytest.mark.asyncio
async def test_an_unfiltered_metric_search_has_no_log_source_predicate() -> None:
    captured: list[str] = []
    provider = _provider(captured)
    await provider.get_log_source_metrics(START, END)
    assert "logsourceid IN" not in captured[0]
    assert "GROUP BY logsourceid" in captured[0]
    await provider.aclose()


@pytest.mark.asyncio
async def test_a_top_up_metric_search_names_only_the_given_sources() -> None:
    captured: list[str] = []
    provider = _provider(captured)
    await provider.get_log_source_metrics(START, END, qradar_ids=(9, 10))
    assert "AND logsourceid IN (9, 10)" in captured[0]
    assert "GROUP BY logsourceid" in captured[0]
    await provider.aclose()


@pytest.mark.asyncio
async def test_a_top_up_bucketed_search_names_only_the_given_sources() -> None:
    captured: list[str] = []
    provider = _provider(captured)
    window_end = START + timedelta(minutes=10)
    await provider.get_log_source_metrics_bucketed(
        START, window_end, bucket_seconds=300, max_rows=2, qradar_ids=[9]
    )
    assert "AND logsourceid IN (9) " in captured[0]
    assert "GROUP BY logsourceid, starttime /" in captured[0]
    await provider.aclose()


@pytest.mark.asyncio
async def test_an_empty_source_filter_is_refused_before_any_search() -> None:
    captured: list[str] = []
    provider = _provider(captured)
    with pytest.raises(ValueError, match="empty"):
        await provider.get_log_source_metrics(START, END, qradar_ids=[])
    assert captured == []
    await provider.aclose()
