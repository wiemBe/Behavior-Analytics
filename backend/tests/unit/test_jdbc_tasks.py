"""JDBC-1 J1d2: walk task routing, budgets, soft/hard limits and Beat entry."""

from __future__ import annotations

from app.core.config import Settings, get_settings
from app.workers import tasks
from app.workers.celery_app import (
    QRADAR_WALKS_QUEUE,
    WALK_WALL_CLOCKS,
    celery_app,
    walk_time_limits,
)

NAME = "collect_jdbc_configs"
BEAT_KEY = "collect-jdbc-configs"


def test_walk_queue_limits_timeout_inventory() -> None:
    # Registered with the walk base class, on the isolated walker queue.
    task = celery_app.tasks[NAME]
    assert isinstance(task, tasks.WalkTask)
    assert WALK_WALL_CLOCKS[NAME] == ("jdbc_walk_max_seconds",)
    assert celery_app.conf.task_routes[NAME] == {"queue": QRADAR_WALKS_QUEUE}
    assert celery_app.amqp.router.route({}, NAME)["queue"].name == QRADAR_WALKS_QUEUE

    # The family ships off; the Beat entry exists but the task returns disabled.
    assert Settings.model_fields["jdbc_collection_enabled"].default is False
    assert Settings.model_fields["jdbc_walk_interval_seconds"].default == 900
    assert Settings.model_fields["jdbc_walk_max_seconds"].default == 600

    # Default wall -> soft/hard 660/720 by the existing +60/+120 formula.
    assert walk_time_limits(NAME) == {"soft_time_limit": 660.0, "time_limit": 720.0}
    assert task.soft_time_limit == 660.0 and task.time_limit == 720.0
    # A custom wall follows the same formula.
    custom = Settings(jdbc_walk_max_seconds=900)
    assert walk_time_limits(NAME, custom) == {"soft_time_limit": 960.0, "time_limit": 1020.0}

    # One Beat entry every jdbc_walk_interval_seconds.
    entry = celery_app.conf.beat_schedule[BEAT_KEY]
    assert entry["task"] == NAME
    assert entry["schedule"] == float(get_settings().jdbc_walk_interval_seconds)
