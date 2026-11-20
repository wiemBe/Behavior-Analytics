"""Inventory sync against a real database. Skips without TEST_DATABASE_URL."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models.enums import (
    AnomalyState,
    AnomalyType,
    EvidenceStatus,
    Severity,
    SourceClass,
)
from app.models.fleet import LogSourceClassification
from app.models.log_source import LogSource, LogSourceAnomaly
from app.providers.base import ProviderUnavailableError
from app.providers.mock import MockQRadarProvider
from app.repositories.log_source import LogSourceRepository
from app.services.inventory_sync import FAILURE_TYPES_UNAVAILABLE, InventorySyncService

from .factories import make_instance, make_log_source

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


async def test_sync_creates_then_updates(db_session) -> None:
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(db_session, provider)
    instance = await svc.ensure_default_instance()

    first = await svc.sync(instance)
    assert first.created > 0
    assert first.updated == 0
    assert first.log_sources_seen == first.created

    # Second sync is idempotent: nothing new created, everything updated.
    second = await svc.sync(instance)
    assert second.created == 0
    assert second.updated == first.created


async def test_sync_preserves_soc_metadata(db_session) -> None:
    provider = MockQRadarProvider(seed=1337)
    svc = InventorySyncService(db_session, provider)
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)

    repo = LogSourceRepository(db_session)
    sources = await repo.list()
    target = sources[0]
    target.owner = "soc-owner"
    target.criticality = "CRITICAL"
    target.maintenance_mode = True
    await db_session.flush()

    # A re-sync must not clobber operator-owned fields.
    await svc.sync(instance)
    await db_session.refresh(target)
    assert target.owner == "soc-owner"
    assert str(target.criticality) == "CRITICAL"
    assert target.maintenance_mode is True


async def test_sync_sanitizes_source_names(db_session) -> None:
    class XssProvider(MockQRadarProvider):
        async def list_log_sources(self):
            sources = await super().list_log_sources()
            first = sources[0].model_copy(update={"name": "<script>evil</script>fw-01"})
            return [first, *sources[1:]]

    svc = InventorySyncService(db_session, XssProvider())
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)

    repo = LogSourceRepository(db_session)
    names = [s.name for s in await repo.list()]
    assert all("<script>" not in n for n in names)


class _SlimTypesDownProvider(MockQRadarProvider):
    """The slim walk carries no type name; the type catalog can be made to fail.

    The internal source's identifier is not the loopback address, so its class comes
    from the stored type fact alone — the fact a types outage must not drop.
    """

    def __init__(self, *, seed: int = 1337) -> None:
        super().__init__(seed=seed)
        self.types_down = False

    async def list_log_sources(self):
        out = []
        for source in await super().list_log_sources():
            update = {"type_name": None}
            if source.internal:
                update["identifier"] = "health-console"
            out.append(source.model_copy(update=update))
        return out

    async def list_log_source_types(self):
        if self.types_down:
            raise ProviderUnavailableError("type catalog unavailable")
        return await super().list_log_source_types()


async def test_types_failure_keeps_stored_types_and_is_partial(db_session) -> None:
    provider = _SlimTypesDownProvider()
    svc = InventorySyncService(db_session, provider)
    instance = await svc.ensure_default_instance()

    first = await svc.sync(instance)
    assert first.complete is True
    await db_session.flush()

    internal = (
        await db_session.scalars(
            select(LogSource).where(
                LogSource.instance_id == instance.id, LogSource.type_id == 368
            )
        )
    ).one()
    internal_id = internal.id
    assert internal.type_name == "Health Metrics"

    provider.types_down = True
    result = await svc.sync(instance)
    await db_session.flush()

    assert result.complete is False
    assert result.failure_kind == FAILURE_TYPES_UNAVAILABLE
    assert result.log_sources_seen > 0

    rows = list(
        (
            await db_session.scalars(
                select(LogSource).where(LogSource.instance_id == instance.id)
            )
        ).all()
    )
    assert len(rows) == result.log_sources_seen

    # The walk carried no type name and the catalog failed: the stored one survives.
    await db_session.refresh(internal)
    assert internal.type_name == "Health Metrics"
    # Classified from the stored catalog, not from an empty one.
    classification = await db_session.get(LogSourceClassification, internal_id)
    assert classification is not None
    assert classification.source_class == SourceClass.QRADAR_INTERNAL


async def test_collector_reports_partial_on_types_failure(db_session) -> None:
    from app.collectors.log_source_collector import RUN_PARTIAL, LogSourceCollector

    provider = _SlimTypesDownProvider()
    svc = InventorySyncService(db_session, provider)
    instance = await svc.ensure_default_instance()
    await svc.sync(instance)
    await db_session.flush()

    provider.types_down = True
    report = await LogSourceCollector(db_session, provider).sync(instance)

    assert report.status == RUN_PARTIAL
    assert report.failure_kind == FAILURE_TYPES_UNAVAILABLE
    assert report.log_sources_seen > 0


class _GroupsDownProvider(MockQRadarProvider):
    async def list_log_source_groups(self):
        raise ProviderUnavailableError("group catalog unavailable")


async def test_groups_failure_stays_complete_and_upserts(db_session) -> None:
    svc = InventorySyncService(db_session, _GroupsDownProvider(seed=1337))
    instance = await svc.ensure_default_instance()

    result = await svc.sync(instance)
    await db_session.flush()

    # A groups failure is not part of the pass's completeness (unchanged behaviour).
    assert result.complete is True
    assert result.failure_kind is None
    assert result.log_sources_seen > 0
    rows = list(
        (
            await db_session.scalars(
                select(LogSource).where(LogSource.instance_id == instance.id)
            )
        ).all()
    )
    assert len(rows) == result.log_sources_seen


async def test_types_and_walk_succeed_is_not_partial(db_session) -> None:
    svc = InventorySyncService(db_session, MockQRadarProvider(seed=1337))
    instance = await svc.ensure_default_instance()

    result = await svc.sync(instance)

    assert result.complete is True
    assert result.failure_kind is None
    assert result.log_sources_seen > 0


class TestActiveAnomalyPredicate:
    """An incident is active by lifecycle state, never by `resolved_at IS NULL`.

    A CANDIDATE that returns to normal before opening never opened, so it is
    never resolved and keeps `resolved_at` NULL permanently. Counting that row
    as active reports a live incident that no longer exists -- observed against
    the lab appliance on 2026-08-02, where an idle gap between scenarios left
    exactly such a row on a source with no live incident.
    """

    @staticmethod
    def _anomaly(src, state: AnomalyState, **kwargs) -> LogSourceAnomaly:
        return LogSourceAnomaly(
            log_source_id=src.id,
            anomaly_type=AnomalyType.VOLUME_DROP,
            severity=Severity.MEDIUM,
            state=state,
            detected_at=datetime(2026, 8, 2, 20, 18, tzinfo=UTC),
            anomaly_start=datetime(2026, 8, 2, 20, 18, tzinfo=UTC),
            consecutive_buckets=1,
            policy_version=1,
            evidence_status=EvidenceStatus.NOT_REQUESTED,
            **kwargs,
        )

    @pytest.mark.asyncio
    async def test_unopened_candidate_returned_to_normal_is_not_active(
        self, db_session
    ) -> None:
        instance = await make_instance(db_session)
        src = await make_log_source(db_session, instance, qradar_id=9001, name="idle")
        # state NORMAL, resolved_at NULL: it never opened, so it never resolved.
        db_session.add(self._anomaly(src, AnomalyState.NORMAL))
        await db_session.flush()

        repo = LogSourceRepository(db_session)

        assert await repo.open_anomaly_counts() == {}
        assert await repo.open_anomalies_for(src.id) == []
        assert await repo.anomalous_source_count() == 0

    @pytest.mark.asyncio
    async def test_live_states_are_active(self, db_session) -> None:
        instance = await make_instance(db_session)
        repo = LogSourceRepository(db_session)

        for offset, state in enumerate(
            (AnomalyState.CANDIDATE, AnomalyState.OPEN, AnomalyState.RECOVERING)
        ):
            src = await make_log_source(
                db_session, instance, qradar_id=9100 + offset, name=f"live-{offset}"
            )
            db_session.add(self._anomaly(src, state))
            await db_session.flush()

            assert await repo.open_anomaly_counts() == {src.id: 1}, state
            assert len(await repo.open_anomalies_for(src.id)) == 1, state
            assert await repo.anomalous_source_count() == 1, state

            await db_session.delete(
                (await repo.open_anomalies_for(src.id))[0]
            )
            await db_session.flush()

    @pytest.mark.asyncio
    async def test_resolved_incident_is_not_active(self, db_session) -> None:
        instance = await make_instance(db_session)
        src = await make_log_source(db_session, instance, qradar_id=9200, name="done")
        db_session.add(
            self._anomaly(
                src,
                AnomalyState.RESOLVED,
                resolved_at=datetime(2026, 8, 2, 20, 25, tzinfo=UTC),
            )
        )
        await db_session.flush()

        repo = LogSourceRepository(db_session)
        assert await repo.open_anomaly_counts() == {}
        assert await repo.anomalous_source_count() == 0
