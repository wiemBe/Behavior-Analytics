"""Query helpers for the IPS analyst behavior reads (design §D.11.2).

Same contract as the WAF half of `AnomalyRepository`: the route handlers stay
free of query construction, pagination and range guards live in one place, and
every page costs a fixed number of statements whatever the fleet size. The
bounded contributor lists on `ips_source_bucket` are deferred wherever that row
is read, so a source address cannot reach a response by accident.
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from datetime import datetime
from typing import NamedTuple

from sqlalchemy import BigInteger, Float, cast, func, or_, select, true
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

#: The destination-IP measurement states whose entity counts are usable.
_DESTINATION_IP_MEASURED = ("EXACT", "LOWER_BOUND")


class IpsTrafficRow(NamedTuple):
    """One ranked IPS destination IP in the traffic page."""

    id: uuid.UUID
    events: int
    buckets_with_data: int
    partial_buckets: int
    peak_rate_per_second: float
    peak_bucket_start: datetime
    destination_ip: str
    display_name: str | None
    active: bool


class IpsTrafficPage(NamedTuple):
    """A page of ranked destination IPs and the whole set's summary."""

    items: list[IpsTrafficRow]
    total: int
    max_events: int | None
    attributed_events: int


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

    async def source_and_profile(
        self, log_source_id: uuid.UUID
    ) -> tuple[LogSource, IpsSourceProfile | None] | None:
        """The log source and its IPS profile, if it has one.

        None only when the log source itself does not exist. An existing source
        without a profile was never collected, which the route answers as
        NOT_COLLECTED rather than as an unknown id.
        """
        row = (
            await self.session.execute(
                select(LogSource, IpsSourceProfile)
                .outerjoin(IpsSourceProfile, IpsSourceProfile.log_source_id == LogSource.id)
                .where(LogSource.id == log_source_id)
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

    # ----------------------------------------------------- IPS traffic (TOP-1)
    async def ips_traffic_bucket_stats(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        min_coverage: float,
    ) -> tuple[int, int, bool, int | None, int | None]:
        """(measured, partial, any truncated, source events, untracked events).

        Measured means the destination-IP search for that bucket was taken;
        only those buckets' rows are ranked. A measured bucket is partial when
        the search was a lower bound or the dimension's coverage is below the
        floor (`app.ips.inputs.entity_count`'s rule), so its entity counts are
        visible lower bounds. `untracked_events` is the JSONB count of events
        outside admitted destination-IP entities; a measured bucket that does
        not carry the key leaves the whole sum unknown (None), never zero.
        Truncation is checked on every bucket of the range.
        """
        b = IpsSourceBucket
        measured = b.destination_ip_measurement.in_(_DESTINATION_IP_MEASURED)
        partial = measured & or_(
            b.destination_ip_measurement == "LOWER_BOUND",
            b.destination_ip_coverage_ratio.is_(None),
            b.destination_ip_coverage_ratio < min_coverage,
        )
        untracked = b.untracked_events["DESTINATION_IP"].astext
        truncated = b.admission_truncated["DESTINATION_IP"].astext == "true"
        row = (
            await self.session.execute(
                select(
                    func.count().filter(measured).label("measured"),
                    func.count().filter(partial).label("partial"),
                    func.coalesce(func.bool_or(truncated), False).label("truncated"),
                    func.sum(b.total_events).filter(measured).label("source_events"),
                    func.count()
                    .filter(measured, untracked.is_(None))
                    .label("untracked_missing"),
                    func.sum(cast(untracked, BigInteger)).filter(measured).label("untracked"),
                ).where(
                    b.log_source_id == log_source_id,
                    b.bucket_start >= since,
                    b.bucket_start < until,
                )
            )
        ).one()
        unattributed: int | None
        if int(row.untracked_missing) or not int(row.measured):
            unattributed = None
        else:
            unattributed = int(row.untracked or 0)
        return (
            int(row.measured),
            int(row.partial),
            bool(row.truncated),
            int(row.source_events) if row.source_events is not None else None,
            unattributed,
        )

    async def ips_traffic_page(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        min_coverage: float,
        sort: str,
        descending: bool,
        limit: int,
        offset: int,
    ) -> IpsTrafficPage:
        """One page of destination IPs ranked over the whole measured set.

        Same shape as the WAF half: one statement holding the aggregate, the
        peak bucket, the server-side sort and page, and the whole set's
        count/max/sum, with the summary LEFT JOINed so an empty page still
        reports the true total.
        """
        b = IpsSourceBucket
        measured_buckets = select(b.bucket_start).where(
            b.log_source_id == log_source_id,
            b.bucket_start >= since,
            b.bucket_start < until,
            b.destination_ip_measurement.in_(_DESTINATION_IP_MEASURED),
        )
        m = IpsEntityMetric
        e = IpsEntity
        partial = func.count().filter(
            or_(
                b.destination_ip_measurement == "LOWER_BOUND",
                b.destination_ip_coverage_ratio.is_(None),
                b.destination_ip_coverage_ratio < min_coverage,
            )
        )
        agg = (
            select(
                m.ips_entity_id.label("entity_id"),
                func.sum(m.events).label("events"),
                func.count().label("buckets_with_data"),
                partial.label("partial_buckets"),
            )
            .select_from(m)
            .join(e, e.id == m.ips_entity_id)
            .join(
                b,
                (b.log_source_id == e.log_source_id) & (b.bucket_start == m.bucket_start),
            )
            .where(
                m.bucket_start.in_(measured_buckets),
                e.log_source_id == log_source_id,
                e.dimension == IpsDimension.DESTINATION_IP.value,
            )
            .group_by(m.ips_entity_id)
            .subquery()
        )
        peak_rate = cast(m.events, Float) / m.bucket_seconds
        peak = (
            select(
                m.ips_entity_id.label("entity_id"),
                m.bucket_start.label("peak_bucket_start"),
                peak_rate.label("peak_rate"),
            )
            .select_from(m)
            .join(e, e.id == m.ips_entity_id)
            .where(
                m.bucket_start.in_(measured_buckets),
                e.log_source_id == log_source_id,
                e.dimension == IpsDimension.DESTINATION_IP.value,
            )
            .ext(distinct_on(m.ips_entity_id))
            .order_by(m.ips_entity_id, peak_rate.desc(), m.bucket_start)
            .subquery()
        )
        summary = (
            select(
                func.count().label("total"),
                func.max(agg.c.events).label("max_events"),
                func.coalesce(func.sum(agg.c.events), 0).label("attributed_events"),
            )
            .select_from(agg)
            .subquery()
        )
        if sort == "ip":
            ordered = e.value_key.desc() if descending else e.value_key.asc()
        elif sort == "peak":
            ordered = peak.c.peak_rate.desc() if descending else peak.c.peak_rate.asc()
        else:
            ordered = agg.c.events.desc() if descending else agg.c.events.asc()
        page = (
            select(
                agg.c.entity_id,
                agg.c.events,
                agg.c.buckets_with_data,
                agg.c.partial_buckets,
                peak.c.peak_rate,
                peak.c.peak_bucket_start,
                e.value_key,
                e.display_name,
                e.active,
            )
            .select_from(agg)
            .join(e, e.id == agg.c.entity_id)
            .join(peak, peak.c.entity_id == agg.c.entity_id)
            .order_by(ordered, e.id.asc())
            .limit(limit)
            .offset(offset)
            .subquery()
        )
        statement = select(
            summary.c.total,
            summary.c.max_events,
            summary.c.attributed_events,
            page.c.entity_id,
            page.c.events,
            page.c.buckets_with_data,
            page.c.partial_buckets,
            page.c.peak_rate,
            page.c.peak_bucket_start,
            page.c.value_key,
            page.c.display_name,
            page.c.active,
        ).select_from(summary.outerjoin(page, true()))
        rows = (await self.session.execute(statement)).all()
        items = [
            IpsTrafficRow(
                id=row.entity_id,
                events=int(row.events),
                buckets_with_data=int(row.buckets_with_data),
                partial_buckets=int(row.partial_buckets),
                peak_rate_per_second=float(row.peak_rate),
                peak_bucket_start=row.peak_bucket_start,
                destination_ip=row.value_key,
                display_name=row.display_name,
                active=bool(row.active),
            )
            for row in rows
            if row.entity_id is not None
        ]
        summary_row = rows[0]
        return IpsTrafficPage(
            items=items,
            total=int(summary_row.total or 0),
            max_events=(
                int(summary_row.max_events) if summary_row.max_events is not None else None
            ),
            attributed_events=int(summary_row.attributed_events or 0),
        )

    async def ips_traffic_series(
        self,
        log_source_id: uuid.UUID,
        *,
        since: datetime,
        until: datetime,
        step_seconds: int,
        entity_ids: Collection[uuid.UUID],
    ) -> tuple[dict[int, int], dict[tuple[int, uuid.UUID], int]]:
        """(measured buckets per step, events per entity and step), one statement.

        Same contract as the WAF half: the step's measured count comes from the
        destination-IP buckets, an unmeasured step is a gap and never zero, and a
        measured step without a row for the entity means zero events over that
        step's measured seconds.
        """
        if not entity_ids:
            return {}, {}
        b = IpsSourceBucket
        m = IpsEntityMetric
        e = IpsEntity
        measured_buckets = select(b.bucket_start).where(
            b.log_source_id == log_source_id,
            b.bucket_start >= since,
            b.bucket_start < until,
            b.destination_ip_measurement.in_(_DESTINATION_IP_MEASURED),
        )
        step = func.floor(
            func.extract("epoch", b.bucket_start - since) / step_seconds
        ).cast(BigInteger)
        steps = (
            select(step.label("step"), func.count().label("measured"))
            .where(
                b.log_source_id == log_source_id,
                b.bucket_start >= since,
                b.bucket_start < until,
                b.destination_ip_measurement.in_(_DESTINATION_IP_MEASURED),
            )
            .group_by(step)
            .subquery()
        )
        entity_step = func.floor(
            func.extract("epoch", b.bucket_start - since) / step_seconds
        ).cast(BigInteger)
        events = (
            select(
                m.ips_entity_id.label("entity_id"),
                entity_step.label("step"),
                func.sum(m.events).label("events"),
            )
            .select_from(m)
            .join(e, e.id == m.ips_entity_id)
            .join(b, (b.log_source_id == e.log_source_id) & (b.bucket_start == m.bucket_start))
            .where(
                m.ips_entity_id.in_(entity_ids),
                m.bucket_start.in_(measured_buckets),
                e.log_source_id == log_source_id,
                e.dimension == IpsDimension.DESTINATION_IP.value,
            )
            .group_by(m.ips_entity_id, entity_step)
            .subquery()
        )
        rows = (
            await self.session.execute(
                select(steps.c.step, steps.c.measured, events.c.entity_id, events.c.events)
                .select_from(steps.outerjoin(events, events.c.step == steps.c.step))
            )
        ).all()
        measured: dict[int, int] = {}
        per_entity: dict[tuple[int, uuid.UUID], int] = {}
        for row in rows:
            measured[int(row.step)] = int(row.measured)
            if row.entity_id is not None:
                per_entity[(int(row.step), row.entity_id)] = int(row.events)
        return measured, per_entity
