"""D-52 activation through actual worker tasks and audited feature changes."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import delete, inspect, select, text, update
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.anomaly import baseline
from app.collectors import log_source_collector, metric_collector
from app.collectors.log_source_collector import (
    COLLECTOR_NAME as INVENTORY_COLLECTOR,
)
from app.collectors.log_source_collector import INVENTORY_RUN_KEY, LogSourceCollector
from app.collectors.metric_collector import MetricCollector
from app.core.config import Settings
from app.history import phase_a
from app.history.progress_codec import decode_progress
from app.models.alert import Alert
from app.models.enums import AlertStatus, AnomalyState, AnomalyType, Criticality
from app.models.feature_switch import FeatureSwitch
from app.models.identity import AuditLog
from app.models.log_source import (
    AnomalyStateTransition,
    LogSource,
    LogSourceAnomaly,
    LogSourceBaseline,
    LogSourceDetectorState,
    LogSourceMetric,
)
from app.models.log_source_pause import LogSourcePause
from app.models.monitoring import AnomalyEvaluationWatermark, CollectionWatermark
from app.providers import factory
from app.providers.base import ProviderUnavailableError
from app.providers.dto import LogSourceWalk
from app.providers.mock import MockQRadarProvider
from app.services.feature_management import change
from app.services.feature_switches import UNREADABLE_REASON
from app.services.inventory_sync import InventorySyncService
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source
from tests.integration.history_phase_a_support import (
    ANCHOR,
    FIRST_TICK,
    STEP,
    ScriptedMockProvider,
    scope_of,
)
from tests.integration.test_history_rebuild_source_pauses import _pending, _record

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 21, 12, tzinfo=UTC)
KEY = "qradar_source_pause_enabled"


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):  # type: ignore[no-untyped-def]
        return NOW


async def test_unreadable_switch_table_refuses_evaluation_and_training(
    db_schema: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreadable policy is not an authorized rollback to legacy training (P12b §4)."""
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    base = Settings(
        _env_file=None, feature_switches_db=True, qradar_source_pause_enabled=False,
        baseline_min_samples=3, baseline_lookback_days=120,
    )
    # Patch only database ownership, base configuration and clocks, never policy consumers.
    # MonkeyPatch restores these attributes; no env or settings-cache mutation is needed.
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr(tasks, "datetime", FrozenDatetime)
    monkeypatch.setattr(baseline, "get_settings", lambda: base)
    monkeypatch.setattr(baseline, "datetime", FrozenDatetime)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            enabled = await make_log_source(
                session, instance, qradar_id=1, enabled=True, monitoring_enabled=True,
            )
            disabled = await make_log_source(
                session, instance, qradar_id=2, enabled=False, monitoring_enabled=True,
            )
            disabled_id = disabled.id
            session.add_all([
                LogSourceMetric(
                    log_source_id=source.id,
                    bucket_start=NOW - timedelta(weeks=week),
                    average_eps=50.0, event_count=15000, bucket_seconds=300,
                )
                for source in (enabled, disabled) for week in range(1, 7)
            ])
            await change(session, base, {KEY: True}, actor="activation-test")
            await session.commit()
            audits = list(await session.scalars(select(AuditLog).where(
                AuditLog.object_id == KEY,
            )))
            assert len(audits) == 1
            assert audits[0].action == "feature_switch.update"
            assert audits[0].detail["new"] is True

        # Control: the real on-policy daily entry trains only the stored-enabled source.
        assert await tasks.rebuild_baselines() == {"sources": 1, "cells": 2}
        async with maker() as session:
            assert list(await session.scalars(select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == disabled_id,
            ))) == []

        # A real SQL read failure, confined to the schema the fixture owns. A new session
        # per task also proves this is not a leftover failed transaction in the test.
        async with engine.begin() as connection:
            await connection.execute(text("DROP TABLE feature_switch"))
        evaluation = await tasks.evaluate_anomalies()
        training = await tasks.rebuild_baselines()
        async with maker() as session:
            new_cells = list(await session.scalars(select(LogSourceBaseline).where(
                LogSourceBaseline.log_source_id == disabled_id,
            ).order_by(LogSourceBaseline.metric_name)))
        refusal = {"status": "disabled", "reason": UNREADABLE_REASON}
        assert {
            "evaluation": evaluation,
            "training": training,
            "newly_trained_disabled_cells": [
                (cell.metric_name, cell.sample_count, cell.is_reliable) for cell in new_cells
            ],
        } == {
            "evaluation": refusal,
            "training": refusal,
            "newly_trained_disabled_cells": [],
        }
    finally:
        await engine.dispose()


