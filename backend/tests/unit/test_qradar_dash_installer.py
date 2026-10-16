"""The installer's non-interactive parser refuses bad answers before it writes anything.

These tests run `qradar-dash` with bash against fixture answer files. They need no
Docker: every refusal happens before the first Docker call, and the missing-Docker
case runs with a PATH that has no `docker` at all. The script is copied into
`tmp_path` and `cd`s to its own directory, so a refused run leaves that directory
holding the script and nothing else.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

PEM_CERT = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----\n"
PEM_KEY = "-----BEGIN PRIVATE KEY-----\nMIIB\n-----END PRIVATE KEY-----\n"

# Tools the script uses before it reaches its first Docker call.
NEEDED_TOOLS = [
    "bash", "sh", "cat", "stat", "grep", "sed", "awk", "head", "tail", "sort", "tr", "dirname",
    "readlink", "uname", "mkdir", "rm", "date", "ls", "id", "env", "mktemp", "install", "tar",
]


class Install:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.script = root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        self.files = root.parent / "inputs"
        self.files.mkdir()
        (self.files / "token").write_text("not-a-real-token\n")
        (self.files / "ca.pem").write_text(PEM_CERT)
        (self.files / "tls.crt").write_text(PEM_CERT)
        (self.files / "tls.key").write_text(PEM_KEY)

    def answers(self, **overrides: str | None) -> Path:
        values: dict[str, str | None] = {
            "PUBLIC_HOSTNAME": "soc.example.test",
            "TLS_CERT_FILE": str(self.files / "tls.crt"),
            "TLS_KEY_FILE": str(self.files / "tls.key"),
            "QRADAR_URL": "https://qradar.example.test",
            "QRADAR_TOKEN_FILE": str(self.files / "token"),
            "QRADAR_CA_FILE": str(self.files / "ca.pem"),
            "BACKUP_DIR": str(self.files / "backups"),
        }
        values.update(overrides)
        path = self.files / "answers"
        path.write_text(
            "".join(f"{k}={v}\n" for k, v in values.items() if v is not None)
        )
        path.chmod(0o600)
        return path

    def run(
        self, *args: str, path: str | None = None
    ) -> subprocess.CompletedProcess[str]:
        env = {"HOME": str(self.root.parent), "PATH": path or os.environ["PATH"]}
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.script), *args],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
            stdin=subprocess.DEVNULL,
        )

    def nothing_written(self) -> bool:
        return sorted(p.name for p in self.root.iterdir()) == ["qradar-dash"]


@pytest.fixture
def inst(tmp_path: Path) -> Install:
    root = tmp_path / "checkout"
    root.mkdir()
    return Install(root)


@pytest.fixture
def no_docker_path(tmp_path: Path) -> str:
    """A PATH with the script's own tools and no `docker`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in NEEDED_TOOLS:
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    assert not (bin_dir / "docker").exists()
    return str(bin_dir)


def install(inst: Install, answers: Path, **kw: str) -> subprocess.CompletedProcess[str]:
    return inst.run("install", "--non-interactive", str(answers), **kw)


def assert_refused(inst: Install, result: subprocess.CompletedProcess[str], needle: str) -> None:
    assert result.returncode == 2, result.stderr
    assert needle in result.stderr
    assert "nothing was written" in result.stderr
    assert inst.nothing_written()


def test_missing_token_file_is_refused_before_anything_is_written(inst: Install) -> None:
    answers = inst.answers(QRADAR_TOKEN_FILE=str(inst.files / "absent.sec"))
    assert_refused(inst, install(inst, answers), "QRADAR_TOKEN_FILE: file not readable")


def test_empty_token_file_is_refused(inst: Install) -> None:
    (inst.files / "token").write_text("")
    assert_refused(inst, install(inst, inst.answers()), "QRADAR_TOKEN_FILE: file is empty")


@pytest.mark.parametrize(
    "url", ["http://qradar.example.test", "qradar.example.test", "https://", "https://q/api"]
)
def test_non_https_qradar_url_is_refused(inst: Install, url: str) -> None:
    assert_refused(inst, install(inst, inst.answers(QRADAR_URL=url)), "QRADAR_URL: must be an https://")


@pytest.mark.parametrize("port", ["0", "65536", "-1", "http", "44 3"])
def test_bad_port_is_refused(inst: Install, port: str) -> None:
    assert_refused(
        inst, install(inst, inst.answers(HTTPS_PORT=port)), "HTTPS_PORT: not a port number"
    )


def test_a_port_used_twice_is_refused(inst: Install) -> None:
    answers = inst.answers(API_LOOPBACK_PORT="9000", UI_LOOPBACK_PORT="9000")
    assert_refused(inst, install(inst, answers), "port 9000 is used twice")


