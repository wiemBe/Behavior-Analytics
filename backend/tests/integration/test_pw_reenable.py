"""A re-enabled property watch starts fresh (T-136).

The `PATCH` transition `enabled` false → true nulls `collected_through` and pins
`evaluated_through` at the pre-disable watermark. So the first tick seeds at the latest
closed bucket, the disabled period is never collected, and no stored bucket is evaluated
again — without leaving the values invisible to training. The pipeline tests run collection
and evaluation for real through the mock provider (the engine tests' shape); the watermarks
they read at re-enable are exactly what the transition leaves, which the API tests pin.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.custom_rules.engine import CustomRuleEngine
from app.models.custom_rule import CustomRuleBreach, CustomRuleWatermark
from app.models.enums import PwAnomalyType
from app.models.property_watch import PropertyWatch, PwBucket, PwValueMetric
from app.property_watch.engine import _bucket_fact
from app.property_watch.history import verify_value_history
from app.repositories.property_watch import PropertyWatchRepository
from tests.integration.factories import make_instance, make_log_source
from tests.integration.pw_engine_support import (
    NEW_VALUE,
    STEP,
    T0,
    make_rig,
    now_of,
    plan_range,
)
from tests.integration.test_custom_rules_pw_value import (
    ACTION,
    _bucket,
    _rule,
    _value,
    _watch,
)
from tests.integration.test_property_watches_api import PREFIX, _client, _principal
from tests.integration.test_pw_alerting import _patch, _watch_api, alerting_rig, spike, step
from tests.integration.test_pw_collection import cfg

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

DAY = timedelta(days=1)
STEP_PER_DAY = 288
#: Bucket index of the first tick after a re-enable at T0 + 7 days: the disabled week is
#: never collected, so this tick carries the re-enable instant.
RE_TICK = 7 * STEP_PER_DAY
RE_BUCKET = T0 + 7 * DAY


async def _route_watch(db_session, db_schema: str):  # type: ignore[no-untyped-def]
    """One watch created through the API, returned with its client and creation body."""
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    await db_session.commit()
    client = _client(db_schema, _principal("detection_engineer"))
    made = client.post(
        PREFIX,
        json={
            "name": f"w-{uuid.uuid4().hex[:8]}",
            "scope_kind": "LOG_SOURCE",
            "log_source_id": str(source.id),
            "properties": [ACTION],
        },
    )
    assert made.status_code == 201, made.text
    return client, made.json()


async def _watch_row(db_session, watch_id: str) -> PropertyWatch:  # type: ignore[no-untyped-def]
    db_session.expire_all()
    watch = await db_session.get(PropertyWatch, uuid.UUID(watch_id))
    assert watch is not None
    return watch


# ---------------------------------------------------------------- the PATCH transition
async def test_a_reenable_resets_only_the_watermarks(db_session, db_schema: str) -> None:
    """Collected through T, disabled, re-enabled: collected NULL, evaluated pinned at T."""
    _client_of, created = await _route_watch(db_session, db_schema)
    watch = await _watch_row(db_session, created["id"])
    watch.collected_through = T0 - STEP
    watch.evaluated_through = T0 - STEP
    await db_session.commit()

    disabled = _client_of.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": watch.updated_at.isoformat(), "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.enabled is False
    # the disable alone changes no watermark
    assert watch.collected_through == T0 - STEP and watch.evaluated_through == T0 - STEP

    enabled = _client_of.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": disabled.json()["updated_at"], "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.enabled is True
    # the chosen seeds: the collector's first-run rule starts at the latest closed bucket,
    # and no stored bucket is pending again
    assert watch.collected_through is None
    assert watch.evaluated_through == T0 - STEP
    # the definition is untouched
    assert watch.watch_version == 1
    assert watch.enabled is True and watch.name == created["name"]


async def test_a_rename_or_a_disable_keeps_the_watermarks(db_session, db_schema: str) -> None:
    _client_of, created = await _route_watch(db_session, db_schema)
    watch = await _watch_row(db_session, created["id"])
    watch.enabled = True
    watch.collected_through = T0 - STEP
    watch.evaluated_through = T0 - STEP
    await db_session.commit()

    renamed = _client_of.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": watch.updated_at.isoformat(), "name": "renamed"},
    )
    assert renamed.status_code == 200, renamed.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.name == "renamed" and watch.enabled is True
    assert watch.collected_through == T0 - STEP and watch.evaluated_through == T0 - STEP

    disabled = _client_of.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": renamed.json()["updated_at"], "enabled": False},
    )
    assert disabled.status_code == 200, disabled.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.enabled is False
    assert watch.collected_through == T0 - STEP and watch.evaluated_through == T0 - STEP


async def test_a_first_enable_still_seeds_nothing(db_session, db_schema: str) -> None:
    """A watch never collected holds both watermarks NULL through its first enable."""
    _client_of, created = await _route_watch(db_session, db_schema)
    watch = await _watch_row(db_session, created["id"])
    assert watch.collected_through is None and watch.evaluated_through is None

    enabled = _client_of.patch(
        f"{PREFIX}/{created['id']}",
        json={"expected_updated_at": created["updated_at"], "enabled": True},
    )
    assert enabled.status_code == 200, enabled.text
    watch = await _watch_row(db_session, created["id"])
    assert watch.enabled is True
    assert watch.collected_through is None and watch.evaluated_through is None


# ------------------------------------------------------------------ the first tick after
async def _reenable_fresh(rig) -> None:  # type: ignore[no-untyped-def]
    """Turn `enabled` on and leave the watermarks exactly as the PATCH transition does."""
    rig.watch.enabled = True
    rig.watch.evaluated_through = rig.watch.collected_through
    rig.watch.collected_through = None
    await rig.session.commit()


async def _buckets_of(rig) -> list[PwBucket]:  # type: ignore[no-untyped-def]
    return list(
        (
            await rig.session.scalars(  # type: ignore[attr-defined]
                select(PwBucket)
                .where(PwBucket.watch_id == rig.watch.id)
                .order_by(PwBucket.bucket_start)
                .execution_options(populate_existing=True)
            )
        ).all()
    )


async def test_the_first_tick_after_a_reenable_runs_the_latest_closed_bucket_only(db_session):
    rig = await make_rig(db_session, values=("alice",))
    plan_range(rig.plan, 0, 3, {"alice": 100, "bob": 400})
    await rig.ticks(0, 3)
    assert (await rig.reload_watch()).collected_through == T0 + 2 * STEP
    rig.watch.enabled = False
    await db_session.commit()

    await _reenable_fresh(rig)
    rig.plan[RE_TICK] = {"alice": 100, "bob": 400}
    await rig.collect(RE_TICK)
    report = await rig.evaluate(RE_TICK)

    stored = await _buckets_of(rig)
    assert [b.bucket_start for b in stored] == [T0, T0 + STEP, T0 + 2 * STEP, RE_BUCKET]
    assert report.buckets_evaluated == 1 and report.values_evaluated == 2
    watch = await rig.reload_watch()
    assert watch.collected_through == RE_BUCKET and watch.evaluated_through == RE_BUCKET


async def test_no_incident_opens_on_the_gap(db_session, db_schema: str) -> None:
    """PW-3c plus the reset: the resolved incident stays ended, and the gap is never judged."""
    rig = await alerting_rig(db_session)
    spike(rig, 1, 3)
    for k in range(0, 3):
        await step(rig, k)
    [open_] = await rig.anomalies(PwAnomalyType.PW_VALUE_SPIKE)
    assert open_.state == "OPEN"

    client = await _watch_api(db_schema)
    assert (await _patch(rig, client, enabled=False)).status_code == 200
    watch = await rig.reload_watch()
    through = watch.collected_through
    assert through is not None and watch.evaluated_through == through

    assert (await _patch(rig, client, enabled=True)).status_code == 200
    watch = await rig.reload_watch()
    assert watch.collected_through is None and watch.evaluated_through == through

    rig.plan[RE_TICK] = {"alice": 100, "bob": 400}
    report = await step(rig, RE_TICK)
    assert report.buckets_evaluated == 1
    rows = await rig.anomalies(PwAnomalyType.PW_VALUE_SPIKE)
    assert [row.state for row in rows] == ["RESOLVED"], "the old incident stays ended"


async def test_a_new_value_after_the_gap_is_insufficient_never_new(db_session) -> None:
    """The newcomer's history window crosses the gap, so it judges INSUFFICIENT_DATA.

    The materiality numbers would be satisfied on their own, so the only reason the verdict
    can take is the incomplete history. The verifier the engine runs exists as a pure
    function, so the reason is pinned against the stored buckets the engine loads.
    """
    rig = await make_rig(
        db_session, values=("alice",), detectors=(NEW_VALUE,), pw_new_value_history_days=1
    )
    plan_range(rig.plan, 0, 3, {"alice": 100, "bob": 400})
    await rig.ticks(0, 3)
    rig.watch.enabled = False
    await db_session.commit()
    await _reenable_fresh(rig)

    rig.plan[RE_TICK] = {"alice": 100, "bob": 400, "mallory": 90}
    rig.plan[RE_TICK + 1] = {"alice": 100, "bob": 400, "mallory": 90}
    await rig.collect(RE_TICK)
    await rig.evaluate(RE_TICK)
    assert await rig.state_name("mallory", NEW_VALUE) == "INSUFFICIENT_DATA"
    await rig.collect(RE_TICK + 1)
    await rig.evaluate(RE_TICK + 1)
    # 180 events over 2 buckets meet the materiality rule; the history alone blocks it
    assert await rig.state_name("mallory", NEW_VALUE) == "INSUFFICIENT_DATA"
    assert await rig.anomalies() == []

    first = (await rig.value("mallory")).first_seen_at
    assert first == RE_BUCKET
    rows = await PropertyWatchRepository(rig.session).buckets_between(
        [rig.watch.id], start=first - DAY, end=first
    )
    history = verify_value_history(
        [_bucket_fact(row) for row in rows],
        history_start=first - DAY,
        history_end=first,
        bucket_seconds=300,
        watch_version=rig.watch.watch_version,
        min_coverage=rig.settings.pw_min_coverage,
    )
    assert history.complete is False
    assert "was never collected" in (history.reason or ""), history.reason


# ------------------------------------------------------------------- the F.1 threshold
async def _arm_pw_watermark(session, through):  # type: ignore[no-untyped-def]
    """One PW_VALUE watermark at `through`: the family's F.1 state at the disable."""
    await session.execute(
        CustomRuleWatermark.__table__.delete().where(
            CustomRuleWatermark.subject_kind == "PW_VALUE"
        )
    )
    session.add(CustomRuleWatermark(subject_kind="PW_VALUE", evaluated_through=through))
    await session.commit()