async def test_all_policy_consumers_same_effective_snapshot(db_session, monkeypatch):
    """A collection run's REST mapper, live filter and pending builder share its policy."""
    console, samples, base = await _pending(db_session, pause=False, count=3)
    console.sources[2].enabled = False
    console.instance.provider_kind = "rest"
    console.instance.console_host = "https://qradar.invalid"
    console.instance.sec_token = "test-token"
    await db_session.execute(delete(LogSourceBaseline))
    base = base.model_copy(update={
        "qradar_source_pause_enabled": False,
        "history_backfill_baseline_sources_per_tick": 3,
    })
    await change(db_session, base, {KEY: True}, actor="activation-test")
    await db_session.commit()
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr(factory, "get_settings", lambda: base)
    at = FIRST_TICK + 2 * STEP
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: at)
    providers, collectors, builders, reports = [], [], [], []
    flipped = False

    async def metrics(*args, **kwargs):
        nonlocal flipped
        if not flipped:
            # A separate committed transaction changes the next run's policy during
            # this run's upstream search. The current run must retain its on snapshot.
            async with maker() as session:
                await session.execute(update(FeatureSwitch).where(
                    FeatureSwitch.key == KEY,
                ).values(enabled=False))
                await session.commit()
            flipped = True
        return await samples.get_log_source_metrics(*args, **kwargs)

    def build(instance, settings=None, **kwargs):
        # Keep the real factory and REST mapper; replace only the upstream search.
        provider = factory.build_provider_for_instance(instance, settings, **kwargs)
        providers.append((settings, provider._strict_source_enabled))
        monkeypatch.setattr(provider, "get_log_source_metrics", metrics)
        return provider

    class Recorder(MetricCollector):
        def __init__(self, session, provider, *, settings):
            collectors.append(settings)
            super().__init__(session, provider, settings=settings, clock=lambda: at)

        async def collect(self, instance):
            report = await super().collect(instance)
            reports.append(report)
            return report

    real_builder = metric_collector.BaselineBuilder

    def builder(session, **kwargs):
        builders.append(kwargs)
        return real_builder(session, **kwargs)

    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    monkeypatch.setattr(tasks, "MetricCollector", Recorder)
    monkeypatch.setattr(metric_collector, "BaselineBuilder", builder)
    with _record(db_session) as first_sql:
        first = await tasks.collect_metrics()
    async with maker() as session:
        first_rows = list(await session.scalars(select(LogSourceMetric).where(
            LogSourceMetric.bucket_start == ANCHOR + 2 * STEP,
        )))
        first_cells = list(await session.scalars(select(LogSourceBaseline)))
        scope = await scope_of(session, console.instance)
        assert decode_progress(scope.progress).adapter[phase_a.REBUILD_PENDING_KEY] == []
        assert (await session.get(FeatureSwitch, KEY)).enabled is False
    # A new cadence slot makes this a real next worker run, not a not_due shortcut.
    at += STEP
    with _record(db_session) as next_sql:
        second = await tasks.collect_metrics()

    async with maker() as session:
        next_rows = list(await session.scalars(select(LogSourceMetric).where(
            LogSourceMetric.bucket_start == ANCHOR + 3 * STEP,
        )))
    assert {row.log_source_id for row in next_rows} == {
        source.id for source in console.sources.values()
    }
    assert [run["results"][0]["status"] for run in (first, second)] == ["ok", "ok"]
    assert [sum("FROM feature_switch" in sql for sql in run) for run in (
        first_sql, next_sql,
    )] == [1, 1]
    assert flipped
    assert {row.log_source_id for row in first_rows} == {
        console.sources[qid].id for qid in (1, 3)
    }
    assert {cell.log_source_id for cell in first_cells} == {console.sources[3].id}
    assert [report.history.rebuilt for report in reports] == [1, 0]
    assert len(builders) == 1
    assert builders[0]["settings"] is collectors[0]
    assert builders[0]["source_pause_policy"] is True
    assert [settings.qradar_source_pause_enabled for settings in collectors] == [True, False]
    assert {
        "provider_strict_policy": [strict for _, strict in providers],
        "provider_uses_collector_snapshot": [
            settings is collector for (settings, _), collector in zip(
                providers, collectors, strict=True,
            )
        ],
    } == {
        "provider_strict_policy": [True, False],
        "provider_uses_collector_snapshot": [True, True],
    }


