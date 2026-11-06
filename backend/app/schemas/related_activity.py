"""Related activity around one anomaly: offenses, other anomalies and rules.

The read is a point-in-time projection of stored data, not a new collection. Every
section carries its own state, because "nothing happened" and "nothing was collected"
must not look the same: an empty list is only an answer when the state is `OK` or
`TRUNCATED`. A section that could not be resolved says so with a stable reason code and
an English sentence, and never draws a zero for a missing measurement.

`matched_events` follows the same rule: null with `measurement = NOT_COLLECTED` means
no measured window proved anything about that rule, which is not a measured zero.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Self

from pydantic import BaseModel, Field, model_validator

from app.models.enums import AnomalyFamily, AnomalyState, Severity, TriageStatus


class RelatedPad(StrEnum):
    """The padding added on each side of the anomaly's own interval."""

    THIRTY_MINUTES = "30m"
    ONE_HOUR = "1h"
    TWO_HOURS = "2h"
    SIX_HOURS = "6h"


class RelatedSectionState(StrEnum):
    """What one section of the response can say about its window."""

    #: The input was collected (or the local tables are current) and is complete.
    OK = "OK"
    #: The input for this window was never collected.
    NOT_COLLECTED = "NOT_COLLECTED"
    #: The input exists but is older than its collection cadence allows.
    STALE = "STALE"
    #: Some input was collected and some was not; per-item fields say which.
    PARTIAL = "PARTIAL"
    #: The window holds more rows than the response returns; `total` is the full count.
    TRUNCATED = "TRUNCATED"
    #: The anchor has no log source, so the section does not apply.
    NOT_APPLICABLE = "NOT_APPLICABLE"


class RelatedMeasurement(StrEnum):
    """What one rule's `matched_events` count proves."""

    #: Every matching event in the window was measured.
    EXACT = "EXACT"
    #: The count is a lower bound: the window was truncated, failed in part, or the
    #: rule type is only partly measured by an event search.
    LOWER_BOUND = "LOWER_BOUND"
    #: No measured window covers this rule, so the count is null, never zero.
    NOT_COLLECTED = "NOT_COLLECTED"


class RelatedAnchorOut(BaseModel):
    """The anomaly the read is anchored on, and the window it produced.

    `log_source_id` and `qradar_log_source_id` are null only for the
    `NOT_APPLICABLE` case: a property watch scoped to a type or a group has no single
    source to resolve.
    """

    family: AnomalyFamily
    anomaly_id: uuid.UUID
    log_source_id: uuid.UUID | None = None
    qradar_log_source_id: int | None = None
    log_source_name: str | None = None
    window_start: datetime
    window_end: datetime
    pad: RelatedPad


class RelatedOffenseOut(BaseModel):
    """One QRadar offense, from its newest snapshot inside the window."""

    qradar_offense_id: int
    description: str | None = None
    status: str | None = None
    magnitude: int | None = None
    severity: int | None = None
    start_time: datetime | None = None
    last_updated_time: datetime | None = None
    close_time: datetime | None = None
    offense_type_name: str | None = None
    #: How many QRadar rule ids the offense names. The ids themselves are in `rules`.
    rule_count: int
    #: When this snapshot was captured, not when the offense changed.
    captured_at: datetime


class RelatedAnomalyOut(BaseModel):
    """Another anomaly on the same source whose own interval overlaps the window.

    `family` is one of the four triage families plus `custom_rule`: a custom-rule
    breach is a finding, but it has no triage row (SOC-1a), so its `triage_status` is
    always `NEW`.
    """

    family: str
    id: uuid.UUID
    #: The family's own detector type value; `CUSTOM_RULE_BREACH` for a breach.
    type: str
    lifecycle_state: AnomalyState
    severity: Severity | None = None
    start: datetime
    end: datetime
    triage_status: TriageStatus


class RelatedRuleOut(BaseModel):
    """One QRadar rule named by at least one matching offense.

    A rule id QRadar named but the catalog lacks is still listed, with `id`, `name`
    and `type` null and its offense count intact: dropping it would understate what
    fired.
    """

    id: uuid.UUID | None = None
    qradar_id: int
    name: str | None = None
    type: str | None = None
    related_offenses: int
    matched_events: int | None = None
    measurement: RelatedMeasurement


class _RelatedSection(BaseModel):
    """The fields every section shares."""

    state: RelatedSectionState
    reason_code: str | None = None
    total: int = Field(ge=0)

    @model_validator(mode="after")
    def _reason_when_not_ok(self) -> Self:
        if self.state is RelatedSectionState.OK:
            if self.reason_code is not None:
                raise ValueError("an OK section carries no reason code")
            return self
        if not self.reason_code:
            raise ValueError("a section that is not OK says why")
        return self


class RelatedOffensesSection(_RelatedSection):
    items: list[RelatedOffenseOut]


class RelatedAnomaliesSection(_RelatedSection):
    items: list[RelatedAnomalyOut]


class RelatedRulesSection(_RelatedSection):
    items: list[RelatedRuleOut]


class RelatedActivityOut(BaseModel):
    """One anomaly's related activity, with each section's honesty state.

    `state` is `NOT_APPLICABLE` when the anchor has no log source; every section is
    then `NOT_APPLICABLE` with the same reason and no items.
    """

    state: RelatedSectionState
    reason_code: str | None = None
    anchor: RelatedAnchorOut
    offenses: RelatedOffensesSection
    anomalies: RelatedAnomaliesSection
    rules: RelatedRulesSection

    @model_validator(mode="after")
    def _reason_when_not_ok(self) -> Self:
        if self.state is RelatedSectionState.OK and self.reason_code is not None:
            raise ValueError("an OK response carries no reason code")
        if self.state is not RelatedSectionState.OK and not self.reason_code:
            raise ValueError("a response that is not OK says why")
        return self
