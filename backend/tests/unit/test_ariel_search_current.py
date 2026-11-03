"""Ariel load, round 1: the mock notes what REST sends, in-flight rows, the current read."""

from __future__ import annotations

import contextlib
import inspect
import json
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.cli import ariel_load as cli
from app.core.database import get_session
from app.main import create_app
from app.providers.base import ProviderError, ProviderUnavailableError
from app.providers.dto import (
    ArielSearchStatusDTO,
    IpsFieldProbeRequest,
)
from app.providers.mock import MockQRadarProvider
from app.providers.qradar_rest import QRadarRestProvider
from app.security.auth import get_principal
from app.security.rbac import Principal
from app.services import ariel_permit
from app.services import ariel_search_log as ariel_log
from app.services.ariel_permit import ArielPermitCounts

START = datetime(2026, 10, 22, 10, 0, tzinfo=UTC)
END = datetime(2026, 10, 22, 10, 5, tzinfo=UTC)
HOUR = START + timedelta(hours=1)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
BASE_URL = "https://qradar.example.internal"

C = ariel_log.OUTCOME_COMPLETED
E = ariel_log.OUTCOME_ERROR
WAF = ariel_log.CALLER_WAF_COLLECTION
IPS = ariel_log.CALLER_IPS_COLLECTION

ADMIN = Principal(subject="admin", permissions=frozenset({"admin:*"}))
READER = Principal(subject="reader", permissions=frozenset({"read:*"}))
WAF_ADMIN = Principal(subject="waf", permissions=frozenset({"admin:waf"}))


@asynccontextmanager
async def captured() -> AsyncIterator[list[ariel_log.ArielSearchObservation]]:
    token = ariel_log.open_run_buffer()
    assert token is not None
    found: list[ariel_log.ArielSearchObservation] = []
    try:
        yield found
    finally:
        found.extend(ariel_log.take_run_buffer(token))


def _pairs(rows: list[ariel_log.ArielSearchObservation]) -> list[tuple[str, str]]:
    return [(row.caller, row.outcome) for row in rows]


class _Budget:
    wait_seconds = 0.25
    instance_limit = 3
    global_limit = 6
    instance_id = uuid.UUID("00000000-0000-0000-0000-000000000a11")

    @asynccontextmanager
    async def permit(self) -> AsyncIterator[object | None]:
        yield object()


async def _noop(_seconds: float) -> None:
    return None


