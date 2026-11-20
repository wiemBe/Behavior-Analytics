"""Filtered JDBC paging without widening the fleet inventory or retaining raw pages."""

from __future__ import annotations

import asyncio
import gc
import json
import logging
import traceback
import weakref
from collections.abc import Callable
from dataclasses import asdict, dataclass

import httpx
import pytest
import pytest_asyncio

from app.jdbc.dto import (
    ConfigState,
    JdbcConfigWalk,
    JdbcRowRejection,
    JdbcWalkFailure,
    JdbcWalkState,
)
from app.providers.qradar_rest import LOG_SOURCE_LIST_FIELDS, QRadarRestProvider

PATH = "/api/config/event_sources/log_source_management/log_sources"
FIELDS = "id,protocol_type_id,protocol_parameters"
CANARY = "DENIED-JDBC-PASSWORD-CANARY"


@dataclass
class LoopClock:
    now: float

    def time(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest_asyncio.fixture
async def loop_clock(monkeypatch: pytest.MonkeyPatch) -> LoopClock:
    """Freeze both the walk's checks and asyncio's timeout scheduling until advanced."""
    loop = asyncio.get_running_loop()
    clock = LoopClock(loop.time())
    monkeypatch.setattr(loop, "time", clock.time)
    return clock


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
async def test_failed_pages_end_incomplete_without_losing_safe_pages(failure: int | str) -> None:
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
    assert len(seen) == (2 if failure in (401, 403) else 4)
    assert result.pages_failed == (1 if failure in (401, 403) else 3)
    assert result.state is JdbcWalkState.PARTIAL
    assert CANARY not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_row", [CANARY, {"id": True, "protocol_type_id": 8},
                                     {"id": "1", "protocol_type_id": 8},
                                     {"id": 1}, {"id": 1, "protocol_type_id": "8"}])
async def test_malformed_rows_fail_three_pages_and_drop_each_page(bad_row: object) -> None:
    walker = provider(lambda request: httpx.Response(200, json=[row(0), bad_row]))
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert not result.complete
    assert (result.pages_read, result.rows_read, result.configs) == (3, 6, [])
    assert result.pages_failed == 3 and result.state is JdbcWalkState.FAILED
    assert result.rejected_rows == {JdbcRowRejection.MALFORMED_ROW: 3}


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


@pytest.mark.asyncio
async def test_partial_keeps_safe_pages() -> None:
    ranges: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers["Range"])
        if len(ranges) in (2, 3, 5, 6):
            return httpx.Response(500, text=CANARY)
        return httpx.Response(200, json=page(request, 650))

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.PARTIAL and not result.complete
    assert result.failure_code is JdbcWalkFailure.UPSTREAM and result.pages_failed == 4
    assert (result.pages_read, result.rows_read) == (3, 250)
    assert [item.qradar_id for item in result.configs] == [
        *range(100), *range(300, 400), *range(600, 650),
    ]
    assert ranges == [f"items={i * 100}-{i * 100 + 99}" for i in range(7)]
    assert result.rejected_rows == {} and result.elapsed_seconds >= 0


