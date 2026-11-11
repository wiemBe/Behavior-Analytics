"""SOC-16f4 unit: CLI arguments and exit codes, settings bounds, beat entry, task gate."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.cli import entity_mentions as cli
from app.core.config import Settings
from app.services.instance_registry import InstanceNotFound
from app.workers import tasks
from app.workers.celery_app import celery_app


def test_parser_accepts_the_documented_commands() -> None:
    parser = cli.build_parser()
    run = parser.parse_args(["run", "--instance", "lab", "--max-batches", "3"])
    assert (run.command, run.instance, run.max_batches) == ("run", "lab", 3)
    assert parser.parse_args(["run"]).max_batches is None
    status = parser.parse_args(["status", "--json"])
    assert (status.command, status.instance, status.json) == ("status", None, True)


@pytest.mark.parametrize(
    "argv",
    [[], ["bogus"], ["run", "--max-batches", "0"], ["run", "--max-batches", "x"], ["status", "-z"]],
)
def test_bad_arguments_exit_2_without_touching_the_database(
    argv: list[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli, "get_sessionmaker", lambda: pytest.fail("bad arguments must not reach the database")
    )
    assert cli.main(argv) == cli.EXIT_BAD_ARGUMENTS == 2


def test_help_exits_0_and_names_every_exit_code(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["run", "--help"]) == 0
    out = capsys.readouterr().out
    for code in ("0  ", "1  ", "2  ", "3  ", "4  "):
        assert code in out


def test_exit_code_mapping_of_unexpected_failures(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    async def unknown(_args: Any) -> int:
        raise InstanceNotFound("no QRadar instance registered under the name 'x'")

    monkeypatch.setattr(cli, "_run", unknown)
    assert cli.main(["run", "--instance", "x"]) == 2

    async def broken(_args: Any) -> int:
        raise ConnectionRefusedError("postgres://user:hunter2@db/x")

    monkeypatch.setattr(cli, "_status", broken)
    assert cli.main(["status"]) == 1
    err = capsys.readouterr().err
    assert "ConnectionRefusedError" in err
    assert "hunter2" not in err


def test_settings_defaults_and_bounds() -> None:
    settings = Settings()
    assert settings.entity_mention_backfill_enabled is True
    assert settings.entity_mention_backfill_batch_size == 500
    assert settings.entity_mention_backfill_max_batches_per_run == 20
    assert settings.entity_mention_backfill_interval_seconds == 300
    for name, good, bad in (
        ("entity_mention_backfill_batch_size", (1, 5000), (0, 5001)),
        ("entity_mention_backfill_max_batches_per_run", (1, 1000), (0, 1001)),
        ("entity_mention_backfill_interval_seconds", (60, 86400), (59, 0)),
    ):
        for value in good:
            assert getattr(Settings(**{name: value}), name) == value
        for value in bad:
            with pytest.raises(ValidationError):
                Settings(**{name: value})


def test_beat_entry_follows_the_interval_setting() -> None:
    entry = celery_app.conf.beat_schedule["backfill-entity-mentions"]
    assert entry["task"] == "backfill_entity_mentions"
    assert entry["schedule"] == float(Settings().entity_mention_backfill_interval_seconds)
    assert "backfill_entity_mentions" in celery_app.tasks


@pytest.mark.asyncio
async def test_task_disabled_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tasks,
        "get_settings",
        lambda: Settings(entity_mention_backfill_enabled=False),
    )
    monkeypatch.setattr(
        tasks, "get_sessionmaker", lambda: pytest.fail("a disabled task must not open a session")
    )
    report = await tasks.backfill_entity_mentions()
    assert report["status"] == "disabled"
