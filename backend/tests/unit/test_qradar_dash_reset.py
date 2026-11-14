"""`./qradar-dash reset` is run for real in a tmp_path site, with a fake `docker` on PATH.

The fake records every call and keeps its volumes as files, so the tests see which volumes a
command removed. The real Docker is never reached: PATH holds only the fake and symlinks to the
host tools the script needs.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"
HOST_TOOLS = (
    "bash", "sed", "head", "cat", "rm", "rmdir", "dirname", "readlink", "sort", "tr",
    "mkdir", "date", "grep", "install", "stat", "mv", "chmod", "printf", "basename",
)  # fmt: skip
PROJECT = "fixture-project"
SECRET_VALUE = "TOPSECRET-token-value-123"
LOCAL_VOLUMES = (f"{PROJECT}_postgres_data", f"{PROJECT}_redis_data", f"{PROJECT}_mcp_logs")
OTHER_VOLUME = "other-project_postgres_data"
EXTERNAL_VOLUMES = ("fixture-secrets", "fixture-proxy")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

DOCKER_STUB = r"""#!/usr/bin/env bash
printf '%s\n' "$*" >> "$STUB_CALLS"
case "$1" in
  compose)
    case " $* " in
      *" ps "*) printf 'fixture-backend-1\nfixture-postgres-1\n'; exit 0 ;;
      *" down "*)
        [ "${STUB_DOWN_RC:-0}" = 0 ] || { echo "down exploded" >&2; exit "$STUB_DOWN_RC"; }
        for path in "$STUB_VOLUMES"/fixture-project_*; do
          name=$(basename "$path")
          [ "$name" = "$STUB_KEEP_VOLUME" ] || rm -f "$STUB_VOLUMES/$name"
        done
        exit 0 ;;
    esac
    exit 0 ;;
  volume)
    case "$2" in
      ls)
        [ "${STUB_LS_RC:-0}" = 0 ] || exit "$STUB_LS_RC"
        case "$*" in
          *label=com.docker.compose.project=fixture-project*)
            for path in "$STUB_VOLUMES"/fixture-project_*; do
              [ -e "$path" ] && basename "$path"
            done ;;
        esac
        exit 0 ;;
      inspect) [ -e "$STUB_VOLUMES/$3" ] ;;
      rm)
        [ "${STUB_RM_FAIL:-}" != "$3" ] || exit 1
        rm -f "$STUB_VOLUMES/$3" ;;
    esac
    exit $? ;;