def _rest(rows: int = 1) -> QRadarRestProvider:
    """A REST provider whose every search completes and returns ``rows`` rows."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            return httpx.Response(201, json={"search_id": "s-1", "status": "WAIT"})
        if request.method == "DELETE":
            return httpx.Response(200, json={})
        if path.endswith("/results"):
            return httpx.Response(
                200, json={"events": [{"qradar_id": 7, "event_count": 3}] * rows}
            )
        return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=BASE_URL + "/api",
        headers={"SEC": "t", "Version": "20.0", "Accept": "application/json"},
    )
    return QRadarRestProvider(
        base_url=BASE_URL,
        sec_token="t",
        client=client,
        permit=_Budget(),  # type: ignore[arg-type]
        sleep=_noop,
        max_retries=0,
    )


# --------------------------------------------------------------- mock == REST
_WINDOW = {"window_start": START, "window_end": END}
_WAF_KEYS = {
    "qradar_log_source_id": 7,
    "url_property": "url",
    "method_property": "method",
}
_NORMALIZED_QID = IpsFieldProbeRequest(
    logical_field="SIGNATURE", reference_kind="NORMALIZED", name="qid"
)
_ACTION = IpsFieldProbeRequest(
    logical_field="ACTION", reference_kind="CUSTOM_PROPERTY", name="IPS_Action"
)
_OFF_LIST = IpsFieldProbeRequest(
    logical_field="SEVERITY", reference_kind="NORMALIZED", name="not_a_field"
)
_REJECTED_PROPERTY = IpsFieldProbeRequest(
    logical_field="SEVERITY", reference_kind="CUSTOM_PROPERTY", name="NotAllowed"
)

# (id, method, kwargs, searches both providers send, REST rows to return or None)
# A case with ``rest_kwargs`` gives REST the call that reaches the same point there.
_PAIRS: list[tuple[str, str, dict[str, Any], list[tuple[str, str]], int | None]] = [
    (
        "volume",
        "get_log_source_metrics",
        {"window_start": START, "window_end": END},
        [(ariel_log.CALLER_LOG_SOURCE_VOLUME, C)],
        1,
    ),
    (
        "explanation: only mapped dimensions search",
        "get_dimension_aggregates",
        {
            "qradar_log_source_id": 7,
            **_WINDOW,
            "dimensions": ["source_ip", "not_a_dimension", "destination_port"],
            "top_n": 5,
        },
        [(ariel_log.CALLER_ANOMALY_EXPLANATION, C)] * 2,
        1,
    ),
    (
        "explanation: nothing mapped sends nothing",
        "get_dimension_aggregates",
        {"qradar_log_source_id": 7, **_WINDOW, "dimensions": ["nope"], "top_n": 5},
        [],
        1,
    ),
    (
        "waf observations: grouped then totals",
        "get_waf_observations",
        {**_WAF_KEYS, **_WINDOW, "max_groups": 5},
        [(WAF, C), (WAF, C)],
        1,
    ),
    (
        "waf bucketed: ValueError before any search",
        "get_waf_observations_bucketed",
        {**_WAF_KEYS, **_WINDOW, "bucket_seconds": 300, "max_rows": 0},
        [],
        None,
    ),
    (
        "waf bucketed: a truncated grouped page skips the totals search",
        "get_waf_observations_bucketed",
        {**_WAF_KEYS, **_WINDOW, "bucket_seconds": 60, "max_rows": 1},
        [(WAF, C)],
        3,
    ),
    (
        "waf probe: an invalid name sends nothing",
        "probe_waf_properties",
        {"qradar_log_source_id": 7, **_WINDOW, "property_names": ["bad name!"]},
        [(WAF, C)],
        1,
    ),
    (
        "waf rejection probe: an empty list sends nothing",
        "observe_property_rejection",
        {"qradar_log_source_id": 7, **_WINDOW, "property_names": []},
        [],
        1,
    ),
    (
        "waf rejection probe: an invalid name is not sent",
        "observe_property_rejection",
        {"qradar_log_source_id": 7, **_WINDOW, "property_names": ["bad name!"]},
        None,
        None,
    ),
    (
        "ips probe: total, QID population plus its extra, ACTION population plus values;"
        " a field off the allowlist sends nothing",
        "probe_ips_fields",
        {
            "qradar_log_source_id": 7,
            **_WINDOW,
            "fields": [_NORMALIZED_QID, _ACTION, _OFF_LIST],
        },
        [(IPS, C)] * 5,
        1,
    ),
    (
        "ips probe: a custom property QRadar rejects was sent and failed",
        "probe_ips_fields",
        {"qradar_log_source_id": 7, **_WINDOW, "fields": [_REJECTED_PROPERTY]},
        [(IPS, C), (IPS, E)],
        None,
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("method", "kwargs", "expected", "rest_rows"),
    [pytest.param(m, k, x, r, id=name) for name, m, k, x, r in _PAIRS if x is not None],
)
async def test_mock_notes_exactly_the_searches_rest_sends(
    method: str,
    kwargs: dict[str, Any],
    expected: list[tuple[str, str]],
    rest_rows: int | None,
) -> None:
    mock = MockQRadarProvider(seed=1)
    async with captured() as from_mock:
        with contextlib.suppress(ValueError):
            await getattr(mock, method)(**kwargs)
    assert _pairs(from_mock) == expected

    if rest_rows is None:
        return
    rest = _rest(rows=rest_rows)
    async with captured() as from_rest:
        with contextlib.suppress(ValueError, ProviderError):
            await getattr(rest, method)(**kwargs)
    assert _pairs(from_rest) == _pairs(from_mock), "REST and the mock disagree"


@pytest.mark.asyncio
async def test_rest_notes_nothing_for_an_error_raised_before_any_search() -> None:
    async with captured() as rows:
        with pytest.raises(ValueError, match="unsafe"):
            await _rest().get_waf_observations_bucketed(
                **{**_WAF_KEYS, "url_property": "bad name!"},
                **_WINDOW,
                bucket_seconds=300,
                max_rows=10,
            )
        with pytest.raises(ValueError, match="bound"):
            await _rest().get_dimension_aggregates(
                qradar_log_source_id=7,
                window_start=START,
                window_end=START + timedelta(hours=200),
                dimensions=["source_ip"],
                top_n=5,
            )
    assert rows == []


@pytest.mark.asyncio
async def test_mock_rejection_probe_notes_per_observed_outcome() -> None:
    """Known name completes, an unknown valid name is rejected at create (ERROR),
    a name REST would refuse before sending is not a search at all."""
    mock = MockQRadarProvider(seed=1)
    known = next(iter(MockQRadarProvider._MOCK_WAF_PROPERTY_VALUES))
    async with captured() as rows:
        await mock.observe_property_rejection(
            qradar_log_source_id=7,
            **_WINDOW,
            property_names=[known, "Unknown_But_Valid", "bad name!"],
        )
    assert _pairs(rows) == [(WAF, C), (WAF, E)]

    async with captured() as probed:
        await mock.probe_waf_properties(
            qradar_log_source_id=7,
            **_WINDOW,
            property_names=[known, "Unknown_But_Valid", "bad name!", known],
        )
    # total; the known name (population + values, once: names are de-duplicated);
    # the unknown valid name rejected at its population search; the invalid name none.
    assert _pairs(probed) == [(WAF, C), (WAF, C), (WAF, C), (WAF, E)]


@pytest.mark.asyncio
async def test_mock_ips_probe_is_noted_as_ips_collection() -> None:
    mock = MockQRadarProvider(seed=1)
    async with captured() as rows:
        await mock.probe_ips_fields(
            qradar_log_source_id=7, **_WINDOW, fields=[_NORMALIZED_QID]
        )
    assert _pairs(rows) == [(IPS, C), (IPS, C), (IPS, C)]


@pytest.mark.asyncio
async def test_mock_faults_note_one_failed_search_and_argument_errors_note_none() -> None:
    mock = MockQRadarProvider(seed=1)
    call = {
        "devicetype_ids": [1],
        "window_start": START,
        "window_end": END,
        "bucket_seconds": 300,
        "max_rows": 10,
    }
    sender = ariel_log.CALLER_CATCHALL_SENDERS
    async with captured() as ok:
        await mock.get_catchall_senders_keyed(**call)  # type: ignore[arg-type]
    assert _pairs(ok) == [(sender, C)]

    mock.catchall_fault = lambda _search: ProviderError("boom")
    async with captured() as failed:
        with pytest.raises(ProviderError):
            await mock.get_catchall_senders_keyed(**call)  # type: ignore[arg-type]
    assert _pairs(failed) == [(sender, E)]

    mock.catchall_fault = lambda _search: ProviderUnavailableError("QRadar search timed out")
    async with captured() as slow:
        with pytest.raises(ProviderUnavailableError):
            await mock.get_catchall_senders_keyed(**call)  # type: ignore[arg-type]
    assert _pairs(slow) == [(sender, ariel_log.OUTCOME_TIMEOUT)]

    mock.catchall_fault = None
    async with captured() as invalid:
        with pytest.raises(ValueError, match="positive"):
            await mock.get_catchall_senders_keyed(**{**call, "max_rows": 0})  # type: ignore[arg-type]
    assert invalid == []


def test_the_pair_table_covers_the_cases_the_spec_names() -> None:
    names = " ".join(name for name, *_ in _PAIRS)
    for needle in (
        "ValueError before any search",
        "only mapped dimensions",
        "invalid name sends nothing",
        "empty list sends nothing",
        "truncated grouped page",
        "ips probe",
    ):
        assert needle in names


# ------------------------------------------------------- no test switch in code
def test_the_log_has_no_test_switch() -> None:
    source = inspect.getsource(ariel_log)
    assert not hasattr(ariel_log, "_worker_flush_allowed")
    assert "sys.modules" not in source
    assert '"pytest"' not in source


def test_the_suite_points_the_worker_engine_at_nothing_by_default() -> None:
    assert ariel_log.worker_engine() is None


# ------------------------------------------------------------ in-flight rows
class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Engine:
    """Records the statements the log sends on its own connection."""

    def __init__(self, *, update_rowcount: int = 1, fail_insert: bool = False) -> None:
        self.update_rowcount = update_rowcount
        self.fail_insert = fail_insert
        self.statements: list[tuple[str, dict[str, Any]]] = []

    def connect(self) -> _Engine:
        return self

    async def __aenter__(self) -> _Engine:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: Any) -> _Result:
        kind = type(statement).__name__
        if kind == "Insert" and self.fail_insert:
            raise RuntimeError("db down")
        self.statements.append((kind, dict(statement.compile().params)))
        return _Result(self.update_rowcount if kind == "Update" else 1)

    async def commit(self) -> None:
        return None

    def kinds(self) -> list[str]:
        return [kind for kind, _ in self.statements]


def _ticking(*moments: datetime) -> Callable[[], datetime]:
    queue = list(moments)

    def clock() -> datetime:
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return clock


@pytest.fixture(autouse=True)
def _reset_failures() -> Any:
    ariel_log.reset_write_failures()
    yield
    ariel_log.reset_write_failures()


@pytest.mark.asyncio
async def test_a_search_is_written_in_flight_at_submit_and_closed_at_its_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: engine)
    clock = _ticking(START, START + timedelta(milliseconds=1500))
    async with captured() as buffered, ariel_log.track_search(
        clock=clock, instance_id=None, caller=ariel_log.CALLER_GEO_COLLECTION
    ) as attempt:
        # Inside the search: the row exists, open, with no wall and no finish.
        assert engine.kinds() == ["Insert"]
        opened = engine.statements[0][1]
        assert opened["outcome"] == ariel_log.OUTCOME_IN_FLIGHT
        assert opened["caller"] == ariel_log.CALLER_GEO_COLLECTION
        assert opened["wall_ms"] is None and opened["finished_at"] is None
        await attempt.saw(ArielSearchStatusDTO(search_id="s", status="COMPLETED"))
    assert engine.kinds() == ["Insert", "Update"]
    closed = engine.statements[1][1]
    assert closed["outcome"] == C and closed["wall_ms"] == 1500
    assert buffered == [], "a row closed at its end is not written again by the flush"


@pytest.mark.asyncio
async def test_a_row_that_could_not_be_closed_is_left_to_the_flush(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine(update_rowcount=0)
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: engine)
    async with captured() as buffered:
        with pytest.raises(ProviderError):
            async with ariel_log.track_search(
                clock=_ticking(START, END), instance_id=None, caller=IPS
            ):
                raise ProviderError("search failed")
    assert engine.kinds() == ["Insert", "Update"]
    [row] = buffered
    assert row.outcome == E and row.caller == IPS
    assert row.id == uuid.UUID(str(engine.statements[0][1]["id"]))


@pytest.mark.asyncio
async def test_a_failed_submit_write_is_counted_and_the_search_still_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine(fail_insert=True)
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: engine)
    async with captured() as buffered, ariel_log.track_search(
        clock=_ticking(START, END), instance_id=None, caller=IPS
    ):
        pass
    assert ariel_log.write_failure_count() == 1
    assert engine.kinds() == []
    [row] = buffered
    assert row.outcome == C, "with no open row the terminal note carries the whole record"


@pytest.mark.asyncio
async def test_nothing_is_written_without_a_buffer_or_a_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: engine)
    async with ariel_log.track_search(
        clock=_ticking(START, END), instance_id=None, caller=IPS
    ):
        pass
    assert engine.kinds() == [], "no run buffer is open, so nothing is written"
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: None)
    async with captured() as buffered, ariel_log.track_search(
        clock=_ticking(START, END), instance_id=None, caller=IPS
    ):
        pass
    assert len(buffered) == 1 and ariel_log.write_failure_count() == 0


@pytest.mark.asyncio
async def test_the_poll_count_is_refreshed_on_every_fifth_status_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: engine)
    status = ArielSearchStatusDTO(search_id="s", status="EXECUTE")
    async with captured(), ariel_log.track_search(
        clock=_ticking(START, END), instance_id=None, caller=IPS
    ) as attempt:
        for _ in range(11):
            await attempt.saw(status)
        refreshed = [params["polls"] for kind, params in engine.statements if kind == "Update"]
    assert refreshed == [5, 10]


# ---------------------------------------------------------- the current load
class _Rows:
    def __init__(self, rows: list[Any]) -> None:
        self._rows = rows

    def all(self) -> list[Any]:
        return self._rows


class _Reads:
    """Stands in for the session: one canned answer per read, or a failure."""

    def __init__(
        self,
        in_flight: list[Any] | None = None,
        recent: list[Any] | None = None,
        *,
        fail: tuple[str, ...] = (),
    ) -> None:
        self.in_flight = in_flight or []
        self.recent = recent or []
        self.fail = fail
        self.reads = 0

    async def scalars(self, _statement: Any) -> _Rows:
        self.reads += 1
        if "in_flight" in self.fail:
            raise RuntimeError("db down")
        return _Rows(self.in_flight)

    async def execute(self, _statement: Any) -> _Rows:
        self.reads += 1
        if "recent" in self.fail:
            raise RuntimeError("db down")
        return _Rows(self.recent)


def _recent(
    minutes_ago: float,
    outcome: str,
    wall: int | None,
    execution: int | None = None,
    records: int | None = None,
    caller: str = "RULE_MATCH",
) -> SimpleNamespace:
    return SimpleNamespace(
        submitted_at=NOW - timedelta(minutes=minutes_ago),
        caller=caller,
        outcome=outcome,
        wall_ms=wall,
        qradar_execution_ms=execution,
        record_count=records,
    )


def _flying(seconds_ago: int, caller: str = IPS) -> SimpleNamespace:
    return SimpleNamespace(
        caller=caller,
        instance_id=uuid.UUID("00000000-0000-0000-0000-000000000a11"),
        submitted_at=NOW - timedelta(seconds=seconds_ago),
        polls=4,
        range_seconds=3600,
    )


@pytest.fixture
def permit_pool(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """The permit read the doctor makes, answered from a dict."""
    answer: dict[str, Any] = {
        "counts": ArielPermitCounts(
            global_held=2,
            instance_held=1,
            global_cap=6,
            instance_cap=3,
            reserved_live_slots=2,
        ),
        "error": None,
    }

    class _Pool:
        async def active_permits(self, _session: Any) -> ArielPermitCounts | None:
            if answer["error"] is not None:
                raise answer["error"]
            counts: ArielPermitCounts | None = answer["counts"]
            return counts

    monkeypatch.setattr(
        ariel_permit.ArielPermitBudget,
        "for_session",
        classmethod(lambda cls, *_a, **_k: _Pool()),
    )
    return answer


@pytest.mark.asyncio
async def test_the_current_read_lists_what_is_in_flight_and_splits_off_stale_rows(
    permit_pool: dict[str, Any],
) -> None:
    limit = ariel_log.stale_after_seconds()
    reads = _Reads(
        in_flight=[_flying(12), _flying(limit), _flying(limit + 1), _flying(5000, "HISTORY")]
    )
    current = await ariel_log.load_current(reads, now=NOW)  # type: ignore[arg-type]
    flight = current["in_flight"]
    assert current["as_of"] == NOW.isoformat()
    assert [entry["age_seconds"] for entry in flight["active"]] == [12, limit]
    assert [entry["age_seconds"] for entry in flight["stale"]] == [limit + 1, 5000]
    assert flight["stale_after_seconds"] == limit
    first = flight["active"][0]
    assert first == {
        "caller": IPS,
        "instance_id": "00000000-0000-0000-0000-000000000a11",
        "submitted_at": (NOW - timedelta(seconds=12)).isoformat(),
        "age_seconds": 12,
        "polls": 4,
        "range_seconds": 3600,
    }
    assert reads.reads == 2, "a fixed number of reads: in flight, then the last hour"


def test_a_row_is_stale_only_after_the_longest_search_the_application_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A scheduled search may poll for ARIEL_MAX_TIMEOUT_SECONDS; it is not lost at two minutes."""
    from app.core.config import get_settings

    default = get_settings().ariel_max_timeout_seconds
    assert ariel_log.stale_after_seconds() == max(ariel_log.POLL_LIMIT, default) + 30
    assert ariel_log.stale_after_seconds() > ariel_log.POLL_LIMIT + 30
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(ariel_max_timeout_seconds=60),
    )
    assert ariel_log.stale_after_seconds() == ariel_log.POLL_LIMIT + 30
    monkeypatch.setattr(
        "app.core.config.get_settings",
        lambda: SimpleNamespace(ariel_max_timeout_seconds=1800),
    )
    assert ariel_log.stale_after_seconds() == 1830


