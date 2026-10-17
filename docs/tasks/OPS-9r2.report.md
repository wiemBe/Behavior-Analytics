# OPS-9 round 2 report

## 1. Files changed

- `docs/OPERATIONS.md`: removed the eight "henüz yayımlanmadı" lines, brought `Yükseltme`,
  `Yedek ve geri alma`, `Tanı` and the `restore`/`rollback`/`upgrade`/`backup`/`status`/
  `support-bundle` text in line with `qradar-dash` as built (real refusals, real messages, real
  flags), rewrote OPS-R41 to the as-built `celery_workers`, and added OPS-R43, OPS-R44, OPS-R45.
- `docs/tasks/OPS-9r2.report.md` (new): this report.

Nothing else was edited. No file was deleted. No commit.

## 2. What I did

Sources: `qradar-dash` (in this worktree, lines read directly and `./qradar-dash --help`),
`docs/OPERATIONS-DESIGN.md` §4/§5/§6/§7/§8 including the OPS-5/OPS-6 as-built notes,
`docs/tasks/OPS-5.report.md`, `docs/tasks/OPS-6.report.md`, `docs/tasks/OPS-7.report.md`, and
`docker-compose.yml` / `backend/app/cli/doctor.py` for OPS-R41.

### Introduction (item 1)

- The paragraph near the top now reads: `install`, `status`, `logs`, `doctor`, `backup`,
  `restore`, `upgrade`, `rollback` and `support-bundle` work today; only `bundle` (the offline
  bundle, OPS-3 / OPS-10) does not. This matches `main()` in `qradar-dash`, where the only stub is
  `bundle) not_built "$cmd" "OPS-3 and OPS-10"`.
- All eight occurrences of the phrase are gone, including the intro sentence (the check requires
  `grep -c` to be 0, so the intro could not keep the phrase). The TOC line now says
  `OPS-R1 ... OPS-R45`.

### `Yükseltme`

- Marker removed; command block is `./qradar-dash upgrade <version> [--offline]`.
- Plain SemVer refusal, the "already installed and healthy" / "recorded but not healthy" / "newer
  is rollback" branches, the pull-before-stop and `--offline` local-image rule, the release-notes
  / `min_from` refusal, the backup-root refusal, the fail-closed `db_connect` + `migration_head`
  PASS rule, the `.env`-vs-state version mismatch, and the `outcome=failed` block (cleared by
  `restored_from`) are all copied from `cmd_upgrade`, `upgrade_doctor_preflight` and
  `upgrade_pull_target`.
- `Yedek, sonra migrasyon` now records the Redis flush, then the attempt record before the `.env`
  edit and the migrate, then the atomic `.env` version edit, exactly as `cmd_upgrade` orders it.
- `Otomatik geri alma` lists each trigger with the script's own line, states the non-zero exit of a
  successful automatic rollback, the `Rollback failed; … Verified backup: <path>` path, and the
  final `upgrade to <target> failed and was rolled back to <old>; the broken database was kept`.
- `Durum dosyasını okuma`: the example `last_upgrade.backup` is now a backup **directory**
  (`qradar-dash-<utc>-<reason>`) as OPS-5 writes it, not a `.dump` file, and notes `restored_from`.

### `Yedek ve geri alma`

- `Yedek nedir`: the `state.json` row says "when it exists"; the `MANIFEST` row now lists the seven
  as-built fields (version, image digests, `alembic_version`, database size, hostname, state
  presence, secret-volume set).
- `Yedek alma`: command `./qradar-dash backup [manual|scheduled|pre-upgrade] [--no-verify]`; the
  staged `.<name>.partial` flow and its cleanup; the verify rule and the
  `pre-upgrade backups must be verified` refusal; `--print-timer` (writes nothing) and
  `--install-timer` (writes and enables root or user units) with the user-timer lingering note;
  the disk guard, retention and the daily `backup scheduled` timer with its 15-minute delay.
- `Geri alma (rollback)`: command `./qradar-dash rollback [--non-interactive --yes]`; the full
  refusal list copied from `cmd_rollback`; the stdin word `rollback` and the
  `--non-interactive rollback requires --yes` rule; the intermediate database renamed to
  `qradar_obs_restore_<utc>` (the design said `qradar_obs_rollback`; as built it is
  `qradar_obs_restore_<utc>`), the kept `qradar_obs_broken_<utc>` copy and the printed drop
  command, "never a pull", and the failed-rollback path.
