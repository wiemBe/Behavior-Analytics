"""Property-watch evaluation and lifecycle, end to end through the mock provider (PW-3b).

Collection is the real one (mock provider, real builder, parser, fold); baselines are written
directly because a trained cell needs weeks of history. Each test steps one bucket per tick.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, update

from app.models.property_watch import PropertyWatch, PwAnomaly, PwValueBaseline
from tests.integration.pw_engine_support import (
    DROP,
    GONE,
    SPIKE,
    STEP,
    T0,
    Rig,
    add_cells,
    make_rig,
    plan_range,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ACTIVE = {"CANDIDATE", "OPEN", "RECOVERING"}


async def assert_one_active_incident(rig: Rig) -> None:
    """At most one active incident per (watch, detector, value_key), after every tick."""
    rows = (
        await rig.session.execute(  # type: ignore[attr-defined]
            select(
                PwAnomaly.watch_id,
                PwAnomaly.detector_type,
                PwAnomaly.value_key,
                func.count(),
            )
            .where(PwAnomaly.state.in_(ACTIVE))
            .group_by(PwAnomaly.watch_id, PwAnomaly.detector_type, PwAnomaly.value_key)
        )
    ).all()
    assert all(count == 1 for *_, count in rows), rows


async def tick_all(rig: Rig, start: int, stop: int) -> None:
    for k in range(start, stop):
        await rig.tick(k)
        await assert_one_active_incident(rig)


async def make_alice(session, **kw) -> Rig:  # type: ignore[no-untyped-def]
    rig = await make_rig(session, **kw)
    return rig


async def reliable(rig: Rig) -> None:
    await rig.session.execute(  # type: ignore[attr-defined]
        update(PwValueBaseline).values(is_reliable=True, sample_count=10)
    )
    await rig.session.commit()  # type: ignore[attr-defined]


# ------------------------------------------------------------------- the spike
async def test_spike_runs_from_insufficient_data_to_resolved(db_session) -> None:
    rig = await make_alice(db_session)
    alice = await rig.value("alice")
    await add_cells(db_session, alice, samples=3, reliable=False)
    plan_range(rig.plan, 2, 4, {"alice": 500, "bob": 400})
    rig.plan[4] = {"bob": 400}
    rig.truncate = {4}

    await tick_all(rig, 0, 1)
    # a thin cell is a stated learning period, never a silent NORMAL
    assert await rig.state_name("alice", SPIKE) == "INSUFFICIENT_DATA"

    await reliable(rig)
    await tick_all(rig, 1, 2)
    assert await rig.state_name("alice", SPIKE) == "NORMAL"

    await tick_all(rig, 2, 3)
    assert await rig.state_name("alice", SPIKE) == "CANDIDATE"
    assert rig.last_report is not None and rig.last_report.opened == 0
    await tick_all(rig, 3, 4)
    assert await rig.state_name("alice", SPIKE) == "OPEN"
    assert rig.last_report.opened == 1
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "OPEN" and anomaly.opened_at is not None
    assert anomaly.watch_version == 1 and anomaly.consecutive_buckets >= 2
    transitions_before = len(await rig.transitions(anomaly))

    # alice is unknown in a truncated bucket: the incident holds, it is not recovered
    await tick_all(rig, 4, 5)
    assert await rig.state_name("alice", SPIKE) == "OPEN"
    assert len(await rig.transitions(anomaly)) == transitions_before

    await tick_all(rig, 5, 6)
    assert await rig.state_name("alice", SPIKE) == "RECOVERING"
    await tick_all(rig, 6, 8)
    assert await rig.state_name("alice", SPIKE) == "RESOLVED"
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "RESOLVED" and anomaly.resolved_at is not None
    assert [str(t.to_state) for t in await rig.transitions(anomaly)] == [
        "CANDIDATE",
        "OPEN",
        "RECOVERING",
        "RESOLVED",
    ]
    state = await rig.state("alice", SPIKE)
    assert state is not None and state.active_anomaly_id is None

    # a recurrence is a new incident, never the old one revived
    plan_range(rig.plan, 8, 10, {"alice": 500, "bob": 400})
    await tick_all(rig, 8, 10)
    assert len(await rig.anomalies(SPIKE)) == 2
    assert (await rig.reload_watch()).evaluated_through == T0 + STEP * 9


async def test_one_abnormal_bucket_is_a_candidate_that_never_opens(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    rig.plan[1] = {"alice": 500, "bob": 400}
    opened = 0
    for k in range(4):
        opened += (await rig.tick(k)).opened
    assert opened == 0
    assert await rig.state_name("alice", SPIKE) == "NORMAL"
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "NORMAL" and anomaly.opened_at is None


async def test_a_relapse_while_recovering_reopens_the_same_incident(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    rig.plan[4] = {"alice": 500, "bob": 400}
    await tick_all(rig, 0, 4)
    assert await rig.state_name("alice", SPIKE) == "RECOVERING"
    await tick_all(rig, 4, 5)
    assert await rig.state_name("alice", SPIKE) == "OPEN"
    [anomaly] = await rig.anomalies(SPIKE)
    assert [str(t.to_state) for t in await rig.transitions(anomaly)] == [
        "CANDIDATE",
        "OPEN",
        "RECOVERING",
        "OPEN",
    ]


# ----------------------------------------------------------- the learning period
@pytest.mark.parametrize("reliable_flag", [False, True])
async def test_a_cell_below_the_minimum_never_drives_a_verdict(db_session, reliable_flag) -> None:
    rig = await make_alice(db_session)
    await add_cells(
        db_session, await rig.value("alice"), samples=7, reliable=reliable_flag, median=100
    )
    plan_range(rig.plan, 0, 6, {"alice": 900, "bob": 400})
    await tick_all(rig, 0, 6)
    assert await rig.state_name("alice", SPIKE) == "INSUFFICIENT_DATA"
    assert await rig.anomalies() == []


async def test_a_missing_cell_is_insufficient_data_not_normal(db_session) -> None:
    rig = await make_alice(db_session)
    await tick_all(rig, 0, 3)
    assert await rig.state_name("alice", SPIKE) == "INSUFFICIENT_DATA"
    assert await rig.anomalies() == []


# ------------------------------------------------------- drop and disappearance
async def test_drop_opens_on_exact_counts_and_never_on_a_lower_bound(db_session) -> None:
    rig = await make_alice(db_session, detectors=(DROP,))
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 2, 5, {"alice": 10, "bob": 400})
    await tick_all(rig, 0, 2)
    assert await rig.state_name("alice", DROP) == "NORMAL"
    await tick_all(rig, 2, 4)
    assert await rig.state_name("alice", DROP) == "OPEN"
    [anomaly] = await rig.anomalies(DROP)
    assert anomaly.observed_value == 10 and anomaly.observed_measurement == "EXACT"

    # the same low count in a lower-bound bucket proves nothing: hold, no new incident
    other = await make_alice(db_session, detectors=(DROP,))
    await add_cells(db_session, await other.value("alice"))
    plan_range(other.plan, 2, 6, {"alice": 10, "bob": 400})
    other.truncate = {2, 3, 4, 5}
    await tick_all(other, 0, 6)
    assert await other.anomalies() == []
    assert await other.state_name("alice", DROP) != "NORMAL"


async def test_disappearance_needs_measured_zeros_and_resolves_on_return(db_session) -> None:
    rig = await make_alice(db_session, detectors=(GONE,), pw_disappear_buckets=3)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 2, 6, {"bob": 400})
    await tick_all(rig, 0, 5)
    # three measured zeros are the first anomalous verdict, the confirmation follows
    assert await rig.state_name("alice", GONE) == "CANDIDATE"
    await tick_all(rig, 5, 6)
    assert await rig.state_name("alice", GONE) == "OPEN"
    [anomaly] = await rig.anomalies(GONE)
    assert anomaly.observed_value == 0.0
    await tick_all(rig, 6, 9)
    assert await rig.state_name("alice", GONE) == "RESOLVED"
    assert (await rig.anomalies(GONE))[0].state == "RESOLVED"


async def test_unknown_zeros_never_make_a_disappearance(db_session) -> None:
    rig = await make_alice(db_session, detectors=(GONE,), pw_disappear_buckets=3)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 2, 10, {"bob": 400})
    rig.truncate = set(range(2, 10))
    await tick_all(rig, 0, 10)
    assert await rig.anomalies() == []
    assert await rig.state_name("alice", GONE) != "NORMAL"


async def test_a_missing_bucket_inside_the_window_is_not_a_zero(db_session) -> None:
    rig = await make_alice(db_session, detectors=(GONE,), pw_disappear_buckets=3)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 2, 8, {"bob": 400})
    await tick_all(rig, 0, 3)
    # buckets 3 and 4 fail to collect; the watermark holds and nothing is evaluated
    rig.fail = {3, 4}
    await rig.collect(4)
    report = await rig.evaluate(4)
    assert report.buckets_evaluated == 0
    rig.fail = set()
    await tick_all(rig, 5, 8)
    assert await rig.state_name("alice", GONE) in {"CANDIDATE", "OPEN"}


# ------------------------------------------------------- failure never resolves
async def test_a_failed_collection_never_resolves_and_evaluation_resumes(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await tick_all(rig, 0, 3)
    assert await rig.state_name("alice", SPIKE) == "OPEN"
    [anomaly] = await rig.anomalies(SPIKE)
    seen = len(await rig.transitions(anomaly))
    through = (await rig.reload_watch()).evaluated_through

    rig.fail = {3, 4, 5}
    for k in (3, 4, 5):
        await rig.tick(k)
        assert await rig.state_name("alice", SPIKE) == "OPEN"
    assert len(await rig.transitions(anomaly)) == seen
    assert (await rig.reload_watch()).evaluated_through == through

    rig.fail = set()
    await rig.tick(8)  # backfills 3..8 in one run, all healthy
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "RESOLVED"
    assert [str(t.to_state) for t in (await rig.transitions(anomaly))[seen:]] == [
        "RECOVERING",
        "RESOLVED",
    ]
    assert await rig.state_name("alice", SPIKE) == "NORMAL"
    assert (await rig.reload_watch()).evaluated_through == T0 + STEP * 8


async def test_a_disabled_watch_keeps_its_incident_untouched(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await tick_all(rig, 0, 3)
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(enabled=False)
    )
    await db_session.commit()
    report = await rig.evaluate(5)
    assert report.buckets_evaluated == 0
    assert await rig.state_name("alice", SPIKE) == "OPEN"


async def test_a_blocking_collection_status_blocks_verdicts_and_is_kept(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    await rig.collect(0)
    await db_session.execute(
        update(PropertyWatch)
        .where(PropertyWatch.id == rig.watch.id)
        .values(last_status="SCOPE_STALE", last_status_reason="stale")
    )
    await db_session.commit()
    await rig.evaluate(0)
    watch = await rig.reload_watch()
    assert watch.last_status == "SCOPE_STALE" and watch.last_status_reason == "stale"
    assert watch.evaluated_through == T0
    assert await rig.state_name("alice", SPIKE) == "INSUFFICIENT_DATA"


async def test_the_watch_status_follows_its_incidents(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await tick_all(rig, 0, 1)
    assert (await rig.reload_watch()).last_status == "OK"
    await tick_all(rig, 1, 3)
    assert (await rig.reload_watch()).last_status == "OPEN"
    await tick_all(rig, 3, 4)
    assert (await rig.reload_watch()).last_status == "RECOVERING"
    await tick_all(rig, 4, 6)
    assert (await rig.reload_watch()).last_status == "OK"


# --------------------------------------------------------------- watch_version
async def test_a_version_bump_resolves_the_old_incident_and_starts_fresh(db_session) -> None:
    rig = await make_alice(db_session)
    old = await rig.value("alice")
    await add_cells(db_session, old)
    plan_range(rig.plan, 1, 3, {"alice": 500, "bob": 400})
    await tick_all(rig, 0, 3)
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "OPEN"

    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
    )
    await db_session.commit()
    plan_range(rig.plan, 3, 6, {"alice": 500, "bob": 400})
    report = await rig.tick(3)
    assert report.version_resolved == 1
    [anomaly] = await rig.anomalies(SPIKE)
    assert anomaly.state == "RESOLVED" and anomaly.watch_version == 1
    last = (await rig.transitions(anomaly))[-1]
    assert str(last.to_state) == "RESOLVED" and "watch definition changed" in (last.reason or "")

    # the version-2 value has no baseline of its own: a version-1 cell is never compared
    fresh = await rig.value("alice", version=2)
    assert fresh.id != old.id
    await add_cells(db_session, fresh, version=1)
    await tick_all(rig, 4, 6)
    assert await rig.state_name_for(fresh, SPIKE) == "INSUFFICIENT_DATA"
    assert len(await rig.anomalies(SPIKE)) == 1
    # the resolved old state is not rewritten on every later run
    second = await rig.tick(6)
    assert second.version_resolved == 0


async def test_a_value_is_not_judged_at_buckets_before_it_was_tracked(db_session) -> None:
    rig = await make_alice(db_session)
    await add_cells(db_session, await rig.value("alice"))
    rig.plan[3] = {"alice": 100, "bob": 400, "dave": 50}
    await rig.collect(3)  # one run backfills buckets 0..3; dave is admitted at bucket 3 only
    report = await rig.evaluate(3)
    assert report.buckets_evaluated == 4
    # alice and bob are judged in 4 buckets each, dave in the 1 where he was observed
    assert report.values_evaluated == 9
    dave = await rig.state("dave", SPIKE)
    assert dave is not None and dave.last_interval_start == T0 + STEP * 3
