"""Inventory sync: pull log sources from a provider and upsert them.

Only QRadar-owned fields are ever overwritten. SOC-owned metadata (criticality,
owner, maintenance, thresholds) is never touched by a sync — clobbering an
operator's classification with each poll would make the platform unusable.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.fingerprint import inventory_fingerprint
from app.alerts.service import AlertInput, AlertService
from app.core.config import Settings, get_settings
from app.fleet.service import classify_sources
from app.models.enums import ClassificationReviewStatus, InstanceStatus, Severity
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.providers.base import ProviderError, QRadarProvider
from app.repositories.log_source import LogSourceRepository
from app.schemas.log_source import SyncResult
from app.security.sanitizer import sanitize_text


class InventorySyncService:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.repo = LogSourceRepository(session)
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def sync(self, instance: QRadarInstance) -> SyncResult:
        info = await self.provider.get_instance_info()
        instance.qradar_version = info.version
        instance.status = (
            InstanceStatus.HEALTHY if info.reachable else InstanceStatus.UNREACHABLE
        )

        now = self._clock()
        type_dtos = await self.provider.list_log_source_types()
        type_names = {t.type_id: t.name for t in type_dtos}
        try:
            group_dtos = await self.provider.list_log_source_groups()
        except ProviderError:
            group_dtos = []
        dtos = await self.provider.list_log_sources()

        type_rows = [
            LogSourceType(
                instance_id=instance.id,
                type_id=dto.type_id,
                name=sanitize_text(dto.name) or dto.name,
                internal=dto.internal,
                custom=dto.custom,
                synced_at=now,
            )
            for dto in type_dtos
        ]
        if type_rows:
            type_statement = pg_insert(LogSourceType).values(
                [
                    {
                        "instance_id": row.instance_id,
                        "type_id": row.type_id,
                        "name": row.name,
                        "internal": row.internal,
                        "custom": row.custom,
                        "synced_at": row.synced_at,
                    }
                    for row in type_rows
                ]
            )
            type_statement = type_statement.on_conflict_do_update(
                index_elements=[LogSourceType.instance_id, LogSourceType.type_id],
                set_={
                    "name": type_statement.excluded.name,
                    "internal": type_statement.excluded.internal,
                    "custom": type_statement.excluded.custom,
                    "synced_at": type_statement.excluded.synced_at,
                },
            )
            await self.session.execute(type_statement)

        group_rows = [
            LogSourceGroup(
                instance_id=instance.id,
                qradar_id=dto.qradar_id,
                name=sanitize_text(dto.name) or dto.name,
                description=sanitize_text(dto.description),
                parent_qradar_id=dto.parent_qradar_id,
                assignable=dto.assignable,
                synced_at=now,
            )
            for dto in group_dtos
        ]
        if group_rows:
            group_statement = pg_insert(LogSourceGroup).values(
                [
                    {
                        "instance_id": row.instance_id,
                        "qradar_id": row.qradar_id,
                        "name": row.name,
                        "description": row.description,
                        "parent_qradar_id": row.parent_qradar_id,
                        "assignable": row.assignable,
                        "synced_at": row.synced_at,
                    }
                    for row in group_rows
                ]
            )
            group_statement = group_statement.on_conflict_do_update(
                index_elements=[LogSourceGroup.instance_id, LogSourceGroup.qradar_id],
                set_={
                    "name": group_statement.excluded.name,
                    "description": group_statement.excluded.description,
                    "parent_qradar_id": group_statement.excluded.parent_qradar_id,
                    "assignable": group_statement.excluded.assignable,
                    "synced_at": group_statement.excluded.synced_at,
                },
            )
            await self.session.execute(group_statement)

        existing_by_qradar_id = await self.repo.by_qradar_id(instance.id)
        bootstrap = not existing_by_qradar_id
        created = updated = 0
        new_sources: list[LogSource] = []
        for dto in dtos:
            existing = existing_by_qradar_id.get(dto.qradar_id)
            resolved_type = dto.type_name or type_names.get(dto.type_id or -1)
            if existing is None:
                existing = LogSource(
                    instance_id=instance.id,
                    qradar_id=dto.qradar_id,
                    first_seen_at=now,
                )
                self.session.add(existing)
                existing_by_qradar_id[dto.qradar_id] = existing
                new_sources.append(existing)
                created += 1
            else:
                updated += 1

            # QRadar-owned fields only. Classification overrides and review
            # state live in their own table and are never assigned here.
            existing.name = sanitize_text(dto.name) or dto.name
            existing.description = sanitize_text(dto.description)
            existing.type_id = dto.type_id
            existing.type_name = sanitize_text(resolved_type)
            existing.protocol_type_id = dto.protocol_type_id
            existing.enabled = dto.enabled
            existing.qradar_status = dto.status
            existing.credibility = dto.credibility
            existing.target_event_collector_id = dto.target_event_collector_id
            existing.last_event_time = dto.last_event_time
            existing.qradar_created_at = dto.qradar_created_at
            existing.qradar_modified_at = dto.qradar_modified_at
            existing.auto_discovered = dto.auto_discovered
            existing.group_ids = dto.group_ids
            existing.identifier = sanitize_text(dto.identifier)
            existing.is_internal = dto.internal

        await self.session.flush()
        sources = list(existing_by_qradar_id.values())
        applied = await classify_sources(
            self.session,
            instance_id=instance.id,
            sources=sources,
            types=type_rows,
            groups=group_rows,
            new_source_ids={source.id for source in new_sources},
            bootstrap=bootstrap,
            classified_at=now,
        )

        alert_inputs = []
        if self.settings.inventory_new_source_alerts_enabled:
            for source in new_sources:
                classification = applied[source.id]
                if classification.review_status != ClassificationReviewStatus.NEW:
                    continue
                result = classification.result
                alert_inputs.append(
                    AlertInput(
                        fingerprint=inventory_fingerprint(instance.id, source.qradar_id),
                        title=f"New log source: {source.name}",
                        severity=Severity.LOW,
                        source_type="log_source",
                        source_id=source.id,
                        description="A new non-internal log source requires SOC review.",
                        evidence={
                            "qradar_id": source.qradar_id,
                            "first_seen_at": source.first_seen_at.isoformat(),
                        },
                        context={
                            "class": result.source_class.value,
                            "source_class": result.source_class.value,
                            "basis": result.basis,
                            "type": source.type_name,
                            "auto_discovered": source.auto_discovered,
                            "identifier": source.identifier,
                            "owner": source.owner,
                            "instance": instance.name,
                        },
                    )
                )
        if alert_inputs:
            await AlertService(self.session, clock=self._clock).apply_bulk(alert_inputs, [])

        await self.session.flush()
        return SyncResult(
            provider=type(self.provider).__name__,
            log_sources_seen=len(dtos),
            created=created,
            updated=updated,
            instance_version=info.version,
        )

    async def ensure_default_instance(self) -> QRadarInstance:
        """Return the singleton default instance, creating it if absent.

        In the MVP there is one monitored QRadar; multi-instance support exists
        in the schema but the API defaults to this one.
        """
        from sqlalchemy import select

        instance = await self.session.scalar(select(QRadarInstance).limit(1))
        if instance is not None:
            return instance
        instance = QRadarInstance(
            id=uuid.uuid4(),
            name="default",
            console_host="mock",
            provider_kind="mock",
        )
        self.session.add(instance)
        await self.session.flush()
        return instance