- `Seçili yedeği geri yükleme (restore)`: command
  `./qradar-dash restore <backup-directory> [--non-interactive --yes]`; the refusals
  (incomplete/unsafe directory, version differing **either way** with both messages, the
  encryption key, the Docker-root disk guard), the word `restore`, the kept
  `qradar_obs_broken_<utc>` copy plus immediate drop command, and the fact that `secrets.tar` is
  **not** restored automatically.

### `Tanı`

- `status`: now lists the real output lines (`docker compose ps`, `API ready: HTTP …`,
  `Installed version`, `Image <service>: <digest>`, `Last upgrade: …` / `Last upgrade backup: …
  (age …)` / `Last upgrade: none`, and the invalid-state `Installed version: unknown` + exit 1).
  The stale "free space on both filesystems" claim is removed — `cmd_status` never prints it.
- `support-bundle`: marker removed; added the staging directory, `<part>.error.txt` behaviour and
  the `docker compose run --rm --no-deps -T backend python -m app.cli.support_bundle` scrub step.

### `Belirtiler`

- All of OPS-R1 … OPS-R42 kept, in order.
- OPS-R11 and OPS-R34 lost their "henüz yayımlanmadı" lines and now say
  `./qradar-dash restore <backup-directory>`.
- OPS-R41 rewritten to the as-built behaviour (see §4 below).
- Three new entries, OPS-R43 … OPS-R45, added after OPS-R42 (see §5 below).

## 3. Checks run

Only file reads, `grep` and `./qradar-dash --help` were run. No install, no stack, no QRadar, no
integration tests. No scratch files were needed under
`/home/efe/Documents/QRadar-Dash-work/ops9r2/`.

```
$ ./qradar-dash --help            -> exit 0; printed the usage block quoted in §6 below
$ grep -c "henüz yayımlanmadı" docs/OPERATIONS.md   -> 0
$ grep -c "OPS-R[0-9]" docs/OPERATIONS.md           -> 101
$ grep -c '^### OPS-R[0-9]' docs/OPERATIONS.md      -> 45
$ for i in $(seq 1 45); do grep -q "^### OPS-R$i\. " docs/OPERATIONS.md || echo MISSING $i; done  -> no output
$ grep -c '^## ' docs/OPERATIONS.md                 -> 6
```

The six `##` headings are still `Kurulum`, `Dizin`, `Yükseltme`, `Yedek ve geri alma`, `Tanı`,
`Belirtiler`.

## 4. OPS-R41, as built

The old text described OPS-7's plan (a filtered ping, then a WARN). As built in
`backend/app/cli/doctor.py` (`check_celery_workers`) and confirmed by `docs/tasks/OPS-7.report.md`
round 1, `celery_workers` sends **one unfiltered** `inspect ping` (2 s timeout) from inside the
backend container:

- at least one pong -> PASS, `f"{len(workers)} worker(s) returned pong: {node_names}"`;
- no pong -> FAIL, `no Celery worker returned pong`, `runbook: OPS-R41`.

So a node-name mismatch is no longer a false FAIL of the whole worker set; it shows up as the
Compose healthcheck flapping. The confirm command is no longer the bare `docker inspect`. It is now
`docker compose ps` — the same health read the doctor code performs
(`dc ps --all --format '{{.Service}}\t{{.State}}\t{{.Health}}\t{{.ExitCode}}'`) — because the
symptom is the worker's **Compose health** flapping while `doctor` `celery_workers` PASSes. The
cause is quoted from the compose file's healthcheck:
`celery -A app.workers.celery_app:celery_app inspect ping -d celery@$$HOSTNAME`
(`docker-compose.yml`, `celery-worker`).

## 5. New symptom ids (item 4)

Three entries were added after OPS-R42 because they are new, operator-facing states that follow
from OPS-5/OPS-6 and had no id in the design's §8 index:

- **OPS-R43. `restore` refuses the selected backup** (OPS-5). Confirm: the `restore refused: …`
  line from `./qradar-dash restore <backup-directory>`. Fix: the reason the line names (re-take the
  backup, upgrade first, use rollback, put the old key back, free space).
- **OPS-R44. An upgrade was rolled back automatically** (OPS-6). Confirm:
  `./qradar-dash status` shows `Last upgrade: rolled_back, …` and `doctor` is healthy. Fix: clear
  the trigger and retry; a `rolled_back` outcome does not block the next upgrade.
- **OPS-R45. A rollback failed** (OPS-6). Confirm: `./qradar-dash status` shows
  `Last upgrade: failed, …` and writers are stopped. Fix: retry `./qradar-dash rollback`, or
  `./qradar-dash restore <path>` with the same verified backup.

Their headings start with the id, so `grep OPS-R43` still finds them. They are not `doctor` ids
(no doctor check emits them), so their confirm commands are `restore`/`status`.

