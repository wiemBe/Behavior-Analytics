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
> enabled per source; WAF incidents have an analyst API and UI (`/behavior/waf`), and production
> WAF onboarding is unvalidated. **Phase B.2** then adds five behavioural detectors — block, 5xx and
> 4xx ratios, source-IP concentration and new-endpoint access — over opt-in per-endpoint
> breakdowns, each
> failing closed to `INSUFFICIENT_DATA` and each stating the exact claim it makes; a default-off
> shadow-mode switch lets WAF incidents raise alerts. See
> [Roadmap](#roadmap), [the Phase A design](docs/PHASE-A-SOURCE-VOLUME-ANOMALY.md),
> [the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) and
> [the Phase 3 handoff](docs/PHASE3-HANDOFF.md).
> **Phase D** (IPS traffic behavior): the engine, the collect → evaluate → train pipeline and the
> capability status are **built** (T-051c, T-051d). One dispatch per tick collects, then evaluates
> each source's collected buckets oldest first, then trains each source's baselines only through that
> source's own evaluated bucket end. A source that failed or has evaluated nothing is skipped alone.
> The seven IPS detectors are `OPERATIONAL` when `IPS_COLLECTION_ENABLED` is true and `NOT_ENABLED`
> otherwise (the default). **IPS alerting is built** (T-052): behind `IPS_ALERTING_ENABLED` (default
> **false**, shadow mode: incidents are recorded with their lifecycle and nothing pages), an OPEN
> incident opens one alert with the `ips_anomaly` fingerprint (entity incidents keyed on the
> dimension and the entity's stable value key; source incidents on the source), a still-anomalous
> OPEN incident refreshes it, RESOLVED resolves it, notifications are enqueued per transition from
> the scheduled run, the context carries `source_class` when the source is classified (class
> routing), and nothing opens or refreshes while the source is in maintenance. See [the Phase D
> design](docs/PHASE-D-IPS-BEHAVIOR.md).

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
- **RBAC + audit.** Role/permission model plus an append-only `AuditLog` for administrative,
  search and custom-rule actions. The seeded roles are `admin`, `analyst` and `viewer`, plus
  `detection_engineer` (Phase F, D-17): every analyst permission plus `search:write` and
  `custom_rule:write`, and no `admin:*` permission. `read:custom_rules` is satisfied by `read:*`;
  `custom_rule:write` is not, though `admin:*` grants it, so the admin role does not list the
  string. `GET /api/v1/me` returns the caller's own subject, email,
  granted permissions and the derived `effective_permissions` (every non-wildcard permission
  the principal holds, wildcard grants included). It needs an authenticated caller and no
  further permission, and it exposes nothing but the caller's own identity.
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

### Production install (one command)

On a Linux x86_64 host with Docker Engine, the Compose v2 plugin (2.24 or newer), `git`, `openssl`
and `curl`, a TLS certificate and key for the public name, a QRadar SEC token file and the QRadar
CA bundle:

```bash
git clone --recurse-submodules https://github.com/wiemBe/QRadar-Dashboard.git
cd QRadar-Dashboard
./qradar-dash install                      # the wizard asks the questions
./qradar-dash install --non-interactive .secrets/install.answers   # the same, from a file
```

The wizard checks the host, generates `SECRET_KEY`, `ENCRYPTION_KEY` and the database password
into `.env` (mode 0600), loads the QRadar and TLS files into Docker volumes (no bind mount of the
checkout, SELinux stays enforcing), migrates, creates one break-glass admin and **prints its
password once**, registers the QRadar console, starts the stack behind the bundled TLS proxy and
prints a summary. Running it again keeps every generated key and does not touch the admin.
LDAP is configured in the UI afterwards. Options: `--build` (build the images from this checkout
instead of pulling them), `--offline BUNDLE`, `--external-proxy` (you run the TLS proxy),
`--reconfigure`, `--dry-run` (checks only), `--skip-qradar-check`. Then `./qradar-dash status` and
`./qradar-dash logs`. The design is [docs/OPERATIONS-DESIGN.md](docs/OPERATIONS-DESIGN.md) section 3.

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

> **If TLS fails only from Python.** The app sets `VERIFY_X509_STRICT` itself on
> every interpreter (3.12, 3.13 and later alike). QRadar's self-generated console certificate carries a Subject Key
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
| `/` | The Dashboard — the widget home (KPIs, observed vs expected EPS, EPS by class, top sources, recent anomalies and offenses) loading the user's chosen, default or template dashboard (saved-dashboards switcher, UI-4.1). The top bar's connection status, on every route, carries `instance_last_health_check_at`, which the inventory sync stamps on every pass; a console that cannot be reached is recorded `UNREACHABLE` with a sanitized reason, so nothing downstream keeps reading a stale `HEALTHY` |
| `/behavior` | Behavioral overview — four decision counters, the worklist needing attention, recently resolved, health distribution |
| `/behavior/sources` | Source inventory — searchable, sortable, filterable, server-paginated |
| `/behavior/sources/[id]` | One source's observed volume against its seasonal baseline, with anomaly overlay |
| `/behavior/waf` · `/behavior/waf/sources/[id]` | WAF behavior (Phase B) — which WAF sources are healthy enough to judge and where endpoint admission overflows; one source's tracked endpoints and how adequate each baseline is |
| `/anomalies` | Unified, filterable, server-paginated incident list — Phase A, WAF and IPS subjects |
| `/anomalies/[id]` | **Anomaly investigation** — what changed during the anomalous interval |
| `/log-sources` · `/log-sources/[id]` | Log-source inventory and one source's metadata and health (Phase 1) — QRadar-owned fields refresh on sync; criticality, owner and maintenance are SOC-owned |
| `/offenses` · `/offenses/[id]` | Offenses, and one offense's attributes, entities and history (Phase 3) |
| `/rules` · `/rules/[id]` · `/rules/statistics` | Rule health; one rule's health and capacity history; rule-set statistics — most matched, costliest, composition, silent rules |
| `/coverage` | Detection coverage (Phase 3) |
| `/searches` · `/searches/new` · `/searches/[id]` · `/alerts` · `/alerts/[id]` | Scheduled searches and alert lifecycle (Phase 2), plus the AQL editor (Phase F): `/searches/new` stores a **disabled** definition — only stored, validated, versioned AQL ever runs — a writer edits it, previews with Run now and enables it on the detail page. The editor states its own latency sentence (one Ariel run on its cron). The `chart.search_results` dashboard widget (F-7) draws a search's stored results as a line, bars, a single value or the latest run's top rows; a stored pie or heatmap is that table, with a note that the shape is not drawn |
| `/fleet` · `/fleet/sources` | Log-source fleet by class — volume against expected EPS with its completeness, the review queue of new and conflicting sources, class overrides (Phase C.1) |
| `/fleet/catchall` | Catch-all senders (Phase C.2b) — who feeds SIM Generic and the Universal DSMs: the new-sender review queue, each sender's 24 h events, per-bucket totals |
| `/admin` · `/admin/search-cadence` · `/admin/classification-rules` · `/admin/waf-sources` · `/admin/ips-sources` · `/admin/outage-policy` | Administration — the hub plus search cadence per family (`admin:search_cadence`), classification rules (`admin:classification`), per-source WAF field mappings with validation (`admin:waf`), per-source IPS field mappings with validation (`admin:ips`), the outage alert policy (`admin:outage`); the profile editors are `/admin/waf-sources/[logSourceId]` and `/admin/ips-sources/[logSourceId]` |
| `/custom-rules` · `/custom-rules/new` · `/custom-rules/[id]` | Custom threshold rules (Phase F), in the Detection group. The list names each rule, its status in words with the engine's reason, its subject, its condition, its severity, whether it is enabled and when it was last evaluated (UTC). `/custom-rules/new` is the editor: metrics, subject kinds and the preview bounds come from `GET /api/v1/custom-rules/catalog`, and a live draft preview (`POST /custom-rules/preview`) sits beside the form. The same editor edits a stored rule. `/custom-rules/[id]` shows the stored preview (`GET /{id}/preview`) and the breach history. Creating, editing, enabling, disabling and deleting need `custom_rule:write` |

`/config-changes` is deliberately unlisted: it is a placeholder outside the navigation with no
API behind it (configuration changes are not collected), kept by decision D-11.

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
[docs/PRODUCT-UX-GUIDELINES.md](docs/PRODUCT-UX-GUIDELINES.md). The verification record, including
the viewport matrix, is
[docs/UI-WORKSTREAM-COMPLETION.md](docs/UI-WORKSTREAM-COMPLETION.md). The screenshot gallery,
recaptured on 2026-10-15 (T-111), is [`docs/screenshots/ui/`](docs/screenshots/ui/), and its index
is [docs/UI-WORKSTREAM-COMPLETION.md](docs/UI-WORKSTREAM-COMPLETION.md) §7.

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

### Production: one origin for UI and API

**Why.** `frontend/src/lib/api.ts` sends every request to `NEXT_PUBLIC_API_BASE_URL`
(`PUBLIC_BASE`, `baseUrl()`) whenever `window` exists, so every call a client component makes is
made **by the browser, across origins**: the top bar's health, overview, cadence and search reads
plus its alert bell (`components/shell/`), the administration reads and edits (WAF and IPS
profiles, outage policy, search cadence), and every operator action — acknowledge, resolve, run a
search, create or edit a classification rule, override a class. The `components/home/*` widgets
are built to fetch the same way (`hooks/useApiResource.ts`), so the browser-side share grows
rather than shrinks. Server Components are the exception: they run inside the frontend container
and use `INTERNAL_API_BASE_URL`, so they never leave the Docker network. Production CORS allows
**no** origin — `backend/app/main.py` installs `CORSMiddleware` with `allow_origins=[]` once
`settings.is_production`, so no response ever carries `Access-Control-Allow-Origin` — and the
browser refuses every one of those calls. Until a proxy puts them on one host, `127.0.0.1:3000`
and `127.0.0.1:8000` are two origins. Production therefore needs **one origin for the UI and the
API**, and nothing else about the stack changes.

**What.** A TLS reverse proxy serves the UI at `/` and the API at `/api/` on that one origin,
and the frontend is built with a relative API base:

```ini
NEXT_PUBLIC_API_BASE_URL=/api/v1   # at build time — see below
```

Next.js inlines `NEXT_PUBLIC_*` **at build time**: the docs say the value is inlined "at build
time, into the js bundle that is delivered to the client, replacing all references to
`process.env.[variable]` with a hard-coded value", and that "after being built, your app will no
longer respond to changes to these environment variables" — a single image built once has them
"frozen with the value evaluated at build time" (Next.js docs, *How to use environment variables
→ Bundling Environment Variables for the Browser*). Two consequences:

- **The image must be rebuilt whenever this value changes.** `docker-compose.yml` passes it to
  the *running* container (`environment:`), which cannot change what the browser already
  executes; `frontend/Dockerfile` has no build-time value for it either, so set it before
  `RUN npm run build` (an `ARG`/`ENV` in the builder stage, or an `.env` in the build context).
  `frontend/next.config.mjs` already copies the variable into the build.
- **If it is missing at build time**, `frontend/src/lib/api.ts` falls back to the hard-coded
  `http://localhost:8000/api/v1`, which is cross-origin from every production origin and fails.

**Example (nginx).** Adapt the name, ports and upstreams; **TLS is yours to configure** — no
certificates are shown here. The example adds no response header of its own:
`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` and
`Cache-Control: no-store` come from the backend's middleware in `backend/app/main.py`, and the
proxy must neither override nor hide them (`add_header`, `proxy_hide_header`).

```nginx
# EXAMPLE ONLY — server name, upstreams and TLS are yours to supply.
server {
    listen 443 ssl;
    server_name soc.example.internal;   # whatever your certificate names
    # ssl_certificate / ssl_certificate_key: yours to supply.

    # The API. No URI after the upstream: the request path travels unchanged,
    # so /api/v1/... reaches the backend's own API prefix. (The frontend's own
    # /api/health probe is called from inside its container, not through here.)
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # The UI: everything else is the Next.js server.
    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

**Proxy headers and the rate limiter.** The limiter keys on the client address:
`backend/app/core/rate_limit.py` uses slowapi's `get_remote_address`, i.e. `request.client.host`,
scoped per request path, and `RATE_LIMIT_DEFAULT` (120/minute) is applied by
`SlowAPIMiddleware` to every route that declares no limit of its own. What that address *is*
behind a proxy is uvicorn's decision — the compose backend runs `uvicorn app.main:app
--host 0.0.0.0 --port 8000 --proxy-headers` (`docker-compose.yml`). From the uvicorn
documentation, *Settings → HTTP*: `--proxy-headers` enables `X-Forwarded-Proto` and
`X-Forwarded-For` "to populate remote address info", "restricted to only trusting connecting IPs
in the `forwarded-allow-ips` configuration", and `--forwarded-allow-ips` is a "comma separated
list of IP Addresses, IP Networks, or literals (e.g. UNIX Socket path) to trust with proxy
headers": it defaults to the `$FORWARDED_ALLOW_IPS` environment variable if set, otherwise
`'127.0.0.1'`; "The literal `'*'` means trust everything". *Deployment → Proxies and
Forwarded Headers* says which headers are honoured and warns "Only trust clients you can
actually trust!". So the headers are honoured only when **the peer that opened the TCP
connection** is in that list, and the default list is `127.0.0.1`:

- **Behind this stack's published port the peer is a Docker bridge address, not `127.0.0.1`.**
  Uvicorn runs inside the `backend` container, which joins the `internal` and `edge` bridge
  networks and publishes `127.0.0.1:8000:8000`, so a proxy on this host that dials
  `127.0.0.1:8000` reaches the container through Docker's port publishing, not over loopback:
  Docker relays the connection in — the `dockerd` reference describes `--userland-proxy` as
  "Use userland proxy for loopback traffic (default true)" — and the peer uvicorn sees is an
  address on that bridge network, normally its gateway. The default trust list therefore trusts
  nothing useful here: uvicorn ignores the proxy's `X-Forwarded-For`, `request.client.host` stays
  the bridge address for every request, and all clients share one bucket, so one analyst can
  spend `RATE_LIMIT_DEFAULT` for everyone. Any other source outside the list — a second host, a
  container on another network — behaves the same way.
- **Find the address to trust.** Docker's documentation never states which source address the
  container ends up seeing; it says only that a published port is "mapped to host IP addresses",
  done with firewall rules that "perform Network Address Translation (NAT), Port Address
  Translation (PAT), and masquerading" (*Port publishing and mapping*), so measure it. Uvicorn's
  default access-log format prints the client address of each request, for example
  `INFO: 172.18.0.1:41052 - "GET /api/v1/health/live HTTP/1.1" 200 OK` — the direct read-out. This
  backend, though, clears that logger's handlers at startup (`backend/app/core/logging.py`,
  "uvicorn access logs are noisy and duplicate our request logging"), so the line is absent as
  shipped; re-enable that one logger if you want it. The fallback is
  `docker network inspect <network>`: Docker's own bridge example says "This shows the network's
  gateway (for example, `172.18.0.1`)", and that gateway is normally the address uvicorn sees for
  proxied traffic.
- **Set exactly that address.** Put it in `FORWARDED_ALLOW_IPS` in `.env` (uvicorn reads the
  variable itself, and the `backend` service loads `.env` wholesale through `env_file`, so no
  change to `docker-compose.yml` is needed) — or add `--forwarded-allow-ips` to the backend
  command — and restart the backend. Never `*`: anyone who can reach the backend from a trusted
  address with a forged `X-Forwarded-For` would then choose their own rate-limit key.
- **Confirm it afterwards.** With that logger readable, send a request through the proxy: it
  must show the real client address — the browser's — rather than the bridge address. If it
  still shows the bridge address, uvicorn is ignoring the header and every analyst is still
  sharing one `RATE_LIMIT_DEFAULT` bucket.

**Development is unchanged.** Outside production the list stays `["http://localhost:3000"]`
(`backend/app/main.py`), so `npm run dev` on `:3000` reading the API on `:8000` behaves exactly
as it does today, and so does the compose stack with `.env.example` as it ships
(`ENVIRONMENT=development`, UI on `:3000`, API on `:8000`). The relative base above belongs to a
production build.

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
pip install --require-hashes -r requirements.txt -r requirements-dev.txt
pip install --no-deps -e .
export ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
export QRADAR_PROVIDER=mock
uvicorn app.main:app --reload
```

**Dependency locks and updates:** `backend/requirements.txt` (production) and
`backend/requirements-dev.txt` (the `dev` extra, constrained by the production lock) are hashed
pip-tools locks resolved for Python 3.12, the interpreter in the image and in CI. The image and CI
install them with `--require-hashes`, so a release installs exactly what was tested. A dependency
change is its own commit, with no bot: edit `backend/pyproject.toml`, regenerate both locks, run the
gates (`ruff check app tests`, `mypy app`, `pytest -q -m "not integration"`), then commit. Regenerate both together
from a dedicated, secret-free scratch directory (it gets the private `:Z` label, so never mount the
repository or `backend/` itself):

```bash
S=$(mktemp -d)   # an empty directory with no secrets in it
cp backend/pyproject.toml "$S"/
printf 'ruff<0.16\nmypy<2.4\n' > "$S"/constraints.txt   # dev-lock pins, see the lock header
docker run --rm -v "$S:/w:Z" -w /w python:3.12-slim sh -c \
  'pip install pip-tools && \
   pip-compile --no-strip-extras --generate-hashes --output-file=requirements.txt pyproject.toml && \
   pip-compile --no-strip-extras --constraint=requirements.txt --constraint=constraints.txt \
     --extra=dev --generate-hashes --output-file=requirements-dev.txt pyproject.toml'
cp "$S"/requirements.txt "$S"/requirements-dev.txt backend/
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

69 tables across bounded contexts (`instance`, `log_source`, `explanation`, `search`,
`search_cadence`, `offense`, `rule`, `config_change`, `alert`, `identity`, `monitoring`, `fleet`,
`outage`, `waf`, `catchall`, `ips`, `geo`, `dashboard`, `custom_rule`). Thirteen are
**TimescaleDB hypertables**:
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
  engine, collect → evaluate → train pipeline and capability status are built (T-051c, T-051d). The
  seven detectors are `OPERATIONAL` when `IPS_COLLECTION_ENABLED` is true and `NOT_ENABLED`
  otherwise (the default). The engine refuses to run a detector on a bucket that did not measure its
  inputs, and `GET /api/v1/behavior/detectors` publishes each status with its reason.
- **IPS analyst API (read-only, `read:anomalies`).** `GET /api/v1/anomalies` and
  `/anomalies/{id}` also serve IPS incidents (`subject_kind` `IPS_ENTITY` or `IPS_SOURCE`, filter
  `ips_entity_id`); `GET /api/v1/behavior/ips/sources`, `.../sources/{id}/entities`,
  `.../sources/{id}/metrics`, `.../sources/{id}/baselines`, `.../entities/{id}/metrics` and
  `.../entities/{id}/baselines` serve the profile state, entity inventory, buckets and baseline
  cells. An unmeasured bucket is `null`, never `0`; no source IP is served outside a stored,
  bounded contributor list, and these routes do not serve those lists.
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

Custom rules inherit exactly this delay — they judge the same collected buckets — plus at most
one 60-second evaluation beat. `/custom-rules` and `/custom-rules/new` state that trade once:
"Rules are judged on collected 5-minute buckets, up to about an hour after the events arrive.
This is not a seconds-level correlation engine — for alerting within seconds, use QRadar's own
rules."

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
`ips_anomaly_transition`). The engine, the collect → evaluate → train pipeline and the capability
status are built (T-051c, T-051d). One dispatch per tick collects, then evaluates each source's
collected buckets oldest first (bounded by `IPS_MAX_EVALUATION_BUCKETS_PER_RUN`), then trains each
source's baselines only through that source's own evaluated bucket end. A source that failed or
has evaluated nothing is skipped alone. The seven IPS detectors are `OPERATIONAL` when
`IPS_COLLECTION_ENABLED` is true and `NOT_ENABLED` otherwise (the default). **IPS alerting is built** (T-052): behind `IPS_ALERTING_ENABLED` (default **false**, shadow mode: incidents are recorded with their lifecycle and nothing pages), an OPEN incident opens one alert with the `ips_anomaly` fingerprint (entity incidents keyed on the dimension and the entity's stable value key; source incidents on the source), a still-anomalous OPEN incident refreshes it, RESOLVED resolves it, notifications are enqueued per transition from the scheduled run, the context carries `source_class` when the source is classified (class routing), and nothing opens or refreshes while the source is in maintenance.

Every `/api/v1/ips` route needs `admin:ips`, applied at the router mount so reads are guarded
too: `GET /mapping-schema`, `GET /candidates`, `GET`/`PUT /log-sources/{id}/profile` (201 on
create, 409 on a version conflict), `POST …/profile/validate` (202), `POST …/profile/enable` and
`…/disable`, `GET …/profile/history`, `GET …/property-catalog` and `GET /sources`, which carries
each profile's per-field validation status and reasons. The UI is `/admin/ips-sources`. Unlike
WAF, there is no IPS CLI; profiles are administered through the API and UI only.

| Setting | Default | Meaning |
|---|---|---|
| `IPS_COLLECTION_ENABLED` | false | Kill switch for fleet collection; each profile must also be enabled and `VALID` |
| `IPS_ALERTING_ENABLED` | false | IPS alerting opt-in: shadow mode first — incidents are recorded, nothing pages |
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
| `IPS_MAX_EVALUATION_BUCKETS_PER_RUN` | 24 | Collected buckets evaluated per source per scheduled run |
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

### Saved dashboards

The dashboards API (UI redesign, migration `0023`) stores a user's widget layout and their default
dashboard: `dashboard` and `user_dashboard_pref`. It stores structure only — widget ids, types,
geometry and an opaque `config` — and the server never interprets or runs anything from a layout.
A layout is version 1 JSON with at most 24 widgets, a 4 KB `config` per widget and 64 KB in all;
a layout version the server does not know is refused with `422`, and a widget `type` it does not
know is stored as is. An owner has at most 20 dashboards, and names are unique per owner.

Routes under `/api/v1/dashboards`: `GET` (list, paged), `POST`, `GET /{id}`, `PUT /{id}`,
`PATCH /{id}`, `DELETE /{id}`, `POST /{id}/duplicate` and `PUT /default`. Reads need
`read:dashboards` (satisfied by `read:*`); every write needs `dashboard:write`, which `read:*`
does **not** grant, and `PUT`, `PATCH` and `DELETE` also need ownership or `admin:*`. Changing a
dashboard's `visibility` between `PRIVATE` and `SHARED` needs `admin:dashboards`. A caller sees
their own dashboards and every shared one; another owner's private dashboard answers `404`.
`PUT` carries `expected_revision` and answers `409` with the current revision when it is stale.
Every write is audited (`dashboard.create`, `.update`, `.rename`, `.share`, `.delete`,
`.duplicate`, `.set_default`) with the name, revision and widget count, never the layout. Until
OIDC lands every dev caller has the subject `local-dev`, so all dashboards share one owner.

### Custom rules

Phase F (D-15): operator-authored threshold rules over aggregates the platform **already
collects**. Evaluation is DB-only — no new Ariel search, ever — and nothing raw is stored: rules
read buckets that already exist, and a breach row carries counts, shares and a bounded subject
label only. A rule names one subject. A log source, an IPS source, a WAF endpoint or an IPS
entity is a pinned row id; a fleet class is a `SourceClass` value. The rule never names free
text. It names one metric from the ten-entry catalog (`app/custom_rules/catalog.py`: `event_count` and
`average_eps` per log source, fleet-class `eps`, a WAF endpoint's `request_count` and its three
ratios `block_ratio`, `server_error_ratio` and `client_error_ratio`, an IPS entity's `events`,
an IPS source's `total_events` and `blocked_share`), an operator (`GT`/`GE`/`LT`/`LE`/`EQ`), a
threshold, a sustain and a recovery in buckets, and a severity.

A 60-second Celery beat (`evaluate_custom_rules`, schedule key `evaluate-custom-rules`) is a
no-op that opens no session while `CUSTOM_RULES_ENABLED` is false, the default. When it runs, a
subject kind is judged only if it has an evaluable rule: enabled, not retired, and its family
collected. The first such run seeds that kind's watermark at the latest closed bucket and judges
nothing, so an upgrade never replays history. Later runs walk collected buckets after the
watermark, at most `CUSTOM_RULES_MAX_BUCKETS_PER_RUN` per kind. That metric read is one statement
per such kind. The tick's statement count is pinned equal at 10 rules and at 100. A tick that
opens alerts inserts one row per new alert, and that cost is outside the pin.

Three tables (migration `0024`, additive, no hypertable — rules are capped and breaches are
bounded to one live row per rule):

- **`custom_rule`** — the rule, its hysteresis counters (`consecutive_breaching` opens after
  `sustain_buckets`, `consecutive_under` resolves after `recovery_buckets`) and its last status.
- **`custom_rule_breach`** — the observation trail: bucket bounds, the worst and last value
  each with its `EXACT`/`LOWER_BOUND` measurement, the unmeasured-bucket count, and a
  `subject_label` snapshot that keeps the trail readable after the subject retires. One live
  breach per rule is structural (partial unique index `uq_custom_rule_breach_active`, where
  `state <> 'RESOLVED'`). The breach states are `OPEN`, `RECOVERING` and `RESOLVED` (the anomaly
  states, reused, with no join to the incident lifecycle). Breaches stay out of `/anomalies`
  (D-17) and surface in `/alerts`. `/custom-rules/[id]` shows the active breach, the breach
  history and a stored preview of what the rule read.
- **`custom_rule_watermark`** — one row per subject kind: how far that kind has been judged.

Fail-closed, as the detectors: an unmeasured bucket (no row, a `PARTIAL` Phase A row, a
breakdown that did not run) is not a zero and not an "under". It pauses both counters. It is
counted on `unmeasured_buckets` only while a breach is already open. A maintenance bucket pauses
the same way and is not counted as unmeasured. What a `LOWER_BOUND` proves comes from the
catalog entry. `event_count`, `average_eps` and `total_events` are exact-only, so a lower bound
proves nothing either way. The other seven metrics may prove `GT`/`GE` from a lower bound and
never `LT`, `LE` or `EQ`. Recovery counts only consecutive exact unders.

Each rule's `last_status` says why it is not being judged, never silence:

| Status | Meaning |
|---|---|
| `NOT_ENABLED` | The fold's answer when `CUSTOM_RULES_ENABLED` is off. The beat does not write it: it returns before opening a session, and each rule keeps the status it already had |
| `DISABLED` | The rule's own switch is off. Counters and any live breach are kept |
| `SUBJECT_RETIRED` | The subject is missing (`the subject no longer exists`) or an endpoint or entity is no longer tracked (the reason names the retirement time when one is stored). A fleet class is never retired. A missing log source or IPS source is retired the same way |
| `NOT_COLLECTED` | WAF or IPS collection is off. The reason is `waf_collection_enabled is off` or `ips_collection_enabled is off`. A log-source or fleet-class rule is not halted this way |
| `STALE` | No bucket stored for that kind for longer than cadence + bucket + settle + 60 s. Every evaluable rule of the kind takes this status, including one whose breach row stays open; counters and the watermark stay, and the backlog is judged when rows return. A disabled, retired or family-off rule keeps its own status |
| `OPEN` | A live breach that is not recovering |
| `RECOVERING` | A live breach after an exact under that has not yet reached `recovery_buckets`. The bucket that meets `recovery_buckets` resolves it |
| `OK` | No live breach, and the last judged bucket was measured. A streak that has not reached `sustain_buckets` is still `OK` |
| `NO_VERDICT` | No live breach, and the last judged bucket proved nothing (unmeasured, or the source was in maintenance) |

A rule that has never been evaluated has a null status, which is not `OK`. An idle tick,
including a gap still inside the staleness bound, keeps the previous status.

**The API** (`/api/v1/custom-rules`; the router is guarded by `read:custom_rules`, which
`read:*` satisfies, and every write also needs `custom_rule:write`): `GET` (list), `POST`,
`GET /{id}`, `PATCH /{id}`, `DELETE /{id}` (resolve the open alert as the deleting principal,
then delete, 204), `POST /preview` (a draft replay), `GET /{id}/preview` (a stored-rule replay)
and `GET /{id}/breaches`. `GET /catalog` is registered before `/{id}`, so `catalog` is not read
as a rule id. Its body is `CustomRuleCatalogOut`: `metrics` (each entry's `metric`,
`subject_kind`, `unit`, `is_ratio`, `measurement` — `EXACT_ONLY` or
`LOWER_BOUND_PROVES_INCREASE` — and `note`; `reads` and `bulk_loader` stay in the catalog
module), `subject_kinds` (a kind and the one `column` a rule of that kind must set),
`source_classes`, `operators`, `evaluation_enabled` (`CUSTOM_RULES_ENABLED`), `alerting_mode`
(`shadow` or `alerting`, the same value a rule carries), `max_enabled`, `enabled_count`
(enabled rules already holding a slot) and `preview_hours_min` / `preview_hours_max` (1 and
168). Both previews run the evaluator's own fold over one bulk read of stored buckets — nothing is
persisted, alerted or audited — and `hours` is 1–168, default 24. The replay starts from a clean
counter and ignores the operational halts (the rule disabled, the subject retired, the family
or the platform off, a stale kind), so a draft still answers the historical question.
Maintenance is applied the same way the evaluator applies it. Domain validation returns every
reason at once (the body is `{"detail": {"message", "reasons"}}`). When the only reason is the
enabled cap, the response is 409 and the detail is that sentence. A duplicate name is also 409.
A patch requires `expected_updated_at` — the row's `updated_at` is the version token, there is
no integer version — and a mismatch is 409 with `current_version` and `updated_by`. The
enabled-count read is one unlocked `SELECT`, so two concurrent creates can both pass the cap.
Every write is audited (`custom_rule.create|update|delete|enable|disable`, with before/after
snapshots). Enable and disable are used only when `enabled` is the only field that changed.
Previews are not audited.

| Setting | Default | Meaning |
|---|---|---|
| `CUSTOM_RULES_ENABLED` | false | Kill switch for the evaluator; the beat opens no session until it is set |
| `CUSTOM_RULES_ALERTING_ENABLED` | false | Shadow mode (D-17). While evaluation is on, breaches and rule state are written either way. The evaluator opens, refreshes, resolves and enqueues nothing until this is on |
| `CUSTOM_RULES_MAX_ENABLED` | 100 | Enabled rules; creating or enabling past the cap is 409 |
| `CUSTOM_RULES_MAX_BUCKETS_PER_RUN` | 288 | Closed buckets judged per subject kind per run (a day of 5-minute buckets) |
| `CUSTOM_RULE_BREACH_RETENTION_DAYS` | 180 | Resolved breaches older than this are deleted. A live breach, and a row a rule still points at, are kept |
| `CUSTOM_RULES_RETENTION_BATCH_SIZE` | 5000 | Breach rows deleted per retention batch |
| `CUSTOM_RULES_RETENTION_MAX_BATCHES` | 20 | Batches per retention run |
| `CUSTOM_RULES_RETENTION_INTERVAL_SECONDS` | 86400 | One breach-retention run per day |

### Scheduled-search dispatch

Phase F (F-4) adds authoring guards on the same stored searches. At most
`SCHEDULED_SEARCHES_MAX_ENABLED` (default 25) may be enabled — creating or enabling past the
cap is 409 naming the limit, and a disabled search is not counted. A new or changed cron must
fire at least 300 s apart (`CRON_FLOOR_SECONDS` in `app/services/search_scheduler.py`).
`check_cron_floor` derives the gaps from the UTC schedule and falls back to walking
`CronTrigger` fires; the check runs off the event loop. An expression this request does not
change, including one faster than the floor, is kept verbatim. AQL and threshold refusals
return every reason at once (the body is `{"detail": {"message", "reasons"}}`). A cron-floor
refusal is 422 with a string detail. A patch whose only counted field is `enabled`
(`change_note` does not count) is audited as `search.enable` / `search.disable`. Stored results
are retained by a daily batch task. The optional Timescale chunk policy
(`retention_search_result_metric_days`) stays off unless `RETENTION_ENABLED` is set.

| Setting | Default | Meaning |
|---|---|---|
| `SCHEDULED_SEARCHES_MAX_ENABLED` | 25 | Enabled scheduled searches; creating or enabling past the cap is 409 |
| `SEARCH_RESULT_METRIC_RETENTION_DAYS` | 90 | `search_result_metric` rows older than this (by `bucket_start`) are deleted daily |
| `SEARCH_RESULT_RETENTION_BATCH_SIZE` | 5000 | Rows deleted per retention batch |
| `SEARCH_RESULT_RETENTION_MAX_BATCHES` | 20 | Batches per retention run |
| `SEARCH_RESULT_RETENTION_INTERVAL_SECONDS` | 86400 | One search-result retention run per day |

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

The conditions in this table each have their own fingerprint, so they never deduplicate into
one another, and they flow into the same lifecycle and notification pipeline. The same pipeline
also carries `waf_anomaly`, `ips_anomaly`, `inventory` (a new log source, or a new catch-all
sender) and `offense_collection_stale`.

| Condition | Opens when | Resolves when |
|---|---|---|
| `anomaly` | a detector stays anomalous for N consecutive intervals (hysteresis) | it stays healthy for M intervals |
| `search_threshold` | a completed run crosses the search's threshold | a later run completes under it |
| `search_failure` | a search fails `SEARCH_FAILURE_ALERT_AFTER` consecutive runs | the next run succeeds |
| `custom_rule_breach` | the metric stays past `sustain_buckets` while `CUSTOM_RULES_ALERTING_ENABLED` is on (the breach row is written either way) | `recovery_buckets` consecutive exact unders, but only while alerting is on. Disabling the rule or retiring its subject resolves the page on the same condition. Deleting the rule resolves it either way. A stale kind, a family that is off, and maintenance do not resolve a live page |

`search_failure` exists because a detection that has quietly stopped executing produces no results
and therefore no threshold breach — it would otherwise fail silently, which is precisely the blind
spot this platform exists to close.

`custom_rule_breach` (Phase F, `custom_rule_breach:<digest>` from
`compute_fingerprint(source_type="custom_rule", source_id=rule_id, condition="custom_rule_breach")`)
is an operator tripwire, not a platform detector finding. Its observation trail is the breach
row under [Custom rules](#custom-rules). It stays out of `/anomalies` (D-17). In the default
shadow mode (`CUSTOM_RULES_ALERTING_ENABLED=false`) breaches and rule state are written and
nothing is opened, refreshed or resolved by the evaluator; a page left open from an earlier
alerting period stays open until alerting is turned back on. Deleting the rule is the
exception: that path resolves the page either way, with the deleting principal as actor. A
cascade that never reaches it is closed on a later alerting tick by the orphan sweep,
attributed to `custom-rule-evaluator`. A source-backed rule whose source is in maintenance
neither opens, refreshes nor resolves an alert. A fleet class has no maintenance window.

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
- **Phase B (backend vertical slice ✅, API/UI ✅)** — WAF endpoint behavior: a versioned
  endpoint normalizer, per-endpoint observation collection with measured completeness, a seasonal
  request-rate baseline, and `WAF_REQUEST_RATE_SPIKE` on the reused Phase A lifecycle. Validated
  end to end against live QRadar telemetry on 2026-08-03 —
  `INSUFFICIENT_DATA → NORMAL → OPEN → RECOVERING → RESOLVED` on real data.
- **Phase B.1 ✅ (collection productionization)** — per-source WAF profiles with audited,
  versioned field mappings and read-only QRadar validation; deterministic endpoint admission
  bounded per bucket and per source; maintenance decisions recorded per bucket; metric-semantics
  identities that make incompatible baselines fail closed; bounded WAF retention; per-source
  scheduled collect → evaluate → train runs with failure isolation; and an admin status API. Collection
  is **off by default** (`WAF_COLLECTION_ENABLED=false`, and per profile). The analyst anomaly API
  and UI for WAF incidents are built, and their alerts route behind `WAF_ALERTING_ENABLED` (off by
  default; Phase B.2). A WAF incident carries no evidence package by design
  (`explanation_package` is null), and production WAF onboarding remains unvalidated. See
  [the Phase B design](docs/PHASE-B-WAF-BEHAVIOR.md) §28.
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
  - Validated live on the lab on 2026-10-12 ([T-040](docs/tasks/T-040.report.md)): one Ariel
    search per family per tick, results equal to the per-bucket path (Phase A exactly; rule
    matches in 5 of 6 buckets, the sixth lower on the per-bucket side because of a QRadar `STOP`
    edge effect), and every new bucket judged in order.
- **Phase C ✅ (log-source fleet — inventory, classification, outage policy, catch-all senders)**
  (goal G1):
  - *Inventory enrichment and classification rules (C.1).* The inventory sync stores the facts a
    class needs — types with their `internal` flag, the group tree, identifiers, QRadar
    created/modified and auto-discovered flags (migration `0011`) — and a pure, deterministic
    classifier applies operator-editable rules with the fixed precedence override > internal >
    group > type > name. Equal-priority ties and lower-level disagreements are recorded as
    conflicts for review; built-in seed rules classify the well-known DSMs, and multi-role NGFW
    DSMs deliberately get no type rule, so the site's naming convention decides (T-022, T-022b,
    T-022c). A source the platform has not seen before is `NEW` and raises a LOW inventory alert
    (`inventory_new_source_alerts_enabled`, default **true**). Fleet views, review and overrides:
    `/fleet`, `/fleet/sources`, `/admin/classification-rules`; routing can match on the class
    (T-023, T-024).
  - *Outage alert policy (C.2).* A class or a single source resolves to `ALERT` (as before) or
    `RECORD_ONLY` — the anomaly and its lifecycle are recorded, nothing pages — with an optional
    silence-grace override. A source row beats its class row, and where no row applies
    `OUTAGE_ALERT_DEFAULT` decides; its default `ALERT` keeps Phase A's behaviour, so an upgrade
    alerts exactly as before (D-9). Administration is audited, needs `admin:outage` (reads
    included) and lives at `/admin/outage-policy` (migration `0019`; T-068, T-070a).
  - *Catch-all senders (C.2b).* The `CATCHALL_SENDERS` family folds sender hostnames out of the
    catch-all types (SIM Generic and the Universal DSMs by default), storing only admitted
    senders: a per-bucket quota, an active ceiling and a minimum event count bound what an
    attacker-chosen name can create. Buckets carry per-bucket totals and a bounded `top_senders`
    list; each sender's 24 h event count is a lower bound. The review queue is `/fleet/catchall`;
    a newly admitted sender *can* raise a LOW inventory alert, but that is **off by default**
    (`CATCHALL_NEW_SENDER_ALERTS_ENABLED=false`; D-9 — the queue is the surface)
    (migration `0020`; T-069, T-070b).
  - *Validation.* The seed patterns were fitted to the live lab's type names
    ([T-015](docs/tasks/T-015.report.md), 2026-10-12), but the fleet pipeline itself is **not
    validated live**: the planned pass — a sender at ≤ 2 EPS appears in the queue as `NEW`, and a
    `RECORD_ONLY` class stays silent while its `NO_EVENTS` anomaly is still recorded — has not
    run. See [the Phase C design](docs/PHASE-C-LOG-SOURCE-FLEET.md).
- **Phase D ✅ (backend, admin UI, analyst API/UI; live-validated in part)** — IPS traffic
  behavior (goal G2): versioned per-source field-mapping profiles with read-only validation and
  presets (Cisco FTD, Palo Alto THREAT), administered at `/admin/ips-sources` (T-049, T-053,
  T-078); keyed fleet collection — one search per dimension per tick, split time-first then by
  sources, with entity admission bounds (T-050a/T-050b, migration `0018`); the seven detectors —
  `IPS_PORT_RATE_SPIKE`, `IPS_DESTINATION_RATE_SPIKE`, `IPS_SIGNATURE_BURST`,
  `IPS_NEW_DESTINATION_PORT`, `IPS_SOURCE_CONCENTRATION_SPIKE`, `IPS_PORT_FANOUT_SPIKE`,
  `IPS_BLOCK_RATIO_SHIFT` — on seasonal cells per entity and per source, each failing closed with
  its reason (T-051a–T-051d, migration `0021`); alerting behind `IPS_ALERTING_ENABLED` (default
  **false**: shadow mode — incidents and their lifecycle are recorded, nothing pages; T-052); and
  the analyst API and UI — IPS incidents in the unified `/anomalies` list and the investigation
  view, plus the `GET /api/v1/behavior/ips/*` reads (T-085, T-087). Collection is off by default
  (`IPS_COLLECTION_ENABLED=false`, and per profile). Live validation on the lab console
  (2026-10-15, [T-054](docs/tasks/T-054.report.md)): port-sweep **passed** —
  `IPS_PORT_FANOUT_SPIKE` opened on both sources, sibling fires were true statements about that
  traffic or failed closed with their reason, and the six pilot incidents walked
  `CANDIDATE → OPEN → RECOVERING → RESOLVED`. Signature-burst, with alerting on, **passed**:
  `IPS_SIGNATURE_BURST` opened on both sources, and two `ips_anomaly` alerts opened and resolved.
  No notification row was stored, because no channel is configured (L-6). Source-concentration
  **passed**. A morning run of those two was voided by generator defect D-1; T-101 fixed the
  generator, and the part-3 re-run is what passed. New-port is still limited by its 24 h history
  rule (L-1) and was not re-run. Block-shift, with alerting on, **passed**:
  `IPS_BLOCK_RATIO_SHIFT` observed 0.60 against expected 0.10 on both lab sources in the change
  buckets, its two alerts opened 20:47:33Z and resolved "condition recovered" at 21:02:32Z; the
  analysis is in [T-054](docs/tasks/T-054.report.md) "Part 4 analysis" (T-110). T-107, on the
  lab console only, added `IPS_Action` expressions for PAN and FTD through
  the API under the lab-only exception. The application stays read-only. The lab profiles map
  ACTION, and FTD `Would block` counts as allowed. See
  [the Phase D design](docs/PHASE-D-IPS-BEHAVIOR.md).
- **Geography ✅ (backend; D-10)** — the `GEO_COLLECTION` family: two keyed searches per instance
  per tick, one per direction in `GEO_DIRECTIONS`, with the QRadar `GEO::` lookup resolving the
  address to an ISO country code inside the search — no IP address is ever selected or stored, and
  the raw lookup string is parsed in the fold and dropped. Countries beyond the per-bucket cap are
  summed into `OVER_CAP`; a bucket that was not measured has no row (`geo_country_bucket`,
  migration `0022`, bounded retention). `GET /api/v1/geo/countries` (`read:geo`) answers with each
  country's events and share, the remainders (`UNRESOLVED`, `OVER_CAP`, `UNPARSEABLE`), the
  measurement (`EXACT`/`LOWER_BOUND`), coverage, and a status of `NOT_COLLECTED`,
  `INSUFFICIENT_DATA`, `PARTIAL` or `OK` (G-2, G-2b). The AQL was validated live on the lab
  console (2026-10-13–14, [T-077](docs/tasks/T-077.report.md)): `GEO::LOOKUP` returns a JSON
  string and ignores its third argument, GeoIP data is loaded (one approved real public address
  resolved), private, internal and unknown addresses all return `{}` and cannot be told apart,
  and the geo key shares a `GROUP BY` with the bucket key at no measured cost. **Not validated**:
  the collector has not run against any console — GeoIP data age, production row counts and cost
  are unmeasured, and the map widget is not built (`map.geography` is `blocked` in the widget
  catalog). Production validation needs a read-only window on the production console, which the
  user has not given yet.
- **UI redesign (D-10/D-11; in progress)** — the charcoal system, the widget dashboard and the
  page restyles of [the UI redesign plan](docs/UI-REDESIGN-PLAN.md):
  - **Phase 1 ✅** — charcoal tokens and self-hosted Inter (D-11 keeps the palette's own muted
    text and input-edge values by the user's decision), the shell with grouped navigation, and
    the top bar — search over existing lists, auto-refresh with the data's age, the open-alert
    bell, connection status (UI-1.1, UI-1.3, UI-1.5).
  - **Phase 2 ✅** — the Home at `/` rebuilt as the Dashboard: four KPI cards, observed vs
    expected fleet EPS (`GET /fleet/eps-series`), bulk `/behavior/sources` and
    `/anomalies/summary` reads, the anomaly histogram, EPS by source class, severity, top
    sources, recent anomalies and offenses (B-2.1–B-2.3, UI-2.1–UI-2.5).
  - **Phase 3 ✅** — the widget system: registry and catalog, `WidgetFrame` with the full state
    set, drag/resize with keyboard steppers (`react-grid-layout`), per-widget range, configure
    drawer and expand dialog, and the catalog widgets including WAF endpoint traffic, a source's
    EPS against its band and recent IPS anomalies (UI-3.1–UI-3.3b, UI-3.5a/b).
  - **Phase 4 ✅** — saved dashboards: models, migration `0023`, the `/api/v1/dashboards` API
    with its own permissions, revision conflicts and audit, never interpreting or executing a
    layout (B-4); the switcher and management UI, with `/` loading the chosen, default or
    template dashboard (UI-4.1, UI-4.3). Saving is merged (UI-4.2): "Done" writes with
    `expected_revision`, a conflict dialog, an unsaved-changes guard, restore defaults, and four
    more templates (QRadar Administration, Behavioral Analytics, Log Source Monitoring, WAF / IPS
    Monitoring). Widget-frame polish is merged (T-098).
  - **Phase 5 (nearly done)** — page restyles merged for the behavior pages (UI-5.1), the
    anomaly list and investigation (UI-5.2), the WAF behavior pages (UI-5.3), the fleet pages
    (UI-5.4), offenses, rules and coverage (UI-5.5), log sources, searches and alerts (UI-5.6,
    UI-5.6b) and the admin pages (UI-5.7). UI-5.8: every loading and error boundary is plain
    text on the route's real page header, and `/config-changes` says configuration changes are
    not collected. UI-5.9 (cross-page consistency) is in flight.
  - **Phase 6 (QA, not merged)** — the full viewport matrix, accessibility pass, performance
    measurement and cleanup (UI-6.1–UI-6.5). Its groundwork merged: characterization page tests
    for the untested routes and boundaries (T-096, T-097) and the unused `echarts-for-react`
    dropped (T-099, a UI-6.4 item).
- **Phase F (built)** — custom detections (D-15, T-108): threshold rules on collected
  aggregates — F-1 (model, migration `0024`, metric catalog, DB-only evaluator, breach
  retention), F-2 (shadow-mode alerting behind `CUSTOM_RULES_ALERTING_ENABLED`), F-3 (the
  custom-rules API, previews, the `detection_engineer` role), F-6a
  (`GET /api/v1/custom-rules/catalog`) and F-6 (the `/custom-rules` list, `/custom-rules/new`
  with its live draft preview, and `/custom-rules/[id]` with the stored preview and breach
  history) — and custom AQL — F-4 (enabled-search cap, cron floor, 422 `reasons[]`,
  enable/disable audit split, result retention), F-5 (the `/searches/new` editor) and F-7
  (the `chart.search_results` widget). Live validation on the lab is the follow-up the design
  schedules. See [the Phase F design](docs/PHASE-F-CUSTOM-DETECTIONS.md).

Next: UI-5.9, then the full gate and `feat/phase-ce` → `main`; Phase 6 QA (UI-6.1–UI-6.4);
the deferred production geo window (G-5); and the 24 h new-port validation
([T-054](docs/tasks/T-054.report.md), L-1). The order is the one in
[the planner](docs/PLANNER.md).

## License

The vendored `qradar-mcp/` retains its upstream Apache-2.0 license and `NOTICE`. This platform's own
code is under this repository's license.
