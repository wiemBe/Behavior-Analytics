"""Host-side doctor tests with no Docker daemon or network access."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

HOST_TOOLS = ("awk", "dirname", "head", "readlink", "sed")
GIB_KIB = 1024 * 1024
PS_FORMAT_ARG = r"{{.Service}}\t{{.State}}\t{{.Health}}\t{{.ExitCode}}"

DOCKER_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB_DOCKER_CALLS"
if [ "$1" = info ]; then
  printf '%s\n' "$STUB_DOCKER_ROOT"
  exit "${STUB_DOCKER_INFO_RC:-0}"
fi
case "$*" in
  *" ps --all --format "*)
    expected='{{.Service}}\t{{.State}}\t{{.Health}}\t{{.ExitCode}}'
    actual=${!#}
    if [ "$actual" != "$expected" ]; then
      printf 'unexpected compose ps format: %s\n' "$actual" >&2
      exit 98
    fi
    printf '%s\n' "$STUB_PS_ROWS"
    exit "${STUB_PS_RC:-0}"
    ;;
  *" exec -T postgres psql "*)
    printf '%s\n' "$STUB_DATABASE_SIZE"
    exit "${STUB_DATABASE_RC:-0}"
    ;;
  *" exec -T backend python -m app.cli.doctor")
    printf '%s\n' "$STUB_INCONTAINER_OUTPUT"
    exit "${STUB_INCONTAINER_RC:-0}"
    ;;
esac
exit 97
"""

DF_STUB = r"""#!/bin/bash
for argument in "$@"; do target=$argument; done
if [ "$target" = "$STUB_DOCKER_ROOT" ]; then
  used=$STUB_DOCKER_USED
  free=$STUB_DOCKER_FREE_KIB
elif [ "$target" = "$STUB_BACKUP_DIR" ]; then
  used=$STUB_BACKUP_USED
  free=$STUB_BACKUP_FREE_KIB
else
  exit 1
fi
printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
printf 'fixture 104857600 1 %s %s%% %s\n' "$free" "$used" "$target"
exit "${STUB_DF_RC:-0}"
"""

TIMEDATECTL_STUB = r"""#!/bin/bash
printf '%s\n' "$STUB_NTP_SYNC"
exit "${STUB_TIMEDATECTL_RC:-0}"
"""

GETENFORCE_STUB = r"""#!/bin/bash
printf '%s\n' "$STUB_SELINUX"
exit "${STUB_GETENFORCE_RC:-0}"
"""


def compose_row(
    service: str,
    state: str = "running",
    health: str = "healthy",
    exit_code: int = 0,
) -> str:
    return f"{service}\t{state}\t{health}\t{exit_code}"


DEFAULT_SERVICES = "\n".join(
    [
        compose_row("postgres"),
        compose_row("redis"),
        compose_row("backend"),
        compose_row("celery-worker"),
        compose_row("celery-beat"),
        compose_row("frontend"),
        compose_row("proxy"),
    ]
)


class DoctorFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        self.script = self.root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)

        self.docker_root = tmp_path / "docker-root"
        self.backup_dir = tmp_path / "backups"
        self.docker_root.mkdir()
        self.backup_dir.mkdir()
        (self.root / ".env").write_text(
            "COMPOSE_PROJECT_NAME=fixture-project\n"
            "COMPOSE_PROFILES=proxy\n"
            f"QRADAR_DASH_BACKUP_DIR={self.backup_dir}\n"
            "POSTGRES_USER=qradar_obs\n"
            "POSTGRES_PASSWORD=planted-secret\n"
            "POSTGRES_DB=qradar_obs\n"
        )

        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        for tool in HOST_TOOLS:
            executable = shutil.which(tool)
            assert executable is not None
            (self.bin_dir / tool).symlink_to(executable)
        self._stub("docker", DOCKER_STUB)
        self._stub("df", DF_STUB)
        self._stub("timedatectl", TIMEDATECTL_STUB)
        self._stub("getenforce", GETENFORCE_STUB)

        self.calls = tmp_path / "docker.calls"
        self.env = {
            "HOME": str(tmp_path),
            "PATH": str(self.bin_dir),
            "STUB_DOCKER_CALLS": str(self.calls),
            "STUB_DOCKER_ROOT": str(self.docker_root),
            "STUB_DOCKER_USED": "50",
            "STUB_DOCKER_FREE_KIB": str(40 * GIB_KIB),
            "STUB_BACKUP_DIR": str(self.backup_dir),
            "STUB_BACKUP_USED": "50",
            "STUB_BACKUP_FREE_KIB": str(20 * GIB_KIB),
            "STUB_DATABASE_SIZE": str(1 * 1024 * 1024 * 1024),
            "STUB_PS_ROWS": DEFAULT_SERVICES,
            "STUB_NTP_SYNC": "yes",
            "STUB_SELINUX": "Enforcing",
            "STUB_INCONTAINER_OUTPUT": "PASS  db_connect       database accepted SELECT 1",
        }

    def _stub(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content)
        path.chmod(0o755)

    def remove_stub(self, name: str) -> None:
        (self.bin_dir / name).unlink()

    def run(self, **overrides: str) -> subprocess.CompletedProcess[str]:
        env = {**self.env, **overrides}
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.script), "doctor"],
            cwd=self.root,
            env=env,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            stdin=subprocess.DEVNULL,
        )


