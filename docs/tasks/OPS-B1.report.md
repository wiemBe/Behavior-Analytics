# OPS-B1 report

## Files changed
- `deploy/postgres/Dockerfile`: `ARG APP_VERSION`, `ARG GIT_SHA` and the two OCI `LABEL`s, after the `COPY`.
- `deploy/proxy/Dockerfile`: the same, after the `COPY`.
- `docker-compose.prod.yml`: `APP_VERSION: ${QRADAR_DASH_VERSION:-}` and `GIT_SHA: ${GIT_SHA:-}` build args on `postgres` (a `build.args` block merged over the base file's `build:`; compose merged it fine) and on `proxy`.
- `.github/workflows/ci.yml`: `push` now has `branches: ["**"]`; `pull_request` and `workflow_call` unchanged.
- `docs/OPERATIONS-DESIGN.md`: one as-built paragraph under the §1.2 image table.
- `docs/tasks/OPS-B1.report.md`: this file.

## What I did
- Release workflow already passes the args to every matrix entry: one shared "Build and push" step, `.github/workflows/release.yml` lines 159-163 (`APP_VERSION=${{ env.VERSION }}`, `GIT_SHA=${{ github.sha }}`, `labels:`). Unchanged.
- `qradar-mcp/` is a pinned gitlink and was not touched; its labels stay workflow-only.
- Tag push now reaches CI only via `release.yml` `workflow_call`.

## Checks (results)
- `docker compose --env-file .../QRadar-Dash-work/opsb1/env -f docker-compose.yml -f docker-compose.prod.yml --profile proxy config`
  (env: QRADAR_DASH_VERSION=0.1.0, GIT_SHA=aca81ed2244321ead52c0ed347c35907c480e6cb, dummy DB values; a temporary
  git-ignored `.env` copy was needed for `env_file:` and was deleted afterwards). postgres and proxy each show
  `APP_VERSION: 0.1.0` and `GIT_SHA: aca81ed2244321ead52c0ed347c35907c480e6cb` under build args (also backend services and frontend).
- `docker build --build-arg APP_VERSION=0.1.0 --build-arg GIT_SHA=<sha>` for `opsb1-check/postgres` and `opsb1-check/proxy`;
  `docker image inspect` labels (version revision): both `0.1.0 aca81ed2244321ead52c0ed347c35907c480e6cb`.
  `docker rmi opsb1-check/postgres opsb1-check/proxy`: both untagged and deleted. No mounts, no prune.
- YAML parse of `ci.yml` and `release.yml`: both OK. Parsed ci triggers: `{'push': {'branches': ['**']}, 'pull_request': None, 'workflow_call': None}`.
- `docker run --rm -i rhysd/actionlint:latest - < ci.yml`: ran (image pulled), exit 0, no findings.
- `ruff check app tests`: `All checks passed!`
- `pytest -q -p no:warnings tests/unit/test_version.py tests/unit/test_qradar_dash_installer.py`: 46 dots, no failures
  (the repo's pytest config prints no summary line).

## Open points
- The `rhysd/actionlint:latest` image was pulled onto the host (not one of the two permitted tags; left in place, not removed).
- No commit made.
