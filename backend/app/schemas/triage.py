"""Anomaly triage request and response shapes (SOC-1a).

Triage state is separate from the detector lifecycle, so a response carries both: the
anomaly's own `state` where it already does, and this `triage` block. A value that does
not exist is `None`, never a zero and never an invented default; the one default here is
`status = NEW`, which is exactly what the design says an anomaly with no triage row
reads as.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import (
    AnomalyFamily,
    TriageDisposition,
    TriageEventKind,
    TriageStatus,
)
from app.models.triage import COMMENT_MAX_LENGTH, DISPOSITION_NOTE_MAX_LENGTH, SUBJECT_MAX_LENGTH


class TriageSummaryOut(BaseModel):
    """The triage block on an anomaly list row and detail response.

    Always present. An anomaly with no triage row reads as `NEW`, unowned, with no
    disposition and no `updated_at`; the client does not have to distinguish "missing"
    from "not started".
    """

    status: TriageStatus = TriageStatus.NEW
    owner: str | None = None
    disposition: TriageDisposition | None = None
    updated_at: datetime | None = None


class TriageEventOut(BaseModel):
    """One row of the append-only activity log.

    `comment` holds a COMMENT's text, or the note attached to a DISPOSITION change. It
    is NULL on a NOTE event that clears the note.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    kind: TriageEventKind
    actor: str
    created_at: datetime
    from_value: str | None = None
    to_value: str | None = None
    comment: str | None = None
    triage_version: int


class PagedTriageEvents(BaseModel):
    items: list[TriageEventOut]
    total: int
    limit: int
    offset: int


class TriageOut(BaseModel):
    """The current triage state of one anomaly plus a page of its activity.

    For an anomaly with no triage row: `status` NEW, every optional field null,
    `version` 0, and an empty activity page.
    """

    family: AnomalyFamily
    anomaly_id: uuid.UUID
    status: TriageStatus
    owner: str | None = None
    disposition: TriageDisposition | None = None
    disposition_note: str | None = None
    updated_by: str | None = None
    updated_at: datetime | None = None
    #: 0 when no triage row exists. A PATCH sends this back as `expected_version`.
    version: int
    events: PagedTriageEvents


class TriagePatchIn(BaseModel):
    """A state change. Every field but `expected_version` is optional.

    A field that is absent is not changed; an explicit `null` clears `owner`,
    `disposition` or `disposition_note`. `expected_version` is required so that every
    state change is an optimistic-concurrency check, including the first one against an
    anomaly with no row (`0`).
    """

    owner: str | None = Field(default=None, max_length=SUBJECT_MAX_LENGTH)
    status: TriageStatus | None = None
    disposition: TriageDisposition | None = None
    disposition_note: str | None = Field(
        default=None, max_length=DISPOSITION_NOTE_MAX_LENGTH
    )
    expected_version: int = Field(ge=0)

    @field_validator("disposition")
    @classmethod
    def _stale_is_system_only(
        cls, value: TriageDisposition | None
    ) -> TriageDisposition | None:
        if value is TriageDisposition.STALE:
            raise ValueError("STALE is set only by the stale triage job")
        return value

    @field_validator("owner")
    @classmethod
    def _clean_owner(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None

    @field_validator("disposition_note")
    @classmethod
    def _clean_note(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        return cleaned or None


class TriageCommentIn(BaseModel):
    """A comment. Its text is bounded, non-blank, and never logged or audited."""

    text: str = Field(min_length=1, max_length=COMMENT_MAX_LENGTH)

    @field_validator("text")
    @classmethod
    def _clean_text(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("comment text must not be blank")
        return cleaned
