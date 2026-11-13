"""Admin API schemas for notification routing rules."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

RuleSeverity = Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"]
# String Literal on purpose. mypy does not accept a Literal unpacked from AlertFamily,
# and a Literal of the enum members rejects the strings this API receives. Drift is
# guarded by test_family_literal_is_exactly_alert_family.
FamilyName = Literal[
    "log_source",
    "waf",
    "ips",
    "property_watch",
    "rule",
    "identity",
    "catchall_sender",
    "custom_rule",
    "scheduled_search",
    "qradar_instance",
]


def _unique(values: list | None) -> list | None:
    """An empty filter means any (stored as NULL); duplicates collapse, order is kept."""
    if not values:
        return None
    return list(dict.fromkeys(values))


class _RuleFields(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @field_validator("families", "owners", "source_classes", "group_ids", check_fields=False)
    @classmethod
    def _filters(cls, value: list | None) -> list | None:
        return _unique(value)

    @field_validator("name", check_fields=False)
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("value must not be blank")
        return value

    @field_validator("destination_ids", check_fields=False)
    @classmethod
    def _destinations(cls, value: list[uuid.UUID] | None) -> list[uuid.UUID] | None:
        return None if value is None else list(dict.fromkeys(value))


class RuleCreate(_RuleFields):
    name: str = Field(min_length=1, max_length=64)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=100000)
    min_severity: RuleSeverity
    families: list[FamilyName] | None = Field(default=None, max_length=16)
    owners: list[str] | None = Field(default=None, max_length=50)
    source_classes: list[str] | None = Field(default=None, max_length=50)
    group_ids: list[int] | None = Field(default=None, max_length=100)
    window_start_minute: int | None = None
    window_end_minute: int | None = None
    destination_ids: list[uuid.UUID] = Field(min_length=1, max_length=10)
    stop_default: bool = False


class RulePatch(_RuleFields):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100000)
    min_severity: RuleSeverity | None = None
    families: list[FamilyName] | None = Field(default=None, max_length=16)
    owners: list[str] | None = Field(default=None, max_length=50)
    source_classes: list[str] | None = Field(default=None, max_length=50)
    group_ids: list[int] | None = Field(default=None, max_length=100)
    window_start_minute: int | None = None
    window_end_minute: int | None = None
    destination_ids: list[uuid.UUID] | None = Field(default=None, min_length=1, max_length=10)
    stop_default: bool | None = None

    @model_validator(mode="after")
    def _no_null_for_required(self) -> Self:
        for field in (
            "name",
            "enabled",
            "priority",
            "min_severity",
            "destination_ids",
            "stop_default",
        ):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be cleared")
        return self


class RuleDestinationOut(BaseModel):
    id: uuid.UUID
    name: str


class RuleOut(BaseModel):
    id: uuid.UUID
    name: str
    enabled: bool
    priority: int
    min_severity: str
    families: list[str] | None
    owners: list[str] | None
    source_classes: list[str] | None
    group_ids: list[int] | None
    window_start_minute: int | None
    window_end_minute: int | None
    destinations: list[RuleDestinationOut]
    stop_default: bool
    created_by: str
    created_at: datetime
    updated_by: str
    updated_at: datetime
