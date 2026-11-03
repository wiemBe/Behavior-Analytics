"""The `qradar` health section of the Ariel-load view: how QRadar is doing, per console (ARL-3).

Every field carries its own state. `OK` means the value was read; any other state means it was
not, and then there is no value at all -- never a 0, never "healthy" (repository rule 8). The
reason is a stable code plus a fixed English sentence; no upstream text, header or token ever
reaches it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class FieldState(StrEnum):
    #: The value was read from QRadar (or from the platform's own record of its searches).
    OK = "OK"
    #: Not read, and not because QRadar refused: the source is not wired, the provider cannot
    #: read it, the console was unreachable, or the response lacked the field.
    NOT_COLLECTED = "NOT_COLLECTED"
    #: QRadar answered 403: the configured token lacks the capability for this read.
    FORBIDDEN = "FORBIDDEN"
    #: The read was attempted and failed (timeout, 5xx, 401, malformed answer).
    FAILED = "FAILED"


class HealthField[T](BaseModel):
    """One measured value, or the reason there is none."""

    state: FieldState
    value: T | None = None
    reason_code: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _a_value_only_when_read(self) -> Self:
        if self.state is FieldState.OK:
            if self.value is None:
                raise ValueError("an OK field carries a value")
        else:
            if self.value is not None:
                raise ValueError("a field that was not read carries no value")
            if not self.reason_code or not self.reason:
                raise ValueError("a field that was not read says why")
        return self


class HaPairState(StrEnum):
    """The state of one managed host as an HA pair (HLT-1, design D-42 section 2).

    `unknown` is a pair that cannot be classified: a status string outside IBM's list, a member
    that was not read, two ACTIVE members, or roles that are not known. It is never read as
    healthy.
    """

    #: One server, no HA partner.
    SINGLE = "single"
    #: The primary is ACTIVE and the secondary is STANDBY.
    HA_HEALTHY = "ha_healthy"
    #: One member is ACTIVE and the other is not on standby: no failover is possible.
    HA_DEGRADED = "ha_degraded"
    #: The secondary is ACTIVE and the primary is not.
    HA_FAILED_OVER = "ha_failed_over"
    #: No member is ACTIVE.
    HA_FAILED = "ha_failed"
    UNKNOWN = "unknown"


class ServerEntry(BaseModel):
    hostname: str | None = None
    #: A single host: its status. An HA pair: both members' statuses, primary first
    #: ("FAILED / OFFLINE"); a member that was not read reads "no status".
    status: str | None = None
    #: Set for a managed host that is an HA pair (or should be); None for a single host.
    ha_state: HaPairState | None = None


class ServersValue(BaseModel):
    """`GET /system/servers` grouped by managed host (an HA pair is one host).

    `total` and `active` count managed hosts: a host is active when exactly one of its members
    is `ACTIVE`, so the standby of a healthy pair is never counted as not active. `by_status`
    and `unreported` count the members (server records), so on an HA site they add up to more
    than `total`.
    """

    total: int
    active: int
    #: Members (servers) behind `total` managed hosts: one per single host, two per HA pair
    #: (a member whose record was not returned counts). `by_status` and `unreported` count
    #: these, not hosts.
    members_total: int = 0
    #: Members whose status was not read. Counted apart, never as active.
    unreported: int
    by_status: dict[str, int]
    #: Managed hosts without exactly one ACTIVE member, at most 20, so the section stays small
    #: on a big deployment.
    not_active: list[ServerEntry] = Field(default_factory=list)
    all_active: bool
    #: Managed hosts that are HA pairs.
    ha_pairs: int = 0
    #: HA pairs not in the `ha_healthy` state (degraded, failed over, failed or unknown).
    ha_problem: int = 0
    #: The HA pairs counted in `ha_problem`, at most 20, with their state.
    ha_issues: list[ServerEntry] = Field(default_factory=list)
    #: The read stopped at its bound: the figures cover the records read only, and
    #: `all_active` is not claimed.
    truncated: bool = False


class HostMember(BaseModel):
    """One server of a managed host: the only one, or one of an HA pair's two."""

    server_id: int | None = None
    hostname: str | None = None
    private_ip: str | None = None
    #: `primary` / `secondary` from the managed host's `primary_server_id` and
    #: `secondary_server_id`; None when the host record was not read or does not name it.
    role: str | None = None
    #: The status QRadar reports, upper-cased; not read -> a state and a reason, no value.
    status: HealthField[str]


