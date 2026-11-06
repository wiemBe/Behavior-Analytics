"""API schemas for log sources and the SOC overview.

Response models are separate from ORM models on purpose: they never expose
internal ids-as-secrets, they carry sanitized text, and their shape is a UI
contract that can evolve independently of the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
)

from app.models.enums import AnomalyType, Criticality, Severity
from app.schemas.messages import MessageParamValue

#: The direction knob's legal values (design §3.1). A stored value outside this set keeps
#: today's behaviour (both directions) because reads are never rejected.
ThresholdDirection = Literal["both", "spike", "drop"]


class ThresholdKnobs(BaseModel):
    """The six tuning knobs of design §3.1, and the legacy `Thresholds` keys.

    Every field is optional so a PATCH may carry a subset, and the model is frozen and
    forbids unknown keys: a typo that today is silently ignored is refused, and a value
    like ``spike_ratio: 0.4`` that today would become an alert storm is refused too.
    The legacy keys stay accepted with type checks only; their bounds are not in the UI
    and this task adds none.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    # --- the six knobs the tuning page writes -------------------------------
    deviation_z: float | None = Field(default=None, ge=0.5, le=20.0)
    spike_ratio: float | None = Field(default=None, ge=1.01, le=100.0)
    drop_ratio: float | None = Field(default=None, ge=0.01, le=0.99)
    min_absolute_delta_events: float | None = Field(default=None, ge=0.0, le=1_000_000.0)
    open_after: int | None = Field(default=None, ge=1, le=10)
    direction: ThresholdDirection | None = None

    # --- every other `Thresholds` field stays legal, with its own type ------
    min_bucket_events: float | None = None
    silence_grace_buckets: int | None = None
    min_parsed_ratio: float | None = None
    max_unknown_ratio: float | None = None
    delay_interval_multiple: float | None = None
    cardinality_drop_fraction: float | None = None
    collection_error_threshold: int | None = None
    repeated_payload_intervals: int | None = None
    resolve_after: int | None = None


class ThresholdConfig(ThresholdKnobs):
    """A stored ``custom_thresholds`` map: the knobs plus their ``by_type`` overrides.

    ``by_type`` is validated against the same keys and bounds, and its anomaly-type names
    must be real ``AnomalyType`` values. This validates new writes only: an existing row
    that never passed this model is still read exactly as before.
    """

    by_type: dict[str, ThresholdKnobs] | None = None

    @field_validator("by_type")
    @classmethod
    def _known_types(cls, value: dict[str, ThresholdKnobs] | None) -> dict[
        str, ThresholdKnobs
    ] | None:
        if value is None:
            return None
        unknown = sorted(set(value) - {kind.value for kind in AnomalyType})
        if unknown:
            raise ValueError(f"unknown anomaly type: {', '.join(unknown)}")
        return value


def thresholds_error(exc: ValidationError) -> str:
    """One readable sentence from a ``ThresholdConfig`` refusal, for the 422 detail."""
    first = exc.errors()[0]
    location = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", "invalid threshold"))
    return f"custom_thresholds.{location}: {message}" if location else message