@pytest.mark.asyncio
async def test_current_windows_count_by_submit_time_and_do_the_percentile_and_sum_rules(
    permit_pool: dict[str, Any],
) -> None:
    recent = [
        _recent(2, C, 100, 50, 5),
        _recent(3, C, 300, None, None),
        _recent(4, ariel_log.OUTCOME_TIMEOUT, 9000, 700, None),
        _recent(1, ariel_log.OUTCOME_IN_FLIGHT, None),
        _recent(10, E, 50, 5, None),
        _recent(20, ariel_log.OUTCOME_REFUSED, 0, None),
        _recent(50, C, 1000, 200, 10),
    ]
    windows = (await ariel_log.load_current(_Reads(recent=recent), now=NOW))[  # type: ignore[arg-type]
        "windows"
    ]
    five, fifteen, sixty = windows["minutes_5"], windows["minutes_15"], windows["minutes_60"]
    assert windows["available"] is True
    assert (five["submitted"], five["completed"], five["failed"]) == (4, 2, 1)
    assert (five["refused"], five["in_flight"]) == (0, 1)
    # Percentiles: the two COMPLETED rows only (100 and 300 ms); the TIMEOUT's 9 s is not one.
    assert (five["wall_ms_p50"], five["wall_ms_p95"]) == (200, 290)
    assert (five["qradar_execution_ms_p50"], five["qradar_execution_ms_p95"]) == (50, 50)
    # Sums: every finished row, the TIMEOUT's wall and partial execution included;
    # the open row has nothing to add yet; the unreported execution is counted, not 0.
    assert five["wall_ms_sum"] == 100 + 300 + 9000
    assert five["qradar_execution_ms_sum"] == 50 + 700
    assert five["qradar_execution_ms_unknown"] == 1
    assert (five["records_returned_sum"], five["records_returned_unknown"]) == (5, 2)
    assert (fifteen["submitted"], fifteen["failed"], fifteen["wall_ms_sum"]) == (5, 2, 9450)
    assert (sixty["submitted"], sixty["completed"], sixty["refused"]) == (7, 3, 1)
    assert sixty["wall_ms_p50"] == 300


