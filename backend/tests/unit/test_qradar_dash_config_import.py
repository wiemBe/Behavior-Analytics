"""Slice 1j: the config-import wrapper (D-78, D-79).

The real script runs with a fake docker on a private PATH, like the other qradar-dash tests.
Paths come from this file or ``tmp_path``; no Docker daemon or database is needed.
"""

from __future__ import annotations

import os
import pty
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
TOOLS = ("basename", "cat", "chmod", "dirname", "head", "id", "mktemp",
         "mv", "readlink", "rm", "sed", "tail", "tr")
JSON = """\
{
  "schema_version": 1,
  "mode": "dry-run",
  "committed": false,
  "instance": "lab",
  "sections": {
    "log_source_settings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "classification_overrides": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "classification_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "analytics_rule_settings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "technique_mappings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "waf_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "ips_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "identity_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "property_watches": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "alert_policies": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "outage_alert_policies": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "alert_suppressions": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "notification_destinations": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "notification_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "email_templates": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "email_recipients": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "ldap_config": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "search_cadence": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "scheduled_searches": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "custom_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "dashboards": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "dashboard_prefs": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    }
  }
}
"""
DOCKER_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$STUB_CALLS"
case "$*" in
  *" exec -T backend python -m app.cli.config import --dry-run"*)
    _dry_stdin="${STUB_DRY_STDIN:-${STUB_STDIN:-}}"
    [ -z "$_dry_stdin" ] || cat > "$_dry_stdin"
    [ -z "${STUB_ERR:-}" ] || printf '%s' "$STUB_ERR" >&2
    printf '%s' "${STUB_JSON:-}"
    [ -z "${STUB_SIGNAL:-}" ] || { kill -s "$STUB_SIGNAL" "$PPID"; sleep 5; }
    exit "${STUB_DRY_RC:-${STUB_RC:-0}}" ;;
  *" exec -T backend python -m app.cli.config import --apply"*)
    _apply_stdin="${STUB_APPLY_STDIN:-${STUB_STDIN:-}}"
    [ -z "$_apply_stdin" ] || cat > "$_apply_stdin"
    [ -z "${STUB_ERR:-}" ] || printf '%s' "$STUB_ERR" >&2
    printf '%s' "${STUB_JSON:-}"
    exit "${STUB_APPLY_RC:-${STUB_RC:-0}}" ;;
  *) exit 97 ;;
esac
"""


class Import:
    """A checkout with a saved .env, an operator directory and a fake docker on PATH."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "checkout"
        self.root.mkdir()
        self.script = self.root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        (self.root / ".env").write_text("COMPOSE_PROJECT_NAME=fixture-project\\n")
        self.cwd = tmp / "operator"
        self.cwd.mkdir()
        self.bin = tmp / "bin"
        self.bin.mkdir()
        for tool in TOOLS:
            executable = shutil.which(tool)
            assert executable is not None
            (self.bin / tool).symlink_to(executable)
        self.calls = tmp / "docker.calls"
        self.stub("docker", DOCKER_STUB)
        self.env = {
            "HOME": str(tmp),
            "PATH": str(self.bin),
            "STUB_CALLS": str(self.calls),
            "STUB_JSON": JSON,
        }

    def stub(self, name: str, body: str) -> None:
        path = self.bin / name
        path.write_text(body)
        path.chmod(0o755)

    def run(self, *args: str, **over: str) -> subprocess.CompletedProcess[str]:
        bash = shutil.which("bash") or "/bin/bash"
        return subprocess.run(  # noqa: S603
            [bash, str(self.script), "config-import", *args], cwd=self.cwd,
            env={**self.env, **over}, capture_output=True, text=True,
            timeout=20, check=False, stdin=subprocess.DEVNULL,
        )

    def tty(self, *args: str, replies: str = "", **over: str) -> subprocess.CompletedProcess[str]:
        master, slave = pty.openpty()
        bash = shutil.which("bash") or "/bin/bash"
        try:
            proc = subprocess.Popen(  # noqa: S603
                [bash, str(self.script), "config-import", *args],
                cwd=self.cwd,
                env={**self.env, **over},
                stdin=slave,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            os.close(slave)
            if replies:
                os.write(master, replies.encode())
            out, err = proc.communicate(timeout=20)
        finally:
            os.close(master)
        return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


@pytest.fixture
def fx(tmp_path: Path) -> Import:
    return Import(tmp_path)


def lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.strip()]


