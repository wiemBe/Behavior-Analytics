# Operations design — install, release, upgrade, backup, rollback, diagnosis

Status: design, 2026-10-16. No code in this task. The implementation split is §10.
The production host has no AI. An operator (efe) installs, upgrades, diagnoses and recovers
from this document, the runbook it specifies, `./qradar-dash`, and the logs.

Binding decisions, already made by the user, are marked **[user]**. Decisions this design
makes are marked **[design]**.

**[user]** D-16. Images are pulled from GHCR. A local build remains the fallback. One
script, `./qradar-dash`, provides `install`, `upgrade <version>`, `rollback`, `backup`,
`restore`, `status`, `logs`, `doctor` and `support-bundle`. `upgrade` takes a backup
before migrating. Rollback is the previous image digests plus that backup. An Alembic
downgrade is not the rollback path: a downgrade drops what that revision added
(`README.md:704-706`). A few minutes of downtime per upgrade is acceptable. The database
survives every upgrade.

**[user]** D-18. The cut that must land by about 2026-10-30 is the minimum that makes
install, upgrade, backup/rollback and diagnosis safe. Polish is named in §10 and is not
in that cut.

**[user]** D-19. Local accounts and AD password over LDAPS. No group is read from AD.
Roles are assigned in the app. The runbook `docs/OPERATIONS.md` is Turkish. Commands,
log lines, `doctor` output and file names stay English.

**[user]** LDAP is configured from the UI. The installer does not ask for an LDAPS URL,
a bind template or a CA. It creates the break-glass local admin and nothing else about
the directory.

**[user]** The production host can reach `ghcr.io`. The repository
`wiemBe/QRadar-Dashboard` is public today and is designed for as private. A
`read:packages` token lives under `.secrets/` and is used by `docker login ghcr.io`.
An offline `docker save` / `docker load` bundle and a `--build` fallback both stay.

**[user]** Host shape: Linux x86_64 (RHEL/Rocky/Alma 9 or Ubuntu 22.04/24.04), Docker
Engine and Compose v2, HTTPS 443 in, QRadar 443 and AD 636 out, `ghcr.io` and GitHub
out, NTP, a DNS name and a TLS certificate for the app, the QRadar CA, a read-only
QRadar token, a backup directory. Sizes are in §3.1.

The code this design is written against is this branch. Migration head is `0025`
(`backend/alembic/versions/0025_auth_sessions.py:28-29`). AUTH-1, AUTH-2 and Phase F
are in the tree. The frontend lockfile pins `postcss` to `8.5.30`
(`frontend/package.json:35-37`).

---

## 1. Versioning

### 1.1 One source of truth

Today the string `0.1.0` is copied in three places and reaches nothing else:

- `backend/pyproject.toml:3`
- `frontend/package.json:3`
- `backend/app/main.py:63` (`FastAPI(..., version="0.1.0")`, hardcoded)

Images have local names only. The backend and frontend services set no `image:` key
(`docker-compose.yml:124-125`, `docker-compose.yml:182-185`). Postgres is
`qradar-observability/postgres:2.17.2-pg16` (`docker-compose.yml:50`). The MCP image
is tagged with the pin, `qradar-observability/qradar-mcp:b8f6a4a3`
(`docker-compose.yml:90`). None of those tags is the application version.

**[design]** The source of truth is a one-line file, `VERSION`, at the repository
root. The scheme is SemVer, `MAJOR.MINOR.PATCH`. The first value is the `0.1.0`
already in the tree. A git tag is `v` plus that line (`v0.1.0`). The release workflow
refuses a tag that does not match `VERSION`.

A CI check, added with the file, fails if any of these differ from `VERSION`:

- `backend/pyproject.toml` `version`
- `frontend/package.json` `version`
- the version the backend reports (it reads `VERSION`, it does not keep a third copy)

The migration head is not the application version. `0025` can stay the head across
several releases, and a release can add `0026` without a major bump. The version
endpoint reports both.

Pre-release tags (`v0.2.0-rc.1`) may be built. `upgrade` on a production host refuses
a version whose `VERSION` line contains a hyphen, so a release candidate is never
pulled by a typo.

There is no `latest` tag. `upgrade` takes an explicit version.

### 1.2 How the version reaches an image, the API and the UI

**Images.** The release workflow passes `VERSION` and the full git SHA as build args.

| Image | Build arg / label |
|---|---|
| backend (also migrate, celery-worker, celery-beat) | `ARG APP_VERSION`, `ARG GIT_SHA`. OCI labels `org.opencontainers.image.version` and `org.opencontainers.image.revision`. The file `VERSION` is copied into the image at `/app/VERSION`. |
| frontend | same labels. `NEXT_PUBLIC_APP_VERSION` is inlined at build the same way the API base is (§2.3). |
| postgres | labels carry the app version and the base `2.17.2-pg16`. |
| qradar-mcp | labels carry the app version and the pin `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51` (`README.md:234-235`). |
| proxy (§3.4) | labels carry the app version. |

Each image is pushed with two tags: the SemVer (`0.1.0`) and `sha-<12 hex chars>`.
The operator script records the image **digest** it started, not only the tag
(§4.5). A moved tag cannot change what `rollback` restarts.

**API.** Add `GET /api/v1/version`, unauthenticated, mounted beside health. Health is
the only open surface today (`backend/app/api/router.py:67-69`). Liveness stays a
pure process check so a slow database does not restart the container
(`backend/app/api/routes/health.py:13-17`). Readiness stays `SELECT 1`
(`backend/app/api/routes/health.py:20-24`). Version is a third route so neither of
those gains a new failure mode.

```json
{"version": "0.1.0", "git_sha": "<full sha>", "migration_head": "0025"}
```

`migration_head` is the revision the image was built with (the head of
`backend/alembic/versions/` at build), not a live query. The live database revision
is `doctor`'s job (§6). The body has no environment, no hostnames, no token.

**UI.** The account menu (`frontend/src/components/shell/Account.tsx:1-5`) gains one
line, the version from `GET /api/v1/version`. The sign-in page
(`frontend/src/app/login/page.tsx`) shows the same line so an operator who cannot
sign in can still read the build. The string uses the existing type tokens. It is
not a new page and not a banner. If the request fails, the line says the version
could not be read. It does not invent `0.1.0`.

The frontend container probe returns only `{"status": "ok"}` today
(`frontend/src/app/api/health/route.ts:1-3`). OPS-1 adds a `version` field to
that JSON, the version baked into the frontend image. `doctor`'s
`version_match` check (§6) WARNs when that field and `GET /api/v1/version`
differ. They are built from one release, so a difference means mixed tags.
Until OPS-1 lands, the probe has nothing to compare.

### 1.3 Release notes

**[design]** `docs/releases/<version>.md`, copied into the backend image at
`/app/releases/<version>.md`. `upgrade` reads the notes from the **target image**
(`docker run --rm --entrypoint cat <image> /app/releases/<version>.md`) before it
stops anything. It does not call the GitHub API. A `read:packages` token cannot be
assumed to read repository contents, and the host may be installing from the offline
bundle.

Front matter:

```yaml
version: 0.2.0
min_from: 0.1.0
```

`min_from` is the oldest installed version that may move to this one in one upgrade.
`upgrade` reads the installed version from `.qradar-dash/state.json` (§4.5) and
stops, having changed nothing, when the installed version is older than `min_from`.
The first release uses `min_from: 0.1.0`.

The body is short: schema changes, new required settings, retention behaviour, and
anything an operator must do by hand. Detector threshold changes go here too, because
they change what the analyst sees after the upgrade.

---

## 2. The release pipeline

### 2.1 Workflow

New file `.github/workflows/release.yml`. Trigger: a tag `v*`. The workflow:

1. Checks out the tag with submodules. `qradar-mcp` is a submodule of the public
   IBM repository (`.gitmodules:1-3`), pinned (`README.md:234-235`). The workflow
   checks `git submodule status` and fails if the recorded pin is not
   `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51` unless the release notes say the pin
   changed. A pin bump is a reviewed change to the allowlist
   (`README.md:255-257`), not a side effect of a release.
2. Fails if the tag does not equal `v` + `VERSION`.
3. Runs the gates. Backend, from `backend/`: ruff, `mypy app` (the result must be
   zero errors; today's CI swallows mypy with `|| true` at
   `.github/workflows/ci.yml:48`), `alembic upgrade head` and `alembic check`
   (the same steps as `.github/workflows/ci.yml:57-69`), then pytest. Frontend:
   `npm ci`, lint, typecheck, vitest, build. CI today runs `npm install` and does
   not run lint or vitest (`.github/workflows/ci.yml:82-92`). The release workflow
   runs the full gate. OPS-3 updates the pull-request workflow to the same
   commands so a red release is not the first time the gate runs.
4. Builds and pushes the images in §2.2.
5. Writes a GitHub Release whose body is `docs/releases/<version>.md`. The release
   is a convenience. The image copy is the one `upgrade` trusts.

`GITHUB_TOKEN` needs `packages:write`. No extra secret is stored in the repository.

### 2.2 Images

| Compose service | Image | Notes |
|---|---|---|
| backend, migrate, celery-worker, celery-beat | `ghcr.io/wiembe/qradar-dashboard/backend` | One image. Migrate is `alembic upgrade head` (`docker-compose.yml:137-143`). Worker and beat are the same image with other commands (`docker-compose.yml:145-179`). |
| frontend | `ghcr.io/wiembe/qradar-dashboard/frontend` | Built with `NEXT_PUBLIC_API_BASE_URL=/api/v1` (§2.3). |
| postgres | `ghcr.io/wiembe/qradar-dashboard/postgres` | Built from `deploy/postgres/Dockerfile:10-12` on `timescale/timescaledb:2.17.2-pg16`. Also tagged `2.17.2-pg16` when the Dockerfile has not changed. |
| qradar-mcp | `ghcr.io/wiembe/qradar-dashboard/qradar-mcp` | Built from the pinned submodule. Not started unless the `mcp` profile is requested (`docker-compose.yml:121`). |
| proxy | `ghcr.io/wiembe/qradar-dashboard/proxy` | New. Nginx config baked into the image (§3.4). |
| redis | `redis:7.4-alpine@sha256:<digest>` | Not built. The release resolves the digest and the production compose file pins it. Today's file floats on the tag (`docker-compose.yml:71`). |

GHCR lowercases the owner and the image name. The repository is
`wiemBe/QRadar-Dashboard`, so the image prefix is
`ghcr.io/wiembe/qradar-dashboard/`.

The production compose file sets `image:` to those names and keeps `build:` so
`--build` still works. `docker compose pull` is the default. `docker compose build`
runs only for `--build`.

### 2.3 One frontend image for every site

`NEXT_PUBLIC_*` is inlined at `next build`. The client uses

```
process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000/api/v1"
```

(`frontend/src/lib/api.ts:6`). Server components use `INTERNAL_API_BASE_URL` when
`window` is absent (`frontend/src/lib/api.ts:8-15`). `next.config.mjs:7-9` copies
the public variable into the build. The frontend Dockerfile runs `npm run build`
with no `ARG` and no `ENV` for it (`frontend/Dockerfile:10-15`). A missing value
becomes the localhost fallback, which is a second origin from every production
hostname.

Production CORS allows the configured public origin only
(`backend/app/main.py:74-85`). In production that origin must be `https` with a
host and no path (`backend/app/core/config.py:893-906`). A browser on
`https://soc.example` calling `http://localhost:8000` is refused. The README
already states this (`README.md:563-600`).

**[design]** The frontend Dockerfile gains

