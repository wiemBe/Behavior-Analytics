# Task OPS-B1 — version labels on the postgres and proxy images; CI runs once per tag push

Read `AGENTS.md` at the repository root first and follow it exactly (§1 scope, §5 SELinux).
Then read `docs/OPERATIONS-DESIGN.md` §2 (images and labels), `backend/Dockerfile` lines 26–37,
`.github/workflows/ci.yml`, `.github/workflows/release.yml`, `docker-compose.yml` and
`docker-compose.prod.yml`, and `docs/tasks/OPS-3.report.md` (its open point on labels).

## Two backlog items from 2026-10-16

1. **A `./qradar-dash … --build` of postgres or proxy produces an image with no version
   labels.** The release workflow adds the labels with `labels:`, but the two `deploy/`
   Dockerfiles have no `ARG`/`LABEL`, so a local build carries none. Backend and frontend
   already do it in their Dockerfiles.
   - Add `ARG APP_VERSION`, `ARG GIT_SHA` and the same two `LABEL`s as `backend/Dockerfile`
     (`org.opencontainers.image.version`, `org.opencontainers.image.revision`) to
     `deploy/postgres/Dockerfile` and `deploy/proxy/Dockerfile`, placed after the `COPY` so
     they do not invalidate the cached layer.
   - Pass `APP_VERSION: ${QRADAR_DASH_VERSION:-}` and `GIT_SHA: ${GIT_SHA:-}` as build args to
     both services in `docker-compose.prod.yml`, the way the file already does for frontend.
     postgres's `build:` lives in `docker-compose.yml`; add the args in the prod overlay only,
     unless compose cannot merge them there (then say why in the report).
   - The release workflow already passes the same build args to every matrix entry; confirm
     that by citing the lines, change it only if it does not.
   - `qradar-mcp/` is a pinned gitlink. **Never modify it** (AGENTS.md §2 rule 2): its labels
     stay workflow-only. Say so in the report.
2. **A tag push runs CI twice.** `ci.yml` has a bare `on: push:`, which matches tag pushes, and
   `release.yml` (on `tags: ["v*"]`) calls CI again through `workflow_call`.
   - Restrict `ci.yml`'s `push` trigger to branches (`branches: ["**"]`), so a tag push runs CI
     only through the release workflow. Keep `pull_request` and `workflow_call` as they are.

## Checks

- `docker compose -f docker-compose.yml -f docker-compose.prod.yml config` with a throwaway env
  file containing `QRADAR_DASH_VERSION=0.1.0` and `GIT_SHA=` set to the current commit SHA:
  show that both services get both args. Write the env file under
  `/home/efe/Documents/QRadar-Dash-work/opsb1/`, never in the repository, and put no real
  secret in it (dummy values for anything compose requires).
- Build the two images from their own `deploy/` contexts with `docker build --build-arg …`,
  tagged `opsb1-check/postgres` and `opsb1-check/proxy`, and show `docker image inspect`'s
  two labels for each. Then remove exactly those two tags (`docker rmi`), nothing else.
  No bind mount, no `:z`/`:Z`, no prune.
- A YAML parse of both workflow files
  (`.venv/bin/python -c "import yaml,sys; yaml.safe_load(open(sys.argv[1]))" <file>`).
- If `actionlint` runs from its image (`docker run --rm -i rhysd/actionlint:latest -` on stdin),
  run it on `ci.yml`; if not, say so.
- From `backend/`: `.venv/bin/ruff check app tests` and
  `PYTHONPATH=. ENCRYPTION_KEY=sxvVvbfjEG8mA0m2m6b1cQ2E0N4l7rXqO4uJ6c8zY5A= .venv/bin/pytest -q tests/unit/test_version.py tests/unit/test_qradar_dash_installer.py`
  (the main checkout's venv).

## Allowed files

- `deploy/postgres/Dockerfile`, `deploy/proxy/Dockerfile`
- `docker-compose.prod.yml`
- `.github/workflows/ci.yml` (and `release.yml` only if item 1 proves it necessary)
- `docs/OPERATIONS-DESIGN.md`, one as-built sentence where §2 describes the labels
- `docs/tasks/OPS-B1.report.md` (new)

**Not `qradar-dash`**: another agent is editing it. Do not commit.
