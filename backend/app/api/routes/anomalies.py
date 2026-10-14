"""Phase A behavioral anomaly and investigation API.

Every endpoint is read-only, paginated, and range-bounded. There is deliberately
no QRadar mutation surface here and none may be added: the platform's
integration with QRadar is read-only by design, not by omission.

Nothing in these responses carries provider credentials, SEC headers or raw
event payloads. The explanation package exposes AQL text as provenance, which
is query structure rather than data, and is what makes a contributor claim
auditable rather than something the UI must take on faith.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.capabilities import (
    DetectorSpec,
    DetectorStatus,
    all_detectors,
    bucket_telemetry,
    detector_status,
)
from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.models.enums import (
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    EvidenceStatus,
    Severity,
    WafAnomalyType,
    WafProfileValidationStatus,
)
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.waf import WafAnomaly, WafEndpoint, WafSourceProfile
from app.providers.base import LogSourceTelemetry
from app.repositories.anomaly import AnomalyRepository
from app.repositories.anomaly_histogram import BUCKET_SECONDS, check_range, histogram
from app.schemas.anomaly import (
    AnomalyDetailOut,
    AnomalyHistogramHourOut,
    AnomalyHistogramOut,
    AnomalyListItem,
    AnomalyTransitionOut,
    BaselineCellOut,
    BehaviorSummaryOut,
    ContributorOut,
    DetectorCapabilityOut,
    ExplanationDimensionOut,
    ExplanationOut,
    InputRequirementOut,
    MetricBucketOut,
    PagedAnomalies,
    PagedWafBaselines,
    PagedWafEndpoints,
    PagedWafMetrics,
    PagedWafSources,
    SourceBehaviorOut,
    SubjectKind,
    WafAdmissionOverflowOut,
    WafBaselineAdequacyOut,
    WafBaselineCellOut,
    WafEndpointInventoryOut,
    WafEndpointRefOut,
    WafLatestBucketOut,
    WafMetricBucketOut,
    WafSourceBehaviorOut,
    WafSourceDetailOut,
    WafValidationFieldOut,
    WafValidationReportOut,
    detection_detail,
    waf_detection_detail,
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])
behavior_router = APIRouter(prefix="/behavior", tags=["behavior"])


def _validate_range(
    since: datetime | None, until: datetime | None, max_days: int
) -> tuple[datetime, datetime]:
    """Clamp and validate a caller-supplied time range.

    An unbounded range on a hypertable is a denial-of-service against our own
    database, so the ceiling is enforced here rather than trusted to the client.
    """
    now = datetime.now(UTC)
    end = until or now
    start = since or (end - timedelta(days=1))
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    if end.tzinfo is None:
        end = end.replace(tzinfo=UTC)
    if end <= start:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "range end must be after its start"
        )
    if (end - start) > timedelta(days=max_days):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"range exceeds the maximum of {max_days} days",
        )
    return start, end


# --------------------------------------------------------------------- list
@router.get("", response_model=PagedAnomalies)
async def list_anomalies(
    session: SessionDep,
    settings: SettingsDep,
    log_source_id: uuid.UUID | None = Query(default=None),
    instance_id: uuid.UUID | None = Query(default=None),
    anomaly_type: AnomalyType | WafAnomalyType | None = Query(default=None),
    subject_kind: SubjectKind | None = Query(default=None),
    waf_endpoint_id: uuid.UUID | None = Query(default=None),
    state: AnomalyState | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    evidence_status: EvidenceStatus | None = Query(default=None),
    active_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedAnomalies:
    start, end = _validate_range(since, until, settings.api_max_range_days)
    repo = AnomalyRepository(session)
    rows, total = await repo.list_unified(
        limit=min(limit, settings.api_max_page_size),
        offset=offset,
        subject_kind=subject_kind,
        waf_endpoint_id=waf_endpoint_id,
        log_source_id=log_source_id,
        instance_id=instance_id,
        anomaly_type=anomaly_type.value if anomaly_type is not None else None,
        state=state,
        severity=severity,
        evidence_status=evidence_status,
        active_only=active_only,
        since=start,
        until=end,
    )
    return PagedAnomalies(
        items=[_unified_item(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _unified_item(row: dict) -> AnomalyListItem:
    """One row of the unified union as a list item."""
    waf_endpoint = None
    if row["waf_endpoint_id"] is not None:
        waf_endpoint = WafEndpointRefOut(
            id=row["waf_endpoint_id"],
            normalized_endpoint=row["endpoint_normalized"],
            http_method=row["endpoint_method"],
            virtual_host=row["endpoint_virtual_host"],
            virtual_host_available=bool(row["endpoint_virtual_host_available"]),
            normalizer_version=row["endpoint_normalizer_version"],
        )
    fields = {
        k: v
        for k, v in row.items()
        if not k.startswith("endpoint_") and k != "waf_endpoint_id"
    }
    return AnomalyListItem(**fields, waf_endpoint=waf_endpoint)


async def _list_items(
    repo: AnomalyRepository, rows: list[LogSourceAnomaly]
) -> list[AnomalyListItem]:
    """Anomaly rows with their source names resolved in a single query."""
    names = await repo.source_names(rows)
    items = []
    for row in rows:
        item = AnomalyListItem.model_validate(row)
        item.log_source_name = names.get(row.log_source_id)
        items.append(item)
    return items


@router.get("/summary", response_model=BehaviorSummaryOut)
async def anomaly_summary(session: SessionDep) -> BehaviorSummaryOut:
    repo = AnomalyRepository(session)
    counts = await repo.state_counts()
    evidence = await repo.evidence_counts()
    sources = await repo.monitored_sources()

    def total(state: AnomalyState, atype: AnomalyType | None = None) -> int:
        if atype is not None:
            return counts.get((state.value, atype.value), 0)
        return sum(v for (s, _), v in counts.items() if s == state.value)

    active = (AnomalyState.OPEN, AnomalyState.CANDIDATE, AnomalyState.RECOVERING)
    open_total = sum(total(s) for s in active)

    # A source with no adequate baseline is counted separately from a healthy
    # one: "we cannot yet judge this source" is an observability gap, and
    # folding it into the healthy count would hide it.
    # One clock read and one cell lookup for the whole fleet, whatever its
    # size: two reads of the clock could straddle a midnight boundary and
    # judge the weekday against one instant and the hour against another.
    now = datetime.now(UTC)
    cells = await repo.baseline_cells(
        [(src.id, now.isoweekday(), now.hour) for src in sources],
        metric="average_eps",
    )
    insufficient = 0
    for src in sources:
        cell = cells.get((src.id, now.isoweekday(), now.hour))
        if cell is None or not cell.is_reliable:
            insufficient += 1

    return BehaviorSummaryOut(
        open_anomalies=open_total,
        spikes=sum(total(s, AnomalyType.VOLUME_SPIKE) for s in active),
        drops=sum(total(s, AnomalyType.VOLUME_DROP) for s in active),
        silent_sources=sum(total(s, AnomalyType.NO_EVENTS) for s in active),
        candidates=total(AnomalyState.CANDIDATE),
        recovering=total(AnomalyState.RECOVERING),
        insufficient_data_sources=insufficient,
        monitored_sources=len(sources),
        evidence_pending=evidence.get(EvidenceStatus.PENDING.value, 0),
        evidence_failed=evidence.get(EvidenceStatus.FAILED.value, 0),
        recently_resolved=await _list_items(repo, await repo.recently_resolved()),
        highest_deviation=await _list_items(repo, await repo.highest_deviation()),
    )


@router.get("/histogram", response_model=AnomalyHistogramOut)
async def anomaly_histogram(
    session: SessionDep,
    settings: SettingsDep,
    # Declared before /{anomaly_id}: a route after it would parse "histogram"
    # as an id and answer every histogram call with a uuid parse error.
    since: datetime = Query(...),
    until: datetime = Query(...),
    subject_kind: SubjectKind | None = Query(default=None),
) -> AnomalyHistogramOut:
    """Anomaly counts per UTC hour and severity: the Home chart's own read.

    The population is exactly the list's -- the same families for the same
    range and `subject_kind`, every state -- but uncapped, so an hour with no
    anomaly is a measured zero and no total is a lower bound. One statement
    per request, whatever the range holds.
    """
    start, end = _validate_range(since, until, settings.api_max_range_days)
    try:
        check_range(start, end)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
    result = await histogram(session, since=start, until=end, subject_kind=subject_kind)
    return AnomalyHistogramOut(
        since=start,
        until=end,
        bucket_seconds=BUCKET_SECONDS,
        families=result.families,
        total=result.total,
        hours=[
            AnomalyHistogramHourOut(
                start=hour.start, counts=hour.counts, total=hour.total
            )
            for hour in result.hours
        ],
    )


@router.get("/{anomaly_id}", response_model=AnomalyDetailOut)
async def get_anomaly(anomaly_id: uuid.UUID, session: SessionDep) -> AnomalyDetailOut:
    repo = AnomalyRepository(session)
    anomaly = await repo.get_detail(anomaly_id)
    if anomaly is None:
        waf_anomaly = await repo.get_waf_detail(anomaly_id)
        if waf_anomaly is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "anomaly not found")
        return await _waf_detail(repo, session, waf_anomaly)

    detail = AnomalyDetailOut.model_validate(anomaly)
    source = await session.get(LogSource, anomaly.log_source_id)
    detail.log_source_name = source.name if source else None
    detail.transitions = [
        AnomalyTransitionOut.model_validate(t) for t in anomaly.transitions
    ]
    detail.explanation_package = _explanation_out(anomaly.explanation_package)
    detail.detection = detection_detail(anomaly.details)
    return detail


async def _waf_detail(
    repo: AnomalyRepository, session: AsyncSession, anomaly: WafAnomaly
) -> AnomalyDetailOut:
    """A WAF anomaly as the shared detail shape.

    Sections that exist only for a log source -- the evidence package and the
    seasonal cells keyed to it -- are None, not reconstructed: a WAF incident
    has no such package, and a fabricated one would claim evidence nobody
    collected.
    """
    endpoint = anomaly.endpoint
    source = await session.get(LogSource, endpoint.log_source_id)
    return AnomalyDetailOut(
        id=anomaly.id,
        subject_kind=SubjectKind.WAF_ENDPOINT,
        log_source_id=endpoint.log_source_id,
        log_source_name=source.name if source else None,
        waf_endpoint=WafEndpointRefOut(
            id=endpoint.id,
            normalized_endpoint=endpoint.normalized_endpoint,
            http_method=endpoint.http_method,
            virtual_host=endpoint.virtual_host,
            virtual_host_available=endpoint.virtual_host is not None
            or await repo.virtual_host_available(endpoint.log_source_id),
            normalizer_version=endpoint.normalizer_version,
        ),
        anomaly_type=WafAnomalyType(anomaly.detector_type),
        state=anomaly.state,
        severity=anomaly.severity,
        observed_value=anomaly.observed_value,
        expected_value=anomaly.expected_value,
        deviation_ratio=anomaly.deviation_ratio,
        robust_z=anomaly.robust_z,
        absolute_delta=anomaly.absolute_delta,
        consecutive_buckets=anomaly.consecutive_buckets,
        confidence=anomaly.confidence,
        detected_at=anomaly.detected_at,
        opened_at=anomaly.opened_at,
        anomaly_start=anomaly.anomaly_start,
        anomaly_end=anomaly.anomaly_end,
        resolved_at=anomaly.resolved_at,
        evidence_status=anomaly.evidence_status,
        suppressed=anomaly.suppressed,
        observed_measurement=str(anomaly.observed_measurement),
        explanation=anomaly.explanation,
        baseline_version=anomaly.baseline_version,
        policy_version=anomaly.policy_version,
        transitions=[AnomalyTransitionOut.model_validate(t) for t in anomaly.transitions],
        explanation_package=None,
        detection=waf_detection_detail(
            anomaly.details, normalizer_version=anomaly.normalizer_version
        ),
    )


def _explanation_out(package) -> ExplanationOut | None:
    """Assemble the evidence package, grouping contributors under dimensions.

    Contributors are stored flat and joined here rather than nested in storage,
    so they stay queryable across anomalies. Every requested dimension appears
    even when it has no contributors — an UNAVAILABLE dimension the UI cannot
    see is a dimension the operator will assume was clean.
    """
    if package is None:
        return None
    by_dimension: dict[str, list[ContributorOut]] = {}
    for c in package.contributors:
        by_dimension.setdefault(c.dimension, []).append(ContributorOut.model_validate(c))
    for rows in by_dimension.values():
        rows.sort(key=lambda r: r.rank)

    out = ExplanationOut.model_validate(package)
    out.dimensions = []
    for dim in package.dimensions:
        item = ExplanationDimensionOut.model_validate(dim)
        item.contributors = by_dimension.get(dim.dimension, [])
        out.dimensions.append(item)
    out.dimensions.sort(key=lambda d: d.dimension)
    return out


# ----------------------------------------------------------------- behavior
@behavior_router.get("/sources", response_model=list[SourceBehaviorOut])
async def list_source_behavior(session: SessionDep) -> list[SourceBehaviorOut]:
    """Observed vs expected volume for every monitored source.

    Two bulk reads for the whole fleet, whatever its size: the Home view calls
    this on every auto-refresh, and a statement per source would make its cost
    grow with the fleet.
    """
    repo = AnomalyRepository(session)
    sources = await repo.monitored_sources()
    active_counts = await repo.active_counts_by_source()
    buckets = await repo.latest_buckets(src.id for src in sources)
    cells = await repo.baseline_cells(
        [
            (src.id, buckets[src.id].bucket_start.isoweekday(), buckets[src.id].bucket_start.hour)
            for src in sources
            if src.id in buckets
        ],
        metric="average_eps",
    )

    out: list[SourceBehaviorOut] = []
    for src in sources:
        bucket = buckets.get(src.id)
        cell = None
        if bucket is not None:
            # The cell is the one this source's own latest bucket lands in, so
            # each row is judged against the season it actually observed.
            cell = cells.get(
                (src.id, bucket.bucket_start.isoweekday(), bucket.bucket_start.hour)
            )
        out.append(_source_behavior(src, bucket, cell, active_counts.get(src.id, 0)))
    return out


def _source_behavior(src, bucket, cell, active_count: int) -> SourceBehaviorOut:
    observed = bucket.average_eps if bucket is not None else None
    expected = cell.median if (cell is not None and cell.is_reliable) else None
    # A ratio against a zero expectation does not exist. Reporting one would
    # turn "the baseline expects nothing here" into a spurious huge number.
    ratio = (
        observed / expected
        if (observed is not None and expected not in (None, 0))
        else None
    )
    if active_count > 0:
        state = AnomalyState.OPEN
    elif cell is None or not cell.is_reliable:
        state = AnomalyState.INSUFFICIENT_DATA
    else:
        state = AnomalyState.NORMAL

    return SourceBehaviorOut(
        log_source_id=src.id,
        name=src.name,
        criticality=str(src.criticality),
        observed_eps=observed,
        expected_eps=expected,
        expected_low=cell.p05 if cell is not None else None,
        expected_high=cell.p95 if cell is not None else None,
        deviation_ratio=ratio,
        state=state,
        baseline_sample_count=cell.sample_count if cell is not None else 0,
        baseline_completeness=cell.completeness if cell is not None else 0.0,
        last_bucket_at=bucket.bucket_start if bucket is not None else None,
        last_bucket_completeness=(
            str(bucket.completeness) if bucket is not None else None
        ),
        last_event_at=src.last_event_time,
        open_anomaly_count=active_count,
    )


@behavior_router.get(
    "/sources/{log_source_id}/metrics", response_model=list[MetricBucketOut]
)
async def source_metrics(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
) -> list[MetricBucketOut]:
    if await session.get(LogSource, log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    start, end = _validate_range(since, until, settings.api_max_range_days)
    repo = AnomalyRepository(session)
    rows = await repo.metric_history(
        log_source_id, since=start, until=end, limit=limit
    )
    out: list[MetricBucketOut] = []
    for row in rows:
        item = MetricBucketOut.model_validate(row)
        telemetry = bucket_telemetry(row.collection_source, row.query_provenance)
        if LogSourceTelemetry.PEAK_RATE not in telemetry:
            item.peak_eps = None
        out.append(item)
    return out


def _detector_limitation(spec: DetectorSpec, state: DetectorStatus) -> str | None:
    """The code's limitation, or for a disabled detector the setting that enables it."""
    if spec.limitation is not None or state is not DetectorStatus.NOT_ENABLED:
        return spec.limitation
    text = f"Disabled in this deployment: set {str(spec.enabling_setting).upper()}=true."
    return f"{text} Activation: {spec.activation}" if spec.activation else text


