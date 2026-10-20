"""The console-wide `SUM(eventcount)` read (HB-5, HISTORY-BACKFILL §B.3.2).

The floor proof of the rule-match history backfill: one stored, validated,
versioned REST/Ariel aggregate, and a deterministic mock of it. Read-only.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.providers.base import CapabilityNotSupportedError, ProviderError
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import CONSOLE_EVENT_TOTALS_QUERY_VERSION
from tests.unit.test_qradar_rest_provider import build_provider

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
HOUR = timedelta(hours=1)
WEEK = timedelta(hours=168)


def key_of(at: datetime, bucket_seconds: int = 3600) -> int:
    return int(at.timestamp()) // bucket_seconds


def totals_handler(
    rows: list[dict],
    captured: dict | None = None,
    *,
    status: str = "COMPLETED",
):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/ariel/searches"):
            if captured is not None:
                captured["query"] = request.url.params["query_expression"]
            return httpx.Response(201, json={"search_id": "tot-1", "status": "WAIT"})
        if request.url.path.endswith("/ariel/searches/tot-1/results"):
            return httpx.Response(200, json={"events": rows})
        if request.url.path.endswith("/ariel/searches/tot-1"):
            return httpx.Response(200, json={"status": status, "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler


class TestRestProvider:
    @pytest.mark.asyncio
    async def test_the_query_is_stored_validated_and_unfiltered(self) -> None:
        captured: dict = {}
        rows = [{"k": key_of(START), "events": 42}]
        provider, _ = build_provider(totals_handler(rows, captured))

        totals = await provider.get_console_event_totals(
            START, START + HOUR, bucket_seconds=3600
        )

        query = captured["query"]
        start_ms = int(START.timestamp() * 1000)
        end_ms = int((START + HOUR).timestamp() * 1000)
        assert 'SELECT starttime / 3600000 AS "k"' in query
        assert 'SUM(eventcount) AS "events" FROM events' in query
        assert f"WHERE starttime >= {start_ms} AND starttime < {end_ms}" in query
        assert "GROUP BY starttime / 3600000" in query
        assert f"START {start_ms} STOP {end_ms}" in query
        # Unfiltered: the proof is that the console held a bucket at all.
        assert "logsourceid" not in query
        assert "creEventList" not in query
        assert totals.totals == {START: 42}
        assert totals.query_version == CONSOLE_EVENT_TOTALS_QUERY_VERSION
        assert totals.query == query
        assert totals.bucket_seconds == 3600
        assert (totals.window_start, totals.window_end) == (START, START + HOUR)

    @pytest.mark.asyncio
    async def test_a_window_over_168_hours_is_refused_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be made")

        provider, _ = build_provider(handler)

        with pytest.raises(ValueError, match="168-hour safety bound"):
            await provider.get_console_event_totals(
                START, START + WEEK + HOUR, bucket_seconds=3600
            )

    @pytest.mark.asyncio
    async def test_an_inverted_window_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be made")

        provider, _ = build_provider(handler)

        with pytest.raises(ValueError, match="after its start"):
            await provider.get_console_event_totals(START + HOUR, START, bucket_seconds=3600)

    @pytest.mark.asyncio
    async def test_an_unaligned_window_is_refused(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be made")

        provider, _ = build_provider(handler)

        with pytest.raises(ValueError, match="epoch-aligned whole buckets"):
            await provider.get_console_event_totals(
                START + timedelta(minutes=10), START + HOUR, bucket_seconds=3600
            )

    @pytest.mark.asyncio
    async def test_more_rows_than_buckets_fails_closed(self) -> None:
        rows = [{"k": key_of(START), "events": 1}, {"k": key_of(START) + 1, "events": 1}]
        provider, _ = build_provider(totals_handler(rows))

        with pytest.raises(ProviderError, match="more rows than buckets"):
            await provider.get_console_event_totals(START, START + HOUR, bucket_seconds=3600)

    @pytest.mark.asyncio
    async def test_a_repeated_bucket_fails_closed(self) -> None:
        rows = [{"k": key_of(START), "events": 1}, {"k": key_of(START), "events": 2}]
        provider, _ = build_provider(totals_handler(rows))

        with pytest.raises(ProviderError, match="repeat a bucket"):
            await provider.get_console_event_totals(
                START, START + 2 * HOUR, bucket_seconds=3600
            )

    @pytest.mark.asyncio
    async def test_a_row_outside_the_window_fails_closed(self) -> None:
        rows = [{"k": key_of(START) - 1, "events": 1}]
        provider, _ = build_provider(totals_handler(rows))

        with pytest.raises(ProviderError, match="outside the window"):
            await provider.get_console_event_totals(START, START + HOUR, bucket_seconds=3600)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "row",
        [
            {"k": "junk", "events": 1},
            {"k": key_of(START), "events": None},
            {"k": key_of(START), "events": -1},
            {"events": 1},
        ],
    )
    async def test_a_malformed_row_fails_closed(self, row) -> None:
        provider, _ = build_provider(totals_handler([row]))

        with pytest.raises(ProviderError, match="malformed"):
            await provider.get_console_event_totals(START, START + HOUR, bucket_seconds=3600)

    @pytest.mark.asyncio
    async def test_an_empty_answer_is_an_empty_total_not_an_error(self) -> None:
        provider, _ = build_provider(totals_handler([]))

        totals = await provider.get_console_event_totals(
            START, START + HOUR, bucket_seconds=3600
        )

        assert totals.totals == {}


class TestMockProvider:
    @pytest.mark.asyncio
    async def test_the_mock_is_deterministic_and_speaks_only_of_buckets_it_saw(self) -> None:
        mock = MockQRadarProvider(seed=1)

        first = await mock.get_console_event_totals(START, START + 3 * HOUR, bucket_seconds=3600)
        again = await mock.get_console_event_totals(START, START + 3 * HOUR, bucket_seconds=3600)

        assert first.totals == again.totals
        assert set(first.totals) == {START, START + HOUR, START + 2 * HOUR}
        assert all(total > 0 for total in first.totals.values())

    @pytest.mark.asyncio
    async def test_overlapping_spans_agree_on_the_shared_bucket(self) -> None:
        mock = MockQRadarProvider(seed=1)

        first = await mock.get_console_event_totals(START, START + 2 * HOUR, bucket_seconds=3600)
        second = await mock.get_console_event_totals(
            START + HOUR, START + 3 * HOUR, bucket_seconds=3600
        )

        assert first.totals[START + HOUR] == second.totals[START + HOUR]


class TestOtherProviders:
    @pytest.mark.asyncio
    async def test_mcp_cannot_run_it(self) -> None:
        provider = QRadarMCPProvider.__new__(QRadarMCPProvider)

        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_console_event_totals(START, START + HOUR, bucket_seconds=3600)

    def test_the_rest_method_is_a_coroutine(self) -> None:
        """Guard: the new method is one awaited read, not a fire-and-forget call."""
        assert inspect.iscoroutinefunction(QRadarMCPProvider.get_console_event_totals)
