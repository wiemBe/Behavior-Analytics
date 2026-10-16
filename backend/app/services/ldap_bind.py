"""Bind to the directory as the user who typed the password.

The call is blocking, so the async wrapper runs it on a thread. No password
is written to a log from here. An empty password never opens a socket: on
many servers it is an anonymous bind, and an anonymous bind must not
authenticate. TLS verification is always on. The server certificate must
chain to ``ca_pem`` when that is set, or to the system trust store when it
is empty, and the hostname must match.
"""

from __future__ import annotations

import asyncio
import ssl
import time
from contextlib import suppress
from dataclasses import dataclass

from ldap3 import NONE, SUBTREE, Connection, Server, Tls
from ldap3.core.exceptions import (
    LDAPCertificateError,
    LDAPCommunicationError,
    LDAPInvalidCredentialsResult,
    LDAPOperationResult,
    LDAPResponseTimeoutError,
    LDAPSocketOpenError,
    LDAPStartTLSError,
)

from app.security.ldap_names import escape_ldap_name, render_bind_name
from app.services.ldap_config import DirectorySpec

# Transport and handshake failures try the next URL. A credential refusal does
# not: the password was wrong on a server that answered.
_TRANSPORT = (
    LDAPSocketOpenError,
    LDAPStartTLSError,
    LDAPCertificateError,
    LDAPResponseTimeoutError,
    LDAPCommunicationError,
    TimeoutError,
    OSError,
    ssl.SSLError,
)


@dataclass(frozen=True)
class CertificateView:
    subject: str
    issuer: str
    not_after: str


@dataclass(frozen=True)
class BindReport:
    """What the probe, or the login, learned. Never carries the password."""

    tls_status: str
    tls_reason: str | None
    trust: str
    certificate: CertificateView | None
    bind_status: str
    bind_reason: str | None
    latency_ms: int
    display_name: str | None
    mail: str | None

    @property
    def ok(self) -> bool:
        return self.bind_status == "ok"

    @property
    def invalid_credentials(self) -> bool:
        return self.bind_status == "invalid_credentials"

    @property
    def unavailable(self) -> bool:
        return self.tls_status == "failed" or self.bind_status == "failed"


def _latency(started: float) -> int:
    return max(0, int((time.perf_counter() - started) * 1000))


def _sanitise(exc: BaseException, password: str) -> str:
    text = f"{type(exc).__name__}: {exc}"
    if password:
        text = text.replace(password, "[redacted]")
    return text[:300]


def _trust(spec: DirectorySpec) -> str:
    return "ca_pem" if spec.ca_pem else "system"


async def bind_directory(
    spec: DirectorySpec, username: str, password: str
) -> BindReport:
    """Try each URL in order. Invalid credentials stop the walk."""
    return await asyncio.to_thread(_bind_sync, spec, username, password)


