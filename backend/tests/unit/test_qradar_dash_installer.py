"""The installer's non-interactive parser refuses bad answers before it writes anything.

These tests run `qradar-dash` with bash against fixture answer files. They need no
Docker: every refusal happens before the first Docker call, and the missing-Docker
case runs with a PATH that has no `docker` at all. The script is copied into
`tmp_path` and `cd`s to its own directory, so a refused run leaves that directory
holding the script and nothing else.
"""

from __future__ import annotations

import json
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
        self.meminfo = self.files / "meminfo"
        self.set_memory_gib(16)

    def set_memory_gib(self, gib: int) -> None:
        self.meminfo.write_text(f"MemTotal:       {gib * 1024 * 1024} kB\n")

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
        env = {
            "HOME": str(self.root.parent),
            "PATH": path or os.environ["PATH"],
            "QRADAR_DASH_MEMINFO_FILE": str(self.meminfo),
        }
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
    for cmd in ("bundle",):
        result = inst.run(cmd)
        assert result.returncode == 64, cmd
        assert "not built yet" in result.stderr


def test_bundle_says_what_to_use_instead(inst: Install) -> None:
    result = inst.run("bundle")
    assert "OPS-" not in result.stderr
    assert "install --build" in result.stderr
    assert "registry" in result.stderr


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


# ---- T-122: COMPOSE_FILE in .env, GIT_SHA for --build -------------------------------------

COMPOSE_FILE_LINE = "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml"

DOCKER_BUILD_STUB = """#!/bin/sh
case "$*" in
  "compose version --short") echo 2.30.0; exit 0 ;;
  *" build "*)
    echo "GIT_SHA=${GIT_SHA-<unset>}" >> "$HOME/build.log"
    exit 1 ;;
esac
exit 0
"""


def _stub(path: Path, body: str) -> None:
    path.write_text(body)
    path.chmod(0o755)


