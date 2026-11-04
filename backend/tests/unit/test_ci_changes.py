"""The path-based job gate of CI (task CI-6): which jobs a push can skip.

The decision cases are named here; ``.github/workflows/ci.yml`` wires the two
outputs to the jobs, and a test below reads that wiring out of the workflow.
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
from pathlib import Path
from types import ModuleType

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "backend" / "scripts" / "ci_changes.py"
WORKFLOW = REPO / ".github" / "workflows" / "ci.yml"
BRANCH = "refs/heads/agent/ci6"
SHA = "a" * 40


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("ci_changes", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ci_changes = load()
# What the tests of each side mention: a document, a backend file, a frontend file.
BACKEND_TEXT = "docs/OPERATIONS.md backendCodes.generated.json"
FRONTEND_TEXT = "backend/app/security/rbac.py rbac.py"


def decide(*files: str) -> tuple[bool, bool]:
    return ci_changes.decide(list(files), BACKEND_TEXT, FRONTEND_TEXT)


@pytest.mark.parametrize(
    ("event", "ref", "before"),
    [
        ("push", "refs/heads/main", SHA),
        ("push", "refs/tags/v0.1.11", SHA),
        ("push", "refs/tags/v0.1.11", "0" * 40),
        ("pull_request", "refs/pull/3/merge", SHA),
        ("workflow_dispatch", BRANCH, ""),
        ("push", BRANCH, "0" * 40),
        ("push", BRANCH, ""),
        ("", "", ""),
    ],
)
def test_main_tags_prs_dispatch_and_new_branches_run_everything(
    event: str, ref: str, before: str
) -> None:
    assert ci_changes.runs_everything(event, ref, before)


def test_a_branch_push_with_a_before_commit_may_skip() -> None:
    assert not ci_changes.runs_everything("push", BRANCH, SHA)
    assert not ci_changes.runs_everything("push", "refs/heads/agent/merge-h", SHA)


def test_docs_only_runs_neither_side() -> None:
    assert decide("docs/NEW-DESIGN.md", "README.md", "backend/NOTES.md") == (False, False)


def test_a_document_a_test_reads_runs_the_side_that_reads_it() -> None:
    assert decide("docs/OPERATIONS.md") == (True, False)
    # A release note is read by the release tests and the release workflow: always code.
    assert decide("docs/releases/0.1.11.md") == (True, True)


def test_frontend_only_skips_the_backend() -> None:
    assert decide("frontend/src/app/page.tsx", "frontend/src/lib/x.test.ts") == (False, True)


def test_backend_only_skips_the_frontend() -> None:
    assert decide("backend/app/services/x.py", "backend/tests/unit/test_x.py") == (True, False)


def test_a_frontend_file_the_backend_reads_runs_both() -> None:
    assert decide("frontend/src/i18n/backendCodes.generated.json") == (True, True)


def test_a_backend_file_a_frontend_test_reads_runs_both() -> None:
    assert decide("backend/app/security/rbac.py") == (True, True)


@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/ci.yml",
        "qradar-dash",
        "deploy/proxy/nginx.conf",
        "backend/requirements-dev.txt",
        "backend/pyproject.toml",
        "backend/scripts/ci_shard.py",
        "scripts/quick-check.sh",
        "frontend/messages/en/common.json",
        "frontend/package-lock.json",
        "frontend/Dockerfile",
        "VERSION",
        "docker-compose.yml",
    ],
)
def test_anything_else_runs_everything(path: str) -> None:
    assert decide(path) == (True, True)
    # One such file among docs-only changes still runs everything.
    assert decide("docs/NEW-DESIGN.md", path) == (True, True)


def test_mixed_backend_and_frontend_runs_both_and_an_empty_diff_runs_everything() -> None:
    assert decide("backend/app/x.py", "frontend/src/app/page.tsx") == (True, True)
    assert decide() == (True, True)


def run_script(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603
        ["python3", str(SCRIPT), "--root", str(root), *arguments],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
        cwd=root,
    )


def test_the_script_prints_the_outputs_and_fails_closed_without_a_diff(tmp_path: Path) -> None:
    """Not a git repository, so the diff cannot be made: everything runs."""
    result = run_script(
        tmp_path, "--event", "push", "--ref", BRANCH, "--before", SHA, "--sha", SHA
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines()[-2:] == ["backend=true", "frontend=true"]


def test_the_script_runs_everything_for_main(tmp_path: Path) -> None:
    result = run_script(
        tmp_path, "--event", "push", "--ref", "refs/heads/main", "--before", SHA, "--sha", SHA
    )
    assert result.stdout.splitlines()[-2:] == ["backend=true", "frontend=true"]


def test_the_script_writes_github_output(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "out"
    monkeypatch.setenv("GITHUB_OUTPUT", str(output))
    assert ci_changes.main(["--event", "pull_request", "--root", str(tmp_path)]) == 0
    assert output.read_text() == "backend=true\nfrontend=true\n"


def test_a_real_diff_in_a_temporary_repository(tmp_path: Path) -> None:
    """Docs-only, frontend-only and backend-only pushes, through git itself."""
    remote = tmp_path / "remote"
    work = tmp_path / "work"
    for path in (remote, work):
        path.mkdir()

    def git(cwd: Path, *args: str) -> str:
        done = subprocess.run(  # noqa: S603
            ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            check=True,
        )
        return done.stdout.strip()

    git(remote, "init", "-q", "-b", "main")
    (remote / "backend").mkdir()
    (remote / "backend" / "x.py").write_text("x = 1\n")
    git(remote, "add", "-A")
    git(remote, "commit", "-q", "-m", "base")
    base = git(remote, "rev-parse", "HEAD")

    def commit(relative: str) -> str:
        target = remote / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("changed\n")
        git(remote, "add", "-A")
        git(remote, "commit", "-q", "-m", relative)
        return git(remote, "rev-parse", "HEAD")

    cases = {
        "docs/NEW-DESIGN.md": ("false", "false"),
        "frontend/src/app/page.tsx": ("false", "true"),
        "backend/app/y.py": ("true", "false"),
        "scripts/z.sh": ("true", "true"),
    }
    git(work, "clone", "-q", str(remote), ".")
    for relative, (backend, frontend) in cases.items():
        sha = commit(relative)
        git(work, "fetch", "-q", "origin", sha)
        git(work, "checkout", "-q", "--detach", sha)
        result = run_script(
            work, "--event", "push", "--ref", BRANCH, "--before", base, "--sha", sha
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout.splitlines()[-2:] == [f"backend={backend}", f"frontend={frontend}"], (
            relative
        )
        base = sha


def test_the_workflow_wires_the_outputs_to_the_jobs() -> None:
    """Gate the right jobs on the right output; ``backend-static`` is never gated."""
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    assert "if" not in jobs["backend-static"]
    expected = {
        "backend-unit": "backend",
        "backend-integration": "backend",
        "frontend": "frontend",
        "frontend-test": "frontend",
    }
    for name, output in expected.items():
        assert jobs[name]["needs"] == "changes", name
        assert jobs[name]["if"] == f"needs.changes.outputs.{output} == 'true'", name
    step = next(s for s in jobs["changes"]["steps"] if s.get("id") == "decide")
    for flag in ("github.event_name", "github.ref", "github.event.before", "github.sha"):
        assert re.search(r"\$\{\{\s*" + re.escape(flag) + r"\s*\}\}", step["run"]), flag


def test_the_mentions_a_test_reads_across_the_sides_are_still_found() -> None:
    """The repository's real cross reads, so the loose name match keeps its grounding."""
    backend = ci_changes.source_text(REPO, ci_changes.BACKEND_SOURCES)
    frontend = ci_changes.source_text(REPO, ci_changes.FRONTEND_SOURCES)
    assert "OPERATIONS.md" in backend
    assert "backendCodes.generated.json" in backend
    assert "rbac.py" in frontend
