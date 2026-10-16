# OPS-4 report: `./qradar-dash install`

## 1. Files changed

- `qradar-dash` (new): bash installer. `install`, `status`, `logs`; stubs for `upgrade`, `rollback`,
  `backup`, `restore`, `doctor`, `support-bundle`, `bundle` (exit 64, "not built yet").
- `docker-compose.prod.yml` (new): release image names (`build:` kept), bundled `proxy` service
  (profile `proxy`), pinned edge subnet and proxy IP, loopback ports, proxy-certs volume.
- `deploy/proxy/Dockerfile`, `deploy/proxy/default.conf` (new): nginx with the config baked in.
- `deploy/frontend/Dockerfile` (new): copy of `frontend/Dockerfile` plus the
  `NEXT_PUBLIC_API_BASE_URL=/api/v1` build arg (see open point 1).
- `deploy/load-secrets.sh`: now `[qradar|proxy|all]` (default `qradar`, behaviour unchanged), volume
  names overridable by `SECRETS_VOLUME` / `PROXY_CERTS_VOLUME`; `proxy` streams `tls.crt` / `tls.key`
  into the separate proxy-certs volume.
- `.gitignore`: `install.answers`, `.install-tmp/`.
- `README.md`: new "Production install (one command)" under Installation.
- `backend/tests/unit/test_qradar_dash_installer.py` (new): 27 tests of the non-interactive parser.
  CI runs it with the existing backend `pytest -m "not integration"` (needs only bash).

## 2. What I did

Operator commands:

```bash
git clone --recurse-submodules https://github.com/wiemBe/QRadar-Dashboard.git
cd QRadar-Dashboard
./qradar-dash install                                   # wizard
./qradar-dash install --non-interactive .secrets/install.answers
./qradar-dash install --non-interactive FILE --build    # no registry
./qradar-dash install --non-interactive FILE --offline qradar-dash-<v>-bundle.tar
./qradar-dash status ; ./qradar-dash logs --tail 50 backend
```

Answers file keys are in `qradar-dash` (`ANSWER_KEYS`). The file must be mode 0600 and holds paths and
names only; a key that looks like a secret is refused. Wizard questions follow design 3.3 (no LDAP).
`--dry-run` runs every check and writes nothing.

Design 3.5 order is followed: preflight, answers, `.secrets` + `.env`, images, Fernet key generated inside
the backend image, `load-secrets.sh`, postgres+redis healthy, migrate, `has-admin` check, `create-admin`
(password on the terminal only, never captured), `qradar add` inside the container, `up --wait`,
`/health/ready`, summary. `app.services.seed` is never called. No `:z`, `:Z`, or bind mount of the checkout.
Exit 0 only when healthy and the QRadar check passed; 1 for a failed QRadar check or unhealthy service;
2 for refused answers; 3 for external proxy without `FORWARDED_ALLOW_IPS`.

### Exercised on this machine (project `qradar-dash-ops4`, ports 18443/18000/13000, dummy token/CA/URL)

- `--build`: ran to the break-glass step in 2 min 4 s wall (build, postgres init, 25 migrations; several
  frontend layers were cached from earlier dev builds, so a cold build is slower). It then failed at
  `create-admin`, see open point 2. I did not get a clean `--build` run start to summary.
- `--offline` (bundle I made with `docker save` of the 4 images + redis, checksummed): load, then the whole flow.
  First complete run, 39 s, summary printed, exit 1 only because the dummy QRadar URL does not resolve
  (expected, design 3.6). Stack healthy: backend, worker, beat, frontend, proxy.
- Through the bundled proxy (self-signed cert, `--resolve`): `/login` 200, `/api/v1/health/ready` 200,
  backend security headers pass through, the printed break-glass password signs in (200), a wrong one is 401.
  The frontend bundle contains `/api/v1` and no `localhost:8000`.
- Re-run (twice): 29 s and 26 s. Hash of `SECRET_KEY`+`ENCRYPTION_KEY`+`POSTGRES_PASSWORD` identical before
  and after; "break-glass admin exists; create-admin is not run again"; the old password still signs in;
  `qradar add` reported "instance updated". The earlier failed first run also proved a re-run after a
  partial install is safe (key kept, admin created on the retry).
- Pull path: `docker compose pull` against a non-existent prefix fails with the designed message
  (needs `GHCR_USER` / `GHCR_TOKEN_FILE`). The `docker login ghcr.io` success path and a real GHCR pull were
  NOT exercised (OPS-3).
