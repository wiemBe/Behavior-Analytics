"""QRadarMCPProvider against the real, pinned qradar-mcp server (SEC-3).

Skipped unless `QRADAR_MCP_REAL_SERVER_IMAGE` names an image built from the pinned
`qradar-mcp/` commit, for example the release image or
`docker compose build qradar-mcp` (tag `qradar-observability/qradar-mcp:b8f6a4a3`):

    QRADAR_MCP_REAL_SERVER_IMAGE=ghcr.io/wiembe/behavior-analytics/qradar-mcp:0.1.4 \\
    PYTHONPATH=. ENCRYPTION_KEY=... .venv/bin/pytest tests/unit/test_qradar_mcp_real_server.py

The test starts its own containers on a private Docker network and removes them. It never
reaches a QRadar console: the server talks to a stub that answers four fixed GET endpoints.
Files go into the containers with `docker cp` (no bind mount, so no SELinux label), and the
server runs with the repository's read-only feature toggles (`deploy/mcp/feature_toggles.json`)
or, for the refusal case, with the same file and the three verbs switched on, which is the
upstream default.
"""

from __future__ import annotations

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

from app.providers.base import ProviderUnavailableError
from app.providers.qradar_mcp import (
    ALLOWED_TOOLS,
    KNOWN_WRITE_TOOLS,
    MCPCapabilityError,
    QRadarMCPProvider,
)

IMAGE = os.environ.get("QRADAR_MCP_REAL_SERVER_IMAGE")
TOGGLES = Path(__file__).resolve().parents[3] / "deploy" / "mcp" / "feature_toggles.json"

pytestmark = pytest.mark.skipif(
    not IMAGE or shutil.which("docker") is None,
    reason=(
        "set QRADAR_MCP_REAL_SERVER_IMAGE to an image built from the pinned qradar-mcp "
        "commit (and have docker on PATH) to run the real-server test"
    ),
)

# A QRadar that answers just enough for the server's per-request authentication
# (`config/access/users?current_user=true`) and for two read tools. TLS with a throwaway
# certificate generated at start-up, because the server always calls https://.
STUB = r'''
import datetime, json, ssl
from http.server import BaseHTTPRequestHandler, HTTPServer
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "stubqradar")])
now = datetime.datetime.now(datetime.timezone.utc)
cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
        .public_key(key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=1))
        .sign(key, hashes.SHA256()))
open("/tmp/c.pem", "wb").write(cert.public_bytes(serialization.Encoding.PEM))
open("/tmp/k.pem", "wb").write(key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
    serialization.NoEncryption()))

ROUTES = {
    "/api/system/about": {"release_name": "QRadar CE", "external_version": "7.5.0",
                          "build_version": "20260101"},
    "/api/config/access/users": [{"id": 7, "username": "stub-user"}],
    "/api/config/event_sources/log_source_management/log_sources": [
        {"id": 1, "name": "stub-ls", "type_id": 11, "enabled": True}],
}

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
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


def docker(*args: str, check: bool = True) -> str:
    done = subprocess.run(  # noqa: S603 - fixed argv, no shell, test-owned values
        ["docker", *args],  # noqa: S607
        capture_output=True, text=True, check=check, timeout=120,
    )
    return done.stdout.strip()


class Lab:
    """A private network, a stub QRadar and any number of pinned servers."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.tag = f"sec3-test-{uuid.uuid4().hex[:8]}"
        self.network = f"{self.tag}-net"
        self.containers: list[str] = []
        self.ports: dict[str, int] = {}

    def start(self) -> None:
        docker("network", "create", self.network)
        stub_file = self.tmp_path / "stub.py"
        stub_file.write_text(STUB)
        stub = f"{self.tag}-stub"
        docker(
            "create", "--name", stub, "--network", self.network,
            "--network-alias", "stubqradar", "--user", "root",
            "--entrypoint", "python", IMAGE or "", "/stub.py",
        )
        self.containers.append(stub)
        docker("cp", str(stub_file), f"{stub}:/stub.py")
        docker("start", stub)

    def server(self, name: str, *, writes_enabled: bool) -> str:
        """Start a pinned server; returns its `http://127.0.0.1:<port>` URL."""
        toggles = json.loads(TOGGLES.read_text())
        if writes_enabled:
            toggles["verb_toggles"] = {"GET": True, "POST": True, "DELETE": True}
        toggles_file = self.tmp_path / f"{name}-toggles.json"
        toggles_file.write_text(json.dumps(toggles))
        container = f"{self.tag}-{name}"
        # A fixed host port, so a restart of the container keeps its address.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        docker(
            "create", "--name", container, "--network", self.network,
            "--security-opt", "no-new-privileges:true", "--cap-drop", "ALL",
            "--tmpfs", "/tmp",  # noqa: S108
            "-p", f"127.0.0.1:{port}:5000",
            "-e", "QRADAR_CONSOLE_FQDN=stubqradar", "-e", "APP_ROOT=/opt/app-root/qradar-mcp",
            IMAGE or "",
        )
        self.containers.append(container)
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
        pytest.fail(f"pinned qradar-mcp did not start in 90 s; last log lines:\n{logs[-1500:]}")

    def stop(self) -> None:
        for container in reversed(self.containers):
            docker("rm", "-f", container, check=False)
        docker("network", "rm", self.network, check=False)


@pytest.fixture(scope="module")
def lab(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Lab]:
    lab = Lab(tmp_path_factory.mktemp("sec3-lab"))
    try:
        lab.start()
        yield lab
    finally:
        lab.stop()


@pytest.fixture(scope="module")
def read_only_url(lab: Lab) -> str:
    return lab.server("ro", writes_enabled=False)


@pytest.fixture(scope="module")
def writes_enabled_url(lab: Lab) -> str:
    return lab.server("rw", writes_enabled=True)


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
    provider = QRadarMCPProvider(base_url=read_only_url, audit_sink=sink)
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
        assert (await client.get("/tools")).status_code == 404


@pytest.mark.asyncio
async def test_a_server_with_writes_enabled_is_refused_and_stays_refused(
    writes_enabled_url: str,
) -> None:
    sink = Sink()
    provider = QRadarMCPProvider(base_url=writes_enabled_url, audit_sink=sink)
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
    provider = QRadarMCPProvider(base_url=read_only_url)
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
