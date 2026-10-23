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
import pty
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

from tests.unit import test_qradar_dash_upgrade as base_upgrade
from tests.unit.test_qradar_dash_upgrade import TARGET_VERSION, UpgradeFixture, output

upgrade = base_upgrade.upgrade

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
        (root / "VERSION").write_text("0.1.0\n")
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
        return sorted(p.name for p in self.root.iterdir()) == ["VERSION", "qradar-dash"]


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


@pytest.mark.parametrize("version_contents", [None, "\n"])
def test_install_refuses_an_incomplete_checkout_without_a_version(
    inst: Install, version_contents: str | None
) -> None:
    version = inst.root / "VERSION"
    if version_contents is None:
        version.unlink()
    else:
        version.write_text(version_contents)

    result = install(inst, inst.answers())

    assert result.returncode == 1
    assert "VERSION" in result.stderr
    assert "checkout is incomplete" in result.stderr
    assert "run ./qradar-dash install again" in result.stderr
    assert not (inst.root / ".env").exists()
    assert not (inst.root / ".secrets").exists()


def test_usage_lists_bundle_with_its_options_and_nothing_is_marked_unbuilt(
    inst: Install,
) -> None:
    result = inst.run("help")

    assert "Not built" not in result.stdout and "not built" not in result.stderr
    assert "bundle        On a connected host" in result.stdout
    assert "--output DIR" in result.stdout and "--version X.Y.Z" in result.stdout
    assert "--offline [BUNDLE]" in result.stdout
    assert "./qradar-dash bundle [--version" in SCRIPT.read_text().split("set -euo")[0]


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


def test_production_images_require_the_installed_version() -> None:
    compose = (REPO / "docker-compose.prod.yml").read_text()
    required = (
        "${QRADAR_DASH_VERSION:?QRADAR_DASH_VERSION is not set in .env; "
        "run ./qradar-dash install}"
    )
    assert compose.count(required) == 8
    assert "QRADAR_DASH_VERSION:-0.1.0" not in compose


# ---- T-122: COMPOSE_FILE in .env, GIT_SHA for --build -------------------------------------

COMPOSE_FILE_LINE = "COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml"
COMPOSE_FILE_OVERRIDE_LINE = COMPOSE_FILE_LINE + ":docker-compose.override.yml"

DOCKER_BUILD_STUB = """#!/bin/sh
printf '%s\\n' "$*" >> "$HOME/docker-args.log"
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


def test_a_fresh_env_has_all_four_retention_defaults(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    result = _build(inst, tmp_path, build_path)

    assert result.returncode == 1
    lines = _env_lines(inst)
    for line in (
        "RETENTION_LOG_SOURCE_METRIC_DAYS=90",
        "RETENTION_SEARCH_RESULT_METRIC_DAYS=90",
        "RETENTION_RULE_METRIC_DAYS=90",
        "RETENTION_OFFENSE_SNAPSHOT_DAYS=90",
    ):
        assert lines.count(line) == 1
    assert "# Kept from the previous .env" not in lines
    assert "first install: new .env" in result.stdout


def test_compose_override_is_in_dc_argv_and_compose_file(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    (inst.root / "docker-compose.override.yml").write_text("services: {}\n")

    result = _build(inst, tmp_path, build_path)

    assert "docker-compose.override.yml is in use" in result.stdout
    lines = _env_lines(inst)
    assert COMPOSE_FILE_OVERRIDE_LINE in lines
    build_call = next(
        line for line in (tmp_path / "docker-args.log").read_text().splitlines()
        if " build " in f" {line} "
    )
    assert (
        "-f docker-compose.yml -f docker-compose.prod.yml "
        "-f docker-compose.override.yml build"
    ) in build_call


def test_compose_without_override_omits_it_from_dc_argv_and_compose_file(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    _build(inst, tmp_path, build_path)

    assert COMPOSE_FILE_LINE in _env_lines(inst)
    assert "docker-compose.override.yml" not in (tmp_path / "docker-args.log").read_text()


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
    curl_stub = r"""#!/bin/bash
rc() { [ -f "$HOME/$1" ] && cat "$HOME/$1" || echo "$2"; }
url=${!#}
case "$url" in
  */health/ready) exit "$(rc ready_rc 0)" ;;
  */version)
    case "$*" in
      *" -w "*) rc version_code 200 ;;
      *) printf '%s\n' '{"version":"0.1.0"}' ;;
    esac
    ;;
