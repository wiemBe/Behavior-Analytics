"""Ariel load log: outcomes, callers, one flush, summary, CLI, and admin route."""

from __future__ import annotations

import json
import math
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.anomaly.statistics import percentile
from app.api.routes import property_watches as preview_routes
from app.cli import ariel_load as cli
from app.collectors.catchall_collector import CatchallCollector
from app.collectors.ips_collector import IpsCollector
from app.collectors.metric_collector import MetricCollector
from app.collectors.pw_collector import PwCollector
from app.collectors.rule_match_collector import RuleMatchCollector
from app.collectors.waf_endpoint_collector import WafEndpointCollector
from app.core.database import get_session
from app.geo.fold import GeoDirection
from app.i18n.messages import MessageRef, render
from app.main import create_app
from app.models.instance import QRadarInstance
from app.models.property_watch import PropertyWatch
from app.models.search import ScheduledSearch, SearchExecution
from app.providers.base import ProviderError, ProviderUnavailableError
from app.providers.dto import (
    ArielSearchHandleDTO,
    ArielSearchResultsDTO,
    ArielSearchStatusDTO,
)
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services import ariel_search_log as ariel_log
from app.services import property_watches as preview_service
from app.services.aql_validator import AQLValidationError
from app.services.ariel_errors import ArielTimeout
from app.services.ariel_permit import ArielPermitUnavailable
from app.services.search_executor import SearchExecutor
from tests.unit.test_qradar_dash_support_bundle import (
    SupportBundleFixture,
    archive_files,
)

SENTINEL = "SENTINEL_AQL_DO_NOT_STORE_9f3c"
START = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)
BASE_URL = "https://qradar.example.internal"
AQL = (
    "SELECT sourceip, COUNT(*) FROM events WHERE sourceip = "
    "'SENTINEL_AQL_DO_NOT_STORE_9f3c' GROUP BY sourceip LAST 1 HOURS"
)

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
WAF_ADMIN = Principal(subject="waf", permissions=frozenset({"admin:waf"}))
NOBODY = Principal(subject="nobody", permissions=frozenset())


@pytest.fixture(autouse=True)
def _reset_failures() -> Iterator[None]:
    ariel_log.reset_write_failures()
    yield
    ariel_log.reset_write_failures()


@asynccontextmanager
async def captured() -> AsyncIterator[list[ariel_log.ArielSearchObservation]]:
    token = ariel_log.open_run_buffer()
    assert token is not None
    found: list[ariel_log.ArielSearchObservation] = []
    try:
        yield found
    finally:
        found.extend(ariel_log.take_run_buffer(token))


def _clock(start: datetime, finished: datetime) -> Callable[[], datetime]:
    calls = {"n": 0}

    def clock() -> datetime:
        calls["n"] += 1
        return start if calls["n"] == 1 else finished

    return clock


class _Budget:
    wait_seconds = 0.25
    instance_limit = 3
    global_limit = 6
    instance_id = uuid.UUID("00000000-0000-0000-0000-000000000a11")

    def __init__(self, *, grant: bool) -> None:
        self.grant = grant
        self.enters = 0

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[object | None]:
        self.enters += 1
        yield object() if self.grant else None


class _Conn:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.statements: list[Any] = []

    def connect(self) -> _Conn:
        return self

    async def execute(self, statement: Any) -> None:
        if self.fail:
            raise RuntimeError("db down")
        self.statements.append(statement)

    async def commit(self) -> None:
        return None

    async def __aenter__(self) -> _Conn:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Session:
    def __init__(
        self,
        conn: _Conn | None = None,
        rows: dict[tuple[type[Any], uuid.UUID], Any] | None = None,
    ) -> None:
        self.bind = conn
        self._rows = rows or {}

    async def flush(self) -> None:
        return None

    async def get(self, model: type[Any], ident: uuid.UUID) -> Any:
        return self._rows.get((model, ident))


def _provider(
    handler: Callable[[httpx.Request], httpx.Response],
    budget: _Budget,
    clock: Callable[[], datetime],
) -> QRadarRestProvider:
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL + "/api",
        headers={"SEC": "test-token", "Version": "20.0", "Accept": "application/json"},
    )
    return QRadarRestProvider(
        base_url=BASE_URL,
        sec_token="test-token",
        client=client,
        permit=budget,  # type: ignore[arg-type]
        clock=clock,
        sleep=_noop,
        max_retries=0,
    )


async def _noop(_seconds: float) -> None:
    return None


def _status(name: str) -> dict[str, Any]:
    return {
        "status": name,
        "progress": 100,
        "record_count": 4,
        "query_execution_time": 1500,
        "processed_record_count": 9,
        "data_file_count": 2,
        "data_total_size": 80,
        "compressed_data_file_count": 1,
        "compressed_data_total_size": 40,
        "index_file_count": 1,
        "index_total_size": 10,
        "query_string": SENTINEL,
        "error_messages": [SENTINEL],
        "snapshot": {"raw": SENTINEL},
        "cursor_id": SENTINEL,
    }