@pytest.mark.asyncio
async def test_a_cancelled_access_probe_is_not_failed_but_any_other_cancel_is(
    permit_pool: dict[str, Any],
) -> None:
    cancelled, error = ariel_log.OUTCOME_CANCELED, ariel_log.OUTCOME_ERROR
    recent = [
        _recent(1, cancelled, 10, caller="ACCESS_CHECK"),
        _recent(2, cancelled, 10, caller="ACCESS_CHECK"),
        _recent(3, error, 10, caller="ACCESS_CHECK"),
        _recent(4, cancelled, 10, caller="HISTORY"),
    ]
    windows = (await ariel_log.load_current(_Reads(recent=recent), now=NOW))[  # type: ignore[arg-type]
        "windows"
    ]
    # The probe's two cancels are its normal end; its error and another caller's cancel fail.
    assert (windows["minutes_5"]["submitted"], windows["minutes_5"]["failed"]) == (4, 2)


@pytest.mark.asyncio
async def test_an_empty_hour_reports_unknown_not_zero_sums(
    permit_pool: dict[str, Any],
) -> None:
    windows = (await ariel_log.load_current(_Reads(), now=NOW))["windows"]  # type: ignore[arg-type]
    for name in ("minutes_5", "minutes_15", "minutes_60"):
        window = windows[name]
        assert window["submitted"] == 0
        for key in (
            "wall_ms_sum",
            "wall_ms_p50",
            "wall_ms_p95",
            "qradar_execution_ms_sum",
            "qradar_execution_ms_p50",
            "records_returned_sum",
        ):
            assert window[key] is None, key


