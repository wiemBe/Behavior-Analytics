# OPS-9 round 3 report — the runbook after OPS-10, T-127 and T-128

## 1. Files changed

- `docs/OPERATIONS.md` — the Turkish operator runbook, updated in the eight places the task
  names (host prep, install-after-effects, upgrade/rollback doctor output, `celery_beat` /
  OPS-R9, `pg_dump` warnings, `bundle` message, the API `Origin` header, the OPS-10 measured
  numbers). No heading, no symptom id and no symptom order changed.
- `docs/tasks/OPS-9r3.report.md` — this file (new).

Nothing else was touched. No commit.

## 2. What I did, section by section

**Top of the file (commands that exist).** Rewrote the `bundle` sentence. `./qradar-dash bundle`
now prints the T-127 message and exits 64; the `install` help line is quoted too. Both are copied
verbatim from `qradar-dash`.

**`Kurulum` (host preparation).**
- The clone block now shows the registry install (no submodule) first and the `install --build`
  clone second, with `--recurse-submodules` only for `--build` (`qradar-mcp` submodule). A
  registry install needs no submodule.
- New subsection **Operatör hesabı ve yedek dizini**: a dedicated operator account that can run
  `docker`; the explicit warning that on RHEL/Rocky/Alma **`operator` is an existing system
  account** (uid 11, no login) and must not be used, with `qdops` as the example name; and the
  pre-install backup-directory commands (`sudo mkdir -p`, `sudo chown`, `sudo chmod 700`).
