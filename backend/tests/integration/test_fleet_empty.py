"""FIX-45c (D-62): a bucket in which no monitored source sent events opens one fleet alert.

The collector writes such a bucket as PARTIAL with `fleet_empty` in its provenance; the
engine reports it once per QRadar instance, never as N NO_EVENTS. DB-gated.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select

from app.alerts.fingerprint import fleet_empty_fingerprint
from app.anomaly.engine import AnomalyEngine
from app.collectors.metric_collector import MetricCollector
from app.core.config import Settings
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyType, BucketCompleteness, Criticality
from app.models.log_source import (
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.providers.base import LogSourceMetricSample
from app.providers.mock import MockQRadarProvider
from app.services.inventory_sync import InventorySyncService
from tests.integration.factories import add_metric, make_instance, make_log_source, utc
from tests.integration.test_rule_match_collection import recorded_statements

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

FLEET_PROVENANCE = {
    "zero_filled": True,
    "fleet_empty": True,
    "telemetry": sorted(t.value for t in MockQRadarProvider.log_source_telemetry),
}


def _settings() -> Settings:
    return Settings(encryption_key="x" * 44, anomaly_deviation_threshold=3.5)


async def _baseline(session, ls, when, *, median: float = 15_000.0, reliable: bool = True):
    session.add(
        LogSourceBaseline(
            log_source_id=ls.id, metric_name="event_count", weekday=when.isoweekday(),
            hour=when.hour, median=median, mad=500.0, p05=median * 0.9, p95=median * 1.1,
            sample_count=30, is_reliable=reliable, baseline_version=1,
            observations=[median],
        )
    )
    await session.flush()


async def _fleet(session, names, *, when, **source_kwargs):
    inst = await make_instance(session)
    sources = []
    for index, name in enumerate(names):
        ls = await make_log_source(
            session, inst, qradar_id=2000 + index, name=name,
            criticality=Criticality.CRITICAL, **source_kwargs,
        )
        await _baseline(session, ls, when)
        sources.append(ls)
    return inst, sources


async def _empty(session, ls, at, *, fleet: bool, complete: bool | None = None):
    flagged = fleet if complete is None else not complete
    return await add_metric(
        session, ls, at, average_eps=0.0, event_count=0, stored_event_count=0,
        last_event_at=None, event_delay_seconds=None, collection_source="mock",
        distinct_source_ip_count=0, distinct_qid_count=0, distinct_username_count=0,
        completeness=BucketCompleteness.PARTIAL if flagged else BucketCompleteness.COMPLETE,
        query_provenance=FLEET_PROVENANCE if fleet else {"zero_filled": True},
    )


async def _busy(session, ls, at):
    return await add_metric(session, ls, at, collection_source="mock")


async def _fleet_alerts(session, inst) -> list[Alert]:
    return list(
        (
            await session.scalars(
                select(Alert)
                .where(Alert.fingerprint == fleet_empty_fingerprint(inst.id))
                .order_by(Alert.opened_at)
            )
        ).all()
    )


async def _no_events_alerts(session) -> int:
    rows = await session.scalars(
        select(Alert).where(Alert.source_type == "log_source", Alert.title.like("No events%"))
    )
    return len(list(rows))


async def _judge(session, sources, at, make):
    """Judge one bucket for every source, with a fresh engine (a new worker run)."""
    engine = AnomalyEngine(session, settings=_settings())
    for ls in sources:
        await engine.evaluate_interval(ls, await make(session, ls, at))


async def test_an_empty_fleet_bucket_opens_one_alert_and_no_per_source_no_events(
    db_session,
) -> None:
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1", "fw-2", "fw-3"], when=start)

    await _judge(
        db_session, sources, start, lambda s, ls, at: _empty(s, ls, at, fleet=True)
    )

    alerts = await _fleet_alerts(db_session, inst)
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert.status == AlertStatus.OPEN
    assert alert.occurrence_count == 1
    assert alert.source_type == "qradar_instance" and alert.source_id == inst.id
    assert alert.title == "No log source sent events in 2026-07-15 10:00 UTC"
    assert alert.description == (
        "No log source sent events in 2026-07-15 10:00 UTC; QRadar may be starting or its "
        "pipeline may have stopped."
    )
    assert alert.title_i18n["code"] == "alert.fleet_empty.title"
    assert await _no_events_alerts(db_session) == 0
    assert (
        await db_session.scalar(
            select(func.count()).select_from(LogSourceAnomaly).where(
                LogSourceAnomaly.anomaly_type == AnomalyType.NO_EVENTS
            )
        )
    ) == 0
    # Why: the stored verdict of every source says INSUFFICIENT_DATA with the fleet reason.
    states = list(
        (
            await db_session.scalars(
                select(LogSourceDetectorState).where(
                    LogSourceDetectorState.anomaly_type == AnomalyType.NO_EVENTS
                )
            )
        ).all()
    )
    assert len(states) == 3
    assert {s.state for s in states} == {"INSUFFICIENT_DATA"}


async def test_one_silent_source_among_busy_ones_still_opens_its_own_no_events(
    db_session,
) -> None:
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1", "fw-2", "fw-3"], when=start)
    silent, busy = sources[0], sources[1:]

    for index in range(2):  # grace is one bucket: the second empty bucket opens it
        at = start + timedelta(minutes=5 * index)
        await _judge(db_session, busy, at, _busy)
        await _judge(
            db_session, [silent], at, lambda s, ls, a: _empty(s, ls, a, fleet=False)
        )

    assert await _fleet_alerts(db_session, inst) == []
    alert = await db_session.scalar(
        select(Alert).where(Alert.title == "No events received from fw-1")
    )
    assert alert is not None and alert.status == AlertStatus.OPEN
    assert await _no_events_alerts(db_session) == 1


async def test_the_alert_refreshes_while_it_holds_and_resolves_on_the_first_bucket_with_events(
    db_session,
) -> None:
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1", "fw-2"], when=start)

    for index in range(2):
        await _judge(
            db_session, sources, start + timedelta(minutes=5 * index),
            lambda s, ls, at: _empty(s, ls, at, fleet=True),
        )
    alerts = await _fleet_alerts(db_session, inst)
    assert len(alerts) == 1 and alerts[0].occurrence_count == 2
    assert alerts[0].status == AlertStatus.OPEN

    await _judge(db_session, sources, start + timedelta(minutes=10), _busy)
    await db_session.refresh(alerts[0])
    assert alerts[0].status == AlertStatus.RESOLVED
    assert alerts[0].resolution_reason == "Log sources are sending events again."
    assert len(await _fleet_alerts(db_session, inst)) == 1

    # Per-source NO_EVENTS resumes: one source silent again among a busy one.
    for index in range(3, 5):
        at = start + timedelta(minutes=5 * index)
        await _judge(db_session, [sources[1]], at, _busy)
        await _judge(
            db_session, [sources[0]], at, lambda s, ls, a: _empty(s, ls, a, fleet=False)
        )
    assert await _no_events_alerts(db_session) == 1
    assert len(await _fleet_alerts(db_session, inst)) == 1


async def test_a_lagging_source_judging_an_older_bucket_does_not_refresh_the_alert(
    db_session,
) -> None:
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1", "fw-2"], when=start)
    for index in range(2):
        await _judge(
            db_session, sources[:1], start + timedelta(minutes=5 * index),
            lambda s, ls, at: _empty(s, ls, at, fleet=True),
        )
    await _judge(
        db_session, sources[1:], start, lambda s, ls, at: _empty(s, ls, at, fleet=True)
    )
    alerts = await _fleet_alerts(db_session, inst)
    assert len(alerts) == 1 and alerts[0].occurrence_count == 2


async def test_a_fleet_empty_bucket_does_not_let_a_sibling_detector_fire_per_source(
    db_session,
) -> None:
    """Cardinality reads the zero row too; NO_EVENTS no longer demotes it, the flag does."""
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1", "fw-2"], when=start)
    for ls in sources:
        db_session.add(
            LogSourceBaseline(
                log_source_id=ls.id, metric_name="distinct_source_ip_count",
                weekday=start.isoweekday(), hour=start.hour, median=60.0, mad=2.0,
                p05=50.0, p95=70.0, sample_count=30, is_reliable=True, baseline_version=1,
                observations=[60.0],
            )
        )
    await db_session.flush()

    await _judge(
        db_session, sources, start, lambda s, ls, at: _empty(s, ls, at, fleet=True)
    )

    assert len(await _fleet_alerts(db_session, inst)) == 1
    others = await db_session.scalar(
        select(func.count()).select_from(Alert).where(Alert.source_type == "log_source")
    )
    assert others == 0


async def test_sources_that_do_not_expect_traffic_never_open_the_fleet_alert(
    db_session,
) -> None:
    start = utc(h=10)
    inst = await make_instance(db_session)
    off_schedule = await make_log_source(
        db_session, inst, qradar_id=1, name="office", criticality=Criticality.CRITICAL,
        business_hours_only=True, business_hours_start=1, business_hours_end=5,
        business_days=[1, 2, 3, 4, 5, 6, 7], timezone_name="UTC",
    )
    zero_median = await make_log_source(
        db_session, inst, qradar_id=2, name="idle", criticality=Criticality.CRITICAL
    )
    no_baseline = await make_log_source(
        db_session, inst, qradar_id=3, name="new", criticality=Criticality.CRITICAL
    )
    maintained = await make_log_source(
        db_session, inst, qradar_id=4, name="patching", criticality=Criticality.CRITICAL,
        maintenance_mode=True, maintenance_until=datetime(2027, 1, 1, tzinfo=UTC),
    )
    await _baseline(db_session, off_schedule, start)
    await _baseline(db_session, zero_median, start, median=0.0)
    await _baseline(db_session, maintained, start)
    sources = [off_schedule, zero_median, no_baseline, maintained]

    await _judge(
        db_session, sources, start, lambda s, ls, at: _empty(s, ls, at, fleet=True)
    )

    assert await _fleet_alerts(db_session, inst) == []
    assert await _no_events_alerts(db_session) == 0

    # One source that does expect traffic is enough.
    expecting = await make_log_source(
        db_session, inst, qradar_id=5, name="core", criticality=Criticality.CRITICAL
    )
    await _baseline(db_session, expecting, start)
    await _judge(
        db_session, [*sources, expecting], start + timedelta(minutes=5),
        lambda s, ls, at: _empty(s, ls, at, fleet=True),
    )
    assert len(await _fleet_alerts(db_session, inst)) == 1


async def test_a_flag_on_a_complete_row_is_not_a_fleet_fact(db_session) -> None:
    """Only the collector's PARTIAL row counts; a stray provenance key never hides silence."""
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1"], when=start)
    for index in range(2):
        at = start + timedelta(minutes=5 * index)
        await _judge(
            db_session, sources, at,
            lambda s, ls, a: add_metric(
                s, ls, a, average_eps=0.0, event_count=0, stored_event_count=0,
                last_event_at=None, event_delay_seconds=None, collection_source="mock",
                query_provenance=FLEET_PROVENANCE,
            ),
        )
    assert await _fleet_alerts(db_session, inst) == []
    assert await _no_events_alerts(db_session) == 1


