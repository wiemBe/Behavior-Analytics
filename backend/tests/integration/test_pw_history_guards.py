"""PW-13c2: bootstrapped buckets are learning evidence, never judged intervals."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.collectors.pw_collector import PwCollector
from app.models.enums import (
    PwBucketMeasurement,
    PwBucketOrigin,
    PwHistoryState,
    PwPinnedMeasurement,
    PwWatchStatus,
)
from app.models.property_watch import PropertyWatch, PwBucket, PwValue
from app.property_watch.detectors import PwDetectionContext, detect_new_value
from app.property_watch.engine import PwAnomalyEngine, _bucket_fact
from app.providers.mock import MockQRadarProvider
from app.repositories.property_watch import PropertyWatchRepository
from tests.integration.factories import make_instance
from tests.integration.pw_engine_support import (
    GONE,
    NEW_VALUE,
    STEP,
    T0,
    add_cells,
    history_buckets,
    make_rig,
    plan_range,
)
from tests.integration.test_pw_collection import add_watch

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _bucket(  # type: ignore[no-untyped-def]
    watch: PropertyWatch,
    at,
    *,
    origin: PwBucketOrigin,
    parsed: int = 100,
) -> PwBucket:
    return PwBucket(
        watch_id=watch.id,
        bucket_start=at,
        bucket_seconds=300,
        watch_version=watch.watch_version,
        origin=origin,
        total_events=100,
        parsed_events=parsed,
        unparsed_events=100 - parsed,
        untracked_events=parsed,
        values_measurement=PwBucketMeasurement.EXACT,
        pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
        totals_complete=True,
        observed_values=1,
        tracked_values_observed=0,
        admitted_values=0,
        below_admission_threshold=1,
        rejected_over_quota=0,
        admission_truncated=False,
        scope_member_count=1,
        scope_changed=False,
        in_maintenance=False,
        query_provenance={"history": origin is PwBucketOrigin.HISTORY},
        collected_at=at,
    )


@pytest.mark.parametrize(
    ("collected", "evaluated"),
    [
        (None, None),  # guard 1: READING keeps both watermarks NULL
        (T0, None),  # a half-written hand-off still cannot expose history
        (T0, T0),  # guard 2: the hand-off seed is the last history bucket
        (T0 + STEP, T0 - STEP),  # corrupt watermarks still meet guard 3
    ],
)
async def test_history_buckets_are_never_pending(db_session, collected, evaluated) -> None:
    instance = await make_instance(db_session)
    watch = await add_watch(db_session, instance)
    watch.collected_through = collected
    watch.evaluated_through = evaluated
    watch.history_state = PwHistoryState.READING
    db_session.add(_bucket(watch, T0, origin=PwBucketOrigin.HISTORY))
    await db_session.commit()

    pending = await PropertyWatchRepository(db_session).pending_buckets(instance.id, limit=10)
    assert pending == []


async def test_history_neither_starts_nor_extends_the_unparsed_streak(db_session) -> None:
    instance = await make_instance(db_session)
    only_history = await add_watch(db_session, instance, through=T0)
    mixed = await add_watch(db_session, instance, through=T0)
    db_session.add_all(
        [
            _bucket(
                only_history,
                T0 - index * STEP,
                origin=PwBucketOrigin.HISTORY,
                parsed=0,
            )
            for index in range(3)
        ]
        + [
            _bucket(mixed, T0 - 2 * STEP, origin=PwBucketOrigin.LIVE, parsed=0),
            _bucket(mixed, T0 - STEP, origin=PwBucketOrigin.HISTORY, parsed=0),
            _bucket(mixed, T0, origin=PwBucketOrigin.LIVE, parsed=0),
        ]
    )
    await db_session.commit()

    streaks = await PwCollector(
        db_session, MockQRadarProvider(seed=1)
    )._unparsed_streaks([only_history.id, mixed.id], since=T0 - timedelta(hours=1))
    assert only_history.id not in streaks
    assert streaks[mixed.id] == 2


async def test_history_zeros_do_not_start_or_extend_a_disappearance_streak(db_session) -> None:
    rig = await make_rig(
        db_session,
        detectors=(GONE,),
        pw_disappear_buckets=3,
    )
    await add_cells(db_session, await rig.value("alice"))
    rig.watch.history_end = T0
    rig.watch.history_state = PwHistoryState.COMPLETE
    await history_buckets(
        db_session,
        rig.watch,
        start=T0 - 3 * STEP,
        stop=T0,
        origin=PwBucketOrigin.HISTORY,
    )
    plan_range(rig.plan, 0, 4, {"bob": 400})

    await rig.ticks(0, 3)
    assert await rig.state_name("alice", GONE) == "CANDIDATE"
    assert all(str(row.state) != "OPEN" for row in await rig.anomalies(GONE))

    await rig.ticks(3, 4)
    assert await rig.state_name("alice", GONE) == "OPEN"
    [opened] = await rig.anomalies(GONE)
    assert opened.anomaly_start == T0 + 2 * STEP


async def test_history_first_seen_is_established_but_a_live_first_seen_value_is_new(
    db_session,
) -> None:
    rig = await make_rig(
        db_session,
        detectors=(NEW_VALUE,),
        values=("bob",),
        pw_new_value_history_days=1,
    )
    rig.default = {"bob": 400}
    rig.watch.history_end = T0
    rig.watch.history_state = PwHistoryState.COMPLETE
    db_session.add(
        PwValue(
            watch_id=rig.watch.id,
            watch_version=rig.watch.watch_version,
            value_key="mallory",
            component_0="mallory",
            pinned=False,
            active=True,
            first_seen_at=T0 - STEP,
            last_seen_at=T0 - STEP,
            tracked_since=T0 - STEP,
        )
    )
    await history_buckets(
        db_session,
        rig.watch,
        start=T0 - timedelta(days=1),
        stop=T0,
        origin=PwBucketOrigin.HISTORY,
    )
    plan_range(rig.plan, 0, 3, {"bob": 400, "mallory": 30, "eve": 30})

    await rig.ticks(0, 1)
    assert await rig.state_name("mallory", NEW_VALUE) == "NORMAL"
    assert await rig.anomalies(NEW_VALUE) == []

    mallory = await rig.value("mallory")
    current_row = await db_session.scalar(
        select(PwBucket).where(
            PwBucket.watch_id == rig.watch.id,
            PwBucket.bucket_start == T0,
        )
    )
    assert current_row is not None
    current = _bucket_fact(current_row)
    engine = PwAnomalyEngine(db_session, settings=rig.settings)
    novelty = engine._established(rig.watch, mallory, current)
    verdict = detect_new_value(
        PwDetectionContext(
            watch_status=PwWatchStatus.OK,
            bucket=current,
            reading=engine._reading(mallory, current, {(mallory.id, T0): 30}),
            thresholds=engine._thresholds(),
            novelty=novelty,
        )
    )
    assert (T0 - STEP).isoformat() in verdict.evidence.reason
    assert T0.isoformat() in verdict.evidence.reason
    capped = T0 - STEP * rig.settings.pw_new_value_window_buckets
    assert capped.isoformat() not in verdict.evidence.reason

    await rig.ticks(1, 3)
    anomalies = await rig.anomalies(NEW_VALUE)
    assert [row.value_key for row in anomalies] == ["eve"]
    assert str(anomalies[0].state) == "OPEN"

    values = list(
        (
            await db_session.scalars(
                select(PwValue).where(PwValue.watch_id == rig.watch.id)
            )
        ).all()
    )
    first_seen = {value.value_key: value.first_seen_at for value in values}
    assert first_seen["mallory"] == T0 - STEP
    assert first_seen["eve"] == T0
