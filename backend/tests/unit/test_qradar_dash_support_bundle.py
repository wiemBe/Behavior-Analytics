"""Host support-bundle tests with Docker, HTTP, and database calls stubbed on PATH."""

from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

HOST_TOOLS = (
    "awk",
    "cat",
    "chmod",
    "date",
    "dirname",
    "head",
    "install",
    "mkdir",
    "mktemp",
    "mv",
    "readlink",
    "rm",
    "sed",
    "tar",
)
SEC_TOKEN = "b4ae60b5-cf8b-4fa4-9fa1-81cf7b79b337"
GHCR_TOKEN = "ghcr-token-material-planted"
TLS_PRIVATE = "planted-tls-private-key-material"

DOCKER_STUB = rf"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB_DOCKER_CALLS"
if [ "$1" = info ]; then
  printf '%s\n' "$STUB_DOCKER_ROOT"
  exit 0
fi
case "$*" in
  *" ps --all --format "*)
    printf '%b\n' 'postgres\trunning\thealthy\t0' 'backend\trunning\thealthy\t0'
    ;;
  *" ps --all")
    printf '%s\n' 'NAME       SERVICE    STATUS' 'fixture-db postgres   running'
    ;;
  *" config --services")
    printf '%s\n' postgres backend
    ;;
  *" images -q "*)
    service=${{!#}}
    printf 'sha256:fixture-%s\n' "$service"
    ;;
  *" logs --tail=2000 --no-color "*)
    printf '%s\n' \
      'ordinary log marker={SEC_TOKEN}' \
      'Authorization: Bearer support-cookie-secret' \
      'cipher=gAAAAABsupport_bundle_fernet_value_0123456789=' \
      'consecutive_failures=2'
    ;;
  *" exec -T backend python -m app.cli.doctor")
    printf '%s\n' 'PASS  db_connect       database accepted SELECT 1'
    ;;
  *" exec -T postgres psql "*)
    query=$(cat)
    printf '%s\n' "$query" >> "$STUB_PSQL_INPUT"
    [ "${{STUB_PSQL_RC:-0}}" -eq 0 ] || exit "$STUB_PSQL_RC"
    case "$query" in
      *pg_database_size*) printf '%s\n' 1073741824 ;;
      *alembic_version*) printf '%s\n' 0042_fixture ;;
      *collection_watermark*)
        printf '%s\n' 'correlation_id={SEC_TOKEN} consecutive_failures=2'
        ;;
      *search_cadence*) printf '%s\n' 'WAF_COLLECTION|900|PASS' ;;
    esac
    ;;
  *" run --rm --no-deps -T backend python -m app.cli.support_bundle")
    cat > "$STUB_DOCKER_STDIN"
    [ "${{STUB_SCRUBBER_RC:-0}}" -eq 0 ] || exit "$STUB_SCRUBBER_RC"
    "$STUB_PYTHON" -m app.cli.support_bundle < "$STUB_DOCKER_STDIN"
    ;;
  *) exit 97 ;;
esac
"""

DF_STUB = r"""#!/bin/bash
printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
printf 'fixture 104857600 1 41943040 10%% %s\n' "${!#}"
"""

CURL_STUB = r"""#!/bin/bash
url=${!#}
case "$url" in
  */api/v1/version) printf '%s\n' '{"version":"2.17.2"}' ;;
  */api/health) printf '%s\n' '{"status":"ok","version":"2.17.2"}' ;;
  *) exit 22 ;;
