"""FIX-24 (AQL-1): the doctor's `--aql` checks record their searches under `DOCTOR_AQL`.

The doctor's AQL path runs end to end against a real database: one enabled REST console,
a fake REST provider that completes every search the way the real one records it, and the
run buffer `Doctor.run_aql` opens. Every search the provider saw must land as exactly one
closed `ariel_search_run` row, whatever their number; plain `doctor` sends nothing and
writes nothing; a flush that cannot write changes no verdict and no exit code.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Awaitable, Callable, Iterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.cli import doctor as doctor_module
from app.cli.doctor import CheckResult, Doctor, Status, exit_code
from app.core.config import Settings
from app.models.ariel_search import ArielSearchRun
from app.models.instance import QRadarInstance
from app.services import ariel_search_log as ariel_log
from tests.unit.test_doctor_aql import METHOD_FAMILY

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 11, 4, 12, 7, 31, tzinfo=UTC)

#: The properties the console must have for the geo and health families to send a search.
PROPERTIES = ("Metric ID", "Value", "Element", "Source Geographic Location")

#: The families with a switch that is off by default; on, they still SKIP with no profile
#: or watch in the database, which is what this fixture database has.
FAMILIES_ON = {
    "rule_match_collection_enabled": True,
    "waf_collection_enabled": True,
    "ips_collection_enabled": True,
    "pw_collection_enabled": True,
}


def _settings(database_url: str) -> Settings:
    parsed = make_url(database_url)
    return Settings(
        postgres_user=parsed.username or "",
        postgres_password=parsed.password or "",
        postgres_db=parsed.database or "",
        postgres_host=parsed.host or "localhost",
        postgres_port=parsed.port or 5432,
        encryption_key=os.environ["ENCRYPTION_KEY"],
        **FAMILIES_ON,
    )


@pytest.fixture(autouse=True)
def _reset_write_failures() -> Iterator[None]:
    ariel_log.reset_write_failures()
    yield
    ariel_log.reset_write_failures()


class RecordingProvider:
    """A REST console that answers every family's search and records it as the real one does.

    Each search opens the same ``track_search`` context the REST provider opens, inside the
    family's own ``load_scope``, so it lands as an ``IN_FLIGHT`` submit row the moment it
    starts and is closed at its terminal state when it ends. While a search runs, its reader
    counts the ``IN_FLIGHT`` rows the database holds.
    """

    def __init__(self, reader: Callable[[], Awaitable[int]], instance_id: uuid.UUID) -> None:
        self.calls: list[str] = []
        self.in_flight_seen: list[int] = []
        self._reader = reader
        self._instance_id = instance_id

    def supports(self, _capability: object) -> bool:
        return True

    async def list_custom_properties(self) -> list[Any]:
        return [SimpleNamespace(name=name) for name in PROPERTIES]

    async def aclose(self) -> None:
        return None

    async def _search(self, name: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        span = ariel_log.span_from_call(args, kwargs)
        with ariel_log.load_scope(METHOD_FAMILY[name], span):
            async with ariel_log.track_search(
                clock=lambda: datetime.now(UTC), instance_id=self._instance_id
            ):
                self.calls.append(name)
                self.in_flight_seen.append(await self._reader())
                rows = [SimpleNamespace(value=4711)]
                return SimpleNamespace(
                    rows=rows,
                    rows_returned=1,
                    truncated=False,
                    batches={0: SimpleNamespace(rows=rows)},
                    totals={0: 5},
                )

    def __getattr__(self, name: str) -> Any:
        if name not in METHOD_FAMILY:
            raise AttributeError(name)

        async def method(*args: Any, **kwargs: Any) -> Any:
            return await self._search(name, args, kwargs)

        return method


class _BrokenEngine:
    """An engine whose every connection fails: the flush cannot write."""

    def connect(self) -> None:
        raise RuntimeError("database unavailable")


async def _seed_console(maker: async_sessionmaker[Any]) -> QRadarInstance:
    instance = QRadarInstance(
        id=uuid.uuid4(),
        name="doctor-aql-recording",
        console_host="https://fixture.invalid",
        provider_kind="rest",
    )
    async with maker() as session:
        session.add(instance)
        await session.commit()
    return instance


async def _row_count(maker: async_sessionmaker[Any]) -> int:
    async with maker() as session:
        return int(
            await session.scalar(select(func.count()).select_from(ArielSearchRun)) or 0
        )


async def _in_flight_count(maker: async_sessionmaker[Any]) -> int:
    async with maker() as session:
        return int(
            await session.scalar(
                select(func.count())
                .select_from(ArielSearchRun)
                .where(ArielSearchRun.outcome == ariel_log.OUTCOME_IN_FLIGHT)
            )
            or 0
        )


def _doctor(
    settings: Settings,
    maker: async_sessionmaker[Any],
    provider: RecordingProvider,
    *,
    check_aql: bool,
) -> Doctor:
    """The doctor as `--aql` runs it, with the ordinary checks stubbed to PASS.

    Only the AQL path is under test here; the ordinary checks read Redis, Celery and the
    console, none of which this fixture database has.
    """
    doctor = Doctor(
        settings=settings,
        sessionmaker=maker,
        provider_factory=lambda _instance: provider,  # type: ignore[arg-type,return-value]
        now=lambda: NOW,
        check_aql=check_aql,
    )
    for check_id, _runbook in doctor_module._CHECK_RUNBOOKS:
        setattr(
            doctor,
            f"check_{check_id}",
            AsyncMock(return_value=CheckResult(check_id, Status.PASS, "healthy")),
        )
    return doctor


async def test_the_aql_checks_record_every_search_they_send(db_schema, ariel_load_engine):
    maker = async_sessionmaker(ariel_load_engine, expire_on_commit=False)
    instance = await _seed_console(maker)
    provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    doctor = _doctor(_settings(db_schema), maker, provider, check_aql=True)

    results = await doctor.run()

    aql = {result.id: result for result in results if result.id.startswith("aql_")}
    assert list(aql) == [check_id for check_id, _runbook in doctor_module._AQL_CHECK_RUNBOOKS]
    # With no WAF or IPS profile and no watch in the database, those three families SKIP
    # and send nothing; every other family's searches completed.
    for check_id in ("aql_waf_collection", "aql_ips_collection", "aql_property_watch"):
        assert aql[check_id].status is Status.SKIP, check_id
    for check_id, result in aql.items():
        if check_id not in ("aql_waf_collection", "aql_ips_collection", "aql_property_watch"):
            assert result.status is Status.PASS, (check_id, result.message)
    assert exit_code(results) == 0

    async with maker() as session:
        rows = (await session.scalars(select(ArielSearchRun))).all()

    assert provider.calls, "the fixture console should have been searched"
    assert len(rows) == len(provider.calls)
    assert {row.caller for row in rows} == {"DOCTOR_AQL"}
    assert all(row.outcome == ariel_log.OUTCOME_COMPLETED for row in rows)
    assert {row.instance_id for row in rows} == {instance.id}
    assert all(row.finished_at is not None and row.wall_ms is not None for row in rows)
    # While each search ran, its own submit row was the only one in flight.
    assert provider.in_flight_seen == [1] * len(provider.calls)


async def test_plain_doctor_sends_no_search_and_writes_no_row(db_schema, ariel_load_engine):
    maker = async_sessionmaker(ariel_load_engine, expire_on_commit=False)
    instance = await _seed_console(maker)
    provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    doctor = _doctor(_settings(db_schema), maker, provider, check_aql=False)

    results = await doctor.run()

    assert provider.calls == []
    assert not any(result.id.startswith("aql_") for result in results)
    assert await _row_count(maker) == 0


async def test_a_failed_flush_changes_no_verdict_and_no_exit_code(
    db_schema, ariel_load_engine, monkeypatch: pytest.MonkeyPatch
):
    maker = async_sessionmaker(ariel_load_engine, expire_on_commit=False)
    instance = await _seed_console(maker)
    settings = _settings(db_schema)

    healthy_provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    healthy = await _doctor(settings, maker, healthy_provider, check_aql=True).run()
    recorded = await _row_count(maker)
    assert recorded == len(healthy_provider.calls) > 0

    broken_provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    monkeypatch.setattr(ariel_log, "worker_engine", lambda: _BrokenEngine())
    broken = await _doctor(settings, maker, broken_provider, check_aql=True).run()

    assert [(r.id, r.status) for r in broken] == [(r.id, r.status) for r in healthy]
    assert exit_code(broken) == exit_code(healthy) == 0
    assert await _row_count(maker) == recorded
    assert broken_provider.calls == healthy_provider.calls
    # Not one submit row landed, so no search ever saw itself in flight.
    assert broken_provider.in_flight_seen == [0] * len(broken_provider.calls)
    # The failed writes are counted: one per submit, one for the flush.
    assert ariel_log.write_failure_count() == len(broken_provider.calls) + 1


async def test_a_raising_engine_lookup_changes_no_verdict_and_no_exit_code(
    db_schema, ariel_load_engine, monkeypatch: pytest.MonkeyPatch
):
    """FIX-27: a ``worker_engine()`` that raises is a counted failure, not a doctor failure."""
    maker = async_sessionmaker(ariel_load_engine, expire_on_commit=False)
    instance = await _seed_console(maker)
    settings = _settings(db_schema)

    healthy_provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    healthy = await _doctor(settings, maker, healthy_provider, check_aql=True).run()
    recorded = await _row_count(maker)
    assert recorded == len(healthy_provider.calls) > 0

    def _raise() -> None:
        raise RuntimeError("engine lookup failed")

    monkeypatch.setattr(ariel_log, "worker_engine", _raise)
    broken_provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    broken = await _doctor(settings, maker, broken_provider, check_aql=True).run()

    assert [(r.id, r.status) for r in broken] == [(r.id, r.status) for r in healthy]
    assert exit_code(broken) == exit_code(healthy) == 0
    assert await _row_count(maker) == recorded
    assert broken_provider.calls == healthy_provider.calls
    # No submit row could be opened, so no search ever saw itself in flight.
    assert broken_provider.in_flight_seen == [0] * len(broken_provider.calls)
    # One counted failure per submit and one for the flush of their rows.
    assert ariel_log.write_failure_count() == len(broken_provider.calls) + 1


async def test_a_flush_that_raises_changes_no_verdict_and_no_exit_code(
    db_schema, ariel_load_engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    maker = async_sessionmaker(ariel_load_engine, expire_on_commit=False)
    instance = await _seed_console(maker)
    settings = _settings(db_schema)

    healthy = await _doctor(
        settings,
        maker,
        RecordingProvider(lambda: _in_flight_count(maker), instance.id),
        check_aql=True,
    ).run()

    real_finish = ariel_log.finish_worker_buffer

    async def _raise(token: Any) -> None:
        await real_finish(token)
        raise RuntimeError("engine lookup failed")

    monkeypatch.setattr(ariel_log, "finish_worker_buffer", _raise)
    broken_provider = RecordingProvider(lambda: _in_flight_count(maker), instance.id)
    with caplog.at_level("WARNING", logger="app.ariel_search_log"):
        broken = await _doctor(settings, maker, broken_provider, check_aql=True).run()

    assert [(r.id, r.status) for r in broken] == [(r.id, r.status) for r in healthy]
    assert exit_code(broken) == 0
    assert ariel_log.write_failure_count() == 1
    assert [
        r.getMessage() for r in caplog.records if r.name == "app.ariel_search_log"
    ] == ["ariel search log write failed what=flush rows=1"]
