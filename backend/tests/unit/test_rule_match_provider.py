"""The `creEventList` provider method: query text, row parsing, truncation.

T-015 section 11 fixes the live shape: `creEventList` comes back as a bare
integer id, and `COUNT(*)` / `SUM(eventcount)` come back as numbers.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.providers.base import CapabilityNotSupportedError, ProviderError
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import parse_rule_match_rows
from tests.unit.test_qradar_rest_provider import build_provider

START = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 12, 11, 0, tzinfo=UTC)


def search_handler(rows: list[dict], captured: dict | None = None, *, status: str = "COMPLETED"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path.endswith("/ariel/searches"):
            if captured is not None:
                captured["query"] = request.url.params["query_expression"]
            return httpx.Response(201, json={"search_id": "rm-1", "status": "WAIT"})
        if request.url.path.endswith("/ariel/searches/rm-1/results"):
            if captured is not None:
                captured["range"] = request.headers.get("Range")
            return httpx.Response(200, json={"events": rows})
        if request.url.path.endswith("/ariel/searches/rm-1"):
            return httpx.Response(200, json={"status": status, "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler


class TestParse:
    def test_the_7_6_row_shape_is_parsed(self) -> None:
        rows, malformed = parse_rule_match_rows(
            [{"rule_id": 100221, "records": 44037, "events": 44037.0}]
        )

        assert malformed == 0
        assert (rows[0].qradar_rule_id, rows[0].records, rows[0].events) == (
            100221, 44037, 44037,
        )

    def test_a_null_id_is_excluded_and_not_counted_as_malformed(self) -> None:
        """Events that matched no rule come back as a null id."""
        rows, malformed = parse_rule_match_rows(
            [
                {"rule_id": None, "records": 900, "events": 1200},
                {"rule_id": 5, "records": 1, "events": 1},
            ]
        )

        assert [r.qradar_rule_id for r in rows] == [5]
        assert malformed == 0

    @pytest.mark.parametrize(
        "row",
        [
            {"records": 1, "events": 1},  # id missing
            {"rule_id": "Load Basic Building Blocks", "records": 1, "events": 1},
            {"rule_id": 5.5, "records": 1, "events": 1},
            {"rule_id": True, "records": 1, "events": 1},
            {"rule_id": -3, "records": 1, "events": 1},
            {"rule_id": 5, "records": None, "events": 1},
            {"rule_id": 5, "records": 1, "events": None},
            {"rule_id": 5, "records": 1, "events": -2},
            {"rule_id": 5, "records": "many", "events": 1},
        ],
    )
    def test_a_malformed_row_is_dropped_and_counted(self, row) -> None:
        rows, malformed = parse_rule_match_rows([row])

        assert rows == []
        assert malformed == 1

    def test_numeric_strings_are_accepted(self) -> None:
        rows, _ = parse_rule_match_rows([{"rule_id": "100216", "records": "2", "events": "3"}])

        assert (rows[0].qradar_rule_id, rows[0].records, rows[0].events) == (100216, 2, 3)


class TestRestProvider:
    @pytest.mark.asyncio
    async def test_query_is_bounded_start_stop_and_capped(self) -> None:
        captured: dict = {}
        provider, _ = build_provider(search_handler([], captured), page_size=100, max_pages=5)

        batch = await provider.get_rule_matches(START, END, max_rows=200)

        query = captured["query"]
        start_ms, end_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
        assert "creEventList" in query
        assert "GROUP BY creEventList" in query
        assert "SUM(eventcount)" in query
        assert f"starttime >= {start_ms} AND starttime < {end_ms}" in query
        assert f"START {start_ms} STOP {end_ms}" in query
        assert "LIMIT 200" in query
        assert captured["range"] == "items=0-200"
        assert batch.row_cap == 200
        assert batch.query_version == "rule_match/v1"
        assert batch.query == query

    @pytest.mark.asyncio
    async def test_rows_are_parsed_and_a_short_page_is_not_truncated(self) -> None:
        rows = [
            {"rule_id": 100221, "records": 5, "events": 9},
            {"rule_id": None, "records": 2, "events": 2},
            {"rule_id": "junk", "records": 1, "events": 1},
        ]
        provider, _ = build_provider(search_handler(rows), page_size=100, max_pages=5)

        batch = await provider.get_rule_matches(START, END, max_rows=10)

        assert [r.qradar_rule_id for r in batch.rows] == [100221]
        assert batch.malformed_rows == 1
        assert batch.truncated is False

    @pytest.mark.asyncio
    async def test_a_full_page_is_truncated(self) -> None:
        rows = [{"rule_id": i, "records": 1, "events": 1} for i in range(3)]
        provider, _ = build_provider(search_handler(rows), page_size=100, max_pages=5)

        batch = await provider.get_rule_matches(START, END, max_rows=3)

        assert batch.truncated is True

    @pytest.mark.asyncio
    async def test_truncation_counts_dropped_rows_in_the_page(self) -> None:
        rows = [{"rule_id": None, "records": 1, "events": 1}] * 2
        provider, _ = build_provider(search_handler(rows), page_size=100, max_pages=5)

        batch = await provider.get_rule_matches(START, END, max_rows=2)

        assert batch.rows == []
        assert batch.truncated is True

    @pytest.mark.asyncio
    async def test_the_cap_is_bounded_by_the_provider_ceiling(self) -> None:
        provider, _ = build_provider(search_handler([]), page_size=10, max_pages=2)

        batch = await provider.get_rule_matches(START, END, max_rows=5000)

        assert batch.row_cap == 20

    @pytest.mark.asyncio
    async def test_a_failed_search_raises_instead_of_returning_an_empty_batch(self) -> None:
        provider, _ = build_provider(search_handler([], status="ERROR"))

        with pytest.raises(ProviderError):
            await provider.get_rule_matches(START, END, max_rows=10)

    @pytest.mark.asyncio
    async def test_an_inverted_window_is_rejected_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be made")

        provider, _ = build_provider(handler)

        with pytest.raises(ValueError, match="after its start"):
            await provider.get_rule_matches(END, START, max_rows=10)


class TestOtherProviders:
    @pytest.mark.asyncio
    async def test_mock_covers_rules_building_blocks_and_an_unknown_id(self) -> None:
        mock = MockQRadarProvider(seed=1)
        batch = await mock.get_rule_matches(START, END, max_rows=100)
        again = await mock.get_rule_matches(START, END, max_rows=100)

        ids = {r.qradar_rule_id for r in batch.rows}
        known_rules = {r.qradar_id for r in await mock.list_rules()}
        known_bbs = {r.qradar_id for r in await mock.list_building_blocks()}
        assert ids & known_rules
        assert ids & known_bbs
        assert ids - known_rules - known_bbs
        assert batch.truncated is False
        assert batch.rows == again.rows

    @pytest.mark.asyncio
    async def test_mock_truncates_at_the_cap(self) -> None:
        batch = await MockQRadarProvider(seed=1).get_rule_matches(START, END, max_rows=2)

        assert len(batch.rows) == 2
        assert batch.truncated is True

    @pytest.mark.asyncio
    async def test_mcp_cannot_run_it(self) -> None:
        provider = QRadarMCPProvider.__new__(QRadarMCPProvider)

        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_rule_matches(START, END, max_rows=10)