```
ARG NEXT_PUBLIC_API_BASE_URL=/api/v1
ENV NEXT_PUBLIC_API_BASE_URL=$NEXT_PUBLIC_API_BASE_URL
```

in the builder stage, before `npm run build`. The default is the relative base, so
a release image is correct without a special compose setting. A development compose
build that wants the split ports passes the build arg explicitly. The running
container's `environment:` (`docker-compose.yml:186-191`) does not change the
bundle the browser already has. `INTERNAL_API_BASE_URL=http://backend:8000/api/v1`
stays a runtime variable (`docker-compose.yml:188`). It is not `NEXT_PUBLIC_`, so
it is read when the server component runs.

The proxy serves the UI at `/` and the API at `/api/` (§3.4). `/api/v1/...` on that
origin is same-origin. One image serves every site. The hostname lives in
`PUBLIC_ORIGIN` and in the TLS certificate, not in the image.

### 2.4 Lockfile

The backend image installs from floating ranges:

```
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .
```

(`backend/Dockerfile:13-16`), then `pip install --no-deps -e .`
(`backend/Dockerfile:18-19`). `backend/pyproject.toml:6-30` is `>=` ranges. CI does
the same with `pip install -e '.[dev]'` (`.github/workflows/ci.yml:39-40`). Two
builds on two days can ship different dependency trees. The frontend already
installs from its lock (`frontend/Dockerfile:4-8`), including the `postcss` override
(`frontend/package.json:35-37`). PostCSS runs at `next build` only (T-117). The
production image must keep that override, which `npm ci` does and a fresh resolve
might not.

**[design]** Use pip-tools, not uv, for this cut. The toolchain today is pip
(the Dockerfile and CI). A second installer is a larger change than the two-week
cut can absorb. uv remains a later option if pip-tools proves awkward. It is not
the first lock.

OPS-2 commits:

- `backend/requirements.txt`, produced by `pip-compile --generate-hashes` from
  `pyproject.toml`, production dependencies only.
- `backend/requirements-dev.txt`, the `dev` extra, hashed, constrained by the
  production lock.

The image installs `pip install --require-hashes -r requirements.txt` and then
`pip install --no-deps .` (not `-e`, so the image does not depend on a writable
source tree; the Dockerfile already drops privileges at
`backend/Dockerfile:21-23`). CI installs the same lock plus the dev lock, then
`pip install --no-deps -e .` so tests import this tree.

**Update policy.** A dependency change is its own commit: edit `pyproject.toml`,
regenerate both lock files, run the gates. The release workflow never upgrades
dependencies. It installs the lock that was tested. No bot in this cut.

`python:3.12-slim` stays the base (`backend/Dockerfile:1`) until a release notes
entry says otherwise. Moving to 3.13 changes TLS behaviour (§8, symptom 3).

### 2.5 Registry authentication

**[design]** `.secrets/ghcr.token` mode `0600`, never an environment variable and
never a compose secret mounted into the app. `install` and `upgrade` run:

```bash
docker login ghcr.io --username "$GHCR_USER" --password-stdin < .secrets/ghcr.token
```

The token is `read:packages` only. It is not written to `~/.git-credentials` and
not placed in the remote URL. `docker logout` is not run afterwards: the Docker
config already stores the credential, and a logout would make the next `upgrade`
prompt. The Docker config file is the operator's. The token file remains the
copy the operator can replace.

If the file is absent, `install` tries the pull once. On a denial it stops and
prints that `.secrets/ghcr.token` is required. A public repository works without
the file. A private one does not. The script is written for the private case.

The offline bundle (§2.6) does not use the registry at all.

Git clone of a private parent repository needs `contents:read`, which is a
different permission. That credential is used only for `git clone`, before the
script runs, via `GIT_ASKPASS` or an equivalent that does not put the token in
`git remote -v`. The script does not clone. The submodule URL is public
(`.gitmodules:3`), so a recursive clone's submodule step does not need the
parent token. `--build` needs the submodule populated. A pull install does not:
the MCP image is already built, and the default install does not start MCP.

### 2.6 Offline bundle and `--build`

**[design]** `qradar-dash bundle`, run on a machine that can pull, writes
`qradar-dash-<version>-bundle.tar` containing:

- `images.tar` from `docker save` of the six images in §2.2 (redis included)
- `SHA256SUMS`
- `VERSION`, `docs/releases/<version>.md`
- `qradar-dash`, `docker-compose.yml`, `docker-compose.prod.yml`, `deploy/`

No `.env`, no `.secrets`. On the production host:

```bash
./qradar-dash install --offline qradar-dash-<version>-bundle.tar
```

loads the images, checks the sums, and does not pull or build.

`--build` runs `docker compose build` from the checkout, including the submodule.
It is the fallback when the registry cannot be used and no bundle is on disk.
It is slower and it compiles the backend (`backend/Dockerfile:9-11` installs a
compiler). The production path is the pull.

---

## 3. `./qradar-dash install`

The human sequence is:

```bash
git clone --recurse-submodules https://github.com/wiemBe/QRadar-Dashboard.git
cd QRadar-Dashboard
./qradar-dash install
```

After the clone, one command. The wizard asks the questions in §3.3. A test and
a re-run use `--non-interactive .secrets/install.answers` so the same questions
have a file form. The answers file is mode `0600`. It holds paths and the admin
username. It does not hold a password, the encryption key, or the QRadar token.
Those are generated or read from the files the answers name.

The script is bash, `set -euo pipefail`, at the repository root. It uses
`docker compose` (v2). It does not call Python on the host. Key generation runs
inside the backend image after the image is present (§3.3).

### 3.1 Host size

Figures below use the settings as they are. Bytes per row are **not measured**.
The planning constant is 1 KiB per metric row, heap plus index, and it is an
estimate to be replaced with `pg_database_size` after the first week. `doctor`
prints that size so the estimate does not become folklore.

What actually bounds the disk:

| Data | Bound in the code | Default retention |
|---|---|---|
| Phase A volume, one row per source per 300 s bucket | `collection_interval_seconds` default 300 (`backend/app/core/config.py:235`). 288 buckets/day. | **None.** `retention_enabled` defaults false (`backend/app/core/config.py:219-223`). The beat task `apply_timescale_policies` calls `apply_policies` (`backend/app/services/timescale.py`) once a day, so the policies exist as soon as the setting is on. Search-result retention's docstring names the policy as optional and off (`backend/app/services/search_result_retention.py:10-16`). |
| WAF endpoint rows | Bucket width is the same 300 s (`backend/app/collectors/waf_endpoint_collector.py:367`). Cap 2,000 active endpoints per source (`backend/app/core/config.py:453`). | 35 days (`backend/app/core/config.py:486`), and only while `waf_retention_enabled` is true (default true, `:484`). Collection itself defaults **off** (`:410`). |
| IPS entity rows | Cap 2,000 active entities per source per dimension (`backend/app/core/config.py:175-177`). | 35 days (`:182`). Collection defaults **off** (`:171`). |
| Catch-all, geo | Cadence row inserted at 3,600 s (`backend/alembic/versions/0020_catchall_senders.py:135-137`, `0022_geo_collection.py:86-89`). | 35 days (`config.py:519`, `:537`). |
| Scheduled-search aggregates | Separate deleter, 90 days (`config.py:153`, `search_result_retention.py:3-8`). | Not the Timescale policy. |
| Custom-rule breaches | Resolved breaches older than `custom_rule_breach_retention_days` (`config.py:554-555`). The engine defaults **off** (`:545`). | 180 days. The deleter runs only while the engine is on. |
| Offense snapshots | Every 300 s (`config.py:264`). | Timescale policy off with the rest of `retention_enabled`. |
| Baseline lookback | WAF refuses to boot when metric retention is not longer than this while WAF collection and WAF retention are both on (`config.py:823-829`). | 28 days (`config.py:334`). The default WAF metric retention is 35 days (`:486`), so the default pair boots. |

Search-family cadences are seeded at 3,600 s (`backend/alembic/versions/0013_search_cadence.py:46-50` and the inserts cited above). The 300 s figure is the bucket width, not how often a search is sent. A 1-hour cadence still stores each 5-minute bucket.

Row formula, per day:

```
phase_a_rows = log_sources × (86400 / collection_interval_seconds)
waf_rows     = waf_sources × active_endpoints × (86400 / collection_interval_seconds)
```

Worked examples at the 1 KiB constant:

- Pilot, 500 sources, WAF and IPS left off, 90 days of Phase A:
  `500 × 288 × 90 × 1 KiB ≈ 12 GiB` of metric rows. With WAL and a second copy
  for the upgrade backup, plus images and the OS, the disk floor is **80 GiB**.
- Recommended, 1,000 sources kept 400 days, five WAF sources at 1,000 endpoints
  for 35 days: Phase A ≈ 110 GiB, WAF ≈ 48 GiB, before WAL and backups.

`apply_policies` has to run or the 90-day and 400-day numbers are fiction.
The beat task `apply_timescale_policies` runs it every
`TIMESCALE_POLICY_INTERVAL_SECONDS` (default 86400, minimum 3600), and once when
beat starts, so a changed setting does not wait a day. It reconciles: a policy
whose interval differs from the setting is removed and added again, an unchanged
one keeps its job id, and a table with no setting loses its policy. Turning
`RETENTION_ENABLED` off removes the retention jobs; it does not leave them
running. Each table is reconciled in its own savepoint, so one failure does not
stop the others. Until the task has run, disk use is unbounded for
`log_source_metric`, `rule_metric` and `offense_snapshot`.

**Minimum host:** 4 vCPU, 8 GiB RAM, 80 GiB disk. The worker is started with
`--concurrency=4` (`docker-compose.yml:153`). Fewer than four CPUs still runs,
and the four worker processes plus Postgres will contend. 8 GiB leaves a small
Postgres cache. This host is for Phase A with the families that default off
left off.

**Recommended host:** 8 vCPU, 16 GiB RAM, 400 GiB for Docker and Postgres, and
a **second filesystem** of 400 GiB mounted as the backup directory. The second
filesystem is what makes a full data disk recoverable: the backup is still
reachable when the data volume cannot grow. Preflight WARNs when the backup
directory and the Docker data root are the same device. It does not refuse.
A same-device backup still recovers a bad migration. It does not recover a
dead disk.

RAM and the 1 KiB constant are planning numbers. OPS-10 records the RSS of a
fresh install. `doctor` records `pg_database_size` from then on. Neither number
is a measured production profile yet.

Ports and paths the host must allow:

- In: `443/tcp` to the proxy.
- Out: the QRadar console `443/tcp`, AD `636/tcp`, `ghcr.io:443`, `github.com:443`, NTP.
- Not published: Postgres (`docker-compose.yml:66` has no `ports:`), Redis
  (`:80`), MCP (`:91-92`). The API and the UI stay on loopback,
  `127.0.0.1:8000` and `127.0.0.1:3000` (`docker-compose.yml:128`, `:193`),
  for diagnosis from the host. Remote analysts use `https://<name>/` only.

### 3.2 Preflight

Preflight changes nothing. Any failure stops the script before `.env` is written.

