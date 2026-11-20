"""Filtered JDBC paging without widening the fleet inventory or retaining raw pages."""

from __future__ import annotations

import json
import logging
import weakref
from collections.abc import Callable

import httpx
import pytest

from app.jdbc.dto import ConfigState, JdbcConfigWalk
from app.providers.qradar_rest import LOG_SOURCE_LIST_FIELDS, QRadarRestProvider

PATH = "/api/config/event_sources/log_source_management/log_sources"
FIELDS = "id,protocol_type_id,protocol_parameters"
CANARY = "DENIED-JDBC-PASSWORD-CANARY"


def provider(
    handler: Callable[[httpx.Request], httpx.Response], *, max_pages: int = 200,
    page_size: int = 7,
) -> QRadarRestProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api"
    )
    return QRadarRestProvider(
        base_url="https://qradar.example", sec_token="t", client=client,
        page_size=page_size, max_pages=max_pages, max_retries=0,
    )


def row(index: int) -> dict:
    return {
        "id": index,
        "protocol_type_id": 8,
        "protocol_parameters": [
            {"id": 8020, "name": "databaseServerHostname", "value": "db.example"},
            {"id": 9999, "name": "password", "value": CANARY},
        ],
    }


def page(request: httpx.Request, total: int) -> list[dict]:
    start, end = map(int, request.headers["Range"].removeprefix("items=").split("-"))
    return [row(index) for index in range(start, min(end + 1, total))]


@pytest.mark.asyncio
async def test_filter_fields_sort_range_100_every_page() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=page(request, 237))

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert isinstance(result, JdbcConfigWalk)
    assert (result.complete, result.pages_read, result.rows_read) == (True, 3, 237)
    assert [config.qradar_id for config in result.configs] == list(range(237))
    assert all(config.database_host == "db.example" for config in result.configs)
    assert len(seen) == 3
    for index, request in enumerate(seen):
        assert request.method == "GET" and request.url.path == PATH
        assert list(request.url.params.multi_items()) == [
            ("fields", FIELDS), ("sort", "+id"), ("filter", "protocol_type_id=8"),
        ]
        assert request.headers["Range"] == f"items={index * 100}-{index * 100 + 99}"


@pytest.mark.asyncio
@pytest.mark.parametrize(("total", "gets"), [(0, 1), (37, 1), (100, 2), (200, 3)])
async def test_empty_short_and_exact_multiple_terminators(total: int, gets: int) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Range"])
        return httpx.Response(200, json=page(request, total))

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.complete
    assert (result.pages_read, result.rows_read, len(result.configs)) == (gets, total, total)
    assert len(seen) == gets


@pytest.mark.asyncio
async def test_full_page_at_ceiling_is_not_a_terminator() -> None:
    walker = provider(lambda request: httpx.Response(200, json=page(request, 100)), max_pages=1)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert not result.complete
    assert (result.pages_read, result.rows_read, len(result.configs)) == (1, 100, 100)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [401, 403, 429, 500, "timeout", "json", "object", "none"])
async def test_failed_page_ends_incomplete_without_losing_safe_pages(failure: int | str) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Range"])
        if len(seen) == 1:
            return httpx.Response(200, json=page(request, 200))
        if failure == "timeout":
            raise httpx.ReadTimeout(CANARY, request=request)
        if failure == "json":
            return httpx.Response(200, content=b"invalid-json")
        if failure == "object":
            return httpx.Response(200, json={"message": CANARY})
        if failure == "none":
            return httpx.Response(204)
        assert isinstance(failure, int)
        return httpx.Response(failure, json={"message": CANARY})

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert not result.complete
    assert (result.pages_read, result.rows_read, len(result.configs)) == (1, 100, 100)
    assert len(seen) == 2
    assert CANARY not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_row", [CANARY, {"id": True, "protocol_type_id": 8},
                                     {"id": "1", "protocol_type_id": 8},
                                     {"id": 1}, {"id": 1, "protocol_type_id": "8"}])
async def test_malformed_row_ends_incomplete_and_drops_its_page(bad_row: object) -> None:
    walker = provider(lambda request: httpx.Response(200, json=[row(0), bad_row]))
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert not result.complete
    assert (result.pages_read, result.rows_read, result.configs) == (1, 2, [])


@pytest.mark.asyncio
async def test_8k_fleet_projection_stays_slim() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        fields = request.url.params["fields"].split(",")
        rows = [{**item, "name": f"source-{item['id']}", "type_id": 1}
                for item in page(request, 8_000)]
        return httpx.Response(200, json=[{k: v for k, v in item.items() if k in fields}
                                       for item in rows])

    walker = provider(handler, page_size=500)
    try:
        result = await walker.walk_log_sources()
    finally:
        await walker.aclose()
    assert result.complete and len(result.sources) == 8_000
    assert result.pages_done == len(seen) == 17
    for index, request in enumerate(seen):
        assert request.method == "GET" and request.url.path == PATH
        assert request.url.params["fields"] == LOG_SOURCE_LIST_FIELDS
        assert "protocol_parameters" not in request.url.params["fields"]
        assert request.url.params["sort"] == "+id"
        assert "filter" not in request.url.params
        assert request.headers["Range"] == f"items={index * 500}-{index * 500 + 499}"


@pytest.mark.asyncio
async def test_raw_canaries_never_escape_result_repr_or_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    walker = provider(lambda request: httpx.Response(200, json=page(request, 101)))
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.complete and len(result.configs) == 101
    assert all(config.config_state is ConfigState.PARTIAL for config in result.configs)
    assert CANARY not in json.dumps([config.model_dump(mode="json") for config in result.configs])
    assert CANARY not in str(result) and CANARY not in repr(result)
    assert CANARY not in caplog.text
    assert all(CANARY not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.asyncio
async def test_raw_page_is_released_before_next_request(monkeypatch: pytest.MonkeyPatch) -> None:
    class RawRow(dict):
        pass

    class RawPage(list):
        pass

    references: list[weakref.ReferenceType[RawPage]] = []
    row_references: list[weakref.ReferenceType[RawRow]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert all(ref() is None for ref in references)
        assert all(ref() is None for ref in row_references)
        return httpx.Response(200, json=page(request, 101))

    walker = provider(handler)
    decode = walker._decode

    def track(response: httpx.Response, outcome: dict) -> RawPage:
        raw = RawPage(RawRow(item) for item in decode(response, outcome))
        references.append(weakref.ref(raw))
        row_references.extend(weakref.ref(item) for item in raw)
        return raw

    monkeypatch.setattr(walker, "_decode", track)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.complete and len(references) == 2
    assert all(ref() is None for ref in references)
    assert len(row_references) == 101 and all(ref() is None for ref in row_references)
