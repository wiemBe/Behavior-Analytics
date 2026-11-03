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
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

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
    HEALTH_HOSTS_BOUND,
    HEALTH_SERVERS_BOUND,
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
    HaPairState,
    HealthField,
    HostEntry,
    HostMember,
    HostsValue,
    LicenseRate,
    LicenseValue,
    QRadarHealth,
    RateValue,
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
_STANDBY = "STANDBY"

#: The host statuses IBM documents for an HA cluster (QRadar High Availability Guide, "Status of
#: HA hosts", Table 4), as the REST API writes them: upper case, words joined with `_`. Anything
#: else is not guessed at: the pair is `unknown` and the string is shown as QRadar wrote it.
HA_DOCUMENTED_STATUSES: frozenset[str] = frozenset(
    {
        "ACTIVE",
        "STANDBY",
        "FAILED",
        "SYNCHRONIZING",
        "ONLINE",
        "OFFLINE",
        "RESTORING",
        "NEEDS_LICENSE",
        "SETTING_OFFLINE",
        "SETTING_ONLINE",
        "NEEDS_UPGRADE",
        "UPGRADING",
    }
)
#: The component only the console runs. IBM: "You can add one Magistrate component for each
#: deployment" (QRadar 7.1 MR2 Installation Guide), and the Magistrate "exists on the QRadar
#: Console" (IBM support note 6558024). The lab console's component list holds it, no other
#: host's does.
CONSOLE_COMPONENT = "magistrate"
#: Pairs listed in `ha_issues`, and members shown for a host, so a big deployment does not
#: grow the section without bound beyond the host bound.
MAX_HA_ISSUES_LISTED = 20

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
    hosts: HealthField[HostsValue]
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


def normalize_status(raw: str | None) -> str | None:
    """A server status as QRadar's HA documentation spells it: upper case, words joined by `_`."""
    if raw is None:
        return None
    text = re.sub(r"[\s\-]+", "_", raw.strip()).upper()
    return text or None


def host_role(components: list[str] | None) -> Literal["console", "managed"] | None:
    """Console or managed host from the component list; no list (or an empty one) is None."""
    if not components:
        return None
    names = {c.strip().lower() for c in components}
    return "console" if CONSOLE_COMPONENT in names else "managed"


def classify_pair(primary: str | None, secondary: str | None, *, roles_known: bool) -> HaPairState:
    """The state of a pair from its two members' normalized statuses (None: not read).

    Without `roles_known` the two statuses are in no particular order, and only a verdict that
    does not depend on which member is which is given. A status outside IBM's list, a member
    that was not read, and two ACTIVE members are all `unknown`: never healthy.
    """
    statuses = (primary, secondary)
    if any(st is None or st not in HA_DOCUMENTED_STATUSES for st in statuses):
        return HaPairState.UNKNOWN
    actives = sum(1 for st in statuses if st == _ACTIVE)
    if actives == 0:
        return HaPairState.HA_FAILED
    if actives == 2 or not roles_known:
        return HaPairState.UNKNOWN
    if secondary == _ACTIVE:
        return HaPairState.HA_FAILED_OVER
    return HaPairState.HA_HEALTHY if secondary == _STANDBY else HaPairState.HA_DEGRADED


@dataclass(frozen=True)
class _Group:
    """One managed host with its server members, classified."""

    host: DeploymentHostDTO | None
    name: str | None
    members: list[HostMember]
    state: HaPairState
    unclassified: list[str]

    @property
    def statuses(self) -> list[str | None]:
        return [m.status.value for m in self.members]

    @property
    def active(self) -> bool:
        """Exactly one member is ACTIVE: the standby of a healthy pair is not a fault."""
        return sum(1 for st in self.statuses if st == _ACTIVE) == 1


def _member(
    row: ManagedServerDTO | None,
    *,
    role: str | None,
    server_id: int | None,
    unread: HealthField[Any] | None,
) -> HostMember:
    if row is None:
        status: HealthField[str] = unread if unread is not None else _not_collected("field_missing")
        return HostMember(server_id=server_id, role=role, status=status)
    normalized = normalize_status(row.status)
    return HostMember(
        server_id=row.server_id,
        hostname=row.hostname,
        private_ip=row.private_ip,
        role=role,
        status=_ok(normalized) if normalized else _not_collected("field_missing"),
    )


