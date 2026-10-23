"""API fields for an anomaly evaluation fault (T-172).

Pins every field of `SourceBehaviorOut.evaluation` and the two summary counts
in each state the page has to render: open (retrying), given up, cleared,
never evaluated, and no fault. The newest collected EPS may come from a bucket
the engine has not judged; `judged_through` is what makes that pair honest.
"""

from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.database import get_session
from app.main import create_app
from app.models.enums import BucketCompleteness, Criticality
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource, LogSourceBaseline, LogSourceMetric
from app.models.monitoring import AnomalyEvaluationFault, AnomalyEvaluationWatermark
from app.security.auth import get_principal
from app.security.rbac import Principal

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        "TEST_DATABASE_URL" not in os.environ,
        reason="set TEST_DATABASE_URL to run the evaluation-fault API tests",
    ),
]

READER = Principal(subject="reader", permissions=frozenset({"read:*"}))


def _now() -> datetime:
    """Clock read at test start, so the 24 h window still covers the rows."""
    return datetime.now(UTC).replace(microsecond=0)


def reader_client(db_url: str) -> TestClient:
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
    return TestClient(app)


async def _source(session, name: str, qradar_id: int) -> LogSource:
    inst = QRadarInstance(
        name=f"inst-{uuid.uuid4().hex[:8]}", console_host="mock", provider_kind="mock"
    )
    session.add(inst)
    await session.flush()
    src = LogSource(
        instance_id=inst.id,
        qradar_id=qradar_id,
        name=name,
        criticality=Criticality.MEDIUM,
        monitoring_enabled=True,
    )
    session.add(src)
    await session.flush()
    return src


async def _bucket(session, source_id: uuid.UUID, at: datetime, *, eps: float) -> None:
    session.add(
        LogSourceMetric(
            log_source_id=source_id,
            bucket_start=at,
            bucket_seconds=300,
            event_count=int(eps * 300),
            average_eps=eps,
            peak_eps=eps,
            completeness=BucketCompleteness.COMPLETE,
        )
    )


async def _cell(session, source_id: uuid.UUID, at: datetime, *, median: float) -> None:
    session.add(
        LogSourceBaseline(
            log_source_id=source_id,
            metric_name="average_eps",
            weekday=at.isoweekday(),
            hour=at.hour,
            median=median,
            mad=2.0,
            p05=median - 5,
            p95=median + 5,
            sample_count=30,
            is_reliable=True,
            baseline_version=2,
            observations=[median],
            completeness=1.0,
        )
    )


async def _watermark(
    session, source_id: uuid.UUID, through: datetime, *, at: datetime
) -> None:
    session.add(
        AnomalyEvaluationWatermark(
            log_source_id=source_id, evaluated_through=through, updated_at=at
        )
    )


async def _fault(
    session,
    source_id: uuid.UUID,
    bucket: datetime,
    *,
    attempts: int,
    error_class: str = "RuntimeError",
    last_failed_at: datetime,
    given_up_at: datetime | None = None,
    resolved_at: datetime | None = None,
) -> None:
    session.add(
        AnomalyEvaluationFault(
            log_source_id=source_id,
            bucket_start=bucket,
            attempts=attempts,
            first_failed_at=last_failed_at - timedelta(minutes=2),
            last_failed_at=last_failed_at,
            error_class=error_class,
            given_up_at=given_up_at,
            resolved_at=resolved_at,
        )
    )


