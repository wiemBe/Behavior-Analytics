# Task OPS-9 round 3 — the runbook after the fresh-VM test (OPS-10), T-127 and T-128

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/tasks/OPS-9.md` (Turkish paragraphs; English commands and outputs, copied from the script),
`docs/tasks/OPS-10.report.md` (what a real operator met on a fresh Rocky 9 VM),
`docs/tasks/T-127.report.md` and `docs/tasks/T-128.report.md`, and the current `qradar-dash`.

## What to change in `docs/OPERATIONS.md`

1. **Host preparation** (the install section):
   - The Docker root needs **80 GiB** free (the installer's preflight number; read it from the
     script) and the host at least 8 GiB of RAM and the CPU minimum the script states.
   - Name a dedicated operator account, and warn that on RHEL/Rocky/Alma **`operator` is an
     existing system account** (uid 11, no login): do not use that name.
   - The backup directory must exist and belong to the operator account before `install` (OPS-10
     ran `sudo mkdir` and `chown`); show the commands.
   - `git clone` needs `--recurse-submodules` only for `install --build` (the `qradar-mcp`
     submodule); a registry install works without it. Say which.
2. **What the operator sees right after install** (from OPS-10): the break-glass admin must change
   its password before any other request (OPS-R27); a QRadar check that fails makes `install` exit
   1 but **the stack and its state file are in place** (T-127): fix QRadar and re-run, or check
   `./qradar-dash status`. Update any text that says the state file is written only on success.
3. **Upgrade and rollback output** (T-127): the doctor lines printed are only `db_connect` and
   `migration_head`, followed by `Full report: ./qradar-dash doctor`. Copy the real lines.
4. **`celery_beat` after a restart** (T-128): the beat now writes its heartbeat at start; a
   `celery_beat` FAIL that persists for more than two minutes after a restart is a real symptom
   (OPS-R9). Adjust OPS-R9's text if it says otherwise.
5. **Backup output:** `pg_dump` prints TimescaleDB "circular foreign-key constraints" warnings;
   they are expected and the backup's exit code is what counts.
6. **The `bundle` command:** copy its new message (T-127): not built in this release; use
   `install --build` or the registry.
7. **API calls in the runbook, if any:** a sign-in through the API needs an `Origin` header
   matching the site (CSRF guard; a 403 otherwise).
8. Measured numbers from OPS-10 may be quoted as "measured on a test VM" (install 1 min 40 s;
   upgrade downtime about 15 s; rollback about 13 s), never as a promise.

Keep every symptom id OPS-R1 to OPS-R45 and their order. Add an id only for a new symptom, after
OPS-R45, and list it in the report.

## Checks

- Every `./qradar-dash …` line and every quoted message matches the script; list each one you
  checked in the report.
- `grep -c "henüz yayımlanmadı" docs/OPERATIONS.md` stays 0.
- Run nothing except reading files, `grep` and `./qradar-dash --help`.

## Allowed files

- `docs/OPERATIONS.md`
- `docs/tasks/OPS-9r3.report.md` (new)

Do not commit. Scratch under `/home/efe/Documents/QRadar-Dash-work/ops9r3/`, never `/tmp`.
