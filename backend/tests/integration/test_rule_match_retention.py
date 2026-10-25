"""Measured rule-match retention deletes by bucket_start and window_start."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.core.config import get_settings
from app.models.rule import (
    AnalyticsRule,
    RuleMatchMetric,
    RuleMatchWindow,
    RuleMatchWindowStatus,
)
from app.services.rule_match_retention import RuleMatchRetention
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 15, tzinfo=UTC)
DAYS = 90


def cfg(**extra: object):
    return get_settings().model_copy(
        update={
            "rule_match_retention_days": DAYS,
            "rule_match_retention_batch_size": 100,
            "rule_match_retention_max_batches": 20,
            "retention_enabled": False,
            **extra,
        }
    )


async def parents(session):
    instance = await make_instance(session)
    rule = AnalyticsRule(instance_id=instance.id, qradar_id=7, name="rule-7")
    session.add(rule)
    await session.flush()
    return instance, rule


def window(instance_id, start: datetime) -> RuleMatchWindow:
    return RuleMatchWindow(
        instance_id=instance_id,
        window_start=start,
        window_seconds=3600,
        origin="LIVE",
        status=RuleMatchWindowStatus.COMPLETE,
        rows_returned=0,
        row_cap=2000,
        unknown_ids=0,
        query_version="rule_match/v1",
        collected_at=NOW,
        attempts=1,
    )


def metric(rule_id, start: datetime) -> RuleMatchMetric:
    return RuleMatchMetric(
        rule_id=rule_id,
        bucket_start=start,
        bucket_seconds=3600,
        match_records=5,
        matched_events=42,
    )


async def run(session, settings=None):
    return await RuleMatchRetention(
        session, settings=settings or cfg(), clock=lambda: NOW
    ).run()


async def count(session, model) -> int:
    return int(await session.scalar(select(func.count()).select_from(model)) or 0)


async def test_deletes_only_rows_older_than_cutoff_on_both_tables(db_session) -> None:
    instance, rule = await parents(db_session)
    cutoff = NOW - timedelta(days=DAYS)
    db_session.add_all(
        [
            window(instance.id, cutoff - timedelta(seconds=1)),
            window(instance.id, cutoff),
            window(instance.id, NOW),
            metric(rule.id, cutoff - timedelta(seconds=1)),
            metric(rule.id, cutoff),
            metric(rule.id, NOW),
        ]
    )
    await db_session.commit()

    report = await run(db_session)

    assert report.as_dict() == {
        "deleted": {"rule_match_metric": 1, "rule_match_window": 1},
        "incomplete": [],
        "skipped_reason": None,
    }
    assert await count(db_session, RuleMatchMetric) == 2
    assert await count(db_session, RuleMatchWindow) == 2
    # The parents are not the rows' age target; neither goes.
    assert await count(db_session, AnalyticsRule) == 1


async def test_batch_cap_is_per_table(db_session) -> None:
    instance, rule = await parents(db_session)
    old = NOW - timedelta(days=DAYS + 10)
    db_session.add_all(
        [window(instance.id, old - timedelta(seconds=i)) for i in range(5)]
        + [metric(rule.id, old - timedelta(seconds=i)) for i in range(5)]
    )
    await db_session.commit()

    report = await run(db_session, cfg(rule_match_retention_batch_size=2,
                                       rule_match_retention_max_batches=1))

    assert report.deleted == {"rule_match_metric": 2, "rule_match_window": 2}
    assert report.incomplete == ["rule_match_metric", "rule_match_window"]
    assert await count(db_session, RuleMatchMetric) == 3
    assert await count(db_session, RuleMatchWindow) == 3


async def test_a_second_run_deletes_the_remainder(db_session) -> None:
    instance, rule = await parents(db_session)
    old = NOW - timedelta(days=DAYS + 10)
    db_session.add_all(
        [window(instance.id, old - timedelta(seconds=i)) for i in range(5)]
        + [metric(rule.id, old - timedelta(seconds=i)) for i in range(5)]
    )
    await db_session.commit()
    capped = cfg(rule_match_retention_batch_size=2, rule_match_retention_max_batches=1)

    first = await run(db_session, capped)
    second = await run(db_session, capped)
    third = await run(db_session, capped)

    assert first.deleted == {"rule_match_metric": 2, "rule_match_window": 2}
    assert second.deleted == {"rule_match_metric": 2, "rule_match_window": 2}
    assert third.deleted == {"rule_match_metric": 1, "rule_match_window": 1}
    assert third.incomplete == []
    assert await count(db_session, RuleMatchMetric) == 0
    assert await count(db_session, RuleMatchWindow) == 0


async def test_retention_enabled_false_still_deletes(db_session) -> None:
    instance, rule = await parents(db_session)
    db_session.add_all(
        [
            window(instance.id, NOW - timedelta(days=DAYS + 1)),
            metric(rule.id, NOW - timedelta(days=DAYS + 1)),
        ]
    )
    await db_session.commit()
    settings = cfg()
    assert settings.retention_enabled is False

    report = await run(db_session, settings)

    assert report.deleted == {"rule_match_metric": 1, "rule_match_window": 1}
    assert report.skipped_reason is None
