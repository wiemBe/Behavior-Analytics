# qradar-observability

An **on-premises QRadar behavioral anomaly and investigation platform**. It baselines what every
log source normally does, detects when that changes, and answers *what changed during the anomalous
interval* with bounded, auditable evidence. Around that it also monitors IBM QRadar SIEM health,
scheduled security searches, offenses, analytics-rule health and MITRE ATT&CK detection coverage —
and it operates strictly **read-only** against QRadar.

> **Status:** Phases 1–3 complete, including a verified live QRadar 7.6.0 FP1 vertical slice.
> **Phase A** (source-volume behavioral analytics) is **complete and validated against live QRadar
> telemetry** (2026-08-02). Spike, drop, multi-source isolation and NO_EVENTS were each observed end
> to end — synthetic telemetry → ingestion → Ariel aggregation → seasonal baseline → detection →
> lifecycle → contributor explanation → API → frontend — through to `RESOLVED`. Live validation
> found and fixed five defects that automated tests had missed; live measurement afterwards found a
> sixth — volume counted Ariel records instead of events (`ae9932a`).
> Of the nine log-source detector types the code names, **only `VOLUME_SPIKE`, `VOLUME_DROP` and
> `NO_EVENTS` run on live data**; the rest lack collected telemetry or a baseline and are not
> executed there. See [detector capability](docs/BEHAVIORAL-ANALYTICS-ARCHITECTURE.md#123-detector-capability).
> **Phase B** (WAF endpoint behavior): the lab-validated backend slice is now **schedulable**
> (Phase B.1) — per-source profiles with read-only QRadar validation, bounded endpoint admission,
> recorded maintenance decisions, metric-semantics identities, WAF retention and per-source Celery
> runs — and **resilient at scale** (Phase B.1.5): raw-path floods degrade to explicit lower bounds
> instead of blinding a source, endpoint storage is sparse, a scheduled run costs a constant number
> of SQL statements, and WAF Ariel concurrency is globally capped. Collection stays off unless
> enabled per source; WAF incidents have no analyst API or UI yet, and production WAF onboarding is
> unvalidated. **Phase B.2** then adds five behavioural detectors — block, 5xx and 4xx ratios,
> source-IP concentration and new-endpoint access — over opt-in per-endpoint breakdowns, each
> failing closed to `INSUFFICIENT_DATA` and each stating the exact claim it makes; a default-off
> shadow-mode switch lets WAF incidents raise alerts. See
> [Roadmap](#roadmap), [the Phase A design](docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md),
> [the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) and
> [the Phase 3 handoff](docs/PHASE3-HANDOFF.md).

Phase 3.5 adds an opt-in, manual synthetic-telemetry lab without changing the read-only QRadar
application contract. See [the lab guide](docs/LAB-SYNTHETIC-TELEMETRY.md) and
[sanitized demo results](docs/LAB-DEMO-RESULTS.md). The generator is never started by the backend,
Celery, Beat, Compose, or normal tests.

---

## Table of contents

- [Why this exists](#why-this-exists)
- [Architecture](#architecture)
- [The QRadar MCP integration](#the-qradar-mcp-integration)
- [Provider abstraction](#provider-abstraction)
- [Security model](#security-model)
- [Installation](#installation)
- [Frontend routes](#frontend-routes)
- [Development](#development)
- [Testing](#testing)
- [Data model](#data-model)
- [Roadmap](#roadmap)

---

## Why this exists

QRadar tells you about *offenses*. It is far less good at telling you when it has quietly stopped
*seeing* — a log source that went silent at 02:00, a DSM update that broke username parsing, a rule
that has not fired in ninety days, a MITRE technique whose only detection is disabled. Those
failures are invisible precisely because they produce no events. This platform watches for the
**absence** of signal as well as its presence and alerts once — without duplicate noise — when a
source's behavior departs from its baseline. Today that means event volume: parsing, delay and
cardinality failures are named in the code but not yet measurable from live telemetry (see
[detector capability](docs/BEHAVIORAL-ANALYTICS-ARCHITECTURE.md#123-detector-capability)).

## Architecture

```
                      ┌──────────────┐
   browser  ───────▶  │  frontend    │   Next.js + TypeScript + ECharts
                      │  (Next.js)   │   (holds NO QRadar credentials)
                      └──────┬───────┘
                             │  REST /api/v1
                      ┌──────▼───────┐
                      │  backend     │   FastAPI + SQLAlchemy 2 + Pydantic 2
                      │  (FastAPI)   │   rate-limited, RBAC, audit log
                      └──┬────────┬──┘
              async jobs │        │ provider abstraction
                  ┌──────▼──┐  ┌──▼─────────────────────────────┐
                  │ Celery  │  │ QRadarProvider (base.py)        │
                  │ worker/ │  │  ├─ QRadarRestProvider  (REST/Ariel — collection + AQL)
                  │ beat    │  │  ├─ QRadarMCPProvider   (GET-only inventory, future AI)
                  └────┬────┘  │  └─ MockQRadarProvider  (dev/tests)
                       │       └──────────────┬──────────────────┘
             ┌─────────▼───────┐              │
             │ PostgreSQL +    │        ┌─────▼────────┐        ┌──────────────┐
             │ TimescaleDB     │        │ QRadar console│  ◀────│  qradar-mcp  │ (internal net,
             │ (hypertables)   │        │  REST + Ariel │        │  GET-only    │  no host ports)
             └─────────────────┘        └───────────────┘        └──────────────┘
             Redis ── Celery broker/result backend
```

**Key rule:** deterministic background data collection and scheduled AQL execution go through the
**direct REST/Ariel** provider. The **MCP** service is used only for inventory retrieval and
future AI-agent investigations. The two are never conflated — see below.

### Backend layout

```
backend/app/
  api/          FastAPI routers + dependencies
  core/         config, database, logging, rate limiting
  models/       SQLAlchemy 2 models (one module per bounded context)
  schemas/      Pydantic 2 request/response models
  services/     domain logic (health scoring, inventory sync, overview, seed,
                AQL validation, search execution + cron scheduling)
  providers/    QRadarProvider ABC + REST / MCP / Mock + DTOs + factory
  collectors/   metric collectors
  anomaly/      baselines, detectors, anomaly engine
  alerts/       alert lifecycle, fingerprints, routing, notifiers
  workers/      Celery app + tasks
  repositories/ data-access layer
  security/     sanitization, RBAC, redaction
  tests/        unit (always run) + integration (Postgres-gated)
```

## The QRadar MCP integration

The upstream [`IBM/qradar-mcp`](https://github.com/IBM/qradar-mcp) repository is vendored, **pinned
to commit `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`**, and run as a **separate Docker service**.
It is not modified.

Before writing a line of integration code we AST-parsed all 73 tools in that commit and produced a
**capability matrix** — [`docs/mcp-capability-matrix.md`](docs/mcp-capability-matrix.md) — mapping
every MCP tool to its QRadar endpoint, HTTP verb, read/write classification, required permission and
suitability for this platform. The README roadmap of the upstream project was deliberately ignored;
only code that exists at the pinned commit is trusted.

That analysis surfaced several things that shaped the design, notably:

- The commit is literally *"enable-crud"* — the MCP server ships with **`POST` and `DELETE`
  enabled** and **`verify_ssl: false`** / **`debug: true`** defaults.
- QRadar uses `POST` (not `PUT`) for updates, so `POST` must be treated as *mutating*.
- Running AQL is itself a `POST` (`create_ariel_search`), which independently confirms our rule to
  keep query execution off the MCP path.

We therefore run the MCP service locked down (§ [Security model](#security-model)) and never depend
the application on it — everything goes through the provider abstraction instead.

To regenerate the matrix after a version bump:

```bash
python backend/scripts/extract_mcp_capabilities.py
```

## Provider abstraction

`app/providers/base.py` defines `QRadarProvider`, a **read-only** interface that returns normalized
DTOs (`app/providers/dto.py`) — never raw QRadar JSON. Three implementations:

| Provider | Role | AQL execution? |
|---|---|---|
| `QRadarRestProvider` | deterministic collection + scheduled AQL | ✅ owns the Ariel lifecycle |
| `QRadarMCPProvider`  | inventory retrieval, future AI investigations | ❌ by design |
| `MockQRadarProvider` | development and tests | ✅ (in-memory, deterministic) |

Code asks a provider **what it can do** via `ProviderCapability`, rather than checking its type:

```python
provider.require(ProviderCapability.AQL_EXECUTION)   # raises on MCP
```

The provider is selected by the `QRADAR_PROVIDER` environment variable (`mock` | `rest` | `mcp`)
through `app/providers/factory.py`.

## Security model

Read-only, defence-in-depth, secrets never in code or browser.

- **Read-only everywhere.** The provider interface has no create/update/delete methods. The MCP
  write surface is disabled by a mounted [`feature_toggles.json`](deploy/mcp/feature_toggles.json)
  (`POST`/`DELETE` off), *and* `QRadarMCPProvider` enforces a hardcoded read-only tool allowlist
  independent of what the server advertises, *and* the QRadar service account is itself view-only.
- **MCP is not exposed.** It runs on an internal Docker network with **no published ports**, with
  `read_only`, `cap_drop: ALL`, `no-new-privileges`.
- **TLS is mandatory.** `QRadarRestProvider` refuses `verify_ssl=False` and non-`https` hosts.
  Production config refuses to boot with TLS verification off.
- **Secrets never in source.** All via env; see [`.env.example`](.env.example). Sensitive
  configuration (QRadar tokens, webhook URLs) is **Fernet-encrypted at rest** via a custom
  `EncryptedString` column type. QRadar tokens are never returned in an API response, never sent to
  the browser, and never sent to an LLM.
- **Structured JSON logging** with a redaction filter that strips token-like values.
- **RBAC + audit.** Role/permission model plus an append-only `AuditLog` for administrative and
  search actions. `GET /api/v1/me` returns the caller's own subject, email, granted permissions
  and the derived `effective_permissions` (every non-wildcard permission the principal holds,
  wildcard grants included). It needs an authenticated caller and no further permission, and it
  exposes nothing but the caller's own identity.
- **Output sanitization.** All QRadar-sourced text (offense descriptions, usernames, source names)
  is sanitized before display to prevent stored XSS.
- **Rate limiting** on the API (`slowapi`), with a tighter limit on search execution.
- **Production hardening is enforced at startup**, not documented and hoped for: `Settings` raises
  if `DEBUG` is on, TLS is off, the provider is `mock`, a REST token is missing, or autonomous LLM
  actions are enabled in production.

### LLM posture

Future LLM support exists **as an interface only and is disabled by default** (`LLM_ENABLED=false`).
The planned `LLMProvider` accepts a **redacted, normalized** offense investigation package and
returns structured JSON (summary, likely scenario, severity, evidence, affected entities, missing
info, recommended AQL, recommended actions, confidence). **The LLM performs no autonomous
actions** — it can never close offenses, block IPs, disable users, modify rules or update reference
sets. This is enforced by the read-only architecture, not merely by prompt.

## Installation

### Prerequisites

- Docker + Docker Compose
- (For a real deployment) a QRadar **read-only authorized service token** and, if QRadar uses an
  internal CA, the CA bundle.

### Cloning (the qradar-mcp submodule)

`qradar-mcp/` is a **git submodule** pinned to commit `b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`
of <https://github.com/IBM/qradar-mcp.git>. The Compose stack builds its image from that checkout,
so a clone without it will fail at `docker compose build qradar-mcp`.

```bash
# Either clone recursively:
git clone --recurse-submodules <this-repo> qradar-observability

# …or populate it after an ordinary clone:
git clone <this-repo> qradar-observability && cd qradar-observability
git submodule update --init --recursive
```

Verify the pin before building — it must report exactly this commit, and the leading status
character must be a space (not `+`, which would mean the checkout has drifted off the pin):

```bash
git submodule status
#  b8f6a4a3fe901eac4f55e4ca5d146d952f55db51 qradar-mcp (heads/main)
```

The pin is deliberate: the MCP allowlist in `backend/app/providers/qradar_mcp.py` is written
against the tool surface of that exact commit. Do not bump it without re-reviewing the allowlist
against [docs/mcp-capability-matrix.md](docs/mcp-capability-matrix.md).

### Quick start (mock provider — no QRadar needed)

```bash
git clone --recurse-submodules <this-repo> qradar-observability && cd qradar-observability

cp .env.example .env
# Generate the two required secrets:
python -c "import secrets; print('SECRET_KEY='+secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY='+Fernet.generate_key().decode())"
# paste both into .env; leave QRADAR_PROVIDER=mock

docker compose up -d postgres redis
docker compose run --rm migrate            # alembic upgrade head
docker compose run --rm backend python -m app.services.seed   # roles + inventory
docker compose up -d backend frontend celery-worker celery-beat
```

Then open **http://localhost:3000**. The SOC Overview and Log Sources pages are populated from the
mock provider.

### Connecting a real QRadar

#### 1. Put the credentials on disk, not in the environment

Create `.secrets/` (git-ignored) with the SEC token and the CA bundle. Do not
pass the token on a command line — an argv element is visible in `ps` output
and lands in shell history.

```bash
mkdir -p .secrets && chmod 700 .secrets
read -rs QR_TOK && printf '%s' "$QR_TOK" > .secrets/qradar.sec && unset QR_TOK
chmod 600 .secrets/qradar.sec
```

The CA bundle must contain the **whole** trust path the appliance does not
send. QRadar presents only its leaf certificate, so a file holding just the
intermediate fails with `unable to get issuer certificate` — concatenate the
root and the intermediate into `.secrets/qradar-ca.pem`.

Then load both into the `qradar-observability-secrets` volume, which is what the
containers actually mount at `/run/secrets`:

```bash
./deploy/load-secrets.sh
```

`.secrets/` is **not** bind-mounted. On an SELinux-enforcing host the repository
is labelled `user_home_t` and no container process can read it — not even root
in the container — and the usual `:z`/`:Z` fix would relabel the host tree as a
side effect. The script streams the files into a named volume over stdin, so no
host label changes. Re-run it after rotating either file; the volume is declared
`external`, so `docker compose down -v` will not wipe your credentials.

Verify the chain before going further:

```bash
openssl s_client -connect qradar.your.internal:443 \
  -CAfile .secrets/qradar-ca.pem -verify_return_error </dev/null 2>&1 | grep Verify
# expect: Verify return code: 0 (ok)
```

#### 2. Configure the provider

In `.env`:

```ini
QRADAR_PROVIDER=rest
QRADAR_BASE_URL=https://qradar.your.internal
QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token   # preferred over QRADAR_SEC_TOKEN
QRADAR_VERIFY_SSL=true
QRADAR_CA_BUNDLE=/run/secrets/qradar_ca.pem
```

> **If TLS fails only from Python.** Python 3.13+ enables `VERIFY_X509_STRICT`
> by default. QRadar's self-generated console certificate carries a Subject Key
> Identifier but no Authority Key Identifier, which RFC 5280 §4.2.1.1 requires,
> so strict mode rejects a chain the `openssl` CLI accepts. The symptom is
> `SSLCertVerificationError: Missing Authority Key Identifier` while the
> `openssl` check above passes. Set `QRADAR_TLS_ALLOW_MISSING_AKI=true`. That
> relaxes a certificate *labelling* assertion only — chain, expiry and
> hostname/IP-SAN verification stay enforced, and it is not equivalent to
> disabling verification, which this codebase refuses outright. The real fix is
> to reissue the appliance certificate with an AKI.

#### 3. If QRadar is a libvirt VM, put the containers on its segment

Skip this unless the appliance is a VM on a libvirt bridge. The symptom is a
container that gets `ECONNREFUSED` reaching QRadar while the host connects
fine — libvirt installs a `FORWARD ... -j REJECT` rule that drops traffic
arriving from other bridges, so the Compose bridge cannot route to the VM.

Rather than change the host firewall, attach the QRadar-facing services to a
macvlan over the libvirt bridge. Edit the interface and subnet in the script to
match `ip -br addr show virbr0`, then:

```bash
./deploy/create-vmnet.sh
```

and add a `docker-compose.override.yml` putting `backend`, `celery-worker`,
`celery-beat` and `migrate` on that network. The override is git-ignored: it
encodes one machine's network layout, not the project's topology.

#### 4. Register the console

Registration is idempotent by name — running it twice updates the existing
instance rather than creating a second one.

```bash
cd backend
python -m app.cli.qradar add \
  --name qradarce2 \
  --url https://qradar.your.internal \
  --token-file ../.secrets/qradar.sec \
  --ca-file ../.secrets/qradar-ca.pem \
  --api-version 29.0
```

The token is encrypted at rest with `ENCRYPTION_KEY` and is never returned by
any API. `add` finishes with a live connection check; `--no-verify` skips it.

```bash
python -m app.cli.qradar list          # registered consoles
python -m app.cli.qradar test --name qradarce2
```

#### 5. Collect

Beat runs these on a schedule, but you do not have to wait for it:

```bash
python -m app.cli.sync log-sources --instance qradarce2
python -m app.cli.sync log-source-metrics --instance qradarce2
python -m app.cli.sync offenses --instance qradarce2
python -m app.cli.sync stale-offenses --instance qradarce2
python -m app.cli.sync offense-aggregates --instance qradarce2
python -m app.cli.sync rules --instance qradarce2   # rules AND building blocks
python -m app.cli.sync rule-metrics --instance qradarce2
python -m app.cli.sync rule-health --instance qradarce2
python -m app.cli.sync coverage --instance qradarce2
python -m app.cli.sync all --instance qradarce2     # dependency order shown above
```

Every sync is idempotent: re-running creates no duplicate rows, and a failed
run does not advance its watermark. Add `--json` for machine-readable output.

Then open <http://localhost:3000/offenses>, `/rules` and `/coverage`.

#### 6. Run and verify the full local stack

The production-style images copy source at build time; the application source tree is not bind
mounted. SELinux stays enforcing and no service is privileged or mounts the Docker socket.

```bash
./deploy/load-secrets.sh
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=300

curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
curl --fail http://127.0.0.1:8000/api/v1/providers/capabilities
curl --fail http://127.0.0.1:3000/offenses

docker compose exec celery-worker \
  celery -A app.workers.celery_app inspect registered
```

Required long-running services are `postgres`, `redis`, `backend`, `celery-worker`,
`celery-beat`, and `frontend`; each must report `healthy`. `migrate` is a one-shot service and must
report exit code 0. `qradar-mcp` is intentionally excluded unless the `mcp` profile is requested.

For a safe operational smoke test, run `sync all` twice. Natural-key row counts must remain stable,
and every `collection_watermark` row must retain `consecutive_failures=0` and a null `last_error`.
The second rule-inventory pass should converge to `unchanged`; log-source upserts may report
`updated` while their natural-key row count remains unchanged.

#### A note on what rule health can honestly report

QRadar's `/analytics/rules` endpoint returns no last-triggered timestamp, no
building-block references and no log-source-type references, and there is no
rule-statistics endpoint. So for a rule that has produced no offense, the
platform has **no evidence either way** about whether it has ever fired.

`RuleMetric` therefore derives only a lower bound from stored offense contributions. Each row is
marked `provenance=offense_contribution`, `completeness=incomplete`, and `inferred=true`. A positive
contribution proves that a rule fired; absence of a contribution does not prove zero. Silent rules
remain `INSUFFICIENT_DATA`, never `NEVER_OBSERVED`, until a future complete source verifies a zero
window. Detection coverage likewise shows `NOT_EVALUATED` rather than 0% until ATT&CK technique
mappings exist.

To also enable the MCP service (inventory / future AI), copy
`deploy/mcp/config.json.example` → `deploy/mcp/config.json`, fill in the read-only token, and start
it with the `mcp` profile:

```bash
docker compose --profile mcp up -d qradar-mcp
```

The MCP container has no host ports; only the backend reaches it.

## Frontend routes

Behavioral analytics leads the navigation because it is what the platform is for; the Phase 3
inventory surfaces remain as supporting capabilities.

| Route | Purpose |
|---|---|
| `/behavior` | Behavioral overview — four decision counters, the worklist needing attention, recently resolved, health distribution |
| `/behavior/sources` | Source inventory — searchable, sortable, filterable, server-paginated |
| `/behavior/sources/[id]` | One source's observed volume against its seasonal baseline, with anomaly overlay |
| `/anomalies` | Filterable, server-paginated anomaly list |
| `/anomalies/[id]` | **Anomaly investigation** — what changed during the anomalous interval |
| `/` · `/log-sources` · `/log-sources/[id]` | SOC overview and log-source inventory (Phase 1). The overview carries `instance_last_health_check_at`, which the inventory sync stamps on every pass; a console that cannot be reached is recorded `UNREACHABLE` with a sanitized reason, so nothing downstream keeps reading a stale `HEALTHY` |
| `/offenses` · `/rules` · `/coverage` | Offenses, rule health, detection coverage (Phase 3) |
| `/searches` · `/searches/[id]` · `/alerts` · `/alerts/[id]` | Scheduled searches and alert lifecycle (Phase 2) |
| `/fleet` · `/fleet/sources` | Log-source fleet by class — volume against expected EPS with its completeness, the review queue of new and conflicting sources, class overrides (Phase C.1) |
| `/fleet/catchall` | Catch-all senders (Phase C.2b) — who feeds SIM Generic and the Universal DSMs: the new-sender review queue, each sender's 24 h events, per-bucket totals |
| `/admin/search-cadence` · `/admin/classification-rules` · `/admin/waf-sources` · `/admin/ips-sources` · `/admin/outage-policy` | Administration — search cadence per family (`admin:search_cadence`), classification rules (`admin:classification`), per-source WAF field mappings with validation (`admin:waf`), per-source IPS field mappings with validation (`admin:ips`), the outage alert policy (`admin:outage`) |

All of these require the `read:anomalies` permission (behavioral routes) or their Phase 2/3
equivalents. The guard lives on the router, not on individual handlers, so adding an endpoint to a
guarded module cannot ship it unauthenticated. The browser holds no QRadar credential and never
contacts QRadar or the MCP service directly.

### WAF field mappings

Each WAF log source has its own profile: which QRadar custom property carries the URL, the HTTP
method, the virtual host, the action, the response code, the rule id and the user agent, and which
action values count as blocked or allowed. `/admin/waf-sources` edits it; the design is
[`docs/FIELD-MAPPING.md`](docs/FIELD-MAPPING.md).

- The property picker lists the console's custom properties — names and types only — with the ones
  that have a parsing expression for the source's log-source type first, and searches them by name
  fragments (`resp code` finds `WAF_ResponseCode`). It is a list to choose from, never a gate: any
  name may be typed, and validation decides.
- Saving a mapping change bumps the profile version and pauses that source's collection until the
  new version validates `VALID`. Validation runs in a worker as bounded, read-only Ariel searches
  that measure how many events carry each property; a result is never stamped onto a newer version
  than the one it checked.
- Two administrators cannot overwrite each other: a change names the version it was made against,
  and a stale one is refused with 409.
- Every change, validation request and result, enable and disable is an `AuditLog` row. No profile
  operation writes to QRadar. The CLI (`python -m app.cli.waf profile …`) keeps working.

### The investigation page

`/anomalies/[id]` answers one question — *what changed during the anomalous interval*. It opens with
a deterministic incident summary and four primary metrics, then the timeline, then what changed; the
lifecycle history, query provenance and detector internals sit behind the investigation tabs, and the
AQL is collapsed inside them. The UX contract for this page and every other behavioral route is
[docs/PRODUCT-UX-GUIDELINES.md](docs/PRODUCT-UX-GUIDELINES.md); the verification record, including
the viewport matrix and screenshots, is
[docs/UI-WORKSTREAM-COMPLETION.md](docs/UI-WORKSTREAM-COMPLETION.md).

Two behaviors there are load-bearing and are covered by tests:

**Unavailable dimensions are rendered, never hidden.** When the QRadar event schema or DSM does not
expose a field for the selected interval, that dimension still gets its section, marked
`UNAVAILABLE` with the reason and an explicit statement that it has not been checked. A hidden
dimension reads to an analyst as one that *was* examined and found clean, which inverts the truth.
Its new/disappeared counts render as em dashes rather than the backend's default zeroes.

**Evidence completeness is stated, not implied.** All six states (`NOT_REQUESTED`, `PENDING`,
`COMPLETE`, `PARTIAL`, `UNAVAILABLE`, `FAILED`) render with their operational meaning, because four
of them produce a page with no contributors — visually identical to "we looked and nothing stood
out". Only the stated status distinguishes a queued job from a failed one from a source whose DSM
emits nothing.

The same rule governs every figure on every behavioral page: a value that was never measured renders
as an em dash, and a value measured as zero renders as `0`. A `NO_EVENTS` anomaly's observed zero is
a real finding; a null is the absence of one. In the charts, a `PARTIAL` or `MISSING` bucket is
plotted as a null with `connectNulls: false` rather than as its reported value — drawing a line
across an uncollected interval would turn a collector outage into an apparent source outage.

## Compose stack

```bash
# One-time, if QRadar runs as a libvirt VM (see deploy/create-vmnet.sh):
bash deploy/create-vmnet.sh

docker compose up -d --build
docker compose ps
docker compose logs --tail=300
```

Expected: `postgres`, `redis`, `backend`, `celery-worker`, `celery-beat` and `frontend` healthy,
with `migrate` exited 0 at the latest revision. The frontend is on `:3000`, the backend on `:8000`
(`/api/v1/health/live`, `/api/v1/health/ready`).

If `docker compose up` fails with `network id ... not found` for `qradar-vmnet`, the external
macvlan is stale — typically after a Docker daemon restart. Remove and recreate it:
`docker network rm qradar-vmnet && bash deploy/create-vmnet.sh`.

### Upgrading

Stop every writer before migrating. Migrations add `NOT NULL` columns whose backfill default is
dropped once the backfill has run (the project's transient-default convention), so a backend or
Celery process still running the old code fails on its next insert until it is replaced.

```bash
git pull --recurse-submodules
docker compose stop backend celery-worker celery-beat
docker compose build backend celery-worker celery-beat migrate frontend
docker compose run --rm migrate            # alembic upgrade head
docker compose up -d backend frontend celery-worker celery-beat
```

`docker compose logs migrate` shows the revision reached. Every migration downgrades one step
(`alembic downgrade -1`), but a downgrade drops the columns and tables that revision added,
with their data.

## Development

Backend, without Docker:

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export QRADAR_PROVIDER=mock
uvicorn app.main:app --reload
```

Frontend:

```bash
cd frontend
npm install
INTERNAL_API_BASE_URL=http://localhost:8000/api/v1 \
NEXT_PUBLIC_API_BASE_URL=http://localhost:8000/api/v1 npm run dev
```

**Scheduling:** Celery + Redis is the default. Every background job is an `async` function in
`app/workers/tasks.py` with a thin Celery shim around it, so the same orchestration runs unchanged
under APScheduler — the documented MVP fallback for teams not running Celery. Celery Beat drives
23 configurable jobs: log-source metrics/inventory, offense collection/staleness/aggregates,
rule inventory/metrics/matches/health, detection coverage, anomaly evaluation, anomaly explanation
collection, scheduled searches, notification dispatch, baseline rebuilds, WAF collection dispatch
and WAF retention, catch-all sender collection and retention, geography collection and retention,
and IPS collection dispatch and retention. WAF dispatch enqueues one `collect_waf_source` task per
enabled, validated WAF profile, and each run collects, evaluates and then trains that source under
its own lock; it is a no-op while `WAF_COLLECTION_ENABLED=false`. IPS dispatch instead claims the
instance's cadence slot and runs one fleet-wide plan per tick; it is a no-op while
`IPS_COLLECTION_ENABLED=false`. Building blocks are collected in the same locked inventory pass
as analytics rules.

WAF sources are administered with `python -m app.cli.waf` (`set`, `validate`, `enable`, `disable`,
`status`, and `probe-rejection` to verify how the appliance refuses an unknown property) and
observed through `GET /api/v1/waf/sources` and `GET /api/v1/waf/ariel-budget`, which require
`admin:waf`. At most `WAF_ARIEL_MAX_CONCURRENT_SOURCES` (default 2) WAF sources search Ariel at once
across all workers.

## Testing

```bash
cd backend
pip install -e '.[dev]'
ruff check app tests
ENCRYPTION_KEY=<fernet-key> pytest -m "not integration"

# Integration tests need PostgreSQL + TimescaleDB:
export TEST_DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/qradar_obs_test
pytest -m integration
pytest
alembic upgrade head
alembic check
```

Unit tests cover the health-score arithmetic, output sanitization, mock-provider determinism, the
MCP allowlist and no-AQL capability, TLS refusal, encrypted-column round-trip, production config
hardening, robust statistics, alert fingerprinting, notification routing and cron expansion.
Integration tests exercise inventory sync, metric collection, baselines, the anomaly engine, the
search executor and scheduler, search alerting, notification dispatch, the result-trend endpoint and
the API against a real database. All QRadar
responses in tests are mocked and no notification is ever really sent (`MockNotifier`).

Current suite (2026-10-13): **2,940 backend tests (2,177 unit, 763 integration) and 1,097
frontend tests**, the integration tests against a real `timescale/timescaledb:2.17.2-pg16`
instance. `ruff check` is clean; `mypy app` reports **31 existing errors in 18 files**, unchanged
from the Phase A backend baseline, with no new errors. Alembic upgrades a fresh database to the
head revision, downgrades two revisions and upgrades again, and `alembic check` reports no drift.

> Never run two `pytest` processes against the same test database concurrently. The schema fixture
> drops and recreates tables, so a second run fails with unrelated collection errors, and a
> subsequent `alembic check` against that database reports spurious drift.

Frontend tests run under Vitest + Testing Library:

```bash
cd frontend
export PATH="$HOME/.local/node/bin:$PATH"
npm ci
npm audit --omit=dev
npm run lint
npm run typecheck
npm test             # 629 tests
npm run build
```

Server components are tested by awaiting the page function and rendering its result with the API
client stubbed, which is how the empty, error and authorization states are covered. ECharts is
stubbed in chart tests — jsdom has no canvas — so what is asserted is the contract: modular
registration, one `init`, resize observation, `dispose` on unmount, and the series data itself,
which is where an unobserved interval would otherwise become a false zero.

The production build succeeds on Next.js **15.5.22**. The production-only audit reports 5 transitive
findings (2 moderate, 3 high), all pre-existing and none introduced by this workstream:

| Package | Severity | Advisory | Assessment |
|---|---|---|---|
| `echarts` 5.5.1 | moderate | XSS in ECharts | Reachable only through a `formatter` that interpolates untrusted text into HTML. Ours interpolate timestamps, numbers and backend enum values only. |
| `echarts-for-react` | moderate | transitively via `echarts` | Not used by the Phase A charts, which call `echarts/core` directly. |
| `postcss` | high | XSS via unescaped `</style>`; source-map path traversal | Build-time only; no attacker-controlled CSS enters the build. |
| `sharp` <0.35 | high | inherited libvips CVEs | Pulled in by Next.js image optimization, which this application does not use. |
| `next` | high | depends on the two above | — |

`npm audit fix --force` would install `next@9.3.3`, a six-major-version downgrade that does not
support the App Router this application is built on. No forced resolution is applied; the fix is an
upstream Next.js release on the 15.x line.

Note on tooling: the enforced gates are `ruff check` and `pytest` (see
[`.github/workflows/ci.yml`](.github/workflows/ci.yml)). `mypy` runs advisory-only until the Phase 1
modules are fully annotated. `ruff format` is deliberately **not** part of the toolchain — the
codebase uses a compact hand-formatted style throughout.

## Data model

62 tables across bounded contexts (`instance`, `log_source`, `explanation`, `search`,
`search_cadence`, `offense`, `rule`, `config_change`, `alert`, `identity`, `monitoring`, `fleet`,
`outage`, `waf`, `catchall`, `ips`, `geo`). Thirteen are **TimescaleDB hypertables**:
`log_source_metric`, `search_result_metric`, `rule_metric`, `rule_match_metric`,
`offense_snapshot`, `rule_health_snapshot`, `detection_coverage_snapshot`, `waf_endpoint_metric`,
`waf_source_bucket`, `ips_source_bucket`, `ips_entity_metric`, `catchall_bucket` and
`geo_country_bucket`.

Highlights:

- **Log-source health** — a pure 0–100 scoring function exists (`app/services/health_score.py`,
  40% freshness + 25% volume + 20% parsing + 15% collection), but **no collector or task calls
  it**, so `log_source.health_score` is not populated by the live pipeline. Its parsing and
  collection components would read fields the Ariel path does not measure.
- **Baselines** use **median + MAD** per (weekday, hour) cell — robust to incident spikes, no ML.
- **Anomaly types.** Operational on live data: `NO_EVENTS`, `VOLUME_DROP`, `VOLUME_SPIKE`.
  Synthetic telemetry only (mock provider): `PARSING_DEGRADATION`, `UNKNOWN_EVENT_SPIKE`,
  `TIMESTAMP_DELAY`, `REPEATED_PAYLOAD`. Unsupported by any collection path: `CARDINALITY_DROP`,
  `COLLECTION_ERROR`. Phase B: `WAF_REQUEST_RATE_SPIKE`, plus the B.2 types `WAF_BLOCK_RATIO_SPIKE`,
  `WAF_ERROR_RATIO_SPIKE`, `WAF_CLIENT_ERROR_RATIO_SPIKE`, `WAF_CLIENT_CONCENTRATION_SPIKE` and
  `WAF_NEW_ENDPOINT_ACCESS`, scheduled for enabled WAF profiles (the first four need their
  collection setting). Phase D names seven IPS types — `IPS_PORT_RATE_SPIKE`,
  `IPS_DESTINATION_RATE_SPIKE`, `IPS_SIGNATURE_BURST`, `IPS_NEW_DESTINATION_PORT`,
  `IPS_SOURCE_CONCENTRATION_SPIKE`, `IPS_PORT_FANOUT_SPIKE` and `IPS_BLOCK_RATIO_SHIFT` — whose
  pure detectors exist, but the engine that schedules them is not built, so none runs yet. The
  engine refuses to run a detector on a bucket that did not measure its inputs, and
  `GET /api/v1/behavior/detectors` publishes each status with its reason.
- **Event counting.** Volume is `SUM(eventcount)`, never `COUNT(*)`: QRadar coalesces identical
  events into one Ariel record, so a row count under-reports by up to ~38x on this appliance.
- **Alert lifecycle:** `OPEN → ACKNOWLEDGED → RESOLVED`, with a **partial unique index** on
  `dedup_key` for non-resolved alerts that structurally prevents duplicate notifications.
- **Scheduled searches** store the AQL, enforce timeout / max time-range / max rows / concurrency,
  keep query **version history**, and store **aggregated** results by default — never raw events.

### Search cadence

Every search the platform sends to QRadar belongs to a **search family**, and each family has its
own cadence: how often it searches.
- Operators set it on `/admin/search-cadence`, or with `PUT /admin/search-cadence/{family}`
  (permission `admin:search_cadence`).
- Every change is audited.
- The choices are 5 min, 15 min, 30 min, 1 h, 2 h, 4 h and 8 h. **Every family defaults to 1 h.**

| Family | What it searches | Bucket |
|---|---|---|
| `PHASE_A_VOLUME` | Event count per log source | `COLLECTION_INTERVAL_SECONDS` (5 min) |
| `WAF_COLLECTION` | Endpoint volume and breakdowns, per WAF source | `COLLECTION_INTERVAL_SECONDS` |
| `ANOMALY_EXPLANATION` | Evidence for open anomalies | — |
| `RULE_MATCH` | Events matched per rule (`creEventList`) | `RULE_MATCH_WINDOW_SECONDS` (1 h) |
| `IPS_COLLECTION` | IPS dimension facts — ports, destinations, signatures, source IPs, fan-out, action — per enabled, validated IPS source | `COLLECTION_INTERVAL_SECONDS` |
| `CATCHALL_SENDERS` | Sender names per catch-all log source (SIM Generic, Universal DSMs) | `COLLECTION_INTERVAL_SECONDS` |
| `GEO_COLLECTION` | Events per country, by source and destination address | `COLLECTION_INTERVAL_SECONDS` |

**The cadence decides when a family searches, not how finely it measures.**
- At each tick, a family runs **one** Ariel search covering every complete bucket since its last
  run. The search is keyed by `starttime / <bucket ms>`.
- Each bucket is stored and judged exactly as before. So a 1-hour cadence sends one Phase A
  search an hour instead of twelve.
- A search that reaches its row cap keeps nothing and is split in halves. No bucket is ever
  written from a truncated result.

**The cost is latency.** A detection can arrive up to *cadence + bucket + settle* late: about
1 h 7 min at the defaults. The admin page shows this for each family.

Operator-defined scheduled searches keep their own cron. Their schedule control offers the same
presets.

| Setting | Default | Meaning |
|---|---|---|
| `SEARCH_CADENCE_SETTLE_SECONDS` | 120 | Wait after a slot boundary before searching, so late events can arrive |
| `ARIEL_MULTI_BUCKET_MAX_ROWS` | 5000 | Row cap of one keyed search; above it, the window is split |
| `SEARCH_CADENCE_MAX_SEARCHES_PER_TICK` | 16 | Searches one family may send per tick, splits included |
| `ANOMALY_MAX_BUCKETS_PER_EVALUATION` | 120 | Phase A buckets judged per source in one evaluation run |

**Settings that changed meaning:**
- `COLLECTION_INTERVAL_SECONDS` now sets only the bucket width. It no longer schedules
  collection.
- `EXPLANATION_COLLECTION_INTERVAL_SECONDS` and `RULE_MATCH_COLLECTION_INTERVAL_SECONDS` are
  deprecated. The scheduler no longer reads them.

**Upgrading.** Migration `0013` seeds every family at 1 h. Before it, Phase A and WAF searched
every 5 minutes. So after the upgrade, an outage (`NO_EVENTS`) alert can arrive about an hour
later than before. If that latency matters more than the search load, set the family back to
5 min on the admin page. The families added later are seeded at 1 h by their own migrations
(`IPS_COLLECTION` by `0018`, `CATCHALL_SENDERS` by `0020`, `GEO_COLLECTION` by `0022`). The
design is in [`docs/SEARCH-CADENCE.md`](docs/SEARCH-CADENCE.md).

### Outage alert policy

A source's `NO_EVENTS` does not have to page a human. An `outage_alert_policy` row (migration
`0019`) says whether the class or the single source it names opens an alert (`ALERT`) or records
the anomaly and its lifecycle without the alert and its notification (`RECORD_ONLY`), and may
override the silence grace for its scope. Rows are scoped to a `SourceClass` or one log source,
optionally to one instance; a source row beats its class row. Where no row applies,
`OUTAGE_ALERT_DEFAULT` decides, and its default `ALERT` keeps Phase A's behaviour, so an upgrade
alerts exactly as before (decision D-9).

Administration is `GET /api/v1/outage-policy` plus `PUT`/`DELETE` on
`/api/v1/outage-policy/classes/{source_class}` and on
`/api/v1/outage-policy/sources/{log_source_id}`, all guarded by `admin:outage` at the router —
`read:*` does not satisfy it, because the policy decides which silences page a human. Every
mutation is audited with the before and after values. The UI is `/admin/outage-policy`.

### Catch-all senders

Phase A judges a catch-all source's total volume; Phase C.2b adds **who is behind it**. The
`CATCHALL_SENDERS` family runs one keyed search per instance per cadence tick over the device
types in `CATCHALL_TYPE_IDS` (SIM Generic and the Universal DSMs by default), folds sender names
out of the raw rows, and stores only what passed admission: sender names are chosen by whoever
sends, so a per-bucket quota and an active ceiling bound what a new name can create, and a
refused name is counted in the bucket but never stored. A newly admitted sender can raise a LOW
inventory alert, but that is off by default (D-9): the review queue is the default surface.

Two tables (migration `0020`): `catchall_sender` — an admitted sender with its review state
(`NEW` → `REVIEWED`/`DISMISSED`) and a 24 h event count that is a lower bound, NULL meaning
unknown — and `catchall_bucket`, a hypertable holding one row per measured (source, bucket) with
events, records, distinct senders and a bounded `top_senders` list. A bucket that was not
measured has no row, so a gap is unknown, not zero; a truncated search makes the row's counts
lower bounds.

| Setting | Default | Meaning |
|---|---|---|
| `CATCHALL_COLLECTION_ENABLED` | true | Kill switch for the family's searches |
| `CATCHALL_TYPE_IDS` | 67, 39, 212, 367, 4000 | Device types whose events are searched for senders; each id is re-validated before it enters a query |
| `CATCHALL_MAX_ROWS_PER_SEARCH` | 2000 | Rows kept per keyed search before truncation splits it |
| `CATCHALL_MAX_NEW_SENDERS_PER_BUCKET` | 20 | New senders admitted per catch-all source per bucket |
| `CATCHALL_MAX_ACTIVE_SENDERS_PER_SOURCE` | 2000 | Active senders kept per catch-all source |
| `CATCHALL_MIN_ADMISSION_EVENTS` | 3 | Events in a bucket below which an unseen sender is not admitted |
| `CATCHALL_SENDER_ACTIVE_DAYS` | 7 | Days without a sighting before a sender is retired; a later sighting reactivates it |
| `CATCHALL_NEW_SENDER_ALERTS_ENABLED` | false | Whether a newly admitted sender also raises a LOW inventory alert |
| `CATCHALL_TOP_SENDERS` | 20 | Senders kept in a bucket's `top_senders` list |
| `CATCHALL_RETENTION_DAYS` | 35 | Buckets, and senders retired for longer than this, are deleted |
| `CATCHALL_RETENTION_BATCH_SIZE` | 5000 | Rows deleted per retention batch |
| `CATCHALL_RETENTION_MAX_BATCHES` | 20 | Batches per retention run |
| `CATCHALL_RETENTION_INTERVAL_SECONDS` | 86400 | One retention run per day |

Reads are `GET /api/v1/fleet/catchall/sources`, `/senders` and `/buckets` (router-guarded by
`read:fleet`); reviewing or dismissing a sender — `POST …/senders/{id}/review` or `/dismiss` —
needs `log_source:write`, is audited, is idempotent, and resolves the sender's open new-sender
alert. The UI is `/fleet/catchall`.

### IPS sources

Phase D baselines what the IPS normally sees: destination ports, destination IPs and signatures
are entities with their own metrics and seasonal cells; source IPs and the block action are
judged at source level; raw values never leave the pure folds except as bounded contributor
lists (at most ten source IPs per source and bucket). Each IPS log source needs a profile
(`ips_source_profile`, migration `0017`): a versioned mapping of logical fields — source and
destination IP, destination port, protocol, signature, action, severity — onto normalized
fields or custom properties, plus the action value map. A mapping change bumps the profile
version and resets validation; collection uses a profile only when its current version is
enabled and `VALID`. Presets exist for Cisco FTD and Palo Alto THREAT.

Collection (migration `0018`) runs one keyed search per dimension per tick for the whole fleet
(`IPS_COLLECTION`; see the family table above), split time-first then by sources when truncated.
Per source and bucket it stores `ips_source_bucket` — totals, per-dimension completeness and
coverage, concentration and fan-out facts — and admits entities into `ips_entity` with sparse
per-bucket counts in `ips_entity_metric` (both hypertables). Detection (migration `0021`) adds
seasonal cells per entity and per source (`ips_entity_baseline`, `ips_source_baseline`),
hysteresis state (`ips_detector_state`) and incidents with transitions (`ips_anomaly`,
`ips_anomaly_transition`). The pure detectors exist (see [the Phase D
design](docs/PHASE-D-IPS-BEHAVIOR.md)); the engine that schedules them is not built, so nothing
is evaluated yet and collection is opt-in (`IPS_COLLECTION_ENABLED`, default false).

Every `/api/v1/ips` route needs `admin:ips`, applied at the router mount so reads are guarded
too: `GET /mapping-schema`, `GET /candidates`, `GET`/`PUT /log-sources/{id}/profile` (201 on
create, 409 on a version conflict), `POST …/profile/validate` (202), `POST …/profile/enable` and
`…/disable`, `GET …/profile/history`, `GET …/property-catalog` and `GET /sources`, which carries
each profile's per-field validation status and reasons. The UI is `/admin/ips-sources`. Unlike
WAF, there is no IPS CLI; profiles are administered through the API and UI only.

| Setting | Default | Meaning |
|---|---|---|
| `IPS_COLLECTION_ENABLED` | false | Kill switch for fleet collection; each profile must also be enabled and `VALID` |
| `IPS_MAX_SOURCES_PER_SEARCH` | 50 | Sources per keyed search (`logsourceid IN (…)`); a larger group is chunked |
| `IPS_MAX_ENTITIES_PER_BUCKET` | 200 | Entities kept per (source, bucket, dimension) after the fold's cut |
| `IPS_MAX_NEW_ENTITIES_PER_BUCKET` | 20 | New entities admitted per source per bucket |
| `IPS_MAX_ACTIVE_ENTITIES_PER_SOURCE_DIMENSION` | 2000 | Active entities kept per source and dimension |
| `IPS_MIN_ADMISSION_EVENTS` | 3 | Events in a bucket below which an unseen entity is not admitted |
| `IPS_ENTITY_ACTIVE_DAYS` | 7 | Days without an observation before an entity is retired |
| `IPS_MIN_COVERAGE` | 0.90 | Dimension coverage below which a bucket is `PARTIAL` and excluded from baselines |
| `IPS_VALIDATION_WINDOW_SECONDS` | 900 | Recent history a validation probes |
| `IPS_VALIDATION_PENDING_TIMEOUT_SECONDS` | 900 | A pending marker older than this may be replaced after a dead worker |
| `IPS_VALIDATION_LOCK_RETRY_DELAY_SECONDS` | 30 | Delay before validation retries a source held by the collection pipeline |
| `IPS_VALIDATION_LOCK_MAX_RETRIES` | 20 | Lock retries before validation records a fail-closed source-busy outcome |
| `IPS_VALIDATION_MIN_RATIO` | 0.90 | Minimum population share for a required field |
| `IPS_VALIDATION_MAX_ACTION_VALUES` | 20 | Hard disclosure and Ariel-result bound for discovered ACTION values |
| `IPS_BASELINE_LOOKBACK_DAYS` | 28 | Days of version-compatible buckets a baseline rebuild considers |
| `IPS_BASELINE_REBUILD_BATCH_SIZE` | 50 | Subjects loaded and upserted together during a bounded rebuild |
| `IPS_RETENTION_ENABLED` | true | Whether the bounded retention task runs |
| `IPS_METRIC_RETENTION_DAYS` | 35 | Age at which buckets and entity metrics are deleted |
| `IPS_RETENTION_BATCH_SIZE` | 5000 | Rows deleted per retention batch |
| `IPS_RETENTION_MAX_BATCHES` | 20 | Batches per retention run |
| `IPS_RETENTION_INTERVAL_SECONDS` | 86400 | One retention run per day |

### Geography

`GEO_COLLECTION` (D-10) answers one question for the home map: how many events per country, by
source or destination address. Each cadence tick sends two keyed searches per instance — one per
direction in `GEO_DIRECTIONS` — so at the 1 h default cadence it is two searches an hour per
instance. The QRadar `GEO::` lookup resolves the address to an ISO country code inside the
search; no IP address is ever selected or stored, and the raw lookup string is parsed in the
fold and dropped. One row per (instance, bucket, direction, country or remainder) lives in the
`geo_country_bucket` hypertable (migration `0022`); countries beyond the per-bucket cap are
summed into `OVER_CAP`, and a bucket that was not measured has no row.

`GET /api/v1/geo/countries` needs `read:geo` (satisfied by `read:*`). Its query parameters are
`since` and `until` — both required, timezone-aware, ordered and at most `API_MAX_RANGE_DAYS`
wide — and `direction` (`SOURCE` or `DESTINATION`). The response carries the `countries` list
(each with `country_code`, `events` and its `share` of country events), the `remainders`
(`UNRESOLVED`, `OVER_CAP`, `UNPARSEABLE`), the `measurement` (`EXACT` or `LOWER_BOUND` when a
search was truncated), a `coverage` object (`measured_buckets` / `expected_buckets`) and a
`status`: `NOT_COLLECTED` when no bucket in the range was collected, `INSUFFICIENT_DATA` when
buckets were collected but no address resolved to a country, `PARTIAL` when some buckets of the
range are missing, and `OK` otherwise. `UNRESOLVED` counts events whose address is private,
internal or unknown to GeoIP — the lookup returns the same empty result for all three, so they
cannot be told apart.

| Setting | Default | Meaning |
|---|---|---|
| `GEO_COLLECTION_ENABLED` | true | Kill switch for the family's searches |
| `GEO_DIRECTIONS` | SOURCE, DESTINATION | Which address is looked up; each direction is a separate stored series |
| `GEO_MAX_ROWS_PER_SEARCH` | 5000 | Rows kept per keyed search before truncation splits it |
| `GEO_MAX_COUNTRIES_PER_BUCKET` | 64 | Countries kept per bucket and direction; the rest is summed into `OVER_CAP` |
| `GEO_RETENTION_DAYS` | 35 | Buckets older than this are deleted |
| `GEO_RETENTION_BATCH_SIZE` | 5000 | Rows deleted per retention batch |
| `GEO_RETENTION_MAX_BATCHES` | 20 | Batches per retention run |
| `GEO_RETENTION_INTERVAL_SECONDS` | 86400 | One retention run per day |

### Scheduled-search dispatch

`app/services/search_scheduler.py` expands each search's cron expression (via APScheduler's
`CronTrigger`, already a dependency) and persists `next_run_at`, so the schedule survives a restart
and two workers agree on which tick is which. The policies are chosen so the scheduler cannot become
an incident of its own:

| Situation | Behaviour |
|---|---|
| A search is seen for the first time | `next_run_at` is seeded; it does **not** run. Deploying 50 searches never fires 50 searches at once. |
| The worker was down for an hour | The overdue search runs **once**, then reschedules from *now*. Replaying every missed tick would stampede QRadar with already-stale queries. |
| A duplicate beat delivery | Same tick → same `run_key` → the unique constraint reuses the execution row. No double-run. |
| A previous run is still in flight | Skipped, not queued. Past twice its own timeout a run is presumed dead so a crashed worker cannot silently retire a search. |
| A cron expression is malformed | Reported and skipped; `next_run_at` is preserved so fixing it resumes the schedule. One bad search never stops the cycle. |
| Many searches are due at once | Bounded by `SEARCH_MAX_DISPATCH_PER_CYCLE`. |

### What raises an alert

Three independent conditions, each with its own fingerprint so they never deduplicate into one
another, all flowing into the same lifecycle and notification pipeline:

| Condition | Opens when | Resolves when |
|---|---|---|
| `anomaly` | a detector stays anomalous for N consecutive intervals (hysteresis) | it stays healthy for M intervals |
| `search_threshold` | a completed run crosses the search's threshold | a later run completes under it |
| `search_failure` | a search fails `SEARCH_FAILURE_ALERT_AFTER` consecutive runs | the next run succeeds |

`search_failure` exists because a detection that has quietly stopped executing produces no results
and therefore no threshold breach — it would otherwise fail silently, which is precisely the blind
spot this platform exists to close.

### Result trends

`GET /api/v1/searches/{search_id}/results` returns a search's stored aggregates, oldest first, for
charting:

| Parameter | Default | Notes |
|---|---|---|
| `metric_key` | `total` | `total` plus one key per GROUP BY dimension |
| `start` / `end` | unbounded | Inclusive. Naive timestamps are read as UTC; offsets are converted, never relabelled |
| `limit` | 500 (max 5000) | When more points match, the **most recent** win — a chart silently showing the oldest 500 of 50,000 points would be actively misleading |

Each point carries its `execution_id`, `execution_status`, `duration_ms`, `result_count`,
`threshold_breached`, `query_version` and `query_version_id`. Executions and version rows are joined
in one statement, so point count never drives query count. An inverted range or an over-cap limit is
rejected with 422; an unknown search is 404.

`/searches/[id]` renders this with Apache ECharts (`components/ResultTrend.tsx`, the only place
ECharts is imported). A **failed run breaks the line rather than plotting zero** — inventing a
traffic cliff that never happened is worse than a visible gap — and **dashed markers annotate
query-version boundaries**, because results either side of an AQL change are not comparable.

### Operator actions

`/alerts/[id]` and `/searches/[id]` expose acknowledge, resolve and manual-run controls
(`components/AlertActions.tsx`, `components/RunSearchButton.tsx`). These are the only client
components in the app: every page remains a React Server Component and passes ids and primitives
across the boundary, never an object or a credential. Authorization stays entirely server-side — the
components call the guarded endpoints and render the outcome, and a 403 surfaces as a clear
permissions message rather than a silent failure.

Operator actions (acknowledge / resolve) enqueue notifications too, so a manual resolve is not
invisible to whoever is on call. Enqueueing is always separate from delivery: an HTTP request
records the intent and returns, and the `dispatch_notifications` worker does the sending with retry
and dead-lettering.

Migrations are managed by Alembic; the initial migration materializes the schema from model
metadata (so it can never drift) then applies hypertable and retention policies.

## Roadmap

Development follows four phases; each phase explains its design, lists files, implements, tests,
lints and updates this README.

- **Phase 1 ✅** — architecture, Docker Compose, models + migrations, `MockQRadarProvider`,
  SOC overview + log-source inventory.
- **Phase 2 ✅** — scheduled searches, metric collection, anomaly detection, alert lifecycle +
  notifications (Teams / email / Slack / generic webhook / syslog), operator action UI
  (acknowledge / resolve / manual run) and the ECharts result-trend chart.
- **Phase 3 ✅** — live read-only QRadar REST/Ariel collection, offenses, analytics rules,
  provenance-aware rule metrics and health, detection coverage, guarded APIs, Celery scheduling,
  MCP audit persistence, and operator dashboards.
- **Phase A ✅ (backend, frontend, UI refinement, automated gates, live validation)** — seasonal
  weekday×hour volume baselines, `VOLUME_SPIKE` / `VOLUME_DROP` / `NO_EVENTS` detectors with an
  explicit anomaly lifecycle, bounded Ariel contributor evidence, and the behavioral overview,
  source-behavior, anomaly-list and investigation views. Validated end to end against live QRadar
  telemetry on 2026-08-02.
- **Phase B (backend vertical slice ✅, API/UI pending)** — WAF endpoint behavior: a versioned
  endpoint normalizer, per-endpoint observation collection with measured completeness, a seasonal
  request-rate baseline, and `WAF_REQUEST_RATE_SPIKE` on the reused Phase A lifecycle. Validated
  end to end against live QRadar telemetry on 2026-08-03 —
  `INSUFFICIENT_DATA → NORMAL → OPEN → RECOVERING → RESOLVED` on real data.
- **Phase B.1 ✅ (collection productionization)** — per-source WAF profiles with audited,
  versioned field mappings and read-only QRadar validation; deterministic endpoint admission
  bounded per bucket and per source; maintenance decisions recorded per bucket; metric-semantics
  identities that make incompatible baselines fail closed; bounded WAF retention; per-source
  scheduled collect → evaluate → train runs with failure isolation; and an admin status API. Collection
  is **off by default** (`WAF_COLLECTION_ENABLED=false`, and per profile). The analyst anomaly API,
  UI, evidence and alert routing for WAF incidents are not built, and production WAF onboarding
  remains unvalidated. See [the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) §28.
- **Phase B.1.5 ✅ (collection resilience and scale)** — per-dimension completeness with explicit
  exact/lower-bound request counts, so a path flood above the group cap can still prove a spike
  but never a normal verdict; sparse, slim endpoint metric rows (about 2.2× smaller per row, and
  rows only where traffic exists); batch evaluation and training at a constant 36 SQL statements per
  scheduled run (6,273 before, at 2,000 endpoints); a crash-safe global WAF Ariel concurrency budget;
  query-budget status; and an operator procedure for QRadar's rejection semantics. See
  [the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) §29.
- **Phase B.2 ✅ (behavioural detectors)** — action, response-code and source-IP breakdowns, each
  one bounded read-only Ariel search per bucket with its own completeness and gated by
  `WAF_OUTCOME_COLLECTION_ENABLED` / `WAF_CLIENT_COLLECTION_ENABLED` (off by default); pooled ratio
  baselines; `WAF_BLOCK_RATIO_SPIKE`, `WAF_ERROR_RATIO_SPIKE`, `WAF_CLIENT_ERROR_RATIO_SPIKE`,
  `WAF_CLIENT_CONCENTRATION_SPIKE` and `WAF_NEW_ENDPOINT_ACCESS` (the last needs a verified history
  and no setting); source-level coverage required on the ratio paths; and alerts from WAF incidents
  behind `WAF_ALERTING_ENABLED` (off by default, shadow mode first).
  `GET /api/v1/behavior/detectors` reports each detector's deployment status. Lab use only:
  production WAF onboarding remains unvalidated and there is no source-level alert coalescing. See
  [the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) §30.
- **Phase 4** — security hardening, expanded tests, documentation, observability.
- **Search cadence ✅ (D-7)** — the operator sets each search family's cadence (5 min to 8 h,
  default 1 h) from the admin UI, and every change is audited.
  - One Ariel search per tick, keyed by time, returns every bucket. It is split when truncated.
  - Phase A judges every new bucket in order.
  - Live validation on the lab is pending.

Next: the analyst API and UI for WAF incidents, then the goals in
[the planner](docs/PLANNER.md) §0. Production WAF onboarding still needs a field-availability pass
against a real WAF product ([the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) §30.14).

## License

The vendored `qradar-mcp/` retains its upstream Apache-2.0 license and `NOTICE`. This platform's own
code is under this repository's license.