def _bind_sync(spec: DirectorySpec, username: str, password: str) -> BindReport:
    started = time.perf_counter()
    trust = _trust(spec)
    if password == "":
        return BindReport(
            tls_status="failed",
            tls_reason="not attempted",
            trust=trust,
            certificate=None,
            bind_status="failed",
            bind_reason="an empty password is refused",
            latency_ms=_latency(started),
            display_name=None,
            mail=None,
        )

    bind_name = render_bind_name(spec.bind_template, username)
    last_tls = "the directory server did not complete TLS"
    last_bind = "the directory server did not accept the bind"
    last_cert: CertificateView | None = None
    tls_ok = False

    for url in spec.urls:
        conn: Connection | None = None
        try:
            conn = _connect(url, spec, bind_name, password)
            conn.open()
            if spec.start_tls and url.startswith("ldap://"):
                conn.start_tls()
            cert = _peer_cert(conn)
            tls_ok = True
            last_cert = cert
            try:
                conn.bind()
            except LDAPInvalidCredentialsResult:
                return BindReport(
                    tls_status="ok",
                    tls_reason=None,
                    trust=trust,
                    certificate=cert,
                    bind_status="invalid_credentials",
                    bind_reason=None,
                    latency_ms=_latency(started),
                    display_name=None,
                    mail=None,
                )
            except LDAPOperationResult as exc:
                last_bind = _sanitise(exc, password)
                continue
            display_name, mail = _profile(conn, spec, username)
            return BindReport(
                tls_status="ok",
                tls_reason=None,
                trust=trust,
                certificate=cert,
                bind_status="ok",
                bind_reason=None,
                latency_ms=_latency(started),
                display_name=display_name,
                mail=mail,
            )
        except _TRANSPORT as exc:
            tls_ok = False
            last_cert = None
            last_tls = _sanitise(exc, password)
            continue
        finally:
            if conn is not None:
                with suppress(Exception):
                    conn.unbind()

    if not tls_ok:
        return BindReport(
            tls_status="failed",
            tls_reason=last_tls,
            trust=trust,
            certificate=None,
            bind_status="failed",
            bind_reason=last_tls,
            latency_ms=_latency(started),
            display_name=None,
            mail=None,
        )
    return BindReport(
        tls_status="ok",
        tls_reason=None,
        trust=trust,
        certificate=last_cert,
        bind_status="failed",
        bind_reason=last_bind,
        latency_ms=_latency(started),
        display_name=None,
        mail=None,
    )


def _connect(url: str, spec: DirectorySpec, bind_name: str, password: str) -> Connection:
    tls_kwargs: dict[str, object] = {
        "validate": ssl.CERT_REQUIRED,
        "version": ssl.PROTOCOL_TLS_CLIENT,
    }
    if spec.ca_pem:
        tls_kwargs["ca_certs_data"] = spec.ca_pem
    tls = Tls(**tls_kwargs)
    server = Server(
        url,
        tls=tls,
        get_info=NONE,
        connect_timeout=spec.timeout_seconds,
    )
    # auto_bind stays off. The default would bind, and an empty password
    # would be an anonymous bind. read_only refuses writes. raise_exceptions
    # turns a result code into an exception this module can classify.
    return Connection(
        server,
        user=bind_name,
        password=password,
        auto_bind=False,
        read_only=True,
        raise_exceptions=True,
        receive_timeout=spec.timeout_seconds,
    )


def _peer_cert(conn: Connection) -> CertificateView | None:
    sock = getattr(conn, "socket", None)
    if sock is None or not hasattr(sock, "getpeercert"):
        return None
    try:
        der = sock.getpeercert(binary_form=True)
    except Exception:
        return None
    if not der:
        return None
    from cryptography import x509

    cert = x509.load_der_x509_certificate(der)
    return CertificateView(
        subject=cert.subject.rfc4514_string(),
        issuer=cert.issuer.rfc4514_string(),
        not_after=cert.not_valid_after_utc.isoformat(),
    )


def _profile(
    conn: Connection, spec: DirectorySpec, username: str
) -> tuple[str | None, str | None]:
    """Read this user's own displayName and mail. Never a group."""
    if not spec.user_search_base:
        return None, None
    # The filter is the raw username, escaped once. It is not the bind DN.
    escaped = escape_ldap_name(username)
    try:
        conn.search(
            search_base=spec.user_search_base,
            search_filter=f"(sAMAccountName={escaped})",
            search_scope=SUBTREE,
            attributes=["displayName", "mail"],
            size_limit=1,
        )
    except Exception:
        return None, None
    entries = getattr(conn, "entries", None) or []
    if not entries:
        return None, None
    return _attribute(entries[0], "displayName"), _attribute(entries[0], "mail")


def _attribute(entry: object, name: str) -> str | None:
    """Accept an ldap3 attribute (``.value``) or a plain string on a fake."""
    raw = getattr(entry, name, None)
    if raw is None:
        return None
    value = getattr(raw, "value", raw)
    if isinstance(value, list):
        value = value[0] if value else None
    if not isinstance(value, str) or not value:
        return None
    return value
