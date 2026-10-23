"""Offense aggregate warming reports a skip when no snapshot has been collected."""

from __future__ import annotations

from typing import Any
from unittest.mock import Mock
from uuid import uuid4

import pytest

from app.workers import tasks


def _metrics(**over: object) -> dict[str, Any]:
    base: dict[str, Any] = {
        "active": 4,
        "critical": 1,
        "unassigned": 2,
        "exceeding_sla": 0,
        "oldest_age_seconds": 3600,
        "average_magnitude": 5.0,
    }
    base.update(over)
    return base


async def _run(monkeypatch: pytest.MonkeyPatch, metrics: dict[str, Any]) -> dict[str, Any]:
    seen: dict[str, Any] = {}

    class _Repo:
        def __init__(self, session: object) -> None:
            seen["session"] = session

        async def aggregate_metrics(
            self,
            instance_id: object,
            *,
            sla_hours: int,
            critical_magnitude: int,
        ) -> dict[str, Any]:
            seen["instance_id"] = instance_id
            seen["sla_hours"] = sla_hours
            seen["critical_magnitude"] = critical_magnitude
            return dict(metrics)

    async def for_each(
        run: Any,
        instance_name: str | None = None,
        *,
        needs_provider: bool = True,
    ) -> dict[str, Any]:
        assert needs_provider is False
        assert instance_name is None
        session = Mock()
        instance = Mock()
        instance.id = uuid4()
        outcome = await run(session, instance, None)
        return {"instances": 1, "results": [{"instance": "lab", **outcome}]}

    monkeypatch.setattr(tasks, "OffenseRepository", _Repo)
    monkeypatch.setattr(tasks, "_for_each_instance", for_each)
    result = await tasks.calculate_offense_aggregates()
    assert seen["sla_hours"] == tasks.get_settings().offense_sla_hours
    assert seen["critical_magnitude"] == tasks.get_settings().offense_critical_magnitude
    entry: dict[str, Any] = result["results"][0]
    return entry


@pytest.mark.asyncio
async def test_no_snapshot_reports_a_skip_and_processed_stays_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = await _run(monkeypatch, _metrics(
        active=None,
        critical=None,
        unassigned=None,
        exceeding_sla=None,
        oldest_age_seconds=None,
        average_magnitude=None,
    ))
    assert entry["status"] == "skipped"
    assert entry["processed"] == 0
    assert entry["processed"] is not None
    assert entry["skipped_reason"] == "no offense snapshot"
    assert entry["active"] is None


@pytest.mark.asyncio
async def test_a_measured_count_is_processed_and_is_not_a_skip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entry = await _run(monkeypatch, _metrics(active=4))
    assert entry["status"] == "ok"
    assert entry["processed"] == 4
    assert "skipped_reason" not in entry


@pytest.mark.asyncio
async def test_a_measured_zero_is_processed_as_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = await _run(monkeypatch, _metrics(active=0, critical=0, unassigned=0, exceeding_sla=0))
    assert entry["status"] == "ok"
    assert entry["processed"] == 0
    assert "skipped_reason" not in entry
