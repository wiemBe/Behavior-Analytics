"""Build the pivot block of an anomaly, an alert, an offense or a log source (SOC-2).

Reads stored rows only and returns text (`app.pivot.aql`). It issues no Ariel search and
never calls a provider: the AQL it builds is for the analyst to run in the console.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.alert import Alert
from app.models.enums import IpsDimension, IpsLogicalField, PwScopeKind
from app.models.instance import QRadarInstance
from app.models.ips import IpsAnomaly, IpsEntity, IpsSourceProfile
from app.models.log_source import LogSource, LogSourceAnomaly
from app.models.offense import OffenseSnapshot
from app.models.property_watch import PropertyWatch, PwAnomaly
from app.models.rule_anomaly import RuleAnomaly
from app.models.waf import WafAnomaly, WafEndpoint, WafSourceProfile
from app.pivot import aql as q
from app.property_watch.facts import split_tuple_key
from app.property_watch.reference import PwFieldReference, PwReferenceError
from app.schemas.pivot import PivotOut, PivotWindowOut


class PivotNotFound(LookupError):
    """The object does not exist, or has no QRadar pivot."""


def _out(
    kind: str,
    text: str,
    window: q.PivotWindow,
    base: q.ConsoleBase,
    notes: list[str],
    *,
    primary_url: Callable[[str], str] | None = None,
) -> PivotOut:
    search = q.ariel_search_url(base.url, text) if base.url else None
    primary = (primary_url(base.url) if primary_url else search) if base.url else None
    if base.reason:
        notes = [*notes, base.reason]
    return PivotOut(
        kind=kind,
        aql=text,
        console_url=primary,
        aql_search_url=search,
        console_unavailable_reason=base.reason,
        time_range=PivotWindowOut(start=window.start, stop=window.stop),
        notes=notes,
    )


async def _instance_base(session: AsyncSession, instance_id: uuid.UUID) -> q.ConsoleBase:
    instance = await session.get(QRadarInstance, instance_id)
    if instance is None:
        return q.ConsoleBase(None, "The instance is not registered.")
    return q.console_base(instance.console_host, instance.provider_kind)


def _filter(
    notes: list[str], label: str, build: Callable[[], str]
) -> str | None:
    """One condition, or None with a note when a value cannot be placed in AQL."""
    try:
        return build()
    except q.PivotRefusal as exc:
        notes.append(f"{label} filter omitted: {exc}.")
        return None


# ------------------------------------------------------------------------------ log source
async def log_source_pivot(
    session: AsyncSession, log_source_id: uuid.UUID, *, now: datetime | None = None
) -> PivotOut:
    """The last hour of the source's events."""
    source = await session.get(LogSource, log_source_id)
    if source is None:
        raise PivotNotFound("log source not found")
    now = now or datetime.now(UTC)
    window, notes = q.make_window(now - timedelta(hours=1), now, now=now)
    text = q.events_grid_aql(conditions=[f"logsourceid = {int(source.qradar_id)}"], window=window)
    base = await _instance_base(session, source.instance_id)
    return _out("log_source", text, window, base, notes)


# ------------------------------------------------------------------------------ anomalies
def _anomaly_window(
    row: LogSourceAnomaly | WafAnomaly | IpsAnomaly | PwAnomaly, now: datetime
) -> tuple[q.PivotWindow, list[str]]:
    start = row.anomaly_start or row.opened_at or row.detected_at
    stop = row.anomaly_end or row.resolved_at
    window, notes = q.make_window(start, stop, now=now)
    if stop is None:
        notes.append("The anomaly is still open; the window ends now, when this page was built.")
    return window, notes


async def anomaly_pivot(
    session: AsyncSession, anomaly_id: uuid.UUID, *, now: datetime | None = None
) -> PivotOut:
    now = now or datetime.now(UTC)
    phase_a = await session.get(LogSourceAnomaly, anomaly_id)
    if phase_a is not None:
        return await _phase_a(session, phase_a, now)
    waf = await session.get(WafAnomaly, anomaly_id)
    if waf is not None:
        return await _waf(session, waf, now)
    ips = await session.get(IpsAnomaly, anomaly_id)
    if ips is not None:
        return await _ips(session, ips, now)
    pw = await session.get(PwAnomaly, anomaly_id)
    if pw is not None:
        return await _pw(session, pw, now)
    if await session.get(RuleAnomaly, anomaly_id) is not None:
        # A rule match is counted per rule, not searched as events on one source.
        raise PivotNotFound("a rule-match incident has no AQL pivot")
    raise PivotNotFound("anomaly not found")


