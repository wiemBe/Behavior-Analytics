"""Upgrade and rollback shell tests with Docker, HTTP, and PostgreSQL stubbed."""

from __future__ import annotations

import json
import shlex
import shutil
import stat
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

GIB = 1024 * 1024 * 1024
GIB_KIB = 1024 * 1024
OLD_VERSION = "0.1.0"
TARGET_VERSION = "0.2.0"
STAMP = "20261017T120000Z"
STATE_TIME = "2026-10-17T12:00:00Z"
SECRET_VALUES = (
    "fixture-encryption-key-material",
    "fixture-postgres-password-material",
    "fixture-qradar-token-material",
)

HOST_TOOLS = (
    "awk",
    "cat",
    "chmod",
    "dirname",
    "grep",
    "head",
    "hostname",
    "install",
    "mkdir",
    "readlink",
    "rm",
    "sed",
    "sort",
    "tail",
    "tr",
)


def digest(number: int) -> str:
    return f"sha256:{number:064x}"


OLD_IMAGES = {
    "postgres": digest(1),
    "redis": digest(2),
    "backend": digest(3),
    "frontend": digest(4),
    "proxy": digest(5),
}
NEW_IMAGES = {
    "postgres": digest(1),  # data services stay running during an application upgrade
    "redis": digest(2),
    "backend": digest(13),
    "frontend": digest(14),
    "proxy": digest(15),
}

DOCKER_STUB = r"""#!/bin/bash
printf 'docker %s\n' "$*" >> "$STUB_ACTIONS"
running_version=$(cat "$STUB_RUNNING_VERSION")
env_version=$(sed -n 's/^QRADAR_DASH_VERSION=//p' .env | head -n1)
if [ "$1" = info ]; then
  printf '%s\n' "$STUB_DOCKER_ROOT"
  exit 0
fi
if [ "$1 $2" = "volume inspect" ]; then exit 0; fi
if [ "$1" = inspect ]; then
  container=${!#}
  case "$container" in
    postgres-container) service=postgres ;;
    redis-container) service=redis ;;
    backend-container) service=backend ;;
    frontend-container) service=frontend ;;
    proxy-container) service=proxy ;;
    *) exit 1 ;;
  esac
  case "$service:$running_version" in
    postgres:* ) number=1 ;;
    redis:* ) number=2 ;;
    backend:0.1.0 ) number=3 ;;
    frontend:0.1.0 ) number=4 ;;
    proxy:0.1.0 ) number=5 ;;
    backend:* ) number=13 ;;
    frontend:* ) number=14 ;;
    proxy:* ) number=15 ;;
  esac
  printf '%s\tFalse\tsha256:%064x\n' "$service" "$number"
  exit 0
fi
if [ "$1 $2" = "image inspect" ]; then
  subject=${!#}
  [ "${STUB_OFFLINE_MISSING:-0}" -eq 0 ] || exit 1
  if [ "$3" = "--format" ]; then
    if [ "${STUB_IMAGE_ID_MISMATCH:-0}" -eq 1 ]; then
      printf 'sha256:%064x\n' 999
    else
      printf '%s\n' "$subject"
    fi
  fi
  exit 0
fi
if [ "$1" = tag ]; then exit 0; fi
case "$*" in
  *" pull "*) exit "${STUB_PULL_RC:-0}" ;;
  "run --rm --entrypoint cat "*)
    [ "${STUB_NOTES_RC:-0}" -eq 0 ] || exit "$STUB_NOTES_RC"
    printf '%b' "$STUB_NOTES"
    ;;
  *" ps -a -q")
    printf '%s\n' postgres-container redis-container backend-container \
      frontend-container proxy-container
    ;;
  *" ps --services --status running")
    [ "$(cat "$STUB_RUNNING")" -eq 1 ] \
      && printf '%s\n' backend celery-worker celery-beat frontend proxy
    exit 0
    ;;
  *" ps --all --format "*)
    if [ "$(cat "$STUB_RUNNING")" -eq 1 ]; then
      printf '%b\n' 'postgres\trunning\thealthy\t0' 'redis\trunning\thealthy\t0' \
        'backend\trunning\thealthy\t0' 'celery-worker\trunning\thealthy\t0' \
        'celery-beat\trunning\thealthy\t0' 'frontend\trunning\thealthy\t0' \
        'proxy\trunning\thealthy\t0'
    fi
    ;;
  *" ps") printf '%s\n' 'fixture compose services' ;;
  *" stop "*) printf '%s\n' 0 > "$STUB_RUNNING" ;;
  *" start "*) printf '%s\n' 1 > "$STUB_RUNNING" ;;
  *" up -d --wait --wait-timeout 180 "*)
    if [ "${STUB_UP_FAIL_VERSION:-}" = "$env_version" ]; then exit 17; fi
    printf '%s\n' "$env_version" > "$STUB_RUNNING_VERSION"
    printf '%s\n' 1 > "$STUB_RUNNING"
    ;;
  *" run --rm -T migrate")
    [ "${STUB_MIGRATE_KILL_PARENT:-0}" -eq 0 ] || kill -KILL "$PPID"
    [ "${STUB_MIGRATE_RC:-0}" -eq 0 ] || exit "$STUB_MIGRATE_RC"
    ;;
  *" exec -T redis redis-cli FLUSHALL") exit "${STUB_FLUSH_RC:-0}" ;;
  *" exec -T postgres psql "*)
    query=$(cat)
    printf 'SQL:%s\n' "$query" >> "$STUB_ACTIONS"
    case "$query" in
      *pg_database_size*) printf '%s\n' "$STUB_DB_SIZE" ;;
      *"SELECT version_num FROM alembic_version"*) printf '%s\n' 0025 ;;
      *"SELECT count(*)"*) printf '%s\n' 1 ;;
    esac
    ;;
  *" exec -T backend python -m app.cli.doctor")
    [ "${STUB_DOCTOR_SILENT:-0}" -eq 0 ] || exit 2
    printf '%s\n' 'PASS  db_connect       database accepted SELECT 1'
    if [ "${STUB_MIGRATION_FAIL_VERSION:-}" = "$running_version" ]; then
      printf '%s\n' 'FAIL  migration_head   database at 0024, image expects 0025'
      exit 2
    fi
    printf '%s\n' 'PASS  migration_head   database at image head'
    [ "${STUB_QRADAR_FAIL:-0}" -eq 0 ] \
      || printf '%s\n' 'FAIL  qradar_auth      QRadar refused credentials'
    ;;
  *" exec -T postgres pg_dump "*) exit "${STUB_DUMP_RC:-0}" ;;
  *" exec -T postgres pg_restore "*) exit 0 ;;
  *" exec -T postgres rm -f -- "*) ;;
  *" cp postgres:"*)
    target=${!#}
    printf '%s\n' fixture-dump > "$target"
    ;;
  *" cp "*" postgres:"*) ;;
  run\ --rm\ -v\ *) printf '%s\n' fixture-secrets-tar ;;
  *)
    printf 'unexpected docker call: %s\n' "$*" >&2
    exit 97
    ;;
esac
"""

