"""Fleet-wide IPS collection with bounded admission and bulk persistence.

One run plans every eligible source together. Sources sharing a window and
field references share keyed searches; the tick-wide search budget is shared by
all dimensions and windows. Raw keyed rows are folded before this module builds
any database value.

A source bucket is written only when TOTALS is COMPLETE. Other dimensions are
independent: FAILED, REJECTED and NOT_COLLECTED are persisted as states with
NULL facts and coverage. Such a bucket advances the watermark, matching WAF's
optional-breakdown rule, because the missing value remains explicit and can
never be interpreted as zero.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, select, text, tuple_, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.anomaly.seasonal import in_maintenance
from app.collectors.metric_collector import floor_to_interval
from app.core.config import Settings, get_settings
from app.ips.collection_plan import plan_searches
from app.ips.fold import (
    IpsActionFact,
    IpsEntityBucketFact,
    IpsEntityFact,
    IpsFanoutFact,
    IpsSourceIpFact,
    IpsTotalsFact,
    coverage,
    fold_outcome,
)
from app.ips.keyed_search import IpsCellOutcome, IpsSearchBudget, run_dimension
from app.ips.profile import IpsFieldMapping, IpsProfileError
from app.models.enums import (
    IpsCellState,
    IpsDimension,
    IpsMeasurement,
    IpsProfileValidationStatus,
    SearchFamily,
)
from app.models.instance import QRadarInstance
from app.models.ips import (
    IPS_NORMALIZER_VERSION,
    IpsEntity,
    IpsEntityMetric,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.providers.base import QRadarProvider
from app.services.ips_pipeline import ips_pipeline_lock_key
from app.services.locks import collector_lock_key
from app.services.search_cadence import SearchCadenceService
from app.waf.admission import AdmissionPolicy, decide_admission

COLLECTOR_NAME = "ips_collection"
ENTITY_DIMENSIONS = (
    IpsDimension.DESTINATION_PORT,
    IpsDimension.DESTINATION_IP,
    IpsDimension.SIGNATURE,
)


def watermark_collector(qradar_log_source_id: int) -> str:
    return f"{COLLECTOR_NAME}:{int(qradar_log_source_id)}"


@dataclass
class IpsCollectionReport:
    instance_id: uuid.UUID
    status: str = "ok"
    sources_planned: int = 0
    sources_collected: int = 0
    sources_locked: int = 0
    skipped: dict[str, int] = field(default_factory=dict)
    searches_sent: int = 0
    buckets_written: int = 0
    entity_rows_written: int = 0
    entities_admitted: int = 0
    entities_reactivated: int = 0
    entities_retired: int = 0
    watermarks_advanced: int = 0

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "sources_planned": self.sources_planned,
            "sources_collected": self.sources_collected,
            "sources_locked": self.sources_locked,
            "skipped": self.skipped,
            "searches_sent": self.searches_sent,
            "buckets_written": self.buckets_written,
            "entity_rows_written": self.entity_rows_written,
            "entities_admitted": self.entities_admitted,
            "entities_reactivated": self.entities_reactivated,
            "entities_retired": self.entities_retired,
            "watermarks_advanced": self.watermarks_advanced,
        }


@dataclass(frozen=True)
class _Source:
    profile: IpsSourceProfile
    log_source: LogSource
    mapping: IpsFieldMapping
    window: tuple[datetime, datetime]


@dataclass(frozen=True)
class _Candidate:
    fact: IpsEntityFact
    dimension: IpsDimension

    @property
    def virtual_host(self) -> None:
        return None

    @property
    def endpoint(self) -> str:
        return self.fact.value_key

    @property
    def method(self) -> str:
        return self.dimension.value

    @property
    def request_count(self) -> int:
        return self.fact.events


class _IpsFleetLocks:
    """Take every per-source IPS pipeline lock in two SQL statements total."""

    def __init__(
        self,
        session: AsyncSession,
        settings: Settings,
        instance_id: uuid.UUID,
        qradar_ids: Sequence[int],
    ) -> None:
        self.session = session
        self.settings = settings
        self.instance_id = instance_id
        self.qradar_ids = tuple(qradar_ids)
        self.acquired: set[int] = set()
        self._keys: dict[int, int] = {}

    async def __aenter__(self) -> set[int]:
        bind = self.session.bind
        if bind is None or bind.dialect.name != "postgresql":
            self.acquired = set(self.qradar_ids)
            return self.acquired
        self._keys = {
            source_id: collector_lock_key(
                self.instance_id, ips_pipeline_lock_key(source_id)
            )
            for source_id in self.qradar_ids
        }
        if not self._keys:
            return self.acquired
        values = ", ".join(
            f"(CAST(:source_{index} AS integer), CAST(:key_{index} AS integer))"
            for index in range(len(self._keys))
        )
        params: dict[str, int] = {"namespace": self.settings.collection_advisory_lock_namespace}
        for index, (source_id, key) in enumerate(self._keys.items()):
            params[f"source_{index}"] = source_id
            params[f"key_{index}"] = key
        lock_sql = (
            "SELECT v.source_id, pg_try_advisory_lock("  # noqa: S608
            "CAST(:namespace AS integer), v.lock_key) "
            f"FROM (VALUES {values}) AS v(source_id, lock_key)"
        )
        rows = await self.session.execute(text(lock_sql), params)
        self.acquired = {int(source_id) for source_id, held in rows if held}
        return self.acquired

    async def __aexit__(self, *exc: object) -> None:
        if not self.acquired or not self._keys:
            return
        keys = [self._keys[source_id] for source_id in sorted(self.acquired)]
        await self.session.execute(
            text(
                "SELECT pg_advisory_unlock(:namespace, lock_key) "
                "FROM unnest(CAST(:keys AS integer[])) AS lock_key"
            ),
            {"namespace": self.settings.collection_advisory_lock_namespace, "keys": keys},
        )


class IpsCollector:
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
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))

    async def collect(self, instance: QRadarInstance) -> IpsCollectionReport:
        report = IpsCollectionReport(instance.id)
        if not self.settings.ips_collection_enabled:
            report.status = "disabled"
            return report
        now = self._clock()
        interval = self.settings.collection_interval_seconds
        boundary = floor_to_interval(now, interval)
        rows = (
            await self.session.execute(
                select(IpsSourceProfile, LogSource)
                .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
                .where(IpsSourceProfile.instance_id == instance.id)
                .order_by(LogSource.qradar_id)
            )
        ).all()
        cadence = await SearchCadenceService(self.session, self.settings).get(
            SearchFamily.IPS_COLLECTION
        )
        max_buckets = max(1, cadence.cadence_seconds // interval)
        max_buckets += self.settings.collection_max_backfill_intervals
        watermark_rows = await self._load_watermarks(instance, [source for _, source in rows])

        eligible: list[_Source] = []
        skipped_rows: list[dict[str, object]] = []
        for profile, source in rows:
            reason = self._ineligibility(profile, source)
            mapping: IpsFieldMapping | None = None
            if reason is None:
                try:
                    mapping = IpsFieldMapping.from_profile(profile)
                except IpsProfileError:
                    reason = "invalid profile mapping"
            if reason is not None or mapping is None:
                report.skipped[reason or "invalid profile mapping"] = (
                    report.skipped.get(reason or "invalid profile mapping", 0) + 1
                )
                skipped_rows.append(
                    self._watermark_values(
                        instance,
                        source,
                        watermark_rows.get(source.qradar_id),
                        now=now,
                        skip_reason=reason or "invalid profile mapping",
                    )
                )
                continue
            watermark = watermark_rows.get(source.qradar_id)
            start = (
                watermark.watermark_at + timedelta(seconds=interval)
                if watermark is not None and watermark.watermark_at is not None
                else boundary - timedelta(seconds=interval)
            )
            end = min(boundary, start + timedelta(seconds=interval * max_buckets))
            if start >= end:
                continue
            eligible.append(_Source(profile, source, mapping, (start, end)))

        report.sources_planned = len(eligible)
        async with _IpsFleetLocks(
            self.session,
            self.settings,
            instance.id,
            [item.log_source.qradar_id for item in eligible],
        ) as acquired:
            locked = [item for item in eligible if item.log_source.qradar_id in acquired]
            report.sources_locked = len(eligible) - len(locked)
            if report.sources_locked:
                report.skipped["source locked"] = report.sources_locked
            budget = IpsSearchBudget(self.settings.search_cadence_max_searches_per_tick)
            outcomes = await self._search(locked, interval, budget)
            report.searches_sent = budget.used
            await self._persist(
                instance,
                locked,
                outcomes,
                watermark_rows,
                skipped_rows,
                now,
                interval,
                report,
            )
        return report

    def _ineligibility(self, profile: IpsSourceProfile, source: LogSource) -> str | None:
        if not profile.enabled:
            return "profile disabled"
        if not source.monitoring_enabled:
            return "monitoring disabled"
        if profile.validated_profile_version != profile.profile_version:
            return "current profile version is not validated"
        if profile.validation_status != IpsProfileValidationStatus.VALID:
            return f"profile validation is {profile.validation_status}"
        return None

    async def _load_watermarks(
        self, instance: QRadarInstance, sources: Sequence[LogSource]
    ) -> dict[int, CollectionWatermark]:
        collectors = [watermark_collector(source.qradar_id) for source in sources]
        if not collectors:
            return {}
        rows = (
            await self.session.scalars(
                select(CollectionWatermark).where(
                    CollectionWatermark.instance_id == instance.id,
                    CollectionWatermark.collector.in_(collectors),
                )
            )
        ).all()
        by_name = {row.collector: row for row in rows}
        return {
            source.qradar_id: row
            for source in sources
            if (row := by_name.get(watermark_collector(source.qradar_id))) is not None
        }

    async def _search(
        self, sources: Sequence[_Source], bucket_seconds: int, budget: IpsSearchBudget
    ) -> dict[tuple[int, datetime, IpsDimension], IpsCellOutcome]:
        by_window: dict[tuple[datetime, datetime], list[_Source]] = defaultdict(list)
        for source in sources:
            by_window[source.window].append(source)
        out: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome] = {}
        for window in sorted(by_window):
            members = by_window[window]
            mapping_by_id = {
                member.log_source.qradar_id: member.mapping for member in members
            }
            plan = plan_searches(
                mapping_by_id.items(),
                max_sources_per_search=self.settings.ips_max_sources_per_search,
            )
            for search in plan:
                cells = await run_dimension(
                    self.provider,
                    search.dimension,
                    search.group,
                    window,
                    bucket_seconds,
                    max_rows=self.settings.ariel_multi_bucket_max_rows,
                    budget=budget,
                )
                for cell in cells:
                    out[(cell.qradar_log_source_id, cell.bucket_start, cell.dimension)] = cell
        return out

    async def _persist(
        self,
        instance: QRadarInstance,
        sources: Sequence[_Source],
        outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
        watermarks: dict[int, CollectionWatermark],
        skipped_rows: list[dict[str, object]],
        now: datetime,
        bucket_seconds: int,
        report: IpsCollectionReport,
    ) -> None:
        source_uuids = [item.log_source.id for item in sources]
        existing_rows = (
            (
                await self.session.scalars(
                    select(IpsEntity).where(
                        IpsEntity.log_source_id.in_(source_uuids),
                    )
                )
            ).all()
            if source_uuids
            else []
        )
        entities = {
            (row.log_source_id, str(row.dimension), row.value_key): row for row in existing_rows
        }
        active_counts: dict[tuple[uuid.UUID, str], int] = defaultdict(int)
        for row in existing_rows:
            if row.active:
                active_counts[(row.log_source_id, str(row.dimension))] += 1

        entity_upserts: dict[tuple[uuid.UUID, str, str], dict[str, object]] = {}
        metric_rows: list[dict[str, object]] = []
        bucket_rows: list[dict[str, object]] = []
        collected_pairs: list[tuple[uuid.UUID, datetime]] = []
        watermark_values = list(skipped_rows)

        for item in sources:
            source = item.log_source
            cursor = item.window[0]
            last_done = (
                watermarks[source.qradar_id].watermark_at
                if source.qradar_id in watermarks
                else None
            )
            collected = 0
            while cursor < item.window[1]:
                totals_outcome = outcomes.get(
                    (source.qradar_id, cursor, IpsDimension.TOTALS)
                )
                if totals_outcome is None or totals_outcome.state is not IpsCellState.COMPLETE:
                    break
                totals = fold_outcome(
                    totals_outcome,
                    mapping=item.mapping,
                    max_entities=self.settings.ips_max_entities_per_bucket,
                )
                if not isinstance(totals, IpsTotalsFact):
                    break
                bucket_values, new_metrics = self._bucket_values(
                    instance,
                    item,
                    cursor,
                    bucket_seconds,
                    totals,
                    outcomes,
                    entities,
                    active_counts,
                    entity_upserts,
                    now,
                    report,
                )
                bucket_rows.append(bucket_values)
                metric_rows.extend(new_metrics)
                collected_pairs.append((source.id, cursor))
                last_done = cursor
                collected += 1
                cursor += timedelta(seconds=bucket_seconds)
            if collected:
                report.sources_collected += 1
                report.buckets_written += collected
                report.watermarks_advanced += 1
            watermark_values.append(
                self._watermark_values(
                    instance,
                    source,
                    watermarks.get(source.qradar_id),
                    now=now,
                    watermark_at=last_done,
                    intervals_added=collected,
                    searches=report.searches_sent,
                )
            )

        if collected_pairs:
            condition = tuple_(IpsEntity.log_source_id, IpsEntityMetric.bucket_start).in_(
                collected_pairs
            )
            await self.session.execute(
                delete(IpsEntityMetric)
                .where(
                    exists(
                        select(1).where(
                            IpsEntity.id == IpsEntityMetric.ips_entity_id,
                            condition,
                        )
                    )
                )
                .execution_options(synchronize_session=False)
            )
        if entity_upserts:
            values = list(entity_upserts.values())
            statement = pg_insert(IpsEntity).values(values)
            statement = statement.on_conflict_do_update(
                constraint="uq_ips_entity_natural_key",
                set_={
                    "display_name": func.coalesce(
                        statement.excluded.display_name, IpsEntity.display_name
                    ),
                    "last_seen_at": func.greatest(
                        statement.excluded.last_seen_at, IpsEntity.last_seen_at
                    ),
                    "active": True,
                    "retired_at": None,
                    "normalizer_version": statement.excluded.normalizer_version,
                },
            )
            await self.session.execute(statement)
        if metric_rows:
            statement = pg_insert(IpsEntityMetric).values(metric_rows)
            statement = statement.on_conflict_do_update(
                index_elements=["ips_entity_id", "bucket_start"],
                set_={
                    "events": statement.excluded.events,
                    "bucket_seconds": statement.excluded.bucket_seconds,
                },
            )
            await self.session.execute(statement)
            report.entity_rows_written = len(metric_rows)
        if bucket_rows:
            statement = pg_insert(IpsSourceBucket).values(bucket_rows)
            statement = statement.on_conflict_do_update(
                index_elements=["log_source_id", "bucket_start"],
                set_={
                    key: statement.excluded[key]
                    for key in bucket_rows[0]
                    if key not in ("log_source_id", "bucket_start")
                },
            )
            await self.session.execute(statement)
        if watermark_values:
            statement = pg_insert(CollectionWatermark).values(watermark_values)
            statement = statement.on_conflict_do_update(
                constraint="uq_collection_watermark_key",
                set_={
                    "watermark_at": statement.excluded.watermark_at,
                    "last_run_at": statement.excluded.last_run_at,
                    "lag_seconds": statement.excluded.lag_seconds,
                    "intervals_collected": statement.excluded.intervals_collected,
                    "consecutive_failures": statement.excluded.consecutive_failures,
                    "last_error": statement.excluded.last_error,
                    "collection_metadata": statement.excluded.collection_metadata,
                },
            )
            await self.session.execute(statement)
        if source_uuids:
            cutoff = now - timedelta(days=self.settings.ips_entity_active_days)
            retired = await self.session.execute(
                update(IpsEntity)
                .where(
                    IpsEntity.log_source_id.in_(source_uuids),
                    IpsEntity.active.is_(True),
                    IpsEntity.last_seen_at < cutoff,
                )
                .values(active=False, retired_at=now)
                .returning(IpsEntity.id)
                .execution_options(synchronize_session=False)
            )
            report.entities_retired = len(retired.all())

    def _bucket_values(
        self,
        instance: QRadarInstance,
        item: _Source,
        bucket_start: datetime,
        bucket_seconds: int,
        totals: IpsTotalsFact,
        outcomes: dict[tuple[int, datetime, IpsDimension], IpsCellOutcome],
        entities: dict[tuple[uuid.UUID, str, str], IpsEntity],
        active_counts: dict[tuple[uuid.UUID, str], int],
        entity_upserts: dict[tuple[uuid.UUID, str, str], dict[str, object]],
        now: datetime,
        report: IpsCollectionReport,
    ) -> tuple[dict[str, object], list[dict[str, object]]]:
        source = item.log_source
        measurements: dict[IpsDimension, str] = {}
        facts: dict[IpsDimension, object | None] = {}
        provenance: dict[str, object] = {}
        for dimension in IpsDimension:
            outcome = outcomes.get((source.qradar_id, bucket_start, dimension))
            if outcome is None:
                if dimension is not IpsDimension.TOTALS:
                    measurements[dimension] = IpsCellState.NOT_COLLECTED.value
                facts[dimension] = None
                continue
            if dimension is not IpsDimension.TOTALS:
                measurements[dimension] = (
                    IpsMeasurement.EXACT.value
                    if outcome.state is IpsCellState.COMPLETE
                    else outcome.state.value
                )
            facts[dimension] = fold_outcome(
                outcome,
                mapping=item.mapping,
                max_entities=self.settings.ips_max_entities_per_bucket,
            )
            if outcome.provenance is not None:
                p = outcome.provenance
                provenance[dimension.value] = {
                    "search_window_start": p.search_window_start.isoformat(),
                    "search_window_end": p.search_window_end.isoformat(),
                    "search_bucket_count": p.search_bucket_count,
                    "search_source_count": p.search_source_count,
                    "split_depth": p.split_depth,
                    "bucket_key": p.bucket_key,
                }

        metrics: list[dict[str, object]] = []
        admitted: dict[str, int] = {}
        below: dict[str, int] = {}
        rejected: dict[str, int] = {}
        untracked: dict[str, int] = {}
        untracked_events: dict[str, int] = {}
        admission_truncated: dict[str, bool] = {}
        policy = AdmissionPolicy(
            max_new_per_bucket=self.settings.ips_max_new_entities_per_bucket,
            max_active=self.settings.ips_max_active_entities_per_source_dimension,
            min_requests=self.settings.ips_min_admission_events,
        )
        for dimension in ENTITY_DIMENSIONS:
            fact = facts.get(dimension)
            if not isinstance(fact, IpsEntityBucketFact):
                continue
            existing_active: list[tuple[IpsEntityFact, uuid.UUID]] = []
            unseen: list[_Candidate] = []
            for entity_fact in fact.entities:
                key = (source.id, dimension.value, entity_fact.value_key)
                current = entities.get(key)
                if current is not None and current.active:
                    existing_active.append((entity_fact, current.id))
                else:
                    unseen.append(_Candidate(entity_fact, dimension))
            decision = decide_admission(
                unseen,
                active_count=active_counts[(source.id, dimension.value)],
                policy=policy,
            )
            overflow_count = max(0, fact.entity_count - len(fact.entities))
            overflow_events = max(
                0,
                fact.dimension_events
                - fact.unattributed_events
                - sum(entity.events for entity in fact.entities),
            )
            admitted[dimension.value] = len(decision.admitted)
            below[dimension.value] = len(decision.below_threshold)
            rejected[dimension.value] = len(decision.over_quota)
            untracked[dimension.value] = len(decision.untracked) + overflow_count
            untracked_events[dimension.value] = decision.untracked_requests + overflow_events
            admission_truncated[dimension.value] = (
                fact.measurement is IpsMeasurement.LOWER_BOUND
                or decision.truncated
                or overflow_count > 0
            )
            for entity_fact, entity_id in existing_active:
                self._queue_entity(
                    instance, source, dimension, entity_fact, entity_id,
                    bucket_start, entity_upserts,
                )
                metrics.append(
                    self._metric_values(entity_id, bucket_start, bucket_seconds, entity_fact.events)
                )
            for candidate in decision.admitted:
                entity_fact = candidate.fact
                key = (source.id, dimension.value, entity_fact.value_key)
                current = entities.get(key)
                entity_id = current.id if current is not None else uuid.uuid4()
                if current is not None:
                    # The bulk upsert performs the database mutation. Marking
                    # these as committed only keeps later buckets in this run
                    # from re-admitting the same entity through an ORM UPDATE.
                    set_committed_value(current, "active", True)
                    set_committed_value(current, "retired_at", None)
                    report.entities_reactivated += 1
                else:
                    entities[key] = IpsEntity(
                        id=entity_id,
                        instance_id=instance.id,
                        log_source_id=source.id,
                        dimension=dimension,
                        value_key=entity_fact.value_key,
                        display_name=entity_fact.display_name,
                        first_seen_at=bucket_start,
                        last_seen_at=bucket_start,
                        active=True,
                        normalizer_version=IPS_NORMALIZER_VERSION,
                    )
                active_counts[(source.id, dimension.value)] += 1
                report.entities_admitted += 1
                self._queue_entity(
                    instance, source, dimension, entity_fact, entity_id,
                    bucket_start, entity_upserts,
                )
                metrics.append(
                    self._metric_values(entity_id, bucket_start, bucket_seconds, entity_fact.events)
                )

        def coverage_values(dimension: IpsDimension) -> tuple[float | None, bool | None]:
            fact = facts.get(dimension)
            coverage_fact = (
                fact
                if isinstance(fact, (IpsEntityBucketFact, IpsSourceIpFact, IpsActionFact))
                else None
            )
            measured = coverage(coverage_fact, totals)
            if measured is None:
                return None, None
            return min(1.0, max(0.0, measured.ratio)), measured.lower_bound

        source_fact = facts.get(IpsDimension.SOURCE_IP)
        fanout_fact = facts.get(IpsDimension.PORT_FANOUT)
        action_fact = facts.get(IpsDimension.ACTION)
        contributors: dict[str, object] = {}
        if isinstance(source_fact, IpsSourceIpFact):
            contributors[IpsDimension.SOURCE_IP.value] = [
                {"source_ip": address, "events": events}
                for address, events in source_fact.contributors
            ]
        if isinstance(fanout_fact, IpsFanoutFact):
            contributors[IpsDimension.PORT_FANOUT.value] = [
                {"source_ip": address, "distinct_ports": ports}
                for address, ports in fanout_fact.contributors
            ]
        row: dict[str, object] = {
            "log_source_id": source.id,
            "bucket_start": bucket_start,
            "bucket_seconds": bucket_seconds,
            "total_events": totals.events,
            "total_records": totals.records,
            "port_fanout_measurement": measurements[IpsDimension.PORT_FANOUT],
            "contributors": contributors,
            "admitted": admitted,
            "below_admission_threshold": below,
            "rejected_over_quota": rejected,
            "untracked_entities": untracked,
            "untracked_events": untracked_events,
            "admission_truncated": admission_truncated,
            "profile_version": item.profile.profile_version,
            "normalizer_version": IPS_NORMALIZER_VERSION,
            "query_provenance": provenance,
            "in_maintenance": in_maintenance(
                bucket_start,
                maintenance_mode=source.maintenance_mode,
                maintenance_until=source.maintenance_until,
            ),
            "collected_at": now,
            "source_events": (
                source_fact.source_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "distinct_sources": (
                source_fact.distinct_sources
                if isinstance(source_fact, IpsSourceIpFact)
                else None
            ),
            "distinct_sources_lower_bound": (
                source_fact.measurement is IpsMeasurement.LOWER_BOUND
                if isinstance(source_fact, IpsSourceIpFact)
                else None
            ),
            "top1_events": (
                source_fact.top1_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "top5_events": (
                source_fact.top5_events if isinstance(source_fact, IpsSourceIpFact) else None
            ),
            "max_distinct_ports": (
                fanout_fact.max_distinct_ports if isinstance(fanout_fact, IpsFanoutFact) else None
            ),
            "blocked_events": (
                action_fact.blocked_events if isinstance(action_fact, IpsActionFact) else None
            ),
            "allowed_events": (
                action_fact.allowed_events if isinstance(action_fact, IpsActionFact) else None
            ),
            "unmapped_events": (
                action_fact.unmapped_events if isinstance(action_fact, IpsActionFact) else None
            ),
        }
        for dimension, prefix in (
            (IpsDimension.DESTINATION_PORT, "destination_port"),
            (IpsDimension.DESTINATION_IP, "destination_ip"),
            (IpsDimension.SIGNATURE, "signature"),
            (IpsDimension.SOURCE_IP, "source_ip"),
            (IpsDimension.ACTION, "action"),
        ):
            ratio, lower = coverage_values(dimension)
            row[f"{prefix}_measurement"] = measurements[dimension]
            row[f"{prefix}_coverage_ratio"] = ratio
            row[f"{prefix}_coverage_lower_bound"] = lower
        return row, metrics

    @staticmethod
    def _metric_values(
        entity_id: uuid.UUID, bucket_start: datetime, bucket_seconds: int, events: int
    ) -> dict[str, object]:
        return {
            "ips_entity_id": entity_id,
            "bucket_start": bucket_start,
            "events": events,
            "bucket_seconds": bucket_seconds,
        }

    @staticmethod
    def _queue_entity(
        instance: QRadarInstance,
        source: LogSource,
        dimension: IpsDimension,
        fact: IpsEntityFact,
        entity_id: uuid.UUID,
        bucket_start: datetime,
        rows: dict[tuple[uuid.UUID, str, str], dict[str, object]],
    ) -> None:
        key = (source.id, dimension.value, fact.value_key)
        previous = rows.get(key)
        first_seen = previous["first_seen_at"] if previous is not None else bucket_start
        rows[key] = {
            "id": entity_id,
            "instance_id": instance.id,
            "log_source_id": source.id,
            "dimension": dimension.value,
            "value_key": fact.value_key,
            "display_name": fact.display_name,
            "first_seen_at": first_seen,
            "last_seen_at": bucket_start,
            "active": True,
            "retired_at": None,
            "normalizer_version": IPS_NORMALIZER_VERSION,
        }

    @staticmethod
    def _watermark_values(
        instance: QRadarInstance,
        source: LogSource,
        existing: CollectionWatermark | None,
        *,
        now: datetime,
        watermark_at: datetime | None = None,
        intervals_added: int = 0,
        searches: int = 0,
        skip_reason: str | None = None,
    ) -> dict[str, object]:
        current = (
            watermark_at
            if watermark_at is not None
            else (existing.watermark_at if existing else None)
        )
        metadata = {
            "last_run_searches": searches,
            **({"last_skip_reason": skip_reason} if skip_reason is not None else {}),
        }
        lag = None
        if current is not None:
            lag = max(0, int((now - current).total_seconds()))
        return {
            "id": existing.id if existing is not None else uuid.uuid4(),
            "instance_id": instance.id,
            "collector": watermark_collector(source.qradar_id),
            "watermark_at": current,
            "last_run_at": now,
            "lag_seconds": lag,
            "intervals_collected": (existing.intervals_collected if existing else 0)
            + intervals_added,
            "consecutive_failures": existing.consecutive_failures if existing else 0,
            "last_error": existing.last_error if existing else None,
            "collection_metadata": metadata,
        }
