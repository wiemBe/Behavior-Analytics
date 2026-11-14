"""SEC-RO2 §1: an Ariel search id from QRadar never reaches a URL path unchecked."""

from __future__ import annotations

import logging

import httpx
import pytest

from app.providers.qradar_rest import MalformedUpstreamResponse
from tests.unit.test_qradar_rest_provider import build_provider

BAD_IDS = [
    "../x",
    "../../siem/offenses/5",
    "a/b",
    "%2e%2e",
    "%2e%2e%2fsiem",
    "",
    "a" * 65,
    "idé-1",
    "ab‮cd",
    "a b",
    "a?b",
    "a.b",
]
GOOD_IDS = ["metric-1", "mock-000001", "6f1d3f0e-8c1a-4b7e-9d52-0a1b2c3d4e5f", "a" * 64]


def _recording():
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        return httpx.Response(200, json={"status": "COMPLETED", "events": []})

    return seen, handler


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_IDS)
async def test_a_bad_id_in_the_create_response_is_refused_and_not_logged(
    bad: str, caplog: pytest.LogCaptureFixture
) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(201, json={"search_id": bad, "status": "WAIT"})

    provider, _ = build_provider(handler)
    with caplog.at_level(logging.DEBUG), pytest.raises(MalformedUpstreamResponse) as caught:
        await provider.create_ariel_search("SELECT 1 FROM events")
    assert bad == "" or bad not in str(caught.value)
    assert bad == "" or all(bad not in r.getMessage() for r in caplog.records)
    assert seen == [("POST", "/api/ariel/searches")]  # nothing else was sent


@pytest.mark.asyncio
async def test_a_non_string_id_is_refused() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"search_id": 7, "status": "WAIT"})

    provider, _ = build_provider(handler)
    with pytest.raises(MalformedUpstreamResponse):
        await provider.create_ariel_search("SELECT 1 FROM events")


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", BAD_IDS)
async def test_every_path_method_revalidates_the_id_and_sends_nothing(bad: str) -> None:
    seen, handler = _recording()
    provider, _ = build_provider(handler)
    with pytest.raises(MalformedUpstreamResponse):
        await provider.get_ariel_search_status(bad)
    with pytest.raises(MalformedUpstreamResponse):
        await provider.get_ariel_search_results(bad, max_rows=10)
    with pytest.raises(MalformedUpstreamResponse):
        await provider.cancel_ariel_search(bad)
    assert seen == []


@pytest.mark.asyncio
@pytest.mark.parametrize("good", GOOD_IDS)
async def test_a_good_id_works_end_to_end_and_stays_under_ariel_searches(good: str) -> None:
    seen: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.raw_path.decode()))
        if request.method == "POST":
            return httpx.Response(201, json={"search_id": good, "status": "WAIT"})
        return httpx.Response(200, json={"status": "COMPLETED", "events": []})

    provider, _ = build_provider(handler)
    handle = await provider.create_ariel_search("SELECT 1 FROM events")
    assert handle.search_id == good
    await provider.get_ariel_search_status(good)
    await provider.get_ariel_search_results(good, max_rows=5)
    await provider.cancel_ariel_search(good)
    assert seen == [
        ("POST", "/api/ariel/searches?query_expression=SELECT+1+FROM+events"),
        ("GET", f"/api/ariel/searches/{good}"),
        ("GET", f"/api/ariel/searches/{good}/results"),
        ("DELETE", f"/api/ariel/searches/{good}"),
    ]
