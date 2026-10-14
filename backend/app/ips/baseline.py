"""IPS entity and source baseline computation, in bounded bulk rebuilds.

The statistical rules come from ``app.anomaly.seasonal`` and the sparse entity
count from ``app.ips.inputs``. Only exact, sufficiently covered, current-version
buckets train. Lower bounds and unknowns are exclusions, never zeros.

Entity metric rows, source bucket facts and incident spans are loaded in bulk.
Statements therefore scale with configured subject chunks, not with entities or
seasonal cells. ``train_through`` is the end of the last evaluated bucket: later
buckets are counted as unfinished exclusions and cannot teach the baseline that
will judge them.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast

from sqlalchemy import Table, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.seasonal import (
    anomaly_exclusion_span,
    in_any_span,
    retained_fraction,
    seasonal_key,
    summarize_cell,
)
from app.core.config import Settings, get_settings
from app.ips.inputs import (
    IPS_BLOCK_RATIO_METRIC,
    IPS_ENTITY_EVENTS_METRIC,
    IPS_FANOUT_METRIC,
    IPS_TOP_SOURCE_SHARE_METRIC,
    IpsBucketMeasurement,
    IpsEntityFacts,
    IpsSourceBucketFacts,
    entity_count,
)
from app.models.enums import (
    IpsAnomalyType,
    IpsDimension,
    IpsProfileValidationStatus,
    WafMeasurement,
)
from app.models.ips import (
    IPS_NORMALIZER_VERSION,
    IpsAnomaly,
    IpsEntity,
    IpsEntityBaseline,
    IpsEntityMetric,
    IpsSourceBaseline,
    IpsSourceBucket,
    IpsSourceProfile,
)

IPS_ENTITY_EVENTS_SEMANTICS = "ips/entity_events/v1"
IPS_SOURCE_METRIC_SEMANTICS = {
    IPS_TOP_SOURCE_SHARE_METRIC: "ips/top1_source_share/v1",
    IPS_BLOCK_RATIO_METRIC: "ips/block_ratio/v1",
    IPS_FANOUT_METRIC: "ips/max_distinct_ports/v1",
}

_SOURCE_DETECTOR = {
    IPS_TOP_SOURCE_SHARE_METRIC: IpsAnomalyType.IPS_SOURCE_CONCENTRATION_SPIKE,
    IPS_BLOCK_RATIO_METRIC: IpsAnomalyType.IPS_BLOCK_RATIO_SHIFT,
    IPS_FANOUT_METRIC: IpsAnomalyType.IPS_PORT_FANOUT_SPIKE,
}


@dataclass
class IpsExclusionTally:
    """Why candidate IPS buckets did not train a cell."""

    known_anomaly: int = 0
    maintenance: int = 0
    unfinished: int = 0
    width_mismatch: int = 0
    version_mismatch: int = 0
    lower_bound: int = 0
    unknown: int = 0
    low_coverage: int = 0
    unmapped_actions: int = 0
    no_population: int = 0

    @property
    def total(self) -> int:
        return sum(self.as_dict().values())

    def as_dict(self) -> dict[str, int]:
        return {
            "known_anomaly": self.known_anomaly,
            "maintenance": self.maintenance,
            "unfinished": self.unfinished,
            "width_mismatch": self.width_mismatch,
            "version_mismatch": self.version_mismatch,
            "lower_bound": self.lower_bound,
            "unknown": self.unknown,
            "low_coverage": self.low_coverage,
            "unmapped_actions": self.unmapped_actions,
            "no_population": self.no_population,
        }


@dataclass(frozen=True)
class IpsSample:
    bucket_start: datetime
    value: float
    numerator: int | None = None
    denominator: int | None = None


def _common_exclusion(
    bucket: IpsSourceBucketFacts,
    *,
    train_through: datetime,
    bucket_seconds: int,
    profile_version: int,
    normalizer_version: int,
    spans: Sequence[tuple[datetime, datetime]],
    tally: IpsExclusionTally,
) -> bool:
    if bucket.bucket_end > train_through:
        tally.unfinished += 1
    elif bucket.bucket_seconds != bucket_seconds:
        tally.width_mismatch += 1
    elif (bucket.profile_version, bucket.normalizer_version) != (
        profile_version,
        normalizer_version,
    ):
        tally.version_mismatch += 1
    elif bucket.in_maintenance:
        tally.maintenance += 1
    elif in_any_span(bucket.bucket_start, spans):
        tally.known_anomaly += 1
    else:
        return False
    return True


def clean_entity_samples(
    entity: IpsEntityFacts,
    buckets: Sequence[IpsSourceBucketFacts],
    metric_rows: Mapping[datetime, tuple[int, int]],
    *,
    train_through: datetime,
    bucket_seconds: int,
    profile_version: int,
    normalizer_version: int,
    min_coverage: float,
    spans: Sequence[tuple[datetime, datetime]] = (),
) -> tuple[list[IpsSample], IpsExclusionTally, int]:
    """Exact entity observations that may train, and their exclusions. Pure."""
    tally = IpsExclusionTally()
    samples: list[IpsSample] = []
    for bucket in buckets:
        if _common_exclusion(
            bucket,
            train_through=train_through,
            bucket_seconds=bucket_seconds,
            profile_version=profile_version,
            normalizer_version=normalizer_version,
            spans=spans,
            tally=tally,
        ):
            continue
        dimension = bucket.dimension(entity.dimension)
        if dimension is None or not dimension.measured:
            tally.unknown += 1
            continue
        if dimension.measurement is IpsBucketMeasurement.LOWER_BOUND:
            tally.lower_bound += 1
            continue
        if not dimension.coverage_met(min_coverage):
            tally.low_coverage += 1
            continue
        row = metric_rows.get(bucket.bucket_start)
        if row is not None and row[1] != bucket_seconds:
            tally.width_mismatch += 1
            continue
        count = entity_count(
            entity,
            bucket,
            row[0] if row is not None else None,
            min_coverage=min_coverage,
        )
        if count.events is None or count.measurement is None:
            tally.unknown += 1
        elif count.measurement is not WafMeasurement.EXACT:
            tally.lower_bound += 1
        else:
            samples.append(IpsSample(bucket.bucket_start, float(count.events)))
    return samples, tally, len(buckets)


def clean_source_samples(
    buckets: Sequence[IpsSourceBucketFacts],
    metric_name: str,
    *,
    train_through: datetime,
    bucket_seconds: int,
    profile_version: int,
    normalizer_version: int,
    min_coverage: float,
    spans: Sequence[tuple[datetime, datetime]] = (),
) -> tuple[list[IpsSample], IpsExclusionTally, int]:
    """Whole source observations for one source-level metric. Pure."""
    if metric_name not in IPS_SOURCE_METRIC_SEMANTICS:
        raise ValueError(f"unknown IPS source baseline metric: {metric_name}")
    tally = IpsExclusionTally()
    samples: list[IpsSample] = []
    for bucket in buckets:
        if _common_exclusion(
            bucket,
            train_through=train_through,
            bucket_seconds=bucket_seconds,
            profile_version=profile_version,
            normalizer_version=normalizer_version,
            spans=spans,
            tally=tally,
        ):
            continue
        if metric_name == IPS_TOP_SOURCE_SHARE_METRIC:
            dimension = bucket.source_ip
            numerator, denominator = bucket.top1_events, bucket.source_events
        elif metric_name == IPS_BLOCK_RATIO_METRIC:
            dimension = bucket.action
            numerator, denominator = bucket.blocked_events, bucket.action_events
        else:
            dimension = bucket.port_fanout
            numerator, denominator = bucket.max_distinct_ports, None

        if dimension.measurement is IpsBucketMeasurement.LOWER_BOUND:
            tally.lower_bound += 1
            continue
        if dimension.measurement is not IpsBucketMeasurement.EXACT:
            tally.unknown += 1
            continue
        coverage = bucket.destination_port if metric_name == IPS_FANOUT_METRIC else dimension
        if not coverage.coverage_met(min_coverage):
            tally.low_coverage += 1
            continue
        if metric_name == IPS_BLOCK_RATIO_METRIC:
            if bucket.unmapped_events is None:
                tally.unknown += 1
                continue
            if bucket.unmapped_events:
                tally.unmapped_actions += 1
                continue
        if numerator is None:
            tally.unknown += 1
            continue
        if metric_name == IPS_FANOUT_METRIC:
            samples.append(IpsSample(bucket.bucket_start, float(numerator)))
            continue
        if denominator is None:
            tally.unknown += 1
        elif denominator <= 0:
            tally.no_population += 1
        else:
            samples.append(
                IpsSample(
                    bucket.bucket_start,
                    numerator / denominator,
                    numerator,
                    denominator,
                )
            )
    return samples, tally, len(buckets)


class IpsBaselineBuilder:
    """Build versioned entity and source cells without per-subject SQL."""

    def __init__(self, session: AsyncSession, *, settings: Settings | None = None) -> None:
        self.session = session
        self.settings = settings or get_settings()

    async def rebuild_entities(
        self,
        entities: Sequence[IpsEntity],
        *,
        train_through: datetime,
        now: datetime | None = None,
    ) -> dict[uuid.UUID, int]:
        """Recompute many entity baselines; statement growth is per configured chunk."""
        written = {entity.id: 0 for entity in entities}
        if not entities:
            return written
        now = now or datetime.now(UTC)
        train_through = min(now, train_through)
        start = train_through - timedelta(days=self.settings.ips_baseline_lookback_days)
        source_ids = {entity.log_source_id for entity in entities}
        profiles = {
            profile.log_source_id: profile
            for profile in (
                await self.session.scalars(
                    select(IpsSourceProfile).where(IpsSourceProfile.log_source_id.in_(source_ids))
                )
            ).all()
        }
        buckets = await self._source_buckets(source_ids, start, now)
        chunk_size = self.settings.ips_baseline_rebuild_batch_size
        for offset in range(0, len(entities), chunk_size):
            chunk = entities[offset : offset + chunk_size]
            rows = await self._entity_rows(chunk, start, now)
            spans = await self._entity_spans(chunk, train_through)
            cell_rows: list[dict] = []
            for entity in chunk:
                profile = profiles.get(entity.log_source_id)
                if profile is None:
                    continue
                facts = IpsEntityFacts(
                    dimension=IpsDimension(str(entity.dimension)),
                    value_key=entity.value_key,
                    display_name=entity.display_name,
                    first_seen_at=entity.first_seen_at,
                    active=entity.active,
                    last_seen_at=entity.last_seen_at,
                    normalizer_version=entity.normalizer_version,
                )
                samples, tally, candidates = clean_entity_samples(
                    facts,
                    buckets.get(entity.log_source_id, ()),
                    rows.get(entity.id, {}),
                    train_through=train_through,
                    bucket_seconds=self.settings.collection_interval_seconds,
                    profile_version=profile.profile_version,
                    normalizer_version=IPS_NORMALIZER_VERSION,
                    min_coverage=self.settings.ips_min_coverage,
                    spans=spans.get(entity.id, ()),
                )
                cells = self._group(samples)
                for (weekday, hour), grouped in cells.items():
                    cell_rows.append(
                        self._cell_row(
                            "ips_entity_id",
                            entity.id,
                            IPS_ENTITY_EVENTS_METRIC,
                            weekday,
                            hour,
                            grouped,
                            tally,
                            candidates,
                            start,
                            train_through,
                            now,
                            profile.profile_version,
                            IPS_ENTITY_EVENTS_SEMANTICS,
                        )
                    )
                written[entity.id] = len(cells)
            await self._upsert(IpsEntityBaseline, "ips_entity_id", cell_rows)
        await self.session.flush()
        return written

    async def rebuild_sources(
        self,
        sources: Sequence[IpsSourceProfile],
        *,
        train_through: datetime,
        now: datetime | None = None,
    ) -> dict[uuid.UUID, int]:
        """Recompute source-level ratio and fan-out cells in source chunks."""
        written = {source.log_source_id: 0 for source in sources}
        if not sources:
            return written
        now = now or datetime.now(UTC)
        train_through = min(now, train_through)
        start = train_through - timedelta(days=self.settings.ips_baseline_lookback_days)
        source_ids = {source.log_source_id for source in sources}
        buckets = await self._source_buckets(source_ids, start, now)
        chunk_size = self.settings.ips_baseline_rebuild_batch_size
        for offset in range(0, len(sources), chunk_size):
            chunk = sources[offset : offset + chunk_size]
            spans = await self._source_spans(chunk, train_through)
            cell_rows: list[dict] = []
            for source in chunk:
                metric_names = [IPS_TOP_SOURCE_SHARE_METRIC, IPS_FANOUT_METRIC]
                action_mapping = (source.mapping or {}).get("ACTION")
                action_values = source.action_value_map or {}
                if action_mapping is not None and any(action_values.values()):
                    metric_names.append(IPS_BLOCK_RATIO_METRIC)
                for metric_name in metric_names:
                    detector = _SOURCE_DETECTOR[metric_name]
                    samples, tally, candidates = clean_source_samples(
                        buckets.get(source.log_source_id, ()),
                        metric_name,
                        train_through=train_through,
                        bucket_seconds=self.settings.collection_interval_seconds,
                        profile_version=source.profile_version,
                        normalizer_version=IPS_NORMALIZER_VERSION,
                        min_coverage=self.settings.ips_min_coverage,
                        spans=spans.get((source.log_source_id, detector), ()),
                    )
                    cells = self._group(samples)
                    for (weekday, hour), grouped in cells.items():
                        cell_rows.append(
                            self._cell_row(
                                "log_source_id",
                                source.log_source_id,
                                metric_name,
                                weekday,
                                hour,
                                grouped,
                                tally,
                                candidates,
                                start,
                                train_through,
                                now,
                                source.profile_version,
                                IPS_SOURCE_METRIC_SEMANTICS[metric_name],
                            )
                        )
                    written[source.log_source_id] += len(cells)
            await self._upsert(IpsSourceBaseline, "log_source_id", cell_rows)
        await self.session.flush()
        return written

    async def rebuild_due(
        self,
        *,
        train_through: datetime,
        limit: int | None = None,
        now: datetime | None = None,
        log_source_id: uuid.UUID | None = None,
    ) -> dict[str, dict[uuid.UUID, int]]:
        """Rebuild the least recently computed active entities and valid sources.

        With ``log_source_id`` only that source's subjects (its entities and its
        own cells) are rebuilt, so ``train_through`` is that source's evaluation
        bound and never reaches another source's data. The cost is the same
        constant set of statements per call, whatever the entity count.
        """
        limit = limit or self.settings.ips_baseline_rebuild_batch_size
        entity_last = (
            select(
                IpsEntityBaseline.ips_entity_id.label("subject_id"),
                func.max(IpsEntityBaseline.computed_at).label("rebuilt_at"),
            )
            .group_by(IpsEntityBaseline.ips_entity_id)
            .subquery()
        )
        entities = list(
            (
                await self.session.scalars(
                    select(IpsEntity)
                    .outerjoin(entity_last, entity_last.c.subject_id == IpsEntity.id)
                    .where(
                        IpsEntity.active.is_(True),
                        *(
                            [IpsEntity.log_source_id == log_source_id]
                            if log_source_id is not None
                            else []
                        ),
                    )
                    .order_by(entity_last.c.rebuilt_at.asc().nulls_first(), IpsEntity.id)
                    .limit(limit)
                )
            ).all()
        )
        source_last = (
            select(
                IpsSourceBaseline.log_source_id.label("subject_id"),
                func.max(IpsSourceBaseline.computed_at).label("rebuilt_at"),
            )
            .group_by(IpsSourceBaseline.log_source_id)
            .subquery()
        )
        sources = list(
            (
                await self.session.scalars(
                    select(IpsSourceProfile)
                    .outerjoin(
                        source_last,
                        source_last.c.subject_id == IpsSourceProfile.log_source_id,
                    )
                    .where(
                        IpsSourceProfile.enabled.is_(True),
                        *(
                            [IpsSourceProfile.log_source_id == log_source_id]
                            if log_source_id is not None
                            else []
                        ),
                        IpsSourceProfile.validation_status == IpsProfileValidationStatus.VALID,
                        IpsSourceProfile.validated_profile_version
                        == IpsSourceProfile.profile_version,
                    )
                    .order_by(source_last.c.rebuilt_at.asc().nulls_first(), IpsSourceProfile.id)
                    .limit(limit)
                )
            ).all()
        )
        return {
            "entities": await self.rebuild_entities(
                entities, train_through=train_through, now=now
            ),
            "sources": await self.rebuild_sources(sources, train_through=train_through, now=now),
        }

    async def _source_buckets(
        self, source_ids: set[uuid.UUID], start: datetime, end: datetime
    ) -> dict[uuid.UUID, list[IpsSourceBucketFacts]]:
        result = await self.session.scalars(
            select(IpsSourceBucket)
            .where(
                IpsSourceBucket.log_source_id.in_(source_ids),
                IpsSourceBucket.bucket_start >= start,
                IpsSourceBucket.bucket_start < end,
            )
            .order_by(IpsSourceBucket.bucket_start)
        )
        out: dict[uuid.UUID, list[IpsSourceBucketFacts]] = {}
        for bucket in result.all():
            out.setdefault(bucket.log_source_id, []).append(IpsSourceBucketFacts.of(bucket))
        return out

    async def _entity_rows(
        self, entities: Sequence[IpsEntity], start: datetime, end: datetime
    ) -> dict[uuid.UUID, dict[datetime, tuple[int, int]]]:
        result = await self.session.execute(
            select(
                IpsEntityMetric.ips_entity_id,
                IpsEntityMetric.bucket_start,
                IpsEntityMetric.events,
                IpsEntityMetric.bucket_seconds,
            ).where(
                IpsEntityMetric.ips_entity_id.in_([entity.id for entity in entities]),
                IpsEntityMetric.bucket_start >= start,
                IpsEntityMetric.bucket_start < end,
            )
        )
        out: dict[uuid.UUID, dict[datetime, tuple[int, int]]] = {}
        for row in result.all():
            out.setdefault(row.ips_entity_id, {})[row.bucket_start] = (
                row.events,
                row.bucket_seconds,
            )
        return out

    async def _entity_spans(
        self, entities: Sequence[IpsEntity], end: datetime
    ) -> dict[uuid.UUID, list[tuple[datetime, datetime]]]:
        result = await self.session.execute(
            select(
                IpsAnomaly.ips_entity_id,
                IpsAnomaly.detected_at,
                IpsAnomaly.anomaly_end,
                IpsAnomaly.resolved_at,
            ).where(
                IpsAnomaly.ips_entity_id.in_([entity.id for entity in entities]),
                IpsAnomaly.detected_at < end,
            )
        )
        out: dict[uuid.UUID, list[tuple[datetime, datetime]]] = {}
        for row in result.all():
            out.setdefault(row.ips_entity_id, []).append(
                anomaly_exclusion_span(row.detected_at, row.anomaly_end, row.resolved_at, end)
            )
        return out

    async def _source_spans(
        self, sources: Sequence[IpsSourceProfile], end: datetime
    ) -> dict[tuple[uuid.UUID, IpsAnomalyType], list[tuple[datetime, datetime]]]:
        result = await self.session.execute(
            select(
                IpsAnomaly.log_source_id,
                IpsAnomaly.detector_type,
                IpsAnomaly.detected_at,
                IpsAnomaly.anomaly_end,
                IpsAnomaly.resolved_at,
            ).where(
                IpsAnomaly.log_source_id.in_([source.log_source_id for source in sources]),
                IpsAnomaly.detected_at < end,
            )
        )
        out: dict[tuple[uuid.UUID, IpsAnomalyType], list[tuple[datetime, datetime]]] = {}
        for row in result.all():
            detector = IpsAnomalyType(str(row.detector_type))
            out.setdefault((row.log_source_id, detector), []).append(
                anomaly_exclusion_span(row.detected_at, row.anomaly_end, row.resolved_at, end)
            )
        return out

    @staticmethod
    def _group(samples: Sequence[IpsSample]) -> dict[tuple[int, int], list[IpsSample]]:
        cells: dict[tuple[int, int], list[IpsSample]] = {}
        for sample in samples:
            cells.setdefault(seasonal_key(sample.bucket_start), []).append(sample)
        return cells

    def _cell_row(
        self,
        subject_column: str,
        subject_id: uuid.UUID,
        metric_name: str,
        weekday: int,
        hour: int,
        samples: Sequence[IpsSample],
        tally: IpsExclusionTally,
        candidates: int,
        window_start: datetime,
        window_end: datetime,
        computed_at: datetime,
        profile_version: int,
        semantics: str,
    ) -> dict:
        values = [sample.value for sample in samples]
        cell = summarize_cell(values, min_samples=self.settings.baseline_min_samples)
        row = {
            "id": uuid.uuid4(),
            subject_column: subject_id,
            "metric_name": metric_name,
            "weekday": weekday,
            "hour": hour,
            "median": cell.median,
            "mad": cell.mad,
            "p05": cell.p05,
            "p95": cell.p95,
            "sample_count": len(values),
            "is_reliable": cell.is_reliable,
            "completeness": retained_fraction(len(values), candidates),
            "observations": cell.observations,
            "window_start": window_start,
            "window_end": window_end,
            "computed_at": computed_at,
            "baseline_version": 1,
            "excluded_sample_count": tally.total,
            "exclusion_counts": tally.as_dict(),
            "metric_semantics": semantics,
            "profile_version": profile_version,
            "normalizer_version": IPS_NORMALIZER_VERSION,
        }
        if subject_column == "log_source_id":
            row.update(
                numerator_sum=sum(
                    sample.numerator for sample in samples if sample.numerator is not None
                )
                if any(sample.numerator is not None for sample in samples)
                else None,
                denominator_sum=sum(
                    sample.denominator for sample in samples if sample.denominator is not None
                )
                if any(sample.denominator is not None for sample in samples)
                else None,
            )
        return row

    async def _upsert(
        self, model: type[IpsEntityBaseline] | type[IpsSourceBaseline], subject: str,
        rows: list[dict],
    ) -> None:
        if not rows:
            return
        table = cast(Table, model.__table__)
        stmt = pg_insert(table)
        key = {"id", subject, "metric_name", "weekday", "hour", "baseline_version"}
        replaced = {name: stmt.excluded[name] for name in rows[0] if name not in key}
        stmt = stmt.on_conflict_do_update(
            index_elements=[subject, "metric_name", "weekday", "hour"],
            set_={
                **replaced,
                "baseline_version": table.c.baseline_version + 1,
                "updated_at": func.now(),
            },
        )
        await self.session.execute(stmt, rows)
        for obj in list(self.session.identity_map.values()):
            if isinstance(obj, model):
                self.session.expire(obj)