esac
"""
    _stub(Path(build_path) / "curl", curl_stub)
    sleep = Path(build_path) / "sleep"
    if sleep.exists() or sleep.is_symlink():
        sleep.unlink()
    _stub(sleep, "#!/bin/sh\nexit 0\n")
    deploy = inst.root / "deploy"
    deploy.mkdir()
    _stub(deploy / "load-secrets.sh", "#!/bin/sh\nexit 0\n")
    return build_path


def _install_flow(
    inst: Install,
    tmp_path: Path,
    path: str,
    *,
    qr_rc: int = 0,
    up_rc: int = 0,
    ready_rc: int = 0,
    version_code: int = 200,
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "qr_rc").write_text(f"{qr_rc}\n")
    (tmp_path / "up_rc").write_text(f"{up_rc}\n")
    (tmp_path / "ready_rc").write_text(f"{ready_rc}\n")
    (tmp_path / "version_code").write_text(f"{version_code}\n")
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


OLD_ENV = """ENVIRONMENT=production
DEBUG=false
LOG_LEVEL=DEBUG
SECRET_KEY=kept-secret-key-material
ENCRYPTION_KEY=kept-active-fernet-key
ENCRYPTION_KEYS_JSON={"1":"kept-old-fernet-key"}
ENCRYPTION_KEY_VERSION=2
POSTGRES_USER=qradar_obs
POSTGRES_PASSWORD=kept-postgres-password
POSTGRES_DB=qradar_obs
POSTGRES_HOST=postgres
QRADAR_PROVIDER=rest
QRADAR_VERIFY_SSL=false
QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token
QRADAR_CA_BUNDLE=/run/secrets/qradar_ca.pem
NEXT_PUBLIC_API_BASE_URL=/api/v1
INTERNAL_API_BASE_URL=http://backend:8000/api/v1
LLM_ALLOW_AUTONOMOUS_ACTIONS=false
RETENTION_RULE_METRIC_DAYS=30
NOTIFY_SLACK_WEBHOOK_URL=https://hooks.example.test/secret-value
SMTP_HOST=mail.example.test
WAF_COLLECTION_ENABLED=true
"""


@pytest.mark.parametrize(
    ("qr_rc", "ready_rc", "expected_rc", "summary"),
    [
        (0, 0, 0, "QRadar connection  ok"),
        (3, 0, 1, "QRadar connection  FAILED (exit 3)"),
        (0, 1, 1, "API readiness      NOT READY"),
    ],
)
def test_install_merges_the_existing_env_on_every_written_exit_path(
    inst: Install,
    tmp_path: Path,
    flow_path: str,
    qr_rc: int,
    ready_rc: int,
    expected_rc: int,
    summary: str,
) -> None:
    env_path = inst.root / ".env"
    env_path.write_text(OLD_ENV)

    result = _install_flow(
        inst, tmp_path, flow_path, qr_rc=qr_rc, ready_rc=ready_rc
    )

    assert result.returncode == expected_rc, result.stdout + result.stderr
    assert summary in result.stdout
    assert (inst.root / ".qradar-dash" / "state.json").is_file()
    lines = env_path.read_text().splitlines()
    expected = {
        "LOG_LEVEL": "DEBUG",
        "ENCRYPTION_KEY": "kept-active-fernet-key",
        "ENCRYPTION_KEYS_JSON": '{"1":"kept-old-fernet-key"}',
        "ENCRYPTION_KEY_VERSION": "2",
        "RETENTION_RULE_METRIC_DAYS": "30",
        "NOTIFY_SLACK_WEBHOOK_URL": "https://hooks.example.test/secret-value",
        "SMTP_HOST": "mail.example.test",
        "WAF_COLLECTION_ENABLED": "true",
        "QRADAR_VERIFY_SSL": "true",
    }
    for key, value in expected.items():
        assert lines.count(f"{key}={value}") == 1
        assert len([line for line in lines if line.startswith(f"{key}=")]) == 1
    marker = "# Kept from the previous .env (not written by ./qradar-dash install)"
    assert lines[lines.index(marker) + 1 :] == [
        "NOTIFY_SLACK_WEBHOOK_URL=https://hooks.example.test/secret-value",
        "SMTP_HOST=mail.example.test",
        "WAF_COLLECTION_ENABLED=true",
    ]
    assert stat.S_IMODE(env_path.stat().st_mode) == 0o600
    combined = result.stdout + result.stderr
    for name in (
        "NOTIFY_SLACK_WEBHOOK_URL",
        "SMTP_HOST",
        "WAF_COLLECTION_ENABLED",
        "ENCRYPTION_KEYS_JSON",
        "ENCRYPTION_KEY_VERSION",
        "LOG_LEVEL",
        "RETENTION_RULE_METRIC_DAYS",
    ):
        assert name in combined
    for value in (
        "https://hooks.example.test/secret-value",
        "mail.example.test",
        "kept-old-fernet-key",
        "kept-active-fernet-key",
        "kept-secret-key-material",
        "kept-postgres-password",
    ):
        assert value not in combined
    assert "QRADAR_VERIFY_SSL was 'false' and is enforced as 'true'" in result.stderr


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
    result = _install_flow(inst, tmp_path, flow_path, version_code=503)

    assert result.returncode == 1
    assert "GET /api/v1/version did not answer 200 (got '503')" in result.stderr
    assert "API readiness      NOT READY" in result.stdout
    assert (inst.root / ".qradar-dash" / "state.json").is_file()
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


@needs_non_root
@pytest.mark.parametrize("case", ["missing_under_unwritable_parent", "existing_unwritable"])
def test_dry_run_prints_no_free_space_line_for_a_refused_backup_dir(
    inst: Install, flow_path: str, case: str
) -> None:
    if case == "existing_unwritable":
        locked = inst.files / "backups"
        locked.mkdir()
        backups = locked
    else:
        locked = inst.files / "ro"
        locked.mkdir()
        backups = locked / "qradar-dash"
    answers = inst.answers(BACKUP_DIR=str(backups))
    locked.chmod(0o500)
    try:
        result = inst.run(
            "install", "--non-interactive", str(answers), "--dry-run", "--skip-qradar-check",
            path=flow_path,
        )
    finally:
        locked.chmod(0o700)

    _assert_refused_unwritten(inst, result)
    assert "[FAIL]" in result.stderr
    assert "free for backups" not in result.stdout + result.stderr


def test_dry_run_prints_the_free_space_line_for_an_accepted_backup_dir(
    inst: Install, flow_path: str
) -> None:
    answers = inst.answers()

    result = inst.run(
        "install", "--non-interactive", str(answers), "--dry-run", "--skip-qradar-check",
        path=flow_path,
    )

    assert "GiB free for backups at " in result.stdout + result.stderr


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


# ---- HB-11: the installer asks about the history backfill once, on the first install ---------

HB_DOCKER_STUB = INSTALL_FLOW_DOCKER_STUB.replace(
    'case "$*" in\n',
    'printf \'%s\\n\' "$*" >> "$HOME/docker.log"\ncase "$*" in\n',
    1,
).replace(
    '  *" app.cli.qradar add "*)',
    '  *" app.cli.history_backfill decide "*)\n'
    '    [ -f "$HOME/hb_out" ] && cat "$HOME/hb_out"\n'
    '    exit "$(rc hb_rc)" ;;\n'
    '  *" app.cli.qradar add "*)',
    1,
)
assert HB_DOCKER_STUB != INSTALL_FLOW_DOCKER_STUB and "docker.log" in HB_DOCKER_STUB

DECIDE_TAIL = (
    "run --rm --no-deps -T backend python -m app.cli.history_backfill decide "
    "--instance qradar --answer {answer} --source installer"
)
RECORDED_YES = "qradar: recorded YES (28 days) from the installer"
RECORDED_NO = "qradar: recorded NO (no history) from the installer"
NOT_DECIDED = (
    "History backfill   not decided; the first administrator sign-in will ask (WAF and IPS read\n"
    "                    history for sources enabled after the answer)"
)
QUESTION = "14. Read the last 28 days of history from QRadar now"
HB_KEEP_ON = "History backfill   not recorded (exit 1); the first administrator sign-in will ask"
HB_WARNING = (
    "history backfill: the answer was not recorded (exit 1); the console stays undecided "
    "and the first administrator sign-in will ask"
)


@pytest.fixture
def hb_path(flow_path: str) -> str:
    _stub(Path(flow_path) / "docker", HB_DOCKER_STUB)
    return flow_path


def _docker_log(tmp_path: Path) -> list[str]:
    log = tmp_path / "docker.log"
    return log.read_text().splitlines() if log.exists() else []


def _decide_calls(tmp_path: Path) -> list[str]:
    return [x for x in _docker_log(tmp_path) if "history_backfill" in x]


def _scripted(
    inst: Install,
    tmp_path: Path,
    path: str,
    *flags: str,
    qr_rc: int = 0,
    hb_rc: int = 0,
    hb_out: str | None = None,
    **answers: str | None,
) -> subprocess.CompletedProcess[str]:
    (tmp_path / "qr_rc").write_text(f"{qr_rc}\n")
    (tmp_path / "hb_rc").write_text(f"{hb_rc}\n")
    if hb_out is not None:
        (tmp_path / "hb_out").write_text(hb_out + "\n")
    return inst.run(
        "install", "--non-interactive", str(inst.answers(**answers)), "--skip-qradar-check",
        *flags, path=path,
    )


def _interactive(
    inst: Install, path: str, replies: list[str], *flags: str
) -> subprocess.CompletedProcess[str]:
    """Run `install` with a terminal on stdin. Ctrl-D after the replies ends a stray question."""
    master, slave = pty.openpty()
    env = {
        "HOME": str(inst.root.parent),
        "PATH": path,
        "QRADAR_DASH_MEMINFO_FILE": str(inst.meminfo),
    }
    bash = shutil.which("bash") or "/bin/bash"
    try:
        proc = subprocess.Popen(  # noqa: S603
            [bash, str(inst.script), "install", "--skip-qradar-check", *flags],
            cwd=inst.root,
            env=env,
            stdin=slave,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        os.close(slave)
        os.write(master, ("".join(f"{r}\n" for r in replies)).encode() + b"\x04")
        out, err = proc.communicate(timeout=60)
    finally:
        os.close(master)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


def _wizard_replies(inst: Install, *history: str) -> list[str]:
    return [
        "soc.example.test",  # 1. host name
        "no",  # 3. own proxy
        str(inst.files / "tls.crt"),  # 2. certificate
        str(inst.files / "tls.key"),
        "https://qradar.example.test",  # 4. URL
        "qradar.example.test",  # 5. FQDN
        "",  # 6. API version, default
        str(inst.files / "token"),  # 7. token
        str(inst.files / "ca.pem"),  # 8. CA
        "",  # 9. AKI, default
        "",  # 10. instance name, default
        str(inst.files / "backups"),  # 11. backup directory
        "",  # 12. registry user, none
        "",  # 13. admin user, default
        *history,
    ]


def _assert_decide_between_add_and_start(tmp_path: Path, answer: str) -> None:
    log = _docker_log(tmp_path)
    calls = _decide_calls(tmp_path)
    assert len(calls) == 1, calls
    assert calls[0].endswith(DECIDE_TAIL.format(answer=answer)), calls[0]
    add = next(i for i, x in enumerate(log) if "app.cli.qradar add" in x)
    start = next(i for i, x in enumerate(log) if "up -d --wait --wait-timeout 180 backend" in x)
    assert add < log.index(calls[0]) < start


def _state_exists(inst: Install) -> bool:
    return (inst.root / ".qradar-dash" / "state.json").is_file()


@pytest.mark.parametrize(
    ("answer", "recorded"), [("yes", RECORDED_YES), ("no", RECORDED_NO)]
)
def test_first_interactive_install_asks_and_records_the_answer(
    inst: Install, tmp_path: Path, hb_path: str, answer: str, recorded: str
) -> None:
    (tmp_path / "hb_out").write_text(recorded + "\n")

    result = _interactive(inst, hb_path, _wizard_replies(inst, answer))

    assert result.returncode == 0, result.stdout + result.stderr
    assert QUESTION in result.stdout
    assert "(yes/no)" in result.stdout.split(QUESTION, 1)[1].splitlines()[0]
    assert "up to 3 days on a large one" in result.stdout
    _assert_decide_between_add_and_start(tmp_path, answer)
    assert f"History backfill   {recorded.split(': ', 1)[1]}\n" in result.stdout
    saved = (inst.root / ".secrets" / "install.answers").read_text().splitlines()
    assert f"HISTORY_BACKFILL={answer}" in saved
    assert _state_exists(inst)


def test_an_empty_reply_is_refused_once_and_the_next_answer_is_recorded(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    (tmp_path / "hb_out").write_text(RECORDED_YES + "\n")

    result = _interactive(inst, hb_path, _wizard_replies(inst, "", "yes"))

    assert result.returncode == 0, result.stdout + result.stderr
    after = result.stdout.split(QUESTION, 1)[1]
    assert after.count("an answer is required") == 1
    assert "[" not in after.split("\n", 1)[0].split("(yes/no)", 1)[1]  # no default is offered
    _assert_decide_between_add_and_start(tmp_path, "yes")


def test_a_bad_reply_is_refused_with_the_reason(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _interactive(inst, hb_path, _wizard_replies(inst, "maybe", "no"))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "must be yes or no" in result.stdout.split(QUESTION, 1)[1]
    _assert_decide_between_add_and_start(tmp_path, "no")


@pytest.mark.parametrize(
    ("answer", "recorded"), [("yes", RECORDED_YES), ("no", RECORDED_NO)]
)
def test_scripted_answer_is_recorded_and_summarised(
    inst: Install, tmp_path: Path, hb_path: str, answer: str, recorded: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path, hb_out=recorded, HISTORY_BACKFILL=answer)

    assert result.returncode == 0, result.stdout + result.stderr
    _assert_decide_between_add_and_start(tmp_path, answer)
    assert f"History backfill   {recorded.split(': ', 1)[1]}\n" in result.stdout
    assert f"[ ok ] history backfill: {recorded}" in result.stdout
    assert QUESTION not in result.stdout
    assert _state_exists(inst)


def test_a_decision_that_already_exists_is_reported_as_the_cli_reports_it(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(
        inst, tmp_path, hb_path, hb_out="qradar: already decided on 2026-10-19: NO",
        HISTORY_BACKFILL="yes",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "History backfill   already decided on 2026-10-19: NO\n" in result.stdout


def test_an_absent_key_is_not_asked_not_guessed_and_runs_no_decide(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path)

    assert result.returncode == 0, result.stdout + result.stderr
    assert _decide_calls(tmp_path) == []
    assert NOT_DECIDED in result.stdout
    assert "HISTORY_BACKFILL" not in (inst.root / ".secrets" / "install.answers").read_text()
    assert _state_exists(inst)


def test_a_bad_value_in_the_answers_file_is_refused_and_nothing_is_written(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path, HISTORY_BACKFILL="maybe")

    assert result.returncode == 2, result.stderr
    assert "HISTORY_BACKFILL: must be yes or no" in result.stderr
    assert "nothing was written" in result.stderr
    # flow_path adds deploy/ to the checkout; nothing the installer writes may appear
    assert sorted(p.name for p in inst.root.iterdir()) == ["VERSION", "deploy", "qradar-dash"]
    assert _docker_log(tmp_path) == []


def test_a_rerun_with_saved_answers_does_not_ask_and_passes_the_saved_answer_again(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    (tmp_path / "hb_out").write_text(RECORDED_YES + "\n")
    first = _interactive(inst, hb_path, _wizard_replies(inst, "yes"))
    assert first.returncode == 0, first.stdout + first.stderr
    (tmp_path / "docker.log").unlink()
    (tmp_path / "hb_out").write_text("qradar: already decided on 2026-10-19: YES\n")

    again = inst.run("install", "--skip-qradar-check", path=hb_path)

    assert again.returncode == 0, again.stdout + again.stderr
    assert "Re-run: reusing the saved answers" in again.stdout
    assert QUESTION not in again.stdout
    _assert_decide_between_add_and_start(tmp_path, "yes")
    assert "History backfill   already decided on 2026-10-19: YES\n" in again.stdout


def test_reconfigure_with_the_key_saved_does_not_ask_it_again(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    (tmp_path / "hb_out").write_text(RECORDED_NO + "\n")
    first = _interactive(inst, hb_path, _wizard_replies(inst, "no"))
    assert first.returncode == 0, first.stdout + first.stderr
    # A first install that failed late leaves the saved answers and no state file.
    (inst.root / ".qradar-dash" / "state.json").unlink()
    (tmp_path / "docker.log").unlink()

    again = _interactive(inst, hb_path, _wizard_replies(inst), "--reconfigure")

    assert again.returncode == 0, again.stdout + again.stderr
    assert "1. Public host name" in again.stdout  # the wizard did run
    assert QUESTION not in again.stdout
    _assert_decide_between_add_and_start(tmp_path, "no")


def test_reconfigure_on_a_site_with_a_state_file_and_no_saved_key_does_not_ask(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    before = _scripted(inst, tmp_path, hb_path)  # a site installed before the question existed
    assert before.returncode == 0, before.stdout + before.stderr
    assert _state_exists(inst)
    (tmp_path / "docker.log").unlink()

    again = _interactive(inst, hb_path, _wizard_replies(inst), "--reconfigure")

    assert again.returncode == 0, again.stdout + again.stderr
    assert "1. Public host name" in again.stdout
    assert QUESTION not in again.stdout
    assert _decide_calls(tmp_path) == []
    assert NOT_DECIDED in again.stdout


def test_a_decide_failure_warns_and_the_install_goes_on(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path, hb_rc=1, HISTORY_BACKFILL="yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"[warn] {HB_WARNING}" in result.stderr
    assert HB_KEEP_ON in result.stdout
    assert len(_decide_calls(tmp_path)) == 1
    assert any("up -d --wait --wait-timeout 180 backend" in x for x in _docker_log(tmp_path))
    assert _state_exists(inst)


def test_a_failed_qradar_check_still_records_the_answer(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path, qr_rc=3, hb_out=RECORDED_YES,
                       HISTORY_BACKFILL="yes")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "QRadar connection  FAILED (exit 3)" in result.stdout
    _assert_decide_between_add_and_start(tmp_path, "yes")
    assert "History backfill   recorded YES (28 days) from the installer\n" in result.stdout
    assert _state_exists(inst)


def test_a_failed_qradar_check_and_a_decide_failure_still_start_the_services(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path, qr_rc=3, hb_rc=2, HISTORY_BACKFILL="no")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "the answer was not recorded (exit 2)" in result.stderr
    assert "History backfill   not recorded (exit 2); the first administrator" in result.stdout
    assert _state_exists(inst)


def test_dry_run_runs_no_decide_and_writes_nothing(
    inst: Install, tmp_path: Path, hb_path: str
) -> None:
    result = _scripted(inst, tmp_path, hb_path, "--dry-run", HISTORY_BACKFILL="yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Dry run: every check passed; nothing was written." in result.stdout
    assert _decide_calls(tmp_path) == []
    assert not (inst.root / ".env").exists()
    assert not (inst.root / ".qradar-dash").exists()


def test_upgrade_never_asks_and_never_calls_decide(upgrade: UpgradeFixture) -> None:
    # A saved answers file that parse_answers would refuse (mode 0644): upgrade must not read it.
    secrets = upgrade.root / ".secrets"
    secrets.mkdir(mode=0o700)
    answers = secrets / "install.answers"
    answers.write_text("HISTORY_BACKFILL=yes\nNOT_A_KEY=1\n")
    answers.chmod(0o644)

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode == 0, output(result)
    assert "must be 0600" not in output(result)
    assert QUESTION not in output(result)
    assert "history_backfill" not in upgrade.action_text()
    assert answers.read_text() == "HISTORY_BACKFILL=yes\nNOT_A_KEY=1\n"


def test_the_upgrade_command_does_not_reach_the_wizard_or_the_answers_parser() -> None:
    text = SCRIPT.read_text()
    body = text.split("\ncmd_upgrade() {", 1)[1].split("\nupgrade_auto_rollback()", 1)[0]
    for name in ("wizard", "parse_answers", "HISTORY_BACKFILL", "history_backfill"):
        assert name not in body, name


def _env_file_mapping(inst: Install) -> dict[str, str]:
    """KEY=VALUE lines from a written ``.env``. Values stay out of assertions."""
    mapping: dict[str, str] = {}
    for line in (inst.root / ".env").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        mapping[key] = value
    return mapping


async def test_a_fresh_install_env_passes_unknown_settings(
    inst: Install, tmp_path: Path, build_path: str
) -> None:
    """The ``.env`` ``install`` just wrote is not an unknown-settings warning."""
    from app.cli.doctor import Status
    from app.core.config import Settings, unknown_setting_names
    from tests.unit.test_doctor import make_doctor

    _build(inst, tmp_path, build_path)
    environ = _env_file_mapping(inst)
    assert environ
    reported = unknown_setting_names(environ)
    assert reported == ()
    result = await make_doctor(
        Settings(_env_file=None, encryption_key="x" * 44),
        environ=environ,
    ).check_unknown_settings()
    assert result.status is Status.PASS
    assert result.message == "no unknown settings"
    assert result.runbook is None