class ActivationRig:
    """Real task orchestration with a fixed clock and deterministic upstream reads."""

    def __init__(self, session, patch):
        self.session = session
        self.maker = async_sessionmaker(session.bind, expire_on_commit=False)
        self.at = NOW + timedelta(minutes=3)
        self.base = Settings(
            _env_file=None, feature_switches_db=True, qradar_source_pause_enabled=False,
            inventory_new_source_alerts_enabled=False, jdbc_collection_enabled=False,
            baseline_min_samples=3, baseline_lookback_days=120,
            collection_max_backfill_intervals=0, anomaly_silence_grace_buckets=0,
        )
        self.raw = {1: False, 2: True, 3: "false"}
        self.samples = ScriptedMockProvider([1, 2, 3], silent=[1])
        self.inventory_status = "COMPLETE"
        self.on_walk = None
        self.providers = []
        self.mapped_flags = []
        self.sync_settings = []
        self.service_settings = []
        self.metric_settings = []
        self.reports = []
        rig = self

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return rig.at

        class Inventory(LogSourceCollector):
            def __init__(self, session, provider, *, settings):
                rig.sync_settings.append(settings)
                super().__init__(session, provider, settings=settings, clock=lambda: rig.at)

        class Sync(InventorySyncService):
            def __init__(self, *args, **kwargs):
                rig.service_settings.append(kwargs["settings"])
                super().__init__(*args, **kwargs)

        class Metrics(MetricCollector):
            def __init__(self, session, provider, *, settings):
                rig.metric_settings.append(settings)
                super().__init__(session, provider, settings=settings, clock=lambda: rig.at)

            async def collect(self, instance):
                report = await super().collect(instance)
                rig.reports.append(report)
                return report

        def build(instance, settings=None, **kwargs):
            provider = factory.build_provider_for_instance(instance, settings, **kwargs)
            rig.providers.append((settings, provider))

            async def walk(**kwargs):
                if rig.on_walk is not None:
                    await rig.on_walk()
                if rig.inventory_status == "FAILED":
                    raise ProviderUnavailableError("inventory unavailable")
                dtos = [provider._to_log_source({
                    "id": qid, "name": f"source-{qid}", "enabled": flag,
                }) for qid, flag in rig.raw.items()]
                rig.mapped_flags.append({dto.qradar_id: dto.enabled for dto in dtos})
                return LogSourceWalk(
                    sources=dtos, complete=rig.inventory_status == "COMPLETE", pages_done=1,
                    pages_failed=int(rig.inventory_status != "COMPLETE"),
                    failure_kind=None if rig.inventory_status == "COMPLETE" else "timeout",
                )

            async def empty():
                return []

            patch.setattr(provider, "get_instance_info", MockQRadarProvider().get_instance_info)
            patch.setattr(provider, "walk_log_sources", walk)
            patch.setattr(provider, "list_log_source_types", empty)
            patch.setattr(provider, "list_log_source_groups", empty)
            patch.setattr(provider, "get_log_source_metrics", rig.samples.get_log_source_metrics)
            patch.setattr(
                provider, "get_log_source_metrics_bucketed",
                rig.samples.get_log_source_metrics_bucketed,
            )
            return provider

        patch.setattr(tasks, "get_sessionmaker", lambda: self.maker)
        patch.setattr(tasks, "get_settings", lambda: self.base)
        patch.setattr(factory, "get_settings", lambda: self.base)
        patch.setattr(tasks, "datetime", Clock)
        patch.setattr(baseline, "datetime", Clock)
        patch.setattr(baseline, "get_settings", lambda: self.base)
        patch.setattr(tasks, "LogSourceCollector", Inventory)
        patch.setattr(log_source_collector, "InventorySyncService", Sync)
        patch.setattr(tasks, "MetricCollector", Metrics)
        patch.setattr(tasks, "build_provider_for_instance", build)
        patch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: rig.at)

    async def seed(self, flags=(True, True, False), *, history=True):
        self.instance = await make_instance(self.session)
        self.instance.provider_kind = "rest"
        self.instance.console_host = "https://qradar.invalid"
        self.instance.sec_token = "test-token"
        self.instance.created_at = NOW - timedelta(days=90)
        self.sources = [await make_log_source(
            self.session, self.instance, qradar_id=qid, enabled=flag,
            monitoring_enabled=True, criticality=Criticality.CRITICAL,
            first_seen_at=NOW - timedelta(days=90),
        ) for qid, flag in enumerate(flags, 1)]
        if history:
            # Clean observations in both seasonal cells crossed by the task sequence.
            self.session.add_all([LogSourceMetric(
                log_source_id=source.id,
                bucket_start=NOW - timedelta(weeks=week, minutes=minutes),
                bucket_seconds=300, event_count=15000, average_eps=50.0,
                collection_source="ariel_history",
            ) for source in self.sources for week in range(1, 7) for minutes in (0, 5)])
        await self.session.commit()

    async def switch(self, enabled):
        async with self.maker() as session:
            await change(session, self.base, {KEY: enabled}, actor="activation-test")
            await session.commit()

    async def run(self, task):
        with _record(self.session) as sql:
            result = await task()
        assert sum("FROM feature_switch" in s for s in sql) == 1
        return result

    async def rows(self, model):
        async with self.maker() as session:
            return list(await session.scalars(select(model)))


