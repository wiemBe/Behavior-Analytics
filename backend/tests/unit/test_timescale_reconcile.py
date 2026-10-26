"""The per-reconcile transaction and deadlock retry in apply_policies.

No database: the reads and the reconciles are replaced and a fake session
records the transactional calls, so the retry and failure-isolation logic is
pinned without Timescale's job catalog.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services import timescale


class _DriverError(Exception):
    """Stand-in for the asyncpg error a DBAPIError wraps."""

    def __init__(self, sqlstate: str) -> None:
        super().__init__(f"SQLSTATE {sqlstate}")
        self.sqlstate = sqlstate


def _dbapi_error(sqlstate: str) -> DBAPIError:
    return DBAPIError("SELECT 1", {}, _DriverError(sqlstate))


class _FakeSession:
    """Records the transactional calls apply_policies makes, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, statement):  # type: ignore[no-untyped-def]
        self.calls.append("execute")

    async def commit(self) -> None:
        self.calls.append("commit")

    async def rollback(self) -> None:
        self.calls.append("rollback")


def _settings() -> Settings:
    return Settings(_env_file=None)


def _present(monkeypatch, tables: set[str]) -> None:  # type: ignore[no-untyped-def]
    """Timescale present, exactly `tables` managed."""

    async def available(session):  # type: ignore[no-untyped-def]
        return True

    async def managed(session):  # type: ignore[no-untyped-def]
        return tables

    monkeypatch.setattr(timescale, "_timescale_available", available)
    monkeypatch.setattr(timescale, "_hypertables", managed)


@pytest.fixture
def no_pause(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setattr(timescale, "_DEADLOCK_RETRY_PAUSES", (0.0, 0.0))


async def _compression_ok(session: AsyncSession, table: str, days: int | None) -> str:
    return "compression=7d"


async def test_a_deadlock_is_retried_and_then_succeeds(monkeypatch, no_pause) -> None:
    _present(monkeypatch, {"log_source_metric"})
    session = _FakeSession()
    reconciles: list[str] = []

    async def flaky(session, table, days):  # type: ignore[no-untyped-def]
        reconciles.append(table)
        if len(reconciles) == 1:
            raise _dbapi_error("40P01")
        return "retention=90d"

    monkeypatch.setattr(timescale, "_reconcile_retention", flaky)
    monkeypatch.setattr(timescale, "_reconcile_compression", _compression_ok)

    outcome = await timescale.apply_policies(session, _settings())

    assert outcome["log_source_metric"] == "retention=90d"
    assert len(reconciles) == 2
    # The reads' commit, the deadlocked attempt rolled back, then one commit per
    # succeeding reconcile.
    assert session.calls == ["commit", "rollback", "commit", "commit"]


async def test_a_deadlock_on_every_attempt_is_an_error_after_three_attempts(
    monkeypatch, no_pause, caplog
) -> None:
    _present(monkeypatch, {"log_source_metric"})
    session = _FakeSession()
    reconciles: list[str] = []

    async def deadlocks(session, table, days):  # type: ignore[no-untyped-def]
        reconciles.append(table)
        raise _dbapi_error("40P01")

    monkeypatch.setattr(timescale, "_reconcile_retention", deadlocks)
    monkeypatch.setattr(timescale, "_reconcile_compression", _compression_ok)

    with caplog.at_level(logging.ERROR, logger="app.timescale"):
        outcome = await timescale.apply_policies(session, _settings())

    assert outcome["log_source_metric"] == "error:DBAPIError"
    assert len(reconciles) == 3
    assert session.calls == ["commit", "rollback", "rollback", "rollback", "commit"]
    record = next(r for r in caplog.records if r.getMessage() == "retention policy failed")
    assert record.table == "log_source_metric"
    assert record.attempts == 3


async def test_another_database_error_is_not_retried(monkeypatch, no_pause, caplog) -> None:
    _present(monkeypatch, {"log_source_metric"})
    session = _FakeSession()
    reconciles: list[str] = []

    async def division_by_zero(session, table, days):  # type: ignore[no-untyped-def]
        reconciles.append(table)
        raise _dbapi_error("22012")

    monkeypatch.setattr(timescale, "_reconcile_retention", division_by_zero)
    monkeypatch.setattr(timescale, "_reconcile_compression", _compression_ok)

    with caplog.at_level(logging.ERROR, logger="app.timescale"):
        outcome = await timescale.apply_policies(session, _settings())

    assert outcome["log_source_metric"] == "error:DBAPIError"
    assert reconciles == ["log_source_metric"]
    assert session.calls == ["commit", "rollback", "commit"]
    record = next(r for r in caplog.records if r.getMessage() == "retention policy failed")
    assert record.attempts == 1


async def test_a_failing_table_does_not_stop_the_next_one(monkeypatch, no_pause) -> None:
    _present(monkeypatch, set(timescale._METRIC_TABLES))
    session = _FakeSession()
    ran: list[str] = []

    async def failing_on_first(session, table, days):  # type: ignore[no-untyped-def]
        ran.append(f"retention:{table}")
        if table == "log_source_metric":
            raise ValueError("retention for log_source_metric must be >= 1 day, got 0")
        return "retention=90d"

    async def compression_ran(session, table, days):  # type: ignore[no-untyped-def]
        ran.append(f"compression:{table}")
        return "compression=7d"

    monkeypatch.setattr(timescale, "_reconcile_retention", failing_on_first)
    monkeypatch.setattr(timescale, "_reconcile_compression", compression_ran)

    outcome = await timescale.apply_policies(session, _settings())

    assert outcome["log_source_metric"] == "error:ValueError"
    assert outcome["search_result_metric"] == "retention=90d"
    assert ran == [
        "retention:log_source_metric",
        "compression:log_source_metric",
        "retention:search_result_metric",
        "compression:search_result_metric",
        "retention:rule_metric",
        "compression:rule_metric",
        "retention:offense_snapshot",
        "compression:offense_snapshot",
    ]
    # The reads' commit, log_source_metric's rollback, and a commit per
    # succeeding reconcile — the next table's is among them.
    assert session.calls == ["commit", "rollback"] + ["commit"] * 7
