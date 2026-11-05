"""API schemas for anomalies, alerts and notifications."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.alerts.query import AlertFamily, AlertSort, SortOrder
from app.models.enums import (
    AlertStatus,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from app.schemas.messages import MessageParamValue


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    fingerprint: str
    title: str
    title_code: str | None
    title_params: dict[str, MessageParamValue] | None
    description: str | None
    description_code: str | None
    description_params: dict[str, MessageParamValue] | None
    severity: Severity
    status: AlertStatus
    source_type: str
    source_id: uuid.UUID | None
    opened_at: datetime
    first_seen_at: datetime
    last_seen_at: datetime | None
    acknowledged_at: datetime | None
    acknowledged_by: str | None
    resolved_at: datetime | None
    resolved_by: str | None
    resolution_reason: str | None
    resolution_reason_code: str | None
    resolution_reason_params: dict[str, MessageParamValue] | None
    occurrence_count: int
    evidence_snapshot: dict
    source_anomaly_ids: list[str]


class AlertFiltersApplied(BaseModel):
    """Every filter the server applied, resolved, so a client reproduces the view.

    A time bound is null when the request asked for no window: the echo never
    shows a default range the caller did not send, and never hides one it did.
    """

    status: AlertStatus | None
    severity: list[Severity]
    log_source_id: list[uuid.UUID]
    family: AlertFamily | None
    q: str | None
    since: datetime | None
    until: datetime | None


class AlertPage(BaseModel):
    """Offset pagination with an explicit total, matching the other list APIs."""

    items: list[AlertOut]
    total: int
    limit: int
    offset: int
    filters_applied: AlertFiltersApplied
    sort: AlertSort
    order: SortOrder


class AlertFacetValue(BaseModel):
    """One chip: a closed value of a facet and how many alerts the filters leave."""

    value: str
    count: int


class AlertFacetsOut(BaseModel):
    """The filtered counts a filter bar draws its chips from.

    Every closed value is present, in a stable order, even when its count is
    zero: zero is a measured count under the same filters, and a missing value
    would make the client guess.
    """

    severity: list[AlertFacetValue]
    family: list[AlertFacetValue]
    status: list[AlertFacetValue]
    total: int
    filters_applied: AlertFiltersApplied


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    alert_id: uuid.UUID
    channel: NotificationChannel
    target: str
    transition: str
    status: NotificationStatus
    attempts: int
    max_attempts: int
    next_attempt_at: datetime | None
    sent_at: datetime | None
    error_message: str | None


class AlertActionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