@pytest.fixture
async def activation(db_session, monkeypatch):
    return ActivationRig(db_session, monkeypatch)


def _instance_result(result):
    assert result.get("instances") == 1, result
    [run] = result["results"]
    return run


async def test_rollback_reactivation_judges_next_whole_bucket(activation):
    rig = activation
    await rig.seed((True, True, True))
    rig.raw = {1: True, 2: True, 3: True}
    await rig.switch(True)
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    assert await rig.run(tasks.rebuild_baselines) == {"sources": 3, "cells": 12}

    rig.at += STEP
    rig.raw[1] = False
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    [opened] = await rig.rows(LogSourcePause)
    assert opened.log_source_id == rig.sources[0].id and opened.ends_at is None
    judged = await rig.run(tasks.evaluate_anomalies)
    assert judged["sources"] == 2 and judged["failed"] == 0

    await rig.switch(False)
    rig.at += STEP
    rig.raw[1] = True
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    assert all(s.enabled is True for s in await rig.rows(LogSource))
    [retained] = await rig.rows(LogSourcePause)
    assert (retained.starts_at, retained.ends_at) == (opened.starts_at, None)

    await rig.switch(True)
    rig.at += STEP  # 12:18 UTC: close at 12:20, not within the observed bucket.
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    [closed] = await rig.rows(LogSourcePause)
    resume = NOW + 4 * STEP
    assert closed.starts_at == opened.starts_at and closed.ends_at == resume
    assert all(s.enabled is True for s in await rig.rows(LogSource))
    assert (await rig.run(tasks.rebuild_baselines))["sources"] == 3

    # Evaluate each retained complete measurement in turn: cold-start evaluation
    # uses only the latest bucket, so a single call would not exercise both sides.
    source_id = rig.sources[0].id
    async with rig.maker() as session:
        session.add(LogSourceMetric(
            log_source_id=source_id, bucket_start=resume - STEP, bucket_seconds=300,
            event_count=0, average_eps=0.0, collection_source="ariel_aggregate",
        ))
        await session.commit()
    rig.at = resume
    judged = await rig.run(tasks.evaluate_anomalies)
    assert judged["sources"] == 3 and judged["failed"] == 0
    assert not [s for s in await rig.rows(LogSourceDetectorState) if s.log_source_id == source_id]
    assert not [a for a in await rig.rows(LogSourceAnomaly) if a.log_source_id == source_id]
    [mark] = [w for w in await rig.rows(AnomalyEvaluationWatermark)
              if w.log_source_id == source_id]
    assert mark.evaluated_through == resume - STEP

    async with rig.maker() as session:
        session.add(LogSourceMetric(
            log_source_id=source_id, bucket_start=resume, bucket_seconds=300,
            event_count=0, average_eps=0.0, collection_source="ariel_aggregate",
        ))
        await session.commit()
    rig.at = resume + STEP
    judged = await rig.run(tasks.evaluate_anomalies)
    assert judged["sources"] == 3 and judged["failed"] == 0
    states = [s for s in await rig.rows(LogSourceDetectorState) if s.log_source_id == source_id]
    assert states and all(s.last_interval_start == resume for s in states)
    [mark] = [w for w in await rig.rows(AnomalyEvaluationWatermark)
              if w.log_source_id == source_id]
    assert mark.evaluated_through == resume
    incidents = {a.id for a in await rig.rows(LogSourceAnomaly) if a.log_source_id == source_id}
    transitions = [t for t in await rig.rows(AnomalyStateTransition)
                   if t.anomaly_id in incidents and t.bucket_start is not None]
    assert transitions and all(t.bucket_start == resume for t in transitions)


