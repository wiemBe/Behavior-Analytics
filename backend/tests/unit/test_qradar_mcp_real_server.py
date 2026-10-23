"""QRadarMCPProvider against the real, pinned qradar-mcp server (SEC-3, SEC-4).

Skipped unless `QRADAR_MCP_REAL_SERVER_IMAGE` names the *upstream* image built from the pinned
`qradar-mcp/` commit, for example the release image of an earlier version or
`docker build ./qradar-mcp`:

    QRADAR_MCP_REAL_SERVER_IMAGE=ghcr.io/wiembe/behavior-analytics/qradar-mcp:0.1.4 \\
    PYTHONPATH=. ENCRYPTION_KEY=... .venv/bin/pytest tests/unit/test_qradar_mcp_real_server.py

The test builds the image the stack really runs from it: `deploy/mcp/Dockerfile` with the image
as the named context `upstream` (the same file compose and the release workflow use), so the
read-only feature toggles are baked in and nothing is bind-mounted. It then starts its own
containers on a private Docker network and removes them, the derived image and its volumes
afterwards.

It never reaches a QRadar console. The server talks to a stub that answers a few fixed GET
endpoints over TLS with a certificate signed by a throwaway CA, and that answers 401 to any
request without the expected `SEC` header. The CA reaches the server the way compose delivers
it: a read-only volume at `/run/secrets` and `REQUESTS_CA_BUNDLE`. Files go into containers with
`docker cp` or stdin (no bind mount, so no SELinux label).
"""

from __future__ import annotations

import datetime
import json
import os
import shutil
import socket
import subprocess
import time
import uuid
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.providers.base import ProviderAuthError, ProviderUnavailableError
from app.providers.qradar_mcp import (
    ALLOWED_TOOLS,
    KNOWN_WRITE_TOOLS,
    MCPCapabilityError,
    QRadarMCPProvider,
)

IMAGE = os.environ.get("QRADAR_MCP_REAL_SERVER_IMAGE")
MCP_DIR = Path(__file__).resolve().parents[3] / "deploy" / "mcp"
TOGGLES = MCP_DIR / "feature_toggles.json"
SEC_TOKEN = "sec4-test-token-0123456789abcdef"

pytestmark = pytest.mark.skipif(
    not IMAGE or shutil.which("docker") is None,
    reason=(
        "set QRADAR_MCP_REAL_SERVER_IMAGE to an image built from the pinned qradar-mcp "
        "commit (and have docker on PATH) to run the real-server test"
    ),
)

# A QRadar that answers just enough for the server's per-request authentication
# (`config/access/users?current_user=true`) and for two read tools. TLS with a certificate
# signed by a throwaway CA, because the server always calls https://. Like the real console it
# answers 401 to any request that does not carry the SEC token (the `STUB_TOKEN` variable).
STUB = r'''
import json, os, ssl
from http.server import BaseHTTPRequestHandler, HTTPServer

TOKEN = os.environ["STUB_TOKEN"]
ROUTES = {
    "/api/system/about": {"release_name": "QRadar CE", "external_version": "7.5.0",
                          "build_version": "20260101"},
    "/api/config/access/users": [{"id": 7, "username": "stub-user"}],
    "/api/config/event_sources/log_source_management/log_sources": [
        {"id": 1, "name": "stub-ls", "type_id": 11, "enabled": True}],
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("SEC") != TOKEN:
            data = b'{"code": 401, "message": "Unauthorized"}'
            self.send_response(401)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers(); self.wfile.write(data); return
        path = "/" + self.path.split("?")[0].lstrip("/")
        body = ROUTES.get(path.replace("//", "/"))
        if body is None:
            self.send_response(404); self.end_headers(); return
        data = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers(); self.wfile.write(data)
    def log_message(self, *args):
        pass

ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain("/tmp/c.pem", "/tmp/k.pem")
server = HTTPServer(("0.0.0.0", 443), Handler)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
server.serve_forever()
'''


