"""The custom-rule beat is registered, and it does nothing while the flag is off."""

from __future__ import annotations

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_the_beat_ticks_every_minute_and_retention_runs_daily() -> None:
    entry = celery_app.conf.beat_schedule["evaluate-custom-rules"]
    assert entry == {"task": "evaluate_custom_rules", "schedule": 60.0}
    retention = celery_app.conf.beat_schedule["apply-custom-rule-retention"]
    assert retention["task"] == "apply_custom_rule_retention"
    assert retention["schedule"] == 86400.0


def test_the_tasks_are_registered() -> None:
    assert "evaluate_custom_rules" in celery_app.tasks
    assert "apply_custom_rule_retention" in celery_app.tasks


def test_settings_default_to_off_with_the_design_caps() -> None:
    fields = Settings.model_fields
    assert fields["custom_rules_enabled"].default is False
    assert fields["custom_rules_max_enabled"].default == 100
    assert fields["custom_rules_max_buckets_per_run"].default == 288
    assert fields["custom_rule_breach_retention_days"].default == 180
    assert fields["custom_rules_retention_batch_size"].default == 5000
    assert fields["custom_rules_retention_max_batches"].default == 20
    assert fields["custom_rules_retention_interval_seconds"].default == 86400


def test_retention_bounds_reject_values_outside_the_field() -> None:
    values = get_settings().model_dump()
    values["custom_rules_retention_batch_size"] = 10
    with pytest.raises(ValidationError):
        Settings(**values)


async def test_disabled_task_does_not_open_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tasks, "get_settings", lambda: Mock(custom_rules_enabled=False))
    monkeypatch.setattr(
        tasks, "get_sessionmaker", Mock(side_effect=AssertionError("no session"))
    )
    assert await tasks.evaluate_custom_rules() == {"status": "disabled"}
