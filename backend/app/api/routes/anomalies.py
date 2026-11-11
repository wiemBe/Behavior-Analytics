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
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Annotated, NamedTuple

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.capabilities import (
    DetectorSpec,
    DetectorStatus,
    all_detectors,
    bucket_telemetry,
    detector_status,
)

# The traffic export's helpers live in `app.api.csv_export`; `_csv_field` stays
# importable from this module because the traffic export's tests import it here.
from app.api.csv_export import _csv_field as _csv_field
from app.api.csv_export import (
    _csv_row,
    _csv_time,
    csv_document,
    csv_export_response,
    csv_range_filename,
    csv_slug,
    csv_stamp,
)
from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.history.origin import origin_of
from app.history.state import HistoryFamily
from app.i18n.messages import MessageRef
from app.i18n.refusal import CodedHTTPException, permission_refusal
from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    AnomalyType,
    BucketCompleteness,
    EvidenceStatus,
    IpsAnomalyType,
    IpsDimension,
    IpsProfileValidationStatus,
    PwAnomalyType,
    PwScopeKind,
    RuleAnomalyType,
    Severity,
    TriageDisposition,
    TriageStatus,
    WafAnomalyType,
    WafProfileValidationStatus,
)
from app.models.explanation import AnomalyExplanation
from app.models.ips import (
    IpsAnomaly,
    IpsAnomalyTransition,
    IpsEntity,
    IpsSourceBucket,
    IpsSourceProfile,
)
from app.models.log_source import LogSource, LogSourceAnomaly, LogSourceBaseline, LogSourceMetric
from app.models.monitoring import AnomalyEvaluationFault
from app.models.property_watch import PropertyWatch, PwAnomaly, PwAnomalyTransition
from app.models.rule import AnalyticsRule
from app.models.rule_anomaly import RuleAnomaly, RuleAnomalyTransition
from app.models.waf import WafAnomaly, WafEndpoint, WafSourceProfile
from app.pivot.service import PivotNotFound, anomaly_pivot
from app.property_watch.facts import split_tuple_key
from app.providers.base import LogSourceTelemetry
from app.repositories.anomaly import AnomalyRepository, WafTrafficRow
from app.repositories.anomaly_faults import AnomalyFaultRepository
from app.repositories.anomaly_histogram import BUCKET_SECONDS, check_range, histogram
from app.repositories.audit import record_audit
from app.repositories.ips_behavior import IpsBehaviorRepository, IpsTrafficRow
from app.repositories.triage import TriageRepository
from app.schemas.anomaly import (
    ActiveByFamily,
    ActiveStateCounts,
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
    IpsEntityRefOut,
    IpsTrafficSort,
    MetricBucketOut,
    PagedAnomalies,
    PagedWafBaselines,
    PagedWafEndpoints,
    PagedWafMetrics,
    PagedWafSources,
    PwValueRefOut,
    RuleRefOut,
    SkippedBucketOut,
    SourceBehaviorOut,
    SourceEvaluationOut,
    SubjectKind,
    TrafficItemOut,
    TrafficOut,
    TrafficSortOrder,
    TrafficState,
    TrafficWindow,
    WafAdmissionOverflowOut,
    WafBaselineAdequacyOut,
    WafBaselineCellOut,
    WafEndpointInventoryOut,
    WafEndpointRefOut,
    WafLatestBucketOut,
    WafMetricBucketOut,
    WafSourceBehaviorOut,
    WafSourceDetailOut,
    WafTrafficSort,
    WafValidationFieldOut,
    WafValidationReportOut,
    detection_detail,
    ips_detection_detail,
    pw_detection_detail,
    waf_detection_detail,
)
from app.schemas.ips_behavior import (
    IpsAdmissionOut,
    IpsBaselineAdequacyOut,
    IpsBaselineCellOut,
    IpsDimensionCoverageOut,
    IpsEntityInventoryOut,
    IpsEntityMetricBucketOut,
    IpsEntityPressureOut,
    IpsLatestBucketOut,
    IpsSourceBehaviorOut,
    IpsSourceMetricBucketOut,
    PagedIpsBaselines,
    PagedIpsEntities,
    PagedIpsMetrics,
    PagedIpsSources,
)
from app.schemas.messages import stored_message_parts, stored_reasons
from app.schemas.pivot import PivotOut
from app.schemas.related_activity import RelatedActivityOut, RelatedPad
from app.schemas.triage import (
    TriageCommentIn,
    TriageEventOut,
    TriageOut,
    TriagePatchIn,
    TriageSummaryOut,
)
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_ANOMALY_READ,
    PERM_ANOMALY_TRIAGE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.services.anomaly_triage import (
    AnomalyTriageService,
    TriageConflict,
    TriageInvalid,
    TriageNotFound,
)
from app.services.queue import FAMILY_BY_SUBJECT
from app.services.related_activity import (
    RelatedActivityNotFound,
    RelatedActivityService,
)

router = APIRouter(prefix="/anomalies", tags=["anomalies"])
behavior_router = APIRouter(prefix="/behavior", tags=["behavior"])

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


def _family(value: str) -> AnomalyFamily:
    """Parse the family after authorization, so a refused caller learns nothing.

    The path parameter is a plain string rather than an enum on purpose: FastAPI
    validates an enum before the handler's write guard runs, so a read-only caller
    would get 422 for a bad family instead of 403 for the missing permission.
    """
    try:
        return AnomalyFamily(value)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "unknown anomaly family: expected log_source, waf, ips, property_watch or rule",
        ) from exc