| Check | Pass | Fail / warn |
|---|---|---|
| Architecture | `uname -m` is `x86_64` | Fail otherwise. The images are built on `ubuntu-latest` (`.github/workflows/ci.yml:9`). |
| Docker | `docker version` and `docker compose version` both succeed | Fail. Compose v2 is required. The script does not call `docker-compose`. |
| Resources | at least 4 CPUs, 8 GiB RAM, 80 GiB free on the Docker root, 20 GiB free on the backup directory | Fail under the floor. |
| Ports | `443` free on the host when the bundled proxy is used. `127.0.0.1:3000` and `127.0.0.1:8000` free. | Fail if taken. |
| SELinux | `getenforce` printed | Never changed. `Enforcing` is a normal result. The script must not `setenforce`, must not pass `:z` or `:Z`, and must not bind-mount the checkout into a container. Secrets go through `deploy/load-secrets.sh` (`deploy/load-secrets.sh:4-9`, `deploy/postgres/Dockerfile:1-9`). |
| Clock | `timedatectl show` reports NTP synchronized, or the Ubuntu equivalent | Warn if not synchronized. Collection, Beat and sessions use UTC (`backend/app/workers/celery_app.py:26-27`). A skewed host moves watermarks and makes `TIMESTAMP_DELAY` fire for a clock rather than a source (`backend/app/models/enums.py:133`). |
| Timezone | printed | The app stores UTC. The host zone is the operator's. The runbook tells them to read UI times as UTC. |
| QRadar TCP | `openssl s_client -connect <host>:443 -CAfile <ca> -verify_return_error` as the README already does (`README.md:314-317`) | Fail on a verify error other than the AKI case in §8 symptom 3. This sends no token and no AQL. |
| Backup directory | exists or can be created, mode will be `0700`, not inside the git checkout | Fail if it cannot be created. |

`docker-compose.override.yml` is gitignored (`.gitignore:45`) and is how a lab
attaches the libvirt macvlan (`README.md:350-360`). Preflight prints whether
that file exists. It does not delete it. A production host that can route to
QRadar:443 does not need it.

### 3.3 The wizard

Asked, in this order:

1. Public hostname. Stored as `PUBLIC_ORIGIN=https://<hostname>` with no path.
   Production refuses anything else (`backend/app/core/config.py:893-906`).
2. TLS certificate file and private-key file for that name. Copied into
   `.secrets/tls.crt` and `.secrets/tls.key` mode `0600`, then streamed into a
   **separate** volume `qradar-observability-proxy-certs`. They are not added
   to the QRadar secrets volume, which the backend mounts
   (`docker-compose.yml:40`). The backend has no need of the app's private key.
3. External proxy, yes or no. Default no. Yes skips the bundled proxy and
   prints the host nginx shape from `README.md:608-632`, with
   `FORWARDED_ALLOW_IPS` still required (§3.4).
4. QRadar base URL (`https://...`). Rejected otherwise
   (`backend/app/core/config.py:782-788`).
5. QRadar console FQDN, bare hostname, for `QRADAR_CONSOLE_FQDN`. The MCP
   service cannot share `QRADAR_BASE_URL` (`docker-compose.yml:95-97`). Asked
   even though MCP is off, so a later profile enable has the value.
6. API version. Default `20.0` (`backend/app/core/config.py:113`). The lab
   command in the README uses `29.0` (`README.md:374`). The wizard says so and
   does not guess.
7. Path to the SEC token file. The script copies it to `.secrets/qradar.sec`
   mode `0600` the way the README does (`README.md:288-290`). The token is not
   typed into the shell and not written into `.env`.
8. Path to the QRadar CA bundle. Copied to `.secrets/qradar-ca.pem`. The bundle
   has to contain the root and the intermediate (`README.md:293-296`,
   `backend/app/providers/tls.py:25-28`).
9. `QRADAR_TLS_ALLOW_MISSING_AKI`, default false. The wizard asks only as a
   yes/no after explaining symptom 3. The shipped image is Python 3.12
   (`backend/Dockerfile:1`), which does not turn on `VERIFY_X509_STRICT`. The
   flag stays available (`backend/app/core/config.py:116-119`).
10. Instance name. Default `qradar`. Passed to `qradar add --name`.
11. Backup directory.
12. GHCR username and the path of the `read:packages` token. Skipped for
    `--offline` and `--build`.
13. Break-glass admin username. The password is not asked. `create-admin`
    generates it (`backend/app/cli/users.py:30-34`, `:101-103`).

Not asked: LDAP (D-19, UI), detector thresholds, family enablement, SMTP,
webhooks, LLM. WAF, IPS, rule-match collection and custom rules stay at their
defaults, which are off (`backend/app/core/config.py:171`, `:276`, `:410`,
`:545`). Catch-all and geo default on (`:496`, `:527`). Leaving them on matches
the code. The summary says so.

Generated into `.env` mode `0600`, after the image exists so the Fernet key can
be made inside it:

- `SECRET_KEY` from `openssl rand -base64 48` (production requires at least 32
  characters, `config.py:879-880`).
- `ENCRYPTION_KEY` from `Fernet.generate_key()` inside the backend container.
  A hand-rolled base64 string is not a Fernet key.
- `ENCRYPTION_KEY_VERSION=1`. `ENCRYPTION_KEYS_JSON` left empty.
- `POSTGRES_PASSWORD` from `openssl rand -base64 24`.
- `ENVIRONMENT=production`, `DEBUG=false`, `AUTH_PROVIDER=session`,
  `QRADAR_PROVIDER=rest`, `QRADAR_VERIFY_SSL=true`.
- `QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token`,
  `QRADAR_CA_BUNDLE=/run/secrets/qradar_ca.pem`. The token value is not in `.env`.
- `POSTGRES_USER=qradar_obs`, `POSTGRES_DB=qradar_obs`, `POSTGRES_HOST=postgres`.
- `NEXT_PUBLIC_API_BASE_URL=/api/v1` (documentation; the image already baked it).
- `INTERNAL_API_BASE_URL=http://backend:8000/api/v1`.
- `RETENTION_ENABLED=true`, `RETENTION_LOG_SOURCE_METRIC_DAYS=90`,
  `RETENTION_RULE_METRIC_DAYS=90`, `RETENTION_OFFENSE_SNAPSHOT_DAYS=90`,
  `COMPRESSION_AFTER_DAYS=7`. `RETENTION_SEARCH_RESULT_METRIC_DAYS` stays unset
  so the 90-day row deleter remains the only policy on that table
  (`search_result_retention.py:10-16`).
- `FORWARDED_ALLOW_IPS=172.28.10.10` (§3.4).
- `LLM_ENABLED=false`, `LLM_ALLOW_AUTONOMOUS_ACTIONS=false`. Production refuses
  to boot if autonomous actions are on (`config.py:889-892`).
- `MCP_ENABLED=false`.

`.env.example` still describes `AUTH_PROVIDER` as `local | oidc`
(`.env.example:226-227`). Production refuses `oidc` (`config.py:907-911`).
OPS-4 updates that comment so the file an operator reads matches the wizard.
The wizard does not copy `.env.example` as the production file. It writes the
list above.

### 3.4 Proxy and TLS

**[design]** Default is a bundled nginx container on the `edge` network,
publishing `443:443`. The config is baked into the image, the same reason the
Postgres init scripts are baked (`deploy/postgres/Dockerfile:1-9`): a bind
mount of the checkout is `user_home_t` on an enforcing host and the container
cannot read it.

The config is the README's example (`README.md:608-632`): `/api/` proxies to
`http://backend:8000`, everything else to `http://frontend:3000`, with `Host`,
`X-Forwarded-For` and `X-Forwarded-Proto`. It does not add or hide the security
headers the backend sets (`README.md:603-606`). Certificates are read from the
proxy volume at fixed paths.

`backend/app/core/rate_limit.py:16` sets `key_func=get_remote_address`
(imported at `:11`). The README identifies that function as
`request.client.host` (`README.md:635-638`). Uvicorn is started with
`--proxy-headers` (`docker-compose.yml:126`) and trusts
`FORWARDED_ALLOW_IPS`, defaulting to `127.0.0.1` (`README.md:635-660`). A
container on the bridge is not `127.0.0.1`, so without an explicit peer every
analyst shares one rate-limit bucket.

The production compose file pins the edge subnet to `172.28.10.0/24` and the
proxy container to `172.28.10.10`. `FORWARDED_ALLOW_IPS` is that address and
nothing else. It is not `*`. Recreating the proxy keeps the address. The
frontend container is on `edge` as well (`docker-compose.yml:202`) and is not
in the trust list, so a request from the browser through the proxy is the only
forwarded path.

External-proxy mode does not publish 443. The operator's proxy on the host
dials `127.0.0.1:8000` and `127.0.0.1:3000`. The peer Uvicorn sees is then a
Docker bridge address, which the README tells the operator to measure
(`README.md:662-678`). The wizard prints that paragraph and leaves
`FORWARDED_ALLOW_IPS` for them to fill before the backend is started. Install
refuses to start the backend in external-proxy mode while the value is empty
or `*`.

### 3.5 Pull, migrate, roles, start

Order:

1. `docker login` when a GHCR token file is configured.
2. `docker compose -f docker-compose.yml -f docker-compose.prod.yml pull`,
   or `docker load`, or `build`.
3. Generate the Fernet key and write `.env`.
4. `./deploy/load-secrets.sh`. The script already refuses a missing token or
   CA (`deploy/load-secrets.sh:27-29`), creates the external volume
   `qradar-observability-secrets` (`docker-compose.yml:217-221`), and streams
   the files so no host label changes. OPS-4 extends it, in the same style,
   with a second function for the proxy certificate volume. The QRadar token
   and CA stay mode `0400` / `0444` owned by uid `10001`
   (`deploy/load-secrets.sh:23-25`, `:41-43`), which is `appuser`
   (`backend/Dockerfile:22`).
5. `up -d postgres redis`. Wait until both healthchecks are healthy
   (`docker-compose.yml:60-65`, `:75-79`).
6. `run --rm migrate`. One shot, restart `no` (`docker-compose.yml:137-143`).
   A non-zero exit stops the install. The database is empty, so there is
   nothing to restore. The script prints `docker compose logs migrate`.
7. Break-glass admin, inside the backend image, after migrate:

   ```bash
   docker compose run --rm --no-deps backend \
     python -m app.cli.users create-admin --username <name>
   ```

   This creates the roles if needed and prints the password once
   (`backend/app/cli/users.py:44-46`, `:101-103`). `must_change_password` is
   set (`:61`). The script does not capture the password into a file or a log.
   It is on the terminal only.

   **Do not run `python -m app.services.seed`.** `seed` commits only after a
   successful inventory sync (`backend/app/services/seed.py:69-82`). On an
   empty database `ensure_default_instance` inserts a row named `default`
   with `provider_kind="mock"` and `console_host="mock"`
   (`backend/app/services/inventory_sync.py:248-266`). Collection later builds
   the provider from that row (`backend/app/providers/factory.py:55-87`), so
   a mock row would silently stop reading QRadar. `create-admin` calls
   `ensure_default_roles` only (`backend/app/cli/users.py:45`). That is the
   seed the installer runs.

8. Register the console, inside the container, so the stored CA path is the
   container path. `register` checks that the CA path exists
   (`backend/app/services/instance_registry.py:99-100`) and stores that path
   (`:115`). Collection reads the instance row, not the environment
   (`factory.py:64-68`, `:81-94`). The environment token is still required:
   production refuses to boot a `rest` provider with no token
   (`config.py:885-888`). Both copies are needed. The command is:

   ```bash
   docker compose run --rm backend \
     python -m app.cli.qradar add \
       --name <name> \
       --url <https-url> \
       --token-file /run/secrets/qradar_sec_token \
       --ca-file /run/secrets/qradar_ca.pem \
       --api-version <version>
   ```

   `add` is idempotent by name (`instance_registry.py:83-91`). A failed
   connection check still commits the row and returns 1
   (`backend/app/cli/qradar.py:47-72`). The install continues, records FAIL
   in the summary, and does not throw away the admin password already printed.