@pytest.mark.asyncio
@pytest.mark.parametrize(("failure", "code", "gets", "failed"), [
    (401, JdbcWalkFailure.AUTH, 1, 1),
    (403, JdbcWalkFailure.AUTH, 1, 1),
    (500, JdbcWalkFailure.UPSTREAM, 3, 3),
    (429, JdbcWalkFailure.RATE_LIMITED, 3, 3),
    ("wall", JdbcWalkFailure.TIMEOUT, 0, 0),
    ("ceiling", JdbcWalkFailure.PAGE_CEILING, 1, 0),
])
async def test_auth_three_failures_wall_ceiling(
    failure: int | str, code: JdbcWalkFailure, gets: int, failed: int,
    loop_clock: LoopClock,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if isinstance(failure, int):
            return httpx.Response(failure, text=CANARY)
        return httpx.Response(200, json=page(request, 500))

    walker = provider(handler, max_pages=1 if failure == "ceiling" else 200)
    try:
        result = await walker.walk_jdbc_configs(wall_seconds=0 if failure == "wall" else 600)
    finally:
        await walker.aclose()
    assert result.failure_code is code and result.pages_failed == failed
    assert len(seen) == gets and not result.complete
    expected = JdbcWalkState.PARTIAL if failure == "ceiling" else JdbcWalkState.FAILED
    assert result.state is expected
    assert len(result.configs) == (100 if failure == "ceiling" else 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["retry_after", "backoff", "request"])
async def test_remaining_deadline_includes_retry_sleep(
    failure: str, monkeypatch: pytest.MonkeyPatch, loop_clock: LoopClock,
) -> None:
    seen: list[str] = []
    sleeps: list[float] = []
    cancelled = False
    deadlines: list[float | None] = []
    timeout_at = asyncio.timeout_at

    def track_deadline(deadline: float | None) -> asyncio.Timeout:
        deadlines.append(deadline)
        return timeout_at(deadline)

    monkeypatch.setattr(asyncio, "timeout_at", track_deadline)

    async def pause() -> None:
        nonlocal cancelled
        try:
            loop_clock.advance(0.75)
            await asyncio.Future()
        except asyncio.CancelledError:
            cancelled = True
            raise

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["Range"])
        if len(seen) == 1:
            loop_clock.advance(0.25)
            return httpx.Response(200, json=page(request, 200))
        if failure == "request":
            await pause()
        if failure == "backoff":
            raise httpx.ReadTimeout(CANARY, request=request)
        return httpx.Response(429, text=CANARY, headers={"Retry-After": "10"})

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        await pause()

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api"
    )
    walker = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="t", client=client, sleep=sleep,
    )
    started = loop_clock.time()
    try:
        result = await asyncio.wait_for(walker.walk_jdbc_configs(wall_seconds=1), timeout=2)
    finally:
        await walker.aclose()
    assert cancelled and loop_clock.time() - started < 2
    assert result.state is JdbcWalkState.PARTIAL and len(result.configs) == 100
    assert result.failure_code is JdbcWalkFailure.TIMEOUT and result.pages_failed == 1
    assert seen == ["items=0-99", "items=100-199"]
    assert len(deadlines) == 2 and deadlines[0] is not None and deadlines[0] == deadlines[1]
    assert len(sleeps) == (0 if failure == "request" else 1)
    assert 0 < result.elapsed_seconds < 2


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    "wrong_protocol", "duplicate", "within_page", "shape", "identity", "protocol_type",
    "identity_bool", "identity_float", "protocol_bool", "protocol_float",
    "missing_parameters", "null_parameters", "empty_parameters", "malformed_parameters",
])
async def test_wrong_protocol_duplicate_malformed_not_complete(bad: str) -> None:
    rejected = {
        "wrong_protocol": {**row(101), "protocol_type_id": 21},
        "duplicate": row(0),
        "within_page": row(100),
        "shape": CANARY,
        "identity": {**row(101), "id": CANARY},
        "protocol_type": {**row(101), "protocol_type_id": CANARY},
        "identity_bool": {**row(101), "id": True},
        "identity_float": {**row(101), "id": 101.0},
        "protocol_bool": {**row(101), "protocol_type_id": True},
        "protocol_float": {**row(101), "protocol_type_id": 8.0},
        "missing_parameters": {"id": 101, "protocol_type_id": 8},
        "null_parameters": {**row(101), "protocol_parameters": None},
        "empty_parameters": {**row(101), "protocol_parameters": []},
        "malformed_parameters": {**row(101), "protocol_parameters": [{"value": CANARY}]},
    }[bad]
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = page(request, 100) if calls == 1 else [row(100), rejected] if calls == 2 else []
        return httpx.Response(200, json=body)

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    reason = (JdbcRowRejection.WRONG_PROTOCOL if bad == "wrong_protocol" else
              JdbcRowRejection.DUPLICATE_ID if bad in ("duplicate", "within_page") else
              JdbcRowRejection.PARAMETERS_UNAVAILABLE if "parameters" in bad else
              JdbcRowRejection.MALFORMED_ROW)
    assert result.state is JdbcWalkState.PARTIAL and not result.complete and calls == 3
    assert result.failure_code is JdbcWalkFailure.MALFORMED and result.pages_failed == 1
    assert result.rejected_rows == {reason: 1}
    assert (result.pages_read, result.rows_read) == (3, 102)
    assert [config.qradar_id for config in result.configs] == list(range(100))


