"""Retention for anomaly evaluation faults: closed rows age out, open ones stay.

An open fault is the source's current state. A given-up row is the only record
that its bucket was never judged, so it is kept for the full window rather than
cleared early.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.core.config import Settings
from app.models.monitoring import AnomalyEvaluationFault
from app.services.anomaly_fault_retention import AnomalyFaultRetention
from tests.integration.factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 22, 12, tzinfo=UTC)


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_fault_retention_days=30)


async def test_deletes_only_closed_faults_past_the_cutoff(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    old = NOW - timedelta(days=40)
    recent = NOW - timedelta(days=5)
    rows = {
        # bucket_start -> (resolved_at, given_up_at)
        "open-old": (None, None),
        "resolved-old": (old, None),
        "given-up-old": (None, old),
        "open-recent": (None, None),
        "resolved-recent": (recent, None),
        "given-up-recent": (None, recent),
    }
    timestamps = {
        name: NOW - timedelta(days=60) + timedelta(minutes=index)
        for index, name in enumerate(rows)
    }
    for name, (resolved_at, given_up_at) in rows.items():
        db_session.add(
            AnomalyEvaluationFault(
                log_source_id=ls.id,
                bucket_start=timestamps[name],
                attempts=1 if resolved_at else 3,
                first_failed_at=old,
                last_failed_at=old,
                error_class="RuntimeError",
                resolved_at=resolved_at,
                given_up_at=given_up_at,
            )
        )
    await db_session.flush()

    report = await AnomalyFaultRetention(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run()

    assert report.deleted == {"anomaly_evaluation_fault": 2}
    assert report.incomplete == []
    assert report.skipped_reason is None
    remaining = set(
        (
            await db_session.scalars(select(AnomalyEvaluationFault.bucket_start))
        ).all()
    )
    assert remaining == {
        timestamps["open-old"],
        timestamps["open-recent"],
        timestamps["resolved-recent"],
        timestamps["given-up-recent"],
    }


async def test_an_open_fault_is_kept_however_old_its_first_failure(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    ancient = NOW - timedelta(days=900)
    db_session.add(
        AnomalyEvaluationFault(
            log_source_id=ls.id,
            bucket_start=ancient,
            attempts=2,
            first_failed_at=ancient,
            last_failed_at=NOW - timedelta(minutes=1),
            error_class="RuntimeError",
        )
    )
    await db_session.flush()

    report = await AnomalyFaultRetention(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run()

    assert report.deleted == {"anomaly_evaluation_fault": 0}
    assert await db_session.scalar(select(AnomalyEvaluationFault.id)) is not None


async def test_report_shape_matches_the_other_retention_tasks(db_session) -> None:
    report = await AnomalyFaultRetention(
        db_session, settings=_settings(), clock=lambda: NOW
    ).run()
    assert report.as_dict() == {
        "deleted": {"anomaly_evaluation_fault": 0},
        "incomplete": [],
        "skipped_reason": None,
    }