9. `up -d` the proxy (unless external), backend, frontend, celery-worker,
   celery-beat. Not the `mcp` profile.
10. Wait until those services are healthy or, for migrate, exited 0. The
    backend start period is 40 s (`docker-compose.yml:135`). The wait budget
    is 180 s. Frontend health is `GET /api/health` inside the container
    (`docker-compose.yml:197`, `frontend/src/app/api/health/route.ts`). The
    runner image does not install wget (`frontend/Dockerfile`). Alpine
    BusyBox usually provides it. OPS-3 installs wget in the runner stage so
    the probe does not depend on that.
11. `GET http://127.0.0.1:8000/api/v1/health/ready` and
    `GET http://127.0.0.1:8000/api/v1/version` from the host.

### 3.6 Summary

Printed last, in this order:

- URL `https://<hostname>/`
- Sign-in path `/login` (`frontend/src/app/login/page.tsx`)
- Admin username, and the sentence "the password was printed above and is not stored"
- "The first sign-in must change that password" (`must_change_password`)
- QRadar connection: the `add` result line
- Families left off: WAF, IPS, rule matches, custom rules
- Next: sign in, then Directory at `/admin/directory` (`frontend/src/lib/nav.ts:87`)
  if AD is used, and Users at `/admin/users` (`nav.ts:86`) to assign roles
- `doctor` and the runbook path `docs/OPERATIONS.md`
- Backup directory

Exit 0 when the stack is healthy and an admin exists. Exit 1 when the stack is
healthy but the QRadar check failed, or when a service never became healthy.
The password is printed before that exit, on stdout, so a failed QRadar check
does not swallow it.

### 3.7 Idempotent re-run

A second `install` with an existing `.env`:

- Does not regenerate `SECRET_KEY`, `ENCRYPTION_KEY` or `POSTGRES_PASSWORD`.
  Replacing `ENCRYPTION_KEY` makes stored ciphertext unreadable
  (`backend/app/security/crypto.py:80-88`). Replacing `POSTGRES_PASSWORD` in
  `.env` does not change the password inside an already-initialised volume:
  `POSTGRES_PASSWORD` is an init-time variable (`docker-compose.yml:51-57`).
- Does not call `create-admin` when that username already exists. Calling it
  again resets the password and revokes sessions (`backend/app/cli/users.py:59-69`). OPS-4
  adds `python -m app.cli.users has-admin --username <name>`, which prints
  `yes` or `no` and no other user fields. Re-run calls `create-admin` only
  when the answer is `no`.
- Re-runs `load-secrets.sh` (safe, `deploy/load-secrets.sh:11`) and `qradar add`
  (updates the row).
- Re-runs migrate (Alembic is a no-op at head).
- `up -d` again.

`--reconfigure` re-asks the non-secret questions and refuses to change
`ENCRYPTION_KEY` or `POSTGRES_PASSWORD` when the Postgres volume already exists.

### 3.8 `--build`

Same steps, with `docker compose build` instead of `pull`. Requires the
submodule. The frontend build arg default `/api/v1` still applies, so a local
production build does not bake in localhost.

---

## 4. `upgrade <version>`

`version` is SemVer without a leading `v`. The script adds the `v` only when it
talks to git. Image tags have no `v` (§1.1).

### 4.1 Preflight

Before any container is stopped:

- The target is not `latest` and contains no hyphen.
- `.qradar-dash/state.json` exists and its `installed_version` is older than
  the target. The same version, with healthy containers, exits 0 and does
  nothing. A newer installed version is refused: that is `rollback`.
- The target image can be pulled, or it is already loaded. Pull happens
  **before** the writers stop, so a registry failure costs no downtime.
- Release notes from the target image parse, and `installed_version` is at
  least `min_from`. Otherwise stop, images already pulled, containers
  untouched.
- Disk guard (§5.3) passes for a dump of the current database plus a scratch
  restore.
- Current `doctor` database and migration checks are not already FAIL. Upgrading
  a database that is not at the **current** image's head means someone migrated
  by hand or a previous migrate crashed. Stop and point at symptom 11.
- A previous upgrade recorded `outcome=failed` blocks a new upgrade until
  `rollback` or a successful `restore` has cleared it.

### 4.2 Backup, then the migration

1. Record the running image digests into the state file as `previous_images`.
2. Stop writers: `backend`, `celery-worker`, `celery-beat`. Leave `postgres`
   and `redis` up. Stop `frontend` and `proxy` as well so analysts get a
   connection failure rather than a half-working UI. The README's rule is the
   reason: a migration adds `NOT NULL` columns and drops the backfill default,
   so an old process still inserting fails or, worse, writes a shape the new
   code does not expect (`README.md:690-694`).
3. Backup (§5), reason `pre-upgrade`, including the scratch restore test.
   A failed test aborts the upgrade. Writers stay stopped only for the
   duration; on abort they are started again on the **old** digests.
4. `compose up -d` is not yet run. Retag the compose project to the new
   digests (the prod compose file's tags are updated by the script's env,
   `QRADAR_DASH_VERSION=<version>`, which the prod file interpolates).
5. `run --rm migrate` on the **new** backend image.
6. On migrate exit 0: `up -d` backend, worker, beat, frontend, proxy.
7. Wait up to 180 s for health. Then:
   - `GET /api/v1/health/ready` is 200 and `"database": "ok"`.
   - `GET /api/v1/version` reports the target version.
   - `doctor` checks `db_connect` and `migration_head` are PASS, and the live
     `alembic_version` equals the image's `migration_head`.
8. Record the outcome (§4.5). Start is not complete until step 7 passes.

Redis is flushed after the writers are stopped and before the new workers
start (`redis-cli FLUSHALL` inside the redis container). Redis holds the broker
and the result backend (`config.py:94-96`), not the system of record. A task
payload queued by the old code and executed by the new code, or the reverse
after a rollback, is a silent mismatch. Beat's schedule file lives on the
container's `/tmp` (`docker-compose.yml:170`) and is recreated on start. The
flush drops in-flight jobs. Work that matters is in Postgres and will be
picked up by the next beat tick. This is acceptable because collection is
watermarked and a failed run does not advance the watermark (`README.md:402-403`).

### 4.3 Automatic rollback

Triggered when any of these happen after the writers were stopped:

- `migrate` exits non-zero
- a required service is not healthy within 180 s
- `/api/v1/version` is not the target version
- `doctor` `db_connect` or `migration_head` is FAIL

Not triggered by QRadar being unreachable, an LDAP failure, a late search
family, or a disk WARN. Those can be true before the upgrade and must not
restore an old database over a new one that is actually fine.

The action is `rollback` (§5.4) using the backup just taken and
`previous_images`. If that rollback's own health check fails, the script
stops the writers again, prints the backup path, and exits non-zero. It does
not try a third strategy.

### 4.4 Downtime

Writers are down from the start of `pg_dump` until `/api/v1/health/ready`
returns 200. The pull is outside that window. The dominant term is the dump.
For a database under about 5 GiB the budget is 10 minutes, which is the
"few minutes" the user accepted, plus dump time when the database is larger.
OPS-6's rehearsal records the actual number on an empty database and on a
database loaded with a fixture. The design does not claim a measured figure.

### 4.5 The state file

`.qradar-dash/state.json`, mode `0600`, gitignored. No secrets, no tokens, no
`.env` values.

```json
{
  "installed_version": "0.1.0",
  "images": {"backend": "sha256:…", "frontend": "sha256:…", "postgres": "sha256:…", "proxy": "sha256:…", "redis": "sha256:…", "qradar-mcp": "sha256:…"},
  "previous_images": null,
  "last_upgrade": {
    "at": "2026-10-30T12:00:00Z",
    "from": "0.1.0",
    "to": "0.2.0",
    "outcome": "success",
    "backup": "/var/backups/qradar-dash/pre-upgrade-20261030T115500Z.dump"
  }
}
```

`outcome` is `success`, `rolled_back`, or `failed`. `status` prints the last
entry.

---

## 5. Backup, restore, rollback

### 5.1 What a backup is

One backup is a directory `qradar-dash-<utc>-<reason>/` mode `0700` inside the
backup root:

| File | Contents | Mode |
|---|---|---|
| `database.dump` | `pg_dump -Fc` of `POSTGRES_DB` | `0600` |
| `env.bak` | copy of `.env` | `0600` |
| `secrets.tar` | tar of the QRadar secrets volume and the proxy cert volume | `0600` |
| `state.json` | the state file at that moment | `0600` |
| `MANIFEST` | version, image digests, `alembic_version`, database size, hostname | `0600` |

`reason` is `manual`, `scheduled`, or `pre-upgrade`.

The dump contains ciphertext (instance tokens), bcrypt hashes, and the LDAP
CA PEM if one has been saved. It does not contain `ENCRYPTION_KEY`. Without
`env.bak` the dump's ciphertext cannot be read (`crypto.py:80-83` tells the
operator to put the old key back in `ENCRYPTION_KEYS_JSON`). `env.bak` is the
key. It stays mode `0600` in the backup directory. It is not copied into a
support bundle (§7).

`secrets.tar` is the token file and the TLS private key. They are not in the
database. A restore that omitted them would bring back a database whose
`ca_bundle_path` points at `/run/secrets/qradar_ca.pem` and whose volume was
empty. The tar is produced by the same stdin-tar approach as `load-secrets.sh`,
reading the volume from a short-lived container, so the checkout is not bind
mounted.

Redis is not in the backup (§4.2).

### 5.2 Dump and restore commands

Writers are stopped for a pre-upgrade dump. A scheduled dump may run with
writers up: `pg_dump` takes a transaction snapshot. The pre-upgrade dump
stops writers anyway so the snapshot matches the schema migrate is about to
change.

Inside the postgres container, as `POSTGRES_USER`:

```bash
pg_dump -Fc -d "$POSTGRES_DB" -f /tmp/database.dump
```

The file is then copied out of the container to the backup directory. `/tmp`
inside the container is wiped with the copy; the dump is not left there.

Restore into a database that already has the extensions. Docker's init scripts
run only on an empty data directory (`deploy/postgres/init/01-timescaledb.sql:1-4`
creates `timescaledb` and `pgcrypto`). `CREATE DATABASE` does not re-run them.
The script:

1. `CREATE DATABASE <name>`
2. `CREATE EXTENSION IF NOT EXISTS timescaledb` and `pgcrypto` in that database
3. `SELECT timescaledb_pre_restore();`
4. `pg_restore -d <name> --no-owner database.dump`
5. `SELECT timescaledb_post_restore();`

`--no-owner` because the role name is already the one in `.env`. A restore
onto this stack does not need the dump's ownership records, and a mismatch
would abort the restore.

This is the TimescaleDB 2.x logical restore sequence for the image we ship
(`timescale/timescaledb:2.17.2-pg16`, `deploy/postgres/Dockerfile:10`). OPS-5's
acceptance test runs it against a scratch database and checks
`alembic_version` and a row count. The test uses the suite's database, not a
QRadar host.

### 5.3 Disk guard and retention

Before writing a backup the script reads `pg_database_size` and `df` of the
backup filesystem. It refuses when free space is less than `2 × database size
+ 2 GiB`. The factor of two covers the dump file and the scratch database
created by the verify step. Custom-format dumps are often smaller. The guard
does not assume compression.

