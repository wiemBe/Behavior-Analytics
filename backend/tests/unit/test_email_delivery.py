"""SMTP transport and the e-mail notifier (MAIL-1), against a fake ``smtplib``.

Nothing here opens a socket: the fakes record the call sequence of each
security mode, so the order (EHLO, STARTTLS, EHLO, login, send) is pinned.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email import message_from_bytes, policy
from typing import ClassVar

import pytest

from app.alerts.notifiers.base import NotificationMessage, NotifierError
from app.alerts.notifiers.channels import EmailNotifier
from app.core.config import Settings
from app.models.enums import AlertTransition, Severity
from app.services import email_delivery as delivery

PASSWORD = "relay-password-planted-4f1c9a"


class FakeSMTP:
    """Records every call; ``plan`` makes a named call raise."""

    instances: ClassVar[list[FakeSMTP]] = []
    plan: ClassVar[dict[str, BaseException]] = {}

    def __init__(self, host: str, port: int, timeout: float = 0, context: object = None) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.sent: list[object] = []
        self.args = (host, port, timeout, context)
        self._record("connect", host, port)
        FakeSMTP.instances.append(self)

    def _record(self, name: str, *args: object) -> None:
        self.calls.append((name, args))
        if name in FakeSMTP.plan:
            raise FakeSMTP.plan[name]

    def ehlo(self) -> None:
        self._record("ehlo")

    def starttls(self, context: object = None) -> None:
        self._record("starttls", context)

    def login(self, user: str, password: str) -> None:
        self._record("login", user, password)

    def send_message(self, message: object) -> None:
        self._record("send_message")
        self.sent.append(message)

    def quit(self) -> None:
        self._record("quit")

    def close(self) -> None:
        self.calls.append(("close", ()))

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeSMTPSSL(FakeSMTP):
    """Implicit TLS: the class itself says the channel is encrypted."""


@pytest.fixture(autouse=True)
def fake_smtp(monkeypatch: pytest.MonkeyPatch) -> type[FakeSMTP]:
    FakeSMTP.instances = []
    FakeSMTP.plan = {}
    monkeypatch.setattr(delivery.smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(delivery.smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def _settings(**over: object) -> Settings:
    base: dict[str, object] = {
        "smtp_host": "relay.example.internal",
        "smtp_port": 587,
        "smtp_from": "alerts@example.internal",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


def _parts() -> delivery.MailParts:
    return delivery.MailParts(subject="Subject ü", html="<p>Hello ü</p>", text="Hello ü")


def _send(settings: Settings) -> FakeSMTP:
    delivery.deliver(settings, delivery.build_message(settings, "soc@example.org", _parts()))
    return FakeSMTP.instances[-1]


# --- the call sequence of each mode ---------------------------------------------


def test_starttls_sequence_verifies_certificates_and_logs_in() -> None:
    server = _send(_settings(smtp_security="starttls", smtp_username="u", smtp_password=PASSWORD))
    assert server.names == [
        "connect", "ehlo", "starttls", "ehlo", "login", "send_message", "quit"
    ]
    context = next(args[0] for name, args in server.calls if name == "starttls")
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname is True
    assert dict(server.calls)["login"] == ("u", PASSWORD)
    assert type(server) is FakeSMTP


def test_ssl_mode_uses_implicit_tls_and_never_starttls() -> None:
    server = _send(_settings(smtp_security="ssl", smtp_port=465))
    assert type(server) is FakeSMTPSSL
    assert server.names == ["connect", "ehlo", "send_message", "quit"]
    context = server.args[3]
    assert isinstance(context, ssl.SSLContext)
    assert context.verify_mode is ssl.CERT_REQUIRED and context.check_hostname is True
    assert server.args[:2] == ("relay.example.internal", 465)


def test_none_mode_is_a_plain_relay_without_tls() -> None:
    server = _send(_settings(smtp_security="none", smtp_port=25))
    assert type(server) is FakeSMTP
    assert server.names == ["connect", "ehlo", "send_message", "quit"]


def test_no_login_without_a_username_even_with_a_password() -> None:
    for mode in ("starttls", "ssl", "none"):
        FakeSMTP.instances = []
        server = _send(_settings(smtp_security=mode, smtp_username="", smtp_password=PASSWORD))
        assert "login" not in server.names, mode


def test_legacy_use_tls_decides_the_mode_when_security_is_unset() -> None:
    assert _settings(smtp_use_tls=True).smtp_security_mode == "starttls"
    assert _settings(smtp_use_tls=False).smtp_security_mode == "none"
    assert _settings(smtp_security="ssl", smtp_use_tls=False).smtp_security_mode == "ssl"
    assert "starttls" in _send(_settings(smtp_use_tls=True)).names
    assert "starttls" not in _send(_settings(smtp_use_tls=False)).names
    with pytest.raises(ValueError, match="SMTP_SECURITY"):
        _settings(smtp_security="tls1.0")
    assert _settings(smtp_security=" SSL ").smtp_security_mode == "ssl"


@pytest.mark.parametrize("use_legacy_switch", [False, True])
def test_a_username_over_a_plain_relay_is_refused_before_any_connection(
    use_legacy_switch: bool,
) -> None:
    over = {"smtp_use_tls": False} if use_legacy_switch else {"smtp_security": "none"}
    settings = _settings(smtp_username="u", smtp_password=PASSWORD, **over)
    assert settings.smtp_security_mode == "none"
    with pytest.raises(delivery.EmailDeliveryError) as caught:
        _send(settings)
    assert caught.value.code == "insecure_login" and caught.value.permanent
    with pytest.raises(delivery.EmailDeliveryError):
        delivery.check_handshake(settings)
    # No connection at all, so no login and no password on the wire.
    assert FakeSMTP.instances == []
    # An unauthenticated plain relay is still allowed.
    assert "send_message" in _send(_settings(smtp_security="none")).names


@pytest.mark.asyncio
async def test_the_notifier_parks_a_clear_text_login_without_retrying() -> None:
    settings = _settings(smtp_security="none", smtp_username="u", smtp_password=PASSWORD)
    with pytest.raises(NotifierError) as caught:
        await EmailNotifier(settings).send("soc@example.org", _alert_message())
    assert caught.value.permanent
    assert str(caught.value) == "SMTP delivery failed: insecure_login"
    assert FakeSMTP.instances == []


def test_starttls_not_offered_fails_closed_and_sends_nothing(fake_smtp) -> None:
    fake_smtp.plan = {"starttls": smtplib.SMTPNotSupportedError("STARTTLS extension not supported")}
    settings = _settings(smtp_security="starttls", smtp_username="u", smtp_password=PASSWORD)
    with pytest.raises(delivery.EmailDeliveryError) as caught:
        _send(settings)
    assert caught.value.code == "tls_failed"
    server = FakeSMTP.instances[-1]
    assert "send_message" not in server.names and "login" not in server.names
    assert server.names[-1] == "close"


def test_a_ca_file_is_loaded_and_a_missing_one_is_a_tls_failure(tmp_path) -> None:
    missing = _settings(smtp_security="ssl", smtp_ca_file=str(tmp_path / "absent.pem"))
    with pytest.raises(delivery.EmailDeliveryError) as caught:
        _send(missing)
    assert caught.value.code == "tls_failed"
    assert FakeSMTP.instances == []


def test_not_configured_never_connects() -> None:
    with pytest.raises(delivery.EmailDeliveryError) as caught:
        _send(_settings(smtp_host=""))
    assert caught.value.code == "not_configured" and caught.value.permanent
    assert FakeSMTP.instances == []


@pytest.mark.parametrize(
    ("raised", "step", "code", "permanent"),
    [
        (ConnectionRefusedError(111, "refused"), "connect", "connection_refused", False),
        (TimeoutError("timed out"), "ehlo", "timeout", False),
        (OSError(113, "no route to host"), "connect", "connection_failed", False),
        (smtplib.SMTPServerDisconnected("closed"), "ehlo", "connection_failed", False),
        (ssl.SSLCertVerificationError("bad certificate"), "starttls", "tls_failed", False),
        (
            smtplib.SMTPAuthenticationError(535, b"relay banner secret"),
            "login", "auth_failed", True,
        ),
        (
            smtplib.SMTPRecipientsRefused({"x@y.example": (550, b"no")}),
            "send_message", "recipient_refused", True,
        ),
        (
            smtplib.SMTPSenderRefused(550, b"no", "a@b.example"),
            "send_message", "sender_refused", True,
        ),
        (
            smtplib.SMTPDataError(554, b"internal host names"),
            "send_message", "server_error", False,
        ),
    ],
)
def test_each_failure_is_a_code_and_never_the_server_text(
    fake_smtp, raised: BaseException, step: str, code: str, permanent: bool
) -> None:
    fake_smtp.plan = {step: raised}
    settings = _settings(smtp_security="starttls", smtp_username="u", smtp_password=PASSWORD)
    with pytest.raises(delivery.EmailDeliveryError) as caught:
        _send(settings)
    assert caught.value.code == code
    assert caught.value.permanent is permanent
    assert str(caught.value) == f"SMTP delivery failed: {code}"
    for secret in ("banner", "internal host", PASSWORD):
        assert secret not in str(caught.value)
    assert caught.value.code in delivery.ERROR_CODES


def test_handshake_check_connects_and_quits_without_login_or_message() -> None:
    settings = _settings(smtp_security="starttls", smtp_username="u", smtp_password=PASSWORD)
    delivery.check_handshake(settings)
    assert FakeSMTP.instances[-1].names == ["connect", "ehlo", "starttls", "ehlo", "quit"]
    FakeSMTP.plan = {"starttls": ssl.SSLError("handshake failure")}
    with pytest.raises(delivery.EmailDeliveryError) as caught:
        delivery.check_handshake(settings)
    assert caught.value.code == "tls_failed"


# --- the message ------------------------------------------------------------------


def test_the_message_is_multipart_alternative_utf8_with_the_required_headers() -> None:
    settings = _settings(smtp_from_name="QRadar Behavior Analytics")
    raw = delivery.build_message(settings, "soc@example.org", _parts()).as_bytes()
    parsed = message_from_bytes(raw, policy=policy.default)
    assert parsed.get_content_type() == "multipart/alternative"
    parts = list(parsed.iter_parts())
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert all(p.get_content_charset() == "utf-8" for p in parts)
    assert parts[0].get_content().strip() == "Hello ü"
    assert parts[1].get_content().strip() == "<p>Hello ü</p>"
    assert parsed["Subject"] == "Subject ü"
    assert parsed["To"] == "soc@example.org"
    assert parsed["From"].addresses[0].addr_spec == "alerts@example.internal"
    assert parsed["From"].addresses[0].display_name == "QRadar Behavior Analytics"
    assert parsed["Date"] and parsed["Date"].datetime.tzinfo is not None
    message_id = str(parsed["Message-ID"])
    assert message_id.startswith("<") and message_id.endswith("@example.internal>")
    again = delivery.build_message(settings, "soc@example.org", _parts())
    assert str(again["Message-ID"]) != message_id


def test_from_without_a_display_name_is_the_bare_address() -> None:
    message = delivery.build_message(_settings(), "soc@example.org", _parts())
    assert message["From"] == "alerts@example.internal"


@pytest.mark.parametrize(
    ("address", "valid"),
    [
        ("soc@example.org", True),
        ("first.last+tag@sub.example.org", True),
        ("", False),
        ("no-at-sign", False),
        ("a@b", False),
        ("a@@b.org", False),
        ("a b@example.org", False),
        ("a@example.org\r\nBcc: x@y.example", False),
        ("Name <a@example.org>", False),
        ("a@example.org, b@example.org", False),
        ("a@example.org;b@example.org", False),
        ("a@.example.org", False),
        ("a@example..org", False),
        ('"quoted"@example.org', False),
        ("x" * 250 + "@example.org", False),
    ],
)
def test_valid_mailbox(address: str, valid: bool) -> None:
    assert delivery.valid_mailbox(address) is valid


# --- the notifier -----------------------------------------------------------------


def _alert_message(**over: object) -> NotificationMessage:
    base: dict[str, object] = {
        "title": "Spike <b>on</b> fw-01",
        "body": "Volume rose.",
        "severity": Severity.CRITICAL,
        "transition": AlertTransition.OPENED,
        "alert_id": "22222222-2222-2222-2222-222222222222",
        "fingerprint": "anomaly:x",
        "fields": {"occurrences": 2},
        "alert_type": "waf_endpoint",
        "opened_at": "2026-11-03T10:00:00+00:00",
        "instance": "lab",
    }
    base.update(over)
    return NotificationMessage(**base)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_the_notifier_sends_the_shipped_template_as_multipart() -> None:
    settings = _settings(smtp_security="none", smtp_port=25)
    await EmailNotifier(settings).send("soc@example.org", _alert_message())
    server = FakeSMTP.instances[-1]
    parsed = server.sent[0]
    assert parsed["Subject"] == "[CRITICAL] Spike <b>on</b> fw-01 (OPENED)"
    html = next(p for p in parsed.iter_parts() if p.get_content_type() == "text/html")
    body = html.get_content()
    assert "&lt;b&gt;on&lt;/b&gt;" in body and "<b>on</b>" not in body
    assert "WAF endpoint" in body and "lab" in body
    assert "/alerts/22222222-2222-2222-2222-222222222222" in body
    text = next(p for p in parsed.iter_parts() if p.get_content_type() == "text/plain")
    assert "Spike <b>on</b> fw-01" in text.get_content()


@pytest.mark.asyncio
async def test_the_notifier_uses_the_template_the_dispatcher_chose() -> None:
    from app.alerts.notifiers.base import ResolvedTemplate

    chosen = ResolvedTemplate("Custom {{ alert.severity }}", "<p>{{ alert.title }}</p>", "")
    await EmailNotifier(_settings(smtp_security="none")).send(
        "soc@example.org", _alert_message(template=chosen)
    )
    parsed = FakeSMTP.instances[-1].sent[0]
    assert parsed["Subject"] == "Custom CRITICAL"


@pytest.mark.asyncio
async def test_the_notifier_fails_closed_without_a_host_or_with_a_bad_recipient() -> None:
    with pytest.raises(NotifierError) as no_host:
        await EmailNotifier(_settings(smtp_host="")).send("soc@example.org", _alert_message())
    assert no_host.value.permanent
    for bad in ("x@example.org\r\nBcc: y@example.org", "Name <x@example.org>", ""):
        with pytest.raises(NotifierError) as caught:
            await EmailNotifier(_settings()).send(bad, _alert_message())
        assert caught.value.permanent
    assert FakeSMTP.instances == []


@pytest.mark.asyncio
async def test_the_notifier_error_carries_the_code_and_retry_class(fake_smtp) -> None:
    settings = _settings(smtp_security="none")
    fake_smtp.plan = {"connect": ConnectionRefusedError()}
    with pytest.raises(NotifierError) as refused:
        await EmailNotifier(settings).send("soc@example.org", _alert_message())
    assert str(refused.value) == "SMTP delivery failed: connection_refused"
    assert not refused.value.permanent
    fake_smtp.plan = {"send_message": smtplib.SMTPRecipientsRefused({"a": (550, b"x")})}
    with pytest.raises(NotifierError) as rejected:
        await EmailNotifier(settings).send("soc@example.org", _alert_message())
    assert rejected.value.permanent


# --- the password ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_password_appears_in_no_log_error_repr_or_response(
    fake_smtp, caplog: pytest.LogCaptureFixture
) -> None:
    settings = _settings(smtp_username="u", smtp_password=PASSWORD, smtp_security="starttls")
    assert PASSWORD not in repr(settings) and PASSWORD not in str(settings)
    assert PASSWORD not in settings.model_dump_json()
    fake_smtp.plan = {"login": smtplib.SMTPAuthenticationError(535, PASSWORD.encode())}
    with caplog.at_level(logging.DEBUG), pytest.raises(NotifierError) as caught:
        await EmailNotifier(settings).send("soc@example.org", _alert_message())
    assert PASSWORD not in str(caught.value)
    assert PASSWORD not in caplog.text
    # The password is read once, at login(), and is not part of the message.
    fake_smtp.plan = {}
    await EmailNotifier(settings).send("soc@example.org", _alert_message())
    assert PASSWORD.encode() not in FakeSMTP.instances[-1].sent[0].as_bytes()
