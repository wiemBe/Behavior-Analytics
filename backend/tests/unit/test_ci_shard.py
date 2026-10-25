"""The CI shard splitter, and the promise the split makes.

The backend gate runs in three kinds of job: the static checks, one job for
everything that is not an integration test, and five jobs that run the
integration suite in shards. ``scripts/ci_shard.py`` decides which files go
into which shard, and these tests pin both the splitter and the split itself:
every test in the repository runs exactly once.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType
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
    """The pytest arguments, with the shard substitution expanded.

    The ``--junitxml`` report is written by a real run; a collection must not
    leave one in the worktree.
    """
    run = run.replace("${{ matrix.shard }}", str(shard))
    match = re.search(r"\$\(([^)]*)\)", run)
    if match is None:
        return _without_junitxml(shlex.split(run))
    inner = [
        sys.executable if token == "python" else token
        for token in shlex.split(match.group(1))
    ]
    files = subprocess.run(  # noqa: S603
        inner, cwd=BACKEND, capture_output=True, text=True, check=True
    ).stdout.split()
    return _without_junitxml(shlex.split(run[: match.start()]) + files)


def _without_junitxml(arguments: list[str]) -> list[str]:
    return [token for token in arguments if not token.startswith("--junitxml=")]


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


# The weights mode: the CI shards publish their own durations as JUnit XML
# reports, and the weights are refreshed from those reports (task CI-3).


def write_report(path: Path, cases: list[tuple[str, str, str]]) -> None:
    """A JUnit XML report: one testcase per (classname, name, seconds)."""
    body = "".join(
        f'<testcase classname="{classname}" name="{name}" time="{seconds}"/>'
        for classname, name, seconds in cases
    )
    suite = f'<testsuite name="pytest" tests="{len(cases)}">{body}</testsuite>'
    path.write_text(f"<testsuites>{suite}</testsuites>", encoding="utf-8")


def weights_from(
    *reports: Path, of: int = 5, root: Path = BACKEND
) -> subprocess.CompletedProcess[str]:
    """Run the weights mode over the given JUnit XML reports."""
    return run_script(
        "--weights-from", *(str(report) for report in reports), "--of", str(of), root=root
    )


def shard_rows(stderr: str) -> list[tuple[int, int, int]]:
    """The (shard, old, new) rows of the stderr table."""
    return [
        (int(shard), int(old), int(new))
        for shard, old, new in re.findall(r"^\s*(\d+)\s+(\d+)\s+(\d+)$", stderr, re.MULTILINE)
    ]


def _ci_shard_module() -> ModuleType:
    """The script as a module, for the functions the CLI is a thin skin over."""
    spec = importlib.util.spec_from_file_location("ci_shard_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_weights_sum_each_file_s_testcases_and_round_half_up(tmp_path: Path) -> None:
    """A file weighs the rounded sum of its testcase seconds.

    ``test_x`` sums to 6.5 and weighs 7: half a second rounds up, the rounding
    CI-2 used. ``test_slow`` sums to 0.4 and gets 1: a 0 weight would put it
    nowhere in particular. The table predicts the default five shards.
    """
    make_tree(tmp_path, {}, ["test_x.py", "test_slow.py"])
    report = tmp_path / "shard-1.xml"
    write_report(
        report,
        [
            ("tests.integration.test_x", "test_one", "1.0"),
            ("tests.integration.test_x", "test_two", "2.0"),
            ("tests.integration.test_x", "test_three", "3.5"),
            ("tests.integration.test_slow", "test_quick", "0.4"),
        ],
    )
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "tests/integration/test_x.py": 7,
        "tests/integration/test_slow.py": 1,
    }
    assert len(shard_rows(result.stderr)) == 5


def test_a_testcase_s_time_is_taken_whole(tmp_path: Path) -> None:
    """The testcase time already covers setup + call + teardown and is used as is.

    pytest reports the total by default (``junit_duration_report = total``);
    the weights must not shrink to the call alone, so nothing may turn that
    option on.
    """
    config = (BACKEND / "pyproject.toml").read_text(encoding="utf-8")
    assert "junit_duration_report" not in config
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    write_report(report, [("tests.integration.test_x", "test_timed", "12.6")])
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tests/integration/test_x.py": 13}


def test_a_testcase_of_a_gone_file_is_skipped_and_named(tmp_path: Path) -> None:
    """A file the reports mention that is not on disk changes nothing but says so."""
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    write_report(
        report,
        [
            ("tests.integration.test_x", "test_here", "2.0"),
            ("tests.integration.test_gone", "test_there", "9.0"),
        ],
    )
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tests/integration/test_x.py": 2}
    assert "test_gone.py" in result.stderr


def test_a_file_on_disk_without_a_time_is_left_out_and_named(tmp_path: Path) -> None:
    """A file the reports do not mention gets no weight: the median then applies."""
    make_tree(tmp_path, {}, ["test_x.py", "test_z.py"])
    report = tmp_path / "shard-1.xml"
    write_report(report, [("tests.integration.test_x", "test_here", "2.0")])
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tests/integration/test_x.py": 2}
    assert "tests/integration/test_z.py" in result.stderr


def test_the_weights_sum_across_reports(tmp_path: Path) -> None:
    """The five shard reports of one run are weighed together.

    A repeated ``--weights-from`` flag must add reports, not replace the
    earlier ones: either call shape weighs the same files.
    """
    make_tree(tmp_path, {}, ["test_x.py"])
    first = tmp_path / "shard-1.xml"
    second = tmp_path / "shard-2.xml"
    write_report(first, [("tests.integration.test_x", "test_one", "1.5")])
    write_report(second, [("tests.integration.test_x", "test_two", "1.0")])
    result = weights_from(first, second, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tests/integration/test_x.py": 3}
    repeated = run_script(
        "--weights-from", str(first), "--weights-from", str(second), root=tmp_path
    )
    assert repeated.returncode == 0, repeated.stderr
    assert json.loads(repeated.stdout) == json.loads(result.stdout)


def test_a_malformed_report_is_an_error(tmp_path: Path) -> None:
    """A truncated report fails the run: never a silent partial result."""
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    report.write_text("<testsuites><testsuite>", encoding="utf-8")
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "shard-1.xml" in result.stderr


def test_a_testcase_without_a_test_file_is_an_error(tmp_path: Path) -> None:
    """A testcase whose classname names no test file fails the run."""
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    write_report(
        report,
        [
            ("tests.integration.test_x", "test_here", "2.0"),
            ("com.example.thing.Check", "run", "1.0"),
        ],
    )
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "shard-1.xml" in result.stderr


def test_a_testcase_without_a_classname_is_an_error(tmp_path: Path) -> None:
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    report.write_text(
        '<testsuites><testsuite name="pytest">'
        '<testcase name="test_noclass" time="1.0"/>'
        "</testsuite></testsuites>",
        encoding="utf-8",
    )
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "shard-1.xml" in result.stderr


def test_a_missing_report_is_an_error(tmp_path: Path) -> None:
    result = weights_from(tmp_path / "shard-1.xml", root=tmp_path)
    assert result.returncode == 2
    assert result.stdout == ""
    assert "shard-1.xml" in result.stderr


def test_a_class_s_testcases_weigh_its_file(tmp_path: Path) -> None:
    """A classname with the test class appended still maps to the file.

    Pytest writes ``tests.integration.test_x.TestGroup``; the class is not
    part of the file's name.
    """
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    write_report(report, [("tests.integration.test_x.TestGroup", "test_in_class", "2.2")])
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {"tests/integration/test_x.py": 2}


def test_the_stdout_is_exactly_what_load_weights_accepts(tmp_path: Path) -> None:
    """Round trip: the printed JSON, written as the weights file, loads back.

    ``load_weights`` drops entries whose file is gone or whose weight is not a
    whole number of seconds, so only the exact shape survives it unchanged.
    """
    make_tree(tmp_path, {}, ["test_x.py", "test_y.py"])
    report = tmp_path / "shard-1.xml"
    write_report(
        report,
        [
            ("tests.integration.test_x", "test_one", "3.0"),
            ("tests.integration.test_y", "test_two", "0.3"),
        ],
    )
    result = weights_from(report, root=tmp_path)
    assert result.returncode == 0, result.stderr
    printed = json.loads(result.stdout)
    weights_path = tmp_path / "tests" / "integration" / "shard_weights.json"
    weights_path.write_text(result.stdout, encoding="utf-8")
    assert _ci_shard_module().load_weights(tmp_path) == printed


def test_the_stderr_table_predicts_the_shard_totals(tmp_path: Path) -> None:
    """The table shows each shard's seconds, from the old and the new weights.

    The old weights file is empty, so every file counts as the median 1 and
    both old shards get one second. The measured times are 6 and 2 seconds.
    """
    make_tree(tmp_path, {}, ["test_x.py", "test_y.py"])
    report = tmp_path / "shard-1.xml"
    write_report(
        report,
        [
            ("tests.integration.test_x", "test_one", "6.0"),
            ("tests.integration.test_y", "test_two", "2.0"),
        ],
    )
    result = weights_from(report, of=2, root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert shard_rows(result.stderr) == [(1, 1, 6), (2, 1, 2)]


def test_the_shard_mode_defaults_to_five_shards(tmp_path: Path) -> None:
    """``--of`` is optional now; it defaults to the CI matrix size."""
    make_tree(tmp_path, KNOWN_WEIGHTS, FIXTURE_FILES)
    result = run_script("--shard", "3", root=tmp_path)
    assert result.returncode == 0, result.stderr
    assert result.stdout.split() == shard_files(3, 5, root=tmp_path)


def test_the_modes_cannot_be_combined(tmp_path: Path) -> None:
    """--shard prints files and --weights-from prints weights: one at a time.

    The report is real, so the guard is the only thing that stops the run.
    """
    make_tree(tmp_path, {}, ["test_x.py"])
    report = tmp_path / "shard-1.xml"
    write_report(report, [("tests.integration.test_x", "test_here", "2.0")])
    result = run_script(
        "--weights-from", str(report), "--shard", "1", "--of", "5", root=tmp_path
    )
    assert result.returncode == 2
    assert result.stdout == ""


def test_the_integration_shards_publish_their_durations() -> None:
    """Every shard writes a JUnit XML report and uploads it, even when failing.

    The weights must come from CI's own numbers: a shard prints only the 15
    slowest tests, which is not enough to weigh files. The upload is pinned by
    commit like every other action in the file, and runs on a failing shard
    too: its timings are still useful.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    job = workflow["jobs"]["backend-integration"]
    run = _job_run(job)
    assert "--junitxml=integration-shard-${{ matrix.shard }}.xml" in run
    assert "$(python scripts/ci_shard.py --shard ${{ matrix.shard }} --of 5)" in run

    uploads = [
        step
        for step in job["steps"]
        if str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]
    assert len(uploads) == 1
    upload = uploads[0]
    assert upload.get("if") == "always()"
    with_block = upload["with"]
    assert with_block["name"] == "integration-durations-shard-${{ matrix.shard }}"
    assert with_block["path"] == "backend/integration-shard-${{ matrix.shard }}.xml"
    assert with_block["retention-days"] == 14
    assert with_block["if-no-files-found"] == "warn"

    # A version comment is a YAML comment, so only the raw text carries it.
    pinned = re.search(
        r"actions/upload-artifact@([0-9a-f]{40}) # (v\S+)",
        WORKFLOW.read_text(encoding="utf-8"),
    )
    assert pinned is not None, "pin the upload by commit, with a version comment"