async def test_off_to_on_actual_tasks_and_sync(activation):
    rig = activation
    await rig.seed()
    # No row and env off: real REST coercion treats the string "false" as true.
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    sources = await rig.rows(LogSource)
    assert {s.qradar_id: s.enabled for s in sources} == {1: False, 2: True, 3: True}
    assert rig.providers[-1][1]._strict_source_enabled is False
    assert rig.mapped_flags[-1][3] is True
    assert await rig.rows(LogSourcePause) == []
    assert await rig.run(tasks.rebuild_baselines) == {"sources": 3, "cells": 12}
    first = _instance_result(await rig.run(tasks.collect_metrics))
    assert first["samples"] == 3 and first["buckets_collected"] == 1
    legacy = await rig.run(tasks.evaluate_anomalies)
    assert legacy["sources"] == 3 and legacy["opened"] >= 1 and legacy["failed"] == 0
    incidents = await rig.rows(LogSourceAnomaly)
    [incident] = [i for i in incidents if i.log_source_id == rig.sources[0].id
                  and i.anomaly_type == AnomalyType.NO_EVENTS]
    assert incident.state == AnomalyState.OPEN
    assert await rig.rows(LogSourcePause) == []
    previous = rig.at
    off_watermark = next(w for w in await rig.rows(AnomalyEvaluationWatermark)
                         if w.log_source_id == rig.sources[0].id).evaluated_through

    await rig.switch(True)
    rig.at += STEP
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    [span] = await rig.rows(LogSourcePause)
    assert (span.log_source_id, span.starts_at, span.ends_at) == (
        rig.sources[0].id, previous, None,
    )
    assert rig.providers[-1][1]._strict_source_enabled is True
    assert rig.mapped_flags[-1][3] is None
    closed = next(i for i in await rig.rows(LogSourceAnomaly) if i.id == incident.id)
    assert closed.state == AnomalyState.RESOLVED and closed.resolved_at == rig.at
    [closure] = [t for t in await rig.rows(AnomalyStateTransition)
                 if t.anomaly_id == incident.id and t.actor == "system:inventory-sync"]
    assert closure.reason == "Closed without a verdict: source disabled in QRadar."
    assert closure.bucket_start is closure.observed_value is closure.expected_value is None
    alerts = [a for a in await rig.rows(Alert) if a.source_id == rig.sources[0].id]
    assert alerts and all(a.status == AlertStatus.RESOLVED for a in alerts)
    assert all(a.resolution_reason_code == "alert.source_disabled_without_verdict" for a in alerts)

    assert _instance_result(await rig.run(tasks.collect_metrics))["samples"] == 2
    evaluated = await rig.run(tasks.evaluate_anomalies)
    assert evaluated["sources"] == 2 and evaluated["failed"] == 0
    assert next(w for w in await rig.rows(AnomalyEvaluationWatermark)
                if w.log_source_id == rig.sources[0].id).evaluated_through == off_watermark
    # A retained/racing measurement is allowed in storage; read-time training excludes it.
    async with rig.maker() as session:
        session.add(LogSourceMetric(
            log_source_id=rig.sources[0].id, bucket_start=NOW + 2 * STEP,
            bucket_seconds=300, event_count=299700, average_eps=999.0,
            collection_source="ariel_aggregate",
        ))
        await session.commit()
    assert (await rig.run(tasks.rebuild_baselines))["sources"] == 2
    paused_cells = [c for c in await rig.rows(LogSourceBaseline)
                    if c.log_source_id == rig.sources[0].id]
    assert paused_cells and all(not c.is_reliable for c in paused_cells)

    # An audited reset returns to env-off; neither reset nor off-sync closes old spans.
    await rig.switch(None)
    rig.at += STEP
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    assert _instance_result(await rig.run(tasks.collect_metrics))["samples"] == 3
    assert (await rig.run(tasks.evaluate_anomalies))["sources"] == 3
    [retained] = await rig.rows(LogSourcePause)
    assert (retained.log_source_id, retained.starts_at, retained.ends_at) == (
        span.log_source_id, previous, None,
    )
    async with rig.maker() as session:
        assert await session.get(FeatureSwitch, KEY) is None
    audits = [a for a in await rig.rows(AuditLog) if a.object_id == KEY]
    assert [a.action for a in sorted(audits, key=lambda a: a.created_at)] == [
        "feature_switch.update", "feature_switch.reset",
    ]
    assert [a.detail["new"] for a in sorted(audits, key=lambda a: a.created_at)] == [True, False]

    rig.raw[1] = True
    # Reactivation reconciles explicit true and rounds the close to the next boundary.
    await rig.switch(True)
    rig.at += STEP
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    [resumed] = await rig.rows(LogSourcePause)
    assert resumed.log_source_id == span.log_source_id and resumed.ends_at == NOW + 4 * STEP
    assert (await rig.run(tasks.rebuild_baselines))["sources"] == 3
    cells = [c for c in await rig.rows(LogSourceBaseline)
             if c.log_source_id == rig.sources[0].id and c.hour == NOW.hour]
    assert cells and all(c.exclusion_counts["qradar_paused"] >= 1 for c in cells)
    assert all(999.0 not in c.observations and 299700.0 not in c.observations for c in cells)
    assert all(c.is_reliable and c.sample_count == 6 for c in cells)