DF_STUB = r"""#!/bin/bash
target=${!#}
if [ "$target" = "$STUB_DOCKER_ROOT" ]; then
  free=$STUB_DOCKER_FREE_KIB
else
  free=$STUB_BACKUP_FREE_KIB
fi
printf '%s\n' 'Filesystem 1024-blocks Used Available Capacity Mounted on'
printf 'fixture 999999999 1 %s 1%% %s\n' "$free" "$target"
"""

STAT_STUB = r"""#!/bin/bash
if [ "$1 $2" = "-c %d" ]; then
  printf '%s\n' 1
  exit 0
fi
exec "$STUB_REAL_STAT" "$@"
"""

DATE_STUB = f"""#!/bin/bash
case "$*" in
  *+%Y%m%dT%H%M%SZ*) printf '%s\n' {STAMP} ;;
  *+%Y-%m-%dT%H:%M:%SZ*) printf '%s\n' {STATE_TIME} ;;
  *+%s*) printf '%s\n' 1792238400 ;;
  *) exit 1 ;;
esac
"""

CURL_STUB = r"""#!/bin/bash
url=${!#}
running_version=$(cat "$STUB_RUNNING_VERSION")
if [[ " $* " == *" -w "* ]]; then
  printf '%s\n' 200
  exit 0
fi
case "$url" in
  */health/ready)
    printf '%s\n' 'curl health' >> "$STUB_ACTIONS"
    if [ "${STUB_HEALTH_FAIL_VERSION:-}" = all ] \
      || [ "${STUB_HEALTH_FAIL_VERSION:-}" = "$running_version" ]; then
      exit 22
    fi
    printf '%s\n' '{"status":"ready","database":"ok"}'
    ;;
  */version)
    printf '%s\n' 'curl version' >> "$STUB_ACTIONS"
    if [ "${STUB_WRONG_VERSION_FOR:-}" = "$running_version" ]; then
      printf '%s\n' '{"version":"9.9.9"}'
    else
      printf '{"version":"%s"}\n' "$running_version"
    fi
    ;;
  *) exit 22 ;;
esac
"""

