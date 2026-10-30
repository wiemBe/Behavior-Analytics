"""Backup and restore shell tests with every external effect stubbed on PATH."""

from __future__ import annotations

import re
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
KEY = "fixture-current-encryption-key"
OLD_KEY = "fixture-old-encryption-key"
STAMP = "20261017T120000Z"
DB_STAMP = STAMP.lower()

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
    "mv",
    "readlink",
    "rm",
    "sed",
    "sort",
    "tail",
    "tr",
)

DOCKER_STUB = r"""#!/bin/bash
printf '%s\n' "$*" >> "$STUB_DOCKER_CALLS"
if [ "$1" = info ]; then
  printf '%s\n' "$STUB_DOCKER_ROOT"
  exit "${STUB_DOCKER_INFO_RC:-0}"
fi
if [ "$1 $2" = "volume inspect" ]; then
  case "$3" in
    fixture-secrets) exit "${STUB_QRADAR_VOLUME_RC:-0}" ;;
    fixture-proxy) exit "${STUB_PROXY_VOLUME_RC:-0}" ;;
    *) exit 1 ;;
  esac
fi
if [ "$1" = inspect ]; then
  container=${!#}
  case "$container" in
    postgres-container)
      printf 'postgres\tFalse\tsha256:%064d\n' 1
      ;;
    backend-container)
      printf 'backend\tFalse\tsha256:%064d\n' 2
      ;;
    frontend-container)
      printf 'frontend\tFalse\tsha256:%064d\n' 3
      ;;
    migrate-container)
      printf 'migrate\tTrue\tsha256:%064d\n' 4
      ;;
    *) exit 1 ;;
  esac
  exit 0
fi
case "$*" in
  *" exec -T postgres psql "*)
    query=$(cat)
    printf 'SQL:%s\n' "$query" >> "$STUB_DOCKER_CALLS"
    case "$query" in
      *pg_database_size*) printf '%s\n' "$STUB_DB_SIZE" ;;
      *"SELECT version_num FROM alembic_version"*)
        case "$*" in
          *" -d qradar_obs_restore_"*) printf '%s\n' "${STUB_RESTORE_ALEMBIC:-0025}" ;;
          *) printf '%s\n' 0025 ;;
        esac
        ;;
      *"SELECT count(*)"*) printf '%s\n' 1 ;;
    esac
    if [ -n "${STUB_SQL_FAIL_MATCH:-}" ] && [[ $query == *"$STUB_SQL_FAIL_MATCH"* ]]; then
      if [ "${STUB_SQL_FAIL_ONCE:-0}" = 1 ]; then
        if [ ! -e "$STUB_SQL_FAILED_ONCE" ]; then
          : > "$STUB_SQL_FAILED_ONCE"
          exit 19
        fi
      else
        exit 19
      fi
    fi
    if [ "${STUB_RENAME_BACK_RC:-0}" -ne 0 ] \
      && [[ $query == *"ALTER DATABASE qradar_obs_broken_"* ]]; then
      exit "$STUB_RENAME_BACK_RC"
    fi
    ;;
  *" exec -T postgres pg_dump "*)
    [ "${STUB_DUMP_RC:-0}" -eq 0 ] || exit "$STUB_DUMP_RC"
    ;;
  *" exec -T postgres pg_restore "*)
    [ "${STUB_PG_RESTORE_RC:-0}" -eq 0 ] || exit "$STUB_PG_RESTORE_RC"
    ;;
  *" exec -T postgres rm -f -- "*) ;;
  *" cp postgres:"*)
    [ "${STUB_COPY_OUT_RC:-0}" -eq 0 ] || exit "$STUB_COPY_OUT_RC"
    target=${!#}
    printf '%s\n' fixture-dump > "$target"
    ;;
  *" cp "*" postgres:"*)
    [ "${STUB_COPY_IN_RC:-0}" -eq 0 ] || exit "$STUB_COPY_IN_RC"
    ;;
  *" config --services") printf '%s\n' postgres migrate backend frontend ;;
  *" ps -a -q")
    printf '%s\n' ${STUB_CONTAINER_IDS:-postgres-container backend-container frontend-container}
    ;;
  *" ps --services --status running")
    printf '%s\n' backend celery-worker celery-beat frontend proxy
    ;;
  *" stop "*|*" start "*) ;;
  run\ --rm\ --network\ none\ --cap-drop\ ALL\ *)
    [ "${STUB_SECRETS_RC:-0}" -eq 0 ] || exit "$STUB_SECRETS_RC"
    printf '%s\n' fixture-secrets-tar
    ;;
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
  if [ "$4" = "$STUB_DOCKER_ROOT" ] || [ "$3" = "$STUB_DOCKER_ROOT" ]; then
    printf '%s\n' "$STUB_DOCKER_DEVICE"
  else
    printf '%s\n' "$STUB_BACKUP_DEVICE"
  fi
  exit 0
fi
exec "$STUB_REAL_STAT" "$@"
"""

