"""POST and PATCH /searches keep a documented JSON body.

The handlers parse a dict so an authoring failure can be the reasons object.
The OpenAPI request body is still the create and update models.
"""

from __future__ import annotations

from app.main import create_app

_FIELDS = ("aql_query", "schedule_cron", "enabled")


def _body_properties(method: str, path: str) -> dict[str, object]:
    operation = create_app().openapi()["paths"][path][method]
    schema = operation["requestBody"]["content"]["application/json"]["schema"]
    properties = schema.get("properties")
    assert isinstance(properties, dict), schema
    return properties


def test_search_write_request_bodies_name_the_model_fields() -> None:
    post = _body_properties("post", "/api/v1/searches")
    patch = _body_properties("patch", "/api/v1/searches/{search_id}")
    for field in _FIELDS:
        assert field in post, field
        assert field in patch, field
