# Task OPS-9 round 2 — the runbook after OPS-5, OPS-6 and OPS-8 landed

Read `AGENTS.md` at the repository root first and follow it exactly. Then read
`docs/tasks/OPS-9.md` (the round 0 rules: Turkish paragraphs, English commands and outputs,
commands copied from the script and not rewritten) and `docs/tasks/OPS-9.report.md`.

`docs/OPERATIONS.md` was drafted before `backup`, `restore`, `upgrade`, `rollback` and
`support-bundle` existed. They exist now. Bring the runbook in line with the script as built.

## Read first

- The operator script `qradar-dash`: its `usage` text (`./qradar-dash --help`, which is safe to
  run) and the functions `cmd_backup`, `cmd_backup_create`, `cmd_restore`,
  `restore_swap_database`, `restore_start_and_wait`, `cmd_upgrade`, `perform_rollback`,
  `cmd_rollback`, `cmd_status`, `cmd_support_bundle`, `cmd_doctor` and the doctor checks.
- `docs/OPERATIONS-DESIGN.md` §4 and §5, including the **as-built** notes OPS-5 and OPS-6 added.
- `docs/tasks/OPS-5.report.md`, `docs/tasks/OPS-6.report.md`, `docs/tasks/OPS-7.report.md`.

## What to change

1. **Remove every "Bu komut henüz yayımlanmadı (OPS-…)" line** (eight today; find them with
   `grep -n "henüz yayımlanmadı" docs/OPERATIONS.md`). Rewrite the introduction paragraph near
   the top that lists unpublished commands: only `bundle` (the offline bundle, OPS-3/OPS-10) is
   still unbuilt. Check `main()` in the script for the current list.
2. **Every command and option in the runbook matches the script.** In particular:
   - `backup [manual|scheduled|pre-upgrade] [--no-verify]`, `backup --print-timer`,
     `backup --install-timer` (and the lingering note it prints for a user timer);
   - `restore <backup-directory> [--non-interactive --yes]`: the word `restore`; the refusals
     (incomplete directory, a **version that differs either way**, the encryption key, the disk
     guard on Docker's root); the kept `qradar_obs_broken_<stamp>` copy and the printed drop
     command; secrets are not restored automatically;
   - `upgrade <version> [--offline]`: the preflight refusals, the pre-upgrade backup, the
     automatic rollback and its **non-zero exit**, the attempt recorded before the migrate;
   - `rollback [--non-interactive --yes]`: the word `rollback`, its refusals, never a pull;
   - `status` output lines, and `support-bundle`.
   Where a symptom's fix used a placeholder or a guessed message, replace it with the script's
   real message text. Copy messages; do not paraphrase them.
3. **OPS-R41** (`celery_workers`). The text describes OPS-7's original plan (a filtered ping,
   then WARN). As built, `celery_workers` PASSes on any pong (OPS-7 round 1). Rewrite it to the
   as-built behaviour, and replace its confirm command (today a bare `docker inspect`) with a
   command that actually confirms the symptom. Take the command from the doctor code or the
   compose file; do not invent one.
4. Keep every symptom id OPS-R1 to OPS-R42 and their order. Add an id only if a new symptom
   follows from OPS-5 or OPS-6 (for example a refused restore, an automatic rollback, a failed
   rollback); if you add one, number it after OPS-R42 and list it in the report.

## Checks

- `grep -c "henüz yayımlanmadı" docs/OPERATIONS.md` prints 0.
- For every `./qradar-dash …` line in the runbook, the subcommand and each option appear in the
  script's `usage` or argument parser. List each one you checked in the report.
- Run nothing else: no install, no stack, no QRadar.

## Allowed files

- `docs/OPERATIONS.md`
- `docs/tasks/OPS-9r2.report.md` (new)

Do not commit. Scratch goes under `/home/efe/Documents/QRadar-Dash-work/ops9r2/`, never `/tmp`.
