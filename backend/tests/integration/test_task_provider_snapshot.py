"""D-100: real task entries and feature rows share one snapshot with their providers.

The database resolves DB-on over env-off. Collection/pipeline work is replaced by
bounded reports: this tests task wiring, not the collectors' independently tested SQL.
The real per-instance loop and factory run; only the factory's mock constructor is fake.
"""

from __future__ import annotations

import ast
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.collectors.catchall_collector import CatchallCollectionReport
from app.collectors.geo_collector import GeoCollectionReport
from app.collectors.ips_collector import IpsCollectionReport
from app.collectors.log_source_collector import LogSourceSyncReport
from app.collectors.metric_collector import CollectionReport
from app.collectors.pw_collector import PwCollectionReport
from app.collectors.qradar_health_collector import QRadarHealthReport
from app.collectors.rule_match_collector import RuleMatchCollectionReport
from app.core.config import Settings
from app.identity.collector import IdentityCollectionReport
from app.jdbc.jobs import JdbcRunResult
from app.models.base import utcnow
from app.models.feature_switch import FeatureSwitch
from app.models.property_watch import PropertyWatch
from app.providers import factory
from app.schemas.property_watch import PreviewOut
from app.services.feature_switches import UNREADABLE_REASON, unreadable_features
from app.services.ips_pipeline import IpsPipelineReport
from app.services.pw_pipeline import PwPipelineReport
from app.workers import tasks
from tests.integration.factories import make_instance, make_log_source
from tests.unit.pw_preview_fakes import make_store

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Every effective-settings consumer that constructs a provider, including the two
# already wired before D-100. The AST guard also pins this runtime coverage list.
PROVIDER_TASKS = (
    "collect_metrics", "sync_log_sources", "collect_jdbc_configs", "collect_rule_matches",
    "collect_catchall_senders", "collect_geo_countries", "collect_qradar_health",
    "collect_anomaly_explanations", "run_property_watch_preview", "collect_waf_source",
    "dispatch_ips_collection", "dispatch_identity_collection", "dispatch_property_watch_collection",
)
SWITCHES = (
    "qradar_source_pause_enabled", "jdbc_collection_enabled", "rule_match_collection_enabled",
    "catchall_collection_enabled", "geo_collection_enabled", "qradar_health_collection_enabled",
    "explanation_enabled", "waf_collection_enabled", "ips_collection_enabled",
    "identity_collection_enabled", "pw_collection_enabled",
)


@pytest.fixture
async def rig(db_schema, monkeypatch):
    engine = create_async_engine(db_schema)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    base = Settings(_env_file=None, feature_switches_db=True, **dict.fromkeys(SWITCHES, False))
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: maker)
    monkeypatch.setattr(tasks, "get_settings", lambda: base)
    snapshots = []
    real_effective = tasks._effective_settings

    async def effective(name):
        snapshot, reason = await real_effective(name)
        snapshots.append(snapshot)
        return snapshot, reason

    monkeypatch.setattr(tasks, "_effective_settings", effective)
    provider = SimpleNamespace(aclose=AsyncMock())
    monkeypatch.setattr(factory, "MockQRadarProvider", lambda **_kw: provider)
    builds = []
    real_build = factory.build_provider_for_instance

    def build(instance, settings=None, **kwargs):
        builds.append(settings)
        return real_build(instance, settings=settings, **kwargs)

    monkeypatch.setattr(factory, "build_provider_for_instance", build)
    monkeypatch.setattr(tasks, "build_provider_for_instance", build)
    try:
        async with maker() as session:
            instance = await make_instance(session)
            source = await make_log_source(session, instance)
            watch = PropertyWatch(
                instance_id=instance.id, name="Snapshot preview", scope_kind="LOG_SOURCE",
                log_source_id=source.id, properties=[{"kind": "NORMALIZED", "name": "username"}],
            )
            session.add(watch)
            session.add_all([
                FeatureSwitch(key=key, enabled=True, updated_at=utcnow(), updated_by="tester")
                for key in SWITCHES
            ])
            await session.commit()
        store = make_store(base)
        preview_id = str(uuid.uuid4())
        store.acquire(watch.id, preview_id, datetime.now(UTC))
        store.create(preview_id, watch.id, "tester", datetime.now(UTC))
        monkeypatch.setattr(tasks, "get_preview_store", lambda _base: store)
        yield SimpleNamespace(
            engine=engine, maker=maker, base=base, instance=instance, watch=watch,
            preview_id=preview_id, store=store, provider=provider, builds=builds,
            snapshots=snapshots,
        )
    finally:
        await engine.dispose()