def _build_group(
    host: DeploymentHostDTO | None,
    rows: list[ManagedServerDTO],
    unread: HealthField[Any] | None,
) -> _Group:
    """Members and pair state of one managed host.

    `rows` are the `/system/servers` records with this `managed_host_id`; `unread` is the
    failed field to put on a member whose record is not there because the whole read failed.
    A host that names a secondary is a pair whatever the records say, so a missing second
    server makes the pair `unknown` rather than a single host.
    """
    primary_id = host.primary_server_id if host else None
    secondary_id = host.secondary_server_id if host else None
    expected_pair = secondary_id is not None or len(rows) >= 2
    name = host.hostname if host and host.hostname else (rows[0].hostname if rows else None)
    by_id = {r.server_id: r for r in rows if r.server_id is not None}
    members: list[HostMember] = []
    if not expected_pair:
        if rows:
            role = "primary" if primary_id is not None else None
            members.append(_member(rows[0], role=role, server_id=None, unread=None))
        else:
            role = "primary" if primary_id is not None else None
            members.append(_member(None, role=role, server_id=primary_id, unread=unread))
        return _Group(host, name, members, HaPairState.SINGLE, [])
    matched: set[int] = set()
    roles = (("primary", primary_id), ("secondary", secondary_id))
    for role, server_id in roles:
        if server_id is None:
            continue
        row = by_id.get(server_id)
        if row is not None:
            matched.add(id(row))
        members.append(_member(row, role=role, server_id=server_id, unread=unread))
    extras = [r for r in rows if id(r) not in matched]
    members.extend(_member(r, role=None, server_id=None, unread=None) for r in extras)
    roles_known = (
        host is not None
        and primary_id is not None
        and secondary_id is not None
        and not extras
        and len(members) == 2
    )
    statuses = [m.status.value for m in members]
    if len(members) == 2:
        state = classify_pair(statuses[0], statuses[1], roles_known=roles_known)
    else:
        state = HaPairState.UNKNOWN
    shown = (
        list(dict.fromkeys(st for st in statuses if st is not None))
        if state is HaPairState.UNKNOWN
        else []
    )
    return _Group(host, name, members, state, shown)


def _rows_by_managed_host(rows: list[ManagedServerDTO]) -> dict[int, list[ManagedServerDTO]]:
    out: dict[int, list[ManagedServerDTO]] = {}
    for row in rows:
        if row.managed_host_id is not None:
            out.setdefault(row.managed_host_id, []).append(row)
    return out


def _server_groups(
    rows: list[ManagedServerDTO], hosts: list[DeploymentHostDTO] | None
) -> list[_Group]:
    """The managed hosts as `/system/servers` reports them, with `hosts` giving the roles.

    A record with no `managed_host_id` is a host of its own. A managed host that the hosts read
    names but `/system/servers` has no record for is a group without members read, so it is
    never counted as active.
    """
    by_host = {h.host_id: h for h in hosts or [] if h.host_id is not None}
    buckets: dict[tuple[str, int], list[ManagedServerDTO]] = {}
    for at, row in enumerate(rows):
        key = ("m", row.managed_host_id) if row.managed_host_id is not None else ("r", at)
        buckets.setdefault(key, []).append(row)
    groups = [
        _build_group(by_host.get(key[1]) if key[0] == "m" else None, grouped, None)
        for key, grouped in buckets.items()
    ]
    groups.extend(
        _build_group(host, [], None)
        for host_id, host in by_host.items()
        if ("m", host_id) not in buckets
    )
    return groups


def _group_status_text(group: _Group) -> str | None:
    if group.state is HaPairState.SINGLE:
        return group.statuses[0] if group.statuses else None
    return " / ".join(st or "no status" for st in group.statuses)


def servers_field(
    rows: list[ManagedServerDTO],
    hosts: list[DeploymentHostDTO] | None = None,
    *,
    truncated: bool = False,
) -> HealthField[ServersValue]:
    """The managed-host summary from `/system/servers`, with the HA pair rule.

    `hosts` (the deployment hosts, when they were read) names each pair's primary and
    secondary; without it a pair's roles are unknown and it can only be `unknown` or `ha_failed`.
    """
    if not rows:
        return _not_collected("empty_response")
    groups = _server_groups(rows, hosts)
    members = [m for g in groups for m in g.members]
    if all(m.status.value is None for m in members):
        return _not_collected("field_missing")
    by_status: dict[str, int] = {}
    for member in members:
        if member.status.value is not None:
            by_status[member.status.value] = by_status.get(member.status.value, 0) + 1
    not_active: list[ServerEntry] = []
    ha_issues: list[ServerEntry] = []
    active = ha_pairs = ha_problem = 0
    for group in groups:
        is_pair = group.state is not HaPairState.SINGLE
        entry = ServerEntry(
            hostname=group.name,
            status=_group_status_text(group),
            ha_state=group.state if is_pair else None,
        )
        if group.active:
            active += 1
        elif len(not_active) < MAX_NOT_ACTIVE_LISTED:
            not_active.append(entry)
        if is_pair:
            ha_pairs += 1
            if group.state is not HaPairState.HA_HEALTHY:
                ha_problem += 1
                if len(ha_issues) < MAX_HA_ISSUES_LISTED:
                    ha_issues.append(entry)
    return _ok(
        ServersValue(
            total=len(groups),
            active=active,
            members_total=len(members),
            unreported=sum(1 for m in members if m.status.value is None),
            by_status=by_status,
            not_active=not_active,
            all_active=active == len(groups) and not truncated,
            ha_pairs=ha_pairs,
            ha_problem=ha_problem,
            ha_issues=ha_issues,
            truncated=truncated,
        )
    )


def _host_rate(
    allocation: int | None, limit: int | None, average: int | None, peak: int | None
) -> HealthField[RateValue]:
    if allocation is None and limit is None and average is None and peak is None:
        return _not_collected("field_missing")
    return _ok(RateValue(allocation=allocation, hardware_limit=limit, average=average, peak=peak))


