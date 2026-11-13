"""API schemas for anomalies, alerts and notifications."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import datetime
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field

from app.alerts.notifiers.destination_sender import DESTINATION_TARGET_PREFIX, destination_target_id
from app.alerts.query import AlertFamily, AlertSort, SortOrder
from app.models.enums import (
    AlertStatus,
    NotificationChannel,
    NotificationStatus,
    Severity,
)
from app.schemas.messages import MessageParamValue

_HTTP_NOTIFICATION_CHANNELS = frozenset(
    {
        NotificationChannel.TEAMS,
        NotificationChannel.SLACK,
        NotificationChannel.WEBHOOK,
    }
)


def notification_target_label(
    channel: NotificationChannel,
    target: str,
    destination_names: Mapping[uuid.UUID, str] | None = None,
) -> str:
    """Return the non-secret destination label exposed by notification history.

    A `destination:<uuid>` webhook target (SOC-12d) shows the destination's name, or
    "(deleted destination)" when it no longer exists; the URL is never read.
    """
    if channel not in _HTTP_NOTIFICATION_CHANNELS:
        return target
    if channel == NotificationChannel.WEBHOOK and target.startswith(DESTINATION_TARGET_PREFIX):
        destination_id = destination_target_id(target)
        name = (destination_names or {}).get(destination_id) if destination_id else None
        return name if name is not None else "(deleted destination)"

    try:
        parsed = urlsplit(target)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return "(webhook)"

    scheme = parsed.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or hostname is None
        or any(char.isspace() or char in "/\\?#@%" for char in hostname)
    ):
        return "(webhook)"

    display_host = f"[{hostname}]" if ":" in hostname else hostname
    default_port = 80 if scheme == "http" else 443
    port_suffix = f":{port}" if port is not None and port != default_port else ""
    return f"{scheme}://{display_host}{port_suffix}"


def notification_error_message(
    channel: NotificationChannel,
    target: str,
    error_message: str | None,
) -> str | None:
    """Replace legacy HTTP target text with its non-secret display label."""
    if error_message is None or channel not in _HTTP_NOTIFICATION_CHANNELS:
        return error_message

    label = notification_target_label(channel, target)
    replacements = {target}
    try:
        parsed = urlsplit(target)
    except ValueError:
        parsed = None

    if parsed is not None:
        path = parsed.path
        query = parsed.query
        if path and query:
            replacements.add(f"{path}?{query}")
        if path and path != "/":
            replacements.add(path)
        if query:
            replacements.update({f"?{query}", query})

    sanitized = error_message
    for value in sorted((value for value in replacements if value), key=len, reverse=True):
        sanitized = sanitized.replace(value, label)
    return sanitized


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
    """The counts a filter bar draws its chips from.

    Each facet is counted with every filter of the request except its own, so a
    chip shows what choosing that value would leave rather than what already
    passes the filter it would replace. `total` is the exception: it applies
    every filter and equals the list's `total` for the same query, so a facet's
    counts add up to it only when that facet's own filter is unset.

    Every closed value is present, in a stable order, even when its count is
    zero: zero is a measured count under the other filters, and a missing value
    would make the client guess.
    """

    severity: list[AlertFacetValue]
    family: list[AlertFacetValue]
    status: list[AlertFacetValue]
    total: int
    filters_applied: AlertFiltersApplied


class NotificationAttemptOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    attempted_at: datetime
    http_status: int | None
    error_class: str


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
    #: SOC-12e: newest first, at most eight, class and status only. Not `attempts`: that
    #: name already carries the attempt count.
    attempt_log: list[NotificationAttemptOut] = Field(default_factory=list)


class AlertActionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)
