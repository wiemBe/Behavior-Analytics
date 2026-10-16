"""A one-connection LDAPS stand-in for the directory probe.

It speaks just enough BER to answer a bind. It does not log the password.
Certificate files live under the task scratch directory, never /tmp.
"""

from __future__ import annotations

import contextlib
import ipaddress
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from ldap3.protocol.rfc4511 import LDAPMessage
from pyasn1.codec.ber import decoder, encoder

SCRATCH = Path("/home/efe/Documents/QRadar-Dash-work/auth1/certs")


def write_server_material(directory: Path) -> tuple[Path, Path, str]:
    """Return ``(cert file, key file, CA PEM)``. The server cert names 127.0.0.1."""
    directory.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "auth1-test-ca")])
    ca_ski = x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key())
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(ca_ski, critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_subject_key_identifier(ca_ski),
            critical=False,
        )
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.IPv4Address("127.0.0.1"))]
            ),
            critical=False,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    cert_path = directory / "server.crt"
    key_path = directory / "server.key"
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    ca_pem = ca_cert.public_bytes(serialization.Encoding.PEM).decode()
    return cert_path, key_path, ca_pem


class DirectoryStandIn:
    """Accepts TLS and answers one bind per connection."""

    def __init__(self, cert_path: Path, key_path: Path, password: str) -> None:
        self._password = password.encode()
        self._stop = threading.Event()
        self._ready = threading.Event()
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certfile=str(cert_path), keyfile=str(key_path))
        self._context = context
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self.error: str | None = None
        self._thread = threading.Thread(target=self._serve, name="ldap-fake", daemon=True)

    def start(self) -> None:
        self._thread.start()
        self._ready.wait(timeout=2)

    def stop(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        self._ready.set()
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                self._handle(client)
            except Exception as exc:
                self.error = f"{type(exc).__name__}: {exc}"
            finally:
                with contextlib.suppress(OSError):
                    client.close()

    def _handle(self, client: socket.socket) -> None:
        tls = self._context.wrap_socket(client, server_side=True)
        try:
            buffer = b""
            tls.settimeout(2)
            while True:
                chunk = tls.recv(4096)
                if not chunk:
                    return
                buffer += chunk
                # The BER frame may still be incomplete. Wait for another recv.
                message = None
                try:
                    message, _rest = decoder.decode(buffer, asn1Spec=LDAPMessage())
                except Exception:
                    message = None
                if message is None:
                    continue
                bind = message["protocolOp"].getComponentByName("bindRequest")
                if bind is None:
                    return
                simple = bind["authentication"].getComponentByName("simple")
                if simple is None:
                    raw = b""
                elif isinstance(simple, str):
                    raw = simple.encode()
                else:
                    raw = bytes(simple)
                code = 0 if raw == self._password else 49
                reply = LDAPMessage()
                reply["messageID"] = int(message["messageID"])
                reply["protocolOp"]["bindResponse"]["resultCode"] = code
                reply["protocolOp"]["bindResponse"]["matchedDN"] = ""
                reply["protocolOp"]["bindResponse"]["diagnosticMessage"] = ""
                tls.sendall(encoder.encode(reply))
                try:
                    tls.recv(4096)
                except OSError:
                    return
                return
        finally:
            with contextlib.suppress(OSError):
                tls.close()


class ClosedPort:
    """Accepts TCP and closes it, so a TLS client fails the handshake."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(8)
        self._sock.settimeout(0.2)
        self.port = self._sock.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, name="ldap-plain", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._sock.close()
        self._thread.join(timeout=2)

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                client, _addr = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                break
            client.close()
