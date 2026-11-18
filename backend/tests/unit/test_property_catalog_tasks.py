"""Property catalog scheduling and the task operator entrypoints."""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

from app.core.config import get_settings
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_catalog_beat_registration_and_refresh_entrypoint(monkeypatch) -> None:
    entry = celery_app.conf.beat_schedule["property-catalog-refresh"]
    assert entry["task"] == "dispatch_property_catalog_refresh"
    assert entry["schedule"] == get_settings().qradar_property_catalog_refresh_seconds
    assert "refresh_property_catalog" in celery_app.tasks
    run = AsyncMock()
    monkeypatch.setattr(tasks, "_refresh_property_catalog", run)
    monkeypatch.setattr(tasks, "_run", asyncio.run)
    instance_id = uuid.uuid4()
    tasks.refresh_property_catalog.run(str(instance_id))
    run.assert_awaited_once_with(instance_id)


def test_catalog_dispatch_entrypoint(monkeypatch) -> None:
    run = AsyncMock()
    monkeypatch.setattr(tasks, "_dispatch_property_catalog_refresh", run)
    monkeypatch.setattr(tasks, "_run", asyncio.run)
    tasks.dispatch_property_catalog_refresh.run()
    run.assert_awaited_once_with()
