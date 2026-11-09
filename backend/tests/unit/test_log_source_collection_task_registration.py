"""Log-source collection health monitoring is registered and scheduled."""

import pytest

from app.cli.sync import COMMANDS
from app.core.config import get_settings
from app.workers import tasks
from app.workers.celery_app import celery_app


@pytest.mark.parametrize(
    ("collection_interval_seconds", "expected"),
    [(300, 900), (600, 1800), (60, 900)],
)
def test_log_source_stale_threshold_has_a_fifteen_minute_floor(
    collection_interval_seconds: int, expected: int
) -> None:
    assert (
        tasks._log_source_stale_threshold_seconds(collection_interval_seconds) == expected
    )


def test_log_source_stale_task_is_registered_scheduled_and_available_to_sync() -> None:
    settings = get_settings()
    schedule = celery_app.conf.beat_schedule

    assert "detect_stale_log_source_collection" in celery_app.tasks
    assert schedule["detect-stale-log-source-collection"] == {
        "task": "detect_stale_log_source_collection",
        "schedule": float(settings.offense_stale_check_interval_seconds),
    }
    assert COMMANDS["stale-log-sources"] is tasks.detect_stale_log_source_collection
