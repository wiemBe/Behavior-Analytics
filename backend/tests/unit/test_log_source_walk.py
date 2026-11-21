"""FIX-74: the slim, page-tolerant log source walk of the REST provider."""

from __future__ import annotations

import httpx
import pytest

from app.providers.base import (
    FAILURE_AUTH,
    FAILURE_MALFORMED,
    FAILURE_RATE_LIMITED,
    FAILURE_TIMEOUT,
    FAILURE_UPSTREAM,
    ProviderUnavailableError,
    failure_kind,
)
from app.providers.qradar_rest import (
    LOG_SOURCE_FIELDS_VERSIONED_ON_QRADAR,
    LOG_SOURCE_LIST_FIELDS,
    QRadarRestProvider,
    log_source_list_fields,
)

PATH = "/api/config/event_sources/log_source_management/log_sources"


def real_row(index: int) -> dict:
    """A QRadar 7.5/7.6 row as the full endpoint returns it, protocol parameters included."""
    return {
        "id": index,
        "name": f"f5-waf-{index:05d}",
        "description": "edge waf",
        "type_id": 212,
        "protocol_type_id": 0,
        "enabled": True,
        "status": {"status": "SUCCESS", "last_updated": 0, "messages": []},
        "credibility": 5,
        "target_event_collector_id": 7,
        "last_event_time": 1_784_649_600_000,
        "creation_date": 1_784_476_800_000,
        "modified_date": 1_784_563_200_000,
        "auto_discovered": False,
        "group_ids": [20],
        "log_source_identifiers": [{"identifier": f"10.0.{index // 250}.{index % 250}"}],
        "internal": False,
        "protocol_parameters": [{"name": "secret", "value": "x" * 400}],
        "average_eps": 3.0,
        "gateway": False,
    }


def slim(row: dict, request: httpx.Request) -> dict:
    wanted = set(request.url.params["fields"].split(","))
    return {key: value for key, value in row.items() if key in wanted}


def provider(handler, *, page_size: int = 500, retries: int = 2,  # type: ignore[no-untyped-def]
             api_version: str = "20.0") -> QRadarRestProvider:
    async def no_sleep(_seconds: float) -> None:
        return None

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api"
    )
    return QRadarRestProvider(
        base_url="https://qradar.example",
        sec_token="t",
        api_version=api_version,
        client=client,
        page_size=page_size,
        max_retries=retries,
        sleep=no_sleep,
    )


def page_of(request: httpx.Request, total: int, page_size: int) -> list[dict]:
    start, end = request.headers["Range"].removeprefix("items=").split("-")
    return [
        slim(real_row(i), request)
        for i in range(int(start), min(int(end) + 1, total))
    ]


def test_the_field_list_never_asks_for_protocol_parameters_and_names_every_mapped_key() -> None:
    slim = set(LOG_SOURCE_LIST_FIELDS.split(","))
    full = set(log_source_list_fields("29.0").split(","))
    assert "protocol_parameters" not in slim and "protocol_parameters" not in full
    assert {
        "id", "name", "description", "type_id", "protocol_type_id", "enabled", "status",
        "credibility", "target_event_collector_id", "last_event_time", "creation_date",
        "modified_date", "auto_discovered", "group_ids", "internal",
    } == slim
    # The < 28.0 form is the slim list; the >= 28.0 form adds exactly the versioned identifier.
    assert set(log_source_list_fields("20.0").split(",")) == slim
    assert full == slim | LOG_SOURCE_FIELDS_VERSIONED_ON_QRADAR


@pytest.mark.asyncio
async def test_a_slim_real_shaped_row_maps_to_every_inventory_field() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=[slim(real_row(41), request)])

    # At 29.0 the identifier is requested, so the row maps to every inventory field.
    walker = provider(handler, api_version="29.0")
    try:
        walk = await walker.walk_log_sources()
        (listed,) = await walker.list_log_sources()
    finally:
        await walker.aclose()
    assert walk.complete and walk.pages_done == 1 and walk.pages_failed == 0
    (source,) = walk.sources
    assert source == listed
    assert (source.type_id, source.protocol_type_id, source.enabled) == (212, 0, True)
    assert source.status == "SUCCESS" and source.credibility == 5
    assert source.target_event_collector_id == 7
    assert source.last_event_time is not None and source.qradar_created_at is not None
    assert source.qradar_modified_at is not None
    assert source.auto_discovered is False and source.group_ids == [20]
    assert source.identifier == "10.0.0.41" and source.internal is False
    assert source.description == "edge waf"
    assert all(r.url.params["fields"] == log_source_list_fields("29.0") for r in seen)
    # A stable order, so a source created between two pages cannot shift rows across them.
    assert all(r.url.params["sort"] == "+id" for r in seen)
    assert "protocol_parameters" not in LOG_SOURCE_LIST_FIELDS

    # Below 28.0 the same slim walk succeeds but leaves the identifier None, as in 0.1.16.
    below: list[httpx.Request] = []

    def handler_below(request: httpx.Request) -> httpx.Response:
        below.append(request)
        return httpx.Response(200, json=[slim(real_row(41), request)])

    walker_below = provider(handler_below, api_version="20.0")
    try:
        walk_below = await walker_below.walk_log_sources()
    finally:
        await walker_below.aclose()
    (source_below,) = walk_below.sources
    assert walk_below.complete and source_below.identifier is None
    assert all(r.url.params["fields"] == log_source_list_fields("20.0") for r in below)
    assert "protocol_parameters" not in log_source_list_fields("20.0")


