"""API schemas for custom threshold rules, previews and breaches.

Domain bounds (a ratio inside (0, 1), sustain at least 1, a known operator)
are not repeated here. F-1's ``validate_rule_draft`` reports every one of
those at once. A schema bound would stop at the first bad field and the
422 would lose the rest. ``subject_kind`` and ``severity`` stay enums: a
value outside the set is not a rule the pure function can describe.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.custom_rules.catalog import MeasurementRule
from app.models.enums import CustomRuleSubjectKind, SearchFamily, Severity
from app.schemas.messages import MessageParamValue
from app.schemas.search_cadence import CadenceOut

MeasurementLabel = Literal["EXACT", "LOWER_BOUND", "NOT_MEASURED"]


class CustomRuleCreate(BaseModel):
    """A new rule. ``hours`` is not a field: preview takes it as a query."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    subject_kind: CustomRuleSubjectKind
    log_source_id: uuid.UUID | None = None
    source_class: str | None = Field(default=None, max_length=32)
    waf_endpoint_id: uuid.UUID | None = None
    ips_entity_id: uuid.UUID | None = None
    pw_value_id: uuid.UUID | None = None
    metric: str = Field(min_length=1, max_length=32)
    operator: str = Field(min_length=1, max_length=16)
    threshold_value: float
    sustain_buckets: int
    recovery_buckets: int
    severity: Severity = Severity.MEDIUM
    enabled: bool = False


class CustomRulePreviewIn(BaseModel):
    """A draft to replay. Nothing here is stored. ``hours`` stays on the query."""

    model_config = ConfigDict(extra="forbid")

    subject_kind: CustomRuleSubjectKind
    log_source_id: uuid.UUID | None = None
    source_class: str | None = Field(default=None, max_length=32)
    waf_endpoint_id: uuid.UUID | None = None
    ips_entity_id: uuid.UUID | None = None
    pw_value_id: uuid.UUID | None = None
    metric: str = Field(min_length=1, max_length=32)
    operator: str = Field(min_length=1, max_length=16)
    threshold_value: float
    sustain_buckets: int
    recovery_buckets: int
    severity: Severity = Severity.MEDIUM
    enabled: bool = False


class CustomRuleUpdate(BaseModel):
    """A patch made against ``expected_updated_at``.

    That instant is the rule's optimistic token. ``custom_rule`` has no
    integer version column; ``updated_at`` is the one the row already carries.
    """

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime
    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, max_length=2000)
    subject_kind: CustomRuleSubjectKind | None = None
    log_source_id: uuid.UUID | None = None
    source_class: str | None = Field(default=None, max_length=32)
    waf_endpoint_id: uuid.UUID | None = None
    ips_entity_id: uuid.UUID | None = None
    pw_value_id: uuid.UUID | None = None
    metric: str | None = Field(default=None, min_length=1, max_length=32)
    operator: str | None = Field(default=None, min_length=1, max_length=16)
    threshold_value: float | None = None
    sustain_buckets: int | None = None
    recovery_buckets: int | None = None
    severity: Severity | None = None
    enabled: bool | None = None

    @field_validator("expected_updated_at")
    @classmethod
    def _timezone_required(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expected_updated_at must include a timezone")
        return value.astimezone(UTC)


class BreachOut(BaseModel):
    """One breach row. Values and measurements only; the label is the stored one."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    rule_id: uuid.UUID
    state: str
    first_bucket_start: datetime
    last_bucket_start: datetime
    opened_at: datetime
    recovering_at: datetime | None
    resolved_at: datetime | None
    worst_value: float | None
    worst_measurement: str | None
    last_value: float | None
    last_measurement: str | None
    unmeasured_buckets: int
    subject_label: str
    details: dict[str, object]


class CustomRuleOut(BaseModel):
    """One rule, its hysteresis, and the platform alerting mode.

    ``alerting_mode`` is computed (``shadow`` or ``alerting``). It is not a
    column. ``last_status`` stays null until a bucket has been judged.
    """

    id: uuid.UUID
    name: str
    description: str | None
    subject_kind: str
    log_source_id: uuid.UUID | None
    source_class: str | None
    waf_endpoint_id: uuid.UUID | None
    ips_entity_id: uuid.UUID | None
    pw_value_id: uuid.UUID | None
    metric: str
    operator: str
    threshold_value: float
    threshold_unit: str | None
    sustain_buckets: int
    recovery_buckets: int
    severity: str
    enabled: bool
    consecutive_breaching: int
    consecutive_under: int
    last_evaluated_bucket_start: datetime | None
    last_evaluated_at: datetime | None
    last_status: str | None
    last_status_reason: str | None
    last_status_reason_code: str | None
    last_status_reason_params: dict[str, MessageParamValue] | None
    active_breach: BreachOut | None
    alerting_mode: str
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    updated_by: str | None


class PreviewBucketOut(BaseModel):
    bucket_start: datetime
    value: float | None
    measurement: MeasurementLabel
    completeness: str | None
    in_maintenance: bool


class BreachIntervalOut(BaseModel):
    """A firing. ``end`` is null while the condition would still be open."""

    start: datetime
    end: datetime | None


class PreviewOut(BaseModel):
    """The §F.7 chart contract. Nothing in it was written by the request."""

    threshold_value: float
    unit: str
    operator: str
    fired_count: int
    measured_count: int
    unmeasured_count: int
    buckets: list[PreviewBucketOut]
    breach_intervals: list[BreachIntervalOut]


class CatalogMetricOut(BaseModel):
    """One metric the editor may offer, with its unit and honesty note.

    ``reads`` and ``bulk_loader`` stay in the catalog module. They name
    tables and loaders, and the editor does not choose either.
    """

    metric: str
    subject_kind: str
    unit: str
    is_ratio: bool
    measurement: MeasurementRule
    note: str


class CatalogSubjectKindOut(BaseModel):
    """A subject kind and the one column a rule of that kind must set."""

    subject_kind: str
    column: str
    search_family: SearchFamily


class CustomRuleCatalogOut(BaseModel):
    """What the rule editor needs before any rule exists.

    ``evaluation_enabled`` is the platform switch. ``alerting_mode`` is the
    same computed value a rule response carries (``shadow`` or ``alerting``).
    ``enabled_count`` is how many rules already hold a slot against
    ``max_enabled``. The preview hour bounds are the preview route's own.
    """

    metrics: list[CatalogMetricOut]
    subject_kinds: list[CatalogSubjectKindOut]
    cadences: list[CadenceOut]
    source_classes: list[str]
    operators: list[str]
    evaluation_enabled: bool
    alerting_mode: str
    max_enabled: int
    enabled_count: int
    preview_hours_min: int
    preview_hours_max: int
