"""The configuration bundle's document contract: section order, canonical bytes and the read gates.

CFG-1 (D-78). A secret-free JSON file moves operator configuration onto another console; this
module is the contract both commands share and is pure: no session, no table, no provider. `parse`
refuses a schema this build does not read, an unknown key or section, a bad shape and a
secret-shaped key; `dumps` refuses only a secret-shaped key and keeps the order it is given.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

#: The only version this build writes, and the newest it reads; a newer file is refused whole.
SCHEMA_VERSION = 1

MAX_DOCUMENT_BYTES = 5_000_000


def _names(text: str) -> tuple[str, ...]:
    """A tuple from a space-separated list, so a constant stays one readable block."""
    return tuple(text.split())


#: `product_version` is informational: copied, never interpreted, and it gates nothing.
TOP_LEVEL_FIELDS = _names("schema_version product_version exported_at source_label sections")

#: The sections in file order; a scheduled search carries its own query, not a section.
SECTION_ORDER = _names(
    "log_source_settings classification_overrides classification_rules "
    "analytics_rule_settings technique_mappings waf_source_profiles ips_source_profiles "
    "identity_source_profiles property_watches alert_policies outage_alert_policies "
    "alert_suppressions notification_destinations notification_rules email_templates "
    "email_recipients ldap_config search_cadence scheduled_searches custom_rules "
    "dashboards dashboard_prefs"
)

#: Keys that fail export and import wherever they appear (AGENTS §2 rule 5), case-insensitively.
SECRET_KEYS: frozenset[str] = frozenset(
    _names(
        "sec_token hashed_password hmac_secret headers password token token_hash "
        "smtp_password authorization"
    )
)

#: The one secret-shaped key the file keeps, only here: a webhook URL is configuration
#: (D-78 open decision 7); the HMAC secret and header block are not.
URL_KEY = "url"
URL_SECTION = "notification_destinations"

#: A source label is a short file-name-shaped token; a console host, the token or a URL fails it.
#: Matched with `fullmatch`: `$` alone also matches before one trailing newline.
LABEL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

#: The only datetime spelling in the file.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
_TIMESTAMP_HINT = "a UTC timestamp such as 2026-11-14T18:00:00Z"


class DocumentError(ValueError):
    """A document breaks the contract; the command refuses it before any session is opened."""


def format_timestamp(moment: datetime) -> str:
    """One datetime as the file spells it. A naive value is taken as UTC."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC).strftime(TIMESTAMP_FORMAT)


def _check_timestamp(value: Any, field: str) -> str:
    if not isinstance(value, str) or not _TIMESTAMP_PATTERN.fullmatch(value):
        raise DocumentError(f"{field} must be {_TIMESTAMP_HINT}")
    try:
        datetime.strptime(value, TIMESTAMP_FORMAT)
    except ValueError as exc:  # a well-shaped string naming no real day
        raise DocumentError(f"{field} must be {_TIMESTAMP_HINT}") from exc
    return value


def check_label(label: str) -> str:
    """The label, or `DocumentError` when it is not a legal source label."""
    if not isinstance(label, str) or not LABEL_PATTERN.fullmatch(label):
        raise DocumentError(f"source label {label!r} does not match {LABEL_PATTERN.pattern}")
    return label


def ordered_sections(sections: Mapping[str, Any]) -> dict[str, list[Any]]:
    """All sections in file order, a missing one empty; anything unknown is refused whole."""
    for name in sections:
        if name not in SECTION_ORDER:
            raise DocumentError(f"unknown section {name!r}")
    for name in SECTION_ORDER:
        rows = sections.get(name, [])
        if not isinstance(rows, list):
            raise DocumentError(f"section {name!r} must be a list")
        if any(not isinstance(row, Mapping) for row in rows):
            raise DocumentError(f"section {name!r} holds a row that is not an object")
    return {name: list(sections.get(name, [])) for name in SECTION_ORDER}


def build_document(
    *,
    product_version: str,
    exported_at: datetime | str,
    source_label: str,
    sections: Mapping[str, Any],
) -> dict[str, Any]:
    """The whole document, top-level keys and every section in the fixed order."""
    stamp = (
        format_timestamp(exported_at)
        if isinstance(exported_at, datetime)
        else _check_timestamp(exported_at, "exported_at")
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "product_version": product_version,
        "exported_at": stamp,
        "source_label": check_label(source_label),
        "sections": ordered_sections(sections),
    }


def dumps(document: Mapping[str, Any]) -> str:
    """Canonical bytes: two-space indent, one trailing newline, insertion order kept.

    A secret-shaped key is refused, so an export cannot emit one by mistake.
    """
    check_secrets(document)
    return json.dumps(document, indent=2, ensure_ascii=False) + "\n"


def parse(raw: bytes | str) -> dict[str, Any]:
    """Read a document, refusing an oversized file, bad JSON, an unknown key, a newer schema
    or a secret-shaped key. A missing section is an empty list, not an error."""
    data = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(data) > MAX_DOCUMENT_BYTES:
        raise DocumentError(f"the file is {len(data)} bytes; the cap is {MAX_DOCUMENT_BYTES}")
    try:
        payload = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DocumentError(f"the file is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise DocumentError("the file must hold one JSON object")
    for name in payload:
        if name not in TOP_LEVEL_FIELDS:
            raise DocumentError(f"unknown top-level key {name!r}")

    version = payload.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise DocumentError(f"schema_version must be the integer {SCHEMA_VERSION}")
    if version != SCHEMA_VERSION:
        raise DocumentError(
            f"schema_version {version} is not supported by this build; it reads {SCHEMA_VERSION}"
        )
    if payload.get("exported_at") is not None:
        _check_timestamp(payload["exported_at"], "exported_at")

    sections = payload.get("sections", {})
    if not isinstance(sections, Mapping):
        raise DocumentError("sections must be an object")
    document: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for name in TOP_LEVEL_FIELDS[1:-1]:
        if name in payload:
            document[name] = payload[name]
    document["sections"] = ordered_sections(sections)
    check_secrets(document)
    return document


def secret_key_paths(value: Any, *, section: str | None = None, path: str = "") -> list[str]:
    """Every path in the tree whose key is secret-shaped.

    `url` is allowed only inside `notification_destinations`; anywhere else, including inside a
    dashboard widget's opaque `config`, it is a leaked endpoint.
    """
    found: list[str] = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key)
            here = f"{path}.{name}" if path else name
            lowered = name.lower()
            if lowered in SECRET_KEYS or (lowered == URL_KEY and section != URL_SECTION):
                found.append(here)
                continue
            child_section = name if path == "sections" else section
            found.extend(secret_key_paths(item, section=child_section, path=here))
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found.extend(secret_key_paths(item, section=section, path=f"{path}[{index}]"))
    return found


def check_secrets(document: Any) -> None:
    """Raise `DocumentError` naming the first secret-shaped key in the document."""
    paths = secret_key_paths(document)
    if paths:
        raise DocumentError(f"secret key {paths[0]} is not allowed in the configuration file")