def _stub_work(rig, monkeypatch):
    """Keep real orchestration and check the collector side of the shared snapshot."""
    iid = rig.instance.id
    consumers = []

    def collect(report, *, has_settings=True):
        async def run(self, instance, **_kwargs):
            assert self.provider is rig.provider
            if has_settings:
                consumers.append(self.settings)
            if isinstance(self, tasks.MetricCollector):
                expected = None if self.settings.qradar_source_pause_enabled else UNREADABLE_REASON
                assert self.training_disabled_reason == expected
            return report
        return run

    for cls, report, has_settings in (
        (tasks.MetricCollector, CollectionReport(iid, 0, 0), True),
        (tasks.LogSourceCollector, LogSourceSyncReport(iid, status="COMPLETE"), True),
        (tasks.RuleMatchCollector, RuleMatchCollectionReport(iid), False),
        (tasks.CatchallCollector, CatchallCollectionReport(iid), True),
        (tasks.GeoCollector, GeoCollectionReport(iid, ["SOURCE"]), True),
        (tasks.QRadarHealthCollector, QRadarHealthReport(iid), True),
        (tasks.IpsCollector, IpsCollectionReport(iid), True),
        (tasks.IdentityCollector, IdentityCollectionReport("ok"), True),
        (tasks.PwInstancePipeline, PwPipelineReport(iid, PwCollectionReport(iid)), True),
    ):
        method = "sync" if cls is tasks.LogSourceCollector else (
            "run" if cls is tasks.PwInstancePipeline else "collect"
        )
        monkeypatch.setattr(cls, method, collect(report, has_settings=has_settings))
    monkeypatch.setattr(tasks.RuleEngineTick, "baseline", AsyncMock())
    monkeypatch.setattr(tasks.RuleEngineTick, "judge", AsyncMock(return_value=None))
    monkeypatch.setattr(tasks.IpsInstancePipeline, "run", AsyncMock(
        return_value=IpsPipelineReport(iid)
    ))
    monkeypatch.setattr(tasks, "request_jdbc_run", AsyncMock(return_value=False))
    monkeypatch.setattr(tasks, "run_jdbc_collection", AsyncMock(
        return_value=JdbcRunResult("COMPLETE")
    ))

    async def waf(session, profile_id, *, provider_factory, settings, **_kwargs):
        consumers.append(settings)
        provider = provider_factory(rig.instance)
        assert provider is rig.provider
        await provider.aclose()
        return {"status": "ok"}

    monkeypatch.setattr(tasks, "run_waf_source", waf)
    monkeypatch.setattr(tasks.pw_svc, "preview_scope_ids", AsyncMock(return_value=[]))

    async def preview(watch, scope_ids, settings, provider, *, now):
        consumers.append(settings)
        assert provider is rig.provider
        return PreviewOut(
            watch_id=watch.id, watch_version=watch.watch_version, window_start=now,
            window_end=now, bucket_seconds=300, searches_sent=0, outcome="NOT_MEASURED",
            buckets_measured=0, buckets_lower_bound=0, buckets_unmeasured=0, rows=[], queries=[],
        )

    monkeypatch.setattr(tasks.pw_svc, "run_preview", preview)
    return consumers


