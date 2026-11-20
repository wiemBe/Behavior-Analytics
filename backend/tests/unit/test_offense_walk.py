"""SCALE-2k: slim, bounded offense reads preserve completed pages."""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
import uuid
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.providers.dto import PageWalkBudget
from app.providers.qradar_rest import OFFENSE_LIST_FIELDS, QRadarRestProvider
from tests.unit.test_rule_inventory_walk import provider


def row(i):
    return {"id": i, "status": "OPEN", "last_persisted_time": 1784109600000 + i}


@pytest.fixture
def settings(monkeypatch):
    configured = Settings(
        offense_page_timeout_seconds=7, offense_page_attempts=2, offense_walk_max_seconds=10,
    )
    monkeypatch.setattr("app.core.config.get_settings", lambda: configured)
    return configured


def test_fields_equal_every_mapper_read():
    tree = ast.parse(textwrap.dedent(inspect.getsource(QRadarRestProvider._to_offense)))
    keys = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "row"):
            assert node.func.attr == "get" and isinstance(node.args[0], ast.Constant)
            keys.add(node.args[0].value)
    assert set(OFFENSE_LIST_FIELDS.split(",")) == keys
    assert len(OFFENSE_LIST_FIELDS.split(",")) == len(keys)


@pytest.mark.asyncio
async def test_complete_walk_uses_fields_sort_and_page_timeout(settings):
    calls = []

    def handler(request):
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/api/siem/offenses"
        assert dict(request.url.params) == {
            "fields": OFFENSE_LIST_FIELDS, "sort": "+last_persisted_time",
        }
        assert request.extensions["timeout"]["read"] == 7
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        return httpx.Response(200, json=[row(i) for i in range(start, min(start + 2, 3))])

    walker = provider(handler)
    try:
        result = await walker.list_offenses(open_only=False)
    finally:
        await walker.aclose()
    assert [r.qradar_id for r in result.rows] == [0, 1, 2]
    assert result.pages_done == len(calls) == 2
    assert result.pages_failed == 0 and result.failure_kind is None
    assert not result.truncated


@pytest.mark.asyncio
@pytest.mark.parametrize("failure,kind", [
    (401, "auth"), (429, "rate_limited"), (503, "upstream"),
    ("timeout", "timeout"), ("network", "network"),
    (None, "malformed"), ({"invalid": []}, "malformed"), ([None], "malformed"),
    ([{"id": 4, "description": ["invalid"]}], "malformed"),
])
@pytest.mark.parametrize("fail_start", [0, 4])
async def test_failure_preserves_only_completed_pages(settings, failure, kind, fail_start):
    attempts = 0

    def handler(request):
        nonlocal attempts
        start = int(request.headers["Range"].split("=")[1].split("-")[0])
        if start < fail_start:
            return httpx.Response(200, json=[row(start), row(start + 1)])
        attempts += 1
        if failure == "timeout":
            raise httpx.ReadTimeout("untrusted", request=request)
        if failure == "network":
            raise httpx.ConnectError("untrusted", request=request)
        if isinstance(failure, int):
            return httpx.Response(failure, json={"message": "untrusted"})
        return httpx.Response(200, json=failure)

    walker = provider(handler)
    try:
        result = await walker.list_offenses(open_only=False)
    finally:
        await walker.aclose()
    assert [r.qradar_id for r in result.rows] == list(range(fail_start))
    assert result.pages_done == fail_start // 2
    assert result.pages_failed == 1 and result.failed_page == fail_start // 2 + 1
    assert result.failure_kind == kind and not result.truncated
    assert attempts == (2 if kind in {"timeout", "network", "upstream", "rate_limited"} else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("retry_sleep", [False, True])
async def test_wall_cap_cancels_request_or_backoff(settings, retry_sleep):
    settings.offense_walk_max_seconds = 0.02
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(200, json=[row(1), row(2)])
        if retry_sleep:
            return httpx.Response(503)
        await asyncio.sleep(1)
        raise AssertionError("deadline failed to cancel request")

    async def sleep(_seconds):
        await asyncio.sleep(1)
        raise AssertionError("deadline failed to cancel backoff")

    walker = provider(handler, sleep=sleep)
    try:
        result = await walker.list_offenses(open_only=False)
    finally:
        await walker.aclose()
    assert [r.qradar_id for r in result.rows] == [1, 2]
    assert result.pages_done == 1 and result.pages_failed == 1
    assert result.failed_page == 2 and result.failure_kind == "timeout"
    assert calls == 2 and not result.truncated


@pytest.mark.asyncio
async def test_successful_retry_is_complete(settings):
    calls = 0

    def handler(request):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, json=[row(1)])

    walker = provider(handler)
    try:
        result = await walker.list_offenses(open_only=False)
    finally:
        await walker.aclose()
    assert calls == 2 and result.pages_done == 1
    assert result.pages_failed == 0 and result.failure_kind is None


@pytest.mark.asyncio
async def test_expired_budget_sends_no_request(settings, monkeypatch):
    def expired(**kwargs):
        return PageWalkBudget(**(kwargs | {"deadline": asyncio.get_running_loop().time() - 1}))

    def handler(request):
        raise AssertionError("expired walk must not send a request")

    monkeypatch.setattr("app.providers.qradar_rest.PageWalkBudget", expired)
    walker = provider(handler)
    try:
        result = await walker.list_offenses(open_only=False)
    finally:
        await walker.aclose()
    assert result.rows == [] and result.pages_done == 0
    assert result.pages_failed == result.failed_page == 1 and result.failure_kind == "timeout"


@pytest.mark.asyncio
async def test_page_ceiling_remains_truncated(settings):
    walker = provider(lambda request: httpx.Response(200, json=[row(1), row(2)]))
    try:
        result = await walker.list_offenses(open_only=False, max_pages=2)
    finally:
        await walker.aclose()
    assert result.truncated and result.pages_done == 2
    assert result.pages_failed == 0 and result.failed_page is None and result.failure_kind is None


@pytest.mark.parametrize("key,value", [
    ("offense_page_timeout_seconds", 0), ("offense_page_timeout_seconds", 301),
    ("offense_page_attempts", 0), ("offense_page_attempts", 7),
    ("offense_walk_max_seconds", 0), ("offense_walk_max_seconds", 3601),
])
def test_settings_reject_unbounded_walks(key, value):
    with pytest.raises(ValidationError):
        Settings(**{key: value})


@pytest.mark.asyncio
async def test_worker_preserves_skipped_lock_status(monkeypatch):
    from app.collectors.offense_collector import OffenseCollectionReport
    from app.workers import tasks

    instance = SimpleNamespace(id=uuid.uuid4())

    async def collect(self, selected):
        return OffenseCollectionReport(instance_id=selected.id, skipped_locked=True)

    async def each(run, name):
        return await run(None, instance, object())

    monkeypatch.setattr(tasks.OffenseCollector, "collect", collect)
    monkeypatch.setattr(tasks, "_for_each_instance", each)
    assert (await tasks.collect_offenses())["status"] == "skipped_locked"
