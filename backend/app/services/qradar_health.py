"""QRadar's own health beside the load the platform puts on it (ARL-3).

`read_qradar_health` returns the per-console `qradar` section: whether the console answers and
how fast, what QRadar says about its managed hosts, its EPS and license, how many Ariel searches
it holds in total (all users), and whether Ariel answered our own latest searches.

Read-only (repository rule 1): the provider reads are four GETs and the reachability probe is
`GET /system/about`, each tried once. Nothing here runs AQL, so no Ariel permit is taken. Every
field is read, or says why it was not (rule 8): a refusal, a timeout, a missing field and an
unwired source each have their own state and code, and none is ever a zero.

The QRadar reads are reused for `QRADAR_HEALTH_CACHE_TTL_SECONDS`, so a page that polls the load
endpoint every few seconds asks the console at most once per TTL per API process, failures
included: a console that is struggling is not asked again just because it failed. The platform's
own search record (`ArielRunsSnapshot`) is a database read and is merged fresh on every call.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.core.config import Settings, get_settings
from app.i18n.messages import MessageRef, render
from app.models.instance import QRadarInstance
from app.providers.base import (
    CapabilityNotSupportedError,
    ProviderAuthError,
    ProviderError,
    ProviderUnavailableError,
    QRadarProvider,
)
from app.providers.dto import (
    ArielSearchListDTO,
    DeploymentHostDTO,
    InstanceInfoDTO,
    LicensePoolDTO,
    LicenseRateDTO,
    ManagedServerDTO,
)
from app.providers.factory import build_provider_for_instance
from app.schemas.qradar_health import (
    ArielListValue,
    ArielRecentValue,
    ArielSection,
    EpsValue,
    FieldState,
    HealthField,
    LicenseRate,
    LicenseValue,
    QRadarHealth,
    ServerEntry,
    ServersValue,
)

logger = logging.getLogger("app.services.qradar_health")

#: One QRadar read is given this long. The provider's own client timeout is a minute, far too
#: long for a view that is polled.
READ_TIMEOUT_SECONDS = 10.0
#: Hosts named in `not_active`; a large deployment does not grow the section.
MAX_NOT_ACTIVE_LISTED = 20
#: The window the platform's own search outcomes are summarised over.
ARIEL_RECENT_WINDOW_MINUTES = 15

_ACTIVE = "ACTIVE"

#: The reason codes a field can carry. Each has a coded message `qradar_health.reason.<code>` in
#: the backend registry (English only); nothing from an upstream body or exception is ever added.
REASON_CODES: tuple[str, ...] = (
    "provider_not_supported",
    "provider_not_configured",
    "console_unreachable",
    "unreachable",
    "timeout",
    "forbidden",
    "unauthorized",
    "endpoint_not_found",
    "ariel_unavailable",
    "upstream_error",
    "rejected",
    "malformed_response",
    "read_failed",
    "field_missing",
    "empty_response",
    "ariel_search_run_not_wired",
    "ariel_search_run_unreadable",
    "in_flight_not_read",
    "no_search_in_window",
    "no_ariel_outcome_in_window",
    "snapshot_inconsistent",
)


def reason_text(code: str) -> str:
    """The English sentence for a reason code, rendered from the message registry."""
    return render(MessageRef(f"qradar_health.reason.{code}"))


#: Code -> sentence, rendered once from the registry.
REASONS: dict[str, str] = {code: reason_text(code) for code in REASON_CODES}


def _fail(state: FieldState, code: str) -> HealthField[Any]:
    return HealthField(state=state, reason_code=code, reason=REASONS[code])


def _ok[T](value: T) -> HealthField[T]:
    return HealthField(state=FieldState.OK, value=value)


def _not_collected(code: str) -> HealthField[Any]:
    return _fail(FieldState.NOT_COLLECTED, code)


def classify_error(exc: BaseException, *, ariel: bool = False) -> tuple[FieldState, str]:
    """Map a failed read to a state and a code, from the exception type and status alone."""
    if isinstance(exc, CapabilityNotSupportedError):
        return FieldState.NOT_COLLECTED, "provider_not_supported"
    if isinstance(exc, TimeoutError):
        return FieldState.FAILED, "timeout"
    status = exc.status_code if isinstance(exc, ProviderError) else None
    if isinstance(exc, ProviderAuthError):
        if status == 403:
            return FieldState.FORBIDDEN, "forbidden"
        return FieldState.FAILED, "unauthorized"
    if isinstance(exc, ProviderUnavailableError) and status is None:
        return FieldState.FAILED, "unreachable"
    if status == 404:
        return FieldState.NOT_COLLECTED, "endpoint_not_found"
    if status == 503 and ariel:
        return FieldState.FAILED, "ariel_unavailable"
    if status is not None and status >= 500:
        return FieldState.FAILED, "upstream_error"
    if status is not None:
        return FieldState.FAILED, "rejected"
    if isinstance(exc, ProviderError):
        return FieldState.FAILED, "malformed_response"
    return FieldState.FAILED, "read_failed"


def _from_error(exc: BaseException, *, ariel: bool = False) -> HealthField[Any]:
    state, code = classify_error(exc, ariel=ariel)
    return _fail(state, code)


def _is_unreachable(exc: BaseException) -> bool:
    """No HTTP answer came back at all: connect, TLS, or timeout."""
    if isinstance(exc, TimeoutError):
        return True
    return isinstance(exc, ProviderUnavailableError) and exc.status_code is None


@dataclass(frozen=True)
class ArielRunsSnapshot:
    """What the platform's own record of its Ariel searches says (`ariel_search_run`).

    The caller reads the table and passes this in; this module never touches the database.
    `unavailable` counts searches whose outcome was `PROVIDER_UNAVAILABLE` in the window.
    """

    window_minutes: int
    searches: int
    unavailable: int
    last_success_at: datetime | None = None
    last_unavailable_at: datetime | None = None
    #: Our searches submitted and not yet terminal; None when that was not read.
    in_flight: int | None = None


@dataclass(frozen=True)
class _Reads:
    """The QRadar half of the section, classified once and reused through the TTL."""

    taken_at: datetime
    reachable: HealthField[bool]
    api_latency_ms: HealthField[int]
    version: HealthField[str]
    servers: HealthField[ServersValue]
    eps: HealthField[EpsValue]
    license: HealthField[LicenseValue]
    list_probe: HealthField[ArielListValue]


@dataclass
class _Entry:
    reads: _Reads
    expires_at: float
    taken_monotonic: float


class HealthReadCache:
    """Per-process, per-console reuse of the QRadar reads, single-flight.

    Concurrent callers on one console wait for the one read in progress instead of each
    sending their own.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _Entry] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def lock(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def get(self, key: str, now: float) -> _Entry | None:
        entry = self._entries.get(key)
        if entry is None or entry.expires_at <= now:
            return None
        return entry

    def put(self, key: str, entry: _Entry) -> None:
        self._entries[key] = entry

    def clear(self) -> None:
        self._entries.clear()


#: The cache the API and the CLI share inside one process.
HEALTH_CACHE = HealthReadCache()


async def _attempt[T](call: Awaitable[T], timeout: float) -> tuple[T | None, BaseException | None]:
    """Run one read; a failure comes back as a value so the others still run."""
    try:
        return await asyncio.wait_for(call, timeout), None
    except Exception as exc:  # classified by type and status only, never by text
        return None, exc


def servers_field(rows: list[ManagedServerDTO]) -> HealthField[ServersValue]:
    if not rows:
        return _not_collected("empty_response")
    by_status: dict[str, int] = {}
    unreported = 0
    not_active: list[ServerEntry] = []
    active = 0
    for row in rows:
        status = (row.status or "").strip().upper()
        if not status:
            unreported += 1
            if len(not_active) < MAX_NOT_ACTIVE_LISTED:
                not_active.append(ServerEntry(hostname=row.hostname, status=None))
            continue
        by_status[status] = by_status.get(status, 0) + 1
        if status == _ACTIVE:
            active += 1
        elif len(not_active) < MAX_NOT_ACTIVE_LISTED:
            not_active.append(ServerEntry(hostname=row.hostname, status=status))
    if unreported == len(rows):
        return _not_collected("field_missing")
    return _ok(
        ServersValue(
            total=len(rows),
            active=active,
            unreported=unreported,
            by_status=by_status,
            not_active=not_active,
            all_active=active == len(rows),
        )
    )


def eps_field(rows: list[DeploymentHostDTO]) -> HealthField[EpsValue]:
    if not rows:
        return _not_collected("empty_response")
    # A host without all three figures makes the sum meaningless: a missing one would
    # otherwise be added as 0.
    if any(r.average_eps is None or r.peak_eps is None or r.eps_allocation is None for r in rows):
        return _not_collected("field_missing")
    average = sum(r.average_eps or 0 for r in rows)
    peak = sum(r.peak_eps or 0 for r in rows)
    allocated = sum(r.eps_allocation or 0 for r in rows)
    return _ok(
        EpsValue(
            hosts=len(rows),
            average_eps=average,
            peak_eps=peak,
            allocated_eps=allocated,
            average_share=round(average / allocated, 4) if allocated > 0 else None,
        )
    )


def _rate(raw: LicenseRateDTO | None) -> LicenseRate | None:
    if raw is None:
        return None
    return LicenseRate(total=raw.total, allocated=raw.allocated, overallocated=raw.overallocated)


def license_field(pool: LicensePoolDTO) -> HealthField[LicenseValue]:
    eps = _rate(pool.eps)
    if eps is None or eps.total is None:
        return _not_collected("field_missing")
    return _ok(LicenseValue(eps=eps, fpm=_rate(pool.fpm)))


def ariel_list_field(result: ArielSearchListDTO) -> HealthField[ArielListValue]:
    return _ok(ArielListValue(held=result.held, truncated=result.truncated))


def _about_fields(
    info: InstanceInfoDTO | None, exc: BaseException | None, latency_ms: int
) -> tuple[HealthField[bool], HealthField[int], HealthField[str]]:
    if exc is None and info is not None:
        version = _ok(info.version) if info.version else _not_collected("field_missing")
        return _ok(True), _ok(latency_ms), version
    assert exc is not None
    failed = _from_error(exc)
    if _is_unreachable(exc):
        code = failed.reason_code or "unreachable"
        reachable = HealthField[bool](
            state=FieldState.OK,
            value=False,
            reason_code=code,
            reason=REASONS[code],
        )
        return reachable, failed, failed
    if isinstance(exc, CapabilityNotSupportedError):
        # Not an HTTP answer, so nothing is known about reachability.
        return _from_error(exc), failed, failed
    # An HTTP status came back, so the console is there even though the read failed.
    return _ok(True), failed, failed


async def _read_qradar(provider: QRadarProvider, *, timeout: float, now: datetime) -> _Reads:
    started = time.perf_counter()
    info, about_exc = await _attempt(provider.probe_about(), timeout)
    latency_ms = round((time.perf_counter() - started) * 1000)
    reachable, latency, version = _about_fields(info, about_exc, latency_ms)

    if about_exc is not None and _is_unreachable(about_exc):
        skipped = _not_collected("console_unreachable")
        return _Reads(now, reachable, latency, version, skipped, skipped, skipped, skipped)

    # The probe answered, so the console is there: the four reads are independent, small, and
    # each tried once. They run together because they are bounded and few.
    (servers, servers_exc), (hosts, hosts_exc), (pool, pool_exc), (listed, list_exc) = (
        await asyncio.gather(
            _attempt(provider.list_managed_servers(), timeout),
            _attempt(provider.list_deployment_hosts(), timeout),
            _attempt(provider.get_license_pool(), timeout),
            _attempt(provider.count_ariel_searches(), timeout),
        )
    )
    return _Reads(
        taken_at=now,
        reachable=reachable,
        api_latency_ms=latency,
        version=version,
        servers=(
            _from_error(servers_exc)
            if servers_exc is not None
            else servers_field(servers or [])
        ),
        eps=(
            _from_error(hosts_exc) if hosts_exc is not None else eps_field(hosts or [])
        ),
        license=(
            _from_error(pool_exc)
            if pool_exc is not None
            else license_field(pool) if pool is not None else _not_collected("field_missing")
        ),
        list_probe=(
            _from_error(list_exc, ariel=True)
            if list_exc is not None
            else ariel_list_field(listed) if listed is not None else _not_collected("field_missing")
        ),
    )


def _failed_reads(now: datetime, code: str) -> _Reads:
    field = _not_collected(code)
    return _Reads(now, field, field, field, field, field, field, field)


def _ariel_section(
    reads: _Reads, runs: ArielRunsSnapshot | None, *, runs_unreadable: bool = False
) -> ArielSection:
    if runs is None:
        unwired = _not_collected(
            "ariel_search_run_unreadable" if runs_unreadable else "ariel_search_run_not_wired"
        )
        return ArielSection(
            list_probe=reads.list_probe, available=unwired, recent=unwired, ours_in_flight=unwired
        )
    in_flight = (
        _ok(runs.in_flight) if runs.in_flight is not None else _not_collected("in_flight_not_read")
    )
    if runs.unavailable > runs.searches or runs.searches < 0:
        bad = _not_collected("snapshot_inconsistent")
        return ArielSection(
            list_probe=reads.list_probe, available=bad, recent=bad, ours_in_flight=in_flight
        )
    if runs.searches == 0:
        quiet = _not_collected("no_search_in_window")
        return ArielSection(
            list_probe=reads.list_probe, available=quiet, recent=quiet, ours_in_flight=in_flight
        )
    recent = _ok(
        ArielRecentValue(
            window_minutes=runs.window_minutes,
            searches=runs.searches,
            unavailable=runs.unavailable,
            unavailable_share=round(runs.unavailable / runs.searches, 4),
            last_success_at=runs.last_success_at,
            last_unavailable_at=runs.last_unavailable_at,
        )
    )
    ok_at, down_at = runs.last_success_at, runs.last_unavailable_at
    if ok_at is None and down_at is None:
        available: HealthField[bool] = _not_collected("no_ariel_outcome_in_window")
    elif down_at is None or (ok_at is not None and ok_at > down_at):
        available = _ok(True)
    else:
        available = HealthField[bool](
            state=FieldState.OK,
            value=False,
            reason_code="ariel_unavailable",
            reason=REASONS["ariel_unavailable"],
        )
    return ArielSection(
        list_probe=reads.list_probe, available=available, recent=recent, ours_in_flight=in_flight
    )


def unreadable_health(instance: QRadarInstance, *, at: datetime, ttl: int) -> QRadarHealth:
    """The section for a console whose whole read raised unexpectedly: every field FAILED."""
    field = _fail(FieldState.FAILED, "read_failed")
    return QRadarHealth(
        instance_id=instance.id,
        instance_name=instance.name,
        as_of=at,
        cached=False,
        age_seconds=0.0,
        cache_ttl_seconds=ttl,
        reachable=field,
        api_latency_ms=field,
        version=field,
        servers=field,
        eps=field,
        license=field,
        ariel=ArielSection(
            list_probe=field, available=field, recent=field, ours_in_flight=field
        ),
    )


def _cache_key(instance: QRadarInstance) -> str:
    # The host and kind are in the key so a console that was edited is read afresh.
    return f"{instance.id}|{instance.console_host}|{instance.provider_kind}"


async def read_qradar_health(
    instance: QRadarInstance,
    *,
    settings: Settings | None = None,
    provider_factory: Callable[[QRadarInstance], QRadarProvider] | None = None,
    ariel_runs: ArielRunsSnapshot | None = None,
    ariel_runs_unreadable: bool = False,
    cache: HealthReadCache | None = None,
    now: Callable[[], datetime] | None = None,
    monotonic: Callable[[], float] | None = None,
    read_timeout: float = READ_TIMEOUT_SECONDS,
) -> QRadarHealth:
    """The `qradar` section for one console.

    `ariel_runs` is the platform's own search record for the console, read by the caller from
    `ariel_search_run`; None leaves its fields `NOT_COLLECTED` with `ariel_search_run_not_wired`,
    or `ariel_search_run_unreadable` when the caller tried to read it and could not
    (`ariel_runs_unreadable`).
    A hit in the cache builds no provider and sends nothing to QRadar.
    """
    settings = settings or get_settings()
    cache = cache if cache is not None else HEALTH_CACHE
    clock = now or (lambda: datetime.now(UTC))
    mono = monotonic or time.monotonic
    ttl = settings.qradar_health_cache_ttl_seconds
    key = _cache_key(instance)

    def compose(entry: _Entry, *, cached: bool) -> QRadarHealth:
        reads = entry.reads
        return QRadarHealth(
            instance_id=instance.id,
            instance_name=instance.name,
            as_of=reads.taken_at,
            cached=cached,
            age_seconds=round(max(0.0, mono() - entry.taken_monotonic), 3) if cached else 0.0,
            cache_ttl_seconds=ttl,
            reachable=reads.reachable,
            api_latency_ms=reads.api_latency_ms,
            version=reads.version,
            servers=reads.servers,
            eps=reads.eps,
            license=reads.license,
            ariel=_ariel_section(reads, ariel_runs, runs_unreadable=ariel_runs_unreadable),
        )

    hit = cache.get(key, mono())
    if hit is not None:
        return compose(hit, cached=True)

    async with cache.lock(key):
        hit = cache.get(key, mono())
        if hit is not None:
            return compose(hit, cached=True)

        taken_monotonic = mono()
        taken_at = clock()
        factory = provider_factory or (lambda inst: build_provider_for_instance(inst, settings))
        try:
            provider = factory(instance)
        except ValueError:
            # `build_provider_for_instance` refuses a console with no stored token.
            reads = _failed_reads(taken_at, "provider_not_configured")
        else:
            try:
                reads = await _read_qradar(provider, timeout=read_timeout, now=taken_at)
            finally:
                await provider.aclose()
        entry = _Entry(reads, expires_at=taken_monotonic + ttl, taken_monotonic=taken_monotonic)
        cache.put(key, entry)
        logger.debug(
            "qradar health read",
            extra={
                "reachable": reads.reachable.value,
                "servers": reads.servers.state.value,
                "eps": reads.eps.state.value,
                "license": reads.license.state.value,
                "ariel_list": reads.list_probe.state.value,
            },
        )
        return compose(entry, cached=False)
