# Task OPS-2 — a hashed backend lock, installed by the image and CI; mypy no longer hidden

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/OPERATIONS-DESIGN.md` §2.4 (binding) and §10.2's OPS-2 line.

## Why

The backend image runs `pip install .` against the `>=` ranges in `backend/pyproject.toml`
(`backend/Dockerfile:13-19`), and CI does the same. Two builds on two days can ship different
dependency trees. The release that goes to production must install exactly what was tested.

## Build

1. **Two lock files, both with hashes.**
   - `backend/requirements.txt`: the production dependencies.
   - `backend/requirements-dev.txt`: the `dev` extra, constrained by the production lock.
   - Generate them with pip-tools (`pip-compile --generate-hashes`), as the design decides.
   - **Resolve them for Python 3.12**, which is the interpreter in the image
     (`backend/Dockerfile:1`) and in CI (`.github/workflows/ci.yml`). The host's venv is Python
     3.14, and a lock resolved there can differ.
     - If you can, run pip-compile under a 3.12 interpreter: a `python:3.12-slim` container,
       if your sandbox can reach Docker.
     - If you cannot, use `uv pip compile --python-version 3.12 --generate-hashes`. It writes the
       same pip-format file, and pip still does the installing. Install `uv` only into a venv
       under your scratch directory, never system-wide.
     - Say in the report which method you used and why.
   - Put the exact regeneration command in a comment at the top of each lock file, and in
     `README.md` "Development".
2. **The image** installs `pip install --require-hashes -r requirements.txt`, then
   `pip install --no-deps .`. That is not `-e`: the image must not depend on a writable source
   tree. Keep the existing non-root user and the layer order, so the dependency layer caches.
3. **CI** installs the production lock and the dev lock, then `pip install --no-deps -e .`, so
   the tests import this tree.
4. **mypy is not hidden.** If CI runs `mypy` with `|| true`, or anything else that swallows the
   failure, remove that, so a type error fails the workflow. The baseline is 0 errors
   (AGENTS.md §3).
5. **The update policy** (design §2.4), in `README.md` "Development": a dependency change is its
   own commit. Edit `pyproject.toml`, regenerate both locks, run the gates. No bot.

## Verification

- In a fresh venv under your scratch directory, `pip install --require-hashes -r
  requirements.txt` succeeds with no resolution of newer versions. Run it with Python 3.12 if
  you have one; otherwise say so.
- Run the current unit gate against an environment built from the locks (production plus dev,
  then `-e .`). `ruff`, `mypy` (0), the unit tests.
- If your sandbox can run `docker build`, build the backend image and report its size.
  Otherwise say so: the orchestrator builds it.

## Allowed files

`backend/requirements.txt`, `backend/requirements-dev.txt` (both new), `backend/Dockerfile`,
`.github/workflows/ci.yml`, `README.md` ("Development" only), `docs/tasks/OPS-2.report.md` (new).
Do not change `pyproject.toml`'s ranges in this task. Do not commit. Never write under `/tmp`.
