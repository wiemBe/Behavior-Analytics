"""Development CORS policy."""

from fastapi.testclient import TestClient

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
