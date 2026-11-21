"""Pure raw-array -> safe DTO projector (design §2): no I/O, no logging.

Unknown names are dropped before their values are read (JDBC-0.report.md:16-54).
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from app.jdbc.dto import (
    ALLOWLIST_VERSION,
    ConfigState,
    FieldState,
    FieldStateCode,
    FieldStateReason,
    JdbcConfigDTO,
)

UNVERIFIED = frozenset({20, 21, 37, 42, 49, 55, 67})
IDENT = re.compile(r"[A-Za-z0-9$#_.-]+")
D = re.compile(r"[A-Za-z0-9 $#_.-]+")
H = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*"
)
L = re.compile(r"[A-Za-z0-9 ._:-]+")
N = re.compile(r"[0-9]+")
V = re.compile(r"([0-9]+)([MH]?)")
T = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}")
TYPES = frozenset({"MSDE", "MySQL", "Postgres", "Oracle", "Sybase", "DB2", "Informix", "Snowflake"})
QUERIES = frozenset(
    {"none", "Microsoft SharePoint", "Microsoft Endpoint Protection",
     "Juniper Networks AVT", "IBM Privileged Session Recorder"}
)
#: Only shapes the grammars can admit: `:` in labels, spaces in D/L. The `=`,
#: `/` and `@` forms are unreachable -- those grammars exclude the characters.
SHAPES = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(?:jdbc|odbc)\s*:", r"\bfile\s*:", r"-----BEGIN [A-Z ]*PRIVATE KEY-----",
        r"\.(?:pem|pfx|p12)\b", r"[/\\][\w.\-]+\.key\b",
    )
)


class Bad(Exception):
    """Internal marker: a value failed its grammar or bound."""


def check(value: str, pattern: re.Pattern[str] | None, limit: int) -> str:
    if not value.strip() or len(value) > limit:
        raise Bad
    if pattern is not None and pattern.fullmatch(value) is None:
        raise Bad
    if any(shape.search(value) for shape in SHAPES):
        raise Bad
    return value


def host(value: str) -> str:
    canonical: str | None = None
    with suppress(ValueError):  # no ipaddress value in a raised error's __context__
        canonical = str(ipaddress.ip_address(value))
    if canonical is not None:
        if canonical != value:
            raise Bad
        return value
    return check(value, H, 253)


def integer(value: str, low: int, high: int) -> int:
    # Length before int(): 19 digits covers the widest range (signed bigint),
    # and int() itself raises past 4300 digits.
    if len(value) > 19 or N.fullmatch(value) is None:
        raise Bad
    number = int(value)
    if not low <= number <= high:
        raise Bad
    return number


def boolean(value: str) -> bool:
    if value not in ("true", "false"):
        raise Bad
    return value == "true"


def interval(value: str) -> int:
    match = V.fullmatch(value)
    if match is None or len(match[1]) > 6:  # 604800 is six digits; guard before int()
        raise Bad
    seconds = int(match[1]) * {"": 1, "M": 60, "H": 3600}[match[2]]
    if not 1 <= seconds <= 604800:
        raise Bad
    return seconds


def start(value: str) -> str:
    if T.fullmatch(value) is None:
        raise Bad
    with suppress(ValueError):  # no strptime value in a raised error's __context__
        datetime.strptime(value, "%Y-%m-%d %H:%M")
        return value
    raise Bad


def select(value: str) -> str:
    if value != "*" and any(not IDENT.fullmatch(part.strip()) for part in value.split(",")):
        raise Bad
    return check(value, None, 2048)  # single length/shape scan


def enum(value: str, allowed: frozenset[str], limit: int) -> str:
    if len(value) > limit or value not in allowed:
        raise Bad
    return value  # exact set membership makes a shape scan redundant


@dataclass(frozen=True)
class Field:
    name: str
    member: str
    pid: int
    parse: Callable[[str], object]


#: The exact 18 names of design §2, with JDBC-0's id for each.
FIELDS = (
    Field("databaseType", "database_type", 8005, lambda v: enum(v, TYPES, 32)),
    Field("databaseServerHostname", "database_host", 8020, host),
    Field("MSSQLServerHostname", "tls_hostname", 8280, host),
    Field("databasePort", "database_port", 8030, lambda v: integer(v, 1, 65535)),
    Field("databaseName", "database_name", 8010, lambda v: check(v, D, 255)),
    Field("schema", "schema_name", 8017, lambda v: check(v, D, 255)),
    Field("eventTable", "table_name", 8090, lambda v: check(v, IDENT, 255)),
    Field("eventQuery", "select_list", 8100, select),
    Field("compareField", "compare_field", 8110, lambda v: check(v, IDENT, 255)),
    Field("pollingInterval", "polling_interval_seconds", 8140, interval),
    Field("startTime", "start_time_local", 8130, start),
    Field("usePreparedStatements", "prepared_statements", 8120, boolean),
    Field("queryList", "predefined_query", 8080, lambda v: enum(v, QUERIES, 128)),
    Field("epsThrottle", "eps_throttle", 8150, lambda v: integer(v, 0, 2**63 - 1)),
    Field("identifier", "source_label", 8000, lambda v: check(v, L, 255)),
    Field("useSSL", "use_ssl", 8210, boolean),
    Field("allowUntrusted", "allow_untrusted", 8320, boolean),
    Field("enableAdvancedOptions", "advanced_options", 8290, boolean),
)
BY_NAME = {field.name: field for field in FIELDS}
ALLOWED_UPSTREAM_NAMES = frozenset(BY_NAME)


def blank(identity: dict[str, Any], state: ConfigState) -> JdbcConfigDTO:
    return JdbcConfigDTO(**identity, config_state=state, field_states={})


def project_jdbc_config(
    *, qradar_id: int, protocol_type_id: int | None, protocol_parameters: object
) -> JdbcConfigDTO:
    """Project one raw array to a containable DTO; reject rather than truncate.

    A repeated allowed name, id/name mismatch or wrong value type nulls the
    field with a fixed reason and makes the config PARTIAL -- never
    last-value-wins. No raw element reaches Pydantic or an exception message.
    """
    ident = {"qradar_id": qradar_id, "protocol_type_id": protocol_type_id,
             "allowlist_version": ALLOWLIST_VERSION}
    if protocol_type_id in UNVERIFIED:
        return blank(ident, ConfigState.UNVERIFIED_PROTOCOL)
    if protocol_type_id != 8:
        return blank(ident, ConfigState.NOT_JDBC)
    if not isinstance(protocol_parameters, list) or not protocol_parameters:
        return blank(ident, ConfigState.NOT_COLLECTED)

    seen: set[str] = set()
    dupes: set[str] = set()
    reasons: dict[str, FieldStateReason] = {}
    parsed: dict[str, object] = {}
    named = 0
    malformed = False
    for element in protocol_parameters:
        if not isinstance(element, dict) or not isinstance(element.get("name"), str):
            malformed = True  # skip it, but it may have hidden an allowed name
            continue
        named += 1
        spec = BY_NAME.get(element["name"])
        if spec is None:
            continue  # unknown name: dropped before the value is read
        member = spec.member
        if member in seen:
            dupes.add(member)
            continue
        seen.add(member)
        # bool is an int subclass and 8030.0 == 8030: neither is a valid id.
        if type(element.get("id")) is not int or element["id"] != spec.pid:
            reasons[member] = FieldStateReason.ID_NAME_MISMATCH
            continue
        value = element.get("value")
        if not isinstance(value, str):
            reasons[member] = FieldStateReason.WRONG_TYPE
            continue
        if value == "":
            parsed[member] = None
            continue
        try:
            parsed[member] = spec.parse(value)
        except Bad:
            reasons[member] = FieldStateReason.UNSUPPORTED_VALUE

    if not named:
        return blank(ident, ConfigState.NOT_COLLECTED)

    values: dict[str, Any] = {}
    states: dict[str, FieldState] = {}
    partial = malformed
    for spec in FIELDS:
        member = spec.member
        reason = FieldStateReason.DUPLICATE_NAME if member in dupes else (
            reasons.get(member) or (None if member in seen else FieldStateReason.MISSING))
        if reason is not None:
            values[member] = None
            states[member] = FieldState(code=FieldStateCode.UNAVAILABLE, reason=reason)
            partial = True
        elif member in parsed and parsed[member] is not None:
            values[member] = parsed[member]
            states[member] = FieldState(code=FieldStateCode.SET)
        else:
            values[member] = None
            states[member] = FieldState(code=FieldStateCode.UNSET)
    return JdbcConfigDTO(
        **ident,
        **values,
        field_states=states,
        config_state=ConfigState.PARTIAL if partial else ConfigState.COMPLETE,
    )
