# Task OPS-3 — the release workflow: one gated tag build, six images on GHCR, the version in every image

Read `AGENTS.md` at the repository root first and follow it exactly. §5 matters most: SELinux
stays enforcing, never `:z`, and never bind-mount the repository into a container. Then read
these parts of `docs/OPERATIONS-DESIGN.md`:
- **§1.2** (how the version reaches each image) and **§1.3** (release notes);
- **§2.1 to §2.3** (the workflow, the image table, one frontend image for every site);
- §2.6;
- §10.2's OPS-3 line.

OPS-1 (`VERSION`, `GET /api/v1/version`, the frontend probe's `version`), OPS-2 (the hashed
locks), OPS-4 (`qradar-dash`, `docker-compose.prod.yml`, `deploy/proxy/`) and OPS-7 are merged
into your branch.

## Build

1. **`backend/Dockerfile`.**
   - `ARG APP_VERSION` and `ARG GIT_SHA`. `ENV GIT_SHA=$GIT_SHA`, so OPS-1's `git_sha()` reads it.
   - The OCI labels `org.opencontainers.image.version` and `org.opencontainers.image.revision`.
   - Copy the repository-root `VERSION` to `/app/VERSION`, and `docs/releases/` to
     `/app/releases/`. Both sit outside the `backend/` build context, so use a named **additional
     build context** (`COPY --from=repo …`):
     - compose: `additional_contexts`, in `docker-compose.yml`'s shared backend build anchor;
     - the workflow: `build-contexts`;
     - a plain build: `docker buildx build --build-context repo=. backend`.
   - Add a build-time guard: when `APP_VERSION` is set and differs from `/app/VERSION`, the
     build fails.
   - Keep everything else: the editable install, uid 10001, and the lock install.