DATE_STUB = f"#!/bin/sh\nprintf '%s\\n' {STAMP}\n"
CURL_STUB = "#!/bin/sh\nexit ${STUB_CURL_RC:-0}\n"
SLEEP_STUB = "#!/bin/sh\nexit 0\n"
ID_STUB = r"""#!/bin/sh
case "$1" in
  -u) printf '%s\n' "${STUB_UID:-1000}" ;;
  -un) printf '%s\n' fixture-operator ;;
  *) exit 1 ;;
esac
"""
SYSTEMCTL_STUB = r"""#!/bin/sh
printf '%s\n' "$*" >> "$STUB_SYSTEMCTL_CALLS"
"""


class BackupFixture:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        self.script = self.root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        (self.root / "VERSION").write_text("0.1.0\n")
        # The backup reads the pinned secrets-helper image out of the helper script,
        # the way the offline bundle reads the redis pin out of the compose file.
        deploy = self.root / "deploy"
        deploy.mkdir()
        shutil.copy(REPO / "deploy" / "load-secrets.sh", deploy / "load-secrets.sh")
        self.backup_root = tmp_path / "backups"
        self.backup_root.mkdir(mode=0o700)
        self.docker_root = tmp_path / "docker-root"
        self.docker_root.mkdir()
        self.state_dir = self.root / ".qradar-dash"
        self.state_dir.mkdir()
        (self.state_dir / "state.json").write_text(
            '{"installed_version":"0.1.0","last_upgrade":null}\n'
        )
        self._write_env()

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
        self._stub("sleep", SLEEP_STUB)
        self._stub("id", ID_STUB)
        self._stub("systemctl", SYSTEMCTL_STUB)

        self.calls = tmp_path / "docker.calls"
        self.systemctl_calls = tmp_path / "systemctl.calls"
        self.env = {
            "HOME": str(tmp_path),
            "PATH": str(self.bin_dir),
            "STUB_DOCKER_CALLS": str(self.calls),
            "STUB_DOCKER_ROOT": str(self.docker_root),
            "STUB_DB_SIZE": str(GIB),
            "STUB_BACKUP_FREE_KIB": str(10 * GIB_KIB),
            "STUB_DOCKER_FREE_KIB": str(10 * GIB_KIB),
            "STUB_BACKUP_DEVICE": "1",
            "STUB_DOCKER_DEVICE": "1",
            "STUB_REAL_STAT": str(shutil.which("stat")),
            "STUB_SQL_FAILED_ONCE": str(tmp_path / "sql-failed-once"),
            "STUB_SYSTEMCTL_CALLS": str(self.systemctl_calls),
        }

    def _write_env(self, *, key: str = KEY, keys: str = "") -> None:
        (self.root / ".env").write_text(
            "COMPOSE_PROJECT_NAME=fixture-project\n"
            "POSTGRES_USER=qradar_obs\n"
            "POSTGRES_DB=qradar_obs\n"
            f"ENCRYPTION_KEY={key}\n"
            f"ENCRYPTION_KEYS_JSON={keys}\n"
            "QRADAR_DASH_VERSION=0.1.0\n"
            f"QRADAR_DASH_BACKUP_DIR={self.backup_root}\n"
            "SECRETS_VOLUME=fixture-secrets\n"
            "PROXY_CERTS_VOLUME=fixture-proxy\n"
            "API_LOOPBACK_PORT=8000\n"
        )

    def _stub(self, name: str, content: str) -> None:
        path = self.bin_dir / name
        path.write_text(content)
        path.chmod(0o755)

    def run(
        self,
        *args: str,
        stdin: str | None = None,
        **overrides: str,
    ) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.script), *args],
            cwd=self.root,
            env={**self.env, **overrides},
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def source(self, command: str, **overrides: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, "-c", f'QRADAR_DASH_SOURCE_ONLY=1 source "$1"; {command}', "bash", self.script],
            cwd=self.root,
            env={**self.env, **overrides},
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def backups(self) -> list[Path]:
        return sorted(self.backup_root.glob("qradar-dash-*"))

    def partials(self) -> list[Path]:
        return sorted(self.backup_root.glob(".*.partial"))

    def make_backup(
        self,
        *,
        version: str = "0.1.0",
        key: str = KEY,
        missing: str | None = None,
        state_json: str = "included",
    ) -> Path:
        path = self.backup_root / f"qradar-dash-{STAMP}-manual"
        path.mkdir(mode=0o700)
        files = {
            "database.dump": "dump\n",
            "env.bak": f"ENCRYPTION_KEY={key}\n",
            "secrets.tar": "secrets\n",
            "MANIFEST": (
                f"VERSION={version}\nIMAGE_DIGESTS=backend:sha256:x\n"
                f"ALEMBIC_VERSION=0025\nDATABASE_SIZE_BYTES={GIB}\nHOSTNAME=fixture\n"
                f"STATE_JSON={state_json}\nSECRETS_VOLUMES=qradar,proxy\n"
            ),
        }
        if state_json == "included":
            files["state.json"] = "{}\n"
        for name, content in files.items():
            if name == missing:
                continue
            target = path / name
            target.write_text(content)
            target.chmod(0o600)
        return path


