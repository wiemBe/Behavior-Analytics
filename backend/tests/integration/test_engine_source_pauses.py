"""JDBC-1 P6: the anomaly engine excludes buckets overlapping a QRadar pause.

Read-time exclusion only, behind the D-52 switch. A retained bucket inside a
pause is not judged (the watermark still advances and the confirmation streaks
reset); the preceding-empty walk stops at an overlapping row; and a pause wholly
inside a missing-row gap breaks every hysteresis streak. With the switch off no
span statement is issued and behaviour is exactly today's. Real Postgres, real
engine, real task body.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.alerts.fingerprint import anomaly_fingerprint
from app.anomaly.engine import (
    SKIPPED_OVERLAPS_PAUSE,
    AnomalyEngine,
    EvaluationReport,
    _evaluated_spans,
    _span_overlaps_bucket,
)
from app.core.config import Settings
from app.models.alert import Alert
from app.models.base import utcnow
from app.models.enums import AlertStatus, AnomalyState, AnomalyType, Criticality
from app.models.feature_switch import FeatureSwitch
from app.models.log_source import (
    AnomalyStateTransition,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.log_source_pause import LogSourcePause
from app.models.monitoring import AnomalyEvaluationWatermark
from app.services.feature_switches import UNREADABLE_REASON
from app.services.source_pause_spans import PauseSpan, close_pause, open_pause, overlapping_spans
from app.workers import tasks
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

REASON = "QRADAR_DISABLED"
T0 = utc(h=10)
NOW = T0 + timedelta(days=1)


def _settings(*, pause: bool) -> Settings:
    return Settings(encryption_key="x" * 44, qradar_source_pause_enabled=pause)


async def _source(session, *, criticality: Criticality = Criticality.MEDIUM):
    instance = await make_instance(session)
    return await make_log_source(
        session,
        instance,
        enabled=True,
        monitoring_enabled=True,
        criticality=criticality,
    )


async def _baseline(session, source, at, *, name: str, median: float) -> None:
    session.add(
        LogSourceBaseline(
            log_source_id=source.id,
            metric_name=name,
            weekday=at.isoweekday(),
            hour=at.hour,
            median=median,
            mad=2.0 if name == "average_eps" else 500.0,
            p05=median * 0.9,
            p95=median * 1.1,
            sample_count=30,
            is_reliable=True,
            baseline_version=1,
            observations=[median],
        )
    )
    await session.flush()


async def _eps_baseline(session, source, at, *, median: float = 50.0) -> None:
    await _baseline(session, source, at, name="average_eps", median=median)


async def _count_baseline(session, source, at, *, median: float = 15_000.0) -> None:
    await _baseline(session, source, at, name="event_count", median=median)


async def _bucket(session, source, at, *, eps: float = 50.0, **kwargs):
    count = int(eps * 300)
    values = dict(
        average_eps=eps,
        event_count=count,
        stored_event_count=count,
        payload_signature=f"s-{at.isoformat()}",
    )
    values.update(kwargs)
    return await add_metric(session, source, at, **values)


async def _empty(session, source, at):
    return await add_metric(
        session,
        source,
        at,
        average_eps=0.0,
        event_count=0,
        stored_event_count=0,
        last_event_at=None,
        event_delay_seconds=None,
        payload_signature=f"e-{at.isoformat()}",
    )


async def _watermark(session, source, at) -> None:
    session.add(
        AnomalyEvaluationWatermark(
            log_source_id=source.id, evaluated_through=at, updated_at=at
        )
    )
    await session.flush()


def _span(session, source, start, end) -> None:
    session.add(
        LogSourcePause(
            log_source_id=source.id, reason=REASON, starts_at=start, ends_at=end
        )
    )


async def _state(session, source, atype: AnomalyType) -> LogSourceDetectorState:
    state = await session.scalar(
        select(LogSourceDetectorState).where(
            LogSourceDetectorState.log_source_id == source.id,
            LogSourceDetectorState.anomaly_type == atype,
        )
    )
    assert state is not None
    return state


async def _open_alert(session, source, atype: AnomalyType) -> Alert | None:
    return await session.scalar(
        select(Alert).where(
            Alert.dedup_key == anomaly_fingerprint(source.id, atype.value),
            Alert.status != AlertStatus.RESOLVED,
        )
    )


async def _assert_no_verdicts(session, *sources) -> None:
    """No incident, transition or alert for these sources."""
    ids = [source.id for source in sources]
    count = await session.scalar(
        select(func.count())
        .select_from(LogSourceAnomaly)
        .where(LogSourceAnomaly.log_source_id.in_(ids))
    )
    assert count == 0
    transitions = await session.scalar(
        select(func.count())
        .select_from(AnomalyStateTransition)
        .join(LogSourceAnomaly, AnomalyStateTransition.anomaly_id == LogSourceAnomaly.id)
        .where(LogSourceAnomaly.log_source_id.in_(ids))
    )
    assert transitions == 0
    alerts = await session.scalar(
        select(func.count()).select_from(Alert).where(Alert.source_id.in_(ids))
    )
    assert alerts == 0


# --- pending iteration ---------------------------------------------------------


async def test_overlap_not_judged_watermark_advances(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    partial = await _source(db_session)
    opened = await _source(db_session)
    # A closed span whose boundaries fall inside buckets: [T0+2, T0+17).
    _span(db_session, partial, T0 + timedelta(minutes=2), T0 + timedelta(minutes=17))
    # An open span (ends_at NULL) overlaps everything after its start.
    await open_pause(
        db_session, [opened.id], reason=REASON, starts_at=T0, now=NOW
    )
    await _watermark(db_session, partial, T0 - timedelta(minutes=5))
    await _watermark(db_session, opened, T0 - timedelta(minutes=5))

    for source in (partial, opened):
        for index in range(4):
            await _bucket(
                db_session, source, T0 + timedelta(minutes=5 * index), eps=3.0
            )
        for atype in (AnomalyType.VOLUME_DROP, AnomalyType.NO_EVENTS):
            db_session.add(
                LogSourceDetectorState(
                    log_source_id=source.id,
                    anomaly_type=atype,
                    state=AnomalyState.OPEN,
                    is_open=True,
                    consecutive_anomalous=3,
                    consecutive_healthy=2,
                    last_interval_start=T0 - timedelta(minutes=5),
                )
            )
    await db_session.flush()

    reports = [
        await engine.evaluate_pending(partial),
        await engine.evaluate_pending(opened),
    ]

    last = T0 + timedelta(minutes=15)
    for source, report in zip((partial, opened), reports, strict=True):
        assert report.skipped_reason == SKIPPED_OVERLAPS_PAUSE
        watermark = await db_session.get(AnomalyEvaluationWatermark, source.id)
        assert watermark is not None and watermark.evaluated_through == last
        states = (
            await db_session.scalars(
                select(LogSourceDetectorState).where(
                    LogSourceDetectorState.log_source_id == source.id
                )
            )
        ).all()
        assert len(states) == 2
        assert all(
            s.consecutive_anomalous == 0 and s.consecutive_healthy == 0 for s in states
        )
        # Only the counters move: no lifecycle move, no state rewrite.
        assert all(
            s.state == AnomalyState.OPEN
            and s.last_interval_start == T0 - timedelta(minutes=5)
            for s in states
        )
    await _assert_no_verdicts(db_session, partial, opened)


async def test_legacy_complete_zero_cannot_extend_streak(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session, criticality=Criticality.CRITICAL)
    await _count_baseline(db_session, source, T0)
    for index in range(4):
        await _empty(db_session, source, T0 + timedelta(minutes=5 * index))
    # [T0+5, T0+15): two legacy COMPLETE zero rows live inside the span.
    await open_pause(
        db_session,
        [source.id],
        reason=REASON,
        starts_at=T0 + timedelta(minutes=5),
        now=NOW,
    )
    await close_pause(
        db_session,
        [source.id],
        reason=REASON,
        observed_at=T0 + timedelta(minutes=14),
        bucket_seconds=300,
    )
    spans = (
        await overlapping_spans(
            db_session,
            [source.id],
            T0 - timedelta(days=1),
            T0 + timedelta(days=1),
        )
    ).get(source.id, [])
    assert len(spans) == 1 and spans[0].ends_at == T0 + timedelta(minutes=15)
    current = await db_session.scalar(
        select(LogSourceMetric).where(
            LogSourceMetric.log_source_id == source.id,
            LogSourceMetric.bucket_start == T0 + timedelta(minutes=15),
        )
    )

    # The walk stops at the first overlapping COMPLETE zero, exactly as at a gap.
    assert await engine._preceding_empty_buckets(source.id, current, spans=spans) == 0
    # Not vacuous: the same legacy zeros do form a streak when no span is present.
    assert await engine._preceding_empty_buckets(source.id, current, spans=[]) == 3

    report = await engine.evaluate_interval(source, current)
    assert report.opened == []  # exact: no detector may open on this bucket
    assert await _open_alert(db_session, source, AnomalyType.NO_EVENTS) is None


async def test_pause_in_missing_gap_breaks_hysteresis(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    drop = await _source(db_session)
    recover = await _source(db_session)
    for source in (drop, recover):
        await _eps_baseline(db_session, source, T0)

    # An anomalous streak that would open on the next anomalous bucket.
    first = await _bucket(db_session, drop, T0, eps=3.0)
    await engine.evaluate_interval(drop, first)
    assert (await _state(db_session, drop, AnomalyType.VOLUME_DROP)).state == (
        AnomalyState.CANDIDATE
    )
    _span(
        db_session,
        drop,
        T0 + timedelta(minutes=7),
        T0 + timedelta(minutes=8),
    )  # wholly inside the gap after the T0 bucket
    second = await _bucket(
        db_session, drop, T0 + timedelta(minutes=10), eps=3.0
    )
    await engine.evaluate_interval(drop, second)
    state = await _state(db_session, drop, AnomalyType.VOLUME_DROP)
    assert state.consecutive_anomalous == 1
    assert state.state == AnomalyState.CANDIDATE  # not promoted across the pause
    assert await _open_alert(db_session, drop, AnomalyType.VOLUME_DROP) is None

    # A healthy streak that would resolve on the next healthy bucket.
    for index in range(2):
        metric = await _bucket(
            db_session, recover, T0 + timedelta(minutes=5 * index), eps=3.0
        )
        await engine.evaluate_interval(recover, metric)
    for index in (2, 3):
        metric = await _bucket(
            db_session, recover, T0 + timedelta(minutes=5 * index), eps=50.0
        )
        await engine.evaluate_interval(recover, metric)
    state = await _state(db_session, recover, AnomalyType.VOLUME_DROP)
    assert state.state == AnomalyState.RECOVERING
    assert state.consecutive_healthy == 2
    _span(
        db_session,
        recover,
        T0 + timedelta(minutes=17),
        T0 + timedelta(minutes=18),
    )  # wholly inside the gap after the T0+15 bucket
    metric = await _bucket(
        db_session, recover, T0 + timedelta(minutes=25), eps=50.0
    )
    await engine.evaluate_interval(recover, metric)
    state = await _state(db_session, recover, AnomalyType.VOLUME_DROP)
    assert state.consecutive_healthy == 1
    assert state.state == AnomalyState.RECOVERING  # not resolved across the pause


async def test_pause_in_long_gap_breaks_hysteresis(db_session) -> None:
    """A pause wholly inside a missing-row gap longer than a day is still read.

    The gap check compares a detector's `last_interval_start` with the next bucket,
    so the span read must start at the oldest state start, not one day before the
    first pending bucket (the audit repro: T0, a T0+1h span and a bucket at T0+7d).
    """
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session)
    await _eps_baseline(db_session, source, T0)

    first = await _bucket(db_session, source, T0, eps=3.0)
    await engine.evaluate_interval(source, first)
    state = await _state(db_session, source, AnomalyType.VOLUME_DROP)
    assert state.consecutive_anomalous == 1
    assert state.state == AnomalyState.CANDIDATE

    _span(db_session, source, T0 + timedelta(hours=1), T0 + timedelta(hours=2))
    await _watermark(db_session, source, T0)  # only the T0+7d bucket is pending
    far = await _bucket(db_session, source, T0 + timedelta(days=7), eps=3.0)

    report = await engine.evaluate_pending(source)

    assert report.skipped_reason is None
    state = await _state(db_session, source, AnomalyType.VOLUME_DROP)
    assert state.consecutive_anomalous == 1  # not 2: the streak restarted at the pause
    assert state.state == AnomalyState.CANDIDATE  # not promoted across a 7-day gap
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_DROP) is None
    watermark = await db_session.get(AnomalyEvaluationWatermark, source.id)
    assert watermark is not None and watermark.evaluated_through == far.bucket_start


async def test_no_events_streak_broken_by_gap_pause(db_session) -> None:
    """A pause inside the preceding-empty window stops the NO_EVENTS streak.

    The walk stops at a row overlapping a span exactly as it stops at a gap, so an
    empty bucket after the pause cannot inherit the earlier silence (D-52 §5).
    """
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session)  # MEDIUM: open_after 2, so CANDIDATE is observable
    await _count_baseline(db_session, source, T0)

    first = await _empty(db_session, source, T0)  # inside grace: healthy
    await engine.evaluate_interval(source, first)
    second = await _empty(db_session, source, T0 + timedelta(minutes=5))
    await engine.evaluate_interval(source, second)
    state = await _state(db_session, source, AnomalyType.NO_EVENTS)
    assert state.consecutive_anomalous == 1
    assert state.state == AnomalyState.CANDIDATE

    _span(
        db_session,
        source,
        T0 + timedelta(minutes=7),
        T0 + timedelta(minutes=8),
    )  # inside the [T0+5, T0+10) row, so the walk must stop there
    third = await _empty(db_session, source, T0 + timedelta(minutes=10))
    await engine.evaluate_interval(source, third)

    state = await _state(db_session, source, AnomalyType.NO_EVENTS)
    # The pause stops the preceding-empty walk at once, so this bucket is healthy
    # inside grace and the anomaly streak is gone — not promoted to OPEN as it
    # would be if the two empty rows still counted (counter 2).
    assert state.consecutive_anomalous == 0
    assert state.state != AnomalyState.OPEN
    assert await _open_alert(db_session, source, AnomalyType.NO_EVENTS) is None


async def test_volume_spike_streak_broken_by_gap_pause(db_session) -> None:
    """A VOLUME_SPIKE confirmation streak does not cross a pause inside a missing gap."""
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session)
    await _eps_baseline(db_session, source, T0)

    first = await _bucket(db_session, source, T0, eps=500.0)
    await engine.evaluate_interval(source, first)
    state = await _state(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert state.consecutive_anomalous == 1
    assert state.state == AnomalyState.CANDIDATE

    _span(
        db_session,
        source,
        T0 + timedelta(minutes=7),
        T0 + timedelta(minutes=8),
    )
    second = await _bucket(db_session, source, T0 + timedelta(minutes=10), eps=500.0)
    await engine.evaluate_interval(source, second)

    state = await _state(db_session, source, AnomalyType.VOLUME_SPIKE)
    assert state.consecutive_anomalous == 1
    assert state.state == AnomalyState.CANDIDATE  # not promoted to OPEN
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_SPIKE) is None


async def test_preceding_empty_lookback_wide_bucket(db_session) -> None:
    """The span lookback scales with bucket width, not a fixed day.

    With a two-day bucket the pause two days before the current bucket is older than
    a one-day lookback. A fixed lookback would miss it and let two empty rows open
    NO_EVENTS; the derived lookback reads it and stops the walk at once.
    """
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session, criticality=Criticality.CRITICAL)
    width = 2 * 24 * 3600
    step = timedelta(seconds=width)
    earlier = T0
    middle = T0 + step
    current = T0 + 2 * step
    for at in (earlier, middle, current):
        await _count_baseline(db_session, source, at)
    for at in (earlier, middle, current):
        await add_metric(
            db_session,
            source,
            at,
            bucket_seconds=width,
            average_eps=0.0,
            event_count=0,
            stored_event_count=0,
            last_event_at=None,
            event_delay_seconds=None,
            payload_signature=f"w-{at.isoformat()}",
        )
    _span(db_session, source, middle + timedelta(minutes=1), middle + timedelta(minutes=2))
    row = await db_session.scalar(
        select(LogSourceMetric).where(
            LogSourceMetric.log_source_id == source.id,
            LogSourceMetric.bucket_start == current,
        )
    )
    assert row is not None

    spans = (
        await overlapping_spans(db_session, [source.id], current - 12 * step, current + step)
    ).get(source.id, [])
    assert len(spans) == 1  # the derived lookback reaches the wide-bucket span

    report = await engine.evaluate_interval(source, row)

    assert report.opened == []
    assert await _open_alert(db_session, source, AnomalyType.NO_EVENTS) is None


async def test_reenable_requires_new_whole_bucket(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session)
    await _eps_baseline(db_session, source, T0)

    # A genuine OPEN incident, then the administrative closure a disable performs.
    for index in range(2):
        metric = await _bucket(
            db_session, source, T0 + timedelta(minutes=5 * index), eps=3.0
        )
        await engine.evaluate_interval(source, metric)
    old = await db_session.scalar(
        select(LogSourceAnomaly).where(
            LogSourceAnomaly.log_source_id == source.id,
            LogSourceAnomaly.anomaly_type == AnomalyType.VOLUME_DROP,
        )
    )
    assert old is not None and old.state == AnomalyState.OPEN
    closed_at = T0 + timedelta(minutes=6)
    old.state = AnomalyState.RESOLVED
    old.resolved_at = closed_at
    state = await _state(db_session, source, AnomalyType.VOLUME_DROP)
    state.state = AnomalyState.RESOLVED
    state.consecutive_anomalous = 0
    state.consecutive_healthy = 0
    state.is_open = False
    state.active_anomaly_id = None
    state.open_anomaly_id = None
    alert = await _open_alert(db_session, source, AnomalyType.VOLUME_DROP)
    if alert is not None:
        alert.status = AlertStatus.RESOLVED
        alert.resolved_at = closed_at
    await db_session.flush()
    await _watermark(db_session, source, T0 + timedelta(minutes=5))

    # [T0+10, T0+22): the [T0+20, T0+25) bucket straddles ends_at and is skipped;
    # [T0+25, T0+30) is the first whole COMPLETE, non-overlapping bucket that counts.
    _span(
        db_session,
        source,
        T0 + timedelta(minutes=10),
        T0 + timedelta(minutes=22),
    )
    for minute in (10, 15, 20, 25):
        await _bucket(db_session, source, T0 + timedelta(minutes=minute), eps=3.0)

    report = await engine.evaluate_pending(source)

    assert report.skipped_reason == SKIPPED_OVERLAPS_PAUSE
    watermark = await db_session.get(AnomalyEvaluationWatermark, source.id)
    assert watermark is not None
    assert watermark.evaluated_through == T0 + timedelta(minutes=25)
    state = await _state(db_session, source, AnomalyType.VOLUME_DROP)
    assert state.consecutive_anomalous == 1
    assert state.state == AnomalyState.CANDIDATE
    await db_session.refresh(old)
    assert old.state == AnomalyState.RESOLVED
    assert await _open_alert(db_session, source, AnomalyType.VOLUME_DROP) is None

    # Later evidence uses the ordinary confirmation rule and opens a new incident.
    await _bucket(db_session, source, T0 + timedelta(minutes=30), eps=3.0)
    await engine.evaluate_pending(source)
    state = await _state(db_session, source, AnomalyType.VOLUME_DROP)
    assert state.state == AnomalyState.OPEN
    await db_session.refresh(old)
    assert old.state == AnomalyState.RESOLVED  # the closed incident was not revived


# --- direct entry, switch-off and cost -----------------------------------------


async def test_direct_interval_overlap_skipped(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    source = await _source(db_session)
    await _eps_baseline(db_session, source, T0)
    await open_pause(
        db_session,
        [source.id],
        reason=REASON,
        starts_at=T0 + timedelta(minutes=2),
        now=NOW,
    )
    overlapping = await _bucket(
        db_session, source, T0 + timedelta(minutes=5), eps=50.0
    )
    clean = await _bucket(db_session, source, T0 - timedelta(minutes=5), eps=50.0)

    report = await engine.evaluate_interval(source, overlapping)

    assert report.interval_start == T0 + timedelta(minutes=5)
    assert report.skipped_reason == SKIPPED_OVERLAPS_PAUSE
    assert report.opened == [] and report.resolved == [] and report.states == {}
    await _assert_no_verdicts(db_session, source)
    assert await db_session.get(AnomalyEvaluationWatermark, source.id) is None

    # A bucket outside the span is judged as usual.
    report = await engine.evaluate_interval(source, clean)
    assert report.skipped_reason is None
    states = (
        await db_session.scalars(
            select(LogSourceDetectorState).where(
                LogSourceDetectorState.log_source_id == source.id
            )
        )
    ).all()
    assert len(states) > 0


async def test_switch_off_ignores_spans(db_session) -> None:
    off_engine = AnomalyEngine(db_session, settings=_settings(pause=False))
    today_engine = AnomalyEngine(db_session, settings=_settings(pause=False))
    off = await _source(db_session)
    today = await _source(db_session)
    for source in (off, today):
        await _eps_baseline(db_session, source, T0)
        for index in range(3):
            await _bucket(
                db_session, source, T0 + timedelta(minutes=5 * index), eps=50.0
            )
        await _watermark(db_session, source, T0 - timedelta(minutes=5))
    await open_pause(db_session, [off.id], reason=REASON, starts_at=T0, now=NOW)

    with recorded_statements(db_session, only="log_source_pause") as statements:
        off_report = await off_engine.evaluate_pending(off)
    assert statements == []  # no span statement at all with the switch off
    today_report = await today_engine.evaluate_pending(today)

    assert off_report.skipped_reason == today_report.skipped_reason
    assert off_report.opened == today_report.opened
    assert off_report.resolved == today_report.resolved
    assert off_report.candidates == today_report.candidates
    assert off_report.states == today_report.states
    for source in (off, today):
        watermark = await db_session.get(AnomalyEvaluationWatermark, source.id)
        assert watermark is not None
        assert watermark.evaluated_through == T0 + timedelta(minutes=10)
    assert await db_session.get(LogSourcePause, (off.id, REASON, T0)) is not None


async def test_one_span_read_per_evaluation(db_session) -> None:
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    one = await _source(db_session)
    many = await _source(db_session)
    far = T0 - timedelta(days=2)
    for source, count in ((one, 1), (many, 30)):
        await _eps_baseline(db_session, source, T0)
        await _watermark(db_session, source, T0 - timedelta(minutes=5))
        # A real span, just outside the pending window: it still costs one read.
        _span(db_session, source, far, far + timedelta(hours=1))
        for index in range(count):
            await _bucket(
                db_session, source, T0 + timedelta(minutes=5 * index), eps=50.0
            )
    await db_session.flush()

    with recorded_statements(db_session, only="log_source_pause") as one_statements:
        await engine.evaluate_pending(one)
    with recorded_statements(db_session, only="log_source_pause") as many_statements:
        await engine.evaluate_pending(many)

    assert len(one_statements) == 1
    assert len(many_statements) == 1


async def test_two_sources_do_not_share_spans(db_session) -> None:
    """One engine scoping two concurrent evaluations keeps each source's spans separate.

    Both calls set their spans before either reads them (a gate after the scope is
    entered forces that interleaving). With instance state both would read the
    last-written list; with a context-local value each reads its own.
    """
    engine = AnomalyEngine(db_session, settings=_settings(pause=True))
    first = await _source(db_session)
    other = await _source(db_session)
    first_metric = await _bucket(db_session, first, T0, eps=50.0)
    other_metric = await _bucket(db_session, other, T0 + timedelta(days=1), eps=50.0)
    spans_first = [
        PauseSpan(first.id, REASON, T0 - timedelta(minutes=1), T0 + timedelta(minutes=1))
    ]

    arrived = 0
    ready = asyncio.Event()
    gate = asyncio.Event()

    async def gated(log_source, metric):  # type: ignore[no-untyped-def]
        nonlocal arrived
        arrived += 1
        if arrived == 2:
            ready.set()
        await gate.wait()
        # Read the spans this call actually received, through the engine's own
        # overlap predicate. With instance state both calls see the last-written
        # list; with a context-local value each sees its own.
        spans = _evaluated_spans.get()
        bucket_end = metric.bucket_start + timedelta(seconds=metric.bucket_seconds)
        skipped = bool(spans) and _span_overlaps_bucket(spans, metric.bucket_start, bucket_end)
        return EvaluationReport(
            log_source.id,
            metric.bucket_start,
            skipped_reason=SKIPPED_OVERLAPS_PAUSE if skipped else None,
        )

    async def run(log_source, metric, spans):  # type: ignore[no-untyped-def]
        async with engine._scoped_spans(spans):
            return await gated(log_source, metric)

    task_first = asyncio.create_task(run(first, first_metric, spans_first))
    task_other = asyncio.create_task(run(other, other_metric, []))
    await asyncio.wait_for(ready.wait(), timeout=10)
    gate.set()
    report_first, report_other = await asyncio.gather(task_first, task_other)

    assert report_first.skipped_reason == SKIPPED_OVERLAPS_PAUSE
    assert report_other.skipped_reason is None


# --- the real task, switch off --------------------------------------------------


class TaskRig:
    """A real engine/maker on the test schema, with the task's globals pointed at it."""

    def __init__(self, url: str, monkeypatch: pytest.MonkeyPatch) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.monkeypatch = monkeypatch
        monkeypatch.setattr(tasks, "get_sessionmaker", lambda: self.maker)

    def base(self, **env: bool) -> None:
        settings = Settings(_env_file=None, **env)
        self.monkeypatch.setattr(tasks, "get_settings", lambda: settings)

    async def row(self, key: str, enabled: bool) -> None:
        async with self.maker() as session:
            session.add(
                FeatureSwitch(
                    key=key, enabled=enabled, updated_at=utcnow(), updated_by="alice"
                )
            )
            await session.commit()