2. **`frontend/Dockerfile`**, builder stage, before `npm run build`:
   - `ARG NEXT_PUBLIC_API_BASE_URL=/api/v1` with the matching `ENV`;
   - `ARG APP_VERSION`, exported as `NEXT_PUBLIC_APP_VERSION` (OPS-1's probe reads it);
   - the same two labels;
   - the runner's healthcheck uses `wget`. Check that `node:22-alpine` has it (busybox). If it
     does not, add it.
   - Then **delete `deploy/frontend/Dockerfile`**, OPS-4's temporary copy, and drop the
     `dockerfile:` line from the frontend service in `docker-compose.prod.yml`.
3. **Compose.**
   - **`docker-compose.yml` (dev)** keeps the split ports. Give the frontend
     `build.args.NEXT_PUBLIC_API_BASE_URL: ${NEXT_PUBLIC_API_BASE_URL:-http://localhost:8000/api/v1}`.
     The current `environment:` entry does not reach the bundle; leave it or drop it, and say
     which in the report.
   - **`docker-compose.prod.yml`** must build `/api/v1` whatever `.env` says.
     - The prod overlay pins that arg, plus `APP_VERSION: ${QRADAR_DASH_VERSION}` and
       `GIT_SHA: ${GIT_SHA:-}` for `--build`.
     - Add the `qradar-mcp` `image:` (`…/qradar-mcp:${QRADAR_DASH_VERSION}`).
     - **Pin Redis by digest:** `redis:7.4-alpine@sha256:<index digest>`. Resolve the multi-arch
       index digest now with `docker buildx imagetools inspect redis:7.4-alpine`.
   - Prove the merge with
     `docker compose -f docker-compose.yml -f docker-compose.prod.yml config`. Use a scratch
     `.env` under your scratch dir with `--env-file`, never the repository's.
4. **`docs/releases/0.1.0.md`.**
   - Front matter: `version: 0.1.0` and `min_from: 0.1.0`.
   - A short factual body: what an operator gets in the first release, the migration head, and
     that retention and compression are off until set (Appendix B).
   - Do not invent features: take them from `README.md`.
5. **`.github/workflows/ci.yml`.**
   - Add `workflow_call:` so the release reuses it.
   - Frontend: `npm ci` (not `npm install`), lint, typecheck, the vitest suite, and build. The
     build gets `NEXT_PUBLIC_API_BASE_URL=/api/v1`, not the localhost value.
   - The backend steps stay as OPS-2 and OPS-1 left them.
6. **`.github/workflows/release.yml`** (new), on tags `v*`. Permissions: `contents: write` and
   `packages: write`, nothing else. In order:
   1. **Check out** with submodules. Fail unless `git submodule status` shows `qradar-mcp` at
      `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`. Keep that pin as one constant, with a comment
      saying that a pin bump is a reviewed edit of that line.
   2. **Check the version.** Fail unless the tag is `v` + `VERSION`, and
      `docs/releases/<VERSION>.md` exists with a matching `version:`.
   3. **Gates:** `uses: ./.github/workflows/ci.yml`. Images build only after the gates pass.
   4. **Images:** build and push the five built images of design §2.2: backend, frontend,
      postgres, qradar-mcp and proxy.
      - Prefix `ghcr.io/${{ github.repository }}`, lower-cased.
      - Tags `<VERSION>` and `sha-<first 12 hex of the commit>`.
      - Build args `APP_VERSION` and `GIT_SHA` (the full SHA), plus the two labels.
   5. **Check the frontend bundle before pushing.** It contains `/api/v1` and does **not**
      contain `http://localhost:8000/api/v1`. Build with `load: true`, check, then push.
   6. **Check Redis.** The digest pinned in `docker-compose.prod.yml` must resolve
      (`docker buildx imagetools inspect`).
   7. **Release:** a GitHub Release whose body is `docs/releases/<VERSION>.md`, made with
      `gh release create` and `GITHUB_TOKEN`.
   - Use only `actions/*` and `docker/*` actions, each pinned to a **full commit SHA** with the
     version tag in a comment. Resolve the SHAs with `git ls-remote`. Store no other secret.

## Acceptance you run locally

GitHub Actions cannot run here, so prove each piece by hand. Copy each command and its result
into the report.

1. **Lint both workflows** with actionlint from its image, reading stdin, with no mount:
   `docker run --rm -i rhysd/actionlint:latest -` (or the pinned tag you find). Also check that
   `python -c "import yaml…"` parses them.
2. **Build the five images locally**, with the same build commands and args the workflow uses.
   Tag them `qradar-dash-ops3/<name>:0.1.0` and **do not push**. Then check:
   - backend:
     - `/app/VERSION` is `0.1.0`;
     - `/app/releases/0.1.0.md` exists;
     - `GIT_SHA` is set;
     - both labels are present (`docker inspect`);
     - `python -c "from app.core.version import read_version, git_sha, migration_head; print(read_version(), git_sha(), migration_head())"`
       prints all three;
   - the build-time guard: a build with `--build-arg APP_VERSION=9.9.9` fails;
   - frontend:
     - the bundle check from step 6.5 passes;
     - started with `docker run`, `GET /api/health` returns `version` `0.1.0`;
     - `wget` exists;
   - postgres, qradar-mcp and proxy build, and carry the labels.
3. **Compose:**
   - `docker compose -f docker-compose.yml -f docker-compose.prod.yml --env-file <scratch> config`
     shows the GHCR names, the Redis digest, the frontend arg `/api/v1` and no `dockerfile:`
     for the frontend;
   - the dev compose `config` shows the localhost arg.
4. **Clean up.** Remove every image and container you created (`docker image rm`, by the names
   above). Do not prune anything.

## Gates

- Frontend, with Node on PATH (`~/.local/node/bin`): `npm ci`, `npm run lint`,
  `npm run typecheck` and the vitest suite. The workflow's commands must be the ones you ran.
- Backend, from `backend/` with `PYTHONPATH=.` and the main checkout's venv: `ruff check app tests`,
  and the unit suite. No integration run is needed. Nothing in `app/` changes.
- The installer tests `tests/unit/test_qradar_dash_installer.py` stay green.

## Allowed files

- `backend/Dockerfile`
- `frontend/Dockerfile`
- `deploy/frontend/Dockerfile` (delete)
- `docker-compose.yml` (the build args and contexts only)
- `docker-compose.prod.yml`
- `.github/workflows/ci.yml`
- `.github/workflows/release.yml` (new)
- `docs/releases/0.1.0.md` (new)
- `README.md`: one short paragraph on how a release is cut, and on `--build-context repo=.` for
  a hand build
- `docs/tasks/OPS-3.report.md` (new)

If `qradar-dash` needs a change for `--build` (for example, passing `GIT_SHA`), describe it in the
report and do not make it.

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops3/`, never `/tmp`.
No push to any registry.
