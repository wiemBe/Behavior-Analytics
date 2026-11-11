"""The report summary (SOC-8b, design `SOC-8.design.md`): four blocks from stored rows.

Nothing here calls QRadar. Every block is one SQL statement whatever the row count (an
aggregate with `percentile_cont`, never a query per row or per family table). A block the
caller may not read is `FORBIDDEN` naming its permission, and a block whose statement fails is
`INSUFFICIENT_DATA` with a reason code: neither hides the other blocks.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import (
    Float,
    Select,
    and_,
    cast,
    extract,
    func,
    literal,
    not_,
    null,
    or_,
    select,
    union_all,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.alerts.query import FAMILY_EXPRESSION
from app.core.config import Settings
from app.i18n.messages import MessageRef
from app.models.alert import Alert
from app.models.enums import (
    AnomalyFamily,
    AnomalyState,
    TriageDisposition,
    TriageEventKind,
    TriageStatus,
)
from app.models.ips import IpsAnomaly
from app.models.log_source import LogSourceAnomaly
from app.models.property_watch import PwAnomaly
from app.models.rule_anomaly import RuleAnomaly
from app.models.triage import AnomalyTriage, AnomalyTriageEvent
from app.models.waf import WafAnomaly
from app.noise.read_model import MIN_NOISE_SHARE_DENOMINATOR
from app.schemas.reports import (
    AlertCell,
    AlertsBlock,
    AnomaliesBlock,
    AnomalyCell,
    AnomalyClocksBlock,
    BlockState,
    Clock,
    FamilyCoverage,
    FigureState,
    NoiseBlock,
    NoiseRow,
    ReportPeriod,
    ReportSummaryOut,
)
from app.security.rbac import PERM_ALERT_READ, PERM_ANOMALY_READ, Principal
from app.services.stale_triage import SYSTEM_ACTOR

logger = logging.getLogger(__name__)

#: Design §1: fewer rows than this is no median and no ratio. The same floor as the noise
#: share (`app/noise/read_model.py:75`), so the two pages agree on what "too few" means.
MIN_FIGURE_N = MIN_NOISE_SHARE_DENOMINATOR
#: Design §2/§3: the longest period the page offers; inside every anomaly retention default.
MAX_PERIOD_DAYS = 31

REASON_BLOCK_FAILED = "reports.reason.block_failed"

#: Design §1: a resolver that is one of the application's own engines did not judge the alert,
#: so its resolve is not an analyst's time to resolve. Each name is the literal the producer
#: passes as `actor` (the design's list, plus `rule-engine`, which the design omitted).
ENGINE_ACTORS: tuple[str, ...] = (
    "anomaly-engine",  # app/anomaly/engine.py:1069
    "waf-anomaly-engine",  # app/waf/engine.py:1093
    "ips-anomaly-engine",  # app/ips/engine.py:937
    "pw-engine",  # app/property_watch/engine.py:161
    "custom-rule-evaluator",  # app/custom_rules/alerts.py:43
    "search-scheduler",  # app/alerts/search_alerts.py:89
    "rule-engine",  # app/rules/engine.py:85
)
#: `system:anomaly-engine` (app/anomaly/engine.py:608) and the monitors in app/workers/tasks.py
#: use this prefix. `_` and `%` are not in it, so the LIKE needs no escaping.
SYSTEM_ACTOR_PREFIX = "system:"

#: Design §2: the retention each family's closed rows are pruned at, as the setting's name.
RETENTION_SETTING: dict[AnomalyFamily, str] = {
    AnomalyFamily.LOG_SOURCE: "log_source_anomaly_retention_days",  # config.py:610
    AnomalyFamily.IPS: "ips_anomaly_retention_days",  # config.py:341
    AnomalyFamily.RULE: "rule_anomaly_retention_days",  # config.py:348
    AnomalyFamily.WAF: "waf_anomaly_retention_days",  # config.py:755
    AnomalyFamily.PROPERTY_WATCH: "pw_anomaly_retention_days",  # config.py:1005
}

#: Design §1: judged = closed with one of these (not STALE, not null).
JUDGED_DISPOSITIONS: tuple[str, ...] = tuple(
    d.value for d in TriageDisposition if d is not TriageDisposition.STALE
)


class PeriodError(ValueError):
    """A period request the route refuses (422); `ref` is the coded sentence."""

    def __init__(self, ref: MessageRef) -> None:
        super().__init__(ref.code)
        self.ref = ref


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _next_month(day: datetime) -> datetime:
    return (
        day.replace(year=day.year + 1, month=1)
        if day.month == 12
        else day.replace(month=day.month + 1)
    )


def resolve_period(
    period: str, start: datetime | None, now: datetime
) -> tuple[ReportPeriod, datetime, datetime]:
    """The half-open UTC range `[start, end)` of a preset (design §3).

    `start` is required for the `calendar_*` periods and must be their boundary, a Monday
    00:00 UTC or the 1st 00:00 UTC; it is ignored for `last_*`. A range over `MAX_PERIOD_DAYS`
    is refused, and so is a `start` after `now`. The returned `end` is clamped to `now`.
    """
    try:
        kind = ReportPeriod(period)
    except ValueError:
        raise PeriodError(MessageRef("reports.period_unknown")) from None
    now = _aware(now)
    if kind is ReportPeriod.LAST_7D:
        begin, end = now - timedelta(days=7), now
    elif kind is ReportPeriod.LAST_30D:
        begin, end = now - timedelta(days=30), now
    else:
        if start is None:
            raise PeriodError(MessageRef("reports.start_required", {"period": kind.value}))
        begin = _aware(start)
        midnight = begin.time() == begin.time().min
        if kind is ReportPeriod.CALENDAR_WEEK:
            if not midnight or begin.weekday() != 0:
                raise PeriodError(MessageRef("reports.start_not_boundary"))
            end = begin + timedelta(days=7)
        else:
            if not midnight or begin.day != 1:
                raise PeriodError(MessageRef("reports.start_not_boundary"))
            end = _next_month(begin)
    check_range(begin, end)
    if begin > now:
        raise PeriodError(MessageRef("reports.start_in_future"))
    # Time that has not happened is not a measured zero: the window ends at `now`.
    return kind, begin, min(end, now)


def check_range(begin: datetime, end: datetime) -> None:
    if end - begin > timedelta(days=MAX_PERIOD_DAYS):
        raise PeriodError(MessageRef("reports.range_too_long", {"max": MAX_PERIOD_DAYS}))


def coverage_for(
    settings: Settings, start: datetime, generated_at: datetime
) -> list[FamilyCoverage]:
    """Per family: `PARTIAL` with `covered_from` when `start` is older than its cutoff."""
    out: list[FamilyCoverage] = []
    for family in AnomalyFamily:
        days = int(getattr(settings, RETENTION_SETTING[family]))
        cutoff = generated_at - timedelta(days=days)
        partial = start < cutoff
        out.append(
            FamilyCoverage(
                family=family,
                state=FigureState.PARTIAL if partial else FigureState.OK,
                retention_days=days,
                covered_from=cutoff if partial else None,
            )
        )
    return out


def _any_partial(coverage: list[FamilyCoverage]) -> bool:
    return any(c.state is FigureState.PARTIAL for c in coverage)


def _clock(median: float | None, n: int | None, *, partial: bool) -> Clock:
    count = int(n or 0)
    if count < MIN_FIGURE_N or median is None:
        return Clock(median_seconds=None, n=count, state=FigureState.INSUFFICIENT_DATA)
    return Clock(
        median_seconds=float(median),
        n=count,
        state=FigureState.PARTIAL if partial else FigureState.OK,
    )


def _median(expr: Any) -> Any:
    """`percentile_cont(0.5)` over a seconds expression (ariel_search_log.py:1264-1280)."""
    return func.percentile_cont(0.5).within_group(expr)


# --- alerts ------------------------------------------------------------------------------


def _engine_resolver() -> Any:
    return or_(
        Alert.resolved_by.like(f"{SYSTEM_ACTOR_PREFIX}%"),
        Alert.resolved_by.in_(ENGINE_ACTORS),
    )


async def alerts_block(
    session: AsyncSession, start: datetime, end: datetime
) -> AlertsBlock:
    """One statement: the family x severity cells and the two clocks as a UNION ALL.

    MTTA is `acknowledged_at - opened_at` over acknowledged alerts. MTTR is
    `resolved_at - opened_at` over resolved alerts whose `resolved_by` is set and is not an
    engine (a NULL resolver cannot be shown to be an analyst, so it is left out).
    """
    window = and_(Alert.opened_at >= start, Alert.opened_at < end)
    ack = Alert.acknowledged_at.is_not(None)
    human_resolve = and_(
        Alert.resolved_at.is_not(None),
        Alert.resolved_by.is_not(None),
        not_(_engine_resolver()),
    )
    ack_secs = extract("epoch", Alert.acknowledged_at - Alert.opened_at)
    res_secs = extract("epoch", Alert.resolved_at - Alert.opened_at)
    nulls = [cast(null(), Float) for _ in range(2)]
    cells = (
        select(
            literal("cell").label("kind"),
            FAMILY_EXPRESSION.label("family"),
            Alert.severity.label("severity"),
            func.count().label("n"),
            *[x.label(name) for x, name in zip(nulls, ("mtta", "mttr"), strict=True)],
            cast(null(), Float).label("mtta_n"),
            cast(null(), Float).label("mttr_n"),
        )
        .where(window)
        .group_by(FAMILY_EXPRESSION, Alert.severity)
    )
    clocks = select(
        literal("clock"),
        cast(null(), Alert.source_type.type),
        cast(null(), Alert.severity.type),
        func.count(),
        _median(ack_secs).filter(ack),
        _median(res_secs).filter(human_resolve),
        func.count().filter(ack),
        func.count().filter(human_resolve),
    ).where(window)
    result = await session.execute(union_all(cells, clocks))
    rows: list[AlertCell] = []
    mtta: Clock | None = None
    mttr: Clock | None = None
    for kind, family, severity, n, p_ack, p_res, n_ack, n_res in result.all():
        if kind == "cell":
            rows.append(AlertCell(family=family, severity=severity, count=int(n)))
        else:
            mtta = _clock(p_ack, n_ack, partial=False)
            mttr = _clock(p_res, n_res, partial=False)
    rows.sort(key=lambda r: (r.family is None, r.family or "", r.severity))
    return AlertsBlock(state=BlockState.OK, rows=rows, mtta=mtta, mttr=mttr)


# --- anomalies ---------------------------------------------------------------------------

_FAMILY_TABLES: tuple[tuple[AnomalyFamily, Any, Any], ...] = (
    (AnomalyFamily.LOG_SOURCE, LogSourceAnomaly, LogSourceAnomaly.anomaly_type),
    (AnomalyFamily.WAF, WafAnomaly, WafAnomaly.detector_type),
    (AnomalyFamily.IPS, IpsAnomaly, IpsAnomaly.detector_type),
    (AnomalyFamily.PROPERTY_WATCH, PwAnomaly, PwAnomaly.detector_type),
    (AnomalyFamily.RULE, RuleAnomaly, RuleAnomaly.detector_type),
)


def _anomaly_union(start: datetime, end: datetime) -> Any:
    """The five family tables as one subquery (design §1/§4).

    Membership is `COALESCE(opened_at, detected_at)` in `[start, end)`
    (`app/models/log_source.py:351-354`); `opened_at` is exposed as it is, so a clock can
    leave out rows that were never promoted.
    """
    branches: list[Select[Any]] = []
    for family, model, detector in _FAMILY_TABLES:
        branches.append(
            select(
                literal(family.value).label("family"),
                model.id.label("id"),
                detector.label("anomaly_type"),
                model.severity.label("severity"),
                model.state.label("state"),
                model.opened_at.label("opened_at"),
            ).where(
                func.coalesce(model.opened_at, model.detected_at) >= start,
                func.coalesce(model.opened_at, model.detected_at) < end,
                # A CANDIDATE was never promoted to an incident: not opened, not counted.
                model.state != AnomalyState.CANDIDATE.value,
            )
        )
    return union_all(*branches).subquery("an")


async def anomalies_block(
    session: AsyncSession, settings: Settings, start: datetime, end: datetime, now: datetime
) -> AnomaliesBlock:
    union = _anomaly_union(start, end)
    result = await session.execute(
        select(union.c.family, union.c.severity, func.count())
        .group_by(union.c.family, union.c.severity)
        .order_by(union.c.family, union.c.severity)
    )
    rows = [
        AnomalyCell(family=AnomalyFamily(family), severity=severity, count=int(n))
        for family, severity, n in result.all()
    ]
    coverage = coverage_for(settings, start, now)
    return AnomaliesBlock(
        state=BlockState.PARTIAL if _any_partial(coverage) else BlockState.OK,
        rows=rows,
        coverage=coverage,
    )


async def clocks_block(
    session: AsyncSession, settings: Settings, start: datetime, end: datetime, now: datetime
) -> AnomalyClocksBlock:
    """One statement: the earliest qualifying triage events per incident, then the medians.

    Start is `opened_at` (null and CANDIDATE are left out). MTTA ends at the earliest OWNER
    event with a `to_value`, or STATUS event to anything but NEW. MTTR ends at the earliest
    STATUS event to CLOSED. Both skip the `system` actor (`stale_triage.py:29`), which is
    what writes the STALE close, so STALE and neglect are never a response time.
    """
    union = _anomaly_union(start, end)
    ev = AnomalyTriageEvent
    human = ev.actor != SYSTEM_ACTOR
    ack_event = and_(
        human,
        or_(
            and_(ev.kind == TriageEventKind.OWNER.value, ev.to_value.is_not(None)),
            and_(
                ev.kind == TriageEventKind.STATUS.value,
                ev.to_value != TriageStatus.NEW.value,
            ),
        ),
    )
    close_event = and_(
        human,
        ev.kind == TriageEventKind.STATUS.value,
        ev.to_value == TriageStatus.CLOSED.value,
    )
    per = (
        select(
            union.c.id.label("id"),
            union.c.opened_at.label("opened_at"),
            func.min(ev.created_at).filter(ack_event).label("ack_at"),
            func.min(ev.created_at).filter(close_event).label("close_at"),
        )
        .select_from(union)
        .join(
            AnomalyTriage,
            and_(AnomalyTriage.family == union.c.family, AnomalyTriage.anomaly_id == union.c.id),
        )
        .join(ev, ev.triage_id == AnomalyTriage.id)
        .where(
            union.c.opened_at.is_not(None),
            union.c.state != AnomalyState.CANDIDATE.value,
        )
        .group_by(union.c.id, union.c.opened_at)
        .subquery("per")
    )
    # An event before the incident was opened cannot be a response to it.
    ack_ok = and_(per.c.ack_at.is_not(None), per.c.ack_at >= per.c.opened_at)
    close_ok = and_(per.c.close_at.is_not(None), per.c.close_at >= per.c.opened_at)
    row = (
        await session.execute(
            select(
                _median(extract("epoch", per.c.ack_at - per.c.opened_at)).filter(ack_ok),
                func.count().filter(ack_ok),
                _median(extract("epoch", per.c.close_at - per.c.opened_at)).filter(close_ok),
                func.count().filter(close_ok),
            )
        )
    ).one()
    coverage = coverage_for(settings, start, now)
    partial = _any_partial(coverage)
    return AnomalyClocksBlock(
        state=BlockState.PARTIAL if partial else BlockState.OK,
        mtta=_clock(row[0], row[1], partial=partial),
        mttr=_clock(row[2], row[3], partial=partial),
        coverage=coverage,
    )


async def noise_block(
    session: AsyncSession, settings: Settings, start: datetime, end: datetime, now: datetime
) -> NoiseBlock:
    """One statement: per detector, judged vs NOISE closes (design §1)."""
    union = _anomaly_union(start, end)
    closed = AnomalyTriage.status == TriageStatus.CLOSED.value
    judged = and_(closed, AnomalyTriage.disposition.in_(JUDGED_DISPOSITIONS))
    noise = and_(closed, AnomalyTriage.disposition == TriageDisposition.NOISE.value)
    result = await session.execute(
        select(
            union.c.family,
            union.c.anomaly_type,
            func.count().filter(judged),
            func.count().filter(noise),
        )
        .select_from(union)
        .outerjoin(
            AnomalyTriage,
            and_(AnomalyTriage.family == union.c.family, AnomalyTriage.anomaly_id == union.c.id),
        )
        .group_by(union.c.family, union.c.anomaly_type)
        .order_by(union.c.family, union.c.anomaly_type)
    )
    coverage = coverage_for(settings, start, now)
    partial_by_family = {c.family: c.state is FigureState.PARTIAL for c in coverage}
    rows: list[NoiseRow] = []
    for family_value, anomaly_type, n_judged, n_noise in result.all():
        family = AnomalyFamily(family_value)
        judged_n, noise_n = int(n_judged), int(n_noise)
        if judged_n < MIN_FIGURE_N:
            rows.append(
                NoiseRow(
                    family=family,
                    anomaly_type=anomaly_type,
                    judged=judged_n,
                    noise=noise_n,
                    ratio=None,
                    state=FigureState.INSUFFICIENT_DATA,
                )
            )
            continue
        rows.append(
            NoiseRow(
                family=family,
                anomaly_type=anomaly_type,
                judged=judged_n,
                noise=noise_n,
                ratio=noise_n / judged_n,
                state=FigureState.PARTIAL if partial_by_family[family] else FigureState.OK,
            )
        )
    return NoiseBlock(
        state=BlockState.PARTIAL if _any_partial(coverage) else BlockState.OK,
        rows=rows,
        coverage=coverage,
    )


# --- the report --------------------------------------------------------------------------


async def build_summary(
    session: AsyncSession,
    settings: Settings,
    principal: Principal,
    *,
    period: ReportPeriod,
    start: datetime,
    end: datetime,
    now: datetime,
) -> ReportSummaryOut:
    """The four blocks. A block is read only when the caller holds its permission.

    A failed statement rolls the (read-only) transaction back and leaves that block
    `INSUFFICIENT_DATA` with `REASON_BLOCK_FAILED`: a missing figure is never a zero.
    """

    async def guarded(permission: str, build: Any, failed: Any, forbidden: Any) -> Any:
        if not principal.has(permission):
            return forbidden(state=BlockState.FORBIDDEN, permission=permission)
        try:
            return await build()
        except Exception:
            logger.exception("report block failed")
            await session.rollback()
            return failed(state=BlockState.INSUFFICIENT_DATA, reason_code=REASON_BLOCK_FAILED)

    alerts = await guarded(
        PERM_ALERT_READ,
        lambda: alerts_block(session, start, end),
        AlertsBlock,
        AlertsBlock,
    )
    anomalies = await guarded(
        PERM_ANOMALY_READ,
        lambda: anomalies_block(session, settings, start, end, now),
        AnomaliesBlock,
        AnomaliesBlock,
    )
    clocks = await guarded(
        PERM_ANOMALY_READ,
        lambda: clocks_block(session, settings, start, end, now),
        AnomalyClocksBlock,
        AnomalyClocksBlock,
    )
    noise = await guarded(
        PERM_ANOMALY_READ,
        lambda: noise_block(session, settings, start, end, now),
        NoiseBlock,
        NoiseBlock,
    )
    return ReportSummaryOut(
        period=period,
        start=start,
        end=end,
        generated_at=now,
        alerts=alerts,
        anomalies=anomalies,
        anomaly_clocks=clocks,
        noise_by_detector=noise,
    )
