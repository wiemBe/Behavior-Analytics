"""Query helpers for the IPS analyst behavior reads (design §D.11.2).

Same contract as the WAF half of `AnomalyRepository`: the route handlers stay
free of query construction, pagination and range guards live in one place, and
every page costs a fixed number of statements whatever the fleet size. The
bounded contributor lists on `ips_source_bucket` are deferred wherever that row
is read, so a source address cannot reach a response by accident.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.ips.inputs import IPS_ENTITY_EVENTS_METRIC
from app.models.enums import ACTIVE_ANOMALY_STATES, IpsDimension
from app.models.ips import (
    IpsAnomaly,
    IpsDetectorState,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSource

#: The persisted subject discriminator of an entity-scoped detector state.
_ENTITY_SUBJECT = "IPS_ENTITY"


class IpsBehaviorRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------------------------------------------------------- IPS sources
    async def sources_page(
        self, *, limit: int, offset: int
    ) -> tuple[list[tuple[IpsSourceProfile, LogSource]], int]:
        # Only sources whose profile is enabled: a disabled one is not being
        # monitored, so it has no current state to report.
        enabled = IpsSourceProfile.enabled.is_(True)
        total = await self.session.scalar(
            select(func.count()).select_from(IpsSourceProfile).where(enabled)
        )
        rows = await self.session.execute(
            select(IpsSourceProfile, LogSource)
            .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
            .where(enabled)
            .order_by(LogSource.name, IpsSourceProfile.id)
            .limit(limit)
            .offset(offset)
        )
        return [(p, ls) for p, ls in rows.all()], int(total or 0)

    async def source(
        self, log_source_id: uuid.UUID
    ) -> tuple[IpsSourceProfile, LogSource] | None:
        row = (
            await self.session.execute(
                select(IpsSourceProfile, LogSource)
                .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
                .where(IpsSourceProfile.log_source_id == log_source_id)
            )
        ).first()
        return (row[0], row[1]) if row is not None else None

    async def latest_buckets(
        self, log_source_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, IpsSourceBucket]:
        """The newest collected bucket per source, in one statement.

        A source with no bucket is absent from the dict, so a caller reads
        absence as "never collected" rather than as a bucket of zeros.
        """
        if not log_source_ids:
            return {}
        b = IpsSourceBucket
        rows = await self.session.scalars(
            select(b)
            .where(b.log_source_id.in_(log_source_ids))
            .ext(distinct_on(b.log_source_id))
            .order_by(b.log_source_id, b.bucket_start.desc())
            .options(defer(b.contributors), defer(b.query_provenance))
        )
        return {r.log_source_id: r for r in rows.all()}

    async def entity_counts(
        self, log_source_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, tuple[int, int]]]:
        """(active, all) entity counts per source and dimension, one statement.

        Retired entities are counted in `all`: the ceiling pressures the active
        set, and a source full of retired rows is a different fact from a small
        one, not the same.
        """
        if not log_source_ids:
            return {}
        e = IpsEntity
        rows = await self.session.execute(
            select(
                e.log_source_id,
                e.dimension,
                func.count().filter(e.active.is_(True)),
                func.count(),
            )
            .where(e.log_source_id.in_(log_source_ids))
            .group_by(e.log_source_id, e.dimension)
        )
        out: dict[uuid.UUID, dict[str, tuple[int, int]]] = {}
        for sid, dimension, active, total in rows.all():
            out.setdefault(sid, {})[str(dimension)] = (int(active), int(total))
        return out

    # --------------------------------------------------------- IPS entities
    async def entities_page(
        self,
        log_source_id: uuid.UUID,
        *,
        dimension: IpsDimension | None,
        limit: int,
        offset: int,
    ) -> tuple[list[IpsEntity], int]:
        e = IpsEntity
        conditions = [e.log_source_id == log_source_id]
        if dimension is not None:
            conditions.append(e.dimension == dimension.value)
        total = await self.session.scalar(
            select(func.count()).select_from(e).where(*conditions)
        )
        rows = await self.session.scalars(
            select(e)
            .where(*conditions)
            .order_by(e.dimension, e.value_key, e.id)
            .limit(limit)
            .offset(offset)
        )
        return list(rows.all()), int(total or 0)

    async def entity_baseline_adequacy(
        self, entity_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, tuple[int, int]]:
        """(cells, reliable cells) of the entity-events baseline per entity.

        Counted for the entity's own normalizer version only: a cell from
        another version is stale and proves nothing about this entity.
        """
        if not entity_ids:
            return {}
        b = IpsEntityBaseline
        rows = await self.session.execute(
            select(b.ips_entity_id, func.count(), func.count().filter(b.is_reliable.is_(True)))
            .join(IpsEntity, IpsEntity.id == b.ips_entity_id)
            .where(
                b.ips_entity_id.in_(entity_ids),
                b.metric_name == IPS_ENTITY_EVENTS_METRIC,
                b.normalizer_version == IpsEntity.normalizer_version,
            )
            .group_by(b.ips_entity_id)
        )
        return {eid: (int(n), int(reliable)) for eid, n, reliable in rows.all()}

    async def entity_detector_states(
        self, entity_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, list[str]]:
        """Every IPS detector's lifecycle state per entity."""
        if not entity_ids:
            return {}
        out: dict[uuid.UUID, list[str]] = {}
        rows = await self.session.execute(
            select(IpsDetectorState.ips_entity_id, IpsDetectorState.state).where(
                IpsDetectorState.subject_kind == _ENTITY_SUBJECT,
                IpsDetectorState.ips_entity_id.in_(entity_ids),
            )
        )
        for eid, state in rows.all():
            if eid is not None:
                out.setdefault(eid, []).append(str(state))
        return out

    async def entity_active_anomaly_counts(
        self, entity_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, int]:
        if not entity_ids:
            return {}
        rows = await self.session.execute(
            select(IpsAnomaly.ips_entity_id, func.count())
            .where(
                IpsAnomaly.ips_entity_id.in_(entity_ids),
                IpsAnomaly.state.in_([s.value for s in ACTIVE_ANOMALY_STATES]),
            )
            .group_by(IpsAnomaly.ips_entity_id)
        )
        return {eid: int(n) for eid, n in rows.all() if eid is not None}

    # ------------------------------------------------- IPS metrics, baselines
    async def entity_metrics_page(
        self,
        entity: IpsEntity,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[tuple[IpsEntityMetric, IpsSourceBucket | None]], int]:
        """Entity buckets in range with the source's bucket record, if any.

        The bucket record carries what the entity row cannot: whether the
        dimension's search was exact, its coverage, and whether admission was
        truncated. It is outer-joined, so a row whose bucket record is missing
        is reported with those fields unknown.
        """
        m = IpsEntityMetric
        b = IpsSourceBucket
        window = (
            m.ips_entity_id == entity.id,
            m.bucket_start >= since,
            m.bucket_start < until,
        )
        total = await self.session.scalar(select(func.count()).select_from(m).where(*window))
        rows = await self.session.execute(
            select(m, b)
            .outerjoin(
                b,
                (b.log_source_id == entity.log_source_id) & (b.bucket_start == m.bucket_start),
            )
            .where(*window)
            .order_by(m.bucket_start)
            .limit(limit)
            .offset(offset)
            .options(defer(b.contributors), defer(b.query_provenance))
        )
        return [(row[0], row[1]) for row in rows.all()], int(total or 0)

    async def entity_baselines_page(
        self, entity_id: uuid.UUID, *, metric: str | None, limit: int, offset: int
    ) -> tuple[list[IpsEntityBaseline], int]:
        b = IpsEntityBaseline
        conditions = [b.ips_entity_id == entity_id]
        if metric is not None:
            conditions.append(b.metric_name == metric)
        total = await self.session.scalar(
            select(func.count()).select_from(b).where(*conditions)
        )
        rows = await self.session.scalars(
            select(b)
            .where(*conditions)
            .order_by(b.metric_name, b.weekday, b.hour)
            .limit(limit)
            .offset(offset)
            .options(defer(b.observations), defer(b.exclusion_counts))
        )
        return list(rows.all()), int(total or 0)

    async def source_metrics_page(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        offset: int,
    ) -> tuple[list[IpsSourceBucket], int]:
        b = IpsSourceBucket
        window = (
            b.log_source_id == log_source_id,
            b.bucket_start >= since,
            b.bucket_start < until,
        )
        total = await self.session.scalar(select(func.count()).select_from(b).where(*window))
        rows = await self.session.scalars(
            select(b)
            .where(*window)
            .order_by(b.bucket_start)
            .limit(limit)
            .offset(offset)
            .options(defer(b.contributors), defer(b.query_provenance))
        )
        return list(rows.all()), int(total or 0)

    async def source_baselines_page(
        self, log_source_id: uuid.UUID, *, metric: str | None, limit: int, offset: int
    ) -> tuple[list[IpsSourceBaseline], int]:
        b = IpsSourceBaseline
        conditions = [b.log_source_id == log_source_id]
        if metric is not None:
            conditions.append(b.metric_name == metric)
        total = await self.session.scalar(
            select(func.count()).select_from(b).where(*conditions)
        )
        rows = await self.session.scalars(
            select(b)
            .where(*conditions)
            .order_by(b.metric_name, b.weekday, b.hour)
            .limit(limit)
            .offset(offset)
            .options(defer(b.observations), defer(b.exclusion_counts))
        )
        return list(rows.all()), int(total or 0)