async def test_fresh_install_relearn_no_cutover_state(activation):
    rig = activation
    async with rig.session.bind.connect() as conn:
        tables, columns = await conn.run_sync(lambda sync: (
            inspect(sync).get_table_names(),
            {table: [c["name"] for c in inspect(sync).get_columns(table)]
             for table in inspect(sync).get_table_names()},
        ))
    def names(name):
        return any(part in name for part in ("cutover", "policy_version", "training_cutoff"))

    assert not [table for table in tables if names(table)]
    # These are existing detector-evidence versions, not a D-52 training cutover.
    evidence_versions = {
        ("log_source_anomaly", "policy_version"), ("waf_anomaly", "policy_version"),
        ("ips_anomaly", "policy_version"), ("pw_anomaly", "policy_version"),
        ("identity_anomaly", "policy_version"), ("rule_anomaly", "policy_version"),
    }
    assert {(table, name) for table, cols in columns.items() for name in cols if names(name)} == (
        evidence_versions
    )
    assert {name for name in Settings.model_fields if names(name)} == {"anomaly_policy_version"}
    await rig.seed((True, True, True))
    rig.raw = {1: True, 2: True, 3: True}
    assert await rig.rows(LogSourceBaseline) == []
    await rig.switch(True)
    assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
    assert await rig.rows(LogSourcePause) == []
    assert await rig.run(tasks.rebuild_baselines) == {"sources": 3, "cells": 12}
    cells = await rig.rows(LogSourceBaseline)
    assert len(cells) == 12
    assert all(c.is_reliable and c.sample_count == 6 for c in cells)
    assert all(c.observations == [50.0 if c.metric_name == "average_eps" else 15000.0] * 6
               for c in cells)
    assert all(c.exclusion_counts.get("qradar_paused", 0) == 0 for c in cells)


@pytest.mark.parametrize("status", ["FAILED", "PARTIAL"])
async def test_stale_inventory_days_never_global_stop(activation, status):
    rig = activation
    await rig.seed((True, False, True))
    await rig.switch(True)
    old = NOW - timedelta(days=30)
    async with rig.maker() as session:
        session.add(CollectionWatermark(
            instance_id=rig.instance.id, collector=INVENTORY_COLLECTOR,
            watermark_at=old, last_run_at=old,
        ))
        # Existing open history must remain byte-for-byte dated through a stale pass.
        session.add(LogSourcePause(
            log_source_id=rig.sources[1].id, reason="QRADAR_DISABLED", starts_at=old,
        ))
        await session.commit()
    before = [(p.log_source_id, p.starts_at, p.ends_at) for p in await rig.rows(LogSourcePause)]
    rig.inventory_status = status
    rig.raw = {1: None}  # PARTIAL sees an unknown; disabled source remains unseen.
    outcome = _instance_result(await rig.run(tasks.sync_log_sources))
    assert outcome["status"] == status.lower()
    inventory = next(w for w in await rig.rows(CollectionWatermark)
                     if w.collector == INVENTORY_COLLECTOR)
    assert inventory.watermark_at == old
    assert inventory.collection_metadata[INVENTORY_RUN_KEY]["status"] == status
    assert [
        (p.log_source_id, p.starts_at, p.ends_at) for p in await rig.rows(LogSourcePause)
    ] == before
    assert {s.qradar_id: s.enabled for s in await rig.rows(LogSource)} == {
        1: True, 2: False, 3: True,
    }
    assert (await rig.run(tasks.rebuild_baselines))["sources"] == 2
    collected = _instance_result(await rig.run(tasks.collect_metrics))
    assert collected["status"] == "ok" and collected["samples"] == 2
    rows = [m for m in await rig.rows(LogSourceMetric) if m.bucket_start == NOW - STEP]
    assert {m.log_source_id for m in rows} == {rig.sources[i].id for i in (0, 2)}
    judged = await rig.run(tasks.evaluate_anomalies)
    assert judged["sources"] == 2 and judged["failed"] == 0
    assert {w.log_source_id for w in await rig.rows(AnomalyEvaluationWatermark)} == {
        rig.sources[i].id for i in (0, 2)
    }
    # Let measurements age, without refreshing inventory: one enabled source keeps P11 judging.
    rig.at += timedelta(days=2)
    monitor = _instance_result(await rig.run(tasks.detect_stale_log_source_collection))
    assert monitor["judged"] is True and monitor["stale"] is True and monitor["reason"] is None
    assert [
        (p.log_source_id, p.starts_at, p.ends_at) for p in await rig.rows(LogSourcePause)
    ] == before


