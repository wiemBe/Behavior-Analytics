"""ARL-5: the reaper closes dead-worker rows as ABANDONED; the truth wins over it."""

from __future__ import annotations

import dataclasses
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy.dialects import postgresql

from app.core.config import get_settings
from app.models.ariel_search import OUTCOMES, TERMINAL_OUTCOMES
from app.services import ariel_search_log as ariel_log
from app.workers import tasks
from app.workers.celery_app import celery_app

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
MEASURED = (
    "wall_ms",
    "qradar_execution_ms",
    "qradar_record_count",
    "processed_record_count",
    "data_file_count",
    "data_total_size",
    "compressed_data_file_count",
    "compressed_data_total_size",
    "index_file_count",
    "index_total_size",
    "record_count",
)


class _Result:
    def __init__(self, rowcount: int) -> None:
        self.rowcount = rowcount


class _Session:
    """Records the statements the reaper sends."""

    def __init__(self, rowcount: int = 3) -> None:
        self.rowcount = rowcount
        self.statements: list[Any] = []
        self.commits = 0

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result(self.rowcount)

    async def commit(self) -> None:
        self.commits += 1


class _Engine:
    def __init__(self, rowcount: int = 1) -> None:
        self.rowcount = rowcount
        self.statements: list[Any] = []

    def connect(self) -> _Engine:
        return self

    async def __aenter__(self) -> _Engine:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def execute(self, statement: Any) -> _Result:
        self.statements.append(statement)
        return _Result(self.rowcount)

    async def commit(self) -> None:
        return None


def _observation(outcome: str = ariel_log.OUTCOME_COMPLETED) -> ariel_log.ArielSearchObservation:
    return ariel_log.ArielSearchObservation(
        id=uuid.uuid4(),
        instance_id=None,
        caller=ariel_log.CALLER_IPS_COLLECTION,
        scheduled_search_id=None,
        query_version=None,
        submitted_at=NOW,
        finished_at=NOW + timedelta(seconds=5),
        wall_ms=5000,
        qradar_execution_ms=4000,
        qradar_record_count=None,
        processed_record_count=None,
        data_file_count=None,
        data_total_size=None,
        compressed_data_file_count=None,
        compressed_data_total_size=None,
        index_file_count=None,
        index_total_size=None,
        record_count=7,
        range_seconds=None,
        polls=3,
        outcome=outcome,
    )


def test_abandoned_is_a_known_terminal_outcome_in_the_failed_band() -> None:
    assert "ABANDONED" in OUTCOMES and "ABANDONED" in TERMINAL_OUTCOMES
    assert ariel_log.OUTCOME_ABANDONED in ariel_log.TERMINAL_FAILED


def test_the_cutoff_is_the_longest_wait_plus_one_scan_interval() -> None:
    scan = get_settings().ariel_search_abandon_scan_seconds
    assert scan == 600
    bound = ariel_log.stale_after_seconds() + scan
    assert ariel_log.abandon_cutoff(NOW) == NOW - timedelta(seconds=bound)
    # Past the point where /current calls a row stale, so a running search is never reaped.
    assert bound > ariel_log.stale_after_seconds()


@pytest.mark.asyncio
async def test_the_reaper_is_one_update_with_the_cutoff_and_the_retention_floor() -> None:
    session = _Session(rowcount=4)
    assert await ariel_log.reap_abandoned(session, now=NOW) == 4  # type: ignore[arg-type]
    [statement] = session.statements
    assert session.commits == 1
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.startswith("UPDATE ariel_search_run SET")
    params = compiled.params
    cutoff = ariel_log.abandon_cutoff(NOW)
    assert cutoff in params.values()
    # Rows older than the retention window are retention's, not the reaper's.
    floor = NOW - timedelta(days=get_settings().ariel_search_run_retention_days)
    assert floor in params.values()
    assert "ariel_search_run.outcome =" in sql
    assert "ariel_search_run.submitted_at <" in sql and "ariel_search_run.submitted_at >=" in sql
    assert params["outcome_1"] == "IN_FLIGHT"
    assert params["outcome"] == "ABANDONED"
    assert params["finished_at"] == NOW


@pytest.mark.asyncio
async def test_a_reaped_row_keeps_every_measured_column_null_never_zero() -> None:
    session = _Session()
    await ariel_log.reap_abandoned(session, now=NOW)  # type: ignore[arg-type]
    params = session.statements[0].compile(dialect=postgresql.dialect()).params
    for column in MEASURED:
        assert column in params, column
        assert params[column] is None, column
    assert "polls" not in params


@pytest.mark.asyncio
async def test_the_late_close_accepts_in_flight_and_abandoned_and_nothing_else(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: engine)
    assert await ariel_log._close_in_flight(_observation()) is True
    [statement] = engine.statements
    params = statement.compile(dialect=postgresql.dialect()).params
    assert sorted(params["outcome_1"]) == ["ABANDONED", "IN_FLIGHT"]
    assert params["outcome"] == "COMPLETED" and params["wall_ms"] == 5000


@pytest.mark.asyncio
async def test_the_buffered_upsert_overwrites_abandoned_but_not_another_terminal_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = _Engine()
    await ariel_log._insert(engine, [_observation()])
    [statement] = engine.statements
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "ON CONFLICT (id) DO UPDATE" in sql
    where = sql.split("DO UPDATE", 1)[1].split(" WHERE ", 1)[1]
    assert where.startswith("ariel_search_run.outcome IN")
    params = statement.compile(dialect=postgresql.dialect()).params
    guard = next(v for k, v in params.items() if isinstance(v, list) and "IN_FLIGHT" in v)
    assert sorted(guard) == ["ABANDONED", "IN_FLIGHT"]


def test_the_beat_runs_the_reaper_on_the_scan_setting() -> None:
    entry = celery_app.conf.beat_schedule["reap-abandoned-ariel-searches"]
    assert entry["task"] == "reap_abandoned_ariel_searches"
    assert entry["schedule"] == float(get_settings().ariel_search_abandon_scan_seconds)
    assert tasks.reap_abandoned_ariel_searches_task.name == "reap_abandoned_ariel_searches"
    assert "reap_abandoned_ariel_searches" in celery_app.tasks


def test_the_operator_table_and_json_show_the_abandoned_outcome() -> None:
    import json

    from app.cli import ariel_load as cli

    lost = dataclasses.replace(
        _observation(ariel_log.OUTCOME_ABANDONED),
        wall_ms=None,
        qradar_execution_ms=None,
        record_count=None,
    )
    summary = ariel_log.summarize_runs([lost], start=NOW, end=NOW + timedelta(hours=1), hours=1)
    [row] = summary["callers"]
    assert row["outcomes"] == {"ABANDONED": 1}
    assert row["wall_ms_p50"] is None and row["qradar_execution_ms_sum"] is None
    assert "ABANDONED:1" in cli.render_table(summary)
    assert json.loads(json.dumps(summary))["callers"][0]["outcomes"] == {"ABANDONED": 1}
