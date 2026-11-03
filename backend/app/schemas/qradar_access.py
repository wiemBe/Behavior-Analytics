"""API schemas for the QRadar access check (ACC-1).

Every field is a status, a code, a time or a catalog sentence. No response body, header, token
or query reaches this shape: the check never kept one.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel

AccessStatus = Literal["ok", "forbidden", "unauthorized", "not_found", "unreachable", "error"]


class CoveredCallOut(BaseModel):
    method: str
    path: str
    #: Why this call is not probed on its own.
    reason: str


class AccessEntryOut(BaseModel):
    id: str
    title: str
    method: str
    path: str
    #: What this API feeds in this application.
    feeds: str
    #: The collectors and pages that depend on it.
    used_by: list[str]
    #: Null while the entry has not been checked on this console.
    status: AccessStatus | None
    #: The status QRadar answered; null when it did not answer or nothing was checked.
    http_status: int | None
    checked_at: datetime | None
    #: The status stored by the check before the latest one.
    previous_status: AccessStatus | None
    #: Syncs the latest check started because this entry became reachable.
    syncs_started: list[str]
    #: The capability IBM documents for this API; null when IBM documents none.
    required_capability: str | None
    #: True only when `required_capability` is one IBM states for this API.
    capability_verified: bool
    #: The closest role permission IBM documents for the same data. A hint, not a fact.
    capability_hint: str | None
    #: IBM pages read for this entry.
    sources: list[str]
    covers: list[CoveredCallOut]


class ConsoleAccessOut(BaseModel):
    instance_id: uuid.UUID
    instance_name: str
    #: False for a console whose provider is not the direct REST one: nothing is checked on it.
    applicable: bool
    #: When the latest check of this console ran; null before the first.
    last_checked_at: datetime | None
    #: True while a check of this console runs (any trigger); a run that started more than ten
    #: minutes ago and never finished counts as not running.
    check_running: bool
    #: UTC; null when no check ever started on this console.
    last_check_started_at: datetime | None
    #: UTC; null when no check ever finished. Keeps the previous run's time while one runs.
    last_check_finished_at: datetime | None
    entries: list[AccessEntryOut]
    #: Syncs the latest check started, by name, over all entries.
    syncs_started: list[str]


class QRadarAccessOut(BaseModel):
    consoles: list[ConsoleAccessOut]
    #: Seconds between the scheduled checks (`QRADAR_ACCESS_CHECK_INTERVAL_SECONDS`).
    check_interval_seconds: int


class AccessCheckOut(QRadarAccessOut):
    """The answer of `POST /check` (202): the stored shape plus what the request did."""

    #: Consoles whose check this request queued.
    started: list[str]
    #: Consoles that already had a check running: nothing was queued for them.
    already_running: list[str]
    #: Consoles whose check could not be queued (the queue refused it).
    failed: list[str]
