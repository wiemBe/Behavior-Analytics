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
docker compose up -d backend frontend celery-worker celery-walker celery-beat
# backend, celery-worker, celery-walker and celery-beat depend on migrate with
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

### Prepare these files

The wizard asks for paths. Have these ready before you run it. Type every path answer without
trailing spaces: 0.1.16 and earlier refuse a pasted answer with a trailing space, and the message
does not show the space. Later versions trim the answer.

| File | Wizard question | Notes |
|---|---|---|
| SEC token file | 7 | One line, mode `0600`. Create it with the `read -rs` recipe in "QRadar credentials and CA bundle". |
| QRadar CA bundle (PEM) | 8 | Root and intermediate certificates, or the self-signed leaf itself. See below. |
| Proxy TLS certificate and key (PEM) | 2 | For `PUBLIC_HOSTNAME`. Not needed with `--external-proxy`. See below. |
| Backup directory | 11 | An absolute path outside the checkout, writable by the installing account. |

Never type the token on a command line (`echo TOKEN > file`, `--token ...`): process arguments are
visible to other users and stay in shell history. If it happened, delete that line from the
history (`history -d <number>`, then `history -w`), and consider the token exposed: ask the QRadar
administrator to replace it.

If your user is not in the `docker` group, run every `./qradar-dash` command with `sudo`, and
keep doing so for every later command (`doctor`, `upgrade`, `backup`), so the files stay owned by
one account.

**QRadar CA bundle.** Read what the console presents and save each certificate block:

```bash
openssl s_client -connect qradar.example.internal:443 -showcerts </dev/null
```

Copy each `-----BEGIN CERTIFICATE----- ... -----END CERTIFICATE-----` block into one file, leaf
first. Then run `openssl x509 -in <block-file> -noout -subject -issuer -fingerprint -sha256` on a
block. If subject and issuer are the same, the certificate is self-signed: the bundle is that
certificate. If they differ and the issuer is not in the file, the chain is missing: obtain the
issuer's certificate from the QRadar administrator and append it. Compare the SHA-256 fingerprint
with the QRadar administrator before you trust it. Verify the way the application does:

```bash
openssl s_client -connect qradar.example.internal:443 -CAfile <bundle> -partial_chain \
  -verify_hostname qradar.example.internal -verify_return_error </dev/null 2>&1 | grep Verify
```

`Verify return code: 0 (ok)` is the expected result. `-partial_chain` is what lets a self-signed
leaf without `keyCertSign` verify, as it does in the backend. The certificate's Subject
Alternative Name must contain the host used in the QRadar URL (question 4); a mismatch fails the
preflight naming that host. Use the name or address that the certificate lists. Question 9 (AKI)
is for a different case: a certificate with no Authority Key Identifier.

**Proxy certificate.** Production uses a certificate from the organisation's CA. For a
pre-production host, create a self-signed one (browsers will warn until it is trusted):

```bash
mkdir -p .secrets/tls && chmod 700 .secrets/tls
openssl req -x509 -newkey rsa:3072 -nodes -days 365 -subj "/CN=soc.example.internal" \
  -addext "subjectAltName=DNS:soc.example.internal" \
  -keyout .secrets/tls/dash.key -out .secrets/tls/dash.crt
chmod 600 .secrets/tls/dash.key
```

Use `IP:10.0.0.5` in place of `DNS:...` when analysts open the host by address. The name must be
`PUBLIC_HOSTNAME`.

**Disk.** Preflight needs 80 GiB free on Docker's data root (database, volumes, backup scratch
restore). With the containerd image store (`docker info` shows the storage driver `overlayfs`),
images and layers live under containerd's root instead (default `/var/lib/containerd`): that
directory needs 20 GiB when it is on another filesystem, and when both share one filesystem that
filesystem needs 80 GiB. Preflight and `./qradar-dash doctor` report each directory and say what it
holds. If `/` is small, move the data root:

1. Stop Docker: `systemctl stop docker docker.socket`.
2. Copy the data: `rsync -a /var/lib/docker/ /data/docker/`, or `cp -a /var/lib/docker/. /data/docker/`
   when `rsync` is absent.
3. Set `{"data-root": "/data/docker"}` in `/etc/docker/daemon.json` (create the file if it is
   missing). Do not also pass `--data-root` on the service unit: Docker refuses to start with
   both.
4. With the containerd image store, do the same for containerd: stop it, copy
   `/var/lib/containerd`, and set `root = "/data/containerd"` in `/etc/containerd/config.toml`
   (`containerd config default > /etc/containerd/config.toml` creates a full file).
5. Start the services and check `docker info | grep -i "root dir"`.

On an SELinux-enforcing host, label the new directories as the old ones before you start Docker:
`semanage fcontext -a -e /var/lib/docker /data/docker && restorecon -R /data/docker`, and the same
with `/var/lib/containerd` and `/data/containerd`.

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
the operator documents (`README.md`, `.env.example`, `docs/INSTALL.md`, `docs/OPERATIONS.md`,
`docs/CONFIGURATION.md`, `docs/SEARCH-CADENCE.md`, `docs/OPERATIONS-DESIGN.md`,
`docs/WEBHOOK-PAYLOAD.md`), `qradar-dash`,
`docker-compose.yml`, `docker-compose.prod.yml` and `deploy/`. It never contains
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