@pytest.mark.asyncio
async def test_rejection_reason_counts_have_no_values() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = [CANARY, CANARY, {**row(1), "protocol_type_id": 20}, row(2), row(2),
                {**row(3), "protocol_parameters": CANARY}] if calls == 1 else []
        return httpx.Response(200, json=body)

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.FAILED and not result.configs
    assert result.rejected_rows == {
        JdbcRowRejection.MALFORMED_ROW: 2, JdbcRowRejection.WRONG_PROTOCOL: 1,
        JdbcRowRejection.DUPLICATE_ID: 1, JdbcRowRejection.PARAMETERS_UNAVAILABLE: 1,
    }
    assert all(type(value) is int for value in result.rejected_rows.values())
    assert CANARY not in json.dumps(asdict(result))


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [None, 401, 500, "object", "json", "none", "row"])
async def test_empty_success_is_complete_empty_failure_is_failed(failure: int | str | None) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failure is None or calls > 1:
            return httpx.Response(200, json=[])
        if failure == "json":
            return httpx.Response(200, content=CANARY.encode())
        if failure == "none":
            return httpx.Response(204)
        if failure == "object":
            return httpx.Response(200, json={"message": CANARY})
        if failure == "row":
            return httpx.Response(200, json=[CANARY])
        assert isinstance(failure, int)
        return httpx.Response(failure, text=CANARY)

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert not result.configs
    assert result.complete is (failure is None)
    assert result.state is (JdbcWalkState.COMPLETE if failure is None else JdbcWalkState.FAILED)
    assert (result.failure_code is None) is (failure is None)
    assert result.pages_failed == (0 if failure is None else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["denied", "row", "json", "object", "auth", "network"])
async def test_raw_canaries_never_escape(
    failure: str, caplog: pytest.LogCaptureFixture,
) -> None:
    canaries = [f"JDBC-SECRET-{kind}-CANARY" for kind in ("PASSWORD", "USERNAME", "KEY", "URL")]
    payload = " ".join(canaries)
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError(payload, request=request)
        if failure == "auth":
            return httpx.Response(403, text=payload)
        if failure == "json":
            return httpx.Response(200, content=payload.encode())
        if failure == "object":
            return httpx.Response(200, json={"status_message": payload})
        if failure == "row":
            return httpx.Response(200, json=[{**row(1), "id": payload}])
        safe = row(1)
        safe["protocol_parameters"].extend([
            {"id": 9999, "name": name, "value": value}
            for name, value in zip(("password", "username", "keyFile", "connection"), canaries,
                                   strict=True)
        ])
        return httpx.Response(200, json=[safe])

    walker = provider(handler)
    try:
        try:
            result = await walker.walk_jdbc_configs()
        except Exception as exc:
            # If an error ever escapes, neither its cause nor context may carry raw input.
            chain: list[BaseException] = [exc]
            for error in chain:
                for linked in (error.__cause__, error.__context__):
                    if linked is not None and linked not in chain:
                        chain.append(linked)
            rendered = "".join(traceback.format_exception(exc)) + repr(chain)
            assert all(canary not in rendered for canary in canaries)
            raise
    finally:
        await walker.aclose()
    rendered = repr(result) + str(result) + json.dumps(
        asdict(result), default=lambda config: config.model_dump(mode="json"),
    ) + caplog.text
    rendered += repr([record.__dict__ for record in caplog.records])
    assert all(canary not in rendered for canary in canaries)
    assert result.state is (JdbcWalkState.COMPLETE if failure == "denied" else JdbcWalkState.FAILED)
    assert result.failure_code is {
        "denied": None, "row": JdbcWalkFailure.MALFORMED, "json": JdbcWalkFailure.MALFORMED,
        "object": JdbcWalkFailure.MALFORMED, "auth": JdbcWalkFailure.AUTH,
        "network": JdbcWalkFailure.NETWORK,
    }[failure]