async def _phase_a(session: AsyncSession, row: LogSourceAnomaly, now: datetime) -> PivotOut:
    source = await session.get(LogSource, row.log_source_id)
    if source is None:
        raise PivotNotFound("log source not found")
    window, notes = _anomaly_window(row, now)
    text = q.events_by_name_aql(
        conditions=[f"logsourceid = {int(source.qradar_id)}"], window=window
    )
    if str(row.anomaly_type) in {"NO_EVENTS", "VOLUME_DROP"}:
        notes.append(
            "A silent or dropped source has few or no events in this window; compare it "
            "with an earlier window of the same length."
        )
    return _out("anomaly", text, window, await _instance_base(session, source.instance_id), notes)


async def _waf(session: AsyncSession, row: WafAnomaly, now: datetime) -> PivotOut:
    endpoint = await session.get(WafEndpoint, row.waf_endpoint_id)
    if endpoint is None:
        raise PivotNotFound("WAF endpoint not found")
    source = await session.get(LogSource, endpoint.log_source_id)
    if source is None:
        raise PivotNotFound("log source not found")
    window, notes = _anomaly_window(row, now)
    conditions = [f"logsourceid = {int(source.qradar_id)}"]
    profile = await _scalar(
        session, WafSourceProfile, WafSourceProfile.log_source_id == source.id
    )
    group_expr: str | None = None
    if profile is None:
        notes.append("Endpoint filter omitted: the source has no WAF profile mapping the URL.")
    else:
        url_expr = _filter(notes, "URL property", lambda: q.property_expr(profile.url_property))
        method_expr = _filter(
            notes, "Method property", lambda: q.property_expr(profile.method_property)
        )
        if method_expr is not None:
            method = _filter(notes, "Method", lambda: q.literal(endpoint.http_method))
            if method is not None:
                conditions.append(f"{method_expr} = {method}")
        if endpoint.virtual_host:
            if profile.virtual_host_property:
                host_expr = _filter(
                    notes, "Virtual host property",
                    lambda: q.property_expr(profile.virtual_host_property or ""),
                )
                host = _filter(
                    notes, "Virtual host", lambda: q.literal(endpoint.virtual_host or "")
                )
                if host_expr is not None and host is not None:
                    conditions.append(f"{host_expr} = {host}")
            else:
                notes.append("Virtual host filter omitted: the profile maps no host property.")
        if url_expr is not None:
            path = endpoint.normalized_endpoint
            prefix = path.split("{", 1)[0]
            if "%" in prefix:
                notes.append("Path filter omitted: the path holds a % character.")
            elif prefix in ("", "/"):
                notes.append(
                    "Path filter omitted: the endpoint starts with a templated segment, so "
                    "no literal prefix remains."
                )
            else:
                pattern = _filter(notes, "Path", lambda: q.literal(f"{prefix}%"))
                if pattern is not None:
                    conditions.append(f"{url_expr} LIKE {pattern}")
                    if "{" in path:
                        notes.append(
                            f"The endpoint {path} has templated segments; the filter uses "
                            "its literal prefix only."
                        )
                    else:
                        notes.append(
                            "The path filter also matches longer paths and query strings "
                            "that start with it."
                        )
                    group_expr = url_expr
    if group_expr is not None:
        text = q.grouped_aql(
            select_expr=group_expr, alias="url", conditions=conditions, window=window
        )
    else:
        text = q.events_by_name_aql(conditions=conditions, window=window)
    return _out("anomaly", text, window, await _instance_base(session, source.instance_id), notes)


async def _ips(session: AsyncSession, row: IpsAnomaly, now: datetime) -> PivotOut:
    entity = await session.get(IpsEntity, row.ips_entity_id) if row.ips_entity_id else None
    source_id = entity.log_source_id if entity is not None else row.log_source_id
    source = await session.get(LogSource, source_id) if source_id else None
    if source is None:
        raise PivotNotFound("log source not found")
    window, notes = _anomaly_window(row, now)
    conditions = [f"logsourceid = {int(source.qradar_id)}"]
    profile = await _scalar(session, IpsSourceProfile, IpsSourceProfile.log_source_id == source.id)
    mapping = (profile.mapping or {}) if profile is not None else {}

    def reference(field: IpsLogicalField) -> tuple[str, str] | None:
        raw = mapping.get(field.value)
        if isinstance(raw, dict) and isinstance(raw.get("name"), str):
            return str(raw.get("kind")), raw["name"]
        return None

    def expr_of(field: IpsLogicalField) -> str | None:
        ref = reference(field)
        if ref is None:
            return None
        return _filter(notes, field.value, lambda: q.property_expr(ref[1]))

    if entity is None:
        notes.append("This is a source-level anomaly; no entity filter applies.")
    else:
        dimension = IpsDimension(entity.dimension)
        field = IpsLogicalField(dimension.value)
        ref = reference(field)
        expr = expr_of(field)
        if ref is None or expr is None:
            if ref is None:
                notes.append(
                    f"Entity filter omitted: the profile maps no {dimension.value.lower()} field."
                )
        else:
            value_key = entity.value_key

            def condition() -> str:
                if dimension is IpsDimension.DESTINATION_IP:
                    return f"{expr} = {q.ip_literal(value_key)}"
                if dimension is IpsDimension.DESTINATION_PORT or (
                    ref[0] == "NORMALIZED" and ref[1] == "qid"
                ):
                    return f"{expr} = {q.int_literal(value_key)}"
                return f"{expr} = {q.literal(value_key)}"

            built = _filter(notes, "Entity", condition)
            if built is not None:
                conditions.append(built)
    source_ip = expr_of(IpsLogicalField.SOURCE_IP)
    if source_ip is None:
        source_ip = '"sourceip"'
        notes.append("The profile maps no source address; the standard sourceip field is used.")
    text = q.grouped_aql(
        select_expr=source_ip, alias="source_ip", conditions=conditions, window=window
    )
    return _out("anomaly", text, window, await _instance_base(session, source.instance_id), notes)


