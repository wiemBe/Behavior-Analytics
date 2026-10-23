"""The beat healthcheck's threshold, pinned to the Celery sync gap.

``Scheduler.sync_every`` is 180s. The beat loop checks it after a tick, and a
tick may sleep for ``max_interval`` (``beat_max_loop_interval`` when set,
otherwise ``DEFAULT_MAX_INTERVAL``, 300s). A sync that just missed its deadline
therefore waits ``sync_every + max_interval``. ``beat_sync_every`` is a task
count (``sync_every_tasks``); the default 0 disables it and cannot widen the gap.

The shelf file that actually changes depends on the dbm backend. This venv is
Python 3.14 ``dbm.sqlite3`` (WAL): the main file's mtime stays at creation and
``-wal`` moves on sync. The image is Python 3.12 and may rewrite another name.
The check uses the newest mtime among those names.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from pathlib import Path

from app.workers.celery_app import celery_app

REPO = Path(__file__).resolve().parents[3]


def _beat_service() -> dict:
    # Imported lazily so a YAML failure names this test rather than collection.
    from tests.unit.test_compose_graph import load_compose

    document = load_compose(REPO / "docker-compose.yml")
    service = document["services"]["celery-beat"]
    assert isinstance(service, dict)
    return service


def _schedule_path() -> str:
    command = _beat_service()["command"]
    assert isinstance(command, list)
    matches = [part.split("=", 1)[1] for part in command if part.startswith("--schedule=")]
    assert len(matches) == 1
    return matches[0]


def _healthcheck_script() -> str:
    probe = _beat_service()["healthcheck"]["test"]
    assert isinstance(probe, list)
    assert probe[0] == "CMD-SHELL"
    raw = probe[1]
    assert isinstance(raw, str)
    prefix = 'python -c "'
    assert raw.startswith(prefix) and raw.endswith('"')
    return raw[len(prefix) : -1]


def _threshold() -> int:
    match = re.search(r"\bthreshold=(\d+)\b", _healthcheck_script())
    assert match is not None
    return int(match.group(1))


def _suffixes() -> list[str]:
    match = re.search(r"ss=\[(.*?)\]", _healthcheck_script())
    assert match is not None
    return re.findall(r"'([^']*)'", match.group(1))


def _write_gap_seconds() -> float:
    """Worst-case seconds between shelve syncs for this Celery and this app."""
    import celery.beat as beat

    configured = float(celery_app.conf.beat_max_loop_interval or 0)
    max_interval = configured if configured > 0 else float(beat.Scheduler.max_interval)
    # beat_sync_every counts tasks. Zero disables that path. A positive value
    # syncs more often and does not raise this quiet-scheduler bound.
    return float(beat.Scheduler.sync_every) + max_interval


def test_threshold_is_twice_the_celery_sync_gap() -> None:
    assert _threshold() >= 2 * _write_gap_seconds()


def test_healthcheck_uses_the_schedule_path_and_mtime() -> None:
    script = _healthcheck_script()
    assert _schedule_path() in script
    assert "st_mtime" in script
    suffixes = _suffixes()
    assert "" in suffixes
    assert "-wal" in suffixes
    assert ".db" in suffixes


def _open_shelf(path: Path) -> object:
    import shelve

    # The probe has to run while the shelf is open: closing a sqlite shelf
    # checkpoints the WAL away, which is not the file beat leaves behind.
    return shelve.open(str(path), writeback=True)  # noqa: S301


def test_healthcheck_covers_files_this_interpreter_writes(tmp_path: Path) -> None:
    base = tmp_path / "celerybeat-schedule"
    with _open_shelf(base) as shelf:
        shelf["entries"] = {"probe": 1}
        shelf.sync()
        created = [path.name for path in tmp_path.iterdir()]
    suffixes = set(_suffixes())
    assert created, "shelve created no file"
    for name in created:
        assert name.startswith(base.name)
        suffix = name[len(base.name) :]
        assert suffix in suffixes, name


def _run_probe(root: Path) -> int:
    base = root / "celerybeat-schedule"
    script = _healthcheck_script().replace(_schedule_path(), str(base))
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return result.returncode


def test_healthcheck_command_passes_while_the_shelf_is_fresh(tmp_path: Path) -> None:
    base = tmp_path / "celerybeat-schedule"
    with _open_shelf(base) as shelf:
        shelf["entries"] = {"probe": 1}
        shelf.sync()
        assert _run_probe(tmp_path) == 0


def test_healthcheck_command_fails_when_the_shelf_is_stale_or_missing(tmp_path: Path) -> None:
    assert _run_probe(tmp_path) == 1
    base = tmp_path / "celerybeat-schedule"
    with _open_shelf(base) as shelf:
        shelf["entries"] = {"probe": 1}
        shelf.sync()
        old = time.time() - (_threshold() + 5)
        for path in tmp_path.iterdir():
            os.utime(path, (old, old))
        assert _run_probe(tmp_path) == 1


def test_comment_does_not_claim_restart_acts_on_unhealthy() -> None:
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    start = text.index("  celery-beat:")
    section = text[start : start + 2500]
    assert "restart policy can fire" not in section
    assert "acts on process" in section
    assert "Redis heartbeat is not used" in section