@pytest.fixture
def backup(tmp_path: Path) -> Iterator[BackupFixture]:
    fixture = BackupFixture(tmp_path)
    yield fixture
    calls = fixture.calls.read_text() if fixture.calls.exists() else ""
    sql = "\n".join(
        line.removeprefix("SQL:")
        for line in calls.splitlines()
        if line.startswith("SQL:")
    )
    assert "DROP DATABASE qradar_obs;" not in sql
    assert "DROP DATABASE IF EXISTS qradar_obs;" not in sql


def output(result: subprocess.CompletedProcess[str]) -> str:
    return result.stdout + result.stderr


def test_disk_guard_refuses_before_directory_or_dump(backup: BackupFixture) -> None:
    result = backup.run("backup", STUB_BACKUP_FREE_KIB=str(5 * GIB_KIB - 1))

    assert result.returncode != 0
    assert "5.0 GiB required" in output(result)
    assert backup.backups() == []
    assert backup.partials() == []
    assert "pg_dump" not in backup.calls.read_text()


def test_disk_guard_passes_at_exact_threshold(backup: BackupFixture) -> None:
    result = backup.run("backup", STUB_BACKUP_FREE_KIB=str(5 * GIB_KIB))

    assert result.returncode == 0, output(result)
    assert "share one filesystem" in result.stdout
    assert "5.0 GiB free; 5.0 GiB required" in result.stdout
    assert len(backup.backups()) == 1
    assert "pg_dump" in backup.calls.read_text()


def test_docker_root_guard_refuses_before_directory_or_dump(
    backup: BackupFixture,
) -> None:
    result = backup.run(
        "backup",
        STUB_BACKUP_DEVICE="1",
        STUB_DOCKER_DEVICE="2",
        STUB_BACKUP_FREE_KIB=str(20 * GIB_KIB),
        STUB_DOCKER_FREE_KIB=str(4 * GIB_KIB - 1),
    )

    assert result.returncode != 0
    assert str(backup.docker_root) in output(result)
    assert "4.0 GiB required" in output(result)
    assert backup.backups() == []
    assert backup.partials() == []
    assert "pg_dump" not in backup.calls.read_text()


