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

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

from app.models.enums import AnomalyType, Criticality, Severity
from app.schemas.messages import MessageParamValue


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