@pytest.mark.asyncio
async def test_a_part_that_cannot_be_read_says_so_and_the_others_still_answer(
    permit_pool: dict[str, Any],
) -> None:
    permit_pool["error"] = RuntimeError("permit read down")
    reads = _Reads(in_flight=[_flying(5)], fail=("recent",))
    current = await ariel_log.load_current(reads, now=NOW)  # type: ignore[arg-type]
    assert current["windows"] == {"available": False, "reason": "recent searches could not be read"}
    assert current["slots"] == {"available": False, "reason": "permit slots could not be read"}
    assert current["in_flight"]["available"] is True
    assert len(current["in_flight"]["active"]) == 1

    broken = await ariel_log.load_current(
        _Reads(fail=("in_flight",)), now=NOW  # type: ignore[arg-type]
    )
    assert broken["in_flight"] == {
        "available": False,
        "reason": "in-flight searches could not be read",
    }
    assert "RuntimeError" not in json.dumps(broken)


@pytest.mark.asyncio
async def test_the_slots_are_the_doctors_permit_read(permit_pool: dict[str, Any]) -> None:
    current = await ariel_log.load_current(_Reads(), now=NOW)  # type: ignore[arg-type]
    assert current["slots"] == {
        "available": True,
        "enforced": True,
        "global_held": 2,
        "global_cap": 6,
        "console_pool_locks": 1,
        "console_cap": 3,
        "reserved_slots": 2,
    }
    permit_pool["counts"] = None
    unenforced = await ariel_log.load_current(_Reads(), now=NOW)  # type: ignore[arg-type]
    assert unenforced["slots"] == {"available": True, "enforced": False}


