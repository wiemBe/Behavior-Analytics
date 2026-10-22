"""The CI shard splitter, and the promise the split makes.

The backend gate runs in three kinds of job: the static checks, one job for
everything that is not an integration test, and five jobs that run the
integration suite in shards. ``scripts/ci_shard.py`` decides which files go
into which shard, and these tests pin both the splitter and the split itself:
every test in the repository runs exactly once.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parent
SCRIPT = BACKEND / "scripts" / "ci_shard.py"
INTEGRATION_DIR = BACKEND / "tests" / "integration"
UNIT_DIR = BACKEND / "tests" / "unit"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
SHARDS = 5

# The known weights of the fixture below. Their median is 2.
KNOWN_WEIGHTS = {"test_a.py": 1, "test_b.py": 1, "test_c.py": 3, "test_d.py": 5}
FIXTURE_FILES = ["test_a.py", "test_b.py", "test_c.py", "test_d.py", "test_z.py"]
# The arguments that make the split invalid.
INVALID_ARGUMENTS = [
    ("--shard", "1", "--of", "0"),
    ("--shard", "1", "--of", "9"),
    ("--shard", "0", "--of", "5"),
    ("--shard", "6", "--of", "5"),
]
# A parametrized test whose id carries a random uuid.
UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def run_script(*arguments: str, root: Path = BACKEND) -> subprocess.CompletedProcess[str]:
    """Run the shard script the way CI runs it."""
    return subprocess.run(  # noqa: S603
        [sys.executable, str(SCRIPT), *arguments, "--root", str(root)],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
    )


def shard_files(shard: int, of: int = SHARDS, root: Path = BACKEND) -> list[str]:
    """The files of one shard, as the script prints them."""
    result = run_script("--shard", str(shard), "--of", str(of), root=root)
    assert result.returncode == 0, result.stderr
    return result.stdout.split()


def write_weights(root: Path, weights: dict[str, int]) -> None:
    """Write the weights file of a fake backend directory."""
    payload = {f"tests/integration/{name}": seconds for name, seconds in weights.items()}
    (root / "tests" / "integration" / "shard_weights.json").write_text(
        json.dumps(payload), encoding="utf-8"
    )


def make_tree(root: Path, weights: dict[str, int], files: list[str]) -> Path:
    """A backend directory with integration test files and a weights file."""
    integration = root / "tests" / "integration"
    integration.mkdir(parents=True)
    for name in files:
        (integration / name).write_text("", encoding="utf-8")
    write_weights(root, weights)
    return root


@pytest.mark.parametrize("of", range(1, 9))
def test_the_shards_cover_every_integration_file_exactly_once(of: int) -> None:
    expected = {f"tests/integration/{path.name}" for path in INTEGRATION_DIR.glob("test_*.py")}
    seen: list[str] = []
    for shard in range(1, of + 1):
        seen.extend(shard_files(shard, of))
    assert len(seen) == len(set(seen)), "a file is in two shards"
    assert set(seen) == expected


def test_the_same_command_gives_the_same_files_twice() -> None:
    for shard in range(1, SHARDS + 1):
        assert shard_files(shard) == shard_files(shard)


def shard_files_with(root: Path, name: str, seconds: int) -> list[str]:
    """The files of the first of two shards, with one more known weight."""
    weights = dict(KNOWN_WEIGHTS)
    weights[name] = seconds
    write_weights(root, weights)
    return shard_files(1, 2, root=root)


def test_a_file_without_a_weight_gets_the_median(tmp_path: Path) -> None:
    """A file the weights file does not know counts as the median weight.

    The known weights are 1, 1, 3 and 5, so the median is 2. The partition
    changes with every other weight, so only the median reproduces it.
    """
    make_tree(tmp_path, KNOWN_WEIGHTS, FIXTURE_FILES)
    without = shard_files(1, 2, root=tmp_path)
    matching = [
        seconds
        for seconds in range(1, 11)
        if shard_files_with(tmp_path, "test_z.py", seconds) == without
    ]
    assert matching == [2]


def test_a_weight_for_a_file_that_is_gone_is_ignored(tmp_path: Path) -> None:
    """An entry for a file that no longer exists changes nothing."""
    make_tree(tmp_path, KNOWN_WEIGHTS, FIXTURE_FILES)
    without = shard_files(1, 2, root=tmp_path)
    weights = dict(KNOWN_WEIGHTS)
    weights["test_gone.py"] = 1000
    write_weights(tmp_path, weights)
    after = shard_files(1, 2, root=tmp_path)
    assert after == without
    assert "tests/integration/test_gone.py" not in after


@pytest.mark.parametrize("arguments", INVALID_ARGUMENTS)
def test_the_shard_arguments_are_checked(arguments: tuple[str, ...]) -> None:
    result = run_script(*arguments)
    assert result.returncode == 2
    assert result.stdout == ""
    assert len(result.stderr.strip().splitlines()) == 1


def _dotted(node: ast.AST) -> str | None:
    """The dotted name of an attribute chain, e.g. ``pytest.mark.integration``."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return None if base is None else f"{base}.{node.attr}"
    return None


