"""API schemas for the SOC-17c alert policy and the digest status.

The policy body names the complete identity of one row: instance (NULL is
global), scope, family, detector (NULL is "all detectors of the family") and
mode. The API validates the detector against the family's own enum, never a SQL
enum (AGENTS.md rule 6).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    AlertDigestState,
    AlertPolicyMode,
    AlertPolicyScope,
    AnomalyFamily,
    SourceClass,
)


class AlertPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instance_id: uuid.UUID | None
    scope_kind: AlertPolicyScope
    scope_value: str
    family: AnomalyFamily
    detector_type: str | None
    mode: AlertPolicyMode
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    #: Instance name for an instance-scoped row; null for a global row.
    instance_name: str | None = None
    #: The governed log source's or watch's name; null for a CLASS row and for a
    #: source that no longer exists. Never a guess from the scope value.
    source_name: str | None = None
    #: The governed log source's effective class; null for a CLASS row, a
    #: property watch and a source that no longer exists.
    source_class: SourceClass | None = None


class AlertPolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instance_id: uuid.UUID | None = None
    scope_kind: AlertPolicyScope
    scope_value: str = Field(min_length=1, max_length=128)
    family: AnomalyFamily
    detector_type: str | None = Field(default=None, max_length=40)
    mode: AlertPolicyMode


class DigestRunOut(BaseModel):
    """One recorded digest run. ``item_count`` is the number of source rows."""

    model_config = ConfigDict(from_attributes=True)

    instance_id: uuid.UUID
    instance_name: str | None = None
    digest_date: date
    state: AlertDigestState
    item_count: int
    recipient_count: int
    error_code: str | None
    sent_at: datetime | None
    created_at: datetime


class DigestInstanceStatusOut(BaseModel):
    instance_id: uuid.UUID
    instance_name: str
    last_run: DigestRunOut | None


class DigestStatusOut(BaseModel):
    """What the policy page's digest section needs, without inventing a value.

    ``last_run`` is the most recent row across instances; ``instances`` repeats
    it per instance so a multi-console site can see each one. A null run means
    "no digest has run yet", never a zero.
    """

    digest_hour_utc: int
    email_configured: bool
    #: Current union of the environment list and enabled stored recipients.
    recipient_count: int
    last_run: DigestRunOut | None
    instances: list[DigestInstanceStatusOut]
