"""Property-watch collection issues a bounded number of SQL statements (design §G.14.2).

Mirrors `test_waf_scale.py` and `test_ips_scale.py`: every statement the driver executes during
one collection tick is counted at two sizes. A query per watch, per value or per tuple makes
the counts unequal across sizes long before any absolute bound is breached.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, insert, select

from app.models.enums import PwScopeKind
from app.models.fleet import LogSourceGroup
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.providers.mock import MockQRadarProvider
from tests.integration.factories import make_instance, make_log_source
from tests.integration.test_pw_collection import (
    NOW,
    STEP,
    add_watch,
    cfg,
    collect,
    pin_cadence,
)
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

#: The design target (§G.14.2) for collection and evaluation together; collection alone is
#: well under it, and the exact count is pinned by equality across sizes.
STATEMENT_CEILING = 40
#: What one tick costs, pinned: the enabled watches, the cadence row, the sources, the groups
#: (group scopes only), the tracked values, the scope history, and one write for each of
#: values, buckets, metrics and the watches. Collection alone, so well under the ceiling.
EXPECTED = {PwScopeKind.LOG_SOURCE: 9, PwScopeKind.LOG_SOURCE_GROUP: 10}
#: Twelve buckets: one cadence window at the default.
START = NOW - timedelta(hours=1, minutes=7)


def many(count: int):  # type: ignore[no-untyped-def]
    def script(key: int, names: tuple[str, ...]) -> list[dict]:
        return [
            {"values": dict.fromkeys(names, f"user{i:03d}"), "events": 10}
            for i in range(count)
        ]

    return script


async def seeded(  # type: ignore[no-untyped-def]
    session, *, watches: int, values: int, kind: PwScopeKind
):
    """`watches` watches of one kind, each with `values` tracked values and a window of 12
    buckets, and the provider that produces those values in every bucket."""
    instance = await make_instance(session)
    if kind is PwScopeKind.LOG_SOURCE_GROUP:
        session.add(
            LogSourceGroup(
                instance_id=instance.id, qradar_id=1, name="g", parent_qradar_id=None,
                assignable=True, synced_at=NOW,
            )
        )
        await make_log_source(session, instance, qradar_id=11, group_ids=[1])
        await session.flush()
    made = []
    for index in range(watches):
        if kind is PwScopeKind.LOG_SOURCE_GROUP:
            watch = await add_watch(
                session, instance, scope_kind=kind, scope_group_id=1,
                through=START - STEP,
            )
        else:
            source = await make_log_source(session, instance, qradar_id=100 + index)
            watch = await add_watch(session, instance, source=source, through=START - STEP)
        made.append(watch)
    await session.flush()
    await session.execute(
        insert(PwValue),
        [
            dict(
                watch_id=watch.id, watch_version=1, value_key=f"user{i:03d}",
                component_0=f"user{i:03d}", pinned=False, active=True,
                first_seen_at=START - timedelta(days=1), last_seen_at=START - STEP,
                tracked_since=START - timedelta(days=1), truncated=False,
            )
            for watch in made
            for i in range(values)
        ],
    )
    await session.commit()
    provider = MockQRadarProvider(seed=1)
    provider.pw_script = many(values)
    return instance, provider


async def count_statements(session, instance, provider) -> list[str]:  # type: ignore[no-untyped-def]
    settings = cfg(
        pw_max_searches_per_tick=200,
        pw_max_searches_per_watch_per_tick=12,
        pw_max_active_values_per_watch=300,
        pw_max_values_per_bucket=300,
    )
    with recorded_statements(session) as statements:
        await collect(session, instance, provider, settings=settings)
    return statements


@pytest.mark.parametrize("kind", [PwScopeKind.LOG_SOURCE, PwScopeKind.LOG_SOURCE_GROUP])
async def test_collection_at_the_default_ceiling_is_bulk(db_session, kind) -> None:
    counts: dict[tuple[int, int], int] = {}
    await pin_cadence(db_session, 3600)  # pre-D-28 default
    for watches, values in ((1, 50), (10, 50), (1, 200), (10, 200)):
        instance, provider = await seeded(db_session, watches=watches, values=values, kind=kind)
        statements = await count_statements(db_session, instance, provider)
        counts[(watches, values)] = len(statements)
        # the work really was done at this size: 12 buckets and every value in each
        assert (
            await db_session.scalar(
                select(func.count()).select_from(PwBucket).where(
                    PwBucket.bucket_start >= START
                )
            )
            >= 12 * watches
        )
        stored = await db_session.scalar(
            select(func.count()).select_from(PwValueMetric).where(
                PwValueMetric.bucket_start >= START
            )
        )
        assert stored >= 12 * watches * values
        await db_session.execute(PwValueMetric.__table__.delete())
        await db_session.execute(PwBucket.__table__.delete())
        await db_session.execute(PwValue.__table__.delete())
        await db_session.commit()
    assert len(set(counts.values())) == 1, counts
    assert next(iter(counts.values())) <= STATEMENT_CEILING, counts
    assert set(counts.values()) == {EXPECTED[kind]}, counts


async def test_collection_with_pins_and_a_combination_is_bulk_too(db_session) -> None:
    counts = []
    for watches in (1, 10):
        instance = await make_instance(db_session)
        made = []
        for index in range(watches):
            source = await make_log_source(db_session, instance, qradar_id=500 + index)
            made.append(
                await add_watch(
                    db_session, instance, source=source, through=START - STEP,
                    properties=[
                        {"kind": "NORMALIZED", "name": "username"},
                        {"kind": "CUSTOM_PROPERTY", "name": "Action"},
                    ],
                    pinned=[["user000", "x"], ["user001", "x"]],
                )
            )
        await db_session.commit()
        assert made
        provider = MockQRadarProvider(seed=1)
        provider.pw_script = many(20)
        counts.append(len(await count_statements(db_session, instance, provider)))
        await db_session.execute(PwValueMetric.__table__.delete())
        await db_session.execute(PwBucket.__table__.delete())
        await db_session.execute(PwValue.__table__.delete())
        await db_session.commit()
    assert counts[0] == counts[1] <= STATEMENT_CEILING, counts


async def test_the_count_does_not_depend_on_how_many_buckets_are_collected(db_session) -> None:
    results = []
    for through in (START - STEP, NOW - timedelta(minutes=12)):
        instance, provider = await seeded(
            db_session, watches=2, values=20, kind=PwScopeKind.LOG_SOURCE
        )
        await db_session.execute(
            PwValue.__table__.update().values(last_seen_at=through)
        )
        await db_session.execute(
            PropertyWatch.__table__.update().values(collected_through=through)
        )
        await db_session.commit()
        results.append(len(await count_statements(db_session, instance, provider)))
        await db_session.execute(PwValueMetric.__table__.delete())
        await db_session.execute(PwBucket.__table__.delete())
        await db_session.execute(PwValue.__table__.delete())
        await db_session.commit()
    assert results[0] == results[1], results