def _validate_range(
    since: datetime | None,
    until: datetime | None,
    max_days: int,
) -> tuple[datetime, datetime]:
    """Clamp and validate a caller-supplied time range.

    Without `since` the range covers the day before its end.

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
            status.HTTP_422_UNPROCESSABLE_CONTENT, "range end must be after its start"
        )
    if (end - start) > timedelta(days=max_days):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"range exceeds the maximum of {max_days} days",
        )
    return start, end


def _active_only_range(
    since: datetime | None,
    until: datetime | None,
    max_days: int,
) -> tuple[datetime | None, datetime]:
    """The range of an active-only list: no lower bound unless `since` is sent.

    "Active" is a state, not a window. An incident detected before the range
    ceiling is still active, and dropping it would hide an open investigation
    while the summary still counted it. Without `since`, an active-only list
    therefore has an end only (`until`, or now), however old its rows are. An
    explicit range keeps the ceiling `_validate_range` enforces: that one is a
    request to scan a named window, not a request for the active set.
    """
    if since is None:
        end = until or datetime.now(UTC)
        if end.tzinfo is None:
            end = end.replace(tzinfo=UTC)
        return None, end
    return _validate_range(since, until, max_days)


# --------------------------------------------------------------------- list
async def _unified_rows(
    repo: AnomalyRepository,
    *,
    principal: Principal,
    settings: Settings,
    limit: int,
    offset: int,
    log_source_id: uuid.UUID | None,
    instance_id: uuid.UUID | None,
    anomaly_type: AnomalyType
    | WafAnomalyType
    | IpsAnomalyType
    | PwAnomalyType
    | RuleAnomalyType
    | None,
    subject_kind: list[SubjectKind] | None,
    waf_endpoint_id: uuid.UUID | None,
    ips_entity_id: uuid.UUID | None,
    property_watch_id: uuid.UUID | None,
    state: AnomalyState | None,
    severity: Severity | None,
    evidence_status: EvidenceStatus | None,
    triage_status: TriageStatus | None,
    owner: str | None,
    disposition: TriageDisposition | None,
    active_only: bool,
    since: datetime | None,
    until: datetime | None,
) -> tuple[list[dict], int, datetime | None, datetime]:
    """The unified list's window, owner resolution and read, plus that window.

    `GET /anomalies` and `GET /anomalies.csv` differ only in how many rows they
    ask for and where they start (`limit`, `min(limit, api_max_page_size)`), so
    the rules that decide *which* rows are meant live here once: the active-only
    window, `owner=me` resolved against the caller, and the filter object the
    repository turns into the union. Returns `(rows, total, start, end)`; the
    export names its file after the window it actually read.
    """
    # "Active" is a state, not a time window: without `since`, an active-only list has no
    # lower bound, so it holds every active incident up to `until`, whatever its age, and
    # matches the Dashboard's count. Without `active_only` the list keeps its 1-day default.
    # An explicit `since`/`until` is used and bounded exactly as before; the summary
    # (`/anomalies/summary`) counts the same active population with no lower bound either.
    if active_only:
        start, end = _active_only_range(since, until, settings.api_max_range_days)
    else:
        start, end = _validate_range(since, until, settings.api_max_range_days)
    # `owner=me` is resolved here, against the authenticated principal, not in SQL:
    # the database has no notion of the caller. `none` stays a literal and asks for
    # an unowned item, including one with no triage row at all.
    owner_filter = principal.subject if owner == "me" else owner
    rows, total = await repo.list_unified(
        limit=limit,
        offset=offset,
        subject_kind=subject_kind,
        waf_endpoint_id=waf_endpoint_id,
        ips_entity_id=ips_entity_id,
        pw_watch_id=property_watch_id,
        log_source_id=log_source_id,
        instance_id=instance_id,
        anomaly_type=anomaly_type.value if anomaly_type is not None else None,
        state=state,
        severity=severity,
        evidence_status=evidence_status,
        triage_status=triage_status,
        triage_owner=owner_filter,
        triage_disposition=disposition,
        active_only=active_only,
        since=start,
        until=end,
    )
    return rows, total, start, end


@router.get("", response_model=PagedAnomalies)
async def list_anomalies(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    log_source_id: uuid.UUID | None = Query(default=None),
    instance_id: uuid.UUID | None = Query(default=None),
    anomaly_type: AnomalyType
    | WafAnomalyType
    | IpsAnomalyType
    | PwAnomalyType
    | RuleAnomalyType
    | None = Query(default=None),
    subject_kind: Annotated[list[SubjectKind] | None, Query()] = None,
    waf_endpoint_id: uuid.UUID | None = Query(default=None),
    ips_entity_id: uuid.UUID | None = Query(default=None),
    property_watch_id: uuid.UUID | None = Query(default=None),
    state: AnomalyState | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    evidence_status: EvidenceStatus | None = Query(default=None),
    triage_status: TriageStatus | None = Query(default=None),
    owner: str | None = Query(default=None, max_length=255),
    disposition: TriageDisposition | None = Query(default=None),
    active_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedAnomalies:
    rows, total, _start, _end = await _unified_rows(
        AnomalyRepository(session),
        principal=principal,
        settings=settings,
        limit=min(limit, settings.api_max_page_size),
        offset=offset,
        log_source_id=log_source_id,
        instance_id=instance_id,
        anomaly_type=anomaly_type,
        subject_kind=subject_kind,
        waf_endpoint_id=waf_endpoint_id,
        ips_entity_id=ips_entity_id,
        property_watch_id=property_watch_id,
        state=state,
        severity=severity,
        evidence_status=evidence_status,
        triage_status=triage_status,
        owner=owner,
        disposition=disposition,
        active_only=active_only,
        since=since,
        until=until,
    )
    return PagedAnomalies(
        items=[_unified_item(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


# --------------------------------------------------------------- CSV export
#: The export's column order, fixed by the SOC-8 design (§5): identity, source,
#: severity, lifecycle and the analyst's triage. The explanation, the contributors
#: and the detector's raw numbers are deliberately absent.
ANOMALY_EXPORT_COLUMNS = (
    "id",
    "family",
    "anomaly_type",
    "log_source_id",
    "log_source_name",
    "severity",
    "state",
    "detected_at",
    "opened_at",
    "resolved_at",
    "triage_status",
    "triage_owner",
    "triage_disposition",
)


def _anomaly_export_row(row: dict) -> tuple[object, ...]:
    """One row of the unified union as the export's cells, in column order.

    `subject_kind` is the union's own discriminator, and the family comes from
    the mapping the triage queue already reads (`app.services.queue`), so a new
    subject kind cannot be exported under a stale family. A kind outside that
    map belongs to no family, and its cell is empty rather than forcing a bad
    value into the file -- an unknown kind must not cost the whole export. A
    missing triage row exports as NEW and unowned, which is what the JSON list
    reads it as; the owner and disposition cells stay empty, because neither is
    known.
    """
    family = FAMILY_BY_SUBJECT.get(row["subject_kind"])
    return (
        row["id"],
        None if family is None else family.value,
        row["anomaly_type"],
        row["log_source_id"],
        row["log_source_name"],
        row["severity"],
        row["state"],
        _csv_time(row["detected_at"]),
        _csv_time(row["opened_at"]),
        _csv_time(row["resolved_at"]),
        row["triage_status"] or TriageStatus.NEW.value,
        row["triage_owner"],
        row["triage_disposition"],
    )


@router.get(
    ".csv",
    response_class=Response,
    responses={
        200: {
            "description": (
                "The filtered anomaly list as RFC 4180 CSV, at most "
                "API_MAX_EXPORT_ROWS rows in the list's own order. The header row is "
                "always present; X-Export-Truncated says the export was cut."
            ),
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def export_anomalies_csv(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    log_source_id: uuid.UUID | None = Query(default=None),
    instance_id: uuid.UUID | None = Query(default=None),
    anomaly_type: AnomalyType
    | WafAnomalyType
    | IpsAnomalyType
    | PwAnomalyType
    | RuleAnomalyType
    | None = Query(default=None),
    subject_kind: Annotated[list[SubjectKind] | None, Query()] = None,
    waf_endpoint_id: uuid.UUID | None = Query(default=None),
    ips_entity_id: uuid.UUID | None = Query(default=None),
    property_watch_id: uuid.UUID | None = Query(default=None),
    state: AnomalyState | None = Query(default=None),
    severity: Severity | None = Query(default=None),
    evidence_status: EvidenceStatus | None = Query(default=None),
    triage_status: TriageStatus | None = Query(default=None),
    owner: str | None = Query(default=None, max_length=255),
    disposition: TriageDisposition | None = Query(default=None),
    active_only: bool = Query(default=False),
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> Response:
    """The filtered list as a CSV attachment, one row per anomaly.

    The parameters are the list's and so are the rows: the window, the filters,
    the union and its ordering all come from `_unified_rows`, so the export of a
    narrowed list is exactly what the page showed. There is no paging here --
    the file is the first `api_max_export_rows` of the filtered set, and when
    more matched the response says `X-Export-Truncated: true`.

    Declared before `/{anomaly_id}`, like `/summary`: a UUID path parameter must
    never be the route that answers an export request.
    """
    rows, total, start, end = await _unified_rows(
        AnomalyRepository(session),
        principal=principal,
        settings=settings,
        limit=settings.api_max_export_rows,
        offset=0,
        log_source_id=log_source_id,
        instance_id=instance_id,
        anomaly_type=anomaly_type,
        subject_kind=subject_kind,
        waf_endpoint_id=waf_endpoint_id,
        ips_entity_id=ips_entity_id,
        property_watch_id=property_watch_id,
        state=state,
        severity=severity,
        evidence_status=evidence_status,
        triage_status=triage_status,
        owner=owner,
        disposition=disposition,
        active_only=active_only,
        since=since,
        until=until,
    )
    return csv_export_response(
        csv_document(ANOMALY_EXPORT_COLUMNS, (_anomaly_export_row(row) for row in rows)),
        filename=csv_range_filename("anomalies", start, end),
        truncated=total > len(rows),
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
    ips_entity = None
    if row["ips_entity_id"] is not None:
        ips_entity = IpsEntityRefOut(
            id=row["ips_entity_id"],
            dimension=row["entity_dimension"],
            value_key=row["entity_value_key"],
            display_name=row["entity_display_name"],
        )
    pw_value = None
    if row["pw_watch_id"] is not None:
        pw_value = PwValueRefOut(
            watch_id=row["pw_watch_id"],
            watch_name=row["pw_watch_name"],
            watch_version=row["pw_watch_version"],
            scope_kind=row["pw_scope_kind"],
            properties=row["pw_properties"],
            value_key=row["pw_value_key"],
            components=split_tuple_key(row["pw_value_key"]),
            value_id=row["pw_value_id"],
        )
    rule = None
    if row["rule_id"] is not None:
        rule = RuleRefOut(
            rule_id=row["rule_id"],
            name=row["rule_name"],
            qradar_id=row["rule_qradar_id"],
            is_building_block=bool(row["rule_is_building_block"]),
        )
    fields = {
        k: v
        for k, v in row.items()
        if not k.startswith(("endpoint_", "entity_", "pw_", "triage_", "rule_"))
        and k not in ("waf_endpoint_id", "ips_entity_id", "explanation_i18n")
    }
    stored = row.get("explanation_i18n")
    explanation_code, explanation_params = (
        stored_message_parts(stored.get("code"), stored.get("params"))
        if isinstance(stored, dict)
        else (None, None)
    )
    return AnomalyListItem(
        **fields,
        explanation_code=explanation_code,
        explanation_params=explanation_params,
        # The join is left, so a missing triage row is a NULL trio: NEW and unowned.
        triage=TriageSummaryOut(
            status=row["triage_status"] or TriageStatus.NEW,
            owner=row["triage_owner"],
            disposition=row["triage_disposition"],
            updated_at=row["triage_updated_at"],
        ),
        waf_endpoint=waf_endpoint,
        ips_entity=ips_entity,
        pw_value=pw_value,
        rule=rule,
    )


async def _list_items(
    repo: AnomalyRepository, rows: list[LogSourceAnomaly]
) -> list[AnomalyListItem]:
    """Anomaly rows with source names and triage resolved in bulk.

    One name query and one triage query for the whole page, never one per row: these
    lists (recently resolved, highest deviation) are small today but the shape must not
    depend on that.
    """
    names = await repo.source_names(rows)
    triage = await TriageRepository(repo.session).summaries_for(
        AnomalyFamily.LOG_SOURCE, [row.id for row in rows]
    )
    items = []
    for row in rows:
        item = AnomalyListItem.model_validate(row)
        item.log_source_name = names.get(row.log_source_id)
        item.triage = triage[row.id]
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
    fault_sources, buckets_not_judged = await AnomalyFaultRepository(
        session
    ).summary_counts(since=now - timedelta(hours=24), until=now)
    resolved_last_24h = await repo.resolved_count(
        since=now - timedelta(hours=24), until=now
    )

    # The same population as `GET /anomalies?active_only=true` without `since`: every active
    # incident up to `now`, however old, so the family counts add up to that list's total.
    family_counts = await repo.active_family_counts(since=None, until=now)

    def family(name: str) -> ActiveStateCounts:
        by_state = {s.value: family_counts.get((name, s.value), 0) for s in active}
        return ActiveStateCounts(
            open=by_state[AnomalyState.OPEN.value],
            candidate=by_state[AnomalyState.CANDIDATE.value],
            recovering=by_state[AnomalyState.RECOVERING.value],
            total=sum(by_state.values()),
        )

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
        sources_with_evaluation_faults=fault_sources,
        buckets_not_judged=buckets_not_judged,
        resolved_last_24h=resolved_last_24h,
        recently_resolved=await _list_items(repo, await repo.recently_resolved()),
        highest_deviation=await _list_items(repo, await repo.highest_deviation()),
        active_by_family=ActiveByFamily(
            log_source=family("log_source"),
            waf=family("waf"),
            ips=family("ips"),
            property_watch=family("property_watch"),
            rule=family("rule"),
        ),
    )


@router.get("/histogram", response_model=AnomalyHistogramOut)
async def anomaly_histogram(
    session: SessionDep,
    settings: SettingsDep,
    # Declared before /{anomaly_id}: a route after it would parse "histogram"
    # as an id and answer every histogram call with a uuid parse error.
    since: datetime = Query(...),
    until: datetime = Query(...),
    subject_kind: Annotated[list[SubjectKind] | None, Query()] = None,
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
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
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


@router.get("/triage/{family}/{anomaly_id}", response_model=TriageOut)
async def get_anomaly_triage(
    family: str,
    anomaly_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> TriageOut:
    """The analyst state and one page of activity for one anomaly.

    Reading it is covered by the router's `read:anomalies` guard, like the anomaly
    itself. An anomaly with no triage row answers NEW, unowned, version 0.
    """
    resolved = _family(family)
    try:
        return await AnomalyTriageService(session).get(
            resolved,
            anomaly_id,
            limit=min(limit, settings.api_max_page_size),
            offset=offset,
        )
    except TriageNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.patch("/triage/{family}/{anomaly_id}", response_model=TriageOut)
async def patch_anomaly_triage(
    family: str,
    anomaly_id: uuid.UUID,
    payload: TriagePatchIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> TriageOut:
    """Change owner, status, disposition or its note.

    A stale `expected_version` is 409; an invalid transition or a close without a
    disposition is 422 and writes nothing.
    """
    _guard(principal, PERM_ANOMALY_TRIAGE)
    resolved = _family(family)
    try:
        result, events = await AnomalyTriageService(session).update(
            resolved, anomaly_id, payload, actor=principal.subject
        )
    except TriageNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TriageConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except TriageInvalid as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if events:
        # Field names and from-to values only. A disposition note is analyst text and
        # stays on the triage event row, never in the audit detail.
        await record_audit(
            session,
            action="anomaly.triage.update",
            actor=principal.subject,
            object_type="anomaly_triage",
            object_id=f"{resolved.value}:{anomaly_id}",
            detail={
                "family": resolved.value,
                "version": result.version,
                "changes": [
                    {
                        "field": event.kind.value,
                        "from": event.from_value,
                        "to": event.to_value,
                    }
                    for event in events
                ],
            },
        )
    return result


@router.post(
    "/triage/{family}/{anomaly_id}/comments",
    response_model=TriageEventOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_anomaly_triage_comment(
    family: str,
    anomaly_id: uuid.UUID,
    payload: TriageCommentIn,
    session: SessionDep,
    principal: PrincipalDep,
) -> TriageEventOut:
    """Append a comment to the activity log.

    A comment creates the triage row when none exists, at status NEW and unowned: an
    analyst may note something before they start working the item. It does not bump
    `version`, because it changes no triage state and must not make a client's next
    PATCH stale. The text is never logged and never reaches the audit detail.
    """
    _guard(principal, PERM_ANOMALY_TRIAGE)
    resolved = _family(family)
    try:
        event = await AnomalyTriageService(session).comment(
            resolved, anomaly_id, payload.text, actor=principal.subject
        )
    except TriageNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except TriageConflict as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    await record_audit(
        session,
        action="anomaly.triage.comment",
        actor=principal.subject,
        object_type="anomaly_triage",
        object_id=f"{resolved.value}:{anomaly_id}",
        detail={
            "family": resolved.value,
        },
    )
    return event


@router.get("/related/{family}/{anomaly_id}", response_model=RelatedActivityOut)
async def get_related_activity(
    family: str,
    anomaly_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    pad: RelatedPad = Query(default=RelatedPad.TWO_HOURS),
) -> RelatedActivityOut:
    """Offenses, other anomalies and rules on the anchor's source around its window.

    Reading is covered by the router's `read:anomalies` guard. Each section carries its
    own state, so an uncollected offense feed is never an empty clean list. The window
    is the anomaly's own stored interval padded on both sides; an open anomaly ends at
    its newest evaluated bucket's end, never at the wall clock.
    """
    resolved = _family(family)
    try:
        return await RelatedActivityService(session, settings).read(
            resolved, anomaly_id, pad
        )
    except RelatedActivityNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/{anomaly_id}", response_model=AnomalyDetailOut)
async def get_anomaly(anomaly_id: uuid.UUID, session: SessionDep) -> AnomalyDetailOut:
    repo = AnomalyRepository(session)
    anomaly = await repo.get_detail(anomaly_id)
    if anomaly is None:
        waf_anomaly = await repo.get_waf_detail(anomaly_id)
        if waf_anomaly is not None:
            detail = await _waf_detail(repo, session, waf_anomaly)
            family = AnomalyFamily.WAF
        else:
            ips = await repo.get_ips_detail(anomaly_id)
            if ips is not None:
                anomaly_row, entity, transitions = ips
                detail = await _ips_detail(session, anomaly_row, entity, transitions)
                family = AnomalyFamily.IPS
            else:
                pw = await repo.get_pw_detail(anomaly_id)
                if pw is not None:
                    detail = _pw_detail(*pw)
                    family = AnomalyFamily.PROPERTY_WATCH
                else:
                    rule = await repo.get_rule_detail(anomaly_id)
                    if rule is None:
                        raise HTTPException(status.HTTP_404_NOT_FOUND, "anomaly not found")
                    detail = _rule_detail(*rule)
                    family = AnomalyFamily.RULE
    else:
        detail = AnomalyDetailOut.model_validate(anomaly)
        source = await session.get(LogSource, anomaly.log_source_id)
        detail.log_source_name = source.name if source else None
        detail.transitions = [
            AnomalyTransitionOut.model_validate(t) for t in anomaly.transitions
        ]
        detail.explanation_package = _explanation_out(anomaly.explanation_package)
        detail.detection = detection_detail(anomaly.details)
        family = AnomalyFamily.LOG_SOURCE

    detail.triage = await TriageRepository(session).summary(family, anomaly_id)
    return detail


@router.get("/{anomaly_id}/pivot", response_model=PivotOut)
async def get_anomaly_pivot(anomaly_id: uuid.UUID, session: SessionDep) -> PivotOut:
    """AQL text and a console link for the analyst. The product does not run the AQL."""
    try:
        return await anomaly_pivot(session, anomaly_id)
    except PivotNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


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
        suppression_reason=anomaly.suppression_reason,
        suppression_reason_code=anomaly.suppression_reason_code,
        suppression_reason_params=anomaly.suppression_reason_params,
        observed_measurement=str(anomaly.observed_measurement),
        explanation=anomaly.explanation,
        explanation_code=anomaly.explanation_code,
        explanation_params=anomaly.explanation_params,
        baseline_version=anomaly.baseline_version,
        policy_version=anomaly.policy_version,
        transitions=[AnomalyTransitionOut.model_validate(t) for t in anomaly.transitions],
        explanation_package=None,
        detection=waf_detection_detail(
            anomaly.details, normalizer_version=anomaly.normalizer_version
        ),
    )


async def _ips_detail(
    session: AsyncSession,
    anomaly: IpsAnomaly,
    entity: IpsEntity | None,
    transitions: list[IpsAnomalyTransition],
) -> AnomalyDetailOut:
    """An IPS incident as the shared detail shape.

    `explanation_package` is None: an IPS incident has no evidence package, and
    a fabricated one would claim evidence nobody collected. Source addresses
    exist only inside the stored, bounded contributor lists, which the
    detection projection does not forward.
    """
    source_id = entity.log_source_id if entity is not None else anomaly.log_source_id
    # The subject constraint guarantees exactly one of the two ids is set.
    assert source_id is not None, "an IPS subject always names a log source"
    source = await session.get(LogSource, source_id)
    return AnomalyDetailOut(
        id=anomaly.id,
        subject_kind=SubjectKind(anomaly.subject_kind),
        log_source_id=source_id,
        log_source_name=source.name if source else None,
        ips_entity=(
            IpsEntityRefOut(
                id=entity.id,
                dimension=entity.dimension,
                value_key=entity.value_key,
                display_name=entity.display_name,
            )
            if entity is not None
            else None
        ),
        anomaly_type=IpsAnomalyType(anomaly.detector_type),
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
        suppression_reason=anomaly.suppression_reason,
        suppression_reason_code=anomaly.suppression_reason_code,
        suppression_reason_params=anomaly.suppression_reason_params,
        observed_measurement=str(anomaly.observed_measurement),
        explanation=anomaly.explanation,
        explanation_code=anomaly.explanation_code,
        explanation_params=anomaly.explanation_params,
        baseline_version=anomaly.baseline_version,
        policy_version=anomaly.policy_version,
        transitions=[AnomalyTransitionOut.model_validate(t) for t in transitions],
        explanation_package=None,
        detection=ips_detection_detail(anomaly.details),
    )


def _pw_detail(
    anomaly: PwAnomaly,
    watch: PropertyWatch,
    source: LogSource | None,
    transitions: list[PwAnomalyTransition],
) -> AnomalyDetailOut:
    """A property-watch incident as the shared detail shape.

    `explanation_package` is None: a PW incident has no evidence package, and a fabricated
    one would claim evidence nobody collected. The value text is shown to any `read:*`
    caller (D-22) in `pw_value`, as in the list, and is never logged. A type- or
    group-scoped watch has no single source, so `log_source_id` is None.
    """
    return AnomalyDetailOut(
        id=anomaly.id,
        subject_kind=SubjectKind.PW_VALUE,
        log_source_id=watch.log_source_id,
        log_source_name=source.name if source else None,
        pw_value=PwValueRefOut(
            watch_id=watch.id,
            watch_name=watch.name,
            watch_version=anomaly.watch_version,
            scope_kind=PwScopeKind(str(watch.scope_kind)),
            properties=watch.properties,
            value_key=anomaly.value_key,
            components=split_tuple_key(anomaly.value_key),
            value_id=anomaly.pw_value_id,
        ),
        anomaly_type=PwAnomalyType(anomaly.detector_type),
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
        explanation_code=anomaly.explanation_code,
        explanation_params=anomaly.explanation_params,
        baseline_version=anomaly.baseline_version,
        policy_version=anomaly.policy_version,
        transitions=[AnomalyTransitionOut.model_validate(t) for t in transitions],
        explanation_package=None,
        detection=pw_detection_detail(anomaly.details),
    )


def _rule_detail(
    anomaly: RuleAnomaly,
    rule: AnalyticsRule,
    measurement: str,
    transitions: list[RuleAnomalyTransition],
) -> AnomalyDetailOut:
    """A rule-match incident as the shared detail shape.

    A rule has no log source, so `log_source_id` is None. `explanation_package` and
    `detection` are None: a rule incident has no evidence package, and the schema has no
    rule projection of its stored evidence; the expected range comes from the rule's own
    match-anomaly section. `observed_measurement` is LOWER_BOUND for a truncated window.
    """
    return AnomalyDetailOut(
        id=anomaly.id,
        subject_kind=SubjectKind.RULE,
        log_source_id=None,
        log_source_name=None,
        rule=RuleRefOut(
            rule_id=rule.id,
            name=rule.name,
            qradar_id=anomaly.qradar_id,
            is_building_block=anomaly.is_building_block,
        ),
        anomaly_type=RuleAnomalyType(anomaly.detector_type),
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
        suppression_reason=anomaly.suppression_reason,
        suppression_reason_code=anomaly.suppression_reason_code,
        suppression_reason_params=anomaly.suppression_reason_params,
        observed_measurement=measurement,
        explanation=anomaly.explanation,
        explanation_code=anomaly.explanation_code,
        explanation_params=anomaly.explanation_params,
        baseline_version=None,
        policy_version=anomaly.policy_version,
        transitions=[AnomalyTransitionOut.model_validate(t) for t in transitions],
        explanation_package=None,
        detection=None,
    )


def _explanation_out(package: AnomalyExplanation | None) -> ExplanationOut | None:
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
_EVALUATION_WINDOW = timedelta(hours=24)
_SKIPPED_BUCKETS_SHOWN = 5


def _current_fault(
    faults: list[AnomalyEvaluationFault],
) -> AnomalyEvaluationFault | None:
    """The fault the page should name: open first, then given-up, then cleared."""
    open_ones = [f for f in faults if f.resolved_at is None and f.given_up_at is None]
    if open_ones:
        return max(open_ones, key=lambda f: f.last_failed_at)
    given_up = [f for f in faults if f.given_up_at is not None]
    if given_up:
        return max(given_up, key=lambda f: f.given_up_at or f.last_failed_at)
    resolved = [f for f in faults if f.resolved_at is not None]
    if resolved:
        return max(resolved, key=lambda f: f.resolved_at or f.last_failed_at)
    return None


def _skipped_buckets(faults: list[AnomalyEvaluationFault]) -> list[SkippedBucketOut]:
    given_up = [f for f in faults if f.given_up_at is not None]
    given_up.sort(key=lambda f: f.bucket_start, reverse=True)
    return [
        SkippedBucketOut(
            bucket_start=f.bucket_start,
            error_class=f.error_class,
            attempts=f.attempts,
            given_up_at=f.given_up_at,
            last_failure_at=f.last_failed_at,
        )
        for f in given_up[:_SKIPPED_BUCKETS_SHOWN]
    ]


def _evaluation_out(
    judged_through: datetime | None,
    fault: AnomalyEvaluationFault | None,
    *,
    max_attempts: int,
) -> SourceEvaluationOut | None:
    if judged_through is None and fault is None:
        return None
    return SourceEvaluationOut(
        judged_through=judged_through,
        failed_bucket_start=fault.bucket_start if fault is not None else None,
        attempts=fault.attempts if fault is not None else None,
        max_attempts=max_attempts,
        error_class=fault.error_class if fault is not None else None,
        last_failure_at=fault.last_failed_at if fault is not None else None,
        given_up_at=fault.given_up_at if fault is not None else None,
        resolved_at=fault.resolved_at if fault is not None else None,
    )


@behavior_router.get("/sources", response_model=list[SourceBehaviorOut])
async def list_source_behavior(
    session: SessionDep, settings: SettingsDep
) -> list[SourceBehaviorOut]:
    """Observed vs expected volume for every monitored source.

    Two bulk reads for the whole fleet, whatever its size, plus one bulk
    evaluation-fault read: the Home view calls this on every auto-refresh, and
    a statement per source would make its cost grow with the fleet.
    """
    repo = AnomalyRepository(session)
    faults_repo = AnomalyFaultRepository(session)
    sources = await repo.monitored_sources()
    active_counts = await repo.active_counts_by_source()
    ids = [src.id for src in sources]
    buckets = await repo.latest_buckets(ids)
    cells = await repo.baseline_cells(
        [
            (src.id, buckets[src.id].bucket_start.isoweekday(), buckets[src.id].bucket_start.hour)
            for src in sources
            if src.id in buckets
        ],
        metric="average_eps",
    )
    now = datetime.now(UTC)
    watermarks = await faults_repo.evaluated_through(ids)
    faults_by_source = await faults_repo.evaluation_faults(
        ids, since=now - _EVALUATION_WINDOW, until=now
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
        faults = faults_by_source.get(src.id, [])
        out.append(
            _source_behavior(
                src,
                bucket,
                cell,
                active_counts.get(src.id, 0),
                judged_through=watermarks.get(src.id),
                faults=faults,
                max_attempts=settings.anomaly_max_bucket_attempts,
            )
        )
    return out


def _source_behavior(
    src: LogSource,
    bucket: LogSourceMetric | None,
    cell: LogSourceBaseline | None,
    active_count: int,
    *,
    judged_through: datetime | None,
    faults: list[AnomalyEvaluationFault],
    max_attempts: int,
) -> SourceBehaviorOut:
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

    skipped = _skipped_buckets(faults)
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
        evaluation=_evaluation_out(
            judged_through, _current_fault(faults), max_attempts=max_attempts
        ),
        skipped_buckets_in_window=sum(1 for f in faults if f.given_up_at is not None),
        skipped_buckets=skipped,
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
        # Phase A keeps no `origin` column: the marker lives in the row's
        # `collection_source` (§B.4.1), and the shared helper owns that spelling.
        item = MetricBucketOut(
            bucket_start=row.bucket_start,
            bucket_seconds=row.bucket_seconds,
            event_count=row.event_count,
            average_eps=row.average_eps,
            peak_eps=row.peak_eps,
            completeness=row.completeness,
            last_event_at=row.last_event_at,
            origin=origin_of(HistoryFamily.PHASE_A, row.collection_source),
        )
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
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
) -> SourceBehaviorOut:
    src = await session.get(LogSource, log_source_id)
    if src is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "log source not found")
    start, end = _validate_range(since, until, settings.api_max_range_days)
    repo = AnomalyRepository(session)
    faults_repo = AnomalyFaultRepository(session)
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
    watermarks = await faults_repo.evaluated_through([log_source_id])
    faults_by_source = await faults_repo.evaluation_faults(
        [log_source_id], since=start, until=end
    )
    return _source_behavior(
        src,
        bucket,
        cell,
        counts.get(log_source_id, 0),
        judged_through=watermarks.get(log_source_id),
        faults=faults_by_source.get(log_source_id, []),
        max_attempts=settings.anomaly_max_bucket_attempts,
    )



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
        detail_code, detail_params = stored_message_parts(
            raw.get("detail_code"), raw.get("detail_params")
        )
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
                detail_code=detail_code,
                detail_params=detail_params,
            )
        )
    reasons, reason_codes = stored_reasons(report.get("reasons"), report.get("reason_codes"))
    found = report.get("log_source_found")

    def text(key: str) -> str | None:
        value = report.get(key)
        return value if isinstance(value, str) else None

    return WafValidationReportOut(
        status=text("status"),
        reasons=reasons,
        reason_codes=reason_codes,
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
                        origin=origin_of(HistoryFamily.WAF, bucket.origin),
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
                # The endpoint metric row carries no marker of its own: the
                # source's bucket record does (§B.4.2).
                origin=(
                    origin_of(HistoryFamily.WAF, bucket.origin) if bucket is not None else None
                ),
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


# ------------------------------------------------------- traffic ranking (TOP-1)
#: Windows in seconds. `5m` is deliberately absent: it is exactly the newest
#: complete stored bucket, whose own width the response reports.
_WINDOW_SECONDS: dict[TrafficWindow, int] = {
    TrafficWindow.ONE_HOUR: 3600,
    TrafficWindow.ONE_DAY: 86400,
    TrafficWindow.ONE_WEEK: 7 * 86400,
    TrafficWindow.FOUR_WEEKS: 28 * 86400,
}

#: Sorts over text default to ascending; count sorts default to descending.
_TEXT_SORTS = frozenset({"host", "endpoint", "ip"})


def _traffic_bounds(
    window: TrafficWindow, newest_start: datetime, bucket_seconds: int
) -> tuple[datetime, datetime, int]:
    """The window's `[start, end)` and how many whole buckets it should hold.

    `5m` is exactly the newest complete bucket. Every other window reaches back
    from that bucket's end over whole buckets, so `range_start` is the first
    bucket's start and `buckets_expected` is exact rather than rounded.
    """
    expected = (
        1
        if window is TrafficWindow.FIVE_MINUTES
        else max(1, _WINDOW_SECONDS[window] // bucket_seconds)
    )
    end = newest_start + timedelta(seconds=bucket_seconds)
    return end - timedelta(seconds=expected * bucket_seconds), end, expected


def _traffic_order(sort: str, order: TrafficSortOrder | None) -> TrafficSortOrder:
    """The resolved order: explicit, else descending for counts and ascending
    for text."""
    if order is not None:
        return order
    return TrafficSortOrder.ASC if sort in _TEXT_SORTS else TrafficSortOrder.DESC


def _traffic_reason(family: str, code: str) -> str:
    """A stable reason code in the `*_detail.reason.*` namespace."""
    return f"{family}_traffic_detail.reason.{code}"


def _traffic_verdict(
    family: str,
    *,
    not_collected: bool,
    has_window: bool,
    buckets_measured: int,
    buckets_expected: int,
    partial_buckets: int,
    truncated: bool,
) -> tuple[TrafficState, str | None]:
    """The state and its coded reason, in precedence order.

    Not collected wins: no bucket was written under a current profile, so no
    rate would be meaningful. No measured bucket is INSUFFICIENT_DATA even when
    a window exists. Truncation outranks a merely incomplete window because it
    makes the measured rows themselves lower bounds.
    """
    if not_collected:
        return TrafficState.NOT_COLLECTED, _traffic_reason(family, "not_collected")
    if buckets_measured == 0:
        code = "no_usable_bucket" if has_window else "no_complete_bucket"
        return TrafficState.INSUFFICIENT_DATA, _traffic_reason(family, code)
    if truncated:
        return TrafficState.TRUNCATED, _traffic_reason(family, "truncated")
    if buckets_measured < buckets_expected:
        return TrafficState.PARTIAL, _traffic_reason(family, "buckets_missing")
    if partial_buckets:
        return TrafficState.PARTIAL, _traffic_reason(family, "bucket_partial")
    return TrafficState.OK, None


def _not_collected_waf(profile: WafSourceProfile | None) -> bool:
    """No profile, or one that collection would not run under.

    The contract's rule is an enabled, validated profile: a version that was
    never validated, a status other than VALID, or a mapping changed since its
    last validation all fail closed as NOT_COLLECTED.
    """
    return (
        profile is None
        or not profile.enabled
        or str(profile.validation_status) != WafProfileValidationStatus.VALID.value
        or profile.validated_profile_version != profile.profile_version
    )


def _not_collected_ips(profile: IpsSourceProfile | None) -> bool:
    return (
        profile is None
        or not profile.enabled
        or str(profile.validation_status) != IpsProfileValidationStatus.VALID.value
        or profile.validated_profile_version != profile.profile_version
    )


def _empty_traffic(
    window: TrafficWindow,
    *,
    sort: str,
    order: TrafficSortOrder,
    limit: int,
    offset: int,
    state: TrafficState,
    reason: str | None,
    span: _TrafficSpan | None = None,
) -> TrafficOut:
    """A response with no items: every measurement is None, never zero.

    A span exists when an anchor bucket defined the window; then the range and
    the (item-less) series grid are reported, so the client can say which range
    it looked at. With no span, every window field is None and there are no
    points.
    """
    points, step = (
        _series_shape(window, span.bucket_seconds, span.buckets_expected)
        if span is not None
        else (0, None)
    )
    return TrafficOut(
        window=window,
        range_start=None if span is None else span.start,
        range_end=None if span is None else span.end,
        bucket_seconds=None if span is None else span.bucket_seconds,
        buckets_expected=0 if span is None else span.buckets_expected,
        buckets_measured=0 if span is None else span.buckets_measured,
        state=state,
        reason_code=reason,
        series_start=None if span is None else span.start,
        series_step_seconds=step,
        series_points=points,
        total=0,
        limit=limit,
        offset=offset,
        sort=sort,
        order=order,
    )


class _TrafficSpan(NamedTuple):
    """A window that an anchor bucket defined, with what was measured in it."""

    start: datetime
    end: datetime
    bucket_seconds: int
    buckets_expected: int
    buckets_measured: int
    source_total: int | None
    unattributed: int | None


class _TrafficReading(NamedTuple):
    """The verdict on a source's window, before any ranked row is read.

    `span` is None when no anchor bucket exists (NOT_COLLECTED or no complete
    bucket); it is present even when nothing inside it was measured, so the
    client can name the range it looked at.
    """

    state: TrafficState
    reason: str | None
    span: _TrafficSpan | None


#: Points per window on the series grid (contract Amendment 1).
_SERIES_POINTS: dict[TrafficWindow, int] = {
    TrafficWindow.FIVE_MINUTES: 1,
    TrafficWindow.ONE_HOUR: 12,
    TrafficWindow.ONE_DAY: 48,
    TrafficWindow.ONE_WEEK: 56,
    TrafficWindow.FOUR_WEEKS: 56,
}


def _series_shape(
    window: TrafficWindow, bucket_seconds: int, buckets_expected: int
) -> tuple[int, int]:
    """The shared series grid: (points, step in seconds).

    The grid covers the window exactly and its step is a whole number of source
    buckets, so a point is always "this step's events over the measured buckets
    in this step". For the 300 s buckets of the contract's table this is exactly
    that table -- 1 / 12 / 48 / 56 / 56 points at 300 / 300 / 1800 / 10800 /
    43200 s -- while a source with wider buckets gets a coarser, whole-bucket
    grid instead of steps narrower than its own measurement.
    """
    if buckets_expected <= 0:
        return 0, bucket_seconds
    per_step = max(1, -(-buckets_expected // _SERIES_POINTS[window]))
    return -(-buckets_expected // per_step), per_step * bucket_seconds


def _series_values(
    points: int,
    measured: dict[int, int],
    events: dict[tuple[int, uuid.UUID], int],
    entity_id: uuid.UUID,
    bucket_seconds: int,
) -> list[float | None]:
    """One entity's series: a rate per step, None where no bucket was measured.

    A measured step without a row for this entity is zero events over the step's
    measured seconds, the same zero rule `rate_per_second` applies to the whole
    window. An unmeasured step is a gap, never 0 (rule 8).
    """
    values: list[float | None] = []
    for step in range(points):
        buckets = measured.get(step, 0)
        values.append(
            None
            if buckets == 0
            else events.get((step, entity_id), 0) / (buckets * bucket_seconds)
        )
    return values


async def _waf_traffic_reading(
    repo: AnomalyRepository,
    log_source_id: uuid.UUID,
    profile: WafSourceProfile | None,
    window: TrafficWindow,
) -> _TrafficReading:
    """The WAF window's anchor, measurement and verdict, or why there is none."""
    if _not_collected_waf(profile):
        state, reason = _traffic_verdict(
            "waf",
            not_collected=True,
            has_window=False,
            buckets_measured=0,
            buckets_expected=0,
            partial_buckets=0,
            truncated=False,
        )
        return _TrafficReading(state, reason, None)
    newest = await repo.waf_latest_complete_bucket(log_source_id)
    if newest is None:
        state, reason = _traffic_verdict(
            "waf",
            not_collected=False,
            has_window=False,
            buckets_measured=0,
            buckets_expected=0,
            partial_buckets=0,
            truncated=False,
        )
        return _TrafficReading(state, reason, None)
    start, end, expected = _traffic_bounds(window, newest[0], newest[1])
    measured, partial, truncated, source_total, unattributed = (
        await repo.waf_traffic_bucket_stats(log_source_id, since=start, until=end)
    )
    state, reason = _traffic_verdict(
        "waf",
        not_collected=False,
        has_window=True,
        buckets_measured=measured,
        buckets_expected=expected,
        partial_buckets=partial,
        truncated=truncated,
    )
    span = _TrafficSpan(start, end, newest[1], expected, measured, source_total, unattributed)
    return _TrafficReading(state, reason, span)


