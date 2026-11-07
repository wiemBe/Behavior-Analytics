"""Triage queue response shapes (SOC-4b, design `SOC-4.design.md` §3).

One list of unfinished work from five sources, plus one status per source. A source that
could not be read says so (`FAILED`, `FORBIDDEN`, `NOT_COLLECTED`, `STALE`, `PARTIAL`); it is
never a row count of zero. `count` and `new_count` are null whenever the source is not known
to have been counted.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from app.models.enums import (
    AlertStatus,
    AnomalyFamily,
    Severity,
    TriageStatus,
)
from app.schemas.messages import MessageParamValue


class QueueKind(StrEnum):
    ANOMALY = "anomaly"
    ALERT = "alert"
    OFFENSE = "offense"
    SOURCE_REVIEW = "source_review"
    CATCHALL_SENDER = "catchall_sender"


class QueueScope(StrEnum):
    MINE = "mine"
    UNASSIGNED = "unassigned"
    ALL = "all"


class QueueChange(StrEnum):
    NEW = "NEW"
    UPDATED = "UPDATED"


class SourceState(StrEnum):
    OK = "OK"
    FORBIDDEN = "FORBIDDEN"
    FAILED = "FAILED"
    NOT_COLLECTED = "NOT_COLLECTED"
    STALE = "STALE"
    PARTIAL = "PARTIAL"


class CompareKind(StrEnum):
    #: No `since` was sent: the last 24 hours count as new.
    DEFAULT_WINDOW = "DEFAULT_WINDOW"
    #: The caller named the comparison instant.
    OVERRIDE = "OVERRIDE"
    #: The caller's stored marker (SOC-4a), used when no `since` was sent.
    STORED = "STORED"


class QueueTriageOut(BaseModel):
    status: TriageStatus
    version: int


class QueueAlertChip(BaseModel):
    id: uuid.UUID
    status: AlertStatus


class QueueRow(BaseModel):
    kind: QueueKind
    #: Set for anomaly rows only.
    family: AnomalyFamily | None
    #: Anomaly, alert, source and sender: a UUID. Offense: `<instance_id>:<qradar_offense_id>`.
    id: str
    title: str
    title_code: str | None
    title_params: dict[str, MessageParamValue] | None
    #: Anomaly: the monitored thing (source, endpoint, entity, watch value). Null otherwise.
    subject: str | None
    #: Anomaly only: the detector type value.
    detector: str | None
    severity: Severity | None
    #: Offense only: QRadar's raw 0-10 magnitude, which `severity` is banded from.
    magnitude: int | None
    #: Alert only.
    occurrence_count: int | None
    #: Detector state, alert status, offense status or review status.
    state: str
    triage: QueueTriageOut | None
    owner: str | None
    #: `app` for an anomaly or alert owner, `qradar` for an offense assignee.
    owner_kind: str | None
    waiting_since: datetime
    age_seconds: int
    new_since_last_visit: bool
    change: QueueChange | None
    alert: QueueAlertChip | None
    #: An application route. Never a QRadar URL.
    link: str


class SourceStatus(BaseModel):
    kind: QueueKind
    state: SourceState
    #: Rows waiting under the applied filters. Null unless the source was counted.
    count: int | None
    #: Of those, rows flagged NEW or UPDATED against the comparison instant.
    new_count: int | None
    #: The permission a FORBIDDEN source needs.
    permission: str | None
    #: Freshness of the data, for STALE (the oldest last collection run).
    as_of: datetime | None
    #: Closed set, see `app/services/queue.py` (`REASON_*`).
    reason_code: str | None


class QueueMarker(BaseModel):
    #: Rows after this instant are NEW; rows changed after it, by someone else, are UPDATED.
    compare_from: datetime
    kind: CompareKind
    #: The caller's stored marker whatever `kind` is; the `expected` of the next advance.
    #: Null when none is stored (or no source is readable by the caller).
    marked_at: datetime | None = None


class QueueFiltersApplied(BaseModel):
    scope: QueueScope
    kind: list[QueueKind]
    family: list[AnomalyFamily]
    severity: list[Severity]
    new_only: bool
    since: datetime | None


class QueueOut(BaseModel):
    as_of: datetime
    marker: QueueMarker
    sources: list[SourceStatus]
    #: Sum of the counted sources. Not the whole queue when `total_exact` is false.
    total: int
    total_exact: bool
    limit: int
    offset: int
    filters_applied: QueueFiltersApplied
    items: list[QueueRow]


class MarkerSeenIn(BaseModel):
    """Advance the caller's marker to the `as_of` the queue response carried."""

    model_config = ConfigDict(extra="forbid")

    as_of: datetime
    #: The `marker.marked_at` the client last read; null when it read none.
    expected: datetime | None
    #: False when any source of that response failed to load: the marker then stays.
    all_sources_ok: bool


class MarkerOut(BaseModel):
    marked_at: datetime
    previous_marked_at: datetime | None
    #: False when the request was a forward-only no-op (`as_of` not after the stored marker).
    advanced: bool
