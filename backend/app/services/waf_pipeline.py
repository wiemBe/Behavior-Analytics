"""Scheduled WAF behavior pipeline for one source: collect, judge, then train.

One run handles one WAF source, under one per-source advisory lock, in a fixed
order:

  1. **Collect.** The collector advances the source's collection watermark past
     completed buckets only, writing a `WafSourceBucket` for each.
  2. **Evaluate.** Every collected, not-yet-evaluated bucket, oldest first and
     never beyond the collection watermark. Each endpoint row in the bucket is
     judged against its baseline *as it stood before this run's training*. The
     evaluation watermark advances bucket by bucket.
  3. **Train.** A bounded number of endpoint baselines are rebuilt, least
     recently rebuilt first, from buckets up to the end of the last *evaluated*
     bucket only.

The order is the point. Training before evaluating would let a spike enter the
baseline it is about to be judged against and dampen its own verdict. Bounding
training by the evaluation watermark means a bucket is never learned from before
it has been judged -- and an anomalous bucket, once judged, is excluded from
training by its incident span.

A run for a source that is not eligible (`collection_ineligibility`) does
nothing. A failed run leaves both watermarks where they were and records the
failure on the source's collection watermark; it never writes a bucket.

Collection takes one global WAF Ariel permit per bucket
(`app.services.waf_ariel_budget`). A run that cannot get one stops collecting,
still evaluates and trains on what was already collected, and reports
`capacity_unavailable`. A run that finds its source locked by another worker
does nothing. Both skips are recorded on the source's scheduling record so an
operator can see that a run was skipped and why -- without touching the
watermark row the running worker holds.

Evaluation and training are bulk operations: a bucket is judged with a fixed
number of queries whatever the number of endpoints, and a rebuild's queries
scale with chunks of endpoints, not endpoints.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.waf_endpoint_collector import (
    WafCollectionReport,
    WafEndpointCollector,
    watermark_collector,
)
from app.core.config import Settings, get_settings
from app.models.instance import QRadarInstance
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.providers.base import ProviderError, QRadarProvider
from app.services.locks import CollectorAdvisoryLock
from app.services.waf_ariel_budget import WafArielBudget
from app.waf.baseline import WafBaselineBuilder
from app.waf.engine import WafAnomalyEngine
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.profile import WafFieldMapping, collection_ineligibility

logger = logging.getLogger("app.waf.pipeline")

EVALUATION_COLLECTOR = "waf_endpoint_evaluation"
SCHEDULING_RECORD = "waf_scheduling"
PIPELINE_LOCK = "waf_pipeline"

#: Why a scheduled run did no collection work, as recorded for operators.
SKIP_SOURCE_LOCKED = "source_locked"
SKIP_GLOBAL_CAPACITY = "global_capacity"


def evaluation_collector(log_source: LogSource) -> str:
    """Watermark key for how far a source's buckets have been evaluated."""
    return f"{EVALUATION_COLLECTOR}:{log_source.qradar_id}"


def scheduling_record(log_source: LogSource) -> str:
    """Key of the row that records skipped runs for one source.

    Separate from the collection watermark on purpose: a run skipped because
    another worker holds the source's lock must not wait on, or overwrite, the
    watermark row that worker is updating.
    """
    return f"{SCHEDULING_RECORD}:{log_source.qradar_id}"


