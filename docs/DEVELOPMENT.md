# Development

This guide covers local development, dependency updates and the test suites. For deployment and
operator procedures, see [`INSTALL.md`](INSTALL.md) and the Turkish runbook,
[`OPERATIONS.md`](OPERATIONS.md).

## Repository layout

```text
backend/app/
  api/          FastAPI routers and dependencies
  core/         configuration, database, logging and rate limiting
  models/       SQLAlchemy models
  schemas/      Pydantic request and response models
  services/     collection and domain services
  providers/    direct REST, MCP and mock QRadar providers
  collectors/   bounded metric collectors
  anomaly/      baselines, detector capabilities and the anomaly engine
  alerts/       alert lifecycle, routing and notifiers
  workers/      Celery application and tasks
  repositories/ data access
  security/     authentication, authorization, sanitization and redaction

frontend/src/
  app/          Next.js App Router pages, layouts and styles
  components/   shared product components
  lib/          typed API client and utilities
  widgets/      dashboard registry, frames and widget implementations
```

The backend uses FastAPI, SQLAlchemy 2 and Pydantic 2. PostgreSQL with TimescaleDB stores the
application data, Redis backs Celery, and the frontend uses Next.js with TypeScript and Apache
ECharts.

## Backend without Docker

Use Python 3.12, which is the interpreter used by the image and CI:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
pip install --no-deps -e .
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export QRADAR_PROVIDER=mock
uvicorn app.main:app --reload
```

The mock provider is deterministic and does not need QRadar. Production configuration refuses the
mock provider, debug mode, missing REST credentials, disabled TLS verification, and autonomous LLM
actions.

## Frontend

```bash
cd frontend
npm ci
INTERNAL_API_BASE_URL=http://localhost:8000/api/v1 \
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

Open <http://localhost:3000>. In development, the backend permits the local frontend origin. A
production image embeds `NEXT_PUBLIC_API_BASE_URL` during the build; runtime changes do not replace
that value.

## Background work

Celery and Redis are the default scheduler and queue. Task orchestration lives in async functions
with thin Celery wrappers, keeping the orchestration usable independently of the transport.

Celery Beat drives inventory and metric collection, offense and rule processing, detection
coverage, anomaly evaluation and evidence, scheduled searches, notifications, baseline rebuilds,
WAF and IPS collection, catch-all and geography collection, property watches, custom rules, and
bounded retention. Every QRadar search family defaults to a five-minute cadence. Bucket width and
search cadence are separate controls; see [`SEARCH-CADENCE.md`](SEARCH-CADENCE.md).

WAF profiles can also be administered from the backend CLI:

```bash
python -m app.cli.waf --help
```

The commands include setting, validating, enabling, disabling and inspecting profiles, plus a
bounded rejection probe. IPS profiles are administered through the API and UI.

## Backend tests

Run commands from `backend/` with the worktree first on `PYTHONPATH`:

```bash
ruff check app tests
mypy app
PYTHONPATH=. ENCRYPTION_KEY=<fernet-key> pytest -m "not integration"
```

`pytest -n auto` (pytest-xdist) runs the unit suite on every core; CI does, and `scripts/quick-check.sh`
does. Integration files stay serial: the schema fixture drops tables.

`pyproject.toml` already passes `-q`; adding another hides pytest's summary line. `make test`
runs that unit selection and does not add `-q`.

`make lint` runs `ruff check app tests`. `make mypy` runs `mypy app`.

Integration tests require PostgreSQL with TimescaleDB and a dedicated database. `make
test-integration` starts the compose test database with `docker compose up -d --wait` and
refuses to run when `TEST_DATABASE_URL` is unset. It does not invent a URL. Pass the database
you were given:

```bash
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5433/behavior_analytics_test \
  make test-integration
```

The same suite from `backend/`:

```bash
PYTHONPATH=. \
ENCRYPTION_KEY=<fernet-key> \
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/behavior_analytics_test \
pytest -m integration tests/integration
```

