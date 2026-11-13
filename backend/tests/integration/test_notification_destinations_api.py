"""Named webhook destination CRUD and test sends through a mocked transport."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text

from app.alerts.notifiers.webhook_v1 import SIGNATURE_HEADER, build_webhook_v1_test
from app.api.routes import notification_destinations as routes
from app.core.config import get_settings
from app.core.rate_limit import limiter
from app.main import create_app
from app.models.alert import AlertNotification
from app.models.identity import AuditLog
from app.models.notification_destination import NotificationDestination
from tests.integration.test_fleet_api import ADMIN, NOTHING, VIEWER, Client, run

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif("TEST_DATABASE_URL" not in os.environ, reason="set TEST_DATABASE_URL"),
]

BASE = "/api/v1/admin/notifications/destinations"
MISSING = "00000000-0000-0000-0000-000000000000"
PUBLIC = "93.184.216.34"
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
SECRET = "destination-secret-77ac1e"
HEADER_VALUE = "header-value-991b"
PATH_TOKEN = "path-secret-183f"


@pytest.fixture(autouse=True)
def _network_free(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    limiter.reset()
    monkeypatch.setattr(routes, "_resolver", lambda host, port: (PUBLIC,))
    monkeypatch.setattr(routes, "_utcnow", lambda: NOW)
    yield
    limiter.reset()


def _rows(model):  # type: ignore[no-untyped-def]
    async def query(session):  # type: ignore[no-untyped-def]
        return list((await session.scalars(select(model))).all())

    return run(query)


def _audits() -> list[AuditLog]:
    async def query(session):  # type: ignore[no-untyped-def]
        rows = await session.scalars(
            select(AuditLog)
            .where(AuditLog.action.like("notification_destination.%"))
            .order_by(AuditLog.created_at, AuditLog.action)
        )
        return list(rows.all())

    return run(query)


def _create(client: Client, name: str = "SOAR", **over: object) -> dict:
    payload: dict[str, object] = {
        "name": name,
        "url": f"https://hooks.example/{PATH_TOKEN}?tenant=blue",
    }
    payload.update(over)
    response = client.post(BASE, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def _mock_client(
    monkeypatch: pytest.MonkeyPatch, handler  # type: ignore[no-untyped-def]
) -> None:
    monkeypatch.setattr(
        routes,
        "_http_transport",
        lambda: httpx.MockTransport(handler),
    )


def test_every_route_needs_authentication_and_admin_notifications(db_schema) -> None:
    app = create_app()
    settings = get_settings().model_copy(update={"auth_provider": "oidc"})
    app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(app, raise_server_exceptions=False) as anonymous:
        assert anonymous.get(BASE).status_code == 401

    body = {"name": "guard", "url": "https://hooks.example/x"}
    for principal in (VIEWER, NOTHING):
        client = Client(principal)
        checks = [
            client.get(BASE),
            client.post(BASE, json=body),
            client.get(f"{BASE}/{MISSING}"),
            client.patch(f"{BASE}/{MISSING}", json={"enabled": False}),
            client.delete(f"{BASE}/{MISSING}"),
            client.post(f"{BASE}/{MISSING}/test"),
        ]
        assert {response.status_code for response in checks} == {403}
        assert all("admin:notifications" in response.text for response in checks)
    assert _rows(NotificationDestination) == [] and _audits() == []


def test_crud_redacts_every_secret_from_get_audit_log_and_raw_storage(
    db_schema, caplog: pytest.LogCaptureFixture
) -> None:
    admin = Client(ADMIN)
    caplog.set_level(logging.INFO)
    body = _create(
        admin,
        hmac_secret=SECRET,
        headers={"X-SOC-Key": HEADER_VALUE},
    )
    destination_id = body["id"]
    assert body["url_display"] == "https://hooks.example"
    assert body["secret_set"] is True and body["header_names"] == ["X-SOC-Key"]
    assert PATH_TOKEN not in json.dumps(body)
    assert SECRET not in json.dumps(body) and HEADER_VALUE not in json.dumps(body)

    listed = admin.get(BASE)
    fetched = admin.get(f"{BASE}/{destination_id}")
    assert listed.status_code == fetched.status_code == 200
    combined = listed.text + fetched.text
    assert PATH_TOKEN not in combined and SECRET not in combined and HEADER_VALUE not in combined

    [audit] = _audits()
    assert audit.action == "notification_destination.create"
    assert audit.object_id == destination_id
    assert audit.detail == {"id": f"urn:uuid:{destination_id}", "name": "SOAR"}
    audit_text = json.dumps(audit.detail)
    assert PATH_TOKEN not in audit_text and SECRET not in audit_text
    assert HEADER_VALUE not in audit_text and "hooks.example" not in audit_text
    assert PATH_TOKEN not in caplog.text and SECRET not in caplog.text
    assert HEADER_VALUE not in caplog.text

    async def ciphertext(session):  # type: ignore[no-untyped-def]
        result = await session.execute(
            text(
                "SELECT url, hmac_secret, headers FROM notification_destination "
                "WHERE id = :id"
            ),
            {"id": uuid.UUID(destination_id)},
        )
        return tuple(result.one())

    raw = " ".join(run(ciphertext))
    assert PATH_TOKEN not in raw and SECRET not in raw and HEADER_VALUE not in raw

    assert admin.delete(f"{BASE}/{destination_id}").status_code == 204
    assert admin.get(f"{BASE}/{destination_id}").status_code == 404
    assert _rows(NotificationDestination) == []
    assert [entry.action for entry in _audits()] == [
        "notification_destination.create",
        "notification_destination.delete",
    ]


def test_patch_keeps_clears_and_replaces_the_secret(db_schema) -> None:
    admin = Client(ADMIN)
    destination_id = _create(admin, hmac_secret=SECRET)["id"]
    kept = admin.patch(f"{BASE}/{destination_id}", json={"enabled": False})
    assert kept.status_code == 200 and kept.json()["secret_set"] is True
    [row] = _rows(NotificationDestination)
    assert row.hmac_secret == SECRET

    cleared = admin.patch(f"{BASE}/{destination_id}", json={"hmac_secret": None})
    assert cleared.status_code == 200 and cleared.json()["secret_set"] is False
    [row] = _rows(NotificationDestination)
    assert row.hmac_secret is None

    replaced = admin.patch(
        f"{BASE}/{destination_id}", json={"hmac_secret": "replacement-secret"}
    )
    assert replaced.status_code == 200 and replaced.json()["secret_set"] is True
    [row] = _rows(NotificationDestination)
    assert row.hmac_secret == "replacement-secret"
    assert "replacement-secret" not in replaced.text


def test_duplicate_name_limit_extra_fields_and_header_guards(db_schema) -> None:
    admin = Client(ADMIN)
    _create(admin, name="destination-0")
    duplicate = admin.post(
        BASE, json={"name": "destination-0", "url": "https://hooks.example/other"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail_code"] == "notifications.name_taken"
    for index in range(1, 10):
        _create(admin, name=f"destination-{index}")
    over = admin.post(
        BASE, json={"name": "destination-10", "url": "https://hooks.example/x"}
    )
    assert over.status_code == 422
    assert over.json()["detail_code"] == "notifications.limit"
    assert len(_rows(NotificationDestination)) == 10

    # Run validation on a fresh schema is unnecessary: Pydantic refuses before the
    # capacity check, and none of these fields may overwrite protocol headers.
    for payload in (
        {"name": "x", "url": "https://hooks.example/x", "extra": True},
        {"name": "x", "url": "https://hooks.example/x", "headers": {"Host": "x"}},
        {
            "name": "x",
            "url": "https://hooks.example/x",
            "headers": {"x-a": "1", "x-b": "2", "x-c": "3", "x-d": "4", "x-e": "5", "x-f": "6"},
        },
    ):
        assert admin.post(BASE, json=payload).status_code == 422


def test_save_refuses_http_and_private_unless_each_flag_allows_it(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = Client(ADMIN)
    http = admin.post(BASE, json={"name": "http", "url": "http://hooks.example/x"})
    assert http.status_code == 422
    assert http.json()["detail_code"] == "notifications.http_refused"
    monkeypatch.setattr(routes, "_resolver", lambda host, port: ("169.254.169.254",))
    private = admin.post(
        BASE, json={"name": "private", "url": "https://hooks.example/x"}
    )
    assert private.status_code == 422
    assert private.json()["detail_code"] == "notifications.private_refused"
    allowed = admin.post(
        BASE,
        json={
            "name": "internal",
            "url": "http://hooks.example/x",
            "allow_http": True,
            "allow_private": True,
        },
    )
    assert allowed.status_code == 201, allowed.text


def test_send_posts_exact_signed_body_to_the_checked_address(
    db_schema,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    admin = Client(ADMIN)
    caplog.set_level(logging.INFO)
    body = _create(
        admin,
        hmac_secret=SECRET,
        headers={"X-SOC-Key": HEADER_VALUE},
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(204, content=b"accepted but discarded", request=request)

    _mock_client(monkeypatch, handler)
    response = admin.post(f"{BASE}/{body['id']}/test")
    assert response.status_code == 200, response.text
    assert response.json()["result"] == "ok" and response.json()["status"] == 204
    assert len(seen) == 1
    request = seen[0]
    expected, expected_headers = build_webhook_v1_test(body["id"], NOW)
    assert request.content == expected
    assert request.headers["Idempotency-Key"] == expected_headers["Idempotency-Key"]
    assert request.headers["X-SOC-Key"] == HEADER_VALUE
    assert request.url.host == PUBLIC and request.url.path == f"/{PATH_TOKEN}"
    assert request.headers["Host"] == "hooks.example"
    assert request.extensions["sni_hostname"] == "hooks.example"
    signature = request.headers[SIGNATURE_HEADER]
    expected_digest = hmac.new(SECRET.encode(), expected, hashlib.sha256).hexdigest()
    assert hmac.compare_digest(signature, f"v1={expected_digest}")
    [row] = _rows(NotificationDestination)
    assert row.last_test_at == NOW and row.last_test_status == 204
    assert row.last_test_class == "ok"
    assert _rows(AlertNotification) == []
    assert _audits()[-1].detail == {
        "id": f"urn:uuid:{body['id']}",
        "name": "SOAR",
    }
    assert PATH_TOKEN not in caplog.text and SECRET not in caplog.text
    assert HEADER_VALUE not in caplog.text


def test_redirect_and_http_error_are_classified_without_following(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = Client(ADMIN)
    destination_id = _create(admin)["id"]
    calls: list[str] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(302, headers={"Location": "https://10.0.0.1/next"}, request=request)

    _mock_client(monkeypatch, redirect)
    refused = admin.post(f"{BASE}/{destination_id}/test")
    assert refused.json()["result"] == "redirect_refused"
    assert refused.json()["status"] == 302 and len(calls) == 1

    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(503, content=b"upstream secret", request=request),
    )
    failed = admin.post(f"{BASE}/{destination_id}/test")
    assert failed.json()["result"] == "http_status" and failed.json()["status"] == 503
    assert "upstream secret" not in failed.text
    [row] = _rows(NotificationDestination)
    assert row.last_test_class == "http_status" and row.last_test_status == 503


def test_timeout_and_rebound_private_address_make_no_false_request(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = Client(ADMIN)
    destination_id = _create(admin)["id"]

    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow peer details", request=request)

    _mock_client(monkeypatch, timeout)
    timed_out = admin.post(f"{BASE}/{destination_id}/test")
    assert timed_out.json()["result"] == "timeout"
    assert timed_out.json()["status"] is None and "slow peer details" not in timed_out.text

    def transport_error(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("socket details", request=request)

    _mock_client(monkeypatch, transport_error)
    failed = admin.post(f"{BASE}/{destination_id}/test")
    assert failed.json()["result"] == "transport"
    assert failed.json()["status"] is None and "socket details" not in failed.text

    monkeypatch.setattr(routes, "_resolver", lambda host, port: ("10.0.0.7",))

    def no_transport() -> httpx.AsyncBaseTransport:
        raise AssertionError("a private rebound target must not reach the transport")

    monkeypatch.setattr(routes, "_http_transport", no_transport)
    rebound = admin.post(f"{BASE}/{destination_id}/test")
    assert rebound.json()["result"] == "ssrf_refused"
    assert rebound.json()["status"] is None
    [row] = _rows(NotificationDestination)
    assert row.last_test_class == "ssrf_refused" and row.last_test_status is None
    assert _rows(AlertNotification) == []


def test_response_body_read_stops_at_64_kib(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = Client(ADMIN)
    destination_id = _create(admin)["id"]

    class CountingStream(httpx.AsyncByteStream):
        def __init__(self) -> None:
            self.chunks = 0
            self.closed = False

        async def __aiter__(self):  # type: ignore[no-untyped-def]
            for _ in range(10):
                self.chunks += 1
                yield b"x" * routes.READ_CHUNK_BYTES

        async def aclose(self) -> None:
            self.closed = True

    stream = CountingStream()
    _mock_client(
        monkeypatch,
        lambda request: httpx.Response(200, stream=stream, request=request),
    )
    response = admin.post(f"{BASE}/{destination_id}/test")
    assert response.json()["result"] == "ok"
    assert stream.chunks == 8 and stream.closed is True


def test_test_send_is_limited_to_five_per_minute_and_writes_no_outbox_row(
    db_schema, monkeypatch: pytest.MonkeyPatch
) -> None:
    admin = Client(ADMIN)
    destination_id = _create(admin)["id"]
    calls = 0

    def accepted(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(204, request=request)

    _mock_client(monkeypatch, accepted)
    statuses = [admin.post(f"{BASE}/{destination_id}/test").status_code for _ in range(6)]
    assert statuses == [200, 200, 200, 200, 200, 429]
    assert calls == 5
    assert _rows(AlertNotification) == []
    assert len([row for row in _audits() if row.action.endswith(".test")]) == 5