async def test_an_f1_rule_judges_the_reenable_bucket_and_nothing_in_the_gap(db_session):
    instance = await make_instance(db_session)
    source = await make_log_source(db_session, instance)
    watch = await _watch(db_session, instance, source)
    value = await _value(db_session, watch)
    pre = [T0, T0 + STEP, T0 + 2 * STEP]
    for at in pre:
        db_session.add(_bucket(watch, 10, bucket_start=at))
        db_session.add(
            PwValueMetric(pw_value_id=value.id, bucket_start=at, events=410, bucket_seconds=300)
        )
    rule = _rule("deny-flood", value, threshold_value=500.0, sustain_buckets=1)
    db_session.add(rule)
    await _arm_pw_watermark(db_session, pre[-1])

    watch.enabled = False
    await db_session.commit()
    # the re-enable, as the PATCH transition leaves the watch
    watch.enabled = True
    watch.evaluated_through = pre[-1]
    watch.collected_through = None
    # the fresh bucket after it: over the threshold
    db_session.add(_bucket(watch, 10, bucket_start=RE_BUCKET))
    db_session.add(
        PwValueMetric(pw_value_id=value.id, bucket_start=RE_BUCKET, events=610, bucket_seconds=300)
    )
    await db_session.commit()

    for _tick in range(9):
        await CustomRuleEngine(
            db_session, cfg(custom_rules_enabled=True), clock=lambda: now_of(RE_TICK)
        ).run()
        await db_session.commit()

    breaches = list((await db_session.scalars(select(CustomRuleBreach))).all())
    assert len(breaches) == 1
    [breach] = breaches
    assert breach.first_bucket_start == RE_BUCKET
    assert breach.last_bucket_start == RE_BUCKET
    await db_session.refresh(rule)
    assert rule.last_status == "OPEN"
