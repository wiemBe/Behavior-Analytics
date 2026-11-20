"""Slice 1f: the config-export wrapper writes a 0600 host file (D-78, D-79).

The real script runs with a fake docker on a private PATH, like the other qradar-dash tests.
Paths come from this file or ``tmp_path``; no Docker daemon or database is needed.
"""

from __future__ import annotations

import os
import pwd
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"
pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")
TOOLS = ("basename", "cat", "chmod", "dirname", "head", "id", "mktemp",
         "mv", "readlink", "rm", "sed", "tail", "tr")
JSON = '{"schema_version": 1, "sections": {}}\n'
CALL = "exec -T backend python -m app.cli.config export"
DOCKER_STUB = """#!/bin/bash
printf '%s\\n' "$*" >> "$STUB_CALLS"
case "$*" in
  *" exec -T backend python -m app.cli.config export"*)
    [ -z "${STUB_ERR:-}" ] || printf '%s' "$STUB_ERR" >&2
    printf '%s' "${STUB_JSON:-}"
    [ -z "${STUB_CHMOD_DIR:-}" ] || chmod 555 "$STUB_CHMOD_DIR"
    [ -z "${STUB_RM_TMP:-}" ] || rm -f "$STUB_RM_TMP"/.qradar-dash-config.??????
    [ -z "${STUB_SIGNAL:-}" ] || { kill -s "$STUB_SIGNAL" "$PPID"; sleep 5; }
    exit "${STUB_RC:-0}" ;;
  *) exit 97 ;;
esac
"""


class Export:
    """A checkout with a saved .env, an operator directory and a fake docker on PATH."""

    def __init__(self, tmp: Path) -> None:
        self.root = tmp / "checkout"
        self.root.mkdir()
        self.script = self.root / "qradar-dash"
        shutil.copy(SCRIPT, self.script)
        self.script.chmod(0o755)
        (self.root / ".env").write_text("COMPOSE_PROJECT_NAME=fixture-project\n")
        # The script cd's to the checkout root; a separate cwd pins ORIG_PWD-relative output.
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
            [bash, str(self.script), "config-export", *args], cwd=self.cwd,
            env={**self.env, **over}, capture_output=True, text=True,
            timeout=20, check=False, stdin=subprocess.DEVNULL,
        )


@pytest.fixture
def fx(tmp_path: Path) -> Export:
    return Export(tmp_path)


def lines(result: subprocess.CompletedProcess[str]) -> list[str]:
    return [line for line in result.stderr.splitlines() if line.strip()]


def test_success_writes_0600_file_equal_to_container_stdout(fx: Export) -> None:
    result = fx.run()

    assert result.returncode == 0, result.stdout + result.stderr
    output = fx.cwd / "qradar-dash-config.json"
    assert output.read_bytes() == JSON.encode()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(fx.cwd.glob(".qradar-dash-config-err.*"))


def test_output_flag_replaces_an_existing_file_and_ends_at_0600(fx: Export) -> None:
    output = fx.cwd / "named.json"
    output.write_text("OLD CONTENT")
    output.chmod(0o644)

    result = fx.run("--output", str(output))

    assert result.returncode == 0, result.stdout + result.stderr
    assert output.read_bytes() == JSON.encode()
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert not list(fx.cwd.glob(".qradar-dash-config-err.*"))


def test_label_and_instance_reach_the_in_container_command(fx: Export) -> None:
    result = fx.run("--label", "preprod", "--instance", "lab")

    assert result.returncode == 0, result.stdout + result.stderr
    actor = pwd.getpwuid(os.getuid()).pw_name
    assert f"{CALL} --actor {actor} --label preprod --instance lab" in fx.calls.read_text()


def test_docker_missing_exits_nonzero_with_one_clear_line(fx: Export) -> None:
    (fx.bin / "docker").unlink()

    result = fx.run()

    assert result.returncode != 0
    assert "Docker is not installed" in result.stderr
    assert len(lines(result)) == 1
    assert not (fx.cwd / "qradar-dash-config.json").exists()


def test_not_installed_exits_nonzero_before_docker_is_called(fx: Export) -> None:
    (fx.root / ".env").unlink()

    result = fx.run()

    assert result.returncode != 0
    assert "not installed here" in result.stderr
    assert len(lines(result)) == 1
    assert not fx.calls.exists()


def test_container_failure_leaves_no_file_and_no_temp_file(fx: Export) -> None:
    result = fx.run(STUB_RC="3")

    assert result.returncode != 0
    assert "config-export failed in the backend container (exit 3)" in result.stderr
    assert "No file was written" in result.stderr
    assert len(lines(result)) == 1
    assert not (fx.cwd / "qradar-dash-config.json").exists()
    assert not list(fx.cwd.glob(".qradar-dash-config.*"))


def test_container_refusal_shows_stderr_and_exits_two(fx: Export) -> None:
    msg = "error: more than one console, pass --instance\n"
    result = fx.run(STUB_RC="2", STUB_ERR=msg, STUB_JSON="leaked")

    assert result.returncode == 2
    assert msg.strip() in result.stderr
    assert "No file was written" in result.stderr
    assert result.stdout == ""
    assert not (fx.cwd / "qradar-dash-config.json").exists()
    assert not list(fx.cwd.glob(".qradar-dash-config.*"))


def test_signal_cleans_temp_files(fx: Export) -> None:
    result = fx.run(STUB_SIGNAL="TERM", STUB_JSON="partial")

    assert result.returncode == 143
    assert not list(fx.cwd.glob(".qradar-dash-config.*"))


def test_mktemp_failure_exits_nonzero_with_one_line(fx: Export) -> None:
    fx.cwd.chmod(0o555)
    try:
        result = fx.run()
    finally:
        fx.cwd.chmod(0o755)

    assert result.returncode != 0
    assert "cannot create a temporary file" in result.stderr
    assert len(lines(result)) == 1
    assert not fx.calls.exists()
    assert not list(fx.cwd.glob(".qradar-dash-config.*"))


def test_mv_failure_exits_nonzero_with_one_line_and_no_leftover(fx: Export) -> None:
    result = fx.run(STUB_RM_TMP=str(fx.cwd))

    assert result.returncode != 0
    assert "could not move the export into place" in result.stderr
    assert len(lines(result)) == 1
    assert not (fx.cwd / "qradar-dash-config.json").exists()
    assert not list(fx.cwd.glob(".qradar-dash-config.*"))


@pytest.mark.parametrize("args, needle", [
    (("--bogus",), "unknown config-export argument"),
    (("--label",), "--label needs a value"),
    (("--instance",), "--instance needs a value"),
    (("--output",), "--output needs a value"),
])
def test_bad_arguments_exit_two_without_touching_docker(
    fx: Export, args: tuple[str, ...], needle: str
) -> None:
    result = fx.run(*args)

    assert result.returncode == 2
    assert needle in result.stderr
    assert len(lines(result)) == 1
    assert not fx.calls.exists()


def test_a_missing_output_directory_is_refused(fx: Export) -> None:
    result = fx.run("--output", str(fx.cwd / "absent" / "config.json"))

    assert result.returncode != 0
    assert "output directory does not exist" in result.stderr
    assert len(lines(result)) == 1
    assert not fx.calls.exists()
