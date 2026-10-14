"""Log source inventory collection against a real database.

The collector is a full inventory pass rather than an incremental window, so
the properties worth proving are convergence (repeat runs do not duplicate),
SOC-metadata preservation, watermark behaviour on success and on failure, and
the advisory lock.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.collectors.log_source_collector import COLLECTOR_NAME, LogSourceCollector
from app.models.enums import Criticality, InstanceStatus
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import (
    ProviderCapability,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import InstanceInfoDTO, LogSourceDTO, LogSourceTypeDTO
from app.services.locks import CollectorAdvisoryLock
from tests.integration.factories import make_instance

pytestmark = pytest.mark.integration

NOW = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)


class StubProvider(QRadarProvider):
    capabilities = frozenset({ProviderCapability.INVENTORY})

    def __init__(
        self,
        sources: list[LogSourceDTO],
        *,
        fail: bool = False,
        reachable: bool = True,
        info_error: ProviderError | None = None,
    ) -> None:
        self._sources = sources
        self._fail = fail
        self._reachable = reachable
        self._info_error = info_error
        self.calls = 0

    async def validate_connection(self) -> InstanceInfoDTO:
        return await self.get_instance_info()

    async def get_instance_info(self) -> InstanceInfoDTO:
        if self._info_error is not None:
            raise self._info_error
        return InstanceInfoDTO(
            version="7.6.0.0", build="b", reachable=self._reachable, raw_about={}
        )

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        return [LogSourceTypeDTO(type_id=7, name="Linux OS")]

    async def list_log_sources(self) -> list[LogSourceDTO]:
        self.calls += 1
        if self._fail:
            raise ProviderUnavailableError("upstream down")
        return self._sources

    # Inert: this stub drives log source collection only. A collector reaching
    # for offenses or rules would be a behaviour change worth failing on rather
    # than silently satisfying.
    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        raise AssertionError("log source collection must not fetch individual sources")

    async def list_offenses(self, **kwargs: object) -> list:
        raise AssertionError("log source collection must not fetch offenses")

    async def list_rules(self, **kwargs: object) -> list:
        raise AssertionError("log source collection must not fetch rules")

    async def aclose(self) -> None:
        return None


def dto(qradar_id: int, name: str = "src", **kw) -> LogSourceDTO:
    defaults = dict(
        qradar_id=qradar_id,
        name=name,
        description=None,
        type_id=7,
        type_name=None,
        protocol_type_id=None,
        enabled=True,
        status="SUCCESS",
        credibility=5,
        target_event_collector_id=None,
        last_event_time=NOW,
    )
    defaults.update(kw)
    return LogSourceDTO(**defaults)  # type: ignore[arg-type]


async def _count(session, instance) -> int:
    return await session.scalar(
        select(func.count()).select_from(LogSource).where(LogSource.instance_id == instance.id)
    )


async def _watermark(session, instance) -> CollectionWatermark | None:
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance.id,
            CollectionWatermark.collector == COLLECTOR_NAME,
        )
    )


class TestConvergence:
    async def test_first_run_creates(self, db_session) -> None:
        instance = await make_instance(db_session)
        provider = StubProvider([dto(1), dto(2)])
        report = await LogSourceCollector(db_session, provider).sync(instance)

        assert report.created == 2
        assert report.updated == 0
        assert await _count(db_session, instance) == 2

    async def test_repeat_runs_update_rather_than_duplicate(self, db_session) -> None:
        """The core idempotency property: N runs, one row per QRadar id."""
        instance = await make_instance(db_session)
        provider = StubProvider([dto(1), dto(2)])
        collector = LogSourceCollector(db_session, provider)

        await collector.sync(instance)
        second = await collector.sync(instance)
        third = await collector.sync(instance)

        assert second.created == 0 and second.updated == 2
        assert third.created == 0 and third.updated == 2
        assert await _count(db_session, instance) == 2

    async def test_qradar_owned_fields_refresh(self, db_session) -> None:
        instance = await make_instance(db_session)
        collector = LogSourceCollector(db_session, StubProvider([dto(1, name="old")]))
        await collector.sync(instance)

        await LogSourceCollector(
            db_session, StubProvider([dto(1, name="renamed", enabled=False)])
        ).sync(instance)

        row = await db_session.scalar(select(LogSource).where(LogSource.qradar_id == 1))
        assert row.name == "renamed"
        assert row.enabled is False

    async def test_type_name_is_resolved_from_the_type_list(self, db_session) -> None:
        instance = await make_instance(db_session)
        await LogSourceCollector(db_session, StubProvider([dto(1)])).sync(instance)

        row = await db_session.scalar(select(LogSource).where(LogSource.qradar_id == 1))
        assert row.type_name == "Linux OS"

    async def test_soc_owned_metadata_survives_a_sync(self, db_session) -> None:
        """An operator's classification must not be clobbered by a poll."""
        instance = await make_instance(db_session)
        collector = LogSourceCollector(db_session, StubProvider([dto(1)]))
        await collector.sync(instance)

        row = await db_session.scalar(select(LogSource).where(LogSource.qradar_id == 1))
        row.criticality = Criticality.CRITICAL
        row.owner = "soc-team"
        await db_session.flush()

        await collector.sync(instance)

        row = await db_session.scalar(select(LogSource).where(LogSource.qradar_id == 1))
        assert row.criticality == Criticality.CRITICAL
        assert row.owner == "soc-team"

    async def test_instances_are_isolated(self, db_session) -> None:
        a = await make_instance(db_session)
        b = await make_instance(db_session)
        await LogSourceCollector(db_session, StubProvider([dto(1)])).sync(a)
        await LogSourceCollector(db_session, StubProvider([dto(1)])).sync(b)

        # Same QRadar id on two consoles is two distinct log sources.
        assert await _count(db_session, a) == 1
        assert await _count(db_session, b) == 1


