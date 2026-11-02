"""The one-shot inventory kick: registered, off the beat, both collectors, that id."""

from __future__ import annotations

import pytest

from app.cli import qradar as cli
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_the_kick_is_registered_and_not_on_the_beat() -> None:
    assert "sync_instance_inventories" in celery_app.tasks
    scheduled = {entry["task"] for entry in celery_app.conf.beat_schedule.values()}
    assert "sync_instance_inventories" not in scheduled
    assert {"sync_log_sources", "sync_rule_inventory"} <= scheduled


@pytest.mark.asyncio
async def test_the_kick_runs_both_inventories_for_that_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str | None, str | None]] = []
    instance_id = "11111111-1111-1111-1111-111111111111"

    async def log_sources(
        instance_name: str | None = None, *, instance_id: str | None = None
    ) -> dict[str, str]:
        seen.append(("log_sources", instance_name, instance_id))
        return {"status": "ok"}

    async def rules(
        instance_name: str | None = None, *, instance_id: str | None = None
    ) -> dict[str, str]:
        seen.append(("rules", instance_name, instance_id))
        return {"status": "ok"}

    monkeypatch.setattr(tasks, "sync_log_sources", log_sources)
    monkeypatch.setattr(tasks, "sync_rule_inventory", rules)

    result = await tasks.sync_instance_inventories(instance_id)

    assert seen == [
        ("log_sources", None, instance_id),
        ("rules", None, instance_id),
    ]
    assert result == {"log_sources": {"status": "ok"}, "rules": {"status": "ok"}}


@pytest.mark.asyncio
async def test_a_log_source_failure_is_not_caught_before_rules(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ran: list[str] = []

    async def log_sources(
        instance_name: str | None = None, *, instance_id: str | None = None
    ) -> dict[str, str]:
        ran.append("log_sources")
        raise RuntimeError("inventory failed")

    async def rules(
        instance_name: str | None = None, *, instance_id: str | None = None
    ) -> dict[str, str]:
        ran.append("rules")
        return {"status": "ok"}

    monkeypatch.setattr(tasks, "sync_log_sources", log_sources)
    monkeypatch.setattr(tasks, "sync_rule_inventory", rules)

    with pytest.raises(RuntimeError, match="inventory failed"):
        await tasks.sync_instance_inventories("abc")
    assert ran == ["log_sources"]


def test_add_help_says_a_disabled_console_is_not_queued(
    capsys: pytest.CaptureFixture[str],
) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["add", "--help"])
    assert caught.value.code == 0
    text = capsys.readouterr().out
    assert "--disabled" in text
    assert "not queued for inventory" in text