def make_ca(common_name: str) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_leaf(
    ca_key: rsa.RSAPrivateKey, ca_cert: x509.Certificate, host: str
) -> tuple[bytes, bytes]:
    """PEM certificate and key for `host`, signed by the CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    now = datetime.datetime.now(datetime.UTC)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        ),
    )


def pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def docker(*args: str, check: bool = True, stdin: str | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, test-owned values
        ["docker", *args],  # noqa: S607
        capture_output=True, text=True, check=check, timeout=180, input=stdin,
    )
    return done.stdout.strip()


class Lab:
    """A private network, a stub QRadar and any number of servers built like the stack's."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.tag = f"sec4-test-{uuid.uuid4().hex[:8]}"
        self.network = f"{self.tag}-net"
        self.image = f"{self.tag}-image"
        self.containers: list[str] = []
        self.volumes: list[str] = []
        self.ports: dict[str, int] = {}
        self.ca_key, self.ca_cert = make_ca("sec4 test CA")
        self.other_ca = make_ca("sec4 some other CA")[1]

    def start(self) -> None:
        # The image the stack runs: deploy/mcp/Dockerfile over the upstream build.
        docker("build", "--tag", self.image, "--build-context", f"upstream=docker-image://{IMAGE}",
               str(MCP_DIR))
        docker("network", "create", self.network)
        stub_file = self.tmp_path / "stub.py"
        stub_file.write_text(STUB)
        leaf, leaf_key = make_leaf(self.ca_key, self.ca_cert, "stubqradar")
        (self.tmp_path / "c.pem").write_bytes(leaf)
        (self.tmp_path / "k.pem").write_bytes(leaf_key)
        stub = f"{self.tag}-stub"
        docker(
            "create", "--name", stub, "--network", self.network,
            "--network-alias", "stubqradar", "--user", "root", "-e", f"STUB_TOKEN={SEC_TOKEN}",
            "--entrypoint", "python", self.image, "/stub.py",
        )
        self.containers.append(stub)
        docker("cp", str(stub_file), f"{stub}:/stub.py")
        docker("cp", str(self.tmp_path / "c.pem"), f"{stub}:/tmp/c.pem")
        docker("cp", str(self.tmp_path / "k.pem"), f"{stub}:/tmp/k.pem")
        docker("start", stub)

    def secrets_volume(self, name: str, ca: x509.Certificate) -> str:
        """A volume laid out as deploy/load-secrets.sh does: CA 0444, token 0400 for uid 10001."""
        volume = f"{self.tag}-{name}-secrets"
        docker("volume", "create", volume)
        self.volumes.append(volume)
        docker(
            "run", "--rm", "-i", "--user", "root", "--entrypoint", "sh", "-v", f"{volume}:/dst",
            self.image, "-c",
            "cat > /dst/qradar_ca.pem && printf 'decoy-token\\n' > /dst/qradar_sec_token"
            " && chmod 444 /dst/qradar_ca.pem && chmod 400 /dst/qradar_sec_token"
            " && chown 10001:10001 /dst/qradar_ca.pem /dst/qradar_sec_token",
            stdin=pem(ca).decode(),
        )
        return volume

    def server(
        self,
        name: str,
        *,
        writes_enabled: bool = False,
        ca: x509.Certificate | None = None,
    ) -> str:
        """Start a server like the compose service; returns its `http://127.0.0.1:<port>` URL.

        The CA volume is mounted read-only, `REQUESTS_CA_BUNDLE` names the file and nothing
        else is mounted from the host. Only the writes-enabled server (the refusal case) has
        a file copied in, because its whole point is a posture other than the image's.
        """
        secrets = self.secrets_volume(name, ca or self.ca_cert)
        logs = f"{self.tag}-{name}-logs"
        docker("volume", "create", logs)
        self.volumes.append(logs)
        container = f"{self.tag}-{name}"
        # A fixed host port, so a restart of the container keeps its address.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        read_only = [] if writes_enabled else ["--read-only"]
        docker(
            "create", "--name", container, "--network", self.network,
            "--security-opt", "no-new-privileges:true", "--cap-drop", "ALL", *read_only,
            "--tmpfs", "/tmp",  # noqa: S108
            "-v", f"{secrets}:/run/secrets:ro", "-v", f"{logs}:/opt/app-root/logs",
            "-p", f"127.0.0.1:{port}:5000",
            "-e", "QRADAR_CONSOLE_FQDN=stubqradar", "-e", "APP_ROOT=/opt/app-root/qradar-mcp",
            "-e", "REQUESTS_CA_BUNDLE=/run/secrets/qradar_ca.pem",
            self.image,
        )
        self.containers.append(container)
        if writes_enabled:
            toggles = json.loads(TOGGLES.read_text())
            toggles["verb_toggles"] = {"GET": True, "POST": True, "DELETE": True}
            toggles_file = self.tmp_path / f"{name}-toggles.json"
            toggles_file.write_text(json.dumps(toggles))
            docker(
                "cp", str(toggles_file),
                f"{container}:/opt/app-root/qradar-mcp/feature_toggles.json",
            )
        docker("start", container)
        self.ports[name] = port
        self.wait_listening(port, container)
        return f"http://127.0.0.1:{port}"

    def restart(self, name: str) -> None:
        container = f"{self.tag}-{name}"
        docker("restart", container)
        self.wait_listening(self.ports[name], container)

    @staticmethod
    def wait_listening(port: int, container: str) -> None:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=1):
                    pass
                # Listening is not ready: wait for the app to answer an HTTP request.
                response = httpx.post(f"http://127.0.0.1:{port}/mcp", content=b"{}",
                                      timeout=3)
                if response.status_code:
                    return
            except (OSError, httpx.HTTPError):
                pass
            time.sleep(1)
        logs = docker("logs", container, check=False)
        pytest.fail(f"qradar-mcp did not start in 90 s; last log lines:\n{logs[-1500:]}")

    def stop(self) -> None:
        for container in reversed(self.containers):
            docker("rm", "-f", container, check=False)
        docker("network", "rm", self.network, check=False)
        for volume in self.volumes:
            docker("volume", "rm", "-f", volume, check=False)
        docker("rmi", self.image, check=False)


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Lab]:
    lab = Lab(tmp_path_factory.mktemp("sec4-lab"))
    try:
        lab.start()
        yield lab
    finally:
        lab.stop()