async def test_the_fleet_alert_costs_the_same_statements_for_two_sources_and_for_eight(
    db_session,
) -> None:
    start = utc(h=10)

    def alert_statements(statements: list[str]) -> int:
        return sum(
            1 for s in statements if re.search(r"(FROM|INTO|UPDATE) alert\b(?!_)", s)
        )

    counts = []
    for size in (2, 8):
        inst, sources = await _fleet(
            db_session, [f"s{size}-{i}" for i in range(size)], when=start
        )
        # Pre-build the rows so only the evaluation is measured.
        rows = [await _empty(db_session, ls, start, fleet=True) for ls in sources]
        engine = AnomalyEngine(db_session, settings=_settings())
        with recorded_statements(db_session) as statements:
            for ls, row in zip(sources, rows, strict=True):
                await engine.evaluate_interval(ls, row)
        counts.append(alert_statements(statements))
        assert len(await _fleet_alerts(db_session, inst)) == 1
    assert counts[0] == counts[1] > 0


# ------------------------------------------------------------------- the collector


async def _seeded(session):
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(session, provider)
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)
    return instance


async def _collect(session, instance, qradar_ids, now):
    """Collect one window in which Ariel reports an event count for `qradar_ids` only."""
    provider = MockQRadarProvider(seed=1337)

    async def _metrics(window_start, window_end):
        return [
            LogSourceMetricSample(
                qradar_id=qid, bucket_start=window_start,
                bucket_seconds=int((window_end - window_start).total_seconds()),
                event_count=5,
            )
            for qid in qradar_ids
        ]

    provider.get_log_source_metrics = _metrics  # type: ignore[method-assign]
    await MetricCollector(
        session, provider, settings=_settings(), clock=lambda: now
    ).collect(instance)