`backup` without `--no-verify` restores into `qradar_obs_restore_test` on the
same server, checks that `alembic_version` matches the source and that
`SELECT count(*)` on `alembic_version` and `role` succeeds, then drops the
scratch database. Pre-upgrade backups always verify. A failed verify deletes
the incomplete directory and exits non-zero.

Retention, applied at the end of a successful backup:

- Keep the last 7 `manual` or `scheduled` backups.
- Keep every `pre-upgrade` backup until two newer upgrades have `outcome=success`.
- Never delete the backup recorded in `last_upgrade.backup` while that outcome
  is `failed` or `rolled_back`.

A backup is deleted only when the guard for the **next** backup already
passes without it, so retention cannot be the thing that fills the disk.
If the guard fails, the script prints the size and the path and stops.

OPS-5 installs a systemd timer, `qradar-dash-backup.timer`, daily, running
`qradar-dash backup`. The host OS in §3.1 has systemd. The timer is the
difference between a backup command that exists and a backup that exists.

### 5.4 `rollback`

No argument. It uses `previous_images` and `last_upgrade.backup`.

1. Refuse if `previous_images` is null or the backup directory is missing.
2. Stop writers and the UI.
3. Restore that dump over the application database with the sequence in §5.2:
   restore into `qradar_obs_rollback`, verify `alembic_version`, then
   terminate connections, rename the current database to
   `qradar_obs_broken_<utc>`, rename the restored one to `POSTGRES_DB`.
   The broken copy is kept until the operator deletes it, so a bad rollback
   still has the database it replaced.
4. Start the previous image digests. Do not pull. Digests are local.
5. The same health checks as §4.2 step 7, expecting the **previous** version.
6. Record `outcome=rolled_back`.

`rollback` does not run `alembic downgrade`. Downgrade drops columns and
tables (`README.md:704-706`). The restored dump is already at the old head.

`restore <path>` is the same sequence for a chosen backup, used when the
operator wants a backup that is not `last_upgrade.backup`. It asks for the
word `restore` on stdin unless `--non-interactive` is set, in which case it
requires `--yes`. It refuses a path whose `MANIFEST` version is newer than
the images it would start: a newer dump against older code is the failure
mode the migration rule exists to prevent.

### 5.5 `status` and `logs`

`status` prints compose health, `installed_version`, digests, the last
upgrade line, the last backup path and age, and free space on both
filesystems. It does not print `.env`.

`logs [service]` is `docker compose logs --tail=200 -f`. With no service it
follows backend, worker and beat. The app's log filter redacts UUID-shaped
tokens only on lines that already mention a secret keyword
(`backend/app/core/logging.py:18-38`). `logs` does not claim to be a second
redactor. The support bundle is (§7).

---

## 6. `doctor`

`./qradar-dash doctor` is the command the operator runs. It does two things:

1. Host checks that a container cannot see: `df` of the Docker root and the
   backup directory, `timedatectl`, `getenforce`, `docker compose ps`.
2. `docker compose exec -T backend python -m app.cli.doctor`, which prints
   the in-container checks.

The host half is built as of OPS-7b. As built, `disk_docker` reports `df -P`
for Docker's root with the 70% WARN and 90% FAIL thresholds. As built,
`disk_backup` applies the same thresholds to `QRADAR_DASH_BACKUP_DIR` and also
checks the §5.3 database-size guard, warning when that size is unavailable. As
built, `qradar_clock` reports `timedatectl` NTP synchronization and uses
OPS-R15 for either an unsynchronized clock or a host without that command. As
built, `selinux` reports the current `getenforce` result as PASS, including
`Enforcing`, or PASSes with "SELinux is not installed" when the command is
absent. As built, `containers` checks every service returned by Compose and
ignores a stopped `qradar-mcp` while the `mcp` profile is off and an exited
`migrate` only when its exit code is zero. As built,
`doctor_incontainer` is emitted only when the backend is not running; otherwise
the wrapper prints the in-container output unchanged and preserves its status.

The disk **free-space** lines are host-side because the backend container
does not mount the Postgres volume (`docker-compose.yml:40` mounts only the
secrets volume). The in-container half prints `pg_database_size`, which is
the number that grows. Both appear in one report.

Each line is:

```
PASS  db_connect     database accepted SELECT 1
WARN  cadence_late   WAF_COLLECTION last run 2 h 10 m past its cadence    runbook: OPS-R30
FAIL  migration_head database at 0024, image expects 0025                 runbook: OPS-R11
```

`PASS`, `WARN`, `FAIL` only. The cause is one line. The runbook id is
`OPS-R` plus the symptom number from §8 (`OPS-R30` is entry 30). No token,
no `.env` value, no DSN, no `ENCRYPTION_KEY`.
Connection errors name the host and the exception class, not the password.
Exit 0 on all PASS, 1 on any WARN with no FAIL, 2 on any FAIL.

The process does not call a write method on the provider. The provider
interface has none (`AGENTS.md` §2). The one QRadar call is `GET /system/about`
through `validate_connection` (`backend/app/providers/qradar_rest.py:482-487`).
A separate TLS handshake to the console uses the CA bundle and sends no
`SEC` header, so certificate expiry can be read without presenting the token.

| Id | What | PASS | WARN | FAIL |
|---|---|---|---|---|
| `db_connect` | `SELECT 1`, same fact as readiness (`health.py:23`) | query returns | — | any error |
| `migration_head` | `SELECT version_num FROM alembic_version` against the image's baked head | one row, equal | — | missing, extra rows, or not equal |
| `timescale` | extension installed and `log_source_metric` is a hypertable (`timescale.py:33-46`) | both true | extension present, table is not a hypertable | extension missing in production |
| `retention_policy` | `timescaledb_information.jobs` has a retention policy on `log_source_metric` when `RETENTION_ENABLED` is true | policy present | enabled in settings, no policy (the gap in §3.1) | — |
| `redis` | `PING` on the broker URL (`config.py:95`) | `PONG` | — | connection error |
| `celery_workers` | `inspect ping` from inside the backend container, which is how the worker healthcheck works (`docker-compose.yml:155-157`) | at least one pong | — | no pong |
| `celery_beat` | a heartbeat key in Redis | age under 180 s | — | missing or older |
| `qradar_tls` | handshake with the configured CA (`tls.py:18-36`) | chain and hostname verify, notAfter more than 30 days away | notAfter inside 30 days, or `QRADAR_TLS_ALLOW_MISSING_AKI` is true | chain, hostname, or expiry failure. The message is the one the code already uses, including `unable to get issuer certificate` (`tls.py:25-28`) |
| `qradar_auth` | `GET /system/about` | reachable, version printed | — | the sanitized status text (`qradar_rest.py:183-202`). 401 and 403 share one sentence on purpose (`:190-191`) |
| `qradar_clock` | skipped. `/system/about` does not return a server time (`qradar_rest.py:486-500` keeps three fields). Host NTP is the host half of `doctor` | — | host NTP not synchronized | — |
| `watermarks` | each `collection_watermark` row (`backend/app/models/monitoring.py:24-51`) | `consecutive_failures` is 0 and lag under twice the family's cadence | lag over twice the cadence | `consecutive_failures` ≥ 3, or `last_error` set. `last_error` is already capped at 512 characters (`:51`) and provider text is from a fixed vocabulary (`backend/app/services/waf_pipeline.py:388-398`) |
| `cadence` | each `search_cadence` family (`backend/app/schemas/search_cadence.py:24-34`) | last run age ≤ cadence + settle + 60 s, the UI's rule (`frontend/src/components/catalog/derive.ts:17-19`, `:82-84`) | late, or never run while that family's enable flag is true | — |
| `families_off` | the four flags that default off | printed as PASS with the flag name, so a fresh install is not red | — | — |
| `errors_recent` | how many `collection_watermark` rows have `consecutive_failures > 0` | zero | one or two | three or more |
| `disk` | host `df` plus `pg_database_size` | under 70% and backup free space passes §5.3 | 70–90% | at or over 90%, or the backup guard would fail |
| `encryption` | decrypt one stored instance token if a row has one | decrypts | no instance token stored | `KeyRotationError` (`crypto.py:80-88`). The ciphertext is not printed |
| `secrets_files` | token file and CA file exist, are non-empty, and are readable by uid 10001 | both | — | missing or empty (`config.py:745-756` is the same failure the provider hits) |
| `public_origin` | the running settings | `https` origin | — | would not have booted; included so the report shows the origin the operator thinks they set |
| `version_match` | frontend probe vs `/api/v1/version` | equal | differ | frontend probe not reachable |

`errors_recent` counts watermark rows. It does not read
`search_failure_alert_after` (`config.py:142-145`). That setting is how many
consecutive failed runs of one scheduled search raise that search's own
alert. A single watermark already FAILs on the `watermarks` row when
`consecutive_failures` is 3 or more. `errors_recent` only says how many
families are in that state together.

Beat's Compose healthcheck only tests that `/tmp/celerybeat-schedule` exists
(`docker-compose.yml:171-176`). A hung beat whose pid 1 has not exited stays
"healthy". OPS-7 adds a 60-second task that sets a Redis key with a TTL of
180 s. `celery_beat` reads that key. The key contains a timestamp and no
payload.

`families_off` is PASS rather than WARN. A fresh install has WAF, IPS, rule
matches and custom rules off on purpose (§3.3). The line still names the flag
so the operator can see why a family has no watermark. The UI shows an em dash
for a family that has never run, not "on time"
(`frontend/src/components/catalog/HealthCollection.tsx:3-5`).

`doctor` does not start a sync, does not run an Ariel search, and does not
log in to LDAP. An Ariel search would spend the console's search budget
(`config.py:122`, the global cap at `:132`). LDAP is checked by the Test
button the runbook already points at.

---

## 7. `support-bundle`

`./qradar-dash support-bundle` writes
`<backup-root>/support-<utc>.tar.gz` mode `0600` and prints the path.

Contents:

- `doctor.txt`, the combined report
- `version.txt`, state file, `GET /api/v1/version`, frontend probe, image digests, `alembic_version`
- `compose-ps.txt`
- `watermarks.txt` and `cadence.txt`, the same rows `doctor` used
- `config.env`, `.env` with secret lines **removed**, not masked
- `logs/`, `docker compose logs --tail=2000 --no-color` for each service, after scrubbing

Not contents: `.secrets/`, either secrets volume, `database.dump`, `env.bak`,
`secrets.tar`, the GHCR token, session cookies, password hashes.

### 7.1 Redaction rules

The audit redactor masks any key matching `sec` as a substring
(`backend/app/security/redaction.py:13-16`). That already hid `timeout_seconds`
until the audit key was renamed (`backend/app/services/ldap_config.py:55-57`).
The same pattern matches inside `consecutive`. The bundle does **not** run
`redact()` over the watermark table or over `.env` keys. It uses an explicit
list.

Removed from `config.env`, line by line, key match, case insensitive:

`SECRET_KEY`, `ENCRYPTION_KEY`, `ENCRYPTION_KEYS_JSON`, `POSTGRES_PASSWORD`,
`QRADAR_SEC_TOKEN`, `OIDC_CLIENT_SECRET`, `LLM_API_KEY`, `SMTP_PASSWORD`,
any key containing `WEBHOOK`, any key containing `PASSWORD`, any key containing
`TOKEN`.

`QRADAR_API_TOKEN_FILE` is a path. It is kept. The file it names is not.

Scrubbed from every text file in the bundle, after assembly:

1. The exact bytes of every secret the script can read: the removed `.env`
   values, `.secrets/qradar.sec`, `.secrets/ghcr.token`, `.secrets/tls.key`.
   Compared before those bytes are copied anywhere, and the comparison values
   are not written into the bundle.