@pytest.fixture
async def rig(db_schema, monkeypatch: pytest.MonkeyPatch):
    task_rig = TaskRig(db_schema, monkeypatch)
    try:
        yield task_rig
    finally:
        await task_rig.engine.dispose()


@pytest.mark.parametrize("row", [None, False])
async def test_policy_off_preserves_existing_behavior(rig: TaskRig, row: bool | None) -> None:
    """With the switch off an open pause is invisible to the real evaluation task."""
    rig.base(qradar_source_pause_enabled=False)
    if row is not None:
        await rig.row("qradar_source_pause_enabled", row)
    bucket = T0 + timedelta(minutes=5)
    async with rig.maker() as session:
        source = await _source(session)
        await open_pause(
            session, [source.id], reason=REASON, starts_at=T0, now=NOW
        )
        await _bucket(session, source, bucket, eps=50.0)
        await session.commit()

    result = await tasks.evaluate_anomalies()

    assert result["sources"] == 1, result
    async with rig.maker() as session:
        watermark = await session.get(AnomalyEvaluationWatermark, source.id)
        assert watermark is not None and watermark.evaluated_through == bucket


async def test_task_off_with_unreadable_switch_is_disabled(rig: TaskRig) -> None:
    """The unreadable-switch path still disables the whole task, as today."""
    rig.base(qradar_source_pause_enabled=False)
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))
    result = await tasks.evaluate_anomalies()
    assert result == {"status": "disabled", "reason": UNREADABLE_REASON}, result
