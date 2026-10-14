"""Constant-cost reads behind the Home view's two fleet-wide endpoints.

`/behavior/sources` and `/anomalies/summary` are called on every auto-refresh,
so their SQL cost must not grow with the monitored fleet. These tests pin the
statement count at two fleet sizes and pin that the bulk reads return exactly
what the per-source path returned, row for row: a constant cost bought by a
changed lookup is not an improvement.
"""

from __future__ import annotations

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    Criticality,
    EvidenceStatus,
    Severity,
)
from app.models.instance import QRadarInstance
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceMetric,
)
from app.repositories.anomaly import AnomalyRepository
from app.security.auth import get_principal
from app.security.rbac import Principal
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the behavior bulk-read tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))

#: How close to the top of the hour a test may start before it waits for the
#: hour to turn. The seeded cells and the route's own clock read must land in
#: the same hour, and no single one of these tests runs for this long.
_HOUR_GUARD_SECONDS = 60


def _utcnow() -> datetime:
    """The test's own clock read, taken when the test runs.

    `/anomalies/summary` judges every source against the cell of the current
    hour at request time, and pytest imports this module at collection, minutes
    before the suite reaches it. A NOW read at import therefore seeds its cells
    in one hour and lets the route judge them in the next. Reading the clock
    here, when the test starts, keeps the seeding and the request together.
    """
    return datetime.now(UTC)


def _now_and_earlier() -> tuple[datetime, datetime]:
    """`(now, two hours earlier)` from one clock read, across no hour boundary.

    If less than the guard remains before the next UTC hour, wait for it to
    turn before returning: a seeding that landed at 18:59:59 and a request that
    reads 19:00:01 would otherwise disagree about the hour. The wait is bounded
    by the guard, so it can add at most that many seconds.
    """
    now = _utcnow()
    next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    remaining = (next_hour - now).total_seconds()
    if remaining <= _HOUR_GUARD_SECONDS:
        time.sleep(remaining + 0.2)
        now = _utcnow()
    now = now.replace(microsecond=0)
    # Two hours earlier: a different (weekday, hour) cell whatever the hour.
    return now, now - timedelta(hours=2)


@pytest.fixture
def clock() -> tuple[datetime, datetime]:
    """The `(now, earlier)` pair for one test, read when the test starts."""
    return _now_and_earlier()


def reader_client(db_url: str) -> TestClient:
    """A read-only client on its own engine, so its statements can be counted."""
    engine = create_async_engine(db_url, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _session():
        async with maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_principal] = lambda: READER
    api = TestClient(app)
    # The engine a request runs on, so a test can count the statements it issues.
    api.engine = engine
    return api


async def _add_source(session, inst, name: str, qradar_id: int) -> uuid.UUID:
    src = LogSource(
        instance_id=inst.id,
        qradar_id=qradar_id,
        name=name,
        criticality=Criticality.MEDIUM,
        monitoring_enabled=True,
    )
    session.add(src)
    await session.flush()
    return src.id


async def _add_bucket(
    session,
    source_id: uuid.UUID,
    at: datetime,
    *,
    eps: float = 6.0,
    completeness: BucketCompleteness = BucketCompleteness.COMPLETE,
) -> None:
    session.add(
        LogSourceMetric(
            log_source_id=source_id,
            bucket_start=at,
            bucket_seconds=300,
            event_count=int(eps * 300),
            average_eps=eps,
            peak_eps=eps,
            completeness=completeness,
        )
    )


async def _add_cell(
    session, source_id: uuid.UUID, at: datetime, *, median: float, reliable: bool
) -> None:
    session.add(
        LogSourceBaseline(
            log_source_id=source_id,
            metric_name="average_eps",
            weekday=at.isoweekday(),
            hour=at.hour,
            median=median,
            mad=0.2,
            p05=max(median - 0.3, 0.0),
            p95=median + 0.3,
            sample_count=30 if reliable else 3,
            is_reliable=reliable,
            baseline_version=2,
            observations=[median],
            completeness=0.9,
        )
    )


