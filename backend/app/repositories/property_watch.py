"""Bulk reads and writes of property-watch collection (design §G.14.2, AGENTS.md rule 9).

Every method is one statement whatever the number of watches, values or buckets: the reads
take id lists, the writes are one multi-row upsert per table. A caller never queries per
watch, per value or per tuple.

The writes use `executemany` form (a list of parameter dicts) rather than one huge
`VALUES` list: a tick can carry tens of thousands of metric rows, which would exceed the
driver's bind-parameter limit as one statement, and `executemany` is still one statement
to the driver.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection, Sequence
from datetime import datetime
from typing import Any, cast

from sqlalchemy import Table, bindparam, func, or_, select, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.enums import PwBucketMeasurement
from app.models.fleet import LogSourceGroup
from app.models.log_source import LogSource
from app.models.property_watch import PropertyWatch, PwBucket, PwValue, PwValueMetric
from app.property_watch.scope import GroupRow, ScopeRecord, SourceRow


class PropertyWatchRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ----------------------------------------------------------------- reads
    async def enabled_watches(self, instance_id: uuid.UUID) -> list[PropertyWatch]:
        """The instance's enabled watches, in a stable order (the budget is shared)."""
        rows = await self.session.scalars(
            select(PropertyWatch)
            .where(PropertyWatch.instance_id == instance_id, PropertyWatch.enabled.is_(True))
            .order_by(PropertyWatch.created_at, PropertyWatch.id)
        )
        return list(rows.all())

    async def groups(self, instance_id: uuid.UUID) -> dict[int, GroupRow]:
        """Every synced group of the instance: the one read every group scope resolves from."""
        rows = await self.session.execute(
            select(
                LogSourceGroup.qradar_id,
                LogSourceGroup.parent_qradar_id,
                LogSourceGroup.synced_at,
            ).where(LogSourceGroup.instance_id == instance_id)
        )
        return {
            qradar_id: GroupRow(qradar_id, parent, synced_at)
            for qradar_id, parent, synced_at in rows.all()
        }

    async def sources(
        self,
        instance_id: uuid.UUID,
        *,
        source_ids: Collection[uuid.UUID],
        whole_inventory: bool,
    ) -> list[SourceRow]:
        """The instance's sources: all of them when a group or type scope needs membership,
        otherwise only the sources `LOG_SOURCE` watches name."""
        if not whole_inventory and not source_ids:
            return []
        query = select(
            LogSource.id,
            LogSource.qradar_id,
            LogSource.type_id,
            LogSource.group_ids,
            LogSource.maintenance_mode,
            LogSource.maintenance_until,
        ).where(LogSource.instance_id == instance_id)
        if not whole_inventory:
            query = query.where(LogSource.id.in_(list(source_ids)))
        rows = await self.session.execute(query)
        return [
            SourceRow(
                id=sid,
                qradar_id=qid,
                type_id=type_id,
                group_ids=tuple(int(g) for g in (group_ids or ())),
                maintenance_mode=bool(maintenance_mode),
                maintenance_until=maintenance_until,
            )
            for sid, qid, type_id, group_ids, maintenance_mode, maintenance_until in rows.all()
        ]

    async def values(self, versions: Sequence[tuple[uuid.UUID, int]]) -> list[PwValue]:
        """Every value row of the watches at their current version, and every still-active
        row of another version (those are retired by the caller)."""
        if not versions:
            return []
        watch_ids = [watch_id for watch_id, _ in versions]
        rows = await self.session.scalars(
            select(PwValue).where(
                PwValue.watch_id.in_(watch_ids),
                or_(
                    tuple_(PwValue.watch_id, PwValue.watch_version).in_(list(versions)),
                    PwValue.active.is_(True),
                ),
            )
        )
        return list(rows.all())

    async def scope_history(
        self, watch_ids: Sequence[uuid.UUID], *, depth: int, since: datetime
    ) -> dict[uuid.UUID, list[ScopeRecord]]:
        """The newest `depth` collected buckets of each watch, newest first, in one statement.

        A bucket that measured nothing (`NOT_COLLECTED`) says nothing about the scope and is
        left out. `since` bounds the scan of the hypertable.
        """
        if not watch_ids or depth < 1:
            return {}
        ranked = (
            select(
                PwBucket.watch_id.label("watch_id"),
                PwBucket.bucket_start.label("bucket_start"),
                PwBucket.scope_fingerprint.label("fingerprint"),
                PwBucket.scope_changed.label("changed"),
                func.row_number()
                .over(partition_by=PwBucket.watch_id, order_by=PwBucket.bucket_start.desc())
                .label("rank"),
            )
            .where(
                PwBucket.watch_id.in_(list(watch_ids)),
                PwBucket.bucket_start >= since,
                PwBucket.values_measurement != PwBucketMeasurement.NOT_COLLECTED.value,
            )
            .subquery()
        )
        rows = await self.session.execute(
            select(ranked.c.watch_id, ranked.c.fingerprint, ranked.c.changed)
            .where(ranked.c.rank <= depth)
            .order_by(ranked.c.watch_id, ranked.c.bucket_start.desc())
        )
        out: dict[uuid.UUID, list[ScopeRecord]] = {}
        for watch_id, fingerprint, changed in rows.all():
            out.setdefault(watch_id, []).append(ScopeRecord(fingerprint, bool(changed)))
        return out

    # ---------------------------------------------------------------- writes
    async def write_values(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table = cast(Table, PwValue.__table__)
        statement = pg_insert(table)
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_pw_value_natural_key",
                set_={
                    "pinned": statement.excluded.pinned,
                    "truncated": statement.excluded.truncated,
                    "first_seen_at": func.coalesce(
                        table.c.first_seen_at, statement.excluded.first_seen_at
                    ),
                    "last_seen_at": func.greatest(
                        table.c.last_seen_at, statement.excluded.last_seen_at
                    ),
                    "tracked_since": statement.excluded.tracked_since,
                    "tracked_until": statement.excluded.tracked_until,
                    "active": statement.excluded.active,
                    "retired_at": statement.excluded.retired_at,
                    "updated_at": func.now(),
                },
            ),
            rows,
        )

    async def write_buckets(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        statement = pg_insert(cast(Table, PwBucket.__table__))
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["watch_id", "bucket_start"],
                set_={
                    key: statement.excluded[key]
                    for key in rows[0]
                    if key not in ("watch_id", "bucket_start")
                },
            ),
            rows,
        )

    async def write_metrics(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        statement = pg_insert(cast(Table, PwValueMetric.__table__))
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["pw_value_id", "bucket_start"],
                set_={
                    "events": statement.excluded.events,
                    "bucket_seconds": statement.excluded.bucket_seconds,
                },
            ),
            rows,
        )

    async def update_watches(self, rows: list[dict[str, Any]]) -> None:
        """Watermark and status of every watch of the tick, as one `executemany`.

        Every row names the same columns (a different set would be a second statement).
        """
        if not rows:
            return
        table = cast(Table, PropertyWatch.__table__)
        await self.session.execute(
            update(table)
            .where(table.c.id == bindparam("watch_id"))
            .values(
                collected_through=bindparam("collected_through"),
                last_status=bindparam("last_status"),
                last_status_reason=bindparam("last_status_reason"),
                last_run_at=bindparam("last_run_at"),
            ),
            rows,
        )

