"""The `qradar` health section of the Ariel-load view: how QRadar is doing, per console (ARL-3).

Every field carries its own state. `OK` means the value was read; any other state means it was
not, and then there is no value at all -- never a 0, never "healthy" (repository rule 8). The
reason is a stable code plus a fixed English sentence; no upstream text, header or token ever
reaches it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
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


class ServerEntry(BaseModel):
    hostname: str | None = None
    status: str | None = None


class ServersValue(BaseModel):
    """`GET /system/servers`: managed hosts and the status QRadar reports for each."""

    total: int
    active: int
    #: Hosts whose entry carried no status. Counted apart, never as active.
    unreported: int
    by_status: dict[str, int]
    #: Hosts not reporting `ACTIVE`, at most 20, so the section stays small on a big deployment.
    not_active: list[ServerEntry] = Field(default_factory=list)
    all_active: bool


class EpsValue(BaseModel):
    """`GET /config/deployment/hosts`, summed: observed event rate against the licensed rate."""

    hosts: int
    average_eps: int
    peak_eps: int
    #: The hosts' EPS allocation summed.
    allocated_eps: int
    #: `average_eps / allocated_eps`; None when the allocation is 0.
    average_share: float | None = None


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
    #: QRadar's own count of search ids it holds (all users). Not "running": the list does
    #: not say which are running, and asking each one would be a query per search.
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
    eps: HealthField[EpsValue]
    license: HealthField[LicenseValue]
    ariel: ArielSection