Never run two test processes against the same test database. The schema fixture recreates tables,
so concurrent runs corrupt one another's assumptions. `make db-test-up` and `make db-test-down`
start and remove the compose test database. `make check` runs `alembic upgrade head` and
`alembic check` against that database.

`alembic` reads `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD` and
`POSTGRES_DB` through settings. Point those at the test database. `TEST_DATABASE_URL` is not
read by Alembic:

```bash
cd backend
POSTGRES_HOST=localhost POSTGRES_PORT=5433 POSTGRES_USER=test POSTGRES_PASSWORD=test \
POSTGRES_DB=qradar_obs_test \
ENCRYPTION_KEY=<fernet-key> \
alembic upgrade head
alembic check
```

The backend suites cover provider behavior, TLS enforcement, encrypted storage, production
hardening, collectors, robust baselines, detector fail-closed paths, alert fingerprints,
notification routing, scheduling, API guards and database integration. QRadar responses are mocked
in automated tests, and `MockNotifier` prevents external delivery.

## The CI gate

`.github/workflows/ci.yml` runs the backend gate in parallel jobs, each with its own time limit:

| Job | What it runs | Limit |
| --- | --- | --- |
| `changes` | decides from the changed paths which of the jobs below run (see "Skipped jobs") | 5 min |
| `shellcheck` | `shellcheck -x --severity=info` over `qradar-dash`, `deploy/*.sh` and `scripts/*.sh`, from a hash-pinned wheel; always runs | 5 min |
| `backend-static` | the version check, `ruff check app tests scripts`, `mypy app`, `alembic upgrade head`, `alembic check`; always runs | 15 min |
| `backend-unit` | every test that is not marked `integration`, without a database, on every core (`pytest -n auto`) | 15 min |
| `backend-integration` | the integration suite in twelve shards, each with its own database, serial inside the shard | 35 min |
| `frontend` | `npm ci`, lint, typecheck, build (with the `.next/cache` restored) | 20 min |
| `frontend-test` | `npm ci`, vitest | 20 min |

The twelve shards show up as `backend-integration (shard 3/12)`. The suite holds about 70 minutes of
test time and a shard costs about one minute of set-up (service container, checkout, pip), so twelve
shards put the slowest at about six minutes of tests. The ceiling in `ci_shard.py` is fifteen
(`MAX_SHARDS`): see "Concurrent jobs" below. Changing the count means editing four places, which
`test_ci_shard.py` checks agree: the matrix, the job name, `--of`, and `DEFAULT_SHARDS` in
`ci_shard.py`; then refresh the weights. Together the unit job and the shards run every test in the
repository exactly once: the shards run everything marked `integration`, the unit job everything
else.
`backend/tests/unit/test_ci_shard.py` reads the commands out of the workflow and fails if a test
runs in no job or in two.