class TestWatermark:
    async def test_success_advances_the_watermark(self, db_session) -> None:
        instance = await make_instance(db_session)
        await LogSourceCollector(db_session, StubProvider([dto(1)])).sync(instance)

        wm = await _watermark(db_session, instance)
        assert wm is not None
        assert wm.intervals_collected == 1
        assert wm.consecutive_failures == 0
        assert wm.watermark_at is not None

    async def test_each_run_increments(self, db_session) -> None:
        instance = await make_instance(db_session)
        collector = LogSourceCollector(db_session, StubProvider([dto(1)]))
        await collector.sync(instance)
        await collector.sync(instance)

        assert (await _watermark(db_session, instance)).intervals_collected == 2

    async def test_failure_does_not_advance_the_watermark(self, db_session) -> None:
        """A failed pass must not read downstream as a completed observation."""
        instance = await make_instance(db_session)
        report = await LogSourceCollector(
            db_session, StubProvider([], fail=True)
        ).sync(instance)

        assert report.partial_failure is True
        wm = await _watermark(db_session, instance)
        assert wm.intervals_collected == 0
        assert wm.watermark_at is None
        assert wm.consecutive_failures == 1

    async def test_failures_accumulate_then_reset_on_success(self, db_session) -> None:
        instance = await make_instance(db_session)
        await LogSourceCollector(db_session, StubProvider([], fail=True)).sync(instance)
        await LogSourceCollector(db_session, StubProvider([], fail=True)).sync(instance)
        assert (await _watermark(db_session, instance)).consecutive_failures == 2

        await LogSourceCollector(db_session, StubProvider([dto(1)])).sync(instance)
        wm = await _watermark(db_session, instance)
        assert wm.consecutive_failures == 0
        assert wm.last_error is None

    async def test_failure_writes_no_log_sources(self, db_session) -> None:
        instance = await make_instance(db_session)
        await LogSourceCollector(db_session, StubProvider([], fail=True)).sync(instance)
        assert await _count(db_session, instance) == 0