async def test_an_empty_aggregate_is_written_partial_with_the_reason(db_session) -> None:
    instance = await _seeded(db_session)
    await _collect(db_session, instance, [], datetime(2026, 7, 15, 10, 3, tzinfo=UTC))

    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    assert len(rows) > 1
    for row in rows:
        assert row.completeness == BucketCompleteness.PARTIAL
        assert row.event_count == 0
        assert row.query_provenance["fleet_empty"] is True
        assert row.query_provenance["zero_filled"] is True


async def test_one_reporting_source_keeps_the_others_complete_zeros(db_session) -> None:
    instance = await _seeded(db_session)
    first = (await db_session.scalars(select(LogSource).order_by(LogSource.qradar_id))).first()
    assert first is not None
    now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    await _collect(db_session, instance, [first.qradar_id], now)

    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    zeros = [r for r in rows if r.log_source_id != first.id]
    assert zeros
    for row in zeros:
        assert row.completeness == BucketCompleteness.COMPLETE
        assert "fleet_empty" not in row.query_provenance


async def test_a_single_monitored_source_is_not_a_fleet(db_session) -> None:
    instance = await _seeded(db_session)
    sources = list((await db_session.scalars(select(LogSource))).all())
    for src in sources[1:]:
        src.monitoring_enabled = False
    await db_session.flush()

    await _collect(db_session, instance, [], datetime(2026, 7, 15, 10, 3, tzinfo=UTC))

    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    assert len(rows) == 1
    assert rows[0].completeness == BucketCompleteness.COMPLETE
    assert "fleet_empty" not in rows[0].query_provenance


