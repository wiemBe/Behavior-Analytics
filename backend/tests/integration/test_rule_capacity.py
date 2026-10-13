"""Capacity history: one sample per new `capacity_timestamp`, in one statement."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.collectors.rule_collector import RuleCollector
from app.models.rule import RuleCapacitySample
from app.providers.dto import AnalyticsRuleDTO
from app.providers.mock import MockQRadarProvider
from tests.integration.factories import make_instance
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
T1 = NOW - timedelta(days=2)
T2 = NOW - timedelta(days=1)


class Inventory(MockQRadarProvider):
    def __init__(self, rules: list[AnalyticsRuleDTO]) -> None:
        super().__init__(seed=1)
        self.rules = rules

    async def list_rules(self) -> list[AnalyticsRuleDTO]:
        return self.rules

    async def list_building_blocks(self) -> list[AnalyticsRuleDTO]:
        return []


def dto(i: int, ts: datetime | None, cap: float = 5.0) -> AnalyticsRuleDTO:
    return AnalyticsRuleDTO(
        qradar_id=i, name=f"r{i}", rule_type="EVENT", average_capacity=cap,
        base_capacity=cap + 1, base_host_id=53, capacity_timestamp=ts,
    )


async def sync(session, instance, rules):
    return await RuleCollector(session, Inventory(rules), clock=lambda: NOW).sync(instance)


async def samples(session) -> list[RuleCapacitySample]:
    return list(
        (
            await session.scalars(
                select(RuleCapacitySample).order_by(
                    RuleCapacitySample.rule_id, RuleCapacitySample.capacity_timestamp
                )
            )
        ).all()
    )


@pytest.mark.asyncio
async def test_a_sample_is_stored_once_per_new_timestamp(db_session) -> None:
    inst = await make_instance(db_session)
    await sync(db_session, inst, [dto(1, T1, 5.0)])
    await sync(db_session, inst, [dto(1, T1, 5.0)])  # same measurement read again
    assert len(await samples(db_session)) == 1

    await sync(db_session, inst, [dto(1, T2, 4.0)])  # QRadar re-benchmarked
    rows = await samples(db_session)
    assert [r.capacity_timestamp for r in rows] == [T1, T2]
    assert (rows[1].average_capacity, rows[1].base_capacity, rows[1].base_host_id) == (
        4.0, 5.0, 53,
    )
    assert rows[1].recorded_at == NOW


@pytest.mark.asyncio
async def test_an_unchanged_timestamp_keeps_the_first_values(db_session) -> None:
    """DO NOTHING, not DO UPDATE: history is not rewritten by a later read."""
    inst = await make_instance(db_session)
    await sync(db_session, inst, [dto(1, T1, 5.0)])
    await sync(db_session, inst, [dto(1, T1, 9.0)])

    (row,) = await samples(db_session)
    assert row.average_capacity == 5.0


@pytest.mark.asyncio
async def test_a_rule_without_a_timestamp_is_not_stored(db_session) -> None:
    inst = await make_instance(db_session)
    report = await sync(db_session, inst, [dto(1, None), dto(2, T1)])

    rows = await samples(db_session)
    assert len(rows) == 1
    assert report.capacity_samples_offered == 1


@pytest.mark.asyncio
async def test_no_timestamps_means_no_statement(db_session) -> None:
    inst = await make_instance(db_session)
    with recorded_statements(db_session, only="rule_capacity_sample") as statements:
        await sync(db_session, inst, [dto(1, None), dto(2, None)])
    assert statements == []


@pytest.mark.asyncio
async def test_the_batch_is_one_statement_whatever_the_rule_count(db_session) -> None:
    counts: dict[int, int] = {}
    for n in (3, 400):
        inst = await make_instance(db_session)
        with recorded_statements(db_session, only="rule_capacity_sample") as statements:
            await sync(db_session, inst, [dto(i, T1) for i in range(n)])
        counts[n] = len(statements)
    assert counts == {3: 1, 400: 1}
    assert await db_session.scalar(select(func.count()).select_from(RuleCapacitySample)) == 403


@pytest.mark.asyncio
async def test_deleting_a_rule_cascades_to_its_samples(db_session) -> None:
    from app.models.rule import AnalyticsRule

    inst = await make_instance(db_session)
    await sync(db_session, inst, [dto(1, T1)])
    rule = await db_session.scalar(select(AnalyticsRule))
    await db_session.delete(rule)
    await db_session.flush()
    assert await samples(db_session) == []
