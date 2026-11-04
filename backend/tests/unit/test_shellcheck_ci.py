"""The shipped bash is shell-linted in CI, over every file and by a pinned, hashed wheel.

`qradar-dash` (install, upgrade, rollback, backup, bundle, restore) and the helpers in
`deploy/` and `scripts/` run as root on the production host; `bash -n` and the stubbed
tests see none of the quoting, `cd` or unset-variable mistakes shellcheck finds. CI-8 makes
shellcheck a gate. These tests pin the job (it always runs, its severity, its file set, its
hash-pinned install) and the one property the single invocation needs: every shipped script
declares its dialect, so no file is checked under the wrong shell.
"""

from __future__ import annotations

import glob
import shlex
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
REQUIREMENTS = REPO / ".github" / "requirements-shellcheck.txt"
JOB = "shellcheck"
# The wheel the shipped scripts were made clean under. Its version carries a fourth part:
# `shellcheck-py` 0.11.0.1 bundles the shellcheck 0.11.0 binary (0.11.0 is not a package
# version; `pip install shellcheck-py==0.11.0` finds no distribution). The runner's
# preinstalled shellcheck is another version, and it changes when GitHub moves
# `ubuntu-latest`; the pin keeps CI's verdict identical to the local run in
# docs/tasks/CI-8.md. The hash is the linux x86_64 wheel from PyPI (the runner's platform),
# taken from PyPI's JSON API and confirmed with `pip hash` on the downloaded wheel. Never
# replace it with a value from memory: a wrong hash fails the job, and a guessed one would
# defeat the point of pinning.
SHELLCHECK_PY_VERSION = "0.11.0.1"
SHELLCHECK_PY_LINUX_X86_64_SHA256 = (
    "1b274df81de5b000ff78db433e7328b87e52e3c38481c60f8e488c3095beef05"
)


def job() -> dict[str, Any]:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"][JOB]


def shipped_scripts() -> list[Path]:
    """Every shell script the product ships: the CLI and the two script directories."""
    return [
        REPO / "qradar-dash",
        *sorted((REPO / "deploy").glob("*.sh")),
        *sorted((REPO / "scripts").glob("*.sh")),
    ]


def lint_command() -> list[str]:
    """The argv of the one `run:` line that lints (the step also prints the version)."""
    runs = [step.get("run", "") for step in job()["steps"]]
    lines = [
        line for run in runs for line in run.splitlines() if line.startswith("shellcheck -x")
    ]
    assert len(lines) == 1, f"the {JOB} job must run exactly one shellcheck command"
    return shlex.split(lines[0])


def test_the_lint_is_its_own_job_that_always_runs() -> None:
    """No `changes` gate and no Docker: seconds on any push, `qradar-dash` never skipped."""
    configured = job()
    assert "needs" not in configured
    assert "if" not in configured
    assert "services" not in configured, "shellcheck needs no database and no container"
    assert configured["runs-on"] == "ubuntu-latest"


def test_the_lint_covers_every_shipped_script_at_info_severity() -> None:
    """Info, not warning: an unquoted expansion (SC2086) is info-level in shellcheck 0.11.0.

    Warning severity would pass an unquoted expansion such as `chmod 600 $ENV_FILE.tmp`,
    which is the defect class the task names. Info also fails the error-level SC2148 (no
    dialect) and everything above it.
    """
    argv = lint_command()
    assert "--severity=info" in argv
    assert "-x" in argv, "scripts/merge-check.sh and quick-check.sh `source` _venv.sh"
    targets: set[Path] = set()
    for token in argv[1:]:
        if token.startswith("-"):
            continue
        # The workflow passes globs; expand them the way the job's shell does.
        matches = glob.glob(str(REPO / token)) if glob.has_magic(token) else [str(REPO / token)]
        assert matches, f"the shellcheck step names {token}, which matches no file"
        targets.update(Path(match).resolve() for match in matches)
    missing = [
        str(path.relative_to(REPO))
        for path in shipped_scripts()
        if path.resolve() not in targets
    ]
    assert not missing, f"the shellcheck step does not lint {missing}"


def test_the_ci_pins_the_wheel_by_version_and_hash() -> None:
    """The install is a hashed requirements file, like every other dependency lock here."""
    runs = "\n".join(step.get("run", "") for step in job()["steps"])
    assert "--require-hashes" in runs
    assert f"-r {REQUIREMENTS.relative_to(REPO)}" in runs, (
        f"the install must read {REQUIREMENTS.relative_to(REPO)}"
    )
    pinned = REQUIREMENTS.read_text(encoding="utf-8")
    assert f"shellcheck-py=={SHELLCHECK_PY_VERSION}" in pinned
    assert f"--hash=sha256:{SHELLCHECK_PY_LINUX_X86_64_SHA256}" in pinned
    assert pinned.count("--hash=sha256:") == 1, "one wheel, the runner's platform, one hash"


def test_every_shipped_script_declares_its_dialect() -> None:
    """One invocation over all of them: a bash shebang, or `# shellcheck shell=bash`.

    A sourced fragment cannot carry a shebang (it is never executed), and a file with neither
    fails the gate with SC2148 or is linted as the wrong shell, so the mistake is pinned here.
    """
    for script in shipped_scripts():
        lines = script.read_text(encoding="utf-8").splitlines()
        assert lines, script
        if lines[0].startswith("#!"):
            assert lines[0].endswith("bash"), f"{script}: shellcheck would lint the wrong shell"
            continue
        directive = next(
            (line.strip() for line in lines if line.startswith("# shellcheck shell=")), None
        )
        assert directive == "# shellcheck shell=bash", (
            f"{script}: no shebang and no `# shellcheck shell=bash` directive"
        )