@pytest.mark.asyncio
async def test_default_budget_and_not_collected_result(
    monkeypatch: pytest.MonkeyPatch, loop_clock: LoopClock,
) -> None:
    result = JdbcConfigWalk([])
    assert result.state is JdbcWalkState.NOT_COLLECTED and not result.complete
    assert result.failure_code is None and result.rejected_rows == {}
    assert (result.pages_read, result.rows_read, result.pages_failed, result.elapsed_seconds) == (
        0, 0, 0, 0,
    )
    deadlines: list[float | None] = []
    timeout_at = asyncio.timeout_at

    def track(deadline: float | None) -> asyncio.Timeout:
        deadlines.append(deadline)
        return timeout_at(deadline)

    monkeypatch.setattr(asyncio, "timeout_at", track)

    async def request(*args: object, **kwargs: object) -> list:
        assert kwargs["timeout"] == 30 and kwargs["max_attempts"] == 3
        return []

    walker = provider(lambda request: httpx.Response(200, json=[]))
    monkeypatch.setattr(walker, "_request", request)
    started = asyncio.get_running_loop().time()
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.COMPLETE
    assert len(deadlines) == 1 and deadlines[0] is not None
    assert started + 600 <= deadlines[0] <= asyncio.get_running_loop().time() + 600


@pytest.mark.asyncio
@pytest.mark.parametrize(("attempts", "gets", "complete"), [(1, 2, False), (2, 2, True)])
async def test_page_timeout_and_attempt_budget(
    attempts: int, gets: int, complete: bool,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if len(seen) == 1:
            return httpx.Response(500, text=CANARY)
        return httpx.Response(200, json=[])

    walker = provider(handler)
    walker._max_retries = 2

    async def no_sleep(seconds: float) -> None:
        pass

    walker._sleep = no_sleep
    try:
        result = await walker.walk_jdbc_configs(page_timeout_seconds=1.25, page_attempts=attempts)
    finally:
        await walker.aclose()
    assert len(seen) == gets and result.complete is complete
    assert [request.headers["Range"] for request in seen] == (
        ["items=0-99", "items=0-99"] if complete else ["items=0-99", "items=100-199"]
    )
    assert all(request.extensions["timeout"] == dict.fromkeys(
        ("connect", "read", "write", "pool"), 1.25,
    ) for request in seen)


@pytest.mark.asyncio
async def test_rejected_raw_page_is_released_before_next_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RawPage(list):
        pass

    class RawRow(dict):
        pass

    pages: list[weakref.ReferenceType[RawPage]] = []
    rows: list[weakref.ReferenceType[RawRow]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert all(ref() is None for ref in [*pages, *rows])
        return httpx.Response(200, json=[{**row(1), "id": CANARY}] if not pages else [])

    walker = provider(handler)
    decode = walker._decode

    def track(response: httpx.Response, outcome: dict) -> RawPage:
        raw = RawPage(RawRow(item) for item in decode(response, outcome))
        pages.append(weakref.ref(raw))
        rows.extend(weakref.ref(item) for item in raw)
        return raw

    monkeypatch.setattr(walker, "_decode", track)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.FAILED and len(pages) == 2 and len(rows) == 1
    assert all(ref() is None for ref in [*pages, *rows])


@pytest.mark.asyncio
async def test_expired_during_projection_never_completes(
    monkeypatch: pytest.MonkeyPatch, loop_clock: LoopClock,
) -> None:
    from app.providers import qradar_rest

    project = qradar_rest.project_jdbc_config

    def expire_project(**kwargs: object):  # type: ignore[no-untyped-def]
        loop_clock.advance(1)
        return project(**kwargs)

    monkeypatch.setattr(qradar_rest, "project_jdbc_config", expire_project)
    walker = provider(lambda request: httpx.Response(200, json=[row(1)]))
    try:
        result = await walker.walk_jdbc_configs(wall_seconds=1)
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.PARTIAL and not result.complete
    assert result.failure_code is JdbcWalkFailure.TIMEOUT and result.pages_failed == 0
    assert [config.qradar_id for config in result.configs] == [1]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["gzip", "deep_json", "runtime"])
@pytest.mark.parametrize("safe_pages", [0, 1])
async def test_unexpected_page_failure_returns_safe_result(
    failure: str, safe_pages: int, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    ranges: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers["Range"])
        if len(ranges) <= safe_pages:
            return httpx.Response(200, json=page(request, 200))
        if failure == "gzip":
            return httpx.Response(
                200, headers={"Content-Encoding": "gzip"}, content=CANARY.encode(),
            )
        if failure == "deep_json":
            return httpx.Response(200, content=b"[" * 200_000 + json.dumps(CANARY).encode()
                                  + b"]" * 200_000)
        try:
            raise ValueError(CANARY)
        except ValueError as exc:
            raise RuntimeError(CANARY) from exc

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is (JdbcWalkState.PARTIAL if safe_pages else JdbcWalkState.FAILED)
    assert not result.complete and result.failure_code is JdbcWalkFailure.MALFORMED
    assert [config.qradar_id for config in result.configs] == list(range(safe_pages * 100))
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        safe_pages, safe_pages * 100, 1, safe_pages,
    )
    assert result.rejected_rows == {} and len(ranges) == safe_pages + 1
    rendered = repr(result) + json.dumps(
        asdict(result), default=lambda config: config.model_dump(mode="json"),
    ) + caplog.text
    rendered += repr([record.__dict__ for record in caplog.records])
    assert CANARY not in rendered


