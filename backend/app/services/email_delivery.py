"""SMTP transport for alert e-mail (MAIL-1): message building and delivery.

One module owns the wire, so the alert notifier, the admin "send a test
message" action and the doctor's handshake check all behave alike:

* A username with ``none`` is refused (``insecure_login``): credentials never
  cross the network in clear text.
* Three security modes. ``starttls`` connects in the clear, upgrades with
  STARTTLS and refuses to send if the server does not offer it; ``ssl`` is
  implicit TLS (``SMTP_SSL``); ``none`` is a plain relay. Certificate and host
  name verification are on for the two TLS modes and there is no switch for it.
* ``login()`` runs only when ``SMTP_USERNAME`` is set; an empty username is an
  unauthenticated relay.
* A failure is a short code (:class:`EmailDeliveryError`), never the server's
  reply text: a relay banner can carry host names and, on a misconfigured
  relay, more. The password is a ``SecretStr`` read only at ``login()``.
"""

from __future__ import annotations

import contextlib
import smtplib
import ssl
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from email.errors import MessageError
from email.headerregistry import Address
from email.message import EmailMessage
from email.utils import format_datetime, formataddr, parseaddr

from app.core.config import Settings

#: Seconds for each socket operation against the relay.
SMTP_TIMEOUT_SECONDS = 15

#: Outcome codes. Each has an ``email.test.<code>`` message in the registry.
ERROR_CODES: tuple[str, ...] = (
    "not_configured",
    "insecure_login",
    "connection_refused",
    "timeout",
    "connection_failed",
    "tls_failed",
    "auth_failed",
    "recipient_refused",
    "sender_refused",
    "server_error",
)

#: Codes a retry cannot fix until an administrator changes something.
PERMANENT_CODES: frozenset[str] = frozenset(
    {"not_configured", "insecure_login", "auth_failed", "recipient_refused", "sender_refused"}
)


class EmailDeliveryError(RuntimeError):
    """A delivery or handshake failed. ``code`` is one of :data:`ERROR_CODES`."""

    def __init__(self, code: str) -> None:
        super().__init__(f"SMTP delivery failed: {code}")
        self.code = code

    @property
    def permanent(self) -> bool:
        return self.code in PERMANENT_CODES


@dataclass(frozen=True)
class MailParts:
    subject: str
    html: str
    text: str


def sender_header(settings: Settings) -> str:
    """``From``: the address, with the optional display name encoded safely."""
    address = settings.smtp_from.strip()
    name = settings.smtp_from_name.strip()
    if not name:
        return address
    # formataddr RFC 2047-encodes a non-ASCII name and quotes specials.
    return formataddr((name.replace("\r", " ").replace("\n", " "), address))


def build_message(settings: Settings, recipient: str, parts: MailParts) -> EmailMessage:
    """A ``multipart/alternative`` message: text part first, HTML part last."""
    message = EmailMessage()
    message["Subject"] = parts.subject
    message["From"] = sender_header(settings)
    message["To"] = recipient
    message["Date"] = format_datetime(datetime.now(UTC))
    domain = _message_id_domain(settings.smtp_from)
    message["Message-ID"] = f"<{uuid.uuid4().hex}@{domain}>"
    message.set_content(parts.text, subtype="plain", charset="utf-8")
    message.add_alternative(parts.html, subtype="html", charset="utf-8")
    return message


def _message_id_domain(sender: str) -> str:
    _, address = parseaddr(sender)
    domain = address.rpartition("@")[2].strip()
    return domain if domain and " " not in domain else "localhost"


def valid_mailbox(value: str) -> bool:
    """True for one plain ``local@domain`` address, no display name, no list."""
    text = value.strip()
    if not text or len(text) > 254 or any(ch in text for ch in "\r\n\t ,;<>\"'()[]\\"):
        return False
    if text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    if not local or not domain or len(local) > 64 or "." not in domain:
        return False
    try:
        Address(addr_spec=text)
    except (ValueError, IndexError, MessageError):
        return False
    return not domain.startswith(".") and not domain.endswith(".") and ".." not in domain


def refuse_clear_text_login(settings: Settings) -> None:
    """Fail closed: a username with ``SMTP_SECURITY=none`` would send the login in
    clear text, so nothing connects, nothing logs in and nothing is sent.
    ``none`` without a username (an unauthenticated plain relay) stays allowed."""
    if settings.smtp_security_mode == "none" and settings.smtp_username:
        raise EmailDeliveryError("insecure_login")


