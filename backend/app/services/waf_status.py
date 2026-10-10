"""Assemble WAF operational status from profiles, watermarks and source buckets."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.collectors.waf_endpoint_collector import watermark_collector
from app.core.config import Settings
from app.models.enums import WafMeasurement
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.schemas.waf import (
    WafArielBudgetOut,
    WafFieldStatusOut,
    WafLastBucketOut,
    WafSourceStatusOut,
)
from app.services.waf_ariel_budget import WafArielBudget
from app.services.waf_pipeline import evaluation_collector, scheduling_record
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.profile import collection_ineligibility


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


async def _watermark(
    session: AsyncSession, instance_id: uuid.UUID, collector: str
) -> CollectionWatermark | None:
    return await session.scalar(
        select(CollectionWatermark).where(
            CollectionWatermark.instance_id == instance_id,
            CollectionWatermark.collector == collector,
        )
    )


async def _count(session: AsyncSession, stmt) -> int:  # type: ignore[no-untyped-def]
    return int(await session.scalar(stmt) or 0)


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


async def waf_ariel_budget_status(
    session: AsyncSession, *, settings: Settings
) -> WafArielBudgetOut:
    budget = WafArielBudget.for_session(session, settings)
    return WafArielBudgetOut(
        enforced=budget.enforced,
        max_concurrent_sources=budget.limit,
        active_permits=await budget.active_permits(session),
        permit_wait_seconds=budget.wait_seconds,
    )


async def waf_source_statuses(
    session: AsyncSession,
    *,
    settings: Settings,
    now: datetime,
    profile_id: uuid.UUID | None = None,
) -> list[WafSourceStatusOut]:
    query = (
        select(WafSourceProfile, LogSource)
        .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
        .order_by(LogSource.qradar_id, WafSourceProfile.id)
    )
    if profile_id is not None:
        query = query.where(WafSourceProfile.id == profile_id)

    out: list[WafSourceStatusOut] = []
    for profile, log_source in (await session.execute(query)).all():
        instance_id = profile.instance_id
        collection = await _watermark(session, instance_id, watermark_collector(log_source))
        evaluation = await _watermark(session, instance_id, evaluation_collector(log_source))
        scheduling = await _watermark(session, instance_id, scheduling_record(log_source))
        coll_meta = (collection.collection_metadata if collection else None) or {}
        eval_meta = (evaluation.collection_metadata if evaluation else None) or {}
        sched_meta = (scheduling.collection_metadata if scheduling else None) or {}
        last_skipped_at = _parse(sched_meta.get("last_skipped_at"))
        last_success_at = _parse(coll_meta.get("last_success_at"))
        last_failure_at = _parse(coll_meta.get("last_failure_at"))
        other_outcomes = [t for t in (last_success_at, last_failure_at) if t is not None]
        # A skip is the latest outcome only when strictly later than the latest
        # success or failure; a run that collected after skipping supersedes it.
        previous_run_skipped = last_skipped_at is not None and all(
            last_skipped_at > t for t in other_outcomes
        )

        last = await session.scalar(
            select(WafSourceBucket)
            .where(WafSourceBucket.log_source_id == log_source.id)
            .order_by(WafSourceBucket.bucket_start.desc())
            .limit(1)
        )
        truncated_24h = await _count(
            session,
            select(func.count())
            .select_from(WafSourceBucket)
            .where(
                WafSourceBucket.log_source_id == log_source.id,
                WafSourceBucket.admission_truncated.is_(True),
                WafSourceBucket.bucket_start >= now - timedelta(hours=24),
            ),
        )
        lower_bound_24h = await _count(
            session,
            select(func.count())
            .select_from(WafSourceBucket)
            .where(
                WafSourceBucket.log_source_id == log_source.id,
                WafSourceBucket.request_count_measurement == WafMeasurement.LOWER_BOUND,
                WafSourceBucket.bucket_start >= now - timedelta(hours=24),
            ),
        )
        active_filter = (
            WafEndpoint.log_source_id == log_source.id,
            WafEndpoint.is_active.is_(True),
            WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
        )
        active = await _count(
            session, select(func.count()).select_from(WafEndpoint).where(*active_filter)
        )
        never_rebuilt = await _count(
            session,
            select(func.count())
            .select_from(WafEndpoint)
            .where(*active_filter, WafEndpoint.baseline_rebuilt_at.is_(None)),
        )

        pending = 0
        if collection is not None and collection.watermark_at is not None:
            pending_q = (
                select(func.count())
                .select_from(WafSourceBucket)
                .where(
                    WafSourceBucket.log_source_id == log_source.id,
                    WafSourceBucket.bucket_start <= collection.watermark_at,
                )
            )
            if evaluation is not None and evaluation.watermark_at is not None:
                pending_q = pending_q.where(WafSourceBucket.bucket_start > evaluation.watermark_at)
            pending = await _count(session, pending_q)

        lag: int | None = None
        if collection is not None and collection.watermark_at is not None:
            lag = int(
                (
                    now
                    - (
                        collection.watermark_at
                        + timedelta(seconds=settings.collection_interval_seconds)
                    )
                ).total_seconds()
            )

        reason = collection_ineligibility(profile, log_source, settings)
        report_fields = (profile.validation_report or {}).get("fields", [])
        out.append(
            WafSourceStatusOut(
                profile_id=profile.id,
                instance_id=profile.instance_id,
                log_source_id=log_source.id,
                qradar_log_source_id=log_source.qradar_id,
                log_source_name=log_source.name,
                product_label=profile.product_label,
                enabled=profile.enabled,
                profile_version=profile.profile_version,
                validation_status=str(profile.validation_status),
                validated_profile_version=profile.validated_profile_version,
                validated_at=profile.validated_at,
                validation_fields=[
                    WafFieldStatusOut(
                        field=str(f.get("field")),
                        property_name=f.get("property_name"),
                        status=str(f.get("status")),
                    )
                    for f in report_fields
                    if isinstance(f, dict)
                ],
                collection_eligible=reason is None,
                ineligible_reason=reason,
                collection_watermark_at=collection.watermark_at if collection else None,
                last_run_at=collection.last_run_at if collection else None,
                last_success_at=last_success_at,
                lag_seconds=lag,
                consecutive_failures=collection.consecutive_failures if collection else 0,
                last_error=collection.last_error if collection else None,
                last_failure_at=last_failure_at,
                ariel_searches_last_run=_int(coll_meta.get("last_run_ariel_searches")),
                last_collection_duration_ms=_int(coll_meta.get("last_run_duration_ms")),
                last_run_intervals_collected=_int(coll_meta.get("last_run_intervals")),
                last_run_intervals_due=_int(coll_meta.get("last_run_intervals_due")),
                last_permit_wait_ms=_int(coll_meta.get("last_run_permit_wait_ms")),
                last_skip_reason=(
                    sched_meta.get("last_skip_reason")
                    if isinstance(sched_meta.get("last_skip_reason"), str)
                    else None
                ),
                last_skipped_at=last_skipped_at,
                previous_run_skipped=previous_run_skipped,
                last_bucket=(
                    WafLastBucketOut(
                        bucket_start=last.bucket_start,
                        completeness=str(last.completeness),
                        request_count_measurement=str(last.request_count_measurement),
                        groups_truncated=last.groups_truncated,
                        discovery_complete=last.discovery_complete,
                        coverage_measurement=str(last.coverage_measurement),
                        sparse_endpoint_rows=last.sparse_endpoint_rows,
                        endpoint_coverage_ratio=last.endpoint_coverage_ratio,
                        total_events=last.total_events,
                        observed_endpoints=last.observed_endpoints,
                        tracked_endpoints_observed=last.tracked_endpoints_observed,
                        admitted_endpoints=last.admitted_endpoints,
                        untracked_endpoints=last.untracked_endpoints,
                        untracked_requests=last.untracked_requests,
                        admission_truncated=last.admission_truncated,
                        in_maintenance=last.in_maintenance,
                        profile_version=last.profile_version,
                        metric_semantics=last.metric_semantics,
                    )
                    if last is not None
                    else None
                ),
                admission_truncated_buckets_24h=truncated_24h,
                lower_bound_buckets_24h=lower_bound_24h,
                active_endpoints=active,
                active_endpoint_ceiling=settings.waf_max_active_endpoints_per_source,
                active_endpoints_exceed_group_cap=active > settings.waf_max_groups_per_bucket,
                # Baselines and detection run inside the scheduled source run, so
                # they are active exactly when the source is eligible.
                baseline_active=reason is None,
                baseline_rebuilt_at=_parse(eval_meta.get("baseline_rebuilt_at")),
                baseline_train_through=_parse(eval_meta.get("baseline_train_through")),
                endpoints_without_baseline_rebuild=never_rebuilt,
                detector_active=reason is None,
                evaluated_through=evaluation.watermark_at if evaluation else None,
                pending_evaluation_buckets=pending,
            )
        )
    return out
