"""Release-notes front matter: large bodies and every refusal path."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.unit.test_qradar_dash_upgrade import (
    TARGET_VERSION,
    UpgradeFixture,
    output,
)

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "qradar-dash"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash is required")

BODY_256_KIB = 256 * 1024
TAB_FIELDS = "0.2.0\t0.1.0"


def run_sourced(
    command: str, *, text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run COMMAND after sourcing the operator script with main() guarded off."""
    bash = shutil.which("bash") or "/bin/bash"
    env = {**os.environ}
    if text is not None:
        env["RELEASE_NOTES_TEXT"] = text
    return subprocess.run(  # noqa: S603
        [
            bash,
            "-c",
            f'QRADAR_DASH_SOURCE_ONLY=1 source "$1"; {command}',
            "bash",
            str(SCRIPT),
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def call_fields(text: str) -> subprocess.CompletedProcess[str]:
    """Call release_notes_fields TEXT and print its status and output."""
    return run_sourced(
        'rc=0; out=$(release_notes_fields "$RELEASE_NOTES_TEXT") || rc=$?; '
        "printf 'rc=%s\\nout=%s\\n' \"$rc\" \"$out\"",
        text=text,
    )


def test_a_small_body_still_prints_the_two_fields() -> None:
    result = call_fields("---\nversion: 0.2.0\nmin_from: 0.1.0\n---\nChanges.\n")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["rc=0", f"out={TAB_FIELDS}"]


def test_a_256_kib_body_is_parsed_200_times_in_one_shell() -> None:
    # The old printf | awk '… exit' pipeline lets printf take SIGPIPE once the body is
    # larger than the pipe buffer; FIX-39 measured 200/200 failures at 256 KiB.
    command = f"""
printf -v body '%*s' {BODY_256_KIB} ''
body=${{body// /x}}
notes=$'---\\nversion: 0.2.0\\nmin_from: 0.1.0\\n---\\n'"$body"
failures=0
round=0
while [ "$round" -lt 200 ]; do
  out=$(release_notes_fields "$notes") || failures=$((failures + 1))
  [ "$out" = $'{TAB_FIELDS}' ] || failures=$((failures + 1))
  round=$((round + 1))
done
printf 'failures=%d\\n' "$failures"
"""
    result = run_sourced(command)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "failures=0"


@pytest.mark.parametrize(
    ("label", "text"),
    [
        ("missing first ---", "version: 0.2.0\nmin_from: 0.1.0\n---\nChanges.\n"),
        ("no closing ---", "---\nversion: 0.2.0\nmin_from: 0.1.0\nChanges.\n"),
        (
            "two version lines",
            "---\nversion: 0.2.0\nversion: 0.2.0\nmin_from: 0.1.0\n---\nChanges.\n",
        ),
        ("missing min_from", "---\nversion: 0.2.0\n---\nChanges.\n"),
        ("invalid version", "---\nversion: 0.2\nmin_from: 0.1.0\n---\nChanges.\n"),
        ("invalid min_from", "---\nversion: 0.2.0\nmin_from: 0.1.0-rc1\n---\nChanges.\n"),
    ],
)
def test_release_notes_fields_refuses(label: str, text: str) -> None:
    result = call_fields(text)

    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("rc=1\n"), f"{label}: {result.stdout}"


def _serve_notes_from_a_file(upgrade: UpgradeFixture, notes: Path) -> None:
    """Point the fixture's docker stub at a notes file instead of the STUB_NOTES value.

    Linux caps one environment string at 128 KiB (MAX_ARG_STRLEN), so a 256 KiB body
    cannot travel in STUB_NOTES; the stub's stdout can still carry it.
    """
    real = upgrade.bin_dir / "docker.stub"
    (upgrade.bin_dir / "docker").rename(real)
    wrapper = upgrade.bin_dir / "docker"
    wrapper.write_text(
        "#!/bin/bash\n"
        'if [ "$1 $2 $3 $4" = "run --rm --entrypoint cat" ]; then\n'
        '  cat "$STUB_NOTES_FILE"\n'
        "  exit 0\n"
        "fi\n"
        'exec "$STUB_REAL_DOCKER" "$@"\n'
    )
    wrapper.chmod(0o755)
    upgrade.env["STUB_REAL_DOCKER"] = str(real)
    upgrade.env["STUB_NOTES_FILE"] = str(notes)


def test_upgrade_with_a_256_kib_image_notes_body_succeeds(tmp_path: Path) -> None:
    upgrade = UpgradeFixture(tmp_path)
    notes = (
        f"---\nversion: {TARGET_VERSION}\nmin_from: 0.1.0\n---\n"
        f"Changes.\n{'x' * BODY_256_KIB}\n"
    )
    path = upgrade.write_release_notes(notes)
    _serve_notes_from_a_file(upgrade, path)

    result = upgrade.run("upgrade", TARGET_VERSION)

    assert result.returncode == 0, output(result)
    assert "target release notes are missing, invalid" not in result.stderr
    assert upgrade.state()["last_upgrade"]["outcome"] == "success"  # type: ignore[index]