def test_ca_file_without_a_certificate_is_refused(inst: Install) -> None:
    (inst.files / "ca.pem").write_text("hello\n")
    assert_refused(inst, install(inst, inst.answers()), "QRADAR_CA_FILE: no PEM certificate")


def test_missing_required_answers_are_listed(inst: Install) -> None:
    result = install(inst, inst.answers(PUBLIC_HOSTNAME=None, QRADAR_URL=None))
    assert_refused(inst, result, "PUBLIC_HOSTNAME: required")
    assert "QRADAR_URL: required" in result.stderr


@pytest.mark.parametrize("name", ["https://soc.example", "soc.example/path", "soc.example:8443"])
def test_hostname_must_be_bare(inst: Install, name: str) -> None:
    assert_refused(inst, install(inst, inst.answers(PUBLIC_HOSTNAME=name)), "PUBLIC_HOSTNAME")


def test_backup_dir_inside_the_checkout_is_refused(inst: Install) -> None:
    answers = inst.answers(BACKUP_DIR=str(inst.root / "backups"))
    assert_refused(inst, install(inst, answers), "must not be inside the checkout")


def test_an_answers_file_never_holds_a_secret(inst: Install) -> None:
    path = inst.answers()
    with path.open("a") as fh:
        fh.write("QRADAR_TOKEN=abcdef\nENCRYPTION_KEY=xyz\n")
    result = install(inst, path)
    assert result.returncode == 2
    assert "QRADAR_TOKEN looks like a secret" in result.stderr
    assert "ENCRYPTION_KEY looks like a secret" in result.stderr
    assert "abcdef" not in result.stderr and "xyz" not in result.stderr
    assert inst.nothing_written()


def test_unknown_key_and_malformed_line_are_refused(inst: Install) -> None:
    path = inst.answers()
    with path.open("a") as fh:
        fh.write("NOT_A_KEY=1\njust words\n")
    result = install(inst, path)
    assert result.returncode == 2
    assert "unknown key NOT_A_KEY" in result.stderr
    assert "not KEY=value" in result.stderr


def test_a_group_readable_answers_file_is_refused(inst: Install) -> None:
    path = inst.answers()
    path.chmod(0o644)
    result = install(inst, path)
    assert result.returncode == 2
    assert "must be 0600" in result.stderr
    assert inst.nothing_written()


def test_external_proxy_needs_no_certificate(inst: Install, no_docker_path: str) -> None:
    answers = inst.answers(EXTERNAL_PROXY="yes", TLS_CERT_FILE=None, TLS_KEY_FILE=None)
    result = install(inst, answers, path=no_docker_path)
    # Past the parser: the next refusal is the missing Docker, not the answers.
    assert result.returncode == 1
    assert "Docker is not installed" in result.stderr


def test_valid_answers_reach_preflight_and_a_missing_docker_writes_nothing(
    inst: Install, no_docker_path: str
) -> None:
    result = install(inst, inst.answers(), path=no_docker_path)
    assert result.returncode == 1
    assert "Docker is not installed" in result.stderr
    assert inst.nothing_written()
    assert not (inst.root / ".env").exists()
    assert not (inst.root / ".secrets").exists()


def test_non_interactive_needs_a_file(inst: Install) -> None:
    result = inst.run("install", "--non-interactive")
    assert result.returncode == 2
    assert inst.nothing_written()


def test_unbuilt_subcommands_exit_non_zero(inst: Install) -> None:
    for cmd in ("upgrade", "rollback", "backup", "restore", "doctor", "support-bundle", "bundle"):
        result = inst.run(cmd)
        assert result.returncode == 64, cmd
        assert "not built yet" in result.stderr


def test_status_without_an_install_says_so(inst: Install) -> None:
    result = inst.run("status")
    assert result.returncode == 1
    assert "not installed here" in result.stderr


def test_the_script_is_executable_and_runs_no_forbidden_command() -> None:
    assert SCRIPT.stat().st_mode & stat.S_IXUSR
    code = [
        line
        for line in SCRIPT.read_text().splitlines()
        if not line.lstrip().startswith("#")
    ]
    text = "\n".join(code)
    for forbidden in ("services.seed", "setenforce", "chcon", "restorecon", "semanage",
                      "label=disable", "label:disable", ":z", ":Z", "docker-compose "):
        assert forbidden not in text, forbidden
    compose = (REPO / "docker-compose.prod.yml").read_text()
    for forbidden in (":z", ":Z", "label=disable"):
        assert forbidden not in compose
