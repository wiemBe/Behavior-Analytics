# Installation

Behavior Analytics can run locally with its deterministic mock provider or as an on-premises
deployment connected to IBM QRadar. The application is read-only against QRadar in both cases.

## Prerequisites

Development requires Docker Engine and Docker Compose. A production installation additionally
requires:

- a Linux x86-64 host with Docker Engine and the Compose v2 plugin, version 2.24 or newer;
- `git`, `openssl` and `curl`;
- a certificate and private key for the public application name;
- a QRadar read-only authorized service token; and
- the CA bundle that validates the QRadar console when it uses an internal CA.

## Clone the repository

The IBM QRadar MCP server is a git submodule pinned to commit
`b8f6a4a3fe901eac4f55e4ca5d146d952f55db51`. Clone recursively:

```bash
git clone --recurse-submodules https://github.com/wiemBe/Behavior-Analytics.git
cd Behavior-Analytics
```

For an existing clone:

```bash
git submodule update --init --recursive
git submodule status
```

The status must show the pinned commit with a leading space. A leading `+` means that the
submodule has drifted. Review the provider allowlist and regenerate
[`mcp-capability-matrix.md`](mcp-capability-matrix.md) before changing the pin.

## Development stack with the mock provider

The mock provider needs no QRadar console.

```bash
cp .env.example .env
python -c "import secrets; print('SECRET_KEY='+secrets.token_urlsafe(64))"
python -c "from cryptography.fernet import Fernet; print('ENCRYPTION_KEY='+Fernet.generate_key().decode())"
```

Put both generated values in `.env` and leave `QRADAR_PROVIDER=mock`, then start the stack:

```bash
docker compose up -d postgres redis
docker compose run --rm migrate
docker compose run --rm backend python -m app.services.seed
docker compose up -d backend frontend celery-worker celery-beat
# backend, celery-worker and celery-beat depend on migrate with
# condition service_completed_successfully. The installer also runs
# migrate explicitly (`docker compose run --rm migrate`).
```

Open <http://localhost:3000>. The mock provider supplies deterministic inventory for development
and tests.

## Production installation

Run the installer from the checkout:

```bash
./qradar-dash install
```

The wizard performs host checks, writes generated secrets to a mode-`0600` `.env`, loads QRadar
and TLS files into Docker volumes, runs migrations, creates the break-glass administrator, registers
the console, starts the services behind the bundled TLS proxy, and prints a summary. The initial
administrator password is printed once. A repeat run preserves generated keys and does not modify
that account. More precisely, it keeps `SECRET_KEY`, `POSTGRES_PASSWORD`, `ENCRYPTION_KEY`,
`ENCRYPTION_KEYS_JSON` and `ENCRYPTION_KEY_VERSION`; keeps existing operator defaults such as
`LOG_LEVEL`, `AUTH_PROVIDER`, `RETENTION_*`, `COMPRESSION_AFTER_DAYS`, `LLM_ENABLED` and
`MCP_ENABLED`; and carries forward every other operator-added `KEY=VALUE` line. Wizard-derived
values are rewritten from the saved answers. Production invariants such as
`QRADAR_VERIFY_SSL=true`, `QRADAR_PROVIDER=rest` and `DEBUG=false` are enforced, with one warning
per changed key. The installer prints names of retained keys, never their values.

The install summary includes QRadar connection and API-readiness results. Its exit codes are:

| Exit code | Meaning |
|---:|---|
| `0` | The stack answered both readiness and version checks, and the QRadar registration check passed |
| `1` | A host/runtime check failed, QRadar registration failed, or readiness/version did not answer 200; when the summary was reached, the install state file was still written |
| `2` | An option or non-interactive answers file was invalid; installation did not start |
| `3` | External-proxy mode had no safe `FORWARDED_ALLOW_IPS`; the backend was not started |

The default release images are under `ghcr.io/wiembe/behavior-analytics/`. The installer supports:

```text
--non-interactive FILE   read non-secret answers from a mode-0600 file
--build                  build images from this checkout
--offline BUNDLE         load images from an offline bundle (made by `./qradar-dash bundle`)
--external-proxy         do not start the bundled TLS proxy
--reconfigure            ask the questions again
--dry-run                run checks without making changes
--skip-qradar-check      skip the QRadar TLS handshake check
```

### Offline hosts

A production host that cannot reach the registry installs from a bundle made on a connected host.
On the connected host, from a checkout of the release (`git checkout v<version>`):

```bash
./qradar-dash bundle [--version X.Y.Z] [--output DIR]
```

