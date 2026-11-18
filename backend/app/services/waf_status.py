"""Assemble WAF operational status from profiles, watermarks and source buckets.

Six statements however many profiles match: the profiles, their watermarks,
the latest bucket per source, the 24-hour bucket counts, the endpoint counts,
and the buckets still waiting for evaluation. A source with no collection
watermark reports an unknown failure count, not zero.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, Row, String, and_, cast, func, literal, or_, select, tuple_
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.collectors.waf_endpoint_collector import COLLECTOR_NAME, watermark_collector
from app.core.config import Settings
from app.history.origin import origin_of
from app.history.state import HistoryFamily, HistoryState
from app.i18n.messages import render
from app.models.enums import WafMeasurement
from app.models.history_backfill import HistoryBackfillScope
from app.models.log_source import LogSource
from app.models.monitoring import CollectionWatermark
from app.models.waf import WafEndpoint, WafSourceBucket, WafSourceProfile
from app.schemas.anomaly import WafCollectionOut
from app.schemas.waf import (
    WafArielBudgetOut,
    WafFieldStatusOut,
    WafLastBucketOut,
    WafSourceStatusOut,
)
from app.services.waf_ariel_budget import WafArielBudget
from app.services.waf_pipeline import (
    EVALUATION_COLLECTOR,
    SKIP_GLOBAL_CAPACITY,
    SKIP_SOURCE_LOCKED,
    evaluation_collector,
    scheduling_record,
)
from app.waf.normalize import ENDPOINT_NORMALIZER_VERSION
from app.waf.profile import collection_ineligibility_message


def _parse(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _collector_sql(prefix: str) -> ColumnElement[str]:
    """The watermark key `prefix:{qradar_id}`, as the collector helpers spell it."""
    return literal(f"{prefix}:", type_=String()) + cast(LogSource.qradar_id, String)


async def _watermarks(
    session: AsyncSession,
    rows: Sequence[Row[WafSourceProfile, LogSource] | tuple[WafSourceProfile, LogSource]],
) -> dict[tuple[uuid.UUID, str], CollectionWatermark]:
    """Every collection, evaluation and scheduling watermark for these profiles."""
    keys = [
        (profile.instance_id, name)
        for profile, log_source in rows
        for name in (
            watermark_collector(log_source),
            evaluation_collector(log_source),
            scheduling_record(log_source),
        )
    ]
    found = await session.scalars(
        select(CollectionWatermark).where(
            tuple_(CollectionWatermark.instance_id, CollectionWatermark.collector).in_(keys)
        )
    )
    return {(row.instance_id, row.collector): row for row in found.all()}


async def _endpoint_counts(
    session: AsyncSession, source_ids: list[uuid.UUID]
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, int]]:
    rows = (
        await session.execute(
            select(
                WafEndpoint.log_source_id,
                func.count(),
                func.count().filter(WafEndpoint.baseline_rebuilt_at.is_(None)),
            )
            .where(
                WafEndpoint.log_source_id.in_(source_ids),
                WafEndpoint.is_active.is_(True),
                WafEndpoint.normalizer_version == ENDPOINT_NORMALIZER_VERSION,
            )
            .group_by(WafEndpoint.log_source_id)
        )
    ).all()
    return (
        {sid: int(n) for sid, n, _ in rows},
        {sid: int(n) for sid, _, n in rows},
    )


#: Message code of `collection_ineligibility_message` -> the short code the page words.
_INELIGIBLE_CODES = {
    "validation.waf.ineligible.collection_disabled": "collection_disabled",
    "validation.waf.ineligible.source_missing": "source_missing",
    "validation.profile.ineligible.disabled": "profile_disabled",
    "validation.waf.ineligible.monitoring_disabled": "monitoring_disabled",
    "validation.profile.ineligible.version_never_validated": "validation_stale",
    "validation.profile.ineligible.version_not_validated": "validation_stale",
    "validation.profile.ineligible.status": "validation_not_valid",
}

#: The lifecycle stages a failed run may name. Anything else recorded is dropped.
_FAILURE_STAGES = frozenset({"setup", "collect", "evaluate", "train", "commit"})
_SKIP_REASONS = frozenset({SKIP_SOURCE_LOCKED, SKIP_GLOBAL_CAPACITY})
#: A recorded partial-run reason that means the per-run search budget ended the run.
_BUDGET_REASONS = frozenset({"search_cap_reached", "budget_exhausted"})
_CLASS_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _later(a: datetime | None, b: datetime | None) -> bool:
    """True when `a` is recorded and strictly after `b` (or `b` is not recorded)."""
    return a is not None and (b is None or a > b)


async def waf_collection_health(
    session: AsyncSession,
    pairs: Sequence[tuple[WafSourceProfile, LogSource]],
    *,
    settings: Settings,
) -> dict[uuid.UUID, WafCollectionOut]:
    """Per-source collection health for a page of profiles: two statements in all.

    One read of every collection and scheduling watermark and one of the WAF
    history scopes, whatever the number of sources. The verdict is only what the
    records say: a source nothing recorded a run for is `no_run_recorded`, which
    is never healthy. Error text is never read, only its class and stage.
    """
    if not pairs:
        return {}
    watermarks = await _watermarks(session, pairs)
    scope_keys = [(p.instance_id, str(ls.qradar_id)) for p, ls in pairs]
    reading = {
        (row[0], row[1])
        for row in await session.execute(
            select(HistoryBackfillScope.instance_id, HistoryBackfillScope.scope_key).where(
                HistoryBackfillScope.family == HistoryFamily.WAF.value,
                HistoryBackfillScope.state == HistoryState.READING.value,
                tuple_(HistoryBackfillScope.instance_id, HistoryBackfillScope.scope_key).in_(
                    scope_keys
                ),
            )
        )
    }
    out: dict[uuid.UUID, WafCollectionOut] = {}
    for profile, log_source in pairs:
        iid = profile.instance_id
        collection = watermarks.get((iid, watermark_collector(log_source)))
        scheduling = watermarks.get((iid, scheduling_record(log_source)))
        meta = (collection.collection_metadata if collection else None) or {}
        sched = (scheduling.collection_metadata if scheduling else None) or {}
        last_run_at = collection.last_run_at if collection else None
        success_at = _parse(meta.get("last_success_at"))
        failure_at = _parse(meta.get("last_failure_at"))
        skipped_at = _parse(sched.get("last_skipped_at"))
        skip = sched.get("last_skip_reason")
        skip = skip if skip in _SKIP_REASONS else None
        stage = meta.get("last_failure_stage")
        stage = stage if stage in _FAILURE_STAGES else None
        error_class = meta.get("last_error_class")
        error_class = (
            error_class if isinstance(error_class, str) and _CLASS_NAME.match(error_class) else None
        )
        due = _int(meta.get("last_run_intervals_due"))
        collected = _int(meta.get("last_run_intervals"))
        ineligible = collection_ineligibility_message(profile, log_source, settings)

        failed = _later(failure_at, success_at)
        skipped = skip is not None and _later(skipped_at, success_at)
        busy = skipped and skip == SKIP_SOURCE_LOCKED
        capacity = skipped and skip == SKIP_GLOBAL_CAPACITY

        state: str
        if ineligible is not None:
            state = "not_eligible"
        elif failed and not (skipped and _later(skipped_at, failure_at)):
            state = "failed"
        elif busy:
            state = "source_busy"
        elif capacity:
            state = "capacity_unavailable"
        elif (iid, str(log_source.qradar_id)) in reading:
            state = "history_reading"
        elif meta.get("last_run_partial_reason") in _BUDGET_REASONS:
            state = "budget_exhausted"
        elif last_run_at is None and success_at is None and failure_at is None:
            state = "no_run_recorded"
        elif due == 0:
            state = "no_interval_due"
        elif due is not None and collected == 0:
            # Intervals were due and none was stored, with no failure and no skip.
            state = "no_progress"
        else:
            state = "collecting"
        out[log_source.id] = WafCollectionOut(
            state=state,
            ineligible_code=(
                _INELIGIBLE_CODES.get(ineligible.code) if ineligible is not None else None
            ),
            last_run_at=last_run_at,
            last_success_at=success_at,
            last_failure_at=failure_at,
            failure_stage=stage if failed else None,
            failure_class=error_class if failed else None,
            consecutive_failures=collection.consecutive_failures if collection else None,
            skip_reason=skip if skipped else None,
            skipped_at=skipped_at if skipped else None,
            intervals_due=due,
            intervals_collected=collected,
        )
    return out


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

    rows = list((await session.execute(query)).all())
    if not rows:
        return []
    source_ids = [log_source.id for _, log_source in rows]
    watermarks = await _watermarks(session, rows)
    # The bucket reads stay in this function. The history-reader inventory names
    # it, not a helper, as the display reader of waf_source_bucket.
    last_found = await session.scalars(
        select(WafSourceBucket)
        .ext(distinct_on(WafSourceBucket.log_source_id))
        .where(WafSourceBucket.log_source_id.in_(source_ids))
        .order_by(WafSourceBucket.log_source_id, WafSourceBucket.bucket_start.desc())
    )
    last_by_source = {bucket.log_source_id: bucket for bucket in last_found.all()}
    cutoff = now - timedelta(hours=24)
    count_rows = (
        await session.execute(
            select(
                WafSourceBucket.log_source_id,
                func.count().filter(WafSourceBucket.admission_truncated.is_(True)),
                func.count().filter(
                    WafSourceBucket.request_count_measurement == WafMeasurement.LOWER_BOUND
                ),
            )
            .where(
                WafSourceBucket.log_source_id.in_(source_ids),
                WafSourceBucket.bucket_start >= cutoff,
            )
            .group_by(WafSourceBucket.log_source_id)
        )
    ).all()
    truncated_by = {sid: int(n) for sid, n, _ in count_rows}
    lower_by = {sid: int(n) for sid, _, n in count_rows}
    active_by, never_by = await _endpoint_counts(session, source_ids)
    # Pending stays zero when collection has no watermark. The failure count is
    # the field that says unknown; this one is a queue length.
    collection_wm = aliased(CollectionWatermark)
    evaluation_wm = aliased(CollectionWatermark)
    pending_rows = (
        await session.execute(
            select(LogSource.id, func.count(WafSourceBucket.bucket_start))
            .select_from(LogSource)
            .join(
                collection_wm,
                and_(
                    collection_wm.instance_id == LogSource.instance_id,
                    collection_wm.collector == _collector_sql(COLLECTOR_NAME),
                    collection_wm.watermark_at.is_not(None),
                ),
            )
            .outerjoin(
                evaluation_wm,
                and_(
                    evaluation_wm.instance_id == LogSource.instance_id,
                    evaluation_wm.collector == _collector_sql(EVALUATION_COLLECTOR),
                ),
            )
            .join(
                WafSourceBucket,
                and_(
                    WafSourceBucket.log_source_id == LogSource.id,
                    WafSourceBucket.bucket_start <= collection_wm.watermark_at,
                    or_(
                        evaluation_wm.watermark_at.is_(None),
                        WafSourceBucket.bucket_start > evaluation_wm.watermark_at,
                    ),
                ),
            )
            .where(LogSource.id.in_(source_ids))
            .group_by(LogSource.id)
        )
    ).all()
    pending_by = {sid: int(n) for sid, n in pending_rows}

    out: list[WafSourceStatusOut] = []
    for profile, log_source in rows:
        instance_id = profile.instance_id
        collection = watermarks.get((instance_id, watermark_collector(log_source)))
        evaluation = watermarks.get((instance_id, evaluation_collector(log_source)))
        scheduling = watermarks.get((instance_id, scheduling_record(log_source)))
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

        last = last_by_source.get(log_source.id)
        truncated_24h = truncated_by.get(log_source.id, 0)
        lower_bound_24h = lower_by.get(log_source.id, 0)
        active = active_by.get(log_source.id, 0)
        never_rebuilt = never_by.get(log_source.id, 0)
        pending = pending_by.get(log_source.id, 0)

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

        reason_message = collection_ineligibility_message(profile, log_source, settings)
        reason = render(reason_message) if reason_message is not None else None
        reason_wire = reason_message.to_wire() if reason_message is not None else None
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
                validation_pending=profile.validation_requested_at is not None,
                validation_requested_at=profile.validation_requested_at,
                validation_requested_by=profile.validation_requested_by,
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
                ineligible_reason_code=(
                    reason_wire["code"] if reason_wire is not None else None
                ),
                ineligible_reason_params=(
                    reason_wire["params"] if reason_wire is not None else None
                ),
                collection_watermark_at=collection.watermark_at if collection else None,
                last_run_at=collection.last_run_at if collection else None,
                last_success_at=last_success_at,
                lag_seconds=lag,
                consecutive_failures=(
                    collection.consecutive_failures if collection is not None else None
                ),
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
                        origin=origin_of(HistoryFamily.WAF, last.origin),
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
