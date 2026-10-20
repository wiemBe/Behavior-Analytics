"""API contracts for operator-managed QRadar search cadence."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel

from app.models.enums import SearchCadence, SearchFamily


class SearchCadenceUpdate(BaseModel):
    cadence_seconds: SearchCadence


class SearchCadenceLastRunOut(BaseModel):
    at: datetime
    outcome: str | None
    searches_sent: int | None
    buckets_collected: int | None
    split_count: int | None


class CadenceOut(BaseModel):
    """The timing fields another API needs to state its collection delay."""

    family: SearchFamily
    cadence_seconds: int
    bucket_seconds: int | None
    settle_seconds: int


class SearchCadenceOut(CadenceOut):
    allowed_cadences_seconds: list[int]
    last_slot_start: datetime | None
    next_dispatch_at: datetime
    updated_at: datetime
    updated_by: str | None
    last_run: SearchCadenceLastRunOut | None