@pytest.mark.asyncio
async def test_a_naive_clock_is_read_as_utc(permit_pool: dict[str, Any]) -> None:
    current = await ariel_log.load_current(_Reads(), now=NOW.replace(tzinfo=None))  # type: ignore[arg-type]
    assert current["as_of"] == NOW.isoformat()


# ------------------------------------------------------------------ the route
def _api_client(principal: Principal) -> TestClient:
    app = create_app()

    async def _session() -> AsyncIterator[None]:
        yield None

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: principal
    return TestClient(app)


def test_the_current_route_is_admin_only_and_returns_the_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[object] = []

    async def read(_session: object) -> dict[str, Any]:
        calls.append(1)
        return {"as_of": NOW.isoformat(), "in_flight": {"available": True, "active": []}}

    monkeypatch.setattr("app.api.routes.ariel_load.load_current_with_health", read)
    for principal in (READER, WAF_ADMIN, Principal(subject="n", permissions=frozenset())):
        response = _api_client(principal).get("/api/v1/admin/ariel-load/current")
        assert response.status_code == 403
        assert response.json()["detail_code"] == "auth.permission.missing"
    assert calls == []
    ok = _api_client(ADMIN).get("/api/v1/admin/ariel-load/current")
    assert ok.status_code == 200
    assert ok.json()["as_of"] == NOW.isoformat()
    assert calls == [1]