async def _ips_traffic_reading(
    repo: IpsBehaviorRepository,
    log_source_id: uuid.UUID,
    profile: IpsSourceProfile | None,
    window: TrafficWindow,
    *,
    min_coverage: float,
) -> _TrafficReading:
    """The IPS window's anchor, measurement and verdict, or why there is none."""
    if _not_collected_ips(profile):
        state, reason = _traffic_verdict(
            "ips",
            not_collected=True,
            has_window=False,
            buckets_measured=0,
            buckets_expected=0,
            partial_buckets=0,
            truncated=False,
        )
        return _TrafficReading(state, reason, None)
    latest = (await repo.latest_buckets([log_source_id])).get(log_source_id)
    if latest is None:
        state, reason = _traffic_verdict(
            "ips",
            not_collected=False,
            has_window=False,
            buckets_measured=0,
            buckets_expected=0,
            partial_buckets=0,
            truncated=False,
        )
        return _TrafficReading(state, reason, None)
    start, end, expected = _traffic_bounds(window, latest.bucket_start, latest.bucket_seconds)
    measured, partial, truncated, source_total, unattributed = (
        await repo.ips_traffic_bucket_stats(
            log_source_id, since=start, until=end, min_coverage=min_coverage
        )
    )
    state, reason = _traffic_verdict(
        "ips",
        not_collected=False,
        has_window=True,
        buckets_measured=measured,
        buckets_expected=expected,
        partial_buckets=partial,
        truncated=truncated,
    )
    span = _TrafficSpan(
        start, end, latest.bucket_seconds, expected, measured, source_total, unattributed
    )
    return _TrafficReading(state, reason, span)


