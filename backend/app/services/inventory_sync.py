"""Inventory sync: pull log sources from a provider and upsert them.

Only QRadar-owned fields are ever overwritten. SOC-owned metadata (criticality,
owner, maintenance, thresholds) is never touched by a sync — clobbering an
operator's classification with each poll would make the platform unusable.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import ARRAY, String, Table, any_, bindparam, delete, literal, or_, select
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import ScalarResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Bundle
from sqlalchemy.sql import Select

from app.alerts.fingerprint import inventory_fingerprint
from app.alerts.service import AlertInput, AlertService
from app.core.config import Settings, get_settings
from app.fleet.service import classify_sources
from app.i18n.messages import MessageRef, render
from app.log_source_policy import source_pause_enabled
from app.models.entity import EntityMention
from app.models.enums import ClassificationReviewStatus, InstanceStatus, Severity
from app.models.fleet import LogSourceGroup, LogSourceType
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.log_source_pause import LogSourcePause
from app.providers.base import ProviderError, QRadarProvider, failure_kind
from app.repositories.log_source import LogSourceRepository
from app.schemas.log_source import SyncResult
from app.security.sanitizer import sanitize_text
from app.services.entity_mention_folds import log_source_mentions
from app.services.entity_mentions import record_mentions
from app.services.source_incident_closure import close_for_disabled_sources
from app.services.source_pause_spans import close_pause, disable_edge_start, open_pause
from app.services.waf_pipeline import sanitized_error

logger = logging.getLogger("app.services.inventory_sync")

#: The PARTIAL reason when the type catalog could not be read. A fixed code, not the
#: provider failure kind: the collector passes `failed_page=None` for a types failure and
#: for a source-page failure alike, so the reason must name which one it was.
FAILURE_TYPES_UNAVAILABLE = "types_unavailable"
QRADAR_DISABLED = "QRADAR_DISABLED"
ABSENT = "ABSENT"


@dataclass(frozen=True)
class AbsenceEvaluation:
    candidates: frozenset[uuid.UUID]
    confirmed: frozenset[uuid.UUID]
    state: str
    reason: str
    held_count: int = 0

    def summary(self) -> dict[str, str | int]:
        return {
            "state": self.state, "reason": self.reason,
            "candidates": len(self.candidates), "confirmed": len(self.confirmed),
            "held_count": self.held_count,
        }


def evaluate_absence(
    stored: set[uuid.UUID], present: set[uuid.UUID], previous: set[uuid.UUID],
    *, complete: bool, row_count: int, previous_row_count: int,
    validated: bool, rows_dropped: int = 0, duplicate_ids: int = 0,
    accept_armed: bool = False,
) -> AbsenceEvaluation:
    """Evaluate inventory evidence only; never write a span or infer physical deletion."""
    retained = previous - present
    reason = None
    if not complete:
        reason = "incomplete_snapshot"
    elif not validated:
        reason = "walk_not_validated"
    elif rows_dropped:
        reason = "rows_dropped"
    elif duplicate_ids:
        reason = "duplicate_ids"
    elif row_count == 0:
        reason = "empty_snapshot"
    if reason is not None:
        return AbsenceEvaluation(frozenset(retained), frozenset(), "skipped", reason)
    missing = stored - present
    confirmed = missing & retained
    candidates = missing - confirmed
    # Integer ceiling avoids float rounding at the 2% threshold.
    threshold = max(20, (previous_row_count + 49) // 50)
    if not accept_armed and len(confirmed) > threshold:
        return AbsenceEvaluation(
            frozenset(missing), frozenset(), "held", "mass_absence_held", len(confirmed)
        )
    if not accept_armed and len(candidates) > threshold:
        return AbsenceEvaluation(
            frozenset(), frozenset(), "held", "mass_absence_held", len(candidates)
        )
    return AbsenceEvaluation(frozenset(candidates), frozenset(confirmed), "evaluated", "complete")


def absence_candidates(metadata: dict) -> set[uuid.UUID]:
    """Decode internal watermark IDs, discarding invalid entries without logging values."""
    raw = metadata.get("absence_candidates", [])
    if not isinstance(raw, list):
        return set()
    result = set()
    for value in raw:
        try:
            result.add(uuid.UUID(value))
        except (ValueError, TypeError, AttributeError):
            continue
    return result


def absence_source_lock_statement(ids: set[uuid.UUID]) -> Select[uuid.UUID]:
    """One array-bound, ordered parent lock for the whole D-107 mutation superset."""
    return (
        select(LogSource.id)
        .where(LogSource.id == any_(bindparam(
            "absence_lock_ids", value=sorted(ids), type_=ARRAY(PgUUID(as_uuid=True)),
        )))
        .order_by(LogSource.id)
        .with_for_update(key_share=True)
    )


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
        self.absence: AbsenceEvaluation | None = None

    async def sync(
        self,
        instance: QRadarInstance,
        *,
        bootstrap_hint: bool = False,
        previous_complete_watermark: datetime | None = None,
        previous_inventory_metadata: dict | None = None,
    ) -> SyncResult:
        # Read once, so the health stamp and every row written by this pass
        # carry the same observation time.
        self.absence = None
        now = self._clock()
        try:
            info = await self.provider.get_instance_info()
        except ProviderError as exc:
            # Fail closed: an unreachable console is recorded on the instance
            # itself, so nothing downstream keeps reading a stale HEALTHY. The
            # error is re-raised untouched -- the caller owns its failure
            # handling, report and watermark -- and it is flushed by that
            # caller's session, so it commits with the watermark update.
            instance.status = InstanceStatus.UNREACHABLE
            instance.last_health_check_at = now
            instance.last_error = sanitized_error(exc)
            raise
        instance.qradar_version = info.version
        instance.status = (
            InstanceStatus.HEALTHY if info.reachable else InstanceStatus.UNREACHABLE
        )
        instance.last_health_check_at = now
        instance.last_error = None

        types_failed_kind: str | None = None
        try:
            type_dtos = await self.provider.list_log_source_types()
        except ProviderError as exc:
            type_dtos = []
            types_failed_kind = failure_kind(exc)
            logger.warning(
                "log source types could not be read; keeping the stored catalog",
                extra={
                    "instance_id": str(instance.id),
                    "failure_kind": types_failed_kind,
                    "error_class": type(exc).__name__,
                },
            )
        type_names = {t.type_id: t.name for t in type_dtos}
        try:
            group_dtos = await self.provider.list_log_source_groups()
        except ProviderError:
            group_dtos = []
        walk = await self.provider.walk_log_sources(
            page_timeout_seconds=self.settings.log_source_page_timeout_seconds,
            page_attempts=self.settings.log_source_page_attempts,
            wall_seconds=float(self.settings.log_source_walk_max_seconds),
        )
        dtos = walk.sources

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
            type_values = [
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
            type_statement = pg_insert(cast(Table, LogSourceType.__table__))
            type_statement = type_statement.on_conflict_do_update(
                index_elements=[LogSourceType.instance_id, LogSourceType.type_id],
                set_={
                    "name": type_statement.excluded.name,
                    "internal": type_statement.excluded.internal,
                    "custom": type_statement.excluded.custom,
                    "synced_at": type_statement.excluded.synced_at,
                },
            )
            await self.session.execute(type_statement, type_values)

        if types_failed_kind is not None:
            # Fail closed: classification reads the type facts. An empty list would strip
            # every source of them and could reclassify an internal-type source during an
            # outage of one small endpoint, so classify from the catalog already stored.
            type_rows = list(
                (
                    await self.session.scalars(
                        select(LogSourceType).where(
                            LogSourceType.instance_id == instance.id
                        )
                    )
                ).all()
            )

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
            group_values = [
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
            group_statement = pg_insert(cast(Table, LogSourceGroup.__table__))
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
            await self.session.execute(group_statement, group_values)

        existing_by_qradar_id = await self.repo.by_qradar_id(instance.id)
        pause_policy = source_pause_enabled(self.settings)
        open_ids: set[uuid.UUID] = set()
        absent_ids: set[uuid.UUID] = set()
        reappeared: set[uuid.UUID] = set()
        if pause_policy:
            stored_enabled = {qid: source.enabled for qid, source in existing_by_qradar_id.items()}
            final_enabled = {dto.qradar_id: dto.enabled for dto in dtos}
            reconcile_ids = {
                source.id for qid, enabled in final_enabled.items()
                if (enabled is True or enabled is False)
                and (source := existing_by_qradar_id.get(qid)) is not None
            }
            stored_ids = {source.id for source in existing_by_qradar_id.values()}
            if stored_ids:
                rows: ScalarResult[tuple[uuid.UUID, str]] = await self.session.scalars(
                    select(Bundle(
                        "open_pause", LogSourcePause.log_source_id, LogSourcePause.reason,
                    )).where(
                        LogSourcePause.log_source_id == any_(bindparam(
                            "stored_ids", value=sorted(stored_ids),
                            type_=ARRAY(PgUUID(as_uuid=True)),
                        )),
                        or_(
                            LogSourcePause.reason == ABSENT,
                            (LogSourcePause.reason == QRADAR_DISABLED)
                            & (LogSourcePause.log_source_id == any_(bindparam(
                                "reconcile_ids", value=sorted(reconcile_ids),
                                type_=ARRAY(PgUUID(as_uuid=True)),
                            ))),
                        ),
                        LogSourcePause.ends_at.is_(None),
                    )
                )
                for source_id, reason in rows:
                    if reason == ABSENT:
                        absent_ids.add(source_id)
                    else:
                        open_ids.add(source_id)
            metadata = previous_inventory_metadata or {}
            previous_count = metadata.get("absence_complete_rows")
            if previous_count is None:
                previous_run = metadata.get("inventory_pass")
                if isinstance(previous_run, dict) and previous_run.get("status") == "COMPLETE":
                    previous_count = previous_run.get("seen")
            if type(previous_count) is not int or previous_count < 0:
                previous_count = 0
            present_ids = {
                source.id for dto in dtos
                if (source := existing_by_qradar_id.get(dto.qradar_id)) is not None
            }
            self.absence = evaluate_absence(
                stored_ids - absent_ids, present_ids,
                absence_candidates(metadata) - absent_ids,
                complete=walk.complete and types_failed_kind is None,
                row_count=len(dtos), previous_row_count=previous_count,
                validated=walk.validated, rows_dropped=walk.rows_dropped,
                duplicate_ids=walk.duplicate_ids,
                accept_armed=isinstance(metadata.get("absence_accept"), dict),
            )
            reappeared = present_ids & absent_ids
        disabled: list[LogSource] = []
        reenabled: list[LogSource] = []
        # `bootstrap_hint`: no complete pass has run yet, so a first partial pass must not
        # make the rest of the console look new when a later pass reaches it.
        bootstrap = not existing_by_qradar_id or bootstrap_hint
        created = updated = 0
        new_sources: list[LogSource] = []
        absence_edge = bool(reappeared or (self.absence and self.absence.confirmed))
        with self.session.no_autoflush if absence_edge else nullcontext():
            for dto in dtos:
                existing = existing_by_qradar_id.get(dto.qradar_id)
                resolved_type = dto.type_name or type_names.get(dto.type_id or -1)
                if existing is None:
                    existing = LogSource(
                        instance_id=instance.id,
                        qradar_id=dto.qradar_id,
                        first_seen_at=now,
                        enabled=False,
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
                # Fail closed: a type name that cannot be resolved (types endpoint down, or
                # no matching type row) keeps whatever was stored rather than being guessed.
                if resolved_type is not None:
                    existing.type_id = dto.type_id
                    existing.type_name = sanitize_text(resolved_type)
                existing.protocol_type_id = dto.protocol_type_id
                if dto.enabled is not None:
                    existing.enabled = dto.enabled
                elif not source_pause_enabled(self.settings):
                    existing.enabled = True
                else:
                    existing.enabled = stored_enabled.get(dto.qradar_id, False)
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

            if pause_policy:
                # Reconcile the final DTO with stored flags and open spans; unknown stays unchanged.
                for qid, enabled in final_enabled.items():
                    source = existing_by_qradar_id[qid]
                    if enabled is False and (stored_enabled.get(qid) or source.id not in open_ids):
                        disabled.append(source)
                    elif enabled is True and (
                        stored_enabled.get(qid) is False or source.id in open_ids
                    ):
                        reenabled.append(source)

            if absence_edge:
                # D-107: one globally ordered superset, before ANY parent UPDATE.
                # Dirty present sources include disable/re-enable attribute changes too.
                changed = {
                    source.id for source in self.session.dirty
                    if isinstance(source, LogSource)
                    and self.session.is_modified(source, include_collections=False)
                }
                assert self.absence is not None
                await self.session.execute(absence_source_lock_statement(
                    changed | reappeared | set(self.absence.confirmed)
                ))
        await self.session.flush()
        # Edge passes already own the complete ordered parent-lock superset.
        # Source attributes are never assigned after this point.
        if disabled:
            by_start: dict[datetime, list[uuid.UUID]] = {}
            for source in disabled:
                start = disable_edge_start(previous_complete_watermark, source.first_seen_at, now)
                by_start.setdefault(start, []).append(source.id)
            for start, ids in by_start.items():
                await open_pause(
                    self.session, sorted(ids), reason=QRADAR_DISABLED, starts_at=start, now=now
                )
            await close_for_disabled_sources(
                self.session, sorted(source.id for source in disabled), observed_at=now
            )
        if reenabled:
            await close_pause(
                self.session, sorted(source.id for source in reenabled), reason=QRADAR_DISABLED,
                observed_at=now, bucket_seconds=self.settings.collection_interval_seconds,
            )
        if self.absence is not None and self.absence.confirmed:
            ids = sorted(self.absence.confirmed)
            start = max(previous_complete_watermark or now - timedelta(hours=24),
                        now - timedelta(hours=24))
            await open_pause(self.session, ids, reason=ABSENT, starts_at=start, now=now)
            absent_reason = MessageRef("anomaly.source_absent_without_verdict")
            await close_for_disabled_sources(
                self.session, ids, observed_at=now,
                reason=absent_reason, alert_reason=absent_reason,
            )
        if reappeared:
            await close_pause(
                self.session, sorted(reappeared), reason=ABSENT, observed_at=now,
                bucket_seconds=self.settings.collection_interval_seconds,
            )
        await self._record_mentions(
            instance,
            [existing_by_qradar_id[q] for q in dict.fromkeys(d.qradar_id for d in dtos)],
            observed_at=now,
            replace_all=walk.complete,
        )
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
                title_message = MessageRef(
                    "alert.inventory.log_source.title", {"name": source.name}
                )
                description_message = MessageRef(
                    "alert.inventory.log_source.description"
                )
                alert_inputs.append(
                    AlertInput(
                        fingerprint=inventory_fingerprint(instance.id, source.qradar_id),
                        title=render(title_message),
                        title_message=title_message,
                        severity=Severity.LOW,
                        source_type="log_source",
                        source_id=source.id,
                        description=render(description_message),
                        description_message=description_message,
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
        # A types failure makes the pass incomplete even when every source page arrived:
        # the type names the pass relies on did not, so absence of a type cannot be read
        # from it. The walk's own failure (if any) keeps precedence in the reason; the
        # types failure gets its own fixed code so it is not mistaken for a source page.
        walk_failed = walk.failure_kind
        if walk_failed is not None:
            failure_kind_value = walk_failed
        elif types_failed_kind is not None:
            failure_kind_value = FAILURE_TYPES_UNAVAILABLE
        else:
            failure_kind_value = None
        return SyncResult(
            provider=type(self.provider).__name__,
            log_sources_seen=len(dtos),
            created=created,
            updated=updated,
            instance_version=info.version,
            complete=walk.complete and types_failed_kind is None,
            pages_done=walk.pages_done,
            pages_failed=walk.pages_failed,
            failed_page=walk.failed_page,
            failure_kind=failure_kind_value,
        )

    async def _record_mentions(
        self,
        instance: QRadarInstance,
        seen: list[LogSource],
        *,
        observed_at: datetime,
        replace_all: bool = True,
    ) -> None:
        """Index the name and identifier of every log source this pass saw as entity mentions.

        A log source is an identity, not an event. The parent key is the source row:
        `ref_table='log_source'`, `ref_id=str(log_source.id)`; `occurred_at` is this pass's
        observation time, so the mention says "in the inventory as of the last sync", which is
        what the timeline's inventory check assumes. Each pass replaces the console's earlier
        LOG_SOURCE mentions, so a re-sync never accumulates them, a renamed source drops its
        old name and a source the console no longer lists drops out. `name` and `identifier`
        are HOST (role HOST); an `identifier` that parses as an address is also an IP. Statements
        per pass: one DELETE, one INSERT per 1,000 rows (the helper's chunk) and the savepoint
        pair. A walk that was not complete (`replace_all` False) replaces only the mentions of
        the sources it saw: a source it did not reach keeps its mention, because absence is
        concluded only from a complete walk. A failure rolls back to the savepoint and is
        logged by class name: mentions are a derived index and never cost the inventory.
        """
        mentions = log_source_mentions(
            instance.id,
            [
                {"id": source.id, "name": source.name, "identifier": source.identifier}
                for source in seen
            ],
            observed_at=observed_at,
        )
        try:
            async with self.session.begin_nested():
                purge = delete(EntityMention).where(
                    EntityMention.instance_id == instance.id,
                    EntityMention.source == "LOG_SOURCE",
                )
                if not replace_all:
                    # One array parameter, not one bind per id: a console of 50k sources would
                    # pass the driver's parameter limit.
                    purge = purge.where(
                        EntityMention.ref_id
                        == any_(literal([str(source.id) for source in seen], ARRAY(String)))
                    )
                await self.session.execute(
                    purge.execution_options(synchronize_session=False)
                )
                await record_mentions(self.session, mentions)
        except Exception as exc:
            logger.warning(
                "log source entity mentions were not recorded",
                extra={"instance_id": str(instance.id), "error_class": type(exc).__name__},
            )
            # LOG_SOURCE is not gated on entity_mention_backfill: the lane uses the
            # inventory check, and the next sync rewrites these mentions. Marking the
            # row would not change the read, so a failed write is only logged.

    async def ensure_default_instance(self) -> QRadarInstance:
        """Return the first stored instance, creating one when the table is empty.

        The schema allows more than one instance. This helper does not choose
        among them.
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