Question 14 of a first install asks whether to read the last 28 days of history from
QRadar, so baselines are steadier from day 1. It is asked once: a re-run, `--reconfigure` and a
site that already has `.qradar-dash/state.json` do not ask it again, and `upgrade` never does. The
question and the note above it are:

```text
    Nothing in the past raises an alert. It sends searches to QRadar for about 3 hours on
    a small estate and up to 3 days on a large one (a large estate gets a shorter window
    so that it fits 72 hours); the admin page shows the progress and the estimate.
14. Read the last 28 days of history from QRadar now, so baselines are steadier from day 1? (yes/no):
```

Questions 15 and 16 then choose the behaviour families and alerting policy:

```text
15. Which behaviour families should collect now? (defaults, none, or comma/space list: waf, ips, rule-match, property-watch, identity, custom-rules) [defaults]:
16. Keep alerting defaults, record without paging, or alert? (defaults/shadow/alerting) [defaults]:
```

`FAMILIES=defaults` and `ALERTING=defaults` preserve the defaults and write no switch rows.
WAF, IPS, identity, property watches, custom rules and rule-match all default on.
The installer prepares the backend image, then prints its code-default list from Python
before asking these two questions.
Use an explicit comma- or space-separated list to collect only those families, or `none`
to disable all six. `defaults` and `none` must each stand alone. `shadow` records without
paging; `alerting` enables alerts for collecting families. Unlisted families have collection
and alerting turned off. WAF includes outcome and client breakdown collection.
These questions are asked once, skipped with saved answers, and asked again by `--reconfigure`.
Answers files accept `FAMILIES` and `ALERTING` too. Only differences from the effective state
are stored. A failure warns and installation continues; the final summary reads the effective
switches. Review or change them under **Administration → Features** (`/admin/features`).
Upgrade never asks or writes switches; existing `.env` values remain the initial defaults.

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
  -CAfile .secrets/qradar-ca.pem -partial_chain -verify_hostname qradar.example.internal \
  -verify_return_error </dev/null 2>&1 | grep Verify
```

The expected result is `Verify return code: 0 (ok)`. The installer's preflight runs the same
check.

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

### What the token must be allowed to read

The application changes no QRadar configuration or data. It uses the token read-only for every API
except Ariel, where it creates searches (`POST /ariel/searches`) and cancels only the ones it
created (`DELETE /ariel/searches/{id}`). QRadar's Log Activity permission is what allows both. You
do not have to know every capability in advance: after the installer registers the console, the
application asks QRadar, API by API, which of the calls below the token can make, and lists what
is still missing together with what each missing API feeds. When the QRadar administrator grants more, one **Check now**
restarts what was blocked. The check runs at start-up, every six hours
(`QRADAR_ACCESS_CHECK_INTERVAL_SECONDS`) and on demand. The result is
`GET /api/v1/admin/qradar-access` ([API](API.md)) and the `qradar_access` line of
`./qradar-dash doctor`.

To request the role up front, ask for read access to these APIs. IBM's per-endpoint API reference
states no required capability, so the last column is the closest role permission IBM documents
for the same data in the Administration Guide ("Creating a user role"). Treat it as a hint to
confirm with the QRadar administrator, not as a guarantee: a `403` from the check is the
authoritative answer for your version.

| API (path as the application calls it) | Method | What it feeds | Role permission (IBM hint) |
|---|---|---|---|
| `/system/about` | GET | The connection check and the QRadar version shown for each console. | not known |
| `/system/servers` | GET | The managed-host status in the QRadar health section. | Admin |
| `/config/deployment/hosts` | GET | Events per second against the allocation in the QRadar health section. | Admin |
| `/config/deployment/license_pool` | GET | The license allocation in the QRadar health section. | Admin |
| `/ariel/searches` | GET | How many Ariel searches QRadar holds, in the QRadar health section. | Log Activity |
| `/config/event_sources/log_source_management/log_sources` | GET | The log source inventory, which every volume and health view starts from. | Read-only Configuration, View Log Sources |
| `/config/event_sources/log_source_management/log_source_types` | GET | The device type of each log source, which the Fleet classification uses. | Read-only Configuration, View Log Sources |
| `/config/event_sources/log_source_management/log_source_groups` | GET | The log source groups shown beside each source and used by the Fleet pages. | Read-only Configuration, View Log Sources |
| `/config/event_sources/custom_properties/regex_properties` | GET | The property names offered by the WAF, IPS and property-watch mapping editors. | Log Activity, User Defined Event Properties |
| `/config/event_sources/custom_properties/property_expressions` | GET | Which log source types each custom property covers, in the mapping editors. | Log Activity, User Defined Event Properties |
| `/analytics/rules` | GET | The rule inventory behind the Rules and Detection coverage pages. | Log Activity, View Custom Rules |
| `/analytics/building_blocks` | GET | The building blocks the rule inventory merges with the rules. | Log Activity, View Custom Rules |
| `/siem/offenses` | GET | The offense snapshots behind the Offenses page and the rule firing evidence. | Offenses |
| `/ariel/searches` | POST (+ DELETE) | Every measured view: volume per log source, WAF, IPS, property watches, catch-all senders, geography, rule matches, anomaly explanations and scheduled searches. | Log Activity |

Calls on a single object of these lists (one log source, the status and the rows of a search the
token created) are not probed separately: they are taken as the same capability as the list. The
check sends one read per row with `Range: items=0-0`, and for Ariel one tiny stored search
(`SELECT starttime FROM events LIMIT 1 LAST 1 MINUTES`) that is cancelled unread. It keeps the
status code of each answer and nothing from the body.

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

For monitoring the whole application, poll `GET /healthz`. It answers `200` with
`{"status": "ok", "components": {...}}` when every component is up and `503` with
`{"status": "degraded", ...}` otherwise. It checks the API itself, the database (`SELECT 1`),
the migration head, Redis (`PING`), Celery workers (at least one `ping()` reply), the walker (a
worker consuming the walk queue) and beat (a fresh heartbeat key in Redis). Each component is
`ok`, `fail` or `unknown` (the check timed out). With the installer's proxy use
`https://<host>/healthz`; without it use `http://<host>:8000/api/v1/healthz`. The endpoint is
unauthenticated and reveals only those three words per component — no versions, hostnames, error
text, counts or worker names. The whole result is cached in the backend for 10 s and concurrent
callers share a single check run, so a monitor polling every few seconds does not fan out Celery
broadcasts. `GET /api/v1/health/live` stays the
container liveness probe: it checks the process only, so a slow database cannot restart-loop it.