It pulls seven images: `postgres`, `backend`, `frontend`, `proxy` and `qradar-mcp` (under
`QRADAR_DASH_IMAGE_PREFIX` from `.env`, else the default prefix), `redis` by the digest pinned in
`docker-compose.prod.yml`, and the secrets helper `alpine:3.20@sha256:…` by the digest pinned in
`deploy/load-secrets.sh`. The helper reference is read from that file; this guide does not copy the
digest. It writes `qradar-dash-<version>-bundle.tar`: `images.tar`, `SHA256SUMS`,
`MANIFEST` (each image reference and its local image ID), `VERSION`, `docs/releases/<version>.md`,
`qradar-dash`, `docker-compose.yml`, `docker-compose.prod.yml` and `deploy/`. It never contains
`.env`, `.secrets/` or `docker-compose.override.yml`. The file is written under a temporary name and
moved into place, so a failure leaves no partial bundle, and an existing bundle is never overwritten.

On the production host, first install: extract the checkout from the bundle into a new directory
and install:

```bash
mkdir qradar-dash-<version>
tar -xf /path/to/qradar-dash-<version>-bundle.tar -C qradar-dash-<version> --exclude=images.tar
cd qradar-dash-<version>
./qradar-dash install --offline /path/to/qradar-dash-<version>-bundle.tar
```

Upgrade of an installed site: run it from the existing installation directory, which holds `.env`,
`.secrets/` and `.qradar-dash/state.json`. Extract the bundle over it (none of those are in the
bundle) and upgrade; a fresh directory fails with "not installed here":

```bash
cd <installation directory>
tar -xf /path/to/qradar-dash-<version>-bundle.tar --exclude=images.tar
./qradar-dash upgrade <version> --offline /path/to/qradar-dash-<version>-bundle.tar
```

`install --offline` refuses, before it loads anything, a bundle whose `VERSION` differs from the
checkout's, whose image prefix differs from the site's, or whose `SHA256SUMS` does not match. After
`docker load` it checks that every `MANIFEST` image reference resolves with `docker image inspect`,
and compares image IDs when the bundle host and this host use the same Docker image store
(`image_store` in `MANIFEST`: `containerd` or `classic`; `.Id` means different things in the two).
When the stores differ it prints that the IDs were not compared and goes on; integrity then rests on
the `SHA256SUMS` check of `images.tar`. `docker save` does not record the name of the redis image
that is pinned by digest, so after the load it exists by ID only, and Docker refuses to tag a digest
reference. The script therefore sets the plain tag (`redis:7.4-alpine`, which moves an existing tag
of that name on the host) and checks that the pinned reference then resolves. If it still does not,
the install stops before starting anything and tells you to make the bundle again on a host with the
same image store; there is no `docker tag` command to run by hand. Without a
bundle, `upgrade <version> --offline` requires the images to be loaded already. The Turkish runbook,
`docs/OPERATIONS.md` (Çevrimdışı paket), has the full list of checks.

The last question of a first install asks whether to read the last 28 days of history from
QRadar, so baselines are steadier from day 1. It is asked once: a re-run, `--reconfigure` and a
site that already has `.qradar-dash/state.json` do not ask it again, and `upgrade` never does. The
question and the note above it are:

```text
    Nothing in the past raises an alert. It sends searches to QRadar for about 3 hours on
    a small estate and up to 3 days on a large one (a large estate gets a shorter window
    so that it fits 72 hours); the admin page shows the progress and the estimate.
14. Read the last 28 days of history from QRadar now, so baselines are steadier from day 1? (yes/no):
```

In an answers file the key is `HISTORY_BACKFILL`:

- `yes` or `no` records that answer for the console, once, before the first collection. A decision
  that already exists is never changed by the installer.
- absent leaves the console undecided; nothing is guessed, and the first administrator sign-in
  asks.

The answer is write-once per direction. The installer records it with

```bash
python -m app.cli.history_backfill decide \
  --instance <console-name> --answer yes --source installer
```

and the command never changes a decision that already exists:

| Exit code | Meaning |
|---:|---|
| `0` | The answer was recorded, or a decision already existed and was left as it is |
| `1` | The database failed; nothing was recorded |
| `2` | The console is not registered, or the arguments are invalid; nothing was recorded |

The installer runs the command right after it registers the console. A failure never stops the
installation: the console stays undecided and the first administrator sign-in asks. The install
summary then prints either the recorded answer or `not recorded (exit N)`.

What the two answers do:

