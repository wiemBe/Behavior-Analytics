"""ACC-3: the access check's probe search is recorded in `ariel_search_run` as `ACCESS_CHECK`.

The provider runs on the access tests' `httpx.MockTransport` wire; the run buffer is the one a
Celery task opens, so the rows are what the task's flush would insert.
"""

from __future__ import annotations

import logging

import pytest

from app.providers.base import ProviderAuthError
from app.services import ariel_search_log as ariel_log
from app.services import qradar_access as access
from app.workers import tasks
from tests.integration.ariel_permit_fakes import CountingBudget
from tests.unit.test_qradar_access import Wire


async def _probe_rows(wire: Wire, budget: CountingBudget | None = None, *, raises=None):  # type: ignore[no-untyped-def]
    token = ariel_log.open_run_buffer()
    try:
        provider = wire.provider(budget or CountingBudget())
        provider._max_retries = 0
        if raises is None:
            await provider.probe_ariel_access()
        else:
            with pytest.raises(raises):
                await provider.probe_ariel_access()
    finally:
        rows = ariel_log.take_run_buffer(token)
    return rows


@pytest.mark.asyncio
async def test_one_probe_writes_one_access_check_row_with_nothing_invented() -> None:
    rows = await _probe_rows(Wire())
    assert len(rows) == 1
    row = rows[0]
    assert row.caller == "ACCESS_CHECK"
    assert row.outcome == "CANCELED"
    assert row.polls == 0 and row.finished_at is not None
    # Never polled, never read: no cost and no count is recorded.
    assert row.record_count is None and row.qradar_record_count is None
    assert row.qradar_execution_ms is None and row.processed_record_count is None


@pytest.mark.asyncio
async def test_the_caller_is_access_check_not_the_family_that_ran_before() -> None:
    wire = Wire()
    token = ariel_log.open_run_buffer()
    try:
        provider = wire.provider(CountingBudget())
        with ariel_log.load_scope("QRADAR_HEALTH", None):
            await provider.probe_ariel_access()

        # An entry that overrides the family (HISTORY, PREVIEW) must not relabel the probe.
        @ariel_log.ariel_caller(ariel_log.CALLER_HISTORY)
        async def from_a_history_entry() -> None:
            await provider.probe_ariel_access()

        await from_a_history_entry()
        await provider.probe_ariel_access()
    finally:
        rows = ariel_log.take_run_buffer(token)
    assert [r.caller for r in rows] == ["ACCESS_CHECK"] * 3


@pytest.mark.asyncio
async def test_a_create_refused_with_403_is_recorded_with_its_error_outcome() -> None:
    rows = await _probe_rows(Wire({"POST /ariel/searches": 403}), raises=ProviderAuthError)
    assert [(r.caller, r.outcome) for r in rows] == [("ACCESS_CHECK", "ERROR")]


@pytest.mark.asyncio
async def test_a_create_that_fails_with_a_server_error_is_recorded() -> None:
    from app.providers.base import ProviderError

    rows = await _probe_rows(Wire({"POST /ariel/searches": 500}), raises=ProviderError)
    assert [(r.caller, r.outcome) for r in rows] == [("ACCESS_CHECK", "PROVIDER_UNAVAILABLE")]


@pytest.mark.asyncio
async def test_a_permit_refusal_is_recorded_as_refused() -> None:
    from app.services.ariel_permit import ArielPermitUnavailable

    wire = Wire()
    rows = await _probe_rows(wire, CountingBudget(refuse=True), raises=ArielPermitUnavailable)
    assert [(r.caller, r.outcome) for r in rows] == [("ACCESS_CHECK", "REFUSED")]
    assert wire.sent() == []


@pytest.mark.asyncio
async def test_a_failed_cancel_still_writes_the_row_and_logs_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="app.providers.qradar_rest")
    rows = await _probe_rows(Wire({"DELETE /ariel/searches/probe-1": 500}))
    assert [(r.caller, r.outcome) for r in rows] == [("ACCESS_CHECK", "ERROR")]
    assert any("was not cancelled" in m for m in caplog.messages)


@pytest.mark.asyncio
async def test_a_good_cancel_logs_one_line_without_the_search_id(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    await _probe_rows(Wire())
    lines = [r for r in caplog.records if r.getMessage() == "access probe search cancelled"]
    assert len(lines) == 1
    ours = [r for r in caplog.records if r.name == "app.providers.qradar_rest"]
    assert "probe-1" not in " ".join(f"{r.getMessage()} {r.__dict__}" for r in ours)


@pytest.mark.asyncio
async def test_a_check_through_the_task_runner_flushes_one_access_check_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The path both the 6 h beat and "Check now" take: `tasks._run` opens and flushes."""
    flushed: list[ariel_log.ArielSearchObservation] = []

    async def capture(bind: object, rows: object) -> None:
        flushed.extend(rows)  # type: ignore[arg-type]

    monkeypatch.setattr(ariel_log, "worker_engine", lambda: object())
    monkeypatch.setattr(ariel_log, "_insert", capture)
    provider = Wire().provider(CountingBudget())
    answers = await _run_in_loop(access.probe_entries(provider))
    assert answers["ariel_searches"] is not None
    assert [(r.caller, r.outcome) for r in flushed] == [("ACCESS_CHECK", "CANCELED")]


async def _run_in_loop(coro):  # type: ignore[no-untyped-def]
    import asyncio

    return await asyncio.to_thread(tasks._run, coro)