async def _seed_mixed_fleet(
    session, now: datetime, earlier: datetime
) -> list[uuid.UUID]:
    """Six monitored sources, one per branch the row builder can take.

    Returned in `/behavior/sources` order, the order the route promises:
    monitored sources sorted by name.
    """
    inst = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(inst)
    await session.flush()

    # 1. Nothing collected: no bucket, no cell.
    never = await _add_source(session, inst, "bulk-01-never", 3101)
    # 2. A bucket but no cell, with an older bucket behind the newest: the row
    #    must read the newest bucket, and has no band to read against it.
    bucket_only = await _add_source(session, inst, "bulk-02-bucket-only", 3102)
    await _add_bucket(session, bucket_only, earlier, eps=1.0)
    await _add_bucket(session, bucket_only, now, eps=6.0)
    # 3. A PARTIAL bucket over an unreliable cell: the value exists, the band
    #    cannot judge it.
    partial = await _add_source(session, inst, "bulk-03-partial", 3103)
    await _add_bucket(
        session, partial, now, eps=6.0, completeness=BucketCompleteness.PARTIAL
    )
    await _add_cell(session, partial, now, median=2.0, reliable=False)
    # 4. A reliable cell whose median is zero: no ratio exists against it.
    zero = await _add_source(session, inst, "bulk-04-zero-median", 3104)
    await _add_bucket(session, zero, now, eps=3.0)
    await _add_cell(session, zero, now, median=0.0, reliable=True)
    # 5. An open anomaly: state OPEN whatever the cell says.
    open_src = await _add_source(session, inst, "bulk-05-open", 3105)
    await _add_bucket(session, open_src, now, eps=9.0)
    await _add_cell(session, open_src, now, median=2.0, reliable=True)
    session.add(
        LogSourceAnomaly(
            log_source_id=open_src,
            anomaly_type=AnomalyType.VOLUME_SPIKE,
            severity=Severity.HIGH,
            state=AnomalyState.OPEN,
            detected_at=now,
            opened_at=now,
            anomaly_start=now,
            observed_value=9.0,
            expected_value=2.0,
            deviation_ratio=4.5,
            evidence_status=EvidenceStatus.PENDING,
            explanation="average EPS 9.00 is above the baseline median 2.00",
        )
    )
    # 6. Its bucket landed in a different cell and only that cell exists, so a
    #    lookup keyed on the wrong hour loses this band.
    other_cell = await _add_source(session, inst, "bulk-06-other-cell", 3106)
    await _add_bucket(session, other_cell, earlier, eps=8.0)
    await _add_cell(session, other_cell, earlier, median=4.0, reliable=True)
    await session.commit()
    return [never, bucket_only, partial, zero, open_src, other_cell]


def _per_source_row(src, bucket, cell, active_count: int) -> dict:
    """What the pre-bulk per-source route returned for one source.

    Recomputed here from the single-source repository methods rather than
    imported from the route, so a change to the row builder shows up as a
    diff rather than moving both sides together.
    """
    observed = bucket.average_eps if bucket is not None else None
    expected = cell.median if (cell is not None and cell.is_reliable) else None
    ratio = (
        observed / expected
        if (observed is not None and expected not in (None, 0))
        else None
    )
    if active_count > 0:
        state = AnomalyState.OPEN
    elif cell is None or not cell.is_reliable:
        state = AnomalyState.INSUFFICIENT_DATA
    else:
        state = AnomalyState.NORMAL
    return {
        "log_source_id": str(src.id),
        "name": src.name,
        "observed_eps": observed,
        "expected_eps": expected,
        "expected_low": cell.p05 if cell is not None else None,
        "expected_high": cell.p95 if cell is not None else None,
        "deviation_ratio": ratio,
        "state": state.value,
        "baseline_sample_count": cell.sample_count if cell is not None else 0,
        "baseline_completeness": cell.completeness if cell is not None else 0.0,
        "open_anomaly_count": active_count,
    }


