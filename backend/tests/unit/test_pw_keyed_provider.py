"""Keyed property-watch searches: the REST query and guards, the MCP refusal, mock parity."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.models.enums import PwScopeKind as K
from app.property_watch.fold import fold_bucket
from app.property_watch.keyed_rows import parse_pw_keyed_rows
from app.property_watch.keyed_search import PwSearchBudget, PwSearchPlan, run_search
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderCapability,
    ProviderError,
)
from app.providers.dto import PwSearchKind as S
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_mcp import QRadarMCPProvider
from tests.unit.test_qradar_rest_provider import build_provider

BUCKET = 300
START = datetime(2026, 10, 13, 9, 0, tzinfo=UTC)
END = START + timedelta(seconds=3 * BUCKET)
KEY0 = int(START.timestamp()) // BUCKET
USER = ("NORMALIZED", "username")
ACTION = ("CUSTOM_PROPERTY", "Action")


def scripted(rows: list[dict[str, Any]]) -> tuple[Callable, list[str]]:
    """An Ariel handler that answers every search with `rows` and records the AQL."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            queries.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "pw-1", "status": "WAIT"})
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": rows})
        if "/ariel/searches/pw-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, queries


def kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "scope_kind": K.LOG_SOURCE,
        "scope_ids": [412],
        "properties": [USER],
        "window_start": START,
        "window_end": END,
        "bucket_seconds": BUCKET,
        "max_rows": 10,
    }
    return {**base, **over}


def row(key: int = KEY0, **over: Any) -> dict[str, Any]:
    return {"p0": "alice", "k": key, "events": 5, **over}


async def search(
    kind: S, rows: list[dict[str, Any]] | None = None, **over: Any
):
    handler, queries = scripted(rows or [])
    provider, _ = build_provider(handler, page_size=50, max_pages=1)
    result = await provider.get_pw_keyed(kind, **kwargs(**over))
    return result, queries[0]


class TestAql:
    async def test_grouped_has_scope_limit_and_the_padded_start_stop(self) -> None:
        result, aql = await search(S.GROUPED)
        start_ms, end_ms = int(START.timestamp() * 1000), int(END.timestamp() * 1000)
        assert aql == (
            'SELECT "username" AS "p0", starttime / 300000 AS "k", '  # noqa: S608
            'SUM(eventcount) AS "events" FROM events '
            "WHERE logsourceid IN (412) AND starttime >= "
            + str(start_ms)
            + " AND starttime < "
            + str(end_ms)
            + ' AND "username" IS NOT NULL GROUP BY "username", starttime / 300000 '
            + "ORDER BY SUM(eventcount) DESC LIMIT 11 START "
            + str(start_ms)
            + " STOP "
            + str(end_ms)
        )
        assert result.query == aql

    async def test_totals_is_one_row_per_bucket_without_a_property(self) -> None:
        _, aql = await search(S.TOTALS, properties=[], max_rows=3)
        assert aql.startswith('SELECT starttime / 300000 AS "k", SUM(eventcount) AS "events"')
        assert "IS NOT NULL" not in aql
        assert "LIMIT 4 " in aql

    async def test_pinned_uses_in_never_or(self) -> None:
        _, aql = await search(
            S.PINNED,
            properties=[USER, ACTION],
            pinned=[("alice", "login"), ("bob", "login")],
        )
        assert "\"username\" IN ('alice','bob') AND \"Action\" IN ('login')" in aql
        assert " OR " not in aql

    async def test_a_type_and_a_group_scope(self) -> None:
        _, type_aql = await search(S.TOTALS, scope_kind=K.LOG_SOURCE_TYPE, scope_ids=[11])
        assert "WHERE devicetype = 11 AND" in type_aql
        _, group_aql = await search(S.TOTALS, scope_kind=K.LOG_SOURCE_GROUP, scope_ids=[9, 3])
        assert "WHERE logsourceid IN (3, 9) AND" in group_aql

    async def test_max_rows_is_bounded_by_the_results_read(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=5, max_pages=2)
        await provider.get_pw_keyed(S.GROUPED, **kwargs(max_rows=5000))
        assert "LIMIT 11 " in queries[0]