@pytest.mark.asyncio
@pytest.mark.parametrize("signal", [asyncio.CancelledError, KeyboardInterrupt, SystemExit])
async def test_process_control_exceptions_propagate(signal: type[BaseException]) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise signal("control")

    walker = provider(handler)
    try:
        with pytest.raises(signal, match="control"):
            await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()


@pytest.mark.asyncio
@pytest.mark.parametrize(("terminal", "code", "failed", "read", "rows"), [
    (401, JdbcWalkFailure.AUTH, 2, 1, 100),
    (403, JdbcWalkFailure.AUTH, 2, 1, 100),
    ("ceiling", JdbcWalkFailure.PAGE_CEILING, 1, 2, 200),
    ("deadline", JdbcWalkFailure.TIMEOUT, 2, 1, 100),
    ("malformed", JdbcWalkFailure.MALFORMED, 2, 1, 100),
])
async def test_terminal_failure_overrides_earlier_upstream(
    terminal: int | str, code: JdbcWalkFailure, failed: int, read: int, rows: int,
    caplog: pytest.LogCaptureFixture, loop_clock: LoopClock,
) -> None:
    ranges: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers["Range"])
        if len(ranges) == 2:
            return httpx.Response(500, text=CANARY)
        if len(ranges) == 3:
            if isinstance(terminal, int):
                return httpx.Response(terminal, text=CANARY)
            if terminal == "deadline":
                loop_clock.advance(1)
                await asyncio.Future()
            if terminal == "malformed":
                raise RuntimeError(CANARY)
        return httpx.Response(200, json=page(request, 500))

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api",
    )
    walker = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="t", client=client,
        max_retries=0, max_pages=3,
    )
    try:
        result = await walker.walk_jdbc_configs(wall_seconds=1 if terminal == "deadline" else 600)
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.PARTIAL and result.failure_code is code
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        read, rows, failed, 1,
    )
    assert [config.qradar_id for config in result.configs] == (
        [*range(100), *range(200, 300)] if terminal == "ceiling" else list(range(100))
    )
    assert ranges == ["items=0-99", "items=100-199", "items=200-299"]
    assert CANARY not in repr(result) + caplog.text


@pytest.mark.asyncio
async def test_third_consecutive_failure_is_terminal_cause() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(500, text=CANARY)
        return httpx.Response(200, content=CANARY.encode())

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.FAILED and result.failure_code is JdbcWalkFailure.MALFORMED
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        0, 0, 3, 0,
    )
    assert calls == 3


