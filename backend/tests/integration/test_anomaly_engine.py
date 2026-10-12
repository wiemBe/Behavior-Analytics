"""Anomaly engine: hysteresis open/resolve, dedup, flapping prevention,
maintenance suppression, and the alert lifecycle it drives. DB-gated."""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.core.config import Settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyType, Criticality
from app.models.log_source import LogSourceAnomaly, LogSourceBaseline
from app.models.monitoring import AnomalyEvaluationWatermark
from tests.integration.factories import add_metric, make_instance, make_log_source, utc

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
