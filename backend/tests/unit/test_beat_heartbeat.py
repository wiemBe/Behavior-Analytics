from __future__ import annotations

from unittest.mock import MagicMock

from app.workers import tasks
from app.workers.celery_app import BEAT_HEARTBEAT_KEY, BEAT_HEARTBEAT_TTL_SECONDS, celery_app


def test_beat_heartbeat_is_scheduled_every_sixty_seconds() -> None:
    entry = celery_app.conf.beat_schedule["beat-heartbeat"]
    assert entry == {"task": "beat_heartbeat", "schedule": 60.0}


def test_beat_heartbeat_writes_timestamp_with_three_minute_ttl(monkeypatch) -> None:
    client = MagicMock()
    context = MagicMock()
    context.__enter__.return_value = client
    monkeypatch.setattr(tasks.Redis, "from_url", MagicMock(return_value=context))

    result = tasks.beat_heartbeat_task.run()

    assert set(result) == {"timestamp"}
    client.set.assert_called_once_with(
        BEAT_HEARTBEAT_KEY,
        result["timestamp"],
        ex=BEAT_HEARTBEAT_TTL_SECONDS,
    )
    assert BEAT_HEARTBEAT_TTL_SECONDS == 180
