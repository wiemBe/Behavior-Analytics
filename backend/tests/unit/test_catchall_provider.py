"""Catch-all senders search: the AQL, the guards, the mock's parity and MCP."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.catchall.keyed_rows import validate_catchall_type_ids
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderCapability,
    ProviderError,
)
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from tests.unit.test_qradar_rest_provider import build_provider

BUCKET = 300
START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
END = START + timedelta(seconds=3 * BUCKET)
KEY0 = int(START.timestamp()) // BUCKET
TYPES = [67, 39, 212, 367, 4000]


def scripted(rows: list[dict[str, Any]]) -> tuple[Callable, list[str]]:
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            queries.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "ca-1", "status": "WAIT"})
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": rows})
        if "/ariel/searches/ca-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, queries


def kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "devicetype_ids": TYPES,
        "window_start": START,
        "window_end": END,
        "bucket_seconds": BUCKET,
        "max_rows": 10,
    }
    return {**base, **over}


def row(source: int = 1001, key: int = KEY0, **over: Any) -> dict[str, Any]:
    return {
        "logsourceid": source,
        "sender": "fw-01",
        "k": key,
        "events": 5,
        "records": 2,
        **over,
    }


async def search(rows: list[dict[str, Any]] | None = None, **over: Any):
    handler, queries = scripted(rows or [])
    provider, _ = build_provider(handler, page_size=50, max_pages=1)
    result = await provider.get_catchall_senders_keyed(**kwargs(**over))
    return result, queries[0]


class TestAql:
    async def test_the_query_is_the_c8_shape(self) -> None:
        _, aql = await search()
        start_ms = int(START.timestamp() * 1000)
        end_ms = int(END.timestamp() * 1000)
        assert aql.startswith(
            'SELECT logsourceid, logsourceidentifier AS "sender", '  # noqa: S608
            f'starttime / {BUCKET * 1000} AS "k", '
            'SUM(eventcount) AS "events", COUNT(*) AS "records" FROM events '
            "WHERE devicetype IN (39, 67, 212, 367, 4000) "
        )
        assert f"starttime >= {start_ms} AND starttime < {end_ms}" in aql
        assert (
            f"GROUP BY logsourceid, logsourceidentifier, starttime / {BUCKET * 1000}" in aql
        )
        assert "ORDER BY SUM(eventcount) DESC LIMIT 11 " in aql

    async def test_the_scope_is_padded_by_ariel_scope(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(
            handler, page_size=50, max_pages=1, ariel_stop_pad_seconds=60
        )
        provider._clock = lambda: END + timedelta(hours=1)
        await provider.get_catchall_senders_keyed(**kwargs())
        end_ms = int(END.timestamp() * 1000)
        assert queries[0].endswith(f"STOP {end_ms + 60_000}")
        assert f"starttime < {end_ms} " in queries[0]

    async def test_the_sender_is_selected_never_filtered(self) -> None:
        _, aql = await search()
        assert " LIKE " not in aql and "CASE" not in aql
        assert aql.count("logsourceidentifier") == 2  # select and group by only
        assert '"Log Source Identifier"' not in aql

    async def test_max_rows_is_bounded_by_the_results_read(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=5, max_pages=2)
        await provider.get_catchall_senders_keyed(**kwargs(max_rows=5000))
        assert "LIMIT 11 " in queries[0]


class TestGuards:
    async def test_an_unaligned_window_is_refused_before_any_request(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError, match="epoch-aligned"):
            await provider.get_catchall_senders_keyed(
                **kwargs(window_start=START + timedelta(seconds=7))
            )
        assert queries == []

    @pytest.mark.parametrize(
        "ids",
        [
            [],
            [True],
            ["67"],
            [67.0],
            [0],
            [-3],
            [100_001],
            [67, "4000) OR (1=1"],
            list(range(1, 34)),
        ],
    )
    async def test_type_ids_must_be_bounded_integers(self, ids: list[Any]) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError) as caught:
            await provider.get_catchall_senders_keyed(**kwargs(devicetype_ids=ids))
        assert "OR (1=1" not in str(caught.value)
        assert queries == []

    def test_type_ids_are_sorted_and_deduplicated_and_32_is_allowed(self) -> None:
        assert validate_catchall_type_ids([4000, 67, 67]) == (67, 4000)
        assert len(validate_catchall_type_ids(range(1, 33))) == 32
        assert validate_catchall_type_ids([1, 100_000]) == (1, 100_000)

    @pytest.mark.parametrize(
        "bad",
        [
            row(key=KEY0 + 3),  # one past the window
            row(key=KEY0 - 1),
            row(key="soon"),
            row(key=1.5),
            row(key=None),
            row(source=None),
            row(source="x"),
            row(events=None),
            row(events=-1),
            row(records=None),
            row(records=-1),
        ],
    )
    async def test_a_malformed_row_fails_the_whole_search(self, bad: dict[str, Any]) -> None:
        handler, _ = scripted([row(), bad])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ProviderError) as caught:
            await provider.get_catchall_senders_keyed(**kwargs())
        assert "soon" not in str(caught.value)

    async def test_a_key_on_the_window_edges_is_accepted(self) -> None:
        handler, _ = scripted([row(key=KEY0), row(key=KEY0 + 2)])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        result = await provider.get_catchall_senders_keyed(**kwargs())
        assert len(result.rows) == 2

    @pytest.mark.parametrize(("returned", "truncated"), [(9, False), (10, False), (11, True)])
    async def test_truncation_is_more_than_max_rows(self, returned: int, truncated: bool) -> None:
        rows = [row(events=100 - i) for i in range(returned)]
        result, aql = await search(rows, max_rows=10)
        assert result.truncated is truncated
        assert len(result.rows) == min(returned, 10)
        assert "LIMIT 11 " in aql

    async def test_a_rejection_surfaces_as_a_provider_error_without_upstream_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "SECRET upstream text"})

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ProviderError) as caught:
            await provider.get_catchall_senders_keyed(**kwargs())
        assert "SECRET" not in str(caught.value)

    async def test_mcp_does_not_support_it(self) -> None:
        assert ProviderCapability.AQL_EXECUTION not in QRadarMCPProvider.capabilities
        provider = QRadarMCPProvider(base_url="http://mcp.invalid:1234")
        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_catchall_senders_keyed(**kwargs())


class TestParsedRows:
    async def test_a_row_is_parsed(self) -> None:
        result, _ = await search([row(source="1001", events="7", records=3, sender="a b")])
        assert result.rows[0].qradar_log_source_id == 1001
        assert result.rows[0].sender == "a b"
        assert (result.rows[0].events, result.rows[0].records) == (7, 3)
        assert result.rows[0].bucket_start == START
        assert result.device_type_ids == (39, 67, 212, 367, 4000)

    async def test_a_missing_sender_is_none_not_an_error(self) -> None:
        result, _ = await search([row(sender=None)])
        assert result.rows[0].sender is None


class TestMock:
    async def test_it_honours_the_window_and_is_deterministic(self) -> None:
        a = await MockQRadarProvider(seed=5, now=START).get_catchall_senders_keyed(
            **kwargs(max_rows=1000)
        )
        b = await MockQRadarProvider(seed=5, now=START).get_catchall_senders_keyed(
            **kwargs(max_rows=1000)
        )
        c = await MockQRadarProvider(seed=6, now=START).get_catchall_senders_keyed(
            **kwargs(max_rows=1000)
        )
        assert a.rows and a.rows == b.rows != c.rows
        assert all(START <= r.bucket_start < END for r in a.rows)

    async def test_a_split_window_is_the_union_of_its_parts(self) -> None:
        mock = MockQRadarProvider(seed=5, now=START)
        whole = await mock.get_catchall_senders_keyed(**kwargs(max_rows=100_000))
        parts = []
        for index in range(3):
            start = START + timedelta(seconds=index * BUCKET)
            alone = await mock.get_catchall_senders_keyed(
                **kwargs(
                    window_start=start,
                    window_end=start + timedelta(seconds=BUCKET),
                    max_rows=100_000,
                )
            )
            parts.extend(alone.rows)
        key = lambda r: r.model_dump_json()  # noqa: E731
        assert sorted(whole.rows, key=key) == sorted(parts, key=key)

    async def test_it_truncates_naturally_and_keeps_the_top(self) -> None:
        mock = MockQRadarProvider(seed=5, now=START)
        mock.catchall_script = lambda key: [
            {"logsourceid": 1001, "sender": f"h{i}", "events": 10 + i, "records": 1}
            for i in range(4)
        ]
        full = await mock.get_catchall_senders_keyed(**kwargs(max_rows=100_000))
        assert len(full.rows) == 12
        cut = await mock.get_catchall_senders_keyed(**kwargs(max_rows=3))
        assert cut.truncated and cut.rows == full.rows[:3]
        exact = await mock.get_catchall_senders_keyed(**kwargs(max_rows=len(full.rows)))
        assert not exact.truncated

    async def test_it_applies_the_same_guards(self) -> None:
        mock = MockQRadarProvider(now=START)
        with pytest.raises(ValueError, match="epoch-aligned"):
            await mock.get_catchall_senders_keyed(
                **kwargs(window_end=END + timedelta(seconds=1))
            )
        with pytest.raises(ValueError):
            await mock.get_catchall_senders_keyed(**kwargs(devicetype_ids=["67"]))
        with pytest.raises(ValueError):
            await mock.get_catchall_senders_keyed(**kwargs(devicetype_ids=[]))
        with pytest.raises(ValueError):
            await mock.get_catchall_senders_keyed(**kwargs(max_rows=0))

    async def test_the_mock_rows_pass_the_real_row_parser(self) -> None:
        mock = MockQRadarProvider(now=START)
        mock.catchall_script = lambda key: [
            {"logsourceid": 1, "sender": "x", "events": 1, "records": 1}
        ]
        # a row whose key is inside the window parses; the parser is the real one
        result = await mock.get_catchall_senders_keyed(**kwargs())
        assert len(result.rows) == 3

    async def test_the_test_hooks_force_a_fault_and_a_truncation(self) -> None:
        mock = MockQRadarProvider(now=START)
        mock.catchall_fault = lambda s: ProviderError("boom")
        with pytest.raises(ProviderError):
            await mock.get_catchall_senders_keyed(**kwargs())
        mock.catchall_fault = None
        mock.catchall_force_truncated = lambda s: True
        assert (await mock.get_catchall_senders_keyed(**kwargs())).truncated
        assert len(mock.catchall_searches) == 2