@pytest.mark.asyncio
async def test_invalid_json_response_released_without_cyclic_gc(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers.qradar_rest import MalformedUpstreamResponse

    responses: list[weakref.ReferenceType[httpx.Response]] = []
    detached: list[bool] = []

    async def request(*args: object, **kwargs: object) -> httpx.Response:
        # Bypass HTTPX's own response/BoundAsyncStream cycle; exercise our decode lifetime.
        assert all(ref() is None for ref in responses)
        response = httpx.Response(200, content=CANARY.encode())
        responses.append(weakref.ref(response))
        return response

    walker = provider(lambda request: httpx.Response(200, json=[]))
    monkeypatch.setattr(walker._client, "request", request)
    decode = walker._decode

    def track(response: httpx.Response, outcome: dict):  # type: ignore[no-untyped-def]
        try:
            return decode(response, outcome)
        except MalformedUpstreamResponse as exc:
            detached.append(exc.__cause__ is None and exc.__context__ is None)
            raise

    monkeypatch.setattr(walker, "_decode", track)
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        result = await walker.walk_jdbc_configs()
        assert all(ref() is None for ref in responses)
    finally:
        if was_enabled:
            gc.enable()
        await walker.aclose()
    assert len(responses) == 3
    assert detached == [True, True, True]
    assert result.state is JdbcWalkState.FAILED and result.failure_code is JdbcWalkFailure.MALFORMED
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        0, 0, 3, 0,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", ["empty", "ceiling", "wall", "projection"])
async def test_non_request_stops_do_not_invent_failed_pages(
    stop: str, monkeypatch: pytest.MonkeyPatch, loop_clock: LoopClock,
) -> None:
    from app.providers import qradar_rest

    project = qradar_rest.project_jdbc_config

    def expire_project(**kwargs: object):  # type: ignore[no-untyped-def]
        loop_clock.advance(1)
        return project(**kwargs)

    if stop == "projection":
        monkeypatch.setattr(qradar_rest, "project_jdbc_config", expire_project)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = [] if stop == "empty" else [row(1)] if stop == "projection" else page(request, 100)
        return httpx.Response(200, json=body)

    walker = provider(handler, max_pages=1)
    try:
        result = await walker.walk_jdbc_configs(
            wall_seconds=0 if stop == "wall" else 1 if stop == "projection" else 600,
        )
    finally:
        await walker.aclose()
    assert result.pages_failed == 0 and result.failed_page is None
    assert (result.pages_read, result.rows_read, calls) == {
        "empty": (1, 0, 1), "ceiling": (1, 100, 1), "wall": (0, 0, 0), "projection": (1, 1, 1),
    }[stop]
    assert result.failure_code is {
        "empty": None, "ceiling": JdbcWalkFailure.PAGE_CEILING,
        "wall": JdbcWalkFailure.TIMEOUT, "projection": JdbcWalkFailure.TIMEOUT,
    }[stop]
    assert result.state is {
        "empty": JdbcWalkState.COMPLETE, "wall": JdbcWalkState.FAILED,
        "ceiling": JdbcWalkState.PARTIAL, "projection": JdbcWalkState.PARTIAL,
    }[stop]


@pytest.mark.asyncio
async def test_oversized_page_rejected_before_any_projection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.providers import qradar_rest

    project = qradar_rest.project_jdbc_config
    projected: list[int] = []
    calls = 0

    def track(**kwargs: object):  # type: ignore[no-untyped-def]
        projected.append(kwargs["qradar_id"])
        return project(**kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        body = page(request, 100) if calls == 1 else [row(i) for i in range(100, 201)]
        return httpx.Response(200, json=body if calls <= 2 else [])

    monkeypatch.setattr(qradar_rest, "project_jdbc_config", track)
    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.PARTIAL
    assert result.failure_code is JdbcWalkFailure.MALFORMED
    assert result.rejected_rows == {JdbcRowRejection.MALFORMED_ROW: 101}
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        3, 201, 1, 1,
    )
    assert projected == list(range(100))
    assert [config.qradar_id for config in result.configs] == list(range(100))
    assert calls == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 422])