# --------------------------------------------------- traffic CSV (Amendment 2)
#: The column order the contract fixes. Both families share the tail; only the
#: entity columns differ.
_EXPORT_WAF_HEAD = ("rank", "host", "method", "path")
_EXPORT_IPS_HEAD = ("rank", "destination_ip", "display_name")
_EXPORT_TAIL = (
    "avg_events_per_s",
    "peak_events_per_s",
    "peak_bucket_start_utc",
    "events",
    "buckets_with_data",
    "partial_buckets",
    "active",
    "window",
    "range_start_utc",
    "range_end_utc",
    "buckets_measured",
    "buckets_expected",
    "state",
    "source",
    "truncated",
)


def _csv_filename(
    family: str, source_name: str, window: TrafficWindow, end: datetime | None
) -> str:
    """The attachment filename; `no-range` when no bucket defined the range."""
    slug = csv_slug(source_name) or "source"
    stamp = "no-range" if end is None else csv_stamp(end)
    return f"traffic-{family}-{slug}-{window.value}-{stamp}.csv"


def _export_header(family: str) -> str:
    head = _EXPORT_WAF_HEAD if family == "waf" else _EXPORT_IPS_HEAD
    return _csv_row((*head, *_EXPORT_TAIL))


def _export_context(
    *,
    window: TrafficWindow,
    span: _TrafficSpan | None,
    state: TrafficState,
    source_name: str,
) -> tuple[object, ...]:
    """The context columns repeated on every row, so a sorted sheet keeps them."""
    return (
        window.value,
        _csv_time(None if span is None else span.start),
        _csv_time(None if span is None else span.end),
        None if span is None else span.buckets_measured,
        None if span is None else span.buckets_expected,
        state.value,
        source_name,
    )


