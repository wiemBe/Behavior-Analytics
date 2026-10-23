"""Anomaly engine: hysteresis open/resolve, dedup, flapping prevention,
maintenance suppression, and the alert lifecycle it drives. DB-gated."""

from __future__ import annotations

import logging
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyState, AnomalyType, Criticality
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline, LogSourceDetectorState
from app.models.monitoring import AnomalyEvaluationFault, AnomalyEvaluationWatermark
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_deviation_threshold=3.5)


async def _reliable_eps_baseline(session, ls, *, weekday=2, hour=10, median=50.0):
    # utc() default 2026-07-15 is a Wednesday -> isoweekday 3. Provide baseline
    # for the hour we feed. weekday param kept explicit for clarity.
    session.add(
        LogSourceBaseline(
            log_source_id=ls.id, metric_name="average_eps", weekday=weekday, hour=hour,
            median=median, mad=2.0, p05=median * 0.9, p95=median * 1.1,
            sample_count=30, is_reliable=True, baseline_version=1, observations=[median],
        )
    )
    await session.flush()


async def _feed(engine, ls, session, *, start, count, eps, sig_prefix="s", step_min=5):
    """Feed `count` consecutive intervals and evaluate each."""
    reports = []
    for i in range(count):
        bucket = start + timedelta(minutes=step_min * i)
        m = await add_metric(
            session, ls, bucket, average_eps=eps, event_count=int(eps * 300),
            payload_signature=f"{sig_prefix}-{i}",
        )
        reports.append(await engine.evaluate_interval(ls, m))
    return reports


async def _open_alert(session, ls, atype: AnomalyType) -> Alert | None:
    return await session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(ls.id, atype.value),
            Alert.status != AlertStatus.RESOLVED,
        )
    )


async def test_opens_after_n_consecutive_anomalies(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    weekday = start.isoweekday()
    await _reliable_eps_baseline(db_session, ls, weekday=weekday, hour=10)

    engine = AnomalyEngine(db_session, settings=_settings())
    # MEDIUM opens after 2 consecutive anomalous intervals.
    await _feed(engine, ls, db_session, start=start, count=1, eps=3.0)
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None  # not yet
    await _feed(engine, ls, db_session,
                start=start + timedelta(minutes=5), count=1, eps=3.0)
    alert = await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP)
    assert alert is not None and alert.status == AlertStatus.OPEN


async def test_dedup_bumps_occurrence_not_new_alert(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    await _feed(engine, ls, db_session, start=start, count=5, eps=3.0)
    alerts = await db_session.scalar(
        select(func.count()).select_from(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(ls.id, AnomalyType.VOLUME_DROP.value)
        )
    )
    assert alerts == 1  # exactly one alert despite 5 anomalous intervals
    alert = await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP)
    assert alert.occurrence_count >= 2


