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

1. [0.1.10](#0110)

---

## 0.1.10

This section upgrades a 0.1.9 site to 0.1.10 and tests the upgraded site. A 0.1.8 site upgrades to 0.1.9 first; that path is in `docs/releases/0.1.9.md` and is not described here. The migration head is `0044` before the upgrade and `0047` after it (`migration_head` in `/api/v1/version`). The production IPS sources are Cisco Firepower/FTD and Palo Alto Threat.

### What this release changes

- **The interface is English only (LANG-1, LANG-2, LANG-3; decision D-41).** The language control on `/account` is gone, and a `ba_locale=tr` cookie or a `ui_language = "tr"` account is shown English. The checks of section 4 that tested the Turkish interface or the language switch were removed or rewritten as English checks (ids unchanged). New check: MT-131.
- **The map's country comes from a QRadar custom property (GEO-3).** `GEO_COUNTRY_SOURCE` defaults to `property`; `GEO_SOURCE_COUNTRY_PROPERTY` defaults to `Source Geographic Location` and `GEO_DESTINATION_COUNTRY_PROPERTY` to empty. The operator sets the exact property name from the console. An empty name means that direction is not collected. `doctor` gains `geo_country_source` (runbook OPS-R30). New check: MT-129.
- **The world map (GEO-4, GEO-5).** The "SOC Overview" template has the Geography widget (`map.geography`) in its bottom row, and `/geography` shows the same answer at full size; a stale answer says so. Check: MT-128.
- **SIEM load (ARL-1, ARL-2, ARL-3).** Every Ariel search the platform sends is recorded (table `ariel_search_run`, kept 30 days by default). The page `/admin/siem-load`, `GET /api/v1/admin/ariel-load` and `/current`, and `python -m app.cli.ariel_load [--current] [--json]` show the load and QRadar's own health (GET requests only). `doctor` gains `qradar_health` (OPS-R67), and the support bundle carries `ariel-load.json`. New check: MT-130.
- **Three migrations, head `0047`.** `0045_rule_match_window_start_index` adds an index on `rule_match_window.window_start` (FIX-14); `0046_ariel_search_run` adds the table `ariel_search_run` (ARL-1); `0047_user_guide_seen` adds the column `app_user.guide_seen` (GUIDE-3). `upgrade` prints the decision pair first with `0044` and then with `0047`, and Alembic prints three `Running upgrade` lines. The only row change is the new column: every account gets an empty list.
- **Every counted response carries the rate-limit window (FIX-14).** A counted 401, 403, 404 or 422 carries `X-RateLimit-Limit`, `X-RateLimit-Remaining` and `X-RateLimit-Reset`; `Retry-After` is written only on a 429. Checks: MT-126 (production) and MT-127 (lab only).
- **A console's first inventory is queued, and `QRADAR_CONSOLE_FQDN` may carry a port (FIX-13).** The installer summary prints `First inventory    queued` or `First inventory    not queued; the hourly beat will catch up`; the second is not an error.
- **Editors and names (POL-8 to POL-14, MSG-9).** The custom rule editor uses words and "?" help; "New combination" says why it is off; the add-source control sits in the page header; a failed session shows "The server said: …" as its own clause; "Phase A" is now "Log source volume" (`doctor` token `LOG_SOURCE_VOLUME`).
- **The page-header "Guide" link shows once, and a fixed "Guide" entry stays in the left navigation (GUIDE-3).** The link shows the first time an account opens a page of a guide section and not again (the account stores it, `guide_seen`); the entry at the bottom of the left navigation is on every page and opens the current page's section. New check: MT-132.
- **`doctor --aql` tries every collection family's real query once (AQL-1).** The opt-in flag sends each family's query to every REST console and says per family whether QRadar accepts it (`aql_<family>`, runbook OPS-R72). Plain `doctor` sends no search. New check: MT-133.
- **There is no user delete (GUIDE-2).** Disabling an account is how access is removed; the account row and the `user.disable` audit record stay and the account's sessions are revoked (MT-19).
- **Before you start:** get the exact name of the country custom property from the QRadar administrator, and decide `GEO_COUNTRY_SOURCE` (`property` with that name, or `lookup`), so that MT-09 and MT-11 read a clean `geo_country_source` line.

### How to use this guide

- **In production only the core is run.** The 38 checks that carry `[core]` in their heading are the production path; the full list is for the lab run (MT-1b) and in production is done only if the operator has the time. The only core checks that write (writes) are the ones the preconditions and the upgrade need (backup, support bundle, upgrade); there is no `lab only` check in the core. MT-127 is `lab only`.
- The order of the checks is the order of the sections. Each check has the fields **Precondition**, **Action**, **Expected** and **Record**. Write the result on the results page as `Pass`, `Fail` or `Not run`; write a Note for `Fail` and `Not run`.
- **(writes)**: the check changes the application's state (it writes a file, an account, a record). Each one has a **Cleanup** step.
- **lab only**: the check changes the state that the analysts see or leaves a trace that cannot be deleted; in production it is written `Not run`, with the reason given in the check. The orchestrator runs the same guide to the letter on a lab VM (MT-1b).
- **QRadar search**: the check sends an Ariel search to QRadar and spends console capacity. Three checks in this test are like that and all three are `lab only`.
- **console administrator step**: the check is done by the administrator of the QRadar console.
- `Not run`: the check could not be done (for example the site has no data of that kind). "No data" is not a Fail; write the reason in the Note field.
- A raw message key (a string of the form `namespace.key`) or Turkish text anywhere in the interface is a Fail of the check where it was seen (section 4).
- **“The `h1`” and “the browser tab”.** The `h1` is the page's one level-1 heading, the title the analyst reads at the top of the page. The browser tab shows the page's `<title>`, which is “{page title} · Behavior Analytics”; a check names the tab only where it differs from the `h1`. The `h1` of a detail page is the name of what it shows (a log source, a rule, an alert's title), not a fixed word; the fixed word of such a page is its tab title. Three routes set no tab title of their own (`/coverage`, `/log-sources` with its detail, `/config-changes`); their tab reads only “Behavior Analytics”.
- No secret is written in any step (section 16). Do not put passwords and `.env` values on the page or in a ticket.
- SELinux is never turned off and `:z` is never used in any step.

### Timing and total duration

After the upgrade, collection restarts at the cadence of the families. The default cadence is 5 minutes for every family; for WAF collection and IPS collection it is 15 minutes (decision D-28). The site's own values are on the `/admin/search-cadence` page; an administrator can set any family to 5, 15 or 30 minutes, or 1, 2, 4 or 8 hours. Four of the eight families are switched off in the code defaults (Rule matches, Property watches, WAF collection, IPS collection); a family that is off does not run, and the `families_off` line of `doctor` says which are on.
A family runs when beat's one-minute tick finds an unclaimed slot. Slots are aligned to the clock (a 5-minute family has slots on :00, :05, :10 …), and a slot opens after the settle margin. The first run after the restart is therefore the next slot boundary after the claimed one + the settle margin, seen at the next beat tick: at most the family's own cadence + 3 minutes after the upgrade. If a whole slot has already passed (the stack was down for longer than one cadence), the first run is at the first beat tick after beat starts, about one minute. The “Last run (UTC)” time is written when the run ends, so a run that has to catch up after an outage adds its own duration; Catch-all senders and Geography take at most the cadence's buckets plus `COLLECTION_MAX_BACKFILL_INTERVALS` of missing buckets per run, and the rest follow in the next runs. Not every collector is a cadence family: `offense_snapshot` (default 5 min), `log_source_inventory` and `rule_inventory` (default 1 h) are plain beat intervals, and beat's first run of such a task comes one interval after beat starts.
For the data of a collection to appear on a page, the cadence and the settle margin (`SEARCH_CADENCE_SETTLE_SECONDS`, default 120 s) must pass; `doctor` also gives a 60 s margin. So:

| What | When to look | What the page shows if you look earlier |
|---|---|---|
| Version, migration, `doctor` (independent lines), sign-in, guide, administration pages | At once | (not data dependent) |
| Dashboard widgets, anomaly and fleet data, rule and coverage reasons | The family's own cadence + 3 min, from the “Next run (UTC)” column of `/admin/search-cadence`. With the default cadences: 8 min for Log source volume, Anomaly explanations, Rule matches, Property watches, Catch-all senders and Geography; 18 min for WAF collection and IPS collection. A family set to a longer cadence waits that long (1 hour: up to 1 h 3 min); a family that is off does not run | “Stale” or “Not collected”; this is correct, do not write Fail |
| New sentence rows (property watch reason, custom rule reason, rule health reason, history backfill reason) | After the next run of the collection that writes the row | The stored previous text |
| A new transition, a new anomaly, a new alert | When an event happens; there is no waiting | The older rows |

**Core (production path):** 38 checks, about **2 hours** of active work (an estimate, not measured) and about 10 minutes of the console administrator (MT-120); the cadence wait is separate. **Production order:** the core checks are done in section order. MT-126, although its number is high, is done right after MT-11; MT-128 is also done after MT-11, once a `GEO_COLLECTION` cadence has passed. The cadence wait (18 minutes at most) is filled with read-only checks that need no new data, such as MT-12 to MT-22; the data-dependent checks (MT-24 and after) are done when the wait is over.

Full list: about **5.5 hours** of active work (an estimate, not measured), not counting the waits. By section: Preconditions 20 min, Upgrade 20 min (the outage is inside the window), Sign-in 20 min, Language 45 min, Dashboard 40 min, Anomalies 30 min, Alerts 10 min, Custom rules 15 min, Searches 15 min, History backfill 10 min, Rules/coverage/fleet 35 min, Administration 25 min, Guide 10 min, Read-only against QRadar 15 min (the console administrator is separate), Rollback criteria 5 min; MT-129 to MT-132 add about 20 minutes, MT-133 about 5 more.
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

- `status`: `Installed version: 0.1.9`; `API ready: HTTP 200 (http://127.0.0.1:8000/api/v1/health/ready)`; every service in the `docker compose ps` table is running and healthy; the `Last upgrade:` line is present; exit code 0.
- `/api/v1/version`: `"version":"0.1.9"` and `"migration_head":"0044"`.
- `/api/v1/health/ready`: `"status":"ready"`, `"database":"ok"`, `"revision":"0044"`.

**Record:** The `Installed version` line, the `Last upgrade:` line, and the `git_sha` and `migration_head` values.

#### MT-02 — `doctor` is green before the upgrade [core]

**Action:**


```bash
./qradar-dash doctor
echo "exit: $?"
```


**Expected:**

- No line is `FAIL`. Exit code 0 (all PASS) or 1 (at least one WARN, no FAIL). If it is 2, the upgrade is not done; read the line's `runbook: OPS-R<n>` entry. The one exception is a leftover failure of a collector, described in the next item.
- **A leftover failure of a collector (`watermarks`, `errors_recent`).** This is how `doctor` decides, and it does not look at the age of the last run. `watermarks` is `FAIL` when any collector's row holds a `last_error` or three or more consecutive failures (a row stalled at the page ceiling counts as a stall, not as a failure), and `WARN` when the row has no error but its lag is more than twice its cadence or it is stalled. `errors_recent` is `WARN` for one or two rows with a failure and `FAIL` for three or more. A row is cleared only when that collector next finishes a clean run: the families of `/admin/search-cadence` at their next slot (cadence + 3 min), `offense_snapshot` at its own interval (default 5 min), and the two inventories `log_source_inventory` and `rule_inventory` once an hour, counted from the start of beat. So a healthy site that was powered off, or whose QRadar console was down or still starting, can show `FAIL` for collectors such as `catchall_senders`, `rule_match`, `geo_countries_source`, `geo_countries_destination`, `offense_snapshot`, `log_source_inventory` and `rule_inventory`, because the failure was recorded earlier and nothing has run since. The row of a family that is switched off (see `families_off`) is not judged and is named after `not judged (family off):`, so it does not raise the exit code; nothing is cleared, because that collector never runs again, and switching the family on again judges the row again, and its next clean run clears it. The rule: when `qradar_auth` is `PASS` (the console answers now) and the only `FAIL` lines are `watermarks` and `errors_recent`, write the collector names (the text of the `watermarks` line) and the time in the Note field; this alone does not stop the upgrade, which neither runs nor resets these collectors, and the rows are read again in MT-09 and MT-11. Stop and tell the owner when `qradar_auth` or `qradar_tls` is `FAIL`, when any other line is `FAIL`, or when the same row is still failing after its collector's next run (OPS-R30).
- On a healthy site these lines may be `WARN` and do not block the upgrade; write each one and its cause in the Note field: `backup_age` (no backup, the age cannot be read, or older than 26 hours; when the backup timer is not installed or is off, even a fresh backup gives a WARN — the `backup_age` row of the doctor table in `docs/OPERATIONS.md`, which has no OPS-R id; the next check fixes the backup, not the timer), `disk_docker` and `disk_backup` (70% to 89% full), `qradar_tls` (the certificate expires in under 30 days), `cadence`, `errors_recent` (one or two rows with a failure), `anomaly_evaluation`, `rule_match_coverage`, `ariel_permits` (the pool is full). These are true before the upgrade as well.
- On a console that has no offenses, a `watermarks` `offense_snapshot` line does not block the upgrade. `consecutive_failures` is 0 and `last_error` is empty. 0.1.9 moves this watermark to the time of the run on a clean run; if no clean run has happened yet, the lag can stay at the `offense_max_backfill_hours` floor (default 168 hours) and exceed twice the cadence. This is a WARN, not a Fail; write it in the Note field. The line's runbook is OPS-R30. The 0.1.9 `doctor` output has the stalled sentence and OPS-R66 (MT-09).
- If any of these is `WARN`, stop and tell the owner: `selinux` (`SELinux is permissive`: do not touch SELinux), `host_clock`. `families_off` is always PASS. A `SKIP` line says a feature is off and does not raise the exit code.
- Line format: `PASS  db_connect         database accepted SELECT 1`. At this stage `db_connect`, `migration_head` (`database and image are at 0044`), `containers`, `celery_workers`, `celery_beat`, `qradar_auth`, `encryption` and `secrets_files` must be PASS.

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
- `MANIFEST` has `VERSION=0.1.9`, `ALEMBIC_VERSION=0044`, `STATE_JSON=included` and a `DATABASE_SIZE_BYTES=<number>` line.

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
- The archive holds `doctor.txt`, `version.txt`, `compose-ps.txt`, `watermarks.txt`, `cadence.txt`, `config.env` and `logs/`. The 0.1.10 script adds one more member: `ariel-load.json`, or `ariel-load.error.txt` when the load summary could not be read (it is the error file when the backend is still 0.1.9, which has no `app.cli.ariel_load`). Either one is correct. This check runs on the 0.1.9 checkout, whose script writes neither; a bundle taken with the 0.1.10 checkout (for example in MT-122) has one of them. A part that could not be read is replaced by `<part>.error.txt`; write each such file in the Note field. The archive does not hold `database.dump`, `env.bak`, `secrets.tar` or `.secrets/`.

**Record:** The full path of the support bundle (not its contents).

**Cleanup:** If no check is Fail at the end of the test, delete this file: `rm "$BUNDLE"`. If a check is Fail, keep it and hand it over as in section 16.

**Note:** If something fails, take a new `support-bundle` **before** the rollback and keep the output of `logs --tail=2000 backend`. The rollback keeps the logs and the broken database copy, but the context of the logs can be lost.

#### MT-05 — The window and the people to inform [core]

**Action:**

- Before you start, write these at the top of the page: the start and end of the maintenance window (UTC), the upgrade path (online or offline bundle), the person who will decide on a rollback, the QRadar console administrator (for section 14), and whether the analysts have been told.

**Expected:**

- During the upgrade the backend, worker, beat, frontend and proxy stop; analysts see a connection error. For the duration, see section 2.

**Record:** The start and end of the window (UTC), the name of the path, and the person or role who was told.

### 2. Upgrade 0.1.9 → 0.1.10

A fresh browser has no `ba_locale` cookie, and the sign-in page shows the `h1` “Sign in” (the browser tab reads “Sign in · Behavior Analytics”) and the version line `API version 0.1.9`. The interface is English only (D-41); a `ba_locale` cookie, whatever its value, does not change it.

There are two ways to upgrade the site from 0.1.9 to 0.1.10; choose one. The target version works the same way as the "Upgrade" and "Offline bundle" sections of `docs/OPERATIONS.md`. The upgrade takes its own verified `pre-upgrade` backup; the backup is also restore-tested. Before the upgrade the migration head is `0044`, and after it `0047`. The head moves once.

**Outage.** The backend, worker and beat are down from the start of `pg_dump` until `/api/v1/health/ready` returns 200; `frontend` and `proxy` are stopped too. On a small database the upgrade outage was measured at about 15-20 s; the budget for a database under 5 GiB is 10 minutes. Pulling the images is outside this window. These are not promises. The upgrade empties Redis (`FLUSHALL`); queued tasks are lost and are set up again at the next beat tick, and the important data is in PostgreSQL. Nothing is written to QRadar.

#### MT-06 — Upgrade, online path (writes) [core]

**Precondition:** The verified backup (MT-03) is done. If you chose the offline path, this check is `Not run`.

**Action:**


```bash
git fetch --tags
git checkout v0.1.10
./qradar-dash upgrade 0.1.10
```

- Write the start and end time in the Note field with `date -u`.

**Expected:**

- The command exits with 0 and the last line is `Upgrade complete: 0.1.9 -> 0.1.10.`.
- Alembic prints three `Running upgrade` lines: `0044 -> 0045`, `0045 -> 0046` and `0046 -> 0047`. The migrate container exits with 0. These lines are expected in this release; it is not like 0.1.9.
- In the pre-check (before the writers stop) these three lines are printed; the head is that of the **installed** version, which is `0044`:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0044
Full report: ./qradar-dash doctor
```

- After the new stack has started, the second pair is printed; the head is that of the **target** version and is `0047`:

```
PASS  db_connect     database accepted SELECT 1
PASS  migration_head database and image are at 0047
Full report: ./qradar-dash doctor
```

- Both pairs are printed; the first is `0044`, the second `0047`. A single pair is printed only if the target is already installed: `Version 0.1.10 is already installed and healthy; nothing changed.` That is not the output of a first upgrade.
- The number of spaces in the `PASS` lines can change with the release; the text is the same.
- If the `.env` of 0.1.9 has no `FRONTEND_EDGE_IP` and `FORWARDED_ALLOW_IPS` is a non-empty list that is not `*` and does not contain the frontend address, these two lines are printed as well. Do not write the addresses on the page; write only that the lines appeared:
  - `Frontend address <address> added to .env (FRONTEND_EDGE_IP).`
  - `FORWARDED_ALLOW_IPS now also trusts the frontend (<address>): <list>`
- If `FRONTEND_EDGE_IP` is already in `.env` and the list does not contain the frontend address, only `FORWARDED_ALLOW_IPS now also trusts the frontend (<address>): <list>` is printed; that is also correct.
- If the keys are already correct (the list already contains the frontend address), these lines are not printed; that is also correct.
- If the output is `upgrade refused: …`, no container has been stopped; read the reason (for example `this checkout is version <x>, not 0.1.10` means `git checkout v0.1.10` was forgotten; `upgrade refused: 0.1.10 requires at least 0.1.9; nothing was changed` means the site is older than 0.1.9, and `.env` and the images are unchanged) and fix it. If there are two lines of the same name, `upgrade refused: .env has more than one FRONTEND_EDGE_IP line; nothing was changed` (or `FORWARDED_ALLOW_IPS`) is printed; delete the extra line and do not write `*`.
- If the exit is non-zero and the last line is `upgrade to 0.1.10 failed and was rolled back to 0.1.9; the broken database was kept`, the automatic rollback has run: go to section 15 (OPS-R44).

**Record:** The exit code, the start and end time (UTC), the two `migration_head` lines, the last line, and which of the address lines appeared (not the addresses themselves). If it is Fail, the first error line.

**Cleanup:** The rollback is the `rollback` command in section 15. There is no other cleanup.

#### MT-07 — Upgrade, offline bundle path (writes) [core]

**Precondition:** The bundle is prepared on a connected machine from the same tag: `git fetch --tags && git checkout v0.1.10 && ./qradar-dash bundle --output <bundle directory>`. `--output DIR` is the directory the bundle is written to; if it is not given, the file is written to the current directory. The file name is `qradar-dash-0.1.10-bundle.tar`. A file at the same path is not overwritten. The bundle is carried to the site. If you chose the online path, this check is `Not run`.

**Action:**

- Compare the checksum on both machines, then unpack the bundle in the install directory and upgrade:

```bash
sha256sum /path/qradar-dash-0.1.10-bundle.tar
cd <install directory>
tar -xf /path/qradar-dash-0.1.10-bundle.tar --exclude=images.tar
./qradar-dash upgrade 0.1.10 --offline /path/qradar-dash-0.1.10-bundle.tar
```


**Expected:**

- The two checksums are the same.
- The bundle is verified and loaded; the line `bundle images loaded and checked against MANIFEST` appears in the output. No container is stopped before the images are loaded and checked.
- The rest is as in the online path: two `PASS` pairs (first `0044`, then `0047`), three `Running upgrade` lines (`0044 -> 0045`, `0045 -> 0046`, `0046 -> 0047`), and the last line `Upgrade complete: 0.1.9 -> 0.1.10.`, exit code 0.
- If an image is missing, `upgrade refused: target images are unavailable: image <image> is not loaded on this host; no container was stopped; pass the bundle: …` is printed and nothing stops.

**Record:** Both checksums, the exit code, the start and end time (UTC), the two `migration_head` lines.

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

- `status`: `Installed version: 0.1.10`; `Last upgrade: success, 0.1.9 -> 0.1.10 at <utc>`; `Last upgrade backup: <path> (age <age>)`; `API ready: HTTP 200 (…)`; exit code 0; every service healthy.
- `/api/v1/version`: `"version":"0.1.10"` and `"migration_head":"0047"`.
- `/api/v1/health/ready`: `"status":"ready"`, `"database":"ok"`, `"revision":"0047"`.
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

- There is no `FAIL`, with one exception: a `watermarks` `FAIL` (and the `errors_recent` count that goes with it) for a collector whose failure was recorded before the upgrade, by the rule in MT-02. The hourly `log_source_inventory` and `rule_inventory` on a changed console are one case of it; the families that were not run while the site was down (for example `catchall_senders`, `rule_match`, `geo_countries_source`) are another. Write the collector names in the Note field; the wait belongs to MT-11. `db_connect` PASS; `migration_head` PASS and `database and image are at 0047`; `version_match` PASS and `frontend and backend report 0.1.10`; `containers` PASS.
- `forwarded_allow_ips` is PASS and the text is `FORWARDED_ALLOW_IPS trusts the frontend (<address>)`. The address is the same as `FRONTEND_EDGE_IP`; do not write the address on the page. This line has no runbook. awk prints `two addresses, the frontend is in the list; addresses not printed`. On a site behind an outer proxy, the list is the operator's old list with the frontend address added once; more than two addresses is correct, and awk prints the address count.
- If the line is missing, if awk says empty or star, or if doctor says FAIL (`FORWARDED_ALLOW_IPS is empty; every analyst shares one rate-limit key` or `FORWARDED_ALLOW_IPS is *; every analyst shares one rate-limit key`), this is Fail. The runbook is OPS-R21. Do not write `*`. If the list does not contain the frontend address, it is a WARN: `FORWARDED_ALLOW_IPS does not trust the frontend (<address>); pages drawn by the server share one rate-limit key`, runbook OPS-R21. This is also Fail.
- Because beat and worker have just started, `celery_workers` or `celery_beat` can give a temporary FAIL in the first minute; wait a minute and repeat the command. If the FAIL stays on the second run, it is Fail.
- Because collection has not run for one cadence yet, `cadence`, `anomaly_evaluation` and `rule_match_coverage` can be WARN. These are read again in MT-11.
- The ordinary PASS text of `watermarks` is `{n} watermark(s) have no error or excess lag` and has no runbook. On a console that has no offenses, a WARN stays before the first clean `offense_snapshot` run: the lag exceeds twice the cadence, `consecutive_failures` is 0, and `last_error` is empty (MT-02). The text has the form `{n} watermark(s) exceed twice their cadence: offense_snapshot` and the runbook is OPS-R30. This is a WARN, not a Fail; write it in the Note field. It is read again in MT-11.
- A stalled offense watermark is a separate WARN: `{n} watermark(s) stalled at the page ceiling: offense_snapshot`. The runbook is OPS-R66. If there is lag as well, the two sentences are joined with a semicolon and the runbook is still OPS-R66. This WARN is not a Fail. A provider error stays FAIL, the runbook is OPS-R30, and the same text can be appended to the end of the FAIL sentence. A clean run clears the stall (MT-11).
- `backup_age` can stay WARN for the same reason as before the upgrade: no backup, the age cannot be read, older than 26 hours, or the backup timer is not installed or is off (a fresh backup also gives a WARN when there is no timer or it is off). This is not a fault of the upgrade. The line is in the doctor table of `docs/OPERATIONS.md`; it has no OPS-R id.

- The report has two lines that 0.1.9 did not have, `geo_country_source` and `qradar_health`. A WARN on either is not a Fail: write its text in the Note field (a lab CE console always gives a WARN on `qradar_health` because its license is over-allocated). When the console does not answer, `qradar_health` is `SKIP` with `the console did not answer; see qradar_auth`: a `SKIP` is neither a `WARN` nor a `FAIL` and does not raise the exit code, and `qradar_auth` is the line to read. A FAIL on `geo_country_source` is a Fail (MT-129 says what it means). A `watermarks` FAIL with “The country property … does not exist on this console” is the same finding seen from the collector; fix the setting (MT-129) and run `doctor` again.

**Record:** The exit code; the ids of the WARN and FAIL lines; the text of the `migration_head`, `version_match` and `forwarded_allow_ips` lines (without the address); the line awk printed.

#### MT-133 — `doctor --aql`: every family's query is accepted by the console [core]

**Precondition:** MT-09 is done. The console answers (`qradar_auth` PASS). The command sends up to 11 read-only searches per REST console, one at a time, through the Ariel permit pool; they are recorded under the caller `DOCTOR_AQL`. Nothing is written to QRadar.

**Action:**

```bash
./qradar-dash doctor --aql
echo "exit: $?"
```

**Expected:**

- Nine extra lines after the ordinary ones: `aql_log_source_volume`, `aql_rule_match`, `aql_history`, `aql_waf_collection`, `aql_ips_collection`, `aql_property_watch`, `aql_catchall_senders`, `aql_geo_collection`, `aql_qradar_health`.
- **Pass = no `FAIL` among the `aql_*` lines.** A `FAIL` names the HTTP status, the query version and the property names; fix it by OPS-R72 and run the command again. A `SKIP` (family off, nothing configured, MCP console) is not a Fail.
- A `WARN` (timeout, unavailable console, no free permit) is not a Fail: write the id and its cause in the Note field.
- No line holds an event value, an IP address or QRadar's own error text.

**Record:** The exit code, the status of each `aql_*` line, and the cause of every `WARN` or `FAIL`.

#### MT-10 — Outage duration and the interface's return [core]

**Action:**

- Use the start and end time of the upgrade command (MT-06 or MT-07). Open `https://<host>/login` in the browser.

**Expected:**

- The upgrade finishes in about a minute to a minute and a half on a small database. The measured 0.1.10 times: online, which pulls the images from the registry inside the command, 99 s wall and 25.9 s of outage; offline from the bundle, which loads the images from it inside the command, 80 s wall and 20.3 s of outage; and with the images already on the machine, 47 s wall and 21.3 s of outage. This is not a promise: the outage window is shorter than the wall time because the image step is outside it. An outage of more than 10 minutes is written in the Note field (on a large database the time of the dump is added; this is not a promise).
- The sign-in page shows the `h1` “Sign in” (the browser tab reads “Sign in · Behavior Analytics”) and under it `API version 0.1.10`. The page is English whatever the `ba_locale` cookie holds.

**Record:** The upgrade duration (minutes), the estimate of the outage duration, and the version line on the sign-in page.

#### MT-11 — Collection runs again after the first cadence [core]

**Precondition:** You know the end time of the upgrade. You have signed in with the administrator account (section 3).

**When to look:** After the upgrade, for each family that is on, its own cadence on the page + 3 minutes (the "Timing and total duration" table above); the “Next run (UTC)” column gives the slot, and the run is seen up to one beat tick (1 minute) later. Default cadences: 8 min for Log source volume, Anomaly explanations, Catch-all senders and Geography (on by default) and for Rule matches and Property watches (off by default); 18 min for WAF collection and IPS collection (off by default). A family set to a longer cadence is read at that cadence + 3 minutes, so the longest cadence on the page decides when the check can be closed.

**Action:**

- Open the `/admin/search-cadence` page (“Search cadence”). Read the “Cadence” value of each family and note the longest one.
- When the wait is over, reload the page and compare the “Last run (UTC)” column with the end time of the upgrade.

```bash
./qradar-dash doctor
```


**Expected:**

- The “Last run (UTC)” time of every enabled family that has a cadence is after the end of the upgrade. “Next run (UTC)” shows only a future time, or, for a family that is due now (and has run at least once), the time. The next run of a disabled family is a dash (—), even if the time would be in the future. The computed time of a family that has never run is also a dash if it lies in the past. A family that has never run shows “No run yet” or “Not run since upgrade” (the dash or this text; it is not zero). It is expected that the next run of a disabled family (for example, if WAF is off on the site) is a dash.
- If you look before the wait is over, you see “No run yet” or an old time: this is not a fault, do not write Fail, wait.
- `doctor`: `cadence` PASS (`<n> cadence row(s) are on time or disabled`), `celery_workers` and `celery_beat` PASS. `watermarks` and `errors_recent` are PASS (`errors_recent`: `no watermark has recent failures`) except for the cases below; a row of a switched-off family is not judged and is named after `not judged (family off):` (MT-02). Any other `FAIL` is Fail. A `watermarks` `FAIL` for a collector that has had its turn since the upgrade and still shows a `last_error` is Fail (OPS-R30); for a collector whose turn has not come, it is not, and `doctor` is read again after that turn. If `cadence` is a WARN, write the names of the families in the Note field and wait another cadence + 3 min.
- On a console that has no offenses, a clean `offense_snapshot` run moves the watermark to the time of the run. This is the difference of 0.1.9: 0.1.8 kept the WARN of about 168 hours on the same console even after a clean run (MT-02). While `consecutive_failures` is 0 and `last_error` is empty, the lag stays within the cadence; no WARN is expected for this line. A failed or truncated run does not advance the watermark. In that case the WARN can remain and is not a Fail; write it in the Note field. The lag runbook is OPS-R30.
- The same clean run also clears the page-ceiling stall. If the stall sentence remains after a clean run, the runbook is OPS-R66; this alone is not a Fail, write it in the Note field. A stall is not a `last_error`.
- If the console was changed (an upgrade on the same console is not this case), `log_source_inventory` and `rule_inventory` are hourly (default 3600 s). These two inventories, if they failed at the change, can wait up to one hour for their next beat. Until that time is over the pages show no sources; if the rule inventory has not arrived, the rule list is empty too. This is not a Fail; write it in the Note field and wait for the hourly retry. Meanwhile `watermarks` can be FAIL for only these two rows and `errors_recent` can be WARN; neither is a Fail.
- Data-dependent checks done before this check (Dashboard, anomalies) can show “stale” or “not collected” until this wait is over; this is the correct state.

**Record:** The longest cadence, the last run time (UTC) of each family, the `doctor` exit code and the WARN/FAIL ids.

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
- The version line in the menu is `API version 0.1.10`.

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

- The navigation groups are “Behavior”, “Fleet”, “Detection”, “Operations” and “Administration”; “Dashboard” and “Guide” stand alone. The items are “Dashboard”, “Overview”, “Anomalies”, “Sources”, “WAF behavior”, “Property watches”, “Geography”, “Fleet”, “Fleet sources”, “Catch-all senders”, “Offenses”, “Alerts”, “Rules”, “Rule statistics”, “Custom rules”, “Coverage”, “Log Sources”, “Searches”, “Administration overview”, “Search cadence”, “Classification rules”, “Outage alerts”, “WAF sources”, “IPS sources”, “History backfill”, “SIEM load”, “Users”, “Directory” and “Guide”. “Administration overview” is the first item of the Administration group and opens `/admin`. “History backfill”, “SIEM load”, “Users” and “Directory” are shown only to a role that holds the permission (`read:history_backfill`, `admin:*`, `admin:users`, `admin:auth`).
- The top bar: “Open alerts: {count}” (the number of open alerts), “Refresh” (“Off”, “1 min”, “5 min”), “Connection status: {status}”; the search box “Search sources, rules, anomalies”. `/config-changes` is not in the navigation; it opens by address.

**Record:** The name of any item that is missing or wrong.

#### MT-23 — English: sign-in page `/login`

**Action:**

- Open the route.

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

**States seen on a healthy site.** Loaded; empty (for example no open offense); “Not collected.” (if collection is off for a family or has never run); “Stale” (“Stale: the latest data is from ‹time›, {age} old.”; this can be seen right after the upgrade before the first cadence has passed and is correct; if it stays after cadence + 3 min it is a fault); forbidden (a viewer, in a window 12 columns wide, reads the “Outage policy” row of the “Customize” → “Add widget” catalog: “Your role lacks permission admin:outage.”; the “Add” button is disabled. In a narrow window it says “Widen the window to customize” instead of “Customize”. The check "The viewer role's limit" in the Sign-in section). **A state that cannot be seen on a healthy site:** "could not be read" (“The {subject} could not be read.”) appears only if the backend or an endpoint fails; it cannot be produced without changing the configuration, so this section skips it. For a family whose collection switch was left off, the widget shows "Not collected."; this is not seen for families whose switches are on. **For every widget in this section** at least one cadence + 3 min must have passed after the upgrade (MT-11); if you look earlier, "stale" is correct. On a changed console, until the hourly inventory arrives, the source widgets show no sources; this is not a Fail (MT-11).

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

- The data loads. The title is “Active anomalies, now”; the “By detector” and “Lifecycle” breakdowns.
- The link opens the `/anomalies?active_only=true` route (link text “Anomalies”). Times are written with the UTC zone; there is a “Data to ‹time›.” line.

**Record:** Whether the data loaded; whether the link opened the right route. If it is “Stale”, write in the Note field whether the wait had passed.

#### MT-64 — Dashboard widget: “Fleet EPS”

**Action:**

- In the “SOC Overview” template find the widget, read its data, press its link.

**Expected:**

- The data loads. The axis carries the unit (“Events per second (EPS)”; the time axis says “Time (UTC)”); the expected sum is a dashed line that is drawn only where every source has a reliable baseline (it is not a range); a step that was not measured is a gap, not a zero. While loading, a text of the form “Loading {what}…” is shown.
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

- The data loads. The latest anomaly rows; the time is written with the `UTC` zone. If there are rows left over from 0.1.6, they are listed too.
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

#### MT-128 — World map: dashboard widget and `/geography` [core]

**Precondition:** Upgrade complete (MT-08), the country property is set (MT-129), and at least one `GEO_COLLECTION` cadence + 3 min has passed (default 5 min; total 8 min). Signed in with an account that has `read:geo`.

**Action:**

- On the `/` page, find the Geography (`map.geography`) widget in the bottom row of the "SOC Overview" template. Change the direction between Source and Destination, and the window between 1 hour, 24 hours and 7 days.
- Click the widget's link and open `/geography`. The same direction and window appear in the URL (`?window=24h&direction=SOURCE`, etc.). Read the map, legend and country table.

**Expected:**

- The map is drawn; the colours use the sequential `--map-0…--map-4` scale, at most four classes on a log axis, with a unit legend ("Events per country"). There is no green/amber/red status tone.
- A country with no events uses `--map-0` as "No data", never the lowest class. A country with events shows "N events"; a lower-bound legend says "At least N events".
- If nothing was collected for the direction in the range, "Not collected." appears and the map is not drawn; the coverage line under the map reads “Buckets measured 0 of N” (N is the number of settled 5-minute buckets the range should hold, per enabled console: at most 12 for the 1-hour window). With the default settings Destination reads "Not collected." once the buckets 0.1.9 stored have expired, because `GEO_DESTINATION_COUNTRY_PROPERTY` is empty (MT-129). If buckets were collected but no country could be assigned, "Insufficient data." appears and again no map is drawn. A partial read draws the map with "Partial"; a lower-bound legend says "At least". None draws a zero.
- The page names where the country came from ("From the QRadar property “<name>”" or "From the GeoIP lookup of the address").
- If the newest bucket is older than the `GEO_COLLECTION` cadence allows, the widget keeps the map and adds a warning line ("Stale: the latest data is from <time> UTC, <age> old."); the page shows the same age line and the top bar shows "Data to …". A fresh answer shows "Data to <time> UTC".
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

The anomaly list gathers four families in one place: log source volume, WAF endpoint, IPS (entity and source) and property watch value. The detail page (`/anomalies/<id>`) opens a different investigation depending on the family. Lifecycle states: `CANDIDATE`, `OPEN`, `RECOVERING`, `RESOLVED`, `SUPPRESSED`; `INSUFFICIENT_DATA` and `NORMAL` are source states. If the site has no anomaly of a kind, the check for that kind is `Not run` and "no data" is written in the Note field; do not try to produce a new anomaly.

#### MT-80 — Anomaly list and filters

**Action:**

- Open the `/anomalies` page. Try the filters one after another: “Subject kind”, “Lifecycle state”, “Active only” (“Filter”).

**Expected:**

- The `h1` is “Anomalies”. The columns are “Subject”, “Detector”, “State”, “Observed → Expected”, “Deviation”, “Severity”, “Started”, “Evidence”.
- The subject kind options are “Log sources”, “WAF endpoints”, “IPS entities”, “IPS sources”, “IPS entities and sources”, “Property watch values”. The “IPS entities and sources” option lists IPS entities and IPS sources together.
- After filtering, the active filter chips (“Filtered by”) appear; if nothing matches: “No anomalies match these filters. This is a filtered view, not a statement about the fleet.” If no anomaly has ever been detected: “No anomalies have been detected. Sources without an adequate baseline are not being judged — check the behavioral overview for how many.” Times are UTC; the state words are the codes (`OPEN`, …).

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

### 7. Alerts

In the interface this page is called **Alerts**, the route is `/alerts`. Lifecycle: open → acknowledged → resolved; a resolved alert cannot be reopened.

#### MT-87 — Alert list

**Action:**

- Open the `/alerts` page. Try the status filter (“Alert status”).

**Expected:**

- The `h1` is “Alerts”; the columns are “Title”, “Severity”, “Status”, “Occurrences”, “First seen”, “Last seen”. The status filter: “Open”, “Acknowledged”, “Resolved”.
- If there is no alert: “No alerts have been raised.” The count line: “Showing {shown} of {total} alerts.”
- The titles of the catalog alerts are English sentences (MSG-7): search threshold and search failure, custom rule, offense collection freshness, new log source, new catch-all sender. Offense collection freshness reads “Offense collection is stale for {instance}”. The alerts the engine writes are English sentences too (MSG-7b): log source volume “{anomaly_type} on {log_source}” (for example “Volume drop”, “No events”), IPS “{detector} on {log_source}” and the destination port, destination IP or signature forms, property watch “{detector} on {watch}: {value}”, WAF “{detector} on {log_source}: {method} {endpoint}”. The same limits as MT-38.

**Record:** That the list opened, the number of rows and the title of one alert.

#### MT-88 — Alert detail: Description and resolution reason

**Precondition:** The site has at least one alert.

**Action:**

- Open an alert from the list.

**Expected:**

- The `h1` is the alert's own title, and the browser tab reads “Alert detail · Behavior Analytics” (“Alert Detail” is the `h1` only when the alert could not be loaded). The “Description” card carries the alert's description. There are the “Evidence” and “Lifecycle” sections and the table “Notification Delivery History”; if there is no notification: “No notifications for this alert.”
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

**Record:** That the page opened; the reason of one history row.

#### MT-104 — Rule statistics

**Action:**

- Open the `/rules/statistics` page. Change the day selection (one value) (“Measurement days”).

**Expected:**

- The `h1` is “Rule statistics”. The sections: “Rule composition”, “Most matched”, “Silent enabled rules”, “Costliest by benchmark”, “Capacity history”. When the measurement window is complete it says “Complete.”, when partial “PARTIAL.”, when insufficient “INSUFFICIENT_DATA.”; an incomplete window is not evidence of silence (“Silence was not judged.”).
- The cost unit: “Enabled rules ordered from the lowest capacity benchmark, which is the costliest to evaluate. Unit: {unit}.” The silent-rule exclusion reason and the measurement window sentence are English sentences (MSG-2). No number is drawn as zero: a section that cannot be measured shows a heading such as “No complete window exists to measure rule matches.”
- If no QRadar instance is registered: “No QRadar instance is registered.”

**Record:** That the page opened; the state of the measurement window (complete/partial/insufficient).

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
- `/behavior/waf`: “WAF behavior”; the WAF source page “WAF source endpoints”; a source that is not collected shows “Not collected”. The pause reasons (MSG-4a) are English sentences.
- `/behavior/watches`: “Property watches”; the columns “Status and reason”, “Scope”, “Open incidents”. If collection is off, “COLLECTION OFF.”; in shadow mode, “SHADOW MODE.” The status reasons are English sentences (MSG-4b) and are rewritten at every collection; for a watch that is reading history, the history state (“Reading history: {progress}”) appears.
- `/offenses`: “Offenses”; if there is no offense, “No offense snapshot has been collected yet.” or “No offenses have been collected from QRadar yet, so this list is empty. Offense collection is set up by an administrator.”; read-only (“This page issues no writes: offenses are triaged in QRadar, not here.”).

**Record:** That the five pages opened; if there is no watch, `Not run` for that part.

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

- The `h1` is “Search cadence”; the columns are “Family”, “Cadence”, “Bucket”, “Worst-case detection delay”, “Last run (UTC)”, “Next run (UTC)”, “Searches in last run”. Eight families are listed (for example “Log source volume”, “WAF collection”, “IPS collection”, “Rule matches”). For a family that has never run, a dash or “Not run since upgrade”/“not counted”; these are not zero.

**Record:** The cadence of each of the eight families (5 min, 15 min…).

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

Nothing in this tool writes to QRadar (the fourth of the four rules in `docs/OPERATIONS.md`). The provider interface (`app/providers/base.py`) has no create, update or delete method for QRadar configuration; it has only the Ariel search lifecycle methods (`create_ariel_search`, `get_ariel_search_status`, `get_ariel_search_results`, `cancel_ariel_search`). The requests the REST provider makes to QRadar are, in the code: `GET /system/about`, `GET /config/event_sources/log_source_management/log_sources`, `…/log_source_types`, `…/log_source_groups`, `GET /config/event_sources/custom_properties/…`, `GET /analytics/rules`, `GET /analytics/building_blocks`, `GET /siem/offenses`, `GET /ariel/searches/<id>` and `…/results`, and, to record an Ariel search, `POST /ariel/searches` and, to cancel its own search, `DELETE /ariel/searches/<id>`. The last two create and delete a temporary search object in QRadar; they do not create, change or delete a configuration object (rule, log source, custom property, offense, reference data).

The place of the QRadar console's audit log is not documented in this repository; the console-side check is therefore a **console administrator step**.

#### MT-119 — `doctor` shows that it makes only one kind of call to QRadar [core]

**Action:**


```bash
./qradar-dash doctor | grep -E 'qradar_tls|qradar_auth'
```


**Expected:**

- The `qradar_auth` line says `GET /system/about succeeded; QRadar <version>` (a read-only `GET`). If `qradar_tls` is PASS it says `verified certificate expires in <n> days`. `doctor` does not run an Ariel search and does not write to QRadar.

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
- The data that 0.1.9 showed is gone: anomalies, alerts, rules, log sources or offenses are empty or missing (and the reason is not a "not collected" honesty state). On a changed console, source pages looking empty until the two hourly inventories are retried is not this item (MT-11).

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
- When it is finished, return the checkout to the running version. The script on disk is still 0.1.10 before the checkout; `install` is not run before it. Then verify:

```bash
git checkout v0.1.9
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

- The line `Rollback complete.`; exit code 0; `PASS  migration_head database and image are at 0044` (once). The schema is not rolled back; the head stays `0044`.
- This backup holds `install.answers`. The rollback puts the file back and writes no line for it. The `Removed answer key` line is not expected. That line appears for an old backup that has no copy: `Removed answer key <name> (unknown to 0.1.9).` This upgrade's own backup is not that case.
- `cmp` prints nothing and exits with 0. `stat` prints `600`; `400` if the backup was `400`.
- `status`: `Installed version: 0.1.9`, `Last upgrade: rolled_back, …`; `/api/v1/version`: `"version":"0.1.9"` and `"migration_head":"0044"`. Sign-in works. `doctor`: no `FAIL` when the console answers; on an unreachable console `qradar_tls` and `qradar_auth` and the `watermarks` / `errors_recent` lines that follow them are FAIL (the `install` item below explains this).
- `install` exits with 0 when the console answers. The summary ends with the `Install time` line, followed by a line made of `=` signs. There is no separate “Install complete” line. There is no `unknown key` in the output. The summary line `QRadar connection  FAILED` is the install where the console did not answer and the exit was 1. The rollback check is Pass on that exit too: if the summary was reached (the `Install time` line and then the `=` line were written), if the output has no `unknown key` and no `install refused`, and if the `qradar_tls` and `qradar_auth` lines of `doctor` show the same console access fault. On an unreachable console both lines are FAIL; the `runbook:` value at the end of the line carries the runbook of console access (`runbook: OPS-R4` for `qradar_tls`, `runbook: OPS-R1` for `qradar_auth`). An exit of 1 that ends with the line `API readiness  NOT READY`, or while the general origin check failed (`public URL <address> failed`), is Fail. If the checkout was forgotten the script refuses: `install refused: this site runs 0.1.9 and this checkout is 0.1.10; use ./qradar-dash upgrade 0.1.10 to change versions, or check out v0.1.9 to re-run install (see docs/OPERATIONS.md, …)` (the message ends with the name of the upgrade section of `docs/OPERATIONS.md`). No container changes version on this refusal; run `git checkout v0.1.9` first.
- `install` exits with 2 and writes `[FAIL] the answers are refused; nothing was written:`, followed by one line per refused answer in the form `<KEY>: <reason>` (for example `QRADAR_TOKEN_FILE: file not readable: <path>`), when the answers file names a file that is not there or not readable (the QRadar token, the CA certificate, the TLS certificate and key, or the registry token) or holds any other value the script refuses. The script has checked the answers before it touched anything: no file, container or setting was changed, the rollback that came before it stands, and this is not a fault of the rollback. Put the named file back at the path the answers file gives (the answers file holds paths, never the secret itself, and the rollback does not unpack `secrets.tar`), or correct that line in `.secrets/install.answers` (mode `0600`), and run the `install` command again. Write the refused lines (the key and the reason, no secret) in the Note field; the `install` exit and the `QRadar connection` result in the Record field are then `2` and `not reached`, and the install item is `Not run` until the repeat has exited with 0 or 1 as above.
- The script writes the name of the broken database copy (`qradar_obs_broken_<utc>`) and the exact command to delete it later; do not rush to **delete** it.
- If the rollback fails its own health check, the writers stop and `Rollback failed; the writers and the UI are stopped. Verified backup: <path>` is written (OPS-R45); repeat `./qradar-dash rollback` or use `./qradar-dash restore <path>`.

**Record:** The exit code of the rollback, the `Rollback complete.` line, `Installed version`, `migration_head`, the `cmp` exit, the digits of the `stat` mode, the `install` exit and the `QRadar connection` result in the summary. The contents of the answer file are not written.

**Cleanup:** None; a rollback is a rollback.

#### MT-123 — Rollback rehearsal (writes) lab only

**Precondition:** **lab only**: this is not done on the production site.

**Action:**

- On the lab VM, after the upgrade, run the rollback steps of MT-122. The checkout is `v0.1.9` in this step and `./qradar-dash install --non-interactive .secrets/install.answers` exits with 0 if the console answers (the condition is in the `install` item of MT-122). MT-16…19 must not have been done yet. Then upgrade again:

```bash
git fetch --tags
git checkout v0.1.10
./qradar-dash upgrade 0.1.10
```

- MT-16, MT-17, MT-18 and MT-19 are done after this re-upgrade.

**Expected:**

- After the rollback the head stays `0044`. The second upgrade is first `0044`, then `0047`; the two `PASS` pairs say this and three `Running upgrade` lines (`0044 -> 0045`, `0045 -> 0046`, `0046 -> 0047`) are written. The last line is `Upgrade complete: 0.1.9 -> 0.1.10.`. The `Last upgrade:` line shows the latest operation.
- When the checkout is not `v0.1.10`, `upgrade 0.1.10` is refused: `upgrade refused: this checkout is version 0.1.9, not 0.1.10; run git fetch --tags && git checkout v0.1.10 first (see docs/OPERATIONS.md, …)` (the message ends with the name of the upgrade section of `docs/OPERATIONS.md`). No container stops.
- The rollback removes the accounts created after the upgrade.

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