def _waf_export_body(
    items: Sequence[WafTrafficRow],
    *,
    window: TrafficWindow,
    span: _TrafficSpan | None,
    state: TrafficState,
    source_name: str,
    measured_seconds: int,
    truncated: bool,
) -> str:
    """The whole WAF export: a header row, then one row per ranked endpoint.

    `measured_seconds` is the whole window's measured time, the same denominator
    as the JSON route's `rate_per_second`, so the export and the page agree.
    """
    context = _export_context(window=window, span=span, state=state, source_name=source_name)
    rows = [_export_header("waf")]
    for rank, item in enumerate(items, start=1):
        rows.append(
            _csv_row(
                (
                    rank,
                    item.virtual_host,
                    item.http_method,
                    item.normalized_endpoint,
                    item.events / measured_seconds,
                    item.peak_rate_per_second,
                    _csv_time(item.peak_bucket_start),
                    item.events,
                    item.buckets_with_data,
                    item.partial_buckets,
                    item.active,
                    *context,
                    truncated,
                )
            )
        )
    return "".join(rows)


def _ips_export_body(
    items: Sequence[IpsTrafficRow],
    *,
    window: TrafficWindow,
    span: _TrafficSpan | None,
    state: TrafficState,
    source_name: str,
    measured_seconds: int,
    truncated: bool,
) -> str:
    """The whole IPS export: a header row, then one row per destination IP."""
    context = _export_context(window=window, span=span, state=state, source_name=source_name)
    rows = [_export_header("ips")]
    for rank, item in enumerate(items, start=1):
        rows.append(
            _csv_row(
                (
                    rank,
                    item.destination_ip,
                    item.display_name,
                    item.events / measured_seconds,
                    item.peak_rate_per_second,
                    _csv_time(item.peak_bucket_start),
                    item.events,
                    item.buckets_with_data,
                    item.partial_buckets,
                    item.active,
                    *context,
                    truncated,
                )
            )
        )
    return "".join(rows)