MV_STUB = r"""#!/bin/bash
target=${!#}
case "$target" in
  .env)
    printf '%s\n' env-edit >> "$STUB_ACTIONS"
    [ "${STUB_MV_FAIL_ENV:-0}" -eq 0 ] || exit 24
    ;;
  .qradar-dash/state.json)
    printf '%s\n' state-write >> "$STUB_ACTIONS"
    [ "${STUB_MV_FAIL_STATE:-0}" -eq 0 ] || exit 23
    ;;
esac
exec "$STUB_REAL_MV" "$@"
"""

SLEEP_STUB = "#!/bin/sh\nexit 0\n"
TIMEDATECTL_STUB = "#!/bin/sh\nprintf '%s\\n' yes\n"
GETENFORCE_STUB = "#!/bin/sh\nprintf '%s\\n' Enforcing\n"


class UpgradeFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        self.script = self.root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        self.backup_root = tmp_path / "backups"
        self.backup_root.mkdir(mode=0o700)
        self.docker_root = tmp_path / "docker-root"
        self.docker_root.mkdir()
        self.state_dir = self.root / ".qradar-dash"
        self.state_dir.mkdir(mode=0o700)
        self._write_env(OLD_VERSION)
        self.write_state()

        self.bin_dir = tmp_path / "bin"
        self.bin_dir.mkdir()
        for tool in HOST_TOOLS:
            executable = shutil.which(tool)
            assert executable is not None
            (self.bin_dir / tool).symlink_to(executable)
        self._stub("docker", DOCKER_STUB)
        self._stub("df", DF_STUB)
        self._stub("stat", STAT_STUB)
        self._stub("date", DATE_STUB)
        self._stub("curl", CURL_STUB)
        self._stub("mv", MV_STUB)
        self._stub("sleep", SLEEP_STUB)
        self._stub("timedatectl", TIMEDATECTL_STUB)
        self._stub("getenforce", GETENFORCE_STUB)

        self.actions = tmp_path / "actions"
        self.running = tmp_path / "running"
        self.running.write_text("1\n")
        self.running_version = tmp_path / "running-version"
        self.running_version.write_text(f"{OLD_VERSION}\n")
        self.env = {
            "HOME": str(tmp_path),
            "PATH": str(self.bin_dir),
            "STUB_ACTIONS": str(self.actions),
            "STUB_RUNNING": str(self.running),
            "STUB_RUNNING_VERSION": str(self.running_version),
            "STUB_DOCKER_ROOT": str(self.docker_root),
            "STUB_DB_SIZE": str(GIB),
            "STUB_BACKUP_FREE_KIB": str(10 * GIB_KIB),
            "STUB_DOCKER_FREE_KIB": str(10 * GIB_KIB),
            "STUB_REAL_STAT": str(shutil.which("stat")),
            "STUB_REAL_MV": str(shutil.which("mv")),
            "STUB_NOTES": self.notes(),
        }

    def _stub(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content)
        path.chmod(0o755)

    def _write_env(self, version: str) -> None:
        (self.root / ".env").write_text(
            "COMPOSE_PROJECT_NAME=fixture-project\n"
            "COMPOSE_PROFILES=proxy\n"
            "POSTGRES_USER=qradar_obs\n"
            f"POSTGRES_PASSWORD={SECRET_VALUES[1]}\n"
            "POSTGRES_DB=qradar_obs\n"
            f"ENCRYPTION_KEY={SECRET_VALUES[0]}\n"
            f"QRADAR_SEC_TOKEN={SECRET_VALUES[2]}\n"
            f"QRADAR_DASH_VERSION={version}\n"
            "QRADAR_DASH_IMAGE_PREFIX=registry.example/qradar\n"
            f"QRADAR_DASH_BACKUP_DIR={self.backup_root}\n"
            "SECRETS_VOLUME=fixture-secrets\n"
            "PROXY_CERTS_VOLUME=fixture-proxy\n"
            "API_LOOPBACK_PORT=8000\n"
        )
        (self.root / ".env").chmod(0o600)

    @staticmethod
    def notes(
        *, version: str = TARGET_VERSION, min_from: str = OLD_VERSION
    ) -> str:
        return f"---\nversion: {version}\nmin_from: {min_from}\n---\nChanges.\n"

    def write_state(
        self,
        *,
        installed: str = OLD_VERSION,
        images: dict[str, str] | None = None,
        previous: dict[str, str] | None = None,
        outcome: str | None = None,
        backup: Path | None = None,
        restored_from: Path | None = None,
    ) -> None:
        state: dict[str, object] = {
            "installed_version": installed,
            "images": images or OLD_IMAGES,
            "previous_images": previous,
        }
        if outcome is not None:
            last: dict[str, str] = {
                "at": STATE_TIME,
                "from": OLD_VERSION,
                "to": TARGET_VERSION,
                "outcome": outcome,
                "backup": str(backup or self.backup_root / "missing"),
            }
            if restored_from is not None:
                last["restored_from"] = str(restored_from)
            state["last_upgrade"] = last
        path = self.state_dir / "state.json"
        path.write_text(json.dumps(state) + "\n")
        path.chmod(0o600)

    def make_backup(self, *, version: str = OLD_VERSION) -> Path:
        path = self.backup_root / f"qradar-dash-{STAMP}-pre-upgrade"
        path.mkdir(mode=0o700, exist_ok=True)
        files = {
            "database.dump": "dump\n",
            "env.bak": f"ENCRYPTION_KEY={SECRET_VALUES[0]}\n",
            "secrets.tar": "secrets\n",
            "state.json": "{}\n",
            "MANIFEST": (
                f"VERSION={version}\nIMAGE_DIGESTS=backend:{OLD_IMAGES['backend']}\n"
                f"ALEMBIC_VERSION=0025\nDATABASE_SIZE_BYTES={GIB}\nHOSTNAME=fixture\n"
                "STATE_JSON=included\nSECRETS_VOLUMES=qradar,proxy\n"
            ),
        }
        for name, content in files.items():
            target = path / name
            target.write_text(content)
            target.chmod(0o600)
        return path

    def run(self, *args: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.script), *args],
            cwd=self.root,
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
            stdin=subprocess.DEVNULL,
        )

    def source(self, command: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [
                bash,
                "-c",
                f'QRADAR_DASH_SOURCE_ONLY=1 source "$1"; {command}',
                "bash",
                str(self.script),
            ],
            cwd=self.root,
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def action_text(self) -> str:
        return self.actions.read_text() if self.actions.exists() else ""

    def state(self) -> dict[str, object]:
        return json.loads((self.state_dir / "state.json").read_text())


@pytest.fixture
def upgrade(tmp_path: Path) -> Iterator[UpgradeFixture]:
    fixture = UpgradeFixture(tmp_path)
    yield fixture
    combined = fixture.action_text()
    for secret in SECRET_VALUES:
        assert secret not in combined


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def assert_no_stop(upgrade: UpgradeFixture) -> None:
    assert " stop " not in upgrade.action_text()


@pytest.mark.parametrize("target", ["latest", "v0.2.0", "0.2.0-rc1"])
def test_invalid_target_is_refused_before_stop(
    upgrade: UpgradeFixture, target: str
) -> None:
    result = upgrade.run("upgrade", target)

    assert result.returncode != 0
    assert "plain SemVer" in result.stderr
    assert_no_stop(upgrade)


def test_newer_installed_version_is_refused_before_stop(upgrade: UpgradeFixture) -> None:
    upgrade.write_state(installed="0.3.0")

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode != 0
    assert "that is rollback" in result.stderr
    assert_no_stop(upgrade)


def test_failed_outcome_blocks_upgrade_before_pull_or_stop(upgrade: UpgradeFixture) -> None:
    upgrade.write_state(outcome="failed")

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode != 0
    assert "last upgrade failed" in result.stderr
    assert " pull " not in upgrade.action_text()
    assert_no_stop(upgrade)


def test_pull_failure_is_before_stop(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, STUB_PULL_RC="19")

    assert result.returncode != 0
    assert "target images are unavailable" in result.stderr
    assert_no_stop(upgrade)


@pytest.mark.parametrize(
    "overrides",
    [
        {"STUB_NOTES_RC": "1"},
        {"STUB_NOTES": "not front matter\n"},
        {"STUB_NOTES": UpgradeFixture.notes(version="0.9.0")},
        {"STUB_NOTES": UpgradeFixture.notes(min_from="0.1.1")},
    ],
)
def test_bad_release_notes_are_refused_after_pull_before_stop(
    upgrade: UpgradeFixture, overrides: dict[str, str]
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, **overrides)

    assert result.returncode != 0
    assert " pull " in upgrade.action_text()
    assert_no_stop(upgrade)


def test_disk_guard_refuses_before_stop(upgrade: UpgradeFixture) -> None:
    result = upgrade.run(
        "upgrade",
        TARGET_VERSION,
        STUB_BACKUP_FREE_KIB=str(5 * GIB_KIB - 1),
    )

    assert result.returncode != 0
    assert "disk guard refused" in result.stderr
    assert_no_stop(upgrade)


def test_migration_head_preflight_failure_is_before_stop(upgrade: UpgradeFixture) -> None:
    result = upgrade.run(
        "upgrade", TARGET_VERSION, STUB_MIGRATION_FAIL_VERSION=OLD_VERSION
    )

    assert result.returncode != 0
    assert "OPS-R11" in result.stderr
    assert_no_stop(upgrade)


def test_unparseable_state_is_refused_before_pull_or_stop(upgrade: UpgradeFixture) -> None:
    (upgrade.state_dir / "state.json").write_text("not-json\n")

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode != 0
    assert ".qradar-dash/state.json" in result.stderr
    assert " pull " not in upgrade.action_text()
    assert_no_stop(upgrade)


def test_same_healthy_version_changes_nothing(upgrade: UpgradeFixture) -> None:
    before = (upgrade.state_dir / "state.json").read_bytes()

    result = upgrade.run("upgrade", OLD_VERSION)

    assert result.returncode == 0, output(result)
    assert "already installed and healthy" in result.stdout
    actions = upgrade.action_text()
    assert " pull " not in actions
    assert " stop " not in actions
    assert "state-write" not in actions
    assert (upgrade.state_dir / "state.json").read_bytes() == before


def test_offline_upgrade_checks_local_images_and_never_pulls(
    upgrade: UpgradeFixture,
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, "--offline")

    assert result.returncode == 0, output(result)
    actions = upgrade.action_text()
    assert "docker image inspect registry.example/qradar/backend:0.2.0" in actions
    assert " pull " not in actions


def test_success_has_safe_order_private_state_and_one_line_env_edit(
    upgrade: UpgradeFixture,
) -> None:
    before_lines = (upgrade.root / ".env").read_text().splitlines()

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode == 0, output(result)
    actions = upgrade.action_text()
    ordered = [
        " pull ",
        "run --rm --entrypoint cat",
        " stop ",
        "pg_dump",
        "qradar_obs_restore_test",
        "redis redis-cli FLUSHALL",
        "state-write",
        "env-edit",
        "run --rm -T migrate",
        " up -d --wait --wait-timeout 180 ",
        "curl health",
        "curl version",
        "exec -T backend python -m app.cli.doctor",
        "state-write",
    ]
    positions = []
    start = 0
    for item in ordered:  # each step must appear after the previous one
        start = actions.index(item, start)
        positions.append(start)
    assert positions == sorted(positions)
    assert actions.count("state-write") == 2  # the attempt, then the outcome
    state = upgrade.state()
    assert state["installed_version"] == TARGET_VERSION
    assert state["previous_images"] == OLD_IMAGES
    assert state["images"] == NEW_IMAGES
    last = state["last_upgrade"]
    assert last["outcome"] == "success"  # type: ignore[index]
    assert (last["from"], last["to"]) == (OLD_VERSION, TARGET_VERSION)  # type: ignore[index]
    assert last["backup"].endswith(f"qradar-dash-{STAMP}-pre-upgrade")  # type: ignore[index]
    assert (upgrade.backup_root / f"qradar-dash-{STAMP}-pre-upgrade" / "MANIFEST").is_file()
    assert stat.S_IMODE((upgrade.state_dir / "state.json").stat().st_mode) == 0o600
    after_lines = (upgrade.root / ".env").read_text().splitlines()
    changed = [
        (old, new)
        for old, new in zip(before_lines, after_lines, strict=True)
        if old != new
    ]
    assert changed == [
        ("QRADAR_DASH_VERSION=0.1.0", "QRADAR_DASH_VERSION=0.2.0")
    ]


def test_failed_backup_restarts_old_services_and_keeps_state(
    upgrade: UpgradeFixture,
) -> None:
    before = (upgrade.state_dir / "state.json").read_bytes()

    result = upgrade.run("upgrade", TARGET_VERSION, STUB_DUMP_RC="17")

    assert result.returncode != 0
    actions = upgrade.action_text()
    assert " start backend celery-worker celery-beat frontend proxy" in actions
    assert "pre-upgrade backup failed" in result.stderr
    assert "FLUSHALL" not in actions
    assert "env-edit" not in actions
    assert "run --rm -T migrate" not in actions
    assert upgrade.running_version.read_text().strip() == OLD_VERSION
    assert (upgrade.state_dir / "state.json").read_bytes() == before


@pytest.mark.parametrize(
    "overrides",
    [
        {"STUB_MIGRATE_RC": "17"},
        {"STUB_HEALTH_FAIL_VERSION": TARGET_VERSION},
        {"STUB_WRONG_VERSION_FOR": TARGET_VERSION},
        {"STUB_MIGRATION_FAIL_VERSION": TARGET_VERSION},
    ],
)
def test_automatic_rollback_triggers_restore_and_old_start(
    upgrade: UpgradeFixture, overrides: dict[str, str]
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, **overrides)

    assert result.returncode != 0, output(result)
    assert "was rolled back" in result.stderr
    actions = upgrade.action_text()
    assert actions.count("ALTER DATABASE qradar_obs RENAME TO qradar_obs_broken_") == 1
    assert "docker tag" in actions
    assert upgrade.running_version.read_text().strip() == OLD_VERSION
    assert (upgrade.root / ".env").read_text().count(
        f"QRADAR_DASH_VERSION={OLD_VERSION}"
    ) == 1
    state = upgrade.state()
    assert state["last_upgrade"]["outcome"] == "rolled_back"  # type: ignore[index]
    assert state["installed_version"] == OLD_VERSION
    assert state["previous_images"] is None


def test_qradar_doctor_failure_is_not_a_rollback_trigger(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, STUB_QRADAR_FAIL="1")

    assert result.returncode == 0, output(result)
    assert upgrade.state()["last_upgrade"]["outcome"] == "success"  # type: ignore[index]
    assert "Database swap complete (rollback)" not in result.stdout
    assert "ALTER DATABASE" not in upgrade.action_text()
    assert upgrade.running_version.read_text().strip() == TARGET_VERSION


def test_failed_rollback_stops_writers_records_failed_and_has_no_third_attempt(
    upgrade: UpgradeFixture,
) -> None:
    result = upgrade.run(
        "upgrade",
        TARGET_VERSION,
        STUB_MIGRATE_RC="17",
        STUB_HEALTH_FAIL_VERSION="all",
    )

    assert result.returncode != 0
    actions = upgrade.action_text()
    assert actions.count("CREATE DATABASE qradar_obs_restore_20261017t120000z") == 1
    assert actions.count("ALTER DATABASE qradar_obs RENAME TO qradar_obs_broken_") == 1
    assert actions.count(" stop ") >= 2
    assert upgrade.running.read_text().strip() == "0"
    assert "Verified backup:" in result.stdout
    assert upgrade.state()["last_upgrade"]["outcome"] == "failed"  # type: ignore[index]


def test_rollback_refuses_null_previous_without_stop_or_pull(upgrade: UpgradeFixture) -> None:
    backup = upgrade.make_backup()
    upgrade.write_state(outcome="success", backup=backup)

    result = upgrade.run("rollback", "--non-interactive", "--yes")

    assert result.returncode != 0
    assert "previous_images is null" in result.stderr
    assert_no_stop(upgrade)
    assert " pull " not in upgrade.action_text()


def test_rollback_refuses_missing_backup_without_stop_or_pull(upgrade: UpgradeFixture) -> None:
    upgrade.write_state(previous=OLD_IMAGES, outcome="success")

    result = upgrade.run("rollback", "--non-interactive", "--yes")

    assert result.returncode != 0
    assert "missing or incomplete" in result.stderr
    assert_no_stop(upgrade)
    assert " pull " not in upgrade.action_text()


def test_rollback_refuses_digest_mismatch_without_stop_or_pull(
    upgrade: UpgradeFixture,
) -> None:
    backup = upgrade.make_backup()
    upgrade.write_state(previous=OLD_IMAGES, outcome="success", backup=backup)

    result = upgrade.run("rollback", "--non-interactive", "--yes", STUB_IMAGE_ID_MISMATCH="1")

    assert result.returncode != 0
    assert "wrong local digest" in result.stderr
    assert_no_stop(upgrade)
    assert " pull " not in upgrade.action_text()


def test_manual_rollback_restores_backup_and_records_outcome(
    upgrade: UpgradeFixture,
) -> None:
    backup = upgrade.make_backup()
    upgrade._write_env(TARGET_VERSION)
    upgrade.running_version.write_text(f"{TARGET_VERSION}\n")
    upgrade.write_state(
        installed=TARGET_VERSION,
        images=NEW_IMAGES,
        previous=OLD_IMAGES,
        outcome="success",
        backup=backup,
    )

    result = upgrade.run("rollback", "--non-interactive", "--yes")

    assert result.returncode == 0, output(result)
    assert " pull " not in upgrade.action_text()
    assert upgrade.running_version.read_text().strip() == OLD_VERSION
    state = upgrade.state()
    assert state["installed_version"] == OLD_VERSION
    assert state["previous_images"] is None
    assert state["last_upgrade"]["outcome"] == "rolled_back"  # type: ignore[index]


def test_successful_restore_marks_failed_state_as_cleared(
    upgrade: UpgradeFixture,
) -> None:
    backup = upgrade.make_backup()
    upgrade.write_state(
        previous=OLD_IMAGES,
        outcome="failed",
        backup=backup,
    )

    result = upgrade.run("restore", "--non-interactive", "--yes", str(backup))

    assert result.returncode == 0, output(result)
    state = upgrade.state()
    assert state["last_upgrade"]["outcome"] == "failed"  # type: ignore[index]
    assert state["last_upgrade"]["restored_from"] == str(backup)  # type: ignore[index]

    follow_up = upgrade.run("upgrade", TARGET_VERSION)
    assert "last upgrade failed" not in follow_up.stderr


def test_status_prints_state_version_digests_and_last_upgrade(
    upgrade: UpgradeFixture,
) -> None:
    backup = upgrade.make_backup()
    upgrade.write_state(
        previous=OLD_IMAGES,
        outcome="success",
        backup=backup,
    )

    result = upgrade.run("status")

    assert result.returncode == 0, output(result)
    assert "Installed version: 0.1.0" in result.stdout
    assert f"Image backend: {OLD_IMAGES['backend']}" in result.stdout
    assert "Last upgrade: success, 0.1.0 -> 0.2.0" in result.stdout
    assert f"Last upgrade backup: {backup}" in result.stdout


@pytest.mark.parametrize(
    "invalid",
    [
        "version",
        "digest",
        "path",
    ],
)
def test_state_writer_refuses_invalid_values(
    upgrade: UpgradeFixture, invalid: str
) -> None:
    version = "not-a-version" if invalid == "version" else OLD_VERSION
    images = (
        "backend:sha256:wrong"
        if invalid == "digest"
        else f"backend:{OLD_IMAGES['backend']}"
    )
    tail = ""
    if invalid == "path":
        tail = " ".join(
            shlex.quote(value)
            for value in (
                STATE_TIME,
                OLD_VERSION,
                TARGET_VERSION,
                "success",
                "/bad\\path",
            )
        )
    command = f"write_state {shlex.quote(version)} {shlex.quote(images)} '' {tail}"

    result = upgrade.source(command)

    assert result.returncode != 0
    assert "state write refused" in result.stderr


def test_failed_atomic_state_move_keeps_old_file_byte_for_byte(
    upgrade: UpgradeFixture,
) -> None:
    state_path = upgrade.state_dir / "state.json"
    before = state_path.read_bytes()
    image_map = f"backend:{OLD_IMAGES['backend']}"

    result = upgrade.source(
        f"write_state {TARGET_VERSION} {shlex.quote(image_map)} ''",
        STUB_MV_FAIL_STATE="1",
    )

    assert result.returncode != 0
    assert state_path.read_bytes() == before


def test_output_never_contains_planted_secrets(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode == 0, output(result)
    combined = output(result) + upgrade.action_text()
    for secret in SECRET_VALUES:
        assert secret not in combined


def test_doctor_that_reports_nothing_refuses_before_stop(upgrade: UpgradeFixture) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, STUB_DOCTOR_SILENT="1")

    assert result.returncode != 0
    assert "did not report db_connect and migration_head" in result.stderr
    assert_no_stop(upgrade)


def test_env_version_that_differs_from_state_refuses_before_stop(
    upgrade: UpgradeFixture,
) -> None:
    upgrade._write_env("0.1.5")

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode != 0
    assert "state file says 0.1.0" in result.stderr
    assert_no_stop(upgrade)


@pytest.mark.parametrize("override", [{"STUB_FLUSH_RC": "9"}, {"STUB_MV_FAIL_ENV": "1"}])
def test_failure_before_migration_restarts_old_services_and_keeps_old_env(
    upgrade: UpgradeFixture, override: dict[str, str]
) -> None:
    before_state = upgrade.state()

    result = upgrade.run("upgrade", TARGET_VERSION, **override)

    assert result.returncode != 0
    actions = upgrade.action_text()
    assert " start backend celery-worker celery-beat frontend proxy" in actions
    assert "run --rm -T migrate" not in actions
    assert upgrade.running_version.read_text().strip() == OLD_VERSION
    assert "QRADAR_DASH_VERSION=0.1.0" in (upgrade.root / ".env").read_text()
    assert upgrade.state() == before_state
    assert not list(upgrade.root.glob(".env.version.*"))


def test_rollback_refuses_state_missing_a_field_with_path_and_field(
    upgrade: UpgradeFixture,
) -> None:
    (upgrade.state_dir / "state.json").write_text('{"installed_version":"0.1.0"}\n')

    result = upgrade.run("rollback", "--non-interactive", "--yes")

    assert result.returncode != 0
    assert ".qradar-dash/state.json field images" in result.stderr
    assert_no_stop(upgrade)


def test_rollback_refuses_backup_key_the_install_cannot_decrypt(
    upgrade: UpgradeFixture,
) -> None:
    backup = upgrade.make_backup()
    (backup / "env.bak").write_text("ENCRYPTION_KEY=some-other-key\n")
    upgrade.write_state(previous=OLD_IMAGES, outcome="success", backup=backup)

    result = upgrade.run("rollback", "--non-interactive", "--yes")

    assert result.returncode != 0
    assert "ENCRYPTION_KEY" in result.stderr
    assert_no_stop(upgrade)
    assert "some-other-key" not in output(result)


def test_status_with_invalid_state_names_the_field_and_fails(
    upgrade: UpgradeFixture,
) -> None:
    (upgrade.state_dir / "state.json").write_text("{}\n")

    result = upgrade.run("status")

    assert result.returncode != 0
    assert ".qradar-dash/state.json field installed_version" in result.stderr


def test_restore_without_upgrade_history_invents_none(upgrade: UpgradeFixture) -> None:
    backup = upgrade.make_backup()

    result = upgrade.run("restore", "--non-interactive", "--yes", str(backup))

    assert result.returncode == 0, output(result)
    state = upgrade.state()
    assert state["installed_version"] == OLD_VERSION
    assert "last_upgrade" not in state


def _attempt_fixture(upgrade: UpgradeFixture) -> subprocess.CompletedProcess[str]:
    """Kill the run right after migrate starts: the stub migrate kills its parent shell."""
    return upgrade.run("upgrade", TARGET_VERSION, STUB_MIGRATE_KILL_PARENT="1")


def test_crash_during_migrate_leaves_state_that_lets_rollback_proceed(
    upgrade: UpgradeFixture,
) -> None:
    result = _attempt_fixture(upgrade)

    assert result.returncode != 0
    state = upgrade.state()
    assert state["installed_version"] == OLD_VERSION
    assert state["previous_images"] == OLD_IMAGES
    assert state["images"] == OLD_IMAGES
    last = state["last_upgrade"]
    assert (last["outcome"], last["from"], last["to"]) == (  # type: ignore[index]
        "failed", OLD_VERSION, TARGET_VERSION)
    assert f"QRADAR_DASH_VERSION={TARGET_VERSION}" in (upgrade.root / ".env").read_text()
    blocked = upgrade.run("upgrade", TARGET_VERSION)
    assert "last upgrade failed" in blocked.stderr
    upgrade.actions.write_text("")

    refused = upgrade.run("rollback", "--non-interactive")
    assert "requires --yes" in refused.stderr  # past every other refusal
    assert_no_stop(upgrade)
    done = upgrade.run("rollback", "--non-interactive", "--yes")
    assert done.returncode == 0, output(done)
    assert upgrade.state()["last_upgrade"]["outcome"] == "rolled_back"  # type: ignore[index]
    assert "QRADAR_DASH_VERSION=0.1.0" in (upgrade.root / ".env").read_text()


def test_attempt_record_failure_restarts_old_services_before_env_edit(
    upgrade: UpgradeFixture,
) -> None:
    result = upgrade.run("upgrade", TARGET_VERSION, STUB_MV_FAIL_STATE="1")

    assert result.returncode != 0
    assert "could not be recorded" in result.stderr
    actions = upgrade.action_text()
    assert "env-edit" not in actions
    assert "run --rm -T migrate" not in actions
    assert upgrade.running.read_text().strip() == "1"
    assert "QRADAR_DASH_VERSION=0.1.0" in (upgrade.root / ".env").read_text()


def _rollback_ready(upgrade: UpgradeFixture) -> None:
    backup = upgrade.make_backup()
    upgrade.write_state(previous=OLD_IMAGES, outcome="success", backup=backup)


def test_manual_rollback_wrong_word_refuses_before_stop(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)
    result = subprocess.run(  # noqa: S603
        [shutil.which("bash") or "/bin/bash", str(upgrade.script), "rollback"],
        cwd=upgrade.root, env=upgrade.env, input="restore\n",
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode != 0
    assert "confirmation was not given" in result.stderr
    assert_no_stop(upgrade)


def test_manual_rollback_right_word_proceeds(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)
    result = subprocess.run(  # noqa: S603
        [shutil.which("bash") or "/bin/bash", str(upgrade.script), "rollback"],
        cwd=upgrade.root, env=upgrade.env, input="rollback\n",
        capture_output=True, text=True, timeout=30, check=False,
    )

    assert result.returncode == 0, output(result)
    assert upgrade.state()["last_upgrade"]["outcome"] == "rolled_back"  # type: ignore[index]


def test_manual_rollback_non_interactive_needs_yes(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)

    result = upgrade.run("rollback", "--non-interactive")

    assert result.returncode != 0
    assert "requires --yes" in result.stderr
    assert_no_stop(upgrade)


def test_manual_rollback_without_stdin_refuses(upgrade: UpgradeFixture) -> None:
    _rollback_ready(upgrade)

    result = upgrade.run("rollback")  # stdin is /dev/null

    assert result.returncode != 0
    assert_no_stop(upgrade)