@dataclass
class WafPipelineReport:
    profile_id: uuid.UUID
    log_source_id: uuid.UUID | None
    status: str = "ok"
    reason: str | None = None
    collection: WafCollectionReport | None = None
    evaluated_buckets: list[datetime] = field(default_factory=list)
    endpoint_evaluations: int = 0
    #: Of those, judged from a reconstructed zero or lower bound rather than a row.
    inferred_evaluations: int = 0
    opened: int = 0
    resolved: int = 0
    evaluated_through: datetime | None = None
    baseline_endpoints_rebuilt: int = 0
    baseline_train_through: datetime | None = None

    def as_dict(self) -> dict[str, Any]:
        c = self.collection
        return {
            "profile_id": str(self.profile_id),
            "log_source_id": str(self.log_source_id) if self.log_source_id else None,
            "status": self.status,
            "reason": self.reason,
            "intervals_collected": c.intervals_collected if c else 0,
            "rows_written": c.rows_written if c else 0,
            "ariel_searches": c.ariel_searches if c else 0,
            "collection_duration_ms": c.duration_ms if c else 0,
            "capacity_unavailable": c.capacity_unavailable if c else False,
            "lower_bound_buckets": len(c.lower_bound_buckets) if c else 0,
            "endpoints_created": c.endpoints_created if c else 0,
            "untracked_endpoints": c.untracked_endpoints if c else 0,
            "admission_truncated_buckets": len(c.admission_truncated_buckets) if c else 0,
            "collection_watermark_at": (
                c.watermark_at.isoformat() if c and c.watermark_at else None
            ),
            "lag_seconds": c.lag_seconds if c else None,
            "evaluated_buckets": len(self.evaluated_buckets),
            "endpoint_evaluations": self.endpoint_evaluations,
            "inferred_evaluations": self.inferred_evaluations,
            "opened": self.opened,
            "resolved": self.resolved,
            "evaluated_through": (
                self.evaluated_through.isoformat() if self.evaluated_through else None
            ),
            "baseline_endpoints_rebuilt": self.baseline_endpoints_rebuilt,
            "baseline_train_through": (
                self.baseline_train_through.isoformat() if self.baseline_train_through else None
            ),
        }


async def _watermark(
    session: AsyncSession, instance_id: uuid.UUID, collector: str
) -> CollectionWatermark:
    wm = await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance_id,
            CollectionWatermark.collector == collector,
        )
    )
    if wm is None:
        wm = CollectionWatermark(instance_id=instance_id, collector=collector)
        session.add(wm)
        await session.flush()
    return wm