2. Any line matching `(?i)authorization:\s*\S+` or `(?i)\bSEC\b\s*[:=]\s*\S+`,
   replaced with the header name and `<redacted>`.
3. Any Fernet token (`gAAAAA` followed by url-safe base64) replaced with
   `<redacted>`. Stored QRadar tokens are Fernet ciphertext with a version
   prefix (`crypto.py:69-71`).
4. Any PEM block whose label contains `PRIVATE KEY`, replaced with
   `<redacted>`. A `CERTIFICATE` block is kept. The LDAP CA and the QRadar CA
   are not secrets. A private key pasted into a log is.

UUIDs are kept. They are instance ids and correlation ids. The exact-value
pass is what removes a SEC token that happens to be a UUID, which is the case
the log filter only partly covers (`logging.py:19-21` requires the line to
mention a secret word first).

### 7.2 The test

OPS-8 adds a unit test that builds a bundle from a fixture directory. The
fixture plants:

- a UUID SEC token in a log line that does **not** contain the word `SEC`
- the same UUID as a `correlation_id` in a watermark row, which must survive
- a Fernet `gAAAAA…` string
- `ENCRYPTION_KEY=` and `POSTGRES_PASSWORD=` lines
- an `Authorization: Bearer …` header
- a `BEGIN PRIVATE KEY` block and a `BEGIN CERTIFICATE` block
- a `consecutive_failures` field, which must survive

The test asserts the planted secrets are absent from the archive bytes and
that `correlation_id`, `consecutive_failures` and `BEGIN CERTIFICATE` are
present. It does not need a database or QRadar.

---

## 8. Runbook outline

`docs/OPERATIONS.md` is written by OPS-9, in Turkish. Commands, log lines,
`doctor` ids, file names, setting names and HTTP statuses stay as they are
here. Each symptom entry has four parts: the symptom, the likely cause, the
command that confirms it, the fix. The Turkish heading for that index is
`Belirtiler`. The other headings OPS-9 uses:

1. `Kurulum` — the clone and `./qradar-dash install`, the summary, the first sign-in
2. `Dizin` — `/admin/directory`, the test button, what a failed test means
3. `Yükseltme` — `upgrade`, what downtime looks like, how to read the state file
4. `Yedek ve geri alma` — `backup`, `restore`, `rollback`, and the sentence that downgrade is not used
5. `Tanı` — `doctor`, `status`, `logs`, `support-bundle`
6. `Belirtiler` — the index below

The entries below are the index, OPS-R1 through OPS-R42 in that order. A
`doctor` line names the id (`runbook: OPS-R30` is entry 30). OPS-9 translates
the prose and copies the commands. It does not invent new causes.

The confirm command is `./qradar-dash doctor` unless a row says otherwise.
`doctor` ids are the ones in §6.

1. **OPS-R1. QRadar rejects the credentials.** Cause: the token is wrong, empty, or
   has a trailing newline that the file reader strips
   (`config.py:747-750`), or the token's role cannot call the endpoint. 401
   and 403 are one sentence (`qradar_rest.py:190-191`, raised at `:355-359`).
   Confirm: `qradar_auth` FAIL with that sentence. Fix: replace
   `.secrets/qradar.sec`, re-run `load-secrets.sh`, re-run `qradar add` inside
   the container (§3.5 step 8). The file and the instance row are two copies
   (`factory.py:25-26` versus `:81-87`). Rotating the file alone leaves
   collection on the old ciphertext.

2. **OPS-R2. `unable to get issuer certificate`.** Cause: the CA file has the
   intermediate and not the root. QRadar sends only the leaf (`tls.py:25-28`,
   `README.md:293-296`). Confirm: `qradar_tls` FAIL, and the openssl command
   in §3.2 fails the same way. Fix: concatenate root then intermediate into
   `.secrets/qradar-ca.pem` and re-run `load-secrets.sh` and `qradar add`.

3. **OPS-R3. `Missing Authority Key Identifier`.** Cause: a Python 3.13+ runtime
   enabling `VERIFY_X509_STRICT` against a QRadar certificate that has an SKI
   and no AKI (`tls.py:38-44`). The image we ship is Python 3.12
   (`backend/Dockerfile:1`), so this appears when the base image moves or when
   someone runs the backend outside the image. Confirm: the exception text,
   while `openssl s_client` with the same CA succeeds (`README.md:332-341`).
   Fix: reissue the console certificate. Until then
   `QRADAR_TLS_ALLOW_MISSING_AKI=true`, which does not turn verification off
   (`tls.py:46-51`). `doctor` WARNs while the flag is on.

4. **OPS-R4. The host reaches QRadar and the container does not.** Cause: a libvirt
   bridge rejecting forwarded traffic (`README.md:343-360`). Confirm: from the
   host, the openssl command works; from `docker compose exec backend`, a TCP
   connect to the same host fails. `qradar_tls` FAIL with a network class.
   The provider maps transport errors to `Could not reach QRadar`
   (`qradar_rest.py:346-351`). Fix: the macvlan override in that README
   section, or a route. Not a firewall change made by the script.

5. **OPS-R5. `QRadar Ariel metric search timed out`.** Cause: the metric search did
   not finish within the two-minute cap (`qradar_rest.py:677-686`). Confirm:
   the phrase in the worker log and a watermark `last_error`. Fix: narrow the
   cadence or the source set. The runbook does not tell the operator to raise
   the cap as a first step. A stuck console must not pin a worker
   (`qradar_rest.py:677-678`).

6. **OPS-R6. `QRadar Ariel metric search was truncated at N rows`.** Cause: the result
   hit the page ceiling and the collector refuses to zero-fill the missing
   sources (`qradar_rest.py:691-699`). Confirm: that phrase in `last_error`.
   Fix: the search has to be split or the source set reduced. Truncation is
   also how WAF and IPS mark a capped breakdown
   (`backend/app/providers/qradar_rest.py` sets `truncated=True` on those
   paths, for example `:1272`). A truncated breakdown is `TRUNCATED` in the
   product, not a silent zero.

7. **OPS-R7. A family shows no data and the UI says it was not collected.** Cause:
   the family flag is off, or the profile is disabled, or monitoring is off.
   WAF: `WAF collection is disabled (WAF_COLLECTION_ENABLED=false)` and the
   three neighbours (`backend/app/waf/profile.py:249-256`). IPS:
   `IPS_COLLECTION_ENABLED` defaults false (`config.py:171`) and the collector
   returns `profile disabled` or `monitoring disabled`
   (`backend/app/collectors/ips_collector.py:313-317`). Confirm: `families_off`
   or the watermark `last_error`. Fix: turn the flag on only after the profile
   is valid. A disabled family on a fresh install is expected (§3.3).

8. **OPS-R8. WAF profile will not collect after an edit.** Cause: the saved version
   has not been validated (`backend/app/waf/profile.py:257-265`). The same shape exists for
   IPS: `current profile version is not validated`
   (`ips_collector.py:318-321`). Confirm: that sentence in the worker log or
   the admin page for the source. Fix: run validation from
   `/admin/waf-sources` or `/admin/ips-sources` and wait until the status is
   valid. Do not collect on the unvalidated mapping.

9. **OPS-R9. Beat is "healthy" and nothing is scheduled.** Cause: the Compose check
   only sees the schedule file (`docker-compose.yml:171-176`). Confirm:
   `celery_beat` FAIL, and `cadence` WARN on every enabled family. Fix:
   `docker compose restart celery-beat`. If the heartbeat task is not in this
   build yet, the confirm command is the file's mtime inside the container
   plus the absence of new task logs.

10. **OPS-R10. `Offense collection is stale for <name>`.** Cause: the offense watermark
    is older than `offense_stale_after_seconds` (default 900) or
    `consecutive_failures` is non-zero (`backend/app/workers/tasks.py:576-608`).
    Confirm: the alert title, and the watermark row. Fix: worker logs for the
    offense collector, then QRadar reachability (symptoms 1–4). A skipped lock
    is not this failure (`backend/app/collectors/offense_collector.py:141-148`).

11. **OPS-R11. The app is up and writes fail, or `doctor` says the head does not match.**
    Cause: migrate did not run, or old workers were still up across a migration
    (`README.md:690-694`). Confirm: `migration_head` FAIL. Fix: stop writers,
    `run --rm migrate` on the image that matches `VERSION`, start writers. If
    the database is **newer** than the image, do not downgrade. Restore the
    backup that matches the image (§5.4).

12. **OPS-R12. The UI loads and every API call fails.** Cause: the browser is calling a
    different origin. Either the image was built without
    `NEXT_PUBLIC_API_BASE_URL` and fell back to localhost (`api.ts:6`), or
    `PUBLIC_ORIGIN` does not match the URL in the address bar
    (`main.py:74-85`). Confirm: the browser's network log shows
    `localhost:8000` or a missing `Access-Control-Allow-Origin`. Fix: rebuild
    or pull an image built with `/api/v1` (§2.3) and set `PUBLIC_ORIGIN` to
    the exact `https` origin.

13. **OPS-R13. A container cannot read a file that is in the checkout.** Cause: SELinux
    `user_home_t` (`deploy/postgres/Dockerfile:3-6`, `load-secrets.sh:4-9`).
    The MCP service bind-mounts the checkout
    (`docker-compose.yml:105-106`), so this is how an enabled `mcp` profile
    fails on an enforcing host. Confirm: `ausearch` or the journal shows
    `denied` for `container_t`, and `getenforce` is `Enforcing`. Fix: do not
    relabel. Keep using `load-secrets.sh`. Leave the `mcp` profile off unless
    the toggles have been baked into an image (deferred, §10).

14. **OPS-R14. Postgres errors with `No space left on device`, or `doctor` disk is FAIL.**
    Cause: `retention_enabled` was false, or it was true and the
    `apply_timescale_policies` task never ran (§3.1). Docker json logs are capped at 20 MB × 5
    (`docker-compose.yml:12-16`) and are not the thing that filled the disk.
    Confirm: `disk` FAIL and `pg_database_size`. Fix: free space, then confirm
    a retention policy exists (`retention_policy` PASS). Deleting the Docker
    log files does not fix a full data volume.

15. **OPS-R15. Watermarks jump, or many sources show `TIMESTAMP_DELAY`, after the host
    was suspended.** Cause: the host clock stepped. Beat is UTC
    (`celery_app.py:26-27`). The delay detector is a real anomaly type
    (`enums.py:133`). Confirm: `qradar_clock` WARN, `timedatectl` not
    synchronized. Fix: enable NTP and wait for the next complete buckets. Do
    not hand-edit watermarks.

16. **OPS-R16. `KeyRotationError` / failed to decrypt.** Cause: `ENCRYPTION_KEY` changed
    and the old key is not in `ENCRYPTION_KEYS_JSON` (`crypto.py:80-88`).
    Confirm: `encryption` FAIL. Fix: put the key that encrypted the rows back
    into the keyring. The backup's `env.bak` is the copy. Do not generate a
    new key to "fix" the error.

17. **OPS-R17. The backend restarts with `Unsafe production configuration`.** Cause:
    one of the startup checks (`config.py:875-914`): `DEBUG`, TLS off, short
    `SECRET_KEY`, missing `ENCRYPTION_KEY`, `QRADAR_PROVIDER=mock`, no token,
    autonomous LLM, a bad `PUBLIC_ORIGIN`, or `AUTH_PROVIDER=oidc`. Confirm:
    the traceback lists the bullets. Fix: the bullet. `LAB_MODE` is a separate
    refusal (`config.py:803-804`).

