# OPS-3 report

## Files changed
- `backend/Dockerfile`: `COPY --from=repo` of `VERSION` and `docs/releases/`; `ARG APP_VERSION`/`GIT_SHA`, `ENV GIT_SHA`, two OCI labels, build-time version guard. Lock install, editable install and uid 10001 kept.
- `frontend/Dockerfile`: `NEXT_PUBLIC_API_BASE_URL` (default `/api/v1`) and `NEXT_PUBLIC_APP_VERSION` in the builder; labels on the runner. `wget` is busybox in `node:22-alpine` (`/usr/bin/wget`), nothing added.
- `deploy/frontend/Dockerfile`: deleted (plain `rm`, not staged).
- `docker-compose.yml`: `additional_contexts: repo: .` on the backend anchor; frontend `build.args.NEXT_PUBLIC_API_BASE_URL` with the localhost default. The runtime `environment:` entry is left in place (no effect on the bundle, not in my allowed edits).
- `docker-compose.prod.yml`: `dockerfile:` removed from frontend; frontend args `/api/v1`, `APP_VERSION`, `GIT_SHA`; the same two args on backend/migrate/celery-worker/celery-beat (YAML anchor); `qradar-mcp` image; Redis pinned `redis:7.4-alpine@sha256:858f009f...3499` (index digest). `APP_VERSION`/`GIT_SHA` use `${...:-}` so an unset variable is empty, not a warning.
- `.github/workflows/ci.yml`: `workflow_call`; frontend `npm ci`, lint, typecheck, vitest, build with `/api/v1`; actions pinned to SHAs.
- `.github/workflows/release.yml` (new): jobs verify (pin constant `QRADAR_MCP_PIN`, tag = `v`+VERSION, notes front matter), gates (`uses: ./.github/workflows/ci.yml`), images (matrix of five; frontend `load: true` bundle check before the push), redis (digest resolves), release (`gh release create`). Permissions `contents: write`, `packages: write`.
- `docs/releases/0.1.0.md` (new), `README.md` (one "Cutting a release" section), `docs/tasks/OPS-3.report.md`.

Action SHAs (git ls-remote): checkout v7.0.1 3d3c42e5..., setup-python v7.0.0 5fda3b95..., setup-node v7.1.0 949feb24..., setup-buildx v4.4.1 f87e5991..., login v4.6.0 dbcb8138..., build-push v7.4.0 c3c9e263...

## Acceptance (verbatim results, abridged where noted)
1. actionlint: `docker run --rm -i rhysd/actionlint:latest - < .github/workflows/release.yml` printed nothing, exit 0; same for `ci.yml`. PyYAML parses both (jobs release: verify, gates, images, redis, release; ci: backend, frontend).
2. Builds (same args as the workflow, `--build-context repo=.` for backend, tags `qradar-dash-ops3/<name>:0.1.0`, `--load`, no push; qradar-mcp built from a copy of the main checkout's submodule in the scratch dir because this worktree's submodule is not initialised):
   - backend: `/app/VERSION` = `0.1.0`; `ls /app/releases` = `0.1.0.md`; `GIT_SHA=827c5c60cb4d106cb9ab3186a3ed5c51514ecb33`; uid 10001; labels `{"org.opencontainers.image.revision":"827c5c60...","org.opencontainers.image.version":"0.1.0"}`; `python -c "from app.core.version import ..."` printed `0.1.0 827c5c60cb4d106cb9ab3186a3ed5c51514ecb33 0025`; `import app.main` printed `import app.main ok` (no mount).
   - guard: `docker buildx build --build-context repo=. --build-arg APP_VERSION=9.9.9 backend` -> `ERROR: failed to build ... exit code: 1`, shell exit 1.
   - frontend: bundle `grep -rF /api/v1` hits (e.g. `./server/app/coverage/page.js`), `localhost hits: 0`; negative control (built with the localhost arg) gave `localhost hits: 4`, so the check discriminates. `docker run` then `wget -qO- http://127.0.0.1:3000/api/health` -> `{"status":"ok","version":"0.1.0"}`; `which wget` -> `/usr/bin/wget`.
   - postgres, qradar-mcp, proxy built and carry `version` and `revision` labels (postgres/proxy also `org.opencontainers.image.base.name`, mcp `dev.qradar-dash.qradar-mcp.pin=b8f6a4a3...`).
3. Compose, with a scratch `.env` in `/home/efe/Documents/QRadar-Dash-work/ops3/cfg` (copies of the two compose files; the repo `.env` untouched): prod merge with `--profile proxy --profile mcp` shows `ghcr.io/wiembe/qradar-dashboard/{backend,frontend,postgres,proxy,qradar-mcp}:0.1.0`, `redis:7.4-alpine@sha256:858f009f...3499`, frontend arg `NEXT_PUBLIC_API_BASE_URL: /api/v1` while `.env` set `http://localhost:9999/api/v1`, `APP_VERSION: 0.1.0`, `GIT_SHA`, `repo:` context, and no `dockerfile:` for the frontend other than the default `Dockerfile`. Dev `config` with the variable unset shows `NEXT_PUBLIC_API_BASE_URL: http://localhost:8000/api/v1`.
4. Cleanup: the five images, the negative-control image and `rhysd/actionlint:latest` removed by name; no containers left (`docker images | grep -c ops3` = 0). No prune.

## Gates
- Frontend (Node on PATH): `npm ci` ok; `npm run lint` "No ESLint warnings or errors"; `npm run typecheck` clean; `npm test`: 290 files, 3193 tests passed. `npm run build` was exercised by the image builds.
- Backend: `ruff check app tests` -> All checks passed; `pytest -m "not integration"` -> 3543 passed, 10 skipped, 1302 deselected; `tests/unit/test_qradar_dash_installer.py` -> 27 passed. `app/` unchanged. mypy and integration not run.

## Open points
- Release body: the workflow strips the YAML front matter before `gh release create` (the spec said the file is the body; raw front matter renders badly on GitHub). Revert the awk line if the exact file is wanted.
- postgres, proxy and qradar-mcp labels come from `labels:` in the workflow (their Dockerfiles are outside my allowed files). A `./qradar-dash --build` of those three therefore has no version labels; only backend and frontend get them from their Dockerfiles. Adding `ARG`/`LABEL` to the two `deploy/` Dockerfiles would fix that.
- `qradar-dash --build` needs `GIT_SHA` in the compose environment (prod overlay reads `${GIT_SHA:-}`) and `QRADAR_DASH_VERSION`, which the installer already writes to `.env`; it should `export GIT_SHA=$(git rev-parse HEAD)` before `docker compose build`. Not changed. Also `--build` needs the repo-root `VERSION` and `docs/releases/` in the checkout, which any clone has. Without `GIT_SHA`, `git_sha()` returns None (fail-honest).
- Backend and frontend images share one build per release only through cache; compose `--build` builds the backend image four times (one per service) as before.
- Worktree `qradar-mcp` submodule is not initialised, so the compose build of qradar-mcp was not run from here.
- Docs line in `docs/OPERATIONS-DESIGN.md` §2.1 says gates include `mypy app`; ci.yml runs it (no `|| true` present). Unchanged by me.
- Action versions (v7 checkout etc.) were taken from `git ls-remote`; GitHub Actions itself cannot run here, so the workflow is linted, not executed.
