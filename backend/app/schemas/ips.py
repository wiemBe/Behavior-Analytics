"""IPS profile administration request and response schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import IpsPreset


class IpsFieldReferenceIn(BaseModel):
    kind: Literal["NORMALIZED", "CUSTOM_PROPERTY"]
    name: str = Field(min_length=1, max_length=64)


class IpsMappingFieldOut(BaseModel):
    key: str
    label: str
    description: str
    required: bool
    used_by_collection: bool
    kinds: list[Literal["NORMALIZED", "CUSTOM_PROPERTY"]]
    normalized_options: list[str]
    default: IpsFieldReferenceIn | None = None


class IpsValueMapSchemaOut(BaseModel):
    field: str
    outcomes: list[str]


class IpsPresetOut(BaseModel):
    key: IpsPreset
    label: str
    mapping: dict[str, IpsFieldReferenceIn | None]
    action_value_map: dict[str, list[str]]


class IpsMappingSchemaOut(BaseModel):
    domain: Literal["IPS"] = "IPS"
    property_name_pattern: str
    min_coverage_ratio: float
    fields: list[IpsMappingFieldOut]
    value_maps: list[IpsValueMapSchemaOut]
    presets: list[IpsPresetOut]


class IpsMappingIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    source_ip: IpsFieldReferenceIn = Field(alias="SOURCE_IP")
    destination_ip: IpsFieldReferenceIn = Field(alias="DESTINATION_IP")
    destination_port: IpsFieldReferenceIn = Field(alias="DESTINATION_PORT")
    protocol: IpsFieldReferenceIn | None = Field(default=None, alias="PROTOCOL")
    signature: IpsFieldReferenceIn = Field(alias="SIGNATURE")
    action: IpsFieldReferenceIn | None = Field(default=None, alias="ACTION")
    severity: IpsFieldReferenceIn | None = Field(default=None, alias="SEVERITY")


class IpsActionValueMapIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    blocked: list[str] = Field(alias="BLOCKED", max_length=20)
    allowed: list[str] = Field(alias="ALLOWED", max_length=20)


class IpsProfileIn(BaseModel):
    expected_profile_version: int | None = Field(default=None, ge=1)
    product_label: str | None = Field(default=None, max_length=128)
    preset: IpsPreset | None = None
    mapping: IpsMappingIn
    action_value_map: IpsActionValueMapIn


class IpsValidationFieldOut(BaseModel):
    field: str
    reference: IpsFieldReferenceIn | None = None
    status: str
    used_by_collection: bool
    populated_events: int | None = None
    populated_ratio: float | None = None
    generic_name_events: int | None = None
    generic_name_ratio: float | None = None
    values_truncated: bool
    discovered_values: list[dict[str, Any]] = Field(default_factory=list)
    detail: str | None = None
    warnings: list[str] = Field(default_factory=list)


class IpsValidationReportOut(BaseModel):
    status: str
    reasons: list[str]
    warnings: list[str]
    total_events: int | None = None
    window_start: datetime
    window_end: datetime
    profile_version: int
    probe_truncated: bool
    ariel_searches: int
    fields: list[IpsValidationFieldOut]


class IpsProfileOut(BaseModel):
    profile_id: uuid.UUID
    instance_id: uuid.UUID
    log_source_id: uuid.UUID
    qradar_log_source_id: int
    log_source_name: str
    log_source_type_name: str | None = None
    product_label: str | None = None
    preset: IpsPreset | None = None
    profile_version: int
    enabled: bool
    mapping: IpsMappingIn
    action_value_map: IpsActionValueMapIn
    validation_status: str
    validated_profile_version: int | None = None
    validated_at: datetime | None = None
    validation: IpsValidationReportOut | None = None
    validation_pending: bool
    validation_requested_at: datetime | None = None
    validation_requested_by: str | None = None
    collection_eligible: bool
    ineligible_reason: str | None = None
    updated_by: str | None = None
    updated_at: datetime


class IpsFieldStatusOut(BaseModel):
    field: str
    status: str
    detail: str | None = None


class IpsSourceStatusOut(BaseModel):
    profile_id: uuid.UUID
    instance_id: uuid.UUID
    log_source_id: uuid.UUID
    qradar_log_source_id: int
    log_source_name: str
    log_source_type_name: str | None = None
    product_label: str | None = None
    preset: IpsPreset | None = None
    enabled: bool
    profile_version: int
    validation_status: str
    validated_profile_version: int | None = None
    validated_at: datetime | None = None
    validation_pending: bool
    validation_requested_at: datetime | None = None
    validation_requested_by: str | None = None
    #: The stored report's per-field results and top-level reasons, so the list
    #: can say why a row is INVALID or INCONCLUSIVE and not only that it is.
    validation_fields: list[IpsFieldStatusOut] = Field(default_factory=list)
    validation_reasons: list[str] = Field(default_factory=list)
    collection_eligible: bool
    ineligible_reason: str | None = None
    updated_by: str | None = None
    updated_at: datetime


class IpsCandidateOut(BaseModel):
    instance_id: uuid.UUID
    log_source_id: uuid.UUID
    qradar_id: int
    name: str
    type_name: str | None = None
    source_class: str | None = None


class IpsProfileHistoryEntryOut(BaseModel):
    at: datetime
    actor: str | None = None
    action: str
    profile_version: int | None = None
    mapping_before: dict[str, Any] | None = None
    mapping_after: dict[str, Any] | None = None
    outcome: str
    reasons: list[str] | None = None
