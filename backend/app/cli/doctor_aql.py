"""`doctor --aql`: send each collection family's real query once and say whether QRadar takes it.

One check per family, id `aql_<family>`. Each check calls the provider method the collector
calls (the AQL text is built there and nowhere else), on every enabled REST console, one family
and one console at a time, through the Ariel permit pool. The window is the latest complete
5-minute bucket and the row cap is the smallest the method allows. The result is discarded: only
the status, a row count and the elapsed time are kept, so no raw value can reach the output.

A family sends one search for each distinct provider method the collector calls per cycle (the
WAF volume search plus each breakdown the profile's `breakdown_plan` collects; one IPS search per
planned dimension). Every step is tried even when an earlier one failed, so one bad property
does not hide the others; the line names the step that failed and the properties it uses.

Verdicts (docs/OPERATIONS.md, OPS-R72):

* PASS: the search completed (a truncated result is still a PASS and says so).
* FAIL: QRadar refused the search request (400, 404, 409, 422 and any other 4xx) or the token
  (401/403), a property the query names is absent from the console, or the query could not be
  built from the configuration. QRadar's reply is never printed: the line carries our fixed
  sentence, the HTTP status, the family's query version and the property names from our own
  settings.
* WARN: a timeout, an unavailable console, rate limiting (429), a 5xx, a search QRadar created
  and then errored, no free permit. These prove nothing about the query.
* SKIP: the family is off, nothing is configured for it, or no REST console is registered.
  Never a PASS on missing input (rule 8).

Out of scope on purpose: ANOMALY_EXPLANATION, PREVIEW, SCHEDULED_SEARCH and MANUAL_RUN. They run
on demand or are saved by a user and validated at save.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.collectors.geo_collector import _PROPERTY_SETTING, GeoCollector
from app.collectors.qradar_health_collector import QRadarHealthCollector
from app.core.config import Settings
from app.geo.fold import QUERY_VERSION as GEO_LOOKUP_VERSION
from app.geo.fold import QUERY_VERSION_PROPERTY as GEO_PROPERTY_VERSION
from app.geo.fold import GeoDirection
from app.health_detail import spec as health_spec
from app.i18n.messages import MessageRef, render
from app.ips.collection_plan import plan_searches
from app.ips.profile import IpsFieldMapping, IpsProfileError
from app.models.enums import PwScopeKind, WafBreakdown
from app.models.instance import QRadarInstance
from app.models.ips import IpsSourceProfile
from app.models.log_source import LogSource
from app.models.waf import WafSourceProfile
from app.property_watch.reference import validate_properties
from app.property_watch.scope import resolve_scope
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderAuthError,
    ProviderError,
    ProviderQueryRejectedError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import PwSearchKind
from app.providers.qradar_rest import (
    CONSOLE_EVENT_TOTALS_QUERY_VERSION,
    RULE_MATCH_WINDOWED_QUERY_VERSION,
)
from app.repositories.property_watch import PropertyWatchRepository
from app.services import ariel_search_log as ariel_log
from app.services.ariel_permit import ArielPermitUnavailable
from app.waf.profile import WafFieldMapping, WafProfileError, breakdown_plan

PASS = "PASS"  # noqa: S105 - diagnostic verdict, not a credential
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"

RUNBOOK = "OPS-R72"
#: The window and the bucket every family asks about: one whole 5-minute bucket.
BUCKET_SECONDS = 300
#: The smallest row cap any family's method accepts.
ROW_CAP = 1

#: Every family checked, in the order the checks print.
FAMILIES: tuple[str, ...] = (
    "log_source_volume",
    "rule_match",
    "history",
    "waf_collection",
    "ips_collection",
    "property_watch",
    "catchall_senders",
    "geo_collection",
    "qradar_health",
)
CHECK_IDS: tuple[str, ...] = tuple(f"aql_{family}" for family in FAMILIES)

#: The off switch of each family that has one (the same names `Doctor` maps).
_SWITCHES: dict[str, str] = {
    "rule_match": "rule_match_collection_enabled",
    "waf_collection": "waf_collection_enabled",
    "ips_collection": "ips_collection_enabled",
    "property_watch": "pw_collection_enabled",
    "catchall_senders": "catchall_collection_enabled",
    "geo_collection": "geo_collection_enabled",
    "qradar_health": "qradar_health_collection_enabled",
}

UNVERSIONED = "this family's query has no version number"
NO_PROPERTIES = "none (the query names no custom property)"


@dataclass(frozen=True)
class StepResult:
    """What one search keeps: a row count and whether the cap cut it. Never a row."""

    rows: int
    truncated: bool


@dataclass(frozen=True)
class Step:
    """One search: the provider call, a label for the line, and the property names it uses."""

    run: Callable[[], Awaitable[StepResult]]
    label: str = ""
    properties: str = NO_PROPERTIES


@dataclass(frozen=True)
class Plan:
    """What one family sends on one console, and what to say about it on a FAIL.

    `names` are the property names from our own settings or mapping that the line may print
    whole: the output layer would mask a long one without a space as a secret-looking token.
    """

    steps: tuple[Step, ...]
    entity: str = ""
    query_version: str = UNVERSIONED
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class Early:
    """A verdict reached without sending a search."""

    verdict: str
    message: str
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class AqlLine:
    family: str
    verdict: str
    message: str
    #: The property names the message may print whole (see `Plan.names`).
    names: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Context:
    settings: Settings
    sessionmaker: async_sessionmaker[AsyncSession]
    instance: QRadarInstance
    provider: QRadarProvider
    start: datetime
    end: datetime
    now: datetime


Prepare = Callable[[_Context], Awaitable[Plan | Early]]


def window_for(now: datetime) -> tuple[datetime, datetime]:
    """The latest complete 5-minute bucket, aligned to the epoch."""
    seconds = int(now.timestamp()) // BUCKET_SECONDS * BUCKET_SECONDS
    end = datetime.fromtimestamp(seconds, tz=now.tzinfo)
    return end - timedelta(seconds=BUCKET_SECONDS), end


# ------------------------------------------------------------------ preparations
async def _prepare_log_source_volume(ctx: _Context) -> Plan | Early:
    async def step() -> StepResult:
        batch = await ctx.provider.get_log_source_metrics_bucketed(
            ctx.start, ctx.end, bucket_seconds=BUCKET_SECONDS, max_rows=ROW_CAP
        )
        return StepResult(batch.rows_returned, batch.truncated)

    return Plan((Step(step),))


async def _prepare_rule_match(ctx: _Context) -> Plan | Early:
    async def step() -> StepResult:
        result = await ctx.provider.get_rule_matches_windowed(
            ctx.start, ctx.end, window_seconds=BUCKET_SECONDS, max_rows=ROW_CAP
        )
        rows = sum(len(batch.rows) for batch in result.batches.values())
        return StepResult(rows, result.truncated)

    return Plan((Step(step),), query_version=RULE_MATCH_WINDOWED_QUERY_VERSION)


async def _prepare_history(ctx: _Context) -> Plan | Early:
    async def step() -> StepResult:
        result = await ctx.provider.get_console_event_totals(
            ctx.start, ctx.end, bucket_seconds=BUCKET_SECONDS
        )
        return StepResult(len(result.totals), False)

    return Plan((Step(step),), query_version=CONSOLE_EVENT_TOTALS_QUERY_VERSION)


async def _prepare_waf(ctx: _Context) -> Plan | Early:
    async with ctx.sessionmaker() as session:
        row = (
            await session.execute(
                select(WafSourceProfile, LogSource)
                .join(LogSource, LogSource.id == WafSourceProfile.log_source_id)
                .where(
                    WafSourceProfile.instance_id == ctx.instance.id,
                    WafSourceProfile.enabled.is_(True),
                )
                .order_by(LogSource.qradar_id, WafSourceProfile.id)
                .limit(1)
            )
        ).first()
    if row is None:
        return Early(SKIP, "no WAF profile is enabled on this console")
    profile, source = row
    entity = f"WAF profile of log source {source.name} (QRadar id {source.qradar_id})"
    try:
        mapping = WafFieldMapping.from_profile(profile)
        # The collector sends the volume search plus each breakdown this plan collects.
        collected = breakdown_plan(profile, ctx.settings).collect
    except WafProfileError:
        return Early(FAIL, f"{entity}: the stored field mapping is not valid; no search was sent")
    base = [profile.url_property, profile.method_property]
    if profile.virtual_host_property:
        base.append(profile.virtual_host_property)
    common: dict[str, Any] = {
        "qradar_log_source_id": source.qradar_id,
        "window_start": ctx.start,
        "window_end": ctx.end,
        "bucket_seconds": BUCKET_SECONDS,
        "url_property": profile.url_property,
        "method_property": profile.method_property,
        "virtual_host_property": profile.virtual_host_property,
        "max_groups": ROW_CAP,
        "max_rows": ROW_CAP,
    }

    async def volume() -> StepResult:
        batch = await ctx.provider.get_waf_observations_bucketed(**common)
        return StepResult(batch.rows_returned, batch.truncated)

    def breakdown_step(breakdown: WafBreakdown) -> tuple[Step, str | None]:
        # The same property the collector hands the provider; SOURCE_IP reads `sourceip`.
        property_name = {
            WafBreakdown.ACTION: mapping.action,
            WafBreakdown.RESPONSE_CODE: mapping.response_code,
            WafBreakdown.SOURCE_IP: None,
        }[breakdown]

        async def run() -> StepResult:
            batch = await ctx.provider.get_waf_breakdown_bucketed(
                **common, breakdown=breakdown.value, breakdown_property=property_name
            )
            return StepResult(batch.rows_returned, batch.truncated)

        used = [*base, property_name] if property_name else base
        listed = ", ".join(f'"{name}"' for name in used)
        if property_name is None:
            listed += " and QRadar's normalized sourceip"
        return Step(run, label=f"{breakdown.value} breakdown search", properties=listed), (
            property_name
        )

    steps = [
        Step(volume, label="volume search", properties=", ".join(f'"{n}"' for n in base))
    ]
    names = list(base)
    for item in sorted(collected):
        step, property_name = breakdown_step(item)
        steps.append(step)
        if property_name:
            names.append(property_name)
    return Plan(tuple(steps), entity=entity, names=tuple(dict.fromkeys(names)))


async def _prepare_ips(ctx: _Context) -> Plan | Early:
    async with ctx.sessionmaker() as session:
        row = (
            await session.execute(
                select(IpsSourceProfile, LogSource)
                .join(LogSource, LogSource.id == IpsSourceProfile.log_source_id)
                .where(
                    IpsSourceProfile.instance_id == ctx.instance.id,
                    IpsSourceProfile.enabled.is_(True),
                )
                .order_by(LogSource.qradar_id, IpsSourceProfile.id)
                .limit(1)
            )
        ).first()
    if row is None:
        return Early(SKIP, "no IPS source profile is enabled on this console")
    profile, source = row
    entity = f"IPS profile of log source {source.name} (QRadar id {source.qradar_id})"
    try:
        mapping = IpsFieldMapping.from_profile(profile)
    except IpsProfileError:
        return Early(FAIL, f"{entity}: the stored field mapping is not valid; no search was sent")
    planned = plan_searches(
        [(source.qradar_id, mapping)],
        max_sources_per_search=ctx.settings.ips_max_sources_per_search,
    )
    if not planned:
        return Early(SKIP, f"{entity}: the profile plans no search")

    def search_step(item: Any) -> Step:
        group = item.group

        async def run() -> StepResult:
            result = await ctx.provider.get_ips_keyed(
                item.dimension,
                qradar_log_source_ids=group.source_ids,
                fields=group.provider_fields(),
                window_start=ctx.start,
                window_end=ctx.end,
                bucket_seconds=BUCKET_SECONDS,
                max_rows=ROW_CAP,
            )
            return StepResult(len(result.rows), result.truncated)

        listed = ", ".join(f'{logical}="{kind}:{name}"' for logical, kind, name in group.fields)
        return Step(
            run, label=f"{item.dimension.value} search", properties=listed or NO_PROPERTIES
        )

    # The collector sends one search per planned dimension; so does this check.
    names = tuple(
        dict.fromkeys(name for item in planned for _l, _k, name in item.group.fields)
    )
    return Plan(tuple(search_step(item) for item in planned), entity=entity, names=names)


async def _prepare_property_watch(ctx: _Context) -> Plan | Early:
    async with ctx.sessionmaker() as session:
        repo = PropertyWatchRepository(session)
        watches = sorted(await repo.enabled_watches(ctx.instance.id), key=lambda w: str(w.id))
        if not watches:
            return Early(SKIP, "no property watch is enabled on this console")
        watch = watches[0]
        kind = PwScopeKind(watch.scope_kind)
        sources = await repo.sources(
            ctx.instance.id,
            source_ids={watch.log_source_id} if watch.log_source_id is not None else set(),
            whole_inventory=kind is not PwScopeKind.LOG_SOURCE,
        )
        groups = (
            await repo.groups(ctx.instance.id) if kind is PwScopeKind.LOG_SOURCE_GROUP else {}
        )
    entity = f"property watch {watch.name}"
    try:
        references = validate_properties(watch.properties)
    except ValueError:
        return Early(FAIL, f"{entity}: the stored properties are not valid; no search was sent")
    properties = tuple((ref.kind.value, ref.name) for ref in references)
    scope = resolve_scope(
        kind,
        log_source_id=watch.log_source_id,
        type_id=watch.scope_type_id,
        group_id=watch.scope_group_id,
        groups=groups,
        sources=sources,
        now=ctx.now,
        stale_after=timedelta(hours=ctx.settings.pw_scope_stale_after_hours),
        max_sources=ctx.settings.pw_max_sources_per_search,
        max_chunks=ctx.settings.pw_max_chunks_per_watch,
    )
    if not scope.resolved:
        return Early(SKIP, f"{entity}: its scope does not resolve; no search was sent")
    # A group scope is cut to its first source: one source is enough to test the query.
    ids = scope.chunks[0][:1] if kind is PwScopeKind.LOG_SOURCE_GROUP else scope.chunks[0]

    async def step() -> StepResult:
        result = await ctx.provider.get_pw_keyed(
            PwSearchKind.GROUPED,
            scope_kind=scope.scope_kind,
            scope_ids=ids,
            properties=properties,
            pinned=(),
            window_start=ctx.start,
            window_end=ctx.end,
            bucket_seconds=BUCKET_SECONDS,
            max_rows=ROW_CAP,
        )
        return StepResult(len(result.rows), result.truncated)

    return Plan(
        (
            Step(
                step,
                properties=", ".join(f'"{kind_}:{name}"' for kind_, name in properties),
            ),
        ),
        entity=entity,
        names=tuple(name for _kind, name in properties),
    )


async def _prepare_catchall(ctx: _Context) -> Plan | Early:
    type_ids = sorted(set(ctx.settings.catchall_type_ids))
    if not type_ids:
        return Early(SKIP, "CATCHALL_TYPE_IDS is empty; nothing is configured to search")
    first = type_ids[0]

    async def step() -> StepResult:
        result = await ctx.provider.get_catchall_senders_keyed(
            devicetype_ids=[first],
            window_start=ctx.start,
            window_end=ctx.end,
            bucket_seconds=BUCKET_SECONDS,
            max_rows=ROW_CAP,
        )
        return StepResult(len(result.rows), result.truncated)

    return Plan(
        (Step(step, properties="none (the query filters on device type ids)"),),
        entity=f"device type {first} of CATCHALL_TYPE_IDS",
    )


async def _prepare_geo(ctx: _Context) -> Plan | Early:
    settings = ctx.settings
    directions = [GeoDirection(item) for item in settings.geo_directions]
    direction = directions[0]
    property_mode = settings.geo_country_source == "property"
    name = settings.geo_property_for(direction.value) if property_mode else ""
    if property_mode and not name:
        return Early(
            SKIP,
            f"{_PROPERTY_SETTING[direction]} is empty; the {direction.value} direction "
            "is not collected",
        )
    if property_mode:
        async with ctx.sessionmaker() as session:
            collector = GeoCollector(session, ctx.provider, settings=settings)
            catalog, failure = await collector._country_catalog([direction], True)
        if failure is not None:
            return Early(WARN, f"{render(failure)} Nothing was proved about the query.")
        if name not in (catalog or set()):
            sentence = render(
                MessageRef(
                    "geo.property.missing",
                    {"name": name, "setting": _PROPERTY_SETTING[direction]},
                )
            )
            return Early(FAIL, f"property absent. {sentence} No search was sent.", (name,))

    async def step() -> StepResult:
        result = await ctx.provider.get_geo_keyed(
            ctx.start,
            ctx.end,
            direction=direction,
            bucket_seconds=BUCKET_SECONDS,
            max_rows=ROW_CAP,
            country_property=name if property_mode else None,
        )
        return StepResult(len(result.rows), result.truncated)

    return Plan(
        (
            Step(
                step,
                properties=(
                    f'"{name}"'
                    if property_mode
                    else "none (GEO::LOOKUP, GEO_COUNTRY_SOURCE=lookup)"
                ),
            ),
        ),
        entity=f"{direction.value} direction",
        query_version=GEO_PROPERTY_VERSION if property_mode else GEO_LOOKUP_VERSION,
        names=(name,) if property_mode else (),
    )


async def _prepare_health(ctx: _Context) -> Plan | Early:
    async with ctx.sessionmaker() as session:
        collector = QRadarHealthCollector(session, ctx.provider, settings=ctx.settings)
        missing = await collector._missing_properties()
    if isinstance(missing, MessageRef):
        return Early(WARN, f"{render(missing)} Nothing was proved about the query.")
    if missing:
        sentence = render(
            MessageRef(
                "qradar_health_detail.reason.property_missing", {"names": ", ".join(missing)}
            )
        )
        return Early(FAIL, f"property absent. {sentence}", tuple(missing))

    async def metrics() -> StepResult:
        result = await ctx.provider.get_health_metrics(ctx.start, ctx.end, max_rows=ROW_CAP)
        return StepResult(len(result.rows), result.truncated)

    async def notifications() -> StepResult:
        result = await ctx.provider.get_health_notifications(
            ctx.start, ctx.end, max_rows=ROW_CAP
        )
        return StepResult(len(result.rows), result.truncated)

    listed = ", ".join(f'"{name}"' for name in health_spec.REQUIRED_PROPERTIES)
    return Plan(
        (
            Step(metrics, label="metrics search", properties=listed),
            Step(notifications, label="notifications search", properties=listed),
        ),
        query_version=f"qradar_health/v{health_spec.QUERY_VERSION}",
        names=tuple(health_spec.REQUIRED_PROPERTIES),
    )


_PREPARE: dict[str, Prepare] = {
    "log_source_volume": _prepare_log_source_volume,
    "rule_match": _prepare_rule_match,
    "history": _prepare_history,
    "waf_collection": _prepare_waf,
    "ips_collection": _prepare_ips,
    "property_watch": _prepare_property_watch,
    "catchall_senders": _prepare_catchall,
    "geo_collection": _prepare_geo,
    "qradar_health": _prepare_health,
}


# -------------------------------------------------------------------- the search
@ariel_log.ariel_caller(ariel_log.CALLER_DOCTOR_AQL)
async def _send(step: Callable[[], Awaitable[StepResult]], timeout: float) -> StepResult:
    """One search, attributed to DOCTOR_AQL so a doctor run never shows as family load."""
    return await asyncio.wait_for(step(), timeout=timeout)


def _status(exc: BaseException) -> str:
    code = getattr(exc, "status_code", None)
    return f"HTTP {code}" if isinstance(code, int) else "no HTTP status"


#: A 4xx that is not an auth refusal: QRadar read the request and said no. (429 is not here:
#: the provider raises it as "unavailable", and it says nothing about the query.)
_REFUSED_CODES = range(400, 500)


def classify(exc: BaseException, plan: Plan, step: Step, *, timeout: float) -> tuple[str, str]:
    """The verdict of a search that raised. The exception text is never read: it can echo
    request detail (`_sanitize_upstream_error`).

    The provider tells a refusal at create from a failed search by what the exception carries,
    and only partly: a `ProviderError` with an HTTP status is a refused request, one without is
    a search QRadar created and then ended. It cannot say which request of the search (create,
    poll or fetch) drew the refusal, so the sentence says "the search request" and no more.
    """
    if isinstance(exc, ArielPermitUnavailable):
        return WARN, (
            f"no free Ariel permit within {exc.waited_seconds:g} s; nothing was sent. "
            "This proves nothing about the query"
        )
    if isinstance(exc, TimeoutError):
        return WARN, (
            f"no answer within {timeout:g} s (DOCTOR_AQL_TIMEOUT_SECONDS). "
            "This proves nothing about the query"
        )
    status = getattr(exc, "status_code", None)
    refused = isinstance(exc, ProviderQueryRejectedError) or (
        isinstance(exc, ProviderError)
        and not isinstance(exc, ProviderUnavailableError | ProviderAuthError)
        and isinstance(status, int)
        and status in _REFUSED_CODES
    )
    if refused:
        return FAIL, (
            f"QRadar refused the search request ({_status(exc)}). "
            f"Query version: {plan.query_version}; "
            f"properties the query names: {step.properties}. Compare them with the console's "
            "custom property list. QRadar's reply is not shown"
        )
    if isinstance(exc, ProviderAuthError):
        return FAIL, (
            f"QRadar refused the token ({_status(exc)}). The token needs the Ariel search "
            "permission; see qradar_auth and qradar_access"
        )
    if isinstance(exc, CapabilityNotSupportedError):
        return SKIP, "AQL runs only on the REST provider"
    if isinstance(exc, ProviderUnavailableError):
        if status == 429:
            return WARN, (
                "QRadar is rate-limiting searches (HTTP 429). This proves nothing about the query"
            )
        if ariel_log.outcome_for_exception(exc) == ariel_log.OUTCOME_TIMEOUT:
            return WARN, (
                "QRadar did not finish the search in time. This proves nothing about the query"
            )
        return WARN, "QRadar did not answer. This proves nothing about the query"
    if isinstance(exc, ProviderError):
        return WARN, (
            f"QRadar accepted the search but it ended without completing ({_status(exc)}). "
            "This proves nothing about the query"
        )
    if isinstance(exc, ValueError):
        return FAIL, (
            "the query could not be built from the configuration "
            f"(properties: {step.properties}); no search was sent"
        )
    return WARN, f"the check failed before an answer ({type(exc).__name__})"


async def _run_plan(plan: Plan, *, timeout: float) -> tuple[str, str]:
    """Every step is tried: a failed step never hides the others (OPS-R72)."""
    rows = 0
    truncated = False
    completed = 0
    problems: list[tuple[str, str]] = []
    started = time.monotonic()
    for step in plan.steps:
        try:
            result = await _send(step.run, timeout)
        except Exception as exc:
            verdict, text = classify(exc, plan, step, timeout=timeout)
            problems.append((verdict, f"{step.label}: {text}" if step.label else text))
            continue
        completed += 1
        rows += result.rows
        truncated = truncated or result.truncated
    prefix = f"{plan.entity}: " if plan.entity else ""
    if problems:
        verdict = min((item[0] for item in problems), key=lambda item: _ORDER[item])
        if verdict == SKIP:
            # Only a provider without AQL says SKIP; it has no step to name.
            return SKIP, problems[0][1]
        detail = "; ".join(text for _verdict, text in problems)
        if len(plan.steps) > 1:
            detail += f". {completed} of {len(plan.steps)} searches completed"
        return verdict, f"{prefix}{detail}"
    elapsed_ms = int((time.monotonic() - started) * 1000)
    text = (
        f"{completed} search(es) completed, {rows} row(s) returned, {elapsed_ms} ms"
        + (", truncated at the 1-row cap" if truncated else "")
    )
    return PASS, f"{prefix}{text}"


def _allowed(names: Sequence[str]) -> tuple[str, ...]:
    """The names the output layer must print whole: each one, and each unbroken run in it (a
    name with a space can hold a 24+ character run the token filter would still mask)."""
    out: dict[str, None] = {}
    for name in names:
        out[name] = None
        for run in re.findall(r"[A-Za-z0-9_-]+", name):
            out[run] = None
    return tuple(out)


_ORDER = {FAIL: 0, WARN: 1, PASS: 2, SKIP: 3}


def _merge(parts: Sequence[tuple[str, str, str]]) -> tuple[str, str]:
    """One family's verdict over its consoles: the worst, all lines kept."""
    verdict = min((part[0] for part in parts), key=lambda item: _ORDER[item])
    message = "; ".join(f"{console}: {text}" for _verdict, console, text in parts)
    return verdict, message