esac
exit 0
"""


class Site:
    def __init__(self, tmp_path: Path) -> None:
        self.tmp = tmp_path
        self.root = tmp_path / "checkout"
        self.root.mkdir()
        shutil.copy(SCRIPT, self.root / "qradar-dash")
        (self.root / "qradar-dash").chmod(0o755)
        (self.root / "VERSION").write_text("0.1.0\n")
        self.backup_root = tmp_path / "backups"
        self.backup_root.mkdir()
        (self.backup_root / "qradar-dash-keep-me").write_text("backup\n")
        (self.root / ".qradar-dash").mkdir()
        (self.root / ".qradar-dash" / "state.json").write_text('{"installed_version":"0.1.0"}\n')
        (self.root / ".secrets").mkdir()
        (self.root / ".secrets" / "install.answers").write_text("QRADAR_URL=https://pre\n")
        (self.root / ".secrets" / "qradar.sec").write_text(SECRET_VALUE + "\n")
        (self.root / ".env").write_text(
            f"COMPOSE_PROJECT_NAME={PROJECT}\n"
            "PUBLIC_ORIGIN=https://dash.example\n"
            "QRADAR_BASE_URL=https://user:pw@qradar-pre.example:443/api\n"
            f"ENCRYPTION_KEY={SECRET_VALUE}\n"
            f"QRADAR_DASH_BACKUP_DIR={self.backup_root}\n"
            "SECRETS_VOLUME=fixture-secrets\nPROXY_CERTS_VOLUME=fixture-proxy\n"
        )
        self.volumes = tmp_path / "volumes"
        self.volumes.mkdir()
        for name in (*LOCAL_VOLUMES, *EXTERNAL_VOLUMES, OTHER_VOLUME):
            (self.volumes / name).write_text("data\n")
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        for tool in HOST_TOOLS:
            found = shutil.which(tool)
            if found:
                (self.bin / tool).symlink_to(found)
        self.calls = tmp_path / "docker.calls"
        self.calls.write_text("")
        self.set_docker(True)

    def set_docker(self, present: bool) -> None:
        stub = self.bin / "docker"
        stub.unlink(missing_ok=True)
        if present:
            stub.write_text(DOCKER_STUB)
            stub.chmod(0o755)

    def _env(self, extra: dict[str, str]) -> dict[str, str]:
        return {
            "HOME": str(self.tmp),
            "PATH": str(self.bin),
            "STUB_CALLS": str(self.calls),
            "STUB_VOLUMES": str(self.volumes),
            "STUB_KEEP_VOLUME": "-none-",
            **extra,
        }

    def run(self, *args: str, stdin: str = "", **env: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.root / "qradar-dash"), "reset", *args],
            cwd=self.root,
            env=self._env(env),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def run_with_backup_stub(self, rc: int, *args: str) -> subprocess.CompletedProcess[str]:
        """Source the script and replace only cmd_backup_create, which the backup tests own."""
        bash = shutil.which("bash") or "/bin/bash"
        body = (
            "QRADAR_DASH_SOURCE_ONLY=1 source ./qradar-dash; "
            f'cmd_backup_create() {{ echo "backup stub $*" >> "$STUB_CALLS"; return {rc}; }}; '
            'cmd_reset "$@"'
        )
        return subprocess.run(  # noqa: S603
            [bash, "-c", body, "bash", *args],
            cwd=self.root,
            env=self._env({}),
            input="",
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )

    def call_lines(self) -> list[str]:
        return self.calls.read_text().splitlines()

    def writes(self) -> list[str]:
        return [c for c in self.call_lines() if " down " in c or c.startswith("volume rm")]

    def files_present(self) -> bool:
        return all(
            p.exists()
            for p in (
                self.root / ".env",
                self.root / ".qradar-dash" / "state.json",
                self.root / ".secrets" / "install.answers",
            )
        )

    def volume_present(self, name: str) -> bool:
        return (self.volumes / name).exists()


@pytest.fixture
def site(tmp_path: Path) -> Iterator[Site]:
    yield Site(tmp_path)


def text(r: subprocess.CompletedProcess[str]) -> str:
    return r.stdout + r.stderr


def assert_untouched(site: Site) -> None:
    assert site.files_present()
    assert site.writes() == []
    assert all(site.volume_present(v) for v in (*LOCAL_VOLUMES, *EXTERNAL_VOLUMES))
    assert site.volume_present(OTHER_VOLUME)


def test_not_installed_refuses(site: Site) -> None:
    (site.root / ".env").unlink()
    (site.root / ".qradar-dash" / "state.json").unlink()
    r = site.run("--non-interactive", "--yes")
    assert r.returncode == 1
    assert "nothing to reset" in r.stderr
    assert site.writes() == []


def test_docker_missing_refuses_and_changes_nothing(site: Site) -> None:
    site.set_docker(False)
    r = site.run("--non-interactive", "--yes")
    assert r.returncode == 1
    assert "Docker is not installed" in r.stderr
    assert site.files_present()


def test_dry_run_prints_plan_and_changes_nothing(site: Site) -> None:
    r = site.run("--dry-run")
    assert r.returncode == 0
    out = r.stdout
    assert PROJECT in out
    assert "https://dash.example" in out
    assert "qradar-pre.example:443" in out
    assert "user:pw" not in out
    for name in LOCAL_VOLUMES:
        assert name in out
    assert ".env" in out and ".qradar-dash/state.json" in out and "install.answers" in out
    assert str(site.backup_root) in out
    assert "fixture-secrets" in out  # listed as kept
    assert "Dry run: nothing was changed." in out
    assert_untouched(site)


def test_interactive_wrong_name_cancels(site: Site) -> None:
    r = site.run(stdin="yes\n")
    assert r.returncode == 1
    assert "reset cancelled; nothing was changed" in r.stderr
    assert_untouched(site)


def test_interactive_empty_input_cancels(site: Site) -> None:
    r = site.run(stdin="")
    assert r.returncode == 1
    assert_untouched(site)


def test_interactive_right_name_wipes(site: Site) -> None:
    r = site.run(stdin=f"{PROJECT}\n")
    assert r.returncode == 0, text(r)
    assert not (site.root / ".env").exists()
    assert not (site.root / ".qradar-dash" / "state.json").exists()
    assert not (site.root / ".secrets" / "install.answers").exists()
    assert (site.root / ".secrets" / "qradar.sec").exists()
    assert (site.backup_root / "qradar-dash-keep-me").exists()
    for name in LOCAL_VOLUMES:
        assert not site.volume_present(name)
    assert "./deploy/load-secrets.sh" in r.stdout and "./qradar-dash install" in r.stdout
    assert any(f"-p {PROJECT}" in c and "down -v --remove-orphans" in c for c in site.call_lines())


def test_non_interactive_requires_yes(site: Site) -> None:
    r = site.run("--non-interactive")
    assert r.returncode == 1
    assert "requires --yes" in r.stderr
    assert_untouched(site)


def test_non_interactive_yes_wipes_and_keeps_external_volumes(site: Site) -> None:
    r = site.run("--non-interactive", "--yes")
    assert r.returncode == 0, text(r)
    assert not site.files_present()
    for name in EXTERNAL_VOLUMES:
        assert site.volume_present(name)
    for name in EXTERNAL_VOLUMES:
        assert f"volume rm {name}" not in site.call_lines()
    assert not any("prune" in c for c in site.call_lines())


def test_include_secrets_removes_the_external_volumes(site: Site) -> None:
    r = site.run("--non-interactive", "--yes", "--include-secrets")
    assert r.returncode == 0, text(r)
    for name in EXTERNAL_VOLUMES:
        assert not site.volume_present(name)
    assert "volume rm fixture-secrets" in site.call_lines()
    assert "volume rm fixture-proxy" in site.call_lines()


def test_backup_success_runs_before_the_wipe(site: Site) -> None:
    r = site.run_with_backup_stub(0, "--non-interactive", "--yes", "--backup")
    assert r.returncode == 0, text(r)
    lines = site.call_lines()
    first_backup = next(i for i, c in enumerate(lines) if c.startswith("backup stub"))
    first_down = next(i for i, c in enumerate(lines) if " down " in c)
    assert first_backup < first_down
    assert not site.files_present()


def test_backup_failure_removes_nothing(site: Site) -> None:
    r = site.run_with_backup_stub(1, "--non-interactive", "--yes", "--backup")
    assert r.returncode == 1
    assert "Nothing was removed" in r.stderr
    assert_untouched(site)


def test_no_backup_by_default(site: Site) -> None:
    r = site.run("--non-interactive", "--yes")
    assert r.returncode == 0, text(r)
    assert not any("pg_dump" in c for c in site.call_lines())
    assert "No backup is taken" in r.stdout


def test_volume_left_by_down_is_removed_by_name(site: Site) -> None:
    r = site.run("--non-interactive", "--yes", STUB_KEEP_VOLUME=LOCAL_VOLUMES[1])
    assert r.returncode == 0, text(r)
    assert f"volume rm {LOCAL_VOLUMES[1]}" in site.call_lines()
    assert not site.volume_present(LOCAL_VOLUMES[1])
    assert not site.files_present()


def test_volume_that_cannot_be_removed_stops_before_deleting_files(site: Site) -> None:
    r = site.run(
        "--non-interactive",
        "--yes",
        STUB_KEEP_VOLUME=LOCAL_VOLUMES[0],
        STUB_RM_FAIL=LOCAL_VOLUMES[0],
    )
    assert r.returncode == 1
    assert LOCAL_VOLUMES[0] in r.stderr
    assert "docker volume rm" in r.stderr
    assert site.files_present()


def test_failing_down_reports_partial_state(site: Site) -> None:
    r = site.run("--non-interactive", "--yes", STUB_DOWN_RC="3")
    assert r.returncode == 1
    assert "down -v" in text(r)
    assert "finish by hand" in text(r)
    assert site.files_present()
    assert all(site.volume_present(v) for v in (*LOCAL_VOLUMES, *EXTERNAL_VOLUMES))


def test_unknown_option_exits_two(site: Site) -> None:
    r = site.run("--nope")
    assert r.returncode == 2
    assert_untouched(site)


@pytest.mark.parametrize(
    "args",
    [
        ["--dry-run"],
        ["--non-interactive", "--yes"],
        ["--non-interactive", "--yes", "--include-secrets"],
        ["--non-interactive"],
        ["--dry-run", "--include-secrets"],
    ],
)
def test_no_secret_is_printed(site: Site, args: list[str]) -> None:
    r = site.run(*args)
    assert SECRET_VALUE not in text(r)
    assert "pw@" not in text(r)


def test_plan_lists_every_labelled_volume_and_not_other_projects(site: Site) -> None:
    r = site.run("--dry-run")
    assert r.returncode == 0
    for name in LOCAL_VOLUMES:
        assert name in r.stdout
    assert f"{PROJECT}_mcp_logs" in r.stdout
    assert OTHER_VOLUME not in r.stdout
    assert any("label=com.docker.compose.project=" + PROJECT in c for c in site.call_lines())


def test_other_projects_volume_is_never_removed(site: Site) -> None:
    r = site.run(
        "--non-interactive", "--yes", "--include-secrets", STUB_KEEP_VOLUME=LOCAL_VOLUMES[2]
    )
    assert r.returncode == 0, text(r)
    assert site.volume_present(OTHER_VOLUME)
    assert f"volume rm {OTHER_VOLUME}" not in site.call_lines()
    assert not site.volume_present(LOCAL_VOLUMES[2])  # the leftover check covers the third


def test_empty_volume_list_is_said_in_the_plan(site: Site) -> None:
    for name in LOCAL_VOLUMES:
        (site.volumes / name).unlink()
    r = site.run("--dry-run")
    assert r.returncode == 0
    assert "none found" in r.stdout


def test_volume_listing_failure_refuses(site: Site) -> None:
    r = site.run("--non-interactive", "--yes", STUB_LS_RC="1")
    assert r.returncode == 1
    assert "could not list the Docker volumes" in r.stderr
    assert_untouched(site)
