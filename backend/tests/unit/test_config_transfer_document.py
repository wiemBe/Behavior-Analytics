"""The configuration document contract (CFG-1a): order, canonical bytes, and the gates.

The file moves configuration between consoles, so two exports of one unchanged database differ
only in ``exported_at``, and a secret-shaped key stops both directions.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from app.config_transfer.document import (
    MAX_DOCUMENT_BYTES,
    SECRET_KEYS,
    SECTION_ORDER,
    TOP_LEVEL_FIELDS,
    DocumentError,
    build_document,
    check_label,
    dumps,
    format_timestamp,
    parse,
    secret_key_paths,
)

# The order the design's file-format section fixes.
DOCUMENTED_SECTIONS = (  # noqa: SIM905 - a list literal would restate the constant, line for line
    "log_source_settings classification_overrides classification_rules "
    "analytics_rule_settings technique_mappings waf_source_profiles ips_source_profiles "
    "identity_source_profiles property_watches alert_policies outage_alert_policies "
    "alert_suppressions notification_destinations notification_rules email_templates "
    "email_recipients ldap_config search_cadence scheduled_searches custom_rules "
    "dashboards dashboard_prefs"
).split()


def _built(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "product_version": "0.1.16",
        "exported_at": datetime(2026, 11, 14, 18, 0, tzinfo=UTC),
        "source_label": "preprod",
        "sections": {},
    }
    payload.update(overrides)
    return build_document(**payload)


def _document(**overrides: Any) -> dict[str, Any]:
    """A small legal document: two sections, two rows."""
    sections = {
        "search_cadence": [{"family": "WAF_COLLECTION", "cadence_seconds": 900}],
        "log_source_settings": [
            {"log_source": {"name": "Edge WAF", "type_name": "F5"}, "criticality": "HIGH"}
        ],
    }
    return _built(sections=sections, **overrides)


def test_the_document_is_ordered_the_way_the_design_fixes_it() -> None:
    assert list(SECTION_ORDER) == DOCUMENTED_SECTIONS
    assert "search_query_version" not in SECTION_ORDER
    rendered = json.loads(dumps(_document()), object_pairs_hook=list)
    assert [key for key, _ in rendered] == list(TOP_LEVEL_FIELDS)
    assert [key for key, _ in dict(rendered)["sections"]] == list(SECTION_ORDER)
    empty = parse('{"schema_version": 1}')["sections"]
    assert list(empty) == list(SECTION_ORDER) and all(rows == [] for rows in empty.values())


def test_two_exports_differ_only_in_exported_at() -> None:
    document = _document(exported_at=datetime(2026, 11, 14, 18, 0, tzinfo=UTC))
    rendered = dumps(document)
    first = rendered.splitlines()
    second = dumps(_document(exported_at=datetime(2026, 11, 14, 18, 5, tzinfo=UTC))).splitlines()
    assert len(first) == len(second)
    differing = [
        index for index, pair in enumerate(zip(first, second, strict=True)) if pair[0] != pair[1]
    ]
    assert differing == [3]
    assert first[3] == '  "exported_at": "2026-11-14T18:00:00Z",'
    assert second[3] == '  "exported_at": "2026-11-14T18:05:00Z",'
    assert first[0] == "{" and first[1] == '  "schema_version": 1,' and "\t" not in rendered
    assert rendered.endswith("}\n") and not rendered.endswith("\n\n")
    assert parse(rendered) == document


@pytest.mark.parametrize(
    ("raw", "reason"),
    [
        (b"{", "not JSON"),
        (b'{"schema_version": 1} extra', "not JSON"),
        (b'{"schema_version": 1, "product_version": "\xff"}', "not JSON"),
        (b"[]", "one JSON object"),
        (b'{"schema_version": 0}', "schema_version 0 is not supported"),
        (b'{"schema_version": 2}', "schema_version 2 is not supported"),
        (b'{"sections": {}}', "schema_version must be the integer 1"),
        (b'{"schema_version": true}', "schema_version must be the integer 1"),
        (b'{"schema_version": 1.0}', "schema_version must be the integer 1"),
        (b'{"schema_version": 1, "notes": "x"}', "unknown top-level key 'notes'"),
        (b'{"schema_version": 1, "token": "x"}', "unknown top-level key 'token'"),
        (b'{"schema_version": 1, "sections": []}', "sections must be an object"),
        (b'{"schema_version": 1, "sections": {"x": []}}', "unknown section 'x'"),
        (b'{"schema_version": 1, "sections": {"ldap_config": {}}}', "must be a list"),
        (b'{"schema_version": 1, "sections": {"ldap_config": [1]}}', "not an object"),
        (b'{"schema_version": 1, "exported_at": "14/11/2026"}', "exported_at must be"),
    ],
)
def test_a_document_outside_the_contract_is_refused(raw: bytes, reason: str) -> None:
    with pytest.raises(DocumentError, match=reason):
        parse(raw)


def test_the_document_cap_is_five_megabytes() -> None:
    prefix = b'{"schema_version": 1, "sections": {"email_templates": [{"text_body": "'
    suffix = b'"}]}}'
    padding = MAX_DOCUMENT_BYTES - len(prefix) - len(suffix)

    def sized(extra: int) -> bytes:
        return prefix + b"x" * (padding + extra) + suffix

    document = parse(sized(0))
    assert len(document["sections"]["email_templates"][0]["text_body"]) > 4_000_000
    with pytest.raises(DocumentError, match="the cap is 5000000"):
        parse(sized(1))


@pytest.mark.parametrize("key", sorted(SECRET_KEYS))
def test_a_secret_shaped_key_is_refused_in_both_directions(key: str) -> None:
    sections = {"notification_destinations": [{"name": "soc-hook", key: "x"}]}
    with pytest.raises(DocumentError, match=f"secret key .*{key}"):
        parse(json.dumps({"schema_version": 1, "sections": sections}))
    with pytest.raises(DocumentError, match=f"secret key .*{key}"):
        dumps({"schema_version": 1, "sections": sections})


@pytest.mark.parametrize("key", sorted(SECRET_KEYS))
def test_a_secret_inside_an_opaque_dashboard_config_is_refused(key: str) -> None:
    sections = {"dashboards": [{"name": "Edge", "layout": {"widgets": [{"config": {key: "x"}}]}}]}
    with pytest.raises(DocumentError, match="secret key"):
        parse(json.dumps({"schema_version": 1, "sections": sections}))


def test_the_refusal_names_the_path_and_ignores_the_key_s_case() -> None:
    paths = secret_key_paths({"sections": {"notification_destinations": [{"headers": {}}]}})
    assert paths == ["sections.notification_destinations[0].headers"]
    assert secret_key_paths({"token_hash": "x"}) == ["token_hash"]
    with pytest.raises(DocumentError, match="secret key"):
        parse('{"schema_version": 1, "sections": {"ldap_config": [{"Password": "x"}]}}')


def test_a_webhook_url_is_configuration_only_inside_destinations() -> None:
    row = {"name": "soc-hook", "url": "https://hooks.example.internal/soc", "allow_http": False}
    raw = json.dumps({"schema_version": 1, "sections": {"notification_destinations": [row]}})
    assert parse(raw)["sections"]["notification_destinations"][0]["url"] == row["url"]
    ldap = {"urls": ["ldaps://directory.example.internal"]}
    document = parse(json.dumps({"schema_version": 1, "sections": {"ldap_config": [ldap]}}))
    assert document["sections"]["ldap_config"][0]["urls"] == ldap["urls"]
    # Anywhere else it is a leaked endpoint; the opaque dashboard config may not carry it.
    with pytest.raises(DocumentError, match=r"secret key .*url"):
        parse('{"schema_version": 1, "sections": {"ldap_config": [{"url": "ldaps://d"}]}}')
    widget = {"layout": {"widgets": [{"config": {"url": "https://hooks.example.internal"}}]}}
    with pytest.raises(DocumentError, match=r"secret key .*url"):
        parse(json.dumps({"schema_version": 1, "sections": {"dashboards": [widget]}}))


@pytest.mark.parametrize("label", ["preprod", "sit-1", "prod.eu_west", "A" * 64])
def test_a_legal_source_label_is_returned(label: str) -> None:
    assert check_label(label) == label


@pytest.mark.parametrize(
    "label",
    ["", "-preprod", ".preprod", "pre prod", "https://qradar.example", "a" * 65, "prod/eu",
     "preprod\n", "A" * 64 + "\n"],
)
def test_a_console_url_a_space_a_newline_or_a_long_name_is_not_a_label(label: str) -> None:
    with pytest.raises(DocumentError, match="does not match"):
        check_label(label)


def test_a_timestamp_is_utc_with_a_z_suffix() -> None:
    assert format_timestamp(datetime(2026, 11, 14, 18, 0, tzinfo=UTC)) == "2026-11-14T18:00:00Z"
    plus_two = timezone(timedelta(hours=2))
    shifted = datetime(2026, 11, 14, 20, 0, tzinfo=plus_two)
    assert format_timestamp(shifted) == "2026-11-14T18:00:00Z"
    # A naive value is taken as UTC rather than guessed from the host zone.
    assert format_timestamp(datetime(2026, 11, 14, 18, 0)) == "2026-11-14T18:00:00Z"


@pytest.mark.parametrize(
    "value", ["2026-11-14 18:00:00", "2026-1-4T18:00:00Z", "2026-13-01T00:00:00Z", 0, None]
)
def test_an_illegal_exported_at_is_refused(value: object) -> None:
    with pytest.raises(DocumentError, match="exported_at must be"):
        _built(exported_at=value)
