"""Real TLS handshakes against a local listener. Nothing here patches ldap3.

Python 3.14 removed ``ssl.match_hostname``. ldap3 falls back to its own
backport and reports a mismatch as ``doesn't match any name``. These tests
pin that refusal, plus an untrusted chain, an expired certificate, and a
matching name whose handshake succeeds. The listener speaks no LDAP, so a
matching certificate must end as a bind failure.
"""

from __future__ import annotations

import socket
import ssl
import threading
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.core.config import get_settings
from app.core.database import get_session
from app.core.rate_limit import limiter
from app.main import create_app
from app.services.ldap_bind import BindReport, _bind_sync
from app.services.ldap_config import DirectorySpec
from app.services.seed import ensure_default_roles

pytestmark = pytest.mark.integration

ORIGIN = "http://localhost:3000"
PASSWORD = "Correct-Horse-Battery-99!"
_CA_NAME = "auth1-trusted-ca"


@pytest.fixture(autouse=True)
def _reset_rate_limit() -> Iterator[None]:
    limiter.reset()
    yield
    limiter.reset()


def _ca(
    common_name: str, now: datetime
) -> tuple[RSAPrivateKey, x509.Certificate, x509.SubjectKeyIdentifier]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    ski = x509.SubjectKeyIdentifier.from_public_key(key.public_key())
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=2))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert, ski


def _leaf(
    ca_key: RSAPrivateKey,
    ca_cert: x509.Certificate,
    ca_ski: x509.SubjectKeyIdentifier,
    dns: str,
    not_before: datetime,
    not_after: datetime,
) -> tuple[RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, dns)])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )
    return key, cert


def _pem(cert: x509.Certificate) -> str:
    return cert.public_bytes(serialization.Encoding.PEM).decode()


