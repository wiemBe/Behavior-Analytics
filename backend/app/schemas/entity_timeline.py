"""Read-only entity timeline response shapes.

The timeline is a projection of stored, bounded evidence.  Each lane keeps its
own honesty state so an empty, measured result remains distinct from a family
that was disabled, stale, bounded, or unavailable to the caller.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models.enums import Severity
from app.schemas.messages import MessageParamValue
from app.schemas.related_activity import RelatedSectionState


class EntityTimelineKind(StrEnum):
    IP = "IP"
    HOST = "HOST"
    USER = "USER"


class EntityTimelineLane(StrEnum):
    EXPLANATION = "EXPLANATION"
    WAF_TOP = "WAF_TOP"
    IPS_TOP = "IPS_TOP"
    IPS_ENTITY = "IPS_ENTITY"
    OFFENSE = "OFFENSE"
    LOG_SOURCE = "LOG_SOURCE"
    CATCHALL = "CATCHALL"
    PW_VALUE = "PW_VALUE"
    IDENTITY_TOP = "IDENTITY_TOP"


class EntityOut(BaseModel):
    kind: EntityTimelineKind
    value: str
    value_norm: str


class EntityWindowOut(BaseModel):
    start: datetime
    end: datetime


class EntityTimelineLaneOut(BaseModel):
    source: EntityTimelineLane
    state: RelatedSectionState
    reason_code: str | None = None
    total: int | None = Field(default=None, ge=0)
    #: Present when the lane is hidden because the caller lacks its own grant.
    permission: str | None = None

    @model_validator(mode="after")
    def _reason_matches_state(self) -> Self:
        if self.state is RelatedSectionState.OK and self.reason_code is not None:
            raise ValueError("an OK lane carries no reason code")
        if self.state is not RelatedSectionState.OK and not self.reason_code:
            raise ValueError("a lane that is not OK says why")
        return self


class EntityTimelineItemOut(BaseModel):
    occurred_at: datetime
    occurred_end: datetime | None = None
    source: EntityTimelineLane
    role: str
    title: str
    title_code: str | None = None
    title_params: dict[str, MessageParamValue] | None = None
    href: str
    #: Phase A incidents use the named severity vocabulary. QRadar offense rows
    #: carry their native numeric severity, so the wire field accepts either.
    severity: Severity | int | None = None
    measurement: str | None = None


class EntityTimelineOut(BaseModel):
    entity: EntityOut
    window: EntityWindowOut
    state: RelatedSectionState
    reason_code: str | None = None
    lanes: list[EntityTimelineLaneOut]
    items: list[EntityTimelineItemOut]
    dangling: int = Field(ge=0)
    truncated: bool
    next_cursor: str | None = None

    @model_validator(mode="after")
    def _page_contract(self) -> Self:
        if self.state is RelatedSectionState.OK and self.reason_code is not None:
            raise ValueError("an OK page carries no reason code")
        if self.state is not RelatedSectionState.OK and not self.reason_code:
            raise ValueError("a page that is not OK says why")
        if self.truncated != (self.next_cursor is not None):
            raise ValueError("only a truncated page carries a next cursor")
        return self
