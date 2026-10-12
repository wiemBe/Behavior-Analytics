"""Provider normalization for Phase C inventory facts."""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from app.providers.base import CapabilityNotSupportedError, ProviderCapability
from app.providers.qradar_mcp import QRadarMCPProvider
from app.providers.qradar_rest import QRadarRestProvider


@pytest.mark.asyncio
async def test_rest_parses_a_qradar_76_log_source_row() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200,
            json=[
                {
                    "id": 62,
                    "name": "source",
                    "type_id": 212,
                    "creation_date": 1_784_476_800_000,
                    "modified_date": 1_784_563_200_000,
                    "last_event_time": 1_784_649_600_000,
                    "auto_discovered": True,
                    "group_ids": [0, "20"],
                    "log_source_identifiers": [
                        {"identifier": "<script>bad</script>dmz-waf-01", "parsing_order": 1}
                    ],
                    "internal": False,
                    "status": {"status": "SUCCESS", "last_updated": 0, "messages": []},
                }
            ],
        )

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://qradar.example/api",
    )
    provider = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="test", client=client
    )
    try:
        (source,) = await provider.list_log_sources()
    finally:
        await provider.aclose()

    assert source.qradar_created_at == datetime(2026, 7, 19, 16, tzinfo=UTC)
    assert source.qradar_modified_at == datetime(2026, 7, 20, 16, tzinfo=UTC)
    assert source.last_event_time == datetime(2026, 7, 21, 16, tzinfo=UTC)
    assert source.identifier == "baddmz-waf-01"
    assert source.group_ids == [0, 20]
    assert source.auto_discovered is True
    assert source.internal is False
    assert source.status == "SUCCESS"


@pytest.mark.asyncio
async def test_rest_parses_type_flags_and_group_hierarchy() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/log_source_types"):
            return httpx.Response(
                200,
                json=[{"id": 368, "name": "Health Metrics", "internal": True, "custom": False}],
            )
        if request.url.path.endswith("/log_source_groups"):
            return httpx.Response(
                200,
                json=[
                    {
                        "id": 0,
                        "name": "Other",
                        "description": "<b>leaf</b>",
                        "parent_id": 1,
                        "assignable": False,
                    }
                ],
            )
        raise AssertionError(request.url)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://qradar.example/api",
    )
    provider = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="test", client=client
    )
    try:
        (source_type,) = await provider.list_log_source_types()
        (group,) = await provider.list_log_source_groups()
    finally:
        await provider.aclose()

    assert source_type.internal is True
    assert source_type.custom is False
    assert (group.qradar_id, group.parent_qradar_id, group.description) == (0, 1, "leaf")


@pytest.mark.asyncio
async def test_mcp_does_not_support_log_source_groups() -> None:
    provider = QRadarMCPProvider(base_url="http://mcp.invalid")
    try:
        assert not provider.supports(ProviderCapability.LOG_SOURCE_GROUPS)
        with pytest.raises(CapabilityNotSupportedError):
            await provider.list_log_source_groups()
    finally:
        await provider.aclose()
