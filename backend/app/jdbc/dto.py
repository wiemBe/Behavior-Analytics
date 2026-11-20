"""Safe JDBC configuration transport shapes (design §2).

Built only from allow-listed, validated values. Frozen, extra-forbid, with an
input-free repr and validation errors, so no raw parameter can leak.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

ALLOWLIST_VERSION = 1


class ConfigState(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    NOT_COLLECTED = "NOT_COLLECTED"
    UNVERIFIED_PROTOCOL = "UNVERIFIED_PROTOCOL"
    NOT_JDBC = "NOT_JDBC"


class FieldStateCode(StrEnum):
    SET = "SET"
    UNSET = "UNSET"
    UNAVAILABLE = "UNAVAILABLE"


class FieldStateReason(StrEnum):
    UNSUPPORTED_VALUE = "unsupported_value"
    ID_NAME_MISMATCH = "id_name_mismatch"
    DUPLICATE_NAME = "duplicate_name"
    WRONG_TYPE = "wrong_type"
    MISSING = "missing"


class JdbcConfigValidationError(ValueError):
    """Fixed codes only; replaces Pydantic's input-echoing error."""

    def __init__(self, codes: tuple[str, ...]) -> None:
        self.codes = codes
        super().__init__("invalid JdbcConfigDTO input: " + ", ".join(codes))


class FieldState(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    code: FieldStateCode
    reason: FieldStateReason | None = None


class JdbcConfigDTO(BaseModel):
    """The only parameter shape allowed past the provider boundary."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    qradar_id: int
    protocol_type_id: int | None = None
    config_state: ConfigState
    allowlist_version: int = ALLOWLIST_VERSION
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
    field_states: dict[str, FieldState] = Field(default_factory=dict)

    def __init__(self, **data: Any) -> None:
        codes: tuple[str, ...] = ()
        try:
            super().__init__(**data)
        except ValidationError as exc:
            codes = tuple(str(error.get("type", "invalid")) for error in exc.errors())
        if codes:  # after the handler: no input-bearing __context__
            raise JdbcConfigValidationError(codes)

    def __repr__(self) -> str:
        return (
            f"JdbcConfigDTO(qradar_id={self.qradar_id}, protocol_type_id={self.protocol_type_id}, "
            f"config_state={self.config_state.value}, allowlist_version={self.allowlist_version})"
        )


@dataclass(frozen=True)
class JdbcConfigWalk:
    """Safe configs and received list-page counts, including an empty terminator.

    Only a short page proves ``complete``; a failed page or a full page at the
    ceiling does not. Per-config field availability is independent of this flag.
    """

    configs: list[JdbcConfigDTO]
    pages_read: int = 0
    rows_read: int = 0
    complete: bool = False