async def test_rows_match_the_per_source_path_for_a_mixed_fleet(
    db_session, db_schema, clock
) -> None:
    now, earlier = clock
    await _seed_mixed_fleet(db_session, now, earlier)
    rows = reader_client(db_schema).get("/api/v1/behavior/sources").json()

    # The pre-bulk lookup: one bucket and one cell query per source.
    repo = AnomalyRepository(db_session)
    sources = await repo.monitored_sources()
    counts = await repo.active_counts_by_source()
    expected = []
    for src in sources:
        bucket = await repo.latest_bucket(src.id)
        cell = None
        if bucket is not None:
            cell = await repo.baseline_cell(
                src.id,
                weekday=bucket.bucket_start.isoweekday(),
                hour=bucket.bucket_start.hour,
                metric="average_eps",
            )
        expected.append(_per_source_row(src, bucket, cell, counts.get(src.id, 0)))

    assert len(rows) == 6
    assert [r["name"] for r in rows] == [s.name for s in sources]
    for row, want in zip(rows, expected, strict=True):
        for field, value in want.items():
            assert row[field] == value, (field, row, want)

    # The same fleet, named case by case, so the equivalence above cannot be
    # satisfied by six identical rows.
    by_name = {r["name"]: r for r in rows}
    never = by_name["bulk-01-never"]
    assert never["state"] == "INSUFFICIENT_DATA"
    assert never["observed_eps"] is None
    only = by_name["bulk-02-bucket-only"]
    assert only["observed_eps"] == 6.0  # the newest of its two buckets
    assert only["expected_eps"] is None
    assert only["state"] == "INSUFFICIENT_DATA"
    unreliable = by_name["bulk-03-partial"]
    assert unreliable["expected_eps"] is None
    assert unreliable["baseline_sample_count"] == 3
    assert unreliable["expected_low"] is not None  # the band is still published
    zero = by_name["bulk-04-zero-median"]
    assert zero["expected_eps"] == 0.0
    assert zero["deviation_ratio"] is None
    assert zero["state"] == "NORMAL"
    active = by_name["bulk-05-open"]
    assert active["state"] == "OPEN"
    assert active["open_anomaly_count"] == 1
    other = by_name["bulk-06-other-cell"]
    assert other["expected_eps"] == 4.0
    assert other["deviation_ratio"] == 2.0


async def test_last_bucket_completeness_reports_partial_and_absence(
    db_session, db_schema, clock
) -> None:
    now, earlier = clock
    await _seed_mixed_fleet(db_session, now, earlier)
    rows = {
        r["name"]: r
        for r in reader_client(db_schema).get("/api/v1/behavior/sources").json()
    }
    assert rows["bulk-02-bucket-only"]["last_bucket_completeness"] == "COMPLETE"
    assert rows["bulk-03-partial"]["last_bucket_completeness"] == "PARTIAL"
    assert rows["bulk-01-never"]["last_bucket_completeness"] is None


async def test_summary_counts_reliable_unreliable_and_missing_cells(
    db_session, db_schema, clock
) -> None:
    now, earlier = clock
    await _seed_mixed_fleet(db_session, now, earlier)
    body = reader_client(db_schema).get("/api/v1/anomalies/summary").json()
    assert body["monitored_sources"] == 6
    # Only 04 and 05 hold a reliable cell at the current hour: 03's cell is
    # unreliable, and 01, 02 and 06 hold no cell there at all.
    assert body["insufficient_data_sources"] == 4
    assert body["open_anomalies"] == 1