def _handler(mode: str, posted: list[str]) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            posted.append(str(request.url.params.get("query_expression")))
            if mode == "unavailable":
                return httpx.Response(503, json={"message": SENTINEL})
            return httpx.Response(201, json={"search_id": "search-1", "status": "WAIT"})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        if path.endswith("/results"):
            query = posted[-1] if posted else ""
            if "GEO::LOOKUP" in query:
                bucket = int(START.timestamp()) // 300
                events: list[dict[str, Any]] = [{"k": bucket, "events": 3, "geo": "US"}]
            else:
                events = [{"qradar_id": 7, "event_count": 3}]
            return httpx.Response(200, json={"events": events})
        if path.endswith("/ariel/searches/search-1"):
            if mode == "timeout":
                return httpx.Response(200, json=_status("EXECUTE"))
            if mode == "error":
                return httpx.Response(200, json=_status("ERROR"))
            if mode == "canceled":
                return httpx.Response(200, json=_status("CANCELED"))
            return httpx.Response(200, json=_status("COMPLETED"))
        raise AssertionError(f"unexpected request {request.method} {path}")

    return handler


def _no_sentinel(row: ariel_log.ArielSearchObservation) -> None:
    blob = json.dumps(row.as_dict(), default=str)
    assert SENTINEL not in blob
    assert "query_string" not in row.as_dict()
    assert "aql" not in row.as_dict()


@pytest.mark.asyncio
async def test_completed_search_stores_cost_fields_and_not_the_aql() -> None:
    posted: list[str] = []
    provider = _provider(
        _handler("completed", posted),
        _Budget(grant=True),
        _clock(START, START + timedelta(milliseconds=2500)),
    )
    try:
        async with captured() as found:
            await provider.get_log_source_metrics(START, END)
    finally:
        await provider.aclose()

    assert len(found) == 1
    row = found[0]
    assert row.caller == ariel_log.CALLER_LOG_SOURCE_VOLUME
    assert row.outcome == ariel_log.OUTCOME_COMPLETED
    assert row.wall_ms == 2500
    assert row.polls == 1
    assert row.range_seconds == 300
    assert row.record_count == 1
    assert row.qradar_execution_ms == 1500
    assert row.qradar_record_count == 4
    assert row.processed_record_count == 9
    assert row.data_file_count == 2
    assert row.data_total_size == 80
    assert row.compressed_data_file_count == 1
    assert row.compressed_data_total_size == 40
    assert row.index_file_count == 1
    assert row.index_total_size == 10
    assert posted and posted[0]
    _no_sentinel(row)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "outcome", "polls", "exc"),
    [
        ("error", ariel_log.OUTCOME_ERROR, 1, ProviderError),
        ("canceled", ariel_log.OUTCOME_CANCELED, 1, ProviderError),
        ("timeout", ariel_log.OUTCOME_TIMEOUT, 120, ProviderUnavailableError),
        ("unavailable", ariel_log.OUTCOME_PROVIDER_UNAVAILABLE, 0, ProviderUnavailableError),
    ],
)
async def test_each_failed_outcome_is_recorded(
    mode: str, outcome: str, polls: int, exc: type[Exception]
) -> None:
    posted: list[str] = []
    provider = _provider(
        _handler(mode, posted),
        _Budget(grant=True),
        _clock(START, START + timedelta(milliseconds=2500)),
    )
    try:
        async with captured() as found:
            with pytest.raises(exc):
                await provider.get_log_source_metrics(START, END)
    finally:
        await provider.aclose()

    assert len(found) == 1
    assert found[0].outcome == outcome
    assert found[0].polls == polls
    assert found[0].caller == ariel_log.CALLER_LOG_SOURCE_VOLUME
    _no_sentinel(found[0])
    if mode == "unavailable":
        assert posted  # the search was attempted
    if mode == "timeout":
        # The last EXECUTE status carried the field. Results were not fetched.
        assert found[0].qradar_execution_ms == 1500
        assert found[0].record_count is None


@pytest.mark.asyncio
async def test_permit_refusal_is_recorded_and_nothing_is_sent() -> None:
    posted: list[str] = []
    provider = _provider(
        _handler("completed", posted),
        _Budget(grant=False),
        _clock(START, END),
    )
    try:
        async with captured() as found:
            with pytest.raises(ArielPermitUnavailable):
                await provider.get_log_source_metrics(START, END)
    finally:
        await provider.aclose()

    assert posted == []
    assert len(found) == 1
    assert found[0].outcome == ariel_log.OUTCOME_REFUSED
    assert found[0].caller == ariel_log.CALLER_LOG_SOURCE_VOLUME
    assert found[0].polls == 0
    assert found[0].range_seconds is None
    assert found[0].qradar_execution_ms is None


