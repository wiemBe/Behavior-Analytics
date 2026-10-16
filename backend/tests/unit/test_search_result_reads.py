"""OpenAPI pins for the two search-aggregate reads.

Shape, ordering and the one-statement behaviour live in the integration file.
This file pins the bounds the handler declares, and that both routes sit in the
catalogue the OpenAPI auth guard walks (401 without a token). The guard's 403
cases are mutating methods only: a read-only bearer holds ``read:*``, which
satisfies the searches router's ``read:searches`` dependency.
"""

from __future__ import annotations

from app.main import create_app

_KEYS = "/api/v1/searches/{search_id}/metric-keys"
_GROUPS = "/api/v1/searches/{search_id}/latest-groups"


def _limit(path: str) -> dict:
    spec = create_app().openapi()["paths"][path]["get"]
    found = [param for param in spec["parameters"] if param["name"] == "limit"]
    assert len(found) == 1, spec["parameters"]
    return found[0]["schema"]


def _bounded(schema: dict, *, default: int, maximum: int) -> None:
    assert schema["type"] == "integer", schema
    assert schema["default"] == default, schema
    assert schema["maximum"] == maximum, schema
    exclusive = schema.get("exclusiveMinimum")
    # OpenAPI 3.0 uses minimum + exclusiveMinimum: true; 3.1 uses the number.
    assert exclusive == 0 or (
        schema.get("minimum") == 0 and exclusive is True
    ) or schema.get("minimum") == 1, schema


def test_metric_keys_limit_defaults_to_200_and_caps_at_1000() -> None:
    _bounded(_limit(_KEYS), default=200, maximum=1000)


def test_latest_groups_limit_defaults_to_50_and_caps_at_500() -> None:
    _bounded(_limit(_GROUPS), default=50, maximum=500)


def test_both_reads_are_in_the_openapi_guard_catalogue() -> None:
    from tests.unit.test_route_auth_guard import ROUTES

    gets = {path for method, path in ROUTES if method == "GET"}
    assert _KEYS in gets
    assert _GROUPS in gets