def _tls_context(settings: Settings) -> ssl.SSLContext:
    return ssl.create_default_context(cafile=settings.smtp_ca_file or None)


def _connect(settings: Settings, timeout: float = SMTP_TIMEOUT_SECONDS) -> smtplib.SMTP:
    """Open the connection and finish the security handshake, never sending mail.

    Raises :class:`EmailDeliveryError`; the caller owns closing the returned
    connection.
    """
    if not settings.smtp_configured:
        raise EmailDeliveryError("not_configured")
    mode = settings.smtp_security_mode
    refuse_clear_text_login(settings)
    host, port = settings.smtp_host.strip(), settings.smtp_port
    server: smtplib.SMTP | None = None
    try:
        if mode == "ssl":
            server = smtplib.SMTP_SSL(
                host, port, timeout=timeout, context=_tls_context(settings)
            )
            server.ehlo()
        else:
            server = smtplib.SMTP(host, port, timeout=timeout)
            server.ehlo()
            if mode == "starttls":
                # smtplib raises SMTPNotSupportedError when the server does not
                # advertise STARTTLS; we never carry on in the clear after that.
                server.starttls(context=_tls_context(settings))
                server.ehlo()
        return server
    except Exception as exc:
        if server is not None:
            _quiet_close(server)
        raise classify(exc) from exc


def classify(exc: BaseException) -> EmailDeliveryError:
    """Map a smtplib/socket/ssl exception to a coded error. Order matters:
    ``ssl.SSLError`` and every smtplib error are ``OSError`` subclasses."""
    if isinstance(exc, EmailDeliveryError):
        return exc
    if isinstance(exc, smtplib.SMTPAuthenticationError):
        return EmailDeliveryError("auth_failed")
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        return EmailDeliveryError("recipient_refused")
    if isinstance(exc, smtplib.SMTPSenderRefused):
        return EmailDeliveryError("sender_refused")
    if isinstance(exc, (ssl.SSLError, ssl.CertificateError, smtplib.SMTPNotSupportedError)):
        return EmailDeliveryError("tls_failed")
    if isinstance(exc, ConnectionRefusedError):
        return EmailDeliveryError("connection_refused")
    if isinstance(exc, TimeoutError):
        return EmailDeliveryError("timeout")
    if isinstance(exc, (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError)):
        return EmailDeliveryError("connection_failed")
    if isinstance(exc, smtplib.SMTPException):
        # Every smtplib error is an OSError too, so this must come before the
        # socket branch: a 554 on DATA is the relay's answer, not a dead socket.
        return EmailDeliveryError("server_error")
    if isinstance(exc, FileNotFoundError):
        # Raised by the TLS context for an unreadable SMTP_CA_FILE, before any socket.
        return EmailDeliveryError("tls_failed")
    if isinstance(exc, OSError):
        return EmailDeliveryError("connection_failed")
    return EmailDeliveryError("server_error")


def _quiet_close(server: smtplib.SMTP) -> None:
    # Closing a half-open socket must not mask the failure that got us here.
    with contextlib.suppress(Exception):
        server.close()


def _login_if_configured(server: smtplib.SMTP, settings: Settings) -> None:
    if settings.smtp_username:
        server.login(settings.smtp_username, settings.smtp_password.get_secret_value())


def deliver(settings: Settings, message: EmailMessage) -> None:
    """Send one message through the configured relay. Blocking: callers run it
    in a thread. Raises :class:`EmailDeliveryError`."""
    server = _connect(settings)
    try:
        _login_if_configured(server, settings)
        server.send_message(message)
        server.quit()
    except Exception as exc:
        _quiet_close(server)
        raise classify(exc) from exc


def check_handshake(settings: Settings, timeout: float = SMTP_TIMEOUT_SECONDS) -> None:
    """Connect, EHLO and finish the STARTTLS/SSL handshake, then QUIT. Nothing
    is sent and nothing is authenticated: the doctor must not spend a login."""
    server = _connect(settings, timeout)
    try:
        server.quit()
    except Exception:  # the handshake already succeeded; QUIT is courtesy
        _quiet_close(server)