@pytest.mark.asyncio
async def test_three_collectors_pass_their_caller_on_rest_and_mock() -> None:
    posted: list[str] = []
    provider = _provider(
        _handler("completed", posted),
        _Budget(grant=True),
        _clock(START, START + timedelta(milliseconds=10)),
    )
    try:
        async with captured() as found:
            await provider.get_log_source_metrics(START, END)
            await provider.get_waf_observations(
                qradar_log_source_id=7,
                window_start=START,
                window_end=END,
                url_property=SENTINEL,
                method_property="method",
            )
            await provider.get_geo_keyed(
                START,
                END,
                direction=GeoDirection.SOURCE,
                bucket_seconds=300,
                max_rows=10,
            )
    finally:
        await provider.aclose()

    callers = [row.caller for row in found]
    assert callers == [
        ariel_log.CALLER_LOG_SOURCE_VOLUME,
        ariel_log.CALLER_WAF_COLLECTION,
        ariel_log.CALLER_WAF_COLLECTION,
        ariel_log.CALLER_GEO_COLLECTION,
    ]
    assert any(SENTINEL in aql for aql in posted)
    assert all(SENTINEL not in json.dumps(row.as_dict(), default=str) for row in found)

    mock = MockQRadarProvider()
    async with captured() as mirrored:
        await mock.get_log_source_metrics(START, END)
        await mock.get_waf_observations(
            qradar_log_source_id=7,
            window_start=START,
            window_end=END,
            url_property=SENTINEL,
            method_property="method",
        )
        await mock.get_geo_keyed(
            START,
            END,
            direction=GeoDirection.SOURCE,
            bucket_seconds=300,
            max_rows=10,
        )
    assert [row.caller for row in mirrored] == callers
    assert all(SENTINEL not in json.dumps(row.as_dict(), default=str) for row in mirrored)


@pytest.mark.asyncio
async def test_mock_notes_the_outer_search_once_and_history_wins() -> None:
    mock = MockQRadarProvider()
    async with captured() as bucketed:
        await mock.get_log_source_metrics_bucketed(
            START,
            START + timedelta(minutes=10),
            bucket_seconds=300,
            max_rows=100,
        )
    assert len(bucketed) == 1
    assert bucketed[0].caller == ariel_log.CALLER_LOG_SOURCE_VOLUME
    assert bucketed[0].range_seconds == 600

    async with captured() as breakdown:
        await mock.get_waf_breakdown(
            qradar_log_source_id=7,
            window_start=START,
            window_end=END,
            url_property="url",
            method_property="method",
            virtual_host_property=None,
            breakdown="SOURCE_IP",
            breakdown_property=None,
        )
    assert len(breakdown) == 1
    assert breakdown[0].caller == ariel_log.CALLER_WAF_COLLECTION

    async with captured() as dimensions:
        await mock.get_dimension_aggregates(
            qradar_log_source_id=7,
            window_start=START,
            window_end=END,
            dimensions=["source_ip", "destination_port"],
            top_n=5,
        )
    assert len(dimensions) == 2
    assert {row.caller for row in dimensions} == {ariel_log.CALLER_ANOMALY_EXPLANATION}

    @ariel_log.ariel_caller(ariel_log.CALLER_HISTORY)
    async def history_step() -> None:
        await mock.get_log_source_metrics(START, END)

    async with captured() as history:
        await history_step()
    assert len(history) == 1
    assert history[0].caller == ariel_log.CALLER_HISTORY


def _override_label(fn: object) -> str | None:
    closure = getattr(fn, "__closure__", None) or ()
    for cell in closure:
        if cell.cell_contents in (ariel_log.CALLER_HISTORY, ariel_log.CALLER_PREVIEW):
            return str(cell.cell_contents)
    return None


def test_history_and_preview_entries_declare_their_caller() -> None:
    history = (
        MetricCollector._history_step,
        RuleMatchCollector._history_step,
        CatchallCollector._history_step,
        WafEndpointCollector.history_volume_step,
        WafEndpointCollector.history_breakdown_step,
        IpsCollector._run_history,
        PwCollector._read_history,
    )
    assert {_override_label(fn) for fn in history} == {ariel_log.CALLER_HISTORY}
    assert _override_label(RuleMatchCollector._read_history) is None
    assert _override_label(CatchallCollector._read_history) is None
    assert _override_label(preview_service.run_preview) == ariel_log.CALLER_PREVIEW


