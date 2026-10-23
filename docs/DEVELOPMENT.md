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

`pyproject.toml` already passes `-q`; adding another hides pytest's summary line.

Integration tests require PostgreSQL with TimescaleDB and a dedicated database:

```bash
PYTHONPATH=. \
ENCRYPTION_KEY=<fernet-key> \
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/behavior_analytics_test \
pytest -m integration tests/integration
```

Never run two test processes against the same test database. The schema fixture recreates tables,
so concurrent runs corrupt one another's assumptions. Migration checks use the same isolated test
database:

```bash
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
| `backend-static` | the version check, `ruff check app tests`, `mypy app`, `alembic upgrade head`, `alembic check` | 15 min |
| `backend-unit` | every test that is not marked `integration`, without a database | 15 min |
| `backend-integration` | the integration suite in five shards, each with its own database | 35 min |
| `frontend` | `npm ci`, lint, typecheck, vitest, build | 20 min |

The five shards show up as `backend-integration (shard 3/5)`. Together the unit job and the five
shards run every test in the repository exactly once: the shards run everything marked
`integration`, the unit job everything else.
`backend/tests/unit/test_ci_shard.py` reads the commands out of the workflow and fails if a test
runs in no job or in two.

`backend/scripts/ci_shard.py` splits `tests/integration` into the shards. The split is a greedy
longest-processing-time assignment over the per-file durations in
`backend/tests/integration/shard_weights.json`, so the shards weigh the same and finish together.
Refresh the weights when the integration suite changes shape (a new file, a much slower test):

```bash
cd backend
PYTHONPATH=. ENCRYPTION_KEY=<fernet-key> \
TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/behavior_analytics_test \
pytest -rs -m integration --durations=0 --durations-min=0
```

Sum the setup, call and teardown seconds of each file in that report and write them into
`backend/tests/integration/shard_weights.json` as `{"tests/integration/test_x.py": seconds, ...}`.
A file with no entry counts as the median of the known weights, so a missing entry is harmless but
unbalanced. The seconds come from one local machine: only their ratios matter.

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