@behavior_router.get("/detectors", response_model=list[DetectorCapabilityOut])
async def detector_capabilities(settings: SettingsDep) -> list[DetectorCapabilityOut]:
    """Every named detector, and whether anything can actually run it.

    Derived from the same registry the engine enforces, and from this
    deployment's settings, not from stored rows, so what this reports cannot
    drift from what is executed.
    """
    out: list[DetectorCapabilityOut] = []
    for spec in all_detectors():
        state = detector_status(spec, settings)
        out.append(
            DetectorCapabilityOut(
                domain=spec.domain.value,
                detector_type=spec.detector_type,
                status=state.value,
                required_telemetry=sorted(t.value for t in spec.telemetry),
                required_baselines=sorted(spec.baseline_metrics),
                scheduled_task=spec.scheduled_task,
                limitation=_detector_limitation(spec, state),
                measurement_requirement=spec.measurement.value if spec.measurement else None,
                enabling_setting=spec.enabling_setting,
                activation=spec.activation,
                input_requirements=[
                    InputRequirementOut(
                        input=i.input.value, role=i.role, requirement=i.requirement.value
                    )
                    for i in spec.inputs
                ],
            )
        )
    return out


@behavior_router.get(
    "/sources/{log_source_id}/baselines", response_model=list[BaselineCellOut]
)
async def source_baselines(
    log_source_id: uuid.UUID,
    session: SessionDep,
    metric: str = Query(default="average_eps", max_length=64),
) -> list[BaselineCellOut]:
    if await session.get(LogSource, log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    repo = AnomalyRepository(session)
    rows = await repo.baselines_for(log_source_id, metric=metric)
    return [BaselineCellOut.model_validate(r) for r in rows]


@behavior_router.get("/sources/{log_source_id}", response_model=SourceBehaviorOut)
async def source_behavior(
    log_source_id: uuid.UUID, session: SessionDep
) -> SourceBehaviorOut:
    src = await session.get(LogSource, log_source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    repo = AnomalyRepository(session)
    bucket = await repo.latest_bucket(log_source_id)
    cell = None
    if bucket is not None:
        cell = await repo.baseline_cell(
            log_source_id,
            weekday=bucket.bucket_start.isoweekday(),
            hour=bucket.bucket_start.hour,
            metric="average_eps",
        )
    counts = await repo.active_counts_by_source()
    return _source_behavior(src, bucket, cell, counts.get(log_source_id, 0))



# ------------------------------------------------------------- WAF behavior
def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _ratio(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _validation_report(report: dict | None) -> WafValidationReportOut | None:
    """The stored validation report, counts only.

    Built field by field from named keys. The stored report also holds the AQL
    that was run and bounded lists of sampled values; neither is read here, so
    neither can reach a response.
    """
    if not report:
        return None
    fields: list[WafValidationFieldOut] = []
    for raw in report.get("fields", []):
        if not isinstance(raw, dict):
            continue
        discovered = raw.get("discovered_values")
        fields.append(
            WafValidationFieldOut(
                field=str(raw.get("field")),
                property_name=raw.get("property_name")
                if isinstance(raw.get("property_name"), str)
                else None,
                status=str(raw.get("status")),
                used_by_collection=raw.get("used_by_collection") is True,
                populated_events=_count(raw.get("populated_events")),
                populated_ratio=_ratio(raw.get("populated_ratio")),
                distinct_values=_count(raw.get("distinct_values")),
                plausible_ratio=_ratio(raw.get("plausible_ratio")),
                sampled_events=_count(raw.get("sampled_events")),
                values_truncated=raw.get("values_truncated")
                if isinstance(raw.get("values_truncated"), bool)
                else None,
                discovered_value_count=len(discovered) if isinstance(discovered, list) else None,
                detail=raw.get("detail") if isinstance(raw.get("detail"), str) else None,
            )
        )
    reasons = report.get("reasons")
    found = report.get("log_source_found")

    def text(key: str) -> str | None:
        value = report.get(key)
        return value if isinstance(value, str) else None

    return WafValidationReportOut(
        status=text("status"),
        reasons=[r for r in reasons if isinstance(r, str)] if isinstance(reasons, list) else [],
        log_source_found=found if isinstance(found, bool) else None,
        total_events=_count(report.get("total_events")),
        window_start=text("window_start"),
        window_end=text("window_end"),
        profile_version=_count(report.get("profile_version")),
        validated_at=text("validated_at"),
        fields=fields,
    )


async def _waf_source_rows(
    repo: AnomalyRepository,
    pairs: list[tuple[WafSourceProfile, LogSource]],
    settings: Settings,
    since: datetime,
    until: datetime,
) -> list[WafSourceBehaviorOut]:
    """Source summaries for a page of profiles: a fixed number of statements."""
    ids = [ls.id for _, ls in pairs]
    latest = await repo.waf_latest_buckets(ids)
    counts = await repo.waf_endpoint_counts(ids)
    overflow = await repo.waf_admission_overflow(ids, since=since, until=until)
    mapped = await repo.waf_virtual_host_mapped(ids)
    out: list[WafSourceBehaviorOut] = []
    for profile, source in pairs:
        bucket = latest.get(source.id)
        active, total = counts.get(source.id, (0, 0))
        seen = overflow.get(source.id)
        out.append(
            WafSourceBehaviorOut(
                log_source_id=source.id,
                log_source_name=source.name,
                product_label=profile.product_label,
                enabled=profile.enabled,
                profile_version=profile.profile_version,
                validation_status=str(profile.validation_status),
                validated_profile_version=profile.validated_profile_version,
                validated_at=profile.validated_at,
                validation_current=profile.validated_profile_version == profile.profile_version
                and str(profile.validation_status) == WafProfileValidationStatus.VALID.value,
                virtual_host_available=source.id in mapped,
                latest_bucket=(
                    WafLatestBucketOut(
                        bucket_start=bucket.bucket_start,
                        bucket_seconds=bucket.bucket_seconds,
                        completeness=str(bucket.completeness),
                        endpoint_coverage_ratio=bucket.endpoint_coverage_ratio,
                        coverage_measurement=str(bucket.coverage_measurement),
                        request_count_measurement=str(bucket.request_count_measurement),
                        total_events=bucket.total_events,
                        in_maintenance=bucket.in_maintenance,
                        groups_truncated=bucket.groups_truncated,
                        admission_truncated=bucket.admission_truncated,
                    )
                    if bucket is not None
                    else None
                ),
                active_endpoints=active,
                total_endpoints=total,
                active_endpoint_ceiling=settings.waf_max_active_endpoints_per_source,
                admission=(
                    WafAdmissionOverflowOut(
                        buckets_in_range=seen[0],
                        buckets_admission_truncated=seen[1],
                        rejected_over_quota=seen[2],
                        below_admission_threshold=seen[3],
                        untracked_requests=seen[4],
                    )
                    if seen is not None
                    else WafAdmissionOverflowOut(buckets_in_range=0)
                ),
            )
        )
    return out


@behavior_router.get("/waf/sources", response_model=PagedWafSources)
async def list_waf_sources(
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedWafSources:
    """Every WAF source's profile status, latest coverage and endpoint pressure.

    The range bounds only the admission-overflow figures; the profile, the latest
    bucket and the endpoint counts are the current state.
    """
    start, end = _validate_range(since, until, settings.api_max_range_days)
    limit = min(limit, settings.api_max_page_size)
    repo = AnomalyRepository(session)
    pairs, total = await repo.waf_sources_page(limit=limit, offset=offset)
    return PagedWafSources(
        items=await _waf_source_rows(repo, pairs, settings, start, end),
        total=total,
        limit=limit,
        offset=offset,
        range_start=start,
        range_end=end,
    )


@behavior_router.get("/waf/sources/{log_source_id}", response_model=WafSourceDetailOut)
async def get_waf_source(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> WafSourceDetailOut:
    start, end = _validate_range(since, until, settings.api_max_range_days)
    repo = AnomalyRepository(session)
    pair = await repo.waf_source(log_source_id)
    if pair is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source not found")
    (summary,) = await _waf_source_rows(repo, [pair], settings, start, end)
    return WafSourceDetailOut(
        **summary.model_dump(), validation_report=_validation_report(pair[0].validation_report)
    )


#: Worst first. INSUFFICIENT_DATA outranks NORMAL so an endpoint some detector
#: cannot yet judge is never summarized as normal.
_STATE_PRECEDENCE = (
    AnomalyState.OPEN,
    AnomalyState.RECOVERING,
    AnomalyState.CANDIDATE,
    AnomalyState.SUPPRESSED,
    AnomalyState.INSUFFICIENT_DATA,
    AnomalyState.NORMAL,
    AnomalyState.RESOLVED,
)


def _worst_state(states: list[str]) -> AnomalyState | None:
    present = set(states)
    return next((s for s in _STATE_PRECEDENCE if s.value in present), None)


@behavior_router.get(
    "/waf/sources/{log_source_id}/endpoints", response_model=PagedWafEndpoints
)
async def list_waf_endpoints(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    active_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedWafEndpoints:
    """The endpoint inventory of one WAF source.

    `since`/`until`, when given, keep endpoints last seen in that range; without
    them the whole inventory is paged. Retired endpoints stay listed unless
    `active_only` is set.
    """
    seen_since = seen_until = None
    if since is not None or until is not None:
        seen_since, seen_until = _validate_range(since, until, settings.api_max_range_days)
    limit = min(limit, settings.api_max_page_size)
    repo = AnomalyRepository(session)
    if await repo.waf_source(log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source not found")
    endpoints, total = await repo.waf_endpoints_page(
        log_source_id,
        limit=limit,
        offset=offset,
        active_only=active_only,
        seen_since=seen_since,
        seen_until=seen_until,
    )
    ids = [e.id for e in endpoints]
    adequacy = await repo.waf_baseline_adequacy(ids)
    states = await repo.waf_detector_states(ids)
    active = await repo.waf_active_anomaly_counts(ids)
    return PagedWafEndpoints(
        items=[
            WafEndpointInventoryOut(
                id=e.id,
                normalized_endpoint=e.normalized_endpoint,
                http_method=e.http_method,
                virtual_host=e.virtual_host,
                normalizer_version=e.normalizer_version,
                first_seen_at=e.first_seen_at,
                last_seen_at=e.last_seen_at,
                is_active=e.is_active,
                retired_at=e.retired_at,
                tracked_since=e.tracked_since,
                baseline=WafBaselineAdequacyOut(
                    cells_total=adequacy.get(e.id, (0, 0))[0],
                    reliable_cells=adequacy.get(e.id, (0, 0))[1],
                ),
                baseline_rebuilt_at=e.baseline_rebuilt_at,
                state=_worst_state(states.get(e.id, [])),
                active_anomaly_count=active.get(e.id, 0),
            )
            for e in endpoints
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@behavior_router.get("/waf/endpoints/{endpoint_id}/metrics", response_model=PagedWafMetrics)
async def waf_endpoint_metrics(
    endpoint_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PagedWafMetrics:
    """Stored buckets of one endpoint with their completeness and coverage.

    Only buckets that have a row appear. A bucket with no row is not a zero: the
    source's bucket record decides whether absence means "no requests".
    """
    start, end = _validate_range(since, until, settings.api_max_range_days)
    limit = min(limit, settings.api_max_page_size)
    endpoint = await session.get(WafEndpoint, endpoint_id)
    if endpoint is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF endpoint not found")
    repo = AnomalyRepository(session)
    rows, total = await repo.waf_metrics_page(
        endpoint, since=start, until=end, limit=limit, offset=offset
    )
    items: list[WafMetricBucketOut] = []
    for metric, bucket in rows:
        items.append(
            WafMetricBucketOut(
                bucket_start=metric.bucket_start,
                bucket_seconds=metric.bucket_seconds,
                request_count=metric.request_count,
                request_count_measurement=(
                    str(bucket.request_count_measurement) if bucket is not None else None
                ),
                completeness=str(metric.completeness),
                partial=metric.completeness == BucketCompleteness.PARTIAL,
                endpoint_coverage_ratio=metric.endpoint_coverage_ratio,
                in_maintenance=bucket.in_maintenance if bucket is not None else None,
                normalizer_version=metric.normalizer_version,
                distinct_client_count=metric.distinct_client_count,
                action_measurement=str(bucket.action_measurement) if bucket else None,
                response_code_measurement=(
                    str(bucket.response_code_measurement) if bucket else None
                ),
                client_measurement=str(bucket.client_measurement) if bucket else None,
                blocked_count=metric.blocked_count,
                action_events=metric.action_events,
                unmapped_action_count=metric.unmapped_action_count,
                response_code_events=metric.response_code_events,
                client_error_count=metric.client_error_count,
                server_error_count=metric.server_error_count,
                unclassified_response_count=metric.unclassified_response_count,
                client_events=metric.client_events,
                client_sources=metric.client_sources,
                top1_client_requests=metric.top1_client_requests,
                top5_client_requests=metric.top5_client_requests,
            )
        )
    return PagedWafMetrics(
        items=items, total=total, limit=limit, offset=offset, range_start=start, range_end=end
    )


@behavior_router.get(
    "/waf/endpoints/{endpoint_id}/baselines", response_model=PagedWafBaselines
)
async def waf_endpoint_baselines(
    endpoint_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    metric: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedWafBaselines:
    """Seasonal cells of one endpoint, every metric and normalizer version.

    Raw training observations are not served; a cell reports its sample count and
    whether it is reliable enough to drive a verdict.
    """
    limit = min(limit, settings.api_max_page_size)
    if await session.get(WafEndpoint, endpoint_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF endpoint not found")
    repo = AnomalyRepository(session)
    rows, total = await repo.waf_baselines_page(
        endpoint_id, metric=metric, limit=limit, offset=offset
    )
    return PagedWafBaselines(
        items=[WafBaselineCellOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )
