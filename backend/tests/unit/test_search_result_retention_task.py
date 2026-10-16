"""Defaults and beat registration for search-result retention and the cap."""

from __future__ import annotations

from app.core.config import Settings
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_guard_defaults() -> None:
    settings = Settings(_env_file=None)
    assert settings.scheduled_searches_max_enabled == 25
    assert settings.search_result_metric_retention_days == 90
    assert settings.search_result_retention_batch_size == 5000
    assert settings.search_result_retention_max_batches == 20
    assert settings.search_result_retention_interval_seconds == 86400


def test_search_result_retention_is_a_daily_beat() -> None:
    entry = celery_app.conf.beat_schedule["apply-search-result-retention"]
    assert entry["task"] == "apply_search_result_retention"
    assert entry["schedule"] == 86400.0
    assert tasks.apply_search_result_retention_task.name == "apply_search_result_retention"
    assert "apply_search_result_retention" in celery_app.tasks
