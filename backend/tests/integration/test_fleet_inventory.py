"""Phase C inventory enrichment, classification, and new-source alerts."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime

import pytest
from sqlalchemy import event, func, select

from app.core.config import Settings
from app.fleet.seed_rules import TYPE_NAME_RULES
from app.models.alert import Alert
from app.models.enums import ClassificationReviewStatus, SourceClass
from app.models.fleet import (
    ClassificationRule,
    LogSourceClassification,
    LogSourceGroup,
    LogSourceType,
)
from app.models.log_source import LogSource
from app.providers.base import ProviderCapability, ProviderUnavailableError, QRadarProvider
from app.providers.dto import (
    InstanceInfoDTO,
    LogSourceDTO,
    LogSourceGroupDTO,
    LogSourceTypeDTO,
)
from app.services.inventory_sync import InventorySyncService
from tests.integration.factories import make_instance

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

NOW = datetime(2026, 10, 12, 10, 0, tzinfo=UTC)


def source(qradar_id: int, name: str, *, type_id: int = 2, **kwargs) -> LogSourceDTO:
    return LogSourceDTO(
        qradar_id=qradar_id,
        name=name,
        type_id=type_id,
        identifier=kwargs.pop("identifier", f"host-{qradar_id}"),
        group_ids=kwargs.pop("group_ids", [20]),
        qradar_created_at=NOW,
        qradar_modified_at=NOW,
        **kwargs,
    )


class FleetProvider(QRadarProvider):
    capabilities = frozenset(
        {ProviderCapability.INVENTORY, ProviderCapability.LOG_SOURCE_GROUPS}
    )

    def __init__(self, sources: list[LogSourceDTO], *, groups_fail: bool = False) -> None:
        self.sources = sources
        self.groups_fail = groups_fail
        self.type_name = "Linux OS"
        self.group_name = "DMZ"

    async def get_instance_info(self) -> InstanceInfoDTO:
        return InstanceInfoDTO(version="7.6.0", reachable=True)

    async def list_log_sources(self) -> list[LogSourceDTO]:
        return self.sources

    async def get_log_source(self, qradar_id: int) -> LogSourceDTO | None:
        raise AssertionError("bulk sync must not fetch individual sources")

    async def list_log_source_types(self) -> list[LogSourceTypeDTO]:
        return [
            LogSourceTypeDTO(type_id=2, name=self.type_name),
            LogSourceTypeDTO(type_id=368, name="Health Metrics", internal=True),
        ]

    async def list_log_source_groups(self) -> list[LogSourceGroupDTO]:
        if self.groups_fail:
            raise ProviderUnavailableError("groups unavailable")
        return [
            LogSourceGroupDTO(qradar_id=10, name="Production", parent_qradar_id=0),
            LogSourceGroupDTO(
                qradar_id=20,
                name=self.group_name,
                parent_qradar_id=10,
                assignable=True,
            ),
        ]

    async def list_rules(self) -> list:
        raise AssertionError("inventory sync must not fetch rules")

    async def list_offenses(self, **kwargs: object) -> list:
        raise AssertionError("inventory sync must not fetch offenses")


async def classifications(session) -> list[LogSourceClassification]:
    return list((await session.scalars(select(LogSourceClassification))).all())


async def alert_count(session) -> int:
    return await session.scalar(select(func.count()).select_from(Alert)) or 0


async def test_first_sync_bootstraps_reviewed_without_alerts(db_session) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider(
        [source(1, "linux-01"), source(2, "Health Metrics", type_id=368)]
    )

    result = await InventorySyncService(db_session, provider, clock=lambda: NOW).sync(instance)

    assert result.created == 2
    rows = await classifications(db_session)
    assert {ClassificationReviewStatus(row.review_status) for row in rows} == {
        ClassificationReviewStatus.REVIEWED
    }
    assert await alert_count(db_session) == 0


async def test_second_sync_opens_exactly_one_new_source_alert_and_resync_is_idempotent(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")])
    service = InventorySyncService(db_session, provider, clock=lambda: NOW)
    await service.sync(instance)

    provider.sources.append(
        source(2, "dmz-waf-01", type_id=212, auto_discovered=True, identifier="waf-01")
    )
    await service.sync(instance)

    row = await db_session.scalar(
        select(LogSourceClassification)
        .join(LogSource)
        .where(LogSource.qradar_id == 2)
    )
    assert row.review_status == ClassificationReviewStatus.NEW
    assert row.source_class == SourceClass.WAF
    assert await alert_count(db_session) == 1

    await service.sync(instance)
    assert await alert_count(db_session) == 1
    alert = await db_session.scalar(select(Alert))
    assert alert.occurrence_count == 1


async def test_internal_source_is_never_new(db_session) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")])
    service = InventorySyncService(db_session, provider, clock=lambda: NOW)
    await service.sync(instance)
    provider.sources.append(
        source(2, "Health Metrics", type_id=368, identifier="metrics-host", internal=False)
    )

    await service.sync(instance)

    row = await db_session.scalar(
        select(LogSourceClassification)
        .join(LogSource)
        .where(LogSource.qradar_id == 2)
    )
    assert row.source_class == SourceClass.QRADAR_INTERNAL
    assert row.review_status == ClassificationReviewStatus.REVIEWED
    assert await alert_count(db_session) == 0


async def test_alert_gate_keeps_new_review_state_without_opening_alert(db_session) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")])
    settings = Settings(inventory_new_source_alerts_enabled=False)
    service = InventorySyncService(
        db_session, provider, settings=settings, clock=lambda: NOW
    )
    await service.sync(instance)
    provider.sources.append(source(2, "windows-01", type_id=13))

    await service.sync(instance)

    row = await db_session.scalar(
        select(LogSourceClassification)
        .join(LogSource)
        .where(LogSource.qradar_id == 2)
    )
    assert row.review_status == ClassificationReviewStatus.NEW
    assert await alert_count(db_session) == 0


async def test_override_survives_and_wins_after_resync(db_session) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")])
    service = InventorySyncService(db_session, provider, clock=lambda: NOW)
    await service.sync(instance)
    row = (await classifications(db_session))[0]
    row.override_class = SourceClass.WAF
    row.override_by = "analyst@example.test"
    await db_session.flush()

    await service.sync(instance)
    await db_session.refresh(row)

    assert row.override_class == SourceClass.WAF
    assert row.override_by == "analyst@example.test"
    assert row.source_class == SourceClass.WAF
    assert row.basis == "OVERRIDE"


async def test_seed_sync_updates_builtin_definition_but_preserves_disabled_state(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")])
    service = InventorySyncService(db_session, provider, clock=lambda: NOW)
    await service.sync(instance)
    expected = TYPE_NAME_RULES[0]
    row = await db_session.get(ClassificationRule, expected.id)
    row.pattern = r"\bstale-pattern\b"
    row.enabled = False
    await db_session.flush()

    await service.sync(instance)
    await db_session.refresh(row)

    assert row.pattern == expected.pattern
    assert row.kind == expected.kind
    assert row.target_class == expected.target_class
    assert row.priority == expected.priority
    assert row.description == expected.description
    assert row.enabled is False


async def test_upgrade_sync_reviews_existing_sources_and_alerts_only_genuinely_new_source(
    db_session,
) -> None:
    instance = await make_instance(db_session)
    pre_existing = [
        LogSource(instance_id=instance.id, qradar_id=1, name="old-linux-01"),
        LogSource(instance_id=instance.id, qradar_id=2, name="old-linux-02"),
    ]
    db_session.add_all(pre_existing)
    await db_session.flush()
    assert await db_session.scalar(select(func.count()).select_from(LogSourceClassification)) == 0
    provider = FleetProvider(
        [source(1, "linux-01"), source(2, "linux-02"), source(3, "linux-03")]
    )

    result = await InventorySyncService(db_session, provider, clock=lambda: NOW).sync(instance)

    rows = (
        await db_session.execute(
            select(LogSource.qradar_id, LogSourceClassification.review_status)
            .join(LogSourceClassification)
            .order_by(LogSource.qradar_id)
        )
    ).all()
    assert result.created == 1
    assert result.updated == 2
    assert rows == [
        (1, ClassificationReviewStatus.REVIEWED),
        (2, ClassificationReviewStatus.REVIEWED),
        (3, ClassificationReviewStatus.NEW),
    ]
    alert = await db_session.scalar(select(Alert))
    assert await alert_count(db_session) == 1
    assert alert.source_id == next(
        row.id
        for row in await db_session.scalars(select(LogSource).where(LogSource.qradar_id == 3))
    )


async def test_types_and_groups_are_upserted(db_session) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")])
    service = InventorySyncService(db_session, provider, clock=lambda: NOW)
    await service.sync(instance)
    provider.type_name = "Linux Operating System"
    provider.group_name = "Perimeter"

    await service.sync(instance)

    source_type = await db_session.get(LogSourceType, (instance.id, 2))
    group = await db_session.get(LogSourceGroup, (instance.id, 20))
    assert source_type.name == "Linux Operating System"
    assert group.name == "Perimeter"


async def test_group_failure_does_not_fail_inventory_sync(db_session) -> None:
    instance = await make_instance(db_session)
    provider = FleetProvider([source(1, "linux-01")], groups_fail=True)

    result = await InventorySyncService(db_session, provider, clock=lambda: NOW).sync(instance)

    assert result.created == 1
    assert await db_session.scalar(select(func.count()).select_from(LogSourceGroup)) == 0
    assert len(await classifications(db_session)) == 1


@contextmanager
def counting(session):  # type: ignore[no-untyped-def]
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):  # type: ignore[no-untyped-def]
        statements.append(statement)

    engine = session.bind.sync_engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", record)


async def test_sync_statement_count_is_constant_at_50_and_1000_sources(db_session) -> None:
    counts: dict[int, int] = {}
    for size in (50, 1000):
        instance = await make_instance(db_session)
        provider = FleetProvider([source(index, f"linux-{index}") for index in range(1, size + 1)])
        with counting(db_session) as statements:
            await InventorySyncService(db_session, provider, clock=lambda: NOW).sync(instance)
        counts[size] = len(statements)

    # SQLAlchemy's insertmanyvalues page is 1,000 bind rows; the larger source
    # flush crosses that page once. No statement is issued per source.
    assert counts == {50: 9, 1000: 10}
