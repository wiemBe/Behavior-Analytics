"""API schemas for the Phase C.2 outage alert policy.

The mode and grace are the operator's whole control surface. A request that
names an unknown mode, a grace outside 0-48, or an unknown class is a 422; a
request against a source that does not exist is a 404.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OutagePolicyMode, OutagePolicyScope


class OutagePolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    instance_id: uuid.UUID | None
    scope_kind: OutagePolicyScope
    scope_value: str
    mode: OutagePolicyMode
    silence_grace_buckets: int | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    #: For a CLASS row, monitored sources the row currently governs; None for a
    #: SOURCE row, where the answer is the one source itself.
    governed_monitored_sources: int | None = None


class OutagePolicyUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: OutagePolicyMode
    # Bounded here as well as by the table CHECK, so the API returns 422 rather
    # than surfacing an IntegrityError.
    silence_grace_buckets: int | None = Field(default=None, ge=0, le=48)
    instance_id: uuid.UUID | None = None