# ------------------------------------------------------------------- the CLI
def _sample_current() -> dict[str, Any]:
    window = {
        "submitted": 4,
        "completed": 2,
        "failed": 1,
        "refused": 0,
        "in_flight": 1,
        "wall_ms_sum": 9400,
        "wall_ms_p50": 200,
        "wall_ms_p95": 290,
        "qradar_execution_ms_sum": 750,
        "qradar_execution_ms_unknown": 1,
        "qradar_execution_ms_p50": 50,
        "qradar_execution_ms_p95": 50,
        "records_returned_sum": 5,
        "records_returned_unknown": 2,
    }
    return {
        "as_of": NOW.isoformat(),
        "in_flight": {
            "available": True,
            "active": [
                {
                    "caller": IPS,
                    "instance_id": None,
                    "submitted_at": NOW.isoformat(),
                    "age_seconds": 12,
                    "polls": 4,
                    "range_seconds": 3600,
                }
            ],
            "stale": [],
            "stale_after_seconds": 930,
        },
        "slots": {
            "available": True,
            "enforced": True,
            "global_held": 2,
            "global_cap": 6,
            "console_pool_locks": 1,
            "console_cap": 3,
            "reserved_slots": 2,
        },
        "windows": {
            "available": True,
            "minutes_5": window,
            "minutes_15": window,
            "minutes_60": window,
        },
    }