@pytest.mark.asyncio
async def test_preview_records_preview_and_the_route_flushes_once() -> None:
    mock = MockQRadarProvider()
    watch = PropertyWatch(
        id=uuid.uuid4(),
        instance_id=uuid.uuid4(),
        name="w",
        scope_kind="LOG_SOURCE_TYPE",
        scope_type_id=1,
        properties=[{"kind": "NORMALIZED", "name": "username"}],
        pinned_values=[],
        watch_version=1,
        enabled=True,
    )
    instance = QRadarInstance(
        id=watch.instance_id,
        name="lab",
        console_host=BASE_URL,
        provider_kind="mock",
    )
    conn = _Conn()
    session = _Session(
        conn,
        {(PropertyWatch, watch.id): watch, (QRadarInstance, instance.id): instance},
    )

    async def scope_ids(*_args: object, **_kwargs: object) -> tuple[int, ...]:
        return (10,)

    async def audit(*_args: object, **_kwargs: object) -> None:
        return None

    app = create_app()

    async def _session() -> AsyncIterator[_Session]:
        yield session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: ADMIN
    app.dependency_overrides[preview_routes.get_provider_factory] = lambda: (
        lambda _instance: mock
    )
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(preview_service, "preview_scope_ids", scope_ids)
        patch.setattr(preview_routes, "record_audit", audit)
        response = TestClient(app).post(f"/api/v1/property-watches/{watch.id}/preview")
    assert response.status_code == 200, response.text
    assert len(conn.statements) == 1
    stored = _inserted(conn.statements[0])
    assert len(stored) == 2
    assert {row["caller"] for row in stored} == {ariel_log.CALLER_PREVIEW}
    assert all(SENTINEL not in json.dumps(row, default=str) for row in stored)
    assert instance.name == "lab"


def _column_name(key: object) -> str:
    name = getattr(key, "key", None)
    return name if isinstance(name, str) else str(key)


def _inserted(statement: Any) -> list[dict[str, Any]]:
    """Rows of a bulk INSERT. SQLAlchemy keys multi-values by Column, not name."""
    multi = getattr(statement, "_multi_values", ())
    if multi and isinstance(multi[0], list) and multi[0] and isinstance(multi[0][0], dict):
        raw_rows = list(multi[0])
    else:
        compiled = statement.compile(dialect=postgresql.dialect())
        params = compiled.params
        raw_rows = list(params) if isinstance(params, list) else [dict(params)]
    return [{_column_name(key): value for key, value in row.items()} for row in raw_rows]


@pytest.mark.asyncio
async def test_buffer_flushes_once_and_a_failed_write_does_not_fail_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    conn = _Conn()
    session = _Session(conn)
    mock = MockQRadarProvider()

    async def collect() -> str:
        token = ariel_log.open_run_buffer()
        try:
            await mock.get_log_source_metrics(START, END)
            await mock.get_rule_matches(START, END, max_rows=10)
            assert conn.statements == []
            return "collected"
        finally:
            await ariel_log.finish_session_buffer(token, session)  # type: ignore[arg-type]

    assert await collect() == "collected"
    assert len(conn.statements) == 1
    assert len(_inserted(conn.statements[0])) == 2

    failing = _Conn(fail=True)
    failed_session = _Session(failing)

    async def collect_anyway() -> str:
        token = ariel_log.open_run_buffer()
        try:
            await mock.get_log_source_metrics(START, END)
            return "collected"
        finally:
            await ariel_log.finish_session_buffer(token, failed_session)  # type: ignore[arg-type]

    with caplog.at_level("WARNING", logger="app.ariel_search_log"):
        assert await collect_anyway() == "collected"
    warnings = [record for record in caplog.records if record.name == "app.ariel_search_log"]
    assert len(warnings) == 1
    assert warnings[0].levelname == "WARNING"
    assert warnings[0].exc_info is None
    assert warnings[0].getMessage() == "ariel search log write failed what=flush rows=1"
    assert "db down" not in caplog.text
    assert ariel_log.write_failure_count() == 1
    assert failing.statements == []