class TestGuards:
    async def test_an_unaligned_window_is_refused_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be sent")

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError, match="epoch-aligned"):
            await provider.get_pw_keyed(
                S.GROUPED, **kwargs(window_end=END + timedelta(seconds=1))
            )

    @pytest.mark.parametrize(
        "over",
        [
            {"properties": [("CUSTOM_PROPERTY", 'x" OR 1=1 --')]},
            {"properties": [("NORMALIZED", "sourceip")]},
            {"properties": [USER, USER]},
            {"scope_ids": []},
            {"scope_ids": [1, 2]},
            {"scope_ids": ["1 OR 1=1"]},
        ],
    )
    async def test_bad_inputs_are_refused_before_any_request(self, over: dict) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be sent")

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError) as caught:
            await provider.get_pw_keyed(S.GROUPED, **kwargs(**over))
        assert "OR 1=1" not in str(caught.value)

    async def test_a_bad_pin_is_refused_before_any_request(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("no request may be sent")

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError):
            await provider.get_pw_keyed(S.PINNED, **kwargs(pinned=[("o'brien",)]))

    async def test_the_group_chunk_is_capped_by_the_provider(self) -> None:
        handler, _ = scripted([])
        provider, _ = build_provider(
            handler, page_size=50, max_pages=1, pw_max_sources_per_search=2
        )
        with pytest.raises(ValueError, match="at most 2"):
            await provider.get_pw_keyed(
                S.TOTALS, **kwargs(scope_kind=K.LOG_SOURCE_GROUP, scope_ids=[1, 2, 3])
            )

    async def test_a_rejection_surfaces_as_the_provider_error_without_its_text(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "SECRET upstream text"})

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ProviderError) as caught:
            await provider.get_pw_keyed(S.GROUPED, **kwargs())
        assert "SECRET" not in str(caught.value)

    async def test_mcp_does_not_support_it(self) -> None:
        assert ProviderCapability.AQL_EXECUTION not in QRadarMCPProvider.capabilities
        provider = QRadarMCPProvider(base_url="http://mcp.invalid:1234")
        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_pw_keyed(S.GROUPED, **kwargs())


class TestParsedRows:
    async def test_a_row_carries_its_bucket_values_and_count(self) -> None:
        result, _ = await search(S.GROUPED, [row(events=7)])
        assert result.rows[0].values == ("alice",)
        assert result.rows[0].events == 7
        assert result.rows[0].bucket_start == START
        assert result.truncated is False

    async def test_values_stay_raw(self) -> None:
        result, _ = await search(
            S.GROUPED, [row(p0=" Al\x00ice "), row(p0=None), row(p0=""), row(p0=True)]
        )
        assert [r.values for r in result.rows] == [(" Al\x00ice ",), (None,), ("",), (None,)]

    async def test_an_integer_valued_float_count_is_accepted(self) -> None:
        result, _ = await search(S.GROUPED, [row(events=12.0, k=float(KEY0))])
        assert result.rows[0].events == 12

    @pytest.mark.parametrize("count", [12.5, -1, "12", True, None])
    async def test_a_malformed_count_fails_the_whole_search(self, count: Any) -> None:
        with pytest.raises(ProviderError, match="count"):
            await search(S.GROUPED, [row(), row(events=count)])

    @pytest.mark.parametrize("count", [float("nan"), float("inf"), float("-inf")])
    def test_a_non_finite_count_fails_the_parse(self, count: float) -> None:
        # JSON cannot carry these, so the REST path cannot see them; the parser still refuses.
        with pytest.raises(ProviderError, match="count"):
            parse_pw_keyed_rows(
                [row(events=count)],
                property_count=1,
                window_start=START,
                window_end=END,
                bucket_seconds=BUCKET,
                max_rows=10,
            )

    @pytest.mark.parametrize("key", [KEY0 - 1, KEY0 + 3, 1.5, "x", None, True])
    async def test_a_key_outside_the_span_fails_the_whole_search(self, key: Any) -> None:
        with pytest.raises(ProviderError, match="bucket key"):
            await search(S.GROUPED, [row(), row(k=key)])

    async def test_a_key_on_the_window_edges_is_accepted(self) -> None:
        result, _ = await search(S.GROUPED, [row(KEY0), row(KEY0 + 2)])
        assert len(result.rows) == 2

    @pytest.mark.parametrize(("returned", "truncated"), [(9, False), (10, False), (11, True)])
    async def test_truncation_is_more_than_max_rows(self, returned: int, truncated: bool) -> None:
        result, _ = await search(S.GROUPED, [row(p0=f"u{i}") for i in range(returned)])
        assert result.truncated is truncated
        assert len(result.rows) == min(returned, 10)

    async def test_totals_rows_carry_no_values(self) -> None:
        result, _ = await search(S.TOTALS, [{"k": KEY0, "events": 40}], properties=[])
        assert result.rows[0].values == ()


FIELDS = {
    S.GROUPED: {"properties": [USER, ACTION]},
    S.TOTALS: {"properties": []},
    S.PINNED: {
        "properties": [USER, ACTION],
        "pinned": [("Username-v1", "Action-v2")],
    },
}