@pytest.fixture(scope="module")
def read_only_url(lab: Lab) -> str:
    return lab.server("ro")


@pytest.fixture(scope="module")
def writes_enabled_url(lab: Lab) -> str:
    return lab.server("rw", writes_enabled=True)


@pytest.fixture(scope="module")
def wrong_ca_url(lab: Lab) -> str:
    return lab.server("wrongca", ca=lab.other_ca)


class Sink:
    def __init__(self) -> None:
        self.records: list[dict] = []

    async def __call__(self, record: dict) -> None:
        self.records.append(record)


@pytest.mark.asyncio
async def test_the_read_only_server_is_negotiated_and_answers_read_calls(
    read_only_url: str,
) -> None:
    sink = Sink()
    provider = QRadarMCPProvider(base_url=read_only_url, sec_token=SEC_TOKEN, audit_sink=sink)
    try:
        info = await provider.get_instance_info()
        assert info.version == "7.5.0"
        assert info.reachable is True

        sources = await provider.list_log_sources()
        assert [s.name for s in sources] == ["stub-ls"]

        # Every allowlisted tool is advertised, and no write tool is.
        assert await provider.negotiate_capabilities() == ALLOWED_TOOLS
        assert [r["outcome"] for r in sink.records] == ["SUCCESS", "SUCCESS"]
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_the_server_has_no_get_tools_route(read_only_url: str) -> None:
    """What the old negotiation asked for does not exist on the pinned server."""
    async with httpx.AsyncClient(base_url=read_only_url) as client:
        assert (await client.get("/tools", headers={"SEC": SEC_TOKEN})).status_code == 404


@pytest.mark.asyncio
async def test_a_server_with_writes_enabled_is_refused_and_stays_refused(
    writes_enabled_url: str,
) -> None:
    sink = Sink()
    provider = QRadarMCPProvider(base_url=writes_enabled_url, sec_token=SEC_TOKEN, audit_sink=sink)
    try:
        with pytest.raises(MCPCapabilityError, match="advertises write tools") as first:
            await provider.call_tool("get_system_info")
        # The repository's group toggles keep `services` off, so dns/whois lookups are not
        # advertised even with the verbs on; every other known write tool is.
        offending = str(first.value).split("Offending tools: ")[1].split(", ")
        assert set(offending) == KNOWN_WRITE_TOOLS - {"dns_lookup", "whois_lookup"}
        with pytest.raises(MCPCapabilityError):
            await provider.call_tool("get_system_info")
        assert [r["outcome"] for r in sink.records] == ["DENIED", "DENIED"]
        assert {r["detail"]["error"] for r in sink.records} == {"CapabilityRefused"}
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_a_restarted_server_forgets_the_session_and_the_provider_recovers(
    lab: Lab, read_only_url: str
) -> None:
    provider = QRadarMCPProvider(base_url=read_only_url, sec_token=SEC_TOKEN)
    try:
        assert (await provider.get_instance_info()).version == "7.5.0"
        lab.restart("ro")  # sessions live in the server's memory
        assert (await provider.get_instance_info()).version == "7.5.0"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_an_unreachable_server_is_unavailable_not_a_refusal() -> None:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    provider = QRadarMCPProvider(base_url=f"http://127.0.0.1:{port}", connect_timeout=1.0)
    try:
        with pytest.raises(ProviderUnavailableError):
            await provider.call_tool("get_system_info")
    finally:
        await provider.aclose()


