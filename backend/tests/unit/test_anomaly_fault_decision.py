"""The bucket attempt bound: retry while attempts < K, give up at K.

Pure, so the guard is pinned without a database. The engine applies this after
recording one failed attempt; `attempts` counts evaluation ticks.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.anomaly.faults import FaultDecision, next_attempt
from app.core.config import Settings
from app.workers import tasks
from app.workers.celery_app import ANOMALY_EVALUATION_CADENCE_SECONDS, celery_app


def test_retries_below_the_bound() -> None:
    assert next_attempt(1, 3) is FaultDecision.RETRY
    assert next_attempt(2, 3) is FaultDecision.RETRY


def test_gives_up_on_the_bound_and_never_retries_after_it() -> None:
    assert next_attempt(3, 3) is FaultDecision.GIVE_UP
    assert next_attempt(4, 3) is FaultDecision.GIVE_UP


def test_bound_of_one_gives_up_on_the_first_failure() -> None:
    assert next_attempt(1, 1) is FaultDecision.GIVE_UP


@pytest.mark.parametrize(
    ("attempts", "max_attempts"),
    [(0, 3), (-1, 3), (1, 0), (1, -1)],
)
def test_refuses_impossible_bounds(attempts: int, max_attempts: int) -> None:
    with pytest.raises(ValueError):
        next_attempt(attempts, max_attempts)


def test_setting_keeps_the_bound_between_one_and_twenty() -> None:
    assert Settings(encryption_key="x" * 44).anomaly_max_bucket_attempts == 3
    raised = Settings(encryption_key="x" * 44, anomaly_max_bucket_attempts=20)
    assert raised.anomaly_max_bucket_attempts == 20
    for refused in (0, 21):
        with pytest.raises(ValidationError):
            Settings(encryption_key="x" * 44, anomaly_max_bucket_attempts=refused)


def test_retention_setting_keeps_the_window_between_one_and_3650_days() -> None:
    lowest = Settings(encryption_key="x" * 44, anomaly_fault_retention_days=1)
    assert lowest.anomaly_fault_retention_days == 1
    assert (
        Settings(encryption_key="x" * 44, anomaly_fault_retention_days=3650)
        .anomaly_fault_retention_days
        == 3650
    )
    for refused in (0, 3651):
        with pytest.raises(ValidationError):
            Settings(encryption_key="x" * 44, anomaly_fault_retention_days=refused)


def test_retention_is_scheduled_daily_and_both_tasks_are_registered() -> None:
    """A fault that never waits is not a queue the operator can see; a fault
    that is never pruned grows forever. Pin the daily entry and both callables.

    The evaluation tick stays 60 s because the doctor reports a source as
    trailing at 2x it: a silent change would move the WARN threshold too.
    """
    entry = celery_app.conf.beat_schedule["apply-anomaly-fault-retention"]
    assert entry == {"task": "apply_anomaly_fault_retention", "schedule": 86400.0}
    assert ANOMALY_EVALUATION_CADENCE_SECONDS == 60
    assert (
        celery_app.conf.beat_schedule["evaluate-anomalies"]["schedule"]
        == ANOMALY_EVALUATION_CADENCE_SECONDS
    )
    assert "apply_anomaly_fault_retention" in tasks.celery_app.tasks
    assert "evaluate_anomalies" in tasks.celery_app.tasks
    assert tasks.apply_anomaly_fault_retention_task.name == (
        "apply_anomaly_fault_retention"
    )
    assert Settings(encryption_key="x" * 44).anomaly_fault_retention_days == 30