- **Yes** — the console may read QRadar's stored past. Each family reads the days before its
  first live data at its next collection: source volume, catch-all senders and rule matches
  always, and a WAF or IPS source only when it has not collected anything yet. Nothing in the past
  is judged: no incident, no breach and no alert refers to an interval before the reading's
  hand-off. A Yes is never reversed; the reading can be stopped instead.
- **No** — nothing is read from QRadar's past. The answer can still be changed to Yes later by an
  administrator on the history backfill page.

The window and the cost of the reading are governed by the `HISTORY_BACKFILL_*` settings; see
[`CONFIGURATION.md`](CONFIGURATION.md#history-backfill). The progress and the estimate are on
`/admin/history-backfill`.

After installation:

```bash
./qradar-dash status
./qradar-dash logs
./qradar-dash doctor
```

LDAP is configured from the administration UI after the first sign-in. The complete Turkish
operator runbook is [`OPERATIONS.md`](OPERATIONS.md); installer design and failure behavior are in
[`OPERATIONS-DESIGN.md`](OPERATIONS-DESIGN.md).

## QRadar credentials and CA bundle

For manual development setup, keep credentials in the gitignored `.secrets/` directory. Never put
the token on a command line: process arguments are visible to other users and are commonly retained
in shell history.

```bash
mkdir -p .secrets
chmod 700 .secrets
read -rs QR_TOK
printf '%s' "$QR_TOK" > .secrets/qradar.sec
unset QR_TOK
chmod 600 .secrets/qradar.sec
```

The CA bundle must contain the complete trust path that the appliance does not send. If the
console sends only its leaf certificate, concatenate the root and intermediate certificates into
`.secrets/qradar-ca.pem`. Verify the chain before connecting the application:

```bash
openssl s_client -connect qradar.example.internal:443 \
  -CAfile .secrets/qradar-ca.pem -verify_return_error </dev/null 2>&1 | grep Verify
```

The expected result is `Verify return code: 0 (ok)`.

Load the token and CA bundle into the named volume used by the containers:

```bash
./deploy/load-secrets.sh
```

The repository is not bind-mounted. The loader streams the files into a named volume, so an
SELinux-enforcing host needs no relabel of the checkout. The volume is external and survives
`docker compose down -v`. Run the loader again after rotating either file.

Configure the direct REST provider in `.env`:

```ini
QRADAR_PROVIDER=rest
QRADAR_BASE_URL=https://qradar.example.internal
QRADAR_API_TOKEN_FILE=/run/secrets/qradar_sec_token
QRADAR_VERIFY_SSL=true
QRADAR_CA_BUNDLE=/run/secrets/qradar_ca.pem
```

`QRADAR_TLS_ALLOW_MISSING_AKI=true` is available for an appliance certificate that omits an
Authority Key Identifier. It relaxes only that certificate-labelling assertion; chain, expiry and
hostname verification remain enabled. Reissuing the certificate with an AKI is the preferred fix.
The application refuses disabled TLS verification.

## Register a console manually

The production installer registers the console. For a manual development deployment, use the CLI:

```bash
cd backend
python -m app.cli.qradar add \
  --name qradar \
  --url https://qradar.example.internal \
  --token-file ../.secrets/qradar.sec \
  --ca-file ../.secrets/qradar-ca.pem \
  --api-version 29.0

python -m app.cli.qradar list
python -m app.cli.qradar test --name qradar
```

Registration is idempotent by name. The token is encrypted at rest and is not returned by an API.
The `add` command performs a connection check; `--no-verify` skips that check, not TLS validation.

## Local collection and verification

Celery Beat schedules collection. The same jobs can be started manually during development:

```bash
python -m app.cli.sync log-sources --instance qradar
python -m app.cli.sync log-source-metrics --instance qradar
python -m app.cli.sync offenses --instance qradar
python -m app.cli.sync stale-offenses --instance qradar
python -m app.cli.sync offense-aggregates --instance qradar
python -m app.cli.sync rules --instance qradar
python -m app.cli.sync rule-metrics --instance qradar
python -m app.cli.sync rule-health --instance qradar
python -m app.cli.sync coverage --instance qradar
python -m app.cli.sync all --instance qradar
```

Sync operations are idempotent. A failed run does not advance its watermark. Add `--json` for
machine-readable output.

For the full local stack:

```bash
docker compose up -d --build
docker compose ps -a
docker compose logs --tail=300

curl --fail http://127.0.0.1:8000/api/v1/health/live
curl --fail http://127.0.0.1:8000/api/v1/health/ready
curl --fail http://127.0.0.1:8000/api/v1/providers/capabilities
curl --fail http://127.0.0.1:3000/
```

The long-running services are `postgres`, `redis`, `backend`, `celery-worker`, `celery-beat` and
`frontend`; each should be healthy. `migrate` is a one-shot and should exit 0. `backend`,
`celery-worker` and `celery-beat` wait for that exit (`depends_on` condition
`service_completed_successfully`) before they start, so `docker compose up` does not serve or
collect against a schema that is still migrating or that failed to migrate. The installer's
explicit `docker compose run --rm migrate` stays. The optional `qradar-mcp` service starts only
with the `mcp` profile.

## One origin in production

Production serves the UI and `/api/` from one HTTPS origin. Browser-side frontend requests use
`NEXT_PUBLIC_API_BASE_URL`, while production CORS does not allow a separate browser origin. The
installer's proxy provides the supported topology. With an external proxy, build the frontend with:

```ini
NEXT_PUBLIC_API_BASE_URL=/api/v1
```

Next.js embeds that value at build time, so changing it requires rebuilding the frontend image.
Forward `/api/` to the backend without stripping the path and forward all other requests to the
frontend. Preserve the backend's response security headers.

Uvicorn trusts forwarded client addresses only from `FORWARDED_ALLOW_IPS`. Set it to the exact
proxy peer address seen by the backend; never use `*`. Otherwise all analysts can share one
rate-limit bucket because the backend sees only the bridge address. The installer asks for this
value and the runbook explains how to measure it.

## QRadar running on a libvirt network

If the host reaches a QRadar VM but containers receive `ECONNREFUSED`, libvirt may be rejecting
forwarded traffic from the Compose bridge. Run `deploy/create-vmnet.sh` after adapting its
interface and subnet to the host. The script creates the external macvlan `qradar-vmnet` and sets
`--ip-range` outside the libvirt DHCP pool. Copy `deploy/docker-compose.override.example.yml` to
the gitignored `docker-compose.override.yml`. The example references that network (`external: true`,
`name: qradar-vmnet`). Do not declare a second macvlan in the override: without the script's
`--ip-range`, a container can be given an address a VM already holds.

Attach the network only to services that open a connection to QRadar: `backend` (manual searches
and the other API provider calls), `celery-worker` (collectors and scheduled searches) and
`qradar-mcp` (the optional profile's console client). `celery-beat` only publishes task names to
the broker. `migrate` only talks to Postgres. Neither is attached. Do not weaken the host firewall,
and do not set `:z` or `label=disable`.

When `docker-compose.override.yml` exists in the checkout, `./qradar-dash install` and `upgrade`
load it after the production overlay, and the installer writes the same file list to `COMPOSE_FILE`
for a bare `docker compose` command.

## Optional MCP service

The MCP container publishes no ports. Upstream write tools are disabled
(`deploy/mcp/feature_toggles.json` sets POST and DELETE to false). AQL stays on
the direct REST/Ariel provider (`QRadarRestProvider`). The application does not
depend on MCP.

Production hosts are not supported until this configuration is delivered without
a checkout bind mount (see [`OPERATIONS.md`](OPERATIONS.md), OPS-R13). Do not
start the `mcp` profile on a production host.

On a development or lab host, copy the example, set the read-only token, restrict
the file and start the profile. `deploy/mcp/config.json` is gitignored.

```bash
cp deploy/mcp/config.json.example deploy/mcp/config.json
chmod 600 deploy/mcp/config.json
docker compose --profile mcp up -d qradar-mcp
```

Set `authorized_service_token` in that file to the read-only QRadar token before
starting the profile.

## Upgrade, backup and recovery

Use the operator CLI rather than running migrations against a live old application:

```bash
./qradar-dash backup manual
./qradar-dash upgrade <version>
./qradar-dash rollback
./qradar-dash restore <backup-directory>
```

The upgrade path stops writers, creates and verifies a backup, migrates, replaces the services and
checks health. A failed upgrade automatically restores the prior release. Downgrading an Alembic
revision can discard data from columns and tables introduced by that revision; it is not the
operational rollback procedure. See [`OPERATIONS.md`](OPERATIONS.md) for exact procedures.

## Release images

A release is identified by a repository tag and the shared version in `VERSION`,
`backend/pyproject.toml` and `frontend/package.json`. The release workflow checks those versions and
the MCP pin, runs the gates, builds the backend, frontend, PostgreSQL, MCP and proxy images under
`ghcr.io/wiembe/behavior-analytics/`, and publishes the release notes in `docs/releases/`.

The backend image needs the repository root as a named build context because it reads `VERSION` and
release notes:

```bash
docker buildx build --build-context repo=. --build-arg APP_VERSION=<version> backend
```