class TestMock:
    @pytest.mark.parametrize("kind", list(S))
    async def test_it_returns_rows_the_real_parser_accepts(self, kind: S) -> None:
        mock = MockQRadarProvider(now=START)
        if kind is S.PINNED:
            # The mock names values `<property>-v<n>`.
            fields = {**FIELDS[kind], "pinned": [("username-v1", "Action-v2")]}
        else:
            fields = FIELDS[kind]
        result = await mock.get_pw_keyed(kind, **kwargs(**fields, max_rows=100_000))
        assert not result.truncated
        assert all(START <= r.bucket_start < END for r in result.rows)
        assert all(len(r.values) == len(fields["properties"]) * (kind is not S.TOTALS)
                   for r in result.rows)

    async def test_grouped_rows_are_never_null_and_may_be_empty_strings(self) -> None:
        mock = MockQRadarProvider(now=START)
        result = await mock.get_pw_keyed(
            S.GROUPED, **kwargs(window_end=START + timedelta(hours=1), max_rows=100_000)
        )
        values = [v for r in result.rows for v in r.values]
        assert values and None not in values

    async def test_it_is_deterministic(self) -> None:
        a = await MockQRadarProvider(seed=5).get_pw_keyed(S.GROUPED, **kwargs(max_rows=1000))
        b = await MockQRadarProvider(seed=5).get_pw_keyed(S.GROUPED, **kwargs(max_rows=1000))
        c = await MockQRadarProvider(seed=6).get_pw_keyed(S.GROUPED, **kwargs(max_rows=1000))
        assert a.rows == b.rows != c.rows

    async def test_totals_cover_the_grouped_events(self) -> None:
        mock = MockQRadarProvider(now=START)
        grouped = await mock.get_pw_keyed(S.GROUPED, **kwargs(max_rows=100_000))
        totals = await mock.get_pw_keyed(S.TOTALS, **kwargs(properties=[], max_rows=100_000))
        assert sum(r.events for r in totals.rows) > sum(r.events for r in grouped.rows) > 0

    async def test_it_truncates_naturally_and_keeps_the_top(self) -> None:
        mock = MockQRadarProvider(now=START)
        full = await mock.get_pw_keyed(S.GROUPED, **kwargs(max_rows=100_000))
        assert len(full.rows) > 3
        cut = await mock.get_pw_keyed(S.GROUPED, **kwargs(max_rows=3))
        assert cut.truncated and cut.rows == full.rows[:3]
        exact = await mock.get_pw_keyed(S.GROUPED, **kwargs(max_rows=len(full.rows)))
        assert not exact.truncated

    async def test_pinned_rows_are_only_the_pinned_values(self) -> None:
        mock = MockQRadarProvider(now=START)
        result = await mock.get_pw_keyed(
            S.PINNED, **kwargs(pinned=[("username-v1",), ("username-v2",)], max_rows=1000)
        )
        assert result.rows and {r.values for r in result.rows} <= {
            ("username-v1",),
            ("username-v2",),
        }

    async def test_it_applies_the_same_guards(self) -> None:
        mock = MockQRadarProvider(now=START, pw_max_sources_per_search=1)
        with pytest.raises(ValueError, match="at most 1"):
            await mock.get_pw_keyed(
                S.TOTALS, **kwargs(scope_kind=K.LOG_SOURCE_GROUP, scope_ids=[1, 2])
            )
        with pytest.raises(ValueError, match="epoch-aligned"):
            await mock.get_pw_keyed(S.GROUPED, **kwargs(window_end=END + timedelta(seconds=1)))
        with pytest.raises(ValueError):
            await mock.get_pw_keyed(S.GROUPED, **kwargs(properties=[("NORMALIZED", "x")]))
        with pytest.raises(ValueError):
            await mock.get_pw_keyed(S.PINNED, **kwargs(pinned=[("a'b",)]))

    async def test_the_test_hooks_force_a_fault_and_a_truncation(self) -> None:
        mock = MockQRadarProvider(now=START)
        mock.pw_fault = lambda s: ProviderError("boom") if s.search is S.TOTALS else None
        with pytest.raises(ProviderError):
            await mock.get_pw_keyed(S.TOTALS, **kwargs(properties=[]))
        mock.pw_force_truncated = lambda s: True
        assert (await mock.get_pw_keyed(S.GROUPED, **kwargs())).truncated
        assert [s.search for s in mock.pw_searches] == [S.TOTALS, S.GROUPED]

    async def test_the_real_driver_and_fold_run_on_the_mock(self) -> None:
        """Mock -> split driver -> fold: the lab profile exercises the real code (§G.15)."""
        mock = MockQRadarProvider(now=START)
        budget = PwSearchBudget(20)
        window = (START, START + timedelta(hours=1))

        async def run(kind: S, properties: tuple, pinned: tuple = ()) -> list:
            return await run_search(
                mock,
                PwSearchPlan(kind, K.LOG_SOURCE, (412,), properties, pinned),
                window,
                BUCKET,
                max_rows=5000,
                max_sources=50,
                budget=budget,
            )

        grouped = await run(S.GROUPED, (USER,))
        totals = await run(S.TOTALS, ())
        pinned = await run(S.PINNED, (USER,), (("username-v1",),))
        facts = [
            fold_bucket(
                bucket_start=g.bucket_start,
                bucket_seconds=BUCKET,
                watch_version=1,
                property_count=1,
                grouped=g,
                totals=t,
                pinned_outcome=p,
                pinned=[("username-v1",)],
                max_values=50,
                value_max_length=255,
            )
            for g, t, p in zip(grouped, totals, pinned, strict=True)
        ]
        assert len(facts) == 12 and budget.used == 3
        assert any(f.values for f in facts)
        assert all(
            f.total_events is not None and f.parsed_events is not None
            and f.parsed_events <= f.total_events
            for f in facts
        )