# --------------------------------------------------------------------------- SEC-4
@pytest.mark.asyncio
async def test_with_the_token_and_the_ca_the_handshake_listing_and_a_call_succeed(
    read_only_url: str,
) -> None:
    provider = QRadarMCPProvider(base_url=read_only_url, sec_token=SEC_TOKEN)
    try:
        advertised = await provider.negotiate_capabilities()
        assert advertised == ALLOWED_TOOLS
        assert (await provider.call_tool("get_system_info"))["external_version"] == "7.5.0"
    finally:
        await provider.aclose()
    # The tool list the server holds has no write tool: the baked toggles are in force.
    async with httpx.AsyncClient(base_url=read_only_url, timeout=30) as client:
        headers = {
            "Accept": "application/json, text/event-stream",
            "SEC": SEC_TOKEN,
        }
        init = await client.post(
            "/mcp", headers=headers,
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "t", "version": "0"}}},
        )
        headers["Mcp-Session-Id"] = init.headers["mcp-session-id"]
        listing = await client.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
    names = {
        tool["name"]
        for line in listing.text.splitlines() if line.startswith("data: ")
        for tool in json.loads(line[6:])["result"]["tools"]
    }
    assert names and not names & KNOWN_WRITE_TOOLS


@pytest.mark.asyncio
async def test_without_the_token_every_call_is_an_auth_failure(read_only_url: str) -> None:
    sink = Sink()
    provider = QRadarMCPProvider(base_url=read_only_url, audit_sink=sink)
    try:
        with pytest.raises(ProviderAuthError):
            await provider.call_tool("get_system_info")
        with pytest.raises(ProviderAuthError):
            await provider.negotiate_capabilities()
        assert sink.records[0]["outcome"] == "FAILURE"
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_a_wrong_token_is_an_auth_failure_too(read_only_url: str) -> None:
    provider = QRadarMCPProvider(base_url=read_only_url, sec_token=SEC_TOKEN + "x")
    try:
        with pytest.raises(ProviderAuthError):
            await provider.call_tool("get_system_info")
    finally:
        await provider.aclose()


@pytest.mark.asyncio
async def test_a_console_certificate_from_another_ca_is_not_accepted(wrong_ca_url: str) -> None:
    """Verification is on: the stub's certificate is not signed by this server's CA."""
    provider = QRadarMCPProvider(base_url=wrong_ca_url, sec_token=SEC_TOKEN)
    try:
        # The server's own call to QRadar fails the TLS handshake and it answers 5xx; the
        # provider reports the service as unavailable and nothing is returned.
        with pytest.raises(ProviderUnavailableError, match="failed upstream"):
            await provider.call_tool("get_system_info")
    finally:
        await provider.aclose()


def test_the_server_has_no_bind_mount_and_reads_the_ca_but_not_the_token(
    lab: Lab, read_only_url: str
) -> None:
    container = f"{lab.tag}-ro"
    mounts = json.loads(docker("inspect", container, "--format", "{{json .Mounts}}"))
    assert {m["Type"] for m in mounts} == {"volume"}
    assert {m["Destination"] for m in mounts} == {"/run/secrets", "/opt/app-root/logs"}
    secrets = next(m for m in mounts if m["Destination"] == "/run/secrets")
    assert secrets["RW"] is False
    host = json.loads(docker("inspect", container, "--format", "{{json .HostConfig}}"))
    assert host["ReadonlyRootfs"] is True
    assert host["CapDrop"] == ["ALL"]

    # The same user, capabilities and mount as the running service, in a throwaway container.
    probe = docker(
        "run", "--rm", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
        "-v", f"{secrets['Name']}:/run/secrets:ro", "--entrypoint", "sh", lab.image, "-c",
        "id -u; cat /run/secrets/qradar_ca.pem >/dev/null && echo ca-readable;"
        " cat /run/secrets/qradar_sec_token >/dev/null 2>&1 && echo token-readable"
        " || echo token-denied",
    ).split()
    assert probe == ["1001", "ca-readable", "token-denied"]


def test_the_toggle_file_in_the_image_is_the_repository_file(lab: Lab) -> None:
    baked = docker(
        "run", "--rm", "--entrypoint", "cat", lab.image,
        "/opt/app-root/qradar-mcp/feature_toggles.json",
    )
    assert baked == TOGGLES.read_text().strip()
