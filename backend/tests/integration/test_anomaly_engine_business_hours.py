"""The engine asks the shared business-hours gate, in the source's zone.

Europe/Istanbul is UTC+3. 06:00Z on 2026-07-15 is 09:00 local (inside 09-18)
and 15:00Z is 18:00 local (outside: the end hour is exclusive). A UTC clock
gets both of those judgements backwards.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.enums import AnomalyType
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline
from tests.integration.factories import add_metric, make_instance, make_log_source, utc

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44)


async def _source(session):
    inst = await make_instance(session)
    return await make_log_source(
        session,
        inst,
        business_hours_only=True,
        business_hours_start=9,
        business_hours_end=18,
        business_days=[1, 2, 3, 4, 5],
        timezone_name="Europe/Istanbul",
        custom_thresholds={"silence_grace_buckets": 0},
    )


async def _event_count_baseline(session, ls, when) -> None:
    """The cell the engine looks up is the UTC weekday and hour of the bucket."""
    session.add(
        LogSourceBaseline(
            log_source_id=ls.id,
            metric_name="event_count",
            weekday=when.isoweekday(),
            hour=when.hour,
            median=15_000.0,
            mad=500.0,
            p05=14_000.0,
            p95=16_000.0,
            sample_count=30,
            is_reliable=True,
            baseline_version=1,
            observations=[15_000.0],
        )
    )
    await session.flush()


async def _silent(session, ls, when):
    return await add_metric(
        session,
        ls,
        when,
        average_eps=0.0,
        event_count=0,
        stored_event_count=0,
        last_event_at=None,
        event_delay_seconds=None,
        collection_source="mock",
        query_provenance={"zero_filled": True},
    )


async def _no_events_rows(session, ls) -> list[LogSourceAnomaly]:
    return list(
        (
            await session.scalars(
                select(LogSourceAnomaly).where(
                    LogSourceAnomaly.log_source_id == ls.id,
                    LogSourceAnomaly.anomaly_type == AnomalyType.NO_EVENTS,
                )
            )
        ).all()
    )


async def test_silence_inside_the_local_window_raises_no_events(db_session) -> None:
    when = utc(2026, 7, 15, 6, 0)
    ls = await _source(db_session)
    await _event_count_baseline(db_session, ls, when)
    metric = await _silent(db_session, ls, when)

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_interval(ls, metric)

    assert AnomalyType.NO_EVENTS.value in report.anomalous
    assert await _no_events_rows(db_session, ls)


async def test_silence_outside_the_local_window_does_not_raise_no_events(db_session) -> None:
    # 15:00Z is 18:00 in Istanbul. End-exclusive 09-18 does not include it.
    # The UTC hour 15 is inside 09-18, so a UTC clock would raise here.
    when = utc(2026, 7, 15, 15, 0)
    ls = await _source(db_session)
    await _event_count_baseline(db_session, ls, when)
    metric = await _silent(db_session, ls, when)

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_interval(ls, metric)

    assert AnomalyType.NO_EVENTS.value not in report.anomalous
    assert await _no_events_rows(db_session, ls) == []