@pytest.fixture
def doctor(tmp_path: Path) -> DoctorFixture:
    return DoctorFixture(tmp_path)


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_all_pass_returns_zero(doctor: DoctorFixture) -> None:
    result = doctor.run()

    assert result.returncode == 0, output(result)
    assert "PASS  disk_docker" in result.stdout
    assert "50% used; 40.0 GiB free" in result.stdout
    assert "PASS  disk_backup" in result.stdout
    assert "backup guard passes" in result.stdout
    assert "PASS  qradar_clock" in result.stdout
    assert "PASS  selinux          Enforcing" in result.stdout
    assert "PASS  containers" in result.stdout
    assert "PASS  db_connect       database accepted SELECT 1" in result.stdout
    assert f"ps --all --format {PS_FORMAT_ARG}" in doctor.calls.read_text()


def test_docker_root_at_75_percent_warns(doctor: DoctorFixture) -> None:
    result = doctor.run(STUB_DOCKER_USED="75")

    assert result.returncode == 1
    assert "WARN  disk_docker" in result.stdout
    assert "75% used" in result.stdout
    assert "runbook: OPS-R14" in result.stdout


def test_docker_root_at_92_percent_fails(doctor: DoctorFixture) -> None:
    result = doctor.run(STUB_DOCKER_USED="92")

    assert result.returncode == 2
    assert "FAIL  disk_docker" in result.stdout
    assert "92% used" in result.stdout


def test_backup_guard_fails_when_free_space_is_too_small(doctor: DoctorFixture) -> None:
    result = doctor.run(
        STUB_BACKUP_FREE_KIB=str(21 * GIB_KIB),
        STUB_DATABASE_SIZE=str(10 * 1024 * 1024 * 1024),
    )

    assert result.returncode == 2
    assert "FAIL  disk_backup" in result.stdout
    assert "21.0 GiB free" in result.stdout
    assert "backup guard needs 22.0 GiB" in result.stdout


def test_missing_backup_directory_warns_and_names_path(doctor: DoctorFixture) -> None:
    doctor.backup_dir.rmdir()

    result = doctor.run()

    assert result.returncode == 1
    assert "WARN  disk_backup" in result.stdout
    assert f"backup directory does not exist: {doctor.backup_dir}" in result.stdout


def test_ntp_not_synchronized_warns(doctor: DoctorFixture) -> None:
    result = doctor.run(STUB_NTP_SYNC="no")

    assert result.returncode == 1
    assert "WARN  qradar_clock" in result.stdout
    assert "NTP is not synchronized" in result.stdout
    assert "runbook: OPS-R15" in result.stdout


def test_missing_timedatectl_warns(doctor: DoctorFixture) -> None:
    doctor.remove_stub("timedatectl")

    result = doctor.run()

    assert result.returncode == 1
    assert "WARN  qradar_clock" in result.stdout
    assert "timedatectl is not available; check NTP by hand" in result.stdout


def test_missing_getenforce_is_a_pass(doctor: DoctorFixture) -> None:
    doctor.remove_stub("getenforce")

    result = doctor.run()

    assert result.returncode == 0, output(result)
    assert "PASS  selinux          SELinux is not installed" in result.stdout