@pytest.fixture
def build_path(tmp_path: Path) -> str:
    """A PATH with the script's tools and a docker stub whose `build` fails after logging."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in [*NEEDED_TOOLS, "openssl", "sha256sum", "timeout", "getenforce",
                 "chmod", "mv", "cp", "ln", "touch", "wc", "cut", "tee", "sleep", "basename"]:
        found = shutil.which(tool)
        if found:
            (bin_dir / tool).symlink_to(found)
    # Preflight wants curl, and a slim image has neither it nor openssl; stub what is absent so
    # the build tests do not depend on the host's tools.
    _stub(bin_dir / "curl", "#!/bin/sh\nexit 0\n")
    if not (bin_dir / "openssl").exists():
        _stub(
            bin_dir / "openssl",
            "#!/bin/sh\n[ \"$1\" = rand ] && echo 0123456789abcdef0123456789abcdef01234567\n"
            "exit 0\n",
        )
    _stub(bin_dir / "docker", DOCKER_BUILD_STUB)
    _stub(bin_dir / "nproc", "#!/bin/sh\necho 8\n")
    _stub(bin_dir / "ss", "#!/bin/sh\nexit 0\n")
    _stub(bin_dir / "df", "#!/bin/sh\nprintf 'Filesystem 1K-blocks Used Available Use%% Mounted\\n"
          "x 999999999999 1 999999999999 1%% /\\n'\n")
    return str(bin_dir)


def _build(inst: Install, tmp_path: Path, path: str) -> subprocess.CompletedProcess[str]:
    answers = inst.answers()
    assert tmp_path == inst.root.parent  # the stub logs to $HOME, which is tmp_path
    return inst.run(
        "install", "--non-interactive", str(answers), "--build", "--skip-qradar-check",
        path=path,
    )


def _env_lines(inst: Install) -> list[str]:
    return (inst.root / ".env").read_text().splitlines()


def test_a_fresh_env_has_compose_file(inst: Install, tmp_path: Path, build_path: str) -> None:
    result = _build(inst, tmp_path, build_path)
    assert "docker compose build failed" in result.stderr + result.stdout
    lines = _env_lines(inst)
    assert lines.count(COMPOSE_FILE_LINE) == 1
    assert lines.index(COMPOSE_FILE_LINE) == lines.index(
        next(x for x in lines if x.startswith("COMPOSE_PROJECT_NAME="))
    ) + 2


def test_a_rerun_adds_a_missing_compose_file_and_keeps_the_other_lines(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _build(inst, tmp_path, build_path)
    env = inst.root / ".env"
    old = [x for x in _env_lines(inst) if x != COMPOSE_FILE_LINE]
    env.write_text("\n".join(old) + "\n")
    _build(inst, tmp_path, build_path)
    new = _env_lines(inst)
    assert new.count(COMPOSE_FILE_LINE) == 1
    assert [x for x in new if x != COMPOSE_FILE_LINE] == old
    keep = [x for x in old if x.split("=")[0] in ("SECRET_KEY", "POSTGRES_PASSWORD")]
    assert len(keep) == 2


def test_a_rerun_over_an_env_with_compose_file_does_not_duplicate_it(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _build(inst, tmp_path, build_path)
    first = (inst.root / ".env").read_text()
    _build(inst, tmp_path, build_path)
    again = _env_lines(inst)
    assert again.count(COMPOSE_FILE_LINE) == 1
    assert len([x for x in again if x.startswith("COMPOSE_FILE=")]) == 1
    assert again[2:4] == first.splitlines()[2:4]  # the stable header keys


def test_build_sees_git_sha_in_a_checkout_and_never_writes_it_to_env(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _stub(
        Path(build_path) / "git",
        "#!/bin/sh\n[ \"$1 $2\" = 'rev-parse HEAD' ] && echo abc123def\n",
    )
    _build(inst, tmp_path, build_path)
    assert (tmp_path / "build.log").read_text().strip() == "GIT_SHA=abc123def"
    assert "GIT_SHA" not in (inst.root / ".env").read_text()


def test_build_without_git_leaves_git_sha_unset_and_does_not_fail_early(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    assert not (Path(build_path) / "git").exists()
    _build(inst, tmp_path, build_path)
    assert (tmp_path / "build.log").read_text().strip() == "GIT_SHA=<unset>"


def test_build_outside_a_repository_leaves_git_sha_unset(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _stub(Path(build_path) / "git", "#!/bin/sh\nexit 128\n")
    _build(inst, tmp_path, build_path)
    assert (tmp_path / "build.log").read_text().strip() == "GIT_SHA=<unset>"


# ---- OPS-6: install state --------------------------------------------------------------

INSTALL_STATE_DOCKER_STUB = """#!/bin/bash
case "$*" in
  *" ps -a -q") printf '%s\\n' postgres-container backend-container ;;
  "inspect "*)
    container=${!#}
    if [ "$container" = postgres-container ]; then
      printf 'postgres\\tFalse\\tsha256:%064d\\n' 1
    else
      printf 'backend\\tFalse\\tsha256:%064d\\n' 2
    fi
    ;;
  *) exit 97 ;;