def test_successful_backup_is_private_complete_and_verified(backup: BackupFixture) -> None:
    result = backup.run("backup")

    assert result.returncode == 0, output(result)
    [path] = backup.backups()
    assert stat.S_IMODE(path.stat().st_mode) == 0o700
    expected = {"database.dump", "env.bak", "secrets.tar", "state.json", "MANIFEST"}
    assert {item.name for item in path.iterdir()} == expected
    assert all(stat.S_IMODE((path / name).stat().st_mode) == 0o600 for name in expected)
    manifest = (path / "MANIFEST").read_text().splitlines()
    assert [line.split("=", 1)[0] for line in manifest] == [
        "VERSION",
        "IMAGE_DIGESTS",
        "ALEMBIC_VERSION",
        "DATABASE_SIZE_BYTES",
        "HOSTNAME",
        "STATE_JSON",
        "SECRETS_VOLUMES",
    ]
    assert "STATE_JSON=included" in manifest
    assert "SECRETS_VOLUMES=qradar,proxy" in manifest
    assert backup.partials() == []
    calls = backup.calls.read_text()
    assert "CREATE DATABASE qradar_obs_restore_test" in calls
    assert "DROP DATABASE IF EXISTS qradar_obs_restore_test" in calls
    assert calls.index("volume inspect fixture-secrets") < calls.index(
        "-v fixture-secrets:"
    )
    assert calls.index("volume inspect fixture-proxy") < calls.index("-v fixture-proxy:")


def test_backup_without_state_json_is_restorable(backup: BackupFixture) -> None:
    (backup.state_dir / "state.json").unlink()
    created = backup.run("backup")

    assert created.returncode == 0, output(created)
    [path] = backup.backups()
    assert not (path / "state.json").exists()
    assert "STATE_JSON=absent" in (path / "MANIFEST").read_text()

    restored = backup.run("restore", "--non-interactive", "--yes", str(path))

    assert restored.returncode == 0, output(restored)
    assert " stop backend" in backup.calls.read_text()