async def test_the_collector_statement_count_does_not_depend_on_the_flag(db_session) -> None:
    instance = await _seeded(db_session)
    first = (await db_session.scalars(select(LogSource).order_by(LogSource.qradar_id))).first()
    assert first is not None
    # The first run creates the watermark; measure two later runs, flagged and not.
    first_now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC)
    await _collect(db_session, instance, [first.qradar_id], first_now)
    counts = []
    for index, samples in enumerate(([], [first.qradar_id]), start=1):
        now = datetime(2026, 7, 15, 10, 3, tzinfo=UTC) + timedelta(minutes=5 * index)
        with recorded_statements(db_session) as statements:
            await _collect(db_session, instance, samples, now)
        counts.append(len(statements))
    assert counts[0] == counts[1]


async def _write(session, instance, qradar_ids, start):
    """Write one bucket directly, as a re-collection of the same window would."""
    collector = MetricCollector(
        session, MockQRadarProvider(seed=1337), settings=_settings(),
        clock=lambda: start + timedelta(minutes=8),
    )
    end = start + timedelta(minutes=5)
    samples = [
        LogSourceMetricSample(
            qradar_id=qid, bucket_start=start, bucket_seconds=300, event_count=5
        )
        for qid in qradar_ids
    ]
    await collector._write_bucket(
        instance, start, end, samples, watermark_at=None, duration_ms=1,
        collected_at=end + timedelta(minutes=1),
    )


