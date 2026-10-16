"""search_result_metric retention deletes by bucket_start and stops at the batch bound.

The test schema is a plain table (`Base.metadata.create_all`), the same shape the
batch task is written for. It does not install a Timescale hypertable.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.enums import ExecutionStatus
from app.models.search import ScheduledSearch, SearchExecution, SearchResultMetric
from app.services.search_result_retention import SearchResultRetention
from tests.integration.factories import make_search

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, tzinfo=UTC)
DAYS = 10


async def _seed(session, *, old: int, boundary: bool, recent: bool) -> datetime:
    search = await make_search(session, name="retained", schedule_cron="0 * * * *")
    execution = SearchExecution(
        search_id=search.id,
        query_version=1,
        run_key="retained-1",
        trigger="SCHEDULED",
        status=ExecutionStatus.COMPLETED,
    )
    session.add(execution)
    await session.flush()
    cutoff = NOW - timedelta(days=DAYS)
    buckets: list[datetime] = [cutoff - timedelta(seconds=i + 1) for i in range(old)]
    if boundary:
        buckets.append(cutoff)
    if recent:
        buckets.append(NOW)
    for bucket in buckets:
        session.add(
            SearchResultMetric(
                execution_id=execution.id,
                search_id=search.id,
                bucket_start=bucket,
                metric_key="total",
                value=1.0,
                dimensions={},
            )
        )
    await session.commit()
    return cutoff


async def _buckets(session) -> list[datetime]:
    session.expire_all()
    return list((await session.scalars(select(SearchResultMetric.bucket_start))).all())


async def test_retention_deletes_only_rows_older_than_the_cutoff(db_session) -> None:
    cutoff = await _seed(db_session, old=1, boundary=True, recent=True)
    settings = get_settings().model_copy(
        update={
            "search_result_metric_retention_days": DAYS,
            "search_result_retention_batch_size": 100,
            "search_result_retention_max_batches": 20,
        }
    )
    report = await SearchResultRetention(
        db_session, settings=settings, clock=lambda: NOW
    ).run()
    assert report.deleted == {"search_result_metric": 1}
    assert report.incomplete == []
    remaining = await _buckets(db_session)
    assert cutoff - timedelta(seconds=1) not in remaining
    assert cutoff in remaining
    assert NOW in remaining
    assert await db_session.scalar(select(func.count()).select_from(ScheduledSearch)) == 1
    assert await db_session.scalar(select(func.count()).select_from(SearchExecution)) == 1


async def test_retention_stops_at_the_batch_bound(db_session) -> None:
    cutoff = await _seed(db_session, old=101, boundary=False, recent=True)
    settings = get_settings().model_copy(
        update={
            "search_result_metric_retention_days": DAYS,
            "search_result_retention_batch_size": 100,
            "search_result_retention_max_batches": 1,
        }
    )
    report = await SearchResultRetention(
        db_session, settings=settings, clock=lambda: NOW
    ).run()
    assert report.deleted == {"search_result_metric": 100}
    assert report.incomplete == ["search_result_metric"]
    remaining = await _buckets(db_session)
    old_left = [bucket for bucket in remaining if bucket < cutoff]
    assert len(old_left) == 1
    assert NOW in remaining
    assert len(remaining) == 2