def test_count_write_failure_increments_and_logs_the_row_count(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The counter a caller outside the module uses; the fixture resets it around every test."""
    with caplog.at_level("WARNING", logger="app.ariel_search_log"):
        ariel_log.count_write_failure(3, what="flush")
        ariel_log.count_write_failure(1, what="submit")
    assert ariel_log.write_failure_count() == 2
    assert [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.ariel_search_log"
    ] == [
        "ariel search log write failed what=flush rows=3",
        "ariel search log write failed what=submit rows=1",
    ]
    assert not hasattr(ariel_log, "_count_failure"), "the counter is public only"


@pytest.mark.asyncio
async def test_execute_flushes_once_when_the_run_fails_or_the_write_fails() -> None:
    conn = _Conn()
    session = _Session(conn)
    executor = SearchExecutor(session, MockQRadarProvider(), None, sleep=_noop)  # type: ignore[arg-type]

    async def recorded(*_args: object, **_kwargs: object) -> str:
        ariel_log.note_finished(
            clock=lambda: START,
            submitted_at=START,
            outcome=ariel_log.OUTCOME_COMPLETED,
            polls=1,
            caller=ariel_log.CALLER_SCHEDULED_SEARCH,
        )
        ariel_log.note_finished(
            clock=lambda: START,
            submitted_at=START,
            outcome=ariel_log.OUTCOME_ERROR,
            polls=2,
            caller=ariel_log.CALLER_SCHEDULED_SEARCH,
        )
        raise RuntimeError("collection failed")

    executor._execute_recorded = recorded  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="collection failed"):
        await executor.execute(ScheduledSearch(), run_key="k", instance_key="i")  # type: ignore[arg-type]
    assert len(conn.statements) == 1
    assert len(_inserted(conn.statements[0])) == 2

    ariel_log.reset_write_failures()
    bad = _Conn(fail=True)
    quiet = SearchExecutor(_Session(bad), MockQRadarProvider(), None, sleep=_noop)  # type: ignore[arg-type]

    async def ok(*_args: object, **_kwargs: object) -> str:
        ariel_log.note_finished(
            clock=lambda: START,
            submitted_at=START,
            outcome=ariel_log.OUTCOME_COMPLETED,
            polls=1,
            caller=ariel_log.CALLER_MANUAL_RUN,
        )
        return "ran"

    quiet._execute_recorded = ok  # type: ignore[method-assign]
    assert await quiet.execute(ScheduledSearch(), run_key="k", instance_key="i") == "ran"  # type: ignore[arg-type]
    assert ariel_log.write_failure_count() == 1


def _search(trigger: str) -> tuple[ScheduledSearch, SearchExecution]:
    search = ScheduledSearch(
        name=f"load-{trigger}-{uuid.uuid4()}",
        aql_query=AQL,
        query_version=4,
        schedule_cron="0 * * * *",
        timeout_seconds=30,
        max_time_range_hours=24,
        max_result_rows=100,
    )
    execution = SearchExecution(
        search_id=search.id,
        run_key="run-1",
        trigger=trigger,
        query_version=search.query_version,
    )
    return search, execution


class _Scripted:
    def __init__(self, status: str, *, create: Exception | None = None) -> None:
        self.status = status
        self.create_error = create
        self.aql: str | None = None
        self.created = 0
        self.cancelled = 0

    async def create_ariel_search(self, aql: str) -> ArielSearchHandleDTO:
        self.created += 1
        self.aql = aql
        if self.create_error is not None:
            raise self.create_error
        return ArielSearchHandleDTO(search_id="search-1", status="WAIT")

    async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
        return ArielSearchStatusDTO(
            search_id=search_id,
            status=self.status,
            record_count=4,
            query_execution_time=80,
            error_messages=[SENTINEL] if self.status == "ERROR" else [],
        )

    async def get_ariel_search_results(
        self, search_id: str, *, max_rows: int
    ) -> ArielSearchResultsDTO:
        return ArielSearchResultsDTO(
            search_id=search_id, rows=[{"count": 1}], total_count=1
        )

    async def cancel_ariel_search(self, search_id: str) -> None:
        self.cancelled += 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "outcome"),
    [
        ("COMPLETED", ariel_log.OUTCOME_COMPLETED),
        ("ERROR", ariel_log.OUTCOME_ERROR),
        ("CANCELED", ariel_log.OUTCOME_CANCELED),
    ],
)
async def test_scheduled_search_records_the_trigger_and_not_the_aql(
    status: str, outcome: str
) -> None:
    provider = _Scripted(status)
    search, execution = _search("SCHEDULED")
    executor = SearchExecutor(_Session(), provider, None, clock=lambda: START, sleep=_noop)  # type: ignore[arg-type]
    async with captured() as found:
        try:
            await executor._run_once(search, execution)
        except Exception:
            if status == "COMPLETED":
                raise
    assert provider.aql is not None and SENTINEL in provider.aql
    assert len(found) == 1
    assert found[0].caller == ariel_log.CALLER_SCHEDULED_SEARCH
    assert found[0].outcome == outcome
    assert found[0].scheduled_search_id == search.id
    assert found[0].query_version == 4
    assert found[0].range_seconds is None
    assert found[0].instance_id is None
    _no_sentinel(found[0])


@pytest.mark.asyncio
async def test_manual_run_timeout_refusal_and_skipped_triggers() -> None:
    provider = _Scripted("EXECUTE")
    search, execution = _search("MANUAL")
    search.timeout_seconds = 1
    ticks = {"n": 0}

    def clock() -> datetime:
        ticks["n"] += 1
        return START + timedelta(seconds=ticks["n"] * 10)

    executor = SearchExecutor(_Session(), provider, None, clock=clock, sleep=_noop)  # type: ignore[arg-type]
    async with captured() as found:
        with pytest.raises(ArielTimeout):
            await executor._run_once(search, execution)
    assert len(found) == 1
    assert found[0].caller == ariel_log.CALLER_MANUAL_RUN
    assert found[0].outcome == ariel_log.OUTCOME_TIMEOUT
    assert provider.cancelled == 1
    _no_sentinel(found[0])

    refused = _Scripted("COMPLETED")
    budget = _Budget(grant=False)
    search, execution = _search("SCHEDULED")
    executor = SearchExecutor(_Session(), refused, budget, clock=lambda: START, sleep=_noop)  # type: ignore[arg-type]
    async with captured() as found:
        with pytest.raises(ArielPermitUnavailable):
            await executor._run_once(search, execution)
    assert refused.created == 0
    assert len(found) == 1
    assert found[0].outcome == ariel_log.OUTCOME_REFUSED
    assert found[0].caller == ariel_log.CALLER_SCHEDULED_SEARCH

    down = _Scripted("COMPLETED", create=ProviderUnavailableError("Could not reach QRadar"))
    search, execution = _search("SCHEDULED")
    executor = SearchExecutor(_Session(), down, None, clock=lambda: START, sleep=_noop)  # type: ignore[arg-type]
    async with captured() as found:
        with pytest.raises(ProviderUnavailableError):
            await executor._run_once(search, execution)
    assert found[0].outcome == ariel_log.OUTCOME_PROVIDER_UNAVAILABLE

    search, execution = _search("REPLAY")
    executor = SearchExecutor(
        _Session(),
        _Scripted("COMPLETED"),
        None,
        clock=lambda: START,
        sleep=_noop,
    )  # type: ignore[arg-type]
    async with captured() as found:
        await executor._run_once(search, execution)
    assert found == []

    search, execution = _search("SCHEDULED")
    search.aql_query = "DELETE FROM events"
    executor = SearchExecutor(
        _Session(),
        _Scripted("COMPLETED"),
        None,
        clock=lambda: START,
        sleep=_noop,
    )  # type: ignore[arg-type]
    async with captured() as found:
        with pytest.raises(AQLValidationError):
            await executor._run_once(search, execution)
    assert found == []


def _sample_rows() -> list[ariel_log.ArielSearchObservation]:
    start = datetime(2026, 11, 2, 1, 15, tzinfo=UTC)
    walls = (10, 20, 30, 40)
    execution = (100, None, 0, 100)
    records = (1, None, 2, 3)
    sizes = (10, None, None, 5)
    outcomes = (
        ariel_log.OUTCOME_COMPLETED,
        ariel_log.OUTCOME_COMPLETED,
        ariel_log.OUTCOME_COMPLETED,
        ariel_log.OUTCOME_TIMEOUT,
    )
    return [
        ariel_log.ArielSearchObservation(
            id=uuid.uuid4(),
            instance_id=None,
            caller=ariel_log.CALLER_LOG_SOURCE_VOLUME,
            scheduled_search_id=None,
            query_version=None,
            submitted_at=start,
            finished_at=start,
            wall_ms=wall,
            qradar_execution_ms=exec_ms,
            qradar_record_count=None,
            processed_record_count=None,
            data_file_count=None,
            data_total_size=size,
            compressed_data_file_count=None,
            compressed_data_total_size=None,
            index_file_count=None,
            index_total_size=None,
            record_count=record,
            range_seconds=300,
            polls=1,
            outcome=outcome,
        )
        for wall, exec_ms, record, size, outcome in zip(
            walls, execution, records, sizes, outcomes, strict=True
        )
    ]


def _sample_summary() -> dict[str, Any]:
    start = datetime(2026, 11, 2, 0, 30, tzinfo=UTC)
    end = datetime(2026, 11, 2, 2, 30, tzinfo=UTC)
    return ariel_log.summarize_runs(_sample_rows(), start=start, end=end, hours=2)


def test_summary_math_percentiles_nulls_and_empty_window() -> None:
    summary = _sample_summary()
    assert summary["empty"] is False
    caller = summary["callers"][0]
    assert caller["searches"] == 4
    assert caller["outcomes"] == {"COMPLETED": 3, "TIMEOUT": 1}
    # Percentiles and max cover the three COMPLETED rows (10, 20, 30 ms); the
    # TIMEOUT's 40 ms is a failure time, not how long a search takes.
    assert caller["wall_ms_p50"] == 20
    assert caller["wall_ms_p95"] == 29
    assert caller["wall_ms_max"] == 30
    assert caller["qradar_execution_ms_p50"] == 50  # COMPLETED rows: 100 and a measured 0
    assert caller["qradar_execution_ms_p95"] == 95
    # Sums are the load: every row counts, the TIMEOUT's 40 ms wall and its
    # partial 100 ms of execution included.
    assert caller["wall_ms_sum"] == 100
    assert caller["qradar_execution_ms_sum"] == 200
    assert caller["qradar_execution_ms_unknown"] == 1
    assert caller["records_returned_sum"] == 6
    assert caller["records_returned_unknown"] == 1
    assert caller["data_total_size_sum"] == 15
    assert caller["data_total_size_unknown"] == 2
    assert [point["searches"] for point in summary["series"]] == [0, 4, 0]
    assert summary["series"][0]["qradar_execution_ms_sum"] is None
    assert summary["series"][1]["qradar_execution_ms_sum"] == 200
    assert summary["series"][1]["qradar_execution_ms_unknown"] == 1

    missing = ariel_log.ArielSearchObservation(
        id=uuid.uuid4(),
        instance_id=None,
        caller=ariel_log.CALLER_GEO_COLLECTION,
        scheduled_search_id=None,
        query_version=None,
        submitted_at=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
        wall_ms=5,
        qradar_execution_ms=None,
        qradar_record_count=None,
        processed_record_count=None,
        data_file_count=None,
        data_total_size=None,
        compressed_data_file_count=None,
        compressed_data_total_size=None,
        index_file_count=None,
        index_total_size=None,
        record_count=None,
        range_seconds=None,
        polls=0,
        outcome=ariel_log.OUTCOME_TIMEOUT,
    )
    measured_zero = ariel_log.ArielSearchObservation(
        id=uuid.uuid4(),
        instance_id=None,
        caller=ariel_log.CALLER_IPS_COLLECTION,
        scheduled_search_id=None,
        query_version=None,
        submitted_at=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
        finished_at=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
        wall_ms=5,
        qradar_execution_ms=0,
        qradar_record_count=None,
        processed_record_count=None,
        data_file_count=None,
        data_total_size=0,
        compressed_data_file_count=None,
        compressed_data_total_size=None,
        index_file_count=None,
        index_total_size=None,
        record_count=0,
        range_seconds=None,
        polls=1,
        outcome=ariel_log.OUTCOME_COMPLETED,
    )
    both = ariel_log.summarize_runs(
        [missing, measured_zero],
        start=datetime(2026, 11, 2, 0, 30, tzinfo=UTC),
        end=datetime(2026, 11, 2, 2, 30, tzinfo=UTC),
        hours=2,
    )
    by_name = {item["caller"]: item for item in both["callers"]}
    assert by_name[ariel_log.CALLER_GEO_COLLECTION]["qradar_execution_ms_sum"] is None
    assert by_name[ariel_log.CALLER_GEO_COLLECTION]["qradar_execution_ms_unknown"] == 1
    assert by_name[ariel_log.CALLER_IPS_COLLECTION]["qradar_execution_ms_sum"] == 0
    assert by_name[ariel_log.CALLER_IPS_COLLECTION]["qradar_execution_ms_unknown"] == 0

    empty = ariel_log.summarize_runs(
        [],
        start=datetime(2026, 11, 2, 0, 30, tzinfo=UTC),
        end=datetime(2026, 11, 2, 2, 30, tzinfo=UTC),
        hours=2,
    )
    assert empty["empty"] is True
    assert empty["callers"] == []
    assert empty["series"] == []
    assert empty["message"] == "No Ariel search was recorded in this window."
    assert empty["message_code"] == ariel_log.EMPTY_CODE
    assert "searches" not in empty


def test_sql_percentiles_round_half_to_even_like_the_python_fold() -> None:
    """The SQL path rounds its percentiles in Python, never in the query.

    A percentile that lands exactly on .5 is where the two roundings differ:
    Python takes the tie to even, PostgreSQL's round takes it away from zero.
    """
    ties = (0.5, 1.5, 2.5, 9.5, 10.5, 11.5, 12.5)
    assert [ariel_log._rounded_ms(value) for value in ties] == [0, 2, 2, 10, 10, 12, 12]
    # What SQL round would answer on the same values — never this.
    assert [math.floor(value + 0.5) for value in ties] == [1, 2, 3, 10, 11, 12, 13]
    assert ariel_log._rounded_ms(None) is None
    # The ties the fixture builds, from the Python definition itself: two
    # completed walls 10 and 11 make the p50 exactly 10.5; eleven walls 1..11
    # make the p95 exactly 10.5.
    for values, q in (([10, 11], 50), (list(range(1, 12)), 95)):
        floats = [float(value) for value in values]
        tie = percentile(floats, q)
        assert tie == int(tie) + 0.5
        assert ariel_log._rounded_ms(tie) == ariel_log._percentile_ms(values, q)


def test_cli_table_and_json(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = _sample_summary()

    async def load(hours: int) -> dict[str, Any]:
        assert hours == 2
        return summary

    monkeypatch.setattr(cli, "_load", load)
    assert cli.main(["--hours", "2"]) == 0
    table = capsys.readouterr().out
    assert table == cli.render_table(summary) + "\n"
    assert (
        "LOG_SOURCE_VOLUME\t4\tCOMPLETED:3,TIMEOUT:1\t20\t29\t30\t100\t50\t95\t200\t1\t6\t1\t15\t2"
    ) in table
    assert "\t0\tnull\t0" in table
    assert SENTINEL not in table

    assert cli.main(["--hours", "2", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == summary


def test_cli_says_when_the_window_is_empty(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    empty = ariel_log.summarize_runs(
        [],
        start=datetime(2026, 11, 2, 0, 30, tzinfo=UTC),
        end=datetime(2026, 11, 2, 2, 30, tzinfo=UTC),
        hours=2,
    )

    async def load(_hours: int) -> dict[str, Any]:
        return empty

    monkeypatch.setattr(cli, "_load", load)
    assert cli.main(["--hours", "2"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("No Ariel search was recorded in this window.\n")
    assert "0\t0" not in text


def test_cli_refuses_bad_hours_and_hides_load_errors(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    for raw in ("0", "721", "no", "1.5", "-1", "0000"):
        assert cli.main(["--hours", raw]) == 2
        err = capsys.readouterr().err
        assert err == "hours must be a whole number from 1 to 720\n"
        assert "Traceback" not in err

    async def boom(_hours: int) -> dict[str, Any]:
        raise RuntimeError(SENTINEL)

    monkeypatch.setattr(cli, "_load", boom)
    assert cli.main(["--hours", "24"]) == 2
    err = capsys.readouterr().err
    assert err == "ariel load summary could not be read\n"
    assert SENTINEL not in err
    assert "Traceback" not in err


def _api_client(principal: Principal) -> TestClient:
    app = create_app()

    async def _session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_admin_route_permission_and_window_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int] = []

    async def load(_session: object, *, hours: int, now: datetime | None = None) -> dict[str, Any]:
        seen.append(hours)
        if hours == 1:
            return ariel_log.summarize_runs(
                [],
                start=datetime(2026, 11, 2, 0, 0, tzinfo=UTC),
                end=datetime(2026, 11, 2, 1, 0, tzinfo=UTC),
                hours=1,
            )
        return _sample_summary()

    monkeypatch.setattr("app.api.routes.ariel_load.load_summary", load)
    denied = (
        _api_client(READER).get("/api/v1/admin/ariel-load"),
        _api_client(WAF_ADMIN).get("/api/v1/admin/ariel-load"),
        _api_client(NOBODY).get("/api/v1/admin/ariel-load"),
    )
    for response in denied:
        assert response.status_code == 403
        body = response.json()
        assert body["detail_code"] == "auth.permission.missing"
        assert render(MessageRef(body["detail_code"], body["detail_params"])) == body["detail"]
    assert seen == []

    client = _api_client(ADMIN)
    for raw in ("0", "721", "no", "1.5", "-1"):
        response = client.get("/api/v1/admin/ariel-load", params={"hours": raw})
        assert response.status_code == 400
        body = response.json()
        assert body["detail_code"] == "ariel_load.hours.invalid"
        assert body["detail"] == "hours must be a whole number from 1 to 720"
    assert seen == []

    empty = client.get("/api/v1/admin/ariel-load", params={"hours": "01"})
    assert empty.status_code == 200
    assert empty.json()["empty"] is True
    assert empty.json()["callers"] == []
    assert empty.json()["series"] == []
    assert seen == [1]

    filled = client.get("/api/v1/admin/ariel-load", params={"hours": "720"})
    assert filled.status_code == 200
    assert filled.json()["empty"] is False
    assert seen == [1, 720]


def test_support_bundle_includes_ariel_load_and_survives_a_failed_read(tmp_path: Any) -> None:
    support = SupportBundleFixture(tmp_path)
    result = support.run()
    assert result.returncode == 0, result.stdout + result.stderr
    files = archive_files(support.archives()[0])
    body = files["ariel-load.json"]
    assert b"No Ariel search was recorded in this window." in body
    assert b"SELECT" not in body
    assert b"SENTINEL" not in body
    assert support.staging_directories() == []

    failed_root = tmp_path / "failed"
    failed_root.mkdir()
    failed = SupportBundleFixture(failed_root)
    result = failed.run(STUB_ARIEL_RC="9")
    assert result.returncode == 0, result.stdout + result.stderr
    files = archive_files(failed.archives()[0])
    assert "ariel-load.json" not in files
    text = files["ariel-load.error.txt"]
    assert text == (
        b"ariel load summary could not be read; "
        b"inspect ./qradar-dash doctor and the service logs.\n"
    )
    assert b"SELECT" not in text
    assert failed.staging_directories() == []


@pytest.mark.asyncio
async def test_a_session_with_no_bind_logs_what_lookup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = ariel_log.open_run_buffer()
    session = _Session(None)
    mock = MockQRadarProvider()
    await mock.get_log_source_metrics(START, END)
    with caplog.at_level("WARNING", logger="app.ariel_search_log"):
        await ariel_log.finish_session_buffer(token, session)  # type: ignore[arg-type]
    assert [
        r.getMessage() for r in caplog.records if r.name == "app.ariel_search_log"
    ] == ["ariel search log write failed what=lookup rows=1"]
    assert ariel_log.write_failure_count() == 1
