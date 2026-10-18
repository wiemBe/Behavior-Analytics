"""PW collection plus evaluation, and training, stay constant in SQL cost (design §G.14.2).

The `test_waf_scale` / `test_ips_scale` shape: every statement the driver executes during one
tick is counted at several sizes. A query per watch, per value or per tuple makes the counts
unequal across sizes long before the absolute bound of 40 is reached. The fixtures give the
engine its worst case: every value is judged by all five detectors and every judgement writes a
state, an incident and a transition.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, insert, select, update

from app.models.enums import PwAnomalyType, PwScopeKind
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwAnomalyTransition,
    PwDetectorState,
    PwValue,
    PwValueBaseline,
)
from app.property_watch.baseline import PW_VALUE_EVENTS_SEMANTICS
from app.property_watch.engine import PwAnomalyEngine
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC
from app.property_watch.training import PwBaselineTrainer
from tests.integration.pw_engine_support import (
    NEW_COMBINATION,
    T0,
    USER_ACTION,
    history_buckets,
    make_rig,
    plan_range,
)
from tests.integration.test_pw_collection import NOW, STEP, cfg, collect, pin_cadence
from tests.integration.test_pw_scale import START, seeded
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

CEILING = 40
ALL_FIVE = [d.value for d in PwAnomalyType]


def scale_settings(**extra: object):  # type: ignore[no-untyped-def]
    return cfg(
        pw_max_searches_per_tick=200,
        pw_max_searches_per_watch_per_tick=12,
        pw_max_active_values_per_watch=300,
        pw_max_values_per_bucket=300,
        # every value is "above" a median of 1: the incident path runs for every one
        pw_min_absolute_delta_events=1,
        pw_min_bucket_events=1,
        **extra,
    )


async def prepare(session, instance, *, median: float = 1.0) -> None:  # type: ignore[no-untyped-def]
    await session.execute(
        update(PropertyWatch)
        .where(PropertyWatch.instance_id == instance.id)
        .values(detectors=ALL_FIVE, evaluated_through=START - STEP)
    )
    values = (
        await session.execute(
            select(PwValue.id)
            .join(PropertyWatch, PropertyWatch.id == PwValue.watch_id)
            .where(PropertyWatch.instance_id == instance.id)
        )
    ).all()
    await session.execute(
        insert(PwValueBaseline),
        [
            dict(
                pw_value_id=value_id,
                metric_name=PW_VALUE_EVENTS_METRIC,
                weekday=START.isoweekday(),
                hour=START.hour,
                median=median,
                mad=0.1,
                p05=median,
                p95=median,
                sample_count=10,
                is_reliable=True,
                completeness=1.0,
                observations=[median] * 10,
                baseline_version=1,
                metric_semantics=PW_VALUE_EVENTS_SEMANTICS,
                watch_version=1,
            )
            for (value_id,) in values
        ],
    )
    await session.commit()


async def test_collection_plus_evaluation_is_bulk_at_every_required_size(
    db_session, capsys
) -> None:
    counts: dict[tuple[int, int], int] = {}
    await pin_cadence(db_session, 3600)  # pre-D-28 default
    for watches, values in ((1, 50), (10, 50), (1, 200), (10, 200)):
        instance, provider = await seeded(
            db_session, watches=watches, values=values, kind=PwScopeKind.LOG_SOURCE
        )
        await prepare(db_session, instance)
        settings = scale_settings()
        with recorded_statements(db_session) as statements:
            await collect(db_session, instance, provider, settings=settings)
            engine = PwAnomalyEngine(db_session, settings=settings, clock=lambda: NOW)
            report = await engine.evaluate(instance)
            await db_session.commit()
        counts[(watches, values)] = len(statements)
        # the work really was done at this size, through every table the engine writes
        assert report.buckets_evaluated == 12 * watches
        assert report.values_evaluated == 12 * watches * values
        for table in (PwDetectorState, PwAnomaly, PwAnomalyTransition):
            rows = await db_session.scalar(select(func.count()).select_from(table))
            assert rows >= watches * values, table
        await db_session.execute(PwAnomalyTransition.__table__.delete())
        await db_session.execute(PwAnomaly.__table__.delete())
        await db_session.execute(PwDetectorState.__table__.delete())
        await db_session.commit()
    with capsys.disabled():
        print("\nPW collection+evaluation statements per tick:", counts)
    assert len(set(counts.values())) == 1, counts
    assert next(iter(counts.values())) <= CEILING, counts


async def test_evaluation_alone_is_constant_and_small(db_session) -> None:
    counts = {}
    for watches, values in ((1, 50), (10, 200)):
        instance, provider = await seeded(
            db_session, watches=watches, values=values, kind=PwScopeKind.LOG_SOURCE
        )
        await prepare(db_session, instance)
        settings = scale_settings()
        await collect(db_session, instance, provider, settings=settings)
        with recorded_statements(db_session) as statements:
            await PwAnomalyEngine(db_session, settings=settings, clock=lambda: NOW).evaluate(
                instance
            )
            await db_session.commit()
        counts[(watches, values)] = len(statements)
    assert len(set(counts.values())) == 1, counts


async def test_an_idle_tick_evaluates_with_a_fixed_small_cost(db_session) -> None:
    instance, provider = await seeded(
        db_session, watches=3, values=20, kind=PwScopeKind.LOG_SOURCE
    )
    await prepare(db_session, instance)
    settings = scale_settings()
    await collect(db_session, instance, provider, settings=settings)
    engine = PwAnomalyEngine(db_session, settings=settings, clock=lambda: NOW)
    await engine.evaluate(instance)
    await db_session.commit()
    with recorded_statements(db_session) as statements:
        report = await engine.evaluate(instance)
    assert report.buckets_evaluated == 0
    assert len(statements) <= 6


async def test_training_scales_with_chunks_not_values(db_session) -> None:
    counts = {}
    for values in (50, 100):
        instance, provider = await seeded(
            db_session, watches=1, values=values, kind=PwScopeKind.LOG_SOURCE
        )
        settings = scale_settings(pw_baseline_values_per_run=100)
        await collect(db_session, instance, provider, settings=settings)
        await db_session.execute(
            update(PropertyWatch)
            .where(PropertyWatch.instance_id == instance.id)
            .values(evaluated_through=PropertyWatch.collected_through)
        )
        await db_session.commit()
        with recorded_statements(db_session) as statements:
            report = await PwBaselineTrainer(db_session, settings=settings).rebuild_due(
                instance, now=NOW
            )
        assert report.values_rebuilt == values and report.cells_written > 0
        counts[values] = len(statements)
    # Per chunk: the due-values read, the buckets, the metrics, the incident spans, the
    # upsert of the rebuilt cells, and the delete of the cells the rebuild did not write
    # (PW-3c item 3). Every one is bounded, so 50 values and 100 values cost the same.
    assert counts == {50: 6, 100: 6}


async def test_the_component_lookup_costs_one_statement_only_when_a_tuple_is_in_its_window(
    db_session,
) -> None:
    day = timedelta(days=1)
    counts = {}
    for label, new_tuple in (("no new tuple", False), ("a tuple in its window", True)):
        rig = await make_rig(
            db_session,
            detectors=(NEW_COMBINATION,),
            properties=USER_ACTION,
            values=("alice|login",),
            pw_new_value_history_days=1,
        )
        rig.default = {"alice|login": 50}
        await history_buckets(db_session, rig.watch, start=T0 - day, stop=T0)
        if new_tuple:
            plan_range(rig.plan, 0, 3, {"alice|login": 50, "alice|logout": 30})
        await rig.collect(0)
        with recorded_statements(db_session) as statements:
            await rig.evaluate(0)
        counts[label] = len(statements)
    assert counts["a tuple in its window"] == counts["no new tuple"] + 1, counts
