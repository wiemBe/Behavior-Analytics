"""Source-enabled normalization through the REST and MCP read paths (JDBC-1 P1f)."""

import json
from unittest.mock import Mock

import httpx
import pytest

from app.providers import qradar_mcp, qradar_rest
from app.providers.normalize import source_enabled
from tests.unit.test_qradar_mcp_provider import build_provider, rpc_result

SOURCE_PATH = "/api/config/event_sources/log_source_management/log_sources"
CASES = [
    pytest.param({"enabled": True}, True, True, id="true"),
    pytest.param({"enabled": False}, False, False, id="false"),
    pytest.param({}, None, True, id="missing"),
    pytest.param({"enabled": None}, None, False, id="null"),
    pytest.param({"enabled": 0}, None, False, id="zero"),
    pytest.param({"enabled": 1}, None, True, id="one"),
    pytest.param({"enabled": "true"}, None, True, id="string-true"),
    pytest.param({"enabled": "false"}, None, True, id="string-false"),
]
READ_PATHS = ["walk_log_sources", "list_log_sources", "get_log_source"]


@pytest.mark.parametrize("values,strict_expected,legacy_expected", CASES)
@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
def test_source_enabled_helper(values, strict_expected, legacy_expected, strict):
    original = values.copy()
    assert source_enabled(values, strict=strict) is (
        strict_expected if strict else legacy_expected
    )
    assert values == original


async def read_source(provider, path):
    if path == "get_log_source":
        source = await provider.get_log_source(1)
        assert source is not None
        return source
    if path == "walk_log_sources":
        walk = await provider.walk_log_sources()
        assert walk.complete
        assert walk.pages_failed == 0
        sources = walk.sources
    else:
        sources = await provider.list_log_sources()
    (source,) = sources
    return source


@pytest.mark.parametrize("values,strict_expected,legacy_expected", CASES)
@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
@pytest.mark.parametrize("path", READ_PATHS)
async def test_rest_read_paths(values, strict_expected, legacy_expected, strict, path):
    row = {"id": 1, "name": "source", **values}
    detail = path == "get_log_source"
    requests = []

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.path == (f"{SOURCE_PATH}/1" if detail else SOURCE_PATH)
        return httpx.Response(200, json=row if detail else [row])

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.test/api"
    ) as client:
        # Omitting the constructor argument pins default-off compatibility.
        mode = {"strict_source_enabled": True} if strict else {}
        provider = qradar_rest.QRadarRestProvider(
            base_url="https://qradar.test", sec_token="test", client=client, **mode
        )
        source = await read_source(provider, path)
    assert (source.qradar_id, source.name) == (1, "source")
    assert source.enabled is (strict_expected if strict else legacy_expected)
    assert len(requests) == 1


@pytest.mark.parametrize("values,strict_expected,legacy_expected", CASES)
@pytest.mark.parametrize("strict", [False, True], ids=["default", "strict"])
@pytest.mark.parametrize("path", READ_PATHS)
async def test_mcp_read_paths(values, strict_expected, legacy_expected, strict, path):
    row = {"id": 1, "name": "source", **values}
    detail = path == "get_log_source"
    calls = []

    def handler(request):
        params = json.loads(request.content)["params"]
        calls.append(params)
        assert params["name"] == ("get_log_source" if detail else "list_log_sources")
        if detail:
            assert params["arguments"] == {"log_source_id": 1}
        payload = row if detail else [row]
        return rpc_result({"content": [{"type": "text", "text": json.dumps(payload)}]})

    mode = {"strict_source_enabled": True} if strict else {}
    provider, _, _ = build_provider(handler, **mode)
    try:
        source = await read_source(provider, path)
    finally:
        await provider.aclose()
    assert (source.qradar_id, source.name) == (1, "source")
    assert source.enabled is (strict_expected if strict else legacy_expected)
    assert len(calls) == 1


@pytest.mark.parametrize("module", [qradar_rest, qradar_mcp])
@pytest.mark.parametrize("strict", [False, True])
async def test_mappers_delegate_to_shared_helper(monkeypatch, module, strict):
    helper = Mock(return_value=False)
    monkeypatch.setattr(module, "source_enabled", helper)
    provider_class = (
        qradar_rest.QRadarRestProvider if module is qradar_rest else qradar_mcp.QRadarMCPProvider
    )
    provider = provider_class(
        base_url="https://qradar.test", sec_token="test", strict_source_enabled=strict
    )
    row = {"id": 1, "name": "source"}
    try:
        source = provider._to_log_source(row)
    finally:
        await provider.aclose()
    assert source is not None
    assert source.enabled is False
    helper.assert_called_once_with(row, strict=strict)