async def test_resolves_after_m_healthy_intervals(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    await _feed(engine, ls, db_session, start=start, count=2, eps=3.0)
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is not None

    # MEDIUM resolves after 3 consecutive healthy intervals.
    await _feed(engine, ls, db_session,
                start=start + timedelta(minutes=10), count=3, eps=50.0)
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None
    # The anomaly row is resolved too.
    anomaly = await db_session.scalar(
        select(LogSourceAnomaly).where(
            LogSourceAnomaly.log_source_id == ls.id,
            LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert anomaly.resolved_at is not None


async def test_flapping_never_opens(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    # Alternate anomalous / healthy: never 2 consecutive anomalies -> never opens.
    for i in range(8):
        bucket = start + timedelta(minutes=5 * i)
        eps = 3.0 if i % 2 == 0 else 50.0
        m = await add_metric(db_session, ls, bucket, average_eps=eps,
                             event_count=int(eps * 300), payload_signature=f"f-{i}")
        await engine.evaluate_interval(ls, m)

    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None


async def test_maintenance_suppresses_alert(db_session) -> None:
    from datetime import UTC, datetime

    inst = await make_instance(db_session)
    ls = await make_log_source(
        db_session, inst, maintenance_mode=True,
        maintenance_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    await _feed(engine, ls, db_session, start=start, count=5, eps=3.0)
    # No alert during maintenance, even though the condition holds.
    assert await _open_alert(db_session, ls, AnomalyType.VOLUME_DROP) is None


async def test_idempotent_reevaluation(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    m = await add_metric(db_session, ls, start, average_eps=3.0, event_count=900,
                         payload_signature="i-0")
    await engine.evaluate_interval(ls, m)
    # Re-evaluating the same interval must not double-count the hysteresis.
    await engine.evaluate_interval(ls, m)
    await engine.evaluate_interval(ls, m)

    from app.models.log_source import LogSourceDetectorState
    state = await db_session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == ls.id,
            LogSourceDetectorState.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert state.consecutive_anomalous == 1  # not 3


async def _seed_evaluation_watermark(session, ls, at) -> AnomalyEvaluationWatermark:
    watermark = AnomalyEvaluationWatermark(
        log_source_id=ls.id,
        evaluated_through=at,
        updated_at=at,
    )
    session.add(watermark)
    await session.flush()
    return watermark


async def test_evaluate_pending_judges_twelve_buckets_in_order_and_catches_midwindow_spike(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.MEDIUM)
    start = utc(h=10)
    await _reliable_eps_baseline(
        db_session, ls, weekday=start.isoweekday(), hour=start.hour
    )
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    for index in range(12):
        eps = 120.0 if index in {2, 3} else 50.0
        await add_metric(
            db_session,
            ls,
            start + timedelta(minutes=5 * index),
            average_eps=eps,
            event_count=int(eps * 300),
            payload_signature=f"pending-{index}",
        )

    engine = AnomalyEngine(db_session, settings=_settings())
    evaluated: list = []
    evaluate_interval = engine.evaluate_interval

    async def tracking(log_source, metric):  # type: ignore[no-untyped-def]
        evaluated.append(metric.bucket_start)
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = tracking  # type: ignore[method-assign]
    report = await engine.evaluate_pending(ls)

    assert evaluated == [start + timedelta(minutes=5 * index) for index in range(12)]
    assert AnomalyType.VOLUME_SPIKE.value in report.opened
    watermark = await db_session.get(AnomalyEvaluationWatermark, ls.id)
    assert watermark is not None
    assert watermark.evaluated_through == start + timedelta(minutes=55)


async def test_evaluate_pending_preserves_no_events_on_zero_filled_buckets(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.CRITICAL)
    start = utc(h=10)
    db_session.add(
        LogSourceBaseline(
            log_source_id=ls.id,
            metric_name="event_count",
            weekday=start.isoweekday(),
            hour=start.hour,
            median=15_000.0,
            mad=500.0,
            p05=14_000.0,
            p95=16_000.0,
            sample_count=30,
            is_reliable=True,
            baseline_version=1,
            observations=[15_000.0],
        )
    )
    await db_session.flush()
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    for index in range(2):
        await add_metric(
            db_session,
            ls,
            start + timedelta(minutes=5 * index),
            average_eps=0.0,
            event_count=0,
            stored_event_count=0,
            last_event_at=None,
            event_delay_seconds=None,
            collection_source="mock",
            query_provenance={"zero_filled": True},
        )

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_pending(ls)

    assert AnomalyType.NO_EVENTS.value in report.opened


async def test_evaluate_pending_first_run_only_judges_latest_and_seeds_watermark(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    for index in range(12):
        await add_metric(db_session, ls, start + timedelta(minutes=5 * index))
    engine = AnomalyEngine(db_session, settings=_settings())
    evaluated: list = []
    evaluate_interval = engine.evaluate_interval

    async def tracking(log_source, metric):  # type: ignore[no-untyped-def]
        evaluated.append(metric.bucket_start)
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = tracking  # type: ignore[method-assign]
    await engine.evaluate_pending(ls)

    assert evaluated == [start + timedelta(minutes=55)]
    watermark = await db_session.get(AnomalyEvaluationWatermark, ls.id)
    assert watermark is not None
    assert watermark.evaluated_through == start + timedelta(minutes=55)


async def test_evaluate_pending_respects_per_run_bound(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    for index in range(5):
        await add_metric(db_session, ls, start + timedelta(minutes=5 * index))

    engine = AnomalyEngine(
        db_session,
        settings=Settings(
            encryption_key="x" * 44,
            anomaly_max_buckets_per_evaluation=2,
        ),
    )
    await engine.evaluate_pending(ls)

    watermark = await db_session.get(AnomalyEvaluationWatermark, ls.id)
    assert watermark is not None
    assert watermark.evaluated_through == start + timedelta(minutes=5)


async def test_evaluate_pending_failure_holds_watermark_before_failed_bucket(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    for index in range(3):
        await add_metric(db_session, ls, start + timedelta(minutes=5 * index))
    engine = AnomalyEngine(db_session, settings=_settings())
    evaluate_interval = engine.evaluate_interval
    attempted: list = []

    async def fail_second(log_source, metric):  # type: ignore[no-untyped-def]
        attempted.append(metric.bucket_start)
        if metric.bucket_start == start + timedelta(minutes=5):
            raise RuntimeError("detector failed")
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = fail_second  # type: ignore[method-assign]
    report = await engine.evaluate_pending(ls)

    assert attempted == [start, start + timedelta(minutes=5)]
    assert report.skipped_reason == "evaluation failed"
    watermark = await db_session.get(AnomalyEvaluationWatermark, ls.id)
    assert watermark is not None and watermark.evaluated_through == start


async def test_evaluate_pending_skips_monitoring_disabled_source(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, monitoring_enabled=False)
    await add_metric(db_session, ls, utc(h=10))

    report = await AnomalyEngine(db_session, settings=_settings()).evaluate_pending(ls)

    assert report.skipped_reason == "monitoring disabled"
    assert await db_session.get(AnomalyEvaluationWatermark, ls.id) is None


async def _detector_state_types(session, ls) -> set[str]:
    from app.models.log_source import LogSourceDetectorState

    return set(
        (
            await session.scalars(
                select(LogSourceDetectorState.anomaly_type).where(
                    LogSourceDetectorState.log_source_id == ls.id
                )
            )
        ).all()
    )


VOLUME_DETECTORS = {
    AnomalyType.NO_EVENTS.value,
    AnomalyType.VOLUME_DROP.value,
    AnomalyType.VOLUME_SPIKE.value,
}


async def test_live_ariel_bucket_runs_only_the_volume_detectors(db_session) -> None:
    """A live bucket measures volume and newest-event time, nothing more.

    Its other columns hold DTO defaults -- an unknown-event count of 0, no
    collection errors -- and a detector reading them would record a healthy
    verdict on a measurement nobody made. Those detectors must get no verdict
    and no detector state at all.
    """
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    m = await add_metric(
        db_session, ls, start, average_eps=50.0, event_count=15000,
        unknown_event_count=0, parsed_username_ratio=None, parsed_source_ip_ratio=None,
        payload_signature=None, collection_source="ariel_aggregate",
        query_provenance={
            "event_count_aggregation": "SUM(eventcount)",
            "telemetry": ["EVENT_VOLUME", "LAST_EVENT_TIME"],
        },
    )
    report = await engine.evaluate_interval(ls, m)

    assert await _detector_state_types(db_session, ls) == VOLUME_DETECTORS
    assert set(report.states) == VOLUME_DETECTORS
    assert set(report.unsupported) == {t.value for t in AnomalyType} - VOLUME_DETECTORS
    assert "UNKNOWN_EVENT_COUNT" in report.unsupported["UNKNOWN_EVENT_SPIKE"]


async def test_legacy_live_bucket_without_declared_telemetry_is_gated_the_same(
    db_session,
) -> None:
    """Rows collected before provenance declared telemetry fall back to what the
    Ariel path is known to measure, so upgrading does not re-enable a detector."""
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    m = await add_metric(
        db_session, ls, start, average_eps=50.0, event_count=15000,
        collection_source="ariel_aggregate", query_provenance={},
    )
    await engine.evaluate_interval(ls, m)

    assert await _detector_state_types(db_session, ls) == VOLUME_DETECTORS


async def test_synthetic_bucket_runs_the_detectors_the_mock_measures(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
    engine = AnomalyEngine(db_session, settings=_settings())

    m = await add_metric(db_session, ls, start, collection_source="mock")
    report = await engine.evaluate_interval(ls, m)

    # Neither path can supply these two: no distinct-IP baseline is built, and a
    # failed collection writes no bucket to carry an error count.
    unsupported = {AnomalyType.CARDINALITY_DROP.value, AnomalyType.COLLECTION_ERROR.value}
    assert set(report.unsupported) == unsupported
    assert await _detector_state_types(db_session, ls) == (
        {t.value for t in AnomalyType} - unsupported
    )


async def test_anomaly_alert_context_carries_source_class_without_moving_fingerprint(
    db_session,
) -> None:
    from app.models.enums import ClassificationReviewStatus, SourceClass
    from app.models.fleet import LogSourceClassification

    inst = await make_instance(db_session)
    classified = await make_log_source(db_session, inst, qradar_id=1, name="fw-1")
    bare = await make_log_source(db_session, inst, qradar_id=2, name="unclassified-1")
    start = utc(h=10)
    db_session.add(
        LogSourceClassification(
            log_source_id=classified.id, source_class=SourceClass.FIREWALL.value,
            basis="NAME_REGEX", conflicts=[], classified_at=start,
            review_status=ClassificationReviewStatus.REVIEWED.value,
        )
    )
    await db_session.flush()
    engine = AnomalyEngine(db_session, settings=_settings())
    for ls in (classified, bare):
        await _reliable_eps_baseline(db_session, ls, weekday=start.isoweekday(), hour=10)
        await _feed(engine, ls, db_session, start=start, count=3, eps=3.0)

    with_class = await _open_alert(db_session, classified, AnomalyType.VOLUME_DROP)
    without = await _open_alert(db_session, bare, AnomalyType.VOLUME_DROP)
    assert with_class is not None and without is not None
    assert with_class.context["source_class"] == "FIREWALL"
    assert "source_class" not in without.context
    # Looked up by the class-free fingerprint: identity is unchanged.
    assert with_class.dedup_key == anomaly_fingerprint(classified.id, "VOLUME_DROP")


# ------------------------------------------------------- evaluation faults
# A bucket `evaluate_pending` cannot judge is recorded, retried up to
# `ANOMALY_MAX_BUCKET_ATTEMPTS`, then given up and skipped -- never turned into
# a verdict (T-170 §3, option B). The fault row is the only record of it.

async def _fault_row(
    session, log_source_id, bucket
) -> AnomalyEvaluationFault | None:
    """Read a fault row fresh: the engine writes it through Core statements."""
    return await session.scalar(
        select(AnomalyEvaluationFault)
        .where(
            AnomalyEvaluationFault.log_source_id == log_source_id,
            AnomalyEvaluationFault.bucket_start == bucket,
        )
        .execution_options(populate_existing=True)
    )


async def _watermark(session, ls) -> AnomalyEvaluationWatermark | None:
    return await session.get(AnomalyEvaluationWatermark, ls.id)


def _fail_one_bucket(
    engine: AnomalyEngine, faulted, attempted: list, *, active: dict | None = None
):
    """Wrap evaluate_interval so one bucket raises; `active` can switch it off."""
    evaluate_interval = engine.evaluate_interval

    async def failing(log_source, metric):  # type: ignore[no-untyped-def]
        attempted.append(metric.bucket_start)
        if metric.bucket_start == faulted and (active is None or active["on"]):
            raise RuntimeError("detector fault")
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = failing  # type: ignore[method-assign]


async def test_evaluate_pending_records_attempts_and_gives_up_on_the_third(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    for index in range(3):
        await add_metric(db_session, ls, start + timedelta(minutes=5 * index))
    faulted = start + timedelta(minutes=5)
    following = start + timedelta(minutes=10)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    _fail_one_bucket(engine, faulted, attempted)

    first = await engine.evaluate_pending(ls)
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None
    assert (fault.attempts, fault.given_up_at, fault.resolved_at) == (1, None, None)
    assert fault.first_failed_at == fault.last_failed_at
    assert fault.error_class == "RuntimeError"
    assert (first.failed, first.given_up) == (1, 0)
    assert first.skipped_reason == "evaluation failed"
    assert (await _watermark(db_session, ls)).evaluated_through == start

    second = await engine.evaluate_pending(ls)
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None and fault.attempts == 2
    assert (second.failed, second.given_up) == (1, 0)
    assert (await _watermark(db_session, ls)).evaluated_through == start

    third = await engine.evaluate_pending(ls)
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None
    assert (fault.attempts, fault.resolved_at) == (3, None)
    assert fault.given_up_at is not None
    assert fault.last_failed_at >= fault.first_failed_at
    # Given up: the bucket is skipped, the next one is judged in the same tick,
    # and the tick itself is not reported as an unfinished evaluation.
    assert (third.failed, third.given_up) == (1, 1)
    assert third.skipped_reason is None
    assert attempted == [start, faulted, faulted, faulted, following]
    assert (await _watermark(db_session, ls)).evaluated_through == following

    # Nothing newer exists, so the next tick has nothing to judge -- not the
    # same failing bucket forever.
    later = await engine.evaluate_pending(ls)
    assert later.skipped_reason == "no pending metrics"
    assert attempted.count(faulted) == 3


async def test_given_up_bucket_writes_no_verdict_and_keeps_the_open_incident(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst, criticality=Criticality.CRITICAL)
    start = utc(h=10)
    await _reliable_eps_baseline(
        db_session, ls, weekday=start.isoweekday(), hour=10, median=50.0
    )
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    # The first bucket opens the drop (CRITICAL opens after 1); the second --
    # healthy -- is the one that cannot be judged.
    await add_metric(db_session, ls, start, average_eps=3.0, event_count=900)
    faulted = start + timedelta(minutes=5)
    await add_metric(db_session, ls, faulted, average_eps=50.0, event_count=15000)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    _fail_one_bucket(engine, faulted, attempted)
    for _ in range(3):
        await engine.evaluate_pending(ls)

    incident = await db_session.scalar(
        select(LogSourceAnomaly).where(LogSourceAnomaly.log_source_id == ls.id)
    )
    assert incident is not None
    assert incident.anomaly_type == AnomalyType.VOLUME_DROP.value
    # The unreadable bucket did not downgrade the incident, extend it to the
    # bucket it never measured, or create a row for it.
    assert incident.state == AnomalyState.OPEN.value
    assert incident.anomaly_end == start + timedelta(minutes=5)
    assert incident.observed_value == 3.0
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LogSourceAnomaly)
            .where(
                LogSourceAnomaly.log_source_id == ls.id,
                LogSourceAnomaly.anomaly_start == faulted,
            )
        )
        == 0
    )

    state = await db_session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == ls.id,
            LogSourceDetectorState.anomaly_type == AnomalyType.VOLUME_DROP.value,
        )
    )
    assert state is not None
    # No detector folded the given-up bucket: `last_interval_start` still names
    # the last bucket that was actually judged.
    assert state.last_interval_start == start
    assert AnomalyState(state.state) is AnomalyState.OPEN
    assert (
        await db_session.scalar(
            select(func.count())
            .select_from(LogSourceDetectorState)
            .where(
                LogSourceDetectorState.log_source_id == ls.id,
                LogSourceDetectorState.last_interval_start == faulted,
            )
        )
        == 0
    )
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None and fault.given_up_at is not None


async def test_transient_fault_is_resolved_by_a_later_success(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    await add_metric(db_session, ls, start)
    faulted = start + timedelta(minutes=5)
    await add_metric(db_session, ls, faulted)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    active = {"on": True}
    _fail_one_bucket(engine, faulted, attempted, active=active)

    await engine.evaluate_pending(ls)
    await engine.evaluate_pending(ls)
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None and fault.attempts == 2
    assert fault.given_up_at is None and fault.resolved_at is None

    active["on"] = False
    final = await engine.evaluate_pending(ls)
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None
    assert fault.attempts == 2  # the recovery did not count as an attempt
    assert fault.given_up_at is None
    assert fault.resolved_at is not None
    assert (final.failed, final.given_up) == (0, 0)
    assert final.skipped_reason is None
    assert (await _watermark(db_session, ls)).evaluated_through == faulted


async def test_each_bucket_has_its_own_attempt_count(db_session) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    first_bucket = start
    second_bucket = start + timedelta(minutes=5)
    await add_metric(db_session, ls, first_bucket)
    await add_metric(db_session, ls, second_bucket)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    evaluate_interval = engine.evaluate_interval

    async def fail_both(log_source, metric):  # type: ignore[no-untyped-def]
        attempted.append(metric.bucket_start)
        if metric.bucket_start in {first_bucket, second_bucket}:
            raise RuntimeError("detector fault")
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = fail_both  # type: ignore[method-assign]

    # Two ticks exhaust the first bucket; the third gives it up and starts the
    # second bucket's own count, which the two ticks after exhaust in turn.
    for _ in range(5):
        await engine.evaluate_pending(ls)

    first = await _fault_row(db_session, ls.id, first_bucket)
    second = await _fault_row(db_session, ls.id, second_bucket)
    assert first is not None and second is not None
    assert (first.attempts, second.attempts) == (3, 3)
    assert first.given_up_at is not None and second.given_up_at is not None
    assert (await _watermark(db_session, ls)).evaluated_through == second_bucket


async def test_first_run_failure_seeds_the_watermark_only_when_it_gives_up(
    db_session,
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await add_metric(db_session, ls, start)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    _fail_one_bucket(engine, start, attempted)

    for expected_attempts in (1, 2):
        report = await engine.evaluate_pending(ls)
        fault = await _fault_row(db_session, ls.id, start)
        assert fault is not None and fault.attempts == expected_attempts
        assert fault.given_up_at is None
        assert report.given_up == 0
        # A retryable bucket keeps the first-run path, so the mark stays unset.
        assert await _watermark(db_session, ls) is None

    report = await engine.evaluate_pending(ls)
    fault = await _fault_row(db_session, ls.id, start)
    assert fault is not None and fault.attempts == 3
    assert fault.given_up_at is not None
    assert (report.failed, report.given_up) == (1, 1)
    watermark = await _watermark(db_session, ls)
    assert watermark is not None and watermark.evaluated_through == start

    later = await engine.evaluate_pending(ls)
    assert later.skipped_reason == "no pending metrics"
    assert attempted.count(start) == 3


async def test_a_fault_on_one_source_does_not_stop_another(db_session) -> None:
    inst = await make_instance(db_session)
    start = utc(h=10)
    faulted = await make_log_source(db_session, inst, qradar_id=1, name="faulted")
    healthy = await make_log_source(db_session, inst, qradar_id=2, name="healthy")
    for ls in (faulted, healthy):
        await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
        for index in range(2):
            await add_metric(db_session, ls, start + timedelta(minutes=5 * index))
    failed_bucket = start + timedelta(minutes=5)
    newest = start + timedelta(minutes=5)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    evaluate_interval = engine.evaluate_interval

    async def fail_faulted(log_source, metric):  # type: ignore[no-untyped-def]
        attempted.append((log_source.id, metric.bucket_start))
        if log_source.id == faulted.id and metric.bucket_start == failed_bucket:
            raise RuntimeError("detector fault")
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = fail_faulted  # type: ignore[method-assign]

    # Every tick, the healthy source is judged and its watermark advances even
    # while the other one is failing, and again on the tick that gives up.
    for _ in range(3):
        await engine.evaluate_pending(faulted)
        await engine.evaluate_pending(healthy)
        assert (await _watermark(db_session, healthy)).evaluated_through == newest

    fault = await _fault_row(db_session, faulted.id, failed_bucket)
    assert fault is not None and fault.given_up_at is not None
    assert (await _watermark(db_session, faulted)).evaluated_through == failed_bucket
    healthy_attempts = [bucket for source, bucket in attempted if source == healthy.id]
    assert healthy_attempts == [start, newest]  # judged once, not per fault tick


async def test_failure_log_line_names_the_bucket_and_the_attempt(
    db_session, caplog
) -> None:
    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    faulted = start + timedelta(minutes=5)
    await add_metric(db_session, ls, faulted)

    engine = AnomalyEngine(db_session, settings=_settings())
    attempted: list = []
    _fail_one_bucket(engine, faulted, attempted)

    caplog.set_level(logging.WARNING, logger="app.anomaly.engine")
    await engine.evaluate_pending(ls)

    records = [
        record
        for record in caplog.records
        if record.getMessage() == "anomaly evaluation failed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.log_source_id == str(ls.id)
    assert record.bucket_start == faulted.isoformat()
    assert record.attempts == 1
    assert record.given_up is False
    assert record.error_class == "RuntimeError"


async def test_fault_bulk_load_costs_one_statement_whatever_the_fleet(db_session) -> None:
    inst = await make_instance(db_session)
    start = utc(h=10)
    created = 0

    async def statements_for(source_count: int) -> int:
        nonlocal created
        sources = []
        for _ in range(source_count):
            created += 1
            ls = await make_log_source(
                db_session, inst, qradar_id=3000 + created, name=f"fleet-{created}"
            )
            await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
            await add_metric(db_session, ls, start)
            sources.append(ls)
        engine = AnomalyEngine(db_session, settings=_settings())
        with recorded_statements(db_session, only="anomaly_evaluation_fault") as statements:
            for ls in sources:
                await engine.evaluate_pending(ls)
        return len(statements)

    # One bulk read of open faults per engine instance, not one per source.
    assert await statements_for(2) == 1
    assert await statements_for(6) == 1


async def test_evaluate_anomalies_reports_failed_and_given_up(
    db_session, monkeypatch
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from app.workers import tasks

    inst = await make_instance(db_session)
    ls = await make_log_source(db_session, inst)
    start = utc(h=10)
    await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
    faulted = start + timedelta(minutes=5)
    await add_metric(db_session, ls, start)
    await add_metric(db_session, ls, faulted)
    await db_session.commit()

    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    evaluate_interval = AnomalyEngine.evaluate_interval

    async def fail(self, log_source, metric):  # type: ignore[no-untyped-def]
        if metric.bucket_start == faulted:
            raise RuntimeError("detector fault")
        return await evaluate_interval(self, log_source, metric)

    monkeypatch.setattr(AnomalyEngine, "evaluate_interval", fail)

    assert await tasks.evaluate_anomalies() == {
        "sources": 1,
        "opened": 0,
        "resolved": 0,
        "failed": 1,
        "given_up": 0,
    }
    result = None
    for _ in range(2):
        result = await tasks.evaluate_anomalies()
    assert result is not None
    assert (result["failed"], result["given_up"]) == (1, 1)
    fault = await _fault_row(db_session, ls.id, faulted)
    assert fault is not None and fault.given_up_at is not None


async def test_fault_bookkeeping_failure_holds_one_source_and_judges_the_other(
    db_session, caplog
) -> None:
    """A fault that cannot be written must cost only its own bucket.

    The upsert, the give-up update and the bulk read run after the evaluation
    savepoint rolled back. If they raise too, the bucket is held exactly as it
    was before T-171, and every later source of the tick is still judged.
    """
    inst = await make_instance(db_session)
    start = utc(h=10)
    faulted = await make_log_source(db_session, inst, qradar_id=11, name="bookkeeping")
    healthy = await make_log_source(db_session, inst, qradar_id=12, name="healthy")
    for ls in (faulted, healthy):
        await _seed_evaluation_watermark(db_session, ls, start - timedelta(minutes=5))
        await add_metric(db_session, ls, start)

    engine = AnomalyEngine(db_session, settings=_settings())
    evaluate_interval = engine.evaluate_interval

    async def fail_faulted(log_source, metric):  # type: ignore[no-untyped-def]
        if log_source.id == faulted.id:
            raise RuntimeError("detector fault")
        return await evaluate_interval(log_source, metric)

    engine.evaluate_interval = fail_faulted  # type: ignore[method-assign]

    async def unwritable(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("fault table unavailable")

    engine._faults.record_failure = unwritable  # type: ignore[method-assign]

    caplog.set_level(logging.WARNING, logger="app.anomaly.engine")
    held = await engine.evaluate_pending(faulted)
    assert (held.failed, held.given_up) == (1, 0)
    assert held.skipped_reason == "evaluation failed"
    # The bookkeeping rolled back cleanly and the watermark did not move.
    assert (
        await _watermark(db_session, faulted)
    ).evaluated_through == start - timedelta(minutes=5)
    assert await _fault_row(db_session, faulted.id, start) is None

    judged = await engine.evaluate_pending(healthy)
    assert judged.skipped_reason is None
    assert (await _watermark(db_session, healthy)).evaluated_through == start

    # One warning, naming the source, the bucket and the bookkeeping class:
    # not the evaluation's class, which would hide why no row was written.
    records = [
        record
        for record in caplog.records
        if record.getMessage() == "anomaly fault bookkeeping failed"
    ]
    assert len(records) == 1
    record = records[0]
    assert record.log_source_id == str(faulted.id)
    assert record.bucket_start == start.isoformat()
    assert record.error_class == "RuntimeError"
    assert record.evaluation_error_class == "RuntimeError"