@pytest.mark.asyncio
async def test_a_walk_of_many_pages_is_complete_and_counts_its_pages() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page_of(request, 2_300, 500))

    walker = provider(handler)
    try:
        walk = await walker.walk_log_sources()
    finally:
        await walker.aclose()
    assert (len(walk.sources), walk.pages_done, walk.complete) == (2_300, 5, True)
    assert len({s.qradar_id for s in walk.sources}) == 2_300


@pytest.mark.asyncio
async def test_a_page_that_times_out_once_is_retried_and_the_walk_stays_complete() -> None:
    calls = {"page2": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Range"].startswith("items=500-"):
            calls["page2"] += 1
            if calls["page2"] == 1:
                raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=page_of(request, 1_200, 500))

    walker = provider(handler)
    try:
        walk = await walker.walk_log_sources(page_attempts=2)
    finally:
        await walker.aclose()
    assert calls["page2"] == 2
    assert walk.complete and len(walk.sources) == 1_200 and walk.pages_failed == 0


@pytest.mark.asyncio
async def test_a_page_that_fails_for_good_is_skipped_and_the_walk_is_partial() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Range"].startswith("items=500-"):
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=page_of(request, 1_200, 500))

    walker = provider(handler)
    try:
        walk = await walker.walk_log_sources(page_attempts=2)
    finally:
        await walker.aclose()
    assert not walk.complete
    assert (walk.failed_page, walk.failure_kind, walk.pages_failed) == (2, FAILURE_TIMEOUT, 1)
    # Pages 1 and 3 arrived; the failed page's rows are the only ones missing.
    assert len(walk.sources) == 700 and walk.pages_done == 2


@pytest.mark.asyncio
async def test_an_authorization_failure_ends_the_walk_and_three_failed_pages_in_a_row_too() -> None:
    def forbidden(request: httpx.Request) -> httpx.Response:
        if request.headers["Range"].startswith("items=0-"):
            return httpx.Response(200, json=page_of(request, 5_000, 500))
        return httpx.Response(403, json={"message": "no"})

    walker = provider(forbidden)
    try:
        walk = await walker.walk_log_sources()
    finally:
        await walker.aclose()
    assert (walk.failure_kind, walk.failed_page, walk.pages_failed) == (FAILURE_AUTH, 2, 1)
    assert not walk.complete and len(walk.sources) == 500

    requested: list[str] = []

    def always_500(request: httpx.Request) -> httpx.Response:
        requested.append(request.headers["Range"])
        if request.headers["Range"].startswith("items=0-"):
            return httpx.Response(200, json=page_of(request, 5_000, 500))
        return httpx.Response(500)

    walker = provider(always_500, retries=0)
    try:
        walk = await walker.walk_log_sources(page_attempts=1)
    finally:
        await walker.aclose()
    assert (walk.failure_kind, walk.pages_failed, len(requested)) == (FAILURE_UPSTREAM, 3, 4)
    assert not walk.complete


@pytest.mark.asyncio
async def test_the_wall_clock_cap_ends_the_walk_incomplete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page_of(request, 5_000, 500))

    walker = provider(handler)
    try:
        walk = await walker.walk_log_sources(wall_seconds=0.0)
    finally:
        await walker.aclose()
    assert not walk.complete and walk.sources == []
    assert walk.failure_kind == FAILURE_TIMEOUT and walk.failed_page == 1


@pytest.mark.asyncio
async def test_a_page_ceiling_cut_is_incomplete_not_complete() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=page_of(request, 10_000, 500))

    walker = provider(handler)
    walker._max_pages = 2
    try:
        walk = await walker.walk_log_sources()
    finally:
        await walker.aclose()
    assert not walk.complete and len(walk.sources) == 1_000 and walk.failed_page is None


@pytest.mark.asyncio
async def test_a_non_list_page_is_a_malformed_failure_and_rate_limits_are_named() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["Range"].startswith("items=0-"):
            return httpx.Response(200, json={"not": "a list"})
        return httpx.Response(200, json=[])

    walker = provider(handler)
    try:
        walk = await walker.walk_log_sources()
    finally:
        await walker.aclose()
    assert walk.failure_kind == FAILURE_MALFORMED and not walk.complete

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429)

    walker = provider(limited, retries=0)
    try:
        walk = await walker.walk_log_sources(page_attempts=1)
    finally:
        await walker.aclose()
    assert walk.failure_kind == FAILURE_RATE_LIMITED


def test_failure_kind_reads_the_class_and_status_never_the_message() -> None:
    assert failure_kind(ProviderUnavailableError("timed out", status_code=None)) == "network"
    assert failure_kind(ProviderUnavailableError("x", status_code=503)) == FAILURE_UPSTREAM
    assert failure_kind(ProviderUnavailableError("x", status_code=429)) == FAILURE_RATE_LIMITED
    timed = ProviderUnavailableError("QRadar request timed out")
    timed.__cause__ = httpx.ReadTimeout("t")
    assert failure_kind(timed) == FAILURE_TIMEOUT
