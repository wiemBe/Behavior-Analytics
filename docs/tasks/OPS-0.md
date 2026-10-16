# Task OPS-0 — design: install, release, upgrade, backup, rollback and troubleshooting without AI

Read `AGENTS.md` at the repository root first and follow it. §2 rules 1 and 5 matter most:
read-only against QRadar, and no secrets anywhere. Then read:
- `README.md`: "Installation", "Production: one origin for UI and API", "Upgrading" and
  "Security model";
- `docker-compose.yml`, `docker-compose.override.yml`, `Makefile`, `deploy/load-secrets.sh`,
  `deploy/postgres/`, `backend/Dockerfile`, `frontend/Dockerfile`, `.github/workflows/ci.yml`;
- `backend/app/core/config.py`, for every setting an operator may need.

This is a **design task. Write no code.** The deliverable is `docs/OPERATIONS-DESIGN.md`.

## What the user decided (2026-10-15 ~23:25, PLANNER §4 D-16)

- **The production host has no access to any AI.** AI sites are blocked; Docker registries and
  git are reachable. Whoever operates it must be able to install, upgrade, diagnose and recover
  using only the documentation, built-in diagnostics and logs.
- **Installation must be painless:** the app should come up with as few commands as possible.
  The target is `git clone …`, `cd`, then `./qradar-dash install`.
- **Updates:** a few minutes of downtime per upgrade is acceptable. The databases must survive
  every upgrade, and a failed upgrade must be recoverable.
- **The orchestrator's direction:**
  - versioned, prebuilt images are pushed to GHCR (the repository is on GitHub), so production
    pulls and does not build;
  - a local build stays the fallback;
  - one `qradar-dash` operator script provides `install`, `upgrade <version>`, `rollback`,
    `backup`, `restore`, `status`, `logs`, `doctor` and `support-bundle`;
  - `upgrade` takes an automatic backup before migrating;
  - rollback means returning to the previous image tags and restoring the pre-upgrade backup,
    because an Alembic downgrade drops data.

## The document must answer, with evidence from the repository (`file:line`)

1. **Versioning.**
   - The version scheme, and where the version lives: one source of truth.
   - How it reaches the images (labels, build args), the API (a `GET /api/v1/version` or an
     extension of the health route) and the UI (where it shows).
   - Release notes.
2. **The release pipeline.**
   - A GitHub Actions workflow that, on a tag, runs the gates, builds the images (backend,
     frontend, migrate, postgres, the pinned qradar-mcp, any others in `docker-compose.yml`) and
     pushes them to GHCR with the version and SHA tags.
   - Reproducibility: the backend has **no lock file** today (`pip install .` in the
     Dockerfile). Propose a lock (pip-tools or uv) and its update policy.
   - `NEXT_PUBLIC_API_BASE_URL` is inlined at build time (README "Production"). Show how one
     image serves every site: the relative `/api/v1` behind the one-origin proxy.
   - Registry authentication for a private repository: a read-only token on the production
     host, stored the way other secrets are.
3. **`./qradar-dash install`, step by step.**
   - **Preflight:** Docker and Compose versions, CPU, RAM, disk, free ports, SELinux mode
     (never change it; use the `load-secrets.sh` approach), clock and timezone.
   - **The `.env` wizard:** which questions it asks, which values it generates
     (`ENCRYPTION_KEY`, DB password), where the QRadar token and CA go (`.secrets/`,
     `load-secrets.sh`), and the proxy and TLS choice.
   - Then: pull, migrate, seed (roles, admin user), start, health wait and a summary printout
     (URL, the first login, what to do next).
   - Idempotent re-run.
   - The `--build` fallback.
4. **`upgrade <version>`.**
   - Preflight, which includes reading the target's release notes for the minimum previous
     version.
   - A TimescaleDB-aware backup (`timescaledb_pre_restore`/`post_restore`, or `pg_dump` with
     the right flags), plus the backup of `.env` and the volumes that matter (never the
     secrets in clear).
   - Pull; stop the writers; migrate; start; health and smoke checks.
   - Record the outcome.
   - The automatic rollback trigger.
   - Expected downtime.
5. **`rollback`, `backup` and `restore`.** Exact semantics, retention of backups, disk-space
   guard, and a restore test on a scratch database.
6. **`doctor`.** A diagnostics command, run inside the backend container, that checks:
   - DB connectivity and the migration head against the code's expected head;
   - Redis;
   - Celery workers and beat liveness;
   - QRadar reachability through the read-only provider, the token's validity and the TLS
     chain and expiry;
   - per-family collection lag (watermarks against cadence) and the last successful run per
     family;
   - recent error counts;
   - disk.

   Each check reports PASS, WARN or FAIL, with a one-line cause and a pointer to the runbook
   entry. No QRadar write, and no secret in the output.
7. **`support-bundle`.** A redacted archive (logs, `doctor` output, config without secrets,
   versions, migration state) that can be carried off the host for analysis. Spell out the
   redaction rules and their test.
8. **The runbook `docs/OPERATIONS.md`** (to be written in a later task): its outline,
   **symptom-indexed** (symptom, likely cause, the command to confirm it, the fix). List at
   least 25 concrete symptoms drawn from this codebase's real failure modes. Search the code
   and the task reports for them, for example:
   - QRadar 401/403;
   - TLS AKI / CA errors;
   - Ariel timeouts and truncation;
   - `NOT_COLLECTED` because a family is disabled;
   - an invalid WAF/IPS profile;
   - the beat stopped;
   - a migration out of date;
   - the CORS one-origin mistake;
   - SELinux denials;
   - a full disk;
   - clock skew.

   The language of the runbook is an **open question for the user** (Turkish, English, or both).
9. **Monitoring the monitor.** What the app already exposes (health, status pill, detection
   health, search cadence lateness) and what an operator should watch daily.
10. **Task split** for building it (`OPS-1…`): narrow tasks, each with the agent kind,
    dependencies and acceptance. One task must be a **fresh-VM install test**: run
    `./qradar-dash install` on a clean machine or container, then count the commands and the
    minutes.
11. **Open questions for the user**, marked as such.

## Rules

- Change no file except `docs/OPERATIONS-DESIGN.md` (new) and `docs/tasks/OPS-0.report.md`
  (new).
- Cite `file:line` for every claim about the code.
- Run nothing against QRadar.
- Do not commit.
- Write scratch only under `/home/efe/Documents/QRadar-Dash-work/ops0/`, never `/tmp`.
