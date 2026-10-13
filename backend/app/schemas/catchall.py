"""API schemas for the catch-all senders view (Phase C.2b).

A bucket that was not measured has no row, so a gap in `/buckets` is unknown,
never zero. `events_24h` is null when no stored top list named the sender.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.enums import CatchallMeasurement, CatchallReviewStatus


class CatchallSenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    catchall_log_source_id: uuid.UUID
    catchall_log_source_name: str
    identifier: str
    first_seen_at: datetime
    last_seen_at: datetime
    #: A lower bound from the stored top lists; null when unknown.
    events_24h: int | None
    active: bool
    review_status: CatchallReviewStatus
    reviewed_by: str | None
    reviewed_at: datetime | None


class CatchallSenderPage(BaseModel):
    items: list[CatchallSenderOut]
    total: int
    limit: int
    offset: int


class CatchallTopSender(BaseModel):
    identifier: str
    events: int


class CatchallBucketOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    catchall_log_source_id: uuid.UUID
    bucket_start: datetime
    bucket_seconds: int
    events: int
    records: int
    distinct_senders: int
    distinct_senders_lower_bound: bool
    measurement: CatchallMeasurement
    top_senders: list[CatchallTopSender]
    admitted: int
    below_admission_threshold: int
    rejected_over_quota: int
    untracked_senders: int
    untracked_events: int
    query_provenance: dict
    collected_at: datetime


class CatchallBucketList(BaseModel):
    catchall_log_source_id: uuid.UUID
    since: datetime
    until: datetime
    items: list[CatchallBucketOut]


class CatchallSourceOut(BaseModel):
    id: uuid.UUID
    name: str
    type_name: str | None
    #: The newest measured bucket; null when none was ever measured.
    latest_bucket_start: datetime | None
    new_sender_count: int


class CatchallSourceList(BaseModel):
    #: The QRadar device-type ids that count as catch-all (a setting).
    catchall_type_ids: list[int]
    items: list[CatchallSourceOut]
