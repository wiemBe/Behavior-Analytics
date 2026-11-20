"""Additive JDBC storage (design §3): one safe snapshot per source and one
pass/lease per console. Only the 18 allow-listed DTO members are stored; the
field-state map is validated in Python and coarsely checked in the database.

Writers must set `field_states` and the counters explicitly on every insert:
the columns carry ORM-side defaults only, so a raw SQL or Core insert that
omits them gets a NOT NULL violation rather than an implicit `{}` or `0`.
The `@validates` hook fires only when an attribute is assigned. In-place
mutation of `field_states` (`row.field_states[k] = ...`) and Core writes
(`insert()`, `on_conflict_do_update()`) bypass it, so those writers must call
`validate_field_states` explicitly before issuing the statement.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, validates

from app.jdbc.dto import ConfigState, FieldStateReason
from app.models.base import Base

SAFE_MEMBERS = frozenset(
    "database_type database_host tls_hostname database_port database_name schema_name "  # noqa: SIM905
    "table_name select_list compare_field polling_interval_seconds start_time_local "
    "prepared_statements predefined_query eps_throttle source_label use_ssl "
    "allow_untrusted advanced_options".split()
)
FIELD_STATE_CODES = frozenset({"SET", "UNSET", "REMOVED", "UNAVAILABLE"})
_REASONS = frozenset(member.value for member in FieldStateReason)
_FAILURE = "^[a-z][a-z0-9_]{0,47}$"
_CONFIG_STATES = ", ".join(f"'{member.value}'" for member in ConfigState)
_SYNC_STATES = (
    "'IDLE', 'QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', 'FAILED', "
    "'NOT_COLLECTED', 'UNSUPPORTED'"
)


class FieldStatesValidationError(ValueError):
    """Fixed codes only; the rejected value is never echoed."""

    def __init__(self, codes: tuple[str, ...]) -> None:
        self.codes = codes
        super().__init__("invalid JDBC field_states: " + ", ".join(codes))


def _string_value(value: Any) -> str:
    """Store the plain string of a StrEnum member, not the enum object."""
    return value.value if isinstance(value, Enum) else value


def validate_field_states(value: dict[str, Any]) -> dict[str, Any]:
    """Return a validated copy, or raise a fixed-code error that echoes no input."""
    if not isinstance(value, dict):
        raise FieldStatesValidationError(("not_object",))
    cleaned: dict[str, Any] = {}
    codes: list[str] = []
    for name, state in value.items():
        if not isinstance(name, str) or name not in SAFE_MEMBERS:
            codes.append("unknown_field")
        elif not isinstance(state, dict) or set(state) != {"code", "reason"}:
            codes.append("invalid_entry")
        elif not isinstance(state["code"], str) or state["code"] not in FIELD_STATE_CODES:
            codes.append("unknown_code")
        elif state["reason"] is not None and (
            not isinstance(state["reason"], str) or state["reason"] not in _REASONS
        ):
            codes.append("unknown_reason")
        else:
            cleaned[name] = {
                "code": _string_value(state["code"]),
                "reason": _string_value(state["reason"]) if state["reason"] is not None else None,
            }
    if codes:
        raise FieldStatesValidationError(tuple(dict.fromkeys(codes)))
    return cleaned


class JdbcSourceConfig(Base):
    """One safe configuration snapshot per JDBC log source, with no history."""

    __tablename__ = "jdbc_source_config"
    __table_args__ = (
        CheckConstraint("protocol_type_id = 8", name="protocol_8"),
        CheckConstraint(
            "database_port IS NULL OR database_port BETWEEN 1 AND 65535", name="port_range"
        ),
        CheckConstraint(
            "polling_interval_seconds IS NULL OR polling_interval_seconds BETWEEN 1 AND 604800",
            name="interval_range",
        ),
        CheckConstraint("eps_throttle IS NULL OR eps_throttle >= 0", name="eps_non_negative"),
        CheckConstraint("jsonb_typeof(field_states) = 'object'", name="fields_object"),
        CheckConstraint("allowlist_version >= 1", name="version_positive"),
        CheckConstraint(f"config_state IN ({_CONFIG_STATES})", name="config_state_known"),
        CheckConstraint(f"failure_code ~ '{_FAILURE}'", name="failure_code_fixed"),
    )

    log_source_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("log_source.id", ondelete="CASCADE"), primary_key=True
    )
    protocol_type_id: Mapped[int] = mapped_column(Integer, nullable=False)
    database_type: Mapped[str | None] = mapped_column(String(32))
    database_host: Mapped[str | None] = mapped_column(String(253))
    tls_hostname: Mapped[str | None] = mapped_column(String(253))
    database_port: Mapped[int | None] = mapped_column(Integer)
    database_name: Mapped[str | None] = mapped_column(String(255))
    schema_name: Mapped[str | None] = mapped_column(String(255))
    table_name: Mapped[str | None] = mapped_column(String(255))
    select_list: Mapped[str | None] = mapped_column(String(2048))
    compare_field: Mapped[str | None] = mapped_column(String(255))
    polling_interval_seconds: Mapped[int | None] = mapped_column(Integer)
    start_time_local: Mapped[str | None] = mapped_column(String(16))
    prepared_statements: Mapped[bool | None] = mapped_column(Boolean)
    predefined_query: Mapped[str | None] = mapped_column(String(128))
    eps_throttle: Mapped[int | None] = mapped_column(BigInteger)
    source_label: Mapped[str | None] = mapped_column(String(255))
    use_ssl: Mapped[bool | None] = mapped_column(Boolean)
    allow_untrusted: Mapped[bool | None] = mapped_column(Boolean)
    advanced_options: Mapped[bool | None] = mapped_column(Boolean)
    field_states: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict, nullable=False)
    allowlist_version: Mapped[int] = mapped_column(Integer, nullable=False)
    config_state: Mapped[str] = mapped_column(String(24), nullable=False)
    failure_code: Mapped[str | None] = mapped_column(String(48))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @validates("field_states")
    def _check_field_states(self, key: str, value: dict[str, Any]) -> dict[str, Any]:
        return validate_field_states(value)


class JdbcSyncState(Base):
    """One collection pass and lease per console; counters reset each attempt."""

    __tablename__ = "jdbc_sync_state"
    __table_args__ = (
        CheckConstraint(f"state IN ({_SYNC_STATES})", name="state_known"),
        CheckConstraint("pages >= 0", name="pages_non_negative"),
        CheckConstraint("rows >= 0", name="rows_non_negative"),
        CheckConstraint("failures >= 0", name="failures_non_negative"),
        CheckConstraint("elapsed_ms >= 0", name="elapsed_ms_non_negative"),
        CheckConstraint(
            "response_bytes IS NULL OR response_bytes >= 0", name="response_bytes_non_negative"
        ),
        CheckConstraint(
            "failed_page IS NULL OR failed_page >= 0", name="failed_page_non_negative"
        ),
        CheckConstraint(f"failure_code ~ '{_FAILURE}'", name="failure_code_fixed"),
    )

    instance_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("qradar_instance.id", ondelete="CASCADE"),
        primary_key=True,
    )
    state: Mapped[str] = mapped_column(String(24), nullable=False)
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_complete_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_id: Mapped[uuid.UUID | None] = mapped_column(PgUUID(as_uuid=True))
    pages: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failures: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    elapsed_ms: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    response_bytes: Mapped[int | None] = mapped_column(BigInteger)
    failed_page: Mapped[int | None] = mapped_column(Integer)
    failure_code: Mapped[str | None] = mapped_column(String(48))
    fallback_resume_id: Mapped[int | None] = mapped_column(BigInteger)
