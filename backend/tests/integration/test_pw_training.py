"""Baseline training and the scheduled pipeline around it (PW-3b)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.models.property_watch import PropertyWatch, PwAnomaly, PwBucket, PwValueBaseline
from app.property_watch.training import PwBaselineTrainer
from app.services.pw_pipeline import PwInstancePipeline
from tests.integration.pw_engine_support import (
    SPIKE,
    STEP,
    T0,
    Rig,
    make_rig,
    now_of,
    plan_range,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

LATER = T0 + timedelta(hours=3)


async def cells_of(rig: Rig, text: str) -> list[PwValueBaseline]:
    value = await rig.value(text)
    return list(
        (
            await rig.session.scalars(  # type: ignore[attr-defined]
                select(PwValueBaseline)
                .where(PwValueBaseline.pw_value_id == value.id)
                .order_by(PwValueBaseline.weekday, PwValueBaseline.hour)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def train(rig: Rig, **kw):  # type: ignore[no-untyped-def]
    report = await PwBaselineTrainer(rig.session, settings=rig.settings).rebuild_due(  # type: ignore[arg-type]
        rig.instance, now=kw.get("now", LATER)
    )
    await rig.session.commit()  # type: ignore[attr-defined]
    return report


async def mark_evaluated(rig: Rig, k: int) -> None:
    await rig.session.execute(  # type: ignore[attr-defined]
        update(PropertyWatch)
        .where(PropertyWatch.id == rig.watch.id)
        .values(evaluated_through=T0 + STEP * k)
    )
    await rig.session.commit()  # type: ignore[attr-defined]


async def test_an_hour_of_exact_buckets_gives_a_reliable_cell(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await rig.ticks(0, 12)
    report = await train(rig)
    assert report.values_rebuilt >= 1 and report.chunks == 1
    [cell] = await cells_of(rig, "alice")
    assert (cell.weekday, cell.hour) == (2, 10)
    assert cell.sample_count == 12 and cell.is_reliable and cell.median == 100
    assert cell.watch_version == 1 and cell.metric_semantics == "property_watch/value_events/v1"
    assert cell.computed_at == LATER and cell.exclusion_counts is not None


async def test_a_thin_cell_is_stored_and_never_drives_a_verdict(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    plan_range(rig.plan, 0, 12, {"alice": 900, "bob": 400})
    await rig.ticks(0, 5)
    await train(rig)
    [cell] = await cells_of(rig, "alice")
    assert cell.sample_count == 5 and not cell.is_reliable
    await rig.ticks(5, 8)
    assert await rig.state_name("alice", SPIKE) == "INSUFFICIENT_DATA"
    assert await rig.anomalies() == []


async def test_maintenance_lower_bound_and_incident_buckets_are_excluded_and_tallied(
    db_session,
) -> None:
    rig = await make_rig(db_session, values=("alice",))
    rig.truncate = {3}
    await rig.ticks(0, 12)
    await db_session.execute(
        update(PwBucket)
        .where(PwBucket.watch_id == rig.watch.id, PwBucket.bucket_start.in_([T0, T0 + STEP]))
        .values(in_maintenance=True)
    )
    value = await rig.value("alice")
    db_session.add(
        PwAnomaly(
            watch_id=rig.watch.id,
            pw_value_id=value.id,
            value_key="alice",
            detector_type=SPIKE.value,
            fingerprint="f",
            severity="HIGH",
            state="RESOLVED",
            detected_at=T0 + STEP * 6,
            anomaly_end=T0 + STEP * 7,
            resolved_at=T0 + STEP * 8,
            watch_version=1,
        )
    )
    await db_session.commit()
    await train(rig)
    [cell] = await cells_of(rig, "alice")
    # 12 buckets - 2 maintenance - 1 lower bound - 3 inside the incident (6..8)
    assert cell.sample_count == 6
    counts = cell.exclusion_counts
    assert (counts["maintenance"], counts["lower_bound"], counts["known_anomaly"]) == (2, 1, 3)


async def test_training_stops_at_the_evaluation_watermark(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await rig.ticks(0, 12)
    await mark_evaluated(rig, 5)
    await train(rig)
    [cell] = await cells_of(rig, "alice")
    assert cell.sample_count == 6


async def test_a_watch_that_was_never_evaluated_trains_nothing(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await rig.collect(0)
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(evaluated_through=None)
    )
    await db_session.commit()
    report = await train(rig)
    assert report.values_rebuilt == 0 and await cells_of(rig, "alice") == []


async def test_the_least_recently_computed_values_go_first_and_each_run_is_bounded(
    db_session,
) -> None:
    names = tuple(f"user{i}" for i in range(5))
    rig = await make_rig(db_session, values=names, pw_baseline_values_per_run=2)
    rig.default = dict.fromkeys(names, 30)
    await rig.ticks(0, 4)
    chosen = []
    for run in range(3):
        report = await train(rig, now=LATER + timedelta(hours=run))
        assert report.values_rebuilt == 2
        computed = {
            name: (await cells_of(rig, name))[0].computed_at if await cells_of(rig, name) else None
            for name in names
        }
        chosen.append({n for n, at in computed.items() if at == LATER + timedelta(hours=run)})
    assert all(len(group) == 2 for group in chosen)
    assert chosen[0].isdisjoint(chosen[1])
    # the third run takes the one never computed, then the oldest
    assert (set(names) - (chosen[0] | chosen[1])) <= chosen[2]


async def test_a_new_version_trains_only_on_its_own_buckets(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await rig.ticks(0, 6)
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(watch_version=2)
    )
    await db_session.commit()
    await rig.ticks(6, 12)
    await train(rig)
    fresh = await cells_of(rig, "alice")
    [old] = [c for c in fresh if c.watch_version == 1] or [None]
    new_value = await rig.value("alice", version=2)
    new_cells = list(
        (
            await db_session.scalars(
                select(PwValueBaseline)
                .where(PwValueBaseline.pw_value_id == new_value.id)
                .execution_options(populate_existing=True)
            )
        ).all()
    )
    assert [(c.watch_version, c.sample_count) for c in new_cells] == [(2, 6)]
    assert old is None


# ------------------------------------------------------------------ the pipeline
async def test_the_pipeline_collects_then_evaluates_then_trains(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    pipeline = PwInstancePipeline(
        db_session, rig.provider, settings=rig.settings, clock=lambda: now_of(11)
    )
    report = await pipeline.run(rig.instance)
    await db_session.commit()
    assert report.steps == ["collect", "evaluate", "train"]
    assert report.evaluation is not None and report.training is not None
    # training read the buckets this very run had just evaluated, not an earlier watermark
    assert report.evaluation.buckets_evaluated == 12 and report.training.cells_written >= 1
    payload = report.as_dict()
    # PW-2b's dispatch payload is kept; the truthful list sits beside it
    assert payload["pipeline"] == {"steps": ["collect"]}
    assert payload["steps_run"] == ["collect", "evaluate", "train"]
    assert payload["evaluation_error"] is None and payload["training_error"] is None


class Boom(RuntimeError):
    pass


class FailingEvaluation:
    def __init__(self, *args, **kw) -> None:  # type: ignore[no-untyped-def]
        pass

    async def evaluate(self, instance):  # type: ignore[no-untyped-def]
        raise Boom("secret-user-value")


class FailingTraining:
    def __init__(self, *args, **kw) -> None:  # type: ignore[no-untyped-def]
        pass

    async def rebuild_due(self, instance, *, now):  # type: ignore[no-untyped-def]
        raise Boom("secret-user-value")


async def test_a_failed_evaluation_keeps_the_collection_and_skips_training(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    pipeline = PwInstancePipeline(
        db_session,
        rig.provider,
        settings=rig.settings,
        clock=lambda: now_of(0),
        engine_factory=FailingEvaluation,  # type: ignore[arg-type]
    )
    report = await pipeline.run(rig.instance)
    await db_session.commit()
    assert report.steps == ["collect"] and report.training is None
    # the error is the class only: a database error can echo a value
    assert report.evaluation_error == "Boom"
    watch = await rig.reload_watch()
    assert watch.collected_through == T0 and watch.evaluated_through == T0 - STEP
    assert (
        await db_session.scalar(select(PwBucket).where(PwBucket.watch_id == watch.id))
    ) is not None


async def test_a_failed_training_keeps_the_evaluation(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    pipeline = PwInstancePipeline(
        db_session,
        rig.provider,
        settings=rig.settings,
        clock=lambda: now_of(0),
        trainer_factory=FailingTraining,  # type: ignore[arg-type]
    )
    report = await pipeline.run(rig.instance)
    await db_session.commit()
    assert report.steps == ["collect", "evaluate"] and report.training_error == "Boom"
    assert (await rig.reload_watch()).evaluated_through == T0


async def test_a_collection_failure_leaves_evaluation_where_it_was(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    rig.fail = {0}
    pipeline = PwInstancePipeline(
        db_session, rig.provider, settings=rig.settings, clock=lambda: now_of(0)
    )
    report = await pipeline.run(rig.instance)
    await db_session.commit()
    assert report.evaluation is not None and report.evaluation.buckets_evaluated == 0
    watch = await rig.reload_watch()
    assert watch.evaluated_through == T0 - STEP