async def run_aql_checks(
    *,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    instances: Sequence[QRadarInstance],
    provider_factory: Callable[[QRadarInstance], QRadarProvider],
    now: datetime,
    families: Sequence[str] = FAMILIES,
) -> list[AqlLine]:
    """One line per family. Each family sends one search per provider method its collector calls."""
    rest = [item for item in instances if item.provider_kind == "rest"]
    others = [item for item in instances if item.provider_kind != "rest"]
    start, end = window_for(now)
    timeout = float(settings.doctor_aql_timeout_seconds)
    providers: dict[Any, QRadarProvider] = {}
    lines: list[AqlLine] = []
    try:
        for family in families:
            if not rest:
                reason = (
                    "AQL runs only on the REST provider; no enabled console uses it"
                    if others
                    else "no enabled QRadar console is registered"
                )
                lines.append(AqlLine(family, SKIP, reason))
                continue
            switch = _SWITCHES.get(family)
            if switch is not None and not getattr(settings, switch):
                lines.append(
                    AqlLine(family, SKIP, f"{switch.upper()} is off; the family is not collected")
                )
                continue
            parts: list[tuple[str, str, str]] = []
            names: list[str] = []
            for instance in rest:
                provider = providers.get(instance.id)
                if provider is None:
                    try:
                        provider = provider_factory(instance)
                    except Exception as exc:
                        reason = f"no client could be built ({type(exc).__name__})"
                        parts.append((WARN, instance.name, reason))
                        continue
                    providers[instance.id] = provider
                ctx = _Context(settings, sessionmaker, instance, provider, start, end, now)
                try:
                    plan = await _PREPARE[family](ctx)
                except Exception as exc:
                    parts.append(
                        (WARN, instance.name, f"inputs could not be read ({type(exc).__name__})")
                    )
                    continue
                names.extend(plan.names)
                if isinstance(plan, Early):
                    parts.append((plan.verdict, instance.name, plan.message))
                    continue
                verdict, text = await _run_plan(plan, timeout=timeout)
                parts.append((verdict, instance.name, text))
            parts.extend(
                (SKIP, item.name, "AQL runs only on the REST provider") for item in others
            )
            verdict, message = _merge(parts)
            lines.append(AqlLine(family, verdict, message, _allowed(names)))
    finally:
        for provider in providers.values():
            # Closing a client must not hide the verdicts.
            with contextlib.suppress(Exception):
                await provider.aclose()
    return lines