class WafSourcePipeline:
    def __init__(
        self,
        session: AsyncSession,
        provider: QRadarProvider,
        *,
        settings: Settings | None = None,
        clock: Callable[[], datetime] | None = None,
        ariel_budget: WafArielBudget | None = None,
    ) -> None:
        self.session = session
        self.provider = provider
        self.settings = settings or get_settings()
        self._clock = clock or (lambda: datetime.now(UTC))
        self.ariel_budget = ariel_budget or WafArielBudget.for_session(session, self.settings)

    async def run(self, profile: WafSourceProfile) -> WafPipelineReport:
        report = WafPipelineReport(profile.id, profile.log_source_id)
        log_source = await self.session.get(LogSource, profile.log_source_id)
        reason = collection_ineligibility(profile, log_source, self.settings)
        if reason is not None or log_source is None:
            report.status = "skipped"
            report.reason = reason
            return report
        instance = await self.session.get(QRadarInstance, profile.instance_id)
        if instance is None:
            report.status = "skipped"
            report.reason = "the profile's QRadar instance no longer exists"
            return report

        async with CollectorAdvisoryLock(
            self.session,
            self.settings,
            instance.id,
            f"{PIPELINE_LOCK}:{log_source.qradar_id}",
        ) as acquired:
            if not acquired:
                report.status = "locked"
                return report

            collector = WafEndpointCollector(
                self.session,
                self.provider,
                mapping=WafFieldMapping.from_profile(profile),
                profile_version=profile.profile_version,
                settings=self.settings,
                clock=self._clock,
                ariel_budget=self.ariel_budget,
            )
            report.collection = await collector.collect(instance, log_source=log_source)
            if report.collection.skipped_locked:
                report.status = "locked"
                return report
            if report.collection.capacity_unavailable:
                report.status = "capacity_unavailable"
                report.reason = (
                    "no global WAF Ariel permit freed up within "
                    f"{self.settings.waf_ariel_permit_wait_seconds:g} s; "
                    f"collected {report.collection.intervals_collected} bucket(s) before stopping"
                )

            collection_wm = await _watermark(
                self.session, instance.id, watermark_collector(log_source)
            )
            evaluation_wm = await _watermark(
                self.session, instance.id, evaluation_collector(log_source)
            )
            await self._evaluate(log_source, collection_wm, evaluation_wm, report)
            await self._train(
                log_source, evaluation_wm, collector.metric_semantics, report
            )
            await self.session.flush()
        return report

    async def _evaluate(
        self,
        log_source: LogSource,
        collection_wm: CollectionWatermark,
        evaluation_wm: CollectionWatermark,
        report: WafPipelineReport,
    ) -> None:
        now = self._clock()
        report.evaluated_through = evaluation_wm.watermark_at
        if collection_wm.watermark_at is None:
            return
        query = select(WafSourceBucket).where(
            WafSourceBucket.log_source_id == log_source.id,
            WafSourceBucket.bucket_start <= collection_wm.watermark_at,
        )
        if evaluation_wm.watermark_at is not None:
            query = query.where(WafSourceBucket.bucket_start > evaluation_wm.watermark_at)
        buckets = list(
            (
                await self.session.scalars(
                    query.order_by(WafSourceBucket.bucket_start).limit(
                        self.settings.waf_max_evaluation_buckets_per_run
                    )
                )
            ).all()
        )

        engine = WafAnomalyEngine(self.session, settings=self.settings, clock=self._clock)
        for bucket in buckets:
            result = await engine.evaluate_bucket(log_source, bucket)
            report.endpoint_evaluations += len(result.endpoint_reports)
            report.inferred_evaluations += result.inferred_observations
            report.opened += result.opened
            report.resolved += result.resolved
            evaluation_wm.watermark_at = bucket.bucket_start
            report.evaluated_buckets.append(bucket.bucket_start)

        evaluation_wm.last_run_at = now
        evaluation_wm.intervals_collected += len(buckets)
        if evaluation_wm.watermark_at is not None:
            evaluation_wm.lag_seconds = int(
                (
                    now
                    - (
                        evaluation_wm.watermark_at
                        + timedelta(seconds=self.settings.collection_interval_seconds)
                    )
                ).total_seconds()
            )
        report.evaluated_through = evaluation_wm.watermark_at
        await self.session.flush()

    async def _train(
        self,
        log_source: LogSource,
        evaluation_wm: CollectionWatermark,
        semantics: str,
        report: WafPipelineReport,
    ) -> None:
        if evaluation_wm.watermark_at is None:
            return
        now = self._clock()
        last = await self.session.get(
            WafSourceBucket, (log_source.id, evaluation_wm.watermark_at)
        )
        width = (
            last.bucket_seconds
            if last is not None
            else self.settings.collection_interval_seconds
        )
        train_through = evaluation_wm.watermark_at + timedelta(seconds=width)

        endpoints = list(
            (
                await self.session.scalars(
                    select(WafEndpoint)
                    .where(
                        WafEndpoint.log_source_id == log_source.id,
                        WafEndpoint.is_active.is_(True),
                        WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
                    )
                    .order_by(WafEndpoint.baseline_rebuilt_at.asc().nulls_first(), WafEndpoint.id)
                    .limit(self.settings.waf_baseline_endpoints_per_run)
                )
            ).all()
        )
        builder = WafBaselineBuilder(self.session, settings=self.settings)
        await builder.rebuild_endpoints(
            endpoints,
            semantics=semantics,
            now=now,
            train_through=train_through,
            log_source=log_source,
        )
        report.baseline_endpoints_rebuilt = len(endpoints)
        report.baseline_train_through = train_through
        evaluation_wm.collection_metadata = {
            **(evaluation_wm.collection_metadata or {}),
            "baseline_rebuilt_at": now.isoformat(),
            "baseline_train_through": train_through.isoformat(),
            "baseline_endpoints_rebuilt": len(endpoints),
            "metric_semantics": semantics,
        }
        await self.session.flush()


def sanitized_error(exc: BaseException) -> str:
    """Operator-safe text for a failure.

    Provider errors carry text this codebase wrote from a fixed vocabulary. Any
    other exception -- a database error above all -- can echo statement
    parameters, which here may include request-derived values, so only its class
    is kept.
    """
    if isinstance(exc, ProviderError):
        return f"{type(exc).__name__}: {exc}"[:512]
    return type(exc).__name__


