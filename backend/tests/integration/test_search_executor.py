"""Search executor: timeout, retry, polling, idempotency, threshold, concurrency.

DB-gated (SearchExecution/SearchResultMetric use PgUUID/JSONB). Uses a FakeClock
and a no-op sleep so timeout/retry are deterministic with no real waiting.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select

from app.models.enums import ExecutionErrorType, ExecutionStatus
from app.models.search import SearchExecution
from app.providers.base import ProviderCapability, ProviderUnavailableError
from app.providers.dto import ArielSearchStatusDTO
from app.providers.mock import MockQRadarProvider
from app.services.concurrency import InMemoryConcurrencyLimiter
from app.services.search_executor import SearchExecutor
from tests.integration.factories import FakeClock, make_search, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def _noop_sleep(_seconds: float) -> None:
    return None


def _limiter() -> InMemoryConcurrencyLimiter:
    return InMemoryConcurrencyLimiter(per_instance=2, global_limit=4)


async def test_successful_execution_stores_aggregated_metrics(db_session) -> None:
    search = await make_search(db_session)
    provider = MockQRadarProvider(seed=1337)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)

    execution = await executor.execute(search, run_key="run-1", instance_key="default")
    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.ariel_search_id is not None
    assert execution.duration_ms is not None
    # A 'total' result metric is always stored; raw events never are.
    assert execution.result_count is not None


async def test_idempotent_run_key_reuses_execution(db_session) -> None:
    search = await make_search(db_session)
    provider = MockQRadarProvider(seed=1337)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)

    first = await executor.execute(search, run_key="dup", instance_key="default")
    second = await executor.execute(search, run_key="dup", instance_key="default")
    assert first.id == second.id
    count = await db_session.scalar(
        select(func.count()).select_from(SearchExecution).where(
            SearchExecution.search_id == search.id
        )
    )
    assert count == 1  # retries/re-runs of the same key never duplicate


async def test_timeout_cancels_and_records(db_session) -> None:
    class NeverCompletes(MockQRadarProvider):
        async def get_ariel_search_status(self, search_id: str) -> ArielSearchStatusDTO:
            return ArielSearchStatusDTO(search_id=search_id, status="EXECUTE", progress=10)

    cancelled: list[str] = []

    class Provider(NeverCompletes):
        async def cancel_ariel_search(self, search_id: str) -> None:
            cancelled.append(search_id)

    search = await make_search(db_session, timeout_seconds=10)
    # Clock advances 5s per call so the poll loop crosses the 10s timeout fast.
    clock = FakeClock(utc(), step_seconds=5)
    executor = SearchExecutor(
        db_session, Provider(), _limiter(), clock=clock, sleep=_noop_sleep,
    )
    execution = await executor.execute(search, run_key="to", instance_key="default")
    assert execution.status == ExecutionStatus.TIMEOUT
    assert execution.error_type == ExecutionErrorType.TIMEOUT.value
    assert cancelled, "timed-out search must be cancelled"


async def test_retry_then_success_single_row(db_session, monkeypatch) -> None:
    calls = {"n": 0}

    class Flaky(MockQRadarProvider):
        async def create_ariel_search(self, aql: str):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise ProviderUnavailableError("transient network blip")
            return await super().create_ariel_search(aql)

    from app.core.config import Settings

    settings = Settings(encryption_key="x" * 44, ariel_max_retries=3,
                        ariel_retry_base_seconds=1, ariel_retry_max_seconds=2)
    search = await make_search(db_session)
    executor = SearchExecutor(
        db_session, Flaky(), _limiter(), settings=settings, sleep=_noop_sleep,
    )
    execution = await executor.execute(search, run_key="retry", instance_key="default")
    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.retry_count == 2  # failed twice, succeeded on third
    count = await db_session.scalar(
        select(func.count()).select_from(SearchExecution).where(
            SearchExecution.search_id == search.id
        )
    )
    assert count == 1


async def test_validation_failure_is_not_retried(db_session) -> None:
    # An unsafe stored query fails validation and must fail immediately.
    search = await make_search(db_session, aql="SELECT * FROM events")  # unbounded
    provider = MockQRadarProvider(seed=1)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="bad", instance_key="default")
    assert execution.status == ExecutionStatus.FAILED
    assert execution.error_type == ExecutionErrorType.VALIDATION.value
    assert execution.retry_count == 0


async def test_threshold_breach_flagged(db_session) -> None:
    # Force a deterministic non-zero result and a threshold of 0 so GT breaches.
    class Counting(MockQRadarProvider):
        async def get_ariel_search_results(self, search_id: str, *, max_rows: int):
            from app.providers.dto import ArielSearchResultsDTO
            return ArielSearchResultsDTO(
                search_id=search_id, columns=["grouping", "event_count"],
                rows=[{"grouping": "h1", "event_count": 5}], total_count=5, truncated=False,
            )

    search = await make_search(db_session, threshold_value=0)
    executor = SearchExecutor(db_session, Counting(), _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="thr", instance_key="default")
    assert execution.threshold_breached is True


async def test_incapable_provider_fails_validation(db_session) -> None:
    class NoAql(MockQRadarProvider):
        capabilities = frozenset({ProviderCapability.INVENTORY})

    search = await make_search(db_session)
    executor = SearchExecutor(db_session, NoAql(), _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="x", instance_key="default")
    assert execution.status == ExecutionStatus.FAILED
    assert execution.error_type == ExecutionErrorType.VALIDATION.value


# --------------------------------------------------------- truncated results (T-033)
def _capped_provider(*, reported: int, truncated: bool = True):
    """A provider whose Ariel result was cut at `reported` rows."""
    from app.providers.dto import ArielSearchResultsDTO

    class Capped(MockQRadarProvider):
        async def get_ariel_search_results(self, search_id: str, *, max_rows: int):
            return ArielSearchResultsDTO(
                search_id=search_id, columns=["grouping", "event_count"],
                rows=[{"grouping": "h1", "event_count": 5}],
                total_count=reported, truncated=truncated,
            )

    return Capped()


async def _metric_keys(session, execution) -> list[str]:
    from app.models.search import SearchResultMetric

    return list(
        (
            await session.scalars(
                select(SearchResultMetric.metric_key).where(
                    SearchResultMetric.execution_id == execution.id
                )
            )
        ).all()
    )


async def test_rest_capped_search_is_recorded_as_truncated(db_session) -> None:
    """End to end through the REST provider: a search with more rows than its cap."""
    import random

    import httpx

    from app.providers.qradar_rest import QRadarRestProvider

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path.endswith("/ariel/searches"):
            return httpx.Response(201, json={"search_id": "s-1", "status": "WAIT"})
        if path.endswith("/ariel/searches/s-1/results"):
            first, last = (
                int(n) for n in request.headers["Range"].removeprefix("items=").split("-")
            )
            rows = [{"grouping": f"h{i}", "event_count": 1} for i in range(50)]
            return httpx.Response(200, json={"events": rows[first : last + 1]})
        if path.endswith("/ariel/searches/s-1"):
            return httpx.Response(200, json={"status": "COMPLETED", "progress": 100})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://qradar.example.internal/api",
        headers={"SEC": "t", "Version": "20.0", "Accept": "application/json"},
    )
    provider = QRadarRestProvider(
        base_url="https://qradar.example.internal", sec_token="t", client=client,
        rng=random.Random(1), sleep=_noop_sleep,
    )
    search = await make_search(db_session, threshold_value=10, max_result_rows=20)
    executor = SearchExecutor(db_session, provider, _limiter(), sleep=_noop_sleep)
    execution = await executor.execute(search, run_key="cap", instance_key="default")
    assert execution.status == ExecutionStatus.COMPLETED
    assert execution.truncated is True
    assert "total" not in await _metric_keys(db_session, execution)


async def test_truncated_result_does_not_store_a_total_metric(db_session) -> None:
    search = await make_search(db_session, threshold_value=None)
    executor = SearchExecutor(
        db_session, _capped_provider(reported=1000), _limiter(), sleep=_noop_sleep
    )
    execution = await executor.execute(search, run_key="t1", instance_key="default")
    assert execution.truncated is True
    assert execution.result_count == 1000  # recorded, but as the lower bound it is
    keys = await _metric_keys(db_session, execution)
    assert "total" not in keys
    assert "h1" in keys  # the grouped rows that were returned are still stored


async def test_whole_result_still_stores_the_total_metric(db_session) -> None:
    search = await make_search(db_session, threshold_value=None)
    executor = SearchExecutor(
        db_session, _capped_provider(reported=5, truncated=False), _limiter(),
        sleep=_noop_sleep,
    )
    execution = await executor.execute(search, run_key="w1", instance_key="default")
    assert execution.truncated is False
    assert "total" in await _metric_keys(db_session, execution)


@pytest.mark.parametrize(
    ("operator", "threshold", "reported", "breached"),
    [
        # A lower bound already past the line proves the breach for GT and GE.
        ("GT", 10, 100, True),
        ("GE", 100, 100, True),
        # A lower bound that has not reached the line decides nothing.
        ("GT", 1000, 100, False),
        ("GE", 1000, 100, False),
        # For LT, LE and EQ a lower bound proves nothing in either direction,
        # even where the capped number happens to satisfy the comparison.
        ("LT", 1000, 100, False),
        ("LE", 1000, 100, False),
        ("EQ", 100, 100, False),
        ("LT", 10, 100, False),
        ("EQ", 7, 100, False),
    ],
)
async def test_truncated_threshold_sees_only_a_lower_bound(
    db_session, operator, threshold, reported, breached
) -> None:
    search = await make_search(
        db_session, threshold_value=threshold, threshold_operator=operator
    )
    executor = SearchExecutor(
        db_session, _capped_provider(reported=reported), _limiter(), sleep=_noop_sleep
    )
    execution = await executor.execute(
        search, run_key=f"{operator}-{threshold}", instance_key="default"
    )
    assert execution.truncated is True
    assert execution.threshold_breached is breached


async def test_whole_result_keeps_every_operator_decisive(db_session) -> None:
    search = await make_search(db_session, threshold_value=1000, threshold_operator="LT")
    executor = SearchExecutor(
        db_session, _capped_provider(reported=5, truncated=False), _limiter(),
        sleep=_noop_sleep,
    )
    execution = await executor.execute(search, run_key="lt", instance_key="default")
    assert execution.threshold_breached is True


@pytest.mark.parametrize(
    ("operator", "threshold", "breached"),
    [
        # A truncated run reports total_count == max_result_rows; the proven
        # count is one past the cap (T-047), so GT at the cap breaches.
        ("GT", 5, True),
        # ... while GT at cap + 1 is exactly the boundary and does not.
        ("GT", 6, False),
        # GE at cap + 1 is proven (>= 6 holds for a proven count of 6).
        ("GE", 6, True),
        # For LT, LE and EQ a lower bound proves nothing in either direction.
        ("LT", 6, False),
        ("LE", 6, False),
        ("EQ", 6, False),
    ],
)
async def test_truncated_lower_bound_is_exactly_one_past_the_cap(
    db_session, operator, threshold, breached
) -> None:
    search = await make_search(
        db_session,
        threshold_value=threshold, threshold_operator=operator, max_result_rows=5,
    )
    executor = SearchExecutor(
        db_session, _capped_provider(reported=5), _limiter(), sleep=_noop_sleep,
    )
    execution = await executor.execute(
        search, run_key=f"b-{operator}-{threshold}", instance_key="default"
    )
    assert execution.truncated is True
    assert execution.threshold_breached is breached


@pytest.mark.parametrize(
    ("operator", "threshold", "breached"),
    [
        # A whole result is decisive even exactly at the cap: the count is the
        # measurement itself, so GT at max is not breached and GE at max is.
        ("GT", 5, False),
        ("GE", 5, True),
        ("LT", 5, False),
        ("LE", 5, True),
        ("EQ", 5, True),
    ],
)
async def test_whole_result_at_cap_is_decisive(
    db_session, operator, threshold, breached
) -> None:
    search = await make_search(
        db_session,
        threshold_value=threshold, threshold_operator=operator, max_result_rows=5,
    )
    executor = SearchExecutor(
        db_session, _capped_provider(reported=5, truncated=False), _limiter(),
        sleep=_noop_sleep,
    )
    execution = await executor.execute(
        search, run_key=f"w-{operator}-{threshold}", instance_key="default"
    )
    assert execution.truncated is False
    assert execution.threshold_breached is breached