async def test_recollecting_the_bucket_with_events_overwrites_the_partial_rows(
    db_session,
) -> None:
    instance = await _seeded(db_session)
    sources = list((await db_session.scalars(select(LogSource))).all())
    start = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)

    await _write(db_session, instance, [], start)
    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    assert len(rows) == len(sources)
    assert {r.completeness for r in rows} == {BucketCompleteness.PARTIAL}

    await _write(db_session, instance, [s.qradar_id for s in sources], start)
    rows = list((await db_session.scalars(select(LogSourceMetric))).all())
    assert len(rows) == len(sources)
    for row in rows:
        await db_session.refresh(row)
        assert row.completeness == BucketCompleteness.COMPLETE
        assert row.event_count == 5
        assert "fleet_empty" not in row.query_provenance
        assert "zero_filled" not in row.query_provenance


async def test_an_aggregate_naming_only_unmonitored_sources_is_still_fleet_empty(
    db_session,
) -> None:
    instance = await _seeded(db_session)
    sources = list((await db_session.scalars(select(LogSource))).all())
    quiet = sources[0]
    quiet.monitoring_enabled = False
    await db_session.flush()
    start = datetime(2026, 7, 15, 10, 0, tzinfo=UTC)

    await _write(db_session, instance, [quiet.qradar_id], start)

    rows = {
        r.log_source_id: r for r in (await db_session.scalars(select(LogSourceMetric))).all()
    }
    assert rows[quiet.id].completeness == BucketCompleteness.COMPLETE
    assert rows[quiet.id].event_count == 5
    others = [r for key, r in rows.items() if key != quiet.id]
    assert others
    for row in others:
        assert row.completeness == BucketCompleteness.PARTIAL
        assert row.query_provenance["fleet_empty"] is True


async def test_engine_statements_grow_by_a_constant_per_source_not_with_the_fleet(
    db_session,
) -> None:
    """Total cost is linear with one slope: nothing in it is per-pair or per-fleet-size."""
    start = utc(h=10)
    totals = {}
    for size in (2, 4, 8):
        inst, sources = await _fleet(
            db_session, [f"t{size}-{i}" for i in range(size)], when=start
        )
        rows = [await _empty(db_session, ls, start, fleet=True) for ls in sources]
        engine = AnomalyEngine(db_session, settings=_settings())
        with recorded_statements(db_session) as statements:
            for ls, row in zip(sources, rows, strict=True):
                await engine.evaluate_interval(ls, row)
        totals[size] = len(statements)
        assert len(await _fleet_alerts(db_session, inst)) == 1
    assert (totals[4] - totals[2]) * 2 == totals[8] - totals[4]


async def test_two_instances_get_two_fleet_alerts(db_session) -> None:
    start = utc(h=10)
    first, first_sources = await _fleet(db_session, ["a-1", "a-2"], when=start)
    second, second_sources = await _fleet(db_session, ["b-1", "b-2"], when=start)
    assert fleet_empty_fingerprint(first.id) != fleet_empty_fingerprint(second.id)

    await _judge(
        db_session, [*first_sources, *second_sources], start,
        lambda s, ls, at: _empty(s, ls, at, fleet=True),
    )

    for inst in (first, second):
        alerts = await _fleet_alerts(db_session, inst)
        assert len(alerts) == 1 and alerts[0].source_id == inst.id


async def test_only_a_complete_newer_bucket_resolves_the_alert(db_session) -> None:
    start = utc(h=10)
    inst, sources = await _fleet(db_session, ["fw-1", "fw-2"], when=start)
    await _judge(
        db_session, sources, start + timedelta(minutes=5),
        lambda s, ls, at: _empty(s, ls, at, fleet=True),
    )
    (alert,) = await _fleet_alerts(db_session, inst)

    # A PARTIAL bucket that is not the collector's fleet fact proves nothing.
    await _judge(
        db_session, sources, start + timedelta(minutes=10),
        lambda s, ls, at: _empty(s, ls, at, fleet=False, complete=False),
    )
    await db_session.refresh(alert)
    assert alert.status == AlertStatus.OPEN

    # A source still judging an older bucket cannot resolve what a newer bucket opened.
    await _judge(db_session, sources, start, _busy)
    await db_session.refresh(alert)
    assert alert.status == AlertStatus.OPEN

    await _judge(db_session, sources, start + timedelta(minutes=15), _busy)
    await db_session.refresh(alert)
    assert alert.status == AlertStatus.RESOLVED
