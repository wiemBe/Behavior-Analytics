"""Keyed IPS searches: the AQL per dimension, the provider guards, and mock parity."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest

from app.models.enums import IpsDimension as D
from app.providers.base import (
    IPS_DIMENSION_FIELDS,
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
IDS = [412, 413]

NORMALIZED = {
    "SOURCE_IP": ("NORMALIZED", "sourceip"),
    "DESTINATION_IP": ("NORMALIZED", "destinationip"),
    "DESTINATION_PORT": ("NORMALIZED", "destinationport"),
    "PROTOCOL": ("NORMALIZED", "protocolid"),
    "SIGNATURE": ("NORMALIZED", "qid"),
}
WITH_ACTION = {**NORMALIZED, "ACTION": ("CUSTOM_PROPERTY", "IPS_Action")}


def scripted(rows: list[dict[str, Any]]) -> tuple[Callable, list[str]]:
    """An Ariel handler that answers every search with `rows` and records the AQL."""
    queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            queries.append(request.url.params["query_expression"])
            return httpx.Response(201, json={"search_id": "ips-1", "status": "WAIT"})
        if path.endswith("/results"):
            return httpx.Response(200, json={"events": rows})
        if "/ariel/searches/ips-" in path:
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected {request.method} {request.url}")

    return handler, queries


def kwargs(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "qradar_log_source_ids": IDS,
        "fields": NORMALIZED,
        "window_start": START,
        "window_end": END,
        "bucket_seconds": BUCKET,
        "max_rows": 10,
    }
    return {**base, **over}


def row(source: int = 412, key: int = KEY0, **over: Any) -> dict[str, Any]:
    return {"log_source_id": source, "k": key, "events": 5, "records": 2, **over}


async def search(dimension: D, rows: list[dict[str, Any]] | None = None, **over: Any):
    handler, queries = scripted(rows or [])
    provider, _ = build_provider(handler, page_size=50, max_pages=1)
    result = await provider.get_ips_keyed(dimension, **kwargs(**over))
    return result, queries[0]


class TestAql:
    @pytest.mark.parametrize("dimension", list(D))
    async def test_every_search_has_key_scope_limit_and_no_case(self, dimension: D) -> None:
        _, aql = await search(dimension, fields=WITH_ACTION)
        start_ms = int(START.timestamp() * 1000)
        end_ms = int(END.timestamp() * 1000)
        assert f'starttime / {BUCKET * 1000} AS "k"' in aql
        assert f"starttime / {BUCKET * 1000}" in aql.split("GROUP BY")[1]
        assert "logsourceid IN (412, 413)" in aql
        assert f"starttime >= {start_ms} AND starttime < {end_ms}" in aql
        assert aql.endswith(f"START {start_ms} STOP {end_ms}")
        assert "LIMIT 11 " in aql
        assert "CASE" not in aql
        assert " LIKE " not in aql
        assert "FROM events" in aql
        assert "logsourceid" in aql.split("GROUP BY")[1]
        assert "ORDER BY" in aql

    async def test_padded_scope_comes_from_ariel_scope(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(
            handler, page_size=50, max_pages=1, ariel_stop_pad_seconds=60
        )
        provider._clock = lambda: END + timedelta(hours=1)
        await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}))
        end_ms = int(END.timestamp() * 1000)
        assert queries[0].endswith(f"STOP {end_ms + 60_000}")
        assert f"starttime < {end_ms} " in queries[0] + " "

    async def test_totals_counts_every_event_and_has_no_filter(self) -> None:
        _, aql = await search(D.TOTALS, fields={})
        assert 'SUM(eventcount) AS "events", COUNT(*) AS "records"' in aql
        assert "> 0" not in aql and "INCIDR" not in aql
        assert "ORDER BY SUM(eventcount) DESC" in aql

    async def test_ports_filter_the_port_and_group_protocol_without_filtering_it(self) -> None:
        _, aql = await search(D.DESTINATION_PORT)
        assert '"destinationport" > 0' in aql
        assert '"protocolid" > 0' not in aql  # protocol 255 stays a row, folded to unknown
        assert 'GROUP BY logsourceid, "destinationport", "protocolid", starttime /' in aql
        assert "ORDER BY SUM(eventcount) DESC" in aql

    async def test_ports_without_a_protocol_do_not_select_one(self) -> None:
        fields = {k: v for k, v in NORMALIZED.items() if k != "PROTOCOL"}
        _, aql = await search(D.DESTINATION_PORT, fields=fields)
        assert "protocolid" not in aql

    @pytest.mark.parametrize(
        ("dimension", "name"),
        [(D.DESTINATION_IP, "destinationip"), (D.SOURCE_IP, "sourceip")],
    )
    async def test_ip_dimensions_reuse_the_population_helper(
        self, dimension: D, name: str
    ) -> None:
        _, aql = await search(dimension)
        assert f"NOT INCIDR('127.0.0.0/8', \"{name}\")" in aql
        assert f'"{name}" IS NOT NULL' in aql
        assert f"\"{name}\" != '::1'" in aql
        assert " LIKE " not in aql

    async def test_a_normalized_signature_filters_the_qid_and_names_it(self) -> None:
        _, aql = await search(D.SIGNATURE)
        assert '"qid" > 0' in aql
        assert 'QIDNAME("qid") AS "qname"' in aql
        assert 'GROUP BY logsourceid, "qid", starttime /' in aql

    async def test_a_custom_signature_is_quoted_not_named_and_only_checked_for_null(self) -> None:
        fields = {**NORMALIZED, "SIGNATURE": ("CUSTOM_PROPERTY", "IPS Signature")}
        _, aql = await search(D.SIGNATURE, fields=fields)
        assert '"IPS Signature" IS NOT NULL' in aql
        assert "QIDNAME" not in aql
        assert "> 0" not in aql

    async def test_a_custom_property_called_qid_is_not_treated_as_the_qid(self) -> None:
        fields = {**NORMALIZED, "SIGNATURE": ("CUSTOM_PROPERTY", "qid")}
        _, aql = await search(D.SIGNATURE, fields=fields)
        assert '"qid" IS NOT NULL' in aql
        assert "QIDNAME" not in aql

    async def test_fanout_counts_distinct_ports_and_orders_by_them(self) -> None:
        _, aql = await search(D.PORT_FANOUT, rows=[row(sip="203.0.113.5", distinct_ports=3)])
        assert 'UNIQUECOUNT("destinationport") AS "distinct_ports"' in aql
        assert '"destinationport" > 0' in aql
        assert 'GROUP BY logsourceid, "sourceip", starttime /' in aql
        assert 'ORDER BY UNIQUECOUNT("destinationport") DESC' in aql
        assert "INCIDR" in aql

    async def test_action_quotes_a_custom_property_and_filters_null(self) -> None:
        _, aql = await search(D.ACTION, fields=WITH_ACTION)
        assert '"IPS_Action" AS "act"' in aql
        assert '"IPS_Action" IS NOT NULL' in aql
        assert 'GROUP BY logsourceid, "IPS_Action", starttime /' in aql

    async def test_qidname_appears_only_for_a_normalized_qid(self) -> None:
        for dimension in D:
            _, aql = await search(dimension, fields=WITH_ACTION)
            assert ("QIDNAME" in aql) is (dimension is D.SIGNATURE)

    async def test_a_normalized_and_a_custom_reference_are_quoted_alike_but_validated(
        self,
    ) -> None:
        _, aql = await search(D.ACTION, fields={"ACTION": ("NORMALIZED", "category")})
        assert '"category" AS "act"' in aql


class TestGuards:
    async def test_an_unaligned_window_is_refused_before_any_request(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError, match="epoch-aligned"):
            await provider.get_ips_keyed(
                D.TOTALS, **kwargs(window_start=START + timedelta(seconds=7), fields={})
            )
        assert queries == []

    async def test_too_many_sources_are_refused(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(
            handler, page_size=50, max_pages=1, ips_max_sources_per_search=2
        )
        with pytest.raises(ValueError, match="at most 2"):
            await provider.get_ips_keyed(
                D.TOTALS, **kwargs(qradar_log_source_ids=[1, 2, 3], fields={})
            )
        assert queries == []

    @pytest.mark.parametrize("ids", [[], [True], ["412"], [412.0]])
    async def test_ids_must_be_integers_and_present(self, ids: list[Any]) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError):
            await provider.get_ips_keyed(D.TOTALS, **kwargs(qradar_log_source_ids=ids, fields={}))
        assert queries == []

    @pytest.mark.parametrize(
        "reference",
        [
            ("NORMALIZED", "username"),
            ("NORMALIZED", 'x"; DROP'),
            ("CUSTOM_PROPERTY", 'x"; DROP'),
            ("CUSTOM_PROPERTY", ""),
            ("CUSTOM_PROPERTY", "a" * 65),
            ("RAW", "sourceip"),
        ],
    )
    async def test_a_bad_reference_is_refused_without_echoing_it(
        self, reference: tuple[str, str]
    ) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError) as caught:
            await provider.get_ips_keyed(
                D.SOURCE_IP, **kwargs(fields={**NORMALIZED, "SOURCE_IP": reference})
            )
        assert "DROP" not in str(caught.value)
        assert queries == []

    async def test_a_bad_reference_of_an_unused_field_is_refused_too(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError):
            await provider.get_ips_keyed(
                D.SOURCE_IP,
                **kwargs(fields={**NORMALIZED, "SEVERITY": ("NORMALIZED", "username")}),
            )
        assert queries == []

    async def test_an_unknown_logical_field_is_refused(self) -> None:
        provider, _ = build_provider(scripted([])[0], page_size=50, max_pages=1)
        with pytest.raises(ValueError, match="unknown IPS logical field"):
            await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={"HOST": ("NORMALIZED", "qid")}))

    @pytest.mark.parametrize("dimension", [d for d in D if IPS_DIMENSION_FIELDS[d][0]])
    async def test_a_missing_required_reference_is_refused(self, dimension: D) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ValueError, match="needs"):
            await provider.get_ips_keyed(dimension, **kwargs(fields={}))
        assert queries == []

    @pytest.mark.parametrize(
        "bad",
        [
            row(key=KEY0 + 3),  # one past the window
            row(key=KEY0 - 1),
            row(key="soon"),
            row(key=1.5),
            row(key=None),
            row(source=999),
            row(source="x"),
            row(events=None),
            row(events=-1),
        ],
    )
    async def test_a_malformed_row_fails_the_whole_search(self, bad: dict[str, Any]) -> None:
        handler, _ = scripted([row(), bad])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ProviderError) as caught:
            await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}))
        assert "soon" not in str(caught.value)

    async def test_a_key_on_the_window_edges_is_accepted(self) -> None:
        handler, _ = scripted([row(key=KEY0), row(key=KEY0 + 2)])
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        result = await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}))
        assert len(result.rows) == 2

    @pytest.mark.parametrize(("returned", "truncated"), [(9, False), (10, False), (11, True)])
    async def test_truncation_is_more_than_max_rows(self, returned: int, truncated: bool) -> None:
        rows = [row(events=100 - i) for i in range(returned)]
        handler, queries = scripted(rows)
        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        result = await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}, max_rows=10))
        assert result.truncated is truncated
        assert len(result.rows) == min(returned, 10)
        assert [r.events for r in result.rows] == [100 - i for i in range(len(result.rows))]
        assert "LIMIT 11 " in queries[0]

    async def test_max_rows_is_bounded_by_the_results_read(self) -> None:
        handler, queries = scripted([])
        provider, _ = build_provider(handler, page_size=5, max_pages=2)
        await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}, max_rows=5000))
        assert "LIMIT 11 " in queries[0]

    async def test_a_rejection_surfaces_as_the_provider_error_subclass(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, json={"message": "SECRET upstream text"})

        provider, _ = build_provider(handler, page_size=50, max_pages=1)
        with pytest.raises(ProviderError) as caught:
            await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}))
        assert "SECRET" not in str(caught.value)

    async def test_mcp_does_not_support_it(self) -> None:
        assert ProviderCapability.AQL_EXECUTION not in QRadarMCPProvider.capabilities
        provider = QRadarMCPProvider(base_url="http://mcp.invalid:1234")
        with pytest.raises(CapabilityNotSupportedError):
            await provider.get_ips_keyed(D.TOTALS, **kwargs(fields={}))


class TestParsedRows:
    async def test_port_rows_carry_port_and_protocol(self) -> None:
        result, _ = await search(
            D.DESTINATION_PORT, [row(dport=443, proto=6, events="7", records=3)]
        )
        assert result.rows[0].destination_port == 443
        assert result.rows[0].protocol_id == 6
        assert result.rows[0].events == 7
        assert result.rows[0].records == 3
        assert result.rows[0].bucket_start == START
        assert result.truncated is False
        assert result.qradar_log_source_ids == (412, 413)

    async def test_a_normalized_signature_row_has_qid_and_name(self) -> None:
        result, _ = await search(D.SIGNATURE, [row(qid=5000123, qname="Probe")])
        assert (result.rows[0].qid, result.rows[0].name) == (5000123, "Probe")

    async def test_a_custom_signature_row_has_the_value(self) -> None:
        fields = {**NORMALIZED, "SIGNATURE": ("CUSTOM_PROPERTY", "Sig")}
        result, _ = await search(D.SIGNATURE, [row(sig="ET SCAN")], fields=fields)
        assert result.rows[0].signature == "ET SCAN" and result.rows[0].qid is None

    async def test_fanout_needs_a_distinct_count(self) -> None:
        with pytest.raises(ProviderError):
            await search(D.PORT_FANOUT, [row(sip="203.0.113.5")])

    async def test_ids_are_sorted_and_deduplicated(self) -> None:
        result, aql = await search(D.TOTALS, fields={}, qradar_log_source_ids=[413, 412, 413])
        assert result.qradar_log_source_ids == (412, 413)
        assert "IN (412, 413)" in aql


FIELDS_FOR = {
    D.TOTALS: {},
    D.DESTINATION_PORT: NORMALIZED,
    D.DESTINATION_IP: NORMALIZED,
    D.SIGNATURE: NORMALIZED,
    D.SOURCE_IP: NORMALIZED,
    D.PORT_FANOUT: NORMALIZED,
    D.ACTION: WITH_ACTION,
}


class TestMock:
    @pytest.mark.parametrize("dimension", list(D))
    async def test_a_multi_source_window_is_the_union_of_its_cells(self, dimension: D) -> None:
        mock = MockQRadarProvider(now=START)
        whole = await mock.get_ips_keyed(
            dimension, **kwargs(fields=FIELDS_FOR[dimension], max_rows=100_000)
        )
        assert not whole.truncated
        parts = []
        for source in IDS:
            for index in range(3):
                start = START + timedelta(seconds=index * BUCKET)
                alone = await mock.get_ips_keyed(
                    dimension,
                    **kwargs(
                        qradar_log_source_ids=[source],
                        fields=FIELDS_FOR[dimension],
                        window_start=start,
                        window_end=start + timedelta(seconds=BUCKET),
                        max_rows=100_000,
                    ),
                )
                parts.extend(alone.rows)
        key = lambda r: r.model_dump_json()  # noqa: E731
        assert sorted(whole.rows, key=key) == sorted(parts, key=key)
        assert whole.rows, "the mock produced nothing to compare"

    async def test_it_honours_window_and_sources(self) -> None:
        mock = MockQRadarProvider(now=START)
        result = await mock.get_ips_keyed(
            D.TOTALS, **kwargs(qradar_log_source_ids=[412], fields={}, max_rows=1000)
        )
        assert {r.qradar_log_source_id for r in result.rows} == {412}
        assert all(START <= r.bucket_start < END for r in result.rows)

    async def test_it_is_deterministic(self) -> None:
        a = await MockQRadarProvider(seed=5).get_ips_keyed(D.SOURCE_IP, **kwargs(max_rows=1000))
        b = await MockQRadarProvider(seed=5).get_ips_keyed(D.SOURCE_IP, **kwargs(max_rows=1000))
        c = await MockQRadarProvider(seed=6).get_ips_keyed(D.SOURCE_IP, **kwargs(max_rows=1000))
        assert a.rows == b.rows != c.rows

    async def test_the_filters_match_the_rest_semantics(self) -> None:
        mock = MockQRadarProvider(now=START)
        ports = await mock.get_ips_keyed(D.DESTINATION_PORT, **kwargs(max_rows=100_000))
        assert ports.rows and all((r.destination_port or 0) > 0 for r in ports.rows)
        sources = await mock.get_ips_keyed(D.SOURCE_IP, **kwargs(max_rows=100_000))
        assert all(r.source_ip != "127.0.0.1" for r in sources.rows)
        totals = await mock.get_ips_keyed(D.TOTALS, **kwargs(fields={}, max_rows=100_000))
        assert sum(r.events for r in totals.rows) >= sum(r.events for r in ports.rows)

    @pytest.mark.parametrize("dimension", list(D))
    async def test_it_truncates_naturally_and_keeps_the_top(self, dimension: D) -> None:
        mock = MockQRadarProvider(now=START)
        full = await mock.get_ips_keyed(
            dimension, **kwargs(fields=FIELDS_FOR[dimension], max_rows=100_000)
        )
        assert len(full.rows) > 3
        cut = await mock.get_ips_keyed(
            dimension, **kwargs(fields=FIELDS_FOR[dimension], max_rows=3)
        )
        assert cut.truncated and cut.rows == full.rows[:3]
        exact = await mock.get_ips_keyed(
            dimension, **kwargs(fields=FIELDS_FOR[dimension], max_rows=len(full.rows))
        )
        assert not exact.truncated

    async def test_it_applies_the_same_guards(self) -> None:
        mock = MockQRadarProvider(now=START, ips_max_sources_per_search=1)
        with pytest.raises(ValueError, match="at most 1"):
            await mock.get_ips_keyed(D.TOTALS, **kwargs(fields={}))
        with pytest.raises(ValueError, match="epoch-aligned"):
            await mock.get_ips_keyed(
                D.TOTALS,
                **kwargs(
                    qradar_log_source_ids=[1], fields={}, window_end=END + timedelta(seconds=1)
                ),
            )
        with pytest.raises(ValueError):
            await mock.get_ips_keyed(
                D.SOURCE_IP,
                **kwargs(qradar_log_source_ids=[1], fields={"SOURCE_IP": ("NORMALIZED", "x")}),
            )

    async def test_the_test_hooks_force_a_fault_and_a_truncation(self) -> None:
        mock = MockQRadarProvider(now=START)
        mock.ips_fault = lambda s: ProviderError("boom") if s.dimension is D.TOTALS else None
        with pytest.raises(ProviderError):
            await mock.get_ips_keyed(D.TOTALS, **kwargs(fields={}))
        mock.ips_force_truncated = lambda s: True
        assert (await mock.get_ips_keyed(D.SOURCE_IP, **kwargs())).truncated
        assert [s.dimension for s in mock.ips_searches] == [D.TOTALS, D.SOURCE_IP]
