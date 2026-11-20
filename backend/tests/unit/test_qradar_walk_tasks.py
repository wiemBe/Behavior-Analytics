"""Dedicated queue, bounded tasks and the operator's walker service contract."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.cli import doctor as doctor_module
from app.cli.doctor import Status
from app.core.config import Settings
from app.workers import tasks
from app.workers.celery_app import (
    QRADAR_WALKS_QUEUE,
    WALK_WALL_CLOCKS,
    celery_app,
    walk_time_limits,
)
from tests.unit.test_compose_graph import REPO, compose_merge, load_compose
from tests.unit.test_doctor import make_doctor

REAL_EFFECTIVE_SETTINGS = tasks._effective_settings

WALKS = {
    "sync_log_sources", "sync_rule_inventory", "collect_offenses",
    "refresh_property_catalog", "sync_instance_inventories", "collect_jdbc_configs",
}


def queue(name):
    return celery_app.amqp.router.route({}, name)["queue"].name


def test_all_walks_and_only_walks_are_routed():
    assert set(WALK_WALL_CLOCKS) == WALKS
    assert set(celery_app.conf.task_routes) == WALKS
    for name in WALKS:
        assert queue(name) == QRADAR_WALKS_QUEUE
        task = celery_app.tasks[name]
        assert isinstance(task, tasks.WalkTask)
        assert task.soft_time_limit == walk_time_limits(name)["soft_time_limit"]
        assert task.time_limit == walk_time_limits(name)["time_limit"]
    for name in ("evaluate_anomalies", "beat_heartbeat", "collect_qradar_health",
                 "collect_geo_countries", "dispatch_property_catalog_refresh"):
        assert queue(name) == "celery"
    scheduled_walks = {entry["task"] for entry in celery_app.conf.beat_schedule.values()} & WALKS
    assert scheduled_walks == {
        "sync_log_sources", "sync_rule_inventory", "collect_offenses", "collect_jdbc_configs",
    }
    assert all(queue(name) == QRADAR_WALKS_QUEUE for name in scheduled_walks)


@pytest.mark.parametrize("custom", [False, True])
def test_time_limits_follow_every_wall_clock(custom):
    config = Settings(**({
        "log_source_walk_max_seconds": 2100,
        "rule_walk_max_seconds": 1900,
        "offense_walk_max_seconds": 1800,
        "qradar_property_catalog_walk_timeout_seconds": 1200,
        "jdbc_walk_max_seconds": 900,
    } if custom else {}))
    for name, fields in WALK_WALL_CLOCKS.items():
        limits = walk_time_limits(name, config)
        assert limits["soft_time_limit"] > sum(getattr(config, field) for field in fields)
        assert limits["time_limit"] > limits["soft_time_limit"]
        if custom:
            assert limits != walk_time_limits(name)


def test_both_compose_files_include_the_isolated_worker():
    base = load_compose(REPO / "docker-compose.yml")
    prod = load_compose(REPO / "docker-compose.prod.yml")
    assert "celery-walker" in prod["services"]
    assert prod["services"]["celery-walker"]["image"] == prod["services"]["backend"]["image"]
    for document in (base, compose_merge(base, prod)):
        services = document["services"]
        for name, expected in (("celery-worker", "celery"), ("celery-walker", "qradar_walks")):
            command = services[name]["command"]
            assert command[command.index("-Q") + 1] == expected
            assert services[name]["healthcheck"] == services["celery-worker"]["healthcheck"]
        command = services["celery-walker"]["command"]
        assert "--concurrency=${WALKER_CONCURRENCY:-2}" in command
        assert services["celery-walker"]["build"] == services["backend"]["build"]


def test_operator_script_includes_walker_in_every_worker_list():
    script = (REPO / "qradar-dash").read_text()
    lines = [line for line in script.splitlines() if "celery-worker" in line]
    assert len(lines) >= 5
    assert all("celery-walker" in line for line in lines)
    # Logs/status/support discover services, rather than maintaining another list.
    assert 'dc logs "$@"' in script
    assert 'done <"$SUPPORT_STAGE/services"' in script


@pytest.mark.asyncio
@pytest.mark.parametrize("queues, status", [
    (None, Status.FAIL), ({"worker": [{"name": "celery"}]}, Status.FAIL),
    ({"walker": [{"name": "qradar_walks"}]}, Status.PASS),
    (RuntimeError("secret"), Status.FAIL),
])
async def test_doctor_requires_walk_queue_consumer(monkeypatch, queues, status):
    inspector = MagicMock()
    if isinstance(queues, Exception):
        inspector.active_queues.side_effect = queues
    else:
        inspector.active_queues.return_value = queues
    factory = MagicMock(return_value=inspector)
    monkeypatch.setattr(doctor_module.celery_app.control, "inspect", factory)
    result = await make_doctor(Settings()).check_celery_walker()
    assert result.status is status
    assert "secret" not in result.message
    if status is Status.FAIL:
        assert "celery-walker" in result.message and result.runbook == "OPS-R41"
    factory.assert_called_once_with(timeout=2.0)
    assert ("celery_walker", "OPS-R41") in doctor_module._CHECK_RUNBOOKS


@pytest.mark.parametrize("name", sorted(WALKS))
def test_task_soft_limit_unwinds_then_records_and_reraises(monkeypatch, name):
    from billiard.exceptions import SoftTimeLimitExceeded

    instance_id = uuid.uuid4()
    # The JDBC task has no real timeout target: J1d1 records its own soft_timeout
    # in jdbc_sync_state. This forced target only exercises the generic WalkTask
    # unwind path for every registered walk.
    collector = "property_catalog" if name == "refresh_property_catalog" else (
        "jdbc_config" if name == "collect_jdbc_configs" else
        "log_source_inventory" if name in {"sync_log_sources", "sync_instance_inventories"}
        else "rule_inventory" if name == "sync_rule_inventory" else "offense_snapshot"
    )
    record = AsyncMock()
    unwound = []

    async def interrupted(*args, **kwargs):
        tasks._walk_timeout_target(instance_id, collector)
        try:
            raise SoftTimeLimitExceeded()
        finally:
            unwound.append(True)

    async_name = "_refresh_property_catalog" if name == "refresh_property_catalog" else name
    monkeypatch.setattr(tasks, async_name, interrupted)
    monkeypatch.setattr(tasks, "_run", asyncio.run)
    monkeypatch.setattr(tasks, "_record_walk_timeout", record)
    task = celery_app.tasks[name]
    with pytest.raises(SoftTimeLimitExceeded):
        task.apply(args=[str(instance_id)] if name in {
            "refresh_property_catalog", "sync_instance_inventories"
        } else [], throw=True)
    assert unwound == [True]
    record.assert_awaited_once_with(instance_id, collector)
    assert tasks._WALK_TARGET.get() is None


@pytest.mark.parametrize("outcome", ["ok", "error", "timeout_before_instance"])
def test_walk_task_does_not_record_unrelated_or_unstarted_runs(monkeypatch, outcome):
    from billiard.exceptions import SoftTimeLimitExceeded

    record = AsyncMock()

    async def run(*args):
        if outcome == "error":
            raise ValueError("local failure")
        if outcome == "timeout_before_instance":
            raise SoftTimeLimitExceeded()
        return {"status": "no-instance"}

    monkeypatch.setattr(tasks, "sync_log_sources", run)
    monkeypatch.setattr(tasks, "_run", asyncio.run)
    monkeypatch.setattr(tasks, "_record_walk_timeout", record)
    task = celery_app.tasks["sync_log_sources"]
    if outcome == "ok":
        assert task.apply(throw=True).result == {"status": "no-instance"}
    else:
        expected = ValueError if outcome == "error" else SoftTimeLimitExceeded
        with pytest.raises(expected):
            task.apply(throw=True)
    record.assert_not_awaited()
    assert tasks._WALK_TARGET.get() is None



def test_registered_task_limits_follow_nondefault_environment():
    code = """