def test_cli_current_prints_a_table_or_the_json(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def now() -> dict[str, Any]:
        return _sample_current()

    monkeypatch.setattr(cli, "_load_now", now)
    assert cli.main(["--current"]) == 0
    table = capsys.readouterr().out
    assert table == cli.render_current_table(_sample_current()) + "\n"
    assert "in flight: 1 (stale, worker gone: 0, older than 930 s)" in table
    assert f"active\t{IPS}\tnull\t12\t4\t3600" in table
    assert "slots: global 2/6, console-pool locks 1 (cap 3 per console), reserved 2" in table
    assert "5 min\t4\t2\t1\t0\t1\t200\t290\t9400\t50\t50\t750\t1\t5\t2" in table
    assert "percentiles are over COMPLETED searches" in table

    assert cli.main(["--current", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == _sample_current()


def test_cli_current_says_which_part_is_unavailable_and_hides_errors(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    sample = _sample_current()
    sample["windows"] = {"available": False, "reason": "recent searches could not be read"}
    sample["slots"] = {"available": False, "reason": "permit slots could not be read"}
    text = cli.render_current_table(sample)
    assert "slots: not available (permit slots could not be read)" in text
    assert "windows: not available (recent searches could not be read)" in text
    assert "0\t0" not in text

    async def boom() -> dict[str, Any]:
        raise RuntimeError("password=hunter2")

    monkeypatch.setattr(cli, "_load_now", boom)
    assert cli.main(["--current"]) == 2
    captured_err = capsys.readouterr().err
    assert captured_err == "ariel load could not be read\n"
    assert "hunter2" not in captured_err


def test_no_reader_facing_text_calls_the_load_live_or_real_time() -> None:
    import re

    from app.api.routes import ariel_load as route

    pattern = re.compile(r"\blive\b|real[- ]?time", re.IGNORECASE)
    for module in (ariel_log, cli, route):
        assert pattern.search(inspect.getsource(module)) is None, module.__name__
    assert pattern.search(cli.render_current_table(_sample_current())) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["-c", "import app.services.ariel_search_log"],
        ["-c", "import app.providers.mock"],
        ["-c", "import app.workers.tasks"],
        ["-m", "app.cli.ariel_load", "--hours", "0"],
        [
            "-c",
            "from app.services import ariel_search_log as m;"
            " assert m._permit_budget_class().__name__ == 'ArielPermitBudget'",
        ],
    ],
    ids=["log-first", "mock-first", "worker", "cli", "permit-budget-from-the-current-read"],
)
def test_the_log_imports_cleanly_as_the_first_import_of_a_process(
    argv: list[str], tmp_path: Any
) -> None:
    """The operator's command and the worker start with a cold interpreter, so a
    circular import that the suite's import order hides fails there."""
    import os
    import subprocess
    import sys
    from pathlib import Path

    import app

    backend = str(Path(app.__file__).resolve().parent.parent)
    env = {**os.environ, "PYTHONPATH": backend}
    done = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, *argv],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert "ImportError" not in done.stderr and "circular" not in done.stderr, done.stderr
    # The CLI refuses hours=0 with its own exit code; the imports exit 0.
    assert done.returncode == (2 if argv[0] == "-m" else 0), done.stderr