18. **OPS-R18. `QRADAR_HOST must use https://`.** Cause: the URL has no `https` scheme
    (`config.py:782-788`). The instance registry repeats the rule
    (`instance_registry.py:93-96`). Confirm: the exception. Fix: correct the
    URL. There is no plaintext setting.

19. **OPS-R19. `WAF_METRIC_RETENTION_DAYS must exceed BASELINE_LOOKBACK_DAYS`.** Cause:
    both WAF collection and WAF retention are on, and retention is not longer
    than the 28-day lookback (`config.py:825-829`). The novelty check is the
    neighbour (`:841-846`). Confirm: the startup error. Fix: raise retention
    or lower the history. Do not turn retention off to get the process to boot
    unless the operator accepts unbounded WAF growth.

20. **OPS-R20. The frontend container exits with `getaddrinfo EAI_AGAIN`.** Cause:
    `HOSTNAME` was left as the container id (`frontend/Dockerfile:20-24` sets
    it to `0.0.0.0` for this reason). Confirm: that error in the frontend log.
    Fix: keep `HOSTNAME=0.0.0.0`. A rebuilt image that drops the line brings
    the failure back.

21. **OPS-R21. One analyst's traffic makes the API return 429 for everyone.** Cause:
    `FORWARDED_ALLOW_IPS` does not include the proxy, so every client shares
    the bridge address (`README.md:650-660`). The default limit is 120/minute
    (`config.py:660`). Confirm: backend logs show one client address for
    distinct browsers, and `FORWARDED_ALLOW_IPS` is empty or `127.0.0.1`.
    Fix: set it to the proxy address in §3.4. Do not set `*`.

22. **OPS-R22. A signed-in browser gets `Cross-site request refused`.** Cause: `Origin`
    is not `PUBLIC_ORIGIN` (`backend/app/security/csrf.py:53-56`). Typical
    case: the wizard stored `https://soc.example` and the operator opened
    `https://192.0.2.10` or an `http` URL. The cookie is `Secure` in
    production (`backend/app/services/auth_sessions.py:67-74`), so an `http`
    origin also drops the cookie. Confirm: the 403 body contains that
    sentence. Fix: use the hostname from the certificate.

23. **OPS-R23. Sign-in returns 503 `The directory server is unavailable`.** Cause: the
    LDAPS handshake or the bind did not complete
    (`backend/app/services/auth_login.py:44-46`, `:248-254`). The test button
    on `/admin/directory` returns the structured TLS reason. Common cases the
    runbook names, from the code: the CA does not chain
    (`ldap_config.py:182-195`), the certificate is expired or not yet valid
    (`:205-208`), the hostname does not match (`ldap_bind.py:89-98` keeps the
    `doesn't match` clause), port 636 is blocked (a transport error in
    `_TRANSPORT`, `ldap_bind.py:35-44`), `start_tls` combined with `ldaps://`
    (`ldap_config.py:110-115`), `ldap://` without StartTLS (`:114-115`).
    Confirm: the test result on the Directory page, not a guessed packet
    capture. Fix: the reason the test names. The installer does not collect
    these values.

24. **OPS-R24. Sign-in says `Invalid username or password` and the directory is fine.**
    Cause: the HTTP body is always that sentence, including when the directory
    is turned off (`auth_login.py:43`, `:217-223`) and when the password is
    wrong (`:235-246`). Confirm: the audit reason is `ldap_disabled` or
    `invalid_credentials`, not the message on screen. Fix: if the directory
    should be on, enable it and use the test button. Five failures lock the
    username for 15 minutes (`config.py:652-653`). A lock is a failed sign-in
    of a known local or existing user, not a 503.

25. **OPS-R25. A directory user signs in and every page is forbidden.** Cause: a first
    successful bind creates the user when no row exists
    (`auth_login.py:257-265`). That `User()` call sets no roles. `User.roles`
    is the relationship at `identity.py:98-100`, through `user_role`
    (`identity.py:34-41`), and it stays empty. Roles are not read from AD
    (D-19). Confirm: `/admin/users`
    shows the user with no role, and `/me` succeeds. Fix: an admin assigns a
    role. There is no AD group to fix.

26. **OPS-R26. The last administrator cannot be disabled.** Cause: the last active
    `admin:*` holder is protected (AUTH-1). Confirm: HTTP 409 from the users
    API. Fix: create another admin first. `create-admin` is the break-glass
    for the case where that admin's password is lost. It resets the password
    (`backend/app/cli/users.py:59-69`).

27. **OPS-R27. The UI will only change the password.** Cause: `must_change_password` is
    set, which `create-admin` always sets (`backend/app/cli/users.py:61`). Confirm: the
    account menu or `/me`. Fix: change the password. This is the first-login
    state, not a fault.

28. **OPS-R28. `pagination stopped at the page ceiling`.** Cause: a list endpoint hit
    `max_pages` (`qradar_rest.py:475-478`). Offense and rule page caps default
    to 20 (`config.py:251`, `:268`). Confirm: that log line. Fix: raise the
    matching `*_MAX_PAGES` only after confirming the console is larger than
    the cap. The inventory is incomplete until then. The UI must not be told
    the missing rows are zero.

29. **OPS-R29. `offense collection skipped; another worker holds the lock`.** Cause: a
    second worker lost the advisory lock (`offense_collector.py:141-148`).
    Confirm: that log line and a later successful run. Fix: none if a later
    run succeeds. If every run skips, two beats are scheduled. `status` should
    show one `celery-beat`.

30. **OPS-R30. A search family is late.** Cause: the last run is older than
    cadence + settle + 60 s (`derive.ts:73-84`). Settle defaults to 120 s
    (`config.py:158`). Confirm: `cadence` WARN, and the Scheduled searches
    widget (`HealthCollection.tsx:3-5`). The top-bar collection signal is only
    `PHASE_A_VOLUME` (`frontend/src/components/shell/ConnectionStatus.tsx:130-142`),
    so a late WAF family does not move that pill. Fix: worker logs for that
    family, then symptoms 5, 6, 7 and 9.

31. **OPS-R31. `network qradar-vmnet not found`.** Cause: a stale macvlan after a
    daemon restart (`README.md:559-561`). Confirm: compose names that network
    and `docker network ls` does not. Fix: the README's recreate command.
    A production install that never created the override will not see this.

32. **OPS-R32. `volume qradar-observability-secrets not found`.** Cause: the volume is
    `external: true` so `down -v` cannot delete it, and it has not been
    created yet (`docker-compose.yml:217-221`). Confirm: `docker volume ls`.
    Fix: `./deploy/load-secrets.sh`.

33. **OPS-R33. Migrate is refused or the new columns are missing while old containers
    are still up.** Same mechanism as symptom 11. Confirm: `docker compose ps`
    shows a worker up during `run migrate`. Fix: stop the writers first, as
    `upgrade` does.

34. **OPS-R34. Someone ran `alembic downgrade` and rows are gone.** Cause: downgrade
    drops the revision's tables (`README.md:704-706`). Confirm: `migration_head`
    moved backwards and a table the previous version had is gone. Fix:
    `restore` of the pre-upgrade backup. Do not run downgrade to "finish" the
    rollback.

35. **OPS-R35. `password authentication failed for user qradar_obs` after `.env` was edited.**
    Cause: `POSTGRES_PASSWORD` is applied when the data directory is created
    (`docker-compose.yml:51-57`). Confirm: the Postgres log says authentication
    failed and the volume `postgres_data` already existed. Fix: put the old
    password back, from `env.bak`. Changing the password inside Postgres is a
    later, deliberate `ALTER ROLE`, followed by an `.env` edit to the same
    value. The script does not do this in the two-week cut.

36. **OPS-R36. `/api/v1/health/live` is 200 and `/ready` is not.** Cause: the process is
    up and the database is not (`health.py:13-24`). Confirm: those two URLs.
    Fix: Postgres health, then symptom 14 or 35.

37. **OPS-R37. Custom rules never evaluate.** Cause: `CUSTOM_RULES_ENABLED` defaults
    false and the beat task is a no-op while it is false
    (`celery_app.py:175-179`, `config.py:545`). Confirm: `families_off`. Fix:
    enable it from the setting once the operator wants the engine. The same
    shape is `RULE_MATCH_COLLECTION_ENABLED` (`config.py:276`,
    `celery_app.py:100-106`).

38. **OPS-R38. `docker pull` from `ghcr.io` returns denied.** Cause: the repository is
    private and `.secrets/ghcr.token` is missing, wrong, or not
    `read:packages` (§2.5). Confirm: `docker login` with that file, then pull
    one image. Fix: a new token. `--offline` is the path when the registry
    cannot be reached at all.

39. **OPS-R39. The Directory page refuses a CA the operator pasted.** Cause: the PEM
    contains a private key (`ldap_config.py:188-189`), is not a certificate
    (`:193-195`), or is expired (`:205-208`). Confirm: the 422 lists every
    reason (AUTH-1). Fix: paste the CA certificate only.

40. **OPS-R40. An empty directory password does not even try.** Cause: an empty
    password is refused before a socket is opened (`ldap_bind.py:116-124`).
    Confirm: the test result says `an empty password is refused`. Fix: type
    the password. This is the test button, not a stored service account.
    There is no service account (AUTH-1).

41. **OPS-R41. The worker healthcheck flaps with `inspect ping`.** Cause: the check
    addresses `celery@$HOSTNAME` (`docker-compose.yml:156`). A hostname that
    does not match the Celery node name fails the ping. Confirm: `doctor`
    `celery_workers` versus `docker inspect` health. Fix: the node name and
    the healthcheck have to use the same name. OPS-7 makes `doctor` use
    `inspect ping` without a hardcoded node when the filtered ping returns
    nothing, and report both results, so a naming mismatch is a WARN with a
    cause rather than a false FAIL of the whole worker set. If an unfiltered
    ping returns a node, the workers are up.

42. **OPS-R42. Login works once from the host and not from a browser.** Cause: the
    session cookie is `Secure` and `SameSite=Strict`
    (`auth_sessions.py:67-74`). A browser on a non-HTTPS origin will not store
    it. Confirm: the browser shows no `qd_session` cookie after a 200 from
    `/api/v1/auth/login`. Fix: use the `https` public origin. Loopback HTTP is
    not the production UI.

---

## 9. Monitoring the monitor

What an operator can already see, without `doctor`:

| Surface | What it answers | Limit |
|---|---|---|
| Top bar, three signals (`ConnectionStatus.tsx:1-16`) | Backend readiness (`/health/ready`), the QRadar instance status from the last inventory sync (`backend/app/services/inventory_sync.py:51-67`, statuses at `enums.py:657-661`), and the last `PHASE_A_VOLUME` run | The collection signal is one family (`ConnectionStatus.tsx:142`). A 403 on overview or cadence hides that signal rather than painting it red (`:128`, `:163`). `UNKNOWN` is not green (`:10-12`). |
| Scheduled searches widget (`HealthCollection.tsx:3-5`) | Every search family's cadence, last run and lateness (`derive.ts:72-94`) | A family that has never run is an em dash, not "on time". Empty data says the cadence migration may not have been applied (`HealthCollection.tsx:31-36`). |
| Stale rule used by dashboard widgets (`frontend/src/widgets/state.ts:13-29`) | A tile is stale when its `asOf` is older than twice the cadence, or 4 h when the cadence is unknown | This is display staleness, not a page. |
| Detection coverage and rule health (`frontend/src/components/catalog/derive.ts:21-57`) | Whether coverage was ever evaluated, and rule-health counts other than healthy | A total of zero techniques is "never evaluated", not 0% covered (`:24-27`). |
| Offense-collection alert (`tasks.py:597-601`) | The offense collector has stopped completing | One collector, not the Ariel families. |
| Container healthchecks | Process up, Postgres accepts connections, Redis answers `PING`, the worker answers a ping, the beat created its schedule file, the frontend probe returns | Beat's check does not prove dispatch (`docker-compose.yml:171-176`). |