esac
"""

TIMEDATECTL_STUB = "#!/bin/bash\nprintf '%s\\n' yes\n"
GETENFORCE_STUB = "#!/bin/bash\nprintf '%s\\n' Enforcing\n"


class SupportBundleFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        self.script = self.root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        self.backup_dir = tmp_path / "backups"
        self.backup_dir.mkdir()
        self.docker_root = tmp_path / "docker-root"
        self.docker_root.mkdir()

        self.secret_values = {
            "SECRET_KEY": "secret-key-material-planted",
            "ENCRYPTION_KEY": "encryption-key-material-planted",
            "ENCRYPTION_KEYS_JSON": "encryption-json-material-planted",
            "POSTGRES_PASSWORD": "postgres-password-material-planted",
            "QRADAR_SEC_TOKEN": "env-sec-token-material-planted",
            "OIDC_CLIENT_SECRET": "oidc-client-secret-material-planted",
            "LLM_API_KEY": "llm-api-key-material-planted",
            "SMTP_PASSWORD": "smtp-password-material-planted",
            "ALERT_WEBHOOK_URL": "webhook-value-material-planted",
            "SECONDARY_PASSWORD_HASH": "password-hash-material-planted",
            "SERVICE_TOKEN_EXTRA": "service-token-material-planted",
        }
        env_lines = {
            "COMPOSE_PROJECT_NAME": "fixture-project",
            "COMPOSE_PROFILES": "",
            "QRADAR_DASH_BACKUP_DIR": str(self.backup_dir),
            "POSTGRES_USER": "qradar_obs",
            "POSTGRES_DB": "qradar_obs",
            "API_LOOPBACK_PORT": "8100",
            "UI_LOOPBACK_PORT": "3100",
            "QRADAR_API_TOKEN_FILE": "/run/secrets/qradar_sec_token",
            "CONSECUTIVE_FAILURES": "2",
            **self.secret_values,
        }
        (self.root / ".env").write_text(
            "".join(f"{key}={value}\n" for key, value in env_lines.items())
        )
        secrets = self.root / ".secrets"
        secrets.mkdir()
        (secrets / "qradar.sec").write_text(f"{SEC_TOKEN}\n")
        (secrets / "ghcr.token").write_text(f"{GHCR_TOKEN}\n")
        (secrets / "tls.key").write_text(
            f"-----BEGIN PRIVATE KEY-----\n{TLS_PRIVATE}\n-----END PRIVATE KEY-----\n"
        )
        state = self.root / ".qradar-dash"
        state.mkdir()
        (state / "state.json").write_text('{"installed_version":"2.17.2"}\n')

        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        for tool in HOST_TOOLS:
            executable = shutil.which(tool)
            assert executable is not None
            (self.bin_dir / tool).symlink_to(executable)
        self._stub("docker", DOCKER_STUB)
        self._stub("df", DF_STUB)
        self._stub("curl", CURL_STUB)
        self._stub("timedatectl", TIMEDATECTL_STUB)
        self._stub("getenforce", GETENFORCE_STUB)

        self.calls = tmp_path / "docker.calls"
        self.stdin = tmp_path / "docker.stdin.tar"
        self.psql_input = tmp_path / "psql.input"
        self.env = {
            "HOME": str(tmp_path),
            "PATH": str(self.bin_dir),
            "PYTHONPATH": str(REPO / "backend"),
            "STUB_DOCKER_CALLS": str(self.calls),
            "STUB_DOCKER_ROOT": str(self.docker_root),
            "STUB_DOCKER_STDIN": str(self.stdin),
            "STUB_PSQL_INPUT": str(self.psql_input),
            "STUB_PYTHON": sys.executable,
        }

    def _stub(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content)
        path.chmod(0o755)

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.script), "support-bundle"],
            cwd=self.root,
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        )

    def archives(self) -> list[Path]:
        return sorted(self.backup_dir.glob("support-*.tar.gz"))

    def staging_directories(self) -> list[Path]:
        return sorted(self.backup_dir.glob(".support-staging.*"))


@pytest.fixture
def support(tmp_path: Path) -> SupportBundleFixture:
    return SupportBundleFixture(tmp_path)


def archive_files(path: Path) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    with tarfile.open(path, mode="r:gz") as archive:
        for member in archive.getmembers():
            extracted = archive.extractfile(member)
            assert extracted is not None
            result[member.name] = extracted.read()
    return result


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_bundle_is_private_scrubbed_and_removes_staging(
    support: SupportBundleFixture,
) -> None:
    result = support.run()

    assert result.returncode == 0, output(result)
    [archive] = support.archives()
    assert str(archive) in result.stdout
    assert stat.S_IMODE(archive.stat().st_mode) == 0o600
    assert support.staging_directories() == []

    files = archive_files(archive)
    assert {
        "doctor.txt",
        "version.txt",
        "compose-ps.txt",
        "watermarks.txt",
        "cadence.txt",
        "config.env",
        "logs/backend.log",
        "logs/postgres.log",
    } <= files.keys()
    assert not any(name.startswith(".secrets/") for name in files)
    assert not any(name.startswith(".support-input/") for name in files)

    config = files["config.env"].decode()
    for key in support.secret_values:
        assert f"{key}=" not in config
    assert "QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token" in config
    assert "CONSECUTIVE_FAILURES=2" in config

    contents = b"\n".join(files.values())
    for secret in (
        SEC_TOKEN,
        GHCR_TOKEN,
        TLS_PRIVATE,
        *support.secret_values.values(),
        "support-cookie-secret",
    ):
        assert secret.encode() not in contents
    assert b"consecutive_failures" in contents

    docker_arguments = support.calls.read_text()
    for secret in (SEC_TOKEN, GHCR_TOKEN, TLS_PRIVATE, *support.secret_values.values()):
        assert secret not in docker_arguments
    assert SEC_TOKEN.encode() in support.stdin.read_bytes()


def test_failing_scrubber_leaves_no_archive_or_staging(
    support: SupportBundleFixture,
) -> None:
    result = support.run(STUB_SCRUBBER_RC="19")

    assert result.returncode != 0
    assert "support-bundle scrubber failed" in result.stderr
    assert "No archive was written" in result.stderr
    assert support.archives() == []
    assert support.staging_directories() == []


def test_failing_psql_parts_are_errors_but_bundle_is_written(
    support: SupportBundleFixture,
) -> None:
    result = support.run(STUB_PSQL_RC="17")

    assert result.returncode == 0, output(result)
    [archive] = support.archives()
    files = archive_files(archive)
    assert "watermarks.error.txt" in files
    assert "cadence.error.txt" in files
    assert "version-alembic.error.txt" in files
    assert "watermarks.txt" not in files
    assert files["watermarks.error.txt"].count(b"\n") == 1
    assert support.staging_directories() == []
