"""FIX-94: slim `fields=` lists must name only fields the real QRadar API accepts.

The fixture holds key names (never values) copied from the LAB-50 probe at API 20.0 and 29.0.
A strict transport rejects any requested field that is not in the fixture's key set with the
verbatim 422 body the lab console returns, so a bad field cannot hide behind a permissive mock.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from app.providers.qradar_rest import (
    JDBC_LIST_FIELDS,
    LOG_SOURCE_LIST_FIELDS,
    OFFENSE_LIST_FIELDS,
    RULE_LIST_FIELDS,
    QRadarRestProvider,
    _parse_api_version,
    log_source_list_fields,
)

FIXTURE = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "qradar_api_fields.json").read_text()
)
VERSIONS = FIXTURE["versions"]
KEYS = FIXTURE["endpoints"]

# The field strings the provider sends inline (no module constant), mirrored here so the
# static subset check still covers them; the strict-transport test drives them for real.
PROPERTY_LIST_FIELDS = {
    "regex_properties": "id,identifier,name,property_type,use_for_rule_engine,auto_discovered",
    "property_expressions": "regex_property_identifier,log_source_type_id,log_source_id,enabled",
}

ENDPOINT_BY_PATH = {
    "/api/config/event_sources/log_source_management/log_sources": "log_sources",
    "/api/analytics/rules": "rules",
    "/api/analytics/building_blocks": "building_blocks",
    "/api/siem/offenses": "offenses",
    "/api/config/event_sources/custom_properties/regex_properties": "regex_properties",
    "/api/config/event_sources/custom_properties/property_expressions": "property_expressions",
}

_LIST_FIELDS = frozenset({
    "group_ids", "categories", "building_block_ids", "log_source_type_ids", "rule_responses",
    "usernames", "source_address_ids", "local_destination_address_ids", "log_sources", "rules",
})
_BOOL_FIELDS = frozenset({"enabled", "auto_discovered", "internal", "use_for_rule_engine"})
_STR_FIELDS = frozenset({
    "name", "identifier", "regex_property_identifier", "description", "status", "origin",
    "owner", "offense_source", "source_network", "assigned_to",
})


def _rejection_body(field: str) -> str:
    """The QRadar 422 body, verbatim, naming the first unrecognised field."""
    return (
        '{"code":30,"message":"Specified field \\"' + field +
        '\\" is not recognized by this endpoint for media type \\"application/json\\""}'
    )


def _sample(field: str) -> object:
    if field == "protocol_type_id":
        return 8  # the JDBC consumer keeps only protocol 8
    if field == "protocol_parameters":
        # One named, well-formed parameter so project_jdbc_config does not mark it NOT_COLLECTED.
        return [{"name": "databaseServerHostname", "id": 8020, "value": "db.example"}]
    if field in _LIST_FIELDS:
        return []
    if field in _BOOL_FIELDS:
        return True
    if field in _STR_FIELDS:
        return "sample"
    return 1


def _strict_transport(version: str, seen: list[httpx.Request]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        endpoint = ENDPOINT_BY_PATH[request.url.path]
        valid = set(KEYS[endpoint][version])
        fields_param = request.url.params.get("fields")
        if fields_param is None:
            row_keys = sorted(valid)
        else:
            requested = fields_param.split(",")
            unknown = [field for field in requested if field not in valid]
            if unknown:
                return httpx.Response(
                    422,
                    content=_rejection_body(unknown[0]).encode(),
                    headers={"Content-Type": "application/json"},
                )
            row_keys = requested
        return httpx.Response(200, json=[
            {**{key: _sample(key) for key in row_keys}, "id": index}
            for index in range(1, 3)
        ])

    return handler


@pytest.mark.parametrize("version", VERSIONS)
def test_every_list_is_subset_of_real_keys(version: str) -> None:
    lists = {
        "offenses": OFFENSE_LIST_FIELDS,
        "rules": RULE_LIST_FIELDS,
        "building_blocks": RULE_LIST_FIELDS,
        "log_sources_jdbc": JDBC_LIST_FIELDS,
    }
    for endpoint, fields in lists.items():
        assert set(fields.split(",")) <= set(KEYS[endpoint][version]), (endpoint, version)
    assert set(log_source_list_fields(version).split(",")) <= set(KEYS["log_sources"][version])
    for endpoint, fields in PROPERTY_LIST_FIELDS.items():
        assert set(fields.split(",")) <= set(KEYS[endpoint][version]), (endpoint, version)


@pytest.mark.parametrize("version,include", [
    ("20.0", False), ("27.0", False), ("9.0", False),
    ("28.0", True), ("29.0", True), ("30.1", True),
    ("abc", False), ("", False), (None, False), (30.1, False),
])
def test_version_parse(version: object, include: bool) -> None:
    fields = log_source_list_fields(version).split(",")
    assert ("log_source_identifiers" in fields) is include
    if isinstance(version, str) and version.replace(".", "").isdigit():
        major, _, minor = version.partition(".")
        assert _parse_api_version(version) == (int(major), int(minor or 0))
    else:
        assert _parse_api_version(version) is None


async def _drive(version: str) -> tuple[list[httpx.Request], dict[str, Any]]:
    async def no_sleep(_seconds: float) -> None:
        return None

    seen: list[httpx.Request] = []
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(_strict_transport(version, seen)),
        base_url="https://qradar.example/api",
        headers={"Version": version},
    )
    provider = QRadarRestProvider(
        base_url="https://qradar.example", sec_token="t", api_version=version, client=client,
        sleep=no_sleep,
    )
    try:
        result = {
            "log_walk": await provider.walk_log_sources(),
            "log_list": await provider.list_log_sources(),
            "rules": await provider.list_rules(),
            "blocks": await provider.list_building_blocks(),
            "offenses": await provider.list_offenses(open_only=False),
            "jdbc": await provider.walk_jdbc_configs(),
            "properties": await provider.list_custom_properties(),
            "catalog": await provider.read_property_catalog(),
        }
    finally:
        await provider.aclose()
    return seen, result


@pytest.mark.asyncio
@pytest.mark.parametrize("version", VERSIONS)
async def test_walks_succeed_against_strict_transport(version: str) -> None:
    seen, result = await _drive(version)

    log_walk = result["log_walk"]
    assert log_walk.complete and log_walk.pages_failed == 0 and len(log_walk.sources) == 2
    assert len(result["log_list"]) == 2
    assert len(result["rules"]) == 2 and len(result["blocks"]) == 2
    offenses = result["offenses"]
    assert offenses.failure_kind is None and offenses.pages_failed == 0
    jdbc = result["jdbc"]
    assert jdbc.complete and jdbc.failure_code is None
    assert len(result["properties"]) == 2
    catalog = result["catalog"]
    assert catalog.failure is None and len(catalog.properties) == 2

    # The log-source inventory (not the filtered JDBC walk) carries the version-chosen list.
    inventory = [r for r in seen if r.url.path.endswith("/log_sources")
                 and "filter" not in r.url.params]
    assert inventory
    sent = {r.url.params["fields"] for r in inventory}
    assert sent == {log_source_list_fields(version)}
    assert sent == {LOG_SOURCE_LIST_FIELDS if version == "20.0"
                    else LOG_SOURCE_LIST_FIELDS + ",log_source_identifiers"}