def _marks_integration(node: ast.AST) -> bool:
    if _dotted(node) == "pytest.mark.integration":
        return True
    if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
        return any(_marks_integration(element) for element in node.elts)
    return False


def declares_integration_marker(path: Path) -> bool:
    """Whether the file marks its tests with the ``integration`` marker."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and any(
            _marks_integration(decorator) for decorator in node.decorator_list
        ):
            return True
        if isinstance(node, ast.Assign) and any(
            _dotted(target) == "pytestmark" for target in node.targets
        ) and _marks_integration(node.value):
            return True
    return False


def test_every_integration_file_declares_the_integration_marker() -> None:
    unmarked = [
        path.name
        for path in sorted(INTEGRATION_DIR.glob("test_*.py"))
        if not declares_integration_marker(path)
    ]
    assert unmarked == []


def test_no_unit_file_declares_the_integration_marker() -> None:
    marked = [
        path.name
        for path in sorted(UNIT_DIR.glob("test_*.py"))
        if declares_integration_marker(path)
    ]
    assert marked == []


def _job_run(job: dict[str, Any]) -> str:
    """The command of the job's pytest step."""
    for step in job["steps"]:
        run = step.get("run", "")
        if "pytest" in run:
            return str(run)
    raise AssertionError("the job has no pytest step")


def _expand(run: str, shard: int) -> list[str]:
    """The pytest arguments, with the shard substitution expanded."""
    run = run.replace("${{ matrix.shard }}", str(shard))
    match = re.search(r"\$\(([^)]*)\)", run)
    if match is None:
        return shlex.split(run)
    inner = [
        sys.executable if token == "python" else token
        for token in shlex.split(match.group(1))
    ]
    files = subprocess.run(  # noqa: S603
        inner, cwd=BACKEND, capture_output=True, text=True, check=True
    ).stdout.split()
    return shlex.split(run[: match.start()]) + files


def _collect(arguments: list[str]) -> set[str]:
    """The test ids a pytest command selects.

    A parametrized test whose id carries a random uuid is normalized, so the
    two collections can be compared.
    """
    result = subprocess.run(  # noqa: S603
        [
            sys.executable,
            "-m",
            "pytest",
            "--collect-only",
            "-p",
            "no:cacheprovider",
            *arguments[1:],
        ],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        check=False,
        env=os.environ,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return {
        UUID.sub("<uuid>", line.strip())
        for line in result.stdout.splitlines()
        if line.strip().startswith("tests/") and "::" in line
    }


def test_the_split_runs_every_test_exactly_once() -> None:
    """The unit job and the five shards together run every test, once.

    This reads the commands out of the workflow, so the workflow and this
    test cannot drift apart: a test that no job runs, or a test that two
    jobs run, fails here.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    unit = _collect(_expand(_job_run(jobs["backend-unit"]), 1))
    shards = [
        _collect(_expand(_job_run(jobs["backend-integration"]), shard))
        for shard in range(1, SHARDS + 1)
    ]
    everything = _collect(["pytest"])

    sharded = set().union(*shards)
    assert unit.isdisjoint(sharded), "a test runs in the unit job and in a shard"
    assert unit | sharded == everything, "a test runs in no job"
