"""Decide which CI jobs a push can affect.

    python backend/scripts/ci_changes.py --event push --ref refs/heads/x \
        --before <sha> --sha <sha>

Prints ``backend=true|false`` and ``frontend=true|false`` (the format of
``$GITHUB_OUTPUT``) and appends them to that file when it is set. ``backend``
gates the unit and the integration jobs, ``frontend`` the frontend jobs.
``backend-static`` always runs.

Only a push to a branch other than ``main`` may skip anything. ``main``, a tag
(the release's ``workflow_call`` carries the tag's ref), a pull request, a
dispatched run, a new branch (a ``before`` of all zeros), a ``before`` commit
that cannot be fetched or diffed, and an empty diff all run everything.

For a branch push the changed paths decide, file by file, and the answers are
joined:

* a document (``docs/``, any ``*.md``, ``LICENSE``, ``NOTICE``) runs neither,
  unless a test source mentions its file name: some unit tests read
  ``docs/OPERATIONS.md`` and the like. ``docs/releases/`` always counts as code;
* a file under ``backend/app/``, ``backend/tests/`` or ``backend/alembic/``
  runs the backend jobs, and the frontend job too when a frontend test source
  mentions its file name (a frontend test reads ``backend/app/security/rbac.py``);
* a file under ``frontend/src/`` or ``frontend/public/`` runs the frontend job,
  and the backend jobs too when a backend source mentions its file name (the
  generated i18n artifact is checked by a backend test);
* anything else (workflows, ``qradar-dash``, ``deploy/``, requirements, scripts,
  lock files, ``frontend/messages/``, a Dockerfile) runs everything.

A name match is deliberately loose: it can only make a run bigger, never smaller.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

ZEROS = "0" * 40
DOC_SUFFIX = ".md"
DOC_NAMES = {"LICENSE", "NOTICE"}
# Where the tests and the application that could read a file of the other side live.
BACKEND_SOURCES = ("backend/tests", "backend/app")
FRONTEND_SOURCES = ("frontend/src",)
# The backend paths that are plain code. The rest of backend/ (requirements, pyproject,
# Dockerfile, scripts, config) is "anything else".
BACKEND_CODE = ("backend/app/", "backend/tests/", "backend/alembic/")
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".mjs", ".js")


def runs_everything(event: str, ref: str, before: str) -> bool:
    """Whether the trigger alone forces every job: only a branch push may skip."""
    if event != "push":
        return True
    if not ref.startswith("refs/heads/") or ref == "refs/heads/main":
        return True
    return not before or before == ZEROS


def source_text(root: Path, directories: tuple[str, ...]) -> str:
    """All source text under ``directories``, to look file names up in."""
    parts: list[str] = []
    for directory in directories:
        base = root / directory
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix in SOURCE_SUFFIXES and path.is_file():
                parts.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(parts)


def is_document(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    if path.startswith("docs/releases/"):
        return False
    return path.startswith("docs/") or path.endswith(DOC_SUFFIX) or name in DOC_NAMES


def decide(
    files: list[str], backend_text: str, frontend_text: str
) -> tuple[bool, bool]:
    """``(backend, frontend)`` for the changed ``files``; both when in any doubt."""
    if not files:
        return True, True
    backend = frontend = False
    for path in files:
        name = path.rsplit("/", 1)[-1]
        if is_document(path):
            # A document a test reads is code for the side that reads it.
            backend = backend or name in backend_text
            frontend = frontend or name in frontend_text
        elif path.startswith(BACKEND_CODE):
            backend = True
            frontend = frontend or name in frontend_text
        elif path.startswith(("frontend/src/", "frontend/public/")):
            frontend = True
            backend = backend or name in backend_text
        else:
            return True, True
    return backend, frontend


def changed_files(before: str, sha: str) -> list[str] | None:
    """The paths that differ between ``before`` and ``sha``; ``None`` when git cannot say."""
    fetch = subprocess.run(  # noqa: S603
        ["git", "fetch", "--no-tags", "--depth=1", "origin", before],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if fetch.returncode != 0:
        print(f"cannot fetch {before}: {fetch.stderr.strip()}", file=sys.stderr)
        return None
    diff = subprocess.run(  # noqa: S603
        ["git", "diff", "--name-only", before, sha],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if diff.returncode != 0:
        print(f"cannot diff {before}..{sha}: {diff.stderr.strip()}", file=sys.stderr)
        return None
    return [line for line in diff.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Which CI jobs can this push affect?")
    parser.add_argument("--event", default="", help="github.event_name")
    parser.add_argument("--ref", default="", help="github.ref")
    parser.add_argument("--before", default="", help="github.event.before")
    parser.add_argument("--sha", default="HEAD", help="github.sha")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)

    backend = frontend = True
    reason = "trigger runs everything"
    if not runs_everything(args.event, args.ref, args.before):
        files = changed_files(args.before, args.sha)
        if files is None:
            reason = "diff unavailable"
        else:
            backend, frontend = decide(
                files,
                source_text(args.root, BACKEND_SOURCES),
                source_text(args.root, FRONTEND_SOURCES),
            )
            reason = f"{len(files)} changed file(s)"
    lines = f"backend={str(backend).lower()}\nfrontend={str(frontend).lower()}\n"
    print(f"{reason}\n{lines}", end="")
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(lines)
    return 0


if __name__ == "__main__":
    sys.exit(main())