class TestAdvisoryLock:
    async def test_a_held_lock_skips_the_run(self, db_schema) -> None:
        """Skipping beats queueing: the work is periodic, so a missed tick is
        cheaper than a pool of workers blocked on one console.

        Two real sessions are required — advisory locks are session-scoped, so
        one session would simply re-acquire its own lock.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from app.core.config import get_settings

        engine = create_async_engine(db_schema, future=True)
        maker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            async with maker() as session_a, maker() as session_b:
                instance = await make_instance(session_a)
                await session_a.commit()
                instance_b = await session_b.get(type(instance), instance.id)

                provider = StubProvider([dto(1)])
                async with CollectorAdvisoryLock(
                    session_a, get_settings(), instance.id, COLLECTOR_NAME
                ) as acquired:
                    assert acquired is True
                    report = await LogSourceCollector(session_b, provider).sync(instance_b)

                assert report.skipped_locked is True
                assert report.log_sources_seen == 0
                # Skipped before any upstream call: a skipped run must not cost
                # a request to QRadar.
                assert provider.calls == 0
        finally:
            await engine.dispose()

    async def test_the_lock_is_released_after_a_run(self, db_session) -> None:
        """A leaked lock would stall every subsequent tick for that console."""
        instance = await make_instance(db_session)
        collector = LogSourceCollector(db_session, StubProvider([dto(1)]))
        assert (await collector.sync(instance)).skipped_locked is False
        assert (await collector.sync(instance)).skipped_locked is False

    async def test_the_lock_is_released_after_a_failure(self, db_session) -> None:
        """The failure path returns early — it must still release the lock."""
        instance = await make_instance(db_session)
        await LogSourceCollector(db_session, StubProvider([], fail=True)).sync(instance)
        report = await LogSourceCollector(db_session, StubProvider([dto(1)])).sync(instance)
        assert report.skipped_locked is False
        assert report.created == 1


class TestInstanceHealthStamp:
    """The top bar reports `instance.status` and `instance_last_health_check_at`.

    Nothing used to write either on a failed check, so a console that went dark
    kept reading HEALTHY with no evidence of when it was last seen — the
    opposite of failing closed. A pass must stamp the time it checked, and a
    check that never got an answer must replace the stale verdict rather than
    leave it standing.
    """

    async def test_success_stamps_the_clock_and_clears_last_error(self, db_session) -> None:
        instance = await make_instance(db_session)
        instance.status = InstanceStatus.UNREACHABLE
        instance.last_error = "ProviderUnavailableError: upstream down"
        await db_session.flush()

        collector = LogSourceCollector(db_session, StubProvider([dto(1)]), clock=lambda: NOW)
        report = await collector.sync(instance)

        assert report.partial_failure is False
        assert instance.status == InstanceStatus.HEALTHY
        assert instance.last_health_check_at == NOW
        assert instance.last_error is None

    async def test_an_unreachable_console_is_recorded_as_unreachable(self, db_session) -> None:
        instance = await make_instance(db_session)

        collector = LogSourceCollector(
            db_session, StubProvider([dto(1)], reachable=False), clock=lambda: NOW
        )
        report = await collector.sync(instance)

        assert report.partial_failure is False
        assert instance.status == InstanceStatus.UNREACHABLE
        assert instance.last_health_check_at == NOW
        assert instance.last_error is None

    async def test_a_failed_check_records_why_and_when_and_persists(self, db_session) -> None:
        """The collector keeps its own failure handling; the instance must
        still carry the reason, and it must survive the collector's commit."""
        instance = await make_instance(db_session)
        message = "connection refused" * 64
        provider = StubProvider([], info_error=ProviderUnavailableError(message))

        report = await LogSourceCollector(db_session, provider, clock=lambda: NOW).sync(instance)

        assert report.partial_failure is True
        assert instance.status == InstanceStatus.UNREACHABLE
        assert instance.last_health_check_at == NOW
        # Class plus message, truncated — the same rule as `sanitized_error`.
        expected_error = f"ProviderUnavailableError: {message}"[:512]
        assert instance.last_error == expected_error
        assert len(instance.last_error) <= 512

        # Already flushed by the collector's failure path, so it rides the same
        # commit as the watermark update rather than waiting for one.
        assert db_session.is_modified(instance) is False

        # A failed pass is not an observation: the watermark stays where it was.
        wm = await _watermark(db_session, instance)
        assert wm.intervals_collected == 0
        assert wm.watermark_at is None
        assert wm.consecutive_failures == 1

        # The collector flushes on the failure path, so the instance change
        # commits with the watermark; a separate session must see both.
        await db_session.commit()
        maker = async_sessionmaker(db_session.bind, expire_on_commit=False)
        async with maker() as fresh:
            stored = await fresh.get(QRadarInstance, instance.id)
            assert stored is not None
            assert stored.status == InstanceStatus.UNREACHABLE
            assert stored.last_health_check_at == NOW
            assert stored.last_error == expected_error

    async def test_a_later_success_restores_healthy_and_clears_last_error(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        failing = LogSourceCollector(
            db_session,
            StubProvider([], info_error=ProviderUnavailableError("upstream down")),
            clock=lambda: NOW,
        )
        await failing.sync(instance)
        assert instance.status == InstanceStatus.UNREACHABLE
        assert instance.last_error is not None

        recovered_at = datetime(2026, 7, 20, 12, 5, tzinfo=UTC)
        report = await LogSourceCollector(
            db_session, StubProvider([dto(1)]), clock=lambda: recovered_at
        ).sync(instance)

        assert report.partial_failure is False
        assert instance.status == InstanceStatus.HEALTHY
        assert instance.last_health_check_at == recovered_at
        assert instance.last_error is None
