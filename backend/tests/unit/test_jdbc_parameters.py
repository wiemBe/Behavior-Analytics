"""Proofs for the J1a safe JDBC parameter projection (design §2).

The canaries are distinct, recognizable strings placed in denied fields,
unknown names, malformed elements and connection-shaped accepted text. None
may appear in a DTO, an exception, or a captured log record.
"""

from __future__ import annotations

import logging
import traceback
from typing import Any

import pytest
from pydantic import ValidationError

from app.jdbc.dto import (
    ConfigState,
    FieldStateCode,
    FieldStateReason,
    JdbcConfigDTO,
    JdbcConfigValidationError,
)
from app.jdbc.parameters import (
    ALLOWED_UPSTREAM_NAMES,
    SHAPES,
    Bad,
    host,
    project_jdbc_config,
    start,
)
from app.providers.base import CapabilityNotSupportedError
from app.providers.mock import MockQRadarProvider

#: Upstream name -> member, from design §2's table.
MEMBER = {
    "databaseType": "database_type",
    "databaseServerHostname": "database_host",
    "MSSQLServerHostname": "tls_hostname",
    "databasePort": "database_port",
    "databaseName": "database_name",
    "schema": "schema_name",
    "eventTable": "table_name",
    "eventQuery": "select_list",
    "compareField": "compare_field",
    "pollingInterval": "polling_interval_seconds",
    "startTime": "start_time_local",
    "usePreparedStatements": "prepared_statements",
    "queryList": "predefined_query",
    "epsThrottle": "eps_throttle",
    "identifier": "source_label",
    "useSSL": "use_ssl",
    "allowUntrusted": "allow_untrusted",
    "enableAdvancedOptions": "advanced_options",
}

#: JDBC-0.report.md:20-54 pairs, restricted to the 18 accepted names.
IDS = {
    "identifier": 8000,
    "databaseType": 8005,
    "databaseName": 8010,
    "schema": 8017,
    "databaseServerHostname": 8020,
    "databasePort": 8030,
    "queryList": 8080,
    "eventTable": 8090,
    "eventQuery": 8100,
    "compareField": 8110,
    "usePreparedStatements": 8120,
    "startTime": 8130,
    "pollingInterval": 8140,
    "epsThrottle": 8150,
    "useSSL": 8210,
    "MSSQLServerHostname": 8280,
    "enableAdvancedOptions": 8290,
    "allowUntrusted": 8320,
}

VALID = {
    "databaseType": "Postgres",
    "databaseServerHostname": "db.example.com",
    "MSSQLServerHostname": "tls.example.com",
    "databasePort": "5432",
    "databaseName": "app_audit",
    "schema": "public",
    "eventTable": "app_audit_flowing",
    "eventQuery": "id,event_time",
    "compareField": "id",
    "pollingInterval": "30",
    "startTime": "2026-11-20 10:30",
    "usePreparedStatements": "true",
    "queryList": "none",
    "epsThrottle": "20000",
    "identifier": "labjdbc-flowing",
    "useSSL": "false",
    "allowUntrusted": "false",
    "enableAdvancedOptions": "false",
}

CANARY = {
    "password": "CANARY-PASSWORD-9f3a",
    "username": "CANARY-USERNAME-7c1b",
    "keyfile": "CANARY-KEYFILE-2e8d",
    "unknown": "CANARY-UNKNOWN-4a6f",
    "malformed": "CANARY-MALFORMED-1b5c",
    "connection": "jdbc:postgresql://db.internal/app?user=svc&password=CANARY-CONNECTION-8d2e",
}


