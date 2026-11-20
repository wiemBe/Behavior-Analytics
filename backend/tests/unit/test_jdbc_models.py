"""Pure field-state validator and stored-shape pins for JDBC storage (J1c1)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, cast

import pytest
from sqlalchemy import CheckConstraint
from sqlalchemy.dialects import postgresql
from sqlalchemy.schema import CreateTable

from app.jdbc.dto import FieldStateCode, FieldStateReason, JdbcConfigDTO
from app.models import Base
from app.models.jdbc import (
    FIELD_STATE_CODES,
    SAFE_MEMBERS,
    FieldStatesValidationError,
    JdbcSourceConfig,
    validate_field_states,
)

_CONFIG_COLUMNS = {
    "log_source_id",
    "protocol_type_id",
    "database_type",
    "database_host",
    "tls_hostname",
    "database_port",
    "database_name",
    "schema_name",
    "table_name",
    "select_list",
    "compare_field",
    "polling_interval_seconds",
    "start_time_local",
    "prepared_statements",
    "predefined_query",
    "eps_throttle",
    "source_label",
    "use_ssl",
    "allow_untrusted",
    "advanced_options",
    "field_states",
    "allowlist_version",
    "config_state",
    "failure_code",
    "observed_at",
    "last_success_at",
}

_SYNC_COLUMNS = {
    "instance_id",
    "state",
    "queued_at",
    "started_at",
    "finished_at",
    "last_complete_at",
    "lease_expires_at",
    "attempt_id",
    "pages",
    "rows",
    "failures",
    "elapsed_ms",
    "response_bytes",
    "failed_page",
    "failure_code",
    "fallback_resume_id",
}

# The five DTO fields that are not one of the 18 safe configuration members.
_NON_MEMBER_FIELDS = {
    "qradar_id",
    "protocol_type_id",
    "config_state",
    "allowlist_version",
    "field_states",
}

# The exact CHECK bodies the models must render for the postgresql dialect.
# These are the same literals the alembic migration uses; compiling CreateTable
# catches a broken CHECK that plain column-name assertions cannot see.
_EXPECTED_CHECK_BODIES = {
    "jdbc_source_config": {
        "ck_jdbc_source_config_protocol_8": "protocol_type_id = 8",
        "ck_jdbc_source_config_port_range": (
            "database_port IS NULL OR database_port BETWEEN 1 AND 65535"
        ),
        "ck_jdbc_source_config_interval_range": (
            "polling_interval_seconds IS NULL OR polling_interval_seconds BETWEEN 1 AND 604800"
        ),
        "ck_jdbc_source_config_eps_non_negative": "eps_throttle IS NULL OR eps_throttle >= 0",
        "ck_jdbc_source_config_fields_object": "jsonb_typeof(field_states) = 'object'",
        "ck_jdbc_source_config_version_positive": "allowlist_version >= 1",
        "ck_jdbc_source_config_config_state_known": (
            "config_state IN ('COMPLETE', 'PARTIAL', 'NOT_COLLECTED', "
            "'UNVERIFIED_PROTOCOL', 'NOT_JDBC')"
        ),
        "ck_jdbc_source_config_failure_code_fixed": "failure_code ~ '^[a-z][a-z0-9_]{0,47}$'",
    },
    "jdbc_sync_state": {
        "ck_jdbc_sync_state_state_known": (
            "state IN ('IDLE', 'QUEUED', 'RUNNING', 'COMPLETE', 'PARTIAL', "
            "'FAILED', 'NOT_COLLECTED', 'UNSUPPORTED')"
        ),
        "ck_jdbc_sync_state_pages_non_negative": "pages >= 0",
        "ck_jdbc_sync_state_rows_non_negative": "rows >= 0",
        "ck_jdbc_sync_state_failures_non_negative": "failures >= 0",
        "ck_jdbc_sync_state_elapsed_ms_non_negative": "elapsed_ms >= 0",
        "ck_jdbc_sync_state_response_bytes_non_negative": (
            "response_bytes IS NULL OR response_bytes >= 0"
        ),
        "ck_jdbc_sync_state_failed_page_non_negative": "failed_page IS NULL OR failed_page >= 0",
        "ck_jdbc_sync_state_failure_code_fixed": "failure_code ~ '^[a-z][a-z0-9_]{0,47}$'",
    },
}


def test_safe_members_are_exactly_the_dtos_eighteen_members() -> None:
    derived = frozenset(JdbcConfigDTO.model_fields) - _NON_MEMBER_FIELDS
    assert derived == SAFE_MEMBERS
    assert len(SAFE_MEMBERS) == 18
    assert not SAFE_MEMBERS & _NON_MEMBER_FIELDS


def test_stored_codes_extend_the_dto_with_removed() -> None:
    expected = {"SET", "UNSET", "UNAVAILABLE", "REMOVED"}
    assert expected == FIELD_STATE_CODES
    assert {member.value for member in FieldStateCode} <= FIELD_STATE_CODES


def test_valid_field_states_are_copied_and_normalised() -> None:
    value: dict[str, Any] = {
        "database_type": {"code": "SET", "reason": None},
        "database_host": {"code": "REMOVED", "reason": None},
        "select_list": {"code": "UNAVAILABLE", "reason": FieldStateReason.WRONG_TYPE.value},
        "eps_throttle": {"code": "UNSET", "reason": FieldStateReason.MISSING.value},
    }
    result = validate_field_states(value)
    assert result == value
    assert result is not value
    assert result["database_type"] is not value["database_type"]


def test_empty_field_states_is_valid() -> None:
    assert validate_field_states({}) == {}


def test_unknown_field_name_is_rejected_without_echo() -> None:
    sentinel = "database_password"
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states({sentinel: {"code": "SET", "reason": None}})
    assert caught.value.codes == ("unknown_field",)
    assert sentinel not in str(caught.value)
    assert sentinel not in repr(caught.value)


def test_a_non_string_key_is_rejected() -> None:
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states({7: {"code": "SET", "reason": None}})  # type: ignore[dict-item]
    assert caught.value.codes == ("unknown_field",)


@pytest.mark.parametrize(
    "entry",
    [
        "SET",
        {"code": "SET"},
        {"reason": None},
        {"code": "SET", "reason": None, "extra": "x"},
        {},
    ],
)
def test_bad_entry_shapes_are_rejected(entry: object) -> None:
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states({"database_type": cast(dict[str, Any], entry)})
    assert caught.value.codes == ("invalid_entry",)


def test_a_non_object_top_level_is_rejected() -> None:
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states(cast(dict[str, Any], ["database_type"]))
    assert caught.value.codes == ("not_object",)


def test_unknown_code_is_rejected_without_echo() -> None:
    sentinel = "PRESENT"
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states({"database_type": {"code": sentinel, "reason": None}})
    assert caught.value.codes == ("unknown_code",)
    assert sentinel not in str(caught.value)


def test_unknown_reason_is_rejected_without_echo() -> None:
    sentinel = "upstream_status_message"
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states(
            {"database_type": {"code": "UNAVAILABLE", "reason": sentinel}}
        )
    assert caught.value.codes == ("unknown_reason",)
    assert sentinel not in str(caught.value)


def test_multiple_codes_are_reported_once_and_in_order() -> None:
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states(
            {
                "password": {"code": "SET", "reason": None},
                "username": {"code": "SET", "reason": None},
                "database_type": {"code": "BOGUS", "reason": None},
            }
        )
    assert caught.value.codes == ("unknown_field", "unknown_code")


def test_no_input_text_is_echoed_by_any_error() -> None:
    sentinels = ("s3cr3t_password", "jdbc:h2:mem:leak", "/etc/keys/id_rsa")
    value = {
        sentinels[0]: {"code": "SET", "reason": None},
        "database_type": {"code": sentinels[1], "reason": sentinels[2]},
    }
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states(value)
    for sentinel in sentinels:
        assert sentinel not in str(caught.value)
        assert sentinel not in repr(caught.value)


def test_the_orm_refuses_a_bad_value_on_assignment() -> None:
    row = JdbcSourceConfig(
        log_source_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        protocol_type_id=8,
        allowlist_version=1,
        config_state="COMPLETE",
        observed_at=datetime.now(UTC),
        field_states={"database_type": {"code": "SET", "reason": None}},
    )
    assert row.field_states == {"database_type": {"code": "SET", "reason": None}}
    with pytest.raises(FieldStatesValidationError):
        row.field_states = {"database_password": {"code": "SET", "reason": None}}  # type: ignore[assignment]


def test_both_tables_are_registered_with_only_the_safe_columns() -> None:
    assert {"jdbc_source_config", "jdbc_sync_state"} <= set(Base.metadata.tables)
    assert set(Base.metadata.tables["jdbc_source_config"].c.keys()) == _CONFIG_COLUMNS
    assert set(Base.metadata.tables["jdbc_sync_state"].c.keys()) == _SYNC_COLUMNS
    column_names = set(Base.metadata.tables["jdbc_source_config"].c.keys())
    assert not any(
        word in column
        for column in column_names
        for word in ("password", "username", "url", "connection", "token", "secret")
    )


def test_model_ddl_check_bodies_match_the_expected_literals() -> None:
    """Compile CreateTable for postgresql; a broken CHECK body cannot hide here."""
    dialect = postgresql.dialect()
    for table_name, expected in _EXPECTED_CHECK_BODIES.items():
        table = Base.metadata.tables[table_name]
        ddl = str(CreateTable(table).compile(dialect=dialect))
        assert ddl
        bodies = {
            c.name: str(c.sqltext.compile(dialect=dialect))
            for c in table.constraints
            if isinstance(c, CheckConstraint) and c.name is not None
        }
        for name, body in expected.items():
            assert bodies[name] == body
            assert body in ddl


_NON_STRING_VALUES = (
    ["SET"],  # unhashable list
    {"SET": 1},  # unhashable dict
    7,  # hashable but not a string
    True,  # bool is not a string
    b"SET",  # bytes are not a string
)


@pytest.mark.parametrize("bad", _NON_STRING_VALUES)
def test_unhashable_or_non_string_code_raises_validation_error(bad: object) -> None:
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states({"database_type": {"code": bad, "reason": None}})
    assert caught.value.codes == ("unknown_code",)


@pytest.mark.parametrize("bad", _NON_STRING_VALUES)
def test_unhashable_or_non_string_reason_raises_validation_error(bad: object) -> None:
    with pytest.raises(FieldStatesValidationError) as caught:
        validate_field_states({"database_type": {"code": "UNAVAILABLE", "reason": bad}})
    assert caught.value.codes == ("unknown_reason",)


def test_enum_members_are_normalised_to_plain_strings() -> None:
    result = validate_field_states(
        {"database_type": {"code": FieldStateCode.SET, "reason": FieldStateReason.MISSING}}
    )
    assert result == {"database_type": {"code": "SET", "reason": "missing"}}
    assert type(result["database_type"]["code"]) is str
    assert type(result["database_type"]["reason"]) is str