class _TlsListener:
    """Completes one TLS handshake and then closes. It does not speak LDAP."""

    def __init__(self, cert: x509.Certificate, key: RSAPrivateKey, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self._cert_path = directory / "server.crt"
        self._key_path = directory / "server.key"
        self._cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        self._key_path.write_bytes(
            key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(self._cert_path, self._key_path)
        self._context = context
        self._stop = threading.Event()
        # localhost resolves to ::1 first on this host. A dual-stack socket
        # accepts that and 127.0.0.1.
        sock = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        sock.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("::", 0))
        sock.listen(8)
        sock.settimeout(0.2)
        self.port = sock.getsockname()[1]
        self._sock = sock
        self._thread = threading.Thread(target=self._serve, name="tls-proof", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)
        for path in (self._cert_path, self._key_path):
            with suppress(OSError):
                path.unlink()

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                tls = self._context.wrap_socket(client, server_side=True)
                tls.settimeout(1)
                with suppress(OSError):
                    tls.recv(128)
                with suppress(OSError):
                    tls.close()
            except Exception:
                with suppress(OSError):
                    client.close()
            finally:
                with suppress(OSError):
                    client.close()


@contextmanager
def _serving(cert: x509.Certificate, key: RSAPrivateKey, directory: Path) -> Iterator[int]:
    listener = _TlsListener(cert, key, directory)
    try:
        yield listener.port
    finally:
        listener.close()


def _probe(ca_pem: str, port: int) -> BindReport:
    return _bind_sync(
        DirectorySpec(
            enabled=True,
            urls=(f"ldaps://localhost:{port}",),
            start_tls=False,
            ca_pem=ca_pem,
            bind_template="{username}",
            timeout_seconds=5,
            user_search_base=None,
        ),
        "ada",
        PASSWORD,
    )


def _trusted_pair(now: datetime):
    return _ca(_CA_NAME, now)


def test_untrusted_ca_is_refused(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _trusted_key, trusted, _ski = _trusted_pair(now)
    other_key, other, other_ski = _ca("auth1-other-ca", now)
    key, cert = _leaf(
        other_key, other, other_ski, "localhost", now - timedelta(days=1), now + timedelta(days=2)
    )
    with _serving(cert, key, tmp_path) as port:
        report = _probe(_pem(trusted), port)
    assert report.tls_status == "failed"
    assert report.tls_reason is not None
    assert "unable to get local issuer certificate" in report.tls_reason
    assert report.certificate is None
    assert report.bind_status != "ok"
    assert PASSWORD not in (report.tls_reason or "")


def test_wrong_hostname_is_refused(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ca_key, trusted, ski = _trusted_pair(now)
    key, cert = _leaf(
        ca_key,
        trusted,
        ski,
        "other.example",
        now - timedelta(days=1),
        now + timedelta(days=2),
    )
    with _serving(cert, key, tmp_path) as port:
        report = _probe(_pem(trusted), port)
    assert report.tls_status == "failed"
    reason = report.tls_reason or ""
    assert "doesn't match any name" in reason
    assert "localhost" in reason
    assert "other.example" in reason
    assert report.certificate is None
    assert report.bind_status != "ok"
    assert PASSWORD not in reason


def test_matching_hostname_is_tls_ok_and_bind_failed(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ca_key, trusted, ski = _trusted_pair(now)
    key, cert = _leaf(
        ca_key, trusted, ski, "localhost", now - timedelta(days=1), now + timedelta(days=2)
    )
    with _serving(cert, key, tmp_path) as port:
        report = _probe(_pem(trusted), port)
    assert report.tls_status == "ok"
    assert report.tls_reason is None
    assert report.trust == "ca_pem"
    assert report.certificate is not None
    assert "localhost" in report.certificate.subject
    assert _CA_NAME in report.certificate.issuer
    assert report.certificate.not_after
    assert report.bind_status == "failed"
    assert report.ok is False
    blob = f"{report.tls_reason} {report.bind_reason}"
    assert PASSWORD not in blob


def test_expired_certificate_is_refused(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    ca_key, trusted, ski = _trusted_pair(now)
    key, cert = _leaf(
        ca_key, trusted, ski, "localhost", now - timedelta(days=10), now - timedelta(days=1)
    )
    with _serving(cert, key, tmp_path) as port:
        report = _probe(_pem(trusted), port)
    assert report.tls_status == "failed"
    assert report.tls_reason is not None
    assert "certificate has expired" in report.tls_reason
    assert report.certificate is None
    assert report.bind_status != "ok"


@pytest.fixture
def api(db_schema: str):
    engine = create_async_engine(db_schema, future=True, poolclass=NullPool)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def session_override():  # type: ignore[no-untyped-def]
        async with maker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_session] = session_override
    app.dependency_overrides[get_settings] = lambda: get_settings().model_copy(
        update={"auth_provider": "session", "public_origin": ORIGIN}
    )
    client = TestClient(app, headers={"Origin": ORIGIN})
    try:
        yield client, maker
    finally:
        client.close()
        engine.sync_engine.dispose()


async def test_matching_hostname_probe_returns_the_certificate(api, tmp_path: Path) -> None:
    from sqlalchemy import select

    from app.models.identity import Role, User
    from app.security.passwords import hash_password

    client, maker = api
    async with maker() as session:
        await ensure_default_roles(session)
        role = await session.scalar(select(Role).where(Role.name == "admin"))
        assert role is not None
        session.add(
            User(
                username="ada",
                auth_source="LOCAL",
                hashed_password=hash_password(PASSWORD),
                must_change_password=False,
                is_active=True,
                display_name="ada",
                roles=[role],
            )
        )
        await session.commit()
    assert client.post(
        "/api/v1/auth/login", json={"username": "ada", "password": PASSWORD}
    ).status_code == 200

    now = datetime.now(UTC)
    ca_key, trusted, ski = _trusted_pair(now)
    key, cert = _leaf(
        ca_key, trusted, ski, "localhost", now - timedelta(days=1), now + timedelta(days=2)
    )
    with _serving(cert, key, tmp_path) as port:
        response = client.post(
            "/api/v1/auth/ldap-config/test",
            json={
                "urls": [f"ldaps://localhost:{port}"],
                "start_tls": False,
                "ca_pem": _pem(trusted),
                "bind_template": "{username}",
                "timeout_seconds": 5,
                "username": "ada",
                "password": PASSWORD,
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tls"]["status"] == "ok"
    assert body["tls"]["trust"] == "ca_pem"
    assert "localhost" in body["certificate"]["subject"]
    assert _CA_NAME in body["certificate"]["issuer"]
    assert body["certificate"]["not_after"]
    assert body["bind"]["status"] == "failed"
    assert body["bind"]["status"] != "ok"
    assert isinstance(body["latency_ms"], int)
    assert PASSWORD not in response.text
