"""The rule-match job is registered, scheduled, and off by default."""

import asyncio

import pytest

from app.core.config import Settings, get_settings
from app.workers import tasks as _tasks
from app.workers.celery_app import celery_app


def test_the_task_is_registered_and_scheduled() -> None:
    schedule = celery_app.conf.beat_schedule

    assert "collect_rule_matches" in celery_app.tasks
    assert schedule["collect-rule-matches"]["task"] == "collect_rule_matches"
    assert schedule["collect-rule-matches"]["schedule"] == float(
        get_settings().rule_match_collection_interval_seconds
    )


def test_collection_is_disabled_by_default() -> None:
    assert Settings.model_fields["rule_match_collection_enabled"].default is False
    assert Settings.model_fields["rule_match_window_seconds"].default == 3600


def test_the_disabled_task_touches_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A switched-off collector must not even open a session."""

    def boom():
        raise AssertionError("a disabled collector must not open a session")

    monkeypatch.setattr(_tasks, "get_sessionmaker", boom)
    monkeypatch.setattr(
        _tasks, "get_settings", lambda: get_settings().model_copy(
            update={"rule_match_collection_enabled": False}
        )
    )

    assert asyncio.run(_tasks.collect_rule_matches()) == {"status": "disabled"}