- `Ana makine boyutu` minimum line: "80 GiB disk" is now "**Docker kökünde 80 GiB boş** disk"
  (the installer's preflight number; `DISK_MIN_KB=$((80 * 1024 * 1024))`). RAM stays 8 GiB and
  the CPU minimum is the script's 4 (`CPU_MIN=4`).
- `Adım sırası`: added the OPS-10 measured install time (1 dk 40 sn), labelled as a test-VM
  measurement and not a promise.

**`Kurulum` — what the operator sees right after install (Özet ve ilk oturum açma).**
- The exit-code bullets now say that when the QRadar check fails, the stack **and**
  `.qradar-dash/state.json` are in place (T-127), so `status`, `upgrade` and `rollback` work;
  fix QRadar and re-run, or check `./qradar-dash status`. It also says no state file is written
  when the stack itself did not come up.
- The first sign-in paragraph now carries the API gate: 403
  `A password change is required before any other request.` until the password is changed
  (OPS-R27), and the CSRF `Origin` requirement: a command-line API sign-in needs an `Origin`
  header matching the site, else 403
  `Cross-site request refused: the request has no Origin or Referer header.` (OPS-R22).

**`Yükseltme`.** Added a short paragraph (with the real line shape) that `upgrade` and
`rollback` print only `db_connect` and `migration_head`, followed by
`Full report: ./qradar-dash doctor` (T-127), and why the rest is hidden.

**`Yükseltme` → `Kesinti`.** Removed "Ölçülmüş bir değer yoktur." and quoted the OPS-10 test-VM
measurements: upgrade downtime ≈ 15 sn, rollback ≈ 13 sn, explicitly "bir söz değildir".

**`Yedek ve geri alma` → `Yedek alma`.** Added that `pg_dump` writes TimescaleDB
`circular foreign-key constraints` / `continuous_agg` warnings; they are expected, the dump is
fine, and the exit code is what counts (0 for a healthy backup).

**`Belirtiler`.**
- **OPS-R9** rewritten: beat now writes its heartbeat at beat start (T-128); a `celery_beat`
  FAIL within the first two minutes after a restart or `upgrade` (`FLUSHALL`) is transient, and
  only a FAIL that lasts **longer than two minutes** is the real symptom.
- **OPS-R27** now names the API 403 body and the routes allowed before the password change.
- **OPS-R42** now tells the command-line reader to send an `Origin` header.

**New symptom id:** none. All of the above fits an existing id (install/QRadar in the install
section and OPS-R1; the CSRF header in OPS-R22/OPS-R42; the beat in OPS-R9; the `pg_dump`
warnings as an expected-output note in the backup section). So nothing was appended after
OPS-R45.

## 3. Commands and messages checked against `qradar-dash`

`./qradar-dash --help` was run; it lists `install status logs doctor backup restore
support-bundle upgrade rollback bundle`. Every invocation line in the doc uses one of those
subcommands with an option from the help text:

- `install` (+ `--build`, `--non-interactive FILE`), `upgrade <version> [--offline]`,
  `rollback [--non-interactive --yes]`, `backup [manual|scheduled|pre-upgrade] [--no-verify]`
  (+ `--print-timer`, `--install-timer`), `restore <backup-directory> [--non-interactive --yes]`,
  `status`, `logs` (+ `frontend`), `doctor`, `support-bundle` — all present in `main()`
  (`qradar-dash:2670-2687`) and in `usage()` (`:34-76`).
- Quoted messages checked letter for letter:
  - `'bundle' is not built yet (the offline bundle is not part of this release; use
    ./qradar-dash install --build from a checkout, or install by pulling from the registry).`
    — `not_built` call at `qradar-dash:2683`; exit 64 from `not_built` (`:2665-2668`).
  - `Not built in this release (use install --build, or pull from the registry).` — `usage()`
    `qradar-dash:49`.
  - `Full report: ./qradar-dash doctor` — `print_deciding_checks` `qradar-dash:1888`.
  - `db_connect` PASS text `database accepted SELECT 1` and `migration_head` PASS text
    `database and image are at <head>` — `backend/app/cli/doctor.py:194`, `:213`.
  - `A password change is required before any other request.` —
    `backend/app/security/auth.py:32`.
  - `Cross-site request refused: the request has no Origin or Referer header.` —
    `backend/app/security/csrf.py:19`.
- Numbers read from the script: 80 GiB Docker root (`DISK_MIN_KB`, `:325`), 20 GiB backup
  (`BACKUP_MIN_KB`, `:326`), 8 GiB RAM (`MEM_MIN_KB` 7.5 GiB, message "at least 8 GiB is
  required", `:324`, `:356`), 4 CPUs (`CPU_MIN=4`, `:323`, `:353`), 180 sn waits, `pg_dump -Fc`
  (`:1474`).

Self-check runs (grep only):

```
$ grep -c "henüz yayımlanmadı" docs/OPERATIONS.md   -> 0
$ grep -c '^### OPS-R' docs/OPERATIONS.md           -> 45
$ grep -o '^### OPS-R[0-9]*' docs/OPERATIONS.md     -> OPS-R1 ... OPS-R45, in order, no gaps
$ grep -c '^## ' docs/OPERATIONS.md                 -> 6 (Kurulum, Dizin, Yükseltme,
                                                       Yedek ve geri alma, Tanı, Belirtiler)
```

Scratch under `/home/efe/Documents/QRadar-Dash-work/ops9r3/` was not needed for anything (no
file written there).

## 4. Open points

- **`operator` uid.** The uid-11 fact comes from the OPS-10 report (Rocky 9.8) and the task; I
  did not re-read the Rocky `setup` package on this host. If another distro's `operator` differs,
  the warning is still safe (it only says do not use that name), but the "(uid 11)" detail could
  be wrong there.
- **Submodule scope.** "registry install needs no submodule" is the task's statement; I verified
  the submodule is `qradar-mcp` (`.gitmodules`) and that `install --build` is where a checkout is
  compiled, but I did not run a build to prove a missing submodule fails only there. If a build
  somehow needs the submodule for another reason, the text would overstate it.
- **Doctor line spacing.** The two printed lines in the doc use the runbook's existing example
  alignment, not the exact column width the container produces (`doctor.py` pads to the longest
  id in the report). The ids, the messages and the `Full report:` pointer are exact; only the
  inter-column spaces are illustrative. Not a promise in the sense of a contract.
- **`pg_dump` warning text.** Quoted from the OPS-10 report (`circular foreign-key constraints
  ... continuous_agg`), not from a live `pg_dump` on this host; I ran no backup here.
- **Measured numbers** are OPS-10's on a small test database; the runbook says so and says they
  are not a promise. A large database changes the downtime entirely.
- Not run against a real stack: this is a documentation task; no Docker, no QRadar and no
  database was used. The report's accuracy rests on reading the script and the named sources.