@pytest.mark.parametrize("entry", ["sync", "evaluate", "rebuild"])
async def test_inventory_engine_and_daily_builder_keep_effective_snapshot(
    activation, monkeypatch, entry,
):
    rig = activation
    await rig.seed((True, False, True))
    await rig.switch(True)
    resolved, consumers = [], []
    real_effective = tasks._effective_settings

    async def effective(task):
        settings, reason = await real_effective(task)
        resolved.append(settings)
        return settings, reason

    async def flip():
        async with rig.maker() as session:
            await session.execute(update(FeatureSwitch).where(
                FeatureSwitch.key == KEY,
            ).values(enabled=False))
            await session.commit()

    monkeypatch.setattr(tasks, "_effective_settings", effective)
    if entry == "sync":
        rig.raw = {1: False, 2: "false", 3: True}
        rig.on_walk = flip
        first = _instance_result(await rig.run(tasks.sync_log_sources))
        assert first["status"] == "ok"
        flags = {s.qradar_id: s.enabled for s in await rig.rows(LogSource)}
        assert flags == {1: False, 2: False, 3: True}
        [span] = await rig.rows(LogSourcePause)
        assert span.log_source_id == rig.sources[0].id
        assert span.starts_at == rig.sources[0].first_seen_at
        rig.on_walk = None
        assert _instance_result(await rig.run(tasks.sync_log_sources))["status"] == "ok"
        assert {s.qradar_id: s.enabled for s in await rig.rows(LogSource)} == {
            1: False, 2: True, 3: True,
        }
        assert [p._strict_source_enabled for _, p in rig.providers] == [True, False]
        for index in range(2):
            assert rig.providers[index][0] is resolved[index]
            assert rig.sync_settings[index] is resolved[index]
            assert rig.service_settings[index] is resolved[index]
        assert len(await rig.rows(LogSourcePause)) == 1
    elif entry == "evaluate":
        # Retained complete inputs for every source; the task and engine must both filter.
        async with rig.maker() as session:
            for source in rig.sources:
                session.add(LogSourceMetric(
                    log_source_id=source.id, bucket_start=NOW - STEP,
                    event_count=15000, average_eps=50.0, bucket_seconds=300,
                    collection_source="ariel_aggregate",
                ))
            await session.commit()
        real_engine = tasks.AnomalyEngine
        judged = []

        class Engine(real_engine):
            def __init__(self, *args, **kwargs):
                consumers.append(kwargs["settings"])
                super().__init__(*args, **kwargs)

            async def evaluate_pending(self, source):
                if len(judged) == 0:
                    await flip()
                judged.append(source.id)
                return await super().evaluate_pending(source)

        monkeypatch.setattr(tasks, "AnomalyEngine", Engine)
        first = await rig.run(tasks.evaluate_anomalies)
        assert first["sources"] == 2 and first["failed"] == 0
        assert set(judged) == {rig.sources[i].id for i in (0, 2)}
        assert {w.log_source_id for w in await rig.rows(AnomalyEvaluationWatermark)} == set(judged)
        judged.clear()
        second = await rig.run(tasks.evaluate_anomalies)
        assert second["sources"] == 3 and second["failed"] == 0
        assert set(judged) == {s.id for s in rig.sources}
        assert all(c is r for c, r in zip(consumers, resolved, strict=True))
    else:
        async with rig.maker() as session:
            session.add(LogSourcePause(
                log_source_id=rig.sources[0].id, reason="QRADAR_DISABLED",
                starts_at=NOW - timedelta(weeks=1),
                ends_at=NOW - timedelta(weeks=1) + STEP,
            ))
            await session.commit()
        real_builder = tasks.BaselineBuilder

        class Builder(real_builder):
            def __init__(self, *args, **kwargs):
                consumers.append(kwargs)
                super().__init__(*args, **kwargs)

            async def rebuild_many(self, *args, **kwargs):
                if len(consumers) == 1:
                    await flip()
                return await super().rebuild_many(*args, **kwargs)

        monkeypatch.setattr(tasks, "BaselineBuilder", Builder)
        assert await rig.run(tasks.rebuild_baselines) == {"sources": 2, "cells": 8}
        assert consumers[0]["settings"] is resolved[0]
        assert consumers[0]["source_pause_policy"] is True
        cells = [c for c in await rig.rows(LogSourceBaseline)
                 if c.log_source_id == rig.sources[0].id and c.hour == NOW.hour]
        assert cells and all(c.sample_count == 5 and c.exclusion_counts["qradar_paused"] == 1
                             for c in cells)
        assert await rig.run(tasks.rebuild_baselines) == {"sources": 3, "cells": 12}
        assert consumers[1] == {}  # The documented off path retains its original constructor.
        cells = await rig.rows(LogSourceBaseline)
        assert all(c.sample_count == 6 for c in cells)
    assert [s.qradar_source_pause_enabled for s in resolved] == [True, False]


