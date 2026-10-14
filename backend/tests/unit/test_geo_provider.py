"""Geo search: the AQL, the guards, the mock's parity and MCP."""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.geo.fold import GeoDirection, fold_geo_bucket
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderCapability,
    ProviderError,
)
from app.providers.mock import MockQRadarProvider, geo_key
from app.providers.qradar_mcp import QRadarMCPProvider
from tests.unit.test_qradar_rest_provider import build_provider

BUCKET = 300
START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
END = START + timedelta(seconds=3 * BUCKET)
KEY0 = int(START.timestamp()) // BUCKET
US = geo_key("US", "United States", 6252001)


def scripted(rows: list[dict[str, Any]]) -> tuple[Callable, list[str]]:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            queries.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "geo-1", "status": "WAIT"})
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": rows})
        if "/ariel/searches/geo-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, queries


def row(key: int = KEY0, **over: Any) -> dict[str, Any]:
    return {"geo": US, "k": key, "events": 5, **over}


async def search(
    rows: list[dict[str, Any]] | None = None,
    direction: GeoDirection = GeoDirection.SOURCE,
    max_rows: int = 10,
    **over: Any,
):
    handler, queries = scripted(rows or [])
    provider, _ = build_provider(handler, page_size=50, max_pages=1)
    result = await provider.get_geo_keyed(
        over.pop("start", START),
        over.pop("end", END),
        direction=direction,
        bucket_seconds=BUCKET,
        max_rows=max_rows,
    )
    return result, queries[0]


def outside_lookup(aql: str) -> str:
    """The query with every ``GEO::LOOKUP(...)`` call removed."""
    return re.sub(r"GEO::LOOKUP\([^)]*\)", "", aql)


class TestAql:
    @pytest.mark.parametrize(
        ("direction", "field", "other"),
        [
            (GeoDirection.SOURCE, "sourceip", "destinationip"),
            (GeoDirection.DESTINATION, "destinationip", "sourceip"),
        ],
    )
    async def test_the_address_is_only_inside_the_lookup(
        self, direction: GeoDirection, field: str, other: str
    ) -> None:
        _, aql = await search(direction=direction)
        assert f"GEO::LOOKUP({field}, 'country', 'iso_code')" in aql
        assert field not in outside_lookup(aql)
        assert other not in aql
        assert "INCIDR" not in aql

    async def test_it_groups_by_the_lookup_and_the_bucket_key(self) -> None:
        _, aql = await search()
        lookup = "GEO::LOOKUP(sourceip, 'country', 'iso_code')"
        start_ms = int(START.timestamp() * 1000)
        end_ms = int(END.timestamp() * 1000)
        assert aql.startswith(
            f'SELECT {lookup} AS "geo", starttime / {BUCKET * 1000} AS "k", '  # noqa: S608
            'SUM(eventcount) AS "events" FROM events '
        )
        assert f"WHERE starttime >= {start_ms} AND starttime < {end_ms} " in aql
        assert f"GROUP BY {lookup}, starttime / {BUCKET * 1000} " in aql
        assert "ORDER BY SUM(eventcount) DESC LIMIT 11 " in aql

    async def test_the_scope_is_padded_by_ariel_scope(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(
            handler, page_size=50, max_pages=1, ariel_stop_pad_seconds=60
        )
        provider._clock = lambda: END + timedelta(hours=1)
        await provider.get_geo_keyed(
            START, END, direction=GeoDirection.SOURCE, bucket_seconds=BUCKET, max_rows=10
        )
        end_ms = int(END.timestamp() * 1000)
        assert queries[0].endswith(f"STOP {end_ms + 60_000}")
        assert f"starttime < {end_ms} " in queries[0]

    async def test_max_rows_is_bounded_by_the_results_read(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=5, max_pages=2)
        await provider.get_geo_keyed(
            START, END, direction=GeoDirection.SOURCE, bucket_seconds=BUCKET, max_rows=5000
        )
        assert "LIMIT 11 " in queries[0]


class TestGuards:
    async def test_an_unaligned_window_is_refused_before_any_request(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError, match="epoch-aligned"):
            await provider.get_geo_keyed(
                START + timedelta(seconds=7),
                END,
                direction=GeoDirection.SOURCE,
                bucket_seconds=BUCKET,
                max_rows=10,
            )
        assert queries == []

    async def test_an_unknown_direction_is_refused_before_any_request(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError):
            await provider.get_geo_keyed(
                START,
                END,
                direction="sourceip, destinationip",  # type: ignore[arg-type]
                bucket_seconds=BUCKET,
                max_rows=10,
            )
        assert queries == []

    @pytest.mark.parametrize(
        "bad",
        [
            row(key=KEY0 + 3),  # one past the window
            row(key=KEY0 - 1),
            row(key="soon"),
            row(key=None),
            row(events=None),
            row(events=-1),
        ],
    )
    async def test_a_malformed_row_fails_the_whole_search(self, bad: dict[str, Any]) -> None:
        with pytest.raises(ProviderError) as caught:
            await search([row(), bad])
        assert "soon" not in str(caught.value)

    @pytest.mark.parametrize(("returned", "truncated"), [(9, False), (10, False), (11, True)])
    async def test_truncation_is_more_than_max_rows(self, returned: int, truncated: bool) -> None:
        rows = [row(events=100 - i) for i in range(returned)]
        result, aql = await search(rows, max_rows=10)
        assert result.truncated is truncated
        assert len(result.rows) == min(returned, 10)
        assert "LIMIT 11 " in aql

    async def test_a_rejection_surfaces_without_upstream_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "SECRET upstream text"})

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ProviderError) as caught:
            await provider.get_geo_keyed(
                START, END, direction=GeoDirection.SOURCE, bucket_seconds=BUCKET, max_rows=10
            )
        assert "SECRET" not in str(caught.value)

    async def test_mcp_does_not_support_it(self) -> None:
        assert ProviderCapability.AQL_EXECUTION not in QRadarMCPProvider.capabilities
        provider = QRadarMCPProvider(base_url="http://mcp.invalid:1234")
        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_geo_keyed(
                START, END, direction=GeoDirection.SOURCE, bucket_seconds=BUCKET, max_rows=10
            )


