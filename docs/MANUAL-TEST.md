# QRadar-Dash manual test guide

This guide describes the manual test that is done step by step, by one person, before a QRadar-Dash release goes live. Commands, log lines, file paths, setting names, HTTP status codes and `doctor` ids are left as the sources print them. Interface labels are quoted as the English interface shows them (the catalog in `frontend/messages/en/`). The interface is English only from 0.1.10 (decision D-41), so this guide is English only too.

Four rules apply in every section and this test follows them:

> 1. The Alembic downgrade is never used. A rollback is the restore of a backup.
> 2. SELinux is never turned off and `:z` is never used.
> 3. Secrets never go into the `.env` file, the shell history, a ticket, a support bundle or the results page.
> 4. Nothing in this tool writes to QRadar. The tool is read-only against QRadar.

There is no step in this test that changes QRadar's configuration. The only write request to QRadar is the application creating and cancelling its own Ariel search (section 14).

The results are written to `docs/MANUAL-TEST-RESULTS.md`. This guide is divided by release: each release carries its own section; a new release adds its section **above** this one.

**Contents**

1. [0.1.16](#0116)

---

## 0.1.16

This section upgrades a 0.1.15 site to 0.1.16 and tests the upgraded site. A 0.1.14 site upgrades to 0.1.15 first; that path is in `docs/releases/0.1.15.md` and is not described here. 0.1.16 adds three migrations: the migration head is `0060` before the upgrade and `0063` after it (`migration_head` in `/api/v1/version`). The production IPS sources are Cisco Firepower/FTD and Palo Alto Threat.

### What this release changes

- **Three migrations; head `0063` (SOC-16a1, SOC-16f2, SOC-14a).** `upgrade` prints the decision pair twice, `0060` before the stop and `0063` after the new stack is up, and three `Running upgrade` lines between them (`0060 -> 0061`, `0061 -> 0062`, `0062 -> 0063`), in the output of the command itself (section 2, MT-06 to MT-08).
- **A 0.1.15 site upgrades directly.** `min_from` is `0.1.15` (`docs/releases/0.1.16.md`). A 0.1.14 site upgrades to 0.1.15 first; that path is in `docs/releases/0.1.15.md`.
- **The `postgres` container is recreated and starts with TimescaleDB telemetry off (FIX-47)** (MT-160).
- **Entity timeline `/entities`, its index and the backfill (SOC-16a to SOC-16f).** The lookup (MT-166); the `doctor` line `entity_mentions` and the backfill status (MT-09, MT-159).
- **Shift handover (SOC-14a, SOC-14b).** The page (MT-164), writing one (MT-165) and the “Latest handover” widget (MT-161). Writing needs `anomaly:triage`.
- **Keyboard triage (SOC-15a, SOC-15b).** `j`, `k`, Enter, `o`, Esc, `n`, `g` and the remembered refresh (MT-162); `a` acknowledges an alert (MT-163).
- **IPS behavior pages (IPS-UI-1, IPS-UI-2, IPS-API-1)** (MT-167).
- **EMA overlays on the charts, off by default and display only (CHART-1, CHART-2, UIQ-3)** (MT-168; the Fleet EPS widget in MT-64).
- **Search cadence: “Bucket width” and the reason of a failed run (FIX-44, FIX-44-ui)** (MT-114, MT-169).
- **Rule-match anomalies on the read side (SOC-6d to SOC-6h).** “Rule” in the anomaly list, the blocks on Rule statistics and the rule page, the Dashboard count (MT-170, MT-63, MT-80, MT-103, MT-104). Collection is still off by default (MT-144).
- **Alerts: one console-level alert for a fleet-wide empty answer (FIX-45c, D-62), one for stale volume collection (FIX-45e, FIX-45f), a NO_EVENTS title that says what was observed (FIX-45b), and the counter “Detections while open” (FIX-45-ui, UIQ-2)** (MT-171, MT-87, MT-88).
- **The Dashboard geography widget drops the text under the map (UIQ-1)** (MT-128).
- **The navigation has two new items:** “Handover” and “IPS behavior” (MT-22). `/entities` is not in the navigation; it opens from the top-bar search.
- **Corrected in this guide for 0.1.16.** MT-06 and MT-07 expect the three migration lines; MT-08 reads `0063`; MT-122 and MT-123 roll back to 0.1.15 and return to 0.1.16; MT-22 lists the navigation; MT-80, MT-87, MT-88, MT-114 and MT-128 follow the changed texts; MT-144 is renamed because it no longer compares with the previous release.
- **Thirteen new checks, MT-159 to MT-171; the core is 42 checks.** MT-159 and MT-160 are core; the other eleven are not. MT-163, MT-165 and MT-171 write to the site's own tables and are `lab only`. MT-168 and MT-169 view and store nothing in the site; MT-170 does not turn rule-match collection on.
- **Before you start:** nothing in `.env` needs an edit for this release. The new optional settings (`ENTITY_MENTION_BACKFILL_*` and `HANDOVER_RETENTION_*`) have defaults and are documented in `docs/CONFIGURATION.md`; they are not in `.env.example` (MT-159, MT-165). The alert e-mail and `QRADAR_HEALTH` steps of 0.1.11 (`SMTP_HOST`; the custom properties `Metric ID`, `Element` and `Value`) are described in `docs/releases/0.1.11.md`; MT-138 to MT-142 and MT-133 need them.

### How to use this guide

- **In production only the core is run.** The 42 checks that carry `[core]` in their heading are the production path; the full list is for the lab run (MT-1b) and in production is done only if the operator has the time. The only core checks that write (writes) are the ones the preconditions and the upgrade need (backup, support bundle, upgrade); there is no `lab only` check in the core (none of the `lab only` headings carries `[core]`). The `lab only` checks are not listed here: each one says so in its heading, so search this guide for `lab only`.
- The order of the checks is the order of the sections. Each check has the fields **Precondition**, **Action**, **Expected** and **Record**. Write the result on the results page as `Pass`, `Fail` or `Not run`; write a Note for `Fail` and `Not run`.
- **(writes)**: the check changes the application's state (it writes a file, an account, a record). Each one has a **Cleanup** step.
- **lab only**: the check changes the state that the analysts see or leaves a trace that cannot be deleted; in production it is written `Not run`, with the reason given in the check. The orchestrator runs the same guide to the letter on a lab VM (MT-1b).
- **QRadar search**: the check sends an Ariel search to QRadar and spends console capacity. Three checks in this test are like that and all three are `lab only`; the fourth case is part 2 of MT-150, where the tester runs the shown query in the console's own page (the product does not).
- **console administrator step**: the check is done by the administrator of the QRadar console.
- `Not run`: the check could not be done (for example the site has no data of that kind). "No data" is not a Fail; write the reason in the Note field.
- A raw message key (a string of the form `namespace.key`) or Turkish text anywhere in the interface is a Fail of the check where it was seen (section 4).
- **“The `h1`” and “the browser tab”.** The `h1` is the page's one level-1 heading, the title the analyst reads at the top of the page. The browser tab shows the page's `<title>`, which is “{page title} · Behavior Analytics”; a check names the tab only where it differs from the `h1`. The `h1` of a detail page is the name of what it shows (a log source, a rule, an alert's title), not a fixed word; the fixed word of such a page is its tab title. Three routes set no tab title of their own (`/coverage`, `/log-sources` with its detail, `/config-changes`); their tab reads only “Behavior Analytics”.
- No secret is written in any step (section 16). Do not put passwords and `.env` values on the page or in a ticket.
- SELinux is never turned off and `:z` is never used in any step.

### Timing and total duration

After the upgrade, collection restarts at the cadence of the families. The default cadence is 5 minutes for six of the nine families; WAF collection, IPS collection and QRadar health (`QRADAR_HEALTH`, on by default) default to 15 minutes (decisions D-28 and D-42). The site's own values are on the `/admin/search-cadence` page; an administrator can set any family to 5, 15 or 30 minutes, or 1, 2, 4 or 8 hours. Four of them are switched off in the code defaults (Rule matches, Property watches, WAF collection, IPS collection); a family that is off does not run, and the `families_off` line of `doctor` says which are on.
A family runs when beat's one-minute tick finds an unclaimed slot. Slots are aligned to the clock (a 5-minute family has slots on :00, :05, :10 …), and a slot opens after the settle margin. The first run after the restart is therefore the next slot boundary after the claimed one + the settle margin, seen at the next beat tick: at most the family's own cadence + 3 minutes after the upgrade. If a whole slot has already passed (the stack was down for longer than one cadence), the first run is at the first beat tick after beat starts, about one minute. The “Last run (UTC)” time is written when the run ends, so a run that has to catch up after an outage adds its own duration; Catch-all senders and Geography take at most the cadence's buckets plus `COLLECTION_MAX_BACKFILL_INTERVALS` of missing buckets per run, and the rest follow in the next runs. Not every collector is a cadence family: `offense_snapshot` (default 5 min), `log_source_inventory` and `rule_inventory` (default 1 h) are plain beat intervals, and beat's first run of such a task comes one interval after beat starts.
For the data of a collection to appear on a page, the cadence and the settle margin (`SEARCH_CADENCE_SETTLE_SECONDS`, default 120 s) must pass; `doctor` also gives a 60 s margin. So:

| What | When to look | What the page shows if you look earlier |
|---|---|---|
| Version, migration, `doctor` (independent lines), sign-in, guide, administration pages | At once | (not data dependent) |
| Dashboard widgets, anomaly and fleet data, rule and coverage reasons | The family's own cadence + 3 min, from the “Next run (UTC)” column of `/admin/search-cadence`. With the default cadences: 8 min for Log source volume, Anomaly explanations, Rule matches, Property watches, Catch-all senders and Geography; 18 min for WAF collection, IPS collection and QRadar health. A family set to a longer cadence waits that long (1 hour: up to 1 h 3 min); a family that is off does not run. The “Bucket width” column is not the cadence: Catch-all senders and Geography keep 5-minute buckets when their cadence is 1 hour, so read “Cadence” and “Next run (UTC)” (MT-11) | “Stale” or “Not collected”; this is correct, do not write Fail |
| `doctor` line `entity_mentions` (MT-159) | The backfill tick (5 min by default) and as long as the stored history needs | `WARN` with “entity timeline index incomplete …”; correct while it is filled |
| New sentence rows (property watch reason, custom rule reason, rule health reason, history backfill reason) | After the next run of the collection that writes the row | The stored previous text |
| A new transition, a new anomaly, a new alert | When an event happens; there is no waiting | The older rows |

**Core (production path):** 42 checks, about **2 hours 20 minutes** of active work (an estimate, not measured) and about 10 minutes of the console administrator (MT-120); the cadence wait is separate. **Production order:** the core checks are done in section order. MT-126, although its number is high, is done right after MT-11; MT-128 is also done after MT-11, once a `GEO_COLLECTION` cadence has passed. The cadence wait (18 minutes at most) is filled with read-only checks that need no new data, such as MT-12 to MT-22; the data-dependent checks (MT-24 and after) are done when the wait is over.

Full list: about **5.5 hours** of active work (an estimate, not measured), not counting the waits. By section: Preconditions 20 min, Upgrade 20 min (the outage is inside the window), Sign-in 20 min, Language 45 min, Dashboard 40 min, Anomalies 30 min, Alerts 10 min, Custom rules 15 min, Searches 15 min, History backfill 10 min, Rules/coverage/fleet 35 min, Administration 25 min, Guide 10 min, Read-only against QRadar 15 min (the console administrator is separate), Rollback criteria 5 min; MT-129 to MT-132 add about 20 minutes, and MT-133 to MT-135 about 25 more (MT-135 only where an HA pair exists), MT-136 about 5 more, MT-137 to MT-142 about 35 more (MT-141 and MT-142 only in the lab), and MT-143 about 5 more, and MT-144 to MT-158 about 1 hour 30 minutes more (an estimate: the lab-only MT-146, MT-155, MT-157 and part 2 of MT-150 about 35 of them), and MT-159 to MT-171 about 1 hour 15 minutes more (an estimate: the lab-only MT-163, MT-165 and MT-171 about 20 of them).
Fill the first cadence wait (18 minutes at most with the default cadences) with the Sign-in and Language sections. It can also be done in two half days; between the upgrade and the Dashboard and anomaly checks a wait is needed.


### 1. Preconditions

This section is done before the upgrade. The commands run from the install directory: `cd <install directory>`. Each check's result goes into the table on the results page. If a precondition fails, do not start the upgrade.

#### MT-01 — Version and health before the upgrade [core]

**Action:**


```bash
./qradar-dash status
curl -s http://127.0.0.1:8000/api/v1/version
curl -s http://127.0.0.1:8000/api/v1/health/ready
```

- The script uses `API_LOOPBACK_PORT` from `.env` (default 8000). If the port is different, write that one.

**Expected:**

- `status`: `Installed version: 0.1.15`; `API ready: HTTP 200 (http://127.0.0.1:8000/api/v1/health/ready)`; every service in the `docker compose ps` table is running and healthy; the `Last upgrade:` line is present; exit code 0.
- `/api/v1/version`: `"version":"0.1.15"` and `"migration_head":"0060"`.
- `/api/v1/health/ready`: `"status":"ready"`, `"database":"ok"`, `"revision":"0060"`.

**Record:** The `Installed version` line, the `Last upgrade:` line, and the `git_sha` and `migration_head` values.

#### MT-02 — `doctor` is green before the upgrade [core]

**Action:**


```bash
./qradar-dash doctor
echo "exit: $?"
```


**Expected:**

- No line is `FAIL`. Exit code 0 (all PASS) or 1 (at least one WARN, no FAIL). If it is 2, the upgrade is not done; read the line's `runbook: OPS-R<n>` entry. The one exception is a leftover failure of a collector, described in the next item.
- **A leftover failure of a collector (`watermarks`, `errors_recent`).** This is how `doctor` decides, and it does not look at the age of the last run. `watermarks` is `FAIL` when any collector's row holds a `last_error` or three or more consecutive failures (a row stalled at the page ceiling counts as a stall, not as a failure), and `WARN` when the row has no error but its lag is more than twice its cadence or it is stalled. `errors_recent` is `WARN` for one or two rows with a failure and `FAIL` for three or more. A row is cleared only when that collector next finishes a clean run: the families of `/admin/search-cadence` at their next slot (cadence + 3 min), `offense_snapshot` at its own interval (default 5 min), and the two inventories `log_source_inventory` and `rule_inventory` once an hour, counted from the start of beat. So a healthy site that was powered off, or whose QRadar console was down or still starting, can show `FAIL` for collectors such as `catchall_senders`, `rule_match`, `geo_countries_source`, `geo_countries_destination`, `offense_snapshot`, `log_source_inventory` and `rule_inventory`, because the failure was recorded earlier and nothing has run since. The row of a family that is switched off (see `families_off`) is not judged and is named after `not judged (family off):`, so it does not raise the exit code; nothing is cleared, because that collector never runs again, and switching the family on again judges the row again, and its next clean run clears it. The rule: when `qradar_auth` is `PASS` (the console answers now) and the only `FAIL` lines are `watermarks` and `errors_recent`, write the collector names (the text of the `watermarks` line) and the time in the Note field; this alone does not stop the upgrade, which neither runs nor resets these collectors, and the rows are read again in MT-09 and MT-11. Stop and tell the owner when `qradar_auth` or `qradar_tls` is still `FAIL` after the wait described below, when any other line is `FAIL`, or when the same row is still failing after its collector's next run (OPS-R30).
- On a healthy site these lines may be `WARN` and do not block the upgrade; write each one and its cause in the Note field: `backup_age` (no backup, the age cannot be read, or older than 26 hours; when the backup timer is not installed or is off, even a fresh backup gives a WARN — the `backup_age` row of the doctor table in `docs/OPERATIONS.md`, which has no OPS-R id; the next check fixes the backup, not the timer), `disk_docker` and `disk_backup` (70% to 89% full), `qradar_tls` (the certificate expires in under 30 days), `cadence`, `errors_recent` (one or two rows with a failure), `anomaly_evaluation`, `rule_match_coverage`, `ariel_permits` (the pool is full). These are true before the upgrade as well.
- On a console that has no offenses, a `watermarks` `offense_snapshot` line does not block the upgrade. `consecutive_failures` is 0 and `last_error` is empty. Since 0.1.9 a clean run moves this watermark to the time of the run; if no clean run has happened yet, the lag can stay at the `offense_max_backfill_hours` floor (default 168 hours) and exceed twice the cadence. This is a WARN, not a Fail; write it in the Note field. The line's runbook is OPS-R30. The 0.1.10 `doctor` output has the stalled sentence and OPS-R66 (MT-09).
- If any of these is `WARN`, stop and tell the owner: `selinux` (`SELinux is permissive`: do not touch SELinux), `host_clock`. `families_off` is always PASS. A `SKIP` line says a feature is off and does not raise the exit code.
- Line format: `PASS  db_connect         database accepted SELECT 1`. At this stage `db_connect`, `migration_head` (`database and image are at 0060`), `containers`, `celery_workers`, `celery_beat`, `qradar_auth`, `encryption` and `secrets_files` must be PASS.
- **A console that is still starting.** The upgrade itself does not use the console: it reads this checkout, the images, the database, the backup and the stack's own health, and sends nothing to QRadar (`qradar-dash:3513-3715`). When the console has just booted — in the OPS-35 run REST answered about 5 minutes and Ariel about 15 minutes after the boot — the lines that reach it (`qradar_tls`, `qradar_auth`, `geo_country_source`) can be `FAIL` with `did not answer within 10 s` (`backend/app/cli/doctor.py:69`, `:2015-2027`; the slower `qradar_auth` prints `GET /system/about on <host> failed (...)` instead, `:863-870`), the collector rows behind `watermarks` and `errors_recent` can be `FAIL` because their runs failed with it, and `qradar_access`, `qradar_health`, `qradar_disk` and `qradar_pipeline` can be `WARN` on what the console has not refreshed yet. Do not start the upgrade while a QRadar-reach line (`qradar_tls`, `qradar_auth`, `geo_country_source`) is `FAIL`. Note the lines, their text and the time in the Note field, then wait and run `doctor` again every few minutes until `qradar_auth` is `PASS` (expected within about 5 minutes of the boot; the Ariel-backed lines up to about 15), and judge MT-02 only then. A QRadar-reach `FAIL` that is still there after the wait is a Fail. If `qradar_auth` is still `FAIL` about 20 minutes after the boot, stop and tell the owner with the line's text and the `runbook: OPS-R<n>` id it prints (`qradar_auth` prints OPS-R1; the collector rows behind `watermarks` print OPS-R30).

**Record:** The exit code, the id of every `WARN` line and, if there is a `FAIL`, its id.

#### MT-03 — Verified backup (writes) [core]

**Precondition:** `doctor` (the previous check) has no FAIL. There is free space in the backup root (`disk_backup`).

**Action:**


```bash
./qradar-dash backup manual
```

- The command writes the path of the backup directory on its last line (`<backup root>/qradar-dash-<utc>-manual`). Take the path and read the `MANIFEST` file:

```bash
BACKUP=<the path it wrote>
ls -l "$BACKUP"
cat "$BACKUP/MANIFEST"
```

- Do not pass `--no-verify`: the backup is restored into a separate database and verified.

**Expected:**

- The command exits with 0. For TimescaleDB it may print `circular foreign-key constraints` warnings; this is expected, and what decides is the exit code and that the backup exists.
- The directory holds `database.dump`, `env.bak`, `secrets.tar`, `state.json` and `MANIFEST`, and `install.answers` when `.secrets/install.answers` exists as a regular file with mode `0600` or `0400` (the copy keeps that mode); the directory mode is `0700` and the other file modes are `0600`. The support bundle (MT-04) is a different archive and is not in this directory.
- `MANIFEST` has `VERSION=0.1.15`, `ALEMBIC_VERSION=0060`, `STATE_JSON=included` and a `DATABASE_SIZE_BYTES=<number>` line.

**Record:** The name of the backup directory (only the `qradar-dash-<utc>-manual` part), and the `VERSION` and `ALEMBIC_VERSION` lines. The contents of `env.bak` and `secrets.tar` are written nowhere.

**Cleanup:** Not needed. The backup stays under the retention rule (the last 7 `manual` backups); the upgrade also takes its own `pre-upgrade` backup.

#### MT-04 — Where the logs and the support bundle are (writes) [core]

**Action:**


```bash
./qradar-dash logs --tail=20 backend
./qradar-dash support-bundle
```

- `logs` passes the arguments it receives to `docker compose logs`. `support-bundle` prints the path; list its contents:

```bash
BUNDLE=<the path it wrote>
ls -l "$BUNDLE"
tar -tzf "$BUNDLE" | head -40
```


**Expected:**

- `logs` prints the last 20 lines of the backend and exits with 0.
- `support-bundle` prints the path `<backup root>/support-<utc>.tar.gz`; the file mode is `0600`.
- The archive holds `doctor.txt`, `version.txt`, `compose-ps.txt`, `watermarks.txt`, `cadence.txt`, `config.env` and `logs/`. The script adds one more member: `ariel-load.json`, or `ariel-load.error.txt` when the load summary could not be read. Either one is correct. A part that could not be read is replaced by `<part>.error.txt`; write each such file in the Note field. The archive does not hold `database.dump`, `env.bak`, `secrets.tar` or `.secrets/`.

**Record:** The full path of the support bundle (not its contents).

**Cleanup:** If no check is Fail at the end of the test, delete this file: `rm "$BUNDLE"`. If a check is Fail, keep it and hand it over as in section 16.

**Note:** If something fails, take a new `support-bundle` **before** the rollback and keep the output of `logs --tail=2000 backend`. The rollback keeps the logs and the broken database copy, but the context of the logs can be lost.

#### MT-05 — The window and the people to inform [core]

**Action:**

- Before you start, write these at the top of the page: the start and end of the maintenance window (UTC), the upgrade path (online or offline bundle), the person who will decide on a rollback, the QRadar console administrator (for section 14), and whether the analysts have been told.

**Expected:**

- During the upgrade the backend, worker, beat, frontend and proxy stop; analysts see a connection error. For the duration, see section 2.

**Record:** The start and end of the window (UTC), the name of the path, and the person or role who was told.

### 2. Upgrade 0.1.15 → 0.1.16

A fresh browser has no `ba_locale` cookie, and the sign-in page shows the `h1` “Sign in” (the browser tab reads “Sign in · Behavior Analytics”) and the version line `API version 0.1.15`. The interface is English only (D-41); a `ba_locale` cookie, whatever its value, does not change it.

There are two ways to upgrade the site from 0.1.15 to 0.1.16; choose one. The target version works the same way as the "Upgrade" and "Offline bundle" sections of `docs/OPERATIONS.md`. The upgrade takes its own verified `pre-upgrade` backup; the backup is also restore-tested. 0.1.16 adds three migrations: the migration head is `0060` before the upgrade and `0063` after it, and the newest file in `backend/alembic/versions/` is `0063_shift_handover.py` (`0061_entity_mention`, `0062_entity_mention_backfill`, `0063_shift_handover`). The migrate step runs them once (`alembic upgrade head`), in order, and prints one `Running upgrade` line for each. They are additive and nothing is backfilled. No migration is run downward on a site.

**Outage.** The backend, worker and beat are down from the start of `pg_dump` until `/api/v1/health/ready` returns 200; `frontend` and `proxy` are stopped too. On a small database the upgrade outage was measured at about 15-25 s with migrations (22.3 s in the 0.1.14 → 0.1.15 run of REL-15e, offline, six migrations on the lab VM; 22.9 s in the 0.1.10 → 0.1.11 run with seven migrations); the budget for a database under 5 GiB is 10 minutes. Pulling the images is outside this window. These are not promises. The upgrade empties Redis (`FLUSHALL`); queued tasks are lost and are set up again at the next beat tick, and the important data is in PostgreSQL. Nothing is written to QRadar.

#### MT-06 — Upgrade, online path (writes) [core]

**Precondition:** The verified backup (MT-03) is done. If you chose the offline path, this check is `Not run`.

**Action:**


```bash
git fetch --tags
git checkout v0.1.16
./qradar-dash upgrade 0.1.16
```

- Write the start and end time in the Note field with `date -u`.

**Expected:**

- The command exits with 0 and the last line is `Upgrade complete: 0.1.15 -> 0.1.16.`.
- Three migrations run, once, in order. The command prints them in **its own output** (the `migrate-run` container the upgrade starts), between the two `PASS` pairs. Alembic's two start-up lines come first, then one `Running upgrade` line for each revision. They carry Alembic's prefix and the revision's own description (the first line of each migration's docstring, `backend/alembic/versions/0061_entity_mention.py`, `0062_entity_mention_backfill.py`, `0063_shift_handover.py`); the 0.1.15 lab run (REL-15e) measured the same form for its migrations:

```
INFO  [alembic.runtime.migration] Context impl PostgresqlImpl.
INFO  [alembic.runtime.migration] Will assume transactional DDL.
INFO  [alembic.runtime.migration] Running upgrade 0060 -> 0061, SOC-16a1: normalized entity mentions and contributor backfill index.
INFO  [alembic.runtime.migration] Running upgrade 0061 -> 0062, SOC-16f2: resumable entity mention backfill state.
INFO  [alembic.runtime.migration] Running upgrade 0062 -> 0063, SOC-14a: immutable shift handover notes and frozen queue snapshots.
```

The three revision pairs `0060 -> 0061`, `0061 -> 0062` and `0062 -> 0063` must all be present, in this order. A missing pair, a pair out of order, or an Alembic error is a Fail (write the first error line in the Note field). The text after the comma is the migration's description and can differ in a later build; the pair is what decides. Do not look for these lines in `docker compose logs migrate` after the upgrade: that container is started again at the end, finds nothing left to do and shows only the two start-up lines (REL-15e, finding D2). The migrate container exits with 0. A different start-up line is written in the Note field, not a Fail.
- In the pre-check (before the writers stop) these three lines are printed; the head is that of the **installed** version, which is `0060`:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0060
Full report: ./qradar-dash doctor
```

- After the new stack has started, the second pair is printed; the head is that of the **target** version, `0063`:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0063
Full report: ./qradar-dash doctor
```

- Both pairs are printed: the first says `0060`, the second `0063`. A single pair is printed only if the target is already installed: `Version 0.1.16 is already installed and healthy; nothing changed.` That is not the output of a first upgrade.
- The number of spaces in the `PASS` lines can change with the release; the text is the same.
- If the `.env` of 0.1.15 has no `FRONTEND_EDGE_IP` and `FORWARDED_ALLOW_IPS` is a non-empty list that is not `*` and does not contain the frontend address, these two lines are printed as well. Do not write the addresses on the page; write only that the lines appeared:
  - `Frontend address <address> added to .env (FRONTEND_EDGE_IP).`
  - `FORWARDED_ALLOW_IPS now also trusts the frontend (<address>): <list>`
- If `FRONTEND_EDGE_IP` is already in `.env` and the list does not contain the frontend address, only `FORWARDED_ALLOW_IPS now also trusts the frontend (<address>): <list>` is printed; that is also correct.
- If the keys are already correct (the list already contains the frontend address), these lines are not printed; that is also correct.
- If the output is `upgrade refused: …`, no container has been stopped; read the reason (for example `this checkout is version <x>, not 0.1.16` means `git checkout v0.1.16` was forgotten; `upgrade refused: 0.1.16 requires at least 0.1.15; nothing was changed` means the site is older than 0.1.15 (the minimum comes from `min_from` in the checkout's own `docs/releases/0.1.16.md`, read before any `.env` edit, bundle load or pull), and `.env` and the images are unchanged) and fix it. If there are two lines of the same name, `upgrade refused: .env has more than one FRONTEND_EDGE_IP line; nothing was changed` (or `FORWARDED_ALLOW_IPS`) is printed; delete the extra line and do not write `*`.
- If the exit is non-zero and the last line is `upgrade to 0.1.16 failed and was rolled back to 0.1.15; the broken database was kept`, the automatic rollback has run: go to section 15 (OPS-R44).

**Record:** The exit code, the start and end time (UTC), the two `migration_head` lines (`0060`, then `0063`), whether the three `Running upgrade` lines were present, the last line, and which of the address lines appeared (not the addresses themselves). If it is Fail, the first error line.

**Cleanup:** The rollback is the `rollback` command in section 15. There is no other cleanup.

#### MT-07 — Upgrade, offline bundle path (writes) [core]

**Precondition:** The bundle is prepared on a connected machine from the same tag: `git fetch --tags && git checkout v0.1.16 && ./qradar-dash bundle --output <bundle directory>`. `--output DIR` is the directory the bundle is written to; if it is not given, the file is written to the current directory. The file name is `qradar-dash-0.1.16-bundle.tar`. A file at the same path is not overwritten. The bundle is carried to the site. If you chose the online path, this check is `Not run`.

**Action:**

- Compare the checksum on both machines, then unpack the bundle in the install directory and upgrade:

```bash
sha256sum /path/qradar-dash-0.1.16-bundle.tar
cd <install directory>
tar -xf /path/qradar-dash-0.1.16-bundle.tar --exclude=images.tar
./qradar-dash upgrade 0.1.16 --offline /path/qradar-dash-0.1.16-bundle.tar
```


**Expected:**

- The two checksums are the same.
- The bundle is verified and loaded; the line `bundle images loaded and checked against MANIFEST` appears in the output. No container is stopped before the images are loaded and checked.
- The rest is as in the online path: two `PASS` pairs (`0060`, then `0063`), the three `Running upgrade` lines of MT-06 in the command's own output, and the last line `Upgrade complete: 0.1.15 -> 0.1.16.`, exit code 0.
- If an image is missing, `upgrade refused: target images are unavailable: image <image> is not loaded on this host; no container was stopped; pass the bundle: …` is printed and nothing stops.

**Record:** Both checksums, the exit code, the start and end time (UTC), the two `migration_head` lines, whether the three `Running upgrade` lines were present.

**Cleanup:** The `rollback` in section 15.

#### MT-08 — Version and status after the upgrade [core]

**Action:**


```bash
./qradar-dash status
curl -s http://127.0.0.1:8000/api/v1/version
curl -s http://127.0.0.1:8000/api/v1/health/ready
```

- Take the `Last upgrade backup:` path from the `status` output (without the age part in parentheses). Look at the mode of the answer file in the backup. Do not read or write the contents of the file:

```bash
BACKUP=<Last upgrade backup path>
stat -c '%a %n' "$BACKUP/install.answers"
```


**Expected:**

- `status`: `Installed version: 0.1.16`; `Last upgrade: success, 0.1.15 -> 0.1.16 at <utc>`; `Last upgrade backup: <path> (age <age>)`; `API ready: HTTP 200 (…)`; exit code 0; every service healthy.
- `/api/v1/version`: `"version":"0.1.16"` and `"migration_head":"0063"`.
- `/api/v1/health/ready`: `"status":"ready"`, `"database":"ok"`, `"revision":"0063"`.
- The backup has `install.answers`. `stat` prints the mode `600` (`0600`). If the live file was `0400` before the upgrade, the mode is `400`. The file is missing only if the live file is missing, if it is a symbolic link, or if its mode is neither of these two; on an ordinary install the file exists and its absence is Fail.

**Record:** The version and `migration_head` values of the three outputs; the `Last upgrade:` line; the name of the backup directory and the digits of the `install.answers` mode. The contents of the file are not written.

#### MT-09 — `doctor` right after the upgrade [core]

**Action:**


```bash
./qradar-dash doctor
```

- Check the two names in `.env` without printing the addresses. You can write the output on the page; it does not print an address:

```bash
awk -F= '
  function unquote(s) {
    gsub(/^[ \t]+|[ \t]+$/, "", s)
    if (s ~ /^".*"$/) s = substr(s, 2, length(s) - 2)
    return s
  }
  $1 == "FRONTEND_EDGE_IP" { fe = unquote($2); nfe++ }
  $1 == "FORWARDED_ALLOW_IPS" { fa = unquote($2); nfa++ }
  END {
    if (nfe + 0 != 1 || nfa + 0 != 1) {
      print "line count FRONTEND_EDGE_IP=" nfe + 0 " FORWARDED_ALLOW_IPS=" nfa + 0
      exit 1
    }
    if (fe == "" || fe == "*" || fa == "" || fa == "*") {
      print "FRONTEND_EDGE_IP or FORWARDED_ALLOW_IPS is empty or a star"
      exit 1
    }
    n = split(fa, a, /[, ]+/)
    ok = 0
    for (i = 1; i <= n; i++) if (a[i] == fe) ok = 1
    if (!ok) { print "the list does not contain the frontend address"; exit 1 }
    if (n == 2) print "two addresses, the frontend is in the list; addresses not printed"
    else print "the frontend is in the list; address count " n "; addresses not printed"
  }
' .env
```


**Expected:**

- There is no `FAIL`, with one exception: a `watermarks` `FAIL` (and the `errors_recent` count that goes with it) for a collector whose failure was recorded before the upgrade, by the rule in MT-02. The hourly `log_source_inventory` and `rule_inventory` on a changed console are one case of it; the families that were not run while the site was down (for example `catchall_senders`, `rule_match`, `geo_countries_source`) are another. Write the collector names in the Note field; the wait belongs to MT-11. `db_connect` PASS; `migration_head` PASS and `database and image are at 0063`; `version_match` PASS and `frontend and backend report 0.1.16`; `containers` PASS.
- `forwarded_allow_ips` is PASS and the text is `FORWARDED_ALLOW_IPS trusts the frontend (<address>)`. The address is the same as `FRONTEND_EDGE_IP`; do not write the address on the page. This line has no runbook. awk prints `two addresses, the frontend is in the list; addresses not printed`. On a site behind an outer proxy, the list is the operator's old list with the frontend address added once; more than two addresses is correct, and awk prints the address count.
- If the line is missing, if awk says empty or star, or if doctor says FAIL (`FORWARDED_ALLOW_IPS is empty; every analyst shares one rate-limit key` or `FORWARDED_ALLOW_IPS is *; every analyst shares one rate-limit key`), this is Fail. The runbook is OPS-R21. Do not write `*`. If the list does not contain the frontend address, it is a WARN: `FORWARDED_ALLOW_IPS does not trust the frontend (<address>); pages drawn by the server share one rate-limit key`, runbook OPS-R21. This is also Fail.
- Because beat and worker have just started, `celery_workers` or `celery_beat` can give a temporary FAIL in the first minute; wait a minute and repeat the command. If the FAIL stays on the second run, it is Fail.
- Because collection has not run for one cadence yet, `cadence`, `anomaly_evaluation` and `rule_match_coverage` can be WARN. These are read again in MT-11. `anomaly_evaluation` can read `{n} monitored source(s) have a live bucket that should already have been judged` (runbook OPS-R61) while the engine catches up the buckets the stop left unjudged; the check measures each live bucket from the end of its own window and gives it the settle margin (120 s), one beat (60 s) and two evaluation ticks (2 × 60 s) — five minutes — before it counts it (`backend/app/cli/doctor.py:1084-1091`, `:1126-1145`, `:1188-1195`), and the beat's own tick for the engine is one minute (`backend/app/workers/celery_app.py:66-70`). A restart whose outage is under a minute does not reach that on a stack that was judging every bucket as it arrived; the line names the sources that were behind when the stack stopped and clears on its own once the engine's next tick has judged them (up to 120 pending buckets per source per tick, `backend/app/anomaly/engine.py:262-305`, `anomaly_max_buckets_per_evaluation` at `backend/app/core/config.py:556`). Read it again in MT-11: by then it is normally PASS, and only a count that keeps growing is a Fail (OPS-R61).
- Because the entity-mention backfill (its first tick comes five minutes after beat starts) has not finished, `entity_mentions` is `WARN` (runbook OPS-R73) with `the entity-mention backfill has not run yet; run it now: python -m app.cli.entity_mentions run` or `entity timeline index incomplete for {n} source(s): …`. It is never `FAIL`. Do not run the command in production; MT-159 reads it again after the first ticks.
- The ordinary PASS text of `watermarks` is `{n} watermark(s) have no error or excess lag` and has no runbook. On a console that has no offenses, a WARN stays before the first clean `offense_snapshot` run: the lag exceeds twice the cadence, `consecutive_failures` is 0, and `last_error` is empty (MT-02). The text has the form `{n} watermark(s) exceed twice their cadence: offense_snapshot` and the runbook is OPS-R30. This is a WARN, not a Fail; write it in the Note field. It is read again in MT-11.
- A stalled offense watermark is a separate WARN: `{n} watermark(s) stalled at the page ceiling: offense_snapshot`. The runbook is OPS-R66. If there is lag as well, the two sentences are joined with a semicolon and the runbook is still OPS-R66. This WARN is not a Fail. A provider error stays FAIL, the runbook is OPS-R30, and the same text can be appended to the end of the FAIL sentence. A clean run clears the stall (MT-11).
- `backup_age` can stay WARN for the same reason as before the upgrade: no backup, the age cannot be read, older than 26 hours, or the backup timer is not installed or is off (a fresh backup also gives a WARN when there is no timer or it is off). This is not a fault of the upgrade. The line is in the doctor table of `docs/OPERATIONS.md`; it has no OPS-R id.

- The report has these lines, which 0.1.11 added (they are present before this upgrade too): `email` (SKIP `not configured (SMTP_HOST is empty); alerts are not mailed` until `SMTP_HOST` is set), `qradar_access`, `qradar_disk` and `qradar_pipeline` (MT-133, MT-134). On a 0.1.15 site the `QRADAR_HEALTH` family has already run, so `qradar_disk` and `qradar_pipeline` read the stored windows; `not collected since <time> UTC` (a window older than the family's cadence allows) clears after its next run (15 minutes by default, MT-11), and `not collected yet` appears only where the family never ran (family off, or a console added since). The report also has `geo_country_source` and `qradar_health`. A WARN on either is not a Fail: write its text in the Note field (a lab CE console always gives a WARN on `qradar_health` because its license is over-allocated). When the console does not answer, `qradar_health` is `SKIP` and `qradar_auth` is the line to read: a `SKIP` is neither a `WARN` nor a `FAIL` and does not raise the exit code. Its texts, from the code: `the console did not answer; see qradar_auth` when the console's own reachability probe failed (`backend/app/cli/doctor.py:1792-1795`); `nothing could be read from QRadar: managed hosts (<state>), EPS (<state>), license (<state>); see qradar_auth` when the probe answered but none of the three figures could be read, each state being `NOT_COLLECTED`, `FAILED`, `STALE` or `PARTIAL` as stored (`:1861-1866`); `QRadar health could not be read (<exception class>)` when the read itself raised (`:1786-1791`); `the registered console could not be read (<exception class>)` when the console row could not be loaded (`:1766-1773`); and `no enabled QRadar instance is registered; see qradar_auth` when there is no enabled console at all (`:1774-1777`). In the first minutes after the QRadar console boots, its Ariel and health services are not ready yet while `qradar_auth` can already be `PASS`, and several lines can read this way (observed in OPS-32 and OPS-33; not every line appears every time, so this is not one exact set): `qradar_access` `WARN` with `<instance>: no answer from ariel_search_list (error), ariel_searches (error)`, runbook OPS-R71 (`backend/app/cli/doctor.py:1724-1728`; the names in the list are the entries that did not answer, each with its status); `qradar_disk` and `qradar_pipeline` `WARN` with `<instance>: not collected since <date> <time> UTC (the newest window is older than the cadence allows)`, or `<instance>: not collected yet` before the family's first window (`backend/app/cli/doctor_qradar_health.py:40-58`; OPS-33 read `not collected since 2026-11-04 11:05 UTC` six minutes after the boot); both print `SKIP` with `no enabled QRadar instance is registered; see qradar_auth` when there is no enabled console (`:60`, `:104`). They are expected there, not a Fail. Run `doctor` again after 10 to 15 minutes (OPS-33: `qradar_access` was `PASS` once the console answered, and `qradar_disk` was `PASS` after the next health window); a `SKIP` or `WARN` of these lines that stays after that, with `qradar_auth` PASS, is written in the Note field. A FAIL on `geo_country_source` is a Fail (MT-129 says what it means), with one exception: when the console does not answer, `geo_country_source` prints `FAIL did not answer within 10 s` with the runbook OPS-R30 (`backend/app/cli/doctor.py:2015-2027`; `CHECK_TIMEOUT_SECONDS = 10.0` at `:69`), because the check reads the console's property list and the console is not there. With no enabled console at all it prints `FAIL property; no enabled QRadar instance is registered` instead (`:1951-1957`). That line is the same finding as `qradar_tls` and `qradar_auth` (both `FAIL`, runbook OPS-R4 and OPS-R1) and is not a product failure; it clears on the first `doctor` after the console answers again. Read `qradar_auth` first: when it is `PASS` and `geo_country_source` still says `did not answer`, the property list is the part that is slow, and that is a Fail to report (MT-129). A `watermarks` FAIL with “The country property … does not exist on this console” is the same finding seen from the collector; fix the setting (MT-129) and run `doctor` again.

**Record:** The exit code; the ids of the WARN and FAIL lines; the text of the `migration_head`, `version_match` and `forwarded_allow_ips` lines (without the address); the line awk printed.

#### MT-136 — `doctor --aql`: every family's query is accepted by the console [core]

**Precondition:** MT-09 is done. The console answers (`qradar_auth` PASS). The command sends up to 20 read-only searches per REST console (WAF volume plus each breakdown, each IPS dimension, two health searches, one for each other family), one at a time, through the Ariel permit pool; they are recorded under the caller `DOCTOR_AQL`. Nothing is written to QRadar.

**Action:**

```bash
./qradar-dash doctor --aql
echo "exit: $?"
```

**Expected:**

- Nine extra lines after the ordinary ones: `aql_log_source_volume`, `aql_rule_match`, `aql_history`, `aql_waf_collection`, `aql_ips_collection`, `aql_property_watch`, `aql_catchall_senders`, `aql_geo_collection`, `aql_qradar_health`.
- **Pass = no `FAIL` among the `aql_*` lines.** A `FAIL` names the step that failed (for WAF and IPS: the breakdown or dimension), the HTTP status, the query version and the property names; fix it by OPS-R72 and run the command again. A `SKIP` (family off, nothing configured, MCP console) is not a Fail.
- A `WARN` (timeout, unavailable console, rate limiting HTTP 429, a 5xx, no free permit) is not a Fail: write the id and its cause in the Note field.
- No line holds an event value, an IP address or QRadar's own error text.

**Record:** The exit code, the status of each `aql_*` line, and the cause of every `WARN` or `FAIL`.

#### MT-10 — Outage duration and the interface's return [core]

**Action:**

- Use the start and end time of the upgrade command (MT-06 or MT-07). Open `https://<host>/login` in a private window, so that no session is held. A browser that still holds a valid session is sent on to `/` and does not show the form (MT-23).

**Expected:**

- The upgrade finishes in about a minute to two minutes on a small database (47 to 118 s wall in the measured runs, depending on the path and on whether the images are pulled). The upgrade outage was 15 to 39 s in the measured runs. The measured 0.1.10 → 0.1.11 times: online, which pulls the images from the registry inside the command, 99 s wall and 25.9 s of outage; offline from the bundle, which loads the images from it inside the command, 80 s wall and 20.3 s of outage; and with the images already on the machine, 47 s wall and 21.3 s of outage. The first 0.1.11 run (0.1.10 → 0.1.11, online, seven migrations, the lab VM) took 71 s wall and 22.9 s of outage. The measured 0.1.11 → 0.1.12 times: online from the registry, with no migration, 118 s wall and 39.0 s of outage (the pull runs inside the command, which is why its outage is at the top of the band); offline with the images already on the machine, 47 s wall and 21.3 s of outage. The 0.1.13 → 0.1.14 upgrade ran no migration: online from the registry, 101 s wall and 34.7 s of outage (REL-15e). The 0.1.14 → 0.1.15 upgrade ran six: offline from the loaded images, 49 s wall and 22.3 s of outage (REL-15e). The 0.1.15 → 0.1.16 upgrade runs three and recreates the `postgres` container; no time has been measured for it yet. Write your own figures in the Note field. These are not promises: the outage window is shorter than the wall time because the image step is outside it. An outage of more than 10 minutes is written in the Note field (on a large database the time of the dump is added; this is not a promise).
- The sign-in page shows the `h1` “Sign in” (the browser tab reads “Sign in · Behavior Analytics”) and under it `API version 0.1.16`. The page is English whatever the `ba_locale` cookie holds.

**Record:** The upgrade duration (minutes), the estimate of the outage duration, and the version line on the sign-in page.

#### MT-11 — Collection runs again after the first cadence [core]

**Precondition:** You know the end time of the upgrade. You have signed in with the administrator account (section 3).

**When to look:** After the upgrade, for each family that is on, its own cadence on the page + 3 minutes (the "Timing and total duration" table above); the “Next run (UTC)” column gives the slot, and the run is seen up to one beat tick (1 minute) later. The seeded cadences are 5 minutes for Log source volume, Anomaly explanations, Catch-all senders and Geography (on by default) and for Rule matches and Property watches (off by default), and 15 minutes for QRadar health (on by default), WAF collection and IPS collection (off by default) (`docs/CONFIGURATION.md`, “Search cadence and bucket collection”; migrations `0028` and `0050`). The first run is therefore seen up to 8 minutes after a slot for the 5-minute families and up to 18 minutes for the 15-minute ones; the “Cadence” cell reads `5 min` or `15 min`, not 8 or 18. A family set to a longer cadence is read at that cadence + 3 minutes, so the longest cadence on the page decides when the check can be closed.

**Action:**

- Open the `/admin/search-cadence` page (“Search cadence”). Read the “Cadence” value of each family and note the longest one.
- When the wait is over, reload the page and compare the “Last run (UTC)” column with the end time of the upgrade.

```bash
./qradar-dash doctor
```


**Expected:**

- The “Last run (UTC)” time of every enabled family that has a cadence is after the end of the upgrade. “Next run (UTC)” shows only a future time, or, for a family that is due now (and has run at least once), the time. The next run of a disabled family is a dash (—), even if the time would be in the future. The computed time of a family that has never run is also a dash if it lies in the past. A family that has never run shows “No run yet” or “Not run since upgrade” (the dash or this text; it is not zero). It is expected that the next run of a disabled family (for example, if WAF is off on the site) is a dash.
- If you look before the wait is over, you see “No run yet” or an old time: this is not a fault, do not write Fail, wait.
- The wait of a family is its **own** cadence + 3 min, not 8 min: the 8 minutes hold only for a family at the 5-minute default. A family set to 1 hour waits up to 63 minutes. The slot is epoch-aligned (a 1-hour family has slots at :00) and opens after the settle margin of 120 s (`backend/app/services/search_cadence.py:60-67`, `:187-206`; `backend/app/core/config.py:303`), so the “Next run (UTC)” column is the authority: it shows the time of the next run, for example `13:02` for a 1-hour family whose last slot was 12:00. A run that **failed** (for example because the console had just booted) still takes its slot and is not repeated before the next one, so “Last run” can stay older than the end of the upgrade, with “Failed” as its outcome, until “Next run (UTC)”. OPS-33 saw exactly this for Catch-all senders (last run 12:20 “Failed”, before the upgrade ended at 12:26; next run 13:02); OPS-30 and OPS-34 saw the same one-hour spacing. Those times fit a 1-hour cadence, while the default is 5 minutes (`backend/app/models/enums.py:130-135`), so read the “Cadence” cell first and add 3 minutes to that. **Read the “Cadence” cell, not the “Bucket” cell.** The Cadence column is the run interval the scheduler claims slots at (`backend/app/services/search_cadence.py:95-129`); Catch-all senders' collector then reads up to `cadence_seconds // bucket_seconds` buckets per run plus the backfill allowance (`backend/app/collectors/catchall_collector.py:301-304`), so the family has no separate slot rule. The Bucket column is the event bucket width (`backend/app/services/search_cadence.py:38-51`): Catch-all senders and Geography collect 5-minute buckets even when their cadence is 1 hour, so a 5-minute figure beside them is not their cadence. The “Worst-case detection delay” column settles it: it is cadence + bucket + settle (`frontend/src/lib/searchCadence.ts:60-74`), so “up to 1 h 7 min” in the Catch-all senders row is a 1-hour cadence with a 5-minute bucket (3600 + 300 + 120 s), and its next run is then about an hour away. That row is the site's stored cadence setting, not a page that disagrees with the scheduler. It is a Fail only when the time in “Next run (UTC)” has passed by more than one beat tick (1 minute) plus the duration of a run and “Last run” has still not moved; a “Next run (UTC)” farther away than the row's own cadence + 3 min is also a Fail, and then write the row's Cadence, Bucket, Worst-case and Next run cells in the Note field.
- `doctor`: `cadence` PASS (`<n> cadence row(s) are on time or disabled`), `celery_workers` and `celery_beat` PASS. `watermarks` and `errors_recent` are PASS (`errors_recent`: `no watermark has recent failures`) except for the cases below; a row of a switched-off family is not judged and is named after `not judged (family off):` (MT-02). Any other `FAIL` is Fail. A `watermarks` `FAIL` for a collector that has had its turn since the upgrade and still shows a `last_error` is Fail (OPS-R30); for a collector whose turn has not come, it is not, and `doctor` is read again after that turn. If `cadence` is a WARN, write the names of the families in the Note field and wait another cadence + 3 min.
- On a console that has no offenses, a clean `offense_snapshot` run moves the watermark to the time of the run. Since 0.1.9 this is so; 0.1.8 kept the WARN of about 168 hours on the same console even after a clean run (MT-02). While `consecutive_failures` is 0 and `last_error` is empty, the lag stays within the cadence; no WARN is expected for this line. A failed or truncated run does not advance the watermark. In that case the WARN can remain and is not a Fail; write it in the Note field. The lag runbook is OPS-R30.
- The same clean run also clears the page-ceiling stall. If the stall sentence remains after a clean run, the runbook is OPS-R66; this alone is not a Fail, write it in the Note field. A stall is not a `last_error`.
- If the console was changed (an upgrade on the same console is not this case), `log_source_inventory` and `rule_inventory` are hourly (default 3600 s). These two inventories, if they failed at the change, can wait up to one hour for their next beat. Until that time is over the pages show no sources; if the rule inventory has not arrived, the rule list is empty too. This is not a Fail; write it in the Note field and wait for the hourly retry. Meanwhile `watermarks` can be FAIL for only these two rows and `errors_recent` can be WARN; neither is a Fail.
- Data-dependent checks done before this check (Dashboard, anomalies) can show “stale” or “not collected” until this wait is over; this is the correct state.

**Record:** The longest cadence, the last run time (UTC) of each family, the `doctor` exit code and the WARN/FAIL ids.

#### MT-144 — The alerting, triage and rule-match defaults are unchanged [core]

**Precondition:** The upgrade is complete (MT-08) and the first cadence has passed (MT-11). Signed in with the administrator account. `$SESSION` is the cookie `qd_session=<value>` of that signed-in window (developer tools, Application, Cookies); it is a secret and is written nowhere.

**Action:**

- Read the policy list, the digest status and the `.env` lines that 0.1.15 added (all optional; 0.1.16 changes none of these defaults), then the worker's log and the switch line of `doctor`:

```bash
curl -s --cookie "$SESSION" https://<host>/api/v1/alert-policy
curl -s --cookie "$SESSION" https://<host>/api/v1/alert-policy/digest
grep -E '^(ALERT_POLICY_DEFAULT|DIGEST_HOUR_UTC|TRIAGE_STALE_CLOSE_DAYS|RULE_MATCH_COLLECTION_ENABLED|RULE_ALERTING_ENABLED|LOW_VOLUME_MODEL_PREVIEW_ENABLED)=' .env || echo "none set: the defaults apply"
./qradar-dash logs --since=5m celery-worker | grep collect_rule_matches
./qradar-dash doctor | grep families_off
```

- Open Administration → “Alert policy” (`/admin/alert-policy`).

**Expected:**

- The policy list is `[]`: no `alert_policy` row exists, so no source is recorded only or digested. `/admin/alert-policy` says “Every source alerts as before (default ALERT).” and “No policy row exists, so no source or class is recorded only or digested.”; its “Quiet periods” section says “Nothing is quiet. Every condition alerts by its policy.” (`frontend/messages/en/alertPolicy.json`, `empty`; `suppression.json`, `quiet.empty`). No quiet period exists until an analyst makes one (MT-157).
- The digest answer on a site with no e-mail relay is `"digest_hour_utc":7`, `"email_configured":false`, `"recipient_count":0` (REL-15e). The page shows “Daily at 07:00 UTC” under “Due” and “0 addresses” under “Recipients now”. The digest sends nothing: “Last run” is absent (“No digest has run yet. The first is due at 07:00 UTC.”), “Not e-mailed” (the task writes one record a day, state `NOT_CONFIGURED`, with no e-mail sent) or, on a site that has a relay and no digest incident, “Nothing to send”. “Delivered” or “Delivery failed” is a Fail: with no policy row no source can be in digest mode. A site that already e-mails alerts (MT-138) shows its own relay state and recipient count; the policy list is still `[]`.
- `.env` has none of the lines, or only values the site set on purpose (write them in the Note field). The defaults are `ALERT_POLICY_DEFAULT=ALERT`, `DIGEST_HOUR_UTC=7`, `TRIAGE_STALE_CLOSE_DAYS=3`, `RULE_MATCH_COLLECTION_ENABLED=false`, `RULE_ALERTING_ENABLED=false` and `LOW_VOLUME_MODEL_PREVIEW_ENABLED=true` (`backend/app/core/config.py`).
- The worker log has one line a minute of the form `Task collect_rule_matches[…] succeeded in … s: {'status': 'disabled'}` (`backend/app/workers/tasks.py:480-481`; the beat entry ticks every minute, `celery_app.py:165`). `doctor` shows `RULE_MATCH_COLLECTION_ENABLED=off` in the `families_off` line. A site that turned the family on on purpose reads `RULE_MATCH_COLLECTION_ENABLED=on`, and a `collect_rule_matches` line with another status is then correct: write it in the Note field. Even then, `RULE_ALERTING_ENABLED` unset means rule incidents are recorded and open no alert.

**Record:** The policy list answer, the digest answer (without any address), the `.env` lines found, the `collect_rule_matches` status and the `families_off` switch for rule matches.

#### MT-145 — Role permissions after the upgrade [core]

**Precondition:** The upgrade is complete (MT-08). Signed in with an account that has `admin:*`. The upgrade adds no permission to an existing role row: `anomaly:triage` and `alert:suppress` are on the seeded `admin`, `analyst` and `detection_engineer` rows only on a site that was seeded at 0.1.15 (`backend/app/services/seed.py`). The upgrade does not call `ensure_default_roles`.

**Action:**

```bash
curl -s --cookie "$SESSION" https://<host>/api/v1/users/roles
curl -s --cookie "$SESSION" https://<host>/api/v1/me
```

- If the site has an existing `analyst` or `viewer` account, sign in with it (do not create one here; MT-16 is the lab check for that), open an anomaly (`/anomalies/<id>`) and an alert (`/alerts/<id>`), and read the Triage block and the quiet-period button. Sign out.

**Expected:**

- In the roles answer, neither `anomaly:triage` nor `alert:suppress` is in the `permissions` list of `analyst`, `detection_engineer`, `viewer` or `admin` on an upgraded site (REL-15e: `admin` held `admin:*, search:execute, … admin:classification`; `viewer` only `read:*`). If either string is present, `python -m app.cli.users create-admin` or `python -m app.services.seed` has refreshed the roles since the upgrade (it adds missing strings to the four seeded rows): write it in the Note field. That is not a Fail.
- `GET /api/v1/me` for the administrator lists both `anomaly:triage` and `alert:suppress` in `effective_permissions`: `admin:*` expands to every permission (`backend/app/api/routes/me.py:25-38`). So the administrator can triage and quiet a condition at once.
- The analyst and viewer accounts see the Triage block read-only with “You can read this triage but not change it. Changing it needs the anomaly:triage permission.” (`frontend/messages/en/triage.json`, `panel.readOnly`) and, on the alert, “Quieting a condition needs the alert:suppress permission.” (`suppression.json`, `button.noPermission`). The viewer, who holds only `read:*`, reads the triage and the quiet periods and cannot change either. Writing a shift handover needs `anomaly:triage` too: these accounts read `/handover` and have no “Write handover” button (MT-164, MT-165). 0.1.16 adds no permission and changes no role row (`backend/app/security/rbac.py` and `backend/app/services/seed.py` are the same as at 0.1.15). With no such account the part is `Not run`.
- There is no screen that edits a role's permission list. The administrator who wants an analyst to triage grants it with the site's own role procedure; this check does not.

**Record:** Whether each role row lacks the two strings, whether `effective_permissions` of the administrator lists both, and the read-only sentence seen by the analyst or viewer.

#### MT-159 — `doctor` line `entity_mentions` and the entity-mention backfill [core]

**Precondition:** The upgrade is complete (MT-08). The backfill is a beat task, `backfill-entity-mentions`, every `ENTITY_MENTION_BACKFILL_INTERVAL_SECONDS` (default 300 s; `backend/app/workers/celery_app.py`), so its first tick comes five minutes after beat starts; read this check after MT-11. It fills the entity timeline's index for rows stored before 0.1.16 (`/entities`, MT-166), in bounded batches (500 rows, 20 batches a tick by default), reads only stored aggregates and calls no QRadar API. In a production site do **not** run `python -m app.cli.entity_mentions run`: it ignores the beat task's switch and works until it is done.

**Action:**

```bash
./qradar-dash doctor | grep entity_mentions
docker compose exec -T backend python -m app.cli.entity_mentions status
```

Read the two again after one more tick (5 minutes) if the first read is not `PASS`.

**Expected:**

- The `entity_mentions` line is never `FAIL` (`backend/app/cli/doctor.py`, `check_entity_mentions`). Right after the upgrade it is `WARN` with runbook OPS-R73: `the entity-mention backfill has not run yet; run it now: python -m app.cli.entity_mentions run` while no state row exists, or `entity timeline index incomplete for {n} source(s): <console>/<SOURCE> (<STATUS>, {n} row(s) written); …; absence cannot be proven there until it completes; run it now: python -m app.cli.entity_mentions run`. A `RUNNING` source that has not changed for three ticks adds `, not advancing since <date> <time> UTC`. A failed source reads `entity-mention backfill failed for <console>/<SOURCE> (<reason>); retry: python -m app.cli.entity_mentions run`. These are the correct state of an index that is still being filled, not a Fail of the upgrade.
- When every source is `COMPLETE` the line is `PASS` with `{n} source(s) indexed, {m} row(s) written`. With `ENTITY_MENTION_BACKFILL_ENABLED=false` and no state row it is `PASS` with `entity-mention backfill is switched off (ENTITY_MENTION_BACKFILL_ENABLED=false)`.
- `status` prints one line per console and source, `<console> <SOURCE> <STATUS> rows_written=<n> started=<time> completed=<time> last_error=<text or ->`, with the sources among `EXPLANATION`, `WAF_TOP`, `IPS_TOP`, `IPS_ENTITY`, `OFFENSE`, `CATCHALL`, `PW_VALUE` and `LOG_SOURCE` (`backend/app/cli/entity_mentions.py`; the list is the check constraint of migration `0062`). Before the first tick it prints `the entity-mention backfill has not started: no state rows exist yet`. The statuses are `PENDING`, `RUNNING`, `COMPLETE` and `FAILED`.
- Pass: no `FAIL`, and either `PASS`, or a `WARN` whose `rows_written` grows between two `status` reads a tick apart. A source that stays `FAILED` on two ticks in a row, or a `RUNNING` source that does not advance, is a Fail: write its line (the `last_error` text, no secret) in the Note field. How long a large history takes is not a Fail; write the time of the last read.
- `LOG_SOURCE` is `COMPLETE` from the start with `rows_written=0`: it needs no backfill, and its rows come from the log-source inventory sync (every `LOG_SOURCE_SYNC_INTERVAL_SECONDS`, default one hour). So `PASS` here does not mean that the entity timeline's “Log sources” lane has rows: until the first sync after the upgrade, that lane reads “Partial” for the identifier or name of a registered log source (MT-166). That is expected.

**Record:** The `entity_mentions` line and the `status` lines (with the time of the read).

#### MT-160 — The postgres container runs with TimescaleDB telemetry off (FIX-47) [core]

**Precondition:** The upgrade is complete (MT-08). The upgrade starts the `postgres` image of the new version, so Compose recreates the `postgres` container (its image changed); the data volume is kept. The new image starts PostgreSQL with `-c timescaledb.telemetry_level=off` (`deploy/postgres/Dockerfile`, `CMD`; `docs/CONFIGURATION.md`). The setting is server-wide, so it covers every database, including one created later.

**Action:**

```bash
docker compose exec -T postgres sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -tAc "SHOW timescaledb.telemetry_level;"'
docker compose images postgres
docker compose ps postgres
```

**Expected:**

- The first command prints exactly `off`. Any other value (for example `basic`) is a Fail: the container is still the old one or was started from another image; write the value and the image tag in the Note field.
- `docker compose images postgres` lists the tag `0.1.16`.
- `docker compose ps postgres` shows the container `healthy`, with an uptime no longer than the time since the upgrade started (the container was recreated by the upgrade; it was not left running from before). The restart of the database is inside the upgrade's outage (MT-10).
- Nothing is written, and the interface does not change.

**Record:** The value printed, the image tag and the uptime of the container.

#### MT-126 — The rate limit does not cut the page in production [core]

**Precondition:** The upgrade is finished (MT-08). The session is open (MT-12). `.env` is not changed.

**Action:**

- The script uses `API_LOOPBACK_PORT` from `.env` (default 8000). If the port is different, write that one.

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/health/ready
```

- While signed in, open the `/alerts` page once. `/` is fine too.

**Expected:**

- `health/ready` returns HTTP 200. This endpoint does not enter the limit; a 200 does not mean the limit is off.
- The page does not show this sentence: “Too many requests came from this address within a minute.” “Could not be loaded” messages are a different fault and are not the 429 of this check.

**Record:** The HTTP code and whether the sentence did not appear (yes/no).

#### MT-127 — The lowered limit gives 429 and the retry sentence (writes) lab only

**Precondition:** **lab only.** In production, `Not run`. This check temporarily sets the `RATE_LIMIT_DEFAULT` line in `.env` to `2/minute` and recreates only the `backend` container. Secrets are not written on the page. If `.env.rate-limit-before` already exists, do not start; run the Cleanup below first.

**Action:**

- In the install directory, take the copy, lower the limit and recreate the backend. `--no-deps` stops; postgres is not recreated. `COMPOSE_FILE` and `COMPOSE_PROJECT_NAME` are already in `.env`; the command uses plain `docker compose` from the install directory.

```bash
cd <install directory> \
&& test ! -e .env.rate-limit-before \
&& cp -a .env .env.rate-limit-before \
&& ( umask 077; awk '
  BEGIN { done = 0 }
  /^RATE_LIMIT_DEFAULT=/ {
    if (done == 0) print "RATE_LIMIT_DEFAULT=2/minute"
    done = 1
    next
  }
  { print }
  END { if (done == 0) print "RATE_LIMIT_DEFAULT=2/minute" }
' .env.rate-limit-before > .env.rate-limit-new ) \
&& test -s .env.rate-limit-new \
&& chmod 600 .env.rate-limit-new \
&& mv .env.rate-limit-new .env \
&& docker compose up -d --force-recreate --no-deps backend
```

- If the block stops before the `mv` line, `.env` is unchanged. Delete the file `.env.rate-limit-new` and start again. If it stops after `mv` has run, run the Cleanup.

- Wait until `health/ready` is 200. This endpoint does not enter the limit; the container stays healthy. Staying healthy does not mean the limit is off. The port is the value of `API_LOOPBACK_PORT` (default 8000).

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/health/ready
```

- Pass the limit with an open `GET` to the same endpoint. `/api/v1/version` needs no session; the limit runs before the identity check. Do not use `/health`. These three requests share one key: the peer address the backend sees on its loopback port. This address is not the analyst's key; these three requests do not make the browser's page a 429.

```bash
for n in 1 2 3; do
  echo "---- $n ----"
  curl -s -i http://127.0.0.1:8000/api/v1/version
  echo
done
```

- After the minute has passed, see that a counted refusal also carries the window. An anonymous `GET /api/v1/me` returns 401 and is counted; the response carries no `Retry-After`:

```bash
curl -s -i http://127.0.0.1:8000/api/v1/me
```

- Then, in the signed-in browser, open `/alerts/00000000-0000-0000-0000-000000000099` (an id that does not exist) and look at the 404 response in the network panel. A 404 is counted too; it has the three `X-RateLimit-*` headers and no `Retry-After`.

- While signed in, reload the `/alerts` page until the sentence appears. At 2 requests per minute the third load of the same path gives a 429. A full load also reads `GET /api/v1/me`; if that read is the refused request, the sentence appears at the shell's gate instead of the page. If the minute has passed, reload again. Do not use the Dashboard: a widget whose data stops shows only the short note.

**Expected:**

- The first two `curl` calls return HTTP 200. Both have `X-RateLimit-Limit: 2` and `X-RateLimit-Remaining` is present.
- The third `curl` returns HTTP 429. `Retry-After` is a number in seconds. `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` are present. The body contains `"detail":"rate limit exceeded"`. Do not match the `limit` field in the body and do not write it on the page; that field is not a stable number.
- `GET /api/v1/me` returns HTTP 401; the response has `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset` and no `Retry-After`. This shows that a counted refusal carries the window.
- The 404 response in the browser also has the same three `X-RateLimit-*` headers and no `Retry-After`. `Retry-After` belongs to the 429 only.
- The page says: “Too many requests came from this address within a minute. Nothing is wrong with the data or the backend. Try again in N s.” N is the `Retry-After` seconds.
- The catalog also has a second form without a wait time: “Try again in a minute.” Because the 429 of this lab carries `Retry-After`, the page shows the numbered form.
- The sentence can appear on the `/alerts` page or at the shell's gate; both are Pass. The gate writes the same sentence and a “Try again” button without showing any part of the page. If the gate shows “The session could not be read.” and the raw detail `rate limit exceeded`, the check is Fail.

**Record:** The three HTTP codes; whether the third response has `Retry-After` and `X-RateLimit-Limit`; whether the 401 and 404 responses have the three `X-RateLimit-*` headers and no `Retry-After`; the first five words of the sentence. The address, the `.env` line and the `limit` field are not written.

**Cleanup:** Run this block even if one of the steps is Fail. The copy holds secrets; do not read it and do not write it anywhere.

```bash
cd <install directory> \
&& test -f .env.rate-limit-before \
&& cp -a .env.rate-limit-before .env \
&& chmod 600 .env \
&& docker compose up -d --force-recreate --no-deps backend \
&& rm -f .env.rate-limit-before
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/api/v1/health/ready
```

- If `test` cannot find the copy, the chain stops and `rm` does not run. If the copy is there, put it back by hand: `cp -a .env.rate-limit-before .env && chmod 600 .env`. Do not delete the copy until the backend is up with the old limit.

- `health/ready` is 200 again. When `/alerts` loads once, the 429 sentence is not there.

### 3. Sign-in

Open `https://<host>/` in the browser. The interface is English only (D-41); the labels in this section are the ones the English interface shows.

#### MT-12 — Sign-in of an existing user [core]

**Action:**

- Open `https://<host>/login` and sign in with the existing administrator account. The form fields are “Username” and “Password”; the button is “Sign in”.

**Expected:**

- The page redirects to `/` and the “Dashboard” page opens. The user name is shown in the top bar.
- The text “The session could not be read.” does not appear.
- On the administrator's first sign-in, if the console's "read the history?" decision has never been recorded, a dialog opens. With a day count the title is “Read the last 28 days from QRadar?”; 28 is the `HISTORY_BACKFILL_DAYS` default, and if the site has a different value, that number is shown. If the day count is not available yet, the short form appears: “Read QRadar's past?”. Choose “Decide later” here; record nothing (section 10).

**Record:** Whether the sign-in worked; whether the decision dialog appeared on the first sign-in; the user name is not written, only the role (admin, analyst, viewer…).

#### MT-13 — Account menu and API version [core]

**Action:**

- Click the user name in the top bar.

**Expected:**

- The menu has the line “Signed in as {name}”, the line “Source: {source}” (`LOCAL` or `LDAP`), the line “Roles: {roles}”, the link “Account settings” and the button “Sign out”. A local account also has a “Change password” link.
- The version line in the menu is `API version 0.1.16`.

**Record:** The API version in the menu.

#### MT-14 — Account page [core]

**Action:**

- Open the `/account` page (“Account settings” in the menu).

**Expected:**

- The title is “Account settings”. There is one section, “Password”; there is no language section (D-41; MT-131).
- On an LDAP account the password section shows the text “An LDAP account changes its password in the directory, not here.”; on a local account the password form is embedded (“Current password”, “New password”, “Confirm new password”).

**Record:** The account's source (`LOCAL`/`LDAP`) and whether the page opened.

#### MT-15 — Signing out and in again [core]

**Action:**

- Choose “Sign out” in the menu. Then sign in again with the same account.

**Expected:**

- On sign-out `/login` opens. On signing in again `/` opens.

**Record:** Whether sign-out and sign-in worked.

#### MT-143 — A request right after sign-in is accepted (FIX-23)

**Precondition:** Upgrade complete (MT-08) and MT-12 done. A browser, and the developer tools it ships with; nothing is changed in `.env` or on the console. This check sends only the requests the page makes by itself.

**Action:**

- Open a private window at `/login`, open the browser's developer tools on the Network tab and keep the request log.
- Sign in with the administrator account and read, in this order, the `POST …/api/v1/auth/login` request and every request the page makes after it with the `qd_session` cookie.
- Sign out (MT-15) and sign in again, five times in all, and read the first requests after every sign-in.

**Expected:**

- `POST …/api/v1/auth/login` answers `200` and sets the `qd_session` cookie (`HttpOnly`, `SameSite=Strict`).
- Every request the page makes after that answer answers `200`; none answers `401`. No page shows “Your session has expired. Sign in again to continue.” and the browser is not sent back to `/login`. The login commits its session row before the answer is sent (`backend/app/api/deps.py:19-23`), so the page's own first requests may already use the cookie.
- The Network tab also holds one `401` per sign-in **before** that `POST`: the sign-in page's shell reads the session at once with `GET /api/v1/me` (`frontend/src/app/layout.tsx:30` mounts the provider on every page, `frontend/src/components/auth/session.tsx:137-143` sends the read on mount, and `:119-126` records the `401` as signed out), and the private window has no `qd_session` cookie yet. That request is the start of the log, not a Fail: this check is about the requests after the login `POST`, which is followed by a second `/me` read with the new cookie (`frontend/src/app/login/LoginForm.tsx:59`, `:66`).
- The top bar's connection pill reaches “Connected”; it can read “Checking” for a moment first (UX-1).
- This is a behaviour check, not a timing measurement: the defect FIX-23 removes answered `401` to a request sent at 0 ms after the login and `200` at 50 ms, which a person cannot hit on purpose. The requests the page makes by itself right after the sign-in are exactly the ones the defect refused, so one `401` among the requests after the login `POST` is a Fail (write the request's path and status, never the cookie's value); repeated passes do not prove the timing, only the behaviour.

**Record:** For each of the five sign-ins, the status of the login POST and of the first requests after it; any `401` and the page the browser landed on.

#### MT-16 — Creating a new user (writes) lab only

**Order.** If MT-123 will be run, do this check and MT-17, MT-18 and MT-19 after MT-123's re-upgrade is finished. The rollback removes the accounts created after the upgrade. In production these four checks stay `Not run`.

**Precondition:** An administrator account. **lab only**: there is no user delete in the interface (under `/admin/users` there are only roles, disable, unlock and password reset). At the end the test account is only disabled and stays in the list. In production mark this check and the next three `Not run`; write the reason in the Note field.

**Action:**

- Open the `/admin/users` page (“Users”). The form section is “Create an account”.
- Fields: “Source” = `LOCAL`; “Username” = `mt-test-<day>` (lower case, for example `mt-test-1101`); “Display name”; “Initial password” = a password of at least 12 characters that differs from the user name (do not write this password on paper); role = “viewer”. Then press the “Create account” button.

**Expected:**

- The result line: “Created local user {name}. The account must change the password at the next sign-in.” The password is shown once: “The initial password for {name} is shown once. The account must change it at the next sign-in.”
- The table has a new row: source `LOCAL`, status `active`. Open the row with “Manage”: “Password change” = “Required at the next sign-in.”

**Record:** Whether the account was created (yes/no). The user name and password are not written.

**Cleanup:** The check "Disabling the test account" (MT-19).

#### MT-17 — Forced password change (writes) lab only

**Precondition:** The account was created in the previous check. Open a second browser window or a private window; do not sign out the administrator session.

**Action:**

- In the private window open `/login` and sign in with the new account and the initial password written by the administrator.
- Try to change the page: type `/alerts` in the address bar.
- In the password change form try, in this order: (1) “Current password” = the initial password, “New password” and “Confirm new password” = a 6-character value; (2) the new password and the confirmation are different; (3) a valid new password (12+ characters, different from the user name), same confirmation.

**Expected:**

- After sign-in the browser goes to `/account/password`; the title is “Change password”, the description is “This account cannot continue until the password is changed.” At the top right there is a “Sign out” button; there is no menu and no navigation.
- Typing `/alerts` brings the same form (no other page is shown). The backend refuses this account's other requests with 403: “A password change is required before any other request.”
- (1) Under the heading “The password was not changed.” the line “password must be at least 12 characters”; (2) “The new password and the confirmation do not match.”; (3) “The password is changed.” and the page redirects to `/`. From then on the account sees all the pages it is allowed to see.

**Record:** The result of the three steps (yes/no). The passwords are not written.

**Cleanup:** The "Disabling the test account" check.

#### MT-18 — The viewer role's limit (writes) lab only

**Precondition:** The password was changed in the previous check; the test account has the `viewer` role and is signed in (private window).

**Action:**

- Type the address `/admin/users`. Then type `/admin/directory`. Look at the navigation.
- On the `/` page open the “Dashboards” menu. In the template group there is only “SOC Overview (template)”. “QRadar Administration (template)” is not in the menu.
- When the window is 12 columns wide, “Customize” is shown. In a narrow window it says “Widen the window to customize” and there is no customizing. Open “Customize”, then “Add widget”. In the catalog look at the “Outage policy” row.

**Expected:**

- `/admin/users`: “Your role cannot administer users (permission admin:users).” `/admin/directory`: “Your role cannot change the directory (permission admin:auth).”
- The navigation has no “Users” and “Directory” links.
- In the catalog the “Outage policy” row says “Your role lacks permission admin:outage.” The “Add” button is disabled; the button's accessible name is “Add Outage policy”. The “QRadar Administration” template is not in the menu; the viewer cannot save it (there is no `dashboard:write`).

**Record:** The result of the three observations. The forbidden state is read in the disabled Add row of the catalog, not in a placed widget (section 5).

**Cleanup:** The "Disabling the test account" check.

#### MT-19 — Disabling the test account (writes) lab only

**Precondition:** The administrator session. Close the private window.

**Action:**

- On the `/admin/users` page open the test row with “Manage” and press the button “Disable {name}”.

**Expected:**

- The result line is “Disabled {name}.”; the row's status is `disabled`. The account can no longer sign in. There is no user delete: this is the way to remove access, and the `user.disable` audit record stays.

**Record:** Whether the account was disabled.

**Cleanup:** This is the cleanup. The account cannot be deleted; it stays in the list as disabled. Write this in the Note field.

### 4. Language

**0.1.10 (D-41).** The interface is English only; there is no language control. This section keeps its ids from the earlier releases so that numbering and history stay stable. Each check is one of three kinds:

- **MT-131** checks that the interface is English only, whatever the cookie or the account holds.
- **English checks** (MT-21, MT-22, the `English:` page checks MT-23 to MT-56, and MT-57): they open a page (or try the sign-in) and check that its fixed words and the sentences built from message codes are English sentences from the catalog (`frontend/messages/en/`, `backend.json` for the backend's sentences).
- **Removed in 0.1.10 (D-41: the interface is English only)**: MT-20, MT-58, MT-59 and MT-124. Their only subject was the Turkish language or the language control. Nothing is run; record them as `Removed`.

**Raw key.** If a message has no catalog entry, a dotted lower-case string of the form `namespace.key` is shown instead of the text, for example `alerts.detail.title` or `anomalies.list.metaTitle`. If you see such a string anywhere in this test, write the page, the string and the step in the Note field; this is Fail. The same applies to any Turkish text (a Turkish letter in a sentence the interface wrote): the catalogs of Turkish are frozen and must never show.

**What stays as the source wrote it.** Text that comes from QRadar, from a provider or from the operator is shown as it is and is not checked here: offense descriptions, log source, rule, tactic and technique names, the operator's resolution reason, search and rule names, `anomaly.error`, the `last_error` of a watermark, and Ariel's own error messages. Terms of the glossary (`coverage`, `scope`, `baseline`, `offense`, `log source`, `instance`, `custom property`, `cron`, `EPS`, `robust z`) are English everywhere.

#### MT-131 — English only (LANG-1, D-41)

**Precondition:** Signed in. No language is changed in this check.

**Action:**

- Open `/account`.
- In the browser's storage set the cookie `ba_locale` to `tr` (path `/`), then open `/` and `/login` as a fresh document load.

**Expected:**

- `/account` has a password section (for a local account) and no "Interface language" section.
- With `ba_locale=tr` the pages are English, with no Turkish text and no raw message keys. The page does not reload in a loop and does not rewrite the cookie.
- An account whose stored preference is Turkish (`ui_language = "tr"`) also sees English.

**Record:** Whether `/account` has a language section; the language of `/` and `/login` with the cookie set.

**Cleanup:** Delete the `ba_locale` cookie.

#### MT-20 — Removed in 0.1.10 (D-41: the interface is English only)

The check was the language switch on `/account` (“Interface language”) and how the choice is remembered. The control does not exist while `ENABLED_LOCALES` holds one locale. MT-14 checks the account page and MT-131 checks that there is no language control and that a cookie or account preference does not change the language. Nothing to run; record `Removed`.

#### MT-21 — Number, ratio and time format [core]

**Action:**

- Open a page that shows a number of a thousand or more. Examples: `/alerts` (“Showing {shown} of {total} alerts.”, if the total is 1000 or more), `/log-sources` (“{count} sources.”), the EPS box on the `/` page, the “Searches” column.
- Open a place that shows a ratio (for example the source table of `/behavior/waf`, “Endpoint coverage”, or the KPI boxes on `/`). Open a place that shows a time (for example `/alerts`, “Last seen”).

**Expected:**

- Thousands separator **comma**: `2,016`. Decimal separator **point**: `1,260.0`.
- A ratio has the number first and a non-breaking space before the sign: `31.8 %`. A very small share is `<0.1 %`.
- A time is 24-hour and the **UTC** zone is always written: in full `2026-10-29 12:00:00 UTC`, in a table cell `10-29 12:00 UTC`. A relative age (for example `5 min ago`) sits next to the absolute time and never replaces it.

**Record:** Whether each of the three formats is correct, and the page it was on. If there is no number of a thousand or more, write `Not run`.

#### MT-22 — Top bar and navigation [core]

**Action:**

- On any page look at the top bar and at the navigation on the left.

**Expected:**

- The navigation groups are “Behavior”, “Fleet”, “Detection”, “Operations” and “Administration”; “Dashboard” and “Guide” stand alone. The items, in order (from `frontend/src/lib/nav.ts:28-112`; the words are `frontend/messages/en/shell.json`, `nav`): “Dashboard” (alone, at the top); Behavior: “Queue”, “Handover”, “Overview”, “Anomalies”, “Sources”, “Noisy sources”, “WAF behavior”, “IPS behavior”, “Traffic”, “Property watches”, “Geography”; Fleet: “Fleet”, “Fleet sources”, “Catch-all senders”; Detection: “Offenses”, “Alerts”, “Rules”, “Rule statistics”, “Custom rules”, “Coverage”; Operations: “Log Sources”, “Searches”; Administration: “Administration overview”, “Search cadence”, “Classification rules”, “Outage alerts”, “Alert policy”, “WAF sources”, “IPS sources”, “History backfill”, “SIEM load”, “QRadar access”, “Alert e-mail”, “Users”, “Directory”; and “Guide” (alone). “Administration overview” is the first item of the Administration group and opens `/admin`. Six Administration items are shown only to a role that holds the permission: “History backfill” (`read:history_backfill`), “SIEM load” (`admin:*`), “QRadar access” (`admin:*`), “Alert e-mail” (`admin:notifications`), “Users” (`admin:users`) and “Directory” (`admin:auth`). The other Administration items need no permission here: “Alert policy” opens for any account that can read alerts, and changing a policy needs `admin:outage` (MT-158). “Queue” is the first item of the Behavior group, “Handover” follows it, and “Noisy sources”, “IPS behavior” and “Traffic” stand beside “Sources” and “WAF behavior”. “Handover” and “IPS behavior” need no permission to be listed (MT-164, MT-167).
- The top bar: “Open alerts: {count}” (the number of open alerts), “Refresh” (“Off”, “1 min”, “5 min”), “Connection status: {status}” (the status reads “Checking” while a signal has not answered yet, with no colour, and then “Connected”, “Degraded”, “Problem” or “Unknown”; it does not stay on “Unknown” for a signal that is only loading); the search box “Search sources, rules, anomalies”. Beside “Refresh” the top bar shows “Refreshed ‹time›”, the browser's clock, and “Data to ‹time›” (`frontend/messages/en/shell.json`, `refresh`): the data instant is a clock time (`14:02:11 UTC`) when it is on the current UTC day and carries its date (`2026-11-03 21:05:00 UTC`) when it is on another one (UX-7; `frontend/src/components/shell/AutoRefresh.tsx:170-177`). `/config-changes` and `/entities` are not in the navigation; `/config-changes` opens by address and `/entities` from the top-bar search (MT-166).

**Record:** The name of any item that is missing or wrong.

#### MT-23 — English: sign-in page `/login`

**Action:**

- Open the route in a private window, with no session.
- Then open `/login` in a window that is signed in. The page reads the account and sends it on: to `/` (or to the `next` address in the URL; to the password page when the password must be changed). While it reads, it shows “Loading the sign-in form…”. This is the intended behaviour, not a Fail; the form is shown to an anonymous visitor only.

**Expected:**

- The `h1` is “Sign in”; the browser tab reads “Sign in · Behavior Analytics”.
- Fields built from message codes (they must read as English sentences): the failed sign-in sentence (the separate check MT-57); the line “API version {version}”.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-24 — English: Dashboard `/` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is the name of the dashboard that opens: “SOC Overview” (the template) when the account has no default dashboard, otherwise the saved dashboard's own name. The browser tab reads “Dashboard · Behavior Analytics”.
- Fields built from message codes (they must read as English sentences): the widgets (section 5); the status reason of the property watch widget (MSG-4b); the recent anomalies and offenses; the alert count in the top bar.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-25 — English: Behavioral overview `/behavior`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Behavioral overview”; the browser tab reads “Overview · Behavior Analytics”.
- Fields built from message codes: the state words and the “Needs attention” table.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-26 — English: Anomaly list `/anomalies` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Anomalies”.
- Fields built from message codes: the filters, the state and detector names, the subject kinds. The rows hold no stored sentence.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-27 — English: Anomaly detail, log source volume `/anomalies/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is the name of the log source (“Unknown source” if the anomaly has none). The fixed words “Anomaly investigation” are the browser tab (“Anomaly investigation · Behavior Analytics”).
- Fields built from message codes: on the “Technical details” tab the “Detector reasoning” sentence (MSG-5); on the “Lifecycle” tab the reason of each transition (MSG-5).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-28 — English: Anomaly detail, WAF endpoint `/anomalies/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is the endpoint, `<METHOD> <normalized path>` (for example `GET /portal/login`). The fixed words “Anomaly investigation” are the browser tab (“Anomaly investigation · Behavior Analytics”).
- Fields built from message codes: the reasoning sentence and the alert note in the detector block (MSG-6), with the labels “Materiality floor” and “Verified history”; the lifecycle reasons (MSG-5).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-29 — English: Anomaly detail, IPS `/anomalies/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is the IPS subject: the entity the detector judged, or “Source-level” for a source-level incident (“Entity not recorded” if an entity incident has none). The fixed words “Anomaly investigation” are the browser tab (“Anomaly investigation · Behavior Analytics”).
- Fields built from message codes: the reasoning and the alert note in the detector block (MSG-6b); the lifecycle reasons (MSG-5). A suppressed IPS anomaly shows only the “Suppressed” label; the sentence with the suppression reason is not shown.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-30 — English: Anomaly detail, property watch value `/anomalies/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is the watched value (the value key, or its components joined with “ × ”). The fixed words “Anomaly investigation” are the browser tab (“Anomaly investigation · Behavior Analytics”).
- Fields built from message codes: the explanation, the claim and the history verification sentences (MSG-4b); the transition reasons (MSG-5).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-31 — English: Source behavior `/behavior/sources` and `/behavior/sources/<id>`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Sources” on the list, and the log source's name on `/behavior/sources/<id>` (its browser tab reads “Source behavior · Behavior Analytics”).
- Fields built from message codes: the state words (`INSUFFICIENT_DATA`, “Still learning”), the baseline and collection notes.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-32 — English: WAF behavior `/behavior/waf` and `/behavior/waf/sources/<id>`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “WAF behavior” on the list, and the log source's name on `/behavior/waf/sources/<id>` (its browser tab reads “WAF source endpoints · Behavior Analytics”).
- Fields built from message codes: the collection state words; the pause and eligibility reasons (MSG-4a).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-33 — English: Property watches `/behavior/watches`, `/behavior/watches/new`, `/behavior/watches/<id>`

**When to look:** For new rows, the wait in section 2.

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Property watches” on the list, “New property watch” on the form, and the watch's name on `/behavior/watches/<id>` (its browser tab reads “Property watch · Behavior Analytics”).
- Fields built from message codes: the “Status and reason” column (MSG-4b, rewritten at every collection); the scope reasons; the history-gap rows; the validation reasons in the editor (MSG-1b/4b).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-34 — English: Fleet `/fleet`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Log-source fleet”; the browser tab reads “Fleet · Behavior Analytics”.
- Fields built from message codes: the class table and the review queue; there is no stored sentence.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-35 — English: Fleet sources `/fleet/sources`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Fleet sources”.
- Fields built from message codes: the classification reasons (shown as the rule name); the conflict messages (MSG-1b) only in an error state.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-36 — English: Catch-all senders `/fleet/catchall`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Catch-all senders”.
- Fields built from message codes: the sender table and the review state (“reviewed”).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-37 — English: Offenses `/offenses` and `/offenses/<id>`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Offenses” on the list and “Offense {id}” (the QRadar offense id, for example “Offense 1234”) on the detail; the detail's browser tab reads “Offense · Behavior Analytics”.
- Fields built from message codes: the labels and the states. The offense description is the text from QRadar and is shown as it is.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-38 — English: Alerts `/alerts` and `/alerts/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Alerts” on the list. On the detail the `h1` is the alert's own title (a sentence; the fixed words “Alert Detail” appear only as the `h1` of the error state), and the browser tab reads “Alert detail · Behavior Analytics”.
- Fields built from message codes: the MSG-7 catalog alerts: search threshold and search failure, custom rule threshold, offense collection freshness, new log source, new catch-all sender. The title, the “Description” and the “Resolution:” line are English sentences. Offense collection freshness is the example: the title is “Offense collection is stale for {instance}”, the description is “The read-only offense collector has not completed within its configured freshness threshold.”, and the resolution reason is “Offense collection freshness recovered.”
- The alerts the engine writes (log source volume, WAF, IPS, property watch) show a title, a description and a resolution reason as English sentences. The resolution reason of a recovered condition is “condition recovered”. Same limits as the Alerts section (MT-87, MT-88).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-39 — English: Rule health `/rules` and `/rules/<id>` [core]

**When to look:** For new rows, the wait in section 2.

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Rule health” on the list. On the detail the `h1` is the rule's name, and the browser tab reads “Rule · Behavior Analytics”.
- Fields built from message codes: the health reason under the rule name and the reason sentence in the detail (MSG-3). The detail heading is “Health evidence”. “Reason” is a cell of the health history table. It is rewritten at every evaluation.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-40 — English: Rule statistics `/rules/statistics`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Rule statistics”.
- Fields built from message codes: the measurement window sentence, the exclusion reason of silent rules, and the cost label with its unit (MSG-2); computed, not stored.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-41 — English: Custom rules `/custom-rules`, `/custom-rules/new`, `/custom-rules/<id>`

**When to look:** For new rows, the wait in section 2.

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Custom rules” on the list, “New custom rule” on the form, and the rule's name on the detail (its browser tab reads “Custom rule · Behavior Analytics”).
- Fields built from message codes: the status reason in the “Status and reason” column (MSG-7, rewritten at every evaluation); the save errors (MSG-1b).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-42 — English: Detection coverage `/coverage`

**When to look:** For new rows, the wait in section 2.

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Detection Coverage”. This route sets no tab title of its own: the browser tab reads only “Behavior Analytics”.
- Fields built from message codes: the summary sentences in the “Evidence” column (MSG-2); rewritten at the coverage sync.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-43 — English: Log sources `/log-sources` and `/log-sources/<id>`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Log Sources” on the list and the log source's name on the detail. These routes set no tab title of their own: the browser tab reads only “Behavior Analytics”.
- Fields built from message codes: the state words; there is no stored sentence.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-44 — English: Searches `/searches`, `/searches/new`, `/searches/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Search Catalog” on the list, “New search” on the form, and the search's name on the detail (its browser tab reads “Search · Behavior Analytics”).
- Fields built from message codes: the “Error” sentences in the run history (MSG-8b); the save errors (MSG-1b).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-45 — English: Configuration changes `/config-changes`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Configuration changes”. This route sets no tab title of its own: the browser tab reads only “Behavior Analytics”.
- Fields built from message codes: the state “Configuration changes are not collected.” (this is a "not collected" honesty state).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-46 — English: Administration `/admin`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Administration”.
- Fields built from message codes: the page description.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-47 — English: Search cadence `/admin/search-cadence`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Search cadence”.
- Fields built from message codes: the table, the family names, the delay sentences.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-48 — English: Classification rules `/admin/classification-rules`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Classification rules”.
- Fields built from message codes: the rule list; the built-in rule conflict messages (MSG-1b) only in an error state.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-49 — English: Outage alerts `/admin/outage-policy`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Outage alerts”.
- Fields built from message codes: the policy table and the note.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-50 — English: WAF sources `/admin/waf-sources` and `/admin/waf-sources/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is “WAF sources” on the list and the log source's name on the profile (its browser tab reads “WAF source · Behavior Analytics”).
- Fields built from message codes: the reasons of the validation report, the field details and the eligibility (pause) reasons (MSG-4a); the profile conflicts (MSG-1b).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-51 — English: IPS sources `/admin/ips-sources` and `/admin/ips-sources/<id>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is “IPS sources” on the list and the log source's name on the profile (its browser tab reads “IPS source · Behavior Analytics”).
- Fields built from message codes: the reasons of the validation report and the eligibility reasons (MSG-4a).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-52 — English: History backfill `/admin/history-backfill` [core]

**When to look:** For new rows, the wait in section 2.

**Action:**

- Open the route.

**Expected:**

- The `h1` is “History backfill”.
- Fields built from message codes: the scope reasons (all families, catch-all included), the gap reasons, the estimate sentences, the assumptions and the decision messages are English sentences (MSG-8a). The scope rewrites its reason at every step. The Ariel deferral reason that the writer rewrites is the sentence `deferred: no Ariel permit within N s` (MSG-8c).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-53 — English: Users `/admin/users`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Users”.
- Fields built from message codes: the table and action labels; the account action errors (MSG-1).
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-54 — English: Directory `/admin/directory`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Directory”.
- Fields built from message codes: the form labels; the validation errors (MSG-1) only when a save is tried.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-55 — English: Guide `/guide` and `/guide/<section>` [core]

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Guide” on the list; on a section page the `h1` and the browser tab are the section's title.
- Fields built from message codes: the section titles and texts; section 13.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-56 — English: Account `/account` and `/account/password`

**Action:**

- Open the route.

**Expected:**

- The `h1` is “Account settings” on `/account` and “Change password” on `/account/password`.
- Fields built from message codes: the password policy errors (MSG-1): “password must be at least 12 characters”.
- No raw key and no Turkish text.

**Record:** A raw key or a Turkish sentence (page, text); “none” if there is none.

#### MT-57 — Failed sign-in sentence (writes)

**Precondition:** Signed out.

**Action:**

- On the `/login` page try to sign in with a user name that **does not exist** (for example `mt-no-such-user`) and any password. Do not use a real user name: the failure count locks the account (5 failures, 15 minutes).

**Expected:**

- The page shows “Invalid username or password”. If the directory cannot be reached it is “The directory server is unavailable. Sign-in was not completed.”

**Record:** Whether the sentence is English and complete.

**Cleanup:** Not needed; an `auth.login_failed` audit record is written.

#### MT-58 — Removed in 0.1.10 (D-41: the interface is English only)

The check was that the stored sentences written by 0.1.6 stay English in the Turkish interface while newer rows are Turkish. There is no Turkish rendering any more, so there is no mixed view to check. Nothing to run; record `Removed`.

#### MT-59 — Removed in 0.1.10 (D-41: the interface is English only)

The check was the list of texts that stay English in the Turkish interface (stored legacy rows, notification texts, texts from QRadar and operators, glossary terms). With one language the list has no subject. The fact that texts from QRadar, from providers and from the operator are shown as written is stated at the top of this section. Nothing to run; record `Removed`.

### 5. Dashboard

The Dashboard is at `/`. In the “Dashboards” menu the “Template” group holds only “SOC Overview (template)”. The label on the page is “Template, not saved”; the sub-label in the menu is “Not saved”. “QRadar Administration” and “WAF / IPS Monitoring” are not in this menu. An administrator opens them with “Manage” → “New dashboard from template…”; this saves the dashboard and needs `dashboard:write`. A viewer does not have this button. MT-71…79 are done on these saved dashboards; the rows call the dashboard a “template”, while in the menu it is among the saved dashboards. When the work is finished, delete it from the same “Manage” menu with “Delete…”; the delete is permanent. Each widget reads its own data; if one fails, only that widget shows an error and the others work. Each widget's link goes to the route that owns its data, and the link stays on the widget in every state (loaded, empty, stale, forbidden, error).

**States seen on a healthy site.** Loaded; empty (for example no open offense); “Not collected.” (if collection is off for a family or has never run); “Stale” (“Stale: the latest data is from ‹time›, {age} old.”; the time is `14:02:11 UTC` when it is on the current UTC day and `2026-11-03 14:02:11 UTC` when it is on another day, and the age keeps two units, for example “1 d old” or “3 h 20 min old”; the same holds for the “Data to ‹time›, {age}.” line; this can be seen right after the upgrade before the first cadence has passed and is correct; if it stays after cadence + 3 min it is a fault); forbidden (a viewer, in a window 12 columns wide, reads the “Outage policy” row of the “Customize” → “Add widget” catalog: “Your role lacks permission admin:outage.”; the “Add” button is disabled. In a narrow window it says “Widen the window to customize” instead of “Customize”. The check "The viewer role's limit" in the Sign-in section). Deferred (a box that is more than about two screens down is not read until it scrolls near; until then it says “‹Title› is read when this box scrolls into view.”, for example “Geography is read when this box scrolls into view.”, and not “Loading …”; scroll to it and it shows its data or its own error). The widgets “Recent anomalies”, “Recent WAF anomalies” and “Recent IPS anomalies” list every active anomaly, whatever its age (the active list has no lower bound), not only those of the last 24 hours. **A state that cannot be seen on a healthy site:** "could not be read" (“The {subject} could not be read.”) appears only if the backend or an endpoint fails; it cannot be produced without changing the configuration, so this section skips it. For a family whose collection switch was left off, the widget shows "Not collected."; this is not seen for families whose switches are on. **For every widget in this section** at least one cadence + 3 min must have passed after the upgrade (MT-11); if you look earlier, "stale" is correct. On a changed console, until the hourly inventory arrives, the source widgets show no sources; this is not a Fail (MT-11).

#### MT-60 — Dashboard widget: “Total EPS”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The title is “Total EPS, {window}”; the big number is in the EPS unit; under it “expected {eps} EPS” or “expected — (no reliable baseline)”; the line “baselines {have} of {total} sources”. A missing measurement is not drawn as zero.
- The link opens the `/fleet` route (link text “Fleet”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-61 — Dashboard widget: “Log sources”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The title is “Log sources, now”; “of {total} monitored”; “{normal} of {judged} judged normal” or “— judged normal (no source is judged yet)”; “Silent: the detector saw no events where some were expected (NO_EVENTS anomaly).”
- The link opens the `/behavior/sources` route (link text “Sources”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-62 — Dashboard widget: “Open offenses”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The title is “Open offenses, latest snapshot”; the breakdown is critical, below, unassigned and over SLA; if there are offenses whose magnitude or age is unknown, “+ {count} with unknown magnitude” / “+ {count} with unknown age”. If the snapshot has never been collected: “No offense snapshot has been collected yet.”
- The link opens the `/offenses?status=OPEN` route (link text “Offenses”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-63 — Dashboard widget: “Active anomalies”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The title is “Active anomalies, now”; the figure is the unit “active anomalies”, and it counts every family (log sources, WAF endpoints, IPS, property watches and rule matches). The lines are “Log sources” (“{n} spikes”, “{n} drops”, “{n} silent”, “{n} other”), “Also” (“{n} WAF”, “{n} IPS”, “{n} property watches”, “{n} rule matches”) and “Lifecycle” (“{n} open”, “{n} candidates”, “{n} recovering”); each count carries its noun, in the singular for 1 (“1 spike”, “1 candidate”). The “Log sources” parts add up to the log sources' part of the total, the “Also” parts to the rest, and the “Lifecycle” parts to the whole figure. When the detector split is not available, “Log sources” shows one part (“{n} log sources”). A family the page could not read shows “WAF not read” (or “IPS not read”, “property watches not read”, “rule matches not read”) in its place, never a zero, and the figure reads “≥ N” with the note “At least this many: a family could not be read.”; if no family could be read, the figure is a dash with “not read”. Under them the note “{n} log sources not judged; they are not in these counts.” appears (“1 log source not judged; it is not in these counts.” for one).
- Compare the figure with the number of rows the link opens (`/anomalies?active_only=true`): they are equal on a healthy site, since both count the active anomalies of all five families, whatever their age. A difference is written in the Note field.
- The link opens the `/anomalies?active_only=true` route (link text “Anomalies”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-64 — Dashboard widget: “Fleet EPS”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The axis carries the unit (“Events per second (EPS)”; the time axis says “Time (UTC)”); the expected sum is a dashed line that is drawn only where every source has a reliable baseline (it is not a range); a step that was not measured is a gap, not a zero. While loading, a text of the form “Loading {what}…” is shown.
- The expanded view of this widget (not the tile) has the “EMA” buttons “3”, “5”, “10” and “20”, all off at first; MT-168 checks them. The tile draws no average whatever is stored.
- The link opens the `/fleet` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-65 — Dashboard widget: “EPS by source class”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The class names are in the form “Firewall”, “Windows”; for classes without a complete bucket the reason is “No class has a complete bucket in this window.” (or the count line “# class has no complete bucket and is not in the total.”).
- The link opens the `/fleet` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-66 — Dashboard widget: “Top log sources by EPS”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. Source rows ordered by EPS; the numbers are right-aligned, with English grouping (`2,016`).
- The link opens the `/behavior/sources` route (link text “View all”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-67 — Dashboard widget: “Anomalies by severity”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. Counts by severity: “Critical”, “High”, “Medium”, “Low”, “Info”.
- The link opens the `/anomalies` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-68 — Dashboard widget: “Log source health”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. Source health counts; “Insufficient data” is counted separately and is not added to "normal".
- The link opens the `/behavior/sources` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-69 — Dashboard widget: “Recent anomalies”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The latest active anomaly rows (every active anomaly, whatever its age, not only those of the last 24 hours); the time is written with the `UTC` zone. If there are rows left over from 0.1.6, they are listed too. “No active anomalies” is shown only when the “Active anomalies, now” card (MT-63) counts none.
- The link opens the `/anomalies` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-70 — Dashboard widget: “Recent offenses”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The latest offense rows; the description is as it came from QRadar.
- The link opens the `/offenses` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-71 — Dashboard widget: “Scheduled searches”

**Action:**

- In the “QRadar Administration” template find the widget, read its data, press its link.

**Expected:**

- The data loads. For each family the cadence, the last run and how late it is (“Family”, “Cadence”, “Last run”, “Late by”); a family that has not run since the upgrade shows an unmeasured mark whose explanation is “This family has not run since the upgrade”, and the line above the table says “some have not run since the upgrade”; neither is a zero. This widget is the one screen for "scheduled searches".
- The link opens the `/admin/search-cadence` route (link text “Cadence”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-72 — Dashboard widget: “Detection health”

**Action:**

- In the “QRadar Administration” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The rule and coverage summary (“{covered} of {total} techniques covered”); if coverage was not evaluated, “Coverage has not been evaluated.” This is not a finding of zero coverage.
- The link opens the `/coverage` route (link text “Coverage”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-73 — Dashboard widget: “Evidence collection”

**Action:**

- In the “QRadar Administration” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The evidence collection status counts (“Pending”, “Failed”); the failed count feeds the filter that the link opens.
- The link opens the `/anomalies?evidence_status=FAILED` route (link text “Failed”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-74 — Dashboard widget: “Catch-all senders to review”

**Action:**

- In the “QRadar Administration” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The senders to review (“Sender”, “First seen”, “Events, 24 h”); if there are none, the empty-state text “No unreviewed senders. Every sender seen feeding the catch-all sources has been reviewed or dismissed.”
- The link opens the `/fleet/catchall` route (link text “Review”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-75 — Dashboard widget: “Source classes”

**Action:**

- In the “QRadar Administration” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The source class table (“Class”, “Sources”, “Mean EPS”, “Silent”); the class names are in English.
- The link opens the `/fleet` route (link text “Fleet”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-76 — Dashboard widget: “Outage policy”

**Action:**

- In the “QRadar Administration” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The outage alert policy per class (“Alert”, “Record only”). It loads on an administrator account; on a `viewer` account the forbidden state is shown (the check in the Sign-in section).
- The link opens the `/admin/outage-policy` route (link text “Policy”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-77 — Dashboard widget: “WAF sources”

**Action:**

- In the “WAF / IPS Monitoring” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The WAF sources (“Source”, “Profile”, “Coverage”, “Endpoints”); the latest bucket's coverage and the endpoint count against the ceiling; a source that is not collected shows “Disabled” or “No bucket has been collected”. If the site has no WAF: “No WAF source is registered. A source appears here once its WAF profile exists.”
- The link opens the `/behavior/waf` route (link text “WAF”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-78 — Dashboard widget: “Recent WAF anomalies”

**Action:**

- In the “WAF / IPS Monitoring” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The active WAF anomalies; if there are none, the empty text “No active WAF endpoint anomalies. WAF sources without a valid profile are not collected, so they are not judged.” If there is no anomaly, do not mark `Not run`: the empty state is also a valid result.
- The link opens the `/anomalies?subject_kind=WAF_ENDPOINT&active_only=true` route. Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-79 — Dashboard widget: “Recent IPS anomalies”

**Action:**

- In the “WAF / IPS Monitoring” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The active IPS anomalies; the link opens IPS entities and sources together (the “IPS entities and sources” option that came with 0.1.6). The sentences on the rows are English sentences from the catalog (MSG-6b).
- The link opens the `/anomalies?subject_kind=IPS&active_only=true` route (link text “View all”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-161 — Dashboard widget: “Latest handover” (SOC-14b)

**Precondition:** Signed in with `read:anomalies`. The widget is in the “SOC Overview” template (`frontend/src/widgets/templates.ts`, id `w_handover`, a row of its own near the bottom). A dashboard you saved before the upgrade keeps its own widgets; if the widget is not on it, add it from the widget catalog (category “Table”, title “Latest handover”) and write that in the Note field.

**Action:**

- In the “SOC Overview” template find the widget, read it, press its link (“Open handover”).

**Expected:**

- The data loads (`GET /api/v1/handover/latest`). With no handover written the widget says “No handover yet.” With one, it shows who wrote it and when (the time with its UTC zone), the first 240 characters of the note (an ellipsis follows a longer note) and “‹n› watched” or “Nothing watched”. If a source was not read cleanly when the note was written, “Not read cleanly: ‹sources›” appears in the warning tone, never a smaller count (`frontend/messages/en/handover.json`, `widget`; `frontend/src/components/catalog/HandoverWidget.tsx`).
- The link opens `/handover` in every state. A role without `read:anomalies` reads the widget's forbidden sentence naming that permission. While loading, “Loading the latest handover…”.
- Nothing on the widget writes anything.

**Record:** Whether the data loaded; the state seen (empty, a record, incomplete); whether the link opened `/handover`.

#### MT-128 — World map: dashboard widget and `/geography` [core]

**Precondition:** Upgrade complete (MT-08), the country property is set (MT-129), and at least one `GEO_COLLECTION` cadence + 3 min has passed (default 5 min; total 8 min). Signed in with an account that has `read:geo`.

**Action:**

- On the `/` page, find the Geography (`map.geography`) widget in the bottom row of the "SOC Overview" template. The box is far down the page, so before you scroll to it, it says “Geography is read when this box scrolls into view.” (it is not loading yet); after you scroll, it shows the map, “Loading the country counts…” while the read is slow, or its own error with the HTTP status. Change the direction between Source and Destination, and the window between 1 hour, 24 hours and 7 days.
- Click the widget's link and open `/geography`. The same direction and window appear in the URL (`?window=24h&direction=SOURCE`, etc.). Read the map, legend and country table.

**Expected:**

- The map is drawn; the colours use the sequential `--map-0…--map-4` scale, at most four classes on a log axis, with a unit legend ("Events per country"). There is no green/amber/red status tone.
- A country with no events uses `--map-0` as "No data", never the lowest class. A country with events shows "N events"; a lower-bound legend says "At least N events".
- If nothing was collected for the direction in the range, "Not collected." appears and the map is not drawn; the coverage line “Buckets measured 0 of N” is on `/geography` only (N is the number of settled 5-minute buckets the range should hold, per enabled console: at most 12 for the 1-hour window); the widget's compact view keeps the state's one-sentence reason and drops the coverage, remainder and provenance lines under the map (UIQ-1; `frontend/src/components/geo/GeographyBody.tsx`, `density`). With the default settings Destination reads "Not collected." once the buckets that an earlier release stored have expired, because `GEO_DESTINATION_COUNTRY_PROPERTY` is empty (MT-129). If buckets were collected but no country could be assigned, "Insufficient data." appears and again no map is drawn. A partial read draws the map with "Partial"; a lower-bound legend says "At least". None draws a zero.
- The page (not the widget's compact view) names where the country came from ("From the QRadar property “<name>”" or "From the GeoIP lookup of the address").
- If the newest bucket is older than the `GEO_COLLECTION` cadence allows, the widget keeps the map and adds a warning line ("Stale: the latest data is from <time> UTC, <age> old.", with the date before the time when it is on another UTC day, and an age of two units such as “1 d old”); the page shows the same age line and the top bar shows "Data to …". A fresh answer shows "Data to <time> UTC" on the page and in the widget's expanded view; the compact widget says nothing about a fresh answer (UIQ-1).
- The widget's link always opens `/geography`. For a role without `read:geo`, the widget shows its forbidden sentence naming the missing permission.
- Times show the UTC zone. A country too small for the 1:110m outlines (Singapore, Malta, etc.) is named under the map and kept in the table.

**Record:** Did the map draw; does the legend carry a unit; is `--map-0` "No data"; which honesty state appeared for each direction; did the link open `/geography`; are direction and window in the URL; was the answer stale.

#### MT-129 — Country source and the `geo_country_source` check (GEO-3)

**Precondition:** The exact name of the country custom property from the QRadar administrator. Property names are not secrets and may be written on the sheet.

**Action:**

- Read the three settings (an absent line means the default: `property`, `Source Geographic Location`, empty):

```bash
grep -E '^GEO_(COUNTRY_SOURCE|SOURCE_COUNTRY_PROPERTY|DESTINATION_COUNTRY_PROPERTY)=' .env
```

- Run `./qradar-dash doctor` and read the `geo_country_source` line. After a change in `.env`, recreate the backend, worker and beat containers first.

**Expected:**

- `GEO_COUNTRY_SOURCE=lookup`: `PASS geo_country_source lookup; SOURCE and DESTINATION use GEO::LOOKUP`.
- `property` and the named property exists on every enabled console: `PASS`, for example `property; SOURCE "Source Geographic Location" present on <console>; DESTINATION not collected (no property)`. An empty name for a direction is not a problem; that direction is not collected and sends no search.
- The property is not on the console: `WARN` with `absent on <console>`, runbook OPS-R30. The collector sends no search for that direction and records "The country property “<name>” does not exist on this console. Set GEO_SOURCE_COUNTRY_PROPERTY to the property's exact name."; `watermarks` then FAILs. Set the exact name and re-run.
- The console's property list could not be read, or no console is enabled: `FAIL`, runbook OPS-R30. Both names empty: `WARN`. Geography collection off: `SKIP`.
- With the property set, the Source map on `/geography` reads "From the QRadar property “<name>”" once a cadence has run (MT-128). A value that is not a country the platform recognises is counted under Unparseable, never as a country; note its size if it is large.

**Record:** The three settings, the `geo_country_source` status and text (property names only), and the Source map's provenance line.



### 6. Anomalies

The anomaly list gathers five families in one place: log source volume, WAF endpoint, IPS (entity and source), property watch value and rule match (SOC-6). The detail page (`/anomalies/<id>`) opens a different investigation depending on the family. Lifecycle states: `CANDIDATE`, `OPEN`, `RECOVERING`, `RESOLVED`, `SUPPRESSED`; `INSUFFICIENT_DATA` and `NORMAL` are source states. If the site has no anomaly of a kind, the check for that kind is `Not run` and "no data" is written in the Note field; do not try to produce a new anomaly.

#### MT-80 — Anomaly list and filters

**Action:**

- Open the `/anomalies` page. Try the filters one after another: “Subject kind”, “Lifecycle state”, “Active only” (“Filter”).

**Expected:**

- The `h1` is “Anomalies”. The columns are “Subject”, “Detector”, “State”, “Observed → Expected”, “Deviation”, “Severity”, “Started”, “Age”, “Evidence”, “Triage”, “Owner” (`frontend/src/app/anomalies/page.tsx:278-311`); the three new ones are checked in MT-147. On a narrow window the Triage and Owner columns fold into the row as “Triage: ‹status›, ‹owner›” and “Started ‹age› ago”.
- The subject kind options are “Log sources”, “WAF endpoints”, “IPS entities”, “IPS sources”, “IPS entities and sources”, “Property watch values”, “Rule”. The “IPS entities and sources” option lists IPS entities and IPS sources together. A rule-match incident exists only when `RULE_MATCH_COLLECTION_ENABLED` is on (MT-170).
- After filtering, the active filter chips (“Filtered by”) appear; if nothing matches: “No anomalies match these filters. This is a filtered view, not a statement about the fleet.” With no filter and no anomaly in the window the page asked for, the sentence is “No anomalies were detected in the window this page asked for: the last 24 h, ‹from› to ‹to›. Set the Start time (UTC) filter to look further back.” (both times are UTC), followed, when active anomalies exist before that window, by “{n} active anomalies were detected before this window.” (“1 active anomaly was …” for one) with the link beside it (the English catalog labels it “Show every active anomaly”), and then by “Sources without an adequate baseline are not being judged — check the behavioral overview for how many.” If the count of older anomalies could not be read, the sentence has no number (never a zero). The number counts **every** active anomaly older than the window the page asked for, in all four families and whatever its age, not only those of the last year; follow the link: the active list opens with the “Active only” chip and no start time, its pagination total is that number, and paging to the end reaches the site's oldest active anomaly even when it is more than a year old. “Active only” without a start time lists every active anomaly, whatever its age, not only those of the last 24 hours. Times are UTC; the state words are the codes (`OPEN`, …).

**Record:** Whether the five filters and the result page worked; the number of rows in the list (approximately).

#### MT-81 — Log source volume anomaly detail and the honesty states

**Precondition:** The site has at least one log source volume anomaly (active or resolved).

**Action:**

- Open a log source anomaly from the list. Open the tabs in turn: “Evidence”, “Lifecycle”, “Technical details”.

**Expected:**

- The `h1` is the name of the log source (the fixed words “Anomaly investigation” are the browser tab, “Anomaly investigation · Behavior Analytics”); “What changed” and “Incident measurements”: “Observed”, “Expected”, “Deviation”, “Duration”; the timeline “Timeline”.
- Depending on the evidence status, one of these banners appears (only in the matching case): “Contributor evidence is still being collected. The analysis below is not yet available: this is a backlog, not a finding.” / “Some QRadar fields were unavailable or truncated. The available evidence is shown below; the dimensions that were not collected have not been checked, and no conclusion follows from the absence of contributors there.” / “This log source's DSM exposes none of the requested fields for this interval, so no contributor analysis was possible. This is a property of the source, not a transient error.” / “Contributor evidence collection failed. Detection metrics and lifecycle data remain available, and this says nothing about what happened during the anomaly — it is worth retrying.” / “No evidence collection has been requested for this anomaly. Nothing has been looked at.”; on a truncated result a text that begins with “Truncated.”. These are the honesty states "insufficient", "partial", "truncated" and "not collected"; none is drawn as a zero.
- If the evidence failed, “Collection error.” and the provider's error text appear; this text is free text from the provider and is shown as it came (see the top of section 4).

**Record:** The anomaly's state, which evidence status banner appeared (if any), and whether the three tabs opened. If the site has no anomaly for this check, `Not run`.

#### MT-82 — Lifecycle tab and reasons

**Precondition:** An anomaly of log source volume or of another family is open.

**When to look:** A new transition is seen only with an anomaly whose state changed after the upgrade; there is no need to wait.

**Action:**

- In the detail open the “Lifecycle” tab.

**Expected:**

- Each transition row has the previous and the next state, the time (UTC) and the reason. The first transition is written as “initial”. A transition whose reason was not recorded shows “No reason recorded”.
- The reason of a transition is an English sentence from the catalog (MSG-5: all four families). If there is no new transition for this check, only verify that the older row reads as English text, and write "no new transition" in the Note field.

**Record:** Whether the tab opened; whether the reason text is present; the write time of the newest transition.

#### MT-83 — WAF anomaly detail

**Precondition:** The site has a WAF anomaly.

**Action:**

- Open a WAF endpoint anomaly from the list. Read the detector block and the “Lifecycle” section.

**Expected:**

- The block has the lines “Seasonal p05–p95 at detection” or “Expected range at detection”, “Materiality floor” (“material at ≥ {count} requests”), “Verified history” and “Consecutive abnormal buckets”. A value that was not measured is shown as a dash with its reason (for example “not used: MAD was zero”); a zero is not drawn.
- The reasoning sentence and, if there is one, the alert note (for example the block ratio or the new endpoint) are English sentences (MSG-6).
- A suppressed anomaly carries the labels “SUPPRESSED” and “Suppressed”. If the reason is stored, a line “Suppression reason: …” appears under the identity row. The coded maintenance reason is “maintenance window”. A row with no code shows its stored text. An anomaly that was never suppressed has no such line; its absence is not a fault.
- The source link is “View WAF source”.

**Record:** One reasoning sentence (that it is English and complete) and the anomaly's open time.

#### MT-84 — IPS anomaly detail

**Precondition:** The site has an IPS anomaly (from a Cisco Firepower/FTD or Palo Alto Threat source).

**Action:**

- Open an IPS entity or IPS source anomaly from the list. Read the detector block and the sections “Timeline”, “What was judged”, “Evidence” and “Lifecycle”.

**Expected:**

- The block has the lines “Seasonal p05–p95 at detection”, “Robust z threshold”, “Materiality floor” and “Verified history”. A value that was not measured carries a dash and its reason. The evidence section says “An IPS incident has no evidence package and no list of contributing values. The series above and what was judged are what the detector recorded; source addresses are not served for IPS.”: no source address is shown for IPS.
- The reasoning sentence is an English sentence from the catalog (MSG-6b): an entity spike (“{observed} {unit} for {subject} is above the baseline median {expected} ({ratio_text}, +{delta} {unit}/bucket); {basis}”), a block ratio shift, a concentration, a new destination port (“destination port {port} carried {events} events across {buckets} buckets since it first appeared at {first_seen}; in the verified history {history_start} to {history_end} it stayed below {admission} events in every bucket”; three UTC-stamped parts). The alert note is a fixed sentence, for example “Blocking is judged through the profile's action map. A higher block ratio describes the IPS's decisions; it does not establish that an attack succeeded.” When a count is a lower bound the sentence has the form “at least {observed} {unit} (a lower bound: {scope}) for {subject} is above the baseline median {expected} ({ratio_text}, +{delta} {unit}/bucket); {basis}”. Numbers of a thousand or more are grouped with commas (`12,345`) and ratios have the form `60.0 %`.
- An inner clause after a colon or a dash starts with a lower-case letter (for example "a lower bound: …", "…, {role}: {reason}"); an inner clause that starts with a capital is a fault.
- A suppressed IPS anomaly carries the labels “SUPPRESSED” and “Suppressed”. If the reason is stored, a line “Suppression reason: …” appears under the identity row. The coded maintenance reason is “maintenance window”. A row with no code shows its stored text. An anomaly that was never suppressed has no such line; its absence is not a fault.
- The lifecycle reasons are English sentences (MSG-5).

**Record:** One reasoning sentence (that it is English and complete) and the anomaly's open time. If there is no IPS anomaly, `Not run`.

#### MT-85 — Property watch anomaly detail

**Precondition:** The site has a property watch anomaly.

**Action:**

- Open a “New value” or a “Value spike” anomaly from the list.

**Expected:**

- The explanation and the reasoning sentences are English sentences from the catalog (MSG-4b), and so are the lifecycle reasons (MSG-5). A new value anomaly has the "claim" and the history verification sentences (with ISO time stamps).

**Record:** One sentence (that it is English and complete) and the anomaly's open time. If there is no property watch anomaly, `Not run`.

#### MT-86 — Honesty states are visible and are not turned into zero

**Action:**

- Look at these places: (1) a source with no adequate baseline in `/behavior/sources`; (2) a source that is not collected in `/behavior/waf` or `/admin/waf-sources`; (3) `/config-changes`; (4) `/offenses`, if the snapshot has never been collected.

**Expected:**

- (1) The source shows the state `INSUFFICIENT_DATA` and the words “Still learning”; in the counts it does not go into "normal" (“Not being judged — a zero above does not cover these.”).
- (2) The source that is not collected shows “Not collected” or “No bucket has been collected”; it does not write zero events.
- (3) “Configuration changes are not collected.” and “A data model for them exists, but no collector or API fills it, so this page has nothing to show.”: no zero and no empty list is drawn for data that is not collected.
- (4) “No offense snapshot has been collected yet.” Partial and truncated measurements are also labelled “Partial” and “Truncated”.

**Record:** What you saw for (1)-(4); `Not run` for the one you could not see.

#### MT-146 — Triage block on the anomaly detail (writes) lab only

**Precondition:** **lab only**: the check changes the triage of a real anomaly and the interface cannot return it to “New”. Signed in with an account that holds `anomaly:triage` (the administrator does, through `admin:*`; MT-145). The site has an anomaly of any of the four families (log source, WAF, IPS, property watch). If none exists, `Not run`. Note the anomaly's `State` on the list before you start.

**Action:**

- Open the anomaly (`/anomalies/<id>`) and find the block headed “Triage”. Read it. Press “Take it”.
- Write a comment in “Add a comment (up to 2000 characters)” and press “Add comment”.
- Press “Close with a disposition”. Press “Close triage” with no disposition chosen. Choose “Noise”, write the note “MT-146” and press “Close triage”.
- Press “Reopen”.
- On an anomaly of the “Custom rule breach” type (if the site has one), read the block.

**Expected:**

- The block explains itself: “The analyst's decision on this anomaly. It is separate from the detector's state, which triage never changes.” An anomaly nobody has touched reads “Triage status” “New”, “Owner” “Unassigned” and “Nobody has acted on this anomaly yet.” (`frontend/messages/en/triage.json`, `panel`).
- “Take it” sets “Triage status” to “In progress” and “Owner” to the signed-in account; “Updated by ‹account›, ‹time›” is in UTC; the activity list (“Activity”, newest first) holds “Owner set to ‹account›” and “Status changed from New to In progress”. The comment gives “Comment added.” and a “Comment” row.
- The close dialog (“Close this triage”) offers “True positive”, “Benign true positive”, “Noise”, “Collection problem” and “Known change” under “Disposition (required)”, and not “Auto-closed: stale” (which only the system sets, MT-148). Pressing “Close triage” with nothing chosen shows “Choose a disposition before closing.” and saves nothing. After the close the block reads “Closed”, “Disposition” “Noise” and “Closing note” “MT-146”, and the button is “Reopen”. Reopen gives “In progress” and the closed history stays in the activity list.
- The detector's `State` of the anomaly (MT-81 to MT-85) is the one noted before; an alert the anomaly has is not changed. Two labels, two things.
- A custom-rule breach says “Triage is not available for this kind of anomaly. It covers log-source, WAF, IPS and property-watch anomalies.”
- If another account changed the same triage between your read and your save, the block names who did, turns the controls off and offers “Load their version” (HTTP 409). This is not forced here; if it appears, write it in the Note field.

**Record:** The anomaly's family, the status shown after each step and the sentence of the empty close.

**Cleanup:** The interface cannot return a triage to “New”. End the check with the anomaly closed as “Noise” with the note “MT-146”: the neutral end state. In the lab the rollback rehearsal (MT-123) removes the row.

#### MT-147 — Anomaly list: Triage, Owner and Age, and the filters

**Precondition:** The upgrade is complete (MT-08); the site has at least one anomaly in the list. Sign in with an account that holds `read:anomalies`.

**Action:**

- Open `/anomalies`. Read the columns and a row. Read the triage filters (some sit under “More filters”).
- Choose “Triage status” “In progress” and press “Filter”; then “Owner” “Owned by me”; then “Unassigned”; then a “Disposition”. Read the chips (“Filtered by”). Remove one chip and use “Clear all”.
- Send an invalid status:

```bash
curl -s -o /dev/null -w '%{http_code}\n' --cookie "$SESSION" "https://<host>/api/v1/anomalies?triage_status=BOGUS"
```

**Expected:**

- The columns include “Age”, “Triage” and “Owner” after “Started” and “Evidence” (`frontend/src/app/anomalies/page.tsx:278-311`). A row with no triage record reads “New” and the owner “Unassigned”, never a blank cell. A row whose triage was not reported reads “not reported”. On a narrow window the same facts fold into “Triage: New, Unassigned” and “Started ‹age› ago” (`frontend/messages/en/anomalies.json`, `rows`).
- “Age” counts whole minutes from the start (for example “5 d 14 h ago”) beside the UTC start time and does not tick until the page is loaded again.
- The filters are “Triage status” (“All triage statuses”, “New”, “In progress”, “Closed”), “Owner” (“Any owner”, “Owned by me”, “Unassigned”, “Named owner” with “Owner name”) and “Disposition” (“Any disposition” and the dispositions of MT-146, plus “Auto-closed: stale”). Each choice becomes a chip (“Triage: In progress”, “Owner: me”, “Owner: unassigned”, “Disposition: ‹name›”) and stays in the address (`triage_status`, `owner`, `disposition`). A filter that matches nothing says “No anomalies match these filters. This is a filtered view, not a statement about the fleet.”
- The invalid status answers `422` with “Input should be 'NEW', 'IN_PROGRESS' or 'CLOSED'” in the body (REL-15e).

**Record:** The columns seen, one row's Triage and Owner cells, and which filters gave a result.

#### MT-148 — A NEW anomaly nobody took is closed as stale (SOC-1c)

**Precondition:** The beat of the new stack has run for more than one hour (the job runs hourly, first one hour after beat starts). The site had, at the upgrade, anomalies detected at least `TRIAGE_STALE_CLOSE_DAYS` (3) days earlier that nobody triaged. If it had none, `Not run`. A site that set `TRIAGE_STALE_CLOSE_DAYS=0` turned the job off: write it and `Not run`.

**Action:**

```bash
docker compose exec -T backend python -c "from app.workers.celery_app import celery_app; print(celery_app.conf.beat_schedule['close-stale-triage'])"
./qradar-dash logs --since=70m celery-worker | grep close_stale_triage
grep -E '^TRIAGE_STALE_CLOSE_DAYS=' .env || echo "not set: the default (3) applies"
```

- Open `/anomalies`, choose “Disposition” “Auto-closed: stale”, press “Filter”. Open one anomaly from it and read the Triage block and the detector `State`.

**Expected:**

- The schedule prints `'task': 'close_stale_triage'` and `'schedule': 3600.0` (`backend/app/workers/celery_app.py:71-75`).
- The worker log has `Task close_stale_triage[…] succeeded in … s: {'status': 'ok', 'days': 3, 'closed': ‹n›, 'families': {'log_source': ‹a›, 'waf': ‹b›, 'ips': ‹c›, 'property_watch': ‹d›, 'rule': ‹e›}}` (`backend/app/workers/tasks.py:1801-1811`; REL-15e saw `'closed': 40`). One run closes at most 500 candidates per family; a larger backlog continues on the next hour. A site with the job off prints `{'status': 'disabled', …}`.
- The filtered list shows the chip “Disposition: Auto-closed: stale” and rows reading “Triage: Closed, Unassigned”. The Triage block of one of them reads “Closed”, “Disposition” “Auto-closed: stale”, “Closing note” “No analyst took this anomaly within 3 days.” (`backend/app/i18n/messages.py:3359-3369`) and “Updated by system”.
- The detector `State` is unchanged by the job (an anomaly that was `OPEN` is still `OPEN`, a `RESOLVED` one still `RESOLVED`) and the alert of the anomaly is not changed (REL-15e: no state transition, no anomaly or alert row touched by the run). An anomaly detected less than three days ago and still “New” is not closed.
- The close dialog of MT-146 does not offer the stale disposition; “Reopen” on a stale item works as for any closed item (do not press it here).

**Record:** The `closed` count and the families of the log line, the number of rows in the filtered list, and one stale row's Triage block.

#### MT-149 — Related activity on the anomaly detail

**Precondition:** The site has a log-source anomaly (`/anomalies/<id>`); offenses are collected if the offense list should be non-empty. Sign in with an account that holds `read:anomalies`.

**Action:**

- Open the anomaly and find “Related activity”. Read the heading, the three lists and the pad control. Choose each pad (“30 min”, “1 h”, “2 h”, “6 h”) and read the address.
- Open a property-watch anomaly that has no single log source, if the site has one.

**Expected:**

- The heading reads “Related activity, ‹from›-‹to› UTC (±2 h)”: the anomaly's window plus the pad. The line under it reads “Log source ‹name›. Offense times and magnitudes are QRadar's, as last collected.” The pad control (“Pad”) offers “30 min”, “1 h”, “2 h” (the default) and “6 h”; the choice stays in the address as `pad` and the window in the heading changes with it (`frontend/messages/en/related.json`; REL-15e saw “Related activity, 18:00-22:05 UTC (±2 h)”).
- Three lists, each with its own state: “Offenses on this source” (columns “Offense”, “Description”, “Magnitude (0–10)”, “Status”, “Started (UTC)”, “Last update (UTC)”; each offense opens the offense page of this product), “Other anomalies on this source” (columns “Anomaly”, “Detector state”, “Triage”, “Started (UTC)”: the anomaly you are on is not in it, and the detector state and the triage status are two columns) and “Rules from those offenses” (columns “Rule”, “Offenses”, “Matched events”).
- Nothing found reads as an absence, not as a zero: “No offense on this source in this window. Only offenses collected by this platform are listed, so none that ended before offense collection started can appear.”, “No other anomaly on this source in this window.”, “The offenses in this window name no rule.” When offenses were never collected: “Offense collection has not run for this console, so no offense could be listed. An empty list here would not mean there was none. …”. A late collection says “Offense collection is behind, so offenses in this window may be missing or out of date.” followed by the age of the newest snapshot.
- A rule that is not in the rule catalog reads “Rule not in the catalog (QRadar id ‹n›)”. A matched-event count that was not measured reads “Not measured”, never `0`; a measured zero is `0`; a lower bound reads “≥ ‹n›” with the title “At least this many events matched; the true count may be higher.”
- Each list shows at most 20 rows and the total when there are more (“20 of 34”). The page reads stored rows only; it sends nothing to QRadar.
- A property watch with no single log source says “This anomaly has no single log source, so its related activity cannot be resolved. Nothing is shown for the offenses, other anomalies or rules.” A read the account may not make says “Related activity needs the read:anomalies permission, which your account does not have. Ask an administrator to grant it.” If the site has no offenses, every list that has no data is `Not run` for the rows part; the state sentences are still read.

**Record:** The window and pad shown, the state or count of each list, and one offense or rule row (without customer data).

#### MT-150 — Investigate in QRadar (SOC-2)

**Precondition:** The site has an anomaly, an alert, an offense and a log source. Part 2 is **lab only** and **QRadar search**: it makes the tester run the shown query in the console's own page, under the tester's console login, and the product does not run it. Part 1 sends nothing.

**Action:**

- Part 1: open an anomaly (`/anomalies/<id>`), an alert (`/alerts/<id>`), an offense (`/offenses/<id>`) and a log source (`/log-sources/<id>`). On each find the block “Investigate in QRadar”. Press “Copy AQL” and “Copy window” (paste into a text editor and read).
- Part 1: read the address of “Open in QRadar” (hover or copy the link) without opening it.
- Part 2 (lab only): press “Open in QRadar”. It opens a new tab on the console. Sign in with your own console account.

**Expected:**

- Part 1: the block says “This query is for you to run in the QRadar console. The product does not run it.”, shows the AQL in a code block, “Time range (UTC)” with the start and the stop (UTC), and the buttons “Copy AQL”, “Copy window” and, when the console host is usable, “Open in QRadar” (`frontend/messages/en/ui.json`, `pivot`). “AQL copied to clipboard” and “Time range copied to clipboard” confirm the copies.
- The link opens in a new tab (`target="_blank"`, `rel="noopener noreferrer"`) and carries no token or secret. Its address has the form `https://<console>/console/do/ariel/arielSearch?appName=EventViewer&pageId=EventList&dispatch=performSearch&value(searchMode)=AQL&value(timeRangeType)=aqlTime&value(aql)=<url-encoded AQL>` (`backend/app/pivot/aql.py`; REL-15e read it on a lab console).
- When the stored console host is missing, empty, invalid or carries credentials there is no “Open in QRadar” and the block says “Open in QRadar is not shown: ‹reason› Copy the AQL and run it in the console.” A value the query builder will not put into AQL (a quote, a backslash, a control character) is left out and named under “Notes”, not escaped.
- Part 2: the console accepts the path. REL-15e saw the console redirect to its login page (`/console/logon.jsp?loadback=…`); whether, after the sign-in, the console shows Log Activity with the shown AQL filled in was **not** verified there (no console login). Write what you see: Log Activity with the query filled in is Pass; a console page that does not take the query is a Fail of the link form, not of the copied AQL, and the copy still works.

**Record:** The four pages that had the block, the address form of the link (without the AQL), and for Part 2 what the console showed after the sign-in.

#### MT-151 — Noisy sources

**Precondition:** Signed in with an account that holds `read:anomalies`. Anomalies have been stored for a while; on a young site the sentences about partial coverage are expected.

**Action:**

- Open Behavior → “Noisy sources” (`/behavior/noise`). Read the heading, the table and the footer. Choose the 7-day and the 28-day “Incident window”. Follow “Tune” on one row and use the browser's back; follow an “Alerts of ‹source›” link.

**Expected:**

- The `h1` is “Noisy sources”; the description is “Which log sources keep raising anomalies, and how much of that the closed triage items call noise. Times are UTC.” (`frontend/messages/en/noise.json`). The facts line reads “‹n› sources raised at least one anomaly in the last ‹days› days. Ordered by incidents.” and “Last evaluation: ‹time›.” (“No evaluation has been recorded yet.” when none).
- The columns are “Source”, “Class”, “Incidents”, “Alerts”, “Noise”, “Worst deviation” and “Judged”. The noise cell is `‹noise› noise / ‹closed› closed` only when at least five incidents of that source were closed with a disposition; before that it reads “none closed yet” or “share needs 5+ closed”. A count the platform did not measure reads “not measured”, never `0`. The worst deviation is in “× expected”.
- A source that is not judged says “Not judged” with “A low count here is not evidence of calm.”; it is not shown as quiet. The footer “No incidents on ‹n› sources” is collapsed, not hidden. Under the table: “Noise and worst deviation always cover the last 28 days of stored data, whichever window is selected. Incidents and alerts follow the selected window.”
- A window with fewer stored days says “Partial coverage. The counts cover ‹n› of ‹window› days.” and that the numbers are lower bounds. An empty list says “No source raised an anomaly” with the sentence about the last evaluation.
- “Tune” opens `/behavior/noise/<id>` (MT-152). “Alerts of ‹source›” opens `/alerts` filtered to that source. Without `read:anomalies` the page says “You do not have permission to view noisy sources. Ask an administrator for the read:anomalies grant.”

**Record:** The facts line, the number of rows, and one row's incident, alert and noise cells.

#### MT-152 — Tuning preview, without saving

**Precondition:** A monitored log source from MT-151. An account with `read:anomalies`; the account may or may not hold `log_source:write`. **Do not press “Save”.** If it is pressed, the saved change is listed under “Saved. Changed: …”; set the same values back with the form and write it in the Note field.

**Action:**

- Open `/behavior/noise/<id>`. Read the counts table (“What this source raised”) and the form (“Detection values”).
- Choose “Preview 28 days” (the button names the chosen window). Read “Replay result”.
- Change “Criticality” and preview again; then change one value and read the page before previewing.

**Expected:**

- The counts table lists the detectors with “Incidents”, “Alerts”, “Noise” and “Worst deviation” for 7 and 28 days; a source with no incident says “No detector has raised an incident for this source in the stored 28 days.” The form has six values with their units — “Deviation” (z), “Spike ratio” (× expected), “Drop ratio” (× expected), “Minimum change” (events / bucket), “Open after” (buckets) and “Direction” (“Both: spikes and drops”, “Spikes only”, “Drops only”) — and “Criticality”; each value is marked “default”, “from criticality ‹value›”, “LAB_MODE” or “custom”. An edited value reads “edited, now ‹now›, was ‹was›”.
- The result reads “Replay of ‹n› days of stored buckets, from ‹first› to ‹last›; ‹covered› of ‹n› days are stored.” and a table “Replay comparison” with “Current values” (M · incidents), “Proposed values” (N · incidents) and “Change” (N − M) per detector and “All detectors”. A detector the direction does not run says “not run (direction)”, never `0`. “M and N count the incidents a replay would open from a clean start, using the stored buckets and today's baseline. They are not a forecast, and they are not what was recorded.” The recorded history is a separate line (“Recorded in the same window: ‹n› incidents, ‹x› opened, ‹y› suppressed. This is the recorded history, not a replay count.”).
- A changed criticality is replayed: “Replayed with criticality ‹used› (stored: ‹stored›).” Under LAB_MODE the page says instead “LAB_MODE: criticality changes no detection threshold, so the counts do not depend on the criticality change.”
- After an edit the result is dimmed: “Values changed. Preview again before saving; the numbers below belong to the earlier values.” and “Save” is off with “Run a preview for these values to enable Save.” An account without `log_source:write` can preview and reads “You can preview but not save: saving needs the log_source:write permission. Ask an administrator for it.”
- A source with fewer than 7 stored days gets no count: “No replay for this window” — “Only ‹covered› of ‹window› days are stored; a replay needs at least 7.” and “No count is shown, because a count here would be a guess.” That is the correct branch, not a Fail; the counts part is then `Not run`. A source that is not monitored says “This source is not monitored, so no counts are collected and a replay has nothing to read.”

**Record:** The source, the window, the M, N and Change of “All detectors” (or the insufficient-data sentence), and whether Save stayed unused.

#### MT-153 — Low-volume model (shadow view)

**Precondition:** The page of MT-152. `LOW_VOLUME_MODEL_PREVIEW_ENABLED` is `true` by default. Read-only.

**Action:**

- Scroll to “Low-volume model” on `/behavior/noise/<id>`. Read the state, the facts, both grids, a cell's detail, the proposals and the calibration table.

**Expected:**

- The section opens with “A shadow view. No detector uses it and it opens no alert. It shows what a count model would see in the stored baseline cells.” The model state is one of “Scored”, “Insufficient data”, “Not judged” and “Partial”, with its sentence (`frontend/messages/en/noise.json`, `model`).
- The facts give “Window” (“‹covered› of ‹window› days covered”), “Tail threshold” (“p = 0.001: a bucket counts when its count is at least this unlikely under the fitted model”) and “Shrinkage” (“k = 8”: a thin cell's median is pulled toward the source's pooled median).
- Two grids, “Event count” (events per bucket) and “Average rate” (events per second), by weekday and UTC hour; a cell holds the number of buckets of the last 28 days that fell below p, with a legend (“0 buckets below p”, “1 to 2”, “3 to 9”, “10 or more”). A cell without a tail count is drawn as a dash with “Insufficient data: no tail count” or “Not judged: no tail count”, never as `0`. A selected cell shows its fit (“Poisson” or “Negative binomial”), “Samples”, “Mean”, “Dispersion”, “Raw median”, “Shrunk estimate” and its tails.
- “‹n› cells propose a new normal” lists proposals, each “‹metric›, ‹weekday› ‹hour› UTC”, with “Proposals only. Nothing is written back to a baseline.”; none reads “No cell proposes a new normal: …”.
- “Calibration against closed noise” counts per detector the replayed flags and those on incidents closed as `NOISE`. A refused replay shows dashes and “Not calibrated.” with the reason, never `0`.
- A young site (REL-15e: 2 of 28 days stored) reaches only the insufficient branch: the state “Insufficient data” with “Only ‹n› of 28 days are stored, fewer than 7, so no tail probability is reported.” (`backend/app/i18n/messages.py:5745`) and grids of dashes. That is correct; the scored branch is then `Not run`. A deployment with the setting off says “The low-volume model preview is switched off”.

**Record:** The model state, the covered days, and whether the scored or the insufficient-data branch was seen.

#### MT-154 — Traffic by destination

**Precondition:** Signed in with `read:anomalies`. WAF and IPS collection are off in the code defaults (MT-11); on a site that has not set them up, only the empty branch can be seen and the rows part is `Not run`.

**Action:**

- Open Behavior → “Traffic” (`/behavior/traffic`). Read the heading and the switch “WAF endpoints” / “IPS destination IPs”. Choose each window (“5 min”, “1 hour”, “24 hours”, “7 days”, “28 days”), a sort and the order, and the source when there is more than one. Follow a row's link. Press “Export CSV”.

**Expected:**

- The `h1` is “Traffic by destination”; the description is “Average events per second received by each WAF endpoint and IPS destination IP. Times are UTC.” (`frontend/messages/en/traffic.json`). The view, the window, the sort, the order, the page size and the source stay in the address (`view`, `window`, `sort`, `order`, `limit`, `source`).
- A line states the span (“Newest complete bucket: ‹start› to ‹end›.” for the 5-minute window) and the unit: “Average events/s over ‹n›-minute buckets, divided by measured time only.” The table has the position, the host and “Method and path” (WAF) or “Destination IP” (IPS), “Average (events/s)”, “Trend” and “Peak (events/s)”. A trend is a line without a marker on every point; one bucket says “one bucket, no trend”; a rate that was not measured is “not measured”, never `0`. A row no longer tracked says “Retired”.
- A partial or truncated window says so (“Partial data.”, “Truncated.”) and events that no listed row carries are stated: “‹n› of ‹m› events (‹x›) are not attributed to a listed endpoint. A total built from these rows is a lower bound.”
- A WAF row opens `/behavior/waf/sources/<id>` (“Open the endpoints of this source”); an IPS row opens the anomaly list for that address (“Anomalies of this IP”). “Export CSV (all ‹n› endpoints, ‹window›)” downloads the same window and sort without the page, with the note “Large exports are cut at the server's limit and marked in the file.” A cell that starts with `=`, `+`, `-`, `@`, a tab or a carriage return is not written as a formula.
- A site with no profile says “No WAF source has a behavior profile.” (IPS: “No IPS source has a behavior profile.”) with “Nothing is collected until a log source is mapped and validated. An empty list here does not mean there is no traffic.” and the link “WAF sources” (“IPS sources”). A source with a profile that is not collected says “This WAF source is not collected.”, one with no complete bucket “No complete bucket in this window.”, one with no data “No endpoint has data in this window.”; none of these is a zero. In the no-profile state the “Export CSV” line is shown as not available; the reason it prints there (“the data could not be loaded”) is REL-15e's product finding P1 and is not scored. Without `read:anomalies`: “You do not have permission to view traffic. Ask an administrator for the read:anomalies grant.”

**Record:** The view and window, the state seen (rows, no profile, not collected, no data) and one row's average and peak.

#### MT-155 — Queue and the "last visit" marker (writes) lab only

**Precondition:** **lab only** from the second step: opening `/queue` without a chosen comparison moves the signed-in account's marker (its "last visit") and the interface cannot move it back. The first step writes nothing and can be done anywhere; in production write the rest `Not run`. An account with `read:anomalies` and `read:alerts`.

**Action:**

- Open `/queue?compare=24h`. Read the line under the heading.
- Open Behavior → “Queue” (`/queue`). Read the line, the strip “Sources” and the table. Choose “8 h”, “24 h” and “7 d”, “Back to last visit”, the scopes “Mine”, “Unassigned” and “All”, a “Kind”, a “Family”, a “Severity” and “New or updated only”. Open the page again, unfiltered.

**Expected:**

- With a chosen comparison the line reads “Showing what changed since ‹time› (compared with 24 h ago)” and “Your marker was not moved while a comparison time is chosen.” (`frontend/messages/en/queue.json`, `marker`).
- The first unfiltered visit reads “Showing what changed since ‹time› (last 24 h — first visit)” (later visits: “(your last visit)”) and then “Your marker moved to ‹time›. The New and Updated labels still compare with the visit before.” A filtered, narrowed or paged view says “Your marker was not moved because this view is narrowed by a scope, filter or page. Open the unfiltered first page to move it.”
- The strip names five kinds with their state: “Anomalies”, “Alerts”, “Offenses”, “Source reviews” and “Catch-all senders”, each “‹n› waiting” and “‹n› new or updated”. A source that failed says “failed to load. Its rows are missing and the total below is a lower bound.”, a hidden one “not shown. Needs the ‹permission› permission.”, a never-collected one “not collected. …”, a stale one “stale. Last collected ‹age› ago (‹time›). …”; a failed source makes the totals “at least ‹n›” and the marker is kept.
- The table has the columns “Change”, “Severity”, “Kind”, “Title”, “State”, “Owner” and “Waiting since (UTC)”. A row says “New” or “Updated” in words, or neither; an anomaly row with an alert carries the chip “Alert: ‹status›”. Order is severity, then the longest wait. The scope (default “All”), the kind, the family, the severity and “New or updated only” stay in the address. More than 1000 rows say “The queue holds more than 1000 rows. Narrow it with a filter.” An empty queue says “Nothing is waiting. Every source you may read loaded at ‹time›.” and never an empty table without a sentence.

**Record:** The comparison line of each visit, the five source counts, and the state of any source that was not “loaded”.

**Cleanup:** None in the interface: the marker is the account's own and only moves forward. In the lab the rollback rehearsal (MT-123) removes it with the other rows written after the upgrade.

#### MT-162 — Keyboard on the lists: `j`, `k`, Enter, `o`, Esc, `n` and `g` (SOC-15a, SOC-15b)

**Precondition:** Signed in with `read:anomalies`. At least two anomalies in the list. Nothing here writes: `n` only opens a page. Keep the pointer away from the table and do not type in a field while you press the keys.

**Action:**

- Open `/anomalies`. Read the two hint lines above the table. Press `j` twice, `k` once, then Enter. Go back. Press `j`, then `o`. Go back. Press `j`, then `n`. Go back. Press `j`, then Esc. Click into a filter field and press `j`. Press Ctrl+K.
- Press `j` on the last row of the page.
- Press `g`, then `q` within a second. Press `g`, then wait two seconds, then `a`. Press `g`, then `x`. From `/anomalies` press `g` then `l`, `g` then `o` and `g` then `d`.
- In the top bar set “Refresh” to “5 min”, reload the page, then open the same address in a private window.

**Expected:**

- The two hint lines start with “Keys” (then `j` next row, `k` previous row, `Enter` open, `Esc` clear, `n` add a note) and “Go to” (`g` `d` Dashboard, `g` `q` Queue, `g` `a` Anomalies, `g` `l` Alerts, `g` `o` Offenses); the key caps are literal keys and the words are `frontend/messages/en/ui.json`, `listKeys`.
- `j` marks the first row (a lighter background and a bar at its left edge, `aria-current`); `j` and `k` move the mark; `k` with nothing marked does nothing. Enter or `o` opens the marked row's anomaly. `n` opens it with the cursor in its “Add a comment (up to 4000 characters)” field (an account with only a read-only Triage block gets the page and no field). Esc removes the mark. In a field, with Ctrl or Alt held, and under an open dialog the keys do nothing; Ctrl+K stays the search.
- On the last row `j` does not wrap and does not page: the screen-reader line (in the accessibility tree, not drawn) reads “Row ‹n› of ‹total› is the last on this page. Use the page links below the table for more.” Each move says “Row ‹n› of ‹total›: ‹label›” there.
- `g` then `q` goes to `/queue` (likewise `a` `/anomalies`, `l` `/alerts`, `o` `/offenses`, `d` `/`). The second key has to come within a second and a half (`GO_WINDOW_MS`, `frontend/src/components/shell/GoKeys.tsx`); after more time, or with another key (`x`), nothing happens and the key does its own job (`a` alone does nothing on `/anomalies`).
- After the reload “Refresh” still reads “5 min” (the choice is remembered in the browser, key `qradar-dash.refresh`); the private window reads “Off”.

**Record:** What each key did, and the two “Refresh” readings.

#### MT-163 — `a` acknowledges the marked alert (SOC-15b) (writes) lab only

**Precondition:** **lab only**: an acknowledged alert changes the state the analysts see; in production write `Not run`. An account with `alert:ack` (the administrator holds it through `admin:*`), and an alert that is open and was made for the test.

**Action:**

- Open `/alerts`. Press `j` until the open test alert is marked, then `a`. Mark an alert that is acknowledged or resolved and press `a`. If the site has an account without `alert:ack`, repeat on an open alert with it.

**Expected:**

- The first `a` calls the backend. Only after it answers does the pill under the table say “Alert acknowledged: ‹title›.”; the list refreshes, the row keeps its mark and its status is “Acknowledged” (`frontend/messages/en/ui.json`, `listKeys.acknowledged`). While the call runs a second `a` does nothing.
- On an alert that is not open: “Only an open alert can be acknowledged.” and no call.
- Without `alert:ack` the pill is in the failure tone and reads “You need the alert:ack permission for this.” (`frontend/messages/en/errors.json`, `forbiddenPermission`); the status does not change and no success text is shown.
- On `/alerts` the hint line has `a` acknowledge in place of `n`.

**Record:** The text of each pill and the status of the alert after the first `a`.

**Cleanup:** It cannot be undone in the interface (MT-89). The alert was made for the test; the lab VM's rollback rehearsal (MT-123) removes the row with the others written after the upgrade.

#### MT-164 — Handover page: the latest record and the earlier ones (SOC-14b)

**Precondition:** Signed in with an account that can read anomalies. The page reads `GET /api/v1/handover/latest` and the list; it writes nothing. On a site where no handover was ever written, only the empty branch can be seen and the rest is `Not run` until MT-165 has written one in the lab.

**Action:**

- Open Behavior → “Handover” (`/handover`). Read the page from top to bottom. If there is more than one record, press “Show earlier” and open an earlier record from the list, then “Back to the latest handover”.

**Expected:**

- The `h1` is “Handover”, the description “What did the last shift leave?”, and the browser tab reads “Handover · Behavior Analytics” (`frontend/messages/en/handover.json`). An account with `anomaly:triage` has the button “Write handover” in the header (MT-165); another account has none.
- With no record: “No handover yet. The queue lists what is waiting.” with the link “Open the queue” (and “Write the first handover” for an account that may write). No empty table.
- A record shows “Latest handover”, “Written by ‹author›”, “Covers what changed from ‹time› to ‹time›.” (UTC), then “Note”, “Watch” (“‹n› watched”), “Open when this was written” with “‹shown› of ‹total›” (or “of at least”), a table “Open items frozen with this handover” with “Severity”, “Item”, “State”, “Owner”, “Flags” (“Unassigned”, “Over SLA”, “New”, “Updated”) and “Waiting since” (“‹age› when written”), then “Acted on by ‹author› in this window” (“No recorded action by ‹author› in this window.” when there is none) and “Earlier handover”. The link “See all in the queue” opens `/queue`.
- A source the author's page could not read is listed under “Sources not read cleanly” with its reason — “not read. It failed to load when this was written, so its rows are missing, not absent.”, “not collected when this was written, so its rows are missing, not absent.” or “not read. The author's account lacks ‹permission›, so its rows are not in this record.” — and an empty frozen list then says “No open rows were frozen. Check the source lines below before reading this as an empty queue.”, never “No open rows were waiting when this was written.”
- “Earlier handovers” lists “Author”, “Written” and “Note” (“‹n› characters”) with “Show earlier” (“‹shown› of ‹total› shown”), or “No earlier handover.” The record opened from it reads “Handover” and the page offers “Back to the latest handover”.
- A record cannot be changed here: there is no edit or delete control on the page.

**Record:** The state seen (empty or a record), the author and time of the latest record, and whether any source line appeared.

#### MT-165 — Writing a handover (writes) lab only

**Precondition:** **lab only**: a handover is an immutable record in a log shared by every analyst. The interface has no edit and no delete, and the only removal is the retention job after `HANDOVER_RETENTION_DAYS` (default 90 days; `docs/CONFIGURATION.md`). In production write `Not run`. An account with `anomaly:triage` (the administrator holds it through `admin:*`; MT-145). Write a plainly marked test note, for example “MT-165 test, ignore”.

**Action:**

- Open `/handover` and press “Write handover”. Press “Save handover” with the note empty. Type a note, watch the counter, and mark two rows under “Watch” if the table has rows. Press “Cancel” once and start again. Press “Save handover”.
- If the site has an account without `anomaly:triage`, open `/handover?compose=1` with it.

**Expected:**

- The `h1` is “Write a handover”, the description “Freeze what is open now and leave a note for the next shift.” The note field is “Note”, with “What the next shift needs to know. Notes cannot be edited or deleted; a correction is a new handover.” and the counter “‹n› of 4000 characters” (“‹n› of 4000 characters: ‹m› too many” past the limit). “Rows that will be frozen” lists the open rows with a “Watch” checkbox each; “‹n› of 20 watched”, and at 20 “20 rows are watched, the most a handover holds. Clear one to watch another.” An empty preview says “No open rows would be frozen right now.”
- Saving with no note: “Write a note before saving.” and nothing is saved.
- A save shows “Handover saved. It cannot be edited.” and the saved record under it (MT-164). A save that dropped a watched row says “Handover saved. ‹n› watched rows were not in the snapshot and were not frozen: ‹keys›.” A failed save keeps the note in the field and says what failed (“The handover was not saved: the backend did not answer. Your note is still here. Retry.”).
- Without `anomaly:triage` the page says “You cannot write a handover.” and “Writing a handover needs the anomaly:triage permission. Nothing was saved. Ask an administrator for it.” While the account is still being read: “Checking your permissions…”.
- The new record is now the latest (MT-164) and the “Latest handover” widget shows it (MT-161). Saving changed nothing in triage, alerts or QRadar.

**Record:** The record's time and author, the counter at the limit if you tried it, and the sentence of each refusal.

**Cleanup:** None: notes cannot be deleted. The test note stays until retention removes it; the lab VM's rollback rehearsal (MT-123) removes it with the other rows written after the upgrade.

#### MT-166 — Entity timeline: what the platform stored about an IP, host or user (SOC-16)

**Precondition:** Signed in with `read:anomalies`. A value that is stored **in the last 7 days**: the page shows at most 7 days (“7 days” is its longest choice) and ignores `start` and `end` in the address (they exist only in the API, `GET /api/v1/entities/timeline`), so a mention older than 7 days cannot be shown in the interface. A value that is always recent on a lab with a working inventory sync: the identifier (an IP address) or the name (a host) of a monitored log source, as listed on the Fleet page. The inventory sync (`LOG_SOURCE_SYNC_INTERVAL_SECONDS`, default 3600 s; `backend/app/services/inventory_sync.py`) writes one “Log sources” mention per source with the time of the sync, so it is never older than that interval; the “Log sources” lane (`read:fleet`) shows it once a sync has run after the upgrade. Other sources: an IP address from the “Top contributors” of an anomaly detail, or the name of an offense's log source, if it was stored in the last 7 days. If the site has no mention younger than 7 days (no sync since the upgrade and no recent anomaly or offense), do not search by an old value: run the checks that need no row (the lane table, the banner, the unstored `203.0.113.250`) and write `No recent mention; rows not seen` in the record. The timeline is read from the platform's own database only; it is not a QRadar search and it never shows raw events (`frontend/messages/en/entities.json`, `description`). Run MT-159 first: a lane whose index is not complete cannot prove absence.

**Action:**

- Type the value into the top-bar search. In the result list find the group “Entities” and its row “Entity timeline for “‹value›”” (“Stored mentions of this IP, host or user”) and open it. Open `/entities` directly as well. Change “Time range (UTC)” between “1 hour”, “24 hours” and “7 days”. Search a plain host name without choosing “What it is”. Search a value that was never stored, such as `203.0.113.250`. On an anomaly detail, follow a contributor's link “Open the stored timeline for ‹value›”.

**Expected:**

- `/entities` is not in the navigation (MT-22). Its `h1` is “Entity timeline” with no value, and “‹Kind› ‹value›” (for example “IP 203.0.113.7”) with one; the browser tab reads “Entity timeline · Behavior Analytics”. The form has “Value”, “What it is” (“IP address”, “Host”, “User”) and “Show timeline”. The window is 24 hours unless “Time range (UTC)” says otherwise and never more than seven days; the range and the value stay in the address.
- “What was checked” is a table of lanes with “Lane”, “Status”, “Stored mentions”, “On this page” and “Note”: “Behavior anomaly contributors”, “WAF top clients”, “IPS source contributors”, “IPS destination entities”, “QRadar offenses”, “Log sources”, “Catch-all senders” and “Property watch values”. A status is “Measured”, “None stored”, “Not collected”, “Forbidden” (“Not shown: this lane requires ‹permission›.”), “Partial”, “Stale”, “Truncated” or “Not applicable”; a lane that was not measured says “Not measured”, never `0`.
- “Stored mentions” lists, newest first, “Time (UTC)”, “Lane”, “Role” (“Source address”, “Destination address”, “Host”, “Username”), “Record”, “Severity” and “Basis” (“Exact”, “Top contributor”, “Admitted entity”); a top-contributor row's tooltip says “From a bounded top list: values below the cut were not stored.” A long list is paged (“Showing ‹n› mentions, newest first.”, “Older mentions”, “Back to the newest”).
- A value with no stored mention in the window says “No stored mention of ‹value› in this window.” and “That is not a finding that it was absent. Read the status of each lane below: a lane that was not collected, or that stores only top contributors, cannot show absence.” An incomplete lane (MT-159) is “Partial”: “This lane's index is not complete, so absence cannot be proven here: older records are still being indexed, or a stored record could not be indexed or opened.” The “Log sources” lane is “Partial” with this sentence, whatever the `entity_mentions` line of `doctor` says, when the value is the identifier or name of a registered log source and no “Log sources” mention has been stored yet (`backend/app/services/entity_timeline.py`, `proves_absence=not coverage.log_source_match`; `inventory_sync.py`). This lane has no backfill: it becomes “Measured” with a row after the first log-source inventory sync that follows the upgrade, at most `LOG_SOURCE_SYNC_INTERVAL_SECONDS` (default one hour) later. Until then “Partial” is the correct answer, not a Fail. A value that is not a registered log source reads “None stored”. A banner states the page's own state (“Partial.”, “Not collected.”, “Truncated.”).
- A host name with no kind is refused with “kind is required when value is not an IP address” and “Choose what “‹value›” is, then show the timeline again.”; a value over 255 characters, “The value is longer than 255 characters, so no search was made. Shorten it and try again.”
- An account without a lane's permission sees that lane as “Forbidden” and the others read normally.

**Record:** The value and range searched, the status of each lane, the number of mentions, and any lane that was “Partial” and its reason. If no mention younger than 7 days existed, say so.

### 7. Alerts

In the interface this page is called **Alerts**, the route is `/alerts`. Lifecycle: open → acknowledged → resolved; a resolved alert cannot be reopened.

#### MT-87 — Alert list

**Action:**

- Open the `/alerts` page. Try the status filter (“Alert status”).

**Expected:**

- The `h1` is “Alerts”; the columns are “Title”, “Anomaly”, “Severity”, “Status”, “Detections while open”, “First seen”, “Last seen” (`frontend/src/app/alerts/page.tsx:171-191`). The status filter: “Open”, “Acknowledged”, “Resolved”. “Detections while open” counts how often the condition was detected while the alert was open, the first detection included, so a first-time alert reads `1` (it is “not repeated” on its detail, MT-88) (`frontend/messages/en/alerts.json`, `table.occurrences`). The other filters, the counts beside them and the link are MT-156.
- If there is no alert: “No alerts have been raised.” The count line: “Showing {shown} of {total} alerts.”
- The titles of the catalog alerts are English sentences (MSG-7): search threshold and search failure, custom rule, offense collection freshness, new log source, new catch-all sender. Offense collection freshness reads “Offense collection is stale for {instance}”. The alerts the engine writes are English sentences too (MSG-7b): log source volume “{anomaly_type} on {log_source}” (for example “Volume drop”), a silent source “No events received from {log_source}” (FIX-45b), one console-wide “No log source sent events in {bucket}” (FIX-45c), the console's collection “Log source volume collection is stale for {instance}” (MT-171), IPS “{detector} on {log_source}” and the destination port, destination IP or signature forms, property watch “{detector} on {watch}: {value}”, WAF “{detector} on {log_source}: {method} {endpoint}”. The same limits as MT-38.

**Record:** That the list opened, the number of rows and the title of one alert.

#### MT-88 — Alert detail: Description and resolution reason

**Precondition:** The site has at least one alert.

**Action:**

- Open an alert from the list.

**Expected:**

- The `h1` is the alert's own title, and the browser tab reads “Alert detail · Behavior Analytics” (“Alert Detail” is the `h1` only when the alert could not be loaded). The “Description” card carries the alert's description. There are the “Evidence” and “Lifecycle” sections and the table “Notification Delivery History”; if there is no notification: “No notifications for this alert.” Under the `h1` the line reads “not repeated” for an alert detected once, or “repeated ‹n›× while open”, and the “Lifecycle” section has “Opened:”, “Last anomaly bucket:” (a time, or “not recorded in this alert's evidence” for an alert that carries no bucket, such as a collection alert), “Last evaluated:”, “Acknowledged by:”, “Resolved by:” and “Resolution:” (`frontend/messages/en/alerts.json`, `detail`; FIX-45-ui).
- On a resolved alert the “Resolution:” line shows the resolution reason. The reason of a catalog recovery is an English sentence (for example “condition recovered”, “Offense collection freshness recovered.”). The recovery reason of an engine alert is “condition recovered”. A reason written by an operator is shown as written. The same limits as MT-38.

**Record:** That the Description card appeared and the text of one resolution reason. If there is no resolved alert, `Not run` for that part.

#### MT-89 — Acknowledging and resolving an alert (writes) lab only

**Precondition:** **lab only**: an alert marked acknowledged or resolved changes the state the analysts see, and a resolved alert cannot be reopened; in production mark this check `Not run`. In the lab, do it on an alert that was created for the test.

**Action:**

- In the detail press the “Acknowledge” button. Then choose “Resolve”, write a reason in the “Resolution reason” field, read the text “Resolve this alert? A resolved alert cannot be reopened.” and confirm (“Resolve alert”).

**Expected:**

- “Alert acknowledged.”; the status is `ACKNOWLEDGED`. After resolving: “Alert resolved.”; the status is `RESOLVED`; “This alert is resolved. No further action is available.” The reason you wrote appears unchanged on the “Resolution:” line.

**Record:** The result of the two steps.

**Cleanup:** It cannot be undone (resolution). In the lab choose the alert for the test; there is no delete afterwards. MT-1b does this on the lab VM.

#### MT-156 — Alert filters, facet counts and a link that reproduces the view

**Precondition:** The site has alerts of more than one status or severity. An account with `read:alerts` (and `read:anomalies` for the anomaly list). Read-only.

**Action:**

- Open `/alerts`. Read the counts beside the status, severity and kind controls. Choose one severity (for example “HIGH”) and read all the counts again. Choose a kind, add a log source (“Add a log source…”), type a word in “Search alert titles” and press “Apply”. Choose a range chip (“1h”, “24h”, “7d”, “30d”) and “Custom” with a start and an end. Press “Copy link”, open the copied address in a new tab.
- Open `/alerts?severity=BOGUS` and `/alerts?range=2y`. Open `/anomalies` and choose each range chip.
- Read the facet counts from the API:

```bash
curl -s --cookie "$SESSION" "https://<host>/api/v1/alerts/facets?severity=HIGH&status=OPEN"
```

**Expected:**

- The controls are “Alert status”, “Severity”, “Kind” (“All kinds”, “Log-source anomalies”, “WAF anomalies”, “IPS anomalies”, “Property-watch anomalies”, “Catch-all senders”, “Custom rules”, “Scheduled searches”, “QRadar instance”), “Log source” and the title search (up to 100 characters); there is no per-detector filter (`frontend/messages/en/alerts.json`, `list`). Each choice becomes a chip under “Filtered by” (“Status: …”, “Severity: …”, “Kind: …”, “Log source: ‹name›”, “Title contains: …”) with “Clear all”.
- Counts come from `GET /api/v1/alerts/facets` and each count ignores that control's own filter: with “HIGH” chosen the other severities still show how many alerts they have under the rest of the filters (the API answer has the other severities' counts under `status=OPEN`). A zero is a measured zero (“0”); “All statuses (‹n›)” is the sum of the status counts, not the filtered list total. If the count read failed, the page says “Counts are unavailable: the count read failed. The alert list below is complete; reload the page to try the counts again.” and draws no zero.
- The list columns are those of MT-87 (with “Anomaly”), a title reads “‹Detector label› on ‹source›” (for example “Volume spike on ‹source›”), and a filtered list says “Showing ‹n› of ‹m› matching alerts.” An empty result says “No alert matches these filters.” with “Clear filters”.
- The range chips (`1h`, `24h`, `7d`, `30d`) stay relative in the address (`range=24h`); “Custom” writes absolute UTC `since` and `until`; the window is printed in UTC beside the chips (“‹from› to ‹to› (now)”). With no chip and no custom range, the alert list says “All time: no start or end time is set.”; `/anomalies` says “Default window, last 24 hours: ‹from› to ‹to›” (`anomalies.json`, `list.defaultWindow`).
- “Copy link” answers “Link copied”; the copied address opens the same filters, range and sort (`frontend/messages/en/ui.json`, `copyLink`).
- An unknown value is dropped and named: “Ignored an unknown severity value.” and “Ignored an unknown range value; the range chips are 1h, 24h, 7d and 30d.” (the address keys are `status`, `severity`, `family`, `log_source_id`, `q`, `sort`, `order`, `range`, `since`, `until`; `frontend/src/lib/alertQuery.ts:101-127`).

**Record:** The status, severity and kind counts before and after choosing a severity, and the address that “Copy link” gave (without any host).

#### MT-157 — Don't alert this again (writes) lab only

**Precondition:** **lab only**: the check holds an alert condition back and writes a row (history stays in the table after it ends). Signed in with an account that holds `alert:suppress` (the administrator does, MT-145). An alert or anomaly of a log source, WAF, IPS or property watch exists. Choose a condition on a lab source; choose **1 day**, never 7 or 30.

**Action:**

- Open the alert (`/alerts/<id>`) or the anomaly (`/anomalies/<id>`). Press “Don't alert this again”.
- Leave “Reason” empty and press “Quiet this condition”. Then write the reason “MT-157”, keep 1 day, read “Before you save”, and press “Quiet this condition”.
- Read the banner “Quiet period”. Open Administration → “Alert policy” and read “Quiet periods”.
- Press “End now” in the banner (or “End” in the table), read the question, press “Keep it quiet”, then press “End now” again and “End quiet period”.
- If the site has an account that does not hold `alert:suppress`, open the same page with it.

**Expected:**

- The form (“Don't alert this again”) offers “Quiet for” 1, 7 or 30 days — “Only 1, 7 or 30 days. To keep a condition silent for good, set a record-only policy on the alert policy page.” —, “Reason” (3 to 1000 characters), “Owner” (the signed-in account), “Quiet the whole source (every detector)” (for a log source, WAF or IPS condition, not for a property watch) and “This is a planned change” with “Change or ticket reference”. An empty reason shows “Enter a reason of at least 3 characters.” and nothing is saved. “Before you save” reads “Alerts for ‹detector› on ‹source› are held back for 1 day. Incidents are still recorded.” (`frontend/messages/en/suppression.json`, `form`). The page never sends a detector: the server derives the condition.
- After saving, the banner reads “Quiet until ‹time› UTC by ‹owner› (1 day).” with “No incidents recorded yet while quiet.” (a count once incidents were recorded), the “Scope”, the “Reason”, the “Kind” (“Analyst”; “Planned change” with its reference), and “Alerts opened during the quiet period” with “No alert broke through the quiet period.” Incidents are still recorded; only the alert and its notification are held back. A finished incident cannot be counted, so the count can stay at none on the lab (REL-15e); a break-through (a ratio of at least 10 times normal for a volume spike, and the rules in the release notes) cannot be made on purpose, and that part is `Not run`.
- `/admin/alert-policy` lists it under “Quiet periods” with “Subject”, “Detector”, “Kind”, “Owner”, “Reason”, “Ends (UTC)”, “Incidents recorded” and “End”.
- “End now” asks “End this quiet period now?” with “End quiet period” and “Keep it quiet”. “Keep it quiet” changes nothing. After the end: “The quiet period was ended. Alerts for this condition follow the alert policy again.” and the table says “Nothing is quiet. Every condition alerts by its policy.” (or lists the other periods that are active).
- An account without `alert:suppress` sees no form, only “Quieting a condition needs the alert:suppress permission.”, and the Quiet periods table reads “Ending a quiet period needs the alert:suppress permission.”

**Record:** The condition's family and detector, the banner text, and the sentence after the end.

**Cleanup:** “End now” (above) ends the quiet period at once. The ended row stays in the history until the retention rules remove it; in the lab the rollback rehearsal (MT-123) removes it.

#### MT-171 — The log-source collection alert and `sync stale-log-sources` (FIX-45e, FIX-45f) (writes) lab only

**Precondition:** **lab only**: the command runs the same detection as the beat task `detect_stale_log_source_collection` and, if the console's volume collection is stale, opens an alert and queues its notification; in production write `Not run`. The beat task runs every `OFFENSE_STALE_CHECK_INTERVAL_SECONDS` (default 300 s). The console must have at least one log source with monitoring on. Do not stop a collector to provoke the alert; read the answer of a healthy lab.

**Action:**

```bash
docker compose exec -T backend python -m app.cli.sync stale-log-sources --instance <console name>
```

Then open `/alerts` and look for “Log source volume collection is stale for ‹console›”.

**Expected:**

- On a console whose collection ran within the threshold the line is `stale-log-sources <console> ok stale=False judged=True age_seconds=<n> consecutive_failures=0 transition=unknown duration_ms=<n>`, exit code 0 (`backend/app/cli/sync.py`, `_report`: the task's `transition` is `None` when no alert changed state, and the command prints a `None` value as `unknown`, so `transition=unknown` is the correct line of a healthy console, not a missing measurement; `backend/app/workers/tasks.py`; `backend/app/workers/tasks.py`, `detect_stale_log_source_collection`). The threshold is `max(900 s, 3 × COLLECTION_INTERVAL_SECONDS)`, 900 s with the default of 300 s. There is no alert in the list.
- A console with no monitored log source answers `stale=False judged=False reason=no_monitored_sources`; an open alert of this kind is closed with the reason “Closed without a verdict: no log source of this console is monitored, so collection freshness is not judged.” — not “freshness recovered” (FIX-45f). A console registered less than the threshold ago with no run yet answers `reason=awaiting_first_run` and opens nothing.
- A console whose last volume run failed or is older than the threshold gives `stale=True judged=True`, and one alert per console, severity High: title “Log source volume collection is stale for ‹console›”, description “The last read failed or did not run, so the platform cannot confirm whether sources are silent. NO_EVENTS alerts are not raised for the missing intervals.” Its evidence has `last_run_at`, `age_seconds`, `threshold_seconds`, `consecutive_failures` and `last_error`. The next successful run resolves it with “Log source volume collection freshness recovered.” (`backend/app/i18n/messages.py`).
- When no monitored source sent events in a complete bucket the engine opens one console-level alert, “No log source sent events in ‹bucket›” (“…; QRadar may be starting or its pipeline may have stopped.”), not one “No events received from ‹source›” per source (D-62, FIX-45c). A per-source silence reads “No events received from ‹source›” with the streak and the expected count (FIX-45b).

**Record:** The command's line and exit code, and whether any alert of these kinds is open.

**Cleanup:** An alert it opened closes by itself at the next successful run, or is resolved in the interface (MT-89); it cannot be deleted.

### 8. Custom rules

A custom rule is a threshold rule over the stored aggregates; it sends nothing to QRadar. The route is `/custom-rules`. Creating a rule needs `custom_rule:write` (administrator or `detection_engineer`); `read:custom_rules` only reads.

#### MT-90 — Custom rule list and states

**When to look:** The new reason sentence appears after one 5-minute evaluation following the upgrade (one cadence + 3 min).

**Action:**

- Open the `/custom-rules` page.

**Expected:**

- The `h1` is “Custom rules”; the columns are “Name”, “Status and reason”, “Subject”, “Condition”, “Severity”, “Enabled”, “Last evaluated (UTC)”.
- The state words: “OK”, “Open”, “Recovering”, “No verdict”, “Stale”, “Not collected”, “Subject retired”, “Disabled”, “Not enabled”; a rule that has never been evaluated shows “Never evaluated”. The reason sentence next to the state is an English sentence (MSG-7, rewritten at every evaluation).
- If the evaluation switch is off: “Rule evaluation is off because CUSTOM_RULES_ENABLED is false. Enabled rules are not being judged.” If alerting is off: “Breaches are recorded and nothing pages until CUSTOM_RULES_ALERTING_ENABLED is on.” If there is no rule: “No custom rules exist yet.”

**Record:** That the list opened; the state of one rule and that its reason sentence is English. If there is no rule, `Not run`.

#### MT-91 — Custom rule detail

**Precondition:** There is at least one custom rule.

**Action:**

- Open a rule from the list.

**Expected:**

- The `h1` is the rule's name, and the browser tab reads “Custom rule · Behavior Analytics”; there are the sections “Current state”, “Subject”, “Condition”, “Last evaluated” and “Breach history”. If there is no breach: “No active breach is recorded for this rule.” An enabled rule also carries the label “Alerting mode” or “Shadow mode”.

**Record:** That the detail page opened.

#### MT-92 — Custom rule draft preview (without saving)

**Precondition:** The account has `custom_rule:write` (administrator).

**Action:**

- Open the `/custom-rules/new` page (“New custom rule”). Choose “Fleet class” as the subject kind, a fleet class and a metric; enter the “Threshold ({unit})”. **Do not save.**

**Expected:**

- In the lower section “Draft preview” opens: “Replays this draft over stored aggregates. It persists nothing and sends nothing to QRadar.” The preview is drawn from the stored buckets; a missing bucket is shown as “Not measured” and as a gap, and no zero is drawn. The completeness and measurement labels: “Exact” / “Lower bound” / “Not measured”.

**Record:** That the preview was drawn.

**Cleanup:** Leave the page without saving: nothing is stored.

#### MT-93 — Creating and deleting a custom rule (writes)

**Precondition:** The account has `custom_rule:write`. The rule is saved **disabled**; it is deleted when the test is finished.

**Action:**

- On the `/custom-rules/new` page: “Name” = `MT-test-rule`; subject = a fleet class; a metric and a threshold; “Enabled” **unchecked**; press the “Create rule” button.
- On the rule's detail page choose “Delete”; read the text “Delete this rule? If it has an open alert, the alert will be resolved before the rule and its breach history are removed.” and confirm with “Delete rule”.

**Expected:**

- After saving: “Rule saved.”; the rule is “Disabled” in the list and raises no alert (if the evaluation switch `CUSTOM_RULES_ENABLED` is off, the state is “Not enabled”).
- After the delete the rule is not in the list.

**Record:** Whether creating and deleting worked. The rule name is not written on the page.

**Cleanup:** The second step is the cleanup. The audit log keeps the `custom_rule.create` and `custom_rule.delete` rows.

### 9. Searches

The search catalog holds stored and validated AQL searches (`/searches`). The interface does not run raw AQL. A search going to QRadar spends Ariel capacity; in this section the check that sends a search to QRadar is **lab only**.

#### MT-94 — Search list

**Action:**

- Open the `/searches` page.

**Expected:**

- The `h1` is “Search Catalog”; the columns are “Name”, “Category”, “MITRE”, “Schedule”, “Severity”, “Ver”, “Enabled”. If there is no search: “No scheduled searches defined yet.” On an account without write permission: “Creating a search needs search:write. An admin or the detection_engineer role can grant it.” or “The editor is hidden.”.

**Record:** That the list opened; the number of rows.

#### MT-95 — Search run history and the error row [core]

**Precondition:** At least one search is scheduled and has run.

**When to look:** A scheduled run needs a wait as long as the search's cron cadence.

**Action:**

- Open a search (`/searches/<id>`). Read the “Execution history” table.

**Expected:**

- The `h1` is the search's name, and the browser tab reads “Search · Behavior Analytics”; the table columns are “Started (UTC)”, “Status”, “Trigger”, “Ver”, “Duration”, “Results”, “Retries”, “Threshold”, “Error”. The statuses are `COMPLETED`, `FAILED`, `TIMEOUT`; the trigger is `SCHEDULED` or `MANUAL`.
- On a truncated run the count is a lower bound (with the `≥` mark; the search stopped at its row cap) and the threshold column says “no verdict”.
- The “Error” cell of a failed row: the fixed error messages are English sentences from the catalog (MSG-8b): “Ariel search failed”, “search exceeded timeout of {timeout}s”, “No Ariel permit became available within {waited} seconds (per-console cap {instance_cap}, global cap {global_cap})”. Ariel's own message and the exception text are free text and are shown as they came. If there is no failed row, `Not run`.

**Record:** That the table opened; the error message of one failed row (if any).

#### MT-96 — Schedule floor (cron) and presets

**Precondition:** The account has `search:write` (administrator).

**Action:**

- In the “Schedule” section of a search open the schedule list (“Change to”). **Do not save.**

**Expected:**

- The list offers seven presets: 5 min, 15 min, 30 min, 1 h, 2 h, 4 h, 8 h (cron `*/5 * * * *` … `0 */8 * * *`). None is shorter than 5 minutes.
- The floor is **300 seconds** (`CRON_FLOOR_SECONDS`). A `schedule_cron` that fires more often than every 300 s is refused by the API with 422; the message is coded by MSG-8b: “schedule_cron {cron} ({timezone}) fires {gap} seconds apart ({previous} then {next}); a search must fire at least {min_seconds} seconds apart”. Because the interface offers only presets, you cannot trigger this refusal from the interface; the refusal is proved by automated tests and is not tried by hand here.
- If a search has a custom cron, the list shows “Custom (keep current)” and the cron as it is.

**Record:** Whether the seven presets are right. The refusal itself was not tried (write this in the Note field).

**Cleanup:** Leave the page without saving.

#### MT-97 — Running a search now (writes) lab only QRadar search

**Precondition:** **lab only**: a manual run sends an Ariel search to QRadar (spends console capacity), leaves a "MANUAL" run row that cannot be deleted, and opens or closes an alert if it passes the threshold. In production mark `Not run`. In the lab choose a single search **with no threshold** and run it only once.

**Action:**

- In the search's detail press the “Run now” button (“Running…”).

**Expected:**

- A new row with the trigger `MANUAL` is added at the top of the run history; the status is first `RUNNING`, then `COMPLETED`.
- If the Ariel pool is full the run does not start and the sentence appears with 503: “the Ariel search budget is full ({held} in flight); waited {waited} s; nothing was sent to QRadar” (MSG-8b). For another failure: “The run did not start. Reload the page and try again.”; without permission: “Your role cannot run a stored search (permission search:execute).”

**Record:** Whether the new row appeared; the result count. Do not run more than one search.

**Cleanup:** The row cannot be deleted. Write the time in the Note field.

#### MT-98 — Storing a new search (writes) lab only

**Precondition:** **lab only**: there is no search delete in the interface; a stored search stays in the catalog even if disabled, and enters the list the analysts see.

**Action:**

- Open the `/searches/new` page (“New search”). Write a name, AQL and a schedule; press the “Save as disabled” button.

**Expected:**

- The search is saved disabled (“A new search is saved disabled. Run it from its page to preview the result; only the saved query ever runs.”). For invalid AQL, reasons appear under the heading “Validation reasons” as English sentences (MSG-1b).

**Record:** Whether it was saved; whether the invalid AQL reasons are English.

**Cleanup:** It cannot be deleted; disable the search.

### 10. History backfill

The page is at `/admin/history-backfill` (“History backfill”); reading needs `read:history_backfill`, changing the decision needs `admin:history_backfill`. History backfill is for learning only; the buckets read are not judged. The default of `HISTORY_BACKFILL_ENABLED` is `true`. The reading sends read-only searches to QRadar and stays within the 72-hour budget (`docs/OPERATIONS.md`, the section on history backfill).

#### MT-99 — History backfill page: decision block and table

**Precondition:** An administrator account.

**When to look:** The scope rows are updated at the next collection of the family concerned; that family's own cadence + 3 minutes (the "Timing and total duration" table above, from the “Next run (UTC)” column of `/admin/search-cadence`): with the default cadences 8 minutes at most for Log source volume, Catch-all senders and Rule matches, and 18 minutes for WAF collection and IPS collection. A family whose collection switch is off does not run, and its scope rows do not move; the family's section on this page says which switch.

**Action:**

- Open the `/admin/history-backfill` page.

**Expected:**

- The `h1` is “History backfill”. For each enabled console a section “Console {name}”: the decision line (“History backfill: Yes{days}. Recorded by {by} on ‹time›.”, “History backfill: No, history is not read. Recorded by {by} on ‹time›.” or “History backfill: not decided. Nothing is read from QRadar's past.”) and the table.
- The table columns are “Family”, “Scope”, “State”, “Window asked”, “Window proved”, “Read”, “Searches”, “Read through”, “Reason”, “Later Yes”. The families are “Source volume”, “WAF”, “IPS”, “Catch-all senders”, “Rule matches”.
- The state words include “Waiting”, “Reading”, “Complete”, “Partial”, “Unavailable”, “Not eligible”. A number that was not read is shown as a dash, not as zero.
- The sentences in the “Reason” column are English sentences from the catalog for every family (catch-all included) (MSG-8a); the parts of a sentence start with a lower-case letter and are joined with semicolons. Examples: “the scan reached the end of the window”; “the reading reached the end, but QRadar holds less than asked or a gap exists”; “reached the end; {problems}” (inner parts: “QRadar holds less than asked for {cut} of {total}”, “{sources} had no event in the window”); “window reduced from {asked} to {days} days: {limits}”; “waiting for search budget”; the gap row “search failed {failures} times at the minimum width”; catch-all: “no catch-all source had an event in the window; nothing is written”. Numbers are grouped with commas and durations have the form "about X hours". The “Later Yes” column: “possible”, “not possible: this source already collected”, “not needed: this source has a history scope”.
- If the scope was deferred because the Ariel pool was busy, the sentence is `deferred: no Ariel permit within N s` (MSG-8c). The scope rewrites its reason at every step.
- If there is no console: “No QRadar console is enabled.”; if there is no permission: “Your role cannot read it (permission read:history_backfill). Nothing was changed.”

**Record:** That the page opened; the state of the decision (Yes / No / not decided); a summary of the family states; that one reason sentence is English and complete.

#### MT-100 — History backfill: the first-open question

**Precondition:** The console's decision has never been recorded (the dialog opened in section 3).

**Action:**

- In the question dialog choose the “Decide later” button. Read the strip at the top of the page.

**Expected:**

- With a day count the title of the dialog is “Read the last 28 days from QRadar?”; 28 is the `HISTORY_BACKFILL_DAYS` default, and if the site has a different value, that number is shown. Without a day count the short form “Read QRadar's past?” is shown.
- When “Decide later” is chosen the dialog closes, no decision is recorded, and the strip “History backfill is not decided. Nothing is read from QRadar's past until it is. Open History backfill” appears on the page. If the dialog never opened, the decision is already recorded: this check is `Not run`.

**Record:** Whether the dialog appeared; whether "Decide later" was chosen.

#### MT-101 — Read history now / Stop reading (writes) lab only QRadar search

**Precondition:** **lab only**: these buttons make each family send read-only searches to QRadar at its next collection (spends capacity) and start or stop the reading; in production mark `Not run`.

**Action:**

- Press the “Read history now” button in the decision block, and confirm in the “Read history now?” dialog (“Confirm: Read history now”). Then stop it in the same place with “Stop reading”.

**Expected:**

- “Recorded. ” appears. The rows do not change at once; each family applies it at its next collection. On stopping, the scopes that are reading end “Partial” and the reason names who stopped it (“stopped by {by}”); after "Read history now", a scope that is topping up what is missing says “read history now: topping up”. The recorded messages: “Read history now is recorded. Each family applies it at its next collection; no scope has changed yet.” / “Stop reading is recorded. Each family ends its reading scopes at its next collection; no scope has changed yet.” On an account without permission: “Not changed. Your role cannot change the history backfill (permission {permission}).”

**Record:** The notice of the two steps; the state of the row at the next cadence.

**Cleanup:** As a next step "Read history now" restarts the reading; a 'Yes' decision is never reversed, the reading can be stopped.

### 11. Rule health, statistics, coverage and fleet

All these pages read data collected from QRadar; none has a write operation.

#### MT-102 — Rule health list

**When to look:** The rule health evaluation is its own beat task, not part of a cadence family and not run with the rule sync; it reads only what the collectors have stored. It runs every 30 minutes (`RULE_HEALTH_EVALUATION_INTERVAL_SECONDS`, default 1800 s), and its first run after the upgrade comes one interval, 30 minutes, after beat starts.

**Action:**

- Open the `/rules` page. Try the health filter (“Filter by health status”).

**Expected:**

- The `h1` is “Rule health”; the columns are “Rule”, “Type”, “Enabled”, “Health”, “Meaning”, “Last fired (UTC)”, “Offenses”. The health words: `HEALTHY`, `NEVER OBSERVED`, `INACTIVE`, `NOISY`, `DISABLED`, `DEPENDENCY DEGRADED`, `INSUFFICIENT DATA`, `UNKNOWN`.
- Under the name of every rule that is neither healthy nor unknown the reason for the verdict is written; if no reason was recorded, “No reason recorded”. The reason sentence is an English sentence (MSG-3); it is rewritten at every rule evaluation, so after the first evaluation all rows show English.
- If there are rules with no verdict, the notice “{count} rules have no health verdict yet.” appears; this is not a fault.

**Record:** That the list opened; that one reason is English.

#### MT-103 — Rule detail and health history

**Precondition:** At least one rule has been collected.

**Action:**

- Open a rule from the list (`/rules/<id>`).

**Expected:**

- The `h1` is the rule's name, and the browser tab reads “Rule · Behavior Analytics”; the sections are “Health”, “Health evidence”, “Metadata”, “MITRE techniques”, “Health history”. In the health history table every row has a “Reason” cell.
- If there is no benchmark, the capacity history section (“Capacity history”) says “QRadar has not re-benchmarked this rule since collection began.”; this is not a fault.
- Below the health blocks the section “Match anomalies” (SOC-6e) asks “Is this rule's match rate being judged, and is there an open change?”. With rule-match collection off (the default) its “Status” is “Not collected” with its reason and “Incidents” says why nothing is judged; it never reads as healthy. MT-170 reads it in full.

**Record:** That the page opened; the reason of one history row.

#### MT-104 — Rule statistics

**Action:**

- Open the `/rules/statistics` page. Change the day selection (one value) (“Measurement days”).

**Expected:**

- The `h1` is “Rule statistics”. The sections: “Rule composition”, “Most matched”, “Silent enabled rules”, “Costliest by benchmark”, “Capacity history”. When the measurement window is complete it says “Complete.”, when partial “PARTIAL.”, when insufficient “INSUFFICIENT_DATA.”; an incomplete window is not evidence of silence (“Silence was not judged.”).
- The cost unit: “Enabled rules ordered from the lowest capacity benchmark, which is the costliest to evaluate. Unit: {unit}.” The silent-rule exclusion reason and the measurement window sentence are English sentences (MSG-2). No number is drawn as zero: a section that cannot be measured shows a heading such as “No complete window exists to measure rule matches.”
- If no QRadar instance is registered: “No QRadar instance is registered.”
- The block “Rule-match anomalies” (SOC-6e) is there with its own state (MT-170): with collection off, “Rule-match collection has not run for this span.” and no table.

**Record:** That the page opened; the state of the measurement window (complete/partial/insufficient).

#### MT-170 — Rule-match anomalies: statistics, rule detail and the anomaly list (SOC-6d, SOC-6e)

**Precondition:** Signed in with `read:anomalies`. Rule-match collection is off in the defaults (`RULE_MATCH_COLLECTION_ENABLED=false`, MT-144), so a site that has not turned it on sees the "not collected" branch everywhere and the rows part is `Not run`. This check does not turn it on.

**Action:**

- Open `/rules/statistics` and read the block “Rule-match anomalies”. Open a rule (`/rules/<id>`) and read “Match anomalies”. Open `/anomalies`, choose “Rule” under “Subject kind” and press “Filter”. If the site has a rule-match incident, open it.

**Expected:**

- On `/rules/statistics` the block “Rule-match anomalies” asks “Which rules changed match rate this span?”. With collection off it says “Rule-match collection has not run for this span.” and “No match window was recorded, so no rule was compared with its usual rate. This is not a count of zero.”, with no table. A partly measured span says “Match rates were not fully measured over this span.” and “Silence is not judged over a gappy span. …”. A complete span with no incident says “No rule-match anomalies in this span.” (`frontend/messages/en/rules.json`, `matchBlock`). Its table has “Rule”, “Detector” (“Rule silence”, “Rule storm”), “Observed (matches / window)”, “Expected (matches / window)”, “State”, “Started (UTC)” and “Incident”; a truncated window says “Truncated window: at least this many matches, not an exact count.” A failed read says “Match anomalies could not be loaded.” and the other blocks are unaffected.
- On a rule's page the section “Match anomalies” asks “Is this rule's match rate being judged, and is there an open change?” and its “Status” is “Judged”, “Not collected”, “Not provable”, “Disabled”, “Monitoring off”, “Modified”, “Truncated” or “Insufficient data”, each with its reason; “Incidents” says “No open or recent match anomaly is stored for this rule.” or, for a rule that is not judged, “No incident is stored for this rule. That is not a judgment: the status above says why the match rate is not judged.” (`matchSection`).
- “Rule” is a “Subject kind” option on `/anomalies`; the chip reads “Subject: Rule” and, with collection off, the list says “No anomalies match these filters. This is a filtered view, not a statement about the fleet.” A rule incident's detail (`h1` the rule's name, description “QRadar rule ‹id›”) shows “Evidence” with “Detector”, “State”, “Observed”, “Expected”, “Expected range”, and the sentence “Matched events were above the usual count for this weekday and slot: ‹observed› against ‹expected› expected per window.” (storm) or “The rule had no match in complete windows where ‹expected› per window was expected.” (silence), and the link “Open the rule and its match-anomaly section”. The evidence area says “Evidence collection does not apply to rule-match anomalies”.
- `GET /api/v1/behavior/detectors` lists `RULE_SILENCE` and `RULE_STORM` with the status `NOT_ENABLED` and the setting `RULE_MATCH_COLLECTION_ENABLED` while the setting is off (`backend/app/anomaly/capabilities.py`). Rule incidents open no alert unless `RULE_ALERTING_ENABLED=true`.

**Record:** The state of the block on `/rules/statistics`, the “Status” of one rule's section, and the list result for “Rule”.

#### MT-105 — Coverage

**Action:**

- Open the `/coverage` page.

**Expected:**

- The `h1` is “Detection Coverage” (the route sets no tab title of its own). If there is no mapping: “Coverage has not been evaluated.” and “No ATT&CK technique mappings are configured, so coverage cannot be measured. An administrator adds them. This is not a finding of zero coverage.” If there is a mapping: the summary “{total} techniques evaluated · {covered} covered · {explicit} explicit / {inferred} inferred mappings · generated ‹time›” and the “Techniques” table with the columns “Technique”, “Name”, “Tactic”, “Status”, “Rules”, “Score”, “Evidence”. The status words: `COVERED`, `PARTIAL`, `DEGRADED`, `MISSING`, `NOT EVALUATED`.
- The summary sentences in the “Evidence” cells are English sentences (MSG-2) and are rewritten at the coverage sync.

**Record:** That the page opened; that one evidence sentence is English.

#### MT-106 — Fleet, fleet sources and catch-all senders

**Action:**

- Open in this order: `/fleet`, `/fleet/sources`, `/fleet/catchall`.

**Expected:**

- `/fleet`: “Log-source fleet”; the class table and the “Review queue”; the window line “Last {window}: ‹start› to ‹end›, in {minutes}-minute buckets.” An incomplete window is written with the number of partial/missing buckets (“Events and mean EPS count complete buckets only. Where the window is incomplete, the row says how many buckets are partial or missing.”).
- `/fleet/sources`: “Fleet sources”; new and ambiguously classified sources with the class given and the reason.
- `/fleet/catchall`: “Catch-all senders”; if the inventory has no catch-all source, “No catch-all source is in the inventory.”

**Record:** That the three pages opened.

#### MT-107 — Log sources and detail

**Action:**

- Open the `/log-sources` page, open one source.

**Expected:**

- The `h1` is “Log Sources” (the route sets no tab title of its own); “{count} sources.”; the columns are “Name”, “Type”, “Criticality”, “Status”, “Anomalies”, “Last Event”. The `h1` of the detail is the log source's name; “Last event: ‹time›”; if there is no event, “Last event: never”.

**Record:** That the pages opened; the number of sources.

#### MT-108 — Configuration changes

**Action:**

- Type the address `/config-changes` (it is not in the navigation).

**Expected:**

- The `h1` is “Configuration changes” (the route sets no tab title of its own); “Configuration changes are not collected.” and “A data model for them exists, but no collector or API fills it, so this page has nothing to show.” This is the "not collected" honesty state: the data is not collected, so no empty list or zero is shown.

**Record:** That the page shows this text.

#### MT-109 — Behavior pages: overview, sources, WAF, property watches, offenses

**When to look:** The property watch status reason becomes an English sentence at the watch's next collection; one cadence + 3 min.

**Action:**

- Open in this order: `/behavior`, `/behavior/sources` (open one source), `/behavior/waf` (open the endpoint page of one source), `/behavior/watches` (open one watch), `/offenses` (open one offense).

**Expected:**

- `/behavior`: “Behavioral overview”; the top statistics “Open anomalies”, “Silent sources”, “Insufficient data” (“Not being judged — a zero above does not cover these.”).
- `/behavior/sources`: “Sources”; the source detail “Source behavior”; a source that is learning shows “Still learning”.
- `/behavior/waf`: “WAF behavior”; the WAF source page “WAF source endpoints”; a source that is not collected shows “Not collected”. The pause reasons (MSG-4a) are English sentences. `/behavior/ips` (“IPS behavior”) is MT-167.
- `/behavior/watches`: “Property watches”; the columns “Status and reason”, “Scope”, “Open incidents”. If collection is off, “COLLECTION OFF.”; in shadow mode, “SHADOW MODE.” The status reasons are English sentences (MSG-4b) and are rewritten at every collection; for a watch that is reading history, the history state (“Reading history: {progress}”) appears.
- `/offenses`: “Offenses”; if there is no offense, “No offense snapshot has been collected yet.” or “No offenses have been collected from QRadar yet, so this list is empty. Offense collection is set up by an administrator.”; read-only (“This page issues no writes: offenses are triaged in QRadar, not here.”).

**Record:** That the five pages opened; if there is no watch, `Not run` for that part.

#### MT-167 — IPS behavior: the source list and one IPS source (IPS-UI-1, IPS-UI-2, IPS-API-1)

**Precondition:** Signed in with `read:anomalies`. The production IPS sources are Cisco Firepower/FTD and Palo Alto Threat. IPS collection is off until it is set up and a source has a valid profile (MT-111); on a site with none only the empty branch can be seen and the rest is `Not run`.

**Action:**

- Open Behavior → “IPS behavior” (`/behavior/ips`). Read the line above the table and every column. Follow a source's name. On the source page read the chart, “Action mix” and “Entities”; change “Range” (“24 hours”, “7 days”), “Series” and the “Entity dimension”. Follow the count under “Active anomalies” of a source that has some.

**Expected:**

- The `h1` is “IPS behavior” (the tab “IPS behavior · Behavior Analytics”) and the description “Times are UTC.” (`frontend/messages/en/ips.json`, `behavior`). The lead line says “‹n› of ‹m› sources are collected and judged; the others produce no verdicts.” and, when some judged source has active anomalies, “‹k› judged sources have active anomalies.” Sources that need attention are first.
- The columns are “Source”, “Validation”, “Latest bucket” (“UTC, events, search dimensions”), “Active entities” (“of ceiling, per dimension”), “Admission overflow” (“latest bucket”) and “Active anomalies” (“with worst severity”). A source with no bucket says “Not collected yet”, never `0` events. A dimension's measurement is “Exact”, “Lower bound”, “Not collected”, “Failed” or “Rejected”. Overflow is “None”, “Overflowing”, “At the ceiling” or “Not measured” (“no entity dimension was measured in this bucket”).
- “Active anomalies” is a count with the worst severity and a link to the anomalies of that source, “None”, or “Not judged” (“no verdicts are produced for this source, so it has no anomaly count”). A source that is not judged never shows a count or a `0`. “Profile settings” opens `/admin/ips-sources/<id>`.
- No profile at all: “No IPS source has a behavior profile.” and “Nothing is collected or judged until a log source is mapped and validated in IPS sources administration. An empty list here is not a statement that IPS traffic is normal.” Without `read:anomalies`: “You do not have permission to view IPS behavior. Ask an administrator for the read:anomalies permission. Nothing was changed.”
- The source page (`h1` “IPS source”) shows “Source activity” with the series “Total events”, “Top source IP share”, “Port fan-out” and “Blocked share” against an expected range (no range is drawn for total events: “No expected range is drawn for total events: the detectors judge the entities and the three other series, not the source total.”), “Action mix” with “Blocked”, “Allowed” and “Unmapped” (or “Action not mapped on this source, or not measured in this range: …”) and “Entities” with “Entity”, “First seen”, “Last seen (UTC)”, “Tracking”, “Baseline cells” and “State”. A source that is not collected has the banner “This source is not collected: its profile is disabled or not valid for the current version. Buckets shown were collected earlier; nothing new is being judged.” A missing bucket is a gap, not a zero (“No bucket was stored for this source in this range. This is an absence of collection, not an observation of zero events.”). An id that is not an enabled IPS source says “IPS source not found.” with “Open IPS behavior”.

**Record:** The lead line, one source's row (validation, latest bucket, active anomalies) and one source page's state.

#### MT-168 — EMA overlays on the charts: off by default, display only (CHART-1, CHART-2, UIQ-3)

**Precondition:** Signed in with `read:anomalies`. A log source with a few hours of history (`/behavior/sources/<id>`) and the Dashboard.

**Action:**

- Open a source on `/behavior/sources`. Above the volume chart find “EMA” with the buttons “3”, “5”, “10” and “20”. Press “5”, then “20”, reload the page, then open the same page in a private window. Press them off again. Open the same control on a WAF source chart (`/behavior/waf/sources/<id>`), an IPS source (MT-167) and a property watch (`/behavior/watches/<id>`) when the site has them. On the Dashboard open the “Fleet EPS” widget in its expanded view (its tile draws no averages).

**Expected:**

- All four buttons are off on first load (`aria-pressed` false, the accessible names “EMA 3”, “EMA 5”, “EMA 10”, “EMA 20”) and the chart shows only the observed line and the expected range. A pressed button adds one line, with a legend entry “EMA ‹period› · ‹span›” (for example the span of 5 buckets in minutes or hours) and a line in the tooltip “EMA ‹period›: ‹value›” (`frontend/messages/en/behavior.json`, `chart.ema`). A line is drawn only where there are enough consecutive buckets (“not enough consecutive buckets”) and stops at a bucket that was not fully observed.
- Under the chart: “EMA lines are drawn in your browser from the observed series. They do not take part in detection; a line stops at a bucket that was not fully observed.” and “Moving-average overlays on: ‹periods›. Each stops at a bucket that was not fully observed.” On a chart that draws several values the note says “EMA lines are drawn for one value at a time: select a single value.”
- The choice is remembered in the browser (key `qradar-dash.chart.ema`) and is the same on every chart; the private window starts with all four off. Turning an average on changes no anomaly, no expected range and no stored value; the Dashboard tile (not expanded) draws no average whatever is stored.

**Record:** Whether all four started off, whether the choice survived the reload, and on which charts you tried it.

### 12. Administration

In this section the pages are first only **viewed**. Every change is a **(writes)** check and its undo is written; in production the change checks are `lab only`. No administration page writes to QRadar. The search cadence, outage policy and classification rule pages are only read in production.

#### MT-110 — WAF sources: list and profile

**Action:**

- Open the `/admin/waf-sources` page; open the profile page of one source (`/admin/waf-sources/<id>`). **Do not start a validation, do not save anything.**

**Expected:**

- The list `h1` is “WAF sources”; the columns are “Source”, “Product”, “Mapping”, “Validation”, “Collection”, “Last validated (UTC)”. The validation labels: `VALID`, `STALE`, `INVALID`, `INCONCLUSIVE`, `UNVALIDATED`. A source that has never been validated shows “never validated” (a dash; it is not a failed validation).
- The collection cell of a paused source carries a reason sentence (for example “profile version {profile_version} has never been validated”, “WAF collection is disabled (WAF_COLLECTION_ENABLED=false)”); these sentences are coded by MSG-4a. The profile page has the sections “Field mapping”, “Validation”, “Collection”, “History”.
- If there is no WAF profile: “No log source is configured as a WAF source.”

**Record:** That the list and the profile opened; that one pause reason is English (if there is one).

#### MT-111 — IPS sources: list and profile

**Precondition:** The production IPS sources: Cisco Firepower/FTD and Palo Alto Threat.

**Action:**

- Open the `/admin/ips-sources` page; open the profile of one source (`/admin/ips-sources/<id>`). **Do not start a validation, do not save anything.**

**Expected:**

- The `h1` is “IPS sources”; the columns are “Source”, “Profile”, “Validation”, “Enabled”, “Collection”, “Last validated (UTC)”. The profile page's `h1` is the log source's name (its browser tab reads “IPS source · Behavior Analytics”); the sections are “Field mapping”, “Validation”, “Collection”, “History”.
- The reasons of the validation report and the field details (for example “QRadar does not accept this field as named.”, “The field exists but no event in the window carried a usable value.”) and the eligibility reasons are English sentences (MSG-4a). If there is no profile: “No IPS source has a profile yet.” IPS operations need `admin:ips` (`admin:waf` does not grant it).

**Record:** That the list and the profile opened; that one reason is English.

#### MT-112 — Re-running a source validation (writes) lab only QRadar search

**Precondition:** **lab only**: “Validate again” re-runs the validation, sends read-only probe searches to QRadar and changes the profile's validation result; this can stop a source's collection. In production mark `Not run`.

**Action:**

- In the lab, press the “Validate again” button on a WAF or IPS profile.

**Expected:**

- “Validation of version {version} has been requested.”; shortly after, the validation result and the reason sentences are updated (English).

**Record:** The result label.

**Cleanup:** The earlier result is kept or replaced by the new one; it cannot be undone. In the lab this does not matter.

#### MT-113 — Users and directory (view only)

**Action:**

- Open the `/admin/users` and `/admin/directory` pages. **Do not run the connection test for the directory** (it needs a real password) and do not save anything.

**Expected:**

- `/admin/users`: “Users”; the columns “Username”, “Source”, “Roles”, “Status”, “Last login (UTC)”. Times are UTC.
- `/admin/directory`: “Directory”; the form “Directory configuration”; the “Test connection” section: “The username and password are sent with the form above and then cleared. They are not saved.”

**Record:** That the two pages opened.

#### MT-114 — Search cadence (view only)

**Action:**

- Open the `/admin/search-cadence` page.

**Expected:**

- The `h1` is “Search cadence”; the columns are “Family”, “Cadence”, “Bucket width”, “Worst-case detection delay”, “Last run (UTC)”, “Next run (UTC)”, “Searches in last run”. Nine families are listed: “Log source volume”, “WAF collection”, “Anomaly explanations”, “Rule matches”, “IPS collection”, “QRadar health”, “Catch-all senders”, “Geography” and “Property watches” (`frontend/src/lib/searchCadence.ts`, `FAMILY_INFO`; the order is the backend's). For a family that has never run, a dash or “Not run since upgrade”/“not counted”; these are not zero. A family whose last run failed says why its next run is not now (MT-169).

**Record:** The cadence of each of the nine families (5 min, 15 min…).

#### MT-169 — Search cadence: “Bucket width” and the reason of a failed run (FIX-44, FIX-44-ui) (view only)

**Precondition:** Signed in with `read:search_cadence`. The sentence in the second part appears only for a family whose last run failed; a healthy site has none, and that part is then `Not run` (do not stop a collector to make one).

**Action:**

- Open `/admin/search-cadence`. Read the column heads and the notes under the table. Find the row of a family whose last run failed, if any, and read its “Next run (UTC)” cell.

**Expected:**

- The column is “Bucket width”, not “Bucket”, and the notes read “Cadence changes when a family searches, not its bucket width.” and “Bucket width is the span of one stored bucket, not how often the family searches; a dash means the family has no buckets.” (`frontend/messages/en/cadence.json`, `table`). A dash is a family with no buckets, not a zero.
- The “Next run (UTC)” cell of a family whose last run failed says “Not retried after the failed run.” and the backend's sentence “The run at ‹time› failed; the family runs again at its next ‹cadence› slot, ‹time›, and is not retried before it.” (code `search_cadence.next_dispatch.after_failed_run`; `frontend/messages/en/backend.json`). The next time shown is the family's next slot, not “now”.
- A family that is off shows a dash in “Next run (UTC)”; “This family is off, so it has no next run.” is the dash's hover text (`title`), not text printed in the cell (`frontend/src/components/cadence/SearchCadenceTable.tsx`; `nextRunOff` in `frontend/messages/en/cadence.json`). Known issue in 0.1.16 (FIX-48): when an off family still holds an old failed run (for example “Rule matches” with `RULE_MATCH_COLLECTION_ENABLED` unset on a site that once ran it), its cell also prints “Not retried after the failed run.” under the dash, although the family will not run at all. Record that as the known issue, not a Fail; the dash itself is the pass condition for an off family.

**Record:** The column head, and for a failed family the cell's sentence; `Not run` for that part if none failed. For an off family: the dash and, if the sentence “Not retried after the failed run.” also shows, “known issue FIX-48”.

#### MT-115 — Changing the search cadence (writes) lab only

**Precondition:** **lab only**: the cadence changes how often searches are sent to QRadar; in production `Not run`.

**Action:**

- Change the cadence of one family to a preset and save; then change it back to the old value.

**Expected:**

- The message “Saved. Changed by {user} at ‹time›.” or “Saved. Changed at ‹time›.”; if there is no permission, “Your role cannot change search cadence (permission admin:search_cadence).”

**Record:** Whether the change was saved and whether it was undone.

**Cleanup:** Change it back to the old value; two rows stay in the audit log.

#### MT-116 — Outage policy and classification rules (view only)

**Action:**

- Open the `/admin/outage-policy` and `/admin/classification-rules` pages.

**Expected:**

- `/admin/outage-policy`: “Outage alerts”; "Record only" and the classes that alert. `/admin/classification-rules`: “Classification rules”; built-in rules cannot be edited, only switched on or off (message: “a built-in rule can only be enabled or disabled”).

**Record:** That the two pages opened.

#### MT-117 — Administration home page

**Action:**

- Open the “Administration overview” item in the Administration group. The address is `/admin`.

**Expected:**

- The `h1` is “Administration”; the description is “Settings that control collection, source classification and outage alerting.” The item is called “Administration overview” and is the first item of the Administration group.

**Record:** That the page opened.

#### MT-158 — Alert policy page (view only)

**Precondition:** Signed in with an account that holds `read:alerts`. The page can write (add, edit, remove a policy row, with `admin:outage`); this check does not, because a policy row changes which alerts the site raises.

**Action:**

- Open Administration → “Alert policy” (`/admin/alert-policy`). Read the page from top to bottom.
- If the site has an account without `admin:outage`, open the page with it.

**Expected:**

- The `h1` is “Alert policy”; the description is “Which sources and classes page a human, which are only recorded, and which go into the daily digest.” (`frontend/messages/en/alertPolicy.json`). The Administration overview (MT-117) has a matching “Alert policy” entry.
- “Daily digest” comes first: “Due” (“Daily at ‹hour› UTC”), “Recipients now” (“‹n› addresses”), “Last run” with its state and sentence. The states are “Delivered” (“The digest listed ‹n› sources and went to ‹m› recipients.”), “Nothing to send”, “Not e-mailed” (“‹n› sources have digest incidents that are not e-mailed; e-mail is not configured.” or “… no recipient is set to receive alert e-mail.”) and “Delivery failed”; the last two carry a link to where it is fixed. A table per console appears only with more than one console.
- “What a mode does” explains “Alert” (raises an alert, as before), “Record only” (records the incident and raises no alert) and “Digest” (records the incident and lists it in the daily e-mail), then “A source or class with no row is ALERT. A family whose alerting switch is off raises nothing, whatever its policy.” and “The most specific row applies: a source over its class, one console over all consoles, one detector over all detectors.”
- “Policies” is a table with “Governs”, “Family”, “Detector”, “Mode”, “Last changed (UTC)” and “Actions”; without rows it says “Every source alerts as before (default ALERT).” The three mode labels share one neutral style. A row for a `NO_EVENTS` silence carries the note that a record-only outage policy wins (“Outage alerts” link).
- With `admin:outage` the page offers “Add policy” and, per row, “Edit” and “Remove”; without it the table is read-only and says “Changing a policy needs the admin:outage permission. This table is read-only for your account.” A viewer without `read:alerts` reads “You need the read:alerts permission to see the alert policy. Ask an administrator to grant it.”
- “Quiet periods” follows the policies (MT-157): every active quiet period and every active maintenance window, or “Nothing is quiet. Every condition alerts by its policy.”

**Record:** The digest state and recipient count, the number of policy rows and the number of quiet periods.

#### MT-130 — SIEM load page, command and `qradar_health` (ARL-1, ARL-2, ARL-3)

**Precondition:** Upgrade complete (MT-08) and one cadence has passed (MT-11), so the platform has sent searches. Signed in with an account that has `admin:*`. This check sends only GET requests to QRadar (`/system/about`, the managed hosts, the deployment hosts, the license pool and the list of search ids); no search is run for it.

**Action:**

- Open Administration → "SIEM load" (`/admin/siem-load`). Read "Our load on QRadar" and the "QRadar" panel first, then "Searches in flight" and "Load by caller". Pick each window (1 h, 24 h, 7 d, 30 d; `?range=1h`, `24h`, `7d`, `30d`). Set Refresh in the top bar to 1 min or 5 min and watch the "As of" time change.
- On the host, read the same facts without the page:

```bash
docker compose exec -T backend python -m app.cli.ariel_load --current
docker compose exec -T backend python -m app.cli.ariel_load --hours 24
./qradar-dash doctor
```

- With a role that lacks `admin:*`, open the page.

**Expected:**

- The page shows the time it was read (UTC). "Our load on QRadar" shows searches in flight (active and stale), permit slots held against the cap (or "Not enforced"), and the last 5, 15 and 60 minutes with submitted, completed, failed and refused counts and wall-time p50 and p95. A dash means no search completed; it is not a zero. If no search was submitted in the last 60 minutes it says so.
- Each console in the "QRadar" panel shows Reachable and API latency, Version, Managed hosts ("ACTIVE n of N", with the ones that are not ACTIVE listed), EPS (average and peak against the allocation), License pool (an over-allocated pool is flagged), and Ariel with "Search ids held". "Held" is what QRadar keeps for all users, finished or active; it is not a count of running searches. A field that was not read shows its state (Not collected, Forbidden or Failed) and a reason, never a number and never "healthy". A cached answer says how old it is and when QRadar is asked again (30 s by default).
- A summary window with no recorded search says so instead of drawing a row of zeros. Caller names are readable ("Log source volume", "Scheduled searches"); a caller the page does not know is shown as its code.
- The command prints "QRadar health" per console with the same figures. `--current` asks QRadar afresh each time. Neither prints a token, a query or a result row.
- `doctor` shows `qradar_health` (runbook OPS-R67 on a WARN). PASS has the host count, the EPS share and the number of held search ids. WARN means a host is not ACTIVE, average EPS is over 90 % of the allocation, or the license pool is over-allocated. On a lab CE console the license pool is always over-allocated, so WARN is the expected result there and is not a Fail; write it in the Note field. SKIP means the console did not answer (see `qradar_auth`) or nothing could be read.
- The role without `admin:*` sees the forbidden notice naming `admin:*`, and no figure.
- In the support bundle (MT-04) the archive holds `ariel-load.json`, or `ariel-load.error.txt` when it could not be read.

**Record:** Whether each part of the page read; the console's reachability, latency and `qradar_health` status (no addresses); whether the page and the command agree on searches in flight; the forbidden notice.

#### MT-133 — QRadar's health in detail on the SIEM load page and `doctor` (HLT-1 to HLT-3)

**Precondition:** Upgrade complete (MT-08) and the `QRADAR_HEALTH` family has stored at least two windows: two cycles of its cadence (15 minutes by default, see `/admin/search-cadence`) plus the settle time, so about 40 minutes after the console first answered. The console has the shipped custom properties `Metric ID`, `Value` and `Element` (the `watermarks` line of `doctor` says so when it does not). Signed in with an account that has `admin:*`. The check sends two aggregating Ariel searches per console per cycle (the family's own, nothing extra) and GET requests only; it changes nothing.

**Action:**

- Open Administration → "SIEM load" (`/admin/siem-load`) and scroll to the console's section ("{console}: hosts, health and trends"). Read, in this order: "Managed hosts", "Disk, system and pipeline", "System notifications, last 24 h", "Trends". In "Trends" pick "24 h" and "7 d".
- In QRadar's own UI, open System and License Management and compare the managed host's status, average and peak EPS and the EPS allocation with the table. For one disk figure, open the same host's Health Metrics in Log Activity (log source type "Health Metrics", property "Metric ID" `DiskUsage`) or ask the console administrator for `df` of `/store`.
- On the host:

```bash
./qradar-dash doctor | grep -E 'qradar_health|qradar_disk|qradar_pipeline|qradar_access'
docker compose exec -T backend python -m app.cli.ariel_load --current
```

- Switch the family off, as the check "family off" below says, only if the site allows a recreate of the application containers (**lab only**; in production write `Not run`): set `QRADAR_HEALTH_COLLECTION_ENABLED=false` in `.env`, run `docker compose up -d --force-recreate backend celery-worker celery-beat`, read the page and `./qradar-dash doctor`, then set the line back to `true` and recreate again.

**Expected:**

- "Managed hosts": one row per managed host with its role (Console or Managed, from the host's component list), appliance and version, status, and the EPS and FPM of the previous month (average / peak, allocated of hardware limit). The figures equal QRadar's own. A host with an HA pair is one row group (MT-135). A dash means QRadar gave no figure; it is not a zero.
- "Disk, system and pipeline": the window is stated in UTC ("Window … to …, one window per cadence, collected at …"; the cadence is the family's own, 15 min by default), then one row per host: the fullest mount (only the mounts of `QRADAR_HEALTH_DISK_MOUNTS`; no container overlay, `/dev`, `/run` or `/sys` mount appears), CPU, memory, CRE queue, throttle spill and busiest stage. Every figure except the busiest stage is the maximum in that window and equals what the console's Health Metrics hold for the same window. The mount shows "{size} free" only when a `DiskSpaceTotal` is known: QRadar sends it once an hour, and the page carries the newest total of the same console, host and mount from a window that ended at most 2 hours earlier ("total as of hh:mm UTC"). On a console that has just booted, or a site where `QRADAR_HEALTH` has just started, no total has arrived yet and the free space is a dash, with the used share still shown; the first figure can take up to about an hour (measured: the first window was collected at 10:30 UTC and the first total arrived with the 11:00 UTC window). Look again after the next full hour. A dash that stays beyond that while the family keeps collecting is written in the Note field. The "Busiest stage (avg % busy)" figure is the window average of `RunnableRatio` (D-43): the check is that the stage shown is the one with the highest `AVG(RunnableRatio)` per stage in the console's own Health Metrics for that window, and the figure equals that average. A figure the console does not send (for example `HostRunningQueries` on a lab console) is "Not collected", never a zero.
- "System notifications": the groups of the last 24 hours (host, notification, events, severity 0 to 10, last seen in UTC). The counts equal the "System Notification" events of QRadar for the same hours, `Information Message` left out. If the family has collected fewer hours than asked, the page says "Only … of the last 24 h were collected, so these counts can be low." An empty answer is "none" only when the console has an enabled `System Notification` log source: the page then says "QRadar logged no system notification in the last 24 h, other than information messages." With no enabled source the part is "Not collected" with the sentence "The console has no enabled System Notification log source, so an empty notification answer does not show that nothing happened."; when the stored log-source inventory could not be read it is "Not collected" with "The stored log-source inventory could not be read, so an empty notification answer was not taken as none." Either state is a reading about what could be proved, not a Fail; write which one in the Note field. It clears on its own once the inventory holds an enabled `System Notification` source (or the inventory read succeeds) and a window is collected after that. The family's “Last run” outcome on `/admin/search-cadence` reads “Failed” on that window (`backend/app/collectors/qradar_health_collector.py:257-260`, `:116-125`): the run counts any `NOT_COLLECTED` part as a partial failure, so this is the same state seen from the run, not a second fault; by MT-11 a failed run is judged by its “Next run (UTC)”, not by the word alone.
- "Trends": reachability and API latency, and the fullest mount, CRE queue and throttle spill per host, each axis with its unit and time in UTC. A break in a line is a stretch with no stored window. With less than 7 days stored, the "7 d" chart is short; that is not a fault. No raw key and no zero for a missing figure on the page.
- `doctor`: `qradar_disk` and `qradar_pipeline` read the latest stored window: PASS or a named WARN with the runbook (OPS-R69, OPS-R70). The `qradar_disk` PASS line shows `(<size> free)` after the share only once a total is known; before the first hourly total it shows the share alone (`<host> <mount> <share> %`), which is not a fault. A window that is missing, failed, partial or stale is a WARN that says `not collected since <UTC time>`; it is never a PASS. `qradar_health` (OPS-R67) reads the console afresh. On a lab CE console it WARNs permanently because the license pool is over-allocated.
- With the family off: `qradar_disk` and `qradar_pipeline` are `SKIP` with "QRadar health collection is off", `families_off` lists `QRADAR_HEALTH_COLLECTION_ENABLED=off`, and `watermarks` does not name the family (it does not FAIL on its old row; a `watermarks` FAIL for another family, for example `geo_countries_source` on a console without the country property, is a different finding). The page keeps showing the last stored window with its own start, end and collection time, and above the stored figures, between "Managed hosts" and "Disk, system and pipeline", it says that collection is off ("QRadar health collection is off (QRADAR_HEALTH_COLLECTION_ENABLED). Disk, system, pipeline, notifications and trends show the last window collected, at <time> UTC."; with no window ever collected it says "... show no window: none was ever collected."). The figures turn "Stale" after three cadences plus the settle time (about 48 minutes with the defaults). No figure reads as zero. After switching it on, the next window brings the figures back.

**Record:** The window shown (UTC); for the host, the fullest mount and its share, CPU, memory, CRE queue, throttle spill against what QRadar shows (give the pairs); the number of notification groups against QRadar's count (or the notification part's state and its reason sentence when it is not "none"); the status of the four `doctor` lines (the text of any WARN); whether the free space showed; the result of the family-off step or `Not run`.

#### MT-134 — The access check: which QRadar APIs the token can reach (ACC-1, ACC-2)

**Precondition:** Upgrade complete (MT-08). Signed in with an account that has `admin:*`. The check sends one GET with a one-item range to each API of the catalog and one Ariel search, `SELECT starttime FROM events LIMIT 1 LAST 1 MINUTES`, that it cancels unread. It writes nothing to QRadar. The platform runs it at start-up (once per start, in the worker), every `QRADAR_ACCESS_CHECK_INTERVAL_SECONDS` (6 hours by default) and when an administrator asks.

**Action:**

- Open Administration → "QRadar access" (`/admin/qradar-access`). Read the verdict line and the table for the console: one row per API with its status, HTTP code, when it was checked and what it feeds here.
- Press "Check now", or send the request. The response is `202` at once; the check runs in the worker and takes a few seconds:

```bash
curl -s --cookie "$SESSION" https://<host>/api/v1/admin/qradar-access | head -c 600
curl -s -X POST --cookie "$SESSION" -H "Origin: https://<host>" https://<host>/api/v1/admin/qradar-access/check
```

- Send the request three times within a minute (the interface: press "Check now" three times).
- On the host: `./qradar-dash doctor | grep qradar_access`.
- In QRadar's Log Activity (or `GET /ariel/searches`), look for the probe: a search with the text above.

**Expected:**

- The verdict says "Every API the application uses is reachable with this token." when the token has the rights, and the table has 14 rows, each "Reachable" with a 2xx code. A refused API reads "Forbidden" (403), a missing one "Not found" (404), a rejected token "Token not accepted" (401), one that did not answer "Did not answer", and one never asked "Not checked yet"; each row says what it feeds. While the console's Ariel is not up (for example just after a restart) the Ariel rows read "Error" (HTTP 503) and clear at the next check.
- The response of the check is `202` with `check_running` true; the page ("A check is running. This page reads the result every … s.") and `GET /api/v1/admin/qradar-access` show `check_running` true for a few seconds and then false, with the times of the last start and finish. The third request within a minute is refused with `429` ("Too many checks were requested. Try again in … s."): the limit is `RATE_LIMIT_ACCESS_CHECK`, `2/minute` by default. That is not a failure.
- `doctor` `qradar_access` is `PASS` (`<n> APIs reachable, last checked <UTC time>`), or a WARN naming each refused, missing or unanswered API with what it feeds, or FAIL when the token is rejected (OPS-R71). A result older than twice the interval is a WARN.
- The probe search stays in QRadar's list until QRadar's own retention removes it, because it had already completed when the cancel was sent. It adds one id to "Search ids held" per check and is not a failure. The probe is recorded: on the SIEM load page (`/admin/siem-load`), "Load by caller" lists one search per check under "Access check" (the row is "canceled", with no record count). It never selects a payload.
- The check changes no data in QRadar and no setting of the platform.

**Record:** The number of entries and the status of each one that is not `ok` (with its HTTP code); the time of the last check; whether the third request gave `429`; the `doctor` line; whether the probe search was found.

#### MT-135 — HA pairs on the SIEM load page (production only; needs a console with an HA pair)

**Precondition:** A site whose QRadar deployment has at least one managed host with an HA pair (a primary and a secondary). The lab console is one host without HA, so this check has never run against a real pair: the platform's HA reading is proven with fixtures only (HLT-1, HLT-3), and this check is where it meets QRadar. Signed in with `admin:*`; access to QRadar's own Administration tab (Deployment Editor or System and License Management) on the same site, as a person with the QRadar administrator role. The platform sends only GET requests for this check (`/config/deployment/hosts` and `/system/servers`); do not change the pair, do not trigger a failover, and do not set a member offline to make a state appear. Run it on the state the site is in.

**Action:**

- In QRadar, open System and License Management and note, for every HA pair, the primary and the secondary host name and the **status** QRadar shows for each member (QRadar's own table shows them in its Status column), and the EPS allocation of the managed host. If a pair exists in a state other than the usual one (a failover in the past, a member offline for maintenance), note that too.
- Open `/admin/siem-load` and read the "QRadar" panel and the "Managed hosts" table of the same console. Compare, pair by pair:
  - the managed-host line: "ACTIVE n of N" counts managed hosts, and a pair counts once; the line says "m HA pairs, k not healthy" or "m HA pairs, all healthy";
  - the table: the pair is one row group with both members, each with its role ("Primary", "Secondary") and its status;
  - the EPS and FPM of the pair against QRadar's allocation of the managed host.
- On the host, read the same facts without the page:

```bash
docker compose exec -T backend python -m app.cli.ariel_load --current
./qradar-dash doctor
```

- Read the raw status strings the API returns, so a status the page did not recognise can be written down exactly (the strings are statuses of servers, not secrets; no address is needed):

```bash
curl -s --cookie "$SESSION" https://<host>/api/v1/admin/ariel-load/current
```

**Expected:**

- A **healthy pair** (primary `ACTIVE`, secondary `STANDBY`) reads "Healthy pair" with "The primary is active and the secondary is on standby, as expected." The standby member reads "Standby, as expected". It is not listed as "not ACTIVE", it is not a WARN, and `doctor` `qradar_health` is `PASS` with "1 HA pair(s)" in its facts.
- A pair that **failed over** (the secondary `ACTIVE`, the primary not `ACTIVE`) reads "Failed over" with "The secondary is running this host and the primary is not." and each member's status as QRadar reports it. `doctor` `qradar_health` is `WARN` and names the host and both statuses, primary first. This is a fact to tell the QRadar administrator, not a platform fault; it stays until QRadar returns the pair to normal.
- A **degraded pair** (one member `ACTIVE`, the other in a documented state that is not `STANDBY`: for example `OFFLINE`, `FAILED`, `SYNCHRONIZING`) reads "Degraded pair" with "One member is active, but the other is not on standby, so no failover is possible."; `doctor` is `WARN`. A pair with **no `ACTIVE` member** reads "Failed pair" and `doctor` is `FAIL` (runbook OPS-R67).
- **Statuses.** The platform normalises a status to upper case with words joined by `_` and then compares it with the statuses IBM documents for HA hosts (QRadar High Availability Guide, "Status of HA hosts"): `ACTIVE`, `STANDBY`, `FAILED`, `SYNCHRONIZING`, `ONLINE`, `OFFLINE`, `RESTORING`, `NEEDS_LICENSE`, `SETTING_OFFLINE`, `SETTING_ONLINE`, `NEEDS_UPGRADE`, `UPGRADING` (so QRadar's "Needs License" and "Setting Offline" read the same as `NEEDS_LICENSE` and `SETTING_OFFLINE`). Any other string is **not guessed at**: the pair reads "Unknown pair state", the page shows the string exactly as QRadar wrote it ("QRadar reported ..., which this platform does not classify, so the pair is not read as healthy."), and `doctor` is `WARN`. An unknown string on a pair that QRadar shows as healthy is a finding for this check (a spelling the platform does not know yet): write the exact string, both members' roles and what QRadar's own table shows. Do not edit anything.
- **EPS is counted once per pair.** The EPS and FPM of the pair row equal QRadar's allocation, average and peak of the managed host, not twice that figure; the "EPS allocated" total of the console panel does not change when the pair has a second member.
- Disk, system and pipeline figures are per member (each member has its own disks and its own Health Metrics log source), so a pair shows two sets of figures.
- The two members' names on the page are the ones QRadar shows. If the page cannot match the members to the managed host (for example the server list was not read), it says so and shows the members as "HA member"; it never shows a pair as healthy on a guess.

**Record:** For each pair: QRadar's two statuses, the state the page showed (Healthy pair, Failed over, Degraded pair, Failed pair, Unknown pair state), the `qradar_health` status, whether the EPS equals QRadar's figure once, and any status string the page showed as written (copy it exactly).


#### MT-137 — Abandoned searches: the reaper's schedule and the `ABANDONED` count (ARL-5)

**Precondition:** Upgrade complete (MT-08). Signed in with an account that has `admin:*`. The check reads the beat schedule in the application's code and settings, the worker's log, and our own `ariel_search_run` table. It sends no search to QRadar and writes nothing. No step makes a worker die: killing a worker in the middle of a search loses that search's result and spends console capacity for nothing, so the closing itself is not provoked here. The check proves that the reaper is scheduled and runs, and that the page and the command report what it closed.

**Action:**

- Read the schedule the application hands to beat, and the worker's log of the last 30 minutes:

```bash
docker compose exec -T backend python -c "from app.workers.celery_app import celery_app; print(celery_app.conf.beat_schedule['reap-abandoned-ariel-searches'])"
./qradar-dash logs --since=30m celery-worker | grep reap_abandoned_ariel_searches
grep -E '^ARIEL_SEARCH_ABANDON_SCAN_SECONDS=' .env || echo "not set: the default applies"
```

- Read the searches by outcome and the searches in flight now:

```bash
docker compose exec -T backend python -m app.cli.ariel_load --hours 24
docker compose exec -T backend python -m app.cli.ariel_load --current
```

- Open Administration → "SIEM load" (`/admin/siem-load`), read "Load by caller" and the note under it.

**Expected:**

- The first command prints `{'task': 'reap_abandoned_ariel_searches', 'schedule': 600.0}`. The `schedule` is `ARIEL_SEARCH_ABANDON_SCAN_SECONDS` in seconds (default 600; the setting accepts 60 to 86400). A site that set another value in `.env` prints that value; the third command shows the line.
- The first scan comes one scan interval (600 s by default) after the worker starts, so a read right after an upgrade or a worker restart (MT-06, MT-07) shows only the task-list line `. reap_abandoned_ariel_searches` and no scan line yet (OPS-33: worker recreated 12:26:30, first scan 12:36:32, `succeeded in 0.034s: {'closed': 0}`). Read the log at least one scan interval plus one beat tick after the worker started (11 minutes with the default); count about three lines only once the worker has run 30 minutes.
- The worker's log has one line for each scan that ran in the last 30 minutes (about three with the default, once the worker has run that long). Celery writes it, names the task `reap_abandoned_ariel_searches` and ends it with the returned dictionary, `{'closed': 0}` when no search had to be closed. A line with a number above 0 is not a Fail: it says how many searches the reaper closed in that scan. No line at all in 30 minutes, with `celery_beat` PASS in `doctor`, is written in the Note field.
- `--hours 24`: in the `outcomes` column of each caller, `ABANDONED:<n>` appears only for a caller that had such a search. On a site where no worker was stopped during a search, the word is absent; that is the expected state, and it is not a zero drawn by the page. After an upgrade or a restart of the worker (MT-06, MT-07) a search that was running at that moment can be listed `ABANDONED`: write its caller and count, and the time of the restart. The row is closed only after the longest wait a search may have (`ARIEL_MAX_TIMEOUT_SECONDS` 900 s plus 30 s) plus one scan interval (600 s), so with the defaults 25 minutes and 30 seconds after it was sent, at the next scan. An `ABANDONED` count that grows with no restart is a finding: write it.
- `--current`: the line `in flight: <n> (stale, worker gone: <m>, older than <s> s)` counts in `<m>` the rows older than `<s>` (930 s with the defaults) that are not yet closed; `<m>` falls to 0 within one scan interval after they pass the closing time. A stale row that stays beyond that, with the worker log showing the scans, is written in the Note field.
- On the page, "Load by caller" counts an abandoned search under "Failed" and the note under the table says so ("Abandoned searches are counted in Failed: their worker stopped before it recorded an end, so they carry no measurement."). The percentile cells of an abandoned search stay a dash; none of its figures is drawn as a zero.

**Record:** The schedule line, the number of reaper lines in the log and the largest `closed` value among them, the `ABANDONED` counts per caller with the time of the last worker restart (or "none"), the stale count of `--current`, and whether the note under "Load by caller" is there.

#### MT-138 — Alert e-mail page (view only) (MAIL-1 to MAIL-3)

**Precondition:** Upgrade complete (MT-08). Signed in with an account that has `admin:notifications` (the administrator role has it through `admin:*`). **Do not save a template or a recipient, do not press "Send test message"** (MT-141).

**Action:**

- Open Administration → "Alert e-mail" (`/admin/email`). Read the "Relay" block first, then the tabs "Templates" and "Recipients".
- In the browser's developer tools, search the page for an input of type `password`, or run in the console `document.querySelectorAll('input[type=password]').length`.
- Read what the application reports about the relay, and the `email` line of `doctor`:

```bash
curl -s --cookie "$SESSION" https://<host>/api/v1/admin/email/settings
./qradar-dash doctor | grep -E '^(PASS|WARN|FAIL|SKIP) +email'
```

**Expected:**

- The `h1` is “Alert e-mail” (the tab reads “Alert e-mail · Behavior Analytics”); the description is “Times are UTC.” The "Relay" block lists “Status”, “Relay”, “Security”, “From”, “Login” and “CA file”. The note says “Set in .env on the server, not editable here. The password is never shown.” The page has two tabs, “Templates” and “Recipients”. The Templates tab has the table “E-mail templates” (columns “Template”, “Customised”, “Enabled”, “Sends today”, “Updated (UTC)”), the editor, the placeholders, the preview and “Send a test message”. The Recipients tab has the table “Stored recipients” (columns “Address”, “Minimum severity”, “Alert types”, “Enabled”, “Updated (UTC)”, “Actions”) and the “From .env” block for `NOTIFY_EMAIL_RECIPIENTS`.
- **No password input and no password value.** The count of `input[type=password]` is 0. The JSON of `/settings` holds `configured`, `host`, `port`, `security`, `security_source`, `from_address`, `from_name`, `username_set`, `ca_file_set`, `site_name` and `test_rate_limit`; `username_set` is only true or false. The username, the password and the CA file's path are not in it.
- **With `SMTP_HOST` empty** (the code default, and the state of a site that did not choose alert e-mail): “Status” is “Not configured” and a warning says “No alert e-mail can leave this site.” and that `SMTP_HOST` is empty; `/settings` has `"configured":false`. The `doctor` line is `SKIP  email` with the text `not configured (SMTP_HOST is empty); alerts are not mailed`. In the Templates tab, under “Send a test message”, the page says “No relay is configured, so nothing can be sent. Set SMTP_HOST in .env first.” and the send button cannot be used. Nothing is sent by this site: with no host there is no connection to open (the same refusal answers `POST /api/v1/admin/email/test` with the code `not_configured`).
- **With `SMTP_HOST` set:** “Status” is “Configured” with the note “Configured means a host is set, not that the relay answers.”; “Relay” shows `<host>:<port>` and “Security” one of “STARTTLS (connects, then upgrades)”, “SSL/TLS (encrypted from the start)” or “None (plain connection)”, with “Set by SMTP_SECURITY” or “Derived from SMTP_USE_TLS” under it. “Login” reads “Username set” or “No username (unauthenticated relay)”. The `doctor` line is `PASS  email` with `configured: relay <host>:<port>, security <mode>, <authenticated|no login> (handshake not tested; use --check-smtp)`; a username with the mode `none` is `FAIL` (MT-142).
- If no recipient is stored and `NOTIFY_EMAIL_RECIPIENTS` is empty, the Recipients tab says “No alert e-mail is sent.” and why. That is a statement of the site's configuration, not a Fail.
- No raw message key and no Turkish text on the page (section 4).

**Record:** Whether the site has `SMTP_HOST` set (yes/no), the “Status” and “Security” values, the count of password inputs, the keys of the `/settings` JSON, and the `doctor` `email` line (the host may be written; the credentials are not on the line).

#### MT-139 — Alert e-mail is refused to an account without `admin:notifications`

**Precondition:** An account that already exists and has neither `admin:notifications` nor `admin:*` (for example one with the `viewer` role). **Do not create an account for this check**: creating one is MT-16 (`lab only`); where there is no such account, write `Not run` with that reason. Sign in with it in a private window; take its session cookie from the browser for the second step.

**Action:**

- Type the address `/admin/email`. Look at the Administration group of the navigation.
- Send the request with the account's session:

```bash
curl -s -i --cookie "$SESSION" https://<host>/api/v1/admin/email/settings | head -20
```

**Expected:**

- The navigation has no “Alert e-mail” item. The page opens with the `h1` “Alert e-mail” and, in place of the content, the notice “Alert e-mail cannot be shown to this account.” with “Reading and changing it needs the admin:notifications permission. The read-only role does not include it. Nothing was changed.” No relay fact, template or recipient is on the page.
- The request answers `403` with the text `missing required permission: admin:notifications`. Every route under `/api/v1/admin/email` is refused the same way, so no template, recipient or relay setting is in the answer.

**Record:** That the item was absent, the notice's heading, the HTTP status and the permission the answer names.

#### MT-140 — `./qradar-dash doctor --check-smtp` against the site's relay

**Precondition:** `SMTP_HOST` is set in `.env` and the backend was recreated after it was set (MT-138 shows “Configured”). The relay is the site's own, reachable from the backend container. If `SMTP_HOST` is empty, run the command anyway and write `Not run` for the rest: the line is a `SKIP`. The project ships no test relay, so a lab that wants this check needs a relay the operator provides. The command opens one connection to that relay, says EHLO, finishes the STARTTLS or SSL handshake and quits: no login is attempted and no message is sent, so the check is safe in production when the site uses alert e-mail. It sends nothing to QRadar. It is not in the core, because only a site with a relay can run it.

**Action:**

```bash
./qradar-dash doctor --check-smtp | grep -E '^(PASS|WARN|FAIL|SKIP) +email'
echo "exit: ${PIPESTATUS[0]}"
```

**Expected:**

- `PASS  email` with `relay <host>:<port>, security <mode>, <authenticated|no login>: handshake completed, nothing sent`.
- A failed handshake is `WARN  email` with `...: handshake failed (<code>)` and `runbook: OPS-R68`. The code is one of `connection_refused`, `timeout`, `connection_failed`, `tls_failed` (the codes and their causes are in OPS-R68). A WARN is not a Fail of the product: write the code and have the relay's owner look at the port and the mode (587 with `starttls`, 465 with `ssl`, 25 with `none`) and, for a private CA, `SMTP_CA_FILE`. The line never holds the relay's own reply text or a credential.
- With `SMTP_HOST` empty: `SKIP  email  not configured (SMTP_HOST is empty); alerts are not mailed`.
- The same command with `--json` (`./qradar-dash doctor --check-smtp --json`) prints the same line as one JSON array and no text lines.

**Record:** The `email` line (host and port may be written; no credential is on it), the exit code, and the failure code if any.

#### MT-141 — Alert e-mail: one test message through the relay (writes) lab only

**Precondition:** **lab only**: the check sends one real e-mail through the relay to a mailbox and leaves an audit record (`email.test_send`) that cannot be deleted; in production mark `Not run`. It needs a relay and a mailbox the operator provides (the project ships none) and `SMTP_HOST` set as MT-140 says. Signed in with `admin:notifications`.

**Action:**

- Open `/admin/email`, the Templates tab. Under “Send a test message” type a mailbox you can read in “Send to” and press “Send test message”.
- Then press it five more times within the same minute.
- Read the mailbox, including its junk folder.

**Expected:**

- The page says “The relay accepted the test message.” with “The relay accepted the test message. Delivery to the mailbox is up to the relay.” and “Check the mailbox, including its junk folder. A relay accepting a message does not prove it was delivered.” The mail arrives; its subject starts with `[Test] `. The mail is a fixed sample alert (the preview's sample), never a real alert, and it has a text part and an HTML part.
- A failure is a coded sentence, never the relay's own text: for example “The relay rejected the sign-in. Check SMTP_USERNAME and SMTP_PASSWORD.” (`auth_failed`), “The connection to the relay failed. …”, “The secure connection to the relay failed. …” (OPS-R68 lists them). A failure is a finding about the relay or `.env`, to write down; it is not a defect of the page.
- The limit is `RATE_LIMIT_EMAIL_TEST`, `5/minute` by default (the note under the button says “Limited to 5/minute.”): the sixth press within a minute is refused with `429` and the page says “Too many test messages.” with “Test messages are limited to 5/minute.” and “Try again in <n> s.” That is not a failure.

**Record:** The sentence the page showed, whether the mail arrived and how its subject began, whether the sixth press was refused, and the code of any failure.

**Cleanup:** The sent mail cannot be recalled; delete it from the mailbox. Up to five `email.test_send` rows (one for each message the relay was asked to send) stay in the audit log. No template, recipient or setting was changed.

#### MT-142 — A username with `SMTP_SECURITY=none` is refused (`insecure_login`) (writes) lab only

**Precondition:** **lab only**: the check edits `.env` and recreates the application containers (a short outage), and the page and `doctor` show the fault until the cleanup. In production mark `Not run`. Take a backup of the `.env` first: `cp -p .env .env.mt142.bak` (the copy holds the site's `.env` values: keep it in the install directory, with the file's own permissions, and delete it in the cleanup). Do not type a real password anywhere: leave `SMTP_PASSWORD` empty. The mode refuses before any connection is made, so the host does not have to exist.

**Action:**

- In `.env` set the four lines below (change the lines that exist; add the ones that do not), then recreate the containers that read it:

```bash
SMTP_HOST=smtp.invalid
SMTP_PORT=25
SMTP_SECURITY=none
SMTP_USERNAME=mt142-user
```

```bash
docker compose up -d --force-recreate backend celery-worker celery-beat
./qradar-dash doctor | grep -E '^(PASS|WARN|FAIL|SKIP) +email'
echo "exit: ${PIPESTATUS[0]}"
```

- Open `/admin/email` and read the top of the page.
- Run `./qradar-dash doctor --check-smtp | grep -E '^(PASS|WARN|FAIL|SKIP) +email'`.

**Expected:**

- `FAIL  email` with `relay smtp.invalid:25, security none, authenticated: refused, the username and password would cross the network in clear text; no mail is sent (use starttls or ssl, or clear SMTP_USERNAME)` and `runbook: OPS-R68`. The exit code of `doctor` is 2.
- On the page a banner says “No alert e-mail is sent with this configuration.” and “SMTP_USERNAME is set while SMTP_SECURITY is none, so the login would cross the network in clear text. Nothing was sent. Use starttls or ssl, or clear SMTP_USERNAME for an unauthenticated relay.” “Login” reads “Username set”.
- With `--check-smtp` the line is the same `FAIL`: no connection is opened, so it does not become a handshake WARN, and no host lookup is attempted.
- No password is on the page or in any line.

**Record:** The `doctor` line and exit code, and the banner's heading.

**Cleanup:** Restore the `.env` (`mv .env.mt142.bak .env`), recreate the containers again with the same `docker compose up -d --force-recreate backend celery-worker celery-beat`, and run `./qradar-dash doctor | grep -E '^(PASS|WARN|FAIL|SKIP) +email'`: the line must be what it was before the check (MT-138). Confirm that `.env` no longer holds `smtp.invalid` or `mt142-user`: `grep -cE 'smtp.invalid|mt142-user' .env` prints 0. The refusal wrote nothing to QRadar and nothing to the database.


### 13. Guide

The in-app guide is at `/guide`; it is open to everyone who is signed in and needs no permission. Its text is rendered on the server.

#### MT-118 — The guide opens in English

**Action:**

- Open the `/guide` page and open one section (`/guide/how-it-works`).

**Expected:**

- The `h1` is “Guide”, the description is “Pick a topic by the question it answers.”, the groups are “Concepts”, “Analyst pages”, “Analyst tasks”, “Administrator tasks”; the first section is “How it works”. The section page is not empty and there is no raw key.

**Record:** That the two pages opened.

#### MT-132 — The header Guide link shows once; the Guide entry is always there (GUIDE-3)

**Precondition:** **(writes)**, on the signed-in account only: opening a page stores that page's guide section in the account's `guide_seen` list, and the interface has no way to reset it. Use a page this account has not opened before (a new account, such as the one from MT-16 in the lab, has opened none), and write the page in the Note field. Nothing else changes and no one else's pages are affected.

**Action:**

- Open a page that has a guide section and that this account has not opened before, for example `/geography`. Look at the page header, then at the bottom of the left navigation.
- Open another page and come back to the first one (or reload it). Look at the same two places.
- Open two or three other pages (for example `/fleet`, `/alerts` and `/account`) and look at the bottom of the left navigation on each. On one of them, follow the entry.
- Open `/guide` and look at the bottom of the left navigation.

**Expected:**

- First visit: the header has a small “Guide” link to the page's guide section. It stays on the page while it is open; it does not appear and then vanish.
- Second visit to the same page: the header has no “Guide” link. The same holds after a reload and, when you sign in on another browser with the same account, there too.
- Every page shows one text-only “Guide” entry at the bottom of the left navigation, below the navigation list, and it covers no navigation item (on a short window the list scrolls above it). On a page that has a guide section it opens that section (for example `/guide/geography` from `/geography`); on `/guide` it opens the index.
- No page shows the entry twice, and there is no “Guide” item inside the navigation list.

**Record:** The page used for the first visit; whether the header link showed on the first visit and not on the second; the target of the bottom-left entry on each page you looked at.

**Cleanup:** None: the list has no reset. Write the page used in the Note field.

### 14. Read-only against QRadar

Which commands send an Ariel search: plain `doctor` sends none (its `qradar_access` line reads the stored result of the last access check; it probes nothing itself); `doctor --aql` sends one per collection family query, up to 20 per console (MT-136, caller `DOCTOR_AQL`); the access check sends exactly one probe search per console per check, every 6 hours and on each "Check now" (MT-134, caller `ACCESS_CHECK`). Every search the platform sends is recorded in `ariel_search_run`.

Nothing in this tool writes to QRadar (the fourth of the four rules in `docs/OPERATIONS.md`). The provider interface (`app/providers/base.py`) has no create, update or delete method for QRadar configuration; it has only the Ariel search lifecycle methods (`create_ariel_search`, `get_ariel_search_status`, `get_ariel_search_results`, `cancel_ariel_search`). The requests the REST provider makes to QRadar are, in the code: `GET /system/about`, `GET /config/event_sources/log_source_management/log_sources`, `…/log_source_types`, `…/log_source_groups`, `GET /config/event_sources/custom_properties/…`, `GET /analytics/rules`, `GET /analytics/building_blocks`, `GET /siem/offenses`, `GET /ariel/searches/<id>` and `…/results`, and, to record an Ariel search, `POST /ariel/searches` and, to cancel its own search, `DELETE /ariel/searches/<id>`. The last two create and delete a temporary search object in QRadar; they do not create, change or delete a configuration object (rule, log source, custom property, offense, reference data).

The place of the QRadar console's audit log is not documented in this repository; the console-side check is therefore a **console administrator step**.

#### MT-119 — `doctor` shows that it makes only one kind of call to QRadar [core]

**Action:**


```bash
./qradar-dash doctor | grep -E 'qradar_tls|qradar_auth'
```


**Expected:**

- The `qradar_auth` line says `GET /system/about succeeded; QRadar <version>` (a read-only `GET`). If `qradar_tls` is PASS it says `verified certificate expires in <n> days`. Plain `doctor` does not run an Ariel search and does not write to QRadar (`doctor --aql` and the access check do send searches; see the intro of this section, MT-136 and MT-134).

**Record:** The status of the two lines and the QRadar version.

#### MT-120 — Console side: confirming that there was no write in the test window console administrator step [core]

**Precondition:** The console administrator knows the start and end times of the test (MT-05). The place of this check is not documented in this repository; the administrator uses the audit/access records of their own console. If they cannot be reached, `Not run`.

**Action:**

- Ask the console administrator to review the operations on the console for the application's API user (the authorized service/token) over the test window.

**Expected:**

- The requests in the window are only on the read (`GET`) endpoints listed above; the only writes seen are `POST /ariel/searches` and `DELETE /ariel/searches/<id>`.
- No `POST`, `PUT`, `PATCH` or `DELETE` is seen on a rule, building block, log source, log source group, custom property, reference data set, offense (note, assignment, closing, hiding), user or authorized service.
- If the permission set of the application's API user is limited to read (and Ariel), this is an additional assurance.

**Record:** The administrator's answer: no write / this operation was seen. The console records are not written on the page.

### 15. Rollback criteria and steps

**Roll back at once** (the steps in this section):

- `upgrade` exited non-zero and the automatic rollback could not return the site to its old state either (`Rollback failed; the writers and the UI are stopped. Verified backup: <path>`). OPS-R45.
- The upgrade seems successful but `doctor` after the upgrade gives a `FAIL` and the related OPS-R entry in `docs/OPERATIONS.md` does not clear it (especially `db_connect`, `migration_head`, `encryption`, `secrets_files`, `watermarks`). On a changed console, the `watermarks` FAIL until the hourly `log_source_inventory` and `rule_inventory` are retried is not this item (MT-11). On a console that has no offenses, the `watermarks` WARN for `offense_snapshot` is not a FAIL.
- Sign-in is broken: the administrator and existing users cannot get in, and `/api/v1/health/ready` is not 200.
- After the first cadence + 3 min, no family in `/admin/search-cadence` is running and `doctor` gives `celery_beat` or `celery_workers` FAIL.
- The data that 0.1.15 showed is gone: anomalies, alerts, rules, log sources or offenses are empty or missing (and the reason is not a "not collected" honesty state). On a changed console, source pages looking empty until the two hourly inventories are retried is not this item (MT-11).

**Note and continue:**

- A stray raw key or an untranslated sentence on a page (section 4): the related check is written Fail and handed over, but this is not a reason to roll back.
- Small visual flaws: a cut-off in a cell, alignment.
- A widget showing "stale" before the wait (cadence + 3 min) is over.
- Checks that are `Not run` because the data is not ready (no WAF/IPS anomaly).

**Important:** When something is Fail, **before** the rollback save `./qradar-dash support-bundle` and `./qradar-dash logs --tail=2000 backend`. The rollback leaves the data written after the upgrade in the broken copy (`qradar_obs_broken_<utc>`). The Alembic downgrade is never used; the rollback is the restore of a backup.

#### MT-121 — Evaluating the rollback criteria [core]

**Action:**

- Look at the "Roll back at once" list above and write whether any of these items was observed during the test.

**Expected:**

- None was observed.

**Record:** Whether any of the "Roll back at once" items was observed (yes/no); if yes, which one and the decision.

#### MT-122 — Rollback (only if needed) (writes)

**Precondition:** One of the criteria above was observed and a decision was made. If it is not needed, `Not run`. The support bundle has been taken.

**Action:**


```bash
./qradar-dash support-bundle
./qradar-dash rollback
```

- The command writes `Rollback replaces the live database with <path>; data written since is kept only in the broken copy.` and waits for `Type rollback to continue: `; type `rollback`.
- When it is finished, return the checkout to the running version. The script on disk is still 0.1.16 before the checkout; `install` is not run before it. Then verify:

```bash
git checkout v0.1.15
./qradar-dash status
curl -s http://127.0.0.1:8000/api/v1/version
./qradar-dash doctor
```

- The answer file must have come back from the pre-upgrade backup recorded in MT-08. Do not read its contents. Then run the install again on the old checkout:

```bash
BACKUP=<the pre-upgrade path from MT-08>
cmp "$BACKUP/install.answers" .secrets/install.answers
echo "cmp exit: $?"
stat -c '%a' .secrets/install.answers
./qradar-dash install --non-interactive .secrets/install.answers
echo "install exit: $?"
```


**Expected:**

- The line `Rollback complete.`; exit code 0; `PASS  migration_head database and image are at 0060` (once). The rollback restores the pre-upgrade backup and runs no downgrade, so the database is the one the backup holds; its head is `0060`, the head the site had **before** the upgrade, not the `0063` it had after it: the three tables 0.1.16 adds (`entity_mention`, `entity_mention_backfill`, `shift_handover`) are gone with the data written since (REL-15e saw the same for the 0.1.15 tables). The command also prints `Previous database kept as qradar_obs_broken_<utc>.` and the `DROP DATABASE` command for later; do not run it yet.
- This backup holds `install.answers`. The rollback puts the file back and writes no line for it. The `Removed answer key` line is not expected. That line appears for an old backup that has no copy: `Removed answer key <name> (unknown to 0.1.15).` This upgrade's own backup is not that case.
- `cmp` prints nothing and exits with 0. `stat` prints `600`; `400` if the backup was `400`.
- `status`: `Installed version: 0.1.15`, `Last upgrade: rolled_back, 0.1.15 -> 0.1.16 at <utc>` (the pair names the upgrade that was undone); `/api/v1/version`: `"version":"0.1.15"` and `"migration_head":"0060"`. Sign-in works. `doctor`: no `FAIL` when the console answers; on an unreachable console `qradar_tls` and `qradar_auth` and the `watermarks` / `errors_recent` lines that follow them are FAIL (the `install` item below explains this).
- `install` exits with 0 when the console answers. The summary ends with the `Install time` line, followed by a line made of `=` signs. There is no separate “Install complete” line. There is no `unknown key` in the output. The summary line `QRadar connection  FAILED` is the install where the console did not answer and the exit was 1. The rollback check is Pass on that exit too: if the summary was reached (the `Install time` line and then the `=` line were written), if the output has no `unknown key` and no `install refused`, and if the `qradar_tls` and `qradar_auth` lines of `doctor` show the same console access fault. On an unreachable console both lines are FAIL; the `runbook:` value at the end of the line carries the runbook of console access (`runbook: OPS-R4` for `qradar_tls`, `runbook: OPS-R1` for `qradar_auth`). An exit of 1 that ends with the line `API readiness  NOT READY`, or while the general origin check failed (`public URL <address> failed`), is Fail. If the checkout was forgotten the script refuses: `install refused: this site runs 0.1.15 and this checkout is 0.1.16; use ./qradar-dash upgrade 0.1.16 to change versions, or check out v0.1.15 to re-run install (see docs/OPERATIONS.md, …)` (the message ends with the name of the upgrade section of `docs/OPERATIONS.md`). No container changes version on this refusal; run `git checkout v0.1.15` first.
- `install` exits with 2 and writes `[FAIL] the answers are refused; nothing was written:`, followed by one line per refused answer in the form `<KEY>: <reason>` (for example `QRADAR_TOKEN_FILE: file not readable: <path>`), when the answers file names a file that is not there or not readable (the QRadar token, the CA certificate, the TLS certificate and key, or the registry token) or holds any other value the script refuses. The script has checked the answers before it touched anything: no file, container or setting was changed, the rollback that came before it stands, and this is not a fault of the rollback. Put the named file back at the path the answers file gives (the answers file holds paths, never the secret itself, and the rollback does not unpack `secrets.tar`), or correct that line in `.secrets/install.answers` (mode `0600`), and run the `install` command again. Write the refused lines (the key and the reason, no secret) in the Note field; the `install` exit and the `QRadar connection` result in the Record field are then `2` and `not reached`, and the install item is `Not run` until the repeat has exited with 0 or 1 as above.
- The script writes the name of the broken database copy (`qradar_obs_broken_<utc>`) and the exact command to delete it later; do not rush to **delete** it.
- If the rollback fails its own health check, the writers stop and `Rollback failed; the writers and the UI are stopped. Verified backup: <path>` is written (OPS-R45); repeat `./qradar-dash rollback` or use `./qradar-dash restore <path>`.

**Record:** The exit code of the rollback, the `Rollback complete.` line, `Installed version`, `migration_head`, the `cmp` exit, the digits of the `stat` mode, the `install` exit and the `QRadar connection` result in the summary. The contents of the answer file are not written.

**Cleanup:** None; a rollback is a rollback.

#### MT-123 — Rollback rehearsal (writes) lab only

**Precondition:** **lab only**: this is not done on the production site.

**Action:**

- On the lab VM, after the upgrade, run the rollback steps of MT-122. The checkout is `v0.1.15` in this step and `./qradar-dash install --non-interactive .secrets/install.answers` exits with 0 if the console answers (the condition is in the `install` item of MT-122). MT-16…19 must not have been done yet. Then upgrade again:

```bash
git fetch --tags
git checkout v0.1.16
./qradar-dash upgrade 0.1.16
```

- MT-16, MT-17, MT-18 and MT-19 are done after this re-upgrade.

**Expected:**

- After the rollback the head is `0060`, the head of the restored backup. The second upgrade prints two `PASS` pairs, `0060` and then `0063`, and the three `Running upgrade` lines of MT-06 between them: the restored database has none of the three migrations, so they run again. The last line is `Upgrade complete: 0.1.15 -> 0.1.16.`. The `Last upgrade:` line shows the latest operation.
- When the checkout is not `v0.1.16`, `upgrade 0.1.16` is refused: `upgrade refused: this checkout is version 0.1.15, not 0.1.16; run git fetch --tags && git checkout v0.1.16 first (see docs/OPERATIONS.md, …)` (the message ends with the name of the upgrade section of `docs/OPERATIONS.md`). No container stops.
- The rollback removes the accounts created after the upgrade, and with them everything the lab checks MT-146, MT-155 and MT-157 wrote (triage rows, the queue marker, quiet periods). Do those after the re-upgrade, or expect them to be gone.

**Record:** The two results.

**Cleanup:** The rehearsal leaves a broken copy; in the lab it can be deleted (the command the script wrote).

### 16. Delivery of the results

The results table is the file `docs/MANUAL-TEST-RESULTS.md`; it is filled in on paper or on a copy of the file.

#### MT-124 — Removed in 0.1.10 (D-41: the interface is English only)

The check was "Language, round C": switching the language back to English and re-opening the routes of the `Language:` checks. There is no language to switch back from. The routes are opened in sections 4 to 13 in English. Nothing to run; record `Removed`.

#### MT-125 — Fill in the results page and hand it over [core]

**Action:**

- Fill in the header of the page and every row: `Pass`, `Fail` or `Not run`; write a Note for `Not run` and `Fail`. If a check is Fail, attach the related support bundle and the output of `logs --tail=2000 backend`.

**Expected:**

- There is no empty result cell on the page. These are **written nowhere**: passwords, tokens, `.env` values, `ENCRYPTION_KEY`, the QRadar token, the contents of `env.bak` and `secrets.tar`, user names, customer data (log source names, IP addresses, URL paths; more than the check asks). Use a placeholder for the page if needed (`<site>`, `https://<host>`).

**Record:** The delivery time (UTC) and the files delivered.