async def test_unreadable_collection_keeps_pending_and_legacy_monitor(db_session, monkeypatch):
    """D-99 keeps measurements and pending work; P11 keeps its legacy safety monitor."""
    console, samples, base = await _pending(db_session, pause=False, count=3)
    for source in console.sources.values():
        source.enabled = False
    await db_session.execute(delete(LogSourceBaseline))
    scope = await scope_of(db_session, console.instance)
    before = copy.deepcopy(scope.progress)
    await db_session.execute(text("DROP TABLE feature_switch"))
    await db_session.commit()
    maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
    base = base.model_copy(update={"qradar_source_pause_enabled": True})
    at = FIRST_TICK + 2 * STEP
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    monkeypatch.setattr("app.services.search_cadence.slot_start", lambda *a, **k: at)
    reports, reasons, provider_settings = [], [], []

    def build(instance, settings=None, **kwargs):
        provider_settings.append(settings)
        return samples

    class Collector(MetricCollector):
        def __init__(self, session, provider, *, settings):
            super().__init__(session, provider, settings=settings, clock=lambda: at)

        async def collect(self, instance):
            reasons.append(self.training_disabled_reason)
            assert self.settings is provider_settings[-1]
            report = await super().collect(instance)
            reports.append(report)
            return report

    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    monkeypatch.setattr(tasks, "MetricCollector", Collector)
    with _record(db_session) as sql:
        result = _instance_result(await tasks.collect_metrics())
    assert result["status"] == "ok" and result["samples"] == 3
    assert result["history_rebuild_skipped_reason"] == UNREADABLE_REASON
    assert reasons == [UNREADABLE_REASON]
    assert provider_settings[0].qradar_source_pause_enabled is False
    [report] = reports
    assert report.history.rebuild_skipped_reason == UNREADABLE_REASON
    assert report.history.rebuilt == 0 and report.history.error is None
    assert sum("FROM feature_switch" in s for s in sql) == 1
    assert not any("log_source_baseline" in s for s in sql)
    assert not any("FOR NO KEY UPDATE" in s or "FOR UPDATE" in s for s in sql)
    async with maker() as session:
        assert (await scope_of(session, console.instance)).progress == before
        assert decode_progress(before).adapter[phase_a.REBUILD_PENDING_KEY]
        assert list(await session.scalars(select(LogSourceBaseline))) == []
        rows = list(await session.scalars(select(LogSourceMetric).where(
            LogSourceMetric.bucket_start == ANCHOR + 2 * STEP,
        )))
        assert {r.log_source_id for r in rows} == {s.id for s in console.sources.values()}
        for row in rows:
            qid = next(qid for qid, s in console.sources.items() if s.id == row.log_source_id)
            assert row.event_count == samples.sample(qid, row.bucket_start, 300).event_count
        mark = await session.scalar(select(CollectionWatermark).where(
            CollectionWatermark.collector == metric_collector.COLLECTOR_NAME,
        ))
        assert mark.watermark_at == ANCHOR + 2 * STEP
    assert (await tasks.evaluate_anomalies()) == {
        "status": "disabled", "reason": UNREADABLE_REASON,
    }
    assert (await tasks.rebuild_baselines()) == {
        "status": "disabled", "reason": UNREADABLE_REASON,
    }
    assert await tasks.sync_log_sources() == {
        "status": "disabled", "reason": UNREADABLE_REASON,
    }
    assert len(provider_settings) == 1  # Refusals build no upstream client.
    # All stored flags false would disable P11 under a readable on-policy snapshot.
    # An unreadable switch deliberately keeps its original monitored population instead.
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return at + timedelta(days=2)

    monkeypatch.setattr(tasks, "datetime", Clock)
    with _record(db_session) as monitor_sql:
        monitor = _instance_result(await tasks.detect_stale_log_source_collection())
    assert monitor["judged"] is True and monitor["stale"] is True and monitor["reason"] is None
    probes = [s for s in monitor_sql if "FROM log_source \n" in s]
    assert len(probes) == 1 and "log_source.enabled" not in probes[0]
    async with maker() as session:
        alerts = list(await session.scalars(select(Alert)))
        assert len(alerts) == 1 and alerts[0].status == AlertStatus.OPEN
        assert (await scope_of(session, console.instance)).progress == before
