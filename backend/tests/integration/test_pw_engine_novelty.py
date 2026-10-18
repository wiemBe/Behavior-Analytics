"""PW_NEW_VALUE and PW_NEW_COMBINATION through collection and the engine (PW-3b)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import insert

from app.models.enums import PwBucketMeasurement
from app.models.property_watch import PwBucket, PwValue, PwValueMetric
from app.repositories.property_watch import PropertyWatchRepository
from tests.integration.pw_engine_support import (
    NEW_COMBINATION,
    NEW_VALUE,
    STEP,
    T0,
    USER_ACTION,
    Rig,
    history_buckets,
    make_rig,
    plan_range,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DAY = timedelta(days=1)


async def single(session, **kw) -> Rig:  # type: ignore[no-untyped-def]
    rig = await make_rig(
        session,
        detectors=(NEW_VALUE,),
        values=("bob",),
        pw_new_value_history_days=1,
        **kw,
    )
    rig.default = {"bob": 400}
    return rig


async def test_a_new_value_with_a_complete_history_opens(db_session) -> None:
    rig = await single(db_session)
    await history_buckets(db_session, rig.watch, start=T0 - DAY, stop=T0)
    plan_range(rig.plan, 0, 4, {"bob": 400, "mallory": 30})
    await rig.ticks(0, 1)
    # 30 events in one bucket is not yet material (two buckets with traffic are needed)
    assert await rig.state_name("mallory", NEW_VALUE) == "NORMAL"
    await rig.ticks(1, 2)
    assert await rig.state_name("mallory", NEW_VALUE) == "CANDIDATE"
    await rig.ticks(2, 3)
    assert await rig.state_name("mallory", NEW_VALUE) == "OPEN"
    [anomaly] = await rig.anomalies(NEW_VALUE)
    assert anomaly.value_key == "mallory" and anomaly.observed_value >= 60
    assert anomaly.details["extra"]["history_buckets_verified"] == 288
    assert await rig.state_name("bob", NEW_VALUE) is not None
    assert all(a.value_key == "mallory" for a in await rig.anomalies())


async def test_a_new_value_outside_its_window_is_established_and_recovers(db_session) -> None:
    rig = await single(db_session, pw_new_value_window_buckets=3)
    await history_buckets(db_session, rig.watch, start=T0 - DAY, stop=T0)
    plan_range(rig.plan, 0, 12, {"bob": 400, "mallory": 30})
    await rig.ticks(0, 12)
    [anomaly] = await rig.anomalies(NEW_VALUE)
    assert anomaly.state == "RESOLVED"


@pytest.mark.parametrize(
    "case",
    ["a missing bucket", "a lower-bound bucket", "less than a full history", "a changed version"],
)
async def test_an_incomplete_history_is_insufficient_never_new(db_session, case) -> None:
    rig = await single(db_session)
    gap = T0 - timedelta(hours=3)
    if case == "a missing bucket":
        await history_buckets(
            db_session, rig.watch, start=T0 - DAY, stop=T0, skip=lambda at: at == gap
        )
    elif case == "a lower-bound bucket":
        await history_buckets(db_session, rig.watch, start=T0 - DAY, stop=gap)
        await history_buckets(
            db_session,
            rig.watch,
            start=gap,
            stop=gap + STEP,
            measurement=PwBucketMeasurement.LOWER_BOUND,
        )
        await history_buckets(db_session, rig.watch, start=gap + STEP, stop=T0)
    elif case == "less than a full history":
        await history_buckets(db_session, rig.watch, start=T0 - DAY // 2, stop=T0)
    else:
        await history_buckets(db_session, rig.watch, start=T0 - DAY, stop=T0)
        await db_session.execute(
            PwBucket.__table__.update()
            .where(PwBucket.bucket_start == gap, PwBucket.watch_id == rig.watch.id)
            .values(watch_version=0 + 2)
        )
        await db_session.commit()
    plan_range(rig.plan, 0, 6, {"bob": 400, "mallory": 90})
    await rig.ticks(0, 6)
    assert await rig.anomalies() == []
    assert await rig.state_name("mallory", NEW_VALUE) == "INSUFFICIENT_DATA"


async def test_a_pinned_value_is_never_new(db_session) -> None:
    rig = await single(db_session)
    await history_buckets(db_session, rig.watch, start=T0 - DAY, stop=T0)
    plan_range(rig.plan, 0, 4, {"bob": 400, "mallory": 90})
    rig.watch.pinned_values = [["mallory"]]
    await db_session.commit()
    await rig.ticks(0, 4)
    assert await rig.anomalies() == []


async def test_a_new_combination_of_known_parts_and_a_new_value_are_told_apart(
    db_session,
) -> None:
    rig = await make_rig(
        db_session,
        detectors=(NEW_VALUE, NEW_COMBINATION),
        properties=USER_ACTION,
        values=("alice|login", "bob|logout"),
        pw_new_value_history_days=1,
    )
    rig.default = {"alice|login": 50, "bob|logout": 50}
    await history_buckets(db_session, rig.watch, start=T0 - DAY, stop=T0)
    seen = {text: await rig.value(text) for text in ("alice|login", "bob|logout")}
    await db_session.execute(
        insert(PwValueMetric),
        [
            {
                "pw_value_id": value.id,
                "bucket_start": T0 - STEP * n,
                "events": 20,
                "bucket_seconds": 300,
            }
            for value in seen.values()
            for n in (1, 2, 3)
        ],
    )
    await db_session.commit()
    plan_range(
        rig.plan,
        0,
        5,
        {"alice|login": 50, "bob|logout": 50, "alice|logout": 30, "carol|login": 30},
    )
    await rig.ticks(0, 4)
    combos = await rig.anomalies(NEW_COMBINATION)
    values = await rig.anomalies(NEW_VALUE)
    assert [a.value_key for a in combos] == ["alice\x1flogout"]
    assert [a.value_key for a in values] == ["carol\x1flogin"]
    assert combos[0].state == "OPEN" and values[0].state == "OPEN"


async def test_a_combination_watch_with_one_property_has_no_combination_state(db_session) -> None:
    rig = await make_rig(db_session, detectors=(NEW_COMBINATION,), values=("bob",))
    rig.default = {"bob": 400}
    await rig.ticks(0, 2)
    assert await rig.state("bob", NEW_COMBINATION) is None


async def test_the_component_lookup_keeps_exact_and_lower_bound_apart(db_session) -> None:
    rig = await make_rig(db_session, properties=USER_ACTION, values=("alice|login",))
    value = await rig.value("alice|login")
    for n, measurement in ((5, PwBucketMeasurement.LOWER_BOUND), (3, PwBucketMeasurement.EXACT)):
        await history_buckets(
            db_session,
            rig.watch,
            start=T0 - STEP * n,
            stop=T0 - STEP * (n - 1),
            measurement=measurement,
        )
        db_session.add(
            PwValueMetric(
                pw_value_id=value.id, bucket_start=T0 - STEP * n, events=7, bucket_seconds=300
            )
        )
    await db_session.commit()
    repo = PropertyWatchRepository(db_session)
    rows = await repo.component_occurrences(
        [(rig.watch.id, 1)], {"alice", "login", "other"}, start=T0 - DAY, end=T0
    )
    got = {(c, p, m): at for _w, _v, c, p, m, at in rows}
    assert got == {
        ("alice", 0, "LOWER_BOUND"): T0 - STEP * 5,
        ("alice", 0, "EXACT"): T0 - STEP * 3,
        ("login", 1, "LOWER_BOUND"): T0 - STEP * 5,
        ("login", 1, "EXACT"): T0 - STEP * 3,
    }
    assert await repo.component_occurrences([(rig.watch.id, 1)], set(), start=T0, end=T0) == []
    assert isinstance(value, PwValue)