async def _pw(session: AsyncSession, row: PwAnomaly, now: datetime) -> PivotOut:
    watch = await session.get(PropertyWatch, row.watch_id)
    if watch is None:
        raise PivotNotFound("property watch not found")
    window, notes = _anomaly_window(row, now)
    conditions: list[str] = []
    group_extra: str | None = None
    source: LogSource | None = None
    scope = PwScopeKind(str(watch.scope_kind))
    if scope is PwScopeKind.LOG_SOURCE and watch.log_source_id is not None:
        source = await session.get(LogSource, watch.log_source_id)
        if source is not None:
            conditions.append(f"logsourceid = {int(source.qradar_id)}")
    elif scope is PwScopeKind.LOG_SOURCE_TYPE and watch.scope_type_id is not None:
        conditions.append(f"devicetype = {int(watch.scope_type_id)}")
        group_extra = "logsourceid"
    else:
        notes.append("Source filter omitted: the watch covers a log source group.")
        group_extra = "logsourceid"
    components = split_tuple_key(row.value_key)
    properties = list(watch.properties or [])
    if len(components) != len(properties):
        notes.append(
            "Entity filter omitted: the stored value does not match the watch's properties."
        )
    else:
        for item, component in zip(properties, components, strict=True):
            try:
                name = PwFieldReference.from_dict(item).name
            except PwReferenceError:
                notes.append("Entity filter omitted: a watched property reference is not valid.")
                continue
            expr = _filter(notes, "Property", lambda name=name: q.property_expr(name))  # type: ignore[misc]
            value = _filter(notes, "Value", lambda component=component: q.literal(component))  # type: ignore[misc]
            if expr is not None and value is not None:
                conditions.append(f"{expr} = {value}")
    if not conditions:
        notes.append("No filter could be built, so the query covers every event in the window.")
    text = q.events_by_name_aql(conditions=conditions, window=window, group_extra=group_extra)
    instance_id = source.instance_id if source is not None else watch.instance_id
    return _out("anomaly", text, window, await _instance_base(session, instance_id), notes)


async def _scalar(session, model, *clauses):  # type: ignore[no-untyped-def]
    from sqlalchemy import select

    return await session.scalar(select(model).where(*clauses).limit(1))


# ------------------------------------------------------------------------------ alerts
async def alert_pivot(
    session: AsyncSession, alert_id: uuid.UUID, *, now: datetime | None = None
) -> PivotOut:
    """An alert pivots through its anomaly; an inventory alert through its log source."""
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise PivotNotFound("alert not found")
    for anomaly_id in alert.source_anomaly_ids or []:
        try:
            return await anomaly_pivot(session, uuid.UUID(str(anomaly_id)), now=now)
        except (PivotNotFound, ValueError):
            continue
    if alert.source_type == "log_source" and alert.source_id is not None:
        try:
            return await log_source_pivot(session, alert.source_id, now=now)
        except PivotNotFound:
            pass
    raise PivotNotFound("this alert has no QRadar pivot")


# ------------------------------------------------------------------------------ offenses
async def offense_pivot(
    session: AsyncSession,
    snapshot: OffenseSnapshot,
    *,
    now: datetime | None = None,
) -> PivotOut:
    now = now or datetime.now(UTC)
    notes: list[str] = []
    if snapshot.start_time is None:
        begin = now - timedelta(hours=24)
        notes.append("The offense has no start time; the last 24 hours are searched.")
    else:
        begin = snapshot.start_time
    end = snapshot.close_time or snapshot.last_updated_time
    window, window_notes = q.make_window(begin, end, now=now)
    text = q.inoffense_aql(int(snapshot.qradar_offense_id), window)
    base = await _instance_base(session, snapshot.instance_id)
    offense_id = int(snapshot.qradar_offense_id)
    return _out(
        "offense", text, window, base, [*notes, *window_notes],
        primary_url=lambda origin: q.offense_summary_url(origin, offense_id),
    )
