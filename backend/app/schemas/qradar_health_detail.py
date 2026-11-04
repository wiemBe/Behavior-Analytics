"""The stored QRadar health in detail (D-42): the latest window and the history, per console.

Every part carries its own state. `OK` and `STALE` hold a value (a stale one is the newest
stored, older than the cadence allows, shown with its window); any other state holds none and
says why with a stable code plus an English sentence. A window that was not collected is
NOT_COLLECTED, never a zero (repository rule 8).
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, Field, model_validator


class PartState(StrEnum):
    #: Collected whole, inside the cadence.
    OK = "OK"
    #: Collected, but some rows were missing (a search hit its row cap).
    PARTIAL = "PARTIAL"
    #: Collected, but the newest window is older than the cadence allows.
    STALE = "STALE"
    #: Never asked, or not answerable (a missing property, no window yet).
    NOT_COLLECTED = "NOT_COLLECTED"
    #: Asked and failed.
    FAILED = "FAILED"


_VALUED = (PartState.OK, PartState.PARTIAL, PartState.STALE)


class PartStatus(BaseModel):
    """The state of one part of a console's window, with the reason when it holds no value."""

    state: PartState
    reason_code: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _reason_when_not_clean(self) -> Self:
        if self.state is PartState.OK:
            return self
        if not self.reason_code or not self.reason:
            raise ValueError("a part that is not OK says why")
        return self


class MetricCell(BaseModel):
    """One figure of one host: the window's maximum and average, or why there is none."""

    state: PartState
    #: The window's maximum, in the metric's own unit (see the field it sits in).
    value: float | None = None
    average: float | None = None
    samples: int | None = None
    reason_code: str | None = None
    reason: str | None = None

    @model_validator(mode="after")
    def _value_only_when_read(self) -> Self:
        if self.state in _VALUED:
            if self.value is None:
                raise ValueError("a read cell carries a value")
        else:
            if self.value is not None or self.average is not None or self.samples is not None:
                raise ValueError("a cell that was not read carries no value")
            if not self.reason_code or not self.reason:
                raise ValueError("a cell that was not read says why")
        return self


class MountValue(BaseModel):
    """One allowlisted mount of a host."""

    mount: str
    #: `DiskUsage`, 0 to 1 (the window's maximum). None when that metric had no value.
    used_fraction: float | None = None
    used_percent: float | None = None
    used_bytes: float | None = None
    total_bytes: float | None = None
    #: End of the earlier window the total was carried from, when this window held none (QRadar
    #: sends the total once an hour). None when the total is this window's own, or absent. One
    #: field, not a flag: the page and doctor need the time, and a time says "carried" by itself.
    total_from: datetime | None = None
    #: `total - used`, only when both bytes figures were read (the total possibly carried).
    free_bytes: float | None = None
    #: OK, WARN or FAIL against the configured thresholds; None when `used_fraction` is None.
    level: str | None = None


class HostDisk(BaseModel):
    state: PartState
    reason_code: str | None = None
    reason: str | None = None
    #: The fullest allowlisted mount, by `used_fraction`.
    fullest: MountValue | None = None
    mounts: list[MountValue] = Field(default_factory=list)


class StageValue(BaseModel):
    stage: str
    #: Percent busy, 0 to 100 (the window's average of the RUNNABLE share, D-43).
    busy_percent: float


class HostPipeline(BaseModel):
    #: Events waiting for the rule engine.
    cre_queue: MetricCell
    #: Bytes of the license-throttle queue spilled to disk (the host is over its EPS allocation).
    throttle_spill_bytes: MetricCell
    #: True when the spill is above zero in the window; None when it was not read.
    throttle_spilling: bool | None = None
    ect_tcp_to_ep_disk_bytes: MetricCell
    flow_governor_queue_disk_bytes: MetricCell
    #: The busiest pipeline stage; None when no stage was read.
    busiest_stage: StageValue | None = None


class HostSystem(BaseModel):
    cpu_percent: MetricCell
    memory_percent: MetricCell
    load_avg_5: MetricCell
    swap_percent: MetricCell
    io_wait: MetricCell
    running_queries: MetricCell
    host_running_queries: MetricCell


