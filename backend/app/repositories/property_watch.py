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

from sqlalchemy import (
    Boolean,
    Table,
    and_,
    bindparam,
    case,
    delete,
    func,
    literal,
    or_,
    select,
    tuple_,
    union_all,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.history.origin import is_live_clause
from app.models.enums import AnomalyState, PwBucketMeasurement
from app.models.fleet import LogSourceGroup
from app.models.log_source import LogSource
from app.models.property_watch import (
    PropertyWatch,
    PwAnomaly,
    PwAnomalyTransition,
    PwBucket,
    PwDetectorState,
    PwValue,
    PwValueBaseline,
    PwValueMetric,
)
from app.property_watch.scope import GroupRow, ScopeRecord, SourceRow

#: The columns only the history reading writes (the collector is their only writer), in
#: addition to the watermark and status every row carries. `evaluated_through` is here
#: because only the hand-off and a version restart move it from collection.
HISTORY_WATCH_COLUMNS: tuple[str, ...] = (
    "evaluated_through",
    "history_state",
    "history_target_start",
    "history_floor",
    "history_through",
    "history_end",
    "history_started_at",
    "history_finished_at",
    "history_progress",
)


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
            select(PwValue)
            .where(
                PwValue.watch_id.in_(watch_ids),
                or_(
                    tuple_(PwValue.watch_id, PwValue.watch_version).in_(list(versions)),
                    PwValue.active.is_(True),
                ),
            )
            .execution_options(populate_existing=True)
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

    async def pending_buckets(
        self, instance_id: uuid.UUID, *, limit: int
    ) -> list[tuple[PropertyWatch, PwBucket, datetime | None]]:
        """Current-version buckets after each watch's evaluation watermark, in bulk.

        Each row also carries the first bucket ever stored for the watch's current version
        (the start of its learning period), so that fact needs no statement of its own.
        """
        first = (
            select(
                PwBucket.watch_id.label("watch_id"),
                PwBucket.watch_version.label("watch_version"),
                func.min(PwBucket.bucket_start).label("first_at"),
            )
            .group_by(PwBucket.watch_id, PwBucket.watch_version)
            .subquery()
        )
        ranked = (
            select(
                PwBucket.watch_id.label("watch_id"),
                PwBucket.bucket_start.label("bucket_start"),
                func.row_number()
                .over(partition_by=PwBucket.watch_id, order_by=PwBucket.bucket_start)
                .label("rank"),
            )
            .join(PropertyWatch, PropertyWatch.id == PwBucket.watch_id)
            .where(
                PropertyWatch.instance_id == instance_id,
                PropertyWatch.enabled.is_(True),
                PwBucket.watch_version == PropertyWatch.watch_version,
                is_live_clause(PwBucket),
                PwBucket.bucket_start <= PropertyWatch.collected_through,
                or_(
                    PropertyWatch.evaluated_through.is_(None),
                    PwBucket.bucket_start > PropertyWatch.evaluated_through,
                ),
            )
            .subquery()
        )
        rows = await self.session.execute(
            select(PropertyWatch, PwBucket, first.c.first_at)
            .join(ranked, ranked.c.watch_id == PropertyWatch.id)
            .join(
                first,
                and_(
                    first.c.watch_id == PropertyWatch.id,
                    first.c.watch_version == PropertyWatch.watch_version,
                ),
            )
            .join(
                PwBucket,
                and_(
                    PwBucket.watch_id == ranked.c.watch_id,
                    PwBucket.bucket_start == ranked.c.bucket_start,
                ),
            )
            .where(ranked.c.rank <= limit)
            .order_by(PwBucket.bucket_start, PropertyWatch.id)
            .execution_options(populate_existing=True)
        )
        return [(watch, bucket, first_at) for watch, bucket, first_at in rows.all()]

    async def buckets_between(
        self, watch_ids: Sequence[uuid.UUID], *, start: datetime, end: datetime
    ) -> list[PwBucket]:
        if not watch_ids:
            return []
        rows = await self.session.scalars(
            select(PwBucket)
            .where(
                PwBucket.watch_id.in_(list(watch_ids)),
                PwBucket.bucket_start >= start,
                PwBucket.bucket_start <= end,
            )
            .order_by(PwBucket.watch_id, PwBucket.bucket_start)
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def metrics_between(
        self, value_ids: Sequence[uuid.UUID], *, start: datetime, end: datetime
    ) -> list[PwValueMetric]:
        if not value_ids:
            return []
        rows = await self.session.scalars(
            select(PwValueMetric)
            .where(
                PwValueMetric.pw_value_id.in_(list(value_ids)),
                PwValueMetric.bucket_start >= start,
                PwValueMetric.bucket_start <= end,
            )
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def training_metrics_between(
        self, value_ids: Sequence[uuid.UUID], *, start: datetime, end: datetime
    ) -> list[tuple[uuid.UUID, datetime, int]]:
        """The three metric columns baseline training consumes, without ORM row overhead."""
        if not value_ids:
            return []
        rows = await self.session.execute(
            select(
                PwValueMetric.pw_value_id,
                PwValueMetric.bucket_start,
                PwValueMetric.events,
            ).where(
                PwValueMetric.pw_value_id.in_(list(value_ids)),
                PwValueMetric.bucket_start >= start,
                PwValueMetric.bucket_start <= end,
            )
        )
        return [
            (value_id, bucket_start, int(events))
            for value_id, bucket_start, events in rows.all()
        ]

    async def baseline_cells(
        self,
        value_ids: Sequence[uuid.UUID],
        seasonal_keys: Sequence[tuple[int, int]],
    ) -> list[PwValueBaseline]:
        if not value_ids or not seasonal_keys:
            return []
        rows = await self.session.scalars(
            select(PwValueBaseline)
            .where(
                PwValueBaseline.pw_value_id.in_(list(value_ids)),
                tuple_(PwValueBaseline.weekday, PwValueBaseline.hour).in_(
                    list(set(seasonal_keys))
                ),
            )
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def detector_states(
        self, watch_ids: Sequence[uuid.UUID]
    ) -> list[tuple[PwDetectorState, PwValue]]:
        if not watch_ids:
            return []
        rows = await self.session.execute(
            select(PwDetectorState, PwValue)
            .join(PwValue, PwValue.id == PwDetectorState.pw_value_id)
            .where(PwDetectorState.watch_id.in_(list(watch_ids)))
            .execution_options(populate_existing=True)
        )
        return [(state, value) for state, value in rows.all()]

    async def anomalies(self, anomaly_ids: Collection[uuid.UUID]) -> list[PwAnomaly]:
        if not anomaly_ids:
            return []
        rows = await self.session.scalars(
            select(PwAnomaly)
            .where(PwAnomaly.id.in_(list(anomaly_ids)))
            .execution_options(populate_existing=True)
        )
        return list(rows.all())

    async def component_occurrences(
        self,
        versions: Sequence[tuple[uuid.UUID, int]],
        components: Collection[str],
        *,
        start: datetime,
        end: datetime,
    ) -> list[tuple[uuid.UUID, int, str, int, str, datetime]]:
        """Earliest counted occurrence of each tuple component, per bucket measurement.

        One statement, aggregated in SQL: a row per (watch, version, component, position,
        bucket measurement) with the earliest bucket in ``[start, end)`` that counted it. The
        novelty history never needs more than "was it counted before the tuple's first
        appearance, and was that count exact".
        """
        if not versions or not components:
            return []
        wanted = sorted(components)
        rows: list[Any] = []
        for position, column in enumerate((PwValue.component_0, PwValue.component_1)):
            rows.append(
                select(
                    PwValue.watch_id.label("watch_id"),
                    PwValue.watch_version.label("watch_version"),
                    column.label("component"),
                    literal(position).label("position"),
                    PwBucket.values_measurement.label("measurement"),
                    func.min(PwValueMetric.bucket_start).label("first_at"),
                )
                .join(PwValueMetric, PwValueMetric.pw_value_id == PwValue.id)
                .join(
                    PwBucket,
                    and_(
                        PwBucket.watch_id == PwValue.watch_id,
                        PwBucket.bucket_start == PwValueMetric.bucket_start,
                        PwBucket.watch_version == PwValue.watch_version,
                    ),
                )
                .where(
                    tuple_(PwValue.watch_id, PwValue.watch_version).in_(list(versions)),
                    column.in_(wanted),
                    PwValueMetric.bucket_start >= start,
                    PwValueMetric.bucket_start < end,
                    PwValueMetric.events > 0,
                )
                .group_by(
                    PwValue.watch_id, PwValue.watch_version, column, PwBucket.values_measurement
                )
            )
        result = await self.session.execute(union_all(*rows))
        return [
            (watch_id, version, component, int(position), str(measurement), first_at)
            for watch_id, version, component, position, measurement, first_at in result.all()
        ]

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
        table = cast(Table, PwBucket.__table__)
        statement = pg_insert(table)
        set_: dict[str, Any] = {
            key: statement.excluded[key]
            for key in rows[0]
            if key not in ("watch_id", "bucket_start")
        }
        if "origin" in set_:
            # A write never changes the marker of a row of the same watch version: a live
            # write onto a history row keeps HISTORY. A row of another version is replaced
            # whole, marker included (PHASE-G §H.5.4: the new history overwrites old-version
            # rows at the same `bucket_start`, which no reader uses).
            set_["origin"] = case(
                (
                    table.c.watch_version.is_distinct_from(statement.excluded.watch_version),
                    statement.excluded.origin,
                ),
                else_=table.c.origin,
            )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["watch_id", "bucket_start"], set_=set_
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

        Every row names the same columns (a different set would be a second statement). The
        history columns (`HISTORY_WATCH_COLUMNS`) ride on the same statement: a row that
        carries `history_state` writes all of them, every other row leaves them as they are
        (a `CASE` on one flag, so a live watch's history columns are never rewritten from a
        snapshot).
        """
        if not rows:
            return
        table = cast(Table, PropertyWatch.__table__)
        writes_history = bindparam("history_write", type_=Boolean())
        history_values = {
            column: case(
                (writes_history, bindparam(f"h_{column}", type_=table.c[column].type)),
                else_=table.c[column],
            )
            for column in HISTORY_WATCH_COLUMNS
        }
        params: list[dict[str, Any]] = []
        for row in rows:
            flag = "history_state" in row
            param = {
                "watch_id": row["watch_id"],
                "collected_through": row["collected_through"],
                "last_status": row["last_status"],
                "last_status_reason": row["last_status_reason"],
                "last_run_at": row["last_run_at"],
                "history_write": flag,
            }
            for column in HISTORY_WATCH_COLUMNS:
                param[f"h_{column}"] = row.get(column) if flag else None
            params.append(param)
        await self.session.execute(
            update(table)
            .where(table.c.id == bindparam("watch_id"))
            .values(
                collected_through=bindparam("collected_through"),
                last_status=bindparam("last_status"),
                last_status_reason=bindparam("last_status_reason"),
                last_run_at=bindparam("last_run_at"),
                **history_values,
            ),
            params,
        )

    async def write_detector_states(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table = cast(Table, PwDetectorState.__table__)
        statement = pg_insert(table)
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_pw_detector_state_value",
                set_={
                    key: statement.excluded[key]
                    for key in rows[0]
                    if key not in {"id", "watch_id", "pw_value_id", "detector_type"}
                }
                | {"updated_at": func.now()},
            ),
            rows,
        )

    async def write_anomalies(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table = cast(Table, PwAnomaly.__table__)
        statement = pg_insert(table)
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    key: statement.excluded[key]
                    for key in rows[0]
                    if key not in {"id", "created_at", "updated_at"}
                }
                | {"updated_at": func.now()},
            ),
            rows,
        )

    async def write_transitions(self, rows: list[dict[str, Any]]) -> None:
        if rows:
            await self.session.execute(pg_insert(PwAnomalyTransition), rows)

    async def update_evaluation_watches(self, rows: list[dict[str, Any]]) -> None:
        """Evaluation watermark and detector-owned status in one statement."""
        if not rows:
            return
        table = cast(Table, PropertyWatch.__table__)
        await self.session.execute(
            update(table)
            .where(table.c.id == bindparam("watch_id"))
            .values(
                evaluated_through=bindparam("evaluated_through"),
                last_status=bindparam("last_status"),
                last_status_reason=bindparam("last_status_reason"),
            ),
            rows,
        )

    async def active_incident_spans(
        self, value_ids: Sequence[uuid.UUID], *, end: datetime
    ) -> list[tuple[uuid.UUID, datetime, datetime | None, datetime | None]]:
        if not value_ids:
            return []
        rows = await self.session.execute(
            select(
                PwAnomaly.pw_value_id,
                PwAnomaly.detected_at,
                PwAnomaly.anomaly_end,
                PwAnomaly.resolved_at,
            ).where(
                PwAnomaly.pw_value_id.in_(list(value_ids)),
                PwAnomaly.detected_at < end,
                PwAnomaly.state != AnomalyState.INSUFFICIENT_DATA.value,
            )
        )
        return [
            (value_id, detected_at, anomaly_end, resolved_at)
            for value_id, detected_at, anomaly_end, resolved_at in rows.all()
            if value_id is not None
        ]

    async def due_baseline_values(
        self, instance_id: uuid.UUID, *, limit: int
    ) -> list[tuple[PwValue, datetime]]:
        """Current active values, least recently computed first, in one statement."""
        last = (
            select(
                PwValueBaseline.pw_value_id.label("value_id"),
                func.max(PwValueBaseline.computed_at).label("computed_at"),
            )
            .group_by(PwValueBaseline.pw_value_id)
            .subquery()
        )
        rows = await self.session.execute(
            select(PwValue, PropertyWatch.evaluated_through)
            .join(PropertyWatch, PropertyWatch.id == PwValue.watch_id)
            .outerjoin(last, last.c.value_id == PwValue.id)
            .where(
                PropertyWatch.instance_id == instance_id,
                PropertyWatch.enabled.is_(True),
                PwValue.watch_version == PropertyWatch.watch_version,
                PwValue.active.is_(True),
                PropertyWatch.evaluated_through.is_not(None),
            )
            .order_by(last.c.computed_at.asc().nulls_first(), PwValue.id)
            .limit(limit)
        )
        return [(value, evaluated) for value, evaluated in rows.all() if evaluated is not None]

    async def upsert_baselines(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        table = cast(Table, PwValueBaseline.__table__)
        statement = pg_insert(table)
        await self.session.execute(
            statement.on_conflict_do_update(
                constraint="uq_pw_value_baseline_cell",
                set_={
                    **{
                        key: statement.excluded[key]
                        for key in rows[0]
                        if key
                        not in {
                            "id",
                            "pw_value_id",
                            "metric_name",
                            "weekday",
                            "hour",
                            "baseline_version",
                        }
                    },
                    "baseline_version": table.c.baseline_version + 1,
                    "updated_at": func.now(),
                },
            ),
            rows,
        )

    async def delete_stale_baselines(
        self,
        value_ids: Sequence[uuid.UUID],
        *,
        computed_at: datetime,
    ) -> None:
        """Delete the cells of `value_ids` the rebuild did not produce (PW-3c item 3, §G.8.0).

        A cell whose samples all aged out (or were all excluded) is not among the rows the
        rebuild just upserted, so it keeps its last statistics and would judge a detector on
        old data. Removing it makes the detector read `INSUFFICIENT_DATA` instead. The rebuild
        marks every row it writes with `computed_at`, so any other cell of these values is
        stale. One statement per chunk whatever the number of values, and no bind-parameter
        list of the sized-to-the-chunk cells.
        """
        if not value_ids:
            return
        await self.session.execute(
            delete(PwValueBaseline).where(
                PwValueBaseline.pw_value_id.in_(list(value_ids)),
                or_(
                    PwValueBaseline.computed_at.is_(None),
                    PwValueBaseline.computed_at != computed_at,
                ),
            )
        )