def test_unhealthy_service_fails_and_names_it(doctor: DoctorFixture) -> None:
    services = "\n".join(
        [
            compose_row("postgres"),
            compose_row("backend"),
            compose_row("celery-worker", health="unhealthy"),
        ]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    assert result.returncode == 2
    assert "FAIL  containers" in result.stdout
    assert "celery-worker is running (health: unhealthy)" in result.stdout
    assert "runbook: OPS-R41" in result.stdout


def test_stopped_mcp_is_ignored_when_profile_is_off(doctor: DoctorFixture) -> None:
    services = "\n".join(
        [DEFAULT_SERVICES, compose_row("qradar-mcp", state="exited", health="")]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    assert result.returncode == 0, output(result)
    assert "PASS  containers" in result.stdout
    assert "qradar-mcp is" not in result.stdout


def test_successful_exited_migrate_is_ignored_and_not_counted(doctor: DoctorFixture) -> None:
    services = "\n".join(
        [DEFAULT_SERVICES, compose_row("migrate", state="exited", health="", exit_code=0)]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    assert result.returncode == 0, output(result)
    assert "PASS  containers       7 Compose service(s) are running and healthy" in result.stdout
    assert "migrate is" not in result.stdout


def test_failed_exited_migrate_fails_with_exit_code(doctor: DoctorFixture) -> None:
    services = "\n".join(
        [DEFAULT_SERVICES, compose_row("migrate", state="exited", health="", exit_code=17)]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    assert result.returncode == 2
    assert "FAIL  containers       migrate is exited (exit code: 17)" in result.stdout
    assert "runbook: OPS-R11" in result.stdout


def test_container_runbook_is_kept_when_all_failures_map_to_it(
    doctor: DoctorFixture,
) -> None:
    services = "\n".join(
        [
            compose_row("postgres", state="exited", health=""),
            compose_row("backend", state="exited", health=""),
        ]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    containers = next(line for line in result.stdout.splitlines() if "containers" in line)
    assert containers.endswith("runbook: OPS-R36")


def test_container_runbook_is_omitted_when_failures_do_not_share_one(
    doctor: DoctorFixture,
) -> None:
    services = "\n".join(
        [
            compose_row("backend"),
            compose_row("celery-worker", health="unhealthy"),
            compose_row("frontend", state="exited", health=""),
        ]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    containers = next(line for line in result.stdout.splitlines() if "containers" in line)
    assert "celery-worker is running (health: unhealthy); frontend is exited" in containers
    assert "runbook:" not in containers


def test_incontainer_exit_two_sets_overall_exit_two(doctor: DoctorFixture) -> None:
    result = doctor.run(
        STUB_INCONTAINER_OUTPUT="FAIL  db_connect       database rejected SELECT 1",
        STUB_INCONTAINER_RC="2",
    )

    assert result.returncode == 2
    assert "FAIL  db_connect       database rejected SELECT 1" in result.stdout


def test_backend_not_running_skips_incontainer_exec(doctor: DoctorFixture) -> None:
    services = "\n".join(
        [compose_row("postgres"), compose_row("backend", state="exited", health="")]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    assert result.returncode == 2
    assert "FAIL  doctor_incontainer backend container is not running" in result.stdout
    assert "runbook: OPS-R36" in result.stdout
    calls = doctor.calls.read_text()
    assert "exec -T backend" not in calls


def test_env_secret_never_appears_in_output(doctor: DoctorFixture) -> None:
    result = doctor.run()

    assert "planted-secret" not in output(result)


def test_need_installed_runs_before_host_checks(doctor: DoctorFixture) -> None:
    (doctor.root / ".env").unlink()

    result = doctor.run()

    assert result.returncode == 1
    assert "not installed here: .env is missing" in result.stderr
    assert not doctor.calls.exists()


def test_missing_docker_is_reported_as_fail_lines(doctor: DoctorFixture) -> None:
    doctor.remove_stub("docker")

    result = doctor.run()

    assert result.returncode == 2
    assert "FAIL  disk_docker      Docker is not installed" in result.stdout
    assert "FAIL  containers       Docker is not installed" in result.stdout


def test_unavailable_database_size_warns_without_failing_disk(doctor: DoctorFixture) -> None:
    result = doctor.run(STUB_DATABASE_RC="1")

    assert result.returncode == 1
    assert "WARN  disk_backup" in result.stdout
    assert "database size unavailable" in result.stdout


def test_stopped_mcp_fails_when_profile_is_on(doctor: DoctorFixture) -> None:
    env_file = doctor.root / ".env"
    env_file.write_text(
        env_file.read_text().replace("COMPOSE_PROFILES=proxy", "COMPOSE_PROFILES=mcp")
    )
    services = "\n".join(
        [DEFAULT_SERVICES, compose_row("qradar-mcp", state="exited", health="")]
    )

    result = doctor.run(STUB_PS_ROWS=services)

    assert result.returncode == 2
    assert "qradar-mcp is exited" in result.stdout


def test_compose_ps_failure_is_a_fail(doctor: DoctorFixture) -> None:
    result = doctor.run(STUB_PS_RC="1")

    assert result.returncode == 2
    assert "FAIL  containers       docker compose ps failed" in result.stdout
    assert "FAIL  doctor_incontainer backend container is not running" in result.stdout


def test_incontainer_exit_one_sets_overall_exit_one(doctor: DoctorFixture) -> None:
    result = doctor.run(
        STUB_INCONTAINER_OUTPUT="WARN  cadence          collection is late",
        STUB_INCONTAINER_RC="1",
    )

    assert result.returncode == 1
    assert "WARN  cadence          collection is late" in result.stdout
