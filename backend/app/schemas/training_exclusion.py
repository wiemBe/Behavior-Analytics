"""Request and response shapes for baseline training exclusions (SOC-13b)."""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ExclusionState(StrEnum):
    """The effect of one exclusion on its baseline."""

    #: Active and inside the lookback; training has not been shown to apply it yet.
    PENDING_REBUILD = "PENDING_REBUILD"
    #: Reserved for SOC-13c, which records the rebuild that applied it.
    APPLIED = "APPLIED"
    #: Active but entirely older than the baseline lookback: it changes nothing.
    OUTSIDE_LOOKBACK = "OUTSIDE_LOOKBACK"
    REVOKED = "REVOKED"


class ExclusionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    starts_at: datetime
    ends_at: datetime
    reason: str


class ExclusionRevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str


class ExclusionOut(BaseModel):
    id: uuid.UUID
    origin_family: str
    origin_anomaly_id: uuid.UUID
    subject_kind: str
    subject_id: uuid.UUID
    scope_key: str
    starts_at: datetime
    ends_at: datetime
    reason: str
    created_by: str
    created_at: datetime
    revoked_by: str | None
    revoked_at: datetime | None
    revocation_reason: str | None
    state: ExclusionState


class ExclusionListOut(BaseModel):
    items: list[ExclusionOut]
    limit: int