class HostHealth(BaseModel):
    #: The hostname parsed from the log-source name, as QRadar names it.
    host: str
    disk: HostDisk
    system: HostSystem
    pipeline: HostPipeline


class NotificationEntry(BaseModel):
    host: str
    qid_name: str
    events: int
    max_severity: int | None = None
    last_seen: datetime | None = None


class NotificationRemainder(BaseModel):
    """The groups below the busiest ones, summed so the window's events add up."""

    events: int
    groups: int


class NotificationsValue(BaseModel):
    entries: list[NotificationEntry] = Field(default_factory=list)
    remainder: NotificationRemainder | None = None
    #: Set only when the caller asked for a range (`notifications_hours`): the range, and what
    #: the stored windows inside it cover. An empty `entries` with `windows` > 0 is a measured
    #: "nothing", and `windows` = 0 never reaches this value (the list is then absent).
    hours: int | None = None
    #: Stored windows in the range whose notification search was measured (OK or PARTIAL).
    windows: int | None = None
    #: Start of the first and end of the last of those windows.
    covered_from: datetime | None = None
    covered_until: datetime | None = None


class RestSampleOut(BaseModel):
    """The newest stored REST sample of the console."""

    sampled_at: datetime
    reachable: bool | None = None
    api_latency_ms: int | None = None
    hosts_active: int | None = None
    hosts_total: int | None = None
    ha_problem_count: int | None = None
    license_overallocated: bool | None = None


class WindowOut(BaseModel):
    start: datetime
    end: datetime
    collected_at: datetime
    cadence_seconds: int


class ConsoleLatest(BaseModel):
    instance_id: UUID
    instance_name: str
    #: The newest window any run stored; None when the console has no run yet.
    window: WindowOut | None = None
    metrics: PartStatus
    notifications: PartStatus
    hosts: list[HostHealth] = Field(default_factory=list)
    notification_list: NotificationsValue | None = None
    #: End of the newest window whose metric part was collected, even when a later run failed.
    last_metrics_window_end: datetime | None = None
    #: Counted, never shown: log-source names without a hostname, mounts and metrics outside
    #: the allowlists, rows without a usable value.
    unparseable_hosts: int = 0
    dropped_mounts: int = 0
    dropped_metrics: int = 0
    null_values: int = 0
    invalid_values: int = 0
    dropped_events: int = 0
    rest_sample: RestSampleOut | None = None


class LatestOut(BaseModel):
    generated_at: datetime
    #: False when `QRADAR_HEALTH_COLLECTION_ENABLED` is off: the windows below are then the last
    #: ones collected, not a current reading. They are kept, with their own times.
    collection_enabled: bool = True
    consoles: list[ConsoleLatest]


class HistoryPoint(BaseModel):
    at: datetime
    value: float
    #: The mount behind the value, for the fullest-mount series only.
    mount: str | None = None
    #: Stored windows folded into this point (its value is their maximum).
    windows: int = 1


class HistorySeries(BaseModel):
    state: PartState
    reason_code: str | None = None
    reason: str | None = None
    points: list[HistoryPoint] = Field(default_factory=list)


class HostHistory(BaseModel):
    host: str
    #: The fullest allowlisted mount's `DiskUsage` (0 to 1), per window.
    disk_fullest_fraction: HistorySeries
    cre_queue: HistorySeries
    throttle_spill_bytes: HistorySeries


class ReachabilityPoint(BaseModel):
    at: datetime
    reachable: bool | None = None
    api_latency_ms: int | None = None


class ReachabilitySeries(BaseModel):
    state: PartState
    reason_code: str | None = None
    reason: str | None = None
    points: list[ReachabilityPoint] = Field(default_factory=list)


class ConsoleHistory(BaseModel):
    instance_id: UUID
    instance_name: str
    metrics: PartStatus
    hosts: list[HostHistory] = Field(default_factory=list)
    reachability: ReachabilitySeries


class HistoryOut(BaseModel):
    generated_at: datetime
    hours: int
    since: datetime
    until: datetime
    #: A gap longer than this between two points means windows are missing, not that the
    #: value held.
    expected_interval_seconds: int
    consoles: list[ConsoleHistory]
