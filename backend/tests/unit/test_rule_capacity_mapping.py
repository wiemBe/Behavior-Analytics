"""The 7.6 capacity benchmark triple reaches the DTO, read-only and unguessed."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.providers.mock import MockQRadarProvider
from tests.unit.test_qradar_rest_provider import build_provider

#: 2026-10-10 09:11:24.026 UTC, as the lab sends it (T-015 §8).
TS_MS = 1_791_623_484_026


def _provider(row: dict):
    paths: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append((request.method, request.url.path))
        return httpx.Response(200, json=[row])

    provider, _ = build_provider(handler, page_size=10)
    return provider, paths


@pytest.mark.asyncio
async def test_the_capacity_triple_is_mapped_with_no_extra_request() -> None:
    provider, paths = _provider(
        {
            "id": 7, "name": "r", "type": "EVENT", "average_capacity": 5_277_044,
            "base_capacity": 5_277_044, "base_host_id": 53, "capacity_timestamp": TS_MS,
        }
    )

    (rule,) = await provider.list_rules()

    assert rule.base_capacity == 5_277_044.0
    assert rule.base_host_id == 53
    assert rule.capacity_timestamp == datetime.fromtimestamp(TS_MS / 1000, tz=UTC)
    assert paths and all(method == "GET" for method, _ in paths)


@pytest.mark.asyncio
async def test_absent_or_malformed_capacity_fields_stay_none() -> None:
    provider, _ = _provider(
        {
            "id": 7, "name": "r", "base_capacity": True, "base_host_id": "x",
            "capacity_timestamp": 0,
        }
    )

    (rule,) = await provider.list_rules()

    assert rule.base_capacity is None
    assert rule.base_host_id is None
    assert rule.capacity_timestamp is None


@pytest.mark.asyncio
async def test_the_mock_carries_realistic_capacity_values() -> None:
    rules = await MockQRadarProvider(seed=1).list_rules()

    assert all(r.capacity_timestamp is not None and r.base_host_id for r in rules)
    assert {r.base_capacity for r in rules} == {r.average_capacity for r in rules}