esac
"""


def _write_install_state(inst: Install, tmp_path: Path) -> subprocess.CompletedProcess[str]:
    bin_dir = tmp_path / "state-bin"
    bin_dir.mkdir()
    for tool in ("bash", "chmod", "dirname", "grep", "head", "mkdir", "mv", "readlink",
                 "rm", "sed", "tr"):
        executable = shutil.which(tool)
        assert executable is not None
        (bin_dir / tool).symlink_to(executable)
    _stub(bin_dir / "docker", INSTALL_STATE_DOCKER_STUB)
    (inst.root / ".env").write_text(
        "COMPOSE_PROJECT_NAME=fixture-project\nQRADAR_DASH_VERSION=0.1.0\n"
    )
    bash = shutil.which("bash") or "/bin/bash"
    return subprocess.run(  # noqa: S603
        [
            bash,
            "-c",
            'QRADAR_DASH_SOURCE_ONLY=1 source "$1"; write_install_state',
            "bash",
            str(inst.script),
        ],
        cwd=inst.root,
        env={"HOME": str(tmp_path), "PATH": str(bin_dir)},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def test_install_writes_initial_private_state(inst: Install, tmp_path: Path) -> None:
    result = _write_install_state(inst, tmp_path)

    assert result.returncode == 0, result.stderr
    state_path = inst.root / ".qradar-dash" / "state.json"
    state = json.loads(state_path.read_text())
    assert state["installed_version"] == "0.1.0"
    assert state["previous_images"] is None
    assert "last_upgrade" not in state
    assert state["images"] == {
        "postgres": f"sha256:{1:064d}",
        "backend": f"sha256:{2:064d}",
    }
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700


def test_install_rerun_preserves_previous_images_and_last_upgrade(
    inst: Install, tmp_path: Path
) -> None:
    state_dir = inst.root / ".qradar-dash"
    state_dir.mkdir(mode=0o700)
    existing = {
        "installed_version": "0.1.0",
        "images": {"backend": f"sha256:{2:064d}"},
        "previous_images": {"backend": f"sha256:{3:064d}"},
        "last_upgrade": {
            "at": "2026-10-17T12:00:00Z",
            "from": "0.0.9",
            "to": "0.1.0",
            "outcome": "success",
            "backup": "/var/backups/qradar-dash-previous",
        },
    }
    (state_dir / "state.json").write_text(json.dumps(existing) + "\n")

    result = _write_install_state(inst, tmp_path)

    assert result.returncode == 0, result.stderr
    state = json.loads((state_dir / "state.json").read_text())
    assert state["previous_images"] == existing["previous_images"]
    assert state["last_upgrade"] == existing["last_upgrade"]


# ---- T-127: the state file on every exit path of the install command --------------------

INSTALL_FLOW_DOCKER_STUB = """#!/bin/bash
rc() { [ -f "$HOME/$1" ] && cat "$HOME/$1" || echo 0; }
case "$*" in
  "compose version --short") echo 2.30.0 ;;
  "version"*) echo 27.0.0 ;;
  "info "*) echo "$HOME" ;;
  "run --rm --entrypoint python "*)
    echo sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= ;;
  *" ps --status running -q") ;;
  *" ps -a -q") printf '%s\\n' postgres-container backend-container ;;
  "inspect "*)
    container=${!#}
    if [ "$container" = postgres-container ]; then
      printf 'postgres\\tFalse\\tsha256:%064d\\n' 1
    else
      printf 'backend\\tFalse\\tsha256:%064d\\n' 2
    fi
    ;;
  *" up -d --wait --wait-timeout 180 postgres redis") ;;
  *" up -d --wait --wait-timeout 180 "*) exit "$(rc up_rc)" ;;
  *" pull "*) ;;
  *" run --rm -T migrate") ;;
  *" exec -T postgres psql "*) cat >/dev/null; echo 1 ;;
  *" app.cli.qradar add "*) exit "$(rc qr_rc)" ;;
  *" ps") ;;
  *) echo "unexpected docker call: $*" >&2; exit 97 ;;