class TestParsedRows:
    async def test_a_row_is_parsed_and_the_key_is_carried_raw(self) -> None:
        result, _ = await search([row(events="7")], direction=GeoDirection.DESTINATION)
        assert result.rows[0].key == US and result.rows[0].events == 7
        assert result.rows[0].bucket_start == START
        assert result.direction is GeoDirection.DESTINATION


class TestMock:
    async def get(self, mock: MockQRadarProvider, **over: Any):  # type: ignore[no-untyped-def]
        args: dict[str, Any] = {"direction": GeoDirection.SOURCE, "max_rows": 1000}
        return await mock.get_geo_keyed(
            over.pop("start", START),
            over.pop("end", END),
            bucket_seconds=BUCKET,
            **{**args, **over},
        )

    async def test_it_honours_the_window_and_is_deterministic(self) -> None:
        a = await self.get(MockQRadarProvider(seed=5, now=START))
        b = await self.get(MockQRadarProvider(seed=5, now=START))
        c = await self.get(MockQRadarProvider(seed=6, now=START))
        d = await self.get(
            MockQRadarProvider(seed=5, now=START), direction=GeoDirection.DESTINATION
        )
        assert a.rows and a.rows == b.rows != c.rows != d.rows
        assert all(START <= r.bucket_start < END for r in a.rows)

    async def test_a_split_window_is_the_union_of_its_parts(self) -> None:
        mock = MockQRadarProvider(seed=5, now=START)
        whole = await self.get(mock)
        parts = []
        for index in range(3):
            start = START + timedelta(seconds=index * BUCKET)
            alone = await self.get(mock, start=start, end=start + timedelta(seconds=BUCKET))
            parts.extend(alone.rows)
        key = lambda r: r.model_dump_json()  # noqa: E731
        assert sorted(whole.rows, key=key) == sorted(parts, key=key)

    async def test_its_rows_fold_to_countries_and_an_unresolved_remainder(self) -> None:
        result = await self.get(MockQRadarProvider(seed=5, now=START))
        fact = fold_geo_bucket([r for r in result.rows if r.bucket_start == START])
        assert fact.countries and fact.unresolved > 0 and fact.unparseable == 0

    async def test_it_truncates_naturally_and_keeps_the_top(self) -> None:
        mock = MockQRadarProvider(seed=5, now=START)
        mock.geo_script = lambda direction, key: [
            {"geo": geo_key(f"A{chr(65 + i)}"), "events": 10 + i} for i in range(4)
        ]
        full = await self.get(mock)
        assert len(full.rows) == 12
        cut = await self.get(mock, max_rows=3)
        assert cut.truncated and cut.rows == full.rows[:3]

    async def test_it_records_searches_and_honours_the_hooks(self) -> None:
        mock = MockQRadarProvider(seed=5, now=START)
        mock.geo_fault = lambda search: ProviderError("down")
        with pytest.raises(ProviderError):
            await self.get(mock)
        mock.geo_fault = None
        mock.geo_force_truncated = lambda search: True
        assert (await self.get(mock)).truncated
        assert [s.direction for s in mock.geo_searches] == [GeoDirection.SOURCE] * 2
