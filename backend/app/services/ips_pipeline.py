"""Scheduled IPS pipeline: collect, evaluate, then train.

Fleet collection writes every eligible source first. For each source with a
bucket this service then evaluates collected, not-yet-evaluated buckets oldest
first and only afterwards rebuilds baselines through the end of the last
evaluated bucket. A bucket therefore cannot teach the baseline that judges it.

Each source runs in a savepoint. A failure keeps its collection and evaluation
watermarks where they were, records a sanitized error on the evaluation
watermark, and does not stop later sources.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.ips.baseline import IpsBaselineBuilder
from app.ips.engine import IpsAnomalyEngine
from app.models.enums import SearchFamily
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceBucket, IpsSourceProfile
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.services.search_cadence import SearchCadenceService
from app.services.waf_pipeline import sanitized_error

IPS_PIPELINE_LOCK = "ips_pipeline"
EVALUATION_COLLECTOR = "ips_evaluation"


def ips_pipeline_lock_key(qradar_log_source_id: int) -> str:
    """The lock validation and later collection share for one IPS source."""
    return f"{IPS_PIPELINE_LOCK}:{int(qradar_log_source_id)}"


def collection_collector(qradar_log_source_id: int) -> str:
    """The collection watermark key of one IPS source.

    Imported at call time: the collector imports this module's lock key, so a
    module-level import would be circular.
    """
    from app.collectors.ips_collector import watermark_collector

    return watermark_collector(qradar_log_source_id)


def evaluation_collector(qradar_log_source_id: int) -> str:
    """Watermark key for one QRadar source's IPS detector progress."""
    return f"{EVALUATION_COLLECTOR}:{int(qradar_log_source_id)}"


@dataclass
class IpsSourcePipelineReport:
    log_source_id: uuid.UUID
    evaluated_buckets: list[datetime] = field(default_factory=list)
    subjects_evaluated: int = 0
    inferred_observations: int = 0
    opened: int = 0
    resolved: int = 0
    evaluated_through: datetime | None = None
    status: str = "ok"
    error: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "log_source_id": str(self.log_source_id),
            "status": self.status,
            "error": self.error,
            "evaluated_buckets": len(self.evaluated_buckets),
            "subjects_evaluated": self.subjects_evaluated,
            "inferred_observations": self.inferred_observations,
            "opened": self.opened,
            "resolved": self.resolved,
            "evaluated_through": (
                self.evaluated_through.isoformat() if self.evaluated_through else None
            ),
        }


@dataclass
class IpsPipelineReport:
    instance_id: uuid.UUID
    sources: list[IpsSourcePipelineReport] = field(default_factory=list)
    baseline_train_through: datetime | None = None
    baseline_entities_rebuilt: int = 0
    baseline_sources_rebuilt: int = 0
    #: Why training did not run this tick, or None when it ran.
    baseline_skipped: str | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "sources": len(self.sources),
            "evaluated_buckets": sum(len(item.evaluated_buckets) for item in self.sources),
            "opened": sum(item.opened for item in self.sources),
            "resolved": sum(item.resolved for item in self.sources),
            "failed_sources": sum(item.status == "failed" for item in self.sources),
            "baseline_train_through": (
                self.baseline_train_through.isoformat() if self.baseline_train_through else None
            ),
            "baseline_entities_rebuilt": self.baseline_entities_rebuilt,
            "baseline_sources_rebuilt": self.baseline_sources_rebuilt,
            "baseline_skipped": self.baseline_skipped,
            "results": [item.as_dict() for item in self.sources],
        }


