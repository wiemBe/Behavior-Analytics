"""Development CORS policy, and the hardened site's closed docs and origin list."""

import pytest
from fastapi.testclient import TestClient

from app.core.config import Environment, get_settings
from app.main import create_app


def test_development_origin_can_preflight_put() -> None:
    with TestClient(create_app()) as client:
        response = client.options(
            "/api/v1/admin/search-cadence/WAF_COLLECTION",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "PUT",
            },
        )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "PUT" in response.headers["access-control-allow-methods"].split(", ")


def test_staging_hides_docs_and_does_not_add_the_dev_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    staging = get_settings().model_copy(
        update={
            "environment": Environment.STAGING,
            "public_origin": "https://soc.example",
        }
    )
    monkeypatch.setattr("app.main.get_settings", lambda: staging)
    app = create_app()
    assert app.docs_url is None
    assert app.openapi_url is None
    with TestClient(app) as client:
        dev_origin = client.options(
            "/api/v1/health/live",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        site_origin = client.options(
            "/api/v1/health/live",
            headers={
                "Origin": "https://soc.example",
                "Access-Control-Request-Method": "GET",
            },
        )
    assert dev_origin.headers.get("access-control-allow-origin") != "http://localhost:3000"
    assert site_origin.headers["access-control-allow-origin"] == "https://soc.example"
