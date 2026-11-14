"""The fleet source list: search, filters, sort, facets and the row shape.

Every figure comes from a definition the product already has:

* flow: `SILENT` is the anomaly engine's verdict (an open, unsuppressed NO_EVENTS finding on
  a monitored, enabled source not in maintenance), exactly what the overview counts. A null
  last-event time is `NEVER` (unknown, not silent); `enabled = false` is
  `DISABLED_IN_QRADAR`. An old timestamp alone never makes a source silent.
* expected EPS: the reliable baseline cell of the source's `average_eps` for the current UTC
  weekday and hour, the same cells the fleet summary averages. The band is its p05..p95.
* profiles: the WAF, IPS and identity source-profile rows, one outer join each.

The statement count is constant in the number of sources: one page of ids, one enriched read
of that page, one count, one group-name read and one query per facet family.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import (
    ColumnElement,
    Select,
    and_,
    case,
    exists,
    func,
    literal,
    or_,
    select,
    text,
    true,
)
from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet.summary import EPS_METRIC
from app.models.enums import (
    ACTIVE_ANOMALY_STATES,
    AnomalyType,
    BucketCompleteness,
    ClassificationReviewStatus,
    SourceClass,
)
from app.models.fleet import LogSourceClassification, LogSourceGroup
from app.models.identity_detection import IdentitySourceProfile
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource, LogSourceAnomaly, LogSourceBaseline, LogSourceMetric
from app.models.waf import WafSourceProfile
from app.schemas.fleet import (
    FacetCount,
    FleetSourceFacets,
    FleetSourceRow,
    ProfileState,
    TypeFacetCount,
    fleet_source_out,
)

#: Group names carried on one row; `group_count` says how many there really are.
FLEET_GROUP_NAMES = 5
#: Types in the type facet, by count.
TYPE_FACET_LIMIT = 50
#: How far back "current EPS" looks for a COMPLETE bucket.
CURRENT_EPS_LOOKBACK = timedelta(hours=1)
_INT32_MAX = 2**31 - 1

_UNCLASSIFIED = SourceClass.UNCLASSIFIED.value
_PROFILES = ("WAF", "IPS", "IDENTITY")


@dataclass(frozen=True)
class SourceFilters:
    q: str | None = None
    classes: list[SourceClass] = field(default_factory=list)
    type_ids: list[int] = field(default_factory=list)
    group_ids: list[int] = field(default_factory=list)
    flows: list[str] = field(default_factory=list)
    eps: list[str] = field(default_factory=list)
    profiles: list[str] = field(default_factory=list)
    review_status: ClassificationReviewStatus | None = None
    has_conflict: bool | None = None
    basis: str | None = None
    ids: list[uuid.UUID] | None = None


# --------------------------------------------------------------------------- columns


def _klass() -> ColumnElement[str]:
    return func.coalesce(LogSourceClassification.source_class, literal(_UNCLASSIFIED))


def _flow() -> ColumnElement[str]:
    """FLOWING / SILENT / DISABLED_IN_QRADAR / NEVER. See the module docstring."""
    silent = exists().where(
        LogSourceAnomaly.log_source_id == LogSource.id,
        LogSourceAnomaly.anomaly_type == AnomalyType.NO_EVENTS,
        LogSourceAnomaly.state.in_([state.value for state in ACTIVE_ANOMALY_STATES]),
        LogSourceAnomaly.suppressed.is_(False),
    )
    return case(
        (LogSource.enabled.is_(False), "DISABLED_IN_QRADAR"),
        (LogSource.last_event_time.is_(None), "NEVER"),
        (
            and_(
                LogSource.monitoring_enabled.is_(True),
                LogSource.maintenance_mode.is_(False),
                silent,
            ),
            "SILENT",
        ),
        else_="FLOWING",
    )


class _Eps:
    """The two joins behind current and expected EPS, built once per statement."""

    def __init__(self, now: datetime, bucket_seconds: int) -> None:
        self.latest = (
            select(LogSourceMetric.average_eps.label("eps"))
            .where(
                LogSourceMetric.log_source_id == LogSource.id,
                LogSourceMetric.completeness == BucketCompleteness.COMPLETE.value,
                LogSourceMetric.bucket_seconds == bucket_seconds,
                LogSourceMetric.bucket_start >= now - CURRENT_EPS_LOOKBACK,
                LogSourceMetric.bucket_start <= now,
            )
            .order_by(LogSourceMetric.bucket_start.desc())
            .limit(1)
            .lateral("latest_eps")
        )
        self.weekday = now.isoweekday()
        self.hour = now.hour

    @property
    def current(self) -> ColumnElement[float]:
        return self.latest.c.eps

    @staticmethod
    def has_band() -> ColumnElement[bool]:
        return and_(LogSourceBaseline.p05.is_not(None), LogSourceBaseline.p95.is_not(None))

    def state(self) -> ColumnElement[str]:
        return case(
            (~self.has_band() | LogSourceBaseline.id.is_(None), "NO_BASELINE"),
            (self.current.is_(None), None),
            (self.current < LogSourceBaseline.p05, "BELOW_EXPECTED"),
            (self.current > LogSourceBaseline.p95, "ABOVE_EXPECTED"),
            else_="WITHIN_EXPECTED",
        )


def _join(stmt: Select, eps: _Eps | None, *, profiles: bool) -> Select:
    """The joins every statement shares; the EPS and profile ones only when asked for."""
    stmt = stmt.outerjoin(
        LogSourceClassification, LogSourceClassification.log_source_id == LogSource.id
    )
    if profiles:
        stmt = (
            stmt.outerjoin(WafSourceProfile, WafSourceProfile.log_source_id == LogSource.id)
            .outerjoin(IpsSourceProfile, IpsSourceProfile.log_source_id == LogSource.id)
            .outerjoin(
                IdentitySourceProfile, IdentitySourceProfile.log_source_id == LogSource.id
            )
        )
    if eps is not None:
        stmt = stmt.outerjoin(eps.latest, true()).outerjoin(
            LogSourceBaseline,
            and_(
                LogSourceBaseline.log_source_id == LogSource.id,
                LogSourceBaseline.metric_name == EPS_METRIC,
                LogSourceBaseline.weekday == eps.weekday,
                LogSourceBaseline.hour == eps.hour,
                LogSourceBaseline.is_reliable.is_(True),
            ),
        )
    return stmt


def _no_profile() -> ColumnElement[bool]:
    return and_(
        WafSourceProfile.id.is_(None),
        IpsSourceProfile.id.is_(None),
        IdentitySourceProfile.id.is_(None),
    )


def _like_pattern(needle: str) -> str:
    escaped = needle.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def conditions(
    f: SourceFilters, eps: _Eps | None, *, skip: str | None = None
) -> list[ColumnElement[bool]]:
    """The WHERE terms for `f`, leaving out the family named by `skip` (faceting)."""
    out: list[ColumnElement[bool]] = []
    klass = _klass()
    if f.ids is not None:
        out.append(LogSource.id.in_(f.ids))
    if f.classes and skip != "class":
        out.append(klass.in_([c.value for c in f.classes]))
    if f.type_ids and skip != "type":
        out.append(LogSource.type_id.in_(f.type_ids))
    if f.group_ids:
        out.append(or_(*(LogSource.group_ids.contains([g]) for g in f.group_ids)))
    if f.flows and skip != "flow":
        out.append(_flow().in_(f.flows))
    if f.eps and eps is not None:
        out.append(eps.state().in_(f.eps))
    if f.profiles and skip != "profile":
        terms: list[ColumnElement[bool]] = []
        if "NONE" in f.profiles:
            terms.append(_no_profile())
        if "WAF" in f.profiles:
            terms.append(WafSourceProfile.id.is_not(None))
        if "IPS" in f.profiles:
            terms.append(IpsSourceProfile.id.is_not(None))
        if "IDENTITY" in f.profiles:
            terms.append(IdentitySourceProfile.id.is_not(None))
        out.append(or_(*terms))
    if f.review_status is not None:
        out.append(LogSourceClassification.review_status == f.review_status.value)
    if f.has_conflict is not None:
        out.append(
            func.coalesce(
                func.jsonb_array_length(LogSourceClassification.conflicts) > 0, False
            ).is_(f.has_conflict)
        )
    if f.basis is not None:
        out.append(func.coalesce(LogSourceClassification.basis, literal("UNCLASSIFIED")) == f.basis)
    needle = (f.q or "").strip()
    if needle:
        # Case-insensitive substring of the name, type name or identifier; LIKE wildcards in
        # the input are literal. An all-digit needle also matches the QRadar id exactly.
        pattern = _like_pattern(needle)
        terms = [
            LogSource.name.ilike(pattern, escape="\\"),
            LogSource.type_name.ilike(pattern, escape="\\"),
            LogSource.identifier.ilike(pattern, escape="\\"),
        ]
        if needle.isascii() and needle.isdigit() and int(needle) <= _INT32_MAX:
            terms.append(LogSource.qradar_id == int(needle))
        out.append(or_(*terms))
    return out


def needs_eps(f: SourceFilters, sort: str) -> bool:
    return bool(f.eps) or sort == "eps"


def _order(sort: str, order: str, eps: _Eps | None, now: datetime) -> list[Any]:
    key: Any
    if sort == "type":
        key = func.lower(LogSource.type_name)
    elif sort == "class":
        key = _klass()
    elif sort == "eps" and eps is not None:
        key = eps.current
    elif sort == "last_event":
        key = LogSource.last_event_time
    elif sort == "silent_for":
        # Seconds silent, only for a SILENT source; everything else sorts last.
        key = case(
            (
                _flow() == "SILENT",
                func.extract("epoch", literal(now) - LogSource.last_event_time),
            ),
            else_=None,
        )
    else:
        key = LogSource.name
    primary = key.desc().nulls_last() if order == "desc" else key.asc().nulls_last()
    tie = LogSource.qradar_id.desc() if order == "desc" else LogSource.qradar_id.asc()
    return [primary, tie, LogSource.id]


# ----------------------------------------------------------------------------- reads


async def count_sources(
    session: AsyncSession, f: SourceFilters, *, now: datetime, bucket_seconds: int
) -> int:
    eps = _Eps(now, bucket_seconds) if bool(f.eps) else None
    stmt = _join(select(func.count()).select_from(LogSource), eps, profiles=bool(f.profiles))
    return int(await session.scalar(stmt.where(*conditions(f, eps))) or 0)


async def page_ids(
    session: AsyncSession,
    f: SourceFilters,
    *,
    sort: str,
    order: str,
    limit: int,
    offset: int | None,
    now: datetime,
    bucket_seconds: int,
) -> list[uuid.UUID]:
    eps = _Eps(now, bucket_seconds) if needs_eps(f, sort) else None
    stmt = _join(select(LogSource.id).select_from(LogSource), eps, profiles=bool(f.profiles))
    stmt = stmt.where(*conditions(f, eps)).order_by(*_order(sort, order, eps, now))
    if limit:
        stmt = stmt.limit(limit)
    if offset:
        stmt = stmt.offset(offset)
    return list((await session.scalars(stmt)).all())


def _profile(status: str | None, enabled: bool | None) -> ProfileState | None:
    if status is None or enabled is None:
        return None
    return ProfileState(status=status, enabled=enabled)


async def load_rows(
    session: AsyncSession,
    ids: list[uuid.UUID],
    *,
    now: datetime,
    bucket_seconds: int,
) -> list[FleetSourceRow]:
    """The enriched rows for `ids`, in that order: one read plus one group-name read."""
    eps = _Eps(now, bucket_seconds)
    stmt = _join(
        select(
            LogSource,
            LogSourceClassification,
            _flow().label("flow"),
            eps.current.label("current_eps"),
            LogSourceBaseline.median.label("expected_eps"),
            LogSourceBaseline.p05.label("expected_low"),
            LogSourceBaseline.p95.label("expected_high"),
            eps.state().label("eps_state"),
            WafSourceProfile.validation_status.label("waf_status"),
            WafSourceProfile.enabled.label("waf_enabled"),
            IpsSourceProfile.validation_status.label("ips_status"),
            IpsSourceProfile.enabled.label("ips_enabled"),
            IdentitySourceProfile.validation_status.label("identity_status"),
            IdentitySourceProfile.enabled.label("identity_enabled"),
        ).select_from(LogSource),
        eps,
        profiles=True,
    ).where(LogSource.id.in_(ids))
    fetched = {row[0].id: row for row in (await session.execute(stmt)).all()}

    group_ids: set[int] = set()
    instances: set[uuid.UUID] = set()
    for row in fetched.values():
        group_ids.update(row[0].group_ids or [])
        instances.add(row[0].instance_id)
    names: dict[tuple[uuid.UUID, int], str] = {}
    group_rows = await session.execute(
        select(LogSourceGroup.instance_id, LogSourceGroup.qradar_id, LogSourceGroup.name).where(
            LogSourceGroup.instance_id.in_(instances),
            LogSourceGroup.qradar_id.in_(group_ids),
        )
    )
    for instance_id, qradar_id, name in group_rows.all():
        names[(instance_id, qradar_id)] = name

    items: list[FleetSourceRow] = []
    for sid in ids:
        found = fetched.get(sid)
        if found is None:
            continue
        row = found
        ls, cl = row[0], row[1]
        base = fleet_source_out(ls, cl)
        ids_of = list(ls.group_ids or [])
        group_names = sorted(
            names[(ls.instance_id, g)] for g in ids_of if (ls.instance_id, g) in names
        )
        silent_for = (
            int((now - ls.last_event_time).total_seconds())
            if row.flow == "SILENT" and ls.last_event_time is not None
            else None
        )
        items.append(
            FleetSourceRow(
                **base.model_dump(),
                identifier=ls.identifier,
                type_id=ls.type_id,
                group_names=group_names[:FLEET_GROUP_NAMES],
                group_count=len(ids_of),
                flow=row.flow,
                last_event_at=ls.last_event_time,
                silent_for_seconds=max(0, silent_for) if silent_for is not None else None,
                current_eps=row.current_eps,
                expected_eps=row.expected_eps,
                expected_eps_low=row.expected_low,
                expected_eps_high=row.expected_high,
                eps_state=row.eps_state,
                waf_profile=_profile(row.waf_status, row.waf_enabled),
                ips_profile=_profile(row.ips_status, row.ips_enabled),
                identity_profile=_profile(row.identity_status, row.identity_enabled),
            )
        )
    return items


async def build_facets(
    session: AsyncSession, f: SourceFilters, *, now: datetime, bucket_seconds: int
) -> FleetSourceFacets:
    """Four grouped reads, one per family, each under every other active filter."""
    eps = _Eps(now, bucket_seconds) if f.eps else None

    klass = _klass()
    flow = _flow()
    type_stmt = _join(
        select(LogSource.type_id, LogSource.type_name, func.count().label("n")).select_from(
            LogSource
        ),
        eps,
        profiles=bool(f.profiles),
    ).where(*conditions(f, eps, skip="type"))
    type_rows = (
        await session.execute(
            type_stmt.group_by(LogSource.type_id, LogSource.type_name)
            .order_by(func.count().desc(), LogSource.type_name, LogSource.type_id)
            .limit(TYPE_FACET_LIMIT)
        )
    ).all()
    class_rows = await _group_by(session, f, eps, "class", klass)
    flow_rows = await _group_by(session, f, eps, "flow", flow)
    profile_stmt = _join(
        select(
            func.count().filter(_no_profile()).label("none"),
            func.count().filter(WafSourceProfile.id.is_not(None)).label("waf"),
            func.count().filter(IpsSourceProfile.id.is_not(None)).label("ips"),
            func.count().filter(IdentitySourceProfile.id.is_not(None)).label("identity"),
        ).select_from(LogSource),
        eps,
        profiles=True,
    ).where(*conditions(f, eps, skip="profile"))
    p = (await session.execute(profile_stmt)).one()
    return FleetSourceFacets(
        source_class=[FacetCount(value=v, count=n) for v, n in class_rows],
        type=[TypeFacetCount(type_id=t, name=name, count=n) for t, name, n in type_rows],
        flow=[FacetCount(value=v, count=n) for v, n in flow_rows],
        profile=[
            FacetCount(value="NONE", count=p.none),
            FacetCount(value="WAF", count=p.waf),
            FacetCount(value="IPS", count=p.ips),
            FacetCount(value="IDENTITY", count=p.identity),
        ],
    )


async def _group_by(
    session: AsyncSession,
    f: SourceFilters,
    eps: _Eps | None,
    family: str,
    expr: ColumnElement[str],
) -> list[tuple[str, int]]:
    stmt = _join(
        select(expr.label("v"), func.count().label("n")).select_from(LogSource),
        eps,
        profiles=bool(f.profiles),
    ).where(*conditions(f, eps, skip=family))
    # Grouped by the output alias: a CASE with bind parameters is not groupable by value.
    stmt = stmt.group_by(text("v")).order_by(func.count().desc(), text("v"))
    return [(v, int(n)) for v, n in (await session.execute(stmt)).all()]