The long-running services are `postgres`, `redis`, `backend`, `celery-worker`, `celery-walker`, `celery-beat` and
`frontend`; each should be healthy. `migrate` is a one-shot and should exit 0. `backend`,
`celery-worker`, `celery-walker` and `celery-beat` wait for that exit (`depends_on` condition
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
proxy peer address seen by the backend, plus the frontend container's address (`FRONTEND_EDGE_IP`,
default `172.28.10.11`; the installer appends it); never use `*`. The proxy must append the client
address to `X-Forwarded-For`. Otherwise all analysts can share one
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
and the other API provider calls), `celery-worker` (collectors and scheduled searches),
`celery-walker` (QRadar collection walks) and `qradar-mcp` (the optional profile's console
client). `celery-beat` only publishes task names to the broker. `migrate` only talks to Postgres.
Neither is attached. Do not weaken the host firewall, and do not set `:z` or `label=disable`.

When `docker-compose.override.yml` exists in the checkout, `./qradar-dash install` and `upgrade`
load it after the production overlay, and the installer writes the same file list to `COMPOSE_FILE`
for a bare `docker compose` command.

## Optional MCP service

The MCP container publishes no ports. Upstream write tools are disabled
(`deploy/mcp/feature_toggles.json` sets POST and DELETE to false). AQL stays on
the direct REST/Ariel provider (`QRadarRestProvider`). The application does not
depend on MCP.

The profile needs nothing to copy or edit. It reuses what the backend already has:

- **The token.** The backend sends `QRADAR_API_TOKEN_FILE` (or `QRADAR_SEC_TOKEN`) to the MCP
  service in an `SEC` header on every request, and the service uses it to call QRadar. The
  MCP container does not mount the token file: its user cannot read it. With
  `QRADAR_PROVIDER=mcp`, production refuses to start without a token.
- **The CA.** The service mounts the same secrets volume read-only at `/run/secrets` and verifies
  the console certificate against `qradar_ca.pem` (`REQUESTS_CA_BUNDLE`). Without that variable
  the pinned server does not verify the certificate at all.