def full_array(overrides: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """A complete protocol-8 array, with optional per-name value overrides."""
    values = dict(VALID)
    if overrides:
        values.update(overrides)
    return [
        {"id": IDS[name], "name": name, "value": values[name]}
        for name in MEMBER
    ]


def project(array: object, protocol: int = 8) -> JdbcConfigDTO:
    return project_jdbc_config(qradar_id=562, protocol_type_id=protocol, protocol_parameters=array)


def one(name: str, value: object, *, pid: int | None = None) -> JdbcConfigDTO:
    """Project a full array with just one field replaced."""
    array = [element for element in full_array() if element["name"] != name]
    array.append({"id": IDS[name] if pid is None else pid, "name": name, "value": value})
    return project(array)


def state_of(dto: JdbcConfigDTO, name: str) -> FieldStateCode:
    return dto.field_states[MEMBER[name]].code


def reason_of(dto: JdbcConfigDTO, name: str) -> FieldStateReason | None:
    return dto.field_states[MEMBER[name]].reason


def clean(dto: JdbcConfigDTO, caplog: pytest.LogCaptureFixture, extra: str = "") -> None:
    """The tripwire: no canary in serialization, repr, logs or extra text."""
    text = "\n".join(
        [repr(dto), str(dto), str(dto.model_dump()), caplog.text, extra]
    )
    for canary in CANARY.values():
        assert canary not in text


# ------------------------------------------------------------------- required names


def test_exact_18_names() -> None:
    assert len(ALLOWED_UPSTREAM_NAMES) == 18
    assert frozenset(MEMBER) == ALLOWED_UPSTREAM_NAMES
    assert frozenset(IDS) == ALLOWED_UPSTREAM_NAMES
    assert set(VALID) == set(MEMBER)

    dto = project(full_array())
    assert dto.config_state is ConfigState.COMPLETE
    assert dto.allowlist_version == 1
    assert dto.qradar_id == 562
    assert dto.protocol_type_id == 8

    safe_members = set(type(dto).model_fields) - {
        "qradar_id",
        "protocol_type_id",
        "config_state",
        "allowlist_version",
        "observed_at",
        "last_success_at",
        "field_states",
    }
    assert safe_members == set(MEMBER.values())
    assert len(safe_members) == 18
    assert set(dto.field_states) == safe_members
    assert all(field.code is FieldStateCode.SET for field in dto.field_states.values())
    assert all(field.reason is None for field in dto.field_states.values())

    assert dto.database_port == 5432
    assert dto.polling_interval_seconds == 30
    assert dto.eps_throttle == 20000
    assert dto.prepared_statements is True
    assert dto.use_ssl is False
    assert dto.allow_untrusted is False
    assert dto.advanced_options is False
    assert dto.start_time_local == "2026-11-20 10:30"


def test_unknown_and_credentials_dropped(caplog: pytest.LogCaptureFixture) -> None:
    array = full_array()
    # Denied credential and account fields, plus fields a later version may add.
    array.extend(
        [
            {"id": 8050, "name": "databasePassword", "value": CANARY["password"]},
            {"id": 8040, "name": "databaseUsername", "value": CANARY["username"]},
            {"id": 8052, "name": "privateKeyFileName", "value": CANARY["keyfile"]},
            {"id": 8051, "name": "keyPairAuthentication", "value": "true"},
            {"id": 8022, "name": "warehouse", "value": "snowflake_account"},
            {"id": 8026, "name": "role", "value": "sysadmin"},
            {"id": 99999, "name": "futureCredential", "value": CANARY["unknown"]},
        ]
    )
    with caplog.at_level(logging.DEBUG):
        dto = project(array)
    assert dto.config_state is ConfigState.COMPLETE
    assert set(dto.field_states) == set(MEMBER.values())
    clean(dto, caplog)


def test_id_name_mismatch_and_duplicates() -> None:
    array = [element for element in full_array() if element["name"] != "eventTable"]
    array.append({"id": 99999, "name": "eventTable", "value": "valid_table"})
    array.append({"id": 8030, "name": "databasePort", "value": "6432"})

    dto = project(array)
    assert dto.config_state is ConfigState.PARTIAL
    assert dto.table_name is None
    assert state_of(dto, "eventTable") is FieldStateCode.UNAVAILABLE
    assert reason_of(dto, "eventTable") is FieldStateReason.ID_NAME_MISMATCH
    # Never last-value-wins: the duplicate nulls the field, even though the
    # first element was valid and the second parsed.
    assert dto.database_port is None
    assert state_of(dto, "databasePort") is FieldStateCode.UNAVAILABLE
    assert reason_of(dto, "databasePort") is FieldStateReason.DUPLICATE_NAME


def test_empty_removed_and_invalid() -> None:
    array = full_array({"schema": "", "databasePort": "70000"})
    array = [element for element in array if element["name"] != "databaseName"]
    array.append({"id": 8010, "name": "databaseName", "value": 12345})

    dto = project(array)
    assert dto.config_state is ConfigState.PARTIAL
    assert dto.schema_name is None
    assert state_of(dto, "schema") is FieldStateCode.UNSET
    assert reason_of(dto, "schema") is None
    assert dto.database_port is None
    assert state_of(dto, "databasePort") is FieldStateCode.UNAVAILABLE
    assert reason_of(dto, "databasePort") is FieldStateReason.UNSUPPORTED_VALUE
    assert dto.database_name is None
    assert state_of(dto, "databaseName") is FieldStateCode.UNAVAILABLE
    assert reason_of(dto, "databaseName") is FieldStateReason.WRONG_TYPE

    # An all-empty array is a COMPLETE configuration of unset fields, not a
    # guessed default and not a failure.
    empty = project(full_array(dict.fromkeys(MEMBER, "")))
    assert empty.config_state is ConfigState.COMPLETE
    assert all(value is None for name, value in vars(empty).items() if name in MEMBER.values())


def test_connection_strings_rejected(caplog: pytest.LogCaptureFixture) -> None:
    array = full_array(
        {
            "databaseName": CANARY["connection"],
            "databaseServerHostname": "postgres://admin:secret@db.internal/app",
            "identifier": "user=admin password=secret",
            "eventTable": "../keys/private.pem",
        }
    )
    with caplog.at_level(logging.DEBUG):
        dto = project(array)
    assert dto.config_state is ConfigState.PARTIAL
    for name in ("databaseName", "databaseServerHostname", "identifier", "eventTable"):
        assert getattr(dto, MEMBER[name]) is None
        assert state_of(dto, name) is FieldStateCode.UNAVAILABLE
        assert reason_of(dto, name) is FieldStateReason.UNSUPPORTED_VALUE
    # This one passes the label grammar on its own: only the credential-shape
    # rejection stops it.
    shaped = one("identifier", "jdbc:sqlserver")
    assert state_of(shaped, "identifier") is FieldStateCode.UNAVAILABLE
    assert reason_of(shaped, "identifier") is FieldStateReason.UNSUPPORTED_VALUE
    clean(dto, caplog)


def test_unverified_protocols_have_no_parameters() -> None:
    for protocol in (20, 21, 37, 42, 49, 55, 67):
        dto = project(full_array(), protocol=protocol)
        assert dto.config_state is ConfigState.UNVERIFIED_PROTOCOL
        assert dto.field_states == {}
        assert all(getattr(dto, member) is None for member in MEMBER.values())

    other = project(full_array(), protocol=99)
    assert other.config_state is ConfigState.NOT_JDBC
    assert other.field_states == {}
    assert other.database_name is None


def test_errors_and_repr_never_echo_input(caplog: pytest.LogCaptureFixture) -> None:
    # The accepted-field canary replaces a real element, so it is projected
    # rather than dropped as a duplicate; a raw-passthrough bug would store it.
    array = full_array({"queryList": CANARY["connection"]})
    array = [element for element in array if element["name"] != "databaseName"]
    array.extend(
        [
            {"id": 8050, "name": "databasePassword", "value": CANARY["password"]},
            {"id": 8040, "name": "databaseUsername", "value": CANARY["username"]},
            {"id": 8052, "name": "privateKeyFileName", "value": CANARY["keyfile"]},
            {"id": 99999, "name": "futureCredential", "value": CANARY["unknown"]},
            CANARY["malformed"],
            {"id": 8010, "name": "databaseName", "value": [CANARY["malformed"]]},
        ]
    )
    with caplog.at_level(logging.DEBUG):
        dto = project(array)
        with pytest.raises(JdbcConfigValidationError) as caught:
            JdbcConfigDTO(
                qradar_id=1,
                config_state=ConfigState.COMPLETE,
                databasePassword=CANARY["password"],
            )
        detachable = caught.value
        # A typed-field failure carries the input into Pydantic's error too.
        with pytest.raises(JdbcConfigValidationError) as typed:
            JdbcConfigDTO(qradar_id=CANARY["username"], config_state=ConfigState.COMPLETE)
        # A wholly malformed array fails closed without raising or leaking.
        unusable = project([CANARY["malformed"]])

    assert detachable.codes == ("extra_forbidden",)
    assert unusable.config_state is ConfigState.NOT_COLLECTED
    assert unusable.field_states == {}
    for error in (detachable, typed.value):
        assert error.__cause__ is None
        assert error.__context__ is None
    rendered = "".join(
        traceback.format_exception(type(detachable), detachable, detachable.__traceback__)
    )
    for canary in CANARY.values():
        assert canary not in rendered
    clean(dto, caplog, str(detachable) + str(unusable))


# --------------------------------------------------------------- grammar and bounds


def test_port_bounds() -> None:
    assert one("databasePort", "1").database_port == 1
    assert one("databasePort", "65535").database_port == 65535
    for bad in ("0", "65536", "12.5", "-1", " 5432"):
        dto = one("databasePort", bad)
        assert reason_of(dto, "databasePort") is FieldStateReason.UNSUPPORTED_VALUE
        assert dto.config_state is ConfigState.PARTIAL


def test_interval_units_and_bound() -> None:
    assert one("pollingInterval", "30").polling_interval_seconds == 30
    assert one("pollingInterval", "30M").polling_interval_seconds == 1800
    assert one("pollingInterval", "2H").polling_interval_seconds == 7200
    assert one("pollingInterval", "168H").polling_interval_seconds == 604800
    for bad in ("0", "0M", "169H", "10081M", "30S", "1.5H", "M30"):
        dto = one("pollingInterval", bad)
        assert reason_of(dto, "pollingInterval") is FieldStateReason.UNSUPPORTED_VALUE
        assert dto.config_state is ConfigState.PARTIAL


def test_eps_bigint_bound() -> None:
    assert one("epsThrottle", "0").eps_throttle == 0
    assert one("epsThrottle", str(2**63 - 1)).eps_throttle == 2**63 - 1
    for bad in (str(2**63), "-1", "1.5", "1e3"):
        dto = one("epsThrottle", bad)
        assert reason_of(dto, "epsThrottle") is FieldStateReason.UNSUPPORTED_VALUE


def test_boolean_exact() -> None:
    assert one("useSSL", "true").use_ssl is True
    assert one("useSSL", "false").use_ssl is False
    for bad in ("True", "FALSE", "1", "0", "yes", ""):
        dto = one("useSSL", bad)
        if bad == "":
            assert state_of(dto, "useSSL") is FieldStateCode.UNSET
            assert dto.use_ssl is None
        else:
            assert reason_of(dto, "useSSL") is FieldStateReason.UNSUPPORTED_VALUE


def test_identifier_grammars() -> None:
    assert one("eventTable", "tbl$#_.-1").table_name == "tbl$#_.-1"
    assert one("compareField", "InsertedAt").compare_field == "InsertedAt"
    assert one("databaseName", "app audit 2026").database_name == "app audit 2026"
    assert one("identifier", "10.0.0.1:1433").source_label == "10.0.0.1:1433"
    for name, bad in (
        ("eventTable", "tbl name"),
        ("eventTable", "tbl;DROP"),
        ("databaseName", "db;DROP TABLE x"),
        ("databaseName", "db@host"),
        ("databaseName", "db/sub"),
        ("identifier", "user@example.com"),
        ("databaseServerHostname", "host/path"),
        ("databaseServerHostname", "-bad.example.com"),
    ):
        dto = one(name, bad)
        assert reason_of(dto, name) is FieldStateReason.UNSUPPORTED_VALUE, name
        assert getattr(dto, MEMBER[name]) is None


def test_select_list_grammar() -> None:
    assert one("eventQuery", "*").select_list == "*"
    assert one("eventQuery", "id, event_time").select_list == "id, event_time"
    for bad in ("id, COUNT(*)", "id; DROP", "id,", "id,,event_time", "x" * 2049):
        dto = one("eventQuery", bad)
        assert reason_of(dto, "eventQuery") is FieldStateReason.UNSUPPORTED_VALUE, bad[:12]


def test_start_time_format() -> None:
    assert one("startTime", "2026-02-28 23:59").start_time_local == "2026-02-28 23:59"
    for bad in ("2026-13-20 10:30", "2026-02-30 10:30", "2026-11-20 25:00", "20/11/2026 10:30"):
        dto = one("startTime", bad)
        assert reason_of(dto, "startTime") is FieldStateReason.UNSUPPORTED_VALUE, bad
    assert state_of(one("startTime", ""), "startTime") is FieldStateCode.UNSET


def test_enum_fields() -> None:
    assert one("databaseType", "Snowflake").database_type == "Snowflake"
    assert one("queryList", "none").predefined_query == "none"
    for name, bad in (
        ("databaseType", "H2"),
        ("databaseType", "x" * 33),
        ("queryList", "everything"),
        ("queryList", "x" * 129),
    ):
        dto = one(name, bad)
        assert reason_of(dto, name) is FieldStateReason.UNSUPPORTED_VALUE, name


def test_missing_or_malformed_array_not_collected() -> None:
    for array in (None, "not a list", {}, [], [CANARY["malformed"]], [{"no_name": "x"}]):
        dto = project(array)
        assert dto.config_state is ConfigState.NOT_COLLECTED
        assert dto.field_states == {}
        assert all(getattr(dto, member) is None for member in MEMBER.values())

    unknown_only = project([{"id": 99999, "name": "futureCredential", "value": "x"}])
    assert unknown_only.config_state is ConfigState.PARTIAL
    assert all(getattr(unknown_only, member) is None for member in MEMBER.values())


@pytest.mark.asyncio
async def test_non_rest_provider_jdbc_read_unsupported() -> None:
    """The base default fails closed: no MCP fallback, no empty success."""
    with pytest.raises(CapabilityNotSupportedError) as caught:
        await MockQRadarProvider().walk_jdbc_configs()
    assert "MockQRadarProvider" in str(caught.value)
    assert "JDBC configuration" in str(caught.value)


# ------------------------------------------------------------------- round r1


def test_huge_digit_strings_do_not_raise() -> None:
    """F1: the length guard runs before int(); each numeric field fails closed."""
    huge = "1" * 5000
    for name in ("databasePort", "pollingInterval", "epsThrottle"):
        dto = one(name, huge)
        assert state_of(dto, name) is FieldStateCode.UNAVAILABLE
        assert reason_of(dto, name) is FieldStateReason.UNSUPPORTED_VALUE
        assert getattr(dto, MEMBER[name]) is None
        assert dto.config_state is ConfigState.PARTIAL


def test_internal_parse_error_detaches_context() -> None:
    """F2: even the internal Bad must not carry the strptime value in __context__."""
    with pytest.raises(Bad) as caught:
        start("2026-13-20 10:30")
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_host_parse_error_detaches_context() -> None:
    """B6: a hostname rejected after the IP parse must not carry the raw value."""
    raw = "jdbc:secret-canary-4d2e"
    with pytest.raises(Bad) as caught:
        host(raw)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    rendered = "".join(
        traceback.format_exception(type(caught.value), caught.value, caught.value.__traceback__)
    )
    assert raw not in rendered


def test_length_bounds_at_and_over_limit() -> None:
    """F3: the 255-character identifier bounds and the 253 host bound are pinned."""
    for name in ("databaseName", "schema", "eventTable", "compareField", "identifier"):
        accepted = one(name, "a" * 255)
        assert getattr(accepted, MEMBER[name]) == "a" * 255
        assert state_of(accepted, name) is FieldStateCode.SET
        rejected = one(name, "a" * 256)
        assert getattr(rejected, MEMBER[name]) is None
        assert state_of(rejected, name) is FieldStateCode.UNAVAILABLE
        assert reason_of(rejected, name) is FieldStateReason.UNSUPPORTED_VALUE

    host_253 = "a" * 63 + "." + "b" * 63 + "." + "c" * 63 + "." + "d" * 61
    assert len(host_253) == 253
    for name in ("databaseServerHostname", "MSSQLServerHostname"):
        accepted = one(name, host_253)
        assert getattr(accepted, MEMBER[name]) == host_253
        assert state_of(accepted, name) is FieldStateCode.SET
        rejected = one(name, host_253 + "d")
        assert getattr(rejected, MEMBER[name]) is None
        assert state_of(rejected, name) is FieldStateCode.UNAVAILABLE
        assert reason_of(rejected, name) is FieldStateReason.UNSUPPORTED_VALUE


def test_ipv6_literal_round_trip() -> None:
    """F3: only a canonical IP literal is accepted; a non-canonical one is refused."""
    dto = one("databaseServerHostname", "2001:db8::1")
    assert dto.database_host == "2001:db8::1"
    assert state_of(dto, "databaseServerHostname") is FieldStateCode.SET
    odd = one("databaseServerHostname", "2001:0db8::1")
    assert odd.database_host is None
    assert state_of(odd, "databaseServerHostname") is FieldStateCode.UNAVAILABLE
    assert reason_of(odd, "databaseServerHostname") is FieldStateReason.UNSUPPORTED_VALUE


def test_dto_is_frozen() -> None:
    """F3: assignment to a field of the DTO is refused."""
    dto = project(full_array())
    with pytest.raises(ValidationError):
        dto.qradar_id = 999


def test_repr_has_only_the_fixed_shape() -> None:
    """F3: repr shows identity and state only, never a field value."""
    assert repr(project(full_array())) == (
        "JdbcConfigDTO(qradar_id=562, protocol_type_id=8, "
        "config_state=COMPLETE, allowlist_version=1)"
    )


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("identifier", "jdbc:sqlserver"),
        ("identifier", "odbc:dsn"),
        ("identifier", "file:keystore"),
        ("databaseName", "-----BEGIN PRIVATE KEY-----"),
        ("eventTable", "server.pem"),
    ],
    ids=["jdbc", "odbc", "file", "pem-header", "pem-extension"],
)
def test_reachable_credential_shapes_rejected(name: str, value: str) -> None:
    """F4: every surviving SHAPES alternative is reachable and pinned."""
    dto = one(name, value)
    assert getattr(dto, MEMBER[name]) is None
    assert state_of(dto, name) is FieldStateCode.UNAVAILABLE
    assert reason_of(dto, name) is FieldStateReason.UNSUPPORTED_VALUE
    assert dto.config_state is ConfigState.PARTIAL


