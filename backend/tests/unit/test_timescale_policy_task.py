"""The Timescale policy task and the beat-start sends: registration and paths."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

import pytest
from celery.signals import beat_init

from app.core.config import Settings
from app.workers import celery_app as celery_module
from app.workers import tasks


class _FakeSession:
    pass


def _patch(monkeypatch: pytest.MonkeyPatch, settings: Settings, calls: list) -> None:
    @asynccontextmanager
    async def session_cm():
        yield _FakeSession()

    monkeypatch.setattr(tasks, "get_settings", lambda: settings)
    monkeypatch.setattr(tasks, "get_sessionmaker", lambda: session_cm)

    async def fake_apply(session, passed):  # type: ignore[no-untyped-def]
        calls.append((session, passed))
        return {"log_source_metric": "retention-disabled"}

    monkeypatch.setattr(tasks, "apply_policies", fake_apply)


def test_default_interval_and_beat_entry() -> None:
    assert Settings(_env_file=None).timescale_policy_interval_seconds == 86400
    entry = celery_module.celery_app.conf.beat_schedule["apply-timescale-policies"]
    assert entry["task"] == "apply_timescale_policies"
    assert entry["schedule"] == 86400.0
    assert tasks.apply_timescale_policies_task.name == "apply_timescale_policies"
    assert "apply_timescale_policies" in celery_module.celery_app.tasks


def test_retention_stays_off_by_default() -> None:
    assert Settings(_env_file=None).retention_enabled is False


@pytest.mark.asyncio
async def test_disabled_logs_why_and_only_reconciles_to_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list = []
    settings = Settings(_env_file=None, retention_enabled=False)
    _patch(monkeypatch, settings, calls)
    with caplog.at_level(logging.INFO, logger="app.tasks"):
        result = await tasks.apply_timescale_policies()
    assert "RETENTION_ENABLED is false" in caplog.text
    # apply_policies is what removes a policy left from when the flag was on.
    assert len(calls) == 1
    assert calls[0][1].retention_days() == {}
    assert result["failed"] == []


@pytest.mark.asyncio
async def test_enabled_calls_apply_policies_with_the_settings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list = []
    settings = Settings(
        _env_file=None, retention_enabled=True, retention_log_source_metric_days=90
    )
    _patch(monkeypatch, settings, calls)
    with caplog.at_level(logging.INFO, logger="app.tasks"):
        result = await tasks.apply_timescale_policies()
    assert "RETENTION_ENABLED is false" not in caplog.text
    assert len(calls) == 1 and calls[0][1] is settings
    assert result == {"outcome": {"log_source_metric": "retention-disabled"}, "failed": []}


@pytest.mark.asyncio
async def test_failed_tables_are_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(_env_file=None, retention_enabled=True)
    _patch(monkeypatch, settings, [])

    async def failing(session, passed):  # type: ignore[no-untyped-def]
        return {"rule_metric": "error:ProgrammingError", "offense_snapshot": "retention=90d"}

    monkeypatch.setattr(tasks, "apply_policies", failing)
    result = await tasks.apply_timescale_policies()
    assert result["failed"] == ["rule_metric"]


def test_beat_start_sends_each_task_once_and_survives_a_dead_broker(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sent: list[str] = []
    monkeypatch.setattr(celery_module.celery_app, "send_task", lambda name: sent.append(name))
    beat_init.send(sender=None)  # the signal beat fires, not a direct call
    assert sent.count("apply_timescale_policies") == 1
    assert sent.count("beat_heartbeat") == 1

    attempted: list[str] = []

    def boom(name: str) -> None:
        attempted.append(name)
        raise OSError("broker down")

    monkeypatch.setattr(celery_module.celery_app, "send_task", boom)
    with caplog.at_level(logging.WARNING):
        responses = beat_init.send(sender=None)
    assert attempted == ["apply_timescale_policies", "beat_heartbeat"]
    assert all(not isinstance(r[1], Exception) for r in responses)
    assert "could not queue apply_timescale_policies" in caplog.text
    assert "could not queue beat_heartbeat" in caplog.text


def test_beat_start_still_sends_the_heartbeat_when_the_first_send_raises(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    sent: list[str] = []

    def flaky(name: str) -> None:
        if name == "apply_timescale_policies":
            raise OSError("broker down")
        sent.append(name)

    monkeypatch.setattr(celery_module.celery_app, "send_task", flaky)
    with caplog.at_level(logging.WARNING, logger="app.timescale"):
        beat_init.send(sender=None)
    assert sent == ["beat_heartbeat"]
    assert "could not queue apply_timescale_policies" in caplog.text