class RateValue(BaseModel):
    """EPS or FPM of one managed host. A figure QRadar did not give is None, never 0."""

    allocation: int | None = None
    hardware_limit: int | None = None
    #: Previous month, as QRadar reports it.
    average: int | None = None
    peak: int | None = None


class HostEntry(BaseModel):
    """One managed host (`GET /config/deployment/hosts`) with its server members."""

    host_id: int | None = None
    hostname: str | None = None
    private_ip: str | None = None
    public_ip: str | None = None
    appliance_id: str | None = None
    appliance_description: str | None = None
    version: str | None = None
    #: The deployment record's status for the host ("Active"), as QRadar writes it.
    status: str | None = None
    cpus: int | None = None
    total_memory_kb: int | None = None
    #: The managed host's rates, once per host: an HA pair is never counted twice.
    eps: HealthField[RateValue]
    fpm: HealthField[RateValue]
    #: `console` when the host runs the Magistrate, `managed` when it reports components and
    #: none is the Magistrate, None when QRadar gave no component list. Never a guess.
    role: Literal["console", "managed"] | None = None
    pair_state: HaPairState
    #: For `unknown`: the status strings that were read, rendered as QRadar wrote them.
    unclassified: list[str] = Field(default_factory=list)
    members: list[HostMember] = Field(default_factory=list)


class HostsValue(BaseModel):
    """The per-host table: one entry per managed host."""

    #: Managed hosts read (and listed in `items`).
    hosts: int
    #: The answer was longer than the bound: `hosts` is the count kept, there are more.
    truncated: bool = False
    ha_pairs: int = 0
    ha_problem: int = 0
    items: list[HostEntry] = Field(default_factory=list)


class EpsValue(BaseModel):
    """`GET /config/deployment/hosts`, summed: observed event rate against the licensed rate."""

    hosts: int
    average_eps: int
    peak_eps: int
    #: The hosts' EPS allocation summed.
    allocated_eps: int
    #: `average_eps / allocated_eps`; None when the allocation is 0.
    average_share: float | None = None
    #: The read stopped at its bound: the sums are lower bounds.
    truncated: bool = False


class LicenseRate(BaseModel):
    total: int | None = None
    allocated: int | None = None
    overallocated: bool | None = None


class LicenseValue(BaseModel):
    """`GET /config/deployment/license_pool`."""

    eps: LicenseRate
    fpm: LicenseRate | None = None


class ArielListValue(BaseModel):
    """`GET /ariel/searches`: search ids QRadar holds, every user's, completed and active."""

    held: int
    #: The read stopped at its range cap: `held` is a lower bound.
    truncated: bool = False


class ArielRecentValue(BaseModel):
    """What the platform's own Ariel searches saw in the last `window_minutes`."""

    window_minutes: int
    searches: int
    unavailable: int
    #: `unavailable / searches`.
    unavailable_share: float
    last_success_at: datetime | None = None
    last_unavailable_at: datetime | None = None


class ArielSection(BaseModel):
    #: QRadar's own count of search ids it holds (all users, finished and active alike). The
    #: list does not say which are active, and asking each one would be a query per search.
    list_probe: HealthField[ArielListValue]
    #: From `ariel_search_run`: did our latest search find Ariel answering.
    available: HealthField[bool]
    recent: HealthField[ArielRecentValue]
    #: Our own searches in flight now, to read beside `list_probe.held`.
    ours_in_flight: HealthField[int]


class QRadarHealth(BaseModel):
    """The per-console `qradar` section."""

    instance_id: UUID
    instance_name: str
    #: UTC time the QRadar reads below were taken. Older than now when `cached`.
    as_of: datetime
    cached: bool
    age_seconds: float
    #: How long QRadar reads are reused before QRadar is asked again.
    cache_ttl_seconds: int
    #: True when an HTTP answer came back; False (with a reason) when none did.
    reachable: HealthField[bool]
    #: Round trip of `GET /system/about`, one attempt, in milliseconds.
    api_latency_ms: HealthField[int]
    version: HealthField[str]
    servers: HealthField[ServersValue]
    #: The per-host table with HA members (HLT-1).
    hosts: HealthField[HostsValue]
    eps: HealthField[EpsValue]
    license: HealthField[LicenseValue]
    ariel: ArielSection
