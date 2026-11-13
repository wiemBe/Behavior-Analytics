"""SOC-13h: property-watch baseline training honours analyst exclusions.

The rebuild under test is ``PwBaselineTrainer.rebuild_due``. ``PwInstancePipeline.run``
is the scheduled caller. Spike, drop and disappeared share one ``pw:value`` scope.
A cell an exclusion empties is deleted, which is how this family fails closed.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.anomaly import training_exclusions as te
from app.models.enums import (
    AnomalyState,
    DetectorSignal,
    PwAnomalyType,
    PwBucketMeasurement,
    PwBucketOrigin,
    PwPinnedMeasurement,
    PwWatchStatus,
    Severity,
)
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwBucket,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.property_watch.detectors import PwDetectionContext, detect_value_spike
from app.property_watch.engine import _bucket_fact, _tracked_at
from app.property_watch.facts import reading_for
from app.property_watch.inputs import PW_VALUE_EVENTS_METRIC
from app.property_watch.training import PwBaselineTrainer
from app.security.rbac import Principal
from app.services.pw_pipeline import PwInstancePipeline
from tests.integration.factories import make_instance
from tests.integration.pw_engine_support import STEP, T0, make_rig, now_of, plan_range
from tests.integration.test_fleet_api import Client, run
from tests.integration.test_pw_collection import USERNAME, add_watch, pin_cadence
from tests.integration.test_pw_training import LATER, cells_of, train
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration]

TRIAGER = Principal("ada", permissions=frozenset({"read:anomalies", "anomaly:triage"}))
READER = Principal("rita", permissions=frozenset({"read:*"}))
BAND = (
    PwAnomalyType.PW_VALUE_SPIKE,
    PwAnomalyType.PW_VALUE_DROP,
    PwAnomalyType.PW_VALUE_DISAPPEARED,
)
NOVELTY = (PwAnomalyType.PW_NEW_VALUE, PwAnomalyType.PW_NEW_COMBINATION)


async def exclude(session, subject_id, start, end):  # type: ignore[no-untyped-def]
    result = await te.create(
        session,
        origin_family="property_watch",
        origin_anomaly_id=uuid.uuid4(),
        subject_kind="value",
        subject_id=subject_id,
        scope_key=te.PW_VALUE_SCOPE,
        starts_at=start,
        ends_at=end,
        reason="analyst removed this window",
        created_by="ada",
        lookback_start=start - timedelta(days=30),
    )
    return result.row


def _mentions(statements: list[str]) -> int:
    return sum(1 for sql in statements if "baseline_training_exclusion" in sql)


async def _spike(rig, text: str, at: datetime):  # type: ignore[no-untyped-def]
    """The real spike detector on one stored bucket, with no baseline cell."""
    value = await rig.value(text)
    bucket = await rig.session.get(PwBucket, (rig.watch.id, at))
    assert bucket is not None
    fact = _bucket_fact(bucket)
    events = await rig.session.scalar(
        select(PwValueMetric.events).where(
            PwValueMetric.pw_value_id == value.id,
            PwValueMetric.bucket_start == at,
        )
    )
    reading = reading_for(
        fact,
        pinned=value.pinned,
        tracked_at_bucket=_tracked_at(value, fact.bucket_start),
        metric_events=events,
    )
    return detect_value_spike(
        PwDetectionContext(
            watch_status=PwWatchStatus.OK,
            bucket=fact,
            reading=reading,
            baseline=None,
        )
    )


async def test_excluded_interval_changes_the_band_and_the_completeness(db_session) -> None:
    rig = await make_rig(db_session, values=("alice", "bob"))
    plan_range(rig.plan, 0, 12, {"alice": 100, "bob": 100})
    rig.plan[11] = {"alice": 900, "bob": 100}
    await rig.ticks(0, 12)
    await train(rig)
    [before] = await cells_of(rig, "alice")
    [control] = await cells_of(rig, "bob")
    assert 900.0 in before.observations
    assert before.p95 is not None and control.p95 is not None
    assert before.p95 > control.p95
    assert before.completeness == 1.0
    kept = before.p95

    alice = await rig.value("alice")
    await exclude(db_session, alice.id, T0 + 11 * STEP, T0 + 12 * STEP)
    await train(rig, now=LATER + timedelta(hours=1))
    [alice_cell] = await cells_of(rig, "alice")
    [bob_cell] = await cells_of(rig, "bob")
    assert 900.0 not in alice_cell.observations
    assert alice_cell.sample_count == 11
    assert alice_cell.p95 is not None and alice_cell.p95 < kept
    assert alice_cell.exclusion_counts["analyst_exclusion"] == 1
    assert alice_cell.completeness == 11 / 12
    assert alice_cell.excluded_sample_count == sum(alice_cell.exclusion_counts.values())
    assert "analyst_exclusion" not in bob_cell.exclusion_counts
    assert bob_cell.sample_count == 12 and 100.0 in bob_cell.observations


async def test_a_bucket_at_ends_at_is_trained(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    plan_range(rig.plan, 0, 12, {"alice": 100})
    rig.plan[0] = {"alice": 900}
    rig.plan[1] = {"alice": 800}
    await rig.ticks(0, 12)
    value = await rig.value("alice")
    # Half-open: the bucket that starts at ends_at stays in the cell.
    await exclude(db_session, value.id, T0, T0 + STEP)
    await train(rig)
    [cell] = await cells_of(rig, "alice")
    assert 900.0 not in cell.observations
    assert 800.0 in cell.observations
    assert cell.sample_count == 11
    assert cell.exclusion_counts["analyst_exclusion"] == 1


async def test_revoked_exclusion_trains_as_if_it_had_never_been_there(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    plan_range(rig.plan, 0, 12, {"alice": 100})
    rig.plan[4] = {"alice": 900}
    await rig.ticks(0, 12)
    value = await rig.value("alice")
    row = await exclude(db_session, value.id, T0 + 4 * STEP, T0 + 5 * STEP)
    await train(rig)
    [held] = await cells_of(rig, "alice")
    assert 900.0 not in held.observations

    await te.revoke(db_session, row.id, revoked_by="ada", reason="put the bucket back")
    await train(rig, now=LATER + timedelta(hours=1))
    [restored] = await cells_of(rig, "alice")
    assert 900.0 in restored.observations
    assert "analyst_exclusion" not in restored.exclusion_counts


async def test_a_fully_excluded_cell_is_insufficient_for_the_detector(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await rig.ticks(0, 24)
    await train(rig)
    hours = {cell.hour: cell for cell in await cells_of(rig, "alice")}
    assert set(hours) == {10, 11}
    assert hours[10].sample_count == 12 and hours[11].sample_count == 12

    value = await rig.value("alice")
    await exclude(db_session, value.id, T0, T0 + 12 * STEP)
    await train(rig, now=LATER + timedelta(hours=1))
    left = await cells_of(rig, "alice")
    assert [cell.hour for cell in left] == [11]
    assert left[0].sample_count == 12

    verdict = await _spike(rig, "alice", T0)
    assert verdict.signal is DetectorSignal.UNKNOWN
    assert verdict.evidence.insufficient_data is True
    assert verdict.evidence.expected_value is None
    assert verdict.evidence.reason_message is not None
    assert verdict.evidence.reason_message.code == "pw.detector.no_baseline"


async def test_automatic_and_analyst_overlap_is_counted_once(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await rig.ticks(0, 12)
    value = await rig.value("alice")
    db_session.add(
        PwAnomaly(
            watch_id=rig.watch.id,
            pw_value_id=value.id,
            value_key="alice",
            detector_type=PwAnomalyType.PW_VALUE_SPIKE.value,
            fingerprint="overlap",
            severity=Severity.HIGH.value,
            state=AnomalyState.RESOLVED.value,
            detected_at=T0,
            resolved_at=T0 + 2 * STEP,
            watch_version=1,
        )
    )
    await db_session.commit()
    # The closed incident covers buckets 0, 1 and 2. The first analyst span
    # overlaps bucket 0 only; the second is analyst-only (bucket 3).
    await exclude(db_session, value.id, T0, T0 + STEP)
    await exclude(db_session, value.id, T0 + 3 * STEP, T0 + 4 * STEP)
    await train(rig)
    [cell] = await cells_of(rig, "alice")
    assert cell.exclusion_counts["known_anomaly"] == 3
    assert cell.exclusion_counts["analyst_exclusion"] == 1
    assert cell.sample_count == 8
    assert cell.excluded_sample_count == sum(cell.exclusion_counts.values())


async def test_rebuild_reads_exclusions_once_per_run_not_per_value(db_session) -> None:
    counts = {}
    for size in (2, 10):
        names = tuple(f"user{index}" for index in range(size))
        rig = await make_rig(db_session, values=names)
        rig.default = dict.fromkeys(names, 30)
        await rig.ticks(0, 4)
        with recorded_statements(db_session) as statements:
            report = await PwBaselineTrainer(db_session, settings=rig.settings).rebuild_due(
                rig.instance, now=LATER
            )
        assert report.values_rebuilt == size
        counts[size] = _mentions(statements)
    assert counts == {2: 1, 10: 1}


async def test_an_unevaluated_watch_issues_no_exclusion_read(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    await db_session.execute(
        update(PropertyWatch).where(PropertyWatch.id == rig.watch.id).values(evaluated_through=None)
    )
    await db_session.commit()
    with recorded_statements(db_session) as statements:
        report = await PwBaselineTrainer(db_session, settings=rig.settings).rebuild_due(
            rig.instance, now=LATER
        )
    assert report.values_rebuilt == 0
    assert _mentions(statements) == 0


async def test_the_pipeline_trains_with_the_exclusion(db_session) -> None:
    rig = await make_rig(db_session, values=("alice",))
    rig.default = {"alice": 100}
    rig.plan[5] = {"alice": 900}
    await pin_cadence(db_session, 3600)
    clock = {"at": now_of(11)}
    pipeline = PwInstancePipeline(
        db_session, rig.provider, settings=rig.settings, clock=lambda: clock["at"]
    )
    first = await pipeline.run(rig.instance)
    await db_session.commit()
    assert first.training is not None and first.training.cells_written >= 1
    [trained] = await cells_of(rig, "alice")
    assert 900.0 in trained.observations

    value = await rig.value("alice")
    await exclude(db_session, value.id, T0 + 5 * STEP, T0 + 6 * STEP)
    await db_session.commit()
    clock["at"] = now_of(11) + timedelta(hours=1)
    second = await pipeline.run(rig.instance)
    await db_session.commit()
    assert second.steps == ["collect", "evaluate", "train"]
    assert second.training is not None
    hour = [cell for cell in await cells_of(rig, "alice") if cell.hour == 10]
    assert hour
    assert 900.0 not in hour[0].observations
    assert hour[0].exclusion_counts["analyst_exclusion"] == 1


# --- API ---------------------------------------------------------------------------


def _anchor() -> datetime:
    """A recent hour: inside the default lookback, and aligned to 300s and to 3600s."""
    return (datetime.now(UTC) - timedelta(days=2)).replace(minute=0, second=0, microsecond=0)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _body(start: datetime, end: datetime, reason: str = "change window") -> dict:
    return {"starts_at": _iso(start), "ends_at": _iso(end), "reason": reason}


def _url(anomaly_id: uuid.UUID, family: str = "property_watch") -> str:
    return f"/api/v1/anomalies/{family}/{anomaly_id}/training-exclusions"


def _code(response) -> str:  # type: ignore[no-untyped-def]
    body = response.json()
    detail = body.get("detail")
    if isinstance(detail, dict) and "code" in detail:
        return str(detail["code"])
    return str(body["detail_code"])


def _bucket(watch_id, at, *, seconds: int, origin: PwBucketOrigin) -> PwBucket:
    return PwBucket(
        watch_id=watch_id,
        bucket_start=at,
        bucket_seconds=seconds,
        watch_version=1,
        origin=origin,
        total_events=100,
        parsed_events=100,
        unparsed_events=0,
        untracked_events=0,
        values_measurement=PwBucketMeasurement.EXACT,
        pinned_measurement=PwPinnedMeasurement.NOT_REQUESTED,
        totals_complete=True,
        observed_values=1,
        tracked_values_observed=1,
        admitted_values=0,
        below_admission_threshold=0,
        rejected_over_quota=0,
        admission_truncated=False,
        scope_member_count=1,
        scope_changed=False,
        in_maintenance=False,
        query_provenance={},
        collected_at=at,
    )


async def _seed_pw(  # type: ignore[no-untyped-def]
    session,
    detector: PwAnomalyType,
    *,
    state: AnomalyState = AnomalyState.RESOLVED,
    buckets: str = "live",
    value: bool = True,
) -> tuple[uuid.UUID, uuid.UUID | None]:
    instance = await make_instance(session)
    detected = _anchor()
    watch = await add_watch(session, instance, properties=USERNAME, through=detected)
    value_id = None
    if value:
        row = PwValue(
            watch_id=watch.id,
            watch_version=1,
            value_key="alice",
            pinned=False,
            active=True,
            first_seen_at=detected - timedelta(days=20),
            tracked_since=detected - timedelta(days=20),
        )
        session.add(row)
        await session.flush()
        value_id = row.id
        if buckets == "live":
            session.add(_bucket(watch.id, detected, seconds=300, origin=PwBucketOrigin.LIVE))
        elif buckets == "history":
            session.add(_bucket(watch.id, detected, seconds=3600, origin=PwBucketOrigin.HISTORY))
        elif buckets == "both":
            session.add(
                _bucket(watch.id, detected - STEP, seconds=300, origin=PwBucketOrigin.LIVE)
            )
            session.add(_bucket(watch.id, detected, seconds=3600, origin=PwBucketOrigin.HISTORY))
    anomaly = PwAnomaly(
        watch_id=watch.id,
        pw_value_id=value_id,
        value_key="alice",
        detector_type=detector.value,
        fingerprint=f"pw-{uuid.uuid4().hex[:12]}",
        severity=Severity.HIGH.value,
        state=state.value,
        detected_at=detected,
        resolved_at=None if state is not AnomalyState.RESOLVED else detected + STEP,
        watch_version=1,
    )
    session.add(anomaly)
    await session.flush()
    return anomaly.id, value_id


def seed_pw(detector: PwAnomalyType, **kw):  # type: ignore[no-untyped-def]
    async def work(session):  # type: ignore[no-untyped-def]
        return await _seed_pw(session, detector, **kw)

    return run(work)


@pytest.mark.parametrize("detector", BAND)
def test_api_accepts_a_resolved_band_anomaly(db_schema, detector: PwAnomalyType) -> None:
    anomaly_id, value_id = seed_pw(detector)
    start = _anchor()
    response = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["origin_family"] == "property_watch"
    assert body["subject_kind"] == "value"
    assert body["subject_id"] == str(value_id)
    assert body["scope_key"] == "pw:value"
    assert body["state"] == "PENDING_REBUILD"
    assert body["created_by"] == "ada"
    again = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert again.status_code == 200
    assert again.json()["id"] == body["id"]


def test_pw_guards_match_the_other_families(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    open_id, _ = seed_pw(PwAnomalyType.PW_VALUE_SPIKE, state=AnomalyState.OPEN)
    assert _code(Client(TRIAGER).post(_url(open_id), json=_body(start, start + STEP))) == (
        "baseline_exclusion.anomaly_active"
    )
    missing = Client(TRIAGER).post(_url(uuid.uuid4()), json=_body(start, start + STEP))
    assert missing.status_code == 404
    assert _code(missing) == "baseline_exclusion.not_found"

    no_bucket, _ = seed_pw(PwAnomalyType.PW_VALUE_SPIKE, buckets="none")
    deleted, _ = seed_pw(PwAnomalyType.PW_VALUE_DROP, value=False)
    for anomaly_id in (no_bucket, deleted):
        assert _code(Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))) == (
            "baseline_exclusion.interval_invalid"
        )

    anomaly_id, _ = seed_pw(PwAnomalyType.PW_VALUE_SPIKE)
    refused = Client(READER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert refused.status_code == 403
    blank = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP, reason="  "))
    assert _code(blank) == "baseline_exclusion.reason_invalid"
    # The family is accepted, so a blank reason is read. A name that is not a
    # family is refused before the reason.
    early = Client(TRIAGER).post(
        _url(uuid.uuid4(), "nonsense"), json=_body(start, start + STEP, reason="  ")
    )
    assert _code(early) == "baseline_exclusion.family_unsupported"

    narrowed = Client(TRIAGER).post(_url(anomaly_id), json=_body(start + STEP, start + 2 * STEP))
    assert _code(narrowed) == "baseline_exclusion.interval_not_widened"
    too_long = Client(TRIAGER).post(
        _url(anomaly_id), json=_body(start, start + timedelta(days=7) + STEP)
    )
    assert _code(too_long) == "baseline_exclusion.interval_too_long"

    created = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + STEP))
    assert created.status_code == 201
    overlap = Client(TRIAGER).post(_url(anomaly_id), json=_body(start, start + 2 * STEP))
    assert overlap.status_code == 409
    assert _code(overlap) == "baseline_exclusion.overlap"


@pytest.mark.parametrize("detector", NOVELTY)
def test_novelty_detectors_are_unsupported(db_schema, detector: PwAnomalyType) -> None:
    start = _anchor()
    open_id, _ = seed_pw(detector, state=AnomalyState.OPEN)
    assert _code(Client(TRIAGER).post(_url(open_id), json=_body(start, start + STEP))) == (
        "baseline_exclusion.anomaly_active"
    )
    resolved, _ = seed_pw(detector)
    response = Client(TRIAGER).post(_url(resolved), json=_body(start, start + STEP))
    assert response.status_code == 422
    assert _code(response) == "baseline_exclusion.family_unsupported"


def test_bucket_width_comes_from_the_live_row(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    history_only, _ = seed_pw(PwAnomalyType.PW_VALUE_SPIKE, buckets="history")
    asked = _body(start, start + STEP)
    assert _code(Client(TRIAGER).post(_url(history_only), json=asked)) == (
        "baseline_exclusion.interval_invalid"
    )
    anomaly_id, _ = seed_pw(PwAnomalyType.PW_VALUE_DISAPPEARED, buckets="both")
    # 300 seconds is a live-width boundary and not a 3600-second one.
    accepted = Client(TRIAGER).post(_url(anomaly_id), json=asked)
    assert accepted.status_code == 201, accepted.text
    assert accepted.json()["scope_key"] == "pw:value"


def test_applied_follows_the_value_baseline_computed_at(db_schema) -> None:  # type: ignore[no-untyped-def]
    fresh_id, fresh_value = seed_pw(PwAnomalyType.PW_VALUE_SPIKE)
    stale_id, stale_value = seed_pw(PwAnomalyType.PW_VALUE_DROP)
    start = _anchor()

    async def stamp(session, value_id, at):  # type: ignore[no-untyped-def]
        session.add(
            PwValueBaseline(
                pw_value_id=value_id,
                metric_name=PW_VALUE_EVENTS_METRIC,
                weekday=2,
                hour=10,
                median=100.0,
                watch_version=1,
                computed_at=at,
            )
        )

    run(lambda session: stamp(session, fresh_value, datetime.now(UTC) + timedelta(days=1)))
    run(lambda session: stamp(session, stale_value, datetime.now(UTC) - timedelta(days=30)))
    fresh = Client(TRIAGER).post(_url(fresh_id), json=_body(start, start + STEP))
    stale = Client(TRIAGER).post(_url(stale_id), json=_body(start, start + STEP))
    assert fresh.status_code == 201, fresh.text
    assert stale.status_code == 201, stale.text
    assert fresh.json()["state"] == "APPLIED"
    assert stale.json()["state"] == "PENDING_REBUILD"

    def listed(value_id, state: str) -> list[dict]:
        response = Client(TRIAGER).get(
            "/api/v1/behavior/training-exclusions",
            params={"subject_kind": "value", "subject_id": str(value_id), "state": state},
        )
        assert response.status_code == 200, response.text
        return response.json()["items"]

    assert [item["id"] for item in listed(fresh_value, "APPLIED")] == [fresh.json()["id"]]
    assert listed(fresh_value, "PENDING_REBUILD") == []
    assert [item["id"] for item in listed(stale_value, "PENDING_REBUILD")] == [stale.json()["id"]]
    assert listed(stale_value, "APPLIED") == []


def test_other_families_stay_unsupported(db_schema) -> None:  # type: ignore[no-untyped-def]
    start = _anchor()
    response = Client(TRIAGER).post(
        _url(uuid.uuid4(), "nonsense"), json=_body(start, start + STEP)
    )
    assert response.status_code == 422
    assert _code(response) == "baseline_exclusion.family_unsupported"
