"""The baseline keeps local-window buckets and drops the ones outside it.

Europe/Istanbul is UTC+3. Wednesday 06:00Z is 09:00 local (inside 09-18) and
Wednesday 15:00Z is 18:00 local (outside). A UTC clock keeps hour 15 and drops
hour 6, so both directions of this test fail if the gate stays on UTC.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.anomaly.baseline import BaselineBuilder
from app.core.config import Settings
from app.models.log_source import LogSourceBaseline
from tests.integration.factories import add_metric, make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings() -> Settings:
    return Settings(
        encryption_key="x" * 44,
        baseline_min_samples=4,
        baseline_lookback_days=60,
    )


async def _wednesday_hour(session, ls, *, hour: int, weeks: int = 4) -> datetime:
    """One bucket per week at a fixed UTC hour. 2026-07-01 is a Wednesday."""
    base = datetime(2026, 7, 1, hour, 0, tzinfo=UTC)
    for week in range(weeks):
        await add_metric(
            session,
            ls,
            base + timedelta(weeks=week),
            average_eps=50.0,
            event_count=15_000,
        )
    return base + timedelta(weeks=weeks)


async def test_local_window_keeps_the_inside_hour_and_drops_the_outside_one(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session,
        inst,
        business_hours_only=True,
        business_hours_start=9,
        business_hours_end=18,
        business_days=[1, 2, 3, 4, 5],
        timezone_name="Europe/Istanbul",
    )
    now = await _wednesday_hour(db_session, ls, hour=6)
    await _wednesday_hour(db_session, ls, hour=15)

    written = await BaselineBuilder(db_session, settings=_settings()).rebuild_for_source(
        ls, now=now
    )

    rows = list(
        (
            await db_session.scalars(
                select(LogSourceBaseline).where(LogSourceBaseline.log_source_id == ls.id)
            )
        ).all()
    )
    # Two metrics, one UTC hour. Hour 15 is 18:00 local and must be excluded.
    assert written == 2
    assert {row.hour for row in rows} == {6}
    assert {row.metric_name for row in rows} == {"average_eps", "event_count"}
