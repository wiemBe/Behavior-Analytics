"""SCALE-2f: bounded, slim rule and building-block inventory walks."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap

import httpx
import pytest

from app.core.config import Settings
from app.providers.dto import RuleInventoryWalk
from app.providers.qradar_rest import RULE_LIST_FIELDS, QRadarRestProvider


def provider(handler, *, page_size=2, max_pages=20, sleep=None):
    async def no_sleep(_seconds):
        pass

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api"
    )
    return QRadarRestProvider(
        base_url="https://qradar.example", sec_token="test", client=client,
        page_size=page_size, max_pages=max_pages, max_retries=3, sleep=sleep or no_sleep,
    )


def budget(*, seconds=10, attempts=2):
    return RuleInventoryWalk(
        deadline=asyncio.get_running_loop().time() + seconds,
        page_timeout_seconds=7, page_attempts=attempts,
    )


def row(i):
    return {"id": i, "name": f"Rule {i}", "enabled": True, "building_block_ids": [100]}


def test_fields_equal_every_literal_mapper_read():
    tree = ast.parse(textwrap.dedent(inspect.getsource(QRadarRestProvider._to_rule)))
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "row"):
            assert node.func.attr == "get" and isinstance(node.args[0], ast.Constant)
            keys.add(node.args[0].value)
        if (isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name)
                and node.value.id == "row"):
            assert isinstance(node.slice, ast.Constant)
            keys.add(node.slice.value)
    assert set(RULE_LIST_FIELDS.split(",")) == keys
    assert len(RULE_LIST_FIELDS.split(",")) == len(keys)


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["list_rules", "list_building_blocks"])
async def test_complete_walk_slim_fields_and_page_budget(method):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert dict(request.url.params) == {"fields": RULE_LIST_FIELDS}
        assert request.extensions["timeout"]["read"] == 7
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        return httpx.Response(200, json=[row(i) for i in range(start, min(start + 2, 3))])

    walker = provider(handler)
    walk = budget()
    try:
        result = await getattr(walker, method)(inventory_walk=walk)
    finally:
        await walker.aclose()
    assert [r.qradar_id for r in result] == [0, 1, 2]
    assert all(r.is_building_block == (method == "list_building_blocks") for r in result)
    assert walk.complete and walk.pages_done == 2 and walk.pages_failed == 0
    assert len(calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["list_rules", "list_building_blocks"])
@pytest.mark.parametrize("failure,kind", [
    (401, "auth"), (429, "rate_limited"), (503, "upstream"),
    ("timeout", "timeout"), ("network", "network"),
    (None, "malformed"), ({"invalid": []}, "malformed"), ([None], "malformed"),
    ([{"id": 4}], "malformed"), ([{"id": 4, "name": "r", "description": ["invalid"]}], "malformed"),
])
async def test_page_three_failure_keeps_prefix_and_fixed_kind(method, failure, kind):
    attempts = 0

    def handler(request):
        nonlocal attempts
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        if start < 4:
            return httpx.Response(200, json=[row(start), row(start + 1)])
        attempts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("never store this", request=request)
        if failure == "network":
            raise httpx.ConnectError("never store this", request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, json={"message": "never store this"})
        return httpx.Response(200, json=failure)

    walker = provider(handler)
    walk = budget()
    try:
        result = await getattr(walker, method)(inventory_walk=walk)
    finally:
        await walker.aclose()
    assert [r.qradar_id for r in result] == [0, 1, 2, 3]
    assert not walk.complete and walk.pages_done == 2 and walk.pages_failed == 1
    assert walk.failed_page == 3 and walk.failure_kind == kind
    assert attempts == (2 if kind in {"timeout", "network", "upstream", "rate_limited"} else 1)


@pytest.mark.asyncio
async def test_successful_retry_does_not_make_walk_partial():
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=[row(1)])

    walker = provider(handler)
    walk = budget()
    try:
        assert len(await walker.list_rules(inventory_walk=walk)) == 1
    finally:
        await walker.aclose()
    assert calls == 2 and walk.complete and walk.pages_done == 1 and walk.pages_failed == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("backoff", [False, True])
async def test_wall_cap_cancels_inflight_page_or_retry_sleep_and_is_shared(backoff):
    calls = []

    async def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(200, json=[row(0), row(1)])
        if backoff:
            return httpx.Response(429, headers={"Retry-After": "30"})
        await asyncio.Event().wait()

    walker = provider(handler, sleep=asyncio.sleep)
    walk = budget(seconds=0.02)
    try:
        result = await asyncio.wait_for(walker.list_rules(inventory_walk=walk), timeout=1)
        assert await walker.list_building_blocks(inventory_walk=walk) == []
    finally:
        await walker.aclose()
    assert len(result) == 2 and len(calls) == 2
    assert not walk.complete and walk.failure_kind == "timeout" and walk.failed_page == 2
    assert walk.pages_done == 1


@pytest.mark.asyncio
async def test_page_ceiling_is_incomplete_even_without_a_request_failure():
    walker = provider(lambda r: httpx.Response(200, json=[row(1)]), page_size=1, max_pages=2)
    walk = budget()
    try:
        assert len(await walker.list_rules(inventory_walk=walk)) == 2
    finally:
        await walker.aclose()
    assert not walk.complete and walk.pages_done == 2 and walk.failure_kind == "upstream"


@pytest.mark.parametrize("setting,value", [
    ("rule_page_timeout_seconds", 0), ("rule_page_timeout_seconds", 301),
    ("rule_page_attempts", 0), ("rule_page_attempts", 7),
    ("rule_walk_max_seconds", 0), ("rule_walk_max_seconds", 3601),
])
def test_settings_reject_unbounded_budgets(setting, value):
    with pytest.raises(ValueError):
        Settings(**{setting: value})


@pytest.mark.asyncio
async def test_expired_budget_sends_no_request():
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=[])

    walker = provider(handler)
    walk = budget(seconds=-1)
    try:
        assert await walker.list_rules(inventory_walk=walk) == []
    finally:
        await walker.aclose()
    assert calls == [] and not walk.complete and walk.failure_kind == "timeout"
