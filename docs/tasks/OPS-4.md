# Task OPS-4 — `./qradar-dash install`: preflight, the wizard, secrets, migrate, the break-glass admin, start, summary

Read `AGENTS.md` at the repository root first and follow it exactly. §2 rule 5 (no secrets anywhere)
and §5 (SELinux) matter most.

Then read `docs/OPERATIONS-DESIGN.md`. It is binding:
- **§3** in full;
- §2.3 and §2.6, for the `--build` and `--offline` fallbacks;
- §3.4, for the proxy;
- §10.2's OPS-4 line;
- Appendix A and Appendix B.

Decision D-20 applies: the first version is 0.1.0, and TLS terminates on the **bundled proxy**,
with `--external-proxy` as the alternative.

OPS-3 (the release workflow and the GHCR images) is **not built yet**. Build the script so that
`--build` works end to end now. Write the pull path (`docker login ghcr.io` with the token file,
then `docker compose pull`) to the design, but its live test waits for OPS-3. Say in the report
exactly which paths you exercised.

## Build (design §3)

- **`qradar-dash`** at the repository root.
  - bash, `set -euo pipefail`, `docker compose` v2 only, no Python on the host.
  - Subcommands for this task: `install`, `status` and `logs`. Upgrade, rollback and backup
    belong to OPS-5 and OPS-6; leave clear stubs that exit non-zero with "not built yet", so
    the command surface is visible.
- **Preflight (§3.2).**
  - Check Docker and the Compose version, CPU, RAM, disk, free ports, the clock, and the
    SELinux mode.
  - **Never change** the SELinux mode. Use the `deploy/load-secrets.sh` approach.
  - Fail before writing anything when Docker is missing.
- **The wizard (§3.3).**
  - Interactive, plus `--non-interactive <answers file>`. The answers file is mode `0600` and
    holds no secret.
  - Generate `ENCRYPTION_KEY` and the DB password. The QRadar token and CA go under `.secrets/`
    the way `load-secrets.sh` expects.
  - The proxy and TLS choice: the bundled proxy by default, or `--external-proxy`.
  - The installer does **not** ask for LDAP. LDAP is set up in the UI.
- **Then (§3.5):**
  - pull or build;
  - migrate;
  - create the roles the way the app does at startup;
  - create the break-glass admin with `python -m app.cli.users create-admin` and show its
    one-time password once;
  - start, wait for health, and print the summary (§3.6).
- **Never call `python -m app.services.seed`.** It inserts a mock QRadar instance (design §3.5).
- **An idempotent re-run (§3.7):** never rotate `ENCRYPTION_KEY`, and never call `create-admin`
  again when an admin exists.
- **The production compose file** the design names, and the proxy image or config §3.4 asks
  for. Never `:z`. `:Z` only under AGENTS.md §5's rule.

## Testing on this machine — hard rules

- Work and test **only** in your worktree. Its `.secrets/` is its own and is gitignored. Never
  read or write the main checkout's `/home/efe/Documents/QRadar-Dash/.secrets/`.
  - For the test install, use a **dummy** QRadar token file, a dummy CA file and a dummy QRadar
    URL from your scratch directory.
  - The install must not need a reachable QRadar to come up. If the design says otherwise,
    report it.
- Use your own compose project name (`-p qradar-dash-ops4`) and host ports that are free and
  not in use.
  - Port 5433 belongs to the test DB container `qradar-observability-test-test-db-1`. Never
    stop, change or remove that container, its volume, or any container, image, volume or
    network you did not create.
  - Never run `docker system prune` or `docker volume prune`.
- At the end, remove what you created: `docker compose -p qradar-dash-ops4 down -v`, and the
  images you built under a tag of your own. List the remaining containers to show that only the
  pre-existing ones are left.
- **Shellcheck.** It is not installed system-wide. Use `pip install shellcheck-py` in a venv
  under your scratch directory. The script must be shellcheck-clean.
- A fixture test of the non-interactive parser: a missing token file, a non-https QRadar URL,
  and a bad port are each refused before anything is written. Pick a harness that runs in CI,
  for example a pytest that runs the script with `bash` against fixture answer files.

## Report

`docs/tasks/OPS-4.report.md` (AGENTS.md §6), plus:
- the exact commands an operator types, and the wall minutes of your `--build` install, from
  start to summary;
- what a re-run did;
- what is left for OPS-3 (the pull path) and for OPS-10.

## Allowed files

- `qradar-dash` (new)
- the production compose file and the proxy config or Dockerfile the design names (new)
- `deploy/` (new files, and `load-secrets.sh` only if the design requires it)
- `.gitignore` (the answers file and any generated files)
- a test file under `backend/tests/unit/` or a new `tests/ops/`, with a note in the report on
  how CI runs it
- `README.md` ("Installation" only: the new one-command path)
- `docs/tasks/OPS-4.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops4/`, never `/tmp`.
Nothing runs against a real QRadar.