## 6. Every `./qradar-dash …` line, checked against `usage`/the argument parser

`./qradar-dash --help` lists exactly: `install`, `status`, `logs`, `doctor`, `backup`, `restore`,
`support-bundle`, `upgrade`, `rollback`, `bundle` (not built); and the option groups below. Every
invocation in `docs/OPERATIONS.md` was checked:

| Runbook line | Subcommand | Options / arguments | Where in the script |
|---|---|---|---|
| `./qradar-dash install` | `install` | — | `main()`; `usage` |
| `./qradar-dash install --non-interactive .secrets/install.answers` | `install` | `--non-interactive FILE` | `usage` install options; `cmd_install` case |
| `./qradar-dash upgrade <version> [--offline]` | `upgrade` | `--offline` | `usage` upgrade options; `cmd_upgrade` case |
| `./qradar-dash backup [manual\|scheduled\|pre-upgrade] [--no-verify]` | `backup` | reasons `manual`/`scheduled`/`pre-upgrade`, `--no-verify` | `usage`; `cmd_backup` case |
| `./qradar-dash backup --print-timer` | `backup` | `--print-timer` | `usage`; `cmd_backup` case |
| `./qradar-dash backup --install-timer` | `backup` | `--install-timer` | `usage`; `cmd_backup` case |
| `./qradar-dash rollback [--non-interactive --yes]` | `rollback` | `--non-interactive`, `--yes` | `usage` rollback options; `cmd_rollback` case |
| `./qradar-dash restore <backup-directory> [--non-interactive --yes]` | `restore` | `--non-interactive`, `--yes`; one backup path | `usage` restore options; `cmd_restore` case |
| `./qradar-dash doctor` | `doctor` | — | `main()`; `usage` |
| `./qradar-dash status` | `status` | — | `main()`; `usage` |
| `./qradar-dash logs` | `logs` | — | `main()`; `usage` (`logs [docker compose logs arguments]`) |
| `./qradar-dash logs frontend` | `logs` | service `frontend` | `usage`; `cmd_logs` passes `"$@"` to `docker compose logs` |
| `./qradar-dash support-bundle` | `support-bundle` | — | `main()`; `usage` |
| inline `./qradar-dash backup` (OPS-R43) | `backup` | — | as above |
| inline `./qradar-dash upgrade <sürüm>` (OPS-R43) | `upgrade` | — | as above |
| inline `./qradar-dash rollback` (OPS-R43, R45) | `rollback` | — | as above |
| inline `./qradar-dash restore <yol>` / `restore <backup-directory>` (R11, R34, R43, R45) | `restore` | one backup path | `cmd_restore` case |
| inline `./qradar-dash backup scheduled` (timer note) | `backup` | `scheduled` | `print_backup_timer` `ExecStart=$ROOT/qradar-dash backup scheduled` |

No runbook line uses `bundle`, so no new marker was needed for it. No option appears in the runbook
that the parser does not accept.

## 7. Open points

- I added OPS-R43 … OPS-R45. They are outside the design's §8 index (which stops at OPS-R42); item
  4 of the task explicitly allows a new id "if a new symptom follows from OPS-5 or OPS-6". If the
  orchestrator prefers the runbook to stay at exactly 42 ids, these three entries are the only
  additions and are removable without touching the rest.
- OPS-R9 still keeps the design's fallback sentence ("if the heartbeat task is not in this
  build …") although OPS-7 added the heartbeat, so `celery_beat` is a real check now. OPS-R9 was
  not named in this task, so I left it; flagging it as the remaining design-vs-as-built gap in
  `Belirtiler`.
- The design's `last_upgrade.backup` example in `docs/OPERATIONS-DESIGN.md` §4.5 still shows a
  `.dump` file. The runbook now shows the as-built backup directory, but the design file is outside
  this task's allowed files (`docs/OPERATIONS.md`, this report) and was not touched.
- `upgrade` performs two separate 180-second waits (`up -d --wait --wait-timeout 180` and the
  180 s readiness loop), so a worst case is about 360 s before an automatic rollback. The runbook
  says "180 sn'ye kadar sağlık beklenir" as the design does; OPS-6 flagged this too.
- `status` prints the **last upgrade backup** and its age, not a general "last backup" age; the
  `Günlük bakış` item 1 ("sürümler, sağlık, son yedeğin yaşı") still reads slightly broader than
  `cmd_status`. Left as-is; not part of the named changes.
- `bundle` is named in the intro as the one unbuilt command, but no command block uses it, so it
  carries no marker line (and the phrase is gone by design).
- Not committed. Only `docs/OPERATIONS.md` and this report were written.