- `--external-proxy`: exits 3 with the instruction while `FORWARDED_ALLOW_IPS` is empty; backend not started.
- QRadar TLS preflight: verify error against a wrong CA is FAIL (nothing written); right CA is ok; an
  unreachable console is a WARN (the install must not need a reachable QRadar).
- Missing Docker: tested in pytest with a PATH that has no `docker`; exit 1, nothing written.

## 3. Gates

- `shellcheck qradar-dash deploy/load-secrets.sh` (shellcheck-py in `QRadar-Dash-work/ops4/venv`): clean.
- `pytest tests/unit/test_qradar_dash_installer.py`: 27 passed. Mutation check: loosening the URL
  check, the token check, the 0600 check and the error report each made tests fail; script restored (cmp).
- `ruff check app tests`: All checks passed. `pytest -m "not integration"`: 3470 passed, 9 skipped,
  1286 deselected. `mypy app` not run (no app code changed).
- Cleanup: `docker compose -p qradar-dash-ops4 down -v` done; I removed only my volumes
  `qradar-dash-ops4-secrets` and `qradar-dash-ops4-proxy-certs` and my 4 image tags
  `qradar-dash-ops4/{backend,frontend,proxy,postgres}:0.1.0`. After: 0 containers, volumes, networks or
  images with `ops4` in the name; 68 containers and 61 volumes remain (all pre-existing, including
  `qradar-observability-secrets` and the running `qradar-observability-test-test-db-1`). SELinux still Enforcing.
  The worktree `.env`, `.secrets/` removed. Scratch under `/home/efe/Documents/QRadar-Dash-work/ops4/`
  (logs there have the dummy password scrubbed).

## 4. Open points

1. **Frontend build arg.** `frontend/Dockerfile` has no `ARG NEXT_PUBLIC_API_BASE_URL` and is outside my
   allowed files, so the prod overlay builds from `deploy/frontend/Dockerfile` (a copy with the arg).
   OPS-3: put the arg in `frontend/Dockerfile`, delete the copy, drop `dockerfile:` from the frontend service.
2. **A fresh `--build` image is broken: no `greenlet`.** `backend/Dockerfile` installs floating ranges;
   today that resolves SQLAlchemy 2.1.4, which does not pull `greenlet`, so `python -m app.cli.users` dies
   with "SQLAlchemy asyncio requires greenlet" (the running app would too). This is exactly the OPS-2 lock
   problem. To finish my test I built the backend image from a scratch Dockerfile with `pip install . greenlet`.
   Until OPS-2 lands, `--build` and any release image built from `main` is broken. Needs an urgent fix.
3. **`has-admin` not added.** Design 3.7 puts it in `app/cli/users.py`, outside my allowed list. The
   installer runs a read-only `psql` count (`app_user` joined to the `admin` role) in the postgres container
   instead. Swap for `python -m app.cli.users has-admin` when a task may edit `users.py`.
4. **Deviation: `POSTGRES_PASSWORD` is `openssl rand -hex 24`, not `-base64 24`.** The password is spliced
   unescaped into the database URL (`config.py` `database_url`), and base64 can contain `/` and `+`.
5. `GET /api/v1/version` is 404 until OPS-1; the installer warns, does not fail.
6. QRadar TLS preflight treats "unreachable" as a warning and "verify error" as a failure (design lists
   only the latter); `--skip-qradar-check` exists for hosts that cannot reach the console.
7. Redis is not digest-pinned (OPS-3). The `qradar-mcp` image is not built or pulled (profile off), so
   `--build` does not need the submodule; design 3.8 says it does.
8. Not built, left to others: the pull path's live test and `bundle` (OPS-3 / OPS-10); backup directory
   0700 is created but nothing writes backups (OPS-5). Ports are env-configurable through the answers file
   (`HTTPS_PORT`, `API_LOOPBACK_PORT`, `UI_LOOPBACK_PORT`, `PROJECT_NAME`, `IMAGE_PREFIX`, volume names,
   `EDGE_SUBNET`, `PROXY_EDGE_IP`) so two stacks can share a host; defaults are the design's 443/8000/3000.
   With `HTTPS_PORT` other than 443 the installer writes `PUBLIC_ORIGIN=https://<name>:<port>`.
9. `.env.example` comment for `AUTH_PROVIDER` (design 3.3) is not updated: file not in my allowed list.