async def test_statement_count_does_not_grow_with_the_fleet(
    db_session, db_schema, clock
) -> None:
    """The same statements for 2 monitored sources as for 6."""
    now, earlier = clock
    inst = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    db_session.add(inst)
    await db_session.flush()

    # Two sources, both with a bucket: one over a reliable cell, one over an
    # unreliable cell, so the bucket and the cell paths both run.
    a = await _add_source(db_session, inst, "count-01-reliable", 3201)
    await _add_bucket(db_session, a, now)
    await _add_cell(db_session, a, now, median=2.0, reliable=True)
    b = await _add_source(db_session, inst, "count-02-unreliable", 3202)
    await _add_bucket(db_session, b, now)
    await _add_cell(db_session, b, now, median=2.0, reliable=False)
    await db_session.commit()

    api = reader_client(db_schema)
    async with async_sessionmaker(api.engine, expire_on_commit=False)() as anchor:
        # The anchor session is never used, so it issues nothing; it only names
        # the engine the client's requests run on.
        with recorded_statements(anchor) as statements:
            response = api.get("/api/v1/behavior/sources")
        assert response.status_code == 200
        assert len(response.json()) == 2
        sources_two = len(statements)

        with recorded_statements(anchor) as statements:
            response = api.get("/api/v1/anomalies/summary")
        assert response.status_code == 200
        summary_two = len(statements)

        # Four more: a source with no bucket, one with a bucket but no cell,
        # one whose bucket landed in another cell, and a plain healthy one.
        await _add_source(db_session, inst, "count-03-no-bucket", 3203)
        d = await _add_source(db_session, inst, "count-04-no-cell", 3204)
        await _add_bucket(db_session, d, now)
        e = await _add_source(db_session, inst, "count-05-other-cell", 3205)
        await _add_bucket(db_session, e, earlier)
        await _add_cell(db_session, e, earlier, median=4.0, reliable=True)
        f = await _add_source(db_session, inst, "count-06-reliable", 3206)
        await _add_bucket(db_session, f, now)
        await _add_cell(db_session, f, now, median=2.0, reliable=True)
        await db_session.commit()

        with recorded_statements(anchor) as statements:
            response = api.get("/api/v1/behavior/sources")
        assert response.status_code == 200
        assert len(response.json()) == 6
        assert len(statements) == sources_two, (sources_two, statements)

        with recorded_statements(anchor) as statements:
            response = api.get("/api/v1/anomalies/summary")
        assert response.status_code == 200
        assert len(statements) == summary_two, (summary_two, statements)


async def test_bulk_methods_match_the_single_source_reads(db_session, clock) -> None:
    """The dict contents are the single-source results, keyed as promised."""
    now, earlier = clock
    await _seed_mixed_fleet(db_session, now, earlier)
    repo = AnomalyRepository(db_session)
    sources = await repo.monitored_sources()
    ids = [s.id for s in sources]

    buckets = await repo.latest_buckets(ids)
    # ids[0] ("bulk-01-never") holds no bucket, so it is absent rather than
    # mapped to a zero bucket.
    assert set(buckets) == set(ids[1:])
    for sid, bucket in buckets.items():
        single = await repo.latest_bucket(sid)
        assert single is not None
        assert (bucket.bucket_start, bucket.average_eps, bucket.completeness) == (
            single.bucket_start,
            single.average_eps,
            single.completeness,
        )

    keys = [
        (sid, b.bucket_start.isoweekday(), b.bucket_start.hour)
        for sid, b in buckets.items()
    ]
    cells = await repo.baseline_cells(keys, metric="average_eps")
    # 03, 04 and 05 hold a cell at NOW, 06 one at EARLIER; 02's key has none.
    assert len(cells) == 4
    assert set(cells) <= set(keys)
    assert (ids[1], now.isoweekday(), now.hour) not in cells
    assert (ids[5], earlier.isoweekday(), earlier.hour) in cells
    for key, cell in cells.items():
        single = await repo.baseline_cell(
            key[0], weekday=key[1], hour=key[2], metric="average_eps"
        )
        assert single is not None
        assert (cell.median, cell.is_reliable, cell.sample_count) == (
            single.median,
            single.is_reliable,
            single.sample_count,
        )


async def test_empty_input_is_a_no_op_with_zero_statements(db_session) -> None:
    repo = AnomalyRepository(db_session)
    with recorded_statements(db_session) as statements:
        assert await repo.latest_buckets([]) == {}
        assert await repo.baseline_cells([], metric="average_eps") == {}
    assert statements == []