def test_manifest_included_state_missing_refuses_before_stop(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup(missing="state.json")

    result = backup.run("restore", "--non-interactive", "--yes", str(path))

    assert result.returncode != 0
    assert "incomplete" in result.stderr
    assert " stop " not in (backup.calls.read_text() if backup.calls.exists() else "")


def test_present_state_json_requires_private_regular_file(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup(state_json="absent")
    state_file = path / "state.json"
    state_file.write_text("{}\n")
    state_file.chmod(0o644)

    result = backup.run("restore", "--non-interactive", "--yes", str(path))

    assert result.returncode != 0
    assert "incomplete" in result.stderr
    assert " stop " not in (backup.calls.read_text() if backup.calls.exists() else "")


def test_manifest_without_state_declaration_refuses_before_stop(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()
    manifest = path / "MANIFEST"
    manifest.write_text(
        "\n".join(
            line for line in manifest.read_text().splitlines() if not line.startswith("STATE_JSON=")
        )
        + "\n"
    )

    result = backup.run("restore", "--non-interactive", "--yes", str(path))

    assert result.returncode != 0
    assert "incomplete" in result.stderr
    assert " stop " not in (backup.calls.read_text() if backup.calls.exists() else "")


def test_missing_qradar_volume_fails_without_mounting_it(backup: BackupFixture) -> None:
    result = backup.run("backup", STUB_QRADAR_VOLUME_RC="1")

    assert result.returncode != 0
    assert "required QRadar secrets volume is missing" in result.stderr
    assert backup.backups() == []
    assert backup.partials() == []
    calls = backup.calls.read_text()
    assert "volume inspect fixture-secrets" in calls
    assert "-v fixture-secrets:" not in calls


def test_missing_proxy_volume_is_not_mounted_or_claimed(backup: BackupFixture) -> None:
    result = backup.run("backup", STUB_PROXY_VOLUME_RC="1")

    assert result.returncode == 0, output(result)
    [path] = backup.backups()
    assert "SECRETS_VOLUMES=qradar\n" in (path / "MANIFEST").read_text()
    calls = backup.calls.read_text()
    assert "volume inspect fixture-proxy" in calls
    assert "-v fixture-proxy:" not in calls
    assert calls.index("volume inspect fixture-secrets") < calls.index("-v fixture-secrets:")


def _secrets_helper_image() -> str:
    """The helper constant as written in deploy/load-secrets.sh (exactly once)."""
    constants = re.findall(
        r"^SECRETS_HELPER_IMAGE=(\S+)\s*$",
        (REPO / "deploy" / "load-secrets.sh").read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert len(constants) == 1
    return constants[0]


def test_secrets_backup_run_is_pinned_offline_capability_free_and_read_only(
    backup: BackupFixture,
) -> None:
    result = backup.run("backup")

    assert result.returncode == 0, output(result)
    image = _secrets_helper_image()
    [run] = [
        line for line in backup.calls.read_text().splitlines() if line.startswith("run ")
    ]
    assert run == (
        "run --rm --network none --cap-drop ALL --cap-add DAC_READ_SEARCH "
        "--security-opt no-new-privileges "
        "-v fixture-secrets:/volumes/qradar:ro -v fixture-proxy:/volumes/proxy:ro "
        f"{image} tar -C /volumes -cf - qradar proxy"
    )


def test_backup_without_a_pinned_helper_constant_refuses_before_mounting(
    backup: BackupFixture,
) -> None:
    (backup.root / "deploy" / "load-secrets.sh").write_text("#!/usr/bin/env bash\nexit 0\n")

    result = backup.run("backup")

    assert result.returncode != 0
    assert "no SECRETS_HELPER_IMAGE pinned by digest" in result.stderr
    assert backup.backups() == []
    assert backup.partials() == []
    calls = backup.calls.read_text() if backup.calls.exists() else ""
    assert "-v fixture-secrets:" not in calls
    assert "volume inspect fixture-secrets" not in calls


def test_manifest_uses_created_non_oneoff_container_image_ids(
    backup: BackupFixture,
) -> None:
    result = backup.run(
        "backup",
        STUB_CONTAINER_IDS=(
            "postgres-container migrate-container backend-container frontend-container"
        ),
    )

    assert result.returncode == 0, output(result)
    [path] = backup.backups()
    images = next(
        line for line in (path / "MANIFEST").read_text().splitlines()
        if line.startswith("IMAGE_DIGESTS=")
    )
    assert f"postgres:sha256:{1:064d}" in images
    assert f"backend:sha256:{2:064d}" in images
    assert "migrate:" not in images
    assert "config --services" not in backup.calls.read_text()


def test_service_list_entry_without_container_is_omitted(
    backup: BackupFixture,
) -> None:
    result = backup.run(
        "backup",
        STUB_CONTAINER_IDS="postgres-container backend-container frontend-container",
    )

    assert result.returncode == 0, output(result)
    [path] = backup.backups()
    manifest = (path / "MANIFEST").read_text()
    assert "migrate:" not in manifest
    assert "postgres:sha256:" in manifest


def test_missing_postgres_container_removes_partial(backup: BackupFixture) -> None:
    result = backup.run("backup", STUB_CONTAINER_IDS="backend-container frontend-container")

    assert result.returncode != 0
    assert "backup manifest failed" in result.stderr
    assert backup.backups() == []
    assert backup.partials() == []


@pytest.mark.parametrize(
    ("overrides", "needle"),
    [
        ({"STUB_DUMP_RC": "17"}, "dump failed"),
        ({"STUB_COPY_OUT_RC": "17"}, "copy-out failed"),
        ({"STUB_SECRETS_RC": "17"}, "secrets backup failed"),
        ({"STUB_RESTORE_ALEMBIC": "wrong"}, "verification failed"),
    ],
)
def test_backup_failures_remove_partial_and_drop_created_scratch(
    backup: BackupFixture, overrides: dict[str, str], needle: str
) -> None:
    result = backup.run("backup", **overrides)

    assert result.returncode != 0
    assert needle in output(result)
    assert backup.backups() == []
    assert backup.partials() == []
    calls = backup.calls.read_text()
    if "STUB_RESTORE_ALEMBIC" in overrides:
        assert calls.count("DROP DATABASE IF EXISTS qradar_obs_restore_test") >= 2


def test_no_verify_skips_scratch_database(backup: BackupFixture) -> None:
    result = backup.run("backup", "--no-verify")

    assert result.returncode == 0, output(result)
    calls = backup.calls.read_text()
    assert "qradar_obs_restore_test" not in calls


def _retention_directory(root: Path, name: str, state: str = "{}\n") -> Path:
    path = root / name
    path.mkdir()
    (path / "state.json").write_text(state)
    return path


def test_retention_keeps_seven_and_only_deletes_managed_directories(
    backup: BackupFixture,
) -> None:
    ordinary = [
        _retention_directory(
            backup.backup_root,
            f"qradar-dash-2026010{day}T000000Z-{'manual' if day % 2 else 'scheduled'}",
        )
        for day in range(1, 9)
    ]
    old_pre = _retention_directory(
        backup.backup_root, "qradar-dash-20260101T010000Z-pre-upgrade"
    )
    new_pre = _retention_directory(
        backup.backup_root, "qradar-dash-20260301T010000Z-pre-upgrade"
    )
    unrelated_dir = backup.backup_root / "qradar-dash-not-managed"
    unrelated_dir.mkdir()
    unrelated_file = backup.backup_root / "notes.txt"
    unrelated_file.write_text("keep\n")
    (backup.state_dir / "state.json").write_text(
        '{"upgrade_history":['
        '{"at":"2026-02-01T00:00:00Z","outcome":"success"},'
        '{"at":"2026-04-01T00:00:00Z","outcome":"success"}]}'
    )

    result = backup.source(
        f'apply_backup_retention "{backup.backup_root}" {GIB}'
    )

    assert result.returncode == 0, output(result)
    assert not ordinary[0].exists()
    assert all(path.exists() for path in ordinary[1:])
    assert not old_pre.exists()
    assert new_pre.exists()
    assert unrelated_dir.exists()
    assert unrelated_file.exists()


def test_missing_state_keeps_pre_upgrade_backups(backup: BackupFixture) -> None:
    pre = _retention_directory(
        backup.backup_root, "qradar-dash-20260101T010000Z-pre-upgrade"
    )
    (backup.state_dir / "state.json").unlink()

    result = backup.source(
        f'apply_backup_retention "{backup.backup_root}" {GIB}'
    )

    assert result.returncode == 0, output(result)
    assert pre.exists()


@pytest.mark.parametrize("outcome", ["failed", "rolled_back"])
def test_failed_or_rolled_back_upgrade_backup_is_protected(
    backup: BackupFixture, outcome: str
) -> None:
    protected = _retention_directory(
        backup.backup_root, "qradar-dash-20260101T010000Z-pre-upgrade"
    )
    (backup.state_dir / "state.json").write_text(
        '{"upgrade_history":['
        '{"at":"2026-02-01T00:00:00Z","outcome":"success"},'
        '{"at":"2026-03-01T00:00:00Z","outcome":"success"}],'
        f'"last_upgrade":{{"outcome":"{outcome}","backup":"{protected}"}}}}'
    )

    result = backup.source(
        f'apply_backup_retention "{backup.backup_root}" {GIB}'
    )

    assert result.returncode == 0, output(result)
    assert protected.exists()


def test_retention_names_backup_kept_by_disk_guard(backup: BackupFixture) -> None:
    ordinary = [
        _retention_directory(
            backup.backup_root, f"qradar-dash-2026020{day}T000000Z-manual"
        )
        for day in range(1, 9)
    ]

    result = backup.source(
        f'apply_backup_retention "{backup.backup_root}" {GIB}',
        STUB_BACKUP_FREE_KIB=str(4 * GIB_KIB - 1),
    )

    assert result.returncode != 0
    assert ordinary[0].exists()
    assert str(ordinary[0]) in result.stderr
    assert "retention kept backup" in result.stderr


def test_drop_guard_directly_refuses_postgres_db(backup: BackupFixture) -> None:
    result = backup.source('guard_drop_database "$(env_get POSTGRES_DB)"')

    assert result.returncode != 0
    assert "refusing to drop" in result.stderr
    assert not backup.calls.exists()


@pytest.mark.parametrize(
    ("args", "stdin", "missing", "version", "key", "needle"),
    [
        (("restore",), "not-restore\n", None, "0.1.0", KEY, "confirmation"),
        (("restore", "--non-interactive"), None, None, "0.1.0", KEY, "requires --yes"),
        (("restore", "--non-interactive", "--yes"), None, "secrets.tar", "0.1.0", KEY,
         "incomplete"),
        (("restore", "--non-interactive", "--yes"), None, None, "9.0.0", KEY, "newer"),
        (("restore", "--non-interactive", "--yes"), None, None, "0.0.9", KEY, "older"),
        (("restore", "--non-interactive", "--yes"), None, None, "0.1.0", OLD_KEY,
         "keyring"),
    ],
)
def test_restore_refusals_happen_before_stop_and_do_not_print_keys(
    backup: BackupFixture,
    args: tuple[str, ...],
    stdin: str | None,
    missing: str | None,
    version: str,
    key: str,
    needle: str,
) -> None:
    path = backup.make_backup(missing=missing, version=version, key=key)
    result = backup.run(*args, str(path), stdin=stdin)

    assert result.returncode != 0
    assert needle in output(result)
    calls = backup.calls.read_text() if backup.calls.exists() else ""
    assert " stop " not in calls
    assert KEY not in output(result)
    assert OLD_KEY not in output(result)


def test_restore_prompt_ends_its_line_when_input_is_piped(backup: BackupFixture) -> None:
    path = backup.make_backup()

    result = backup.run("restore", str(path), stdin="restore\n")

    assert result.returncode == 0, output(result)
    prompt_line = next(
        line
        for line in result.stdout.splitlines()
        if "Type restore to replace the application database:" in line
    )
    assert prompt_line == "Type restore to replace the application database: "


def test_restore_prompt_ends_its_line_when_input_is_closed(backup: BackupFixture) -> None:
    path = backup.make_backup()

    result = backup.run("restore", str(path), stdin="")  # an empty pipe: end of input

    assert result.returncode != 0
    assert "Type restore to replace the application database: \n" in result.stdout
    calls = backup.calls.read_text() if backup.calls.exists() else ""
    assert " stop " not in calls


def test_restore_docker_root_guard_refuses_before_stop(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.run(
        "restore",
        "--non-interactive",
        "--yes",
        str(path),
        STUB_BACKUP_DEVICE="1",
        STUB_DOCKER_DEVICE="2",
        STUB_BACKUP_FREE_KIB=str(20 * GIB_KIB),
        STUB_DOCKER_FREE_KIB=str(4 * GIB_KIB - 1),
    )

    assert result.returncode != 0
    assert str(backup.docker_root) in output(result)
    assert "4.0 GiB required" in output(result)
    assert " stop " not in backup.calls.read_text()


def test_restore_accepts_a_key_in_the_current_keyring(backup: BackupFixture) -> None:
    path = backup.make_backup(key=OLD_KEY)
    backup._write_env(key=KEY, keys=f'{{"2":"{OLD_KEY}"}}')

    result = backup.run("restore", "--non-interactive", "--yes", str(path))

    assert result.returncode == 0, output(result)


def test_restore_success_records_safe_order_and_keeps_broken_copy(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.run("restore", "--non-interactive", "--yes", str(path))

    assert result.returncode == 0, output(result)
    calls = backup.calls.read_text()
    ordered = [
        " stop backend celery-worker celery-beat frontend proxy",
        f"CREATE DATABASE qradar_obs_restore_{DB_STAMP}",
        "pg_restore",
        "SELECT version_num FROM alembic_version",
        "SELECT pg_terminate_backend",
        f"ALTER DATABASE qradar_obs RENAME TO qradar_obs_broken_{DB_STAMP}",
        f"ALTER DATABASE qradar_obs_restore_{DB_STAMP} RENAME TO qradar_obs",
        " start backend celery-worker celery-beat frontend proxy",
    ]
    positions = [calls.index(item) for item in ordered]
    assert positions == sorted(positions)
    assert f"DROP DATABASE IF EXISTS qradar_obs_broken_{DB_STAMP}" not in calls
    assert f"Previous database kept as qradar_obs_broken_{DB_STAMP}" in result.stdout
    assert "Secrets were not restored" in result.stdout


def test_restore_swap_database_success_leaves_services_stopped(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.source(
        f'need_installed; restore_swap_database "{path}" rollback'
    )

    assert result.returncode == 0, output(result)
    calls = backup.calls.read_text()
    assert f"ALTER DATABASE qradar_obs_restore_{DB_STAMP} RENAME TO qradar_obs" in calls
    assert " start " not in calls
    assert "services remain stopped" in result.stdout


def test_readiness_failure_still_prints_broken_database_and_drop_command(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.run(
        "restore",
        "--non-interactive",
        "--yes",
        str(path),
        STUB_CURL_RC="22",
    )

    assert result.returncode != 0
    assert f"Previous database kept as qradar_obs_broken_{DB_STAMP}" in result.stdout
    assert f"DROP DATABASE qradar_obs_broken_{DB_STAMP};" in result.stdout
    assert "readiness did not answer" in result.stderr


def test_restore_verify_failure_drops_restore_db_and_restarts(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.run(
        "restore",
        "--non-interactive",
        "--yes",
        str(path),
        STUB_RESTORE_ALEMBIC="wrong",
    )

    assert result.returncode != 0
    calls = backup.calls.read_text()
    assert f"DROP DATABASE IF EXISTS qradar_obs_restore_{DB_STAMP}" in calls
    assert "ALTER DATABASE qradar_obs RENAME" not in calls
    assert " start backend celery-worker celery-beat frontend proxy" in calls


def test_restore_step_five_failure_renames_original_back(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.run(
        "restore",
        "--non-interactive",
        "--yes",
        str(path),
        STUB_SQL_FAIL_MATCH=(
            f"ALTER DATABASE qradar_obs_restore_{DB_STAMP} RENAME TO qradar_obs"
        ),
        STUB_SQL_FAIL_ONCE="1",
    )

    assert result.returncode != 0
    calls = backup.calls.read_text()
    failed = f"ALTER DATABASE qradar_obs_restore_{DB_STAMP} RENAME TO qradar_obs"
    rename_back = f"ALTER DATABASE qradar_obs_broken_{DB_STAMP} RENAME TO qradar_obs"
    assert failed in calls
    assert rename_back in calls
    assert calls.index(failed) < calls.index(rename_back)
    assert " start backend celery-worker celery-beat frontend proxy" in calls


def test_restore_rename_back_failure_prints_both_names_and_recovery_command(
    backup: BackupFixture,
) -> None:
    path = backup.make_backup()

    result = backup.run(
        "restore",
        "--non-interactive",
        "--yes",
        str(path),
        STUB_SQL_FAIL_MATCH=(
            f"ALTER DATABASE qradar_obs_restore_{DB_STAMP} RENAME TO qradar_obs"
        ),
        STUB_RENAME_BACK_RC="23",
    )

    assert result.returncode != 0
    combined = output(result)
    assert f"Original database: qradar_obs_broken_{DB_STAMP}" in combined
    assert f"Restored candidate database: qradar_obs_restore_{DB_STAMP}" in combined
    assert (
        f"ALTER DATABASE qradar_obs_broken_{DB_STAMP} RENAME TO qradar_obs;"
        in combined
    )
    assert " start backend celery-worker celery-beat frontend proxy" in backup.calls.read_text()


def test_recorded_sql_never_drops_postgres_db(backup: BackupFixture) -> None:
    result = backup.run("backup")

    assert result.returncode == 0, output(result)
    sql = "\n".join(
        line.removeprefix("SQL:")
        for line in backup.calls.read_text().splitlines()
        if line.startswith("SQL:")
    )
    assert "DROP DATABASE qradar_obs;" not in sql
    assert "DROP DATABASE IF EXISTS qradar_obs;" not in sql


def test_print_timer_writes_nothing_and_uses_absolute_execstart(
    backup: BackupFixture,
) -> None:
    before = sorted(path.name for path in backup.root.iterdir())

    result = backup.run("backup", "--print-timer")

    assert result.returncode == 0, output(result)
    assert "# qradar-dash-backup.service" in result.stdout
    assert "# qradar-dash-backup.timer" in result.stdout
    assert "OnCalendar=daily" in result.stdout
    assert "Persistent=true" in result.stdout
    assert "RandomizedDelaySec=15min" in result.stdout
    assert f"ExecStart={backup.script} backup scheduled" in result.stdout
    assert sorted(path.name for path in backup.root.iterdir()) == before
    assert not backup.calls.exists()


def test_user_timer_install_explains_session_and_lingering(
    backup: BackupFixture,
) -> None:
    result = backup.run("backup", "--install-timer")

    assert result.returncode == 0, output(result)
    unit_dir = backup.root.parent / ".config" / "systemd" / "user"
    assert (unit_dir / "qradar-dash-backup.service").is_file()
    assert (unit_dir / "qradar-dash-backup.timer").is_file()
    assert "only while that user has a session" in result.stdout
    assert "loginctl enable-linger fixture-operator" in result.stdout
    assert backup.systemctl_calls.read_text().splitlines() == [
        "--user daemon-reload",
        "--user enable --now qradar-dash-backup.timer",
    ]

