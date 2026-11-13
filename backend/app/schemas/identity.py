"""Identity source reads and profile writes. Counts are null when not measured."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class IdentityProfileIn(BaseModel):
    """A mapping. Field text is checked in the handler, which does not echo it."""

    event_id_property: str
    username_property: str
    logon_type_property: str | None = None
    enabled_event_ids: list[int]
    product_label: str | None = None


class IdentityProfileOut(BaseModel):
    id: uuid.UUID
    log_source_id: uuid.UUID
    instance_id: uuid.UUID
    qradar_log_source_id: int
    log_source_name: str
    enabled: bool
    profile_version: int
    product_label: str | None = None
    event_id_property: str
    username_property: str
    logon_type_property: str | None = None
    enabled_event_ids: list[int]
    validation_status: str
    validated_profile_version: int | None = None
    validated_at: datetime | None = None
    validation_report: dict[str, object] = Field(default_factory=dict)


class IdentityCollectionOut(BaseModel):
    """The instance watermark for identity collection. A missing row is null, not zero."""

    watermark_at: datetime | None = None
    last_run_at: datetime | None = None
    lag_seconds: int | None = None
    consecutive_failures: int | None = None
    last_error: str | None = None


class IdentityBucketSummaryOut(BaseModel):
    bucket_start: datetime
    bucket_seconds: int
    measurement: str
    population: int | None = None
    admission_truncated: bool


class IdentitySourceOut(BaseModel):
    profile: IdentityProfileOut
    collection: IdentityCollectionOut
    last_bucket: IdentityBucketSummaryOut | None = None


class IdentityStoredNameOut(BaseModel):
    rank: int
    username: str
    count: int
    truncated: bool


class IdentitySeriesOut(BaseModel):
    """One catalog event ID in one bucket. `event_count` is null when it was not measured."""

    event_id: int
    bucket_start: datetime
    event_count: int | None = None
    measurement: str | None = None
    names_measurement: str | None = None
    admission_truncated: bool | None = None
    usernames: list[IdentityStoredNameOut] = Field(default_factory=list)


class IdentitySourceDetailOut(BaseModel):
    profile: IdentityProfileOut
    series: list[IdentitySeriesOut]
