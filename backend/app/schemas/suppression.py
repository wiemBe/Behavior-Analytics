"""API schemas for the SOC-17d suppressions (design §5.8).

The create body names only what the operator chooses: the duration, the reason,
the owner, and the two scope/kind choices. The condition itself (family,
subject, detector) is derived by the server from the alert or anomaly row, and
a client-supplied detector string is refused rather than trusted
(``extra="forbid"``).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.models.enums import AnomalyFamily, SuppressionKind

#: Design §5.1: 1, 7 or 30 days only. No "forever"; that is the alert policy's
#: RECORD_ONLY, a deliberate, visible, reviewed state rather than a timer.
SUPPRESSION_DAYS = (1, 7, 30)


class SuppressionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: int
    reason: str = Field(min_length=3, max_length=1000)
    #: The accountable principal; defaults to the caller server-side.
    owner: str | None = Field(default=None, min_length=1, max_length=255)
    #: The whole source instead of the one condition. Only the three
    #: source-anchored families may widen the scope this way (design §5.1).
    whole_source: bool = False
    kind: SuppressionKind = SuppressionKind.ANALYST
    #: A change/ticket id; required for KNOWN_CHANGE and refused otherwise.
    reference: str | None = Field(default=None, min_length=1, max_length=255)

    @field_validator("days")
    @classmethod
    def _known_duration(cls, value: int) -> int:
        if value not in SUPPRESSION_DAYS:
            allowed = ", ".join(str(day) for day in SUPPRESSION_DAYS)
            raise ValueError(f"days must be one of {allowed}")
        return value

    @model_validator(mode="after")
    def _reference_matches_kind(self) -> SuppressionCreate:
        if self.kind is SuppressionKind.KNOWN_CHANGE and self.reference is None:
            raise ValueError("a KNOWN_CHANGE suppression requires a reference")
        if self.kind is not SuppressionKind.KNOWN_CHANGE and self.reference is not None:
            raise ValueError("reference is only allowed for a KNOWN_CHANGE suppression")
        return self


class SuppressionOut(BaseModel):
    """One quiet period as the list and the banners show it.

    ``kind`` is a plain string because the list also carries the read-only
    ``MAINTENANCE`` rows (design §5.6): an active ``log_source`` maintenance
    window shown beside the suppressions, not stored in the table. Those rows
    are ``read_only``, carry no ``id`` of their own and count no hits; their
    ``starts_at`` is unknown (the window has no start column) and their
    ``ends_at`` is ``maintenance_until`` (null when the window is open-ended).
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID | None = None
    family: AnomalyFamily
    subject_id: uuid.UUID
    #: Null means every detector of the subject.
    detector_type: str | None
    kind: str
    reason: str | None
    owner: str | None
    starts_at: datetime | None
    ends_at: datetime | None
    ended_at: datetime | None
    ended_by: str | None
    reference: str | None
    created_by: str | None
    created_at: datetime | None
    updated_at: datetime | None
    #: The subject's current name; null when the source or watch is gone.
    subject_name: str | None = None
    #: True only for the synthetic MAINTENANCE rows: no End action, no hits.
    read_only: bool = False
    #: Incidents recorded while this suppression held their alerts back. Null
    #: for MAINTENANCE rows, which record none.
    hit_count: int | None = None


class BreakthroughAlertOut(BaseModel):
    """An alert opened during the window, by source and opened_at (design §5.5)."""

    id: uuid.UUID
    title: str
    severity: str
    opened_at: datetime
    occurrence_count: int


class ConditionSuppressionOut(BaseModel):
    """What an alert or anomaly detail page needs for its suppression banner.

    ``suppression`` is null when nothing is active for the condition -- an
    absence, never an invented quiet period. ``breakthrough_alerts`` follows the
    design exactly: alerts of the subject opened inside the window, read from
    ``alert`` by source and ``opened_at`` with no new column.
    """

    family: AnomalyFamily
    anomaly_id: uuid.UUID
    detector_type: str
    suppression: SuppressionOut | None
    hit_count: int
    breakthrough_alerts: list[BreakthroughAlertOut]