`backend/scripts/ci_shard.py` splits `tests/integration` into the shards. The split is a greedy
longest-processing-time assignment over the per-file durations in
`backend/tests/integration/shard_weights.json`, so the shards weigh the same and finish together.
The weights come from CI's own numbers: every shard writes a JUnit XML report and uploads it as the
artifact `integration-durations-shard-<n>` (test names, class names, times and failure text only —
no secret exists in the job's environment for a failure text to carry). Refresh the weights from
those artifacts when the integration suite changes shape (a new file, a much slower test):

```bash
gh run download <run-id> --pattern 'integration-durations-shard-*' --dir <dir>
cd backend
python scripts/ci_shard.py --weights-from <dir>/*/*.xml > tests/integration/shard_weights.json
```

The command prints the new weights on stdout and, on stderr, the shard seconds the old and the new
weights predict from the measured times — read that table before keeping the file. A file the
reports do not mention is left out and counts as the median of the known weights, so a missing
entry is harmless but unbalanced.

The fallback, when no CI run is at hand, is one local run; those seconds come from one machine, and
only their ratios matter:

```bash
cd backend
PYTHONPATH=. ENCRYPTION_KEY=<fernet-key> \
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/behavior_analytics_test \
pytest -rs -m integration --durations=0 --durations-min=0
```

Sum the setup, call and teardown seconds of each file in that report and write them into
`backend/tests/integration/shard_weights.json` as `{"tests/integration/test_x.py": seconds, ...}`.

### Concurrent jobs

One full run starts 18 jobs: `changes`, `shellcheck`, `backend-static`, `backend-unit`, 12
integration shards, `frontend` and `frontend-test`. GitHub documents 20 concurrent jobs for a
Free plan account; this repository is public and owned by a personal account, and the plan itself
is not readable through the API, so 20 is the documented figure, not a measured one. Two
overlapping runs (a `main` push during a merge-h run, a release on top of a branch run) ask for
36: the first 20 jobs start and the rest wait in the queue until a slot frees; nothing fails, but
the later run's wall time grows by up to a shard's length. A docs-only run needs 3 jobs and waits
behind them too. Do not raise the shard count: at 15 shards one run alone reaches 20.

### Skipped jobs and concurrency

`backend/scripts/ci_changes.py` (job `changes`) decides, for a push to a branch other than `main`,
from the files between the push's `before` commit and its head. `main`, tags, pull requests,
dispatched runs and the release's `workflow_call` always run every job, as does a new branch, a
`before` commit that cannot be fetched, and an empty diff.

| Changed files | Runs |
| --- | --- |
| only documents (`docs/`, `*.md`, `LICENSE`, `NOTICE`) | `shellcheck`, `backend-static` |
| only `frontend/src/` or `frontend/public/` | `shellcheck`, `backend-static`, `frontend`, `frontend-test` |
| only `backend/app/`, `backend/tests/` or `backend/alembic/` | `shellcheck`, `backend-static`, `backend-unit`, `backend-integration` |
| anything else (workflows, `scripts/`, `qradar-dash`, `deploy/`, requirements, `pyproject.toml`, `frontend/messages/`, Dockerfiles, `VERSION`, `docs/releases/`) | everything |

A file that a test of the other side mentions by name counts for that side too (a unit test reads
`docs/OPERATIONS.md`; a frontend test reads `backend/app/security/rbac.py`). `main` has no branch
protection, so a skipped job blocks nothing. 
On `agent/merge-h` the base is the merge base with `origin/feat/phase-ce` instead (the
`changes` job fetches the full history without file contents, about 4 s), so every change not yet
on feat gets its jobs; if that ref or merge base is unavailable, everything runs. On other
branches a docs-only push after a red code push is green: the diff base is the previous push.

A newer push to a branch cancels the older run, except on `main`, tags, pull requests and
`agent/merge-h`: those queue (GitHub keeps one waiting run per branch, so a third push replaces
the waiting one).

### Local checks

| Script | When | What |
| --- | --- | --- |
| `scripts/merge-check.sh` | after a merge into `agent/merge-h`, before its push | about a minute: no conflict markers, one migration head with rising ids, the English catalogs parse, `ruff`, `mypy`, cold imports of the five entry points. Exits non-zero on any failure |
| `scripts/quick-check.sh` | CI is down, or before a risky push | the above plus the artifact check, the whole unit suite with `-n auto`, the integration files you name or your branch changed, and the frontend type check and tests |

```bash
export QRADAR_VENV=/path/to/venv          # default: backend/.venv, else python3 on PATH
scripts/merge-check.sh
scripts/quick-check.sh -d postgresql+asyncpg://user:pass@localhost:5433/mine_only \
  backend/tests/integration/test_auth_api.py
scripts/quick-check.sh -b origin/main -d "$TEST_DATABASE_URL"   # files changed since main
```

The database for `quick-check.sh` must be yours alone: the integration schema fixture drops
tables. Without one, the integration step is skipped and says so. The unit suite is safe under
`pytest -n auto` (xdist); the integration suite is not, and runs serially inside each shard.

## Frontend tests

```bash
cd frontend
export PATH="$HOME/.local/node/bin:$PATH"
npm ci
npm audit --omit=dev
npm run lint
npm run typecheck
npm test
npm run build
```

Server Components are tested by awaiting their page functions and rendering the results against a
stubbed API client. Chart tests stub ECharts because jsdom has no canvas; they assert registration,
lifecycle and series data, including that unobserved intervals remain gaps rather than zeros.

UI changes also require deterministic screenshots at 1440×900, 1280×800 and 1024×768. The
normative product rules are in [`PRODUCT-UX-GUIDELINES.md`](PRODUCT-UX-GUIDELINES.md).

## Dependency locks

`backend/requirements.txt` is the hashed production lock. `backend/requirements-dev.txt` adds the
development extra while remaining constrained by the production lock. The image and CI install
with `--require-hashes`.

When dependencies change, edit `backend/pyproject.toml`, regenerate both locks together in a
secret-free scratch directory using Python 3.12, and run lint, type checking and unit tests before
submitting the change. Do not mount the repository or a secrets directory into a dependency-build
container.

The enforced formatter policy is deliberate: run `ruff check`, but do not run `ruff format` over
the repository.

## Base image pins

Every image the product runs is pinned by digest, tag kept for readability:

| Image | Where the pin lives |
| --- | --- |
| `python:3.12-slim`, `node:22-alpine` | the `FROM` lines of `backend/Dockerfile`, `frontend/Dockerfile` |
| `timescale/timescaledb:2.17.2-pg16` | `deploy/postgres/Dockerfile` |
| `nginx:1.27-alpine` | `deploy/proxy/Dockerfile` |
| `alpine:3.20` (the secrets helper) | `SECRETS_HELPER_IMAGE` in `deploy/load-secrets.sh` — the only place it is written; `qradar-dash` reads it from there |
| `redis:7.4-alpine` | `docker-compose.prod.yml` |

To bump a base image, print the new multi-arch index digest and edit the pin (keep the tag):

```bash
docker buildx imagetools inspect nginx:1.27-alpine   # the "Digest:" line
```

A bump is a reviewed edit like any other. Nothing rebuilds a base silently: the release workflow's
`redis` job (`The pinned digests still resolve` step) runs `docker buildx imagetools inspect` on
every pin above, so a digest that no longer resolves — an upstream deletion or rename — fails the
release instead of building from whatever the bare tag points at that day. An unpinned `FROM` never
gets that far: `backend/tests/unit/test_pinned_images.py` fails the unit gate, and the secrets
helper's argv is pinned by `backend/tests/unit/test_load_secrets_sh.py` and the
`test_qradar_dash_backup.py` tests.

## Adding a migration

Migrations are additive and must implement both upgrade and downgrade. Verify a fresh upgrade, a
downgrade and upgrade round trip, and `alembic check`. The Timescale hypertable registry is
`backend/app/models/__init__.py`; migrations create hypertables only after creating their ordinary
tables.

Do not run an old backend or worker while applying a migration that adds required columns. The
operator upgrade command stops all writers before migration.

## Provider boundary

All product code depends on `QRadarProvider` and declared capabilities. The direct REST provider
owns deterministic collection and the Ariel lifecycle. The MCP provider is limited to read-only
inventory and cannot execute AQL. The mock provider supplies in-memory data for development and
tests.

New provider methods must remain read-only. Never add create, update or delete operations against
QRadar, and never execute frontend-supplied AQL. Stored searches are validated and versioned before
the scheduler can execute them.

## Synthetic telemetry lab

The optional lab generator is manual and isolated from the application. The backend, worker,
scheduler, Compose stack and normal tests never start it. Use the
[`LAB-SYNTHETIC-TELEMETRY.md`](LAB-SYNTHETIC-TELEMETRY.md) guide and review the sanitized
[`LAB-DEMO-RESULTS.md`](LAB-DEMO-RESULTS.md) before running it.