esac
"""


@pytest.fixture
def flow_path(tmp_path: Path, build_path: str, inst: Install) -> str:
    """build_path with a docker stub that lets the whole install run, and the helper script."""
    _stub(Path(build_path) / "docker", INSTALL_FLOW_DOCKER_STUB)
    _stub(Path(build_path) / "curl", "#!/bin/sh\n"
          "case \"$*\" in *'-w '*) echo 200 ;; *version) echo '{\"version\":\"0.1.0\"}' ;; esac\n")
    deploy = inst.root / "deploy"
    deploy.mkdir()
    _stub(deploy / "load-secrets.sh", "#!/bin/sh\nexit 0\n")
    return build_path


def _install_flow(
    inst: Install, tmp_path: Path, path: str, *, qr_rc: int = 0, up_rc: int = 0
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "qr_rc").write_text(f"{qr_rc}\n")
    (tmp_path / "up_rc").write_text(f"{up_rc}\n")
    answers = inst.answers()
    return inst.run(
        "install", "--non-interactive", str(answers), "--skip-qradar-check", path=path
    )


def test_install_with_a_passing_qradar_check_writes_the_state_file(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads((inst.root / ".qradar-dash" / "state.json").read_text())
    assert state["installed_version"] == "0.1.0"
    assert state["images"] == {
        "postgres": f"sha256:{1:064d}",
        "backend": f"sha256:{2:064d}",
    }
    assert "QRadar connection  ok" in result.stdout


def test_install_with_a_failing_qradar_check_still_writes_the_state_file(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    result = _install_flow(inst, tmp_path, flow_path, qr_rc=3)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "QRadar connection  FAILED (exit 3)" in result.stdout
    state_path = inst.root / ".qradar-dash" / "state.json"
    state = json.loads(state_path.read_text())
    assert state["installed_version"] == "0.1.0"
    assert state["images"]["backend"] == f"sha256:{2:064d}"
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


def test_install_whose_stack_does_not_come_up_writes_no_state_file(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    result = _install_flow(inst, tmp_path, flow_path, qr_rc=3, up_rc=1)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "a service did not become healthy" in result.stderr
    assert not (inst.root / ".qradar-dash").exists()


def test_install_summary_has_no_stale_arrives_with_text(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    result = _install_flow(inst, tmp_path, flow_path)

    assert "arrives with" not in result.stdout + result.stderr
    assert "Runbook            docs/OPERATIONS.md; ./qradar-dash doctor" in result.stdout


def test_a_version_endpoint_failure_says_what_failed(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    _stub(Path(flow_path) / "curl", "#!/bin/sh\ncase \"$*\" in *'-w '*) echo 503 ;; esac\n")

    result = _install_flow(inst, tmp_path, flow_path)

    assert "GET /api/v1/version did not answer 200 (got '503')" in result.stderr
    assert "OPS-1" not in result.stderr


def test_preflight_refuses_a_host_with_7_gib_of_memory(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    inst.set_memory_gib(7)

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "RAM 7 GiB; at least 8 GiB is required" in result.stderr
    assert not (inst.root / ".qradar-dash").exists()
    assert not (inst.root / ".env").exists()


# T-130: the backup directory must be one this account can create or write.
needs_non_root = pytest.mark.skipif(
    os.geteuid() == 0, reason="root bypasses file permissions"
)

NOT_WRITABLE = "is not writable by this account; give it to this account"
CANNOT_CREATE = "cannot create the backup directory"


def _assert_refused_unwritten(inst: Install, result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 1, result.stdout + result.stderr
    assert not (inst.root / ".env").exists()
    assert not (inst.root / ".qradar-dash").exists()


@needs_non_root
def test_install_refuses_an_existing_backup_dir_it_cannot_write(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    backups = inst.files / "backups"
    backups.mkdir()
    backups.chmod(0o500)
    try:
        result = _install_flow(inst, tmp_path, flow_path)
    finally:
        backups.chmod(0o700)

    _assert_refused_unwritten(inst, result)
    assert f"the backup directory {backups} {NOT_WRITABLE}" in result.stderr
    assert "(see docs/OPERATIONS.md, Kurulum)" in result.stderr


@needs_non_root
def test_install_refuses_a_missing_backup_dir_under_an_unwritable_parent(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    parent = inst.files / "ro"
    parent.mkdir()
    backups = parent / "qradar-dash"
    answers = inst.answers(BACKUP_DIR=str(backups))
    parent.chmod(0o500)
    try:
        result = inst.run(
            "install", "--non-interactive", str(answers), "--skip-qradar-check", path=flow_path
        )
    finally:
        parent.chmod(0o700)

    _assert_refused_unwritten(inst, result)
    assert (
        f"cannot create the backup directory {backups}: {parent} is not writable by this "
        "account; create it first (see docs/OPERATIONS.md, Kurulum)"
    ) in result.stderr
    assert not backups.exists()


@needs_non_root
def test_dry_run_refuses_an_unwritable_backup_dir_and_writes_nothing(
    inst: Install, flow_path: str
) -> None:
    backups = inst.files / "backups"
    backups.mkdir()
    backups.chmod(0o500)
    answers = inst.answers()
    try:
        result = inst.run(
            "install", "--non-interactive", str(answers), "--dry-run", "--skip-qradar-check",
            path=flow_path,
        )
    finally:
        backups.chmod(0o700)

    _assert_refused_unwritten(inst, result)
    assert NOT_WRITABLE in result.stderr


def test_install_creates_a_missing_backup_dir_with_mode_0700(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    backups = inst.files / "backups"
    assert not backups.exists()

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert stat.S_IMODE(backups.stat().st_mode) == 0o700


def test_a_mkdir_that_fails_after_preflight_stops_the_install(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    backups = inst.files / "backups"
    real_mkdir = shutil.which("mkdir")
    assert real_mkdir
    stub = Path(flow_path) / "mkdir"
    stub.unlink()
    _stub(
        stub,
        "#!/bin/sh\n"
        f"for a in \"$@\"; do [ \"$a\" = '{backups}' ] && exit 1; done\n"
        f"exec {real_mkdir} \"$@\"\n",
    )

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"cannot create the backup directory {backups}" in result.stderr
    assert not (inst.root / ".qradar-dash").exists()
    assert not backups.exists()


# ---- T-131: install never changes the version a site records ----------------------------


def _write_site_state(
    inst: Install, installed: str = "0.1.0", *, outcome: str | None = None, raw: str | None = None
) -> Path:
    state_dir = inst.root / ".qradar-dash"
    state_dir.mkdir(mode=0o700, exist_ok=True)
    state: dict[str, object] = {
        "installed_version": installed,
        "images": {"backend": f"sha256:{2:064d}"},
        "previous_images": None,
    }
    if outcome is not None:
        state["last_upgrade"] = {
            "at": "2026-10-17T12:00:00Z",
            "from": "0.1.0",
            "to": "0.1.1",
            "outcome": outcome,
            "backup": "/var/backups/qradar-dash-previous",
        }
    path = state_dir / "state.json"
    path.write_text(raw if raw is not None else json.dumps(state) + "\n")
    return path


def _env_with_version(inst: Install, version: str) -> bytes:
    (inst.root / "VERSION").write_text(f"{version}\n")
    env = inst.root / ".env"
    env.write_text(f"COMPOSE_PROJECT_NAME=fixture-project\nQRADAR_DASH_VERSION={version}\n")
    return env.read_bytes()


def test_install_from_a_checkout_of_another_version_is_refused_and_keeps_env(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    _write_site_state(inst, "0.1.0")
    before = _env_with_version(inst, "0.1.0")
    (inst.root / "VERSION").write_text("0.1.1\n")

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "install refused: this site runs 0.1.0 and this checkout is 0.1.1; use "
        "./qradar-dash upgrade 0.1.1 to change versions, or check out v0.1.0 to re-run "
        "install (see docs/OPERATIONS.md, Yükseltme)"
    ) in result.stderr
    assert (inst.root / ".env").read_bytes() == before


def test_install_dry_run_from_another_version_is_refused_too(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    _write_site_state(inst, "0.1.0")
    (inst.root / "VERSION").write_text("0.1.1\n")
    answers = inst.answers()

    result = inst.run(
        "install", "--non-interactive", str(answers), "--dry-run", path=flow_path
    )

    assert result.returncode == 1, result.stdout + result.stderr
    assert "install refused: this site runs 0.1.0 and this checkout is 0.1.1" in result.stderr
    assert not (inst.root / ".env").exists()


def test_install_after_a_failed_upgrade_is_refused(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    _write_site_state(inst, "0.1.0", outcome="failed")
    before = _env_with_version(inst, "0.1.0")

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert "install refused: the last upgrade failed; run rollback or restore first" in (
        result.stderr
    )
    assert (inst.root / ".env").read_bytes() == before


def test_install_with_an_invalid_state_file_is_refused(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    _write_site_state(inst, raw='{"installed_version": "latest"}\n')
    before = _env_with_version(inst, "0.1.0")

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert (
        "install refused: .qradar-dash/state.json field installed_version is invalid; "
        "see ./qradar-dash status"
    ) in result.stderr
    assert (inst.root / ".env").read_bytes() == before


def test_install_rerun_at_the_recorded_version_completes(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    _write_site_state(inst, "0.1.0")
    (inst.root / "VERSION").write_text("0.1.0\n")

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "install refused" not in result.stderr


def test_install_rerun_after_a_restored_failure_completes(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    path = _write_site_state(inst, "0.1.0", outcome="failed")
    state = json.loads(path.read_text())
    state["last_upgrade"]["restored_from"] = "/var/backups/qradar-dash-previous"
    path.write_text(json.dumps(state) + "\n")
    (inst.root / "VERSION").write_text("0.1.0\n")

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 0, result.stdout + result.stderr


def test_install_with_no_state_file_completes_at_any_checkout_version(
    inst: Install, tmp_path: Path, flow_path: str
) -> None:
    (inst.root / "VERSION").write_text("0.1.1\n")

    result = _install_flow(inst, tmp_path, flow_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (inst.root / ".qradar-dash" / "state.json").is_file()
