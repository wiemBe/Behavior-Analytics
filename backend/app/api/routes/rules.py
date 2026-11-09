"""Analytics rule and rule-health API.

Read-only against QRadar. The one mutating endpoint here (`PATCH /rules/{id}`)
writes SOC-owned metadata in *this platform's* database — ownership, MITRE
mapping, expected firing rate. Nothing on the update schema corresponds to a
QRadar field, so no request shape can imply enabling, disabling or editing a
rule in the SIEM.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.anomaly.capabilities import RULE_DETECTORS, DetectorStatus, detector_status
from app.anomaly.evidence import AnomalyEvidence
from app.api.deps import SessionDep, SettingsDep
from app.core.config import Settings
from app.i18n.messages import MessageRef
from app.i18n.refusal import permission_refusal
from app.models.enums import AnomalyState, RuleAnomalyType, RuleHealthStatus
from app.models.instance import QRadarInstance
from app.models.rule import AnalyticsRule, RuleCapacitySample, RuleHealthSnapshot
from app.models.rule_anomaly import RuleAnomaly
from app.repositories.audit import record_audit
from app.repositories.rule import SORTABLE, HealthReason, RuleFilters, RuleRepository
from app.rules.engine import RuleMatchEngine, RuleSectionRead
from app.rules.matches import CoverageState, floor_to_window
from app.schemas.messages import MessageParamValue, MessageValue
from app.schemas.rule import (
    RuleDetail,
    RuleHealthSnapshotOut,
    RuleHealthSummaryEntry,
    RulePage,
    RuleStateTransitionOut,
    RuleSummary,
    RuleUpdate,
)
from app.schemas.rule_anomaly import (
    MatchAnomalyBlockState,
    MatchAnomalyRow,
    MatchAnomalyRowState,
    MatchAnomalySpan,
    RuleMatchAnomalies,
    RuleMatchSection,
    RuleMatchSectionCounts,
    RuleMatchSectionIncident,
    RuleMatchSectionState,
)
from app.schemas.rule_statistics import CapacitySampleOut, RuleStatistics
from app.security.auth import get_principal
from app.security.rbac import (
    PERM_RULE_WRITE,
    PermissionDenied,
    Principal,
    require_permission,
)
from app.security.sanitizer import sanitize_mapping, sanitize_text
from app.services.rule_statistics import (
    RuleStatisticsService,
    message_text,
    message_wire,
    window_message,
)

router = APIRouter(prefix="/rules", tags=["rules"])

CAPACITY_HISTORY_LIMIT = 100

PrincipalDep = Annotated[Principal, Depends(get_principal)]


def _guard(principal: Principal, permission: str) -> None:
    try:
        require_permission(principal, permission)
    except PermissionDenied as exc:
        raise permission_refusal(exc) from exc


async def _resolve_instance(
    session: AsyncSession, instance_id: uuid.UUID | None
) -> uuid.UUID | None:
    if instance_id is not None:
        return instance_id
    return await session.scalar(select(QRadarInstance.id).limit(1))


def _coded_reason(
    message: object,
) -> tuple[str | None, dict[str, MessageParamValue] | None]:
    """`(code, params)` of a stored `reason_i18n` value, sanitised and validated.

    The params can carry a QRadar name (a dependency's), so every string leaf goes through
    the same sanitiser as the reason text, the nested references included; the sanitised
    value is then validated into the wire models. Anything malformed fails closed to None:
    a code is never paired with params it cannot declare, and never with another row's text.
    """
    if not isinstance(message, dict) or not isinstance(message.get("code"), str):
        return None, None
    raw = message.get("params")
    if not isinstance(raw, dict):
        return None, None
    try:
        params = MessageValue.model_validate(
            {"code": message["code"], "params": sanitize_mapping(raw)}
        ).params
    except ValidationError:
        return None, None
    return str(message["code"]), params


def _with_reason[T: RuleSummary](view: T, reason: HealthReason | None) -> T:
    """`view` with the reason read for it; the ORM row has no such column.

    `model_copy` skips the schema's validators, so the text is sanitised here, as every
    other string on these schemas is; the coded fields go through `_coded_reason`.
    """
    if reason is None or reason.text is None:
        return view.model_copy(
            update={
                "health_reason": None,
                "health_reason_code": None,
                "health_reason_params": None,
            }
        )
    code, params = _coded_reason(reason.message)
    return view.model_copy(
        update={
            "health_reason": sanitize_text(reason.text),
            "health_reason_code": code,
            "health_reason_params": params,
        }
    )


def _history_row(row: RuleHealthSnapshot) -> RuleHealthSnapshotOut:
    """One history row, with the coded reason beside its text.

    `model_validate` sanitises the schema's strings (`reason` included) but a dict is opaque
    to its validator, so the coded fields go through `_coded_reason` instead of the model's
    plain properties.
    """
    view = RuleHealthSnapshotOut.model_validate(row)
    code, params = _coded_reason(row.reason_i18n)
    return view.model_copy(update={"reason_code": code, "reason_params": params})


async def _detail(repo: RuleRepository, rule: AnalyticsRule) -> RuleDetail:
    reasons = await repo.health_reasons([rule])
    return _with_reason(RuleDetail.model_validate(rule), reasons.get(rule.id))


async def _page(
    session: AsyncSession,
    settings: Settings,
    *,
    filters: RuleFilters,
    limit: int,
    offset: int,
) -> RulePage:
    bounded = max(1, min(limit, settings.api_max_page_size))
    rules, total = await RuleRepository(session).list_rules(
        filters, limit=bounded, offset=offset
    )
    reasons = await RuleRepository(session).health_reasons(rules)
    return RulePage(
        items=[
            _with_reason(RuleSummary.model_validate(r), reasons.get(r.id)) for r in rules
        ],
        total=total,
        limit=bounded,
        offset=offset,
    )


@router.get("", response_model=RulePage)
async def list_rules(
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    enabled: bool | None = Query(default=None),
    health_status: RuleHealthStatus | None = Query(default=None),
    include_building_blocks: bool = Query(default=False),
    origin: str | None = Query(default=None, max_length=64),
    owner: str | None = Query(default=None, max_length=255),
    technique: str | None = Query(default=None, max_length=32),
    search: str | None = Query(default=None, max_length=200),
    sort: str = Query(default="name"),
    descending: bool = Query(default=False),
    instance_id: uuid.UUID | None = Query(default=None),
) -> RulePage:
    if sort not in SORTABLE:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"sort must be one of: {', '.join(sorted(SORTABLE))}",
        )
    filters = RuleFilters(
        instance_id=await _resolve_instance(session, instance_id),
        enabled=enabled,
        health_status=health_status,
        # None means "both"; the repository default excludes building blocks.
        is_building_block=None if include_building_blocks else False,
        origin=origin,
        owner=owner,
        technique=technique,
        search=search,
        sort=sort,
        descending=descending,
    )
    return await _page(session, settings, filters=filters, limit=limit, offset=offset)


@router.get("/health-summary", response_model=list[RuleHealthSummaryEntry])
async def health_summary(
    session: SessionDep,
    instance_id: uuid.UUID | None = Query(default=None),
) -> list[RuleHealthSummaryEntry]:
    resolved = await _resolve_instance(session, instance_id)
    rows = await RuleRepository(session).health_summary(resolved)
    return [RuleHealthSummaryEntry(**r) for r in rows]


@router.get("/statistics", response_model=RuleStatistics)
async def rule_statistics(
    session: SessionDep,
    settings: SettingsDep,
    instance_id: uuid.UUID | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=30),
    top: int = Query(default=10, ge=1, le=50),
) -> RuleStatistics:
    """Rule counts, measured matches, silence and the capacity benchmark.

    Declared before `/{rule_id}` so the literal path wins. Sections that cannot
    be measured come back null with a reason, never as zero.
    """
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no instance registered")
    return await RuleStatisticsService(session, settings).compute(
        resolved, days=days, top=top
    )


#: The incident states a rule-match block lists. A CANDIDATE was never promoted to an incident.
_LISTED_STATES = (AnomalyState.OPEN, AnomalyState.RECOVERING, AnomalyState.RESOLVED)


def _row_state(details: object) -> MatchAnomalyRowState:
    """TRUNCATED when the engine stored that the observed window was truncated."""
    extra = details.get("extra") if isinstance(details, dict) else None
    if isinstance(extra, dict) and extra.get("truncated_window") is True:
        return MatchAnomalyRowState.TRUNCATED
    return MatchAnomalyRowState.OK


@router.get("/match-anomalies", response_model=RuleMatchAnomalies)
async def rule_match_anomalies(
    session: SessionDep,
    settings: SettingsDep,
    instance_id: uuid.UUID | None = Query(default=None),
    days: int = Query(default=7, ge=1, le=30),
    limit: int = Query(default=50, ge=1, le=200),
) -> RuleMatchAnomalies:
    """Rule silence and storm incidents over the statistics span (design §7.1).

    Declared before `/{rule_id}`. A fixed number of statements however many rules or
    incidents exist: the coverage read `/rules/statistics` uses, then one joined select.
    Collection off or never run is NOT_COLLECTED (no list); an incomplete span is
    INSUFFICIENT_DATA (no silence rows, storms marked); only a complete span lists as OK.
    """
    resolved = await _resolve_instance(session, instance_id)
    if resolved is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "no instance registered")
    not_collected = RuleMatchAnomalies(
        instance_id=resolved, state=MatchAnomalyBlockState.NOT_COLLECTED
    )
    if not settings.rule_match_collection_enabled:
        return not_collected

    now = datetime.now(UTC)
    width = settings.rule_match_window_seconds
    span_end = floor_to_window(
        now - timedelta(seconds=settings.rule_match_settle_lag_seconds), width
    )
    span_start = floor_to_window(span_end - timedelta(days=days), width)
    coverage, _history = await RuleStatisticsService(session, settings).coverage(
        resolved, span_start, now
    )
    if coverage.state is CoverageState.NONE:
        return not_collected

    message = window_message(coverage)
    code, params = message_wire(message)
    complete = coverage.state is CoverageState.COMPLETE
    span = MatchAnomalySpan(
        days=days,
        span_start=span_start,
        span_end=span_end,
        window_seconds=width,
        expected_windows=coverage.expected,
        complete_windows=coverage.complete,
        truncated_windows=coverage.truncated,
        failed_windows=coverage.failed,
        missing_windows=coverage.missing,
        complete=complete,
        window_status=coverage.state.value,
        reason=message_text(message),
        reason_code=code,
        reason_params=params,
    )
    conditions = [
        AnalyticsRule.instance_id == resolved,
        RuleAnomaly.state.in_([s.value for s in _LISTED_STATES]),
        # An incident still open stays in view whatever its age; a resolved one must have
        # been detected inside the span.
        or_(
            RuleAnomaly.detected_at >= span_start,
            RuleAnomaly.state != AnomalyState.RESOLVED.value,
        ),
    ]
    if not complete:
        # Over a gappy span an absent count proves nothing, so no silence is listed.
        conditions.append(RuleAnomaly.detector_type != RuleAnomalyType.RULE_SILENCE.value)
    rows = (
        await session.execute(
            select(RuleAnomaly, AnalyticsRule.name, func.count().over())
            .join(AnalyticsRule, AnalyticsRule.id == RuleAnomaly.rule_id)
            .where(*conditions)
            .order_by(RuleAnomaly.detected_at.desc(), RuleAnomaly.id.desc())
            .limit(min(limit, settings.api_max_page_size))
        )
    ).all()
    total = int(rows[0][2]) if rows else 0
    return RuleMatchAnomalies(
        instance_id=resolved,
        state=MatchAnomalyBlockState.OK if complete else MatchAnomalyBlockState.INSUFFICIENT_DATA,
        span=span,
        items=[
            MatchAnomalyRow(
                anomaly_id=incident.id,
                rule_id=incident.rule_id,
                name=name,
                qradar_id=incident.qradar_id,
                is_building_block=incident.is_building_block,
                detector=RuleAnomalyType(incident.detector_type),
                observed=incident.observed_value,
                expected=incident.expected_value,
                state=AnomalyState(incident.state),
                started=incident.anomaly_start or incident.detected_at,
                row_state=_row_state(incident.details),
                window_status=coverage.state.value,
            )
            for incident, name, _total in rows
        ],
        total=total,
        items_truncated=total > len(rows),
        reason=span.reason,
        reason_code=span.reason_code,
        reason_params=span.reason_params,
    )


def _health_view(
    name: str, health: RuleHealthStatus, description: str
) -> Callable[..., Awaitable[RulePage]]:
    """Register one canned rule-health view.

    Inactive/noisy/disabled/degraded are the four lists an operator actually
    works from, so each gets a stable URL rather than requiring the caller to
    know the right filter combination.
    """

    @router.get(f"/{name}", response_model=RulePage, name=f"rules_{name}", summary=description)
    async def _view(
        session: SessionDep,
        settings: SettingsDep,
        limit: int = Query(default=50, ge=1, le=1000),
        offset: int = Query(default=0, ge=0),
        instance_id: uuid.UUID | None = Query(default=None),
    ) -> RulePage:
        filters = RuleFilters(
            instance_id=await _resolve_instance(session, instance_id),
            health_status=health,
            is_building_block=False,
            sort="name",
        )
        return await _page(session, settings, filters=filters, limit=limit, offset=offset)

    return _view


_health_view("inactive", RuleHealthStatus.INACTIVE, "Rules that have stopped firing")
_health_view("noisy", RuleHealthStatus.NOISY, "Rules firing above expectation")
_health_view("disabled", RuleHealthStatus.DISABLED, "Rules disabled in QRadar")
_health_view(
    "dependency-degraded",
    RuleHealthStatus.DEPENDENCY_DEGRADED,
    "Rules whose required telemetry is unavailable",
)
_health_view(
    "never-observed",
    RuleHealthStatus.NEVER_OBSERVED,
    "Enabled rules that have never been seen firing",
)


@router.get("/{rule_id}", response_model=RuleDetail)
async def get_rule(rule_id: uuid.UUID, session: SessionDep) -> RuleDetail:
    rule = await RuleRepository(session).get(rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return await _detail(RuleRepository(session), rule)


# First match among the skips one detector call can actually return. `modified`
# is only reached after the window gate, so a truncated latest window stays
# TRUNCATED. A stale inventory sync is refused before that gate, so it stays
# INSUFFICIENT_DATA rather than TRUNCATED. Both are the engine's own verdict.
_SECTION_FIRST: tuple[tuple[str, RuleMatchSectionState], ...] = (
    ("not_exact", RuleMatchSectionState.NOT_EVENT),
    ("disabled", RuleMatchSectionState.DISABLED),
    ("monitoring_off", RuleMatchSectionState.MONITORING_OFF),
    ("modified", RuleMatchSectionState.MODIFIED),
    ("window_truncated", RuleMatchSectionState.TRUNCATED),
)
# Silence calls a positive count healthy. Storm can still refuse that window.
_STORM_ONLY_SKIPS = frozenset({"cell_thin", "cell_missing", "width_does_not_tile"})
_LIVE_INCIDENTS = frozenset({AnomalyState.OPEN, AnomalyState.RECOVERING})


def _collection_on(settings: Settings) -> bool:
    """Both rule detectors operational. One that is not fails the section closed."""
    return all(
        detector_status(spec, settings) is DetectorStatus.OPERATIONAL
        for spec in RULE_DETECTORS.values()
    )


def _skip_code(evidence: AnomalyEvidence[RuleAnomalyType] | None) -> str | None:
    if evidence is None or not isinstance(evidence.extra, dict):
        return None
    code = evidence.extra.get("skip")
    return code if isinstance(code, str) else None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _parsed_time(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _anomaly_state(value: object) -> AnomalyState:
    if isinstance(value, AnomalyState):
        return value
    return AnomalyState(str(value))


def _detector(value: object) -> RuleAnomalyType:
    if isinstance(value, RuleAnomalyType):
        return value
    return RuleAnomalyType(str(value))


def _incident_ratio(row: RuleAnomaly) -> float | None:
    if row.deviation_ratio is not None:
        return float(row.deviation_ratio)
    details = row.details if isinstance(row.details, dict) else {}
    extra = details.get("extra")
    if not isinstance(extra, dict):
        return None
    for key in ("deviation_ratio", "ratio", "ratio_to_baseline"):
        number = _as_float(extra.get(key))
        if number is not None:
            return number
    return None


def _section_incident(row: RuleAnomaly) -> RuleMatchSectionIncident:
    details = row.details if isinstance(row.details, dict) else {}
    state = _anomaly_state(row.state)
    ended = None
    if state not in _LIVE_INCIDENTS:
        ended = row.resolved_at or row.anomaly_end
    return RuleMatchSectionIncident(
        anomaly_id=row.id,
        detector=_detector(row.detector_type),
        observed=row.observed_value,
        expected=row.expected_value,
        baseline_low=_as_float(details.get("baseline_low")),
        baseline_high=_as_float(details.get("baseline_high")),
        consecutive_buckets=row.consecutive_buckets,
        ratio=_incident_ratio(row),
        started=row.anomaly_start or row.detected_at,
        ended=ended,
        state=state,
    )


def _has_open(read: RuleSectionRead) -> bool:
    return any(_anomaly_state(row.state) in _LIVE_INCIDENTS for row in read.incidents)


def _choose(
    silence: AnomalyEvidence[RuleAnomalyType],
    storm: AnomalyEvidence[RuleAnomalyType],
) -> tuple[RuleMatchSectionState, AnomalyEvidence[RuleAnomalyType] | None]:
    for code, state in _SECTION_FIRST:
        if _skip_code(silence) == code:
            return state, silence
        if _skip_code(storm) == code:
            return state, storm
    storm_skip = _skip_code(storm)
    storm_only = storm_skip in _STORM_ONLY_SKIPS and not silence.insufficient_data
    if storm.insufficient_data and storm_only:
        return RuleMatchSectionState.INSUFFICIENT_DATA, storm
    if silence.insufficient_data:
        return RuleMatchSectionState.INSUFFICIENT_DATA, silence
    if storm.insufficient_data:
        return RuleMatchSectionState.INSUFFICIENT_DATA, storm
    return RuleMatchSectionState.OK, None


def _window_params(evidence: AnomalyEvidence[RuleAnomalyType]) -> dict[str, datetime]:
    return {"window_start": evidence.interval_start, "window_end": evidence.interval_end}


def _tally_params(evidence: AnomalyEvidence[RuleAnomalyType]) -> dict[str, int] | None:
    extra = evidence.extra if isinstance(evidence.extra, dict) else {}
    tally = extra.get("baseline_tally")
    if not isinstance(tally, dict):
        return None
    keys = ("expected", "complete", "missing", "truncated", "failed", "wrong_width")
    numbers: dict[str, int] = {}
    for key in keys:
        number = _as_int(tally.get(key))
        if number is None:
            return None
        numbers[key] = number
    return numbers


def _explain(
    skip: str | None,
    evidence: AnomalyEvidence[RuleAnomalyType],
    read: RuleSectionRead,
    width: int,
) -> tuple[MessageRef | None, RuleMatchSectionCounts | None]:
    """The catalog sentence for this detector skip, plus the counts that sentence names."""
    extra = evidence.extra if isinstance(evidence.extra, dict) else {}
    thresholds = read.thresholds
    windows = _window_params(evidence)
    days = thresholds.silence_baseline_days
    if skip == "not_exact":
        word = extra.get("match_evidence")
        if not isinstance(word, str):
            return None, None
        return MessageRef("rule.match_anomaly.not_event", {"evidence": word}), None
    if skip == "disabled":
        if read.watermark is None:
            return MessageRef("rule.match_anomaly.disabled_no_sync"), None
        return MessageRef("rule.match_anomaly.disabled", {"synced_at": read.watermark}), None
    if skip == "monitoring_off":
        return MessageRef("rule.match_anomaly.monitoring_off"), None
    if skip == "modified":
        modified = _parsed_time(extra.get("modified_at"))
        if modified is None:
            return None, None
        return MessageRef("rule.match_anomaly.modified", {"modified_at": modified}), None
    if skip == "window_truncated":
        return MessageRef("rule.match_anomaly.truncated"), None
    if skip == "baseline_incomplete":
        tally = _tally_params(evidence)
        if tally is None:
            return None, None
        counts = RuleMatchSectionCounts(
            expected_windows=tally["expected"],
            complete_windows=tally["complete"],
            missing_windows=tally["missing"],
            truncated_windows=tally["truncated"],
            failed_windows=tally["failed"],
            wrong_width_windows=tally["wrong_width"],
            baseline_days=days,
        )
        named = {key: value for key, value in tally.items() if key != "complete"}
        return MessageRef(
            "rule.match_anomaly.span_incomplete",
            {"days": days, **named, **windows},
        ), counts
    if skip == "rule_too_new":
        return (
            MessageRef("rule.match_anomaly.rule_too_new", {"days": days, **windows}),
            RuleMatchSectionCounts(baseline_days=days),
        )
    if skip == "stale_inventory":
        end = evidence.interval_end
        if read.watermark is None:
            return (
                MessageRef("rule.match_anomaly.stale_inventory_unknown", {"window_end": end}),
                None,
            )
        return MessageRef(
            "rule.match_anomaly.stale_inventory",
            {"synced_at": read.watermark, "window_end": end},
        ), None
    if skip == "presence_below_gate":
        presence = _as_float(extra.get("nonzero_fraction"))
        if presence is None:
            return None, None
        minimum = thresholds.silence_min_presence
        return MessageRef(
            "rule.match_anomaly.presence_below",
            {"presence": presence, "minimum": minimum, "days": days, **windows},
        ), RuleMatchSectionCounts(
            presence=presence,
            presence_minimum=minimum,
            baseline_days=days,
        )
    if skip == "median_below_floor":
        median = _as_float(extra.get("median"))
        if median is None:
            return None, None
        minimum = thresholds.silence_min_median
        return MessageRef(
            "rule.match_anomaly.median_below",
            {"median": median, "minimum": minimum, **windows},
        ), RuleMatchSectionCounts(median=median, median_minimum=minimum)
    if skip == "cell_thin":
        visits = _as_int(extra.get("visits"))
        if visits is None:
            return None, None
        samples = evidence.sample_count
        minimum_samples = thresholds.storm_min_samples
        minimum_weeks = thresholds.storm_min_visits
        return MessageRef(
            "rule.match_anomaly.cell_learning",
            {
                "samples": samples,
                "weeks": visits,
                "minimum_samples": minimum_samples,
                "minimum_weeks": minimum_weeks,
                **windows,
            },
        ), RuleMatchSectionCounts(
            sample_count=samples,
            visits=visits,
            minimum_samples=minimum_samples,
            minimum_visits=minimum_weeks,
        )
    if skip == "cell_missing":
        return MessageRef("rule.match_anomaly.cell_missing", windows), None
    if skip == "state_transition":
        return MessageRef("rule.match_anomaly.state_transition", windows), None
    if skip == "dependency_disabled":
        ids = ", ".join(str(item) for item in read.subject.disabled_dependencies)
        return MessageRef("rule.match_anomaly.dependency_disabled", {"ids": ids}), None
    if skip == "dependency_unobserved":
        ids = ", ".join(str(item) for item in read.subject.unobserved_dependencies)
        return MessageRef("rule.match_anomaly.dependency_unobserved", {"ids": ids}), None
    if skip == "window_missing":
        return MessageRef("rule.match_anomaly.window_missing", windows), None
    if skip == "window_failed":
        return MessageRef("rule.match_anomaly.window_failed", windows), None
    if skip == "window_wrong_width":
        return MessageRef(
            "rule.match_anomaly.window_wrong_width", {"width": width, **windows}
        ), None
    if skip == "not_settled":
        return MessageRef("rule.match_anomaly.not_settled", windows), None
    if skip == "width_does_not_tile":
        return MessageRef(
            "rule.match_anomaly.width_does_not_tile", {"width": width, **windows}
        ), None
    return None, None


def _wired(
    message: MessageRef | None,
) -> tuple[str | None, str | None, dict[str, MessageParamValue] | None]:
    if message is None:
        return None, None, None
    code, params = message_wire(message)
    return message_text(message), code, params or None


def _not_collected(read: RuleSectionRead, code: str) -> RuleMatchSection:
    reason, reason_code, params = _wired(MessageRef(code))
    return RuleMatchSection(
        rule_id=read.rule_id,
        state=RuleMatchSectionState.NOT_COLLECTED,
        reason=reason,
        reason_code=reason_code,
        reason_params=params,
        judged_through=read.judged_through,
        incidents=[_section_incident(row) for row in read.incidents],
    )


def _match_section(read: RuleSectionRead, settings: Settings) -> RuleMatchSection:
    if not _collection_on(settings):
        return _not_collected(read, "rule.match_anomaly.collection_off")
    if not read.any_window:
        return _not_collected(read, "rule.match_anomaly.no_window")
    silence, storm = read.silence, read.storm
    if silence is None or storm is None:
        return RuleMatchSection(
            rule_id=read.rule_id,
            state=RuleMatchSectionState.INSUFFICIENT_DATA,
            judged_through=read.judged_through,
            incidents=[_section_incident(row) for row in read.incidents],
        )
    state, evidence = _choose(silence, storm)
    message: MessageRef | None
    counts: RuleMatchSectionCounts | None = None
    if state is RuleMatchSectionState.OK:
        if read.judged_through is None:
            message = None
        else:
            code = (
                "rule.match_anomaly.judged"
                if _has_open(read)
                else "rule.match_anomaly.judged_clear"
            )
            message = MessageRef(code, {"judged_through": read.judged_through})
        evidence = None
    elif evidence is None:
        message = None
    else:
        message, counts = _explain(
            _skip_code(evidence), evidence, read, settings.rule_match_window_seconds
        )
    if message is None and evidence is not None:
        reason = sanitize_text(evidence.reason)
        reason_code = _skip_code(evidence)
        params = None
    else:
        reason, reason_code, params = _wired(message)
    match_evidence = None
    synced_at = None
    modified_at = None
    if evidence is not None and isinstance(evidence.extra, dict):
        if state is RuleMatchSectionState.NOT_EVENT:
            word = evidence.extra.get("match_evidence")
            match_evidence = word if isinstance(word, str) else None
        if state is RuleMatchSectionState.MODIFIED:
            modified_at = _parsed_time(evidence.extra.get("modified_at"))
    if state is RuleMatchSectionState.DISABLED or (
        evidence is not None and _skip_code(evidence) == "stale_inventory"
    ):
        synced_at = read.watermark
    window_start = evidence.interval_start if evidence is not None else read.judged_start
    window_end = evidence.interval_end if evidence is not None else read.judged_through
    through = read.judged_through
    return RuleMatchSection(
        rule_id=read.rule_id,
        state=state,
        reason=reason,
        reason_code=reason_code,
        reason_params=params,
        match_evidence=match_evidence,
        inventory_synced_at=synced_at,
        modified_at=modified_at,
        judged_through=through,
        window_start=window_start,
        window_end=window_end,
        counts=counts if state is RuleMatchSectionState.INSUFFICIENT_DATA else None,
        incidents=[_section_incident(row) for row in read.incidents],
    )


@router.get("/{rule_id}/match-anomalies", response_model=RuleMatchSection)
async def rule_match_section(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
) -> RuleMatchSection:
    """Whether this rule's match rate is being judged, and any open change (design §7.3).

    The state is the first detector skip that applies. Incidents are listed either
    way: an open silence is held when the latest window cannot be judged.
    """
    read = await RuleMatchEngine(session, settings=settings).read_rule(rule_id)
    if read is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    return _match_section(read, settings)


@router.get("/{rule_id}/health-history", response_model=list[RuleHealthSnapshotOut])
async def rule_health_history(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=100, ge=1, le=1000),
    days: int = Query(default=90, ge=1),
) -> list[RuleHealthSnapshotOut]:
    repo = RuleRepository(session)
    if await repo.get(rule_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    bounded_days = min(days, settings.api_max_range_days)
    since = datetime.now(UTC) - timedelta(days=bounded_days)
    rows = await repo.health_history(
        rule_id, limit=max(1, min(limit, settings.api_max_page_size)), since=since
    )
    return [_history_row(r) for r in rows]


@router.get("/{rule_id}/capacity-history", response_model=list[CapacitySampleOut])
async def rule_capacity_history(
    rule_id: uuid.UUID, session: SessionDep
) -> list[CapacitySampleOut]:
    """QRadar's capacity benchmarks for the rule, newest first (at most 100).

    A history of benchmarks, one per `capacity_timestamp`; not a firing rate.
    """
    if await RuleRepository(session).get(rule_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    rows = await session.scalars(
        select(RuleCapacitySample)
        .where(RuleCapacitySample.rule_id == rule_id)
        .order_by(RuleCapacitySample.capacity_timestamp.desc())
        .limit(CAPACITY_HISTORY_LIMIT)
    )
    return [CapacitySampleOut.model_validate(r) for r in rows.all()]


@router.get("/{rule_id}/transitions", response_model=list[RuleStateTransitionOut])
async def rule_transitions(
    rule_id: uuid.UUID,
    session: SessionDep,
    settings: SettingsDep,
    limit: int = Query(default=50, ge=1, le=500),
) -> list[RuleStateTransitionOut]:
    repo = RuleRepository(session)
    if await repo.get(rule_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")
    rows = await repo.transitions(rule_id, limit=max(1, min(limit, settings.api_max_page_size)))
    return [RuleStateTransitionOut.model_validate(r) for r in rows]


@router.patch("/{rule_id}", response_model=RuleDetail)
async def update_rule_metadata(
    rule_id: uuid.UUID,
    payload: RuleUpdate,
    session: SessionDep,
    principal: PrincipalDep,
) -> RuleDetail:
    """Update SOC-owned metadata. QRadar is never written to.

    Requires rule:write, checked before the lookup so a forbidden caller cannot
    learn whether the rule exists. The change is audited as {field: {from, to}}.
    """
    _guard(principal, PERM_RULE_WRITE)
    repo = RuleRepository(session)
    rule = await repo.get(rule_id)
    if rule is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "rule not found")

    changes: dict[str, dict[str, object]] = {}
    for field, value in payload.model_dump(exclude_unset=True).items():
        changes[field] = {"from": getattr(rule, field, None), "to": value}
        setattr(rule, field, value)
    await session.flush()
    await record_audit(
        session, action="rule.update", actor=principal.subject,
        object_type="rule", object_id=str(rule_id), detail={"changes": changes},
    )
    return await _detail(repo, rule)