def hosts_field(
    hosts: list[DeploymentHostDTO],
    servers: list[ManagedServerDTO] | None,
    *,
    servers_unread: HealthField[Any] | None = None,
    truncated: bool = False,
) -> HealthField[HostsValue]:
    """The per-host table: one entry per managed host, its server members under it.

    `servers` is None when `/system/servers` was not read; `servers_unread` is then the failed
    field each member carries as its status. EPS and FPM come from the managed host's own
    record, once, whatever the member count.
    """
    if not hosts:
        return _not_collected("empty_response")
    by_managed = _rows_by_managed_host(servers or [])
    items: list[HostEntry] = []
    for host in hosts:
        rows = by_managed.get(host.host_id, []) if host.host_id is not None else []
        group = _build_group(host, rows, servers_unread if servers is None else None)
        items.append(
            HostEntry(
                host_id=host.host_id,
                hostname=host.hostname,
                private_ip=host.private_ip,
                public_ip=host.public_ip,
                appliance_id=host.appliance_id,
                appliance_description=host.appliance_description,
                version=host.version,
                status=host.status,
                cpus=host.cpus,
                total_memory_kb=host.total_memory_kb,
                eps=_host_rate(
                    host.eps_allocation,
                    host.eps_rate_hardware_limit,
                    host.average_eps,
                    host.peak_eps,
                ),
                fpm=_host_rate(
                    host.fpm_allocation,
                    host.fpm_rate_hardware_limit,
                    host.average_fpm,
                    host.peak_fpm,
                ),
                role=host_role(host.components),
                pair_state=group.state,
                unclassified=group.unclassified,
                members=group.members,
            )
        )
    pairs = [i for i in items if i.pair_state is not HaPairState.SINGLE]
    return _ok(
        HostsValue(
            hosts=len(items),
            truncated=truncated,
            ha_pairs=len(pairs),
            ha_problem=sum(1 for i in pairs if i.pair_state is not HaPairState.HA_HEALTHY),
            items=items,
        )
    )


def eps_field(
    rows: list[DeploymentHostDTO], *, truncated: bool = False
) -> HealthField[EpsValue]:
    """Observed and licensed EPS summed over the managed hosts, one record per host.

    A host's rates are the host's, not its members': an HA pair counts once.
    """
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
            truncated=truncated,
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
        return _Reads(
            now, reachable, latency, version, skipped, skipped, skipped, skipped, skipped
        )

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
    # One more row than the bound was asked for, so a longer answer is known to be cut.
    servers_cut = servers_exc is None and len(servers or []) > HEALTH_SERVERS_BOUND
    hosts_cut = hosts_exc is None and len(hosts or []) > HEALTH_HOSTS_BOUND
    server_rows = None if servers_exc is not None else (servers or [])[:HEALTH_SERVERS_BOUND]
    host_rows = None if hosts_exc is not None else (hosts or [])[:HEALTH_HOSTS_BOUND]
    servers_failed = _from_error(servers_exc) if servers_exc is not None else None
    hosts_failed = _from_error(hosts_exc) if hosts_exc is not None else None
    return _Reads(
        taken_at=now,
        reachable=reachable,
        api_latency_ms=latency,
        version=version,
        servers=(
            servers_failed
            if servers_failed is not None
            else servers_field(server_rows or [], host_rows, truncated=servers_cut)
        ),
        hosts=(
            hosts_failed
            if hosts_failed is not None
            else hosts_field(
                host_rows or [], server_rows, servers_unread=servers_failed, truncated=hosts_cut
            )
        ),
        eps=(
            hosts_failed
            if hosts_failed is not None
            else eps_field(host_rows or [], truncated=hosts_cut)
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
    return _Reads(now, field, field, field, field, field, field, field, field)


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
        hosts=field,
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
    sample_sink: Callable[[QRadarHealth], Awaitable[None]] | None = None,
) -> QRadarHealth:
    """The `qradar` section for one console.

    `ariel_runs` is the platform's own search record for the console, read by the caller from
    `ariel_search_run`; None leaves its fields `NOT_COLLECTED` with `ariel_search_run_not_wired`,
    or `ariel_search_run_unreadable` when the caller tried to read it and could not
    (`ariel_runs_unreadable`).
    A hit in the cache builds no provider and sends nothing to QRadar.

    `sample_sink` (HLT-2) receives the section of a read that was taken from QRadar just now,
    never one served from the cache. The stored-trend collector passes one so each collection
    run keeps one REST sample; a sink that raises is logged and never fails the read.
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
            hosts=reads.hosts,
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
                "hosts": reads.hosts.state.value,
                "eps": reads.eps.state.value,
                "license": reads.license.state.value,
                "ariel_list": reads.list_probe.state.value,
            },
        )
        fresh = compose(entry, cached=False)
        if sample_sink is not None:
            try:
                await sample_sink(fresh)
            except Exception as exc:  # the section is still good; only the sample is lost
                logger.warning("qradar health sample not stored (%s)", type(exc).__name__)
        return fresh