def _csv_response(
    body: str,
    *,
    family: str,
    source_name: str,
    window: TrafficWindow,
    span: _TrafficSpan | None,
    state: TrafficState,
    truncated: bool,
) -> Response:
    """A CSV attachment, naming the range, the state and any truncation."""
    return csv_export_response(
        body,
        filename=_csv_filename(
            family, source_name, window, None if span is None else span.end
        ),
        truncated=truncated,
        extra_headers={"X-Export-State": state.value},
    )



@behavior_router.get(
    "/waf/sources/{log_source_id}/traffic", response_model=TrafficOut
)
async def waf_source_traffic(
    log_source_id: uuid.UUID,
    session: SessionDep,
    window: TrafficWindow = Query(default=TrafficWindow.ONE_HOUR),
    sort: WafTrafficSort = Query(default=WafTrafficSort.RATE),
    order: TrafficSortOrder | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TrafficOut:
    """WAF endpoints ranked by average events per second in the window.

    Every endpoint with a metric row in a measured bucket is ranked; one with no
    row in any measured bucket is not listed, never shown as zero. The rate
    divides by the measured seconds of the window (contract), so the same
    denominator scales every item and the bar maximum. A PARTIAL state says the
    window is incomplete or a measured bucket is a lower bound; TRUNCATED says
    a bucket lost endpoints, so the attribution is a lower bound too.

    Each item also carries its rate on the shared series grid, read for this
    page's endpoints in one extra statement whatever the page size. A step with
    no measured bucket is `null` in the series, never 0.
    """
    repo = AnomalyRepository(session)
    found = await repo.waf_source_and_profile(log_source_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source not found")
    _source, profile = found
    resolved = _traffic_order(sort.value, order)
    reading = await _waf_traffic_reading(repo, log_source_id, profile, window)
    if reading.span is None or reading.span.buckets_measured == 0:
        return _empty_traffic(
            window,
            sort=sort.value,
            order=resolved,
            limit=limit,
            offset=offset,
            state=reading.state,
            reason=reading.reason,
            span=reading.span,
        )
    span = reading.span
    page = await repo.waf_traffic_page(
        log_source_id,
        since=span.start,
        until=span.end,
        sort=sort.value,
        descending=resolved is TrafficSortOrder.DESC,
        limit=limit,
        offset=offset,
    )
    measured_seconds = span.buckets_measured * span.bucket_seconds
    points, step = _series_shape(window, span.bucket_seconds, span.buckets_expected)
    measured_steps, per_entity = await repo.waf_traffic_series(
        log_source_id,
        since=span.start,
        until=span.end,
        step_seconds=step,
        entity_ids=[row.id for row in page.items],
    )
    return TrafficOut(
        window=window,
        range_start=span.start,
        range_end=span.end,
        bucket_seconds=span.bucket_seconds,
        buckets_expected=span.buckets_expected,
        buckets_measured=span.buckets_measured,
        state=reading.state,
        reason_code=reading.reason,
        source_total_events=span.source_total,
        attributed_events=page.attributed_events,
        unattributed_events=span.unattributed,
        max_rate_per_second=(
            None if page.max_events is None else page.max_events / measured_seconds
        ),
        series_start=span.start,
        series_step_seconds=step,
        series_points=points,
        total=page.total,
        limit=limit,
        offset=offset,
        sort=sort.value,
        order=resolved,
        items=[
            TrafficItemOut(
                id=row.id,
                virtual_host=row.virtual_host,
                http_method=row.http_method,
                normalized_endpoint=row.normalized_endpoint,
                events=row.events,
                rate_per_second=row.events / measured_seconds,
                peak_rate_per_second=row.peak_rate_per_second,
                peak_bucket_start=row.peak_bucket_start,
                buckets_with_data=row.buckets_with_data,
                partial_buckets=row.partial_buckets,
                active=row.active,
                series=_series_values(
                    points, measured_steps, per_entity, row.id, span.bucket_seconds
                ),
            )
            for row in page.items
        ],
    )


@behavior_router.get(
    "/waf/sources/{log_source_id}/traffic.csv",
    response_class=Response,
    responses={
        200: {
            "description": (
                "The whole ranked set as RFC 4180 CSV. The header row is always "
                "present; X-Export-State names the state, and X-Export-Truncated "
                "says the export was cut at API_MAX_EXPORT_ROWS."
            ),
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def waf_source_traffic_csv(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    window: TrafficWindow = Query(default=TrafficWindow.ONE_HOUR),
    sort: WafTrafficSort = Query(default=WafTrafficSort.RATE),
    order: TrafficSortOrder | None = Query(default=None),
) -> Response:
    """The whole WAF ranking in sort order as CSV, not just the page.

    Rows stop at `api_max_export_rows`, in sort order, and the response then
    carries `X-Export-Truncated: true` and a `truncated` column of `true`: a cut
    export is never silent. With no measured window the body is the header row
    only and `X-Export-State` names why.
    """
    repo = AnomalyRepository(session)
    found = await repo.waf_source_and_profile(log_source_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "WAF source not found")
    source, profile = found
    resolved = _traffic_order(sort.value, order)
    reading = await _waf_traffic_reading(repo, log_source_id, profile, window)
    rows: list[WafTrafficRow] = []
    truncated = False
    if reading.span is not None and reading.span.buckets_measured:
        page = await repo.waf_traffic_page(
            log_source_id,
            since=reading.span.start,
            until=reading.span.end,
            sort=sort.value,
            descending=resolved is TrafficSortOrder.DESC,
            limit=settings.api_max_export_rows,
            offset=0,
        )
        rows = page.items
        truncated = page.total > len(rows)
    return _csv_response(
        _waf_export_body(
            rows,
            window=window,
            span=reading.span,
            state=reading.state,
            source_name=source.name,
            measured_seconds=(
                reading.span.buckets_measured * reading.span.bucket_seconds
                if reading.span is not None
                else 0
            ),
            truncated=truncated,
        ),
        family="waf",
        source_name=source.name,
        window=window,
        span=reading.span,
        state=reading.state,
        truncated=truncated,
    )


# ------------------------------------------------------------- IPS behavior
#: (dimension, its column prefix on `ips_source_bucket`, whether a coverage was
#: measured for it). Port fan-out counts ports, not events, so it has no ratio.
_DIMENSION_COLUMNS = (
    (IpsDimension.DESTINATION_PORT, "destination_port", True),
    (IpsDimension.DESTINATION_IP, "destination_ip", True),
    (IpsDimension.SIGNATURE, "signature", True),
    (IpsDimension.SOURCE_IP, "source_ip", True),
    (IpsDimension.PORT_FANOUT, "port_fanout", False),
    (IpsDimension.ACTION, "action", True),
)

_ENTITY_DIMENSIONS = (
    IpsDimension.DESTINATION_PORT,
    IpsDimension.DESTINATION_IP,
    IpsDimension.SIGNATURE,
)


def _dimension_coverage(
    bucket: IpsSourceBucket, dimension: IpsDimension
) -> IpsDimensionCoverageOut:
    """One search dimension's measurement as stored on the bucket row."""
    # The column arrives as its persisted string; normalize once so the table
    # lookup below compares enum against enum.
    dimension = IpsDimension(str(dimension))
    prefix, covered = next((p, c) for d, p, c in _DIMENSION_COLUMNS if d is dimension)
    return IpsDimensionCoverageOut(
        dimension=dimension,
        measurement=str(getattr(bucket, f"{prefix}_measurement")),
        coverage_ratio=getattr(bucket, f"{prefix}_coverage_ratio") if covered else None,
        coverage_lower_bound=(
            getattr(bucket, f"{prefix}_coverage_lower_bound") if covered else None
        ),
    )


def _bucket_dimensions(bucket: IpsSourceBucket) -> list[IpsDimensionCoverageOut]:
    return [_dimension_coverage(bucket, d) for d, _p, _c in _DIMENSION_COLUMNS]


def _bucket_admission(bucket: IpsSourceBucket) -> list[IpsAdmissionOut]:
    """The bucket's admission outcome per entity dimension, as stored.

    The JSONB columns are keyed by dimension name; a missing key means that
    dimension had no measurement and stays None, never 0.
    """

    def count(mapping: dict, key: str) -> int | None:
        value = mapping.get(key)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    def flag(value: object) -> bool | None:
        return value if isinstance(value, bool) else None

    return [
        IpsAdmissionOut(
            dimension=dimension,
            admitted=count(bucket.admitted, dimension.value),
            below_admission_threshold=count(
                bucket.below_admission_threshold, dimension.value
            ),
            rejected_over_quota=count(bucket.rejected_over_quota, dimension.value),
            untracked_entities=count(bucket.untracked_entities, dimension.value),
            untracked_events=count(bucket.untracked_events, dimension.value),
            admission_truncated=flag(bucket.admission_truncated.get(dimension.value)),
        )
        for dimension in _ENTITY_DIMENSIONS
    ]


def _ips_source_row(
    profile: IpsSourceProfile,
    source: LogSource,
    bucket: IpsSourceBucket | None,
    counts: dict[str, tuple[int, int]],
    anomaly_summary: tuple[int, Severity] | None,
    may_read_anomalies: bool,
    settings: Settings,
) -> IpsSourceBehaviorOut:
    judged = _ips_profile_is_judged(profile)
    active_anomaly_count, worst_active_severity = _ips_active_anomaly_fields(
        judged=judged,
        may_read_anomalies=may_read_anomalies,
        anomaly_summary=anomaly_summary,
    )
    return IpsSourceBehaviorOut(
        log_source_id=source.id,
        log_source_name=source.name,
        product_label=profile.product_label,
        enabled=profile.enabled,
        profile_version=profile.profile_version,
        validation_status=str(profile.validation_status),
        validated_profile_version=profile.validated_profile_version,
        validated_at=profile.validated_at,
        validation_current=profile.validated_profile_version == profile.profile_version
        and str(profile.validation_status) == IpsProfileValidationStatus.VALID.value,
        active_anomaly_count=active_anomaly_count,
        worst_active_severity=worst_active_severity,
        latest_bucket=(
            IpsLatestBucketOut(
                bucket_start=bucket.bucket_start,
                bucket_seconds=bucket.bucket_seconds,
                total_events=bucket.total_events,
                total_records=bucket.total_records,
                in_maintenance=bucket.in_maintenance,
                origin=origin_of(HistoryFamily.IPS, bucket.origin),
                dimensions=_bucket_dimensions(bucket),
                admission=_bucket_admission(bucket),
            )
            if bucket is not None
            else None
        ),
        entities=[
            IpsEntityPressureOut(
                dimension=dimension,
                active=counts.get(dimension.value, (0, 0))[0],
                total=counts.get(dimension.value, (0, 0))[1],
                active_ceiling=settings.ips_max_active_entities_per_source_dimension,
            )
            for dimension in _ENTITY_DIMENSIONS
        ],
    )


def _ips_profile_is_judged(profile: IpsSourceProfile) -> bool:
    """Whether collection and evaluation may use this exact profile version."""
    return (
        profile.enabled
        and profile.validated_profile_version == profile.profile_version
        and str(profile.validation_status) == IpsProfileValidationStatus.VALID.value
    )


def _ips_active_anomaly_fields(
    *,
    judged: bool,
    may_read_anomalies: bool,
    anomaly_summary: tuple[int, Severity] | None,
) -> tuple[int | None, Severity | None]:
    """Fail closed unless both the source and caller may see a verdict."""
    count = (
        (anomaly_summary[0] if anomaly_summary is not None else 0)
        if may_read_anomalies and judged
        else None
    )
    severity = (
        anomaly_summary[1]
        if count not in (None, 0) and anomaly_summary is not None
        else None
    )
    return count, severity


async def _ips_source_rows(
    repo: IpsBehaviorRepository,
    pairs: list[tuple[IpsSourceProfile, LogSource]],
    settings: Settings,
    *,
    may_read_anomalies: bool,
) -> list[IpsSourceBehaviorOut]:
    """Build list and single-source rows through the same bulk-read path."""
    source_ids = [source.id for _, source in pairs]
    latest = await repo.latest_buckets(source_ids)
    counts = await repo.entity_counts(source_ids)
    active = (
        await repo.source_active_anomalies(source_ids) if may_read_anomalies else {}
    )
    return [
        _ips_source_row(
            profile,
            source,
            latest.get(source.id),
            counts.get(source.id, {}),
            active.get(source.id),
            may_read_anomalies,
            settings,
        )
        for profile, source in pairs
    ]


@behavior_router.get("/ips/sources", response_model=PagedIpsSources)
async def list_ips_sources(
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedIpsSources:
    """Every IPS source's profile status, latest coverage and entity pressure.

    Current state only, so unlike the metrics routes this takes no range: the
    latest bucket is the newest collected, not the newest inside a window, so a
    stale source is reported as stale rather than vanishing behind a filter.
    """
    limit = min(limit, settings.api_max_page_size)
    repo = IpsBehaviorRepository(session)
    pairs, total = await repo.sources_page(limit=limit, offset=offset)
    return PagedIpsSources(
        items=await _ips_source_rows(
            repo,
            pairs,
            settings,
            may_read_anomalies=principal.has(PERM_ANOMALY_READ),
        ),
        total=total,
        limit=limit,
        offset=offset,
    )


@behavior_router.get("/ips/sources/{log_source_id}", response_model=IpsSourceBehaviorOut)
async def get_ips_source(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    principal: PrincipalDep,
) -> IpsSourceBehaviorOut:
    """One enabled IPS source, projected by the list's exact bulk-read path."""
    repo = IpsBehaviorRepository(session)
    pairs, _total = await repo.sources_page(
        limit=1, offset=0, log_source_id=log_source_id
    )
    if not pairs:
        raise CodedHTTPException(
            status.HTTP_404_NOT_FOUND, MessageRef("ips.source.not_found")
        )
    rows = await _ips_source_rows(
        repo,
        pairs,
        settings,
        may_read_anomalies=principal.has(PERM_ANOMALY_READ),
    )
    return rows[0]


@behavior_router.get(
    "/ips/sources/{log_source_id}/entities", response_model=PagedIpsEntities
)
async def list_ips_entities(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    dimension: IpsDimension | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedIpsEntities:
    """The admitted-entity inventory of one IPS source.

    Value keys are the estate's own ports, destination addresses and signature
    QIDs (design §D.4). A dimension narrows to one kind of entity; retired
    entities stay listed so the inventory explains what stopped being seen.
    """
    limit = min(limit, settings.api_max_page_size)
    repo = IpsBehaviorRepository(session)
    if await repo.source(log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS source not found")
    entities, total = await repo.entities_page(
        log_source_id, dimension=dimension, limit=limit, offset=offset
    )
    ids = [e.id for e in entities]
    adequacy = await repo.entity_baseline_adequacy(ids)
    states = await repo.entity_detector_states(ids)
    active = await repo.entity_active_anomaly_counts(ids)
    return PagedIpsEntities(
        items=[
            IpsEntityInventoryOut(
                id=e.id,
                dimension=e.dimension,
                value_key=e.value_key,
                display_name=e.display_name,
                first_seen_at=e.first_seen_at,
                last_seen_at=e.last_seen_at,
                active=e.active,
                retired_at=e.retired_at,
                baseline=IpsBaselineAdequacyOut(
                    cells_total=adequacy.get(e.id, (0, 0))[0],
                    reliable_cells=adequacy.get(e.id, (0, 0))[1],
                ),
                state=_worst_state(states.get(e.id, [])),
                active_anomaly_count=active.get(e.id, 0),
            )
            for e in entities
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@behavior_router.get(
    "/ips/entities/{entity_id}/metrics", response_model=PagedIpsMetrics
)
async def ips_entity_metrics(
    entity_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PagedIpsMetrics:
    """Stored buckets of one entity with the completeness of its dimension.

    Only buckets that have a row appear. A bucket with no row is not a zero:
    whether absence means zero is the detectors' own reasoning, and the UI must
    not reconstruct it. What the source's bucket record would have said about a
    row whose record is missing is None, never a guess.
    """
    start, end = _validate_range(since, until, settings.api_max_range_days)
    limit = min(limit, settings.api_max_page_size)
    entity = await session.get(IpsEntity, entity_id)
    if entity is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS entity not found")
    # The dimension column arrives as its persisted string; one normalization,
    # then every lookup below works on the enum.
    dimension = IpsDimension(str(entity.dimension))
    repo = IpsBehaviorRepository(session)
    rows, total = await repo.entity_metrics_page(
        entity, since=start, until=end, limit=limit, offset=offset
    )
    items: list[IpsEntityMetricBucketOut] = []
    for metric, bucket in rows:
        coverage = (
            _dimension_coverage(bucket, dimension) if bucket is not None else None
        )
        truncated = (
            bucket.admission_truncated.get(dimension.value) if bucket is not None else None
        )
        items.append(
            IpsEntityMetricBucketOut(
                bucket_start=metric.bucket_start,
                bucket_seconds=metric.bucket_seconds,
                events=metric.events,
                # The entity metric row carries no marker of its own: the
                # source's bucket record does (§B.4.3).
                origin=(
                    origin_of(HistoryFamily.IPS, bucket.origin) if bucket is not None else None
                ),
                measurement=coverage.measurement if coverage is not None else None,
                coverage_ratio=coverage.coverage_ratio if coverage is not None else None,
                coverage_lower_bound=(
                    coverage.coverage_lower_bound if coverage is not None else None
                ),
                admission_truncated=(
                    truncated if isinstance(truncated, bool) else None
                ),
                in_maintenance=bucket.in_maintenance if bucket is not None else None,
            )
        )
    return PagedIpsMetrics(
        items=items, total=total, limit=limit, offset=offset, range_start=start, range_end=end
    )


@behavior_router.get(
    "/ips/entities/{entity_id}/baselines", response_model=PagedIpsBaselines
)
async def ips_entity_baselines(
    entity_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    metric: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedIpsBaselines:
    """Seasonal cells of one entity, with reliability and the versions that
    built them.

    Raw training observations are not served; a cell reports its sample count
    and whether it is reliable enough to drive a verdict. Cells are keyed by
    weekday and hour, so there is no range to bound.
    """
    limit = min(limit, settings.api_max_page_size)
    if await session.get(IpsEntity, entity_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS entity not found")
    repo = IpsBehaviorRepository(session)
    rows, total = await repo.entity_baselines_page(
        entity_id, metric=metric, limit=limit, offset=offset
    )
    return PagedIpsBaselines(
        items=[IpsBaselineCellOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@behavior_router.get(
    "/ips/sources/{log_source_id}/metrics", response_model=PagedIpsMetrics
)
async def ips_source_metrics(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    since: datetime | None = Query(default=None),
    until: datetime | None = Query(default=None),
    limit: int = Query(default=500, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> PagedIpsMetrics:
    """The source-level series the IPS_SOURCE detectors judge, per bucket.

    Concentration, fan-out and action facts are None whenever their search did
    not measure them, never 0. The bounded source-IP contributor lists the
    bucket also holds are not served by this route.
    """
    start, end = _validate_range(since, until, settings.api_max_range_days)
    limit = min(limit, settings.api_max_page_size)
    repo = IpsBehaviorRepository(session)
    if await repo.source(log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS source not found")
    rows, total = await repo.source_metrics_page(
        log_source_id, since=start, until=end, limit=limit, offset=offset
    )
    items: list[IpsSourceMetricBucketOut] = [
        IpsSourceMetricBucketOut(
            bucket_start=b.bucket_start,
            bucket_seconds=b.bucket_seconds,
            total_events=b.total_events,
            total_records=b.total_records,
            in_maintenance=b.in_maintenance,
            origin=origin_of(HistoryFamily.IPS, b.origin),
            dimensions=_bucket_dimensions(b),
            source_events=b.source_events,
            distinct_sources=b.distinct_sources,
            distinct_sources_lower_bound=b.distinct_sources_lower_bound,
            top1_events=b.top1_events,
            top5_events=b.top5_events,
            max_distinct_ports=b.max_distinct_ports,
            blocked_events=b.blocked_events,
            allowed_events=b.allowed_events,
            unmapped_events=b.unmapped_events,
        )
        for b in rows
    ]
    return PagedIpsMetrics(
        items=items, total=total, limit=limit, offset=offset, range_start=start, range_end=end
    )


@behavior_router.get(
    "/ips/sources/{log_source_id}/baselines", response_model=PagedIpsBaselines
)
async def ips_source_baselines(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    metric: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PagedIpsBaselines:
    """The source-level seasonal cells (`ips_source_baseline`) behind the
    concentration, fan-out and block-ratio detectors.

    Ratio cells carry the pooled sums their expected value divides; count cells
    leave them None. As with entity cells, there is no range to bound and no
    raw observation is served.
    """
    limit = min(limit, settings.api_max_page_size)
    repo = IpsBehaviorRepository(session)
    if await repo.source(log_source_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS source not found")
    rows, total = await repo.source_baselines_page(
        log_source_id, metric=metric, limit=limit, offset=offset
    )
    return PagedIpsBaselines(
        items=[IpsBaselineCellOut.model_validate(r, from_attributes=True) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@behavior_router.get(
    "/ips/sources/{log_source_id}/traffic", response_model=TrafficOut
)
async def ips_source_traffic(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    window: TrafficWindow = Query(default=TrafficWindow.ONE_HOUR),
    sort: IpsTrafficSort = Query(default=IpsTrafficSort.RATE),
    order: TrafficSortOrder | None = Query(default=None),
    limit: int = Query(default=10, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> TrafficOut:
    """IPS destination IPs ranked by average events per second in the window.

    Covers `DESTINATION_IP` only, as the contract scopes it. A destination IP
    with no metric row in any measured bucket is not listed, never shown as
    zero. The rate divides by the measured seconds of the window, and
    `unattributed_events` is the stored count of events outside admitted
    destination-IP entities over the same buckets.

    Each item also carries its rate on the shared series grid, read for this
    page's entities in one extra statement whatever the page size. A step with
    no measured bucket is `null` in the series, never 0.
    """
    repo = IpsBehaviorRepository(session)
    found = await repo.source_and_profile(log_source_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS source not found")
    _source, profile = found
    resolved = _traffic_order(sort.value, order)
    reading = await _ips_traffic_reading(
        repo, log_source_id, profile, window, min_coverage=settings.ips_min_coverage
    )
    if reading.span is None or reading.span.buckets_measured == 0:
        return _empty_traffic(
            window,
            sort=sort.value,
            order=resolved,
            limit=limit,
            offset=offset,
            state=reading.state,
            reason=reading.reason,
            span=reading.span,
        )
    span = reading.span
    page = await repo.ips_traffic_page(
        log_source_id,
        since=span.start,
        until=span.end,
        min_coverage=settings.ips_min_coverage,
        sort=sort.value,
        descending=resolved is TrafficSortOrder.DESC,
        limit=limit,
        offset=offset,
    )
    measured_seconds = span.buckets_measured * span.bucket_seconds
    points, step = _series_shape(window, span.bucket_seconds, span.buckets_expected)
    measured_steps, per_entity = await repo.ips_traffic_series(
        log_source_id,
        since=span.start,
        until=span.end,
        step_seconds=step,
        entity_ids=[row.id for row in page.items],
    )
    return TrafficOut(
        window=window,
        range_start=span.start,
        range_end=span.end,
        bucket_seconds=span.bucket_seconds,
        buckets_expected=span.buckets_expected,
        buckets_measured=span.buckets_measured,
        state=reading.state,
        reason_code=reading.reason,
        source_total_events=span.source_total,
        attributed_events=page.attributed_events,
        unattributed_events=span.unattributed,
        max_rate_per_second=(
            None if page.max_events is None else page.max_events / measured_seconds
        ),
        series_start=span.start,
        series_step_seconds=step,
        series_points=points,
        total=page.total,
        limit=limit,
        offset=offset,
        sort=sort.value,
        order=resolved,
        items=[
            TrafficItemOut(
                id=row.id,
                destination_ip=row.destination_ip,
                display_name=row.display_name,
                events=row.events,
                rate_per_second=row.events / measured_seconds,
                peak_rate_per_second=row.peak_rate_per_second,
                peak_bucket_start=row.peak_bucket_start,
                buckets_with_data=row.buckets_with_data,
                partial_buckets=row.partial_buckets,
                active=row.active,
                series=_series_values(
                    points, measured_steps, per_entity, row.id, span.bucket_seconds
                ),
            )
            for row in page.items
        ],
    )


@behavior_router.get(
    "/ips/sources/{log_source_id}/traffic.csv",
    response_class=Response,
    responses={
        200: {
            "description": (
                "The whole ranked set as RFC 4180 CSV. The header row is always "
                "present; X-Export-State names the state, and X-Export-Truncated "
                "says the export was cut at API_MAX_EXPORT_ROWS."
            ),
            "content": {"text/csv": {"schema": {"type": "string"}}},
        }
    },
)
async def ips_source_traffic_csv(
    log_source_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    window: TrafficWindow = Query(default=TrafficWindow.ONE_HOUR),
    sort: IpsTrafficSort = Query(default=IpsTrafficSort.RATE),
    order: TrafficSortOrder | None = Query(default=None),
) -> Response:
    """The whole destination-IP ranking in sort order as CSV, not just the page.

    Same rule as the WAF export: rows stop at `api_max_export_rows` and say so,
    and a window with nothing measured returns the header row only with
    `X-Export-State` naming why.
    """
    repo = IpsBehaviorRepository(session)
    found = await repo.source_and_profile(log_source_id)
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "IPS source not found")
    source, profile = found
    resolved = _traffic_order(sort.value, order)
    reading = await _ips_traffic_reading(
        repo, log_source_id, profile, window, min_coverage=settings.ips_min_coverage
    )
    rows: list[IpsTrafficRow] = []
    truncated = False
    if reading.span is not None and reading.span.buckets_measured:
        page = await repo.ips_traffic_page(
            log_source_id,
            since=reading.span.start,
            until=reading.span.end,
            min_coverage=settings.ips_min_coverage,
            sort=sort.value,
            descending=resolved is TrafficSortOrder.DESC,
            limit=settings.api_max_export_rows,
            offset=0,
        )
        rows = page.items
        truncated = page.total > len(rows)
    return _csv_response(
        _ips_export_body(
            rows,
            window=window,
            span=reading.span,
            state=reading.state,
            source_name=source.name,
            measured_seconds=(
                reading.span.buckets_measured * reading.span.bucket_seconds
                if reading.span is not None
                else 0
            ),
            truncated=truncated,
        ),
        family="ips",
        source_name=source.name,
        window=window,
        span=reading.span,
        state=reading.state,
        truncated=truncated,
    )