import json
from app.workers import tasks
from app.workers.celery_app import celery_app, WALK_WALL_CLOCKS
print(json.dumps({name: [celery_app.tasks[name].soft_time_limit,
                         celery_app.tasks[name].time_limit] for name in WALK_WALL_CLOCKS}))
"""
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code], capture_output=True, text=True, check=True,
        env={**os.environ, "LOG_SOURCE_WALK_MAX_SECONDS": "2100",
             "RULE_WALK_MAX_SECONDS": "1900", "OFFENSE_WALK_MAX_SECONDS": "1800",
             "QRADAR_PROPERTY_CATALOG_WALK_TIMEOUT_SECONDS": "1200"},
    )
    assert json.loads(result.stdout) == {
        "sync_log_sources": [2160, 2220], "sync_rule_inventory": [1960, 2020],
        "collect_offenses": [1860, 1920], "refresh_property_catalog": [1260, 1320],
        "sync_instance_inventories": [4060, 4120], "collect_jdbc_configs": [660, 720],
    }


@pytest.mark.asyncio
async def test_feature_switch_read_does_not_consume_a_soft_limit(monkeypatch):
    from billiard.exceptions import SoftTimeLimitExceeded

    session = MagicMock()
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: lambda: session)
    monkeypatch.setattr(tasks, "resolve", AsyncMock(side_effect=SoftTimeLimitExceeded()))
    with pytest.raises(SoftTimeLimitExceeded):
        await REAL_EFFECTIVE_SETTINGS("sync_log_sources")


@pytest.mark.asyncio
async def test_catalog_timeout_diagnostic_can_be_served_by_the_existing_api(monkeypatch):
    from app.models.property_catalog import PropertyCatalogState
    from app.providers.dto import CatalogFailureDTO

    state = PropertyCatalogState(
        instance_id=uuid.uuid4(), status="REFRESHING", applicability_complete=False,
    )
    session = MagicMock()
    session.__aenter__.return_value = session
    session.get = AsyncMock(return_value=state)
    session.commit = AsyncMock()
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: lambda: session)
    await tasks._record_walk_timeout(state.instance_id, "property_catalog")
    diagnostic = CatalogFailureDTO.model_validate(state.failure)
    assert state.status == "FAILED" and diagnostic.kind == "time_limit"
    assert diagnostic.endpoint == "task"
    session.commit.assert_awaited_once()