Daily, in this order, which the runbook's `Tanı` section copies:

1. `./qradar-dash status` — versions, health, age of the last backup.
2. `./qradar-dash doctor` — the table in §6. Anything FAIL is acted on the same day. WARN is read.
3. The Scheduled searches widget, because it is the only screen that shows every family.
4. Free space. `doctor` already FAILs at 90%. The daily look is what notices a climb toward that line while retention is still wrong.
5. Certificate dates. `qradar_tls` WARNs inside 30 days. The Directory test shows the LDAP certificate's `not_after` (`ldap_bind.py:48-51`).

The product does not page the operator by itself except for the alerts it already creates (offense staleness, search failures after the configured count, the behavioural alerts). Those land in the notification channels the operator configured. Empty webhook settings mean the channel is off (`.env.example:237-241`). `doctor` is the check that does not depend on a channel.

No Prometheus exporter in this cut. The surfaces above plus `doctor` are the daily watch. An exporter is polish (§10).

---

## 10. Task split

Target: the safety cut done by 2026-10-30, starting 2026-10-17. Each task is one agent. The orchestrator reviews OPS-4, OPS-5 and OPS-6 before merge, because a bug there destroys a database. No task commits except as the orchestrator already does. No task runs anything against QRadar. Integration tests use the `TEST_DATABASE_URL` the orchestrator assigns.

| Task | When | Agent | Depends on | What |
|---|---|---|---|---|
| OPS-1 | 10-17 | Grok | — | `VERSION`, the CI equality check, `GET /api/v1/version`, a `version` field on the frontend health probe, the account menu and the sign-in page. |
| OPS-2 | 10-17 | Grok | — | pip-tools locks, Dockerfile and CI install from the lock, `mypy` no longer hidden by `\|\| true`. |
| OPS-3 | 10-18, 10-19 | Grok | OPS-1, OPS-2 | `release.yml`, GHCR push, frontend build arg, wget in the frontend runner, prod compose `image:` keys, redis digest pin. |
| OPS-4 | 10-20 to 10-22 | Grok | OPS-3 for the pull path. The script can land `--build` first. | `./qradar-dash` preflight, wizard, `--non-interactive`, `--offline`, `--build`, `--external-proxy`, secrets, migrate, `create-admin`, `has-admin`, `qradar add`, proxy image, pinned edge IP, summary, idempotent re-run. |
| OPS-5 | 10-23, 10-24 | Grok | OPS-4 | backup, verify-restore, retention of backups, disk guard, systemd timer, and the daily task that calls `apply_policies`. |
| OPS-6 | 10-25, 10-26 | Grok | OPS-3, OPS-5 | `upgrade` and `rollback`, state file, Redis flush, automatic rollback triggers, rehearsal that records downtime. |
| OPS-7 | 10-27 | Grok | OPS-1, OPS-4 | `python -m app.cli.doctor`, beat heartbeat, host wrapper, exit codes. |
| OPS-8 | 10-28 | Grok | OPS-7 | `support-bundle` and the fixture test in §7.2. |
| OPS-9 | 10-29 | Grok | OPS-4 through OPS-8, so the commands exist | `docs/OPERATIONS.md` in Turkish from §8. Commands copied, not rewritten. |
| OPS-10 | 10-30 | orchestrator | OPS-4 at minimum; OPS-6 if it has landed | Fresh-VM install test (§10.1). |

OPS-1 and OPS-2 run in parallel. OPS-9 can be drafted from §8 before OPS-8 merges and corrected the same day if a command name moved. Nothing else overlaps on the same files: OPS-4 owns `qradar-dash` and the prod compose file, OPS-5 adds backup subcommands to that script, OPS-6 adds upgrade and rollback. They are sequential on purpose.

### 10.1 OPS-10 acceptance

A clean RHEL/Rocky/Alma 9 or Ubuntu 22.04/24.04 VM, x86_64, with only Docker Engine, the Compose v2 plugin, git and a TLS certificate. SELinux left at its default. No QRadar token is required for the count if the test uses a mock only when the orchestrator says so. The production-shaped test uses a lab token the orchestrator already holds, read by the Sonnet path the planner already uses for lab tokens, and this design does not change that rule.

Record:

- The commands the operator typed after the VM had Docker. The target is `git clone` and `./qradar-dash install --non-interactive <answers>`.
- Wall minutes from the start of `install` to the summary.
- Whether the summary's URL answered `/login`, whether the break-glass password signed in, and whether a second `install` left that password alone.

A failure is a report, not a quiet fix inside OPS-10.

### 10.2 Each task's acceptance, short

- **OPS-1.** `GET /api/v1/version` is unauthenticated and returns the three fields. The frontend probe gains a `version` field beside `status` (today it returns only `status`, `frontend/src/app/api/health/route.ts:1-3`). The two UI lines render the API version. A test covers a failed fetch. `VERSION`, `pyproject.toml` and `package.json` match. ruff, mypy, the new unit test.
- **OPS-2.** A second `pip install` from the lock in a clean environment does not resolve newer versions. The image builds. The gates stay green. `mypy app` fails the workflow when mypy fails.
- **OPS-3.** A tag build produces the six images. The frontend bundle contains `/api/v1` and does not contain `http://localhost:8000/api/v1`. `npm ci` in the image install matches the lock, so the `postcss` override is the one T-117 tested.
- **OPS-4.** Preflight fails on a missing Docker and does not write `.env`. A re-run does not rotate `ENCRYPTION_KEY` or call `create-admin` again. `seed` is not invoked. The script never passes `:z` or `:Z`. Shellcheck is clean. A fixture test of the wizard's non-interactive parser covers a missing token file and a non-https QRadar URL.
- **OPS-5.** Round-trip: dump a migrated database, restore into a new database, `alembic_version` and a known row survive. The disk guard refuses when the fixture filesystem is small. `apply_policies` runs from the beat task and a test sees a retention job. The timer unit is installed by the script and is not enabled in CI.
- **OPS-6.** A migrate failure on a fixture restores the previous digest and the previous row count. A health timeout does the same. A QRadar `FAIL` does not. Downtime is written into the OPS-6 report.
- **OPS-7.** Every id in §6 has a test that forces PASS and a test that forces FAIL or WARN. Output fixtures contain a planted token and the test asserts it is absent. No test opens a socket to a QRadar host.
- **OPS-8.** The §7.2 fixture test. Breaking the scrub fails the test; that failure is confirmed and the scrub restored, per `AGENTS.md` §4.
- **OPS-9.** Every symptom id OPS-R1 through OPS-R42 appears. A Turkish paragraph and an English command. A second reader (the orchestrator) checks five commands against this document.
- **OPS-10.** The numbers in §10.1, written in `docs/tasks/OPS-10.report.md`.

### 10.3 After 2026-10-30

Not in the safety cut:

- **OPS-11.** Image signing (cosign) and an SBOM. Pull still verifies the digest recorded in the state file, which is the rollback property. Signing is the next step.
- **OPS-12.** Redis `requirepass` and a review of whether loopback 3000/8000 should stay published. Both are internal today (`docker-compose.yml:70-80` has no host port for Redis).
- **OPS-13.** Bake `deploy/mcp/feature_toggles.json` and the MCP config into an image so the profile can be enabled without a bind mount. POST and DELETE stay off (`deploy/mcp/feature_toggles.json:3-7`). Not required while the profile stays off.
- **OPS-14.** A copy of the backup directory off the host.
- **OPS-15.** An exporter for the `doctor` checks. §9 is the watch until then.
- **OPS-16.** `ALTER ROLE` for a Postgres password change, if it is ever needed. Symptom 35's fix is to put the old password back.

---

## 11. Open questions

These do not block OPS-1. The default is what the tasks will do if the user does not say otherwise.

1. **First tag.** `VERSION` stays `0.1.0`, which is what the tree says today (`backend/pyproject.toml:3`). The planner has mentioned a `v1.0.0` tag as a later, deliberate act. Say if the first GHCR release should be `1.0.0` instead. Default: `0.1.0`.

2. **Bundled proxy.** The installer terminates TLS itself (§3.4). Say if an existing load balancer must be the default instead. Default: bundled proxy, with `--external-proxy` for the other case.

3. **Off-host backups in this cut.** §5 keeps backups on a directory the operator names, and recommends a second filesystem. A copy to another machine is OPS-14. Say if OPS-14 has to move into the two-week cut. Default: it stays after 2026-10-30.

Language and who operates are decided (D-19). The registry is decided (private-shaped, `ghcr.io` reachable). LDAP at install time is decided (not asked).

---

## Appendix A. Settings the operator meets

The wizard writes the ones in §3.3. Everything else stays at the code default.
Detector thresholds (the `anomaly_*`, `waf_spike_*`, `ips_*` floors) are
detection policy. Changing one is a release-notes event, not an install
question. They live in `backend/app/core/config.py` from the Ariel block
(`:122`) through the custom-rule block (`:558`).

Settings an operator may still edit after install, with the effect:

| Setting | Default | Why an operator touches it |
|---|---|---|
| `RETENTION_LOG_SOURCE_METRIC_DAYS` | installer writes 90 | Raise toward 400 when the disk in §3.1 allows a year of Phase A. |
| `COMPRESSION_AFTER_DAYS` | installer writes 7 | Already the right idea. Lower only if disk is tight and the CPU cost of compression is acceptable. |
| `WAF_COLLECTION_ENABLED`, `IPS_COLLECTION_ENABLED`, `RULE_MATCH_COLLECTION_ENABLED`, `CUSTOM_RULES_ENABLED` | false | Turn on when the matching profile or rule is ready. Symptoms 7 and 37. |
| `QRADAR_TLS_ALLOW_MISSING_AKI` | false | Symptom 3 only. |
| `FORWARDED_ALLOW_IPS` | installer writes the proxy address | Symptom 21 if the proxy address changes. |
| `PUBLIC_ORIGIN` | installer writes `https://<name>` | Symptom 22 if the name changes. |
| `LOG_LEVEL` | `INFO` (`config.py:80`) | `DEBUG` is refused in production (`:875-876`). |
| Notification block | empty, channels off (`.env.example:237-252`) | Optional. Webhook URLs and the SMTP password are secrets and stay out of logs (`redaction.py:13-16`). |

`apply_policies` only covers `log_source_metric`, `search_result_metric`,
`rule_metric` and `offense_snapshot` (`timescale.py:23`). WAF, IPS, catch-all,
geo and custom-rule retention are the daily deleter tasks already on the beat
(`celery_app.py:135-184`). Those tasks exist. The Timescale policy task does not,
until OPS-5.

## Appendix B. What install does not turn on

- MCP (`docker-compose.yml:121`, `MCP_ENABLED` default false at `config.py:632`).
- LLM (`config.py:665-669`).
- OIDC (`config.py:907-911`).
- `LAB_MODE` (`config.py:800-804`).
- A mock provider (`config.py:883-884`).
- Any QRadar write. The provider interface has no create, update or delete.
  The MCP toggles, if the profile is ever started, keep POST and DELETE off
  (`deploy/mcp/feature_toggles.json:3-7`).