@pytest.mark.parametrize("safe_pages", [0, 1])
async def test_filter_rejection_ends_walk_immediately(status: int, safe_pages: int) -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return (httpx.Response(200, json=page(request, 100)) if calls <= safe_pages
                else httpx.Response(status, text=CANARY))

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.failure_code is JdbcWalkFailure.FILTER_REJECTED and not result.complete
    assert result.state is (JdbcWalkState.PARTIAL if safe_pages else JdbcWalkState.FAILED)
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        safe_pages, safe_pages * 100, 1, safe_pages,
    )
    assert len(result.configs) == safe_pages * 100 and calls == safe_pages + 1
    assert result.rejected_rows == {} and CANARY not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [404, 405, 408, 409, 429, 499])
@pytest.mark.parametrize("safe_pages", [0, 1])
async def test_other_4xx_keep_ordinary_page_failure_codes(
    status: int, safe_pages: int, caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    ranges: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers["Range"])
        return (httpx.Response(200, json=page(request, 100)) if len(ranges) <= safe_pages
                else httpx.Response(status, text=CANARY))

    walker = provider(handler)
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.failure_code is (
        JdbcWalkFailure.RATE_LIMITED if status == 429 else JdbcWalkFailure.UPSTREAM
    )
    assert not result.complete
    assert result.state is (JdbcWalkState.PARTIAL if safe_pages else JdbcWalkState.FAILED)
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        safe_pages, safe_pages * 100, 3, safe_pages,
    )
    assert [config.qradar_id for config in result.configs] == list(range(safe_pages * 100))
    assert ranges == [f"items={i * 100}-{i * 100 + 99}" for i in range(safe_pages + 3)]
    assert result.rejected_rows == {} and CANARY not in repr(result) + caplog.text
    assert all(CANARY not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.asyncio
async def test_rate_limited_pages_keep_retry_after_behavior() -> None:
    ranges: list[str] = []
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        ranges.append(request.headers["Range"])
        return httpx.Response(429, text=CANARY, headers={"Retry-After": "2"})

    async def sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler), base_url="https://qradar.example/api",
    )
    walker = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="t", client=client,
        max_retries=2, sleep=sleep,
    )
    try:
        result = await walker.walk_jdbc_configs()
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.FAILED and not result.complete
    assert result.failure_code is JdbcWalkFailure.RATE_LIMITED
    assert (result.pages_read, result.rows_read, result.pages_failed, result.failed_page) == (
        0, 0, 3, 0,
    )
    assert ranges == [f"items={i * 100}-{i * 100 + 99}" for i in range(3) for _ in range(3)]
    assert sleeps == [2.0] * 6
    assert result.configs == [] and result.rejected_rows == {} and CANARY not in repr(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("expiry", ["failed_request", "consumed_page"])
async def test_deadline_after_earlier_failure_has_honest_counts(
    expiry: str, monkeypatch: pytest.MonkeyPatch, loop_clock: LoopClock,
) -> None:
    from app.providers import qradar_rest
    from app.providers.base import ProviderError

    calls = 0
    project = qradar_rest.project_jdbc_config

    def expire_project(**kwargs: object):  # type: ignore[no-untyped-def]
        if kwargs["qradar_id"] == 200:
            loop_clock.advance(1)
        return project(**kwargs)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 2 or (calls == 3 and expiry == "failed_request"):
            return httpx.Response(500, text=CANARY)
        return httpx.Response(200, json=page(request, 100) if calls == 1 else [row(200)])

    walker = provider(handler)
    original_request = walker._request

    async def request(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        try:
            return await original_request(*args, **kwargs)
        except ProviderError:
            if calls == 3:
                loop_clock.advance(1)
            raise

    monkeypatch.setattr(walker, "_request", request)
    monkeypatch.setattr(qradar_rest, "project_jdbc_config", expire_project)
    try:
        result = await walker.walk_jdbc_configs(wall_seconds=1)
    finally:
        await walker.aclose()
    assert result.state is JdbcWalkState.PARTIAL and result.failure_code is JdbcWalkFailure.TIMEOUT
    assert result.failed_page == 1 and calls == 3
    assert (result.pages_read, result.rows_read, result.pages_failed) == (
        (1, 100, 2) if expiry == "failed_request" else (2, 101, 1)
    )
    assert [config.qradar_id for config in result.configs] == (
        list(range(100)) if expiry == "failed_request" else [*range(100), 200]
    )