class PreviewBlock(BaseModel):
    """The preview a tuning PATCH showed, recorded in the audit detail (design §3.6).

    Audit only: the route never applies it. It is validated so a malformed block is
    refused like any other body, but it changes nothing that is saved.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    at: datetime
    window: int = Field(ge=1, le=365)
    current: int | None = Field(default=None, ge=0)
    proposed: int | None = Field(default=None, ge=0)
    state: Literal["COMPLETE", "PARTIAL", "INSUFFICIENT_DATA", "TOO_LARGE"]


class LogSourceSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    qradar_id: int
    name: str
    type_name: str | None
    criticality: Criticality
    owner: str | None
    enabled: bool
    monitoring_enabled: bool
    maintenance_mode: bool
    qradar_status: str | None
    last_event_time: datetime | None
    open_anomaly_count: int = 0


class LogSourceDetail(LogSourceSummary):
    description: str | None
    type_id: int | None
    protocol_type_id: int | None
    credibility: int | None
    owner_email: str | None
    expected_interval_seconds: int | None
    business_hours_only: bool
    business_hours_start: int
    business_hours_end: int
    business_days: list[int]
    timezone_name: str
    custom_thresholds: dict


class LogSourceUpdate(BaseModel):
    """Operator-editable SOC metadata. QRadar-mirrored fields are read-only."""

    criticality: Criticality | None = None
    owner: str | None = Field(default=None, max_length=255)
    owner_email: str | None = Field(default=None, max_length=255)
    monitoring_enabled: bool | None = None
    maintenance_mode: bool | None = None
    maintenance_until: datetime | None = None
    maintenance_reason: str | None = Field(default=None, max_length=1000)
    expected_interval_seconds: int | None = Field(default=None, ge=1)
    # Validated before business_days so that field can see this value.
    business_hours_only: bool | None = None
    business_hours_start: int | None = Field(default=None, ge=0, le=23)
    business_hours_end: int | None = Field(default=None, ge=0, le=23)
    business_days: list[int] | None = None
    timezone_name: str | None = Field(default=None, max_length=64)
    custom_thresholds: dict | None = None
    #: Audit-only preview a tuning PATCH showed; never applied (design §3.6).
    preview: PreviewBlock | None = None

    @field_validator("custom_thresholds")
    @classmethod
    def _thresholds(cls, value: dict | None) -> dict | None:
        """Refuse unknown keys and out-of-bounds values on new writes.

        The original dict is returned unchanged, so this validation does not rewrite what
        is stored (an int stays an int, and a subset stays a subset).
        """
        if value is None:
            return None
        try:
            ThresholdConfig.model_validate(value)
        except ValidationError as exc:
            raise ValueError(thresholds_error(exc)) from exc
        return value

    @field_validator("timezone_name")
    @classmethod
    def _timezone_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("must be a valid IANA time zone name") from exc
        return value

    @field_validator("business_days")
    @classmethod
    def _business_days(
        cls, value: list[int] | None, info: ValidationInfo
    ) -> list[int] | None:
        """ISO weekdays 1-7. Empty is allowed only while business hours are off.

        `business_hours_only` is declared above, so a payload that sets both is
        judged here. A PATCH that sets only one of them is judged on the route,
        against the row already stored.
        """
        if value is None:
            return None
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("items must be ISO weekdays from 1 to 7")
        if info.data.get("business_hours_only") is True and len(value) == 0:
            raise ValueError(
                "must list at least one ISO weekday from 1 to 7 when "
                "business_hours_only is true"
            )
        return value


def business_days_usable(days: object) -> bool:
    """True when `days` is a non-empty list of ISO weekdays 1-7.

    The route uses this on the merged row. Existing rows are not rewritten by
    a migration, so a value that never passed the schema can still be stored.
    """
    if not isinstance(days, list) or len(days) == 0:
        return False
    return all(type(day) is int and 1 <= day <= 7 for day in days)


class AnomalySummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    anomaly_type: AnomalyType
    severity: Severity
    detected_at: datetime
    resolved_at: datetime | None
    explanation: str | None
    #: Stable code and raw values of `explanation` for a non-English client. Null: no code.
    explanation_code: str | None = None
    explanation_params: dict[str, MessageParamValue] | None = None
    suppressed: bool


class SyncResult(BaseModel):
    provider: str
    log_sources_seen: int
    created: int
    updated: int
    instance_version: str | None = None


# --- SOC overview ----------------------------------------------------------
class OverviewCounts(BaseModel):
    total_log_sources: int
    monitored_log_sources: int
    silent_log_sources: int
    anomalous_log_sources: int
    in_maintenance: int
    #: Sanitized collection state. The provider error itself is never exposed.
    inventory_state: Literal["NOT_COLLECTED", "COMPLETE", "FAILED"]


class OverviewOffenses(BaseModel):
    #: None when no offense snapshot has been collected. Zero means a snapshot
    #: exists and none of its offenses is open.
    active: int | None
    critical: int | None
    critical_unknown: int | None
    unassigned: int | None
    oldest_age_seconds: int | None = None


class OverviewAlerts(BaseModel):
    open: int
    acknowledged: int
    by_severity: dict[str, int] = Field(default_factory=dict)


class SocOverview(BaseModel):
    instance_status: str
    instance_version: str | None
    #: When the instance's status was last checked, and None when it was
    #: never checked or no instance exists.
    instance_last_health_check_at: datetime | None = None
    generated_at: datetime
    log_sources: OverviewCounts
    offenses: OverviewOffenses
    alerts: OverviewAlerts