- **The toggle file.** `feature_toggles.json` is baked into the `qradar-mcp` image
  (`deploy/mcp/Dockerfile`: IBM's unmodified build plus that one file), so no file of the
  checkout is mounted.

The hop from the backend to the MCP service is plain HTTP on the internal Docker network. The
service publishes no port, so only containers on that network can reach it, and the token
travels in that hop. Do not publish the port.

### Enable the profile on a production host

The `mcp` profile was validated against a QRadar 7.6.0 FP1 console on 2026-10-23: the handshake and
tool negotiation (no write tool), the read tools, a value equal to the REST provider's, TLS
verification on, a missing token refused with 401, and no token in any log. A production host runs
the published image `<QRADAR_DASH_IMAGE_PREFIX>/qradar-mcp:<QRADAR_DASH_VERSION>` (the prefix and
version come from `.env`). It is pulled, or loaded from the offline bundle; nothing is built on
the host.

Before you enable it, check four things:

- **The name.** `QRADAR_CONSOLE_FQDN` in `.env` must match a subject alternative name (SAN) of the
  console certificate. An IP SAN works when the value is that IP address. The value is a host name,
  or a host name with an optional `:port` (`console.example.com:8443`); the installer refuses a
  scheme, a path and any other shape, and accepts a port from 1 to 65535. When the value is derived
  from the QRadar URL, the port is stripped. The MCP service connects to
  `https://<QRADAR_CONSOLE_FQDN>/api/`, so a value with a port dials that port and a value without
  one dials 443; certificate verification matches the host, not the port. The backend is not
  affected: it reaches the console through `QRADAR_BASE_URL`, which may include a port. A console
  whose API is not on 443 can use the MCP profile by setting the port.
- **The chain.** `qradar_ca.pem` in the secrets volume must hold the full chain up to the root the
  console certificate chains to. The server's Python accepted a CA without an Authority Key
  Identifier; the backend still needs `QRADAR_TLS_ALLOW_MISSING_AKI=true` for such a CA (see
  [`OPERATIONS.md`](OPERATIONS.md), OPS-R3).
- **The route.** The service reaches the console from the `internal` network, as the backend does.
  If the backend needs the macvlan override (OPS-R4), `qradar-mcp` needs the same network.
- **The token.** The one the backend already has. The profile adds none.

Then turn it on:

1. Add `mcp` to `COMPOSE_PROFILES` in `.env`, keeping what is there. With the bundled proxy the
   line becomes `COMPOSE_PROFILES=proxy,mcp`; with an external proxy it becomes
   `COMPOSE_PROFILES=mcp`.
2. Start the service. A bare `docker compose` in the checkout reads `COMPOSE_FILE` and
   `COMPOSE_PROFILES` from `.env`:

   ```bash
   docker compose up -d qradar-mcp
   ```

3. Check it: `./qradar-dash doctor` now counts `qradar-mcp` in its `containers` row, and
   `docker compose ps qradar-mcp` should show it healthy.

Starting the profile does not move the application to MCP. `QRADAR_PROVIDER` stays `rest`, and a
console uses the MCP service only when it is registered with `--provider mcp` and
`--mcp-url http://qradar-mcp:5000` (`python -m app.cli.qradar add`, see OPS-R1).

The server writes its log to standard output only. Read it with `docker compose logs qradar-mcp`.
The `mcp_logs` volume stays empty; do not rely on it.

Re-running `./qradar-dash install` keeps `mcp` (and any other profile you added) in
`COMPOSE_PROFILES`; the wizard decides only `proxy`. `./qradar-dash upgrade` and `rollback`
recreate `qradar-mcp` with the target image in the same `up -d --wait` as the application, and wait
for its healthcheck, whenever `mcp` is in `COMPOSE_PROFILES`.

If MCP calls fail after the service is up, follow [`OPERATIONS.md`](OPERATIONS.md), OPS-R64. A TLS
failure reaches the backend as an unavailability, not as an authentication error.

### Development and lab hosts

On a development or lab host, load the secrets as for the rest of the stack and start the profile:

```bash
docker compose --profile mcp up -d qradar-mcp
```

`install --build` does not build this image. To build it from the checkout, run two steps in
this order:

```bash
docker compose --profile mcp-build build qradar-mcp-upstream
docker compose --profile mcp build qradar-mcp
```

The first builds `./qradar-mcp` with IBM's own Dockerfile and tags it
`localhost/qradar-mcp-upstream:b8f6a4a3`. The second passes that image to
`deploy/mcp/Dockerfile` as the named context `upstream`. The `localhost/` name is deliberate:
if the first step was skipped, the build fails instead of fetching an image of that name from a
public registry.

## Moving from a pre-production to a production QRadar

Use `./qradar-dash reset` when a site was installed against a pre-production console and must be
installed again against production. It replaces the manual `docker compose down -v`, the deletion
of `.env` and `.qradar-dash/state.json`, and the reload of secrets. Do not use it to change
versions (`upgrade`) or to recover data (`restore`).

What `reset` removes (this compose project only):

- the containers, and every local Docker volume of this compose project (the database, Redis and
  the MCP logs: `<project>_postgres_data`, `<project>_redis_data`, ...). `reset` lists them from
  Docker by the compose project label, and `--dry-run` shows the exact names;
- `.env`, so the next install generates a new `ENCRYPTION_KEY`;
- `.qradar-dash/state.json`, which makes `install` refuse while it records an installed version;
- `.secrets/install.answers`, so `install` asks again and does not reuse the pre-production
  console URL and paths.

What it keeps: the backup root and every backup in it, the other files in `.secrets/`, the two
external Docker volumes (QRadar token and CA, proxy certificates) unless you pass
`--include-secrets`, this checkout and the loaded images. No backup is taken unless you pass
`--backup`; the backup is verified and, if it fails, nothing is removed.

```bash
./qradar-dash reset --dry-run          # print the plan, change nothing
./qradar-dash reset                    # type the project name to confirm
./deploy/load-secrets.sh               # the production token and CA
./qradar-dash install                  # answer the questions for the production console
```

Then register the console, run the sync, and validate the profiles again. Data from the
pre-production console is gone, and QRadar log source ids differ between consoles, so a profile
that was valid there is not valid here until it is validated on the new console. Run the history
backfill if you want the new console's past.

For a scripted run use `--non-interactive --yes`; without `--yes` it refuses.

### Carry the operator configuration across: `config-export` and `config-import`

`reset` wipes the database, so every profile, policy, watch, dashboard and mail setting is gone
with it. Two commands move that operator configuration as one secret-free JSON file (D-78):

```text
./qradar-dash config-export [--label LABEL] [--instance NAME] [--output FILE]
./qradar-dash config-import FILE (--dry-run | --apply) [--instance NAME] [--non-interactive --yes]
```

Export writes the file on the host; import merges it into this site's database against the
inventory written by `sync`, calling no QRadar API and deleting nothing.

Run the steps in this order:

1. **On the pre-production host, write the file before `reset`:**

   ```bash
   ./qradar-dash config-export --label preprod
   ```

   `--label` is recorded as `source_label` (letters, digits, `.`, `_`, `-`, up to 64 characters;
   a console name that is not one needs `--label`, `backend/app/cli/config.py:68-83`;
   `backend/app/config_transfer/document.py:56`). `--output FILE` defaults to
   `qradar-dash-config.json` in the run directory, replaces any existing file with mode `0600`,
   and prints the path on success (`qradar-dash:5008-5009, 5025-5026, 5050, 5058`).

2. **Copy the file to the production host** over a protected channel, keeping mode `0600` on both
   hosts. It holds no credentials, but it is an admin-level description of the site.

3. **Wipe and reinstall against production:**

   ```bash
   ./qradar-dash reset
   ./deploy/load-secrets.sh
   ./qradar-dash install
   ```

   `reset` takes no backup unless `--backup` is passed. `install` registers the production console
   (`qradar-dash:1601`).

4. **Run the first sync and wait for the inventory to fill.** Import resolves names against local
   rows, so a file imported before sync resolves nothing. Run log-source sync and rule sync
   (`python -m app.cli.sync rules --instance <console name>`, or `all`;
   `backend/app/cli/sync.py:36-47, 105-125`).

5. **Read the plan with a dry run:**

   ```bash
   ./qradar-dash config-import qradar-dash-config.json --dry-run
   ```

   A dry run rolls back the plan and commits only its audit row (`"committed": false`), printing
   the JSON report on stdout. With two or more consoles registered, pass
   `--instance <console name>` to either command. Both commands
   target the sole registered console or `--instance NAME` and exit `2` if none or several are
   registered without it (`backend/app/cli/config.py:42-64`).

   Each row appears under one outcome, keyed by `key` (natural key fields joined with `|`):

   | Outcome | Meaning |
   |---|---|
   | `created` | The target has no row for this natural key, so import writes it (a seed update to `search_cadence`, `ldap_config`, default log-source SOC fields, default built-in classification rules, or analytics-rule SOC fields that still matches its seed default is also reported as `created`). A created profile's reason is `inserted disabled and UNVALIDATED` (`backend/app/config_transfer/import_bundle.py:197-204, 313-317, 677-681, 1167-1171`). |
   | `unchanged` | The stored row equals the file, ignoring `enabled` and validation columns, so a re-import is `unchanged` (`backend/app/config_transfer/import_bundle.py:407-413, 798-800, 841-842, 921-923, 972-975`). |
   | `conflict` | The stored row differs from the file and is not an untouched seed default; it is left unmodified (`backend/app/config_transfer/import_bundle.py:1169, 411-413, 568-570, 735, 800, 842, 923, 972-973`). |
   | `unresolved` | The row was skipped due to an ambiguous or missing reference (no match or multiple matches in the local inventory), an export `warnings` entry, a `duplicate natural key in the file`, a webhook URL failing the new host's egress guard (`destination URL failed the egress guard`; fix reachability or set `allow_private` / `allow_http` on the source and export again), or a section `not applied in this release` (`backend/app/config_transfer/import_bundle.py:555-558, 1177-1195`). |

   Rows resolving to a single log source also carry `"local_id"` (`log_source.id`), which the IPS
   HTTP routes take (`import_bundle.py:108-109, 1185-1192`; `report.py:20-25`). Import refuses the
   whole file (exiting `2`) when it exceeds 5,000,000 bytes, is not valid JSON, has an unknown
   top-level key or section, has `schema_version` other than `1`, or has a secret-shaped key
   (`backend/app/cli/config.py:107, 179-181`; `document.py:20, 137-172`). Any other failure exits
   `1` (including wrapper `config-export` if not installed or the directory is missing); a report
   with `conflict` or `unresolved` rows exits `0` (`backend/app/cli/config.py:157, 182-186`;
   `qradar-dash:5005-5014`).

   The in-container forms print to stdout and read import JSON from stdin (`backend/app/cli/config.py:101, 107, 155`):

   ```bash
   umask 077
   docker compose exec -T backend python -m app.cli.config export --label preprod [--instance <console name>] [--actor ACTOR] > qradar-dash-config.json
   docker compose exec -T backend python -m app.cli.config import --dry-run [--instance <console name>] [--actor ACTOR] < qradar-dash-config.json
   docker compose exec -T backend python -m app.cli.config import --apply --yes [--instance <console name>] [--actor ACTOR] < qradar-dash-config.json
   ```

   A shell `>` redirect uses the shell's umask rather than `0600`; run `umask 077` first or
   `chmod 600 qradar-dash-config.json`. In-container `import --apply` without `--yes` exits `2`
   (`backend/app/cli/config.py:175-176`).

6. **Apply the import:**

   ```bash
   ./qradar-dash config-import qradar-dash-config.json --apply --non-interactive --yes
   ```

   `--apply` writes in one transaction. If an unhandled error rolls the transaction back, nothing
   is committed, `"committed": false` is printed, and the command exits `1`
   (`backend/app/cli/config.py:133-152`). On a TTY, `config-import FILE --apply` runs a dry run
   first and asks you to type the console name (`"instance"`) before applying; anything else exits
   `1` (`qradar-dash:5139-5176`). `--actor` defaults to the host user through the wrapper
   (`qradar-dash:5016, 5111`).

### What the file holds, and what it never holds

It holds this product's operator configuration: log-source SOC fields, classification overrides and
rules, analytics-rule SOC fields and explicit technique mappings, WAF, IPS and identity profiles,
property watches, alert and outage policies, open suppressions, notification destinations and rules,
e-mail templates and recipients, LDAP directory settings, search cadence, scheduled searches with
their current AQL, custom rules, dashboards and dashboard preferences
(`export_bundle.py:66-86, 157-719`).

It never holds the QRadar token, any password or hash, webhook HMAC secret or headers, SMTP
password, users, sessions, roles, or any measured data, baseline, anomaly or raw event. Export and
import refuse any file containing a key in `SECRET_KEYS` (`sec_token`, `hashed_password`,
`hmac_secret`, `headers`, `password`, `token`, `token_hash`, `smtp_password`, `authorization`),
and allow `url` only inside `notification_destinations` (`document.py:42-52, 175-202`). A webhook
destination travels with its `url`, `allow_http`, `allow_private` and
`"secrets_omitted": ["hmac_secret", "headers"]` (`export_bundle.py:386-389`).

### What the operator does after the import

- **Profiles** arrive `enabled=false` and `UNVALIDATED`.
  WAF and identity validate inline and enable via the CLI, requiring `--instance <console name>`
  and `--log-source <QRadar integer log source id>` (not `local_id`;
  `backend/app/cli/waf.py:60-70, 288-290`; `backend/app/cli/identity.py:49-63, 117-118`):

  ```bash
  docker compose exec -T backend python -m app.cli.waf validate \
    --instance <console name> --log-source <QRadar log source id> [--json]
  docker compose exec -T backend python -m app.cli.waf enable \
    --instance <console name> --log-source <QRadar log source id>

  docker compose exec -T backend python -m app.cli.identity validate \
    --instance <console name> --log-source <QRadar log source id> [--json]
  docker compose exec -T backend python -m app.cli.identity enable \
    --instance <console name> --log-source <QRadar log source id>
  ```

  WAF and identity CLI `validate` run inline (read-only against QRadar) and exit `2` when the
  profile is not `VALID` (`waf.py:154-169`; `identity.py:92-105`). `enable` refuses a profile that
  is not `VALID` (`waf_profiles.py:158-169`; `identity/profiles.py:153-160`).
  IPS has no CLI. Post to the HTTP routes from a host that can reach the backend, using the
  `qd_session` cookie of an `admin:ips` operator, `{log_source_id}` set to `local_id`, and an
  `Origin` (or `Referer`) matching `PUBLIC_ORIGIN` (`ips.py:203-209, 402-451`;
  `router.py:185`; `csrf.py:47-55`):

  ```bash
  curl --fail -b qd_session=<session cookie> -H "Origin: $PUBLIC_ORIGIN" -X POST https://<host>/api/v1/ips/log-sources/<local_id>/profile/validate
  curl --fail -b qd_session=<session cookie> -H "Origin: $PUBLIC_ORIGIN" -X POST https://<host>/api/v1/ips/log-sources/<local_id>/profile/enable
  ```

  Unlike the WAF and identity CLI `validate` commands (which run inline), the IPS (and WAF) HTTP
  `validate` routes answer `202` and finish in a worker (`409` if one is pending); `enable` answers
  `200` and refuses a profile that is not `VALID` (`ips.py:402-451`;
  `ips_profiles.py:154-170, 303-316`). A profile does not collect until validated and enabled.

- **Webhook destinations, property watches, custom rules, and scheduled searches** arrive
  `enabled=false`, and their prior toggle is recorded in `enabled_on_source` in the file so you
  can enable only those that were on. **Webhook destinations** have no HMAC secret and empty
  headers (`import_bundle.py:558-563`); set `hmac_secret`, `headers` and `enabled=true` via
  `PATCH /api/v1/admin/notifications/destinations/<id>` (permission `admin:notifications`) or the
  admin UI (`notification_destinations.py:96, 290-293`; `rbac.py:173`). **Scheduled searches**
  have `query_version=1` and one `SearchQueryVersion` (`change_note="initial version"`); their AQL
  is validated on import and never executed (`import_bundle.py:818-836`). **Property watches**
  have `history_days` omitted and `history_state=NOT_REQUESTED` (`import_bundle.py:785-789`);
  start a new history bootstrap if you want QRadar's past.

- **Notification rules, e-mail templates, and e-mail recipients** keep the `enabled` value from
  the file (`import_bundle.py:589-596, 625-631, 649-655`).

- **Dashboards** keep the pre-production `owner_subject`. Run both `UPDATE` statements only after
  the last import; dashboards match on `(owner_subject, name)` (`import_bundle.py:964-975`), so
  rehoming earlier creates duplicates on re-import:
  ```sql
  UPDATE dashboard SET owner_subject = '<new subject>' WHERE owner_subject = '<old subject>';
  UPDATE user_dashboard_pref SET owner_subject = '<new subject>' WHERE owner_subject = '<old subject>';
  ```

- **Cadence and LDAP** update the seeded rows (`search_cadence`, `ldap_config`, reported as
  `created`) only when `updated_by` is `NULL` and the row equals the seed default, bumping
  `ldap_config.version` by 1 (`import_bundle.py:313-317, 672-682`).

- **Rows waiting on collection.** A custom rule or dashboard bound to a WAF endpoint, IPS entity
  or watch value that collection has not yet admitted is `unresolved`; run the same import again
  after collection.

- **SMTP** is not in the file; it is read from environment variables (`config.py:1278-1286`).

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

### Enable JDBC monitoring after an upgrade

JDBC monitoring is on by default (D-104): after the upgrade gates are green
(`./qradar-dash doctor` has no `FAIL`), the `collect_jdbc_configs` task runs with no new switch to
turn on and no new command. It is off only if the site explicitly turned it off: `.env` sets
`JDBC_COLLECTION_ENABLED=false`, or a feature-switch row turns it off. With
`FEATURE_SWITCHES_DB=true`, which is the default, that row wins over `.env` and the code default
until it is reset there; upgrade does not import an `.env` value into the switch table.

To check the effective state, run:

```bash
docker compose exec -T backend python -m app.cli.features show
```

The "JDBC" family's "Collect" switch is shown there; reading the feature overview also needs
`read:features`. To turn monitoring off, set `JDBC_COLLECTION_ENABLED=false` in `.env`, toggle the
"Collect" switch off in the browser at Administration → Features (`/admin/features`; reading needs
`read:features`, changing needs `admin:features`), or run:

```bash
docker compose exec -T backend python -m app.cli.features set jdbc_collection_enabled off
```

To restore the default on, use the "Reset to .env/default" control on the "JDBC" row under
Features or run:

```bash
docker compose exec -T backend python -m app.cli.features reset jdbc_collection_enabled
```

The `collect_jdbc_configs` task runs on the `qradar_walks` queue every
`JDBC_WALK_INTERVAL_SECONDS` (default 900 s). While the switch is off the task returns
`disabled` and builds no provider. After the first walk with the switch on, open Log sources →
"View JDBC" (`/log-sources/jdbc`). The page lists the JDBC-like sources — protocol 8, plus the
unverified protocols 20, 21, 37, 42, 49, 55 and 67 — with the columns Source, Database, Host,
QRadar enabled, Connection, Flow, Events (count) and Observed UTC. Before any walk it reads
"JDBC inventory has not been collected."; a finished inventory with no matching sources reads
"No JDBC sources were found in the completed inventory."; neither is a failure. The "Refresh
JDBC" button appears only for administrators (`admin:*`); a read-only analyst gets no control.
The view stays useful with the switch off: it still shows stored configuration and labels a
source disabled in QRadar.

### Enable QRadar source pauses after an upgrade

QRadar source pauses are gated by a single switch, `qradar_source_pause_enabled`, shown in the
feature overview as the "QRadar source pauses" family with the "Pause" toggle. With the switch
off nothing changes: no pause rows are written, no incident is closed for a disable, and
all counts keep their old meaning. With the switch on, a log source whose stored QRadar `enabled`
flag is false is no longer evaluated for NO_EVENTS; its open incidents are closed without a
verdict, with the fixed reason "Closed without a verdict: source disabled in QRadar."; buckets
that overlap a pause are marked not judged rather than given a verdict, and are dropped from
training; and active-source counts exclude QRadar-disabled sources. The counts change on the Log
Sources list and detail and their anomaly list, on the Anomalies list and summary, and on the
Dashboard overview. Those are the reads that must agree after activation.

Apply the switch in this order. The release ships with the switch off; upgrade every worker and
API to the new release first. Then wait until runs that started with the old, off snapshot have
finished: the product does not expose a per-run switch snapshot or a list of in-flight worker
runs (the Queue page is an analyst triage queue, not a worker run list), so wait one evaluation
interval — the `evaluate-anomalies` beat task runs every `ANOMALY_EVALUATION_CADENCE_SECONDS`
(60 seconds). Next set the switch, either in the browser at Administration → Features
(`/admin/features`; reading needs `read:features`, changing needs `admin:features`) — the
"QRadar source pauses" family, toggle "Pause" — or offline:

```bash
docker compose exec -T backend python -m app.cli.features set qradar_source_pause_enabled on
```

With `FEATURE_SWITCHES_DB=true`, which is the default, that row wins over the
`QRADAR_SOURCE_PAUSE_ENABLED=false` code default. Setting the variable in `.env` only changes
the default used when no row exists; upgrade does not import it into the switch table. Then run
one log-source inventory reconciliation so the stored enabled flags are refreshed from QRadar,
either with the CLI:

```bash
docker compose exec -T backend python -m app.cli.sync log-sources --instance <name>
```

or with the "Sync now" control on the Fleet sources page (`POST /fleet/sources/sync`); the
`POST /log-sources/sync` route queues the same worker task. Finally wait the API switch cache
interval, `EFFECTIVE_CACHE_SECONDS` (5 seconds), before comparing the counts on Log Sources,
Anomalies and the Dashboard overview. A write invalidates the cache on its own replica; another
replica sees the change within that interval.

Stale inventory does not stop collection or evaluation. With the switch on the product keeps
using the last known stored enabled flag, and a successful sync is what reports a new disable
and closes the incident; an unobserved disable during an outage may raise NO_EVENTS until that
sync. The last pass and its age are shown separately, never as a healthy verdict. On the
Dashboard overview, `inventory_state` (`NOT_COLLECTED`, `COMPLETE`, `FAILED`) says only whether
the last inventory pass is missing or failed — a three-day-old complete pass still reads
`COMPLETE` — not how old that pass is. Age is shown by the "Last complete inventory" time and the
"Inventory stale" label in the JDBC view, and by the last-pass line in the Fleet sources "Sync
now" area. There is no fixed inventory-age threshold that suspends evaluation.

A fresh 0.1.17 install has no pre-policy baselines and relearns normally; there is no baseline
cutover step. An existing lab or demo database that holds old, disabled zeros cannot be
certified by a normal rebuild: it must be reset and reseeded by a separate authorized operator
task before acceptance. The product has no command that does this, and no migration deletes demo
data.

The release ships with the switch off; turning it off again restores the previous behaviour and
is a rollback. Set it off explicitly, either with the "Pause" toggle on the "QRadar source pauses"
row under Features or with the CLI:

```bash
docker compose exec -T backend python -m app.cli.features set qradar_source_pause_enabled off
```

Confirm the result with `docker compose exec -T backend python -m app.cli.features show`. "Reset to
.env/default" (or `python -m app.cli.features reset qradar_source_pause_enabled`) deletes the
switch row and falls back to the `.env`/default value: it leaves the switch off only if `.env`
does not set `QRADAR_SOURCE_PAUSE_ENABLED=true`. Where that variable might be set, use the explicit
`set … off` above so the rollback is deterministic.

The write changes only the `feature_switch` row: pause rows are not deleted and remain stored,
ignored while the switch is off, so a later rollback does not destroy pause history.

#### Absent from completed inventory

With the pause switch on, a stored source missing from two complete, validated, nonempty
slim-inventory snapshots opens an `ABSENT` pause. This means the API token did not see the
source; it does not certify physical deletion. A failed, partial, unvalidated, malformed,
duplicate-ID or empty walk cannot confirm absence. A gap in the separate JDBC parameter walk
cannot confirm it either. Stale inventory alone never opens a pause.

The pause starts at the later of the previous complete inventory time and 24 hours before the
confirming pass. Open source-volume incidents and alerts close atomically with the reason
"Closed without a verdict: source absent from the completed QRadar inventory." The stored QRadar
`enabled` flag and JDBC configuration are retained; JDBC details show dated configuration history.
The source stops contributing to active collection/evaluation targets, and pause-overlapping
buckets are excluded from evaluation and training. Seeing the source again closes ABSENT at the
next whole bucket boundary, even in a partial pass. A reappearing disabled source keeps its
independent QRADAR_DISABLED pause.

The mass-absence breaker holds a pass when either its new candidates or confirmations exceed
`max(20, ceil(2% of the previous validated complete inventory count))`. It marks none absent.
`doctor` reports the last run's held count as WARN and names the accept command. Check the token's
visibility first. If the sources were intentionally removed, an administrator with shell access
can inspect and accept the hold:

```bash
docker compose exec -T backend python -m app.cli.inventory absence show --instance <name>
docker compose exec -T backend python -m app.cli.inventory absence accept --instance <name> --actor <operator>
```

Accept refuses while pauses are off or the last inventory run is not held. It writes an audit
entry with the actor, instance ID and held count, then arms a one-shot breaker lift for that
instance. It does not certify the missing set or bypass snapshot validation. Subsequent complete
validated passes still require two-pass evidence: the first stores candidates, the next confirms.
Acceptance expires 24 hours after arming. An expired accept or one with a missing, malformed or
timezone-naive arm time no longer lifts the breaker on that pass. A complete validated nonempty
pass, whether evaluated or held, clears it with reason `expired` or `unreadable_arm_time`.
Before expiry, acceptance clears when a pass confirms any absence or finds zero newly missing
sources (sources already ABSENT are excluded). Incomplete, unvalidated, zero-row or failed passes,
and passes with dropped rows or duplicate IDs, never clear acceptance, even after expiry;
positive presence still removes its candidate. With pauses off, acceptance is untouched.
A repeated accept while armed makes no second audit entry. `absence show` is read-only and prints
state, candidate/confirmation/held counts, whether acceptance is armed, its age and its UTC expiry
time. `doctor` explains the expiry and keeps a separate armed warning visible even
if the latest run is skipped. These commands only write the application's database; the product
never removes or changes a QRadar source.

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


### REL-17 upgrade from 0.1.16: QRadar walk isolation

This upgrade adds `celery-walker`, using the existing backend image. The updated
`qradar-dash upgrade` starts and checks it automatically on online and offline hosts.
The default worker consumes `celery` only; collection walks use `qradar_walks`.
Confirm both services in `./qradar-dash status` and the `celery_walker` check in
`./qradar-dash doctor`. See the configuration reference for
`WALKER_CONCURRENCY` (default `2`).

An operator-owned Compose override must give `celery-walker` the same QRadar egress
networks as `celery-worker`. For the libvirt example, add a `celery-walker` service
with `networks: [qradar_vm]` to that override before upgrading. An older custom stack
without a walk-queue consumer now fails doctor explicitly; queued walks cannot run
until a consumer starts.