class IpsInstancePipeline:
    """Evaluate and train every collected IPS source of one instance."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        engine_factory: Callable[..., IpsAnomalyEngine] | None = None,
        builder_factory: Callable[..., IpsBaselineBuilder] | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._engine_factory = engine_factory or IpsAnomalyEngine
        self._builder_factory = builder_factory or IpsBaselineBuilder

    async def run(self, instance: QRadarInstance) -> IpsPipelineReport:
        report = IpsPipelineReport(instance.id)
        rows = (
            await self.session.execute(
                select(IpsSourceProfile, LogSource)
                .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
                .where(
                    IpsSourceProfile.instance_id == instance.id,
                    exists(
                        select(1).where(
                            IpsSourceBucket.log_source_id == IpsSourceProfile.log_source_id
                        )
                    ),
                )
                .order_by(LogSource.qradar_id)
            )
        ).all()
        if not rows:
            return report
        sources = [source for _profile, source in rows]
        watermarks = await self._load_watermarks(instance.id, sources)
        cadence = await SearchCadenceService(self.session, self.settings).get(
            SearchFamily.IPS_COLLECTION
        )
        cadence_buckets = max(
            1, cadence.cadence_seconds // self.settings.collection_interval_seconds
        )
        per_run = self.settings.ips_max_evaluation_buckets_per_run
        if cadence_buckets > 1:
            per_run = max(
                per_run,
                cadence_buckets + self.settings.collection_max_backfill_intervals,
            )

        for source in sources:
            source_report = IpsSourcePipelineReport(source.id)
            report.sources.append(source_report)
            collection_wm = watermarks.get(collection_collector(source.qradar_id))
            evaluation_name = evaluation_collector(source.qradar_id)
            evaluation_wm = watermarks[evaluation_name]
            try:
                async with self.session.begin_nested():
                    await self._evaluate(
                        source, collection_wm, evaluation_wm, source_report, per_run
                    )
                    evaluation_wm.consecutive_failures = 0
                    evaluation_wm.last_error = None
                    await self.session.flush()
            except Exception as exc:
                source_report.status = "failed"
                source_report.error = sanitized_error(exc)
                stored = await self.session.scalar(
                    select(CollectionWatermark).where(
                        CollectionWatermark.instance_id == instance.id,
                        CollectionWatermark.collector == evaluation_name,
                    )
                )
                if stored is None:
                    raise RuntimeError("IPS evaluation watermark disappeared") from exc
                evaluation_wm = stored
                evaluation_wm.consecutive_failures += 1
                evaluation_wm.last_error = source_report.error
                now = self._clock()
                evaluation_wm.last_run_at = now
                evaluation_wm.collection_metadata = {
                    **(evaluation_wm.collection_metadata or {}),
                    "last_failure_at": now.isoformat(),
                    "last_error_class": type(exc).__name__,
                }
                await self.session.flush()
                watermarks[evaluation_name] = evaluation_wm
        await self._train(sources, watermarks, report)
        return report

    async def _load_watermarks(
        self, instance_id: uuid.UUID, sources: Sequence[LogSource]
    ) -> dict[str, CollectionWatermark]:
        names = [
            name
            for source in sources
            for name in (
                collection_collector(source.qradar_id),
                evaluation_collector(source.qradar_id),
            )
        ]
        rows = list(
            (
                await self.session.scalars(
                    select(CollectionWatermark).where(
                        CollectionWatermark.instance_id == instance_id,
                        CollectionWatermark.collector.in_(names),
                    )
                )
            ).all()
        )
        by_name = {row.collector: row for row in rows}
        for source in sources:
            name = evaluation_collector(source.qradar_id)
            if name not in by_name:
                watermark = CollectionWatermark(instance_id=instance_id, collector=name)
                self.session.add(watermark)
                by_name[name] = watermark
        await self.session.flush()
        return by_name

    async def _evaluate(
        self,
        source: LogSource,
        collection_wm: CollectionWatermark | None,
        evaluation_wm: CollectionWatermark,
        report: IpsSourcePipelineReport,
        per_run: int,
    ) -> None:
        report.evaluated_through = evaluation_wm.watermark_at
        if collection_wm is None or collection_wm.watermark_at is None:
            return
        query = select(IpsSourceBucket).where(
            IpsSourceBucket.log_source_id == source.id,
            IpsSourceBucket.bucket_start <= collection_wm.watermark_at,
        )
        if evaluation_wm.watermark_at is not None:
            query = query.where(IpsSourceBucket.bucket_start > evaluation_wm.watermark_at)
        buckets = list(
            (
                await self.session.scalars(
                    query.order_by(IpsSourceBucket.bucket_start).limit(per_run)
                )
            ).all()
        )
        engine = self._engine_factory(
            self.session, settings=self.settings, clock=self._clock
        )
        for bucket in buckets:
            result = await engine.evaluate_bucket(source, bucket)
            report.subjects_evaluated += len(result.subject_reports)
            report.inferred_observations += result.inferred_observations
            report.opened += result.opened
            report.resolved += result.resolved
            evaluation_wm.watermark_at = bucket.bucket_start
            report.evaluated_buckets.append(bucket.bucket_start)
        now = self._clock()
        evaluation_wm.last_run_at = now
        evaluation_wm.intervals_collected += len(buckets)
        if evaluation_wm.watermark_at is not None:
            evaluation_wm.lag_seconds = max(
                0,
                int(
                    (
                        now
                        - evaluation_wm.watermark_at
                        - timedelta(seconds=self.settings.collection_interval_seconds)
                    ).total_seconds()
                ),
            )
        report.evaluated_through = evaluation_wm.watermark_at
        await self.session.flush()

    async def _train(
        self,
        sources: Sequence[LogSource],
        watermarks: dict[str, CollectionWatermark],
        report: IpsPipelineReport,
    ) -> None:
        """Train once, only through what every source of the instance has evaluated.

        ``rebuild_due`` is not scoped to a source, so training through one
        source's watermark could teach another source's baseline from buckets it
        has not been judged against yet. The common bound is the earliest
        evaluated bucket end; a source that has evaluated nothing, or failed this
        run, holds training back (fail closed) rather than being trained on.
        """
        ends: list[datetime] = []
        for source, source_report in zip(sources, report.sources, strict=True):
            watermark = watermarks[evaluation_collector(source.qradar_id)]
            if source_report.status != "ok" or watermark.watermark_at is None:
                report.baseline_skipped = (
                    f"source {source.qradar_id} has not evaluated its buckets"
                )
                return
            last = await self.session.get(IpsSourceBucket, (source.id, watermark.watermark_at))
            width = (
                last.bucket_seconds
                if last is not None
                else self.settings.collection_interval_seconds
            )
            ends.append(watermark.watermark_at + timedelta(seconds=width))
        train_through = min(ends)
        try:
            async with self.session.begin_nested():
                rebuilt = await self._builder_factory(
                    self.session, settings=self.settings
                ).rebuild_due(
                    train_through=train_through,
                    limit=self.settings.ips_baseline_rebuild_batch_size,
                    now=self._clock(),
                )
        except Exception as exc:
            report.baseline_skipped = f"baseline rebuild failed: {sanitized_error(exc)}"
            return
        report.baseline_train_through = train_through
        report.baseline_entities_rebuilt = len(rebuilt["entities"])
        report.baseline_sources_rebuilt = len(rebuilt["sources"])
        now = self._clock().isoformat()
        for source in sources:
            watermark = watermarks[evaluation_collector(source.qradar_id)]
            watermark.collection_metadata = {
                **(watermark.collection_metadata or {}),
                "baseline_rebuilt_at": now,
                "baseline_train_through": train_through.isoformat(),
            }
        await self.session.flush()