def _parse(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


# ------------------------------------------------------------------ states
async def test_open_fault_exposes_every_evaluation_field(
    db_session, db_schema
) -> None:
    now = _now()
    judged = now - timedelta(minutes=25)
    failed = now - timedelta(minutes=20)
    src = await _source(db_session, "eval-open", 4101)
    await _bucket(db_session, src.id, now, eps=50.0)
    await _cell(db_session, src.id, now, median=50.0)
    await _watermark(db_session, src.id, judged, at=now)
    await _fault(db_session, src.id, failed, attempts=2, last_failed_at=now)
    await db_session.commit()

    api = reader_client(db_schema)
    row = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    listed = next(
        r for r in api.get("/api/v1/behavior/sources").json() if r["name"] == "eval-open"
    )

    for body in (row, listed):
        ev = body["evaluation"]
        assert _parse(ev["judged_through"]) == judged
        assert _parse(ev["failed_bucket_start"]) == failed
        assert ev["attempts"] == 2
        assert ev["max_attempts"] == 3
        assert ev["error_class"] == "RuntimeError"
        assert _parse(ev["last_failure_at"]) == now
        assert ev["given_up_at"] is None
        assert ev["resolved_at"] is None
        assert body["skipped_buckets_in_window"] == 0
        assert body["skipped_buckets"] == []
        # The trap: newest EPS is from `now`, the verdict is from `judged`.
        assert body["observed_eps"] == 50.0
        assert _parse(body["last_bucket_at"]) == now
        assert _parse(body["last_bucket_at"]) > _parse(ev["judged_through"])


async def test_given_up_fault_counts_the_unjudged_bucket(
    db_session, db_schema
) -> None:
    now = _now()
    failed = now - timedelta(minutes=20)
    given_up_at = now - timedelta(minutes=16)
    src = await _source(db_session, "eval-given-up", 4102)
    await _bucket(db_session, src.id, now, eps=50.0)
    await _cell(db_session, src.id, now, median=50.0)
    await _watermark(db_session, src.id, now, at=now)
    await _fault(
        db_session,
        src.id,
        failed,
        attempts=3,
        last_failed_at=given_up_at,
        given_up_at=given_up_at,
    )
    await db_session.commit()

    api = reader_client(db_schema)
    row = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    ev = row["evaluation"]
    assert _parse(ev["judged_through"]) == now
    assert _parse(ev["failed_bucket_start"]) == failed
    assert ev["attempts"] == 3
    assert ev["max_attempts"] == 3
    assert ev["error_class"] == "RuntimeError"
    assert _parse(ev["given_up_at"]) == given_up_at
    assert ev["resolved_at"] is None
    assert row["skipped_buckets_in_window"] == 1
    assert len(row["skipped_buckets"]) == 1
    skipped = row["skipped_buckets"][0]
    assert _parse(skipped["bucket_start"]) == failed
    assert skipped["error_class"] == "RuntimeError"
    assert skipped["attempts"] == 3
    assert skipped["given_up_at"] == ev["given_up_at"]
    assert skipped["last_failure_at"] == ev["last_failure_at"]
    # A given-up bucket is not an incident.
    anomalies = api.get(
        "/api/v1/anomalies", params={"log_source_id": str(src.id)}
    ).json()
    assert anomalies["total"] == 0
    assert anomalies["items"] == []


async def test_cleared_fault_keeps_the_resolved_row_and_no_skipped_count(
    db_session, db_schema
) -> None:
    now = _now()
    failed = now - timedelta(minutes=20)
    resolved = now - timedelta(minutes=19)
    src = await _source(db_session, "eval-cleared", 4103)
    await _bucket(db_session, src.id, now, eps=50.0)
    await _cell(db_session, src.id, now, median=50.0)
    await _watermark(db_session, src.id, now, at=now)
    await _fault(
        db_session,
        src.id,
        failed,
        attempts=1,
        last_failed_at=failed,
        resolved_at=resolved,
    )
    await db_session.commit()

    api = reader_client(db_schema)
    row = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    ev = row["evaluation"]
    assert _parse(ev["judged_through"]) == now
    assert _parse(ev["failed_bucket_start"]) == failed
    assert ev["attempts"] == 1
    assert ev["error_class"] == "RuntimeError"
    assert ev["given_up_at"] is None
    assert _parse(ev["resolved_at"]) == resolved
    assert row["skipped_buckets_in_window"] == 0
    assert row["skipped_buckets"] == []


async def test_never_evaluated_is_null_evaluation_not_a_zero(
    db_session, db_schema
) -> None:
    src = await _source(db_session, "eval-never", 4104)
    await db_session.commit()

    api = reader_client(db_schema)
    row = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    assert row["evaluation"] is None
    assert row["skipped_buckets_in_window"] == 0
    assert row["observed_eps"] is None
    assert row["last_bucket_at"] is None
    # attempts would be a lie as 0: there was no attempt.
    summary = api.get("/api/v1/anomalies/summary").json()
    assert summary["sources_with_evaluation_faults"] == 0
    assert summary["buckets_not_judged"] == 0


async def test_no_fault_publishes_judged_through_and_null_fault_fields(
    db_session, db_schema
) -> None:
    now = _now()
    src = await _source(db_session, "eval-healthy", 4105)
    await _bucket(db_session, src.id, now, eps=50.0)
    await _cell(db_session, src.id, now, median=50.0)
    await _watermark(db_session, src.id, now, at=now)
    await db_session.commit()

    api = reader_client(db_schema)
    row = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    ev = row["evaluation"]
    assert ev is not None
    assert _parse(ev["judged_through"]) == now
    assert ev["failed_bucket_start"] is None
    assert ev["attempts"] is None
    assert ev["max_attempts"] == 3
    assert ev["error_class"] is None
    assert ev["last_failure_at"] is None
    assert ev["given_up_at"] is None
    assert ev["resolved_at"] is None
    assert row["skipped_buckets_in_window"] == 0
    assert row["state"] == "NORMAL"


async def test_summary_counts_open_faults_and_given_up_buckets(
    db_session, db_schema
) -> None:
    now = _now()
    judged = now - timedelta(minutes=25)
    failed = now - timedelta(minutes=20)
    resumed = now - timedelta(minutes=15)
    open_src = await _source(db_session, "sum-open", 4201)
    await _watermark(db_session, open_src.id, judged, at=now)
    await _fault(db_session, open_src.id, failed, attempts=2, last_failed_at=now)

    given = await _source(db_session, "sum-given", 4202)
    await _watermark(db_session, given.id, now, at=now)
    await _fault(
        db_session,
        given.id,
        failed,
        attempts=3,
        last_failed_at=now - timedelta(minutes=5),
        given_up_at=now - timedelta(minutes=5),
    )
    await _fault(
        db_session,
        given.id,
        resumed,
        attempts=3,
        last_failed_at=now - timedelta(minutes=4),
        given_up_at=now - timedelta(minutes=4),
    )

    healthy = await _source(db_session, "sum-healthy", 4203)
    await _watermark(db_session, healthy.id, now, at=now)
    await db_session.commit()

    body = reader_client(db_schema).get("/api/v1/anomalies/summary").json()
    assert body["sources_with_evaluation_faults"] == 2
    assert body["buckets_not_judged"] == 2
    assert body["monitored_sources"] == 3


async def test_first_run_fault_has_null_judged_through(
    db_session, db_schema
) -> None:
    now = _now()
    failed = now - timedelta(minutes=5)
    src = await _source(db_session, "eval-first-run", 4106)
    await _bucket(db_session, src.id, failed, eps=3.0)
    await _fault(db_session, src.id, failed, attempts=1, last_failed_at=now)
    await db_session.commit()

    row = reader_client(db_schema).get(f"/api/v1/behavior/sources/{src.id}").json()
    ev = row["evaluation"]
    assert ev["judged_through"] is None
    assert _parse(ev["failed_bucket_start"]) == failed
    assert ev["attempts"] == 1
    assert ev["given_up_at"] is None


async def test_detail_window_excludes_a_given_up_bucket_outside_the_range(
    db_session, db_schema
) -> None:
    now = _now()
    src = await _source(db_session, "eval-window", 4107)
    await _bucket(db_session, src.id, now, eps=50.0)
    await _watermark(db_session, src.id, now, at=now)
    old = now - timedelta(hours=30)
    await _fault(
        db_session,
        src.id,
        old,
        attempts=3,
        last_failed_at=old,
        given_up_at=old,
    )
    await db_session.commit()

    api = reader_client(db_schema)
    default = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    assert default["skipped_buckets_in_window"] == 0
    wide = api.get(
        f"/api/v1/behavior/sources/{src.id}",
        params={
            "since": old.isoformat().replace("+00:00", "Z"),
            "until": now.isoformat().replace("+00:00", "Z"),
        },
    ).json()
    assert wide["skipped_buckets_in_window"] == 1


async def test_detail_counts_given_up_by_bucket_start_not_given_up_at(
    db_session, db_schema
) -> None:
    """A bucket that started before the page range is not in this window.

    The engine may give up on it after `since`; `summary_counts` still counts
    that give-up in 24 h (doctor FAIL). The detail page's skipped count is
    about the chart, so it uses `bucket_start`.
    """
    now = _now()
    bucket_start = now - timedelta(hours=30)
    given_up_at = now - timedelta(hours=1)
    src = await _source(db_session, "eval-start-vs-give-up", 4108)
    await _bucket(db_session, src.id, now, eps=50.0)
    await _cell(db_session, src.id, now, median=50.0)
    await _watermark(db_session, src.id, now, at=now)
    await _fault(
        db_session,
        src.id,
        bucket_start,
        attempts=3,
        last_failed_at=given_up_at,
        given_up_at=given_up_at,
    )
    await db_session.commit()

    api = reader_client(db_schema)
    detail = api.get(f"/api/v1/behavior/sources/{src.id}").json()
    assert detail["skipped_buckets_in_window"] == 0
    assert detail["skipped_buckets"] == []

    summary = api.get("/api/v1/anomalies/summary").json()
    assert summary["buckets_not_judged"] == 1
    assert summary["sources_with_evaluation_faults"] == 1