async def _run(name, rig):
    if name == "run_property_watch_preview":
        return await tasks.run_property_watch_preview(
            rig.preview_id, str(rig.watch.id), "tester"
        )
    if name == "collect_waf_source":
        return await tasks.collect_waf_source(str(uuid.uuid4()))
    return await getattr(tasks, name)()


@pytest.mark.parametrize("name", PROVIDER_TASKS)
async def test_provider_receives_the_runs_identical_snapshot(rig, monkeypatch, name):
    consumers = _stub_work(rig, monkeypatch)
    result = await _run(name, rig)

    assert len(rig.snapshots) == 1
    snapshot = rig.snapshots[0]
    assert all(getattr(rig.base, key) is False for key in SWITCHES)
    assert all(getattr(snapshot, key) is True for key in SWITCHES)
    assert len(rig.builds) == 1
    assert rig.builds[0] is snapshot
    assert all(settings is snapshot for settings in consumers)
    assert result.get("status") not in {"disabled", "failed"}, result
    assert all(item["status"] != "failed" for item in result.get("results", [])), result
    rig.provider.aclose.assert_awaited_once()


@pytest.mark.parametrize("name", [n for n in PROVIDER_TASKS if n != "collect_metrics"])
async def test_unreadable_switches_refuse_before_building_a_provider(rig, name):
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))

    result = await _run(name, rig)

    assert rig.builds == []
    rig.provider.aclose.assert_not_awaited()
    if name == "run_property_watch_preview":
        assert result["status"] == "failed"
        assert result["reason_code"] == "pw.preview.worker_failed"
        assert rig.store.get(rig.preview_id)["status"] == "FAILED"
    else:
        assert result["status"] == "disabled"
        assert result["reason"] == UNREADABLE_REASON


async def test_unreadable_collection_keeps_the_same_fail_closed_snapshot(rig, monkeypatch):
    consumers = _stub_work(rig, monkeypatch)
    async with rig.engine.begin() as conn:
        await conn.execute(text("DROP TABLE feature_switch"))

    result = await tasks.collect_metrics()

    assert result["results"][0]["status"] == "ok", result
    assert len(rig.snapshots) == len(rig.builds) == len(consumers) == 1
    snapshot = rig.snapshots[0]
    assert rig.builds[0] is snapshot and consumers[0] is snapshot
    assert all(getattr(snapshot, key) is False for key in unreadable_features().values)
    rig.provider.aclose.assert_awaited_once()


async def test_effective_task_provider_calls_explicitly_forward_the_snapshot():
    tree = ast.parse(Path(tasks.__file__).read_text())
    provider_tasks = set()
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        calls = [node for node in ast.walk(fn) if isinstance(node, ast.Call)]
        resolves = [call for call in calls if _call_name(call) == "_effective_settings"]
        if not resolves:
            continue
        resolutions = [
            node for node in ast.walk(fn)
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await)
            and isinstance(node.value.value, ast.Call)
            and _call_name(node.value.value) == "_effective_settings"
        ]
        assert len(resolutions) == len(resolves) == 1, fn.name
        snapshot_name = resolutions[0].targets[0].elts[0].id
        for call in calls:
            name = _call_name(call)
            keywords = {kw.arg: kw.value for kw in call.keywords}
            if name == "_for_each_instance":
                if isinstance(keywords.get("needs_provider"), ast.Constant) and (
                    keywords["needs_provider"].value is False
                ):
                    continue
                argument = keywords.get("provider_settings")
            elif name in {"build_provider", "build_provider_for_instance"}:
                index = 0 if name == "build_provider" else 1
                argument = keywords.get("settings")
                if argument is None and len(call.args) > index:
                    argument = call.args[index]
            else:
                continue
            provider_tasks.add(fn.name)
            assert isinstance(argument, ast.Name) and argument.id == snapshot_name, (
                f"{fn.name}:{call.lineno} must forward {snapshot_name} to {name}"
            )
    assert provider_tasks == set(PROVIDER_TASKS)


def _call_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    return call.func.attr if isinstance(call.func, ast.Attribute) else None
