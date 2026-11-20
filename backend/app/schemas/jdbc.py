"""Explicit safe JDBC list/detail responses; no raw parameter or status-message bag."""

from datetime import UTC
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.jdbc import flow
from app.jdbc.dto import ConfigState, FieldStateReason, JdbcWalkFailure
from app.jdbc.parameters import FIELDS, Bad

UtcDatetime = Annotated[AwareDatetime, AfterValidator(lambda value: value.astimezone(UTC))]
Count = Annotated[int, Field(strict=True, ge=0)]
FailureCode = JdbcWalkFailure | Literal["disabled", "unsupported", "worker_lost"]


class JdbcResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", hide_input_in_errors=True)


class JdbcSafeFields(JdbcResponse):
    model_config = ConfigDict(strict=True)
    database_type: str | None = None
    database_host: str | None = None
    tls_hostname: str | None = None
    database_port: int | None = None
    database_name: str | None = None
    schema_name: str | None = None
    table_name: str | None = None
    select_list: str | None = None
    compare_field: str | None = None
    polling_interval_seconds: int | None = None
    start_time_local: str | None = None
    prepared_statements: bool | None = None
    predefined_query: str | None = None
    eps_throttle: int | None = None
    source_label: str | None = None
    use_ssl: bool | None = None
    allow_untrusted: bool | None = None
    advanced_options: bool | None = None

    @model_validator(mode="after")
    def _safe_values(self) -> "JdbcSafeFields":
        for spec in FIELDS:
            value = getattr(self, spec.member)
            if value is not None:
                text = str(value).lower() if isinstance(value, bool) else str(value)
                try:
                    if spec.parse(text) != value:
                        raise Bad
                except Bad:
                    raise ValueError("unsupported_value") from None
        return self


class JdbcFieldState(JdbcResponse):
    code: Literal["SET", "UNSET", "REMOVED", "UNAVAILABLE"]
    reason: FieldStateReason | None = None


class JdbcConfigOut(JdbcResponse):
    support: Literal["VERIFIED", "UNVERIFIED_PROTOCOL"]
    state: ConfigState
    failure_code: FailureCode | None = None
    observed_at: UtcDatetime | None
    last_success_at: UtcDatetime | None
    stale: bool
    safe_fields: JdbcSafeFields | None
    field_states: dict[str, JdbcFieldState]

    @model_validator(mode="after")
    def _safe_shape(self) -> "JdbcConfigOut":
        if set(self.field_states) - {spec.member for spec in FIELDS}:
            raise ValueError("unknown_field")
        if self.support == "UNVERIFIED_PROTOCOL" and (self.safe_fields or self.field_states):
            raise ValueError("unverified_parameters")
        return self


class JdbcQradarOut(JdbcResponse):
    enabled: bool
    status: flow.ConnectionCode | None
    inconsistency: flow.Inconsistency | None = None
    inventory_last_complete_at: UtcDatetime | None
    inventory_last_attempt_at: UtcDatetime | None
    inventory_state: flow.AttemptState
    inventory_stale: bool
    reason_code: flow.ReasonCode | None = None


class JdbcObservationOut(JdbcResponse):
    start: UtcDatetime
    end: UtcDatetime
    observed_at: UtcDatetime
    event_count: Count | None
    completeness: Literal["COMPLETE", "PARTIAL", "MISSING"]
    origin: Literal["live", "history"]


class JdbcVolumeOut(JdbcResponse):
    measurement: flow.Measurement
    target_start: UtcDatetime
    target_end: UtcDatetime
    bucket_seconds: Annotated[int, Field(gt=0)]
    event_count: Count | None
    average_eps: Annotated[float, Field(ge=0, allow_inf_nan=False)] | None
    collected_at: UtcDatetime | None
    stale: bool
    reason_code: flow.ReasonCode | None = None
    last_observation: JdbcObservationOut | None = None

class JdbcFlowOut(JdbcResponse):
    state: flow.FlowState
    reason_code: flow.ReasonCode | None = None


class JdbcMonitoringOut(JdbcResponse):
    enabled: bool


class JdbcSourceRow(JdbcResponse):
    id: UUID
    qradar_id: int
    name: str
    protocol_type_id: Literal[8, 20, 21, 37, 42, 49, 55, 67]
    protocol_name: str
    qradar: JdbcQradarOut
    config: JdbcConfigOut
    volume: JdbcVolumeOut
    flow: JdbcFlowOut
    monitoring: JdbcMonitoringOut
JdbcSourceDetail = JdbcSourceRow
