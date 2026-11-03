"""The alert e-mail admin API without a database (MAIL-1).

Routes that never touch a table (settings, placeholders, preview, test send) run
through the real ``create_app()``. The test send writes one audit row, so it gets
a small recording session. Template and recipient CRUD is in the integration file.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.routes import admin_email
from app.core.config import Settings, get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.services import email_delivery as delivery
from app.services import email_templates as tpl

BASE = "/api/v1/admin/email"
PASSWORD = "relay-password-planted-77ac1e"
READONLY = {"Authorization": "Bearer read-only"}


class RecordingSession:
    def __init__(self) -> None:
        self.added: list[object] = []

    def add(self, entry: object) -> None:
        self.added.append(entry)

    async def flush(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


def _client(session: RecordingSession | None = None, **settings_over: object) -> TestClient:
    app = create_app()
    settings = get_settings().model_copy(update=settings_over)
    app.dependency_overrides[get_settings] = lambda: settings
    recording = session or RecordingSession()

    async def _session():  # type: ignore[no-untyped-def]
        yield recording

    app.dependency_overrides[get_session] = _session
    return TestClient(app, raise_server_exceptions=False)


def _smtp(**over: object) -> dict[str, object]:
    from pydantic import SecretStr

    base: dict[str, object] = {
        "smtp_host": "relay.example.internal",
        "smtp_port": 465,
        "smtp_security": "ssl",
        "smtp_username": "relay-user",
        "smtp_password": SecretStr(PASSWORD),
        "smtp_ca_file": "/etc/ssl/relay-ca.pem",
        "smtp_from": "alerts@example.internal",
    }
    base.update(over)
    return base


# --- access --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/settings"), ("GET", "/placeholders"), ("GET", "/templates"),
        ("GET", "/templates/default"), ("PUT", "/templates/default"),
        ("DELETE", "/templates/default"), ("POST", "/preview"), ("POST", "/test"),
        ("GET", "/recipients"), ("POST", "/recipients"),
        ("PUT", "/recipients/00000000-0000-0000-0000-000000000000"),
        ("DELETE", "/recipients/00000000-0000-0000-0000-000000000000"),
    ],
)
def test_every_route_needs_admin_notifications(method: str, path: str) -> None:
    body = {"subject": "s", "html_body": "<p>x</p>", "to": "a@example.org",
            "address": "a@example.org"}
    client = _client()
    # `read:*` is not enough: the recipients decide who is mailed about the estate.
    readonly = client.request(method, BASE + path, headers=READONLY, json=body)
    assert readonly.status_code == 403, (method, path, readonly.text)
    assert "admin:notifications" in readonly.text


def test_admin_star_grants_the_permission_and_oidc_without_a_token_is_401() -> None:
    assert _client().get(BASE + "/settings").status_code == 200
    assert _client(auth_provider="oidc").get(BASE + "/settings").status_code == 401


# --- settings and placeholders --------------------------------------------------------


def test_settings_view_never_carries_the_password_and_reports_set_flags() -> None:
    response = _client(**_smtp()).get(BASE + "/settings")
    assert response.status_code == 200
    body = response.json()
    assert PASSWORD not in response.text
    assert "password" not in {key.lower() for key in body}
    assert body == {
        "configured": True,
        "host": "relay.example.internal",
        "port": 465,
        "security": "ssl",
        "security_source": "SMTP_SECURITY",
        "from_address": "alerts@example.internal",
        "from_name": "",
        "username_set": True,
        "ca_file_set": True,
        "site_name": "QRadar Behavior Analytics",
        "test_rate_limit": "5/minute",
    }


def test_settings_view_when_nothing_is_configured_and_the_legacy_switch() -> None:
    body = _client(
        smtp_host="", smtp_security="", smtp_use_tls=False, smtp_username="", smtp_ca_file=None
    ).get(BASE + "/settings").json()
    assert body["configured"] is False
    assert body["security"] == "none" and body["security_source"] == "SMTP_USE_TLS"
    assert body["username_set"] is False and body["ca_file_set"] is False


def test_placeholders_list_names_and_english_descriptions() -> None:
    body = _client().get(BASE + "/placeholders").json()
    assert body["syntax"] == "{{ name }}"
    assert [p["name"] for p in body["placeholders"]] == [p.name for p in tpl.PLACEHOLDERS]
    assert len(body["placeholders"]) == 15
    assert all(p["description"].endswith(".") for p in body["placeholders"])


# --- preview ---------------------------------------------------------------------------


def _preview(client: TestClient, **over: object):
    payload = {
        "alert_type": "waf_endpoint",
        "subject": "[{{ alert.severity }}] {{ alert.title }}",
        "html_body": '<h1>{{ alert.title }}</h1><p onclick="x()">{{ evidence.reason }}</p>'
        "<script>alert(1)</script>",
        "text_body": None,
    }
    payload.update(over)
    return client.post(BASE + "/preview", json=payload)


def test_preview_renders_the_fixed_sample_and_returns_sanitised_html() -> None:
    response = _preview(_client())
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["subject"] == "[HIGH] WAF endpoint block ratio spike: /api/login"
    assert body["html"] == (
        "<h1>WAF endpoint block ratio spike: /api/login</h1>"
        "<p>blocked-request proportion rose 35 percentage points</p>"
    )
    assert body["sanitized_html_body"] == (
        "<h1>{{ alert.title }}</h1><p>{{ evidence.reason }}</p>"
    )
    assert "alert(1)" not in response.text and "onclick" not in response.text
    assert body["sample_alert_type"] == "waf_endpoint"
    # No text part was given, so it is derived from the sanitised HTML.
    assert body["text"] == (
        "WAF endpoint block ratio spike: /api/login\n"
        "blocked-request proportion rose 35 percentage points"
    )


def test_preview_with_no_type_uses_the_generic_sample_and_a_given_text_part() -> None:
    body = _preview(_client(), alert_type=None, text_body="T: {{ alert.type }}").json()
    assert body["text"] == "T: Log source"
    assert body["sample_alert_type"] == "default"


def test_preview_refuses_an_unknown_placeholder_with_a_coded_message_naming_it() -> None:
    response = _preview(
        _client(), subject="{{ alert.titel }}", html_body="<p>{{ nope.thing }}</p>",
        text_body="{{ x }}",
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail == [
        "The template uses the placeholder alert.titel, which is not available. "
        "Use one of the names the placeholder list shows.",
        "The template uses the placeholder nope.thing, which is not available. "
        "Use one of the names the placeholder list shows.",
        "The template uses the placeholder x, which is not available. "
        "Use one of the names the placeholder list shows.",
    ]
    assert response.json()["detail_codes"] == [
        {"code": "email.template.unknown_placeholder", "params": {"name": name}}
        for name in ("alert.titel", "nope.thing", "x")
    ]


def test_preview_refuses_an_unknown_alert_type_and_an_empty_result() -> None:
    assert _preview(_client(), alert_type="made_up").status_code == 422
    stripped = _preview(_client(), html_body="<script>x</script>")
    assert stripped.status_code == 422
    assert _preview(_client(), subject="   ").status_code == 422


def test_preview_cannot_be_given_a_real_alert() -> None:
    # The body has no alert id: the sample is fixed and its link says so.
    body = _preview(_client(), html_body='<a href="{{ alert.url }}">x</a>').json()
    assert body["html"] == (
        '<a href="https://qradar.example.internal/alerts/'
        '00000000-0000-0000-0000-000000000001">x</a>'
    )
    extra = _client().post(BASE + "/preview", json={
        "subject": "s", "html_body": "<p>x</p>", "alert_id": "1"
    })
    assert extra.status_code == 422  # unknown fields are refused, so no alert can be named


# --- test send ---------------------------------------------------------------------------


def _send(client: TestClient, to: str = "soc@example.org", **over: object):
    payload = {
        "to": to, "alert_type": "log_source", "subject": "{{ alert.title }}",
        "html_body": "<p>{{ alert.description }}</p>",
    }
    payload.update(over)
    return client.post(BASE + "/test", json=payload)


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    captured: list[object] = []
    monkeypatch.setattr(admin_email.delivery, "deliver", lambda s, m: captured.append(m))
    return captured


def test_test_send_delivers_the_rendered_sample_to_the_typed_address(sent) -> None:
    session = RecordingSession()
    response = _send(_client(session, **_smtp()))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["outcome"]["code"] == "email.test.sent"
    assert body["outcome"]["message"].startswith("The relay accepted the test message")
    message = sent[0]
    assert message["To"] == "soc@example.org"
    assert message["Subject"] == "[Test] Log source volume spike: fw-edge-01"
    assert message.get_body(("html",)).get_content().startswith("<p>Log source fw-edge-01")
    audit = session.added[0]
    assert audit.action == "email.test_send" and audit.outcome == "SUCCESS"
    assert audit.object_id == "soc@example.org"
    assert PASSWORD not in response.text and PASSWORD not in str(audit.detail)


@pytest.mark.parametrize("code", delivery.ERROR_CODES)
def test_test_send_reports_each_failure_as_a_coded_message(
    monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    def refuse(settings, message):  # type: ignore[no-untyped-def]
        raise delivery.EmailDeliveryError(code)

    monkeypatch.setattr(admin_email.delivery, "deliver", refuse)
    session = RecordingSession()
    response = _send(_client(session, **_smtp()))
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is False
    assert body["outcome"]["code"] == f"email.test.{code}"
    assert body["outcome"]["message"] and "relay banner" not in response.text
    assert session.added[0].outcome == "FAILURE"
    assert session.added[0].detail["result"] == code


def test_test_send_rejects_a_bad_address_and_a_bad_template_before_sending(sent) -> None:
    for bad in ("not-an-address", "a@example.org\r\nBcc: b@example.org", "A <a@example.org>"):
        response = _send(_client(), to=bad)
        assert response.status_code == 422, bad
    assert _send(_client(), subject="{{ nope }}").status_code == 422
    assert _send(_client(), alert_type="made_up").status_code == 422
    assert sent == []


def test_test_send_without_a_relay_says_so_and_connects_nowhere(monkeypatch) -> None:
    def no_network(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("the transport must not open a socket here")

    monkeypatch.setattr(delivery.smtplib, "SMTP", no_network)
    monkeypatch.setattr(delivery.smtplib, "SMTP_SSL", no_network)
    body = _send(_client(smtp_host="")).json()
    assert body["ok"] is False and body["outcome"]["code"] == "email.test.not_configured"


def test_test_send_is_rate_limited(sent, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "rate_limit_email_test", "2/minute")
    client = _client(**_smtp())
    assert [_send(client).status_code for _ in range(3)] == [200, 200, 429]
    # Only the test action carries the tight limit: the default does not.
    assert client.get(BASE + "/settings").status_code == 200
    assert len(sent) == 2


def test_the_default_test_limit_is_five_a_minute() -> None:
    assert Settings.model_fields["rate_limit_email_test"].default == "5/minute"


def test_test_send_over_a_plain_relay_with_a_login_is_refused_and_connects_nowhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def no_network(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("the transport must not open a socket here")

    monkeypatch.setattr(delivery.smtplib, "SMTP", no_network)
    monkeypatch.setattr(delivery.smtplib, "SMTP_SSL", no_network)
    response = _send(_client(**_smtp(smtp_security="none", smtp_port=25)))
    body = response.json()
    assert response.status_code == 200 and body["ok"] is False
    assert body["outcome"]["code"] == "email.test.insecure_login"
    assert "clear text" in body["outcome"]["message"] and PASSWORD not in response.text