async def record_waf_failure(
    session: AsyncSession, profile_id: uuid.UUID, exc: BaseException, *, now: datetime
) -> None:
    """Make a failed run visible without touching the watermark or any bucket."""
    profile = await session.get(WafSourceProfile, profile_id)
    if profile is None:
        return
    log_source = await session.get(LogSource, profile.log_source_id)
    if log_source is None:
        return
    wm = await _watermark(session, profile.instance_id, watermark_collector(log_source))
    wm.consecutive_failures += 1
    wm.last_error = sanitized_error(exc)
    wm.collection_metadata = {
        **(wm.collection_metadata or {}),
        "last_failure_at": now.isoformat(),
        "last_error_class": type(exc).__name__,
    }
    await session.flush()


async def record_waf_skip(
    session: AsyncSession, log_source: LogSource, reason: str, *, now: datetime
) -> None:
    """Record that a run for this source did no collection work, and why.

    One atomic statement on the source's scheduling record -- never the
    collection watermark, which the worker holding the source may be updating.
    Commits on its own.
    """
    await session.execute(
        text(
            "INSERT INTO collection_watermark "
            "(id, instance_id, collector, intervals_collected, consecutive_failures, "
            " collection_metadata) "
            "VALUES (:id, :instance, :collector, 0, 0, CAST(:meta AS jsonb)) "
            "ON CONFLICT (instance_id, collector) DO UPDATE SET "
            "collection_metadata = collection_watermark.collection_metadata "
            "|| EXCLUDED.collection_metadata, updated_at = now()"
        ).bindparams(
            id=uuid.uuid4(),
            instance=log_source.instance_id,
            collector=scheduling_record(log_source),
            meta=json.dumps({"last_skip_reason": reason, "last_skipped_at": now.isoformat()}),
        )
    )
    await session.commit()


async def run_waf_source(
    session: AsyncSession,
    profile_id: uuid.UUID,
    *,
    provider_factory: Callable[[QRadarInstance], QRadarProvider],
    settings: Settings | None = None,
    clock: Callable[[], datetime] | None = None,
    ariel_budget: WafArielBudget | None = None,
) -> dict[str, Any]:
    """One isolated, committed pipeline run for one source.

    Commits on success. On failure rolls back everything the run wrote, then
    records the failure in its own commit, so a failing source never leaves a
    partial bucket behind and never affects another source's run.
    """
    clock = clock or (lambda: datetime.now(UTC))
    profile = await session.get(WafSourceProfile, profile_id)
    if profile is None:
        return {"profile_id": str(profile_id), "status": "missing"}
    instance = await session.get(QRadarInstance, profile.instance_id)
    if instance is None:
        return {"profile_id": str(profile_id), "status": "missing"}
    provider = provider_factory(instance)
    log_source_id = profile.log_source_id
    try:
        report = await WafSourcePipeline(
            session, provider, settings=settings, clock=clock, ariel_budget=ariel_budget
        ).run(profile)
        await session.commit()
        skip = {
            "locked": SKIP_SOURCE_LOCKED, "capacity_unavailable": SKIP_GLOBAL_CAPACITY
        }.get(report.status)
        if skip is not None:
            log_source = await session.get(LogSource, log_source_id)
            if log_source is not None:
                await record_waf_skip(session, log_source, skip, now=clock())
        return report.as_dict()
    except Exception as exc:
        await session.rollback()
        await record_waf_failure(session, profile_id, exc, now=clock())
        await session.commit()
        logger.warning(
            "waf source run failed",
            extra={"profile_id": str(profile_id), "error_class": type(exc).__name__},
        )
        return {
            "profile_id": str(profile_id),
            "status": "failed",
            "error_class": type(exc).__name__,
        }
    finally:
        await provider.aclose()


async def eligible_profile_ids(
    session: AsyncSession, settings: Settings
) -> tuple[list[uuid.UUID], dict[str, int]]:
    """Profiles to schedule this cycle, in a stable order, and why others are not."""
    rows = (
        await session.execute(
            select(WafSourceProfile, LogSource)
            .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
            .order_by(LogSource.qradar_id, WafSourceProfile.id)
        )
    ).all()
    eligible: list[uuid.UUID] = []
    skipped: dict[str, int] = {}
    for profile, log_source in rows:
        reason = collection_ineligibility(profile, log_source, settings)
        if reason is None:
            eligible.append(profile.id)
        else:
            skipped[reason] = skipped.get(reason, 0) + 1
    return eligible, skipped