def test_key_file_shape_requires_a_path() -> None:
    """F9: a bare `config.key` is a benign name; the pattern needs a path separator."""
    plain = one("eventTable", "config.key")
    assert plain.table_name == "config.key"
    assert state_of(plain, "eventTable") is FieldStateCode.SET
    assert not any(shape.search("config.key") for shape in SHAPES)
    assert any(shape.search("/etc/ssl/keys/config.key") for shape in SHAPES)


def test_malformed_elements_make_partial(caplog: pytest.LogCaptureFixture) -> None:
    """F5: a non-dict element or a non-string name makes the config PARTIAL.

    All 18 allowed names are still reported, so the PARTIAL cannot come from a
    missing field: only the two skipped, never-read-further elements cause it.
    """
    array = full_array()
    array.extend(
        [
            CANARY["malformed"],  # non-dict: never read further
            {"name": ["futureField"], "value": CANARY["username"]},  # name not str
        ]
    )
    with caplog.at_level(logging.DEBUG):
        dto = project(array)
    assert dto.config_state is ConfigState.PARTIAL
    assert all(state_of(dto, name) is FieldStateCode.SET for name in MEMBER)
    clean(dto, caplog)


def test_non_int_id_is_id_name_mismatch() -> None:
    """F7: a float equal to the id, a bool and a string id are all mismatches."""
    for bad_id in (8030.0, True, "8030", None):
        array = [e for e in full_array() if e["name"] != "databasePort"]
        array.append({"id": bad_id, "name": "databasePort", "value": "5432"})
        dto = project(array)
        assert state_of(dto, "databasePort") is FieldStateCode.UNAVAILABLE
        assert reason_of(dto, "databasePort") is FieldStateReason.ID_NAME_MISMATCH
        assert dto.config_state is ConfigState.PARTIAL


def test_whitespace_only_value_is_unsupported() -> None:
    """F8: blank text is unsupported_value, not a SET value; exact empty is UNSET."""
    for name in ("databaseName", "schema", "eventTable", "compareField", "identifier"):
        dto = one(name, "   ")
        assert state_of(dto, name) is FieldStateCode.UNAVAILABLE
        assert reason_of(dto, name) is FieldStateReason.UNSUPPORTED_VALUE
        assert getattr(dto, MEMBER[name]) is None
        assert dto.config_state is ConfigState.PARTIAL
    assert state_of(one("databaseName", ""), "databaseName") is FieldStateCode.UNSET


def test_absent_allowed_name_is_missing() -> None:
    """F6: a name absent from the array is UNAVAILABLE/missing and PARTIAL."""
    array = [e for e in full_array() if e["name"] != "eventTable"]
    dto = project(array)
    assert dto.config_state is ConfigState.PARTIAL
    assert dto.table_name is None
    assert state_of(dto, "eventTable") is FieldStateCode.UNAVAILABLE
    assert reason_of(dto, "eventTable") is FieldStateReason.MISSING
    assert all(
        state_of(dto, name) is FieldStateCode.SET for name in MEMBER if name != "eventTable"
    )