def test_dry_run_passes_stdin_and_propagates_zero(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    stdin_record = tmp_path / "stdin.bin"
    result = fx.run("--dry-run", str(source), STUB_STDIN=str(stdin_record))

    assert result.returncode == 0, result.stdout + result.stderr
    assert stdin_record.read_text() == '{"x":1}'
    assert "import --dry-run" in fx.calls.read_text()


def test_dry_run_propagates_exit_two(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--dry-run", str(source), STUB_RC="2")

    assert result.returncode == 2


def test_non_tty_apply_refused_without_yes(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--apply", str(source))

    assert result.returncode != 0
    assert "requires --yes" in result.stderr
    assert len(lines(result)) == 1
    assert not fx.calls.exists()


def test_non_tty_apply_with_noninteractive_yes_runs_apply(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--apply", str(source), "--non-interactive", "--yes")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "import --apply" in fx.calls.read_text()


def test_tty_apply_right_name_runs_apply(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.tty("--apply", str(source), replies="lab\n")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = fx.calls.read_text().splitlines()
    assert any("import --dry-run" in c for c in calls)
    assert any("import --apply" in c for c in calls)


def test_tty_apply_wrong_name_exits_one_and_never_applies(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.tty("--apply", str(source), replies="nope\n")

    assert result.returncode == 1
    calls = fx.calls.read_text().splitlines() if fx.calls.exists() else []
    assert any("import --dry-run" in c for c in calls)
    assert not any("import --apply" in c for c in calls)


def test_docker_missing_exits_nonzero(fx: Import, tmp_path: Path) -> None:
    (fx.bin / "docker").unlink()
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--dry-run", str(source))

    assert result.returncode != 0
    assert "Docker is not installed" in result.stderr


def test_not_installed_exits_nonzero_before_docker(fx: Import, tmp_path: Path) -> None:
    (fx.root / ".env").unlink()
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--dry-run", str(source))

    assert result.returncode != 0
    assert "not installed here" in result.stderr
    assert not fx.calls.exists()


@pytest.mark.parametrize("args, needle", [
    (("--bogus",), "unknown config-import argument"),
    (("--dry-run",), "FILE is required"),
    (("--apply",), "FILE is required"),
    (("missing.json", "--dry-run", "--apply"), "choose exactly one"),
])
def test_bad_arguments_exit_two_without_docker(fx: Import, tmp_path: Path, args, needle) -> None:
    a = list(args)
    result = fx.run(*a)
    assert result.returncode == 2
    assert needle in result.stderr
    assert not fx.calls.exists()


def test_missing_file_refused(fx: Import) -> None:
    result = fx.run("--dry-run", "/no/such/file.json")
    assert result.returncode != 0
    assert "not readable" in result.stderr
    assert not fx.calls.exists()


def test_directory_refused(fx: Import, tmp_path: Path) -> None:
    d = tmp_path / "dir"
    d.mkdir()
    result = fx.run("--dry-run", str(d))
    assert result.returncode != 0
    assert "is a directory" in result.stderr
    assert not fx.calls.exists()


def test_both_modes_refused(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    result = fx.run(str(source), "--dry-run", "--apply")
    assert result.returncode == 2
    assert "choose exactly one" in result.stderr
    assert not fx.calls.exists()


def test_neither_mode_refused(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    result = fx.run(str(source))
    assert result.returncode == 2
    assert "one of --dry-run or --apply is required" in result.stderr
    assert not fx.calls.exists()


TRICKY_JSON = """\
{
  "schema_version": 1,
  "mode": "dry-run",
  "committed": false,
  "instance": "lab",
  "sections": {
    "log_source_settings": {
      "created": [{"key": "k", "reason": "r", "instance": "other"}],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "classification_overrides": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "classification_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "analytics_rule_settings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "technique_mappings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "waf_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "ips_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "identity_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "property_watches": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "alert_policies": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "outage_alert_policies": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "alert_suppressions": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "notification_destinations": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "notification_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "email_templates": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "email_recipients": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "ldap_config": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "search_cadence": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "scheduled_searches": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "custom_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "dashboards": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "dashboard_prefs": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    }
  }
}
"""

SECTIONS_LAB_JSON = """\
{
  "schema_version": 1,
  "mode": "dry-run",
  "committed": false,
  "instance": "sections-lab",
  "sections": {
    "log_source_settings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "classification_overrides": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "classification_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "analytics_rule_settings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "technique_mappings": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "waf_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "ips_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "identity_source_profiles": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "property_watches": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "alert_policies": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "outage_alert_policies": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "alert_suppressions": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "notification_destinations": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "notification_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "email_templates": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "email_recipients": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "ldap_config": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "search_cadence": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "scheduled_searches": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "custom_rules": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "dashboards": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    },
    "dashboard_prefs": {
      "created": [],
      "unchanged": [],
      "conflict": [],
      "unresolved": []
    }
  }
}
"""


def test_unreadable_file_refused(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    source.chmod(0o000)
    try:
        result = fx.run("--dry-run", str(source))
        assert result.returncode != 0
        assert "not readable" in result.stderr
        assert not fx.calls.exists()
    finally:
        source.chmod(0o644)


def test_tty_failed_dry_run_propagates_without_apply(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    result = fx.tty("--apply", str(source), replies="", STUB_RC="2")

    assert result.returncode == 2
    calls = fx.calls.read_text().splitlines() if fx.calls.exists() else []
    assert any("import --dry-run" in c for c in calls)
    assert not any("import --apply" in c for c in calls)


def test_tty_instance_name_lab_applies(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    result = fx.tty("--apply", str(source), replies="lab\n", STUB_JSON=TRICKY_JSON)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = fx.calls.read_text().splitlines()
    assert any("import --dry-run" in c for c in calls)
    assert any("import --apply" in c for c in calls)


def test_tty_instance_name_other_refuses(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    result = fx.tty("--apply", str(source), replies="other\n", STUB_JSON=TRICKY_JSON)

    assert result.returncode == 1
    calls = fx.calls.read_text().splitlines() if fx.calls.exists() else []
    assert any("import --dry-run" in c for c in calls)
    assert not any("import --apply" in c for c in calls)

def test_non_tty_apply_noninteractive_without_yes_refused(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--apply", str(source), "--non-interactive")

    assert result.returncode != 0
    assert "requires --yes" in result.stderr
    assert not fx.calls.exists()


def test_non_tty_apply_yes_without_noninteractive_refused(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--apply", str(source), "--yes")

    assert result.returncode != 0
    assert "requires --yes" in result.stderr
    assert not fx.calls.exists()


def test_non_tty_apply_receives_stdin_bytes(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    stdin_record = tmp_path / "stdin.bin"
    result = fx.run("--apply", str(source), "--non-interactive", "--yes",
                    STUB_STDIN=str(stdin_record))

    assert result.returncode == 0, result.stdout + result.stderr
    assert stdin_record.read_text() == '{"x":1}'
    assert "import --apply" in fx.calls.read_text()


def test_non_tty_apply_exit_code_propagated(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.run("--apply", str(source), "--non-interactive", "--yes",
                    STUB_APPLY_RC="2")

    assert result.returncode == 2


def test_tty_apply_exit_code_propagated(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.tty("--apply", str(source), replies="lab\n",
                    STUB_APPLY_RC="2")

    assert result.returncode == 2
    calls = fx.calls.read_text().splitlines() if fx.calls.exists() else []
    assert any("import --dry-run" in c for c in calls)
    assert any("import --apply" in c for c in calls)


def test_tty_dry_run_receives_stdin_bytes(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    stdin_record = tmp_path / "stdin.bin"
    result = fx.tty("--apply", str(source), replies="lab\n",
                    STUB_DRY_STDIN=str(stdin_record))

    assert result.returncode == 0, result.stdout + result.stderr
    assert stdin_record.read_text() == '{"x":1}'


def test_tty_noninteractive_yes_applies_without_prompt(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text('{"x":1}')
    result = fx.tty("--apply", str(source), "--non-interactive", "--yes", replies="")

    assert result.returncode == 0, result.stdout + result.stderr
    calls = fx.calls.read_text().splitlines() if fx.calls.exists() else []
    assert any("import --apply" in c for c in calls)
    assert "Type the instance name" not in result.stdout
    assert "Type the instance name" not in result.stderr


def test_tty_instance_name_sections_lab_applies(fx: Import, tmp_path: Path) -> None:
    source = tmp_path / "in.json"
    source.write_text("{}")
    result = fx.tty("--apply", str(source), replies="sections-lab\n",
                    STUB_JSON=SECTIONS_LAB_JSON)

    assert result.returncode == 0, result.stdout + result.stderr
    calls = fx.calls.read_text().splitlines()
    assert any("import --dry-run" in c for c in calls)
    assert any("import --apply" in c for c in calls)
