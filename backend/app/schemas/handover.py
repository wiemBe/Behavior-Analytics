"""Request and response shapes for the immutable shift handover log."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import AnomalyFamily, Severity
from app.schemas.messages import MessageParamValue
from app.schemas.queue import QueueChange, QueueKind, SourceState, SourceStatus


class HandoverWatchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: QueueKind
    id: str


class HandoverCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = None
    watch: list[HandoverWatchIn] = Field(default_factory=list)


class HandoverItem(BaseModel):
    kind: QueueKind
    family: AnomalyFamily | None
    id: str
    link: str
    state: str
    owner: str | None
    owner_kind: str | None
    severity: Severity | None
    waiting_since: datetime
    age_seconds: int
    change: QueueChange | None
    watch: bool
    over_sla: bool | None
    title: str
    title_code: str | None
    title_params: dict[str, MessageParamValue] | None


class ActedOnItem(BaseModel):
    kind: str
    id: str
    related_id: str
    link: str
    event: str
    at: datetime
    from_value: str | None
    to_value: str | None


class ActedOnSource(BaseModel):
    kind: str
    state: SourceState
    count: int | None
    permission: str | None
    reason_code: str | None


class HandoverBody(BaseModel):
    as_of: datetime
    window_started_at: datetime
    sources: list[SourceStatus]
    total: int
    total_exact: bool
    truncated: bool
    items: list[HandoverItem]
    acted_on: list[ActedOnItem]
    acted_on_sources: list[ActedOnSource]
    watch_dropped: list[HandoverWatchIn]


class HandoverOut(BaseModel):
    id: uuid.UUID
    author: str
    written_at: datetime
    window_started_at: datetime
    previous_id: uuid.UUID | None
    note: str
    body: HandoverBody


class HandoverLatestOut(BaseModel):
    handover: HandoverOut | None


class HandoverSummary(BaseModel):
    id: uuid.UUID
    author: str
    written_at: datetime
    window_started_at: datetime
    previous_id: uuid.UUID | None
    note_length: int
    note_excerpt: str


class HandoverListOut(BaseModel):
    items: list[HandoverSummary]
    total: int
    limit: int
    offset: int
